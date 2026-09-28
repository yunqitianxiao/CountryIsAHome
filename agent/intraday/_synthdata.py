"""**合成**分钟 K 线 —— 只为离线跑探针/判据，不是行情。

用途只有两条（写死在这里，防止有人把它当成一个"数据源"接进去）：
  1. `_probe.py` 用它把十条策略逐条打出来（无网也能跑）；
  2. 判据可以在离线、可复现的前提下拿到同样的帧（显式播种 ⇒ 同参同帧，
     先例 `agent/feed.py: _stub_frame`）。

★★它**不在** `analyze()` 的降级路径上。只有显式设 `LICAI_INTRADAY_SYNTH`
  （或直接 import 本模块）才会用到；真接口取不到时 `analyze()` 返回 `ok=False`。
  桩**不是兜底** —— 一个"取不到就拿假的顶上"的路径就是一台伪造行情的机器，
  而"这个数是哪来的"这个问题从此没有答案（先例：`feed.py` 的 `LICAI_FEED_STUB`
  同样只在服务端设、不接受客户端开关）。

★★本模块里的每一个数都是**编的**。任何写在这份构造成立的东西（"开盘那根量是
  全天均量的几倍"）都**不能**当行情事实读。要引用真实数字，去跑真数据源。

★为什么按 **1 分钟**造、再聚合成 5/15/30：
  三个周期必须是**同一段分钟路径**的三个视角。各造一段独立随机游走的话，
  5 分钟说涨、15 分钟说跌是常态 ⇒ `多周期共振` 那条策略在最干净的数据上就
  自相矛盾，判据于是只能测出一个空壳。真实上游也是从逐笔/1 分钟聚合上来的。
  ⇒ 种子**不含 period**（同 market+code+scenario 的三个周期共享同一条 1 分钟路径）。

★缺口/涨跌停也造出来：每个交易日先跳空（`gap`），盘中价格被夹在
  `prev_close × (1±limit)` 内（A 股 ±10%）。这既是真实约束，也是为了让
  "跳空"这条最容易骗人的东西在帧里真的出现（ATR 的 TR 含跳空，见 `levels.py`）。
"""
from __future__ import annotations

import math
import random
import zlib

import pandas as pd

try:
    from . import sessions
except ImportError:
    # 直接 `python agent/intraday/_synthdata.py` 时本文件是 __main__、没有父包，
    # 相对导入无从谈起。这里把 `agent/` 塞进 sys.path 再按顶层包名导一次
    # （`agent/__init__.py` 不存在 ⇒ `intraday` 本来就是顶层包）。
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from intraday import sessions

SYNTH_ENV = 'LICAI_INTRADAY_SYNTH'          # 显式开关；不设它就永远走真接口

CANON = ['dt', 'open', 'high', 'low', 'close', 'volume', 'amount']

BASE_PRICE = {'cn_stock': 12.34, 'cn_index': 3412.50, 'cn_etf': 3.856,
              'hk_stock': 382.40, 'us_stock': 183.52, 'crypto': 61240.0}
BASE_DAILY_VOL = {'cn_stock': 8.0e6, 'cn_index': 1.2e8, 'cn_etf': 5.0e7,
                  'hk_stock': 1.5e7, 'us_stock': 2.0e7, 'crypto': 5.0e3}
ROUND_DEC = {'crypto': 1}                   # 其余 2 位小数（A 股/港股的最小报价单位是 0.01）
DAILY_VOL_PCT = 0.012                       # 假设的日波动（1.2%）—— 合成参数，非实测
OPEN_GAP_PCT = 0.004                        # 日开盘跳空的标准差
U_FLOOR = 0.35                              # U 型成交量的谷底（午盘量 ≈ 均量的 0.35 倍）
OPEN_SPIKE = 1.20                           # 开盘那一段额外的量（U 型的左峰）
OPEN_DECAY = 0.08                           # 左峰衰减的尺度（按全天 0~1 的位置）
VOL_NOISE = 0.25                            # 每分钟成交量的相对噪声
# 每根 K 在"路径价"上下各留的固定幅度。**为什么需要它**：合成的收盘路径若在某一段是
# 平的（开盘横盘），那一根的 high==low，于是"开盘区间宽度=0" ⇒ ORB 直接判"一字/停牌"。
# 真实的横盘也有几百块的波动，所以给一个固定 wiggle，让 high/low 永远有宽度。
HI_LO_WIGGLE = 0.0003

FIEND_END = '2026-09-18'                    # 默认的帧末日期（周五）—— 固定日期让输出可复现


# ── 场景 ───────────────────────────────────────────────────────────────
# `segs` = 最后一个交易日的分段构造：`(到第几分钟, 每分钟漂移, 成交量倍数)`。
# ★工程化的那几个（breakout / false_break / divergence / flat）把 `sigma_mult` 设成 0：
#   最后一个交易日的收盘路径变成**确定的分段直线**，几何位置可以手算核对
#   （_probe.py 的复核那节就是这么做的）。其余场景保留随机游走，让数据"像"行情。
# `minutes` = 最后一个交易日跑到第几分钟为止（60 = 上午 10:30 的快照，240 = 收完整天）。
DEFAULT_LAST = dict(gap=0.0, segs=((60, 0.00002, 1.0),), sigma_mult=1.0, minutes=60)
SCENARIOS = {
    'random': dict(),
    # 单边上涨：动量 + 共振 那条有东西可看
    'trend_up': dict(hist_drift=0.0006,
                     last=dict(gap=0.001, segs=((5, 0.00005, 1.0), (60, 0.00030, 1.4)))),
    # 单边下跌：动量给 short、VWAP 偏离给 long（价格一路压到 VWAP 下方）
    'trend_down': dict(hist_drift=-0.0006,
                       last=dict(gap=-0.005, segs=((60, -0.00060, 1.4),))),
    # 横盘震荡：两个方向都不该给高分
    'range': dict(last=dict(segs=((15, 0.0, 1.0), (30, -0.0012, 1.0),
                                 (45, 0.0012, 1.0), (60, -0.0010, 1.0)))),
    # 前半横盘、后半放量拉升 ⇒ **ORB 突破 + 量能确认**
    'breakout': dict(last=dict(sigma_mult=0.0, segs=((30, 0.0, 0.5), (60, 0.00070, 3.0)))),
    # 突破后掉回区间内 ⇒ **ORB 假突破**（最后一个交易日收盘精确回到开盘价）
    'false_break': dict(last=dict(sigma_mult=0.0,
                                  segs=((30, 0.0, 0.5), (38, 0.00200, 3.0),
                                        (60, -0.000727, 1.0)))),
    # 放量下跌 → 缩量新高 ⇒ **顶背离**（OBV 在"下跌那段"被大额扣掉，
    # 之后缩量的上涨补不回来，于是价格新高、OBV 不新高）
    'divergence': dict(hist_drift=0.0015,
                       last=dict(gap=0.002, sigma_mult=0.0,
                                 segs=((15, -0.00060, 2.2), (60, 0.00080, 0.25)))),
    # 历史段放大波动、最后一个交易日几乎不动 ⇒ **布林带挤压**
    'squeeze': dict(hist_vol_mult=2.0,
                    last=dict(sigma_mult=0.05, minutes=240,
                              segs=((240, 0.0, 0.35),))),
    # **跑满一整天**（240 分钟、四个相位都占上）。存在的理由只有一个：
    #   工程化的那几个场景最后一个交易日只跑 60 分钟 ⇒ 15 分钟周期只有 4 根、
    #   30 分钟周期只有 2 根，而 VWAP 偏离与成交量分布都要求当天 ≥ 6 根
    #   ⇒ 这两条在粗周期上**整条不出现**（那是纪律在正确工作，见 volume_profile
    #   的 VP_MIN_BARS），但覆盖矩阵上就永远是 `~`，看不出这两条到底行不行。
    #   本场景补上那一格：早盘横盘（给 ORB 一个区间）→ 上午阴跌 → 下午放量拉升，
    #   于是 VWAP / 分布 / 动量 / ORB 在 5/15/30 三个周期上都有真读数可看。
    'full_day': dict(hist_drift=0.0003,
                     last=dict(gap=0.002, sigma_mult=0.5, minutes=240,
                               segs=((30, 0.0, 1.2), (120, -0.00030, 0.8),
                                     (240, 0.00040, 1.6)))),
}


def _seed(market, code, scenario):
    """★显式播种，且**不含 period**（三个周期共享同一条 1 分钟路径，见模块头）。
    用 `zlib.crc32` 而不是内置 `hash()`：后者对 str 每进程加盐，同一份输入在两个
    进程里会给出两段完全不同的行情（先例 `feed.py: _stub_frame`）。"""
    key = 'intraday-synth|%s|%s|%s' % (market, code, scenario or '')
    return zlib.crc32(key.encode('utf-8'))


def _u_shape(n):
    """U 型成交量权重，**均值 = 1**（于是 `vol_i = 日均量/n × w_i` 时当天总量 = 日均量）。

    形状 = 抛物线（两头高、中间低，谷底 `U_FLOOR`）+ 开盘额外一个尖峰。
    这是合成参数，不是实测的 U 型 —— 真实市场里左峰通常高于右峰，这里用
    `OPEN_SPIKE` 体现（收尾那一侧只有抛物线的回升）。
    """
    if n <= 1:
        return [1.0]
    out = []
    for i in range(n):
        x = i / (n - 1.0)
        raw = U_FLOOR + (1.0 - U_FLOOR) * (2 * x - 1) ** 2 + OPEN_SPIKE * math.exp(-x / OPEN_DECAY)
        out.append(raw)
    s = sum(out)
    return [x * n / s for x in out]


def _seg_at(minute, segs):
    """第 `minute` 分钟落在哪一段 → `(漂移, 量倍数)`。"""
    for until, drift, vmult in segs:
        if minute < until:
            return float(drift), float(vmult)
    u, d, v = segs[-1]
    return float(d), float(v)


def _dates(days, end):
    return list(pd.bdate_range(end=pd.Timestamp(end), periods=int(days)))


def _base_minutes(market, code, days=5, scenario=None, end=FIEND_END, seed=None):
    """1 分钟 canonical 帧（含跳空、涨跌停夹取、U 型量）。"""
    m = sessions.norm_market(market)
    spec = dict(SCENARIOS.get(scenario or 'random') or {})
    grid = sessions.session_minutes(m)
    n = len(grid)
    rnd = random.Random(_seed(m, code, scenario) if seed is None else int(seed))
    base = BASE_PRICE.get(m, 100.0)
    daily_vol = DAILY_VOL_PCT * float(spec.get('hist_vol_mult', 1.0))
    hist_drift = float(spec.get('hist_drift', 0.0))
    sig_m = daily_vol / math.sqrt(n)
    day_vol = BASE_DAILY_VOL.get(m, 1.0e7)
    limit = sessions.cfg(m)['limit']
    dec = ROUND_DEC.get(m, 2)
    w = _u_shape(n)
    last = dict(DEFAULT_LAST)
    last.update(spec.get('last') or {})
    segs = tuple(last['segs'])
    last_sig = sig_m * float(last.get('sigma_mult', 1.0))
    last_minutes = int(last.get('minutes', 60))
    dates = _dates(days, end)

    rows = []
    prev_close = base
    for di, day in enumerate(dates):
        is_last = (di == len(dates) - 1)
        gap = float(last['gap']) if is_last else rnd.gauss(0.0, OPEN_GAP_PCT)
        o = prev_close * (1.0 + gap)
        if limit:
            o = min(max(o, prev_close * (1 - limit)), prev_close * (1 + limit))
        n_min = n if not is_last else min(n, last_minutes)
        prev = o
        for i in range(n_min):
            if is_last:
                drift, vmult = _seg_at(i, segs)
                sig = last_sig
            else:
                drift, vmult = hist_drift / n, 1.0
                sig = sig_m
            c = prev * (1.0 + drift + rnd.gauss(0.0, sig))
            if limit:                     # 涨跌停：盘中价格不许越过昨收的 ±limit
                c = min(max(c, prev_close * (1 - limit)), prev_close * (1 + limit))
            hi = max(prev, c) * (1.0 + HI_LO_WIGGLE)
            lo = min(prev, c) * (1.0 - HI_LO_WIGGLE)
            vol = max(0.0, day_vol / n * w[i] * vmult * (1.0 + VOL_NOISE * rnd.gauss(0.0, 1.0)))
            row = {'dt': pd.Timestamp(day) + pd.Timedelta(minutes=int(grid[i]) + 1),
                   'open': cycle(prev, dec), 'high': cycle(hi, dec),
                   'low': cycle(lo, dec), 'close': cycle(c, dec),
                   'volume': float(int(vol)), 'amount': 0.0}
            # 取整之后重新保证 high>=max(o,c)、low<=min(o,c)（否则会出现"最高价低于收盘价"
            # 这种在任何数据源上都不可能出现的行 —— 而它会让所有 range 类判据静默偏小）
            row['high'] = max(row['high'], row['open'], row['close'])
            row['low'] = min(row['low'], row['open'], row['close'])
            row['amount'] = round(row['volume'] * (row['open'] + row['high']
                                                   + row['low'] + row['close']) / 4.0, 2)
            rows.append(row)
            prev = c
        prev_close = prev
    df = pd.DataFrame(rows, columns=CANON)
    df.attrs['source'] = 'SYNTH'
    df.attrs['scenario'] = scenario or 'random'
    return df


def cycle(x, dec):
    """四舍五入到报价单位（名字故意不叫 `round`：这是一个**约定**，不是内建函数的包装）。"""
    return float(round(x, dec))


def _aggregate(df1, period, market):
    """1 分钟 → N 分钟。★K 线时刻取**结束**时刻（09:30-09:35 那根标 09:35，
    与国内行情软件一致；`sessions.tod_minutes` 与 `phase_of` 都建立在这个口径上）。"""
    period = int(period)
    if period <= 1:
        return df1.reset_index(drop=True)
    m = sessions.norm_market(market)
    grid = sessions.session_minutes(m)
    pos = {minute: i for i, minute in enumerate(grid)}
    d = df1.copy()
    tod = sessions.tod_minutes(d['dt'], m)
    # ★★这里必须用 `tod - 1`（= 这根 1 分钟 K **覆盖**的那一分钟），不能用 `tod`。
    #   本模块的标签口径是"结束时刻"：标 09:31 的那根覆盖 09:30。拿 `tod`（=09:31）
    #   去查位置会整体**偏移一分钟** —— 聚合出来的 5 分钟 K 会比真正的 09:30-09:35
    #   晚一分钟（标成 09:40），而 `_win()` 又从这个标签往回推 5 分钟写进 why ⇒
    #   why 里写的窗口与它真正聚合的行**不是同一段**，两者都"看着正常"。
    #   这个偏移是 `_probe.py` 的手工复核（拿 1 分钟帧按时间窗手算 ORB 上下沿）
    #   抓出来的，肉眼看输出看不出来。
    d['_pos'] = (tod - 1).map(pos)
    d = d.dropna(subset=['_pos'])
    if not len(d):
        return d.iloc[0:0].reset_index(drop=True)
    d['_pos'] = d['_pos'].astype(int)
    d['_day'] = pd.to_datetime(d['dt']).dt.normalize()
    d['_blk'] = d['_pos'] // period
    g = d.groupby(['_day', '_blk'], sort=True)
    out = g.agg(open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
                close=('close', 'last'), volume=('volume', 'sum'),
                amount=('amount', 'sum'), _pos=('_pos', 'max')).reset_index()
    # 标签 = 该桶**名义**的结束时刻（哪怕是尚未走完的最后一桶 —— 真实上游就是这么标的：
    # 10:31 的时候已经有一根标着 10:35 的未完成 K 线）。名义右端 = 桶起点 + period - 1
    # 在 grid 上的位置，越界就取 grid 末位。
    def _label(r):
        last_i = min(int(r['_blk']) * period + period - 1, len(grid) - 1)
        return pd.Timestamp(r['_day']) + pd.Timedelta(minutes=int(grid[last_i]) + 1)
    out['dt'] = out.apply(_label, axis=1)
    out = out[['dt', 'open', 'high', 'low', 'close', 'volume', 'amount']]
    out.attrs['source'] = 'SYNTH'
    out.attrs['period'] = period
    return out.sort_values('dt').reset_index(drop=True)


def minute(market='cn_stock', code='600519', period=5, days=5, scenario=None,
           end=FIEND_END, seed=None):
    """合成分钟 K。**接口与 `datasource.minute` 同形**（CANON 列、dt 升序、attrs）。

    `scenario=None` ⇒ 通用随机游走；其余见 `SCENARIOS`（每个场景是为某条策略
    的某个分支专门造的，见那边的注释）。
    """
    df1 = _base_minutes(market, code, days=days, scenario=scenario, end=end, seed=seed)
    return _aggregate(df1, period, market)


if __name__ == '__main__':
    # 探针：把每个场景的帧形状打出来（行数 / 日期数 / 末根时刻 / 末根收盘）
    for sc in [None] + sorted(SCENARIOS):
        for p in (5, 15, 30):
            d = minute('cn_stock', '600519', period=p, days=5, scenario=sc)
            print('%-12s p=%-3d bars=%-4d days=%-2d 末根 %s close=%.4f vol=%d'
                  % (sc or 'default', p, len(d), d['dt'].dt.normalize().nunique(),
                     d['dt'].iloc[-1], d['close'].iloc[-1], d['volume'].iloc[-1]))
