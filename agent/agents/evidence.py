"""证据层：编排层**统一取一次数**，再按角色分发 —— 角色自己不去调工具。

为什么是这个形状（三条，每条都对应一个已知的坏法）：

1. **同一轮里取一次。** 9 个角色各调一次 `get_kline` 就是 9 次网络请求，而且
   "多空看的是不是同一张图"会变成一个**运气问题**（两次取数之间只要上游抖一下，
   多空就各看各的窗口）。从一份证据里分发 ⇒ "他们看的是同一份数"是**结构上**成立的，
   不靠提示词嘱咐。
   ★这也顺手解决了延迟：一次 get_kline 实测 1~3 秒（走新浪），9 次就是 10~30 秒。

2. **取数必须可审计。** 每个探针都记 `{id, args, ok, ms, why, summary, src}`，这些出处
   原样进 `agent_say.evidence`（一角色一份引文列表）。于是"这个数从哪来"、
   "哪个角色拿的是没取到的数据"在前端和判据里都是**看得见**的，而不是靠模型自觉。

3. **判据要离线跑，所以取数也得能打桩。** 开关和传输层共用一个
   （`LICAI_AGENTS_STUB`，见 `__init__.py`）。桩帧**写死常数、同参同帧**
   （照 `feed._stub_frame` 那条纪律：两次调用之间只要有一个比特不同，
   判据的对照就变成"拿 A 比 B"，绿了也说明不了任何事）。
   ★桩**不跟着问题变**：桩就是桩，它要能写死期望值；跟着问题变的话，判据里的
   期望值只能现算，那就又变成"用产物判产物"了。

★一条纪律（本模块最容易被做歪的地方）：**探针失败不是异常，是一条有名字的记录。**
  取不到数的探针会变成一个 `ok=False` + 人话 `why` 的条目，跟着证据一起发给角色，
  角色才有资格说"这个数据我没取到"。静默吞掉的话，模型只会看到"数据块里没有这一项"，
  而它接下来的行为是**编一个**（`tools.py` 里 `NO_FUNDAMENTALS` 那段讲的就是这个病）。
"""

import os
import re
import time

import feed
import graph_kb
import indicators
import tools

# 单个探针的墙钟上限。**每个外部调用都要有超时**（规格 §0.6）：
# 服务里"挂住"比"报错"糟糕得多 —— 一次卡住的取数会让整轮会议停在 60 秒的心跳里，
# 用户看到的是"它在转"，不是"它坏了"。
# ★20 秒是对着实测定的：`get_kline` 走新浪 1~3 秒（本机 2026-09-22），
#   慢的时候（换源/重试）也要留出余量；`predict_kline` 例外，它要几十秒 GPU（见 DEEP）。
PROBE_TIMEOUT = 20
DEEP_TIMEOUT = 240               # deep 档要跑 Kronos（实测一次 20~60 秒）

# 一次会议里最多看几只标的。★这不是性能参数，是**判据参数**：一份证据里混进两三只票，
# "本轮数据"那几个数字到底说的是谁就没人分得清了，而角色们只会照着最近那只说。
MAX_SYMBOLS = 2

# 技术指标要多少根日线。★`get_kline` 的 data 通道只回最后 120 根（tools._tail_bars），
# 而 MA60 / RSI24 / MACD(26) 都能在 120 根内算出来 ⇒ 这个数刚好够。
# 算不出来的（比如 MA120）按 `indicators.py` 的规矩**整条不给**，不拿短窗口凑。
BARS_FOR_IND = 120

# 角色 → 它需要哪几个探针。★`psych` 是**空的**，那是刻意的：
# 投资心理角色的活是讲常见偏差，不碰价格。给它行情只会诱导它把"损失厌恶"讲成
# "所以你现在不该割肉" —— 那就踩了两条硬规矩（不给建议 / 不拿偏差当依据）。
ROLE_PROBES = {
    'bull': ['get_kline', 'tech', 'get_fundamentals', 'get_news'],
    'bear': ['get_kline', 'tech', 'get_fundamentals', 'get_news'],
    'fundamental': ['get_fundamentals', 'get_kline'],
    'technical': ['get_kline', 'tech'],
    'sentiment': ['get_news', 'get_kline'],
    'risk': ['get_kline', 'tech', 'get_fundamentals', 'analyze_portfolio'],
    'pm': ['get_kline', 'tech', 'get_fundamentals', 'get_news', 'analyze_portfolio'],
    'kg_tutor': ['search_terms', 'walk_graph'],
    'psych': [],
}

# 探针的展示名（引文和证据块里用它）。用工具名 + 派生名，别另起一套黑话。
PROBE_LABEL = {'tech': '技术指标（由 get_kline 的日线现算）'}


# ── 标的识别 ──────────────────────────────────────────────────────────
#
# ★**只认三种写法**，而且第三种是白名单查表。这一条看着保守，是被 `feed.resolve()`
#   的宽松度逼出来的：它把**任何 1~5 个大写字母**都解析成美股（那是给用户输入框用的，
#   用户输 AAPL 就得认）。拿它当"这句话里有没有标的"的校验器的话：
#   问「ROE 是多少」会把 `ROE` 当成一只美股，「ETF 和基金啥区别」会把 `ETF` 当成一只美股，
#   然后我们拿着一只不存在的票去取 K 线、去算指标，最后给出一份**看着像真的**分析。
#   ⇒ 所以这里**不接**裸的大写字母代码。代价是"用户直接写 AAPL 时认不出来"，
#     这一条写进了交付说明的"没做的"里（现状可用「苹果」这类中文名，见 feed.NAME2CODE）。
_CN_CODE = re.compile(r'(?<!\d)(\d{6})(?!\d)')
_EX_CODE = re.compile(r'(?<![A-Za-z0-9])((?:sh|sz|bj)\d{6})(?![0-9])', re.I)


def _symbols_in(question):
    """从问题里认出标的。返回 (syms, notes)：notes 说明**为什么某个候选被丢掉了**。

    ★认不出就认不出 —— 空列表是合法结果，探针会照实报"问题里没认出标的"。
      猜一个标的的代价比"没认出"大得多（见上）。
    """
    q = question or ''
    syms, notes = [], []

    for raw in _EX_CODE.findall(q) + _CN_CODE.findall(q):
        r = feed.resolve(raw)
        if not r.get('ok'):
            notes.append('%s 认不出（%s）' % (raw, r.get('hint')))
            continue
        if r.get('ambiguous'):
            # ★歧义**不替用户选**（000001 = 上证指数 or 平安银行，差得远）。
            #   这里也不抛错：会议照常开，只是没有行情数据，并且把歧义原样说出来 ——
            #   让用户看得到"它为什么没看盘面"。
            notes.append('%s 有歧义（%s），本轮不当标的用' % (raw, r.get('hint')))
            continue
        syms.append({'raw': raw, 'market': r['market'], 'code': r['code'], 'name': r['name'],
                     'how': '代码'})

    # 表里的中文名（茅台 / 沪深300 / 腾讯 / 苹果 / 比特币…）。长名优先，
    # 否则「贵州茅台」会被「茅台」先吃掉，而两者其实是同一个标的（不致命，但引文会难看）。
    for name in sorted(feed.NAME2CODE, key=len, reverse=True):
        if name in q:
            m, c, n = feed.NAME2CODE[name]
            syms.append({'raw': name, 'market': m, 'code': c, 'name': n, 'how': '名称'})

    seen, uniq = set(), []
    for s in syms:
        k = (s['market'], s['code'])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    if len(uniq) > MAX_SYMBOLS:
        notes.append('问题里认出 %d 只标的，只取前 %d 只（%s）'
                     % (len(uniq), MAX_SYMBOLS, '、'.join(x['name'] for x in uniq[MAX_SYMBOLS:])))
        uniq = uniq[:MAX_SYMBOLS]
    return uniq, notes


# ── 技术指标（派生探针）───────────────────────────────────────────────

def _bars_frame(bars):
    """`data.bars`（列式 {t,o,h,l,c,v}）→ canonical DataFrame。

    ★列名**按名字映射，不按位置取**（规格 §0.8）：列式字典恰好是按名字给的，
      但下面每一步都显式写名字，免得将来上游加一列就整体错位。
    ★缺列就返回 None（**不补 0**）：少一列说明上游换了形状，那时给出的是
      一整排错位的指标，比"没算"坏得多。
    """
    import pandas as pd
    need = ('t', 'o', 'h', 'l', 'c', 'v')
    if not isinstance(bars, dict) or any(k not in bars for k in need):
        return None
    n = len(bars['t'])
    if n < 2 or any(len(bars[k]) != n for k in need):
        return None
    return pd.DataFrame({'dt': pd.to_datetime(bars['t']), 'open': bars['o'],
                         'high': bars['h'], 'low': bars['l'],
                         'close': bars['c'], 'volume': bars['v']})


def tech_snapshot(bars):
    """日线 → 一组**算得出来的**技术指标（最后一行）。算不出来的一律 None。

    ★NaN → None，**不是 0**（`indicators.py` 模块头那条规矩：不足窗口的地方
      是 NaN，补一个数出来在图上和真的长得一模一样）。
    """
    df = _bars_frame(bars)
    if df is None or len(df) < 2:
        return {'ok': False, 'why': '日线根数不够/形状不对，算不出指标'}
    ind = indicators.add_indicators(df)
    last = ind.iloc[-1]

    def g(name):
        try:
            v = last[name]
        except KeyError:
            return None
        return feed._num(v, 4)

    band = None
    if g('boll_up') is not None and g('boll_low') is not None and g('boll_mid'):
        band = feed._num((g('boll_up') - g('boll_low')) / g('boll_mid'), 4)
    out = {
        'ok': True, 'rows': len(df), 'dt': str(last['dt'])[:10],
        'close': g('close'),
        # 均线：值 + 排列（排列是**读出来的**，不是算出来的结论，所以两个都给）
        'ma5': g('ma5'), 'ma10': g('ma10'), 'ma20': g('ma20'), 'ma60': g('ma60'),
        'ema12': g('ema12'), 'ema26': g('ema26'),
        'rsi6': g('rsi6'), 'rsi12': g('rsi12'), 'rsi24': g('rsi24'),
        'macd_dif': g('dif'), 'macd_dea': g('dea'), 'macd_hist': g('hist'),
        'kdj_k': g('k'), 'kdj_d': g('d'), 'kdj_j': g('j'),
        'boll_up': g('boll_up'), 'boll_mid': g('boll_mid'), 'boll_low': g('boll_low'),
        # BOLL 带宽 = (上轨−下轨)/中轨 —— 「挤压」这个场景口径要用它（见 weights.py）
        'boll_band': band,
        # ★量比是"当日量 ÷ 前 5 日均量（**不含当日**）"，**不是**交易所口径那个
        #   （那个要按已开盘分钟数折算，本模块没有分钟数据）。名字一样、数不一样，
        #   所以这里把它叫 `vol_ratio_5d`，并在引文里明说算的是哪一个。
        'vol_ratio_5d': g('vol_ratio'),
    }
    # 算不出来的格子只留 None —— 不删键（键在不在是**结构**信息：MA60 这一格在，
    # 值为 None 说的是"这 120 根里算得出来/算不出来"，删了键就分不出"没算"和"没有这项"）。
    return out


# ── 探针的计划与执行 ──────────────────────────────────────────────────

def _plan(question, deep=False):
    """这一轮要打哪些探针。返回 (syms, calls, notes)。

    ★固定顺序（不按集合遍历）：探针的**失败路径**也要可复现 —— 顺序飘了，
      "哪个先超时"就跟着飘，而那一轮的 miss 数会变。
    """
    syms, notes = _symbols_in(question)
    q = (question or '').strip()
    calls = []
    # ★认出来的标的可能**不止一只**（`MAX_SYMBOLS = 2`），但探针只打第一只
    #   （下面 `s0 = syms[0]`）。这件事必须在 notes 里说出来：不说的话，读
    #   `ev['symbols']` 的人会以为"声明 2 只"就是"分析 2 只"，而实际上第二只
    #   **一个字的数据都没有**。（`symbols_planned` / `symbols_probed` /
    #   `symbols_not_probed` 三个字段是同一条的机器可读版，见 `collect`。）
    #   ★纪律：**不补一只假的**（不给第二只编行情）—— 只把"没探"说出来。
    if len(syms) > 1:
        notes.append('认出 %d 只标的，本轮**只探第 1 只**（%s）；未探：%s'
                     % (len(syms), syms[0]['name'],
                        '、'.join(x['name'] for x in syms[1:])))
    if syms:
        s0 = syms[0]
        calls.append(('get_kline', {'symbol': s0['code'], 'days': 400},
                      '行情与区间位置（%s）' % s0['name']))
        calls.append(('get_fundamentals', {'symbol': s0['code']}, '估值与财务（%s）' % s0['name']))
        calls.append(('get_news', {'symbol': s0['code']}, '资讯与热度（%s）' % s0['name']))
        if deep:
            calls.append(('predict_kline', {'symbol': s0['code'], 'pred_len': 20},
                          'Kronos 预测（deep 档，几十秒）'))
    else:
        notes.append('问题里没认出标的（只认 6 位代码 / sh|sz|bj 前缀 / 中文常见名），'
                     '所以没有行情与基本面')
        if q:
            # 没标的就把问题本身当话题去采资讯（"最近有什么事""降息怎么样"这类）。
            calls.append(('get_news', {'query': q[:30]}, '按话题采资讯'))
    # 图谱两张（本地表，零成本零网络）与组合体检（没有持仓时**立刻**结构化失败）常打。
    calls.append(('search_terms', {'query': q[:40]}, '词表命中（本地）'))
    calls.append(('analyze_portfolio', {}, '组合集中度（需要用户持仓，没有就照实说）'))
    return syms, calls, notes


def _with_timeout(fn, seconds, label):
    """墙钟上限。★超时**不杀线程**（Python 杀不掉）—— 只是我们不再等它。

    照 `feed._with_timeout` 的先例：泄漏一个卡住的线程，比让整场会议停在这里好。

    ★★`ctx.run` 这一句**不是装饰，是这条链上唯一让持仓看得见的东西**。
      `tools` 的请求上下文（`_REQ_HOLDINGS` / `_REQ_PROFILE`）是 `ContextVar`，
      而 `ContextVar` **每线程一份、且不会自动传给新线程** —— 新线程拿到的是
      **默认值**。这里每个探针都被 `submit` 到一个池线程里跑，所以：
        在 `sse_stream` 的 worker 线程里老老实实 `set_request_holdings(...)`
        **照样没用**，`analyze_portfolio` 在那个池线程里读到的永远是 `None`。
      症状不是报错，是**静默**走降级分支（"还不知道你的持仓"），
      而风控官那两条 veto 条件（集中度 / 前五大）读的正是这个值 ⇒
      **它们在生产里结构上打不响，而任何同线程的判据都量不出来。**
      （实测：加了持仓走 HTTP 路由，`concentration.value` 仍是 `null`、
      `unknown=True`，与不带持仓**逐字相同**；判据的对照臂就是靠这一点红的。）
      `copy_context()` 取的是**调用方那一刻**的上下文，所以它抄到的正是
      路由/`sse_stream` 设进去的那份。★改这里而不是改各调用点：
      探针有几个、将来还会不会加，这里改一次就全覆盖。
    """
    import concurrent.futures as cf
    import contextvars
    ctx = contextvars.copy_context()
    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(ctx.run, fn)
        try:
            return fut.result(timeout=seconds)
        except cf.TimeoutError:
            return {'summary': {'ok': False, 'error': 'TIMEOUT',
                                'msg': '%s 超过 %ds 没返回' % (label, seconds)},
                    'data': None}


def _run_probe(name, args, why, deep=False):
    """打一个探针 → 记录。**任何异常都在这里变成一个记录**（不往上抛）。"""
    t0 = time.time()
    limit = DEEP_TIMEOUT if name == 'predict_kline' else PROBE_TIMEOUT
    try:
        res = _with_timeout(lambda: tools.call(name, args), limit, name)
    except Exception as e:                      # noqa: BLE001
        # tools.call 自己已经不抛了；真抛了说明是它自己坏了（改坏了/被打了桩），
        # 那也要变成一条记录 —— 让整场会议死在取数上没有任何好处。
        res = {'summary': {'ok': False, 'error': type(e).__name__, 'msg': str(e)[:200]},
               'data': None}
    ms = int((time.time() - t0) * 1000)
    s = (res or {}).get('summary') or {}
    return {'id': name, 'args': args, 'why': why, 'ms': ms,
            'ok': bool(s.get('ok')),
            'why_failed': None if s.get('ok') else (s.get('msg') or s.get('error') or '没取到'),
            'hint': s.get('hint'), 'summary': s, 'data': (res or {}).get('data'),
            'source': s.get('source') or (res or {}).get('source'),
            'src': 'live'}


def collect(question, deep=False):
    """这一轮的全部证据。返回结构见下面 `out`。

    ★**同一轮里每个探针只打一次**（探针表按 id 索引），角色各自取自己那一份。
    """
    t0 = time.time()
    syms, calls, notes = _plan(question, deep=deep)
    probes, order = {}, []

    for name, args, why in calls:
        if name in probes:                       # 计划里重复了（比如两个角色都要 K 线）
            continue
        rec = _run_probe(name, args, why, deep=deep)
        probes[name] = rec
        order.append(name)
        if name == 'get_kline' and rec['ok']:
            # 派生出技术指标：不新开数据源，用同一份日线现算（`indicators` 模块的规矩）。
            data = rec.get('data') or {}
            probes['tech'] = {'id': 'tech', 'args': dict(args), 'ms': 0, 'src': 'derived',
                              'why': '技术指标（由上面那份日线现算，不另取数）',
                              'ok': False, 'why_failed': None, 'hint': None, 'summary': {},
                              'data': None, 'derived_from': 'get_kline'}
            snap = tech_snapshot(data.get('bars'))
            probes['tech'].update({'ok': bool(snap.get('ok')), 'summary': snap,
                                   'why_failed': None if snap.get('ok') else snap.get('why')})
            order.append('tech')

    # 图谱命中（本地表）：给 kg_tutor 用，也用来核实"模型讲的词条在图谱里真有"。
    graph = {'hits': [], 'walk': None, 'query': (question or '')[:40]}
    if 'search_terms' in probes and probes['search_terms']['ok']:
        s = probes['search_terms']['summary']
        graph['hits'] = [{'name': t.get('name'), 'cat': t.get('category'),
                          'family': t.get('family'), 'fields': t.get('fields') or {}}
                         for t in (s.get('terms') or [])]
        if graph['hits']:
            # walk_graph 只对**命中第一名**走一步 —— 走多了是噪声，且它是本地查表（零成本）。
            rec = _run_probe('walk_graph', {'term': graph['hits'][0]['name']},
                             '顺着图谱走一步（本地）')
            probes['walk_graph'] = rec
            order.append('walk_graph')
            if rec['ok']:
                graph['walk'] = rec['summary']

    n_ok = sum(1 for k in order if probes[k]['ok'])
    miss = [k for k in order if not probes[k]['ok']]
    out = {
        'ok': True, 'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'src': 'live',
        'symbols': syms, 'probes': {k: probes[k] for k in order}, 'order': order,
        'graph': graph, 'notes': notes,
        # ★"声明几只"和"实探几只"是**两个数**，所以两个都给（`_plan` 只探 syms[0]）。
        #   只给 `symbols`（= 认出来的全部）会被读成"这些都探过了"。
        #   `symbols_probed` = **打过探针的**（打没打通看 `probes`，不在这里），
        #   `symbols_not_probed` = 认出但**一个字节的数据都没有**的那些。
        'symbols_planned': len(syms),
        'symbols_probed': [s['code'] for s in syms[:1]],
        'symbols_not_probed': [s['name'] for s in syms[1:]],
        'n_probes': len(order), 'n_ok': n_ok,
        # ★缺失率是风控官的一条**数值条件**（规格 §3.4 里点名了"数据缺失率超阈值"），
        #   所以它必须在这里算好、且**分母说清楚**（哪些探针算在内）。
        'miss_rate': round(1.0 - (n_ok / len(order)), 4) if order else 1.0,
        'missing': miss, 'ms': int((time.time() - t0) * 1000),
    }
    return out


# ── 桩（离线判据靠它）─────────────────────────────────────────────────
#
# ★桩帧是**常数**，而且刻意包含一个**必失败**的探针（`analyze_portfolio`：
#   没有用户持仓时它就该失败）。理由：只在全绿的桩上跑，"取不到数时会发生什么"
#   这条路就永远没被走过 —— 而那条路恰恰是本站最在乎的（不许编数字）。
#   `LICAI_AGENTS_STUB_DROP` 可以把指定的探针**全部打成失败**，
#   用来离线验证风控的降级路径（见 risk.py 的 data_gap 条件）。

STUB_ENV = 'LICAI_AGENTS_STUB'
STUB_DROP_ENV = 'LICAI_AGENTS_STUB_DROP'

_STUB_KLINE = {
    'ok': True, 'name': '贵州茅台', 'code': '600519', 'market': 'cn_stock',
    'rows': 400, 'from': '2025-03-10', 'to': '2026-09-18',
    'last_close': 1523.4, 'chg_pct_5d': -1.2, 'chg_pct_20d': 3.4, 'chg_pct_60d': -8.1,
    'range_high': 1780.0, 'range_low': 1400.5, 'ann_vol_pct': 28.4,
    'source': 'stub',
}


def _stub_bars(n=BARS_FOR_IND, base=1523.4):
    """一段**可复现**的日线（显式常数 + 固定步长，不用随机数）。

    ★不用 `random` 也不用时间：判据会把这个帧的指标值写死成期望值，
      而"每次跑出来一样"是那份期望值唯一的前提。
    """
    import datetime as _dt
    out = {'t': [], 'o': [], 'h': [], 'l': [], 'c': [], 'v': []}
    d = _dt.date(2026, 4, 5)
    px = base * 0.94
    for i in range(n):
        # 一个缓慢上行 + 每 7 根回一次的确定性波形（看得出趋势，也看得出回调）
        step = 0.0022 if (i % 7) not in (5, 6) else -0.0028
        px = px * (1 + step)
        o = round(px * 0.998, 2)
        c = round(px, 2)
        out['t'].append(d.isoformat())
        out['o'].append(o)
        out['h'].append(round(max(o, c) * 1.004, 2))
        out['l'].append(round(min(o, c) * 0.996, 2))
        out['c'].append(c)
        out['v'].append(10000 + (i % 11) * 37)
        d = d + _dt.timedelta(days=1)
    return out


def _stub_probe(name, args, why, drop=()):
    if name in drop:
        return {'id': name, 'args': args, 'why': why, 'ms': 0, 'ok': False,
                'why_failed': '（桩：LICAI_AGENTS_STUB_DROP 指定丢弃这个探针）',
                'hint': None, 'summary': {}, 'data': None, 'source': None, 'src': 'stub'}
    if name == 'get_kline':
        return {'id': name, 'args': args, 'why': why, 'ms': 0, 'ok': True,
                'why_failed': None, 'hint': None, 'summary': dict(_STUB_KLINE),
                'data': {'kind': 'kline', 'bars': _stub_bars(), 'name': '贵州茅台',
                         'code': '600519', 'more': 280}, 'source': 'stub', 'src': 'stub'}
    if name == 'tech':
        snap = tech_snapshot(_stub_bars())
        return {'id': name, 'args': args, 'why': why, 'ms': 0, 'ok': bool(snap.get('ok')),
                'why_failed': None, 'hint': None, 'summary': snap, 'data': None,
                'source': 'stub', 'src': 'stub'}
    if name == 'get_fundamentals':
        return {'id': name, 'args': args, 'why': why, 'ms': 0, 'ok': True,
                'why_failed': None, 'hint': None, 'source': 'stub', 'src': 'stub',
                'data': {'kind': 'fundamentals', 'code': '600519'}, 'summary': {
                    'ok': True, 'name': '贵州茅台', 'code': '600519', 'market': 'cn_stock',
                    'industry': '白酒', 'report_date': '2026-06-30',
                    'pe_ttm': 21.3, 'pb': 7.8, 'pe_pctile_5y': 12.0, 'pb_pctile_5y': 34.0,
                    'roe_weighted': 24.1, 'debt_ratio': 16.2, 'rev_growth': 9.8,
                    'profit_growth': 11.4, 'gross_margin': 91.2,
                    'sources': ['stub'], 'missing': []}}
    if name == 'get_news':
        return {'id': name, 'args': args, 'why': why, 'ms': 0, 'ok': True,
                'why_failed': None, 'hint': None, 'source': 'stub', 'src': 'stub',
                'data': {'kind': 'news', 'name': None, 'code': None, 'query': None,
                         'degraded': [], 'items': [
                             {'title': '（桩）白酒板块成交回暖', 'excerpt': '（桩数据，非真实新闻）',
                              'source': 'stub', 'ts': '2026-09-18 09:30', 'url': None,
                              'heat': 2, 'seen_in': ['stub_a', 'stub_b']}]},
                'summary': {'ok': True, 'count': 1, 'as_of': '2026-09-18 09:30',
                            'items': [{'title': '（桩）白酒板块成交回暖', 'source': 'stub',
                                       'ts': '2026-09-18 09:30', 'heat': 2}],
                            'degraded': []}}
    if name == 'search_terms':
        # ★桩仍然走**真**图谱（本地查表、零成本），因为 kg_tutor 的引文必须指向
        #   真词条 —— 桩要是编几个不存在的词，判据就只能拿桩去判桩。
        hit = graph_kb.kb().search(args.get('query') or '')
        return {'id': name, 'args': args, 'why': why, 'ms': 0, 'ok': True,
                'why_failed': None, 'hint': None, 'source': 'local', 'src': 'stub',
                'data': {'kind': 'terms', 'chips': [h['name'] for h in hit]},
                'summary': {'ok': True, 'found': bool(hit), 'query': args.get('query'),
                            'count': len(hit),
                            'terms': [{'name': h['name'], 'category': h['cat'],
                                       'family': h['family'],
                                       'is_starting_point': h['is_start'],
                                       'fields': h['fields'],
                                       'contrast_with': h['contrast_with']} for h in hit]}}
    if name == 'walk_graph':
        w = graph_kb.kb().walk(args.get('term') or '')
        return {'id': name, 'args': args, 'why': why, 'ms': 0, 'ok': bool(w),
                'why_failed': None if w else '词表里没有这个词',
                'hint': None, 'source': 'local', 'src': 'stub', 'data': None,
                'summary': w or {}}
    if name == 'analyze_portfolio':
        return {'id': name, 'args': args, 'why': why, 'ms': 0, 'ok': False,
                'why_failed': '还不知道你的持仓，没法体检（NO_HOLDINGS）',
                'hint': '持仓存在用户自己的浏览器里，本站服务端不保存',
                'summary': {'ok': False, 'error': 'NO_HOLDINGS',
                            'msg': '还不知道你的持仓，没法体检'},
                'data': None, 'source': None, 'src': 'stub'}
    if name == 'predict_kline':
        return {'id': name, 'args': args, 'why': why, 'ms': 0, 'ok': True,
                'why_failed': None, 'hint': None, 'source': 'stub', 'src': 'stub',
                'data': None, 'summary': {
                    'ok': True, 'name': '贵州茅台', 'code': '600519',
                    'pred_len': 20, 'sample_count': 9, 'base_close': 1523.4,
                    'pred_last_close': 1568.2, 'pred_chg_pct': 2.94,
                    'direction': 'up', 'direction_center': 'up',
                    'hist_ann_vol_pct': 28.4, 'pred_ann_vol_pct': 26.1,
                    'spread': {'n_paths': 9, 'level_pct': 80.0, 'lo': 1410.0, 'hi': 1610.0,
                               'width_pct': 14.2, 'withheld': False, 'consensus': 0.78,
                               'vote_ci95': [0.62, 0.91], 'up': 7, 'down': 1, 'flat': 1,
                               'called': 8, 'why': None},
                    'caveat': '（桩数据）★模型的输出只做参考，不构成投资建议。'}}
    return {'id': name, 'args': args, 'why': why, 'ms': 0, 'ok': False,
            'why_failed': '（桩：没有为 %s 准备帧）' % name, 'hint': None,
            'summary': {}, 'data': None, 'source': None, 'src': 'stub'}


def _stub_collect(question, deep=False):
    """离线的一整份证据。★**不跟着问题变** —— 桩要能写死期望值（见模块头第 3 条）。

    `analyze_portfolio` **必失败**、`get_news` 只有一条：这两处是刻意留的
    "非全绿"路径，好让降级/缺失统计也在离线判据的覆盖里。
    """
    drop = set(x.strip() for x in (os.environ.get(STUB_DROP_ENV) or '').split(',') if x.strip())
    t0 = time.time()
    syms = [{'raw': '600519', 'market': 'cn_stock', 'code': '600519',
             'name': '贵州茅台', 'how': '代码'}]
    calls = [('get_kline', {'symbol': '600519', 'days': 400}, '行情与区间位置（贵州茅台）'),
             ('get_fundamentals', {'symbol': '600519'}, '估值与财务（贵州茅台）'),
             ('get_news', {'symbol': '600519'}, '资讯与热度（贵州茅台）'),
             ('search_terms', {'query': (question or '')[:40]}, '词表命中（本地）'),
             ('analyze_portfolio', {}, '组合集中度（没有持仓就照实说）')]
    if deep:
        calls.append(('predict_kline', {'symbol': '600519', 'pred_len': 20}, 'Kronos 预测'))
    probes, order = {}, []
    for name, args, why in calls:
        probes[name] = _stub_probe(name, args, why, drop=drop)
        order.append(name)
        if name == 'get_kline':
            probes['tech'] = _stub_probe('tech', args, '技术指标（由上面那份日线现算）',
                                         drop=drop)
            order.append('tech')
    graph = {'hits': [], 'walk': None, 'query': (question or '')[:40]}
    if probes['search_terms']['ok'] and not drop & {'search_terms'}:
        s = probes['search_terms']['summary']
        graph['hits'] = [{'name': t.get('name'), 'cat': t.get('category'),
                          'family': t.get('family'), 'fields': t.get('fields') or {}}
                         for t in (s.get('terms') or [])]
        if graph['hits']:
            probes['walk_graph'] = _stub_probe('walk_graph', {'term': graph['hits'][0]['name']},
                                               '顺着图谱走一步（本地）', drop=drop)
            order.append('walk_graph')
            if probes['walk_graph']['ok']:
                graph['walk'] = probes['walk_graph']['summary']
    n_ok = sum(1 for k in order if probes[k]['ok'])
    return {'ok': True, 'ts': '2026-09-22 14:30:00', 'src': 'stub',
            'symbols': syms, 'probes': {k: probes[k] for k in order}, 'order': order,
            'graph': graph, 'notes': ['★这一份是桩证据（LICAI_AGENTS_STUB）'],
            # ★桩只有一只标的 ⇒ planned=1、probed 1 只、没有"未探"。**不补一只假的**。
            'symbols_planned': len(syms),
            'symbols_probed': [s['code'] for s in syms[:1]],
            'symbols_not_probed': [s['name'] for s in syms[1:]],
            'n_probes': len(order), 'n_ok': n_ok,
            'miss_rate': round(1.0 - (n_ok / len(order)), 4) if order else 1.0,
            'missing': [k for k in order if not probes[k]['ok']],
            'ms': int((time.time() - t0) * 1000)}


# 可替换的取数入口（照 `llm.TRANSPORT` 的先例：判据替换这一个名字即可离线跑）。
COLLECT = (lambda q, deep=False: _stub_collect(q, deep)
           if stub_active() else collect(q, deep=deep))


def stub_active():
    return bool(os.environ.get(STUB_ENV))


def probe_of(ev, pid):
    return (ev.get('probes') or {}).get(pid)


def for_role(ev, role):
    """一个角色该看到的探针记录。★按 `ROLE_PROBES` 精确给，不给"全都有"。"""
    p = ev.get('probes') or {}
    return [p[k] for k in ROLE_PROBES.get(role, []) if k in p]


# ── 给提示词的一块文本 + 给事件的引文 ─────────────────────────────────

def _fmt_num(v, nd=4):
    if v is None:
        return '算不出来'
    if isinstance(v, float):
        return ('%.*f' % (nd, v)).rstrip('0').rstrip('.')
    return str(v)


def block_for(role, ev):
    """角色看到的那块「本轮数据」。

    ★格式要求：**每个数都带窗口/口径**（"5 日""年化""报告期 2026-06-30"）。
      裸数字进了提示词，模型就会把它们串成一句看起来专业的错话
      （拿 5 日的涨跌说"近一年趋势"），而这没有任何判据拦得住。
    """
    recs = for_role(ev, role)
    if role == 'psych':
        return ('【本轮数据】\n（**刻意不给行情数据**：投资心理角色的活是讲常见偏差，'
                '不碰价格。给你价格只会把"损失厌恶"讲成"所以你现在不该卖" —— '
                '那是买卖建议，本模块一句话都不许有。）')
    if not recs:
        return '【本轮数据】\n（这一轮没有取到任何数据 —— 那就**直说没取到**，不许编。）'

    lines = []
    for r in recs:
        name = r['id']
        if not r['ok']:
            lines.append('%s：**没取到** —— %s%s'
                         % (PROBE_LABEL.get(name, name), r.get('why_failed') or '（没有原因）',
                            ('（%s）' % r['hint']) if r.get('hint') else ''))
            continue
        s = r['summary'] or {}
        if name == 'get_kline':
            lines.append(
                'get_kline %s %s（源 %s，最后一根 %s，取了 %s 根）\n'
                '  - 收盘 %s；5 日 %s%%；20 日 %s%%；60 日 %s%%\n'
                '  - 区间高/低 %s / %s；年化波动率 %s%%'
                % (s.get('code'), s.get('name'), s.get('source'), s.get('to'),
                   s.get('rows'), _fmt_num(s.get('last_close')),
                   _fmt_num(s.get('chg_pct_5d'), 2), _fmt_num(s.get('chg_pct_20d'), 2),
                   _fmt_num(s.get('chg_pct_60d'), 2), _fmt_num(s.get('range_high')),
                   _fmt_num(s.get('range_low')), _fmt_num(s.get('ann_vol_pct'), 2)))
        elif name == 'tech':
            lines.append(
                '技术指标（用上面那份日线的最后 %s 根现算；`算不出来` = 窗口不够，'
                '**不是 0**；最后一根 %s）\n'
                '  - MA5 %s / MA10 %s / MA20 %s / MA60 %s\n'
                '  - RSI6 %s / RSI12 %s / RSI24 %s\n'
                '  - MACD DIF %s / DEA %s / HIST %s\n'
                '  - KDJ K %s / D %s / J %s\n'
                '  - BOLL 上 %s / 中 %s / 下 %s（带宽 %s）\n'
                '  - 量比(当日量 ÷ 前 5 日均量，**不含当日**，不是交易所口径) %s'
                % (_fmt_num(s.get('rows'), 0), s.get('dt'),
                   *[_fmt_num(s.get(k)) for k in
                     ('ma5', 'ma10', 'ma20', 'ma60', 'rsi6', 'rsi12', 'rsi24',
                      'macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j',
                      'boll_up', 'boll_mid', 'boll_low', 'boll_band', 'vol_ratio_5d')]))
        elif name == 'get_fundamentals':
            lines.append(
                'get_fundamentals %s %s（行业 %s；报告期 %s）\n'
                '  - PE(TTM) %s（**这只票跟自己过去 5 年比**的分位 %s%%，不是跟同行比）\n'
                '  - PB %s（自身 5 年分位 %s%%）；ROE(加权) %s%%；资产负债率 %s%%\n'
                '  - 营收增速 %s%%；净利增速 %s%%；毛利率 %s%%\n'
                '  ★字段为"没有"表示这个口径取不到（如银行没有"主营业务收入增长率"），'
                '**不是 0**；财报是报告期的，不许说成"现在"'
                % (s.get('code'), s.get('name'), s.get('industry') or '没有',
                   s.get('report_date') or '没有', _fmt_num(s.get('pe_ttm'), 2),
                   _fmt_num(s.get('pe_pctile_5y'), 1), _fmt_num(s.get('pb'), 2),
                   _fmt_num(s.get('pb_pctile_5y'), 1), _fmt_num(s.get('roe_weighted'), 1),
                   _fmt_num(s.get('debt_ratio'), 1), _fmt_num(s.get('rev_growth'), 1),
                   _fmt_num(s.get('profit_growth'), 1), _fmt_num(s.get('gross_margin'), 1)))
        elif name == 'get_news':
            items = s.get('items') or []
            lines.append('get_news（只采不析；采到 %s 条，下面列了 %d 条）\n%s'
                         % (s.get('count'), len(items),
                            '\n'.join('  - [%s] %s（heat=%s，来源 %s）'
                                      % (it.get('ts') or '时间没给', it.get('title'),
                                         it.get('heat'), it.get('source')) for it in items)
                            or '  - （一条都没有）'))
        elif name == 'search_terms':
            terms = s.get('terms') or []
            lines.append('search_terms（本地词表，255 个词条）命中 %d 条：%s'
                         % (len(terms),
                            '、'.join('%s（%s）' % (t.get('name'), t.get('category'))
                                     for t in terms) or '一个都没命中 —— 词表里没有这个词，'
                                                        '**不要自己造一个解释**'))
        elif name == 'walk_graph':
            nb = s.get('neighbors') or []
            lines.append('walk_graph %s（关系 %s）：%s'
                         % (s.get('term'), s.get('relation'),
                            '、'.join('%s[%s]' % (n.get('name'), n.get('relation')) for n in nb)
                            or '没有邻居关系'))
        elif name == 'analyze_portfolio':
            lines.append('analyze_portfolio：**没取到** —— %s'
                         % (r.get('why_failed') or '（没有原因）'))
        elif name == 'predict_kline':
            sp = s.get('spread') or {}
            lines.append(
                'predict_kline（Kronos，采了 %s 条路径，%s 根）\n'
                '  - 方向 %s（中心线口径 %s）；预测收盘 %s（基准 %s，%s%%）\n'
                '  - 区间 %s~%s（名义 %s%%，弃答=%s）\n'
                '  ★方向"弃答"时**不许**说涨或跌；区间说的是"模型自己认为的范围"，'
                '不是概率承诺；本站回测已实测该模型没有超额能力'
                % (s.get('sample_count'), s.get('pred_len'), s.get('direction'),
                   s.get('direction_center'), _fmt_num(s.get('pred_last_close')),
                   _fmt_num(s.get('base_close')), _fmt_num(s.get('pred_chg_pct'), 2),
                   _fmt_num(sp.get('lo')), _fmt_num(sp.get('hi')), sp.get('level_pct'),
                   sp.get('withheld')))
        else:
            lines.append('%s：%s' % (name, _fmt_num(s)[:200]))
    return '【本轮数据】（**只能用这里面的数**；这里没有的就是没取到，写"这个数据我没取到"，'\
           '绝对不许自己补一个）\n' + '\n'.join(lines)


def cites_for(role, ev):
    """给 `agent_say.evidence` 的引文列表：一条一个出处，**能指着回去查**。

    ★引文只写**真发生过的**：探针失败也是引文（"没取到"本身就是要传下去的事实）。
      格式统一成 `工具 参数：结论（出处）`，前端和判据都能直接读。
    """
    out = []
    for r in for_role(ev, role):
        s = r['summary'] or {}
        if not r['ok']:
            out.append('%s：没取到 —— %s' % (r['id'], r.get('why_failed') or '（没有原因）'))
            continue
        if r['id'] == 'get_kline':
            out.append('get_kline %s %s：%s 收盘 %s（5 日 %s%%，年化波动 %s%%，源 %s）'
                       % (s.get('code'), s.get('name'), s.get('to'),
                          _fmt_num(s.get('last_close')), _fmt_num(s.get('chg_pct_5d'), 2),
                          _fmt_num(s.get('ann_vol_pct'), 2), s.get('source')))
        elif r['id'] == 'tech':
            out.append('技术指标（由同一份日线现算，%s 根，最后 %s）：MA20 %s，RSI6 %s，'
                       'MACD HIST %s，量比 %s'
                       % (_fmt_num(s.get('rows'), 0), s.get('dt'), _fmt_num(s.get('ma20')),
                          _fmt_num(s.get('rsi6')), _fmt_num(s.get('macd_hist')),
                          _fmt_num(s.get('vol_ratio_5d'))))
        elif r['id'] == 'get_fundamentals':
            out.append('get_fundamentals %s：PE(TTM) %s（自身 5 年分位 %s%%），PB %s，'
                       'ROE %s%%（报告期 %s，源 %s）'
                       % (s.get('code'), _fmt_num(s.get('pe_ttm'), 2),
                          _fmt_num(s.get('pe_pctile_5y'), 1), _fmt_num(s.get('pb'), 2),
                          _fmt_num(s.get('roe_weighted'), 1), s.get('report_date'),
                          '、'.join(s.get('sources') or [])))
        elif r['id'] == 'get_news':
            its = s.get('items') or []
            heat = [i for i in its if (i.get('heat') or 1) > 1]
            out.append('get_news：采到 %s 条（多源同题 heat>1 的 %d 条；源 %s）'
                       % (s.get('count'), len(heat), s.get('as_of')))
        elif r['id'] == 'search_terms':
            out.append('search_terms：%s'
                       % ('、'.join(t.get('name') for t in (s.get('terms') or []))
                          or '词表里没有这个词'))
        elif r['id'] == 'walk_graph':
            out.append('walk_graph %s：%s'
                       % (s.get('term'),
                          '、'.join('%s[%s]' % (n.get('name'), n.get('relation'))
                                   for n in (s.get('neighbors') or []))))
        elif r['id'] == 'predict_kline':
            sp = s.get('spread') or {}
            out.append('predict_kline：方向 %s，%s~%s（名义 %s%%，弃答 %s）'
                       % (s.get('direction'), _fmt_num(sp.get('lo')), _fmt_num(sp.get('hi')),
                          sp.get('level_pct'), sp.get('withheld')))
        else:
            out.append('%s：%s' % (r['id'], _fmt_num(s)[:80]))
    return out


if __name__ == '__main__':
    import json
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else '茅台最近走势怎么样'
    ev = collect(q)
    print('标的：%s' % json.dumps(ev['symbols'], ensure_ascii=False))
    print('探针 %d 个，成功 %d 个，缺失率 %.2f：%s'
          % (ev['n_probes'], ev['n_ok'], ev['miss_rate'], ev['missing']))
    for r in ev['order']:
        p = ev['probes'][r]
        print('  %-18s ok=%-5s %5dms  %s' % (r, p['ok'], p['ms'],
                                             p.get('why_failed') or ''))
    for role in ('technical', 'risk', 'kg_tutor', 'psych'):
        print('\n──── %s 看到的数据 ────' % role)
        print(block_for(role, ev)[:1200])
