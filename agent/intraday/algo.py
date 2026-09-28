"""intraday 策略层的算术原语：夹取 / 安全除法 / 分位 / JSON 安全的数。

★这一层存在的理由**不是**"代码整洁"，是判据的形状：
  `verify_agent.py` 里对指标的固定做法是"拿**另一个实现**逐点比"（见
  `agent/indicators.py: boll` 那段 ddof 的注释 —— "判据必须拿 ddof=0 的独立实现
  去比，否则比出来的是约定不同"）。
  ⇒ 所以**判据里不许 import 本模块来复算**，那就成了自己比自己。
  `agent/intraday/_probe.py` 的手工复核那节是**手写循环**，示范了正确做法。

★另一个坑在下面 `clip01/clip1` 的实现里，注释里写了 —— NaN 与 `max()` 的次序有关，
  这是"看着一样、结果不一样"的那类错。
"""
from __future__ import annotations

import math

import feed  # ★全模块只有这一处外部依赖：JSON 安全的数一律走 feed._num()（规格 §0.7）

# 分位需要多少样本才肯给数。**30 是硬门槛**：少于 30 个点的分位，粒度是 3.3% 一档，
# 报出去会让人以为它精确到 1%（`agent/feed.py: _pctile` 用的是 100，那是日线 5 年的量级；
# 分钟线上 30 根 ≈ 半天，是能拿出来的最小样本）。
MIN_PCT_SAMPLES = 30


class StrategySkipped(Exception):
    """数据不够 ⇒ 这条策略**整条不出现**（规格 §3.2 纪律第 2 条）。

    `reason` 是人话，会被调用方拼成 `'5分钟：<reason>'` 放进 `errors`。
    ★和"算得出来但没触发"是**两件事**：后者是一条 `side='flat'` 的信号（带算术理由），
      不是错误。把两者混起来，`errors` 就会变成"策略没给方向"的垃圾桶，
      而它真正要标的是"少了什么数据"。
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = str(reason)


def finite(x):
    """→ float，或 None。NaN / inf / 非数一律 None（**不是 0**）。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def clip01(x):
    """夹到 [0, 1]。NaN/inf/None → 0.0。

    ★**不许写成 `max(0.0, min(1.0, x))`**：`max` 遇到 NaN 时返回哪个参数**取决于
      参数顺序**（`max(0.0, nan)` 是 0.0，而 `max(nan, 0.0)` 是 nan）——
      同一个表达式换个写法就换了结果，而两种写法在代码里长得一样。
      ⇒ 这里显式判断，NaN 直接判 0.0（"这一项没有贡献"）。
    """
    v = finite(x)
    if v is None:
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def clip1(x):
    """夹到 [-1, 1]。NaN/inf/None → 0.0（中性）。与 `clip01` 同一条 NaN 纪律。"""
    v = finite(x)
    if v is None:
        return 0.0
    if v < -1.0:
        return -1.0
    if v > 1.0:
        return 1.0
    return v


def safe_div(a, b):
    """a/b，分母为 0/None/NaN 或结果非有限时返回 **None**（不是 0、不是 inf）。

    ★返回 None 而不是 0：0 是一个"算出来了、结果是零"的数，会被下游当结论用；
      None 会被 `StrategySkipped` 那条路接住，变成"少了什么"。
    """
    fa, fb = finite(a), finite(b)
    if fa is None or fb is None or fb == 0.0:
        return None
    return finite(fa / fb)


def pct_rank(values, v, min_n=MIN_PCT_SAMPLES):
    """`v` 在 `values` 里的分位（0~100，表示"比 X% 的样本更小/相等"）。

    ★用 `<=`（含相等）：带宽挤到与历史最小值完全相等时，分位应该是 0 而不是被排除。
    ★样本不足 `min_n` ⇒ None（不给假精确）。`values` 里有非有限的点就直接判掉，
      不静默跳过它们 —— 跳过会让分母变小、分位虚高。
    """
    fv = finite(v)
    if fv is None:
        return None
    arr = []
    for x in values:
        fx = finite(x)
        if fx is None:
            return None
        arr.append(fx)
    if len(arr) < int(min_n):
        return None
    return 100.0 * sum(1 for x in arr if x <= fv) / len(arr)


def fmt(x, nd=2):
    """数字 → 固定小数位的字符串；None/NaN → '—'。**只用于拼人读的 `why`**。"""
    v = finite(x)
    if v is None:
        return '—'
    return ('%.' + str(int(nd)) + 'f') % v


def num(x, nd=4):
    """→ JSON 安全的数（NaN/inf/numpy 标量 → None，见 `agent/feed.py: _num`）。"""
    return feed._num(x, nd)
