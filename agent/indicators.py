"""技术指标（纯 pandas）。**输入是 `feed` 的 canonical 列，输出是同索引的派生列。**

★这个模块只有一条规矩，但它是这个站最贵的那条：**算不出来就不给。**
  `rolling(n, min_periods=n)` ⇒ 前 n-1 根是 `NaN`，不是"用短窗口凑一个数"。
  凑出来的数在图上和真的长得一模一样，而它是编的 —— 这一页上每一个点都要能
  指回"哪几根 K 线算出来的"。本仓库反复踩过这条（见 `agent/portfolio.py` 的
  "算不出来就说算不出来"）。

★另一条同源的：**约定要写死在代码里，并说明是哪一个约定。**
  同一个"MACD"在不同软件里 HIST 差一个 2 倍、同一个"BOLL"差一个 ddof=0/1、
  同一个"RSI"差一个**种子**。这些差别不会让任何判据变红（两边都"看着像"），
  所以只能靠**写在注释里**+一条"和独立实现逐点比"的判据钉住。下面每一处
  都标了**选的是哪一种**，并且把**实测差多少**写出来（不是"有差异"，
  是"差 10.04 个点、要 50 根才衰减掉" —— 见 `_recursive_sma`）。

列名用 feed 的 canonical（`agent/feed.py:35`）：`dt/open/high/low/close/volume/amount`。
**不是**前端那套 `{t,o,h,l,c,v}` —— 那套是 `/api/kline` 发给浏览器的形状，
转换只在接口层做一次（`to_arrays()`），别在计算层再发明一套缩写。
"""
from __future__ import annotations

import pandas as pd

# 日线默认参数。**写在这里而不是散在调用处**：判据要拿同一组参数自己再算一遍，
# 两边各写一份的话，"参数漂了"会变成"判据红了"，而红的原因看着像"算法错了"。
MA_WINDOWS = (5, 10, 20, 60)
EMA_WINDOWS = (12, 26)
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
RSI_WINDOWS = (6, 12, 24)
KDJ_N, KDJ_K, KDJ_D = 9, 3, 3
BOLL_N, BOLL_K = 20, 2.0
VOL_RATIO_N = 5


def sma(s: pd.Series, n: int) -> pd.Series:
    """简单移动平均。前 n-1 根是 NaN（数据不够就不给）。"""
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """指数移动平均。

    ★约定：`adjust=False`（递推式，不是"加权平均的展开式"）+ `min_periods=n`。
      `adjust=True` 是 pandas 默认，它给前几根一个**不同**的权重结构 ——
      在窗口头部两个约定能差出百分之几，而图上完全看不出来。
      行情软件（通达信/同花顺）用的是递推式，所以这里跟它们一致。
    """
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def macd(close: pd.Series, fast: int = MACD_FAST, slow: int = MACD_SLOW,
         signal: int = MACD_SIGNAL):
    """MACD。返回 `(dif, dea, hist)`。

    ★约定（三处，每一处都有别的写法）：
      · `dif = EMA(fast) - EMA(slow)`（快线减慢线），不是反的；
      · `dea = EMA(dif, signal)`（dif 的 signal 日 EMA），不是 `EMA(close)`；
      · **`hist = (dif - dea) * 2`** —— 这个 **2 倍**是国内行情软件的画法
        （通达信/同花顺的 MACD 柱），而欧美教材和多数 Python 例子**不乘**。
        乘不乘在图上差一倍高度，两边都"看着像" ⇒ 只能写死在这里。
        前端如果要显示 hist 的数值，必须连这个 2 一起说清楚。
    """
    dif = ema(close, fast) - ema(close, slow)
    dea = dif.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return dif, dea, (dif - dea) * 2


def _recursive_sma(s: pd.Series, n: int) -> pd.Series:
    """通达信口径的 `SMA(X, N, 1)`：`y_t = (x_t + (n-1)·y_{t-1}) / n`，
    **第一个有效值直接取 x 本身**（没有前值时 `y = x`）。

    ★这就是 `alpha = 1/n`、`adjust=False` 的 ewm —— 但**「种子取什么」才是
      两种口径的分水岭**，而这一点 pandas 不会替你说：
        · 本函数（= 通达信/同花顺）：种子 = **第一个信号值本身**；
        · Wilder(1978) / TA-Lib：种子 = **前 n 个信号的简单平均**。
      RSI12 上实测两者**最多差 10 个点量级**（10 点就是从"正常"跨到"超卖"），
      而且要**几十根**才衰减到 0.1 以内。⇒ 这不是"实现细节"，是**同一个名字下
      的两个不同的数**，所以只在这里实现一次、由 RSI 与 KDJ 共用，别各自再写一份。

    ★★**这里原来写的是一个具体数字（"最多差 10.04 个点"）—— 那是错的写法。**
      这个差值**随数据变**：2026-09-21 拿判据里那条合成行情逐点量，最大的那一处
      是 **11.00**（在第 12 根上），不是 10.04。差多少不重要，**重要的是它不可复现**
      —— 写死一个数，下一个人换一组数据来核就会得出"docstring 编数"的结论，
      而真正该被读到的信息（量级、衰减长度）反而没人看。
      ⇒ 所以这里只写**形状**（量级 + 衰减长度），精确值交给判据现场量：
        `verify_agent.py` 的「RSI 两种种子的差异确实是"10 点量级"」只判量级，
        并且**必须**用另一个种子的真实现去比（不能用同一个函数倒推）。

    ★`min_periods=n`：前 n-1 个位置是 NaN（见模块头「算不出来就不给」）。
    """
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI（平滑用 `_recursive_sma`，即**通达信口径**）。

    ★两个"看着都像"的错法，都被这一行的选择挡在外面：
      · **不是**对涨幅做 n 日简单平均 —— 那个在趋势里"更灵敏"，像是更好用，
        而它给出的数跟任何行情软件都对不上；
      · **种子**按上一条：通达信取第一个信号值，TA-Lib 取前 n 个的均值。
        本函数选前者（用户在券商软件里看到的是这个），差异实测见
        `_recursive_sma`。
    ★全平（n 根里一个涨跌都没有）时 `au=ad=0`，公式给出 0/0 = NaN。
      行情软件的约定是 **50**（中性），这里照做 —— 但只在这一种情况下补，
      不拿 50 去填"数据不够"的那些格子（那是两回事：一个是"没有波动"，
      一个是"还没算出来"）。
    """
    d = close.diff()
    up = d.clip(lower=0)
    dn = (-d).clip(lower=0)
    au = _recursive_sma(up, n)
    ad = _recursive_sma(dn, n)
    out = 100 - 100 / (1 + au / ad)
    flat = (au == 0) & (ad == 0)
    return out.mask(flat, 50.0)


def kdj(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = KDJ_N, k_period: int = KDJ_K, d_period: int = KDJ_D):
    """KDJ。返回 `(k, d, j)`。

    ★约定：
      · `rsv = (close - n 日最低) / (n 日最高 - n 日最低) * 100`；
      · `k = SMA(rsv, k_period, 1)`、`d = SMA(k, d_period, 1)` —— 走
        `_recursive_sma`（**通达信口径**，不是简单平均，种子取第一个信号值；
        两种种子的差异见那里）；
      · `j = 3k - 2d`（国内约定，取值范围会跑出 0–100，这是对的，不是 bug）。
      · n 日区间为 0（横盘到最高=最低）时 `rsv` 是 0/0：约定取 **50**。
        这一格**不是**"数据不够"，所以补 50 而不是留 NaN —— 两者在图上
        都是"平"，但一个该画、一个不该画。
      · `j` 会跑出 0–100 之外，**这是设计**：本页不许因为它"越界"就把它
        夹到 0–100（夹了就变成另一个指标了，而两条线在图上仍然贴在一起）。
    """
    ll = low.rolling(n, min_periods=n).min()
    hh = high.rolling(n, min_periods=n).max()
    rng = hh - ll
    rsv = ((close - ll) / rng * 100).mask(rng <= 0, 50.0)
    k = _recursive_sma(rsv, k_period)
    d = _recursive_sma(k, d_period)
    return k, d, 3 * k - 2 * d


def boll(close: pd.Series, n: int = BOLL_N, k: float = BOLL_K):
    """布林带。返回 `(mid, upper, lower)`。

    ★约定：标准差用 **`ddof=0`（总体标准差）**，这是行情软件的画法；
      pandas 的 `Series.std()` 默认 `ddof=1`（样本标准差）。n=20 时两者差
      约 2.6%，带宽差 2.6% —— 图上是一条"稍微胖一点"的带子，看不出来。
      ⇒ 判据必须拿 `ddof=0` 的独立实现去比，否则比出来的是"约定不同"。
    """
    mid = close.rolling(n, min_periods=n).mean()
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def vol_ratio(volume: pd.Series, n: int = VOL_RATIO_N) -> pd.Series:
    """量比：**当日成交量 ÷ 前 n 日均量**。

    ★这个定义**不是**交易所口径的"量比"（那个要按已开盘分钟数折算，
      本模块没有分钟数据、也不该假装有）。所以名字取的是"量比"这个通用词，
      而页面上必须写清楚算的是哪一个 —— 两个都叫"量比"的数差得很远。
    ★前 n 日均量是**不含当日**的（`shift(1)`）：含当日的话，一根放量柱子
      会把自己拉低，看起来像"没放量"。
    """
    prev = volume.shift(1).rolling(n, min_periods=n).mean()
    return volume / prev.where(prev > 0)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """canonical 日线 → 追加全部指标列。**返回新 DataFrame，不改入参。**

    不足窗口的格子一律 NaN（见模块头）。调用方要不要把 NaN 画成断线、
    要不要在页面上说明"前面这段算不出来"，是接口层/渲染层的事 ——
    这里**不替它们决定**（比如拿第一个有效值往前填，那会凭空造出一段历史）。
    """
    out = df.copy()
    close, high, low, vol = out['close'], out['high'], out['low'], out['volume']
    for n in MA_WINDOWS:
        out['ma%d' % n] = sma(close, n)
    for n in EMA_WINDOWS:
        out['ema%d' % n] = ema(close, n)
    out['dif'], out['dea'], out['hist'] = macd(close)
    for n in RSI_WINDOWS:
        out['rsi%d' % n] = rsi(close, n)
    out['k'], out['d'], out['j'] = kdj(high, low, close)
    out['boll_mid'], out['boll_up'], out['boll_low'] = boll(close)
    out['vol_ratio'] = vol_ratio(vol)
    return out


# ── 周期：日 / 周 / 月 ───────────────────────────────────────────────────
# ★日周月**不新开数据源**：周线就是日线聚合出来的。多一个数据源就多一处
#   "两边说的不是同一件事"，而它们在图上长得一样。
#
# ★`ME` 不是 `M`：pandas 2.2 起 `M` 被弃用、3.0 起报错。本机是 2.3.3，
#   写 `M` 现在只会**警告**并在将来的版本里静默变成"月末"之外的东西 ——
#   所以直接写 `ME`（month end），别写一个现在能用、以后会变意思的别名。
_RESAMPLE_RULES = {'day': None, 'week': 'W', 'month': 'ME'}


def resample_ohlc(df: pd.DataFrame, period: str, now=None) -> pd.DataFrame:
    """日线 → 周线/月线（`period` ∈ day/week/month）。返回 canonical 列 + 两列说明。

    `now` 只影响最后一根的 `partial`（可注入是为了让判据不受运行日期影响）。

    ★聚合规则（每一条都有别的合理选法，所以写死在这儿）：
      · `open` 取**桶内第一根**、`close` 取**最后一根**、`high/low` 取极值；
      · `volume/amount` **求和**（不是平均 —— 平均出来的"周成交量"没有任何含义）；
      · 空桶**丢掉**（停牌、长假）：留着会画出一根开高低收全 0 的柱子，
        看着像"那一周跌没了"。

    ★★`partial`：**最后一根的周期可能还没走完**。这是这个函数唯一容易骗人的
      地方 —— 把半个月当成一个月画出来，图上那一根"缩量"的月线会被读成
      "这个月在缩量"，而事实是这个月只过了一半。

      **判据只有一条，而且不含任何启发式：桶的右端还在未来 ⇒ `partial=True`。**
      右端 = 桶标签当天**结束**（标签日 +1 天）。于是：
        · 今天 2026-09-20，月桶标签 2026-09-30 ⇒ 10-01 > 今天 ⇒ True（本月没走完）
        · 数据停在 2024-06-30（已过去）⇒ 07-01 > 今天为假 ⇒ False（那是完整的一个月）
        · 周桶标签是**周日**（pandas `W` 的右端）⇒ 只要数据盖到这周的周五，
          标签周日就在今天之后 ⇒ True。**这是对的**：那条周线的周期是"周一到
          周日"，周日还没过完。

      ★**上一版用的是「最后一根日线早于桶标签 ⇒ partial」，那是错的**：周桶
        标签是周日，而交易日到周五 ⇒ **几乎每一份数据的最后一根周线都会被打上
        "未走完"**。一个恒真的标记没有信息量，而且会让用户学会忽略它 ——
        而它要标的那件事（本月只有半个月）恰恰是最该被看见的。
      ★**时间必须可注入**（`now=`）：判据要能钉住"现在是几号"才能在两个分支上
        都跑一遍。不注入的话，这条判据会在**每个月 1 号**莫名其妙地变色。

      ★★**"数据被截断"是另一件事，不要拿 `partial` 去指它。** 桶的右端已过去、
        但桶内只有 10 根日线（同周期其他桶的中位数是 21）——那是**取数只取到
        月中**，不是"这个月没走完"。它会显示成一根"缩量"的月线，同样该被标出来，
        但它和 `partial` 不是一个原因，混在一起就两边都说不清。
        ⇒ 这个信号由 `n_bars` 提供（调用方自己比中位数），**本函数不替它下结论**。
    """
    if period not in _RESAMPLE_RULES:
        raise ValueError('不认识的周期：%r（只有 day/week/month）' % (period,))
    rule = _RESAMPLE_RULES[period]
    now = pd.Timestamp.today().normalize() if now is None else pd.Timestamp(now).normalize()
    # 桶的右端 = 标签当天结束后（标签日 +1 天）；`partial` = 「右端还在未来」
    def _open_ended(label) -> bool:
        return bool(pd.Timestamp(label).normalize() + pd.Timedelta(days=1) > now)

    out = df.copy()
    out['dt'] = pd.to_datetime(out['dt'])
    if rule is None:
        out['n_bars'] = 1
        # 日线也走同一条判据：**今天那根**的周期（今天）还没过完 ⇒ partial=True；
        # 昨天及以前的都是完整的。不要在这里写死 False —— 那就成了两套判据。
        # ★★这一列是**逐行**的，不是"整列一个值"：2026-09-21 查实，原来写成
        #   `out['partial'] = bool(_open_ended(out['dt'].iloc[-1]))` 是**广播** ——
        #   末根是今天时，220 根日线**全被标成 partial**，而下面周/月那两支
        #   只标最后一根。这两支自己打架，而 docstring 站在周/月那边
        #   （"昨天及以前的都是完整的"）。
        #   危害正是本文件在 `partial` 那一节反复说的那件事：**恒真的标记没有信息量**，
        #   用户会学会忽略它，而它要标的那件事（今天这根没收盘）反而看不见了。
        out['partial'] = False
        if len(out):
            out.loc[out.index[-1], 'partial'] = _open_ended(out['dt'].iloc[-1])
        return out
    g = out.set_index('dt')
    agg = pd.DataFrame({
        'open': g['open'].resample(rule).first(),
        'high': g['high'].resample(rule).max(),
        'low': g['low'].resample(rule).min(),
        'close': g['close'].resample(rule).last(),
        'volume': g['volume'].resample(rule).sum(),
        'amount': g['amount'].resample(rule).sum(),
        'n_bars': g['close'].resample(rule).count(),
    })
    agg = agg[agg['n_bars'] > 0]                 # 空桶丢掉
    agg['partial'] = False
    if len(agg):
        agg.loc[agg.index[-1], 'partial'] = _open_ended(agg.index[-1])
    return agg.reset_index().rename(columns={'index': 'dt'})[out.columns.tolist()
                                                             + ['n_bars', 'partial']]


def to_arrays(df: pd.DataFrame) -> dict:
    """canonical → 前端那套列式数组（`/api/kline` 的 `data.bars` 形状）。

    ★`NaN` → `None`：JSON 里没有 NaN（`json.dumps` 会写出非法的 `NaN` 字面量，
      严格的解析器直接报错）。这一步**必须**在这里做完 —— 散在各个接口里做的话，
      迟早有一个接口漏掉，而它的症状是"前端某个字段变成 null、图断一截"。
    """
    def col(name):
        return [None if pd.isna(v) else float(v) for v in df[name]]

    return {'t': [str(x) for x in df['dt']],
            'o': col('open'), 'h': col('high'), 'l': col('low'),
            'c': col('close'), 'v': col('volume')}
