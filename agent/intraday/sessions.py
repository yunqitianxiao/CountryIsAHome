"""交易时段 / 相位（phase）：把一根 K 线的时刻翻译成"在当天的哪个阶段"。

★这个模块的存在理由：**日内的一切都分时段**。
  · 成交量是 U 型的（开盘/收盘两头大、午盘小）⇒ 拿早盘的量去比全天的均量，
    算出来"天天都放量"（见 `volume_profile.rvol_by_tod` 那条注释）；
  · 波动率也是 U 型的 ⇒ "现在这根振幅大"这句话必须带时段才有意义；
  · A 股还有 T+1、涨跌停、集合竞价、午休 —— 这些不是注释里的花边，
    它们直接决定**哪些 K 线根本不存在**（午休 11:30-13:00 没有 K 线，
    所以"5 分钟 K 一天 48 根"这个数是 240/5 来的，不是 24 小时来的）。

★口径（写死在这里，因为换个口径不会有任何东西变红）：
  · 一根 K 线的时刻取它的**结束**时刻（国内行情软件的分钟 K 就是收盘时刻标签：
    09:30-09:35 那根标 09:35）。**上游如果按开始时刻标签，phase 会整体前移一格**
    —— 这是已知的跨源风险，写在交付说明的"没验的"那节里。
  · `dt` 若是 tz-aware，先换算到该市场的时区；若是 naive，**当作交易所本地时间**
    （adata/efinance 给的都是本地时间）。
"""
from __future__ import annotations

import pandas as pd

# ── 各市场的交易日结构 ───────────────────────────────────────────────────
# open/close/breaks 都是"当天的第几分钟"（0 = 00:00），breaks 是**休市区间** [起, 止)。
# limit = 涨跌停幅度（None = 没有涨跌停）；t1 = 当日买入次日才可卖。
# ★`crypto` 是 7×24：没有开盘/收盘，"开盘 30 分钟"在那上面没有定义 ⇒ 见 `phase_of`。
MARKETS = {
    'cn_stock': dict(open=570, close=900, breaks=((690, 780),), tz='Asia/Shanghai',
                     limit=0.10, t1=True, cur='CNY'),
    'cn_index': dict(open=570, close=900, breaks=((690, 780),), tz='Asia/Shanghai',
                     limit=None, t1=False, cur='CNY'),
    'cn_etf': dict(open=570, close=900, breaks=((690, 780),), tz='Asia/Shanghai',
                   limit=0.10, t1=True, cur='CNY'),
    'hk_stock': dict(open=570, close=960, breaks=((720, 780),), tz='Asia/Hong_Kong',
                     limit=None, t1=False, cur='HKD'),
    'us_stock': dict(open=570, close=960, breaks=(), tz='America/New_York',
                     limit=None, t1=False, cur='USD'),
    'crypto': dict(open=0, close=1440, breaks=(), tz='UTC',
                   limit=None, t1=False, cur='USDT'),
}
# datasource 的 `realtime(market='cn')` 用的是粗市场名，`feed.py` 用的是细分名 ——
# 两层都受理，别让调用方去猜该传哪个（猜错的症状是"这个市场不认识"）。
ALIASES = {
    'cn': 'cn_stock', 'cn_stk': 'cn_stock', 'a': 'cn_stock', 'a_stock': 'cn_stock',
    'hk': 'hk_stock', 'us': 'us_stock', 'etf': 'cn_etf', 'index': 'cn_index',
}

PHASE_OPEN_MIN = 30      # 「开盘 30 分」的 30 —— 规格 §3.2 策略 10 的原话
PHASE_CLOSE_MIN = 30     # 「尾盘 30 分」
PHASE_KEYS = ('morning', 'midday', 'afternoon', 'close')

# 各市场的**交易规则原文口径**。这段话会被拼进 `analyze()['session']['note']`，
# 也会进 `errors` 里那些"这个市场没有时段效应"的解释中 —— 用户看到的是一个
# 具体市场、具体规则下的结论，不是一句放之四海的"日内动量"。
RULES = {
    'cn_stock': 'A股 09:30-11:30 / 13:00-15:00 连续竞价；09:15-09:25 开盘集合竞价与 '
                '14:57-15:00 收盘集合竞价不在本 K 线序列内；T+1（当日买入次日方可卖）；'
                '主板涨跌停 ±10%（创业板/科创板 ±20%、北交所 ±30%）；'
                '11:30-13:00 休市无 K 线',
    'cn_index': '指数 09:30-11:30 / 13:00-15:00，同 A 股时段；指数本身不可交易、'
                '无涨跌停与 T+1 约束，但成分股有',
    'cn_etf': '场内基金 09:30-11:30 / 13:00-15:00；多数 T+1（跨境/债券/黄金 ETF 部分 '
              'T+0，本模块不区分，按 T+1 记录）；涨跌停 ±10%',
    'hk_stock': '港股 09:30-12:00 / 13:00-16:00；09:00-09:30 开市前时段与 16:00-16:10 '
                '收市竞价不在本序列；T+0 可当日回转；无涨跌停（有市调机制 VCM 冷静期）',
    'us_stock': '美股 09:30-16:00 ET 常规时段；盘前 04:00-09:30 与盘后 16:00-20:00 '
                '不在本序列；T+0；无涨跌停（有个股熔断 LULD）',
    'crypto': '加密 7×24 无开盘/收盘 ⇒ 没有"开盘 30 分钟"这回事，phase 恒为 all，'
              '时段效应这条策略不适用',
}


def norm_market(market):
    """粗名/细分名 → 本模块的规范名。认不出就说认不出（不猜一个默认市场）。"""
    m = str(market or '').strip().lower()
    m = ALIASES.get(m, m)
    if m not in MARKETS:
        raise ValueError('不认识的市场：%r（有 %s，或别名 %s）'
                         % (market, '/'.join(MARKETS), '/'.join(ALIASES)))
    return m


def cfg(market):
    return MARKETS[norm_market(market)]


def session_minutes(market):
    """当天有哪些分钟（0..1439），升序。**休市区间整段没有**。

    这是"一天多少根 K"的唯一来源：`bars_per_day() = len(session_minutes)//period`。
    A 股 ⇒ 240 ⇒ 5 分钟 48 根 / 15 分钟 16 根 / 30 分钟 8 根。
    """
    c = cfg(market)
    out = []
    for t in range(int(c['open']), int(c['close'])):
        if any(a <= t < b for a, b in c['breaks']):
            continue
        out.append(t)
    return out


def bars_per_day(market, period):
    """一天能出几根 K。`period` 除不尽时**向下取整**并留下尾巴 —— 真实上游也是这么干的。"""
    n = len(session_minutes(market))
    return max(1, n // int(period))


def local_dt(dt_series, market):
    """→ naive 的本地时刻。tz-aware 的换算到该市场时区；naive 的**当作**本地时间。"""
    s = pd.to_datetime(dt_series)
    c = cfg(market)
    try:
        if getattr(s.dt, 'tz', None) is not None:
            return s.dt.tz_convert(c['tz']).dt.tz_localize(None)
    except Exception:                              # noqa: BLE001 —— 时区库缺失就按 naive 走
        pass
    return s


def tod_minutes(dt_series, market):
    """K 线时刻 → 当天第几分钟（0..1439）。返回 pandas Series of int。"""
    s = local_dt(dt_series, market)
    return (s.dt.hour * 60 + s.dt.minute).astype(int)


def phase_of(market, tod):
    """某一分钟落在哪个相位。**没有时段的市场返回 `'all'`。**

    ★规格 §3.2 给的枚举是 `morning|midday|afternoon|close`。加密 7×24 上这四个词
      没有意义，这里返回 `'all'` 而不是硬塞一个 `'morning'` —— 硬塞出来的话
      前端会显示"当前处于早盘"，而那个市场没有早盘。这是**故意的偏离**，
      已写进交付说明（消费方遇到未知取值应按"无时段语义"处理）。
    """
    c = cfg(market)
    if norm_market(market) == 'crypto':
        return 'all'
    if tod < c['open'] + PHASE_OPEN_MIN:
        return 'morning'
    if tod >= c['close'] - PHASE_CLOSE_MIN:
        return 'close'
    mid = (c['open'] + c['close']) / 2.0
    return 'midday' if tod < mid else 'afternoon'


def phase_windows(market):
    """每个相位的**实际**时间窗（只含真正有 K 线的分钟）。

    不是拿 open/close 加减 30 硬算出来的 —— 那样 "midday" 会写成 10:00-12:15，
    而 11:30-12:15 一根 K 都没有，报出去的时间窗是假的。
    这里在 `session_minutes()` 上取每段的首末分钟，报的窗口就是数据里真实存在的。
    """
    if norm_market(market) == 'crypto':
        return {'all': ('00:00', '23:59')}
    mins = session_minutes(market)
    got = {}
    for t in mins:
        p = phase_of(market, t)
        if p not in got:
            got[p] = [t, t]
        got[p][1] = t
    return {k: (_hhmm(v[0]), _hhmm(v[1] + 1)) for k, v in got.items()}


def _hhmm(m):
    m = int(m) % 1440
    return '%02d:%02d' % (m // 60, m % 60)


def session_note(market):
    """给 `analyze()['session']['note']` 的那段话（含该市场的交易规则与 phase 口径）。"""
    m = norm_market(market)
    win = phase_windows(m)
    seg = '、'.join('%s %s-%s' % (k, v[0], v[1]) for k, v in win.items())
    return '%s；phase 以 K 线**结束**时刻切分（距开盘 %d 分钟内=morning、距收盘 %d 分钟内' \
           '=close、其余按开收盘中点分 midday/afternoon），本帧实际窗口：%s' \
           % (RULES[m], PHASE_OPEN_MIN, PHASE_CLOSE_MIN, seg)
