"""市场状态（regime）与跨周期共振。

`regime` ∈ `trend_up | trend_down | range | squeeze`（规格 §3.2）。

★判据的顺序**不是**技术细节，是约定 —— 所以写死在这里并说明：
  先看 `squeeze`（带宽挤在低分位），再看趋势，最后才是 `range`。
  理由：一个"慢慢阴跌但带宽极窄"的行情同时满足 squeeze 与 trend_down，
  把它们按顺序判、先判谁，会给出**不同**的 regime；而两种排法都"说得通"。
  这里选 squeeze 优先 —— 挤压是一个更具体、更可操作的状态（它问的是"要不要等突破"），
  趋势只是方向。若要反过来，改这里一处即可，别在两个地方各写一套。

★`range` 是**兜底**，不是"确认了在震荡"：它只表示"没有明显趋势、也没在挤压"。
  所以 `why` 里报的是两个**可复核的量**（斜率 z、带宽分位），不是一句"震荡"。
"""
from __future__ import annotations

import pandas as pd

import indicators
from . import levels
from .algo import StrategySkipped, finite, pct_rank

REGIME_EMA_N = 9         # 与 indicators.EMA_WINDOWS 里的小窗对齐；分钟线上 9 根 ≈ 45 分钟(5m)
REGIME_SLOPE_LAG = 3     # 斜率取"现在 vs 3 根前"——1 根太抖，>5 根在分钟线上太钝
# 斜率门槛：`|ΔEMA| / (ATR × √lag)` ≥ 0.5 才算趋势。
# ★为什么除 ATR 而不是除价格：0.1% 的斜率在高波动股上是噪声、在低波动股上是趋势；
#   ATR 是这只标的自己的波动尺子，除掉之后**跨标的可比**。√lag 是把 lag 根的位移
#   还原成"每根的平均位移"（随机游走假设下位移的尺度随 √t 长）。
REGIME_TREND_MIN_Z = 0.5

SQZ_LOOKBACK = 60        # 带宽分位的窗口（根）。60 根 5 分钟 = 半天，够看出"最近这段是不是挤"
SQZ_PCTILE = 20.0        # 带宽落在窗口的 ≤20% 分位 ⇒ squeeze
EXP_PCTILE = 80.0        # 带宽落在 ≥80% 分位 ⇒ expansion（扩张）


def ema_slope_z(df, n=REGIME_EMA_N, lag=REGIME_SLOPE_LAG):
    """EMA 斜率（无量纲）：`ΔEMA / (ATR × √lag)`。算不出来 → None。"""
    c = pd.to_numeric(df['close'], errors='coerce')
    if len(c) < max(n + lag + 1, levels.ATR_N + 1):
        return None
    e = indicators.ema(c, n)
    a = levels.atr(df)
    d = finite(e.iloc[-1] - e.iloc[-1 - lag])
    av = finite(a.iloc[-1])
    if d is None or av is None or av <= 0:
        return None
    return d / (av * (lag ** 0.5))


def boll_width_pctile(df, n=indicators.BOLL_N, k=indicators.BOLL_K,
                      lookback=SQZ_LOOKBACK):
    """布林带宽 `(upper-lower)/mid` 在近 `lookback` 根里的分位（0~100）。

    ★width 用**相对带宽**（除以中轨）而不是绝对带宽：绝对带宽随价格水平变，
      1700 元的票和 12 元的票没有可比的"带宽"。
    ★`indicators.boll` 的 ddof=0 与 k=2.0 是行情软件口径（见那个函数的注释）——
      这里直接用它的输出，不自己再写一遍（两份实现 = 两个"带宽"）。
    算不出来（不足 `n + lookback` 根、或分位样本不足）→ None。
    """
    c = pd.to_numeric(df['close'], errors='coerce')
    if len(c) < n + 2:
        return None
    mid, up, low = indicators.boll(c, n, k)
    w = ((up - low) / mid.where(mid > 0)).dropna()
    if len(w) < lookback:
        return None
    return pct_rank(w.iloc[-lookback:].tolist(), w.iloc[-1],
                    min_n=min(30, lookback))


def classify(df):
    """→ `(regime, caveat)`。`caveat` 是人话（"少算了哪一支"），完整时为 None。

    算不出**任何**状态（K 线不够）⇒ 抛 `StrategySkipped`，由调用方记进 `errors`
    并**不往 `regime` 字典里塞一个值** —— 塞一个默认的 `'range'` 就是编。
    """
    z = ema_slope_z(df)
    if z is None:
        raise StrategySkipped('regime 需要至少 %d 根 K（ATR%d + EMA%d + 斜率滞后 %d）'
                             '才能算出斜率，实到 %d 根'
                             % (max(levels.ATR_N + 1, REGIME_EMA_N + REGIME_SLOPE_LAG + 1),
                                levels.ATR_N, REGIME_EMA_N, REGIME_SLOPE_LAG, len(df)))
    pct = boll_width_pctile(df)
    caveat = None
    if pct is None:
        caveat = ('布林带宽分位需要 %d 根（BOLL%d + 窗口 %d），实到 %d 根 ⇒ '
                  '本次 regime 判定不含 squeeze 这一支（只判趋势/震荡）'
                  % (indicators.BOLL_N + SQZ_LOOKBACK, indicators.BOLL_N,
                     SQZ_LOOKBACK, len(df)))
    if pct is not None and pct <= SQZ_PCTILE:
        return 'squeeze', caveat
    if z >= REGIME_TREND_MIN_Z:
        return 'trend_up', caveat
    if z <= -REGIME_TREND_MIN_Z:
        return 'trend_down', caveat
    return 'range', caveat


# ── 跨周期共振 ─────────────────────────────────────────────────────────
# 权重是**设计常量**（没有回测背书，不许当"拟合结果"读）：长周期定方向、短周期定时机，
# 但短周期噪声更大 ⇒ 权重低于长周期。三者相加 = 1，便于直接当 score 用。
# ★"长周期更重"不是定理：日内交易里也有反过来的排法（快线先动、慢线确认）。
#   这里选的是"方向由慢线定"这一派；换成另一派只改这张表，别改下面的公式。
RESONANCE_W = {5: 0.30, 15: 0.35, 30: 0.35}
RESONANCE_MIN_PERIODS = 2   # 只有一个周期在场时，那叫"动量"，不叫"共振"
RESONANCE_MIN_AGREE = 0.20  # 加权方向分低于这个就当没共振（±0.2 ≈ 两成一平的局面）


def resonance(dirs, weights=None):
    """三个周期的方向分 → 共振。`dirs` = `{period: 方向分 ∈ [-1,1] 或 None}`。

    返回 dict（含 score / agree_n / per-period 明细）；可用周期少于
    `RESONANCE_MIN_PERIODS` 时抛 `StrategySkipped`。
    """
    w = dict(weights or RESONANCE_W)
    got = {p: finite(v) for p, v in (dirs or {}).items()}
    got = {p: v for p, v in got.items() if v is not None and p in w}
    if len(got) < RESONANCE_MIN_PERIODS:
        raise StrategySkipped('多周期共振需要至少 %d 个周期的方向分，本次只有 %d 个'
                             '（%s）' % (RESONANCE_MIN_PERIODS, len(got),
                                      '、'.join('%s分钟' % p for p in sorted(got)) or '无'))
    wsum = sum(w[p] for p in got)
    score = sum(w[p] * got[p] for p in got) / wsum if wsum else 0.0
    pos = sum(1 for v in got.values() if v > 0)
    neg = sum(1 for v in got.values() if v < 0)
    return {
        'score': score, 'dirs': {p: got[p] for p in sorted(got)},
        'weights': {p: w[p] for p in sorted(got)},
        'n_up': pos, 'n_down': neg, 'n_periods': len(got),
        'agree': max(pos, neg), 'wsum': wsum,
    }
