"""日内（5/15/30 分钟）策略引擎。对外**只有一个**函数：`analyze()`。

    from intraday import analyze
    r = analyze('cn_stock', '600519', periods=(5, 15, 30))

★返回结构**逐字**对齐 `docs/AGENT-SPEC.md` §3.2（字段名一个都没改，别的模块和前端
  按那份规格消费它）：`ok / market / code / name / ts / bars / regime / signals /
  composite / volume / levels / session / errors`。
  额外多三个键，都是**诚实性**需要的，已写进交付说明的集成点那节：
    · `data_source` —— `'datasource'` 还是 `'SYNTH(...)'`。★**前端必须显示它**：
      否则合成帧会被当行情读（`_synthdata.py` 的模块头写了为什么它必须显式开关）。
    · `periods` —— 实际分析的周期列表（回显，免得调用方自己记）。
    · `ts` 之外还有一个 `bars_per_day`：一天该有几根 K（判断"帧是不是被截断"用）。

★★数据来源的纪律（规格 §0 铁律 + 用户原话"不许编数字"）：
  真源 = `agent/datasource/` 的 `minute(market, code, period=, days=)`（规格 §3.1，接口冻结）。
  取不到时 `analyze()` 返回 `ok=False` 并把人话原因放进 `errors` —— **不用合成数据顶上**。
  只有显式设 `LICAI_INTRADAY_SYNTH=1` 才走 `_synthdata`（离线跑判据用）。
  「取不到就换成假的」是一条静默降级，而静默降级的读数与真读数在返回值上**长得一模一样**。

★每条策略**只要算得出来就一定产出一条**（哪怕 `side='flat'`，它的 why 里是算术理由）；
  只有"数据不够/算不出来"才整条消失并在 `errors` 里说明缺什么（`algo.StrategySkipped`）。
  所以 `signals` 的条数是**数据完整度**的读数，`errors` 是**缺什么**的读数。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_HERE)
# ★本仓库的导入约定是"平铺"（`sys.path` 里放 `agent/`，然后 `import feed`），
#   而且 `agent/__init__.py` **不存在** ⇒ `intraday` 是以顶层包的身份被导入的。
#   这里自己补一次路径：`feed` / `indicators` / `datasource` 都在 `agent/` 下。
#   重复插入是无害的（先查再插），所以被谁导入都成立。
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import math  # noqa: E402

import pandas as pd  # noqa: E402

import indicators  # noqa: E402
from . import levels, regime, sessions  # noqa: E402
from . import strategies as st  # noqa: E402
from . import volume_profile as vp  # noqa: E402
from . import _synthdata as sd  # noqa: E402
from .algo import StrategySkipped, clip01, finite, num, safe_div  # noqa: E402

__all__ = ['analyze', 'SIGNAL_W', 'SYNTH_ENV']

SYNTH_ENV = sd.SYNTH_ENV                 # 'LICAI_INTRADAY_SYNTH'（显式开关，见模块头）
SYNTH_SCENARIO_ENV = 'LICAI_INTRADAY_SCENARIO'   # 只在合成模式下有意义
DEFAULT_PERIODS = (5, 15, 30)

# ── 列名对齐（规格 §3.1 的 CANON 列）───────────────────────────────────
# ★按**名字**对齐，不按位置 —— 上游换个列序（adata / efinance / akshare 三家的列序
#   都不一样）就全线错位，而错位的症状是"数值都不对但形状全对"，很难看出来。
CANON_REQUIRED = ('dt', 'open', 'high', 'low', 'close', 'volume')
COL_ALIASES = {
    'datetime': 'dt', 'date': 'dt', 'time': 'dt', 'trade_time': 'dt', 'trade_date': 'dt',
    'ts': 'dt', 'timestamp': 'dt', 'day': 'dt',
    'vol': 'volume', 'trade_volume': 'volume', '成交量': 'volume', '成交额': 'amount',
    'turnover': 'amount', 'amt': 'amount', 'trade_amount': 'amount', '成交金额': 'amount',
    'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume',
}


class _NoData(Exception):
    """拿不到（或拿回来的东西不成帧）。**这是"没有数据"，不是"没有信号"。**"""


# ── 加权合成 ───────────────────────────────────────────────────────────
# ★规格 §3.2 纪律第 3 条：每个权重必须是**模块级常量**并把理由写在旁边。
#   下面这些数是**设计取舍**，不是回测拟合的结果 —— 我没有回测数据，所以不许把它们
#   当"优化出来的系数"读（交付说明的"没验的"那节写了它们没被验证过）。
#   排序逻辑（可被反驳，但必须有一条）：
#     · orb 0.18 —— 日内最经典、样本最多的方向来源（区间是客观的、突破是客观的），
#       但假突破率高，所以不给更高；orb_fail 是它的镜像分支，只在"突破后回到区间"
#       这个更少见的形态上出现 ⇒ 权重减半（0.09）；
#     · momentum 0.15 —— 方向信息来源第二强，但它与 resonance 高度相关（共振就是
#       靠它算出来的）⇒ 两者加起来 0.27 而不是各给 0.18；
#     · resonance 0.12 —— 多周期一致比单周期更可信，但它也是"momentum 的三个读数"，
#       信息量与 momentum 有重叠 ⇒ 不再给更高；
#     · vwap_revert / divergence 各 0.11 —— 都是"偏离/背离"型的**反向**判据，
#       与趋势类信号天然对冲，给中等权重让它们能抵消一部分趋势票；
#     · volume_profile 0.08 —— 分布是"当天"的静态结构，不含量能时序信息；
#     · rvol 0.06 / session 0.05 —— 这两条单独**不给方向**的能力最强（RVOL 只量量、
#       时段效应是描述统计），它们主要当"确认"，所以权重最低；
#     · squeeze 0.05 —— 挤压本身不给方向（只给"等突破"），扩张态才给；且它问的是
#       "带宽"这一件事，与其他票相关性低，给最低档；
#     · **'atr' 故意不在表里**：它只出价位、side 恒为 flat（`levels.py` 模块头解释了
#       为什么盈亏比不能拿来定方向），把它算进方向票数是倒因为果。
#   表内 10 项之和 = 1.00（乘/除同一张表，和是不是 1 不影响分数，但便于手算核对）。
SIGNAL_W = {
    'orb_break': 0.18, 'orb_fail': 0.09, 'vwap_revert': 0.11, 'rvol': 0.06,
    'volume_profile': 0.08, 'divergence': 0.11, 'momentum': 0.15, 'squeeze': 0.05,
    'resonance': 0.12, 'session': 0.05,
}
COMPOSITE_DEAD_ZONE = 0.15   # |score| < 0.15 判 flat。★死区是**必须**的：加权和天然会在
                             # 0 附近抖，没有死区时"多空几乎均衡"会被报成"看多 0.03"。
# 置信度的三项权重（和 = 1）。★为什么置信度不是 |score| 本身：
# |score| 只反映"票投得多一致"，不反映"有多少票没投出来"。三条腿缺一条的 0.8 分
# 与三条腿齐全的 0.8 分不是同一件事。
CONF_W_SCORE = 0.55          # |score|：净票有多大
CONF_W_AGREE = 0.30          # 多数侧票数 / 有方向的票数（票有多齐）
CONF_W_COVER = 0.15          # 真正跑出来的策略条数 / 预期条数（数据有多全）


def analyze(market, code, periods=DEFAULT_PERIODS, name=None):
    """→ 规格 §3.2 的那个 dict。**任何情况下都不抛异常**（取不到就 `ok=False`）。

    `periods` 是分钟周期（默认 5/15/30）。每个周期单独取数、单独算十条策略，
    再合成一个方向（`composite`）。
    """
    out = {
        'ok': False, 'market': market, 'code': code, 'name': name, 'ts': None,
        'periods': [], 'bars': {}, 'bars_per_day': {}, 'regime': {}, 'signals': [],
        'composite': {'score': 0.0, 'side': 'flat', 'confidence': 0.0,
                      'agree': {'long': 0, 'short': 0, 'flat': 0}},
        'volume': {'rvol': None, 'rvol_by_period': {}, 'rvol_naive_by_period': {},
                   'profile': None, 'divergence': None},
        'levels': None,
        'session': {'phase': None, 'window': None, 'note': None},
        'errors': [], 'data_source': None,
    }
    errors = out['errors']
    try:
        mkt = sessions.norm_market(market)
    except ValueError as e:
        errors.append(str(e))
        return out
    out['market'] = mkt
    try:
        plist = _norm_periods(periods)
    except ValueError as e:
        errors.append(str(e))
        return out
    out['periods'] = list(plist)

    frames, sources = {}, set()
    for p in plist:
        days = _days_for(mkt, p)
        try:
            df, src = _fetch(mkt, code, p, days)
        except _NoData as e:
            errors.append('%d分钟：取数失败 —— %s' % (p, e))
            continue
        except Exception as e:                       # noqa: BLE001 —— 取数层的任何异常都不该炸整个接口
            errors.append('%d分钟：取数时抛了异常 %s: %s' % (p, type(e).__name__, e))
            continue
        if not len(df):
            errors.append('%d分钟：取回来的帧是空的（0 行）' % p)
            continue
        frames[p] = df
        sources.add(src)
        out['bars'][str(p)] = int(len(df))
        out['bars_per_day'][str(p)] = int(sessions.bars_per_day(mkt, p))
    if not frames:
        errors.append('**没有任何一个周期取到数据** ⇒ 不给结论（不用合成数据顶上；'
                      '要离线跑请显式设 %s=1）' % SYNTH_ENV)
        return out
    out['data_source'] = '/'.join(sorted(sources))
    try:
        _run(mkt, frames, out, errors)
    except Exception as e:                           # noqa: BLE001
        errors.append('策略层异常 %s: %s（已返回已算出的部分，ok=False）'
                      % (type(e).__name__, e))
        return out
    out['ok'] = True
    return out


# ── 取数 ───────────────────────────────────────────────────────────────
def _norm_periods(periods):
    if periods is None:
        return DEFAULT_PERIODS
    out = []
    for p in periods:
        try:
            v = int(p)
        except (TypeError, ValueError):
            raise ValueError('周期必须是整数分钟，收到 %r' % (p,))
        if v <= 0 or v > 1440:
            raise ValueError('周期必须在 1~1440 分钟之间，收到 %d' % v)
        if v not in out:
            out.append(v)
    if not out:
        raise ValueError('periods 是空的')
    return tuple(sorted(out))


def _days_for(market, period):
    """每个周期取几天历史。

    ★**不是一个固定值**：决定"够不够"的是**根数**，不是天数。
      · 带宽分位要 `BOLL20 + 60` 根，再加 10 根余量 ⇒ 需要约 90 根；
      · 同一时刻的 RVOL 基线要 ≥ 3 个交易日（当天 + 2 天）；
      30 分钟周期一天只有 8 根 ⇒ 5 天只有 40 根，连布林带 + 分位窗口都凑不齐。
      所以按 `bars_per_day` 反算天数（下限 5 天是为了 RVOL 的"同刻多天样本"）。
      写死 5 的话，30 分钟周期上 `squeeze` 这条策略会**永远静默消失**。
    """
    need = indicators.BOLL_N + regime.SQZ_LOOKBACK + 10
    bpd = max(1, sessions.bars_per_day(market, period))
    return max(5, int(math.ceil(need / float(bpd))))


def _fetch(market, code, period, days):
    """→ `(df, 来源标签)`；取不到抛 `_NoData`。**没有静默降级。**"""
    if os.environ.get(SYNTH_ENV, '').strip():
        sc = _synth_scenario()
        df = sd.minute(market, code, period=period, days=days, scenario=sc)
        return _norm_frame(df, '%d分钟' % period), 'SYNTH(scenario=%s)' % (sc or 'random')
    try:
        from datasource import minute as ds_minute
    except Exception as e:                           # noqa: BLE001
        raise _NoData('这一层没有数据源：`from datasource import minute` 失败（%s: %s）。'
                      '★这不是"这只股票没有行情"，是"取数的包不在" —— 按纪律返回 ok=False，'
                      '不用合成帧顶上（要离线跑，显式设 %s=1）'
                      % (type(e).__name__, e, SYNTH_ENV))
    try:
        df = ds_minute(market, code, period=period, days=days)
    except Exception as e:                           # noqa: BLE001
        raise _NoData('datasource.minute(%r, %r, period=%d, days=%d) 抛出 %s: %s'
                      % (market, code, period, days, type(e).__name__, e))
    if df is None:
        raise _NoData('datasource.minute(%r, %r, period=%d, days=%d) 返回 None'
                      % (market, code, period, days))
    return _norm_frame(df, '%d分钟' % period), 'datasource'


def _synth_scenario():
    v = os.environ.get(SYNTH_SCENARIO_ENV, '').strip().lower()
    if not v or v == 'default':
        return None
    if v not in sd.SCENARIOS:
        return None                                  # 认不出就当通用随机（不猜一个场景）
    return v


def _norm_frame(df, tag):
    """规格 §3.1 的 CANON 列对齐 + 清洗。**坏行剔掉要留痕**（不静默缩样本）。"""
    if not isinstance(df, pd.DataFrame):
        raise _NoData('%s：返回的不是 DataFrame，是 %s' % (tag, type(df).__name__))
    d = df.copy()
    low = [str(c).strip().lower() for c in d.columns]
    if 'dt' not in low and (isinstance(d.index, pd.DatetimeIndex)
                            or getattr(d.index, 'name', None) is not None):
        d = d.reset_index()                          # dt 在索引里（有些源就这么给）
    cols = {}
    for c in d.columns:
        name = COL_ALIASES.get(str(c).strip().lower(), str(c).strip().lower())
        if name in CANON_REQUIRED + ('amount',) and name not in cols:
            col = d[c]
            if isinstance(col, pd.DataFrame):        # 重名列（vol + volume 同时出现）
                col = col.iloc[:, 0]
            cols[name] = col
    missing = [c for c in CANON_REQUIRED if c not in cols]
    if missing:
        raise _NoData('%s：帧里缺必需列 %s（实到列 %s）'
                      % (tag, '/'.join(missing), '/'.join(str(c) for c in d.columns)))
    f = pd.DataFrame(cols)
    f['dt'] = pd.to_datetime(f['dt'], errors='coerce')
    n0 = len(f)
    f = f.dropna(subset=['dt'])
    for c in ('open', 'high', 'low', 'close', 'volume'):
        f[c] = pd.to_numeric(f[c], errors='coerce')
    if 'amount' in f.columns:
        f['amount'] = pd.to_numeric(f['amount'], errors='coerce')
    # 没有收盘价的行不是 K 线（半截行、坏行）⇒ 剔掉，但留下条数痕迹
    n1 = len(f)
    f = f.dropna(subset=['close'])
    dropped = (n0 - n1) + (n1 - len(f))
    f = f.sort_values('dt').drop_duplicates(subset=['dt'], keep='last').reset_index(drop=True)
    if dropped:
        f.attrs['dropped'] = int(dropped)
    if f['dt'].duplicated().any():
        raise _NoData('%s：dt 有重复且去重失败' % tag)
    return f


# ── 策略层 ─────────────────────────────────────────────────────────────
# 每个周期要跑的策略（函数签名统一 `(df, period, market)` → list[signal]）。
# `resonance` 不在里面：它要跨周期，单独跑一次（见 `_resonance`）。
PER_PERIOD = (
    ('orb', st.orb), ('vwap_revert', st.vwap_dev), ('rvol', st.rvol_sig),
    ('volume_profile', st.volume_profile_sig), ('divergence', st.divergence_sig),
    ('momentum', st.momentum), ('squeeze', st.squeeze), ('atr', st.atr_sig),
    ('session', st.session_sig),
)


def _run(market, frames, out, errors):
    plist = sorted(frames)
    signals = []
    expected = len(PER_PERIOD) * len(plist) + 1        # +1 = resonance（跨周期那一条）
    for p in plist:
        df = frames[p]
        n_drop = int(df.attrs.get('dropped') or 0)
        if n_drop:
            errors.append('%d分钟：数据源返回的帧里有 %d 行缺收盘价/时间（已剔除，'
                          '不计入任何统计）' % (p, n_drop))
        # regime：算不出**不往 regime 里塞值**（塞个默认的 'range' 就是编）
        try:
            rg, caveat = regime.classify(df)
            out['regime'][str(p)] = rg
            if caveat:
                errors.append('%d分钟 regime：%s' % (p, caveat))
        except StrategySkipped as e:
            errors.append('%d分钟 regime：%s' % (p, e.reason))
        for sid, fn in PER_PERIOD:
            try:
                got = fn(df, p, market)
            except StrategySkipped as e:
                errors.append('%d分钟 %s：%s' % (p, sid, e.reason))
                continue
            except Exception as e:                   # noqa: BLE001 —— 一条策略炸掉不该带走整帧
                errors.append('%d分钟 %s：算的时候抛了 %s: %s'
                              % (p, sid, type(e).__name__, e))
                continue
            # ★"只产出一条"是这两层之间的契约（见 strategies.py 模块头）：
            #   多条的话 composite 会把同一条策略重复计票（票数虚高），所以这里是硬校验。
            if len(got) != 1:
                errors.append('%d分钟 %s：应恰好产出 1 条信号，实际 %d 条（已按第 1 条处理）'
                              % (p, sid, len(got)))
            if got:
                signals.append(got[0])
    try:
        signals.extend(_resonance(plist, signals, errors))
    except StrategySkipped as e:
        errors.append('多周期共振：%s' % e.reason)
    out['signals'] = signals
    out['composite'] = _composite(signals, len(plist), expected)
    _volume_block(frames, plist, market, out, errors)
    _levels_block(frames, plist, market, out, errors)
    _session_block(frames, plist, market, out, errors)


def _resonance(plist, signals, errors):
    """多周期共振：拿各周期的 momentum 方向分再合成一次。

    ★方向分从 momentum 信号的 `refs['score']` 取（那是它三个分量加权后的**有符号**
      合成分，`strength` 已经取了绝对值，不能拿来当方向）。
    ★某个周期的 momentum 被跳过时那个周期缺席，不是补 0 —— 补 0 等于"那个周期
      给了中性票"，而事实是"那个周期没有读数"（`regime.resonance` 会算加权平均
      并报出实际在场几个周期）。
    """
    dirs = {}
    for s in signals:
        if s['id'] != 'momentum':
            continue
        v = finite((s.get('refs') or {}).get('score'))
        if v is not None and s.get('period') is not None:
            dirs[int(s['period'])] = v
    return st.resonance(dirs)


def _composite(signals, n_periods, n_expected):
    """加权合成 → score / side / confidence / agree。

    ★分数 = Σ(权重 × 方向 × 强度) / Σ(权重)，求和只覆盖**真正投了票的**那些信号；
      `side='flat'` 的不投票（它们没有方向），`SIGNAL_W` 里没有的 id 也不投票。
    ★分母只算在场的权重（不是全表 1.0）：数据缺一块时用全表当分母会把分数
      **系统性拉向 0**，读起来像"多空平衡"，而真相是"少了几条策略"——
      那件事由 `confidence` 的 `cover` 那一项和 `errors` 负责报。
    """
    agree = {'long': 0, 'short': 0, 'flat': 0}
    numer = denom = 0.0
    for s in signals:
        side = s.get('side')
        agree[side] = agree.get(side, 0) + 1
        if side == 'flat':
            continue
        wi = SIGNAL_W.get(s.get('id'))
        stg = finite(s.get('strength'))
        if not wi or stg is None:
            continue
        numer += wi * (1.0 if side == 'long' else -1.0) * stg
        denom += wi
    score = (numer / denom) if denom else 0.0
    if score > COMPOSITE_DEAD_ZONE:
        side = 'long'
    elif score < -COMPOSITE_DEAD_ZONE:
        side = 'short'
    else:
        side = 'flat'
    directional = agree['long'] + agree['short']
    agree_share = (max(agree['long'], agree['short']) / directional) if directional else 0.0
    cover = (len(signals) / float(n_expected)) if n_expected else 0.0
    conf = (CONF_W_SCORE * clip01(abs(score)) + CONF_W_AGREE * clip01(agree_share)
            + CONF_W_COVER * clip01(cover))
    return {'score': round(score, 4), 'side': side, 'confidence': round(conf, 4),
            'agree': agree, 'n_signals': len(signals), 'n_expected': int(n_expected),
            'vote_weight': round(denom, 4),
            'method': ('score=Σ(权重×方向×强度)/Σ(在场权重)，死区 ±%.2f；'
                       'confidence=%.2f×|score|+%.2f×(多数侧票占比)+%.2f×(在场策略占比)'
                       % (COMPOSITE_DEAD_ZONE, CONF_W_SCORE, CONF_W_AGREE, CONF_W_COVER))}


def _volume_block(frames, plist, market, out, errors):
    """`volume` 块：RVOL（**同一时刻**口径）+ 成交量分布 + 背离。

    ★主周期 = 最短的那个（默认 5 分钟）。为什么：块里只能放**一组**数（规格的形状），
      而 RVOL / 分布 / 背离在三个周期上算出来的值不同（分布尤其：桶是按分钟量摊的）。
      取最短周期的理由是"日内结论要落在最新的那根 K 上"，`rvol_by_period` 里
      三个周期的读数都在，读者不用信我这一句。
    """
    vb = out['volume']
    for p in plist:
        df = frames[p]
        rv, info = st._rvol_last(df, market)
        vb['rvol_by_period'][str(p)] = num(rv)
        vb['rvol_naive_by_period'][str(p)] = num((info or {}).get('naive_last') if isinstance(info, dict) else None)
        if rv is None and isinstance(info, str):
            errors.append('%d分钟 RVOL：%s' % (p, info))
    head = plist[0]
    df = frames[head]
    rv, info = st._rvol_last(df, market)
    vb['rvol'] = num(rv)
    vb['rvol_period'] = head
    if isinstance(info, dict):
        vb['rvol_base'] = {'hist_days': info.get('hist_days'),
                           'slots_ready': info.get('slots_ready'),
                           'slots_total': info.get('slots_total'),
                           # ★`slot_mean` = **同一时刻**的历史均量（按 tod 分组那个）；
                           #   `naive_mean` = **不分时段**的全天均量（错做法，留作对照）。
                           #   两者不是一个数，前端展示"同刻均量"时必须取前者。
                           'slot_mean': num(info.get('base_last'), 0),
                           'slot_tod': info.get('base_last_tod'),
                           'slot_samples': info.get('base_last_samples'),
                           'naive_mean': num(info.get('naive_base'), 0),
                           'last_bar_volume': num(info.get('last_vol'), 0),
                           'rvol': num(rv),
                           'naive_ratio': num(info.get('naive_last'), 3)}
    try:
        vb['profile'] = vp.profile(df, market)
    except StrategySkipped as e:
        errors.append('%d分钟 成交量分布：%s' % (head, e.reason))
    try:
        vb['divergence'] = vp.divergence(df)['kind']
    except StrategySkipped as e:
        errors.append('%d分钟 量价背离：%s' % (head, e.reason))


def _levels_block(frames, plist, market, out, errors):
    """`levels`：以合成方向（composite.side）为准的 entry/stop/target/rr。

    ★方向是 flat 时怎么办：**照样算，但把两臂都给出来**（`levels['side']` 写明用的是
      哪一臂，`long`/`short` 两个子字典都在）。理由：`levels` 的 7 个字段是必填的，
      而"没有方向"不等于"没有价位"；但如果只给一臂、又不说是哪一臂，读者会把它
      当成一条方向建议 —— 那正是 `levels.py` 模块头反对的倒因为果。
      flat 时选哪一臂：取**有方向票里票多的那一侧**，再不行用 long（并写进 `method`）。
    """
    p = plist[0]
    df = frames[p]
    comp = out['composite']
    side = comp['side']
    if side not in ('long', 'short'):
        ag = comp['agree']
        side = 'long' if ag['long'] >= ag['short'] else 'short'
    try:
        L = levels.stop_targets(df, side, market)
        S = levels.stop_targets(df, 'short', market)
        G = levels.stop_targets(df, 'long', market)
    except StrategySkipped as e:
        errors.append('%d分钟 止损/目标位：%s' % (p, e.reason))
        return
    out['levels'] = {
        'entry': num(L['entry']), 'stop': num(L['stop']),
        'target1': num(L['target1']), 'target2': num(L['target2']),
        'atr': num(L['atr'], 4), 'rr': num(L['rr'], 2), 'method': L['method'],
        # ↓ 额外的（不是规格要求的字段，是"别让它被误读"需要的）
        'side': side, 'risk': num(L['risk']), 'risk_pct': num(L['risk_pct'], 4),
        'atr_pct': num(L['atr_pct'], 4), 'period': p,
        'composite_side': comp['side'], 'floored': bool(L['floored']),
        'long': {'entry': num(G['entry']), 'stop': num(G['stop']),
                 'target1': num(G['target1']), 'target2': num(G['target2']),
                 'rr': num(G['rr'], 2)},
        'short': {'entry': num(S['entry']), 'stop': num(S['stop']),
                  'target1': num(S['target1']), 'target2': num(S['target2']),
                  'rr': num(S['rr'], 2)},
    }


def _session_block(frames, plist, market, out, errors):
    """`session`：当前相位 + 窗口 + 该市场的交易规则原文。"""
    p = plist[0]
    df = frames[p]
    ts = pd.Timestamp(df['dt'].iloc[-1])
    out['ts'] = ts.strftime('%Y-%m-%d %H:%M')
    tod = int(sessions.tod_minutes(pd.Series([ts]), market).iloc[0])
    ph = sessions.phase_of(market, tod)
    out['session'] = {
        'phase': ph,
        'window': sessions.phase_windows(market).get(ph),
        'note': sessions.session_note(market),
        # ↓ 额外的：相位是**怎么**判出来的（可复核），以及这根 K 属于哪个周期
        'tod_minutes': tod, 'ts': out['ts'], 'period': p, 'bars_in_phase': len(df),
    }
    if ph == 'all':
        errors.append('该市场没有开盘/收盘（7×24）⇒ phase 恒为 all，时段效应这条策略不适用')
