"""冒烟验证：`python agent/intraday/_probe.py`（本仓库用 `.venv/bin/python`）。

它干四件事，**四件都必须真的跑出东西**（规格 §5 的交付说明要求把这里的输出贴进去）：

  0. 形状与 JSON 安全：`json.dumps(..., allow_nan=False)` 必须过；规格 §3.2 的字段名
     一个不缺（多出来的是我们额外加的，列出来）；
  1. 覆盖矩阵：九个场景 × 十条策略 → 每个格子是"三个周期各给了什么方向"，
     顺带把 `errors` 的条数打出来（**空白的格子是本探针最有价值的产出**：
     它说明那条策略在什么数据上会整条消失），外加三条**跨场景不变量**
     （(id,period) 不重复 / orb 两个出口互斥 / 同一帧跑两遍比分逐位相同）；
  2. 完整输出：挑两个场景，把 5/15/30 三个周期的信号、composite、volume、levels、
     session **逐条**打出来（含 `why` 原文）；
  3. **手工复核**：至少三个数是**手写循环**独立算的（ORB 区间上下沿、VWAP 及其两个
     求和量、POC 与量守恒、同刻 RVOL 与错口径对照、ATR 的 Wilder 递推、rr 的除法，
     再加 composite 的加权求和）—— 再与引擎报出来的数**逐位比对**。
     ★复核**不许 import 本包的算术**（`algo`/`levels`/`volume_profile`/`regime`）——
       拿实现去验实现是"自己跟自己比"，一定绿（`algo.py` 模块头写了这条纪律）。
       这里只用 pandas/numpy 的原语和自己写的循环。
     ★复核用的帧走 `intraday._fetch()`（与 `analyze()` **同一条取数路径**）⇒ 同一份帧，
       不会出现"复核的帧与引擎的帧不是同一份"这种假红/假绿。
  4. 纪律反例：数据不够时必须**整条消失 + errors 说明缺什么**，不许给默认值。

退出码：有 ✗ ⇒ 非 0（可以直接当判据挂上去）。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT = os.path.dirname(_HERE)                      # <repo>/agent
if _AGENT not in sys.path:
    sys.path.insert(0, _AGENT)

import numpy as np                                   # noqa: E402  只当容器/原语用
import pandas as pd                                  # noqa: E402

import intraday                                      # noqa: E402
from intraday import _synthdata as sd                # noqa: E402
from intraday import sessions                        # noqa: E402
from intraday.algo import StrategySkipped            # noqa: E402

MARKET, CODE = 'cn_stock', '600519'
PERIODS = (5, 15, 30)
SCENARIOS = (None, 'trend_up', 'trend_down', 'range', 'breakout', 'false_break',
             'divergence', 'squeeze', 'full_day')
SID_SHORT = {'orb_break': 'orb', 'orb_fail': 'flr', 'vwap_revert': 'vwa', 'rvol': 'rvo',
             'volume_profile': 'vpf', 'divergence': 'div', 'momentum': 'mom',
             'squeeze': 'sqz', 'resonance': 'res', 'atr': 'atr', 'session': 'ses'}
ORDER = intraday.st.STRATEGY_ORDER
BANNED = ('技术面偏强', '量能配合', '表现良好', '走势健康', '明显', '较强', '较弱', '有望')

_FAILS = []
_N = [0]


def chk(name, cond, detail=''):
    _N[0] += 1
    # detail 可能是 list（errors 那种）—— 直接 `' —— ' + detail` 会 TypeError，
    # 而那正好发生在"检查失败、正要打印原因"的那一刻（错在报错路径上）。
    if not isinstance(detail, str):
        detail = str(detail)
    if cond:
        print('  ✓ %s%s' % (name, (' —— ' + detail) if detail else ''))
    else:
        _FAILS.append(name)
        print('  ✗ %s%s' % (name, (' —— ' + detail) if detail else ''))


def hr(t):
    print('\n' + '=' * 78)
    print(t)
    print('=' * 78)


def side_ch(s):
    return {'long': 'L', 'short': 'S', 'flat': '-', None: '?'}.get(s, '?')


def run(scenario):
    os.environ[intraday.SYNTH_ENV] = '1'
    os.environ[intraday.SYNTH_SCENARIO_ENV] = scenario or 'default'
    return intraday.analyze(MARKET, CODE, periods=PERIODS, name='合成样例')


# ── 0. 形状 / JSON ─────────────────────────────────────────────────────
SPEC_TOP = {'ok', 'market', 'code', 'name', 'ts', 'bars', 'regime', 'signals',
            'composite', 'volume', 'levels', 'session', 'errors'}
EXTRA_TOP = {'periods', 'bars_per_day', 'data_source'}
SPEC_SIG = {'id', 'name', 'period', 'side', 'strength', 'why', 'refs'}


def check_shape(r, tag):
    hr('0. 形状与 JSON 安全（%s）' % tag)
    chk('顶层字段一个不缺', SPEC_TOP <= set(r), '缺 %s' % sorted(SPEC_TOP - set(r)))
    chk('顶层只多了我们声明的三个', set(r) - SPEC_TOP == EXTRA_TOP,
        '多出来的是 %s' % sorted(set(r) - SPEC_TOP))
    try:
        s = json.dumps(r, allow_nan=False, ensure_ascii=False)
        chk('json.dumps(allow_nan=False) 通过', True, '%d 字节' % len(s.encode('utf-8')))
    except ValueError as e:
        chk('json.dumps(allow_nan=False) 通过', False, str(e))
    chk('composite 四件套齐', {'score', 'side', 'confidence', 'agree'} <= set(r['composite']))
    chk('volume 四件套齐',
        {'rvol', 'rvol_by_period', 'profile', 'divergence'} <= set(r['volume']))
    chk('levels 七件套齐' if r['ok'] else 'levels 为 None（取数失败时允许）',
        (r['levels'] is None) if not r['ok'] else
        {'entry', 'stop', 'target1', 'target2', 'atr', 'rr', 'method'} <= set(r['levels']),
        '' if r['levels'] is None else 'side=%s' % r['levels']['side'])
    if r['ok']:
        chk('ok=True 时 ts / session.phase / data_source 都必须有值',
            bool(r['ts']) and bool(r['session']['phase']) and bool(r['data_source']),
            'ts=%s phase=%s src=%s' % (r['ts'], r['session']['phase'], r['data_source']))
        chk('ok=True 时 bars 三个周期都在', len(r['bars']) == len(PERIODS), str(r['bars']))
    chk('regime 取值在枚举内',
        all(v in ('trend_up', 'trend_down', 'range', 'squeeze')
            for v in r['regime'].values()), str(r['regime']))
    bad = [s for s in r['signals'] if set(s) != SPEC_SIG]
    chk('每条信号的键与规格一致（不多不少）', not bad,
        '' if not bad else '越界：%s' % [sorted(set(x) - SPEC_SIG) for x in bad[:2]])
    chk('signal.id 全部在 STRATEGY_ORDER 里',
        all(s['id'] in ORDER for s in r['signals']))
    chk('side 取值合法', all(s['side'] in ('long', 'short', 'flat') for s in r['signals']))
    chk('strength ∈ [0,1]',
        all(0.0 <= float(s['strength']) <= 1.0 for s in r['signals']))
    nodigit = [s['id'] for s in r['signals'] if not any(c.isdigit() for c in s['why'])]
    chk('why 里都有具体数字（"可复核"的硬要求）', not nodigit, '没有数字的：%s' % nodigit)
    banned = [s['id'] for s in r['signals'] if any(b in s['why'] for b in BANNED)]
    chk('why 里没有"技术面偏强"这类不可复核的话', not banned, '命中：%s' % banned)
    chk('errors 是字符串列表', isinstance(r['errors'], list)
        and all(isinstance(x, str) for x in r['errors']))
    # ★这一条要问的是两件事，分开断言：
    #   (a) 任何**给了方向**（side≠flat）的信号，必须在那张权重表里有条目
    #       —— 没权重的方向票会凭空消失（composite 只对 SIGNAL_W 里的 id 加权）；
    #   (b) atr 那条**永远**是 flat（它只给价位不给方向，见 __init__.py 权重表注释）。
    #   以前这里写成"votable + 没权重的非 flat 条的条数 == 总条数"—— 那个等式
    #   恒真（左边两项按定义就划分了全集），所以它**永远绿**，测不出任何东西。
    nv = [s for s in r['signals'] if s['side'] != 'flat']
    unweighted = sorted({s['id'] for s in nv} - set(intraday.SIGNAL_W))
    chk('给了方向的 id 都在权重表里', not unweighted, '没权重却给了方向：%s' % unweighted)
    atr_nonflat = [s for s in r['signals'] if s['id'] == 'atr' and s['side'] != 'flat']
    chk('atr 那条只给价位、不给方向（side 恒为 flat）', not atr_nonflat,
        'SIGNAL_W 共 %d 项（和为 %.2f）' % (len(intraday.SIGNAL_W),
                                          sum(intraday.SIGNAL_W.values())))
    chk('方向信号条数 ≥ 1（每周期至少有一条非 flat 才算真跑过）', len(nv) >= 1,
        '本轮 %d 条' % len(nv))
    chk('SIGNAL_W 里没有 atr', 'atr' not in intraday.SIGNAL_W)
    return r


# ── 1. 覆盖矩阵 ────────────────────────────────────────────────────────
CROSS = ('resonance',)      # period=None 的跨周期策略：一轮只有**一条**信号，不占三个周期格


def coverage():
    hr('1. 覆盖矩阵：%d 个场景 × 十条策略' % len(SCENARIOS))
    print('  格子 = 三个周期各给了什么方向（%s 顺序）；'
          'L=long S=short -=flat ~=这条策略整条消失' % '/'.join(str(p) for p in PERIODS))
    print('  res 列只有一格（~ %s 是**跨周期**策略，它的 period 是 null，不按周期拆）'
          % '/'.join(CROSS))
    print('  orb/flr 是**同一条策略的两个互斥出口**（收盘突破 / 假突破掉回）：某一格是 ~ '
          '表示那条出口本轮没触发，**不代表 ORB 整条消失** —— 同一周期上两者永不同时出现')
    print('  after = 当天的量是"同一时刻"历史均量的几倍（5 分钟周期那根）\n')
    hdr = '  %-12s %-4s' % ('场景', 'ok') + ''.join('%-6s' % SID_SHORT[k] for k in ORDER) \
          + '%-7s %s' % ('errors', 'rvol(同刻)')
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))
    seen = {k: set() for k in ORDER}
    dup, mutex = [], []
    first_pass = {}          # 场景 → 第一遍的 (composite, signals) 序列化
    for sc in SCENARIOS:
        r = run(sc)
        # ★两条**跨场景**的不变量（形状那节只看得见一个场景，看不见这些）：
        #   (a) 同一个 (id, period) 只能有一条 —— 重复会被 composite 数两遍票；
        #   (b) orb_break 与 orb_fail 在同一个周期上互斥（同一条策略的两个出口）。
        keys = [(s['id'], s['period']) for s in r['signals']]
        d = {k for k in keys if keys.count(k) > 1}
        if d:
            dup.append('%s:%s' % (sc or 'default', sorted(d)))
        for p in PERIODS:
            ids = {s['id'] for s in r['signals'] if s['period'] == p}
            if 'orb_break' in ids and 'orb_fail' in ids:
                mutex.append('%s/%s分钟' % (sc or 'default', p))
        # 对比分要防的账：同一帧跑两遍，比分必须逐位相同（不然比分是个随机数）
        hv = json.dumps((r['composite'], r['signals']), ensure_ascii=False, allow_nan=False)
        first_pass[sc or 'default'] = hv
        cells = {}
        for k in ORDER:
            if k in CROSS:
                # 跨周期那条：命中就是命中，与周期无关 ⇒ 只印一个字符（居中）
                hit = [s for s in r['signals'] if s['id'] == k]
                cells[k] = (' %s ' % side_ch(hit[0]['side'])) if hit else ' ~ '
                if hit and hit[0]['side'] != 'flat':
                    seen[k].add(hit[0]['side'])
                continue
            cell = ''
            for p in PERIODS:
                hit = [s for s in r['signals'] if s['id'] == k and s['period'] == p]
                if not hit:
                    cell += '~'
                else:
                    cell += side_ch(hit[0]['side'])
                    if hit[0]['side'] != 'flat':
                        seen[k].add(hit[0]['side'])
            cells[k] = cell
        print('  %-12s %-4s' % (sc or 'default', 'Y' if r['ok'] else 'N')
              + ''.join('%-6s' % cells[k] for k in ORDER)
              + '%-7d %s' % (len(r['errors']), r['volume']['rvol']))
    print()
    # ★三条**跨场景**的不变量（形状那一节只看得到一个场景，看不见这些）：
    print('  ── 跨场景不变量')
    chk('同一个 (id, period) 只有一条信号（重复会被 composite 数两遍票）',
        not dup, '重复的：%s' % dup)
    chk('orb_break / orb_fail 在同一周期上互斥（同一条策略的两个出口）',
        not mutex, '同时出现的：%s' % mutex)
    # 比分必须是**确定的**：同一帧跑两遍，composite 与 signals 要逐字节相同。
    # 合成源是显式播种的（`_synthdata._seed`：crc32、不含 period），所以跑第二遍就能
    # 断言；不相等意味着链路上混进了时间/随机数/字典序之类的东西 —— 那会让
    # "复现一次给我看"这句话永远做不到，而这一条**只能靠跑两遍**才发现。
    again = {}
    for sc in SCENARIOS:
        r2 = run(sc)
        again[sc or 'default'] = json.dumps((r2['composite'], r2['signals']),
                                            ensure_ascii=False, allow_nan=False)
    drift = sorted(k for k in again if again[k] != first_pass[k])
    chk('同一帧跑两遍 ⇒ composite 与 signals 逐字节相同', not drift,
        '两遍不一致的场景：%s' % drift)
    print()
    for k in ORDER:
        if k == 'atr':
            print('  %-14s %s' % (k, '按设计**永远 flat**（只给价位不给方向，不进投票）'
                                       '—— 它的方向由 composite 决定，见 levels 块'))
        elif k in CROSS:
            got = '有方向' if seen[k] else '**本轮所有场景一个方向都没给过**'
            print('  %-14s %s（period=null，不按周期拆）' % (k, got))
        else:
            got = '有方向' if seen[k] else '**本轮所有场景一个方向都没给过**'
            print('  %-14s %s' % (k, got))
    return seen


# ── 2. 完整输出 ────────────────────────────────────────────────────────
def dump(r, tag):
    hr('2. 完整输出：场景 = %s' % tag)
    print('ok=%s  market=%s  code=%s  name=%r' % (r['ok'], r['market'], r['code'], r['name']))
    print('ts=%s  periods=%s  bars=%s  bars_per_day=%s  data_source=%s'
          % (r['ts'], r['periods'], r['bars'], r['bars_per_day'], r['data_source']))
    print('regime=%s' % r['regime'])
    print('session.phase=%s  window=%s' % (r['session']['phase'], r['session']['window']))
    print('  note: %s' % r['session']['note'])
    print('\n-- signals（%d 条）' % len(r['signals']))
    for s in r['signals']:
        p = '跨周期' if s['period'] is None else '%s分钟' % s['period']
        print('\n  [%s] %s（%s）  side=%s  strength=%.3f'
              % (p, s['name'], s['id'], s['side'], s['strength']))
        print('    why : %s' % s['why'])
        print('    refs: %s' % json.dumps(s['refs'], ensure_ascii=False, allow_nan=False))
    c = r['composite']
    print('\n-- composite: score=%s side=%s confidence=%s agree=%s'
          % (c['score'], c['side'], c['confidence'], c['agree']))
    print('   %s' % c['method'])
    v = r['volume']
    print('\n-- volume: rvol=%s（周期 %s分钟）  rvol_by_period=%s'
          % (v['rvol'], v.get('rvol_period'), v['rvol_by_period']))
    print('   ★错口径对照 rvol_naive_by_period=%s' % v['rvol_naive_by_period'])
    print('   base=%s' % v.get('rvol_base'))
    p = v['profile']
    if p:
        print('   profile: session=%s bars=%d bins=%d range=%s POC=%s（占量 %.1f%%）'
              % (p['session'], p['bars'], p['bins'], p['range'], p['poc'],
                 (p['poc_vol_share'] or 0) * 100))
        print('            价值区(70%%) = [%s, %s]（实覆盖 %.1f%%）  HVN=%s  LVN=%s'
              % (p['va_lo'], p['va_hi'], (p['va_share'] or 0) * 100, p['hvn'], p['lvn']))
    else:
        print('   profile: None')
    print('   divergence=%s' % v['divergence'])
    L = r['levels']
    if L:
        print('\n-- levels: entry=%s stop=%s target1=%s target2=%s atr=%s rr=%s side=%s'
              % (L['entry'], L['stop'], L['target1'], L['target2'], L['atr'], L['rr'],
                 L['side']))
        print('   composite_side=%s（levels.side 是它的兜底后的口径）risk=%s（%.3f%%）'
              ' atr_pct=%s floored=%s'
              % (L['composite_side'], L['risk'], (L['risk_pct'] or 0) * 100, L['atr_pct'],
                 L['floored']))
        print('   method: %s' % L['method'])
        print('   两臂都在: long=%s' % L['long'])
        print('             short=%s' % L['short'])
    else:
        print('\n-- levels: None')
    print('\n-- errors（%d 条）' % len(r['errors']))
    for e in r['errors']:
        print('   · %s' % e)
    if not r['errors']:
        print('   （空）')


# ── 3. 手工复核 ────────────────────────────────────────────────────────
def _day_rows(df, market, which=-1):
    """最后一个交易日的行（手写：按日期分组取最后一组，不调本包的 last_session）。"""
    d = pd.to_datetime(df['dt']).dt.normalize()
    days = sorted(set(d))
    return df[(d == days[which]).values]


def hand_orbit(frame1, frame_p):
    """[3.1] ORB 区间 = 当天第 1 根 K 的 [最低, 最高]，用 **1 分钟帧** 手写聚合核对。"""
    d1 = _day_rows(frame1, MARKET)
    day = d1['dt'].dt.normalize().iloc[-1]
    t0 = day + pd.Timedelta(minutes=570)        # 09:30（A 股开盘那一分钟）
    t1 = t0 + pd.Timedelta(minutes=5)           # 09:35（5 分钟那根的范围）
    win = d1[(d1['dt'] > t0) & (d1['dt'] <= t1)]
    lo, hi = float(win['low'].min()), float(win['high'].max())
    dp = _day_rows(frame_p, MARKET)
    bar0 = dp.iloc[0]
    return {'hand_lo': lo, 'hand_hi': hi, 'hand_open': float(bar0['open']),
            'frame_lo': float(bar0['low']), 'frame_hi': float(bar0['high']),
            'n_1min': int(len(win)), 'window': '%s ~ %s' % (t0, t1)}


def hand_vwap(frame_p):
    """[3.2] VWAP = Σ(典型价×量)/Σ量，典型价=(h+l+c)/3，按日重置。手写循环。"""
    dp = _day_rows(frame_p, MARKET)
    num = 0.0
    den = 0.0
    for _, row in dp.iterrows():
        tp = (float(row['high']) + float(row['low']) + float(row['close'])) / 3.0
        v = float(row['volume'])
        num += tp * v
        den += v
    return {'num': num, 'den': den, 'vwap': (num / den) if den else None,
            'bars': int(len(dp)),
            'close': float(dp['close'].iloc[-1])}


def hand_poc(frame_p, bins=24):
    """[3.3] POC：24 桶、每根按价格重叠把量摊到它覆盖的各桶。手写循环 + 守恒。"""
    dp = _day_rows(frame_p, MARKET)
    lo = float(dp['low'].min())
    hi = float(dp['high'].max())
    edges = [lo + (hi - lo) * i / bins for i in range(bins + 1)]
    vol = [0.0] * bins

    def idx(x):
        for b in range(bins):
            if x < edges[b + 1]:
                return b
        return bins - 1

    for _, row in dp.iterrows():
        h, l, v = float(row['high']), float(row['low']), float(row['volume'])
        if h <= l:
            vol[idx(h)] += v
            continue
        span = h - l
        for b in range(idx(l), idx(h) + 1):
            ov = min(h, edges[b + 1]) - max(l, edges[b])
            if ov > 0:
                vol[b] += v * ov / span
    total = sum(vol)
    best = 0
    for b in range(1, bins):
        if vol[b] > vol[best]:
            best = b
    return {'poc': (edges[best] + edges[best + 1]) / 2.0, 'bin_vol': vol[best],
            'total_bins': total, 'total_bars': float(dp['volume'].sum()),
            'range': (lo, hi), 'bars': int(len(dp))}


def hand_rvol(df_p):
    """[3.4] 同刻 RVOL 与"错口径"对照。手写：按 (日, 时刻) 分组。"""
    tod = (pd.to_datetime(df_p['dt']).dt.hour * 60 + pd.to_datetime(df_p['dt']).dt.minute)
    day = pd.to_datetime(df_p['dt']).dt.normalize()
    v = pd.to_numeric(df_p['volume'])
    today = sorted(set(day))[-1]
    last_tod = int(tod.iloc[-1])
    same = [float(v.iloc[i]) for i in range(len(v))
            if day.iloc[i] != today and int(tod.iloc[i]) == last_tod]
    hist = [float(v.iloc[i]) for i in range(len(v)) if day.iloc[i] != today]
    last_vol = float(v.iloc[-1])
    return {'last_vol': last_vol, 'last_tod': last_tod,
            'same_slot_n': len(same),
            'same_slot_mean': (sum(same) / len(same)) if same else None,
            'rvol': (last_vol / (sum(same) / len(same))) if same else None,
            'naive_mean': (sum(hist) / len(hist)) if hist else None,
            'naive': (last_vol / (sum(hist) / len(hist))) if hist else None}


def hand_atr(df_p, n=14):
    """[3.5] ATR：TR = max(h-l, |h-pc|, |l-pc|)（含跳空），Wilder 递推 alpha=1/n。
    手写：种子取第一根的 TR，之后 a += (tr - a)/n（= pandas ewm(adjust=False) 的口径）。"""
    h = pd.to_numeric(df_p['high']).tolist()
    l = pd.to_numeric(df_p['low']).tolist()
    c = pd.to_numeric(df_p['close']).tolist()
    trs = [h[0] - l[0]]
    for i in range(1, len(df_p)):
        pc = c[i - 1]
        trs.append(max(h[i] - l[i], abs(h[i] - pc), abs(l[i] - pc)))
    a = trs[0]
    for i in range(1, len(trs)):
        a += (trs[i] - a) / float(n)
    return {'atr': a, 'tr_last': trs[-1], 'bars': len(trs)}


def hand_checks(r, tag):
    hr('3. 手工复核（手写循环，不 import 本包的算术）—— 场景 = %s' % tag)
    mkt = r['market']
    days = {p: intraday._days_for(mkt, p) for p in PERIODS}
    f1, _ = intraday._fetch(mkt, CODE, 1, days[5])       # 1 分钟帧（与引擎同一条取数路径）
    f5, _ = intraday._fetch(mkt, CODE, 5, days[5])
    f15, _ = intraday._fetch(mkt, CODE, 15, days[15])
    ref = {}
    for s in r['signals']:
        ref.setdefault((s['id'], s['period']), s)
    print('  帧：1分钟 %d 行 / 5分钟 %d 行（%d 天）/ 15分钟 %d 行'
          % (len(f1), len(f5), pd.to_datetime(f5['dt']).dt.normalize().nunique(), len(f15)))

    # [3.1] ORB
    print('\n  [3.1] ORB 开盘区间上下沿（5 分钟，当天第 1 根）')
    o = hand_orbit(f1, f5)
    sig = ref.get(('orb_break', 5))
    print('   手写：1 分钟帧里 %s 那 %d 根的 [最低, 最高] = [%.4f, %.4f]'
          % (o['window'], o['n_1min'], o['hand_lo'], o['hand_hi']))
    print('   帧里：5 分钟帧当天第 1 根的 [low, high]      = [%.4f, %.4f]'
          % (o['frame_lo'], o['frame_hi']))
    if sig:
        print('   引擎：orb_break refs or_lo=%s or_hi=%s window=%s'
              % (sig['refs'].get('or_lo'), sig['refs'].get('or_hi'), sig['refs'].get('window')))
        chk('ORB 区间下沿 手写==引擎', abs(o['hand_lo'] - float(sig['refs']['or_lo'])) < 5e-5,
            '%.4f vs %s' % (o['hand_lo'], sig['refs']['or_lo']))
        chk('ORB 区间上沿 手写==引擎', abs(o['hand_hi'] - float(sig['refs']['or_hi'])) < 5e-5,
            '%.4f vs %s' % (o['hand_hi'], sig['refs']['or_hi']))
    chk('ORB 区间 手写==5分钟帧原始行',
        abs(o['hand_lo'] - o['frame_lo']) < 5e-5 and abs(o['hand_hi'] - o['frame_hi']) < 5e-5)

    # [3.2] VWAP
    print('\n  [3.2] VWAP = Σ(典型价×量)/Σ量（5 分钟，当天 %d 根；典型价=(高+低+收)/3）'
          % hand_vwap(f5)['bars'])
    w = hand_vwap(f5)
    vs = ref.get(('vwap_revert', 5))
    print('   手写：Σ典型价×量 = %.2f，Σ量 = %.2f ⇒ VWAP = %.6f'
          % (w['num'], w['den'], w['vwap']))
    if vs:
        rr = vs['refs']
        print('   引擎：sum_tp_x_vol=%s  sum_vol=%s  vwap=%s  最新收盘=%s'
              % (rr.get('sum_tp_x_vol'), rr.get('sum_vol'), rr.get('vwap'), rr.get('close')))
        chk('VWAP 分子（Σ典型价×量）手写==引擎',
            abs(w['num'] - float(rr['sum_tp_x_vol'])) < max(1.0, abs(w['num']) * 1e-6),
            '%.2f vs %s' % (w['num'], rr['sum_tp_x_vol']))
        chk('VWAP 分母（Σ量）手写==引擎',
            abs(w['den'] - float(rr['sum_vol'])) < 1.0, '%.2f vs %s' % (w['den'], rr['sum_vol']))
        chk('VWAP 本身 手写==引擎', abs(w['vwap'] - float(rr['vwap'])) < 5e-5,
            '%.6f vs %s' % (w['vwap'], rr['vwap']))
        # z 的分母用"成交量加权 σ"，这里只复核 z 的**方向**与符号：z<0 ⇔ 收盘在 VWAP 下方
        z = float(rr['z'])
        chk('z 的符号与"收盘 vs VWAP"一致',
            (z > 0) == (w['close'] > w['vwap']), 'z=%s close=%.4f vwap=%.4f'
            % (rr['z'], w['close'], w['vwap']))

    # [3.3] POC + 量守恒
    print('\n  [3.3] 成交量分布 POC（24 桶、按价格重叠摊量）')
    p = hand_poc(f5)
    prof = r['volume']['profile']
    print('   手写：区间 [%.4f, %.4f] 分 24 桶，POC 桶量 %.1f ⇒ POC = %.6f'
          % (p['range'][0], p['range'][1], p['bin_vol'], p['poc']))
    if prof:
        print('   引擎：range=%s POC=%s（占当日量 %.1f%%）'
              % (prof['range'], prof['poc'], (prof['poc_vol_share'] or 0) * 100))
        chk('POC 手写==引擎', abs(p['poc'] - float(prof['poc'])) < 5e-5,
            '%.6f vs %s' % (p['poc'], prof['poc']))
        chk('量守恒：Σ桶量 == 当天总量',
            abs(p['total_bins'] - p['total_bars']) < max(1.0, p['total_bars'] * 1e-9),
            '%.1f vs %.1f（差 %.6f）' % (p['total_bins'], p['total_bars'],
                                         p['total_bins'] - p['total_bars']))
        chk('引擎报的 total_volume == 帧里当天总量',
            abs(float(prof['total_volume']) - p['total_bars']) < 1.0,
            '%s vs %.1f' % (prof['total_volume'], p['total_bars']))
        # 价值区门槛复核：引擎报的 va_share 必须 ≥ 70%（连续扩张法追到够为止）
        chk('价值区实覆盖 ≥ 70%（VA_PCT）', (prof['va_share'] or 0) >= 0.70 - 1e-9,
            '%.4f' % (prof['va_share'] or 0))
        chk('POC 落在价值区内', float(prof['va_lo']) <= float(prof['poc'])
            <= float(prof['va_hi']))

    # [3.4] RVOL
    print('\n  [3.4] RVOL：**同一时刻** vs 错口径（这一节是本模块最容易做错的地方）')
    rv = hand_rvol(f5)
    print('   手写：最新那根（第 %d 分钟）量 = %.0f；同一时刻的历史样本 %d 个、均值 %.0f'
          % (rv['last_tod'], rv['last_vol'], rv['same_slot_n'], rv['same_slot_mean'] or 0))
    print('          ⇒ 同刻 RVOL = %.4f' % (rv['rvol'] or 0))
    print('   手写：错口径（比"当天之前全部分钟"的均量 %.0f）⇒ %.4f 倍'
          % (rv['naive_mean'] or 0, rv['naive'] or 0))
    print('   引擎：rvol_by_period=%s  rvol_naive_by_period=%s'
          % (r['volume']['rvol_by_period'], r['volume']['rvol_naive_by_period']))
    eng = r['volume']['rvol_by_period'].get('5')
    chk('同刻 RVOL 手写==引擎', eng is not None and abs(float(eng) - rv['rvol']) < 5e-4,
        '%s vs %.4f' % (eng, rv['rvol']))
    chk('同刻口径与错口径**确实不同**（U 型的证据）',
        rv['rvol'] is not None and rv['naive'] is not None
        and abs(rv['rvol'] - rv['naive']) > 1e-6,
        '同刻 %.3f vs 错口径 %.3f（差 %.2f 倍）'
        % (rv['rvol'], rv['naive'], abs(rv['rvol'] - rv['naive'])))
    chk('同刻样本数 ≥ 2（RVOL_MIN_SLOT_SAMPLES）', rv['same_slot_n'] >= 2)

    # [3.5] ATR + rr
    print('\n  [3.5] ATR 的 Wilder 递推（alpha=1/14、含跳空）与 rr 的除法')
    a = hand_atr(f5)
    ref_atr = (ref.get(('atr', 5)) or {}).get('refs', {}).get('atr')
    print('   手写：最后一根 TR = %.4f；递推 %d 根（种子=首根 TR，之后 a+=(tr-a)/14）⇒ ATR14 = %.6f'
          % (a['tr_last'], a['bars'], a['atr']))
    print('   引擎：atr refs atr=%s' % ref_atr)
    chk('ATR14 手写==引擎', ref_atr is not None and abs(float(ref_atr) - a['atr']) < 5e-4,
        '%s vs %.6f' % (ref_atr, a['atr']))
    L = r['levels']
    if L and L['rr'] is not None:
        hand_rr = abs(float(L['target1']) - float(L['entry'])) / abs(float(L['entry'])
                                                                     - float(L['stop']))
        print('   手写 rr = |目标1-入场| / |入场-止损| = %.4f' % hand_rr)
        print('   引擎 rr = %s（风险 %s = 价的 %.3f%%）'
              % (L['rr'], L['risk'], (L['risk_pct'] or 0) * 100))
        chk('rr 手写==引擎', abs(hand_rr - float(L['rr'])) < 5e-3,
            '%.4f vs %s' % (hand_rr, L['rr']))
        # 止损必须真的在入场下方（long 口径）/ 上方（short 口径）
        chk('止损在入场价的对的一侧（%s）' % L['side'],
            (float(L['stop']) < float(L['entry'])) if L['side'] == 'long'
            else (float(L['stop']) > float(L['entry'])))
        chk('目标1/目标2 在同一侧且 target2 更远',
            (float(L['target1']) > float(L['entry']) > float(L['stop']))
            if L['side'] == 'long' else
            (float(L['target1']) < float(L['entry']) < float(L['stop'])))

    # [3.6] composite：拿打印出来的每一票手算一遍
    #   （这一条是**用户真正看到的那个数**；它错了，前面十条再对也没用）
    print('\n  [3.6] composite 比分：Σ(权重×方向×强度)/Σ(在场权重)')
    c = r['composite']
    W = intraday.SIGNAL_W
    num = den = 0.0
    parts = []
    for s in r['signals']:
        if s['side'] == 'flat' or s['id'] not in W:
            continue
        sign = 1.0 if s['side'] == 'long' else -1.0
        num += W[s['id']] * sign * float(s['strength'])
        den += W[s['id']]
        parts.append('%s(%s)×%+.3f×%.3f' % (s['id'], s['side'][0], sign, float(s['strength'])))
    hand_score = num / den if den else 0.0
    print('   手写：分子 %+.6f / 分母 %.4f = %.6f（%d 票在场）'
          % (num, den, hand_score, len(parts)))
    print('   引擎：score=%s（在场权重 %.4f，n_signals=%s/%s）'
          % (c['score'], c['vote_weight'], c['n_signals'], c['n_expected']))
    chk('composite score 手写==引擎', abs(hand_score - float(c['score'])) < 5e-5,
        '%.6f vs %s' % (hand_score, c['score']))
    chk('composite.score 与 side 一致（死区 ±%.2f）' % intraday.COMPOSITE_DEAD_ZONE,
        (c['side'] == 'long' and c['score'] > intraday.COMPOSITE_DEAD_ZONE)
        or (c['side'] == 'short' and c['score'] < -intraday.COMPOSITE_DEAD_ZONE)
        or (c['side'] == 'flat' and abs(c['score']) <= intraday.COMPOSITE_DEAD_ZONE))
    agreeing = c['agree']['long'] + c['agree']['short']
    share = (max(c['agree']['long'], c['agree']['short']) / agreeing) if agreeing else 0.0
    cover = len(r['signals']) / float(c['n_expected'])
    hand_conf = (intraday.CONF_W_SCORE * min(1.0, abs(c['score']))
                 + intraday.CONF_W_AGREE * min(1.0, share)
                 + intraday.CONF_W_COVER * min(1.0, cover))
    print('   手写 confidence = %.2f×%.4f + %.2f×%.4f + %.2f×%.4f = %.6f'
          % (intraday.CONF_W_SCORE, abs(c['score']), intraday.CONF_W_AGREE, share,
             intraday.CONF_W_COVER, cover, hand_conf))
    chk('composite confidence 手写==引擎', abs(hand_conf - float(c['confidence'])) < 5e-5,
        '%.6f vs %s' % (hand_conf, c['confidence']))
    chk('composite 权重表之和 == 1.00', abs(sum(W.values()) - 1.0) < 1e-9,
        '%.4f' % sum(W.values()))


# ── 4. 纪律反例 ────────────────────────────────────────────────────────
def negatives():
    hr('4. 纪律反例：数据不够 ⇒ 整条消失 + errors 说清缺什么（不许给默认值）')
    tiny = pd.DataFrame({
        'dt': pd.to_datetime(['2026-09-18 09:35', '2026-09-18 09:40']),
        'open': [10.0, 10.1], 'high': [10.1, 10.2], 'low': [9.9, 10.0],
        'close': [10.1, 10.05], 'volume': [1000, 1200], 'amount': [10000.0, 12000.0]})
    cases = [
        ('orb（2 根）', lambda: intraday.st.orb(tiny, 5, MARKET)),
        ('momentum（2 根）', lambda: intraday.st.momentum(tiny, 5, MARKET)),
        ('regime.classify（2 根）', lambda: intraday.regime.classify(tiny)),
        ('profile（当天 2 根 < VP_MIN_BARS=6）',
         lambda: intraday.vp.profile(tiny, MARKET)),
        ('rvol_by_tod（只有 1 天）', lambda: intraday.vp.rvol_by_tod(tiny, MARKET)),
        ('divergence（2 根）', lambda: intraday.vp.divergence(tiny)),
        ('resonance（只有一个周期）', lambda: intraday.st.resonance({5: 0.5})),
    ]
    for name, fn in cases:
        try:
            out = fn()
            chk('%s 应当抛 StrategySkipped' % name, False, '但它返回了 %r' % (out,))
        except StrategySkipped as e:
            chk('%s ⇒ 整条消失' % name, True, e.reason)
        except Exception as e:                       # noqa: BLE001
            chk('%s 抛了**不是** StrategySkipped 的异常' % name, False,
                '%s: %s' % (type(e).__name__, e))

    # 一字板：高低相等 ⇒ ORB 没有区间可言
    flat = pd.DataFrame({
        'dt': pd.to_datetime(['2026-09-18 09:35'] * 3 + ['2026-09-18 09:40'] * 3),
        'open': [10.0] * 6, 'high': [10.0] * 6, 'low': [10.0] * 6,
        'close': [10.0] * 6, 'volume': [1, 1, 1, 1, 1, 1], 'amount': [10.0] * 6})
    try:
        intraday.st.orb(flat, 5, MARKET)
        chk('一字板（高低相等）应当整条消失', False)
    except StrategySkipped as e:
        chk('一字板（高低相等）⇒ 整条消失', True, e.reason)
    except Exception as e:                           # noqa: BLE001
        chk('一字板（高低相等）抛了别的异常', False, '%s: %s' % (type(e).__name__, e))

    # 真接口取不到时：ok=False，**不用合成帧顶上**
    os.environ.pop(intraday.SYNTH_ENV, None)
    try:
        import datasource  # noqa: F401
        have_ds = True
    except Exception:                                # noqa: BLE001
        have_ds = False
    r = intraday.analyze(MARKET, CODE, periods=PERIODS)
    if not have_ds:
        chk('没有 datasource 且没设 %s ⇒ ok=False' % intraday.SYNTH_ENV,
            r['ok'] is False, 'ok=%s' % r['ok'])
        chk('  ∧ 原因是"没有数据源"而不是"没有信号"',
            any('datasource' in e for e in r['errors']),
            (r['errors'] or ['(errors 是空的)'])[0][:110])
    else:
        print('\n  · datasource 已在（并行施工的那个包）⇒ 走一次**真接口**')
        print('    真接口本轮 ok=%s  data_source=%r  errors=%d 条'
              % (r['ok'], r['data_source'], len(r['errors'])))
        for e in (r['errors'] or ['(空)'])[:3]:
            print('      · %s' % e[:110])
    # ★这两条**不假设真接口一定失败**（上游哪天通了，它们照样要成立）——
    #   问的是"取不到时有没有说实话"，是一个条件式契约，不是"它必须取不到"。
    if not r['ok']:
        chk('  ∧ 取不到时 signals 必须是空的（不许有默认信号）', r['signals'] == [])
        chk('  ∧ 取不到时 data_source 必须是 None（不许标成 SYNTH）',
            r['data_source'] is None, 'data_source=%r' % r['data_source'])
        chk('  ∧ 取不到时 errors 必须非空（不许静默返回一个空壳）', len(r['errors']) >= 1,
            (r['errors'] or ['(errors 是空的)'])[0][:110])
    r = intraday.analyze('不认识的market', CODE)
    chk('不认识的市场 ⇒ ok=False + 人话原因',
        (not r['ok']) and any('不认识的市场' in e for e in r['errors']), r['errors'])
    # 7 分钟这类非标准周期：**合成源能出**（`_synthdata._aggregate` 与标准周期同一套
    # 代码，任意 period 都行）；真接口按规格只认 {1,5,15,30,60}，那种情况下必须
    # ok=False 并说明 —— 两条路各测各的，别拿一条的结论盖另一条。
    os.environ[intraday.SYNTH_ENV] = '1'
    r7 = intraday.analyze(MARKET, CODE, periods=(7,))
    chk('合成源下任意周期（7 分钟）能出帧 + 能跑完',
        bool(r7['ok']) and r7['bars'].get('7', 0) > 0,
        'ok=%s bars=%s errors=%s' % (r7['ok'], r7['bars'], len(r7['errors'])))
    chk('  ∧ 非标准周期也不会崩（真接口取不到就是 ok=False + 有原因）',
        bool(r7['ok']) or len(r7['errors']) >= 1, 'ok=%s' % r7['ok'])


def main():
    hr('agent/intraday 冒烟探针 —— 全部数据为**合成**（_synthdata），不是行情')
    print('  python=%s  pandas=%s  numpy=%s' % (sys.version.split()[0], pd.__version__,
                                                np.__version__))
    print('  合成开关 %s=1（本探针显式设置；不设时 analyze() 走真 datasource）'
          % intraday.SYNTH_ENV)
    r = run('breakout')
    check_shape(r, 'breakout')
    seen = coverage()
    for tag in ('breakout', 'squeeze'):
        dump(run(tag), tag)
    hand_checks(run('breakout'), 'breakout')
    negatives()
    hr('汇总')
    print('  检查项 %d 条，失败 %d 条' % (_N[0], len(_FAILS)))
    if _FAILS:
        for f in _FAILS:
            print('  ✗ %s' % f)
        return 1
    print('  ✓ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
