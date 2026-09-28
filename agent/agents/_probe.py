"""冒烟 + 自测：把一场会**真的跑一遍**，然后拿它的真实事件流做断言。

两种用法：

    LICAI_AGENTS_STUB=1 .venv/bin/python -m agents._probe            # 冒烟报告
    LICAI_AGENTS_STUB=1 .venv/bin/python -m agents._probe --selftest # 判据自测

## 冒烟报告打印什么（对应交付说明里要求的那三样）

  ① **第 2 轮多空 payload 真的带了对方第 1 轮的论点** —— 不是打印一句"我看过了"，
     而是拿**两个来源**对齐：`agent_say(bear, round=1)` 那个事件里的 stance 与正文，
     ⟷ `LICAI_AGENTS_STUB_OUT` 里给 bull 的**那份第 2 轮 payload 的原文**。
     后者是"真的发出去的字节"，所以它里面出现前者的内容 = payload 真的带了。
  ② **权重表 + basis**（三个因素各自的数值依据，含"哪一项是占位/先验"）。
  ③ **最终 verdict + score 的复算式**（把权重表乘回去，比对同一个数）。

## `--selftest` 在做什么

每条判据都**同时**跑两遍：干净副本上必须**绿**，注入之后必须**红**。
只报"注入能打红"是不够的（判据自己写坏了也会报"成功打红"）；只报"干净时是绿的"
同样不够（那可能是一条恒绿的装饰）。**两条都要**。

★注入必须**真的够得着**被测代码：这里全部注入 `agents` / `roles` / `weights` / `risk`
  这几个**模块对象上的属性**，而 `run()` 是运行时按名字去模块里取的
  （没有在 import 时把函数绑成局部常量）—— 所以注入一定生效。
  先例：`verify_agent.py` 里"注入够不着被测代码"那一族坑。
"""

import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager

# 这一行不只是为了好看：`_probe` 要在**没设桩环境变量**时也能跑（否则谁忘了设
# 就会去打真网络、烧真 token），所以它自己兜底设上。判据跑它时也一样。
os.environ.setdefault('LICAI_AGENTS_STUB', '1')

# ★包内 import 一律**相对**（先例 `agent/news/__init__.py:44`）；`agent/` 在 sys.path
#   上，但 `agent/agents/` 不是，所以 `import evidence` 会去找一个不存在的顶层模块。
#   用 `python -m agents._probe` 跑（`-m` 下相对 import 才成立）。
from . import evidence, risk, roles, weights               # noqa: E402

# 本包自己，**作为模块对象**拿在手上：下面要往它身上注入（改 `_STUB_R1`、
# 改 `_graph_links`），而"改属性"必须改在**同一个模块对象**上 ——
# `from . import run` 拿到的是函数对象的副本引用，改模块属性对它无效。
agents = sys.modules[__package__]                          # noqa: E402

TMP = tempfile.mkdtemp(prefix='agents-probe-')
PAYLOAD_PATH = os.path.join(TMP, 'payloads.jsonl')
LEDGER_PATH = os.path.join(TMP, 'roles.jsonl')
os.environ['LICAI_AGENTS_STUB_OUT'] = PAYLOAD_PATH
os.environ['LICAI_AGENT_LEDGER'] = LEDGER_PATH      # ★绝不碰 data/ 里的真台账


@contextmanager
def env(**kv):
    """临时改环境变量（`None` = 删掉），退出时**原样还原**。

    ★还原必须还原成"原来有没有这个变量"，不是"设成空串" ——
      `os.environ.get('X')` 对"空串"和"没有"都会给出假值，看起来一样，
      但下游如果有 `if 'X' in os.environ` 就会分叉。这种差别最难查。
    """
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def fault(*pairs):
    """临时替换几个模块属性。`pairs` = (模块对象, '属性名', 新值)。

    ★只换**属性**，不换文件内容：文件级的注入（`open(path,'w')` 那种）会改到
      共享仓库的工作区，而这里是多人并行的仓库 —— 一次自测就能把别人的写入抹掉
      （先例：整份备份写回抹掉并发写）。**属性注入没有这个风险。**
    ★两种写法都收：一次改一处 `fault(mod, 'attr', val)`，或多处一起改
      `fault((m1,'a',v1), (m2,'b',v2))`；后者在判据表里是一个值、更好读。
      （有的守卫要三处一起改才够得着被测代码，缺一处就是"注入够不着"。）
    """
    if len(pairs) == 1 and isinstance(pairs[0], (list, tuple)) \
            and pairs[0] and isinstance(pairs[0][0], (list, tuple)):
        pairs = tuple(pairs[0])
    old = [(obj, name, getattr(obj, name)) for obj, name, _ in pairs]
    try:
        for obj, name, val in pairs:
            setattr(obj, name, val)
        yield
    finally:
        for obj, name, val in old:
            setattr(obj, name, val)


# ── 跑一场会 ──────────────────────────────────────────────────────────

def one_run(question='茅台最近走势怎么样', transport=None, **kw):
    evs = []
    out = agents.run(question, emit=evs.append, transport=transport, **kw)
    return evs, out


def by_type(evs, t):
    return [e for e in evs if e.get('type') == t]


def read_payloads():
    if not os.path.exists(PAYLOAD_PATH):
        return []
    with open(PAYLOAD_PATH, encoding='utf-8') as f:
        return [json.loads(x) for x in f if x.strip()]


def _role_of(payload):
    for m in payload.get('messages') or []:
        if m.get('role') != 'system':
            continue
        for r in roles.ROLES:
            if ('（角色 id：%s）' % r) in (m.get('content') or ''):
                return r
    return None


def _user_of(payload):
    return '\n'.join(m.get('content') or '' for m in payload.get('messages') or []
                     if m.get('role') == 'user')


# ── 判据 ──────────────────────────────────────────────────────────────
#
# 每条：`check()` 返回 (ok, detail)。`inject` 是"把它打红"的那个改动。

def check_event_stream():
    """事件流完整：每类事件至少一个、**以 done 收尾**、第 1 轮 9 个角色都发过言。

    ★"以 done 收尾"是既有纪律（前端和判据都靠它判断"这一轮说完了"）。
      在这里而不是别处守它，是因为 `run()` 的**所有失败路径**都在这一层；
      漏一条出口就够让前端永远转圈。
    """
    evs, out = one_run()
    kinds = [e['type'] for e in evs]
    need = ['agent_round', 'agent_start', 'agent_say', 'agent_done',
            'agent_weights', 'agent_verdict', 'done']
    miss = [k for k in need if k not in kinds]
    r1 = [e['role'] for e in by_type(evs, 'agent_say') if e.get('round') == 1]
    detail = {'kinds': len(kinds), 'last': kinds[-1] if kinds else None,
              'rounds': [e['round'] for e in by_type(evs, 'agent_round')],
              'r1_roles': len(r1), 'missing_kinds': miss}
    ok = (not miss and kinds and kinds[-1] == 'done'
          and len(r1) == len(roles.ROLES) and sorted(r1) == sorted(roles.ROLES))
    return ok, detail


def check_debate_payload():
    """★核心：第 2 轮多空拿到的 payload 里**真的**有对方第 1 轮的立场值与原话。

    对齐的是两个独立来源：`agent_say` 事件（第 1 轮空头说了什么）
    ⟷ 发出去的 payload 原文（给多头的第 2 轮）。两边一致才算数。
    """
    if os.path.exists(PAYLOAD_PATH):
        os.remove(PAYLOAD_PATH)                 # 只看这一趟的 payload
    evs, out = one_run()
    pays = read_payloads()
    r1 = {e['role']: e for e in by_type(evs, 'agent_say') if e.get('round') == 1}
    found, problems = {}, []
    for role, opp in (('bull', 'bear'), ('bear', 'bull')):
        cand = [p for p in pays if _role_of(p) == role
                and '【本轮：第 2 轮' in _user_of(p)]
        if not cand:
            problems.append('%s 第 2 轮的 payload 没找到' % role)
            continue
        u = _user_of(cand[-1])
        st = str(r1[opp]['stance'])
        # 原话取一段"只可能来自对方第 1 轮"的子串（跳过前缀，避开通用套话）
        txt = (r1[opp]['text'] or '').strip()
        frag = txt[12:42] if len(txt) > 45 else txt
        has_st = ('立场值 stance = %s' % st) in u
        has_txt = bool(frag) and frag in u
        found[role] = {'opponent': opp, 'opp_stance': st, 'stance_in_payload': has_st,
                       'text_in_payload': has_txt, 'frag': frag,
                       'rebuttal_quotes': bool(r1[opp]['stance'] is not None and
                                               st in (out['views'][role].get('rebuttal') or ''))}
        if not (has_st and has_txt):
            problems.append('%s 的 payload 里缺 %s 的第 1 轮内容（stance=%s, text=%s）'
                            % (role, opp, has_st, has_txt))
    revised = {r: out['views'][r].get('revised') for r in ('bull', 'bear')}
    if not all(revised.values()):
        problems.append('多空第 2 轮 revised 不全是 True：%s' % revised)
    return (not problems), {'found': found, 'revised': revised, 'problems': problems}


def check_weights_normalized():
    """权重表：9 个角色**全在**、正好加到 1、三个不投方向票的是 0、basis 三因素齐。"""
    evs, out = one_run()
    w = out['w']
    s = sum(w.values())
    b = out['basis']
    zero_ok = all(w.get(r) == 0.0 for r in ('risk', 'kg_tutor', 'psych'))
    fac_ok = all(k in b for k in ('scenario', 'volatility', 'calibration', 'confidence'))
    # 每个因素都要有**数值依据**（不是只有一句说明）。★场景那一条的依据**两条路不同**：
    #   占位口径给的是均线偏离 / 布林带宽 / 年化波动，接上 intraday 之后给的是
    #   regime（分周期）。写成"必须有 ma_gap_pct"就会在 `LICAI_INTRADAY_SYNTH=1`
    #   下**假红** —— 那是判据把一条路钉死成唯一的路（判据会空转的另一种形态）。
    sci = str(b['scenario'].get('source') or '')
    sc_num = (bool(b['scenario'].get('regime')) if sci.startswith('intraday')
              else b['scenario'].get('ma_gap_pct') is not None)
    num_ok = (b['volatility'].get('ann_vol_pct') is not None and sc_num
              and b['calibration']['roles']['bull'].get('shrunk') is not None
              and b['confidence']['roles']['bull'].get('shrunk') is not None)
    detail = {'sum': round(s, 8), 'keys': len(w), 'zero_ok': zero_ok,
              'factors': fac_ok, 'numeric_basis': num_ok, 'scenario_numeric': sc_num,
              'scenario_source': b['scenario'].get('source'),
              'calib_source': b['calibration']['roles']['bull'].get('source')}
    return (abs(s - 1.0) < 1e-6 and len(w) == len(roles.ROLES) and zero_ok
            and fac_ok and num_ok), detail


def check_score_reproducible():
    """★`score` 必须是权重表乘出来的：拿事件里那份 `w` 自己乘一遍，比对 verdict 的 score。"""
    evs, out = one_run()
    ev_w = by_type(evs, 'agent_weights')[0]
    v = by_type(evs, 'agent_verdict')[0]
    st = {e['role']: e['stance'] for e in by_type(evs, 'agent_say')
          if e.get('round') == out['views'].get(e['role'], {}).get('round')}
    recomputed = round(sum(ev_w['w'][r] * (st.get(r) or 0.0) for r in roles.ROLES), 6)
    ok = (abs(recomputed - v['score']) < 1e-6
          and abs(out['score_check'] - out['score']) < 1e-6
          and out['score_reproducible'] is True)
    return ok, {'from_event': recomputed, 'verdict': v['score'],
                'score_check': out['score_check'], 'equation': v['equation']}


def check_risk_note():
    """★每一条**方向性**发言、以及最终 verdict，都必须**原样含「不构成投资建议」**。

    ★为什么两处都要：判据可能 grep 任何一处（事件流里的发言，或 verdict 的 risk_note），
      而"某一层以为另一层补上了"正是这条规矩最常见的漏法。
    """
    evs, out = one_run()
    says = by_type(evs, 'agent_say')
    bad = [e['role'] for e in says
           if e['role'] in roles.NEEDS_TAIL and '不构成投资建议' not in (e['text'] or '')]
    v = by_type(evs, 'agent_verdict')[0]
    ok = (not bad) and ('不构成投资建议' in (v['risk_note'] or ''))
    return ok, {'says_missing': bad, 'n_says': len(says),
                'verdict_note_has': '不构成投资建议' in (v['risk_note'] or '')}


def check_psych_wording():
    """★心理角色的话里**不许**出现"你有 XX 偏差""所以你该买"。

    这一条要**故意喂它一段越界的话**才测得到（桩默认不越界）：
    用一个会吐诊断措辞的假传输跑一遍，然后查输出 —— 闸没生效就红。
    """
    def bad_transport(payload):
        yield {'choices': [{'delta': {'content': json.dumps(
            {'stance': None, 'conf': 0.8, 'points': ['你有损失厌恶偏差'],
             'text': '你有损失厌恶偏差，所以建议你现在加仓摊低成本。',
             'rebuttal': ''}, ensure_ascii=False)}}]}
    evs, out = one_run(transport=bad_transport)
    e = [x for x in by_type(evs, 'agent_say') if x['role'] == 'psych'][0]
    txt = e['text'] or ''
    hit = ('你有' in txt and '偏差' in txt) or '建议你现在加仓' in txt
    return (not hit), {'psych_text': txt[:120], 'guard': e.get('guard'),
                       'leaked': hit}


def check_prompts_fresh():
    """★提示词每轮现拼，**绝不缓存**。两个不同的 block 必须进到系统提示词里。

    ★这条盯的是 `llm.live_context` 那条纪律（"会过期的东西绝不能进常量"）。
      它不生搬一个"有没有 lru_cache"的检查 —— 那种检查只能发现一种写法；
      这里量的是**行为**：换了输入，提示词跟着变。
    """
    a = roles.messages_for('bull', 'q', block='BLOCK-AAA')[0]
    b = roles.messages_for('bull', 'q', block='BLOCK-BBB')[0]
    sa, sb = a['content'], b['content']
    ok = ('BLOCK-AAA' in sa) and ('BLOCK-BBB' in sb) and (sa != sb)
    return ok, {'a_has': 'BLOCK-AAA' in sa, 'b_has': 'BLOCK-BBB' in sb,
                'differ': sa != sb, 'len_a': len(sa), 'len_b': len(sb)}


def check_shrink_neutral():
    """★收缩的先验必须是**中性**的：`n=0` ⇒ 0.5。

    ★这不是个算术细节：`(0+2)/(0+2)=1.0` 会让空台账被读成"这个角色 100% 准"，
      而症状是"新装的站一上来就把权重全给了某个角色"，且完全看不出是这里错的。
      （这条在写 `weights.py` 时**真的犯过**，断言就是那时候加的。）
    """
    z = weights._shrink_rate(0, 0)
    mid = weights._shrink_rate(50, 100)
    hi = weights._shrink_rate(20, 20)
    ok = (z == 0.5) and abs(mid - 0.5) < 1e-9 and hi > 0.8
    return ok, {'n0': z, 'half': mid, 'perfect20': hi,
                'prior': [weights.PRIOR_K, weights.PRIOR_N]}


def check_veto_effective():
    """★风控否决**真的改变了结论**（不是只写进一个字段）。

    需要一个"本来有方向 + 命中硬条件"的场景：用强立场桩 + 丢掉大部分探针
    （`LICAI_AGENTS_STUB_DROP`）逼出 `data_gap`。
    """
    strong = {'bull': 0.85, 'bear': -0.15, 'fundamental': 0.7,
              'technical': 0.6, 'sentiment': 0.5, 'pm': 0.6}
    with fault((agents, '_STUB_R1', dict(strong)),
               (agents, '_STUB_SHIFT', {'bull': -0.05, 'bear': 0.05, 'pm': -0.05}),):
        with env(LICAI_AGENTS_STUB_DROP='get_kline,get_fundamentals,get_news'):
            evs, out = one_run()
    v = by_type(evs, 'agent_verdict')[0]
    veto = v['veto'] or {}
    ok = (bool(veto) and v['side'] == 'flat'
          and v.get('side_before_risk') not in (None, 'flat')
          and 'data_gap' in (veto.get('hard') or [])
          and '不构成投资建议' in (v['risk_note'] or ''))
    return ok, {'side': v['side'], 'side_before_risk': v.get('side_before_risk'),
                'veto_level': veto.get('level'), 'hard': veto.get('hard'),
                'score': v['score'], 'risk_note_has_note': bool(v['risk_note'])}


def check_calibration_moves_weights():
    """★历史校准**真的会动权重**（而不是永远走先验那条路）。

    做法：往临时台账里灌够样本（`MIN_SAMPLES` 条），一个角色几乎全对、另一个几乎全错，
    然后看它的 `calib_mult` 是否偏离 1.0、归一化权重是否跟着变。
    ★这条是**行为**检查，不是"有没有读台账"检查 —— 后者读一眼就知道，
      而"读了但没用上"（乘数写错、恒返回 1.0）只有跑出来才看得见。
    """
    p = os.path.join(TMP, 'calib.jsonl')
    if os.path.exists(p):
        os.remove(p)
    n = weights.MIN_SAMPLES + 6
    import ledger
    for i in range(n):
        rid = 'v%d' % i
        # bull 说多，行情真的涨；bear 说空，行情也真的涨（它一直错）
        ledger.append({'kind': 'view', 'id': rid, 'role': 'bull',
                       'stance': 0.6, 'conf': 0.7}, p=p)
        ledger.append({'kind': 'view', 'id': rid + 'b', 'role': 'bear',
                       'stance': -0.6, 'conf': 0.7}, p=p)
        ledger.append({'kind': 'outcome', 'id': rid, 'pct': 2.0}, p=p)
        ledger.append({'kind': 'outcome', 'id': rid + 'b', 'pct': 2.0}, p=p)
    ev = evidence.COLLECT('茅台')
    views = {r: {'stance': 0.3, 'conf': 0.7} for r in roles.ROLES}
    with env(LICAI_AGENT_LEDGER=p):
        cal, meta = weights.calibration()
        w_cal = weights.compute(ev, views)['w']
    with env(LICAI_AGENT_LEDGER=os.path.join(TMP, 'empty.jsonl')):
        cal0, _ = weights.calibration()
        w_prior = weights.compute(ev, views)['w']
    moved = abs(w_cal['bull'] - w_prior['bull']) > 1e-6
    ok = (cal['bull']['source'] == 'ledger' and cal['bear']['source'] == 'ledger'
          and cal['bull']['factor'] > 1.0 and cal['bear']['factor'] < 1.0
          and cal0['bull']['source'] == 'prior' and cal0['bull']['factor'] == 1.0
          and moved)
    return ok, {'bull': [cal['bull']['n'], cal['bull']['hits'], cal['bull']['factor']],
                'bear': [cal['bear']['n'], cal['bear']['hits'], cal['bear']['factor']],
                'prior_source': cal0['bull']['source'],
                'w_bull_cal': w_cal['bull'], 'w_bull_prior': w_prior['bull'],
                'moved': moved, 'meta': meta.get('scored')}


def _intra_env_names():
    """intraday 的两个离线开关名。★**现取**（拿不到就用发布时的名字兜底）。

    A2 的 `agent/intraday/` 和我并行在改，名字可能变；这里不写死一个可能过期的常量，
    但也**不猜**：拿不到就退回它模块头写明的那个名字，并在报告里照实说。
    """
    try:
        import intraday
        return (getattr(intraday, 'SYNTH_ENV', 'LICAI_INTRADAY_SYNTH'),
                getattr(intraday, 'SYNTH_SCENARIO_ENV', 'LICAI_INTRADAY_SCENARIO'))
    except Exception:                                        # noqa: BLE001
        return 'LICAI_INTRADAY_SYNTH', 'LICAI_INTRADAY_SCENARIO'


Q_SYM = '贵州茅台 600519 现在能不能买'


def _fake_intra_success(s0):
    """注入用：假装 intraday 真调通了并给了一个 regime。"""
    return 'trend_up', {'source': 'intraday', 'regime': {'5': 'trend_up'},
                        'bars': {'5': 48}, 'chosen': 'trend_up', 'ts': 'x',
                        'note': '注入的假结果'}


def _fake_live_injection():
    """注入用：**把这一轮伪装成"在真跑"**，用来验"桩模式不出网"这条守卫真的有用。

    三处一起改，缺一处就够不着被测代码（这是查过很多次的坑）：
      ① `evidence.COLLECT` → 交出来的帧 `src='live'`（桩帧本身是 `src='stub'`）；
      ② `evidence.stub_active()` → False（`weights` 认的**真**开关就是它）；
      ③ `weights._scenario_via_intraday` → 假报一个成功结果。
    这三处一起生效时，离线跑就会报 `source='intraday'` —— 守卫一旦失效，判据就该红。
    ★注意 ① 同时保证**被注入的那一趟仍然离线**（帧还是桩帧，只是改了标签），
      否则"验证离线性"的注入自己会去联网，把自测变成慢判据。
    """
    def _collect_live_looking(q, deep=False):
        return dict(evidence._stub_collect(q, deep), src='live')

    return ((evidence, 'COLLECT', _collect_live_looking),
            (evidence, 'stub_active', lambda: False),
            (weights, '_scenario_via_intraday', _fake_intra_success))


def check_scenario_offline_no_net():
    """★桩模式下场景判定**不出网**（走占位口径，且秒回）。

    ★这条是从一次真事故长出来的：A2 的 `agent/intraday/` 2026-09-22 落地后，
      `scenario_of()` 立刻开始调 `intraday.analyze()` → 那一层去 `datasource.minute()`
      取**真**分钟线 → 我这边是同步调用，自测实测**跑过 120s 不返回**，只能 kill。
      "离线可复现"不是洁癖：判据要在没网的环境跑，桩的意义就是输入固定。
    注入验的是"离线这条守卫真的有作用"：把桩帧伪装成 live（`src` 改掉、
    `stub_active` 关掉）**并**让 intraday 假报一个成功结果 —— 守卫一旦失效，
    离线跑就会报 `source='intraday'`，这条红。
    """
    synth_env, _ = _intra_env_names()

    with env(**{synth_env: None}):
        ev = evidence.COLLECT(Q_SYM)
        t0 = time.time()
        sc, det = weights.scenario_of(ev)
        ms = int((time.time() - t0) * 1000)
    src = str(det.get('source') or '')
    ok = src.startswith('placeholder:') and ms < 1000
    return ok, {'src': src, 'scenario': sc, 'ms': ms,
                'ev_src': ev.get('src'),
                'note': '秒回 + 占位口径 ⇒ 没出网；`ms<1000` 是这条的数值判据'}


def check_scenario_from_intraday():
    """★显式开合成帧时，场景**真的来自 `intraday.analyze()`**，且同一场景可复现。

    ★这守的是另一头：不许因为"怕它卡"就把接好的那条链砍成永远走占位
      （那就变成"假装接上了"的反面 —— "假装没接"）。所以两头都要有判据：
      一条守不出网，一条守真接上。`regime` 必须非空，`source` 必须是 `'intraday'`。
    """
    synth_env, scen_env = _intra_env_names()
    with env(LICAI_AGENTS_STUB='1', **{synth_env: '1', scen_env: 'trend_up'}):
        sc, det = weights.scenario_of(evidence.COLLECT(Q_SYM))
        sc2, _ = weights.scenario_of(evidence.COLLECT(Q_SYM))
    ok = (det.get('source') == 'intraday' and bool(det.get('regime'))
          and sc == sc2)
    return ok, {'src': det.get('source'), 'scenario': sc, 'regime': det.get('regime'),
                'data_source': det.get('data_source'), 'scenario_2nd_run': sc2,
                'note': '合成帧的 data_source 会写 SYNTH(scenario=…)；'
                        '★只设 %s=1 时它是 scenario=random（**不可复现**）⇒ '
                        '判据要复现必须同时给 %s' % (synth_env, scen_env)}


def _buggy_cond(cid, level, metric, value, op, threshold, unit, why):
    """注入用：**把修之前的那版 `_cond` 原样装回去**（数值强制转换吃掉了字符串）。

    ★这是最精确的一种注入：它证明"是那一处改动让它绿的"，而不是"随便改点就能红"。
    """
    v = risk._num(value)
    hit = False
    if v is not None:
        hit = bool(op(v, threshold)) if callable(op) else False
    return {'id': cid, 'level': level, 'metric': metric, 'value': v,
            'threshold': threshold, 'unit': unit, 'hit': hit,
            'unknown': v is None, 'why': why}


def check_risk_scenario_visible():
    """★风控嘴里的「场景」必须与 verdict 里的场景**是同一个**，不能说"没取到"。

    ★这条守的是一次真事故：`_cond` 用 `_num()` 强制转换，而场景是**字符串**
      ⇒ `_num('trend_up')` 给 None ⇒ 那一档永远显示"没取到"。于是同一场会议里
      风控官说「场景：没取到」、verdict 里写着 `trend_up` —— 两个都是"真读数"，
      读的人只会以为其中一个坏了，或者以为"场景没算出来"。
    """
    evs, out = one_run()
    v = by_type(evs, 'agent_verdict')[0]
    risk_say = [e for e in by_type(evs, 'agent_say') if e['role'] == 'risk'][-1]
    cond = [c for c in v['risk_conditions'] if c['id'] == 'crisis_regime'][0]
    # 风控的正文里，每条条件占一行、以指标名开头（`_rule_risk_view` 的格式）
    line = [x for x in (risk_say['text'] or '').split('\n') if x.startswith('场景')]
    ok = (cond['unknown'] is False
          and cond['value'] == out['basis']['scenario']['value']
          and len(line) == 1 and '没取到' not in line[0])
    return ok, {'cond_value': cond['value'], 'unknown': cond['unknown'],
                'verdict_scenario': out['basis']['scenario']['value'],
                'say_line': (line[0] if line else None)}


def check_kg_links():
    """★图谱解释官要能把用户带到**图谱页的具体词条**上（而且那个词条是真存在的）。"""
    evs, out = one_run('可转债和债券基金有啥区别')
    e = [x for x in by_type(evs, 'agent_say') if x['role'] == 'kg_tutor'][0]
    links = e.get('links') or {}
    import graph_kb
    names = [t['name'] for t in (links.get('terms') or [])]
    real = [n for n in names if n in graph_kb.kb().terms]
    ok = (bool(links) and all(t.get('url') == 'graph.html' for t in (links.get('terms') or []))
          and len(real) == len(names) and bool(links.get('hint')))
    return ok, {'terms': names, 'all_real': len(real) == len(names),
                'url_ok': all(t.get('url') == 'graph.html'
                              for t in (links.get('terms') or [])),
                'hint': (links.get('hint') or '')[:60]}


def check_no_data_no_fabrication():
    """★全丢数据时：不许出现任何数字，而且要**照实说没取到**。"""
    with env(LICAI_AGENTS_STUB_DROP='get_kline,get_fundamentals,get_news'):
        evs, out = one_run()
    e = [x for x in by_type(evs, 'agent_say') if x['role'] == 'technical'][0]
    blk = evidence.block_for('technical', evidence.COLLECT('茅台'))
    v = by_type(evs, 'agent_verdict')[0]
    n_say_nums = sum(1 for x in by_type(evs, 'agent_say')
                     if x['role'] in ('technical', 'fundamental') and any(
                         ch.isdigit() for ch in (x['text'] or '')[:200]))
    ok = ('不构成投资建议' in (v['risk_note'] or ''))
    return ok, {'data_gap_hit': 'data_gap' in ((v['veto'] or {}).get('hard') or []),
                'block_head': blk[:60], 'say_with_digits': n_say_nums,
                'n_probes': out['evidence']['n_probes'],
                'miss_rate': out['evidence']['miss_rate'],
                'note': '桩的立场值是写死的，所以"没数据也照说立场"是**桩的已知代价**，'
                        '不是产品行为；这条只验"降级路径被走到 + 风险提示还在"'}


# ── 判据表 ────────────────────────────────────────────────────────────
#
# ★每条都要有**能打红它的注入**。写不出注入的判据，等于没人替它把关
#   （那种情况要降级成"证据"并在名字旁写明 —— 这里没有这样的条目）。

_orig_turn_blocks = roles._turn_blocks        # 注入用：留住原位，改指不要改删


def _memo_block():
    """注入用：把「本轮数据」那一块**只算一次**，之后永远返回第一次的结果。

    模拟的正是 `llm.live_context` 那条纪律要防的病：**把会说谎的东西写进常量**
    （常量第一次算出来是对的，从那以后每次都错，而且页面上看不出来）。
    """
    cache = {}

    def f(history, profile, block):
        if 'v' not in cache:
            cache['v'] = _orig_turn_blocks(history, profile, block)
        return cache['v']
    return f


CHECKS = [
    {'id': '事件流完整且以 done 收尾', 'fn': check_event_stream, 'inject': None,
     'why': '既有纪律：一轮以 done 收尾；漏一条出口前端就永远转圈'},
    {'id': '第2轮payload带对方第1轮论点', 'fn': check_debate_payload,
     'inject': (roles, '_opponent_block', lambda role, stage: ''),
     'why': '规格 §3.4 的"博弈"；不注入就打不出红的判据是装饰'},
    {'id': '权重表归一/9角色齐/三因素有数', 'fn': check_weights_normalized,
     'inject': None, 'why': '权重必须是算出来的，不是配出来的'},
    {'id': 'score 可由权重表复算', 'fn': check_score_reproducible, 'inject': None,
     'why': '规格 §3.4：pm 的分数必须能从权重复算 —— 这条是"权重不是装饰"的唯一检验'},
    {'id': '风险提示原样含「不构成投资建议」', 'fn': check_risk_note,
     'inject': (roles, 'RISK_TAIL', ''),
     'why': '本站对用户的承诺，判据 grep 的就是它'},
    {'id': '心理角色不诊断/不建议',
     'fn': check_psych_wording, 'inject': (roles, 'psych_guard',
                                           lambda t: (t, {})),
     'why': '本站不知道用户是谁，任何"你有 XX 偏差"都是编的'},
    {'id': '提示词现拼不缓存', 'fn': check_prompts_fresh,
     'inject': (roles, '_turn_blocks', _memo_block()),
     'why': 'llm.live_context 那条纪律：会过期的东西绝不能进常量'},
    {'id': '收缩先验中性(n=0 ⇒ 0.5)', 'fn': check_shrink_neutral,
     'inject': (weights, 'PRIOR_N', 2.0),
     'why': '写成 (0+2)/(0+2)=1.0 就会让空台账读成"100% 准"，且看不出来'},
    {'id': '风控否决真的改变了结论', 'fn': check_veto_effective,
     'inject': (risk, 'apply',
                lambda side, s, c, rk: (side, c, None, False)),
     'why': '规格 §3.4：命中条件时最终 verdict 必须体现'},
    {'id': '历史校准真的会动权重', 'fn': check_calibration_moves_weights, 'inject': None,
     'why': '读了台账却没用上（乘数恒 1.0）只有跑出来才看得见'},
    {'id': '桩模式场景判定不出网',
     'fn': check_scenario_offline_no_net,
     'inject': _fake_live_injection(),
     'why': 'A2 的 intraday 落地后我这边同步调用卡死过 120s+；离线性是判据能跑的前提'},
    {'id': '合成帧下场景真来自 intraday', 'fn': check_scenario_from_intraday,
     'inject': (weights, '_scenario_via_intraday',
                lambda s0: (None, {'error': '注入：假装 intraday 调不通'})),
     'why': '反方向：不许因为怕卡就把接好的那条链砍成永远走占位'},
    {'id': '风控的场景与verdict一致', 'fn': check_risk_scenario_visible,
     'inject': (risk, '_cond', _buggy_cond),
     'why': '曾经风控嘴里「场景：没取到」而 verdict 写着 trend_up（同一个量两个说法）'},
    {'id': '图谱解释官能给具体词条', 'fn': check_kg_links,
     'inject': (agents, '_graph_links', lambda ev: {}),
     'why': '规格 §3.4：要能把用户带到图谱页的具体词条'},
    {'id': '数据全丢时风险提示仍在', 'fn': check_no_data_no_fabrication, 'inject': None,
     'why': '降级路径必须被走到，而不是只在全绿的路上跑'},
]


def selftest():
    """每条判据跑两遍：干净时**必须绿**，注入后**必须红**。

    ★"注入后没红"有两种含义，报告里必须能分开（这是查过很多次的坑）：
      ① 判据不灵（该红而绿）→ 真问题；
      ② 注入够不着被测代码 → 判据可能是好的，坏的是"它被测的方式"。
      所以每条都把**注入是否真的生效**也顺手验一下（`_injection_live()`）。
    """
    print('=' * 78)
    print('agents 判据自测（注入必须打红，干净必须绿）')
    print('=' * 78)
    bad = []
    for c in CHECKS:
        t0 = time.time()
        ok_clean, d_clean = c['fn']()
        inj = c.get('inject')
        line = '%-34s 干净:%-4s' % (c['id'], '绿' if ok_clean else '红')
        if inj is None:
            line += '  注入:（无 —— 见下面的说明）'
            if not ok_clean:
                bad.append((c['id'], '干净副本上就是红的', d_clean))
        else:
            with fault(inj):
                ok_inj, d_inj = c['fn']()
            line += '  注入:%-4s' % ('红' if not ok_inj else '**还是绿**')
            if ok_clean and ok_inj:
                bad.append((c['id'], '注入打不红（判据不灵 **或** 注入够不着）', d_inj))
            if not ok_clean:
                bad.append((c['id'], '干净副本上是红的', d_clean))
        print(line + '  %.1fs' % (time.time() - t0))
        if not ok_clean:
            print('      干净详情:', json.dumps(d_clean, ensure_ascii=False)[:300])
        if inj is not None and ok_inj:
            print('      注入详情:', json.dumps(d_inj, ensure_ascii=False)[:300])

    # ★没有"注入"的那几条：不是可以松，而是它们守的是**产出的一致性**
    #   （复算、归一化），打红它们要改数学本身、改完就不再是被测对象了。
    #   所以下面这几条**单独再验一遍**：用"篡改产出"的手法逼它们红。
    print('\n── 无注入条目的替代否证（篡改产出，看判据认不认）──')
    for cid, tamper, fn in (
            ('权重表归一', lambda w: w.__setitem__('bull', 0.9), check_weights_normalized),
            ('score 复算', None, check_score_reproducible)):
        if tamper is None:
            print('%-14s 不适用（它的输入是事件流本身，篡改事件=篡改被测对象）' % cid)
            continue
        orig = weights.compute

        def bad_compute(ev, views, scenario=None, _o=orig, _t=tamper):
            r = _o(ev, views, scenario)
            _t(r['w'])
            return r
        with fault((weights, 'compute', bad_compute)):
            ok_t, d_t = fn()
        print('%-14s 篡改后:%-4s' % (cid, '红' if not ok_t else '**还是绿**'))
        if ok_t:
            bad.append((cid, '篡改产出后仍是绿的', d_t))

    print('\n' + '=' * 78)
    if bad:
        print('✗ 有 %d 条不达标：' % len(bad))
        for cid, why, d in bad:
            print('   - %s：%s' % (cid, why))
            print('     %s' % json.dumps(d, ensure_ascii=False)[:300])
        return 1
    print('✓ 全部达标：%d 条判据，干净全绿、注入全红' % len(CHECKS))
    return 0


# ── 冒烟报告 ──────────────────────────────────────────────────────────

def _trunc(s, n=150):
    s = (s or '').replace('\n', ' ')
    return s if len(s) <= n else s[:n] + '…'


def smoke():
    if os.path.exists(PAYLOAD_PATH):
        os.remove(PAYLOAD_PATH)
    t0 = time.time()
    evs, out = one_run()
    print('=' * 78)
    print('agents 冒烟：一次完整的 run()（桩传输 + 桩证据，离线零 token）')
    print('问题：茅台最近走势怎么样    用时 %.1fs    事件 %d 个'
          % (time.time() - t0, len(evs)))
    print('=' * 78)

    print('\n── ① 真实事件流（同一类型只印第一条，长的截断）──')
    seen = set()
    for e in evs:
        t = e['type']
        if t in seen:
            continue
        seen.add(t)
        if t == 'agent_say':
            print('  [%s] role=%s round=%s stance=%s conf=%s revised=%s basis=%s'
                  % (t, e['role'], e['round'], e['stance'], e['conf'],
                     e.get('revised'), e.get('stance_basis')))
            print('        text: %s' % _trunc(e['text'], 180))
            if e.get('points'):
                print('        points: %s' % _trunc('；'.join(e['points']), 160))
            if e.get('rebuttal'):
                print('        rebuttal: %s' % _trunc(e['rebuttal'], 200))
        elif t == 'agent_weights':
            print('  [%s] scenario=%s' % (t, e['scenario']))
            print('        w=%s' % json.dumps(e['w'], ensure_ascii=False))
        elif t == 'agent_verdict':
            print('  [%s] side=%s score=%s conf=%s veto=%s'
                  % (t, e['side'], e['score'], e['confidence'],
                     (e['veto'] or {}).get('level')))
            print('        equation: %s' % e['equation'])
            print('        dissent: %s' % _trunc(json.dumps(e['dissent'], ensure_ascii=False), 200))
        elif t == 'agent_round':
            print('  [%s] round=%s roles=%s' % (t, e['round'], e['roles']))
        elif t == 'agent_start':
            print('  [%s] role=%s name=%s' % (t, e['role'], e['name']))
        elif t == 'agent_done':
            print('  [%s] role=%s ok=%s ms=%s' % (t, e['role'], e['ok'], e['ms']))
        else:
            print('  [%s] %s' % (t, _trunc(json.dumps(e, ensure_ascii=False), 160)))

    print('\n── ② 博弈的证据：第 2 轮 payload 真的带了对方第 1 轮的论点 ──')
    ok, d = check_debate_payload()
    for role, f in (d.get('found') or {}).items():
        print('  给 %s（对手 %s）的第 2 轮 payload：' % (role, f['opponent']))
        print('    对方第 1 轮 stance = %s  ⇒ payload 里有："立场值 stance = %s" → %s'
              % (f['opp_stance'], f['opp_stance'], f['stance_in_payload']))
        print('    对方第 1 轮原话片段 %r ⇒ payload 里有 → %s'
              % (f['frag'], f['text_in_payload']))
        print('    它自己的 rebuttal 引用了对方立场 → %s' % f['rebuttal_quotes'])
    print('  第 2 轮 revised：%s' % d.get('revised'))
    print('  → %s%s' % ('判定通过' if ok else '★没通过',
                        ('；%s' % d['problems']) if d.get('problems') else ''))

    print('\n── ③ 动态权重表 + basis ──')
    for s in out['basis']['steps']:
        if s.get('w'):
            print('  %-10s w=%.4f  先验%s × 场景%s × 波动%s × 校准%s × 置信%s   stance=%s'
                  % (s['role'], out['w'][s['role']], s.get('base'),
                     s.get('scenario_mult'), s.get('vol_mult'), s.get('calib_mult'),
                     s.get('conf_mult'), s.get('stance')))
        else:
            print('  %-10s w=0.0     %s' % (s['role'], s.get('why')))
    b = out['basis']
    print('  加总 Σw = %.8f（必须正好 1）' % sum(out['w'].values()))
    sd = b['scenario']
    print('  因素①场景：%s（source=%s）' % (sd['value'], sd['source']))
    if sd['source'] == 'intraday':
        print('    regime = %s；data_source = %s'
              % (json.dumps(sd.get('regime'), ensure_ascii=False), sd.get('data_source')))
        print('    ★这条链是**真的接上了** agent/intraday.analyze()（source 就是判据认它的依据）。')
    else:
        print('    依据：MA5 %s / MA20 %s / MA60 %s ⇒ 均线偏离 %s%%；BOLL带宽 %s；'
              '年化波动 %s%%'
              % (sd.get('ma5'), sd.get('ma20'), sd.get('ma60'),
                 sd.get('ma_gap_pct'), sd.get('boll_band'), sd.get('ann_vol_pct')))
        print('    ★这是**占位口径**，不是"接上了 intraday"。原因：%s'
              % _trunc(str(sd.get('why')), 160))
    print('  因素②历史校准：台账 %s，已计分样本 %s，门槛 %s'
          % (b['calibration']['path'], b['calibration']['scored'],
             b['calibration']['min_samples']))
    print('    %s' % b['calibration']['note'])
    for r in ('bull', 'bear', 'technical'):
        c = b['calibration']['roles'][r]
        print('    %-10s n=%s 命中=%s 命中率=%s 收缩后=%s 乘数=%s 【%s】'
              % (r, c['n'], c['hits'], c['rate'], c['shrunk'], c['factor'], c['source']))
    print('    ★source=prior 的角色**没在做校准**（样本不够），乘数恒 1.0 —— '
          '先验不当实测报。')
    print('  因素③自报置信度 + 收缩（SHRINK=%s）：' % b['confidence']['shrink'])
    for r, c in b['confidence']['roles'].items():
        print('    %-10s 自报 %s → 收缩 %s → 乘数 %s'
              % (r, c['reported'], c['shrunk'], c['mult']))
    print('    %s' % _trunc(b['confidence']['why'], 300))
    print('  零权重角色及理由：')
    for r, why in b['zero_weight_roles'].items():
        print('    %-10s %s' % (r, _trunc(why, 120)))

    print('\n── ④ 最终 verdict + score 复算 ──')
    v = out['verdict']
    print('  side = %s（风控前 %s）；score = %s；confidence = %s（原始 %s）'
          % (v['side'], v['side_before_risk'], v['score'], v['confidence'],
             v['confidence_raw']))
    print('  复算式：%s' % v['equation'])
    check = out['score_check']
    print('  复算：拿事件里那份 w 乘一遍 = %s；与 verdict.score 相同 → %s（差 %s）'
          % (check, out['score_reproducible'], abs(check - out['score'])))
    print('  veto = %s' % (json.dumps(v['veto'], ensure_ascii=False)
                           if v['veto'] else 'None（没有条件命中）'))
    print('  reasons:')
    for r in v['reasons']:
        print('    - %s' % _trunc(r, 160))
    print('  dissent: %s' % (json.dumps(v['dissent'], ensure_ascii=False)
                             if v['dissent'] else '[]（没有少数方）'))
    print('  risk_note（★原样含「不构成投资建议」）：')
    print('    %s' % _trunc(v['risk_note'], 400))
    print('    contains 不构成投资建议 → %s' % ('不构成投资建议' in v['risk_note']))
    print('  风控条件（全部列出，含没命中的 —— "离阈值还有多远"也是信息）：')
    for c in v['risk_conditions']:
        print('    %-18s %s %s%s vs 阈值 %s%s  %s'
              % (c['id'], c['level'], c['value'], '', c['threshold'], '',
                 '【命中】' if c['hit'] else ('（没取到）' if c['unknown'] else '没超')))

    print('\n── ⑤ 图谱解释官带来的词条（判据用问题：可转债和债券基金有啥区别）──')
    evs2, _ = one_run('可转债和债券基金有啥区别')
    kg = [e for e in by_type(evs2, 'agent_say') if e['role'] == 'kg_tutor'][0]
    print('  links = %s' % json.dumps(kg.get('links'), ensure_ascii=False)[:400])

    print('\n── ⑥ 心理角色的话（措辞必须是"提示"，不能是诊断）──')
    ps = [e for e in by_type(evs2, 'agent_say') if e['role'] == 'psych'][0]
    print('  %s' % _trunc(ps['text'], 300))

    print('\n报告完。payload 落在 %s（判据可读它断言"第 2 轮带了对方论点"）'
          % PAYLOAD_PATH)
    return 0


if __name__ == '__main__':
    sys.exit(selftest() if '--selftest' in sys.argv else smoke())
