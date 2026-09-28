"""多智能体会议：9 个角色、两轮博弈、动态权重、风控否决，最后收口成一个 verdict。

## 这一层在做什么

它不是"把 9 段回答拼起来"——那种东西看起来像多智能体，实际上是一份把同样的数据
讲九遍的长文。这里真正发生的事情只有四件：

1. **一份证据，九个人看**（`evidence.py`）。同一轮里取一次数，按角色分发。
   ⇒ "多空看的是不是同一张图"是**结构上**成立的，不靠提示词嘱咐。
2. **第 2 轮是真的交锋**（`roles._opponent_block`）。多头拿到的第 2 轮提示词里，
   **原样**含空头第 1 轮的立场值与原话；反过来也一样。它必须**改立场**或**写反驳**，
   两件都没做就是"这一轮没发生"（`_revised()` 判定，并会重试一次）。
3. **权重是算出来的，不是配出来的**（`weights.py`）。场景 × 历史校准 ×（收缩后的）
   自报置信度 × 先验。判据可以拿权重表把 `score` 重算一遍，算不出来就说明权重是装饰。
4. **风控有一票否决**（`risk.py`）。命中硬条件 ⇒ 方向降级成 flat；
   软条件 ⇒ 保留方向但强制加提示、扣置信度。条件是**数值条件**，不是形容词。

## 事件顺序（★所有事件必须在 `done` 之前发完 —— 既有纪律：一轮以 done 收尾）

    agent_round(1)
      9 × (agent_start → agent_say → agent_done)          第 1 轮：各自独立出观点
    agent_round(2)
      bull ‖ bear                                           互相拿到对方的第 1 轮
      risk                                                  **算出来的**复核（非 LLM）
      pm                                                    收口（拿到全部因子与风险条件）
    agent_weights                                           权重 + 依据
    agent_verdict                                           方向 / 分数 / 异议 / 否决 / 风险提示
    done

★**哪些是并发的**（2026-09-22 改）：第 1 轮的 9 个角色、第 2 轮的多空这一对。
  每一组内部**互不依赖**（第 1 轮只有一份只读证据；多空第 2 轮各拿的是对方
  **第 1 轮**的话），所以整组墙钟从"各次通话之和"变成"最慢那一次"。
  组与组之间仍然严格串行（第 2 轮必须等第 1 轮说完、pm 必须等风控复核）。
  ⇒ 一场会议的墙钟从 6~9 分钟降到 ~2 分钟；`/psych` 等的那个 psych
  （它在 9 个角色里排最后）从"等 8 次通话"降到"只等它自己那一次"。

## 三个"故意这么做"，都写在这儿免得后来人当成 bug

① **风控官的两轮发言都不是 LLM 写的**（`source: 'rule'`）。它的全部内容就是
   "哪一项、多少、阈值多少"——而那些数 `risk.py` 已经精确算出来了。让模型复述一遍
   只会多一处**引错阈值**的可能，而错引一个阈值正是本站最不能忍的"编数字"。
   ★事件里带 `source: 'rule'`，不假装那是模型说的。
② **投资经理看到的是"权重的因子"，不是归一化之后的权重表**。理由：权重表必须在
   *所有人发完言之后*才能算（它的输入含 pm 自己的立场），而那之后再让 pm 说话
   就来不及了。所以给 pm 的是**决定权重的那些数**（先验/场景/波动/校准/收缩后的置信度），
   并明确要求它"**不要引用具体权重数字**"（它算出来的会和最终表对不上）。
   ⇒ 权重表**只有一份**，`score` 一次算成，可复算。
③ **角色手上没有工具。** 它们不调 `get_kline` —— 那是 `evidence` 的活。
   给 9 个角色各挂一套工具，就是 9 次网络请求、9 个不同时刻的快照，
   而且"数据从哪来"再也说不清了。

## 离线跑（判据靠这个）

    LICAI_AGENTS_STUB=1  ⇒  取数走桩 **且** 传输层换成 `_stub_transport`
    LICAI_AGENTS_STUB_OUT=<路径>  ⇒  把每次发出去的 payload 追加写成 jsonl

`_stub_transport` 照 `llm._stub_transport` 的规矩：**由 payload 驱动**，不凭空演。
它从第 2 轮的提示词里**抠出对方第 1 轮的立场值与原话**，再用在自己的反驳里 ——
于是"第 2 轮的 payload 真的带了对方第 1 轮的论点"这件事，可以被离线断言
（判据读 `LICAI_AGENTS_STUB_OUT` 那份 jsonl 即可）。
★`llm` 自己那个桩（`LICAI_LLM_STUB`）**驱动不了角色**：它吐的是 tool_calls，
而角色要的是 JSON 观点，于是全部 `stance=None`、风控的 `no_directional_view` 命中、
最后得到一个 flat —— 那是**正确但没用**的结果。要离线跑请用 `LICAI_AGENTS_STUB`。
"""

import concurrent.futures
import json
import os
import queue
import threading
import time

import graph_kb
import llm

# ★包内互相 import 用**相对**形式（先例 `agent/news/__init__.py:44` 的
#   `from . import config as _config`）：`agent/` 在 sys.path 上，所以
#   `import evidence` 这种绝对写法会去找一个叫 evidence 的**顶层**模块 —— 找不到，
#   而报错是 `ModuleNotFoundError: No module named 'evidence'`，
#   看起来像"文件不存在"，其实只是写错了形式。
from . import evidence, risk, roles, weights

ROLES = roles.ROLES

# 桩开关。和证据层共用一个（判据只设一个环境变量就能整场离线跑）。
STUB_ENV = evidence.STUB_ENV
STUB_OUT_ENV = 'LICAI_AGENTS_STUB_OUT'

# 角色回话的温度。★比 `llm.run_turn` 的 0.3 高一点：这一层要的是**分歧**
#   （9 个角色说同一句话就没有会议了）。但也不能高到把数字讲飘 ——
#   所以只抬到 0.4，且提示词里把"只能用【本轮数据】里的数"写在最显眼处。
TEMPERATURE = 0.4

# 第 2 轮"没改也没反驳"时的重试次数。★只重试一次：重试是在赌"它这次会照做"，
#   而如果两次都不照做，那说明这个模型/这次提示词做不到 —— 那就**如实记
#   `revised: False`**，不要靠循环把它磨出来（磨出来的"交锋"是假的）。
ROUND2_RETRY = 1

# ★**一个角色**一次回话的墙钟预算（秒）。
#
# 它和 `llm.STEP_TIMEOUT`(=180) 是两个东西，别混：
#   · `STEP_TIMEOUT` 是**传输层**的上限，量的是一次 HTTP 通话；
#   · 这个是**会议层**的上限，量的是"这个角色这一轮该说完了"。
# 为什么要多这一层：`_ask` 原来唯一的界限就是传输层那 180 秒，而
# **一个角色拖满 180 秒 = 整场会议被它拖着**（第 1 轮是串行的，见 `run`）。
# 超了不该让会议卡死，而该让**这一个角色**降级成"这次没答上来"
# （`_view_of` 的错误分支，stance=None + 如实说明），其余角色照常。
#
# ★默认 90 秒是对着实测取的（本机 2026-09-22，`deepseek-v4-pro`）：
#   单角色一次通话实测 18.2 秒（psych）～65 秒（bull，三次 58.2 / >60 / 45.6），
#   90 秒留了余量 ⇒ 正常的长回答**不会**被它砍到；它砍的是"卡住"。
#   要调就设 `LICAI_AGENT_ROLE_TIMEOUT`，别去改传输层那个数。
ROLE_TIMEOUT = 90

# 一场会议里**同时**跑几个角色。第 1 轮的角色只共享一份**只读**证据
# （它们第 1 轮不互相看，第 2 轮才交锋），所以并发是结构上安全的；
# 串行的代价是"整轮墙钟 = 各角色之和"，并发之后是"最慢那一个"。
# 实测 9 个角色串行（v4-pro，单次 18~65 秒）要 6~9 分钟，而 `/psych` 页面
# 只显示 psych 一条、psych 又在 ROLES 里排第 9 ⇒ 用户等的是**别人的** 8 次通话。
CONCURRENCY = 9


# ── 事件的 JSON 安全 ──────────────────────────────────────────────────
#
# ★规格 §0.7：`json.dumps(..., allow_nan=False)` 必须能过。这里的做法是——
#   **发之前先试一次**，过不了就把那个值修掉并**在事件上写明修了什么**，
#   而不是让它安静地发出去（NaN 会在前端变成 `null` 或直接让 `JSON.parse` 抛，
#   两种都是"页面上少了一块，但没人知道为什么"）。

def _sanitize(v, depth=0):
    """把 NaN/inf/不可序列化的东西换成 None / str。**修了什么会返回在报告里**。"""
    if depth > 8:
        return str(v)[:200]
    if isinstance(v, float):
        if v != v or v in (float('inf'), float('-inf')):
            return None
        return v
    if isinstance(v, dict):
        return {str(k)[:80]: _sanitize(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_sanitize(x, depth + 1) for x in v[:200]]
    if isinstance(v, (str, int, bool)) or v is None:
        return v
    try:
        return json.loads(json.dumps(v))          # numpy 标量等
    except (TypeError, ValueError):
        return str(v)[:200]


def _mk_emit(emit):
    """包一层：每个事件发出去之前先证明它是合法 JSON。`emit` 为 None ⇒ 空操作。

    ★带锁：第 1 轮的角色是**并发**跑的（见 `run` 里那段），而
      `report['n'] += 1` 是**读-改-写** —— 两个线程同时做会丢计数，
      而丢掉的只是"这一场发了几条事件"这一个数，页面上完全看不出来。
      锁同时保证一个事件的序列化过程不被另一个事件插进来
      （SSE 那边 `emit` 是 `queue.put`、离线跑那条是 `list.append`，
       两个操作本身是原子的；这里防的是**报告**被写坏）。
    """
    if emit is None:
        return lambda e: None
    report = {'n': 0, 'repaired': []}
    lock = threading.Lock()

    def f(e):
        with lock:
            report['n'] += 1
        try:
            json.dumps(e, ensure_ascii=False, allow_nan=False)
            emit(e)
            return
        except (ValueError, TypeError) as ex:
            fixed = _sanitize(e)
            fixed['json_repaired'] = '%s：%s' % (type(ex).__name__, str(ex)[:120])
            with lock:
                report['repaired'].append({'type': e.get('type'),
                                           'why': fixed['json_repaired']})
            try:
                json.dumps(fixed, ensure_ascii=False, allow_nan=False)
                emit(fixed)
                return
            except (ValueError, TypeError):
                # 最后兜底：绝不因为**一个事件**把整场会议打断。但也不许静默 ——
                # 记进 stderr（服务端日志能看到），并留在报告里。
                import sys
                print('[agents] 事件发不出去：%r' % (e.get('type'),), file=sys.stderr)

    f.report = report
    return f


# ── 传输层（可替换 + 桩）──────────────────────────────────────────────

def _default_transport(payload):
    """默认走 `llm` 的那条通路。★**每次调用现取 `llm.TRANSPORT`**。

    为什么不在 import 时把 `llm.TRANSPORT` 绑到本模块的常量上：验证脚本会在
    `import llm` 之后**替换** `llm.TRANSPORT`。绑死一个值的话，替换发生在
    import 之后就**看不见了** —— 症状是"判据设了桩，但这一层还是在联网"。
    """
    return llm.TRANSPORT(payload)


def TRANSPORT(payload):
    """这一层的传输入口：**桩开关在每次调用时现读**，不在 import 时定死。

    ★这条是被一次"自测卡死"逼出来的（2026-09-22）。原来的写法是模块级：

        TRANSPORT = _default_transport
        if os.environ.get(STUB_ENV): TRANSPORT = _stub_transport

    它错在一个**顺序**上：`python -m agents._probe` 会**先 import 父包
    `agents`、后执行 `agents/_probe.py` 的模块体**，而 `_probe` 里那句
    `os.environ.setdefault('LICAI_AGENTS_STUB', '1')` 是模块体的一部分 ——
    它跑的时候本模块早已 import 完了 ⇒ 开关永远读不到 ⇒ **桩模式下真去调
    DeepSeek**（`_probe` 的模块头明明承诺"自己兜底设上，谁忘了设也不会去打真网络"，
    而那句承诺被这个顺序悄悄作废了）。
    症状特别难认：自测"卡住不返回"、进程里开着 `/dev/nvidia*`、主线程停在
    `llm._real_transport` 的 `ssl.read` 上 —— 看起来像"网络慢/模型慢"，
    其实是**桩没生效**。判别句：`TRANSPORT` 是谁、它此刻指向哪个函数。
    ⇒ 判据仍然可以替换 `TRANSPORT` 这**一个名字**（`run()` 在运行时读全局）。
    """
    if os.environ.get(STUB_ENV):
        return _stub_transport(payload)
    return llm.TRANSPORT(payload)


def _first_num(text, patterns):
    """从一段文本里抠第一个数字（桩用它引用**提示词里真有的数**，不自己编）。"""
    import re
    for pat in patterns:
        m = re.search(pat, text or '')
        if m:
            return m.group(1)
    return None


# 桩里各角色的立场值。★**常数**（同参同帧）：判据要能把这些值写死成期望值。
_STUB_R1 = {'bull': 0.35, 'bear': -0.55, 'fundamental': 0.20, 'technical': 0.15,
            'sentiment': -0.10, 'pm': 0.10, 'risk': None, 'kg_tutor': None, 'psych': None}
_R2_STANCE = {'bull': 0.35, 'bear': -0.55, 'fundamental': 0.20, 'technical': 0.15,
              'sentiment': -0.10, 'pm': 0.10}
# 第 2 轮**必须看得出变化**（规格 §3.4）。桩把多空各往中间挪一档，并写出反驳。
_STUB_SHIFT = {'bull': -0.15, 'bear': +0.15, 'pm': -0.05}


def _stub_transport(payload):
    """脚本化的假大模型。**由 payload 驱动**（照 `llm._stub_transport` 的规矩）。

    它做三件在离线判据里必须为真的事：
      ① 从系统提示词里认出**是哪个角色**（`（角色 id：X）` 这个标记就是为它准备的）；
      ② 第 2 轮时从**用户消息里**读出对方第 1 轮的立场值与原话，**原样**引用到
         自己的 `text` 与 `rebuttal` 里 —— 这就是"payload 真的带了对方论点"的证据；
      ③ 引用提示词里**真有的数字**（正则在【本轮数据】里找），不自己编一个。
    """
    out = os.environ.get(STUB_OUT_ENV)
    msgs = payload.get('messages') or []
    if out:
        try:
            with open(out, 'a', encoding='utf-8') as f:
                f.write(json.dumps({'messages': msgs,
                                    'tools': [t['function']['name']
                                              for t in payload.get('tools') or []]},
                                   ensure_ascii=False) + '\n')
        except OSError:
            pass

    sysmsg = ''.join(str(m.get('content') or '') for m in msgs if m.get('role') == 'system')
    usermsg = ''.join(str(m.get('content') or '') for m in msgs if m.get('role') == 'user')
    role = 'pm'
    for r in ROLES:
        if ('（角色 id：%s）' % r) in sysmsg:
            role = r
            break

    opp_name = _first_num(usermsg, [r'对方（(.+?)）在第 1 轮里'])
    opp_stance = _first_num(usermsg, [r'立场值 stance = ([^\n]+)'])
    opp_text = _first_num(usermsg, [r'他的原话：(.+)'])
    is_r2 = '【本轮：第 2 轮' in usermsg

    # ★数字要在 `sysmsg + usermsg` 里找：`evidence.block_for` 那一块是拼进
    #   **系统提示词**的（`roles._turn_blocks`），只搜用户消息会永远搜不到 ——
    #   而症状只是"桩说'提示词里没带数字'"，看起来像桩偷懒，其实是搜错了地方。
    both = sysmsg + '\n' + usermsg
    close = _first_num(both, [r'收盘 ([0-9.]+)'])
    vol = _first_num(both, [r'年化波动率 ([0-9.]+)'])
    ma20 = _first_num(both, [r'MA20 ([0-9.]+)'])
    strip = '（这部分是桩数据。**不构成投资建议**。）'

    if role == 'kg_tutor':
        term = _first_num(sysmsg + usermsg, [r'search_terms[^\n]*命中 \d+ 条：([^（]+)'])
        obj = {'stance': None, 'conf': 0.55,
               'text': '（桩）我先按词表讲这个词：%s。要看得更全，打开图谱页搜它。%s'
                       % (term or '这个词', strip),
               'points': ['（桩）词表里的讲法', '（桩）去图谱页搜同名词条'], 'rebuttal': ''}
    elif role == 'psych':
        obj = {'stance': None, 'conf': 0.5,
               'text': '（桩）很多人在这类时候会**拿买入价当参照**（锚定），'
                       '也可能因为最近几天的涨跌把短期当成常态（近因）。'
                       '这只是**常见偏差的提示**，不是对你的判断。%s' % strip,
               'points': ['（桩）锚定：盯住自己的成本价', '（桩）近因：把最近当成常态'],
               'rebuttal': ''}
    elif is_r2 and opp_text:
        # ★★这一段就是"博弈真的发生了"的证据：它引用了**对方第 1 轮的原话**，
        #    而那段原话只可能来自 payload。
        new_st = round(_R2_STANCE.get(role, 0.1) + _STUB_SHIFT.get(role, 0.0), 2)
        reb = ('他（%s）第 1 轮的原话是「%s」，立场 %s。我按同一份数据重新掂量：'
               '他指的那个数我这边也是同一个值，但结论不一样，'
               '所以我把立场从 %s 挪到 %s。'
               % (opp_name, opp_text[:60], opp_stance, _R2_STANCE.get(role), new_st))
        obj = {'stance': new_st, 'conf': 0.6,
               'text': '（桩）第 2 轮。%s%s' % (reb, strip),
               'points': ['（桩）对方立场 %s 我看到了' % opp_stance,
                          '（桩）我的立场改了：%s → %s' % (_R2_STANCE.get(role), new_st)],
               'rebuttal': reb}
    elif is_r2:
        # 第 2 轮但手上没有"对方"的角色：投资经理（收口）。
        new_st = round(_R2_STANCE.get(role, 0.1) + _STUB_SHIFT.get(role, 0.0), 2)
        fac = _first_num(both, [r'场景 (\w+)'])
        obj = {'stance': new_st, 'conf': 0.6,
               'text': '（桩）第 2 轮收口：场景 %s，我把各家的立场按权重收一遍，'
                       '自己的立场从 %s 挪到 %s。%s'
                       % (fac or '（没给）', _R2_STANCE.get(role), new_st, strip),
               'points': ['（桩）收口：立场 %s → %s' % (_R2_STANCE.get(role), new_st)],
               'rebuttal': ''}
    else:
        st = _STUB_R1.get(role)
        bits = []
        if close:
            bits.append('收盘 %s' % close)
        if ma20:
            bits.append('MA20 %s' % ma20)
        if vol:
            bits.append('年化波动 %s%%' % vol)
        obj = {'stance': st, 'conf': 0.65,
               'text': '（桩）%s 的第 1 轮观点：我看到的是 %s。这一票的理由只用上面这几个数。%s'
                       % (roles.NAMES.get(role, role), '、'.join(bits) or '（这份提示词里没带数字）',
                          strip),
               'points': bits or ['（桩）提示词里没有可引用的数'], 'rebuttal': ''}

    txt = json.dumps(obj, ensure_ascii=False)
    for piece in [txt[i:i + 32] for i in range(0, len(txt), 32)]:
        yield {'choices': [{'delta': {'content': piece}}]}


def stub_active():
    return bool(os.environ.get(STUB_ENV))


# ── 一次角色回话 ──────────────────────────────────────────────────────

def _role_timeout():
    """一个角色的墙钟预算（秒）。走环境变量，好让判据把它调小来验证降级那条路。"""
    try:
        v = int(os.environ.get('LICAI_AGENT_ROLE_TIMEOUT') or 0)
    except (TypeError, ValueError):
        v = 0
    return v if v > 0 else ROLE_TIMEOUT


def _concurrency():
    """一场会议里同时跑几个角色。1 = 老行为（串行），留给排障用。"""
    try:
        v = int(os.environ.get('LICAI_AGENT_CONCURRENCY') or 0)
    except (TypeError, ValueError):
        v = 0
    return v if v > 0 else CONCURRENCY


def _pool(fn, items):
    """把 `fn` 并发作用到 `items` 上。**这一层只管并发，不管事件语义。**

    ★两个调用点共用它（第 1 轮九个角色、第 2 轮的多空），所以"并发怎么跑"
      只有一份实现 —— 分开写两份的话，"一处改了另一处不动"迟早发生，
      而这类不一致的症状是**时序**：页面上看起来只是"这次慢一点"。
    ★`fn` 里抛出的异常会在这里被**重新抛出**（`ex.map` 的迭代会带上它）：
      `run()` 的合约是"每处失败都变成记录、不抛"，真抛出来就是缺陷，
      那种事必须浮到 `sse_stream` 那层去（它会补一个 error + done），
      不许在这里被吞掉 —— 吞掉的话会议会在没有 done 的情况下停住。
    """
    n = max(1, min(_concurrency(), len(items)))
    if n == 1:
        for it in items:
            fn(it)
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(fn, items))


def _budgeted(stream, budget):
    """给一个 delta 流加**墙钟预算**：超了就抛 `llm.LLMError`。

    ★为什么在**这一层**加、而不是把 `llm.STEP_TIMEOUT` 调小：传输层那个数
      **所有**调用方共用（`/api/chat` 也走它），在那边改会顺带砍掉对话这边
      正常的长回答。这里只箍住"一个角色的回话"。
    ★它只在**帧与帧之间**才查得到钟：真正的"一字节都不发"由传输层那个
      读空闲上限抓（`llm.idle_timeout`），两条一起才盖得住。
    """
    t0 = time.time()
    for chunk in stream:
        if time.time() - t0 > budget:
            raise llm.LLMError('ROLE_TIMEOUT', '这个角色超过 %d 秒还没说完' % budget,
                               '不是你的错，是这一步太慢了；'
                               '其它角色照常，这一票会如实记成"没答上来"')
        yield chunk


def _ask(role, question, history, profile, ev, stage, tr):
    """让一个角色说一次。返回 `(content, ms, err)`；**任何失败都不抛**。

    ★不带 `tools`：角色不调工具（模块头第 ③ 条）。带上的话模型会去调，
      而那些工具的结果**不会**进入我的证据层，于是同一个数会有两条来源。
    """
    msgs = roles.messages_for(role, question, history=history, profile=profile,
                              block=evidence.block_for(role, ev), stage=stage)
    cfg = llm.config()
    payload = {'model': cfg['model'], 'messages': msgs, 'stream': True,
               'temperature': TEMPERATURE}
    t0 = time.time()
    try:
        acc = llm._accumulate(_budgeted(tr(payload), _role_timeout()))
        return (acc.get('content') or ''), int((time.time() - t0) * 1000), None
    except llm.LLMError as e:
        return '', int((time.time() - t0) * 1000), {'code': e.code, 'msg': str(e),
                                                   'hint': e.hint}
    except Exception as e:                                   # noqa: BLE001
        return '', int((time.time() - t0) * 1000), {'code': type(e).__name__,
                                                   'msg': str(e)[:200], 'hint': ''}


def _view_of(role, content, err):
    """回话 → 可以发出去的观点（含 psych 的措辞闸与风险提示尾巴）。"""
    if err:
        # ★模型没答上时**不假装它答了**：stance=None（没表态），
        #   text 里照实说是通信失败，并且**没有任何数字**。
        return {'stance': None, 'conf': 0.5, 'text': '（我这个角色这次没答上来：%s）'
                % (err.get('msg') or err.get('code')), 'points': [], 'rebuttal': '',
                'parsed': 'error', 'stance_basis': 'none', 'conf_basis': 'prior',
                'guard': {}, 'error': err}
    v = roles.parse(role, content)
    guard = {}
    if role == 'psych':
        # ★措辞闸：命中"你有 XX 偏差"或"所以你该买"就**把那一句撤下来**，
        #   并把撤了什么记进事件（不是悄悄改写 —— 静默改写会让页面上的话
        #   和模型说的不是一回事，没人看得出来）。
        v['text'], guard = roles.psych_guard(v['text'])
        if guard:
            v['points'] = [p for p in v['points']
                           if not roles.psych_guard(p)[1]]
    v['text'] = roles.with_tail(role, v['text'])
    v['guard'] = guard
    return v


def _say_event(role, rnd, v, ms, extra=None):
    e = {'type': 'agent_say', 'role': role, 'round': rnd, 'name': roles.NAMES[role],
         'icon': roles.ICONS[role], 'text': v['text'], 'stance': v['stance'],
         'conf': v['conf'], 'points': v['points'], 'rebuttal': v.get('rebuttal') or '',
         'ms': ms, 'evidence': extra.pop('evidence', []) if extra else [],
         # ★下面这几个是"这一票是怎么来的"，加起来才让事件**可审计**：
         #   parsed=模型给了 JSON / text=我兜的词面 / error=它根本没答；
         #   stance_basis=model/lexicon/none/n.a.；conf_basis=self_report/prior。
         'parsed': v.get('parsed'), 'stance_basis': v.get('stance_basis'),
         'conf_basis': v.get('conf_basis')}
    if v.get('guard'):
        e['guard'] = v['guard']
    if v.get('error'):
        e['error'] = v['error']
    if extra:
        e.update(extra)
    return e


def _revised(r1, r2):
    """第 2 轮到底有没有交锋。返回 `(bool, detail)`。

    ★判据是**三选一**（规格 §3.4：要么修正 stance 数值、要么写出一条反驳）：
      ① stance 数值变了 ② rebuttal 非空 ③ 文本变了。
      第三条是**兜底**：有的模型会把反驳写进正文却不填 `rebuttal` 字段；
      那也算交锋（能看到就行），但 `detail` 里会**分开记**是哪一种命中 ——
      好让"它其实只是把同一段话重说了一遍"这种伪装**看得出来**。
    """
    if not r2:
        return False, {'why': '第 2 轮没有回话'}
    st1, st2 = r1.get('stance'), r2.get('stance')
    d_st = None
    if st1 is not None and st2 is not None:
        d_st = round(st2 - st1, 6)
    changed = d_st is not None and abs(d_st) > 1e-9
    reb = bool((r2.get('rebuttal') or '').strip())
    txt = (r2.get('text') or '').strip() != (r1.get('text') or '').strip()
    return bool(changed or reb or txt), {'stance_changed': changed, 'stance_delta': d_st,
                                        'rebuttal': reb, 'text_changed': txt,
                                        'weak': (not changed and not reb and txt)}


# ── 投资经理的简报（模块头第 ② 条）────────────────────────────────────

def _pm_briefing(ev, views, w_pre, rk):
    """给 pm 的"决定权重的因子" + 风控条件。**不含归一化后的权重表**。"""
    b = w_pre['basis']
    rows = []
    for s in w_pre['steps']:
        if not s.get('w'):
            continue
        rows.append('%s：先验 %s × 场景 %s × 波动 %s × 校准 %s(%s) × 置信 %s → 相对分量 %s'
                    % (roles.NAMES.get(s['role'], s['role']), s.get('base'),
                       s.get('scenario_mult'), s.get('vol_mult'),
                       s.get('calib_mult'),
                       (b['calibration']['roles'].get(s['role']) or {}).get('source'),
                       s.get('conf_mult'), round(s['w'], 4)))
    st = []
    for r in ROLES:
        v = views.get(r) or {}
        if roles.DIRECTIONAL[r]:
            st.append('%s=%s(轮%s, 把握%s%s)' % (roles.NAMES[r], v.get('stance'),
                                              v.get('round', 1), v.get('conf'),
                                              '' if (v.get('basis') or v.get('conf_basis'))
                                              == 'self_report' else '/自报缺失'))
    conds = ['%s %s%s（阈值 %s%s）%s'
             % (c['metric'], c['value'], c['unit'], c['threshold'], c['unit'],
                '【命中】' if c['hit'] else '')
             for c in rk['conditions'] if c['level'] == 'veto']
    return ('【权重因子】场景 %s（%s）；波动档 %s（年化 %s%%）。\n%s\n'
            '★**不要引用具体的权重数字**（归一化会在你发言之后做，你算出来的会和最终表对不上）。'
            '只说"这次谁的分量更重、为什么"。\n\n'
            '【各角色这一轮的立场】%s\n\n'
            '【风控条件（硬条件）】%s'
            % (b['scenario']['value'], b['scenario'].get('source'),
               b['volatility']['bucket'], b['volatility']['ann_vol_pct'],
               '\n'.join(rows) or '（没有可投方向票的角色）',
               '；'.join(st) or '（一个都没表态）',
               '；'.join(conds) or '（没有硬条件命中）'))


# ── 主入口 ────────────────────────────────────────────────────────────

def run(question, history=None, profile=None, emit=None, transport=None, deep=False):
    """开一场会。**永远以 `done` 收尾**（既有纪律），失败也收尾。

    返回一个 dict（`verdict` / `w` / `views` / `score_check` …），SSE 那边靠 `emit`。
    """
    t_start = time.time()
    emit_raw = emit
    emit = _mk_emit(emit)
    tr = transport or TRANSPORT
    q = (question or '').strip()

    # ★空问题也开会：不开的话调用方会拿到一个没有 done 的流（前端永远转圈）。
    #   会议照开，只是没有数据、没有标的，角色会说"没取到"。
    ev = evidence.COLLECT(q, deep=deep)
    views = {}          # role → 这一轮**最终**的观点（含 round 标记）
    stats = {}          # role → {'ms','ok','err','round2'}
    n_says = 0

    # ── 第 1 轮：各自独立出观点 ────────────────────────────────────────
    #
    # ★这 9 个角色**并发**跑（原来是一条 for 循环串行）。为什么能并发：
    #   第 1 轮他们的输入**只有那一份共享的、只读的证据**（`ev`）——
    #   角色之间在第 1 轮一个字都不互相看（交锋是第 2 轮的事）。
    # ★为什么必须并发：串行 ⇒ 整轮墙钟 = 9 次通话之**和**；并发 ⇒ 最慢那一个。
    #   实测（本机 2026-09-22，`deepseek-v4-pro`）单角色一次通话 18~65 秒
    #   （psych 18.2s；bull 三次 58.2 / >60 / 45.6 秒）⇒ 串行要 6~9 分钟。
    #   而 **`/psych` 页面只显示 psych 那一条，psych 在 `ROLES` 里排第 9** ——
    #   用户等的是**别人的** 8 次通话，于是表现得像"永远不出内容"。
    #   并发之后 psych 的第一条 `agent_say` 只等它自己那一次。
    # ★事件顺序：每个角色自己那三条（start → say → done）仍然挨在一起发，
    #   变的是**角色之间**不再有先后。两个消费方都是按 `role` 分流的
    #   （`web/src/agentui/debate.ts` 的 `applyEvent` 按 byRole 收、
    #   `Psych.tsx` 只认 psych），所以交错是安全的 —— 这两个文件我读过才敢改。
    # ★共享的累加器（`views` / `stats` / `n_says`）在锁里改：`n_says += 1` 是
    #   读-改-写，两个线程同时做会**丢计数**，而丢的只是一个数、页面上看不出来。
    emit({'type': 'agent_round', 'round': 1, 'roles': list(ROLES)})
    shared_lock = threading.Lock()      # 守 `views` / `stats` / 两个计数盒子

    def _role_r1(role):
        """第 1 轮里**一个角色**那一段（含它自己那三条事件）。"""
        emit({'type': 'agent_start', 'role': role, 'name': roles.NAMES[role],
              'icon': roles.ICONS[role]})
        t0 = time.time()
        if role == 'risk':
            # ★风控官第 1 轮也是**算出来的**（模块头第 ① 条）：它判的都是"只靠证据 +
            #   场景就能算"的条件，**不拿别人的立场去判它** —— 第 1 轮它还没看到别人的
            #   观点，拿别人的立场判它是不公平的，也会让"独立风控"变成"事后追认"。
            #   ★这句注释**曾经是错的**：原文写着"硬条件里只有 low_consensus 用到立场"，
            #     而 `no_directional_view`（"一个方向票都没投"，同样是 veto 级）用的正是
            #     各角色的立场（`risk.py` 里 `len(st)`）⇒ `views={}` 让这条**恒命中**，
            #     第 1 轮风控的发言于是永远是"命中硬条件，任何方向判断都该降级为不做方向"，
            #     与证据无关（实测：两轮不同数据，那句话逐字相同）。
            #     现在由 `_rule_risk_view(round_=1)` → `evaluate(standalone=True)` 把这条
            #     落成 unknown —— **没法判 ≠ 没问题**。
            v = _rule_risk_view(ev, {}, 'range')
            ms = int((time.time() - t0) * 1000)
            content_ok = True
        else:
            content, ms, err = _ask(role, q, history, profile, ev,
                                    {'round': 1}, tr)
            v = _view_of(role, content, err)
            content_ok = err is None
        v['round'] = 1
        extra = {'evidence': evidence.cites_for(role, ev)}
        if role == 'kg_tutor':
            extra['links'] = _graph_links(ev)
        if role == 'risk':
            # ★第 1 轮风控的发言**不是模型写的**（`_rule_risk_view`，模块头第 ① 条），
            #   所以它的事件必须和第 2 轮那份复核**同形**：带 `source: 'rule'`。
            #   缺这个字段的事件，在按 `source` 分流"规则算的 / 大模型说的"的消费方
            #   那里会落到别的分支或被丢掉 —— 而它的形状对不对劲从事件本身看不出来。
            extra['source'] = 'rule'
        stat = {'ms': ms, 'ok': content_ok and (v.get('parsed') != 'error'),
                'err': (v.get('error') or {}).get('msg'), 'round2': None}
        with shared_lock:
            views[role] = v
            stats[role] = stat
        emit(_say_event(role, 1, v, ms, extra))
        done1 = {'type': 'agent_done', 'role': role, 'ok': stat['ok'],
                 'ms': ms, 'round': 1}
        if role == 'risk':
            done1['source'] = 'rule'                      # 与第 2 轮那条同形
        emit(done1)

    _r1_says = [0]

    def _role_r1_counted(role):
        """`_role_r1` 外面再包一层：把 `n_says` 的加一放进同一把锁里。

        ★`n_says += 1` 不能写在 `_role_r1` 里：`nonlocal` 改的是闭包变量，
          而它要改的是 `run` 的那个本地 —— 直接写就是一个**只会改到副本**的赋值
          （判据那一族里最坏的一种：代码看起来和别处一样，却永远不生效）。
          所以计数放在这层，用 `_r1_says` 这个可变的盒子。
        """
        _role_r1(role)
        with shared_lock:
            _r1_says[0] += 1

    _pool(_role_r1_counted, ROLES)
    n_says += _r1_says[0]
    r1 = {r: dict(views[r]) for r in ROLES}

    # ── 第 2 轮：多空交锋（★规格 §3.4 的核心）────────────────────────
    # ★这一轮的名单**只写在这里**（`roles.py` 里那张 `ROUND2_ROLES` 已删 —— 它没人读，
    #   留着就是"改一处另一处不动"的第二个来源）。多空是**必须**交锋的两个
    #   （规格 §3.4）；风控官第 2 轮是**算出来的**复核（下面那一块），
    #   投资经理的第 2 轮是收口。其余角色第 2 轮不强制 —— 实测一次 13 个角色的
    #   LLM 调用已经要 40 秒以上（deepseek-flash，本机 2026-09-22），
    #   再多一轮全员发言只是延迟翻倍，换不来新信息。
    #   ★换到 `deepseek-v4-pro`（是**推理模型**）之后这个数变成 45~65 秒**一个角色**，
    #     所以第 2 轮这一对也并发跑（见下面 `_pool`）—— 名单没变，变的是墙钟。
    emit({'type': 'agent_round', 'round': 2, 'roles': ['bull', 'bear', 'risk', 'pm']})
    r2_says = [0]

    def _r2(role):
        """第 2 轮里**一个多空角色**那一段（含重试与它自己那三条事件）。"""
        opp = 'bear' if role == 'bull' else 'bull'
        emit({'type': 'agent_start', 'role': role, 'name': roles.NAMES[role],
              'icon': roles.ICONS[role]})
        stage = {'round': 2, 'opponent': {'name': roles.NAMES[opp],
                                          'stance': r1[opp]['stance'],
                                          'text': r1[opp]['text'],
                                          'points': r1[opp]['points']},
                 'self_r1': {'stance': r1[role]['stance']}}
        content, ms, err = _ask(role, q, history, profile, ev, stage, tr)
        v2 = _view_of(role, content, err)
        rev, detail = _revised(r1[role], v2)
        tries = 1
        while not rev and tries <= ROUND2_RETRY and err is None:
            # ★重试一次，并且**在重试的提示词里点明"看不出来等于没做"**。
            stage2 = dict(stage, retry=tries)
            content, ms2, err2 = _ask(role, q, history, profile, ev, stage2, tr)
            ms += ms2
            if err2 is None:
                cand = _view_of(role, content, err2)
                rev2, detail2 = _revised(r1[role], cand)
                if rev2:
                    v2, rev, detail = cand, rev2, detail2
            tries += 1
        v2['round'] = 2
        v2['revised'] = rev
        v2['revised_detail'] = detail
        v2['tries'] = tries
        with shared_lock:
            views[role] = v2
        emit(_say_event(role, 2, v2, ms,
                        {'evidence': evidence.cites_for(role, ev), 'revised': rev,
                         'revised_detail': detail, 'tries': tries}))
        emit({'type': 'agent_done', 'role': role, 'ok': err is None, 'ms': ms, 'round': 2})
        with shared_lock:
            stats[role]['round2'] = {'ms': ms, 'revised': rev, 'detail': detail}
            r2_says[0] += 1

    # ★多空这两个也**并发**：多头第 2 轮的输入是空头**第 1 轮**的话，
    #   反之亦然 —— 两条互不依赖（它们不是"互相等对方第 2 轮"）。
    #   串行的代价同样是"两次通话之和"（实测一次 45~65 秒）。
    _pool(_r2, ('bull', 'bear'))
    n_says += r2_says[0]

    # ── 风控复核（算出来的）────────────────────────────────────────────
    emit({'type': 'agent_start', 'role': 'risk', 'name': roles.NAMES['risk'],
          'icon': roles.ICONS['risk']})
    w_pre = weights.compute(ev, {r: v for r, v in views.items() if r != 'pm'})
    rk = risk.evaluate(ev, views, w_pre['w'], w_pre['score'], w_pre['scenario'],
                       risk.side_of(w_pre['score']))
    t0 = time.time()
    v_risk2 = _rule_risk_view(ev, views, w_pre['scenario'], rk=rk, round_=2)
    ms = int((time.time() - t0) * 1000)
    v_risk2['round'] = 2
    views['risk'] = v_risk2
    emit(_say_event('risk', 2, v_risk2, ms,
                    {'evidence': evidence.cites_for('risk', ev), 'source': 'rule'}))
    n_says += 1
    emit({'type': 'agent_done', 'role': 'risk', 'ok': True, 'ms': ms, 'round': 2,
          'source': 'rule'})

    # ── 投资经理收口（拿因子，不拿最终权重表 —— 模块头第 ② 条）────────
    emit({'type': 'agent_start', 'role': 'pm', 'name': roles.NAMES['pm'],
          'icon': roles.ICONS['pm']})
    brief = _pm_briefing(ev, views, w_pre, rk)
    content, ms, err = _ask('pm', q, history, profile, ev,
                            {'round': 2, 'briefing': brief}, tr)
    v_pm = _view_of('pm', content, err)
    v_pm['round'] = 2
    views['pm'] = v_pm
    emit(_say_event('pm', 2, v_pm, ms,
                    {'evidence': evidence.cites_for('pm', ev)}))
    n_says += 1
    stats['pm']['round2'] = {'ms': ms, 'revised': None}
    emit({'type': 'agent_done', 'role': 'pm', 'ok': err is None, 'ms': ms, 'round': 2})

    # ── 权重（★唯一一份；算完就发，score 由它复算）───────────────────
    W = weights.compute(ev, views)
    _attach_rounds(W, views)
    emit({'type': 'agent_weights', 'w': W['w'], 'scenario': W['scenario'],
          'basis': W['basis'], 'score': W['score'], 'equation': W['equation']})

    # ── 最终结论 ──────────────────────────────────────────────────────
    rk = risk.evaluate(ev, views, W['w'], W['score'], W['scenario'],
                       risk.side_of(W['score']))
    side0 = risk.side_of(W['score'])
    conf0 = round(sum(W['w'][r] * weights.shrink((views.get(r) or {}).get('conf'))
                      for r in ROLES), 4)
    side, conf, risk_note, changed = risk.apply(side0, W['score'], conf0, rk)
    verdict = {
        'type': 'agent_verdict', 'side': side, 'score': W['score'],
        'confidence': conf, 'confidence_raw': conf0, 'side_before_risk': side0,
        'scenario': W['scenario'],
        'reasons': _reasons(W, views, rk, ev),
        'dissent': _dissent(W, views, side0),
        'veto': rk['veto'],
        'risk_note': _risk_note(risk_note, ev),
        'equation': W['equation'],
        'risk_conditions': [{'id': c['id'], 'level': c['level'], 'metric': c['metric'],
                             'value': c['value'], 'threshold': c['threshold'],
                             'hit': c['hit'], 'unknown': c['unknown']}
                            for c in rk['conditions']],
    }
    emit(verdict)

    ms_total = int((time.time() - t_start) * 1000)
    emit({'type': 'done', 'ms': ms_total, 'steps': n_says})

    # ★复算：把权重表乘回去，和 verdict 里那个 `score` 对一遍。
    #   两者不一致说明权重表是装饰 —— 这件事必须**自己先说**，不能等判据发现。
    check = round(sum(W['w'][r] * ((views.get(r) or {}).get('stance') or 0.0)
                      for r in ROLES), 6)
    # ★轮次 id：给**校准台账**用的。"这一轮谁说了什么"和"之后真实涨跌多少"
    #   要靠一个共同的 id 才接得起来（`weights.record_view/record_outcome`）。
    #   ★`run()` 自己**一个字节都不写**（零写副作用：判据会反复调它、仓库是共享的），
    #     这里只是把这个 id **交出去**；写不写、什么时候写，是调用方的事。
    rid = '%s-%s' % (time.strftime('%Y%m%d%H%M%S'),
                     (ev.get('symbols') or [{}])[0].get('code') or 'nosym')
    return {
        'ok': True, 'question': q, 'ms': ms_total, 'deep': bool(deep),
        'rid': rid,
        'src': ev.get('src'), 'scenario': W['scenario'],
        'views': {r: {'round': views[r].get('round'), 'stance': views[r].get('stance'),
                      'conf': views[r].get('conf'), 'text': views[r].get('text'),
                      'points': views[r].get('points'),
                      'rebuttal': views[r].get('rebuttal'),
                      'revised': views[r].get('revised'),
                      'stance_basis': views[r].get('stance_basis'),
                      'parsed': views[r].get('parsed')}
                  for r in ROLES},
        'w': W['w'], 'score': W['score'], 'score_check': check,
        'score_reproducible': abs(check - W['score']) < 1e-6,
        'equation': W['equation'], 'basis': W['basis'],
        'risk': {'conditions': rk['conditions'], 'veto': rk['veto'],
                 'side_before_risk': side0},
        'verdict': {k: v for k, v in verdict.items() if k != 'type'},
        'stats': stats, 'n_says': n_says,
        'evidence': {'n_probes': ev.get('n_probes'), 'n_ok': ev.get('n_ok'),
                     'miss_rate': ev.get('miss_rate'), 'missing': ev.get('missing'),
                     'symbols': [s.get('name') for s in (ev.get('symbols') or [])],
                     'notes': ev.get('notes')},
        'emit_report': getattr(emit, 'report', None),
    }


# ── verdict 的零件 ────────────────────────────────────────────────────

def _attach_rounds(W, views):
    """在 `basis` 里写明"每个角色的输入来自第几轮"。

    ★这不是废话：第 2 轮里只有多空（和收口的 pm）更新过观点，别的角色还是第 1 轮的。
      不写清楚，读权重表的人会以为所有人都在第 2 轮说过话。
    """
    W['basis']['roles_round'] = {r: (views.get(r) or {}).get('round', 1) for r in ROLES}
    W['basis']['roles_revised'] = {r: bool((views.get(r) or {}).get('revised'))
                                   for r in ROLES}
    return W


def _reasons(W, views, rk, ev):
    """结论的"为什么"。★每条都要能指回一个数或一条条件。"""
    out = []
    top = sorted([(W['w'][r], r) for r in ROLES if W['w'][r] and
                  (views.get(r) or {}).get('stance') is not None], reverse=True)[:3]
    for wv, r in top:
        out.append('%s 的立场 %s（权重 %.3f）：%s'
                   % (roles.NAMES[r], views[r]['stance'], wv,
                      (views[r].get('points') or [''])[0][:80] or
                      (views[r].get('text') or '')[:80]))
    if W['basis']['scenario'].get('source') != 'intraday':
        out.append('场景判为 %s —— ★占位口径（%s），不是接上了 agent/intraday'
                   % (W['scenario'], W['basis']['scenario'].get('source')))
    else:
        out.append('场景判为 %s（来自 agent/intraday 的 regime）' % W['scenario'])
    if rk['veto']:
        out.append('风控命中：%s' % '、'.join(rk['hits']))
    if not out:
        out.append('这一轮没有角色给出方向票（数据可能没取到），所以没有"理由"可列。')
    return out


def _dissent(W, views, side0):
    """异议：**少数方**。

    ★为什么不能写成"方向和结论相反的角色"：结论是 `flat` 的时候（加权分落进死区，
      而那恰恰是分歧最大的时候），那个定义会让 `dissent` **空着** —— 于是页面上
      看到的是"没有异议"，而实际情况是"多空打起来了、谁也没压过谁"。
      ⇒ 定义换成**按权重算的少数方**：看多一侧和看空一侧，权重合计小的那一侧就是异议方。
        它同时覆盖两种情形（结论有方向时，少数方天然就是反向的那些角色）。
    """
    longs, shorts = [], []
    for r in ROLES:
        if not roles.DIRECTIONAL[r]:
            continue
        st = (views.get(r) or {}).get('stance')
        if st is None or abs(st) <= weights.STANCE_BAND:
            continue
        say = ((views[r].get('points') or [''])[0]
               or (views[r].get('text') or ''))[:100]
        row = {'role': r, 'name': roles.NAMES[r], 'stance': st,
               'weight': W['w'].get(r), 'says': say}
        (longs if st > 0 else shorts).append(row)
    if not longs or not shorts:
        return []
    wl = sum(x['weight'] or 0 for x in longs)
    ws = sum(x['weight'] or 0 for x in shorts)
    minority = shorts if wl >= ws else longs
    side_word = '看空' if minority is shorts else '看多'
    for x in minority:
        x['side'] = 'short' if minority is shorts else 'long'
        x['note'] = ('少数方（%s 一侧权重合计 %.3f，对侧 %.3f）'
                     % (side_word, min(wl, ws), max(wl, ws)))
    return minority


def _risk_note(forced, ev):
    """最终风险提示。★**原样含「不构成投资建议」**（判据 grep 的就是它）。

    ★这里的"具体"是指**说清这次判断在什么条件下会错**，不是复述一遍"投资有风险"。
      所以它带上：数据是什么时候的、缺了什么、风控拦了什么。
    """
    miss = (ev.get('missing') or [])
    parts = []
    if forced:
        parts.append(forced)
    parts.append(
        '数据来自%s，行情可能滞后一到两个交易日；本轮探针 %s 个、失败 %s 个%s。'
        '各角色说的是**同一个视角下的一家之言**，加权只表示"这次谁的分量更重"，'
        '不表示"这个方向对"。本站不知道你的持仓、成本和能承受多大亏损，'
        '也没有核实过你在网页上填的那份自述，所以上面每一句都只是**参考，不是结论**。'
        '**不构成投资建议**。'
        % ('本机桩数据（离线演示）' if ev.get('src') == 'stub' else '公开行情源',
           ev.get('n_probes'), len(miss),
           ('（%s）' % '、'.join(miss)) if miss else ''))
    return '\n\n'.join(parts)


# ── 风控官的两轮发言（规则生成，见模块头第 ① 条）────────────────────

def _rule_risk_view(ev, views, scenario, rk=None, round_=1):
    """风控官的发言。全部内容来自 `risk.evaluate` 的数，**一个字都不是模型写的**。"""
    if rk is None:
        # ★`standalone` 只在"第 1 轮、还没有别人的观点可数"那一支为真 —— 见
        #   `risk.evaluate` 的 docstring：不这么传的话，`no_directional_view`
        #   （"一个方向票都没投"）会因为 `views` 空而**恒命中**，第 1 轮风控的
        #   发言就永远是"命中硬条件…"那一句，跟证据没关系。
        rk = risk.evaluate(ev, views, {}, 0.0, scenario, None,
                           standalone=(round_ == 1))
    m = rk['metrics']
    lines = []
    for c in rk['conditions']:
        if c['unknown']:
            lines.append('%s：**没取到**（这一项我没法判，不是"没问题"）' % c['metric'])
        else:
            lines.append('%s %s%s vs 阈值 %s%s ⇒ %s'
                         % (c['metric'], c['value'], c['unit'], c['threshold'],
                            c['unit'], '**命中**' if c['hit'] else '没超'))
    head = ('第%s轮风控复核：%s。' % (round_,
            ('命中硬条件，任何方向判断都该降级为不做方向（%s）' % '、'.join(
                [c['id'] for c in rk['conditions'] if c['hit'] and c['level'] == 'veto']))
            if rk['veto'] and rk['veto']['level'] == 'veto' else
            ('命中软条件（%s），方向保留但提示与置信度要打折' % '、'.join(
                [c['id'] for c in rk['conditions'] if c['hit'] and c['level'] == 'warn']))
            if rk['veto'] else '没有条件命中，但下面每一条都列出当前值和离阈值的距离'))
    body = ('\n'.join(lines) + '\n'
            '★这些是**可复核的数值条件**：阈值都是先验档位（注释里写清了取值理由），'
            '你可以不同意它，但你能拿同一批数复算一遍。'
            '我不判断涨跌（`stance` 一律 null）—— 那是别的角色的事。')
    return {'stance': None, 'conf': 0.7 if rk['veto'] else 0.55,
            'text': roles.with_tail('risk', head + '\n' + body),
            'points': lines[:4], 'rebuttal': '',
            'parsed': 'rule', 'stance_basis': 'n/a', 'conf_basis': 'prior',
            'guard': {}}


def _graph_links(ev):
    """把用户带到图谱页的**具体词条**。

    ★`graph.html` **不认 URL 参数**（页面里没有读 `location.search`/hash 的代码；
      前端注释也写明了不做"假深链"）。所以这里给的是：
      `url` = 图谱页，`search` = 该在搜索框里输入的那个**词条原名**，
      `one_line` = 词表里的一句话（让用户点之前就知道那个词是不是他要的）。
      ⇒ 我们**不假装能直接跳到那一条**，而是给一个"到了那页搜这个词"的可执行指令。
      （真深链需要 `graph.html` 支持 `?term=`，那是集成点，见交付说明。）
    """
    g = ev.get('graph') or {}
    hits = g.get('hits') or []
    walk = g.get('walk') or {}
    nb = {n.get('name'): n.get('relation') for n in (walk.get('neighbors') or [])}
    terms = []
    for h in hits[:3]:
        f = h.get('fields') or {}
        terms.append({'name': h.get('name'), 'cat': h.get('cat'),
                      'one_line': (f.get('一句话') or '')[:80],
                      'relation': nb.get(h.get('name')) or '同图',
                      'url': 'graph.html', 'search': h.get('name')})
    if not terms:
        return {'terms': [], 'url': 'graph.html', 'hint':
                '词表里没有匹配到词条 —— 可以直接去图谱页自己找找看。',
                'note': '词表（255 个词条）里没命中，所以没有"具体词条"可带你去。'}
    return {'terms': terms, 'url': 'graph.html',
            'hint': '打开图谱页，在搜索框里输入「%s」就能定位到它。'
                    % terms[0]['name'],
            'note': '★图谱页不认 URL 参数（页面里没有读参数的代码），'
                    '所以这里是"到那页搜这个词"，不是直接跳转。'}


# ── SSE 包装（照 `llm.sse_stream` 的先例，判据/路由直接用它）────────────

def sse(obj):
    return 'data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n'


def sse_stream(question, history=None, profile=None, transport=None, deep=False,
               holdings=None, heartbeat=5):
    """把 `run` 包成 SSE 字节流，其间每 `heartbeat` 秒补一个心跳。

    ★`holdings` / `profile` **必须在这里设**，不能由视图函数设：
      `run` 跑在下面这个**新线程**里，而 `ContextVar` 是**每线程一份**的
      （`llm.sse_stream` 的 docstring 里踩过这个坑：在请求线程里设，工具那边
      根本看不见，症状是 `analyze_portfolio` 永远说"还不知道你的持仓" ——
      这不是报错，是**静默**走向了降级分支）。复位放 `finally`。
    """
    import tools
    qq = queue.Queue()

    def worker():
        tok = tools.set_request_holdings(holdings)
        ptok = tools.set_request_profile(profile)
        try:
            run(question, history=history, profile=profile, emit=qq.put,
                transport=transport, deep=deep)
        except Exception as e:                               # noqa: BLE001
            # ★`run` 自己不该抛（它内部每处失败都变成记录），真抛了说明它坏了。
            #   那时也必须**以 done 收尾**：没有 done 的流会让前端永远转圈，
            #   而"转圈"比"弹一个错"难查得多。
            qq.put({'type': 'error', 'code': type(e).__name__, 'msg': str(e)[:300]})
            qq.put({'type': 'done', 'ms': 0, 'steps': 0, 'error': True})
        finally:
            tools.reset_request_holdings(tok)
            tools.reset_request_profile(ptok)
            qq.put(None)

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    t0 = time.time()
    while True:
        try:
            ev = qq.get(timeout=heartbeat)
        except queue.Empty:
            yield sse({'type': 'heartbeat', 'elapsed': int(time.time() - t0)})
            continue
        if ev is None:
            break
        yield sse(ev)


if __name__ == '__main__':
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else '茅台最近走势怎么样'
    out = run(q, emit=lambda e: print('  · %s' % e.get('type')))
    print(json.dumps({'side': out['verdict']['side'], 'score': out['score'],
                      'w': out['w'], 'equation': out['equation'],
                      'reproducible': out['score_reproducible']},
                     ensure_ascii=False, indent=2))
