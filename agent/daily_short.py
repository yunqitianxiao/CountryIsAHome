# -*- coding: utf-8 -*-
"""港美股的**日线口径**短期策略 —— `intraday.analyze()` 的降级替身。

★存在的理由是**数据源**，不是"港美股该用日线"：
  `datasource.minute()` 对 hk_stock/us_stock/crypto **没有可用来源**。2026-09-22 实测：
    · 腾讯 `fqkline` 的 m5 对港美股只回一根聚合日线；
    · 新浪 `getKLineData` 只服务 A 股；
    · 东财 `push2his`（akshare 的 `stock_hk/us_hist_min_em` 也走它）在本机**不可达** ——
      直连与走 Clash 代理都是 `RemoteDisconnected`；**对照**：同一个东财的
      datacenter-web 域名是通的（`stock_boards('000002')` 0.3s 取到 33 行）。
  ⇒ 那些市场的 5/15/30 分钟策略是**没有数据可算**，不是"没接线"。
  而**日线**三个市场都有源且快（实测 hk 0.2s / us 0.6s，各 120 行）。

★**为什么另开一个模块，而不是让 `intraday.analyze()` 内部降级**：
  `agent/intraday/` 是另一个并行工作者的地盘 —— 它的 `agents/_probe.py` 里写着
  "A2 的 `agent/intraday/` 和我并行在改"，而且 2026-09-22 20:32 还有一批写入
  （`strategies.py` / `levels.py` / `_probe.py` 在同一个分钟级窗口落盘）。
  在那边加一条日线分支要同时改 `_fetch` / `_norm_periods` / 各策略的语义，
  两边都动同一个函数之后就**没有可单独回滚的粒度**了。
  本模块只**读**它的通用件（`_composite` / `_volume_block` / `_levels_block` /
  `_norm_frame` / `strategies.*` / `regime.classify`），**不写它任何一个文件** ⇒
  删掉本文件即完全回滚（`app.py` / `weights.py` 那两处接线各是一个 if 分支）。

★**口径必须被看见**：返回里 `bar_scale='daily'`、`periods=[1440]`、
  `period_label='日线'`，并且 `errors` 里**逐条**说明哪几条策略因为日内口径不成立
  而没跑。**不冒充分钟级** —— 页面上写着"5/15/30"而实际算的是日线，比不显示更坏
  （用户会拿它当分钟级结论用）。

★**哪几条策略在日线上仍然成立**（这是本模块唯一的策略取舍）：
    · `momentum` / `squeeze` / `divergence` / `rvol` —— 都只看"K 线序列的形态与量能"，
      与 bar 的时长无关；
    · `atr` —— ATR 止损目标位，同样只依赖序列（`side` 恒 flat，不进票）。
  在日线上**不成立**的 4 条（`orb` 开盘区间 / `vwap_revert` 日内 VWAP /
  `volume_profile` 当天分布 / `session` 时段效应）与 `resonance`（共振要有多个周期）
  —— 它们的定义里都写着"当天"或"多周期"，日线上要么只有一根 K、要么只有一个周期，
  所以**明写进 errors**，不静默消失。

★**`as_of` 日期上界**（`/research` 研判页要的能力）：`analyze(..., as_of='2025-06-30')`
  只喂 `dt <= as_of` 的 K 线，回答的是"**站在那一天，能看到的材料是什么**"。
  这是本仓**唯一一处日期上界** —— `game.py:217` 那个切法是下界（`>= buy`），
  `feed.fetch` 只能取到"今天为止"，`datasource.daily` 没有 `end`。别把它当
  "顺手多一个参数"，下面三件事各自都是一条纪律：
    · **`days` 必须反推**。取数层拿的是"到今天为止的最近 N 根"，所以 N 得是
      "今天倒推回 as_of 要多少根" + "研判本身要多少根"。照搬默认的 `DAILY_DAYS`
      会让 as_of 靠前时**截断后一根都不剩**，而 `analyze` 那时仍会 `ok=True`
      并给出一份空读数 —— 看起来像"这只票没信号"。
    · **取数层把 `days` 夹在 1200（约 4.8 年）**，as_of 太远时就是取不到足够 K 线。
      这种时候**明确报错**（`errors` 里写清是"太早"还是"太远"），不拿一小段硬算
      —— 与 `game.py` 的"抓不到就明确报错，绝不编数字"是同一条。
    · `as_of=None`（默认）时全函数**逐字节等于**加这个参数之前的行为。
  ★顺带一个好处：as_of 是历史日期时最后一根 K 必然已定稿（`_bar_closed` 判 True），
    上面 `rvol_ex_last_bar` 那条"盘中缩量假象"的警告不会触发 —— 研判页天然避开了
    我在日线降级里踩过的那个坑（见下面 `_adjust_of` 上方那段）。
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:                       # 与 `app.py` 同款：本仓的包按顶层名 import
    sys.path.insert(0, _HERE)

import indicators as _ind                                                # noqa: E402
import intraday                                                          # noqa: E402
from intraday import regime as _rg, sessions as _ss                      # noqa: E402
from intraday import strategies as _st                                   # noqa: E402
from intraday.algo import StrategySkipped, num                           # noqa: E402

__all__ = ['analyze', 'AS_OF_MIN_BARS', 'DAILY_PERIOD', 'MARKETS_WITHOUT_MINUTE',
           'needs_daily']

# 一天 1440 分钟。★用这个值当 `period` 而不是自己发明 `'D'`：
#   `intraday` 全链路的 period 都是"分钟数"，`sessions.bars_per_day(mkt, 1440)`
#   恰好 = `max(1, 240//1440)` = 1（一天一根），`_days_for` 那套反算也仍然成立。
#   换成一个字符串就会同时打穿 `sessions` 与 `bars_per_day` 的类型假设。
DAILY_PERIOD = 1440

# 请求多少天日线。要盖住最长的那条策略的窗口：
# `momentum` 要 max(ATR_N+1, RSI_N+1, ROC_N+1)、`squeeze` 要 BOLL_N+SQZ_LOOKBACK、
# `regime.classify` 要 ATR_N + EMA + 斜率滞后。取 400（上游腾讯链能给到 640，新浪更深）。
DAILY_DAYS = 400

# ★`as_of` 截断之后至少要剩多少根，这份研判才还有意义。
#   取的是**最长的那条策略的窗口**，并且**算出来**而不是写死 80：
#   `squeeze` 调的 `regime.boll_width_pctile` 要 `BOLL_N + SQZ_LOOKBACK` 根
#   （`strategies.py:619` 的 StrategySkipped 原话就是"至少 %d 根"）。
#   写成 `= 80` 的话，哪天 `SQZ_LOOKBACK` 调了，这里会**静默**变成一条太松的门槛。
#   为什么要拦：不够时各策略会各自 `StrategySkipped`，而 `analyze` 仍然
#   `ok=True`、给出一份只剩两三条信号的读数 —— 读起来像"这只票今天没信号"，
#   实际是"as_of 那天的材料不够"。⇒ 在截断处就拦住，把真正的原因说出来。
AS_OF_MIN_BARS = _ind.BOLL_N + _rg.SQZ_LOOKBACK

# ★**没有分钟线源的市场白名单**。按实测列，不按"外国市场"猜：
#   `datasource.minute()` 对这三个的出口是 `SourceError('NO_MINUTE_SOURCE')`。
#   写成"哪些市场有"的反面表而不是"hk/us 两个 if"：将来 crypto 的分钟线有源了、
#   或者 A 股之外多一个市场时，改的是这一张表，而不是散在调用方的那句判断。
MARKETS_WITHOUT_MINUTE = ('hk_stock', 'us_stock', 'crypto')

# 日线上仍然成立的策略（顺序即 signals 的输出顺序，与 `STRATEGY_ORDER` 精神一致：
# 先量能后形态、最后放不给方向的 ATR）。
_PERIOD_STRATEGIES = (
    ('rvol', _st.rvol_sig),
    ('divergence', _st.divergence_sig),
    ('momentum', _st.momentum),
    ('squeeze', _st.squeeze),
    ('atr', _st.atr_sig),
)

# 日线口径下**不成立**的策略，连同"为什么不成立"的原话 —— 逐条进 errors。
_INTRADAY_ONLY = (
    ('orb', '开盘区间突破', '它的区间定义在"当天开盘后的最初若干根 K"上，'
                            '日线上当天只有一根 K ⇒ 区间宽度就是那一根的幅度，突破判定没有意义'),
    ('vwap_revert', 'VWAP 偏离回归', 'VWAP 是**日内**成交量加权均价，每天重置；'
                                     '日线上每天的 VWAP 等于当天均价，偏离失去参照'),
    ('volume_profile', '成交量分布', 'POC / 价值区按**当天**的分钟量摊桶；'
                                     '日线上一天一个样本，分布退化成单点'),
    ('session', '时段效应', '开盘 30 分 / 午盘 / 尾盘的切分是分钟级的；'
                            '日线上没有"时段"这个维度'),
    ('resonance', '多周期共振', '共振要有**至少两个周期**才有"共"可言；'
                                '日线口径只有一个周期'),
)


def needs_daily(market):
    """这个市场是不是"没有分钟线源、只能走日线"。认不出的市场**不猜**（返回 False）。"""
    try:
        return _ss.norm_market(market) in MARKETS_WITHOUT_MINUTE
    except ValueError:
        return False


def _as_date(v):
    """`as_of` → `datetime.date`；认不出就 `ValueError`。

    ★只收 `YYYY-MM-DD`（与 `game._day` 同一套措辞），**不吃别的格式**：
      `pd.Timestamp('06/30/2025')` 会按美式月/日解释成 6 月 30 日，而写的人
      可能是想写 6 月 30 日也可能是别的 —— 一个把 3 月 4 日和 4 月 3 日互换的
      解析器，产出的是一份**日期错了但看起来正常**的研判。宁可报"看不懂"。
    """
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        try:
            return dt.datetime.strptime(v.strip(), '%Y-%m-%d').date()
        except ValueError:
            pass
    raise ValueError('as_of 看不懂：「%r」，要写成 2026-01-31 这样' % (v,))


# ★这个陷阱是**日线口径独有的**，所以它归本模块管，不归 `intraday`：
#   分钟 K 一收盘就定稿；日线的那一根是**当天**的，盘中它一直在变。
#   2026-09-22 21:57（北京）= 09:57（纽约）= 美股开盘后 27 分钟，实测 BABA 的
#   末行 `dt=2026-09-22`、`volume=4,745,208` 而全天均量 15,550,682 ⇒
#   RVOL 读出 0.31"缩量" —— 那不是缩量，是**这天只走了 27 分钟**。
#   分钟级不会出现这个形状（最后一根是刚收盘的那 5 分钟，不是"半个今天"）。
def _adjust_of(src, mkt):
    """从**源名本身**读复权口径 → `(mode, note)`；不认识这个源就 `(None, 为什么)`。

    ★为什么能从源名读：`datasource/tencent.py` 里 `df.attrs['source'] =
      '%s.kline.%s' % (SRC, key)`，而 `key` 是**服务端回的那个 key 名**
      （`_pick_series` 从回包里挑的），不是我们请求时写死的 —— 所以
      `qfqday` / `day` / `hfqday` 是上游的实际口径，不是我们的意图。
      实测 2026-09-22：`daily('hk_stock','00700')` 与 `daily('us_stock','BABA')`
      都回 `tencent.kline.day`，**尽管 `datasource.daily` 默认传 `adjust='qfq'`**
      ⇒ 腾讯对港股/美股的 K 线**不提供复权**（传了也回不复权）。
      A 股同一条链回的是 `tencent.kline.qfqday`（前复权生效）。
    ★不认识的源（新浪 `daily_via_scale`、akshare、STUB）**不猜** —— 它们的
      复权口径我没实测过，返回 None 比返回一个猜的 'qfq' 安全。
    """
    s = str(src or '')
    if not s.startswith('tencent.kline.'):
        return None, '源是 %r，它的复权口径我没实测过（只有 tencent.kline.* 我核过）' % s
    k = s.rsplit('.', 1)[-1].lower()
    if 'qfq' in k:
        return 'qfq', '前复权（源名里的 key=%s）' % k
    if 'hfq' in k:
        return 'hfq', '后复权（源名里的 key=%s）' % k
    return 'none', ('**不复权**（源名里的 key=%s）：腾讯对港股/美股的 K 线不提供复权，'
                    '即使请求里传了 qfq 也回这一步 ⇒ 除权/分红日的价格跳空会进 ATR 与 '
                    '均线，止损会偏宽、跨除权日的动量会失真' % k)


def _bar_closed(df, mkt, now=None):
    """最后一根 K 定稿了吗 → `(last_dt, closed, why)`；`closed=None` = 判不了。

    ★`now` 可注入（判据要能喂任意时刻，否则这条逻辑没法在"盘中"这个状态下被测）。
    ★判不了时返回 `None` 而**不是猜一个 True**：时区拿不到就说拿不到，
      猜 True 会让"未定稿"这个警告在最需要它的时候消失。
    """
    c = _ss.cfg(mkt)
    last = pd.Timestamp(df['dt'].iloc[-1])
    if now is None:
        try:
            now = pd.Timestamp.now(tz=c['tz'])
        except Exception as e:                                       # noqa: BLE001
            return last, None, ('取不到 %s 的时区（%s: %s）⇒ 判不了这根 K 定稿没有'
                                % (c['tz'], type(e).__name__, e))
    ld, nd = last.date(), now.date()
    tod = now.hour * 60 + now.minute
    if ld > nd:
        return last, None, ('最后一根 K 的日期 %s 晚于 %s 的今天 %s —— 数据或时区不对'
                            % (ld, c['tz'], nd))
    if ld < nd:
        return last, True, '最后一根是 %s，早于 %s 的今天 %s ⇒ 已收盘定稿' % (ld, c['tz'], nd)
    if int(c['open']) <= tod < int(c['close']):
        return last, False, ('现在是 %s 的 %02d:%02d，在该市场交易时段内 ⇒ 今天这根 K '
                             '**还没收盘**，它的量、收盘价、最高最低都还会变'
                             % (c['tz'], now.hour, now.minute))
    if tod >= int(c['close']):
        return last, True, '现在是 %s 的 %02d:%02d，今天已收盘' % (c['tz'], now.hour, now.minute)
    return last, False, ('现在是 %s 的 %02d:%02d，还没开盘，却已经有今天这根 K ⇒ '
                         '它不可能是正常收盘价' % (c['tz'], now.hour, now.minute))


def analyze_auto(market, code, periods=None, name=None, as_of=None):
    """**唯一的接线点**：没有分钟线源的市场走日线口径，其余原样交给 `intraday.analyze()`。

    ★为什么要有这个函数、而不是让三个调用方各自写一句
      `if daily_short.needs_daily(...)`：那样"哪些市场走日线"这件事就有了**三份
      真源**，将来 crypto 的分钟线有源了要改三个地方、漏一个就有一处行为不一致。
      这里一份；调用方只需把 `intraday.analyze(...)` 换成 `analyze_auto(...)`（一行）。
    ★`periods` 只对分钟那条路有意义（日线口径固定 1440）—— 调用方传了 5/15/30
      却走日线时，**必须让它知道这个周期被忽略了**：它多半会拿 `periods` 去
      `signals` 里筛，而日线的 `period` 是 1440，一条都筛不出来，看起来就像
      "策略全挂了"。所以这条说明放在 `errors[0]`。
    ★`as_of` 一给就**必须**走日线口径，哪怕这个市场有分钟源：
      `intraday.analyze()` 里没有日期上界（那是我不能改的别人的模块）。要是
      在这里静静忽略 `as_of`、把"截至今天"的材料当成"截至 as_of"的交给调用方，
      产出的就是一份**含未来数据、却自称是历史研判**的读数 —— 这正是本功能
      唯一一条硬纪律要防的事。⇒ 不给它静默忽略的机会：换口径，并在 errors[0] 说清。
    """
    if as_of is not None and not needs_daily(market):
        out = analyze(market, code, name=name, as_of=as_of)
        out['errors'].insert(
            0, '⚠ 你要了 as_of=%s，但 %s 在本机走的是分钟级那条路，而那条路'
               '**没有日期上界**（`agent/intraday/` 里没有这个能力）⇒ 已改按**日线**'
               '口径算（period=%d），材料只到 as_of 那天为止'
               % (as_of, out.get('market'), DAILY_PERIOD))
        return out
    if needs_daily(market):
        out = analyze(market, code, name=name, as_of=as_of)
        try:
            want = tuple(int(p) for p in periods) if periods else ()
        except (TypeError, ValueError):
            want = ()
        if want and want != (DAILY_PERIOD,):
            out['errors'].insert(
                0, '⚠ 你要的周期是 %s，但 %s 在本机没有分钟线源，已按**日线**口径算'
                   '（period=%d）：理由与取舍见 agent/daily_short.py 模块头'
                   % (list(want), out.get('market'), DAILY_PERIOD))
        return out
    return intraday.analyze(market, code, periods=periods or intraday.DEFAULT_PERIODS,
                            name=name)


def analyze(market, code, days=DAILY_DAYS, name=None, as_of=None):
    """→ 与 `intraday.analyze()` **同形状**的 dict（`bar_scale='daily'` 标出口径）。

    ★**任何情况下都不抛异常** —— 与 `intraday.analyze()` 同一条纪律：取不到就
      `ok=False` 并把原因写进 `errors`。"没数据"和"没信号"是两件事。
    ★`as_of`（`YYYY-MM-DD` / `date`）只喂 `dt <= as_of` 的 K 线，见模块头那一节。
      不给（`None`）就是原来的行为。
    """
    out = {
        'ok': False, 'market': market, 'code': code, 'name': name, 'ts': None,
        'periods': [DAILY_PERIOD], 'bar_scale': 'daily', 'period_label': '日线',
        'bars': {}, 'bars_per_day': {},
        'regime': {}, 'signals': [],
        'composite': {'score': 0.0, 'side': 'flat', 'confidence': 0.0,
                      'agree': {'long': 0, 'short': 0, 'flat': 0}},
        'volume': {'rvol': None, 'rvol_by_period': {}, 'rvol_naive_by_period': {},
                   'profile': None, 'divergence': None,
                   'rvol_ex_last_bar': None},
        'levels': None,
        'session': {'phase': None, 'window': None,
                    'note': '日线口径没有时段效应（见 errors 里「时段效应」那一条）'},
        'last_bar': None, 'price_adjust': None, 'upstream_source': None,
        'errors': [], 'data_source': None,
    }
    errors = out['errors']
    try:
        mkt = _ss.norm_market(market)
    except ValueError as e:
        errors.append(str(e))
        return out
    out['market'] = mkt

    # ── as_of（日期上界）：解析 + 把 days 反推成"够截断用的"根数 ──
    as_of_d = None
    if as_of is not None:
        try:
            as_of_d = _as_date(as_of)
            # ★归一化 + 契约校验。`analyze` 的纪律是**永不抛**，所以"解析函数
            #   回了意料之外的东西"也必须在这里接住 —— 不然下面那句
            #   `as_of_d > today_d` 会抛 TypeError，把"参数不对"报成"服务端坏了"。
            #   ★`dt.datetime` 是 `dt.date` 的**子类**，`isinstance` 会放行它，
            #     而它与 date 不可比 ⇒ 只写 isinstance 会漏掉这一支，所以先拍平。
            if isinstance(as_of_d, dt.datetime):
                as_of_d = as_of_d.date()
            if not isinstance(as_of_d, dt.date):
                raise ValueError('as_of 解析成了 %r —— 我要一个日期' % (as_of_d,))
        except ValueError as e:
            errors.append(str(e))
            return out
        today_d = dt.date.today()
        if as_of_d > today_d:
            # 与 `game.py:180` 的 FUTURE_DATE 同一条：站在还没到的那天没有材料可看。
            # ★这里不"顺带当成今天"—— 那样调用方以为自己在看历史，其实在看今天。
            errors.append('as_of 是 %s，还没到（今天是 %s）⇒ 那一天的材料还不存在'
                          % (as_of_d.isoformat(), today_d.isoformat()))
            return out
        # ★天数反推。`feed.fetch` / `datasource.daily` 拿的是"**到今天为止**的
        #   最近 days 根"，所以 days 得是"今天倒推回 as_of 要多少根" + "研判本身
        #   要多少根"。前半段直接用 `game._days_needed` —— 它就是为这个问题写的
        #   （交易日 ≈ 自然日 ×5/7、留 10 根吃节假日、夹在 1200），那里已经有一份
        #   实现，不在这里抄第二份（抄了就会有一天两边不一样）。
        #   ★`back + days` 会被取数层再夹一次 1200 ⇒ as_of 太远时**根本取不够**，
        #   由下面截断处的根数检查接住，在这里假装算得出来是错的。
        try:
            from game import _days_needed                       # noqa: SLF001
            back = _days_needed(as_of_d, today_d)
        except Exception:                                       # noqa: BLE001
            back = max(60, min(int(max((today_d - as_of_d).days, 0) * 5 / 7) + 10, 1200))
        days = back + days

    # ── 取数（日线；即使这个市场有分钟源也照样能走这条路，调用方决定用不用）──
    try:
        from datasource import daily as ds_daily
    except Exception as e:                                       # noqa: BLE001
        errors.append('这一层没有数据源：`from datasource import daily` 失败（%s: %s）'
                      % (type(e).__name__, e))
        return out
    try:
        raw = ds_daily(mkt, code, days)
    except Exception as e:                                       # noqa: BLE001
        errors.append('datasource.daily(%r, %r, %d) 抛出 %s: %s'
                      % (mkt, code, days, type(e).__name__, e))
        return out
    if raw is None or not len(raw):
        errors.append('datasource.daily(%r, %r, %d) 回的是空的（0 行）'
                      % (mkt, code, days))
        return out
    # ★源名必须在 `_norm_frame` **之前**取：它内部是 `pd.DataFrame(cols)` 重建，
    #   `attrs` 会一起丢掉（只把 `dropped` 另存回来）。晚一步取就只剩 None。
    src = (getattr(raw, 'attrs', None) or {}).get('source')
    try:
        df = intraday._norm_frame(raw, '日线')                   # noqa: SLF001 —— 同包内复用
    except intraday._NoData as e:                                # noqa: SLF001
        errors.append('日线帧不成形：%s' % e)
        return out
    # ★`dropped` 在**任何切片之前**取：`df[mask]` 不保留 `attrs`（`_norm_frame`
    #   自己就是靠另存把它带回来的），截断之后再读只会读到 None。
    n_drop = int(df.attrs.get('dropped') or 0)
    # ── as_of 上界截断（★本仓唯一一处日期上界）──
    if as_of_d is not None:
        df = df[df['dt'] <= pd.Timestamp(as_of_d)].reset_index(drop=True)
        # ★根数不够时**明确报错**，不拿一小段硬算。两种原因分开说 ——
        #   它们要用户做的事完全不同（一个是"换个晚一点的日子"，一个是"本站就
        #   只能回看这么久"），合成一句就等于两句都没说。
        if not len(df):
            errors.append('as_of=%s 太早了：这只标的的日线最早只回到 %s'
                          % (as_of_d.isoformat(),
                             str(raw['dt'].iloc[0])[:10] if len(raw) else '（取不到）'))
            return out
        if len(df) < AS_OF_MIN_BARS:
            errors.append(
                'as_of=%s 太远了：截断后只剩 %d 根日线，不足 %d 根 ⇒ 研判要的窗口'
                '（最长的一条是 squeeze 的 BOLL%d + %d 根）盖不住。'
                '取数层最多回看 1200 个交易日（约 4.8 年），as_of 再往前就取不到'
                '那之前的行情了' % (as_of_d.isoformat(), len(df), AS_OF_MIN_BARS,
                                    _ind.BOLL_N, _rg.SQZ_LOOKBACK))
            return out
    # ★★★`data_source` 用的是 `intraday` 的**约定**，不是上游的源名：
    #   `intraday._fetch` 真数据回字面量 `'datasource'`、合成回 `'SYNTH(...)'`，
    #   而 `tools.is_synthetic` 就是按 `not startswith('datasource')` 判的。
    #   实测踩过：把上游真源名 `'tencent.kline.day'` 塞进这个字段 ⇒
    #   `is_synthetic` 判**合成** ⇒ 页面和聊天摘要都会打出
    #   "★★这一份不是真行情…价位与成交量都是造出来的" —— **真行情被标成编的**。
    #   具体哪个上游另放 `upstream_source`（那是给人看的，不参与任何判定）。
    _stub = (src is None) or str(src).upper().startswith('STUB')
    out['data_source'] = 'STUB(daily)' if _stub else 'datasource'
    out['upstream_source'] = src or '未标注（上游没给 attrs["source"]）'
    if src is None:
        # ★"认不出"与"已知是桩"是两件事，报出来也不该合成一句：
        #   合成的措辞会把"我不知道"说成"我知道这是假的"。
        errors.append('⚠ 上游没给 `attrs["source"]` ⇒ 认不出这是真行情还是桩。'
                      '按"可能不是真的"处理（`data_source` 不以 datasource 开头，'
                      '`is_synthetic` 会判它合成）—— 宁可白紧张，不拿它当行情讲')
    elif _stub:
        # ★桩数据不能冒充真行情。上游 `datasource` 在 `stub_active()` 时会在
        #   `attrs['source']` 里写 'STUB' —— 这是唯一能把两者分开的地方。
        errors.append('⚠ 这是**桩数据**（datasource 处于 stub 模式），不是真行情 ⇒ '
                      '下面所有读数只能当管线通不通的检查，不能当结论')
    # ── 复权口径（能从源名读出来就读，读不出就说读不出）──
    adj, adj_note = _adjust_of(src, mkt)
    out['price_adjust'] = {'mode': adj, 'note': adj_note}
    if adj == 'none':
        errors.append('⚠ 价是不复权的：%s' % adj_note)
    out['bars'][str(DAILY_PERIOD)] = int(len(df))
    out['bars_per_day'][str(DAILY_PERIOD)] = int(_ss.bars_per_day(mkt, DAILY_PERIOD))

    # ── 这根 K 定稿了没（日线口径独有的陷阱，见 `_bar_closed` 上面那段）──
    last_dt, closed, why = _bar_closed(df, mkt)
    # `ts` 保持与 `intraday` 同格式（`'%Y-%m-%d %H:%M'`）—— 上游 `_session_block`
    # 是这么写的，前端按它解析；日线的时分是 00:00（上游把日期归零），
    # 给人看的干净日期在 `last_bar['date']`。
    out['ts'] = last_dt.strftime('%Y-%m-%d %H:%M')
    out['last_bar'] = {'dt': str(last_dt), 'date': last_dt.strftime('%Y-%m-%d'),
                       'closed': closed, 'why': why,
                       'bars': int(len(df))}
    if closed is False:
        errors.append('⚠ 最后一根 K（%s）**未收盘**：%s ⇒ RVOL、形态、止损位都是按 '
                      '"半根 K"算出来的，别当定稿读数用'
                      % (last_dt.strftime('%Y-%m-%d'), why))
    elif closed is None:
        errors.append('⚠ 判不了最后一根 K（%s）定稿没有：%s'
                      % (last_dt.strftime('%Y-%m-%d'), why))

    # `n_drop` 已在上面截断**之前**取好（切片不保留 `attrs`）。
    if n_drop:
        errors.append('日线：数据源返回的帧里有 %d 行缺收盘价/时间（已剔除，不计入任何统计）'
                      % n_drop)

    # ── regime（算不出就不塞值；塞一个默认的 'range' 就是编）──
    try:
        rg, caveat = _rg.classify(df)
        out['regime'][str(DAILY_PERIOD)] = rg
        if caveat:
            errors.append('日线 regime：%s' % caveat)
    except StrategySkipped as e:
        errors.append('日线 regime：%s' % e.reason)
    except Exception as e:                                       # noqa: BLE001
        errors.append('日线 regime：算的时候抛了 %s: %s' % (type(e).__name__, e))

    # ── 逐条策略 ──
    signals = []
    for sid, fn in _PERIOD_STRATEGIES:
        try:
            got = fn(df, DAILY_PERIOD, mkt)
        except StrategySkipped as e:
            errors.append('日线 %s：%s' % (sid, e.reason))
            continue
        except Exception as e:                                   # noqa: BLE001
            errors.append('日线 %s：算的时候抛了 %s: %s' % (sid, type(e).__name__, e))
            continue
        # 与 `intraday._run` 同一条硬校验："只产出一条"是两层之间的契约；
        # 多条的话 composite 会把同一条策略重复计票。
        if len(got) != 1:
            errors.append('日线 %s：应恰好产出 1 条信号，实际 %d 条（已按第 1 条处理）'
                          % (sid, len(got)))
        if got:
            signals.append(got[0])

    # ★日内专属的**明写**进 errors，不静默消失（否则读者会以为"日线只算得出 5 条"
    #   是这个标的的属性，而不是口径的取舍）。
    for _sid, nm, why in _INTRADAY_ONLY:
        errors.append('日线口径下不跑「%s」：%s' % (nm, why))

    out['signals'] = signals
    # `n_expected` = 本口径**真正该跑出来**的条数（5），不是 intraday 那边的
    # `len(PER_PERIOD) * len(plist) + 1` —— 用那边的数会让 `confidence` 的
    # `cover` 项恒约 0.5，把"口径取舍"读成"数据缺了一半"。
    out['composite'] = intraday._composite(signals, 1, len(_PERIOD_STRATEGIES))  # noqa: SLF001

    frames = {DAILY_PERIOD: df}
    plist = [DAILY_PERIOD]
    try:
        intraday._volume_block(frames, plist, mkt, out, errors)   # noqa: SLF001
    except Exception as e:                                       # noqa: BLE001
        errors.append('volume 块异常 %s: %s' % (type(e).__name__, e))
    try:
        intraday._levels_block(frames, plist, mkt, out, errors)   # noqa: SLF001
    except Exception as e:                                       # noqa: BLE001
        errors.append('levels 块异常 %s: %s' % (type(e).__name__, e))
    # `_session_block` **故意不调**：它的每一条都建在"当天第几分钟"上，日线上没有这个维度。
    #   `out['session']` 保持上面那句说明，而不是一个 None —— None 会被读成"没算出来"。

    # ★未定稿时补一个对照读数：把今天那根（半根）剔掉之后，**上一根**的 RVOL。
    #   理由：`volume['rvol']` 会在盘中系统性偏低，而它长得就像一个结论（"缩量 0.31"）。
    #   给一个剔掉半根的读数，读者一眼能看出"缩量"是那 27 分钟造成的、还是真的
    #   （实测 BABA：当场那根 0.31，剔掉后回到 1 附近 ⇒ 缩量是假象）。
    #   只在**未定稿**时算 —— 定稿时两个数本来就该一致，多一个字段只是噪音。
    if closed is False and len(df) >= 2:
        try:
            prev, _info = _st._rvol_last(df.iloc[:-1], mkt)      # noqa: SLF001
            out['volume']['rvol_ex_last_bar'] = num(prev)
        except Exception as e:                                   # noqa: BLE001
            errors.append('剔掉未收盘那根再算 RVOL 时抛了 %s: %s' % (type(e).__name__, e))

    out['ok'] = True
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 判据 —— `python3 daily_short.py`（离线·秒级）；加 `--net` 再跑两条真取数的
#
#   rc 语义照 `datasource/_probe.py:101` 那套（别自己发明）：
#     0 = 跑了且全过   1 = 跑了且有红   3 = **一条都没跑**
#   ★`--net` 那两条**不算进 rc**（网络不通是环境、不是产品），但会**显式打一行**
#     说它们没跑 —— 静默折叠成"通过"才是这里最该防的：**"没跑"和"跑了且对"
#     在读数上一模一样**，而"真行情被判成合成"那个缺陷正好只落在 E 组。
# ═══════════════════════════════════════════════════════════════════════════
PROBE_OK_RC = 0
PROBE_XX_RC = 1
PROBE_NOTHING_RC = 3


def _main(argv):
    net = '--net' in argv
    # ★用 dict 累加**不是为了方便**：判据写在嵌套函数里时 `xx = xx + 1` 绑的是
    #   **局部**变量 —— 外层那个永远是 0，这条判据结构上无法失败，而它读起来
    #   和别处一模一样。改 dict 的**内容**、不改名字，就躲开了这个坑。
    t = {'ok': 0, 'xx': 0, 'skip': 0}

    def chk(tag, cond, detail=''):
        if cond:
            t['ok'] += 1
            print('  ✓ %-44s %s' % (tag, detail))
        else:
            t['xx'] += 1
            print('  ✗ %-44s %s' % (tag, detail))

    def framed(*dts):
        return pd.DataFrame({'dt': list(dts)})

    # `tools` 只在这几组里用到（D 守约定、E 守端到端），import 失败就整组 ⊘。
    try:
        import tools as _tools
        _tools_err = None
    except Exception as e:                                       # noqa: BLE001
        _tools, _tools_err = None, '%s: %s' % (type(e).__name__, e)

    # ── A：needs_daily —— 谁没有分钟线源 ─────────────────────────────────
    print('══ A：needs_daily 分市场 ══')
    # ★★下面两条是**判据自己的守卫**，必须先跑：A 组那两条 `for` 循环的**样本集
    #   来自常量**，常量一变空，循环就不跑 —— 循环不跑**不打红也不打⊘**，
    #   整组静默消失，而读数与"全过"逐字相同。措辞**分方向**（两句话必须是两句）：
    #   少了 = 否证能力丢了一条（有市场悄悄不再降级，或对照臂没了）；
    #   多了 = 常量过期（真有新市场没分钟线源了，去核实后改常量，别照读数改）。
    _dl = len(MARKETS_WITHOUT_MINUTE)
    chk('A0 降级清单 ≥3（%d 个）' % _dl, _dl >= 3,
        '少了 = 否证能力丢了；多了 = 常量过期，去核实再改')
    _cmp = sorted(set(_ss.MARKETS) - set(MARKETS_WITHOUT_MINUTE))
    chk('A0b 对照组非空（%d 个）' % len(_cmp), len(_cmp) >= 1,
        '空的话下面那一组一条都不跑 ⇒ 静默假绿')
    for m in MARKETS_WITHOUT_MINUTE:
        chk('needs_daily(%s)' % m, needs_daily(m) is True, '真的没有分钟线源')
    # ★对照组。没有它的话，"三个市场 True"在"所有市场都 True"时照样成立。
    for m in _cmp:
        chk('needs_daily(%s)（对照）' % m, needs_daily(m) is False, '有分钟线源 ⇒ 不许降级')
    # ★别名那一路：`app.py` / `tools.py` 传进来的可能就是粗名。
    for m in ('us', 'hk', 'crypto'):
        chk('needs_daily(%r)（别名）' % m, needs_daily(m) is True, '')
    chk('needs_daily(%r)（别名对照）' % 'cn', needs_daily('cn') is False, '')
    # ★否证支：认不出的市场**不猜**。实现若改成"认不出就降级"，这几条会红。
    #   （`norm_market` 用 `str(market or '')` ⇒ 非字符串也安全，所以 12345 也能喂。）
    for bad in ('', None, '不是市场', 12345):
        chk('needs_daily(%r) 不猜' % (bad,), needs_daily(bad) is False, '认不出 ⇒ False')

    # ── B：_bar_closed 的五种情形（`now` 可注入才测得了） ────────────────
    print()
    print('══ B：_bar_closed —— 日线口径独有的陷阱 ══')
    M = 'us_stock'                                           # open 570 / close 960
    # B1 末根是昨天 ⇒ 定稿。★这是**对照组**：证明这函数不是恒返回 False。
    _, cl, _w = _bar_closed(framed('2026-09-21'), M, pd.Timestamp('2026-09-22 10:00'))
    chk('B1 末根早于今天 ⇒ 已收盘', cl is True, 'closed=%r' % cl)
    # B2 盘中 ⇒ **没收盘**。实测 BABA 就是这一支：北京 21:57 = 纽约 09:57 =
    #    开盘后 27 分钟，那根"日线"只走了 27 分钟，RVOL 读出 0.31 的"缩量"。
    _, cl, why = _bar_closed(framed('2026-09-22'), M, pd.Timestamp('2026-09-22 09:57'))
    chk('B2 盘中 ⇒ 未收盘（BABA 实测）', cl is False, 'closed=%r' % cl)
    # B3 今天已收盘（≥16:00）⇒ 定稿。
    _, cl, _w = _bar_closed(framed('2026-09-22'), M, pd.Timestamp('2026-09-22 16:30'))
    chk('B3 今天已收盘 ⇒ 定稿', cl is True, 'closed=%r' % cl)
    # B4 还没开盘（<09:30）却有今天的 K ⇒ 那根不可能是正常收盘价。
    _, cl, _w = _bar_closed(framed('2026-09-22'), M, pd.Timestamp('2026-09-22 03:00'))
    chk('B4 未开盘却有今天的 K ⇒ 未收盘', cl is False, 'closed=%r' % cl)
    # B5 末根日期**晚于**今天 ⇒ 数据/时区不对 ⇒ **判不了**（None，不是猜 True）。
    _, cl, _w = _bar_closed(framed('2026-09-23'), M, pd.Timestamp('2026-09-22 10:00'))
    chk('B5 末根晚于今天 ⇒ 判不了(None)', cl is None, 'closed=%r（不猜）' % cl)
    # B6 加密没有收盘（open 0 / close 1440，tod 永远 <1440）⇒ 当天的 K 恒未定稿。
    _, cl, _w = _bar_closed(framed('2026-09-22'), 'crypto', pd.Timestamp('2026-09-22 12:00'))
    chk('B6 加密 24h ⇒ 恒未定稿', cl is False, 'closed=%r' % cl)
    # ★本判据**没盖住**的那一支：`_ss.cfg()` 自己抛（时区表拿不到）时返回 None。
    #   要注进去得 mock `_ss.cfg`，这里没有 ⇒ 那条分支目前只有"读代码知道"这一级证据。

    # ── C：_adjust_of 从**源名**读复权口径 ──────────────────────────────
    print()
    print('══ C：_adjust_of —— 复权口径从源名读 ══')
    chk('C1 qfqday ⇒ qfq', _adjust_of('tencent.kline.qfqday', 'cn_stock')[0] == 'qfq', 'A 股实测这一支')
    # ★C2 就是那个实测：请求里传了 qfq，腾讯对港美股**仍然回不复权**。
    chk('C2 day ⇒ none（港美股）', _adjust_of('tencent.kline.day', 'us_stock')[0] == 'none', '')
    chk('C3 hfqday ⇒ hfq', _adjust_of('tencent.kline.hfqday', 'cn_stock')[0] == 'hfq', '')
    # ★否证支：不认识的源**不猜**。实现若加一句兜底 `return 'qfq'`，这几条会红。
    for s in ('sina.kline.day', 'STUB(daily)', '', None, 'tencent.daily'):
        mode, why = _adjust_of(s, 'us_stock')
        chk('C4 %r 不猜' % (s,), mode is None and bool(why), 'mode=%r' % (mode,))

    # ── D：data_source 的约定 ⟷ tools.is_synthetic（跨模块） ──────────────
    print()
    print('══ D：data_source 约定与 tools.is_synthetic 一致 ══')
    if _tools is None:
        t['skip'] += 1
        print('  ⊘ D 组没跑：import tools 抛 %s' % _tools_err)
    else:
        # ★这一组守的是**我自己犯过的最严重的错**：把上游真源名
        #   （`tencent.kline.day`）塞进 `data_source` —— 而 `is_synthetic` 的判据是
        #   "不以 datasource 开头" ⇒ **真行情被判成合成**，摘要里多出一段
        #   "价位与成交量都是造出来的"。离线那几组一条都看不到这个。
        chk('D1 "datasource" ⇒ 判真行情', _tools.is_synthetic('datasource') is False, '')
        chk('D2 "STUB(daily)" ⇒ 判合成', _tools.is_synthetic('STUB(daily)') is True, '')
        for bad in (None, '', 'tencent.kline.day', 'SYNTH(scenario=x)'):
            chk('D3 %r ⇒ 判合成' % (bad,), _tools.is_synthetic(bad) is True, '宁可白紧张')

    # ── G：as_of 日期上界（桩帧·**离线**·秒级） ─────────────────────────
    #
    # ★为什么这组用桩而不是等 `--net`：as_of 的四件事（解析 / 反推 / 截断 /
    #   不泄漏未来）没有一件需要真行情，而桩能把"未来"**直接造出来**——
    #   真数据里"未来"是取不到的，所以真取数那条路**测不了"泄漏"这件事本身**。
    # ★桩能接管取数，靠的是 `analyze` 里写的是 `from datasource import daily`
    #   而它在**函数体内**：每次调用都重新从模块属性取。哪天有人把它挪到模块顶层，
    #   `_ds.daily = ...` 就够不着了 —— 那时 G 组会整体变红（G0 先红），不会静默。
    print()
    print('══ G：as_of 上界截断（桩帧·离线） ══')
    # 桩：600 个**工作日**，**到今天为止**（用 `bdate_range` 不用 `date_range` ——
    #   研判的窗口是交易日，桩里塞进周末会让"够不够 80 根"变成自然日口径，
    #   与真源不同形）。★长度要到 600：它必须**大于** `analyze` 反推出来的 days
    #   （≈ DAILY_DAYS + 距今交易日数），否则取数窗口永远盖得住 as_of，
    #   "天数反推漏了"这件事在桩上就显不出来（G6c 会变成恒真判据）。
    _dts = pd.bdate_range(end=dt.date.today(), periods=600)
    _calls = []
    _provider = {}

    def _synth(flat_after=None):
        """桩帧；`flat_after` 那天**之后**的价格压成常数 —— 用来查未来有没有漏进来。

        ★为什么是"压成常数"而不是"整段 ×10"：试过了，**×10 那种等比缩放
          对读数不敏感** —— `signals` 的强度分是归一化到 [0,1] 的，`squeeze`
          看的是带宽**分位**、`momentum` 看的是**相对**累涨跌，整段同比放大后
          这些逐字节相同。用它当扰动的话，G9 会是一条**恒真判据**（下面 G9b
          就是专门抓这个的对照臂，它第一次跑就红了，我才换掉）。
          压成常数则把波动率与累涨跌同时打到 0 ⇒ regime/squeeze/momentum 都会动。
        """
        px = pd.Series([100.0 + 0.5 * i for i in range(len(_dts))])
        if flat_after is not None:
            m = _dts <= pd.Timestamp(flat_after)
            px = px.where(m, float(px[m].iloc[-1]))
        f = pd.DataFrame({'dt': _dts, 'open': px, 'high': px * 1.01, 'low': px * 0.99,
                          'close': px, 'volume': pd.Series([1e6] * len(_dts))})
        f.attrs['source'] = 'STUB(daily)'
        return f

    _provider['fn'] = lambda: _synth()

    def _fake_daily(mkt, code, days):
        """桩取数层：只回**最后 days 根**。

        ★按 days 截尾不是装饰 —— 真源就是这个语义（"到今天为止的最近 N 根"）。
          不截尾的话 `days` 怎么变都影响不到返回的帧，"天数反推漏了"在桩上
          就永远显不出来，G6c 会是一条恒真判据。
        """
        _calls.append(days)
        f = _provider['fn']()
        g = f.iloc[-max(1, min(int(days), len(f))):].reset_index(drop=True)
        g.attrs['source'] = f.attrs.get('source')      # 切片不保留 attrs
        return g

    import datasource as _ds
    _orig_daily = _ds.daily
    _ds.daily = _fake_daily

    _p0 = analyze('us_stock', 'BABA')
    # ★G0/G0b 是**这组自己的守卫**：桩没接管、或桩帧过不了 `_norm_frame`，
    #   下面每条都在测空气，而读数与"全过"逐字相同。
    chk('G0 桩真的接管了取数（守卫）', len(_calls) == 1, 'days=%r' % (_calls,))
    # ★G0b 同时证明**桩按 days 截尾**生效了：不给 as_of 时 days=DAILY_DAYS，
    #   所以拿到的就该是 400 根（桩本身有 600 根）。桩帧过不了 `_norm_frame`、
    #   或者截尾没生效，下面每条都在测空气，而读数与"全过"逐字相同。
    chk('G0b 桩帧成形且按 days 截尾（%d 根）' % DAILY_DAYS,
        (_p0.get('bars') or {}).get(str(DAILY_PERIOD)) == DAILY_DAYS,
        'bars=%r ok=%r' % ((_p0.get('bars') or {}).get(str(DAILY_PERIOD)), _p0.get('ok')))

    # G1/G2 `_as_date`：收什么、不吃什么。
    _ok3 = [_as_date('2025-01-15'), _as_date(dt.date(2025, 1, 15)),
            _as_date(dt.datetime(2025, 1, 15, 9, 30))]
    chk('G1 _as_date 收 str/date/datetime',
        all(x == dt.date(2025, 1, 15) for x in _ok3),
        # ★detail 用 `repr` 不用 `x.isoformat()`：detail 在**判据红了的时候**
        #   也要构得出来。注入（让 `_as_date` 返 None）第一次跑时就是在这里
        #   抛了 AttributeError ⇒ 整组判据没跑完，而工具报的是"注入没打中"。
        str([repr(x) for x in _ok3]))
    # ★否证支：不吃别的格式。`01/15/2025` 那一支最要紧 —— 放行它意味着
    #   `pd.Timestamp` 那类解析器会把 3 月 4 日和 4 月 3 日互换，产出**日期错了
    #   但看起来正常**的研判。`2025-1-5` **故意不算拒**（`%m` 收 1 位数字，
    #   而它没有第二种解释）—— 把无歧义的写法也拒掉只是难用，不是纪律。
    _rej = []
    for _badfmt in ('01/15/2025', '2025/01/15', '', None, 12345, '昨天'):
        try:
            _as_date(_badfmt)
            _rej.append(repr(_badfmt))
        except ValueError:
            pass
    chk('G2 _as_date 不吃别的格式', not _rej, '被放行：%s' % ('、'.join(_rej) or '无'))

    # G3/G4 两个"在取数之前就该拦住"的分支 —— 拦在取数之前本身就是一条判据：
    #   放行的话会白跑一次网络，再拿它的结果去算一份日期不对的研判。
    _n_call = len(_calls)
    _fut = analyze('us_stock', 'BABA',
                   as_of=(dt.date.today() + dt.timedelta(days=3)).isoformat())
    _fe = ' '.join(str(x) for x in (_fut.get('errors') or []))
    chk('G3 未来日期 ⇒ ok=False + 明说', _fut.get('ok') is False and '还没到' in _fe,
        (_fut.get('errors') or ['（errors 是空的！）'])[0][:50])
    chk('G3b 未来日期**没去取数**', len(_calls) == _n_call, '取数被叫了 %d 次' % (len(_calls) - _n_call))
    _badd = analyze('us_stock', 'BABA', as_of='昨天')
    chk('G4 坏日期 ⇒ ok=False + "看不懂"',
        _badd.get('ok') is False
        and '看不懂' in ' '.join(str(x) for x in (_badd.get('errors') or [])),
        (_badd.get('errors') or ['（errors 是空的！）'])[0][:50])
    chk('G4b 坏日期**没去取数**', len(_calls) == _n_call, '')

    # G5 截断真的落在 as_of 那天上。
    _cut = _dts[199]
    _cut_s = _cut.date().isoformat()
    _a200 = analyze('us_stock', 'BABA', as_of=_cut_s)
    _lb = _a200.get('last_bar') or {}
    chk('G5 截断生效：bars == 200（不是 600）',
        (_a200.get('bars') or {}).get(str(DAILY_PERIOD)) == 200,
        'bars=%r 期望 200' % ((_a200.get('bars') or {}).get(str(DAILY_PERIOD)),))
    chk('G5b last_bar 就是 as_of 那天', _lb.get('date') == _cut.strftime('%Y-%m-%d'),
        '%r' % (_lb.get('date'),))
    # ★这条钉的是模块头里那句"顺带的好处"：as_of 是历史日 ⇒ 那根 K 必然已定稿
    #   ⇒ `rvol_ex_last_bar` 那条"盘中缩量假象"的警告不该出现。哪天截断挪到
    #   `_bar_closed` 之后就反过来了（拿"今天"去量一根历史的 K）。
    chk('G5c as_of 是历史日 ⇒ 判定已收盘', _lb.get('closed') is True,
        'closed=%r why=%s' % (_lb.get('closed'), str(_lb.get('why'))[:40]))
    chk('G5d 历史口径没有"未收盘"警告',
        not any('未收盘' in str(x) for x in (_a200.get('errors') or [])), '')

    # G6 天数**反推**。★这是最容易静默做错的一步：照搬 DAILY_DAYS 的话
    #   `analyze` 仍会 ok=True，只是截断后一根不剩 —— 读数像"这只票没信号"。
    _calls.clear()
    analyze('us_stock', 'BABA')
    _d_none = _calls[-1]
    analyze('us_stock', 'BABA', as_of=_cut_s)
    _d_200 = _calls[-1]
    analyze('us_stock', 'BABA', as_of=_dts[10].date().isoformat())
    _d_10 = _calls[-1]
    chk('G6 as_of=None ⇒ days 就是 DAILY_DAYS（逐字节原路）',
        _d_none == DAILY_DAYS, 'days=%d' % _d_none)
    chk('G6b as_of 越早 ⇒ 要的根数越多', _d_10 > _d_200 > DAILY_DAYS,
        '%d > %d > %d' % (_d_10, _d_200, DAILY_DAYS))
    # ★G6c 反推**真的换取到了窗口** —— 这是反推唯一的目的，也是它最容易
    #   **静默**失效的地方：漏掉 `+ back` 时 `days` 还是 400，取数层照样返回
    #   400 根，`analyze` 照样 `ok=True`、信号照样出得来，只是 as_of 那天的
    #   材料**短了一截**（下面这条量的就是那一截）。没有任何别的地方看得见。
    _far = _dts[499]
    _afar = analyze('us_stock', 'BABA', as_of=_far.date().isoformat())
    _nb = (_afar.get('bars') or {}).get(str(DAILY_PERIOD)) or 0
    chk('G6c 反推够用：as_of 之前留着 ≥%d 根' % DAILY_DAYS, _nb >= DAILY_DAYS,
        '实到 %d 根（as_of=%s）—— 反推漏了的话这里会短一截'
        % (_nb, _far.strftime('%Y-%m-%d')))

    # G7/G8 根数不够时**明确报错**，且两种原因的措辞必须分开 —— 它们要用户做的
    #   事不同（一个"换个晚点的日子"，一个"本站只能回看这么久"）。
    _a30 = analyze('us_stock', 'BABA', as_of=_dts[29].date().isoformat())
    _e30 = ' '.join(str(x) for x in (_a30.get('errors') or []))
    chk('G7 截断后不足 %d 根 ⇒ ok=False 且不给信号' % AS_OF_MIN_BARS,
        _a30.get('ok') is False and not (_a30.get('signals') or []),
        'ok=%r signals=%d' % (_a30.get('ok'), len(_a30.get('signals') or [])))
    chk('G7b 措辞说的是"太远"（不是"太早"）', '太远' in _e30 and '太早' not in _e30,
        (_a30.get('errors') or [''])[0][:50])
    _aold = analyze('us_stock', 'BABA', as_of='2023-06-01')
    _eold = ' '.join(str(x) for x in (_aold.get('errors') or []))
    chk('G8 as_of 早于最早那根 ⇒ "太早"', _aold.get('ok') is False and '太早' in _eold,
        (_aold.get('errors') or [''])[0][:50])

    # ★★G9 这组最值钱的一条：**未来有没有漏进来**。
    #   造一帧"as_of 之后横盘成常数"的行情，同一 as_of 的研判必须**逐字节不变**。
    #   截断一旦失效（或被挪到算完之后），未来价格会进 regime/signals ——
    #   而它在**正常数据**上产出的读数看起来完全正常，没有别的地方能发现。
    _provider['fn'] = lambda: _synth()
    _clean = analyze('us_stock', 'BABA', as_of=_cut_s)
    _provider['fn'] = lambda: _synth(flat_after=_cut_s)
    _dirty = analyze('us_stock', 'BABA', as_of=_cut_s)
    _same = (_clean.get('signals') == _dirty.get('signals')
             and _clean.get('regime') == _dirty.get('regime')
             and _clean.get('composite') == _dirty.get('composite'))
    chk('G9 未来压成常数 ⇒ 研判逐字节不变', _same,
        '相同' if _same else '不同 ⇒ 未来漏进来了')
    # ★★G9b 是 G9 的**对照臂**：不给 as_of 时，同一扰动**必须**让读数变。
    #   没有它的话，G9 可能是"这函数对任何输入都返回同样的东西"（恒真 = 装饰）。
    #   ★这条不是假想的谨慎：它第一次跑就红了（当时扰动是等比 ×10，那种扰动
    #   本身对归一化读数无效）。第二次它又红了，那次是**判据自己的 bug** ——
    #   下面这行 `_provider` 重置漏了，"干净"那次拿到的还是扰动帧，两次同源。
    #   两次红都值得留在这条注释里：这条对照臂抓到的第一个问题就在它自己身上。
    _provider['fn'] = lambda: _synth()
    _n_clean = analyze('us_stock', 'BABA')
    _provider['fn'] = lambda: _synth(flat_after=_cut_s)
    _n_dirty = analyze('us_stock', 'BABA')
    _n_diff = (_n_clean.get('signals') != _n_dirty.get('signals')
               or _n_clean.get('regime') != _n_dirty.get('regime'))
    chk('G9b 对照：不给 as_of 时同一扰动会改变读数', _n_diff,
        '变了（对照臂有效）' if _n_diff else '没变 ⇒ G9 是恒真判据（装饰），什么都没证明')
    _provider['fn'] = lambda: _synth()

    # G10 ★`analyze_auto` 那条防线的落点：**A 股要了 as_of 也不许静默忽略**。
    #   分钟那条路（`intraday.analyze`）没有日期上界，静默忽略 = 把"截至今天"的
    #   材料当成"截至 as_of"的交给调用方 ⇒ 一份**含未来、却自称历史**的读数。
    _auto = analyze_auto('cn_stock', '000002', periods=(5, 15, 30), as_of=_cut_s)
    _ae = ' '.join(str(x) for x in (_auto.get('errors') or []))
    chk('G10 A 股 + as_of ⇒ 改走日线口径（不静默忽略）',
        _auto.get('bar_scale') == 'daily' and _auto.get('periods') == [DAILY_PERIOD],
        'bar_scale=%r periods=%r' % (_auto.get('bar_scale'), _auto.get('periods')))
    chk('G10b 并在 errors 里说清换了口径', '没有日期上界' in _ae,
        (_auto.get('errors') or ['（errors 是空的！）'])[0][:50])
    _f4 = analyze_auto('us_stock', 'BABA', periods=(5, 15, 30))
    chk('G11 as_of=None 时 errors[0] 仍是周期说明（没被挤掉）',
        '你要的周期是' in str((_f4.get('errors') or [''])[0]),
        str((_f4.get('errors') or [''])[0])[:50])

    _ds.daily = _orig_daily
    # ★桩必须卸干净：E/F 组就在下面、要真取数。没卸的话它们会在**桩上**跑出
    #   一片绿 —— 那正是"注入够不着被测代码"的镜像。
    chk('G12 桩已卸下（E/F 组不许被它接管）', _ds.daily is _orig_daily, '')

    # ── E/F：要真取数（--net） ───────────────────────────────────────────
    print()
    if not net:
        t['skip'] += 1
        print('  ⊘ E/F 组没跑（要 --net）：形状对齐 + data_source 端到端 + 分流')
        print('     ★这两条不算进 rc，但**必须打出来** —— 折叠成"通过"的话，')
        print('       "真行情被判成合成"在离线判据里就是隐形的。')
    else:
        print('══ E：analyze() 的形状与 data_source（真取数） ══')
        try:
            a = analyze('us_stock', 'BABA')
            # ★形状的**权威是上游**：`intraday.analyze` 给哪些键，日线就得给哪些键。
            #   期望值不是我列的清单，是另一次真调用的键集合 —— 列清单的话，
            #   上游加了键而我不知道，这条判据会绿着放行。
            b = intraday.analyze('cn_stock', '000002')
            miss = sorted(set(b) - set(a))
            chk('E1 日线的键 ⊇ 分钟级的键', not miss, '缺 %s' % (miss or '无'))
            chk('E2 data_source 是 "datasource"', a.get('data_source') == 'datasource',
                'data_source=%r ← 不是上游真源名！' % (a.get('data_source'),))
            chk('E3 真行情不许被判成合成',
                _tools is not None and _tools.is_synthetic(a.get('data_source')) is False, '')
            chk('E4 bar_scale=daily 且 periods=[1440]',
                a.get('bar_scale') == 'daily' and a.get('periods') == [DAILY_PERIOD],
                'bar_scale=%r periods=%r' % (a.get('bar_scale'), a.get('periods')))
            chk('E5 upstream_source 是真源名',
                str(a.get('upstream_source') or '').startswith('tencent.'),
                '%r' % (a.get('upstream_source'),))

            print()
            print('══ F：analyze_auto 的分流（三个入口共用的那一个） ══')
            f1 = analyze_auto('us_stock', 'BABA')
            chk('F1 美股 ⇒ 日线', f1.get('bar_scale') == 'daily', '')
            # ★对照组：A 股必须逐字节还是原路（`bar_scale` 缺键、多档周期）。
            f2 = analyze_auto('cn_stock', '000002')
            chk('F2 A 股 ⇒ 原路（对照）',
                f2.get('bar_scale') is None and len(f2.get('periods') or []) > 1,
                'bar_scale=%r periods=%r' % (f2.get('bar_scale'), f2.get('periods')))
            # ★周期被忽略时**必须说出来**：调用方多半会拿 `periods` 去筛 `signals`，
            #   而日线的 `period` 是 1440 ⇒ 一条都筛不出来，看起来像"策略全挂了"。
            f3 = analyze_auto('us_stock', 'BABA', periods=(5, 15, 30))
            errs = ' '.join(str(x) for x in (f3.get('errors') or []))
            # ★锚只用**不含 `**` 的片段**：产出文本里 `**日线**` 那种强调记号会把
            #   认红的锚从中间劈开（红字是人读的、可带记号；认红短语必须只含内容）。
            chk('F3 周期被忽略时有说明', '你要的周期是' in errs and '已按' in errs,
                (f3.get('errors') or ['（errors 是空的！）'])[0][:56])
        except Exception as e:                                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            chk('E/F 组（真取数）跑完没抛', False, '%s: %s' % (type(e).__name__, e))

    # ── 收尾 ────────────────────────────────────────────────────────────
    print()
    print('═' * 64)
    n = t['ok'] + t['xx']
    print('小计：通过 %d，失败 %d，没跑 %d 组' % (t['ok'], t['xx'], t['skip']))
    if n == 0:
        print('⚠ 一条判据都没跑 ⇒ rc=%d（**不是**通过）' % PROBE_NOTHING_RC)
        return PROBE_NOTHING_RC
    if t['xx']:
        print('❌ 有红')
        return PROBE_XX_RC
    print('✓ 全过')
    return PROBE_OK_RC


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
