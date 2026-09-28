"""成交量域的三件东西：**相对成交量 RVOL**、**成交量分布 Volume Profile**、
**量价背离（OBV）**。

★这个模块里最贵的一条纪律，在 `rvol_by_tod` 的注释里 —— 日内成交量是 U 型的，
  拿早盘的量去比全天的均量，会得到一个"天天都放量"的数。**这是本项目最容易做错的
  一处**，所以那里连"错做法的读数"一起返回（`info['naive_last']`），
  `_probe.py` 把两者并排打出来，让人一眼看见差多少。

★第二贵的是"算不出来就不给"（`agent/indicators.py` 模块头那条）：
  · RVOL 需要**同一时段**的历史样本 ≥ `RVOL_MIN_SLOT_SAMPLES` 个，否则那一根的
    RVOL 是 `None`（不是拿 1 个样本当基线，也不是拿 0 顶上）；
  · 分布需要当天 ≥ `VP_MIN_BARS` 根、且当天有真实的价格区间（高==低 ⇒ 没有分布可言）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import feed
from . import sessions
from .algo import StrategySkipped, finite, safe_div

# ── RVOL ───────────────────────────────────────────────────────────────
RVOL_MIN_HIST_DAYS = 2        # 至少 2 个**历史**交易日（当天不算）才算有基线
RVOL_MIN_SLOT_SAMPLES = 2     # 某个时刻的历史样本至少 2 个 —— 1 个样本的"均值"是那一个数本身
# 三档语义（规格 §3.2 策略 3 点名的 1.5/2.5/4.0）。这是**行业惯用的粗档位**，
# 不是本模块拟合出来的阈值：>1.5 温和放量、>2.5 显著放量、>4.0 异动放量。
RVOL_GRADES = (1.5, 2.5, 4.0)
RVOL_GRADE_NAMES = ('温和放量', '显著放量', '异动放量')


def rvol_by_tod(df, market, min_hist_days=RVOL_MIN_HIST_DAYS):
    """**同一时段**的相对成交量。返回 `(rvol_series, info)`。

    定义：`rvol(t) = 今天 t 时刻的量 / 前 N 个交易日在**同一时刻**的平均量`。

    ★★为什么必须按同一时段比（这行注释是本模块存在的主要理由）：
      日内成交量是 **U 型**的 —— 开盘那几根天然是全天均量的好几倍，午盘最小。
      如果拿 `今天 09:35 的量 / 前 4 天的**全天**均量`，那根天天都在 2 倍以上，
      于是"RVOL > 1.5"这个判据**天天成立** ⇒ 它再也没有分辨能力，
      而读数看着完全正常。判别句：**你比的那两个数，是不是同一个时段？**
      （`info['naive_last']` 就是那个错做法的读数，跟 `info['base_last']` 并排看。）

    ★基线**不含当天**（`date < today`）：含当天的话，一根异动巨量会把自己拉高，
      看起来像"没放量"—— 和 `agent/indicators.py: vol_ratio` 的 `shift(1)` 同一个道理。

    返回的 `rvol_series` 与 `df` 同索引；某根的基线**样本不足**时该根是 NaN（不是 1.0）。
    """
    if len(df) == 0:
        raise StrategySkipped('空表')
    tod = sessions.tod_minutes(df['dt'], market)
    d = pd.DataFrame({'v': pd.to_numeric(df['volume'], errors='coerce'),
                      'tod': tod.astype(int)}, index=df.index)
    day = pd.to_datetime(df['dt']).dt.normalize()
    if getattr(day.dt, 'tz', None) is not None:
        day = day.dt.tz_convert(sessions.cfg(market)['tz']).dt.tz_localize(None)
    d['day'] = pd.Series(day.values, index=df.index)
    d = d.dropna(subset=['v'])
    dates = sorted(pd.unique(d['day']))
    if len(dates) < min_hist_days + 1:
        raise StrategySkipped('RVOL 需要至少 %d 个交易日（当天 + %d 个历史日），实到 %d 天 '
                             '—— 分钟线一次只取到 %d 天，同一个时刻没有可比的历史样本'
                             % (min_hist_days + 1, min_hist_days, len(dates), len(dates)))
    today = dates[-1]
    hist = d[d['day'] < today]
    base = hist.groupby('tod')['v'].mean()
    nsam = hist.groupby('tod')['v'].count()
    ok = nsam.reindex(base.index).fillna(0) >= RVOL_MIN_SLOT_SAMPLES
    base = base.where(ok)

    rv = d['v'] / d['tod'].map(base)
    rv = rv.where(d['tod'].map(base).notna())
    # ★索引跟着 df 走（不是重新 RangeIndex）：调用方要拿 `df.loc[i]` 去对齐"哪一根"，
    #   索引漂了会让 why 里的时刻与数值错位 —— 那种错在肉眼上完全看不出来。
    rv = pd.Series(rv.values, index=d.index).reindex(df.index)

    # ★★最新一根的**同刻基线**（= 上面那个 `base` 里对应最新一根 tod 的那个数）。
    #   为什么要单独返回：调用方的 why 里要写"同刻均值 12345"，而 `info` 里还有
    #   另一个数 `naive_base`（不分时段的全天均量），两者**不是一个东西**。
    #   曾经四处 why 把 `naive_base` 当成同刻均值印出去了 —— 句子里写着"同一时刻
    #   均量"，印出来的却是不分时段的数，而这种错**读起来完全正常**（就是个数字）。
    #   判别句：这个数是按"同一时刻"算的吗？按 tod 分组的 `base` 才是。
    last_tod = int(d['tod'].iloc[-1]) if len(d) else None
    base_last = finite(base.get(last_tod)) if last_tod is not None else None
    info = {
        'hist_days': len(dates) - 1,
        'slots_ready': int(ok.sum()),
        'slots_total': int(len(base)),
        'today': str(pd.Timestamp(today).date()),
        'base_last': base_last,                 # 同刻基线（**这个**才是 why 里要引用的）
        'base_last_tod': last_tod,
        'base_last_samples': (int(nsam.get(last_tod, 0)) if last_tod is not None else None),
        # 错做法的读数：拿当天的量比**当天之前所有分钟**的均量（不分时段）。
        'naive_base': finite(hist['v'].mean()),
    }
    return rv, info


def rvol_grade(x):
    """→ (档位序号 0/1/2/3, 档名)。0 = 不到第一档（<1.5）。"""
    v = finite(x)
    if v is None:
        return 0, '无量比'
    g = 0
    for i, th in enumerate(RVOL_GRADES):
        if v >= th:
            g = i + 1
    return g, (RVOL_GRADE_NAMES[g - 1] if g else '未达 1.5 倍')


# ── Volume Profile ─────────────────────────────────────────────────────
VP_BINS = 24          # 价格分桶数。★24 的理由：一天 240 分钟 / 24 = 每桶覆盖约 10 分钟的量，
                      #   在 5 分钟粒度上是"能看出峰、又不至于碎成噪声"的折中；
                      #   桶数写成常量是因为 POC/VA 对桶数**敏感**，换个桶数会换一个 POC。
VA_PCT = 0.70         # 价值区 = 覆盖 70% 成交量的**连续**价格带（Market Profile 的常规取值）
HVN_MULT = 1.5        # 高量节点：桶量 ≥ 平均桶量的 1.5 倍
LVN_MULT = 0.5        # 低量节点：桶量 ≤ 平均桶量的 0.5 倍（含 0 —— 价格跳过的空洞最典型）
NODE_TOP = 3          # 高/低量节点各报几个
VP_MIN_BARS = 6       # 当天少于 6 根就不给分布（4 根画出来的"分布"是画出来的，不是量出来的）


def profile(df, market, bins=VP_BINS, va_pct=VA_PCT):
    """**当日**成交量分布：POC / 价值区 / 高量节点 / 低量节点。

    ★三个口径，都在这里写死：
      1. **只用最后一个交易日**（Volume Profile 是"当天"的分布 —— 跨日累计会得到
         一条跟着价格走的斜线，那是另一个东西）；
      2. 每根 K 的量按**价格重叠长度**分摊到它覆盖的各桶（不是整根记到收盘价那一桶）。
         整根记账会把峰值**放大**：一根横跨 3 个桶的 K 线把全部量压在 1 个桶上，
         POC 会变成一个尖刺，而真实的成交是铺开的。分摊是可复算的：
         `vol_i = v * overlap_i / (high - low)`，Σvol_i == v（_probe 里有守恒校验）；
      3. 价值区用 **Market Profile 的连续扩张法**：从 POC 起，每次把"上邻桶/下邻桶里
         量更大的那个"纳进来，直到累计 ≥ 70%。**不是**"取量最大的若干桶再取 min/max"
         —— 后者给出的区间里可能夹着没被选中的桶（区间不连续，VWAP 与它有交叉时
         两个数说的不是一件事）。并列时**先上后下**（唯一约定，写死）。
    """
    n = len(df)
    if n == 0:
        raise StrategySkipped('空表')
    day_key = pd.to_datetime(df['dt']).dt.normalize()
    if getattr(day_key.dt, 'tz', None) is not None:
        day_key = day_key.dt.tz_convert(sessions.cfg(market)['tz']).dt.tz_localize(None)
    last_day = day_key.iloc[-1]
    # ★与**标量** Timestamp 比（不写 `last_day.values`：pandas 2.x 的 Timestamp 没这个属性）
    day = df[(day_key == last_day).values]
    if len(day) < VP_MIN_BARS:
        raise StrategySkipped('成交量分布只用当天、且当天至少 %d 根 K（实到 %d 根）'
                             '—— 少于这个数画出来的不是分布，是那几根 K 的高低点'
                             % (VP_MIN_BARS, len(day)))
    lo = float(pd.to_numeric(day['low'], errors='coerce').min())
    hi = float(pd.to_numeric(day['high'], errors='coerce').max())
    if not (hi > lo):
        raise StrategySkipped('当天最高价 == 最低价（%.4f），没有价格区间可分桶' % hi)
    bins = int(bins)
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    h = pd.to_numeric(day['high'], errors='coerce').values
    l = pd.to_numeric(day['low'], errors='coerce').values
    v = pd.to_numeric(day['volume'], errors='coerce').values

    def _bin_of(price):
        return int(np.clip(np.searchsorted(edges, price, side='right') - 1, 0, bins - 1))

    for i in range(len(day)):
        vol_i, hi_i, lo_i = v[i], h[i], l[i]
        if not finite(vol_i) or not finite(hi_i) or not finite(lo_i):
            continue
        if hi_i <= lo_i:                          # 一字板：整根落到那一桶
            vol[_bin_of(hi_i)] += vol_i
            continue
        span = hi_i - lo_i
        for b in range(_bin_of(lo_i), _bin_of(hi_i) + 1):
            ov = min(hi_i, edges[b + 1]) - max(lo_i, edges[b])
            if ov > 0:
                vol[b] += vol_i * ov / span

    total = float(vol.sum())
    if total <= 0:
        raise StrategySkipped('当天成交量为 0，分布无从谈起')
    mid = (edges[:-1] + edges[1:]) / 2.0
    poc_i = int(np.argmax(vol))
    need = total * float(va_pct)
    lo_i = hi_i = poc_i
    acc = float(vol[poc_i])
    while acc < need and (lo_i > 0 or hi_i < bins - 1):
        up = vol[hi_i + 1] if hi_i + 1 < bins else -1.0
        dn = vol[lo_i - 1] if lo_i - 1 >= 0 else -1.0
        if up < 0 and dn < 0:
            break
        if up >= dn:                              # 并列先上（唯一约定）
            hi_i += 1
            acc += float(vol[hi_i])
        else:
            lo_i -= 1
            acc += float(vol[lo_i])
    avg_bin = total / bins
    order = np.argsort(vol)
    lvn = [float(mid[i]) for i in order[:NODE_TOP] if vol[i] <= LVN_MULT * avg_bin]
    hvn = [float(mid[i]) for i in order[::-1][:NODE_TOP] if vol[i] >= HVN_MULT * avg_bin]
    return {
        'session': str(pd.Timestamp(last_day).date()),
        'bars': int(len(day)),
        'bins': bins,
        'range': [feed._num(lo), feed._num(hi)],
        'poc': feed._num(mid[poc_i]),
        'poc_vol_share': feed._num(vol[poc_i] / total if total else None, 4),
        'va_lo': feed._num(edges[lo_i]),
        'va_hi': feed._num(edges[hi_i + 1]),
        'va_share': feed._num(acc / total, 4),
        'hvn': [feed._num(x) for x in hvn],
        'lvn': [feed._num(x) for x in lvn],
        'hvn_mult': HVN_MULT, 'lvn_mult': LVN_MULT,
        'total_volume': feed._num(total, 2),
    }


# ── OBV / 量价背离 ─────────────────────────────────────────────────────
DIV_WINDOW = 10        # 每个比较臂的根数（近 10 根 vs 前 10 根）
PV_CORR_N = 20         # 价格-成交量滚动相关的窗口（只作为辅助读数，不单独当判据）


def obv(df):
    """能量潮 OBV（Granville）：`OBV_t = OBV_{t-1} ± volume_t`，收涨加、收跌减、平不变。

    ★OBV 的**绝对值没有意义**（取决于从哪一天开始累），只有**变化量**有意义。
      所以下面所有用法都只做差，不报绝对值当水平位。
    ★跨日连续累加（不按日重置）—— 这是经典定义；按日重置的话"昨天尾盘到今天早盘"
      这一段的变化就丢了，而那段恰好是隔夜信息最集中的地方。
    """
    c = pd.to_numeric(df['close'], errors='coerce')
    v = pd.to_numeric(df['volume'], errors='coerce').fillna(0.0)
    d = c.diff()
    step = pd.Series(0.0, index=c.index)
    step[d > 0] = v[d > 0]
    step[d < 0] = -v[d < 0]
    return step.cumsum()


def divergence(df, window=DIV_WINDOW):
    """量价背离：**价格创了新高/新低，但同期的 OBV 没有跟着走**。

    做法（两臂等长，全部可复算）：
      · 近 `window` 根的最高价 `ph_r`（记下它在哪一根 `i_r`）与前 `window` 根的最高价 `ph_p`（`i_p`）；
      · 顶背离 ⟺ `ph_r > ph_p` **且** `OBV[i_r] < OBV[i_p]`；
      · 底背离 ⟺ `pl_r < pl_p` **且** `OBV[i_r] > OBV[i_p]`。
    ★OBV 取的是**那一根**的值（不是窗口内的极值）—— 报"价格在 A 点新高、OBV 在 A 点
      却没跟上"才对得上；取窗口极值会把两个不同时刻的事拼成一句话。
    ★辅助读数 `corr` = 近 `PV_CORR_N` 根 close 与 volume 的滚动相关。**它不进判据**：
      它量的是"价和量同向不同向"，而背离问的是"新高处量有没有跟上"，两回事；
      样本不足 `PV_CORR_N` 或方差为 0 时给 None。
    """
    n = len(df)
    if n < 2 * window + 1:
        raise StrategySkipped('量价背离需要 %d 根 K（两臂各 %d 根 + 1），实到 %d 根'
                             % (2 * window + 1, window, n))
    o = obv(df)
    hi = pd.to_numeric(df['high'], errors='coerce')
    lo = pd.to_numeric(df['low'], errors='coerce')
    i_r = int(hi.iloc[-window:].idxmax()) if hi.iloc[-window:].notna().any() else None
    i_p = int(hi.iloc[-2 * window:-window].idxmax())
    j_r = int(lo.iloc[-window:].idxmin())
    j_p = int(lo.iloc[-2 * window:-window].idxmin())
    ph_r, ph_p = float(hi.iloc[-window:].max()), float(hi.iloc[-2 * window:-window].max())
    pl_r, pl_p = float(lo.iloc[-window:].min()), float(lo.iloc[-2 * window:-window].min())
    obv_r, obv_p = float(o.loc[i_r]), float(o.loc[i_p])
    obv_lr, obv_lp = float(o.loc[j_r]), float(o.loc[j_p])
    bear = bool(ph_r > ph_p and obv_r < obv_p)
    bull = bool(pl_r < pl_p and obv_lr > obv_lp)
    kind = 'bearish' if bear else ('bullish' if bull else 'none')
    # OBV 变化的量纲：用窗口内「单根 OBV 步长的绝对值均值 × 根数」当一把尺子，
    # 免得报"OBV 小了 123456"这种没法跨标的比较的数。
    step = o.diff().abs().iloc[-2 * window:]
    scale = safe_div(float(step.mean()) * window, 1.0)
    corr = None
    if n >= PV_CORR_N + 1:
        c = pd.to_numeric(df['close'], errors='coerce').iloc[-PV_CORR_N:]
        vv = pd.to_numeric(df['volume'], errors='coerce').iloc[-PV_CORR_N:]
        if c.notna().all() and vv.notna().all() and c.std(ddof=0) > 0 and vv.std(ddof=0) > 0:
            corr = float(np.corrcoef(c.values, vv.values)[0, 1])
    return {
        'kind': kind, 'window': int(window),
        'price_hi_recent': ph_r, 'price_hi_prior': ph_p,
        'price_lo_recent': pl_r, 'price_lo_prior': pl_p,
        'obv_at_hi_recent': obv_r, 'obv_at_hi_prior': obv_p,
        'obv_at_lo_recent': obv_lr, 'obv_at_lo_prior': obv_lp,
        'obv_scale': scale, 'pv_corr': corr, 'bars': n,
    }
