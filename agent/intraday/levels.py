"""波动率与价位：ATR、以及由它推出来的 entry / stop / target1 / target2 / rr。

★本模块**不给方向**。方向由别的策略投票（`__init__.py` 的 composite）决定，
  这里只回答"如果做多/做空，止损该放哪、目标在哪、盈亏比多少"。
  把这件事混进"信号强弱"里，就会出现"因为盈亏比高所以看涨"这种倒因为果的推理。

★ATR 的口径（三处，每一处都有别的写法）：
  · `TR = max(high-low, |high-prev_close|, |low-prev_close|)`（Wilder 1978 原式，
    **含跳空**）—— 不含跳空的那版（只取 high-low）在隔夜跳空上会低估风险，
    而日内策略里跳空恰恰是最常被触发止损的地方；
  · 平滑用 **Wilder 递推**：`alpha = 1/n`、`adjust=False` —— 与
    `agent/indicators.py: _recursive_sma` 同一条递推（那里解释了"种子取什么"
    才是分水岭：通达信取第一个信号值、TA-Lib 取前 n 个的均值）。这里跟通达信/本仓库一致。
  · `min_periods=n` ⇒ 前 n-1 根是 NaN（"算不出来就不给"，见 indicators 模块头）。

★`rr` 不是常数：止损取"1.2×ATR"与"当日低点-0.1×ATR"里**更近**的那个（跌破当日低点
  即失效），目标的 ATR 倍数是固定的 ⇒ 风险端一变，rr 就跟着变。另加 `MIN_RISK_PCT`
  地板，否则在一段极窄的行情里止损会贴到价格上、rr 虚高成几十倍。
"""
from __future__ import annotations

import pandas as pd

from .algo import StrategySkipped, finite, safe_div

ATR_N = 14               # Wilder 的经典取值（也是本仓库 KDJ 之外最通用的 14）
ATR_STOP_MULT = 1.2      # 止损 = 1.2×ATR。>1 才躲得开单根 K 的正常抖动（约 0.7×ATR）
ATR_T1_MULT = 1.5        # 目标1 = 1.5×ATR（≈1.25 倍风险，是"能落袋"的那一档）
ATR_T2_MULT = 3.0        # 目标2 = 3.0×ATR（2.5 倍风险，趋势延续那一档）
ATR_STOP_BUFFER = 0.1    # "当日低点 - 0.1×ATR"：把止损放在低点**下方一点**，
                         # 正好压在低点上的止损是最容易被一根影线扫掉的
MIN_RISK_PCT = 0.003     # 风险地板 = 价的 0.3%：再窄的止损在分钟线上等于"必被扫"


def tr(df):
    """真实波幅序列（Wilder 1978，含跳空）。第一根是 NaN（没有前收）。"""
    h = pd.to_numeric(df['high'], errors='coerce')
    l = pd.to_numeric(df['low'], errors='coerce')
    pc = pd.to_numeric(df['close'], errors='coerce').shift(1)
    a = (h - l).abs()
    b = (h - pc).abs()
    c = (l - pc).abs()
    out = pd.concat([a, b, c], axis=1).max(axis=1)
    out[pc.isna()] = float('nan')             # 第一根没有"前收"，TR 无从谈起
    return out


def atr(df, n=ATR_N):
    """ATR：TR 的 Wilder 递推均值。前 n-1 根 NaN。"""
    return tr(df).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def last_session(df, market):
    """最后一个交易日的那些行（与 `volume_profile.profile` 同一把尺子）。"""
    from . import sessions
    key = pd.to_datetime(df['dt']).dt.normalize()
    if getattr(key.dt, 'tz', None) is not None:
        key = key.dt.tz_convert(sessions.cfg(market)['tz']).dt.tz_localize(None)
    # ★与**标量** Timestamp 比，不写 `key.iloc[-1].values`：pandas 2.x 的 Timestamp
    #   没有 `.values`（1.x 有），那种写法在装 2.x 的机器上直接 AttributeError。
    return df[(key == key.iloc[-1]).values]


def stop_targets(df, side, market='cn_stock', atr_last=None, n=ATR_N):
    """→ entry / stop / target1 / target2 / rr / risk / method。

    `side` ∈ {'long','short'}。`atr_last` 可以外部注入（调用方已经算过 ATR 时省一遍）。
    ATR 算不出来（K 线 < n+1 根）⇒ 抛 `StrategySkipped`，**不拿一个"大概 1%"顶上**。
    """
    if side not in ('long', 'short'):
        raise StrategySkipped('止损/目标位需要一个方向（long/short），收到 %r' % (side,))
    if len(df) < n + 1:
        raise StrategySkipped('ATR%d 需要至少 %d 根 K，实到 %d 根'
                             % (n, n + 1, len(df)))
    a = finite(atr_last) if atr_last is not None else finite(atr(df, n).iloc[-1])
    entry = finite(pd.to_numeric(df['close'], errors='coerce').iloc[-1])
    if a is None or entry is None or a <= 0 or entry <= 0:
        raise StrategySkipped('ATR 或最新收盘价算不出来（ATR=%s, close=%s）' % (a, entry))
    day = last_session(df, market)
    day_low = finite(pd.to_numeric(day['low'], errors='coerce').min())
    day_high = finite(pd.to_numeric(day['high'], errors='coerce').max())
    stop_atr = entry - ATR_STOP_MULT * a if side == 'long' else entry + ATR_STOP_MULT * a
    stop = stop_atr
    if side == 'long' and day_low is not None:
        stop = max(stop_atr, day_low - ATR_STOP_BUFFER * a)     # 取更近（更高）的那个
    if side == 'short' and day_high is not None:
        stop = min(stop_atr, day_high + ATR_STOP_BUFFER * a)    # 取更近（更低）的那个
    risk = abs(entry - stop)
    floor = entry * MIN_RISK_PCT
    floored = risk < floor                                  # 有没有被地板顶上来（记进返回值）
    if floored:                                             # 风险地板（见模块头）
        risk = floor
        stop = entry - risk if side == 'long' else entry + risk
    sgn = 1.0 if side == 'long' else -1.0
    t1 = entry + sgn * ATR_T1_MULT * a
    t2 = entry + sgn * ATR_T2_MULT * a
    rr = safe_div(abs(t1 - entry), risk)
    if rr is None:
        raise StrategySkipped('风险为 0，盈亏比无从谈起')
    return {
        'entry': entry, 'stop': stop, 'target1': t1, 'target2': t2,
        'atr': a, 'risk': risk, 'rr': rr, 'side': side,
        'day_low': day_low, 'day_high': day_high,
        'atr_pct': safe_div(a, entry),
        'risk_pct': safe_div(risk, entry),
        'floored': bool(floored),
        'method': 'ATR%d(Wilder 递推, 含跳空)；止损=「%s×ATR」与「当日%s%s×ATR」取更近者、'
                  '风险不低于价的 %.1f%%；目标1=%.1f×ATR、目标2=%.1f×ATR'
                  % (n, ATR_STOP_MULT, '低点-' if side == 'long' else '高点+',
                     ATR_STOP_BUFFER, MIN_RISK_PCT * 100, ATR_T1_MULT, ATR_T2_MULT),
    }
