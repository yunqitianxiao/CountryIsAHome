#!/usr/bin/env python3
"""导览团：本站的**站内引导**由一个小团来答，不是一个智能体一个人答完。

★ 为什么站内引导要是**多智能体**（而不是把 `siteguide.prompt_block()` 塞给一个模型）：

  ① **目录是分块的，块与块之间没有共同语言。** 页面地图 / 议会 / 工具 / 图谱 /
     策略 / 回测口径 —— 五套完全不同的字段。塞进一份提示词里，模型会把注意力
     摊薄，然后在"某某页在哪"这种最该答准的地方含糊过去。
  ② **「只答目录里有的」这句话，在这里是结构，不是嘱咐。** 每个导览员**物理上**
     只拿到自己那一块（`slices.part`），别人的数字根本没进它的 prompt；
     主持手上**一个数都没有**，只能转述导览员说过的原话。嘱咐是可以不照做的，
     而且不照做的时候看不出来；切片不能。
  ③ **于是"数字从哪来"才有唯一答案，才审得了。** 成稿里每个数都该能指回某位
     导览员那句话，而那句话里的数能拿去目录里查 —— 这就是 `audit.py` 那个
     非 LLM 校对位干的事。一份提示词方案的成稿里，"模型编的数"和"目录里的数"
     本来就分不出来，也就没有任何自动手段能守。
  ④ **它是"多智能体"这个技术槽在站内的第二次落地**，和议会那套是对照组：
     那边是**对抗**（两轮交锋、按表现算权重、风控否决），这边是**分工**
     （各管一块、互不反驳、主持收口、规则校对）。两条路都走通了，
     "多智能体"才不是一个名词而是一个能力。

★ 说清楚**它不是**什么：它不是议会的第二份拷贝，也不并进 `/agent`。
  名册（`roster.py`）、目录切片（`slices.py`）、校对规则（`audit.py`）都是这一站
  自己的东西，只有**工程形状**照议会写 —— 事件顺序、离线桩、并发池、
  `done` 必发。那是纪律，不是设计。

★ 事件顺序（**全部在 `done` 之前**，前端靠这个顺序铺"谁在答"）：

      guide_start
      ├─ 每位导览员：guide_docent_start → guide_say → guide_docent_done
      ├─ guide_host_start
      ├─ token × N            ← 主持的成稿，边想边说（复用既有气泡）
      ├─ guide_host
      ├─ guide_audit          ← 非 LLM，规则算出来的
      └─ done

★ 零写副作用：`run()` 一个字节都不落盘（它会被判据反复调，仓库是共享的）。
"""

import concurrent.futures
import json
import os
import queue
import threading
import time

import llm
import siteguide

from . import audit, roster, slices

__all__ = ['run', 'sse_stream', 'sse', 'TRANSPORT', 'STUB_ENV', 'available']

STUB_ENV = 'LICAI_GUIDE_STUB'
STUB_OUT_ENV = 'LICAI_GUIDE_STUB_OUT'

DOCENT_TEMPERATURE = 0.3
HOST_TEMPERATURE = 0.3

HEARTBEAT = 5


def available():
    """这一趟有没有目录可讲。没有 → 导览员会直说读不到，而不是编。"""
    return siteguide.available()


# ── 事件出口 ──────────────────────────────────────────────────────────
#
# ★照抄议会的 `_mk_emit` 那条纪律：**一个事件发不出去，不能让整场垮掉**。
#   区别只是这里不记"修复了几条 JSON"——导览团的事件都是自己拼的纯 dict，
#   没有"从模型输出里抠 JSON"那一环，所以真出问题只可能是拼了个不可序列化的东西。

def _sanitize(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, dict):
        return {str(k): _sanitize(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_sanitize(x) for x in v]
    return str(v)


def _mk_emit(emit):
    sink = emit or (lambda ev: None)
    rep = {'n': 0, 'bad': []}
    lock = threading.Lock()

    def out(ev):
        try:
            json.dumps(ev, ensure_ascii=False)
        except (TypeError, ValueError):
            with lock:
                rep['bad'].append(ev.get('type') if isinstance(ev, dict) else '?')
            ev = _sanitize(ev)
        with lock:
            rep['n'] += 1
        try:
            sink(ev)
        except Exception:                                          # noqa: BLE001
            # 出口坏了（比如客户端断了）**不许**把整场带下水：剩下的角色照常跑完，
            # 最后一句话仍然是 done。这跟"发不出去就抛"是两种产品。
            pass

    out.report = rep
    return out


# ── 传输 ──────────────────────────────────────────────────────────────
#
# ★这两个名字照议会写，理由也是同一条：`TRANSPORT` 要**每次调用现读**开关。
#   在 import 时读一次的话，`python -m guide` 那种"先 import 父包"的走法会让
#   桩永远不生效 —— 而症状是"桩跑了，但走的是真网络"，烧 token 还看不出来。

def _stub_enabled():
    return bool(os.environ.get(STUB_ENV))


def TRANSPORT(payload):
    """这一趟用哪个传输。★**调用时**读开关，不是 import 时。"""
    if _stub_enabled():
        return _stub_transport(payload)
    return llm.TRANSPORT(payload)


# 桩要认得出"主持那段原话从哪儿开始"。★这个名字**直接引 `roster.LINES_MARK`**，
#   不在两处各写一份：各写一份的话，改了其中一处，桩会静默地退化成
#   "找不到原话 ⇒ 一个数都不引"，症状是"校对位说成稿里 0 个数字"（看着像全对）。
_HOST_LINES_MARK = roster.LINES_MARK

_STUB_TAIL = ('\n\n⚠️ 这段是**离线桩**说的话：它只念目录里分给它的数，'
              '一个数都不自己造 —— 判据靠这个性质来验"编的数会被校对位抓出来"。')


def _stub_text(prompt, limit=2):
    """从一段提示词里挑几个**真实出现过**的数，串成一句话。

    ★桩必须**由 payload 驱动**，不能凭空演：它要是自己造数，
      干净副本上就会永远红，而"红"这件事就再也说明不了任何问题。
    """
    nums = slices.numbers_in(prompt)
    seen, pick = set(), []
    for n in nums:
        if n in seen:
            continue
        seen.add(n)
        pick.append(n)
        if len(pick) >= limit:
            break
    if not pick:
        return '我这一块这次没拿到目录，所以我说不出数来。' + _STUB_TAIL
    return ('按分给我的那块目录：这一块里出现的数是 %s。'
            '我只说目录里有的，别的我这一块看不到。' % '、'.join(pick)) + _STUB_TAIL


def _stub_transport(payload):
    """离线桩：不联网、不花 token，走的是同一套真实代码路径。

    ★它认角色靠系统提示词里那两行抬头（`（导览员 id：X）` / `（角色 id：host）`），
      和议会那边一个路子 —— 桩认错了角色，判据就会在**桩自己的错**上打转。
    """
    out = os.environ.get(STUB_OUT_ENV)
    msgs = payload.get('messages') or []
    sysmsg = ''
    for m in msgs:
        if m.get('role') == 'system':
            sysmsg = m.get('content') or ''
            break
    user = ''
    for m in msgs:
        if m.get('role') == 'user':
            user = m.get('content') or ''
    if out:
        with open(out, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'system': sysmsg, 'user': user}, ensure_ascii=False) + '\n')

    role = None
    for r in roster.DOCENTS:
        if ('（导览员 id：%s）' % r) in sysmsg:
            role = r
            break
    if role is None and ('（角色 id：%s）' % roster.HOST) in sysmsg:
        role = roster.HOST

    if role == roster.HOST:
        # ★主持手上一个数都没有（除了导览员那几句原话），所以它只能从**原话那一段**
        #   里取数 —— 从别处取就是越权。取法上把这条落实成"切那一段、只在里面找"，
        #   而不是"在整份提示词里找"：后者会把定位块/纪律里的数也当自己的素材，
        #   而那正是主持编数的起点。
        seg = (sysmsg.split(_HOST_LINES_MARK)[-1] if _HOST_LINES_MARK in sysmsg else '')
        txt = ('这几位导览员分头看了一遍，合起来是这样：'
               + _stub_text(seg, 2).split('\n')[0]
               + '\n\n以上每一句都出自某位导览员，我没有自己加数。不构成投资建议。')
    elif role:
        block = sysmsg.split('【分给你的目录】')
        seg = block[1] if len(block) > 1 else sysmsg
        txt = '%s：%s' % (roster.NAMES[role], _stub_text(seg, 2))
    else:
        txt = '（桩：认不出这一趟是谁在说话）'

    for piece in [txt[i:i + 24] for i in range(0, len(txt), 24)]:
        yield {'choices': [{'delta': {'content': piece}}]}


# ── 并发 ──────────────────────────────────────────────────────────────

def _concurrency():
    try:
        n = int(os.environ.get('LICAI_GUIDE_CONCURRENCY') or 0)
    except ValueError:
        n = 0
    if n > 0:
        return n
    return min(len(roster.DOCENTS), 6)


def _pool(fn, items):
    """并发跑。★与议会那条同一个理由：串行的代价是"几次通话之和"。

    ★兜底照旧交给调用方（`run` 的合同是"绝不抛"）：这里不做 try/except，
      让 `fn` 自己把失败变成事件 —— 在这里吞掉的话，"哪个角色挂了"就没人知道了。
    """
    items = list(items)
    if not items:
        return
    n = min(_concurrency(), len(items))
    if n <= 1:
        for x in items:
            fn(x)
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(fn, items))


# ── 两趟调用 ──────────────────────────────────────────────────────────
#
# ★导览员和主持**都没有 tools** —— 这不是省事，是这个团的前提：
#   它们只能看目录里分给它们的那一块，别的什么都不许去取。
#   手上没有工具 = "我再去查一下"这条路物理上不存在。
#   ★超时不用在这里再包一层：`llm._real_transport` 自己带 idle/step 超时，
#     到点会抛 `llm.LLMError`，被下面接住变成"这一位没说出话来"。

def _ask_docent(role, question, history, block, tr):
    msgs = roster.docent_messages(role, question, history=history, block=block)
    cfg = llm.config()
    payload = {'model': cfg['model'], 'messages': msgs, 'stream': True,
               'temperature': DOCENT_TEMPERATURE}
    acc = llm._accumulate(tr(payload))
    return (acc.get('content') or '').strip()


def _host_say(question, history, lines, overview, emit, tr):
    """主持这一趟。★**流式**：它的正文直接以 `token` 事件出去。

    为什么要流：成稿是一段两三句的答案，前面已经等了六位导览员，
    再让用户对着空气等一次完整生成，界面上"正在读站点目录"就一直挂着。
    `token` 也正好复用既有那条气泡（`chat.ts` 的 reducer）——
    所以**前端一个字都不用为正文改**。
    """
    msgs = roster.host_messages(question, history=history, lines=lines, overview=overview)
    cfg = llm.config()
    payload = {'model': cfg['model'], 'messages': msgs, 'stream': True,
               'temperature': HOST_TEMPERATURE}
    parts, t0 = [], time.time()
    try:
        for chunk in tr(payload):
            for ch in chunk.get('choices') or []:
                piece = (ch.get('delta') or {}).get('content')
                if piece:
                    parts.append(piece)
                    emit({'type': 'token', 'text': piece})
    except llm.LLMError as e:
        return (''.join(parts), int((time.time() - t0) * 1000),
                {'code': e.code, 'msg': str(e), 'hint': e.hint})
    except Exception as e:                                         # noqa: BLE001
        return (''.join(parts), int((time.time() - t0) * 1000),
                {'code': type(e).__name__, 'msg': str(e)[:300]})
    return ''.join(parts), int((time.time() - t0) * 1000), None


# ── 编排 ──────────────────────────────────────────────────────────────

def run(question, history=None, profile=None, emit=None, transport=None):
    """跑一趟导览。emit(dict) 往外推事件。

    ★合同（与议会那条一样硬）：**绝不抛异常，最后一定是 `done`**。
      这是一条流式接口 —— 中途抛掉的话，用户那边是"页面永远在等"，
      而服务端日志里只有一条 traceback，两者对不上。
    """
    emit = _mk_emit(emit)
    tr = transport or TRANSPORT
    t_start = time.time()
    q = (question or '').strip()

    d = siteguide.raw()
    emit({'type': 'guide_start', 'roles': list(roster.DOCENTS),
          'host': roster.HOST, 'audit': roster.AUDIT,
          'catalog': bool(d), 'n_docents': len(roster.DOCENTS)})

    lock = threading.Lock()
    says, stats = {}, {}
    n_says = [0]

    def _docent(role):
        emit({'type': 'guide_docent_start', 'role': role,
              'name': roster.NAMES[role], 'icon': roster.ICONS[role]})
        t0 = time.time()
        err, txt = None, ''
        try:
            txt = _ask_docent(role, q, history, slices.text_of(d, role), tr)
        except Exception as e:                                     # noqa: BLE001
            err = {'code': getattr(e, 'code', type(e).__name__), 'msg': str(e)[:300]}
        ms = int((time.time() - t0) * 1000)
        with lock:
            says[role] = txt
            stats[role] = {'ms': ms, 'ok': err is None, 'chars': len(txt)}
            n_says[0] += 1
        emit({'type': 'guide_say', 'role': role, 'name': roster.NAMES[role],
              'icon': roster.ICONS[role], 'text': txt, 'ms': ms,
              'ok': err is None, 'error': err,
              'sources': slices.sources_of(role)})
        emit({'type': 'guide_docent_done', 'role': role, 'ok': err is None, 'ms': ms})

    try:
        _pool(_docent, roster.DOCENTS)
    except Exception as e:                                         # noqa: BLE001
        # 并发池自己炸了（极少见）。**不抛**：主持照常收口，只是手上少几句话。
        emit({'type': 'error', 'code': 'DOCENT_POOL', 'msg': '导览员那一层挂了：%s'
              % str(e)[:200]})

    # ── 主持收口 ──────────────────────────────────────────────────────
    emit({'type': 'guide_host_start', 'role': roster.HOST,
          'name': roster.NAMES[roster.HOST], 'icon': roster.ICONS[roster.HOST]})
    lines = [{'role': r, 'name': roster.NAMES[r], 'text': says.get(r) or ''}
             for r in roster.DOCENTS]
    text, ms, err = _host_say(q, history, lines, slices.overview_text(d), emit, tr)
    emit({'type': 'guide_host', 'role': roster.HOST, 'name': roster.NAMES[roster.HOST],
          'icon': roster.ICONS[roster.HOST], 'text': text, 'ms': ms,
          'ok': err is None, 'error': err})
    n_says[0] += 1

    # ── 校对位（非 LLM，算出来的）──────────────────────────────────────
    a = audit.check(text, d, extra=slices.numbers_in(q))
    emit({'type': 'guide_audit', 'role': roster.AUDIT, 'name': roster.NAMES[roster.AUDIT],
          'icon': roster.ICONS[roster.AUDIT], 'text': audit.line(a), **a})

    ms_total = int((time.time() - t_start) * 1000)
    emit({'type': 'done', 'ms': ms_total, 'steps': n_says[0]})

    return {
        'ok': True, 'question': q, 'ms': ms_total,
        'catalog': bool(d), 'n_says': n_says[0],
        'docents': {r: {'text': says.get(r) or '', **stats.get(r, {})}
                    for r in roster.DOCENTS},
        'host': {'text': text, 'ms': ms, 'ok': err is None, 'error': err},
        'audit': a,
        'emit_report': getattr(emit, 'report', None),
    }


# ── SSE ───────────────────────────────────────────────────────────────

def sse(obj):
    return 'data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n'


def sse_stream(question, history=None, profile=None, transport=None,
               heartbeat=HEARTBEAT):
    """把 `run` 包成 SSE 字节流，期间每 `heartbeat` 秒补一个心跳。

    ★`run` 跑在下面这个**新线程**里（阻塞的编排不能占住请求线程）。
    ★兜底与议会同一条：`run` 万一还是抛了，这里补一个 `error` **再补一个 `done`** ——
      前端认的是 `done`，少了它页面就永远在"正在读站点目录"。
    """
    q = queue.Queue()
    err = {}

    def worker():
        try:
            run(question, history=history, profile=profile, emit=q.put, transport=transport)
        except Exception as e:                                     # noqa: BLE001
            err['e'] = e
            q.put({'type': 'error', 'code': type(e).__name__, 'msg': str(e)[:300]})
            q.put({'type': 'done', 'ms': 0, 'steps': 0, 'degraded': True})
        finally:
            q.put(None)

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    t0 = time.time()
    while True:
        try:
            ev = q.get(timeout=heartbeat)
        except queue.Empty:
            yield sse({'type': 'heartbeat', 'elapsed': int(time.time() - t0)})
            continue
        if ev is None:
            break
        yield sse(ev)


def _main():
    import sys
    os.environ.setdefault(STUB_ENV, '1')          # 命令行自看一律走桩，不烧 token
    qs = [a for a in sys.argv[1:] if not a.startswith('-')] or ['这个站一共有几页？']
    for q in qs:
        print('=' * 72)
        print('问：%s' % q)
        print('=' * 72)
        out = []
        r = run(q, emit=out.append)
        for ev in out:
            t = ev.get('type')
            if t == 'guide_say':
                print('  [%s] %s（%sms，%s）' % (t, ev['name'], ev['ms'],
                                                '说了' if ev['ok'] else '没说'))
                print('      %s' % (ev['text'] or '（空）').replace('\n', ' '))
            elif t == 'guide_host':
                print('  [%s] %s（%sms）' % (t, ev['name'], ev['ms']))
                print('      %s' % (ev['text'] or '（空）').replace('\n', ' '))
            elif t == 'guide_audit':
                print('  [%s] %s' % (t, ev.get('text')))
            elif t == 'token':
                pass
            else:
                print('  [%s] %s' % (t, {k: v for k, v in ev.items() if k != 'type'}))
        print('  → 事件 %d 条，audit.ok=%s，unsourced=%s'
              % (len(out), r['audit']['ok'], r['audit']['unsourced']))


if __name__ == '__main__':
    _main()
