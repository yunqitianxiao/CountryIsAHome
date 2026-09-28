"""十条策略，**每条一个纯函数**（输入 DataFrame + period，输出信号 dict 列表）。

    orb            开盘区间突破 / 假突破      (id: orb_break / orb_fail)
    vwap_dev       VWAP 偏离与回归            (id: vwap_revert)
    rvol_sig       相对成交量分级             (id: rvol)
    volume_profile_sig  成交量分布            (id: volume_profile)
    divergence_sig 量价背离                   (id: divergence)
    momentum       短周期动量（EMA斜率+RSI+ROC）(id: momentum)
    squeeze        布林带挤压/扩张            (id: squeeze)
    resonance      多周期共振                 (id: resonance)
    atr_sig        ATR 止损/目标位            (id: atr)
    session_sig    时段效应                   (id: session)

★两条贯穿全文件的约定（写死在这里，因为换个写法不会有任何东西变红）：

  1. **每条策略只要"算得出来"就一定产出一条**，哪怕没有方向 —— 那条是 `side='flat'`，
     它的 `why` 里写着**算术理由**（"收盘 12.41 在区间 [12.10,12.34] 内 0.07，未突破"）。
     "算不出来"（数据不够）才走 `StrategySkipped` ⇒ 整条不出现 + 一句 errors。
     把两者混起来，`errors` 会变成"策略没给方向"的垃圾桶，而它要标的是"少了什么数据"。

  2. **`strength` = "这条算法的读数有多极端"，与"有没有给出方向"无关**；
     `side == 'flat'` 时它也照样有值（挤压越窄、读数越极端）。
     只有 composite 那一步才按 side 决定要不要把它算进票数。

★`why` 的写法：**每一句都要能被人拿计算器复核** —— 带具体价位、倍数、样本数、时刻。
  不许出现"技术面偏强""量能配合良好"这种没法复核的话（规格 §3.2 纪律第 1 条）。
"""
from __future__ import annotations

import math

import pandas as pd

import indicators
from . import levels, regime, sessions, volume_profile as vp
from .algo import StrategySkipped, clip01, clip1, finite, fmt, num, safe_div

# 阈值触发型信号的强度地板：**刚过门槛的信号不该记 0 分**（0 分等于"没信号"，
# 可它明明进了 signals 列表 ⇒ 前端会显示一条强度为 0 的信号，读起来像 bug），
# 也不该记高分。所以统一映射到 [0.30, 1.00]。
TRIGGER_FLOOR = 0.30


def ramp(x, lo, hi, floor=TRIGGER_FLOOR):
    """`x` 从 `lo` 涨到 `hi` ⇒ 强度从 `floor` 涨到 1.0（超出按 1.0 封顶，低于按 floor 封底）。"""
    v = finite(x)
    if v is None:
        return 0.0
    span = float(hi) - float(lo)
    if span <= 0:
        return 1.0 if v >= hi else floor
    return floor + (1.0 - floor) * clip01((v - float(lo)) / span)


# ── 输出形状 ───────────────────────────────────────────────────────────
STRATEGY_ORDER = ('orb_break', 'orb_fail', 'vwap_revert', 'rvol', 'volume_profile',
                  'divergence', 'momentum', 'squeeze', 'resonance', 'atr', 'session')
STRATEGY_NAMES = {
    'orb_break': '开盘区间突破', 'orb_fail': '开盘区间假突破',
    'vwap_revert': 'VWAP 偏离回归', 'rvol': '相对成交量 RVOL',
    'volume_profile': '成交量分布', 'divergence': '量价背离',
    'momentum': '短周期动量', 'squeeze': '布林带挤压/扩张',
    'resonance': '多周期共振', 'atr': 'ATR 止损目标位', 'session': '时段效应',
}


def _sig(sid, period, side, strength, why, refs=None):
    return {'id': sid, 'name': STRATEGY_NAMES[sid], 'period': period, 'side': side,
            'strength': round(clip01(strength), 3), 'why': why, 'refs': refs or {}}


def _hm(ts):
    return pd.Timestamp(ts).strftime('%H:%M')


def _win(ts, period):
    """一根 K 的时间窗 `'09:30-09:35'`（用**结束**时刻减去周期宽度推的起点）。"""
    t = pd.Timestamp(ts)
    return '%s-%s' % ((t - pd.Timedelta(minutes=int(period))).strftime('%H:%M'),
                      t.strftime('%H:%M'))


def _rvol_last(df, market):
    """最新一根的 RVOL 与基线明细；算不出来返回 `(None, 原因)`（**不抛**，
    因为多数策略只是"拿它当确认"，不该因为 RVOL 缺了就整条消失）。"""
    try:
        rv, info = vp.rvol_by_tod(df, market)
    except StrategySkipped as e:
        return None, e.reason
    v = finite(rv.iloc[-1])
    if v is None:
        return None, ('最新一根的时刻在历史交易日里没有 ≥%d 个同刻样本，RVOL 无基线'
                      % vp.RVOL_MIN_SLOT_SAMPLES)
    info = dict(info)
    info['last'] = v
    info['last_vol'] = finite(pd.to_numeric(df['volume'], errors='coerce').iloc[-1])
    info['naive_last'] = safe_div(info['last_vol'], info['naive_base'])
    return v, info


# ── 1. 开盘区间突破 ORB ────────────────────────────────────────────────
# 算法出处：**Opening Range Breakout**（Toby Crabel《Day Trading with Short Term
#   Price Patterns and Opening Range Breakout》1990）；"突破失败反手"这一支是
#   Raschke 那一路的 failed-breakout / fade 用法。区间宽度用 ATR 归一化是本仓库
#   加的口径（原始版用百分比或固定 tick 数）。
# 开盘区间 = **当天的第 1 根 K**（5 分钟周期 ⇒ 经典的 5 分钟开盘区间，
# 15/30 分钟同理）—— 规格 §3.2 策略 1 的"前 N 分钟（5/15/30 各一套）"就是这个意思：
# N 跟着周期走，而不是另外再定一个与周期无关的 N。
ORB_MIN_BARS = 2
ORB_RVOL_MIN = 1.5        # 突破要有量：低于 1.5 倍同刻均量不算"确认"（= vp.RVOL_GRADES[0]）
ORB_RVOL_FULL = 4.0       # 4 倍记满分（= vp.RVOL_GRADES[2]，异动放量）
ORB_MARGIN_FULL = 0.5     # 收盘超出区间边界的幅度到 0.5×ATR 记满分
ORB_RANGE_FULL_ATR = 1.5  # 开盘区间自身宽度到 1.5×ATR 记满分（区间太窄的突破没有信息量）
ORB_STRENGTH_W = (0.50, 0.30, 0.20)   # (突破幅度, 量能确认, 区间宽度)；和为 1
ORB_FAIL_LOOKBACK = 8     # 假突破要"最近 8 根内突破过、现在又回到区间里"
ORB_FAIL_STRENGTH_W = (0.60, 0.40)    # (跌回区间内的深度/ATR, 回撤占区间宽度的比例)


def orb(df, period, market='cn_stock'):
    """开盘区间突破 + 假突破。

    ★触发用**收盘价**突破，不是"影线碰到"：用影线判突破，一根插针就报一次信号，
      而该根的收盘还在区间里 —— 那正是下面 `orb_fail` 要抓的东西，两者会同时成立。
      收盘确认把这两件事分开了（代价是入场价差一点，这是**有意**的取舍）。
    ★假突破只认"最近 `ORB_FAIL_LOOKBACK` 根内突破过"：三天前突破、今天跌回来，
      那叫回踩，不叫假突破。
    """
    if len(df) < ORB_MIN_BARS:
        raise StrategySkipped('ORB 需要至少 %d 根 K（区间 1 根 + 至少 1 根后续），实到 %d 根'
                             % (ORB_MIN_BARS, len(df)))
    day = levels.last_session(df, market)
    if len(day) < ORB_MIN_BARS:
        raise StrategySkipped('当天只有 %d 根 K，凑不出"开盘区间 + 后续走势"两段' % len(day))
    a = finite(levels.atr(df).iloc[-1])
    if a is None or a <= 0:
        raise StrategySkipped('ORB 的幅度要用 ATR%d 归一化，但 ATR 算不出来（K 线 < %d 根）'
                             % (levels.ATR_N, levels.ATR_N + 1))
    bar0 = day.iloc[0]
    or_hi, or_lo = finite(bar0['high']), finite(bar0['low'])
    if or_hi is None or or_lo is None or not (or_hi > or_lo):
        raise StrategySkipped('第 1 根 K 的最高==最低（一字/停牌），没有区间可言')
    rng = or_hi - or_lo
    win = _win(bar0['dt'], period)
    cl = pd.to_numeric(day['close'], errors='coerce')
    last_close = finite(cl.iloc[-1])
    n_day = len(day)
    L = n_day - 1
    rvol, rinfo = _rvol_last(df, market)
    range_f = clip01(rng / a / ORB_RANGE_FULL_ATR)
    refs = {'or_hi': num(or_hi), 'or_lo': num(or_lo), 'range_atr': num(rng / a),
            'atr': num(a), 'window': win, 'bars_after_range': n_day - 1,
            'rvol': num(rvol), 'close': num(last_close)}

    def _confirmed():
        return rvol is not None and rvol >= ORB_RVOL_MIN

    def _vol_txt():
        if rvol is None:
            return '量能无法确认（%s）' % rinfo
        # ★这里引的必须是 `base_last`（同刻基线），不是 `naive_base`（不分时段的全天均量）：
        #   句子里写着"同刻均值"，印错那个数不会有任何东西变红。
        return 'RVOL %s（该根量 %s / 同刻均值 %s，该时刻有 %s 个历史样本、共 %d 天）' % (
            fmt(rvol), fmt(rinfo.get('last_vol'), 0), fmt(rinfo.get('base_last'), 0),
            rinfo.get('base_last_samples'), rinfo.get('hist_days'))

    # ① 最新一根收在区间上/下方 ⇒ 突破
    # ★注意元组的次序是 (方向, 价位, 那个沿的名字)。**不可以**把它们对调：
    #   写成 `for side, brk, edge in (...)` 的话 `edge` 会是字符串 '上沿'，
    #   而下面要算的是 `last_close - edge` ⇒ TypeError（这个坑真踩过，
    #   而且只在"真的发生了突破"那一支炸，横盘那一支照样好好的）。
    for side, edge, edge_name in (('long', or_hi, '上沿'), ('short', or_lo, '下沿')):
        beyond = (last_close > or_hi) if side == 'long' else (last_close < or_lo)
        if not beyond:
            continue
        margin = abs(last_close - edge)
        i_brk = None
        for i in range(1, n_day):
            c = finite(cl.iloc[i])
            if c is None:
                continue
            if (side == 'long' and c > or_hi) or (side == 'short' and c < or_lo):
                i_brk = i
                break
        age = (L - i_brk) if i_brk is not None else 0
        m_f = clip01(margin / a / ORB_MARGIN_FULL)
        v_f = clip01((rvol - ORB_RVOL_MIN) / (ORB_RVOL_FULL - ORB_RVOL_MIN)) if rvol else 0.0
        w = ORB_STRENGTH_W
        strength = w[0] * m_f + w[1] * v_f + w[2] * range_f
        brk_txt = ('首次突破在第 %d 根（%s），距今 %d 根'
                   % (i_brk + 1, _hm(day['dt'].iloc[i_brk]), age) if i_brk is not None
                   else '首根即已在该侧之外')
        why = ('开盘区间 [%s, %s]（当天第 1 根 %d 分钟 K，%s，宽 %s）；最新收盘 %s %s'
               '区间%s %s 达 %s（=%.2f×ATR %s）；%s；%s；区间宽度 %s×ATR（该项贡献 %.2f）'
               % (fmt(or_lo), fmt(or_hi), period, win, fmt(rng, 4),
                  fmt(last_close), '高于' if side == 'long' else '低于', edge_name,
                  fmt(edge), fmt(margin),
                  margin / a, fmt(a, 4), brk_txt, _vol_txt(),
                  fmt(rng / a), range_f * w[2]))
        if _confirmed():
            why += '；按"收盘价确认"口径触发（%s 方向）' % side
            return [_sig('orb_break', period, side, strength, why,
                         dict(refs, bars_since_break=age, margin_atr=num(margin / a),
                              confirmed=True))]
        why += '；**量能未确认**（RVOL %s < 门槛 %.1f）⇒ 只记状态、不给方向' \
               % (fmt(rvol), ORB_RVOL_MIN)
        return [_sig('orb_break', period, 'flat', strength, why,
                     dict(refs, bars_since_break=age, margin_atr=num(margin / a),
                          confirmed=False))]

    # ② 收在区间内：先看有没有"最近突破过又掉回来"⇒ 假突破
    brk_recent = None
    for i in range(max(1, L - ORB_FAIL_LOOKBACK), L):
        c = finite(cl.iloc[i])
        if c is None:
            continue
        if c > or_hi:
            brk_recent = ('up', i)
        elif c < or_lo:
            brk_recent = ('dn', i)
    if brk_recent is not None:
        kind, i = brk_recent
        if kind == 'up':                       # 向上突破失败 ⇒ 反手看空
            side, edge, brk_txt = 'short', or_hi, '上沿'
        else:                                  # 向下突破失败 ⇒ 反手看多
            side, edge, brk_txt = 'long', or_lo, '下沿'
        back = abs(edge - last_close)
        w = ORB_FAIL_STRENGTH_W
        strength = w[0] * clip01(back / a) + w[1] * clip01(back / rng)
        # 反向突破边界是"更深的失败"：收盘已跑到另一侧 ⇒ 强度不再打折
        why = ('开盘区间 [%s, %s]（%s，宽 %s）；第 %d 根（%s）收盘曾突破%s %s，'
               '最新收盘 %s 已跌回区间内、距该沿 %s（=%.2f×ATR、占区间宽 %.2f）；%s'
               '⇒ 假突破，反手方向 %s'
               % (fmt(or_lo), fmt(or_hi), win, fmt(rng, 4), i + 1,
                  _hm(day['dt'].iloc[i]), brk_txt, fmt(edge), fmt(last_close),
                  fmt(back), back / a, back / rng, _vol_txt(), side))
        return [_sig('orb_fail', period, side, strength, why,
                     dict(refs, broke_at=_hm(day['dt'].iloc[i]), peak_atr=num(back / a),
                          giveback=num(back / rng), bars_since_break=L - i))]

    # ③ 区间内、也没突破过 ⇒ 无方向（带算术理由）
    up_d = or_hi - last_close
    dn_d = last_close - or_lo
    why = ('开盘区间 [%s, %s]（%s，宽 %s=%s×ATR）；最新收盘 %s 在区间内 —— '
           '距上沿 %s（=%.2f×ATR）、距下沿 %s（=%.2f×ATR），%d 根以来未有一根收在区间之外；%s'
           % (fmt(or_lo), fmt(or_hi), win, fmt(rng, 4), fmt(rng / a), fmt(last_close),
              fmt(up_d), up_d / a, fmt(dn_d), dn_d / a, n_day - 1, _vol_txt()))
    return [_sig('orb_break', period, 'flat', 0.0, why,
                 dict(refs, dist_hi_atr=num(up_d / a), dist_lo_atr=num(dn_d / a)))]


# ── 2. VWAP 偏离与回归 ─────────────────────────────────────────────────
# 算法出处：**VWAP**（成交量加权均价，机构执行基准，Berkowitz-Logue-Noser 1988
#   那篇交易成本框架之后成为标准基准）；"偏离 σ 倍数后回归"是执行台的常见用法。
#   σ 用**成交量加权**标准差（不是收盘价等权标准差）—— 量大的那几根对 VWAP 的
#   贡献大，对"偏离的尺度"的贡献也该大，两边同权才对得上。
# VWAP = 日内累计 Σ(典型价×量)/Σ量，**按日重置**（"日内"VWAP 的定义；跨日累计是另一个
# 锚定 VWAP（anchored VWAP），两者在图上长得像、数值差很远）。
# 典型价取 (high+low+close)/3（有平台用 (h+l)/2 或 (o+h+l+c)/4 —— 换一个就换一个 VWAP）。
VWAP_MIN_BARS = 6         # 少于此根，σ 是拿几根算的，不值得当 σ
VWAP_Z_ENTRY = 2.0        # 偏离 2σ 才给信号（≈正态下 5% 的两尾，这里只是**门槛约定**）
VWAP_Z_FULL = 3.5         # 3.5σ 记满分
VWAP_VOL_MIN = 1.0        # 量能确认门槛：RVOL ≥ 1（**不是** 1.5 —— 这是"确认有成交"，
                          # 不是"放量"，门槛取错会把大部分回归信号挡掉）
VWAP_STRENGTH_W = (0.75, 0.25)   # (偏离强度, 量能确认强度)


def vwap_series(day):
    """当天（已切好的一个交易日的行）的 (vwap, sigma, z)。全部累计、逐根可复算。"""
    tp = (pd.to_numeric(day['high'], errors='coerce')
          + pd.to_numeric(day['low'], errors='coerce')
          + pd.to_numeric(day['close'], errors='coerce')) / 3.0
    v = pd.to_numeric(day['volume'], errors='coerce').fillna(0.0)
    cv = v.cumsum()
    vwap = (tp * v).cumsum() / cv.where(cv > 0)
    var = (v * (tp - vwap) ** 2).cumsum() / cv.where(cv > 0)   # 成交量加权方差
    sig = var.clip(lower=0) ** 0.5
    z = (pd.to_numeric(day['close'], errors='coerce') - vwap) / sig.where(sig > 0)
    return vwap, sig, z


def vwap_dev(df, period, market='cn_stock'):
    """VWAP 偏离（z）+ 回归方向；成交量做确认。"""
    day = levels.last_session(df, market)
    if len(day) < VWAP_MIN_BARS:
        raise StrategySkipped('VWAP 偏离需要当天至少 %d 根 K 才能估出偏离的 σ，实到 %d 根'
                             % (VWAP_MIN_BARS, len(day)))
    vwap, sig, z = vwap_series(day)
    w_last = finite(vwap.iloc[-1])
    s_last = finite(sig.iloc[-1])
    z_last = finite(z.iloc[-1])
    if w_last is None or s_last is None or s_last <= 0 or z_last is None:
        raise StrategySkipped('当天成交量累计为 0 或 σ=0（价格没有波动），VWAP 偏离无定义')
    cl = finite(pd.to_numeric(day['close'], errors='coerce').iloc[-1])
    rvol, rinfo = _rvol_last(df, market)
    tp_sum = float((((pd.to_numeric(day['high'], errors='coerce')
                      + pd.to_numeric(day['low'], errors='coerce')
                      + pd.to_numeric(day['close'], errors='coerce')) / 3.0)
                    * pd.to_numeric(day['volume'], errors='coerce').fillna(0.0)).sum())
    v_sum = float(pd.to_numeric(day['volume'], errors='coerce').fillna(0.0).sum())
    refs = {'vwap': num(w_last), 'sigma': num(s_last, 6), 'z': num(z_last),
            'close': num(cl), 'day_bars': int(len(day)),
            'sum_tp_x_vol': num(tp_sum, 2), 'sum_vol': num(v_sum, 2),
            'rvol': num(rvol), 'ts': _hm(day['dt'].iloc[-1])}
    base = ('当天 %d 根累计 VWAP %s（Σ典型价×量 %s / Σ量 %s，典型价=(高+低+收)/3）；'
            '成交量加权 σ %s；最新收盘 %s 偏离 %sσ（%s）'
            % (len(day), fmt(w_last, 4), fmt(tp_sum, 0), fmt(v_sum, 0), fmt(s_last, 6),
               fmt(cl), fmt(z_last, 2), '上方' if z_last > 0 else '下方'))
    w = VWAP_STRENGTH_W
    # 偏离强度：刚好到门槛（2σ）记 0，到 3.5σ 记 1（门槛内的不给分，见模块头那条"flat 也可以有强度"）
    dev_f = clip01((abs(z_last) - VWAP_Z_ENTRY) / (VWAP_Z_FULL - VWAP_Z_ENTRY))
    vol_f = clip01((rvol - VWAP_VOL_MIN) / (vp.RVOL_GRADES[1] - VWAP_VOL_MIN)) if rvol else 0.0
    strength = w[0] * dev_f + w[1] * vol_f
    if abs(z_last) < VWAP_Z_ENTRY:
        return [_sig('vwap_revert', period, 'flat', strength,
                     base + '，未达 %.1fσ 门槛 ⇒ 不给方向' % VWAP_Z_ENTRY, refs)]
    side = 'short' if z_last > 0 else 'long'
    if rvol is None:
        return [_sig('vwap_revert', period, 'flat', strength,
                     base + '，已达 %.1fσ 门槛，但**量能无法确认**（%s）⇒ 只记偏离不给方向'
                     % (VWAP_Z_ENTRY, rinfo), refs)]
    if rvol < VWAP_VOL_MIN:
        return [_sig('vwap_revert', period, 'flat', strength,
                     base + '，已达 %.1fσ 门槛，但 RVOL %s < %.1f：偏离不是在有成交的'
                     '情况下发生的 ⇒ 不给方向' % (VWAP_Z_ENTRY, fmt(rvol), VWAP_VOL_MIN),
                     refs)]
    why = (base + '，≥ %.1fσ 门槛且 RVOL %s（≥%.1f 门槛，同刻均值 %s、历史 %d 天）'
                  '⇒ 均值回归方向 %s，回归目标 = VWAP %s（距最新收盘 %.2f×σ）'
           % (VWAP_Z_ENTRY, fmt(rvol), VWAP_VOL_MIN, fmt(rinfo.get('base_last'), 0),
              rinfo.get('hist_days'), side, fmt(w_last, 4), abs(z_last)))
    return [_sig('vwap_revert', period, side, strength, why,
                 dict(refs, target=num(w_last), sigma_dist=num(abs(z_last))))]


# ── 3. 相对成交量 RVOL 分级 ────────────────────────────────────────────
RVOL_RET_LOOKBACK = 3     # 方向看"最近 3 根累计涨跌"，不是只看最后一根（最后一根常是噪声）
RVOL_RET_FULL = 0.005     # 3 根累计 0.5% 记满分
RVOL_STRENGTH_W = (0.70, 0.30)   # (量能档位, 价格同向幅度)


def rvol_sig(df, period, market='cn_stock'):
    """RVOL 分三档（1.5/2.5/4.0），方向取自"量增时价格往哪走"。

    ★方向**不是**"放量本身看涨"：放量只说明分歧变大。这里的判据是
      **量增 + 价同向**（近 `RVOL_RET_LOOKBACK` 根累计涨/跌），
      量增而价几乎不动时给 flat（"放量滞涨/滞跌"）。
    ★`why` 里同时报**错做法**的读数（不分时段的全天均值口径）——
      这是本模块最容易做错的地方，把它摆在同一个句子里，读者一眼能看出差多少。
    """
    rvol, info = _rvol_last(df, market)
    if rvol is None:
        raise StrategySkipped(info)
    cl = pd.to_numeric(df['close'], errors='coerce')
    if len(cl) < RVOL_RET_LOOKBACK + 1:
        raise StrategySkipped('RVOL 的方向要看最近 %d 根累计涨跌，实到 %d 根 K'
                             % (RVOL_RET_LOOKBACK, len(cl)))
    c0 = finite(cl.iloc[-1 - RVOL_RET_LOOKBACK])
    c1 = finite(cl.iloc[-1])
    if c0 is None or c1 is None or c0 <= 0:
        raise StrategySkipped('最近 %d 根的收盘价有缺值，算不出累计涨跌' % RVOL_RET_LOOKBACK)
    ret = c1 / c0 - 1.0
    grade, gname = vp.rvol_grade(rvol)
    last_vol = info.get('last_vol')
    naive = info.get('naive_last')
    refs = {'rvol': num(rvol), 'grade': grade, 'grade_name': gname,
            'last_vol': num(last_vol, 0), 'base_same_slot': num(info.get('base_last'), 0),
            'slot_samples': info.get('base_last_samples'),
            'rvol_naive': num(naive), 'naive_base': num(info.get('naive_base'), 0),
            'hist_days': info.get('hist_days'),
            'ret_3bars': num(ret), 'ts': _hm(df['dt'].iloc[-1])}
    base = ('最新 %d 分钟 K（%s）量 %s；**同一时刻**（%s）前 %d 个交易日均量 %s '
            '⇒ RVOL %s（%s）；近 %d 根累计 %s%s'
            % (period, _win(df['dt'].iloc[-1], period), fmt(last_vol, 0),
               _hm(df['dt'].iloc[-1]), info.get('hist_days'), fmt(info.get('base_last'), 0),
               fmt(rvol), gname, RVOL_RET_LOOKBACK, '+' if ret >= 0 else '', fmt(ret * 100, 2) + '%'))
    if naive is not None and naive > 0:
        base += ('；对照：若按**不分时段**的口径（比当天之前全部分钟的均量 %s），'
                 '同一根是 %s 倍 —— 日内量是 U 型，两种口径差 %.1f 倍'
                 % (fmt(info.get('naive_base'), 0), fmt(naive), rvol / naive))
    w = RVOL_STRENGTH_W
    # 量能档位：1.5 倍（第一档）记 0，4.0 倍（第三档，异动）记 1 —— 中间线性
    vol_f = clip01((rvol - vp.RVOL_GRADES[0]) / (vp.RVOL_GRADES[2] - vp.RVOL_GRADES[0]))
    ret_f = clip01(abs(ret) / RVOL_RET_FULL)
    strength = w[0] * vol_f + w[1] * ret_f
    if grade == 0:
        return [_sig('rvol', period, 'flat', strength,
                     base + '，未达 %.1f 倍门槛（三档：%s）⇒ 量能无异动，不给方向'
                     % (vp.RVOL_GRADES[0], '/'.join('%.1f' % x for x in vp.RVOL_GRADES)),
                     refs)]
    if ret_f <= 0:
        return [_sig('rvol', period, 'flat', strength,
                     base + '，%s但近 %d 根价格几乎没动（量增价滞）⇒ 分歧变大不给方向'
                     % (gname, RVOL_RET_LOOKBACK), refs)]
    side = 'long' if ret > 0 else 'short'
    why = base + '，%s且价%s（量价同向）⇒ %s' % (gname, '涨' if ret > 0 else '跌', side)
    return [_sig('rvol', period, side, strength, why, refs)]


# ── 4. 成交量分布 ──────────────────────────────────────────────────────
# 算法出处：**Market Profile / TPO**（J. Peter Steidlmayer，CBOT 1980 年代）
#   —— POC（成交最密集的价位）、价值区 VA（覆盖 70% 量的**连续**价格带）、
#   高/低量节点（HVN/LVN）。分桶口径与"连续扩张法"都写在 `volume_profile.py`
#   模块头（那里解释了为什么不能用"取量最大的若干桶再取 min/max"）。
VP_BREAK_FULL = 0.5       # 收盘离开价值区边界 0.5×ATR 记满分
VP_STRENGTH_W = (0.70, 0.30)   # (离开价值区的幅度/ATR, POC 在价值区内的占比)


def volume_profile_sig(df, period, market='cn_stock'):
    """POC / 价值区。方向判据：收盘**离开**价值区哪一侧被接受。

    ★读法（写在 why 里，因为"离开价值区"可以读成突破也可以读成超买）：
      价格在价值区**上方**被接受 ⇒ 上方没有套牢盘、市场认可更高的价 ⇒ long；
      下方 ⇒ short；在区内 ⇒ flat（均衡，POC 是磁石）。
      这是 Market Profile 的常规读法，**不是**"偏离就回归"（那是 VWAP 策略的读法）——
      两条策略因此在同一根 K 上可能给相反方向，这是设计，不是矛盾。
    """
    prof = vp.profile(df, market)
    cl = finite(pd.to_numeric(df['close'], errors='coerce').iloc[-1])
    if cl is None:
        raise StrategySkipped('最新收盘价是坏值')
    a = finite(levels.atr(df).iloc[-1])
    if a is None or a <= 0:
        raise StrategySkipped('成交量分布的方向要用 ATR%d 归一化，但 ATR 算不出来'
                             % levels.ATR_N)
    va_lo, va_hi, poc = prof['va_lo'], prof['va_hi'], prof['poc']
    refs = dict(prof, close=num(cl), atr=num(a),
                va_lo=num(va_lo), va_hi=num(va_hi), poc=num(poc))
    head = ('当日（%s，%d 根）成交量分布：区间 [%s, %s] 分 %d 桶；POC %s（占当日量 %s%%）；'
            '价值区（连续覆盖 %d%% 量）[%s, %s]；高量节点 %s；低量节点 %s；收盘 %s'
            % (prof['session'], prof['bars'], fmt(prof['range'][0]), fmt(prof['range'][1]),
               prof['bins'], fmt(poc), fmt((prof['poc_vol_share'] or 0) * 100, 1),
               int(vp.VA_PCT * 100), fmt(va_lo), fmt(va_hi),
               prof['hvn'] or '无', prof['lvn'] or '无', fmt(cl)))
    poc_share = clip01(((prof['poc_vol_share'] or 0.0) - vp.VA_PCT)
                       / (1 - vp.VA_PCT) + TRIGGER_FLOOR)
    # ↑ POC 集中度：POC 桶占当日量 70%（= 价值区门槛）时记 0.30，占满 100% 记 1.0。
    #   取 70% 当地板是因为 POC 桶的占比**必然** ≥ 价值区门槛的单桶份额量级（否则它
    #   不会是 POC 所在那一档），从 0 起算会让这一项恒在低位、等于没起作用。
    w = VP_STRENGTH_W
    if va_hi is not None and cl > va_hi:
        d = cl - va_hi
        strength = w[0] * ramp(d / a, 0, VP_BREAK_FULL) + w[1] * poc_share
        return [_sig('volume_profile', period, 'long', strength,
                     head + '；收盘在价值区**上方** %s（=%.2f×ATR）⇒ 上方被接受，方向 long'
                            % (fmt(d), d / a), refs)]
    if va_lo is not None and cl < va_lo:
        d = va_lo - cl
        strength = w[0] * ramp(d / a, 0, VP_BREAK_FULL) + w[1] * poc_share
        return [_sig('volume_profile', period, 'short', strength,
                     head + '；收盘在价值区**下方** %s（=%.2f×ATR）⇒ 下方被接受，方向 short'
                            % (fmt(d), d / a), refs)]
    dist = abs(cl - poc) if poc is not None else None
    return [_sig('volume_profile', period, 'flat', w[1] * poc_share,
                 head + '；收盘落在价值区内（距 POC %s）⇒ 均衡区，不给方向'
                        % (fmt(dist) if dist is not None else '—'), refs)]


# ── 5. 量价背离 ────────────────────────────────────────────────────────
# 算法出处：**OBV**（On-Balance Volume，Joseph Granville 1963）的经典用法
#   ——"价格新高而 OBV 不新高"= 顶背离（Granville 原书的说法是"量能没有确认价格"）。
#   两臂等长（近 N 根 vs 前 N 根）是本仓库定的口径，见 `volume_profile.divergence`
#   的注释（那里解释了为什么 OBV 取"那一根"的值、不取窗口极值）。
DIV_PRICE_FULL_ATR = 1.0  # 价格新高比前臂高出 1×ATR 记满分
DIV_STRENGTH_W = (0.50, 0.50)   # (价格新高幅度/ATR, OBV 反向落差/窗口尺度)


def divergence_sig(df, period, market='cn_stock'):
    """OBV 量价背离（顶背离 / 底背离 / 无）。"""
    d = vp.divergence(df)
    a = finite(levels.atr(df).iloc[-1])
    w = DIV_STRENGTH_W
    win = d['window']
    if d['kind'] == 'none':
        refs = {'kind': 'none', 'window': win,
                'price_hi_recent': num(d['price_hi_recent']),
                'price_hi_prior': num(d['price_hi_prior']),
                'price_lo_recent': num(d['price_lo_recent']),
                'price_lo_prior': num(d['price_lo_prior']),
                'pv_corr': num(d['pv_corr'], 3), 'bars': d['bars']}
        return [_sig('divergence', period, 'flat', 0.0,
                     '近 %d 根高点 %s vs 前 %d 根高点 %s（%s）；近 %d 根低点 %s vs 前 %d 根低点 %s'
                     '（%s）⇒ 价格与 OBV 未出现反向（价-量滚动相关 %s，%d 根）'
                     % (win, fmt(d['price_hi_recent']), win, fmt(d['price_hi_prior']),
                        '未创新高' if d['price_hi_recent'] <= d['price_hi_prior'] else '创新高',
                        win, fmt(d['price_lo_recent']), win, fmt(d['price_lo_prior']),
                        '未创新低' if d['price_lo_recent'] >= d['price_lo_prior'] else '创新低',
                        fmt(d['pv_corr'], 3), vp.PV_CORR_N), refs)]
    bear = d['kind'] == 'bearish'
    scale = d['obv_scale']
    # ★两个分支引的必须是**各自那一臂**的数（顶背离看高点、底背离看低点）。
    #   曾经这里把高点的价位和 OBV 写死进模板、只在标题上换"顶/底"两个字，
    #   于是底背离那条会印出"近 10 根高点 A > 前 10 根高点 B"，紧接着报一个
    #   **低点那一臂**的 OBV 差值 ⇒ 句子里三个数自己就对不上（拿计算器一减就露馅）。
    #   这是"why 必须可复核"最典型的破法：读起来通顺、算一遍不对。
    if bear:
        p_rec, p_pri = d['price_hi_recent'], d['price_hi_prior']
        o_rec, o_pri = d['obv_at_hi_recent'], d['obv_at_hi_prior']
        dp = p_rec - p_pri
        dobv = o_pri - o_rec                     # 取正 = "OBV 没跟上价格"
        side, txt = 'short', '顶背离（价新高、量能潮不新高）'
        cmp_txt = ('近 %d 根高点 %s > 前 %d 根高点 %s（高出 %s=%.2f×ATR %s）'
                   % (win, fmt(p_rec), win, fmt(p_pri), fmt(dp),
                      (dp / a) if a else 0.0, fmt(a, 4) if a else '—'))
        obv_txt = '但那两处对应的 OBV 从 %s **降到** %s（降幅 %s = 窗口尺度 %s 的 %s 倍）' \
                  % (fmt(o_pri, 0), fmt(o_rec, 0), fmt(dobv, 0), fmt(scale, 0),
                     fmt(safe_div(dobv, scale)))
    else:
        p_rec, p_pri = d['price_lo_recent'], d['price_lo_prior']
        o_rec, o_pri = d['obv_at_lo_recent'], d['obv_at_lo_prior']
        dp = p_pri - p_rec                       # 取正 = "低点更低了"
        dobv = o_rec - o_pri                     # 取正 = "OBV 没跟下去"
        side, txt = 'long', '底背离（价新低、量能潮不新低）'
        cmp_txt = ('近 %d 根低点 %s < 前 %d 根低点 %s（低了 %s=%.2f×ATR %s）'
                   % (win, fmt(p_rec), win, fmt(p_pri), fmt(dp),
                      (dp / a) if a else 0.0, fmt(a, 4) if a else '—'))
        obv_txt = '但那两处对应的 OBV 从 %s **升到** %s（升幅 %s = 窗口尺度 %s 的 %s 倍）' \
                  % (fmt(o_pri, 0), fmt(o_rec, 0), fmt(dobv, 0), fmt(scale, 0),
                     fmt(safe_div(dobv, scale)))
    p_f = ramp(dp / a, 0, DIV_PRICE_FULL_ATR) if a else TRIGGER_FLOOR
    o_f = clip01(safe_div(dobv, scale) or 0.0)
    strength = w[0] * p_f + w[1] * o_f
    refs = {'kind': d['kind'], 'window': win, 'price_delta': num(dp),
            'price_delta_atr': num(dp / a) if a else None,
            'obv_delta': num(dobv, 2), 'obv_scale': num(scale, 2),
            'obv_ratio': num(safe_div(dobv, scale)), 'pv_corr': num(d['pv_corr'], 3),
            'price_recent': num(p_rec), 'price_prior': num(p_pri),
            'obv_at_recent': num(o_rec, 2), 'obv_at_prior': num(o_pri, 2),
            'price_hi_recent': num(d['price_hi_recent']),
            'price_hi_prior': num(d['price_hi_prior']),
            'price_lo_recent': num(d['price_lo_recent']),
            'price_lo_prior': num(d['price_lo_prior']), 'bars': d['bars']}
    why = ('%s：%s，%s；价-量滚动相关 %s（%d 根，辅助读数、不单独当判据）⇒ 方向 %s'
           % (txt, cmp_txt, obv_txt, fmt(d['pv_corr'], 3), vp.PV_CORR_N, side))
    return [_sig('divergence', period, side, strength, why, refs)]


# ── 6. 短周期动量 ──────────────────────────────────────────────────────
# 算法出处：**RSI**（J. Welles Wilder 1978《New Concepts in Technical Trading
#   Systems》）+ **ROC**（变动率）+ EMA 斜率。三者的口径都由本仓库已有实现决定：
#   RSI 走 `agent/indicators.py: rsi`（通达信递推），EMA 走 `adjust=False`，
#   斜率除以 ATR×√3 是为了把"每根涨多少"归一成与波动率同量纲的数
#   （√3 来自"三点线性拟合的斜率方差"这个近似，见下面 MOM 那几个常量）。
MOM_RSI_N = 14            # RSI 窗口（Wilder 的经典值；口径由 indicators.rsi 决定：通达信递推）
MOM_RSI_HALF = 25.0       # RSI 偏离 50 到 25 个点记满方向分（RSI 75 ⇒ +1.0）
MOM_ROC_N = 10            # ROC 窗口
MOM_STRENGTH_W = (0.40, 0.30, 0.30)   # (EMA 斜率 z, RSI 归一, ROC z)
MOM_DEAD_ZONE = 0.15      # 合成方向分低于此 ⇒ 不给方向


def momentum(df, period, market='cn_stock'):
    """EMA 斜率 + RSI + ROC 的多周期合成。

    ★三个分量各自**先归一化再合成**，权重在 `MOM_STRENGTH_W`：
      · 斜率与 ROC 除的是 `ATR×√N` —— 那是"这只标的自己走 N 根的正常位移"，
        除掉之后不同标的、不同周期的读数才可比（0.1% 在高波动股上什么都不是）；
      · RSI 没有天然的量纲，用 `(RSI-50)/25` 映射到 [-1,1]。
    ★RSI 在这里**当趋势分量用**（高 RSI = 强），不是当超买超卖用 ——
      后者的方向是反的，混起来会让合成分自相抵消。超买超卖由 VWAP 偏离那条负责。
    """
    need = max(levels.ATR_N + 1, MOM_RSI_N + 1, MOM_ROC_N + 1)
    if len(df) < need:
        raise StrategySkipped('动量需要至少 %d 根 K（ATR%d/RSI%d/ROC%d 里最长的那个），'
                             '实到 %d 根' % (need, levels.ATR_N, MOM_RSI_N, MOM_ROC_N,
                                           len(df)))
    c = pd.to_numeric(df['close'], errors='coerce')
    a = finite(levels.atr(df).iloc[-1])
    if a is None or a <= 0:
        raise StrategySkipped('动量归一化要用 ATR%d，但 ATR 算不出来' % levels.ATR_N)
    slope_z = regime.ema_slope_z(df)
    rsi_v = finite(indicators.rsi(c, MOM_RSI_N).iloc[-1])
    c1 = finite(c.iloc[-1])
    c0 = finite(c.iloc[-1 - MOM_ROC_N])
    if slope_z is None or rsi_v is None or c1 is None or c0 is None or c0 <= 0:
        raise StrategySkipped('动量三个分量里有算不出来的（斜率=%s, RSI%d=%s, ROC 端点=%s/%s）'
                             % (slope_z, MOM_RSI_N, rsi_v, c0, c1))
    roc = c1 / c0 - 1.0
    atr_pct = a / c1
    roc_z = roc / (atr_pct * math.sqrt(MOM_ROC_N)) if atr_pct > 0 else None
    if roc_z is None:
        raise StrategySkipped('ATR 占价比例为 0，ROC 无法归一化')
    comp = (clip1(slope_z), clip1((rsi_v - 50.0) / MOM_RSI_HALF), clip1(roc_z))
    w = MOM_STRENGTH_W
    score = w[0] * comp[0] + w[1] * comp[1] + w[2] * comp[2]
    refs = {'slope_z': num(comp[0], 3), 'rsi': num(rsi_v, 2), 'rsi_norm': num(comp[1], 3),
            'roc': num(roc, 4), 'roc_z': num(comp[2], 3), 'score': num(score, 3),
            'atr_pct': num(atr_pct, 4), 'weights': list(w), 'bars': int(len(df))}
    why = ('EMA%d 斜率 %s（=ΔEMA/(ATR%s×√%d) 归一）→ %s；RSI%d %s（(RSI-50)/%s）→ %s；'
           'ROC%d %s（ATR 占价 %s%%、√%d 归一）→ %s；加权（%s）合成 %s'
           % (regime.REGIME_EMA_N, fmt(comp[0], 3), fmt(a, 4), regime.REGIME_SLOPE_LAG,
              fmt(comp[0], 3), MOM_RSI_N, fmt(rsi_v, 2), fmt(MOM_RSI_HALF, 0),
              fmt(comp[1], 3), MOM_ROC_N, fmt(roc * 100, 2) + '%', fmt(atr_pct * 100, 3),
              MOM_ROC_N, fmt(comp[2], 3), '/'.join('%.2f' % x for x in w), fmt(score, 3)))
    strength = clip01(abs(score))
    if abs(score) < MOM_DEAD_ZONE:
        return [_sig('momentum', period, 'flat', strength,
                     why + '，绝对值 < %.2f 的死区 ⇒ 不给方向' % MOM_DEAD_ZONE, refs)]
    return [_sig('momentum', period, 'long' if score > 0 else 'short', strength, why, refs)]


# ── 7. 布林带挤压 / 扩张 ───────────────────────────────────────────────
# 算法出处：**Bollinger Bands**（John Bollinger）+ 他本人推荐的 **BandWidth**
#   指标（(上轨-下轨)/中轨）；"挤压后必然扩张"（squeeze → expansion）是
#   Bollinger 与 TTM Squeeze（John Carter）那一系的用法。判据用**带宽的分位数**
#   而不是绝对带宽 —— 绝对带宽不可跨标的比较（12 元的票和 3400 点的指数没法比）。
# 阈值直接从 regime 拿（那里已经写了 squeeze=≤20% 分位、expansion=≥80% 分位）——
# 两处各写一份的话，"20" 迟早变成一个地方改了另一个地方没改的两个人。
SQZ_STRENGTH_W = (0.60, 0.40)   # (挤压/扩张程度, 收盘相对中轨的位置)


def squeeze(df, period, market='cn_stock'):
    """带宽分位判 squeeze（≤20%）/ expansion（≥80%）/ 常态。

    ★挤压**不给方向**（它给的是一句"等突破"），方向提示放在 `refs['breakout_hint']`
      与 `why` 里：收盘在中轨哪一侧。硬给一个方向就等于把"要不要等"偷偷变成了"买"。
    ★扩张**给方向**：顺着收盘相对中轨的那一侧（带宽张开时，价格已经在带子边缘）。
    """
    pct = regime.boll_width_pctile(df)
    if pct is None:
        raise StrategySkipped('带宽分位需要至少 %d 根 K（BOLL%d + 窗口 %d），实到 %d 根'
                             % (indicators.BOLL_N + regime.SQZ_LOOKBACK, indicators.BOLL_N,
                                regime.SQZ_LOOKBACK, len(df)))
    c = pd.to_numeric(df['close'], errors='coerce')
    mid, up, low = indicators.boll(c, indicators.BOLL_N, indicators.BOLL_K)
    mid_l, up_l, low_l = finite(mid.iloc[-1]), finite(up.iloc[-1]), finite(low.iloc[-1])
    cl = finite(c.iloc[-1])
    if mid_l is None or up_l is None or low_l is None or cl is None or mid_l <= 0:
        raise StrategySkipped('布林带三轨或最新收盘是坏值')
    width = (up_l - low_l) / mid_l
    a = finite(levels.atr(df).iloc[-1])
    pos = (cl - mid_l)
    refs = {'width': num(width, 6), 'width_pctile': num(pct, 1),
            'lookback': regime.SQZ_LOOKBACK, 'boll_n': indicators.BOLL_N,
            'boll_k': indicators.BOLL_K, 'mid': num(mid_l), 'upper': num(up_l),
            'lower': num(low_l), 'close': num(cl), 'atr': num(a),
            'breakout_hint': 'long' if pos > 0 else ('short' if pos < 0 else 'flat')}
    head = ('布林带（%d 根、%sσ、ddof=0）带宽 (上轨-下轨)/中轨 = %s%%（上轨 %s / 中轨 %s / 下轨 %s）；'
            '该带宽在近 %d 根里的分位 %s%%；收盘 %s 在中轨%s %s%s'
            % (indicators.BOLL_N, fmt(indicators.BOLL_K, 1), fmt(width * 100, 3),
               fmt(up_l), fmt(mid_l), fmt(low_l), regime.SQZ_LOOKBACK, fmt(pct, 1),
               fmt(cl), '上方' if pos >= 0 else '下方', fmt(abs(pos)),
               ('=%.2f×ATR' % (abs(pos) / a)) if a else ''))
    w = SQZ_STRENGTH_W
    pos_f = clip01(abs(pos) / a) if a else 0.0       # 收盘离中轨多远（以 ATR 为单位）
    if pct <= regime.SQZ_PCTILE:
        s = w[0] * ramp(regime.SQZ_PCTILE - pct, 0, regime.SQZ_PCTILE) + w[1] * pos_f
        return [_sig('squeeze', period, 'flat', s,
                     head + '；带宽分位 ≤ %.0f%% ⇒ **挤压**（比 %.0f%% 的时间更窄），'
                            '方向待突破，突破提示方向 = %s（收盘在中轨%s）'
                     % (regime.SQZ_PCTILE, 100 - regime.SQZ_PCTILE,
                        refs['breakout_hint'], '上方' if pos >= 0 else '下方'), refs)]
    if pct >= regime.EXP_PCTILE:
        side = 'long' if pos > 0 else ('short' if pos < 0 else 'flat')
        s = w[0] * ramp(pct, regime.EXP_PCTILE, 100.0) + w[1] * pos_f
        return [_sig('squeeze', period, side, s,
                     head + '；带宽分位 ≥ %.0f%% ⇒ **扩张**，顺收盘相对中轨的一侧 ⇒ %s'
                     % (regime.EXP_PCTILE, side), refs)]
    return [_sig('squeeze', period, 'flat', 0.0,
                 head + '；带宽分位在 %.0f%%~%.0f%% 之间 ⇒ 既未挤压也未扩张，不给方向'
                 % (regime.SQZ_PCTILE, regime.EXP_PCTILE), refs)]


# ── 8. 多周期共振 ──────────────────────────────────────────────────────
# 算法出处：**多重时间框架分析**（Multiple Timeframe Analysis；Elder 的
#   triple-screen 是同一思想的经典写法）。这里不是"三个周期都看涨才做多"的
#   开关式写法，而是**加权打分**：每个周期的方向分来自第 6 条的动量合成，
#   权重按"粗周期定方向、细周期定时机"的惯例给（30/15 各 0.35、5 分钟 0.30）。
def resonance(dirs):
    """`dirs` = `{period: momentum 的方向分}`（由 `__init__.py` 收集）。"""
    r = regime.resonance(dirs)
    score = r['score']
    detail = '、'.join('%s分钟 %s（权重 %s）' % (p, fmt(r['dirs'][p], 3), r['weights'][p])
                      for p in sorted(r['dirs']))
    refs = {'score': num(score, 3), 'dirs': {str(p): num(v, 3) for p, v in r['dirs'].items()},
            'weights': {str(p): w for p, w in r['weights'].items()},
            'n_up': r['n_up'], 'n_down': r['n_down'], 'n_periods': r['n_periods'],
            'periods': sorted(r['dirs'])}
    why = ('方向分：%s；加权（Σ权重 %s）合成 %s；%d 个周期里 %d 上 / %d 下'
           % (detail, fmt(r['wsum'], 2), fmt(score, 3), r['n_periods'], r['n_up'], r['n_down']))
    strength = clip01(abs(score))
    if abs(score) < regime.RESONANCE_MIN_AGREE:
        return [_sig('resonance', None, 'flat', strength,
                     why + '，|加权分| < %.2f ⇒ 周期之间不一致，不给方向'
                     % regime.RESONANCE_MIN_AGREE, refs)]
    return [_sig('resonance', None, 'long' if score > 0 else 'short', strength,
                 why + ' ⇒ 多周期共振方向 %s' % ('long' if score > 0 else 'short'), refs)]


# ── 9. ATR 止损/目标位 ─────────────────────────────────────────────────
def atr_sig(df, period, market='cn_stock'):
    """只出**价位**，不给方向（见 `levels.py` 模块头：把盈亏比混进方向判断是倒因为果）。

    ⇒ 这条的 `side` 恒为 `'flat'`；它在 `signals` 里出现是为了让前端能一条条列出
      算法与它的读数，composite 不把它算进票数（`SIGNAL_W` 里**没有** `atr` 这一项）。
    """
    both = {}
    for side in ('long', 'short'):
        st = levels.stop_targets(df, side, market)
        both[side] = {'entry': num(st['entry']), 'stop': num(st['stop']),
                      'target1': num(st['target1']), 'target2': num(st['target2']),
                      'risk': num(st['risk']), 'rr': num(st['rr'], 2),
                      'risk_pct': num(st['risk_pct'], 4)}
    L = both['long']
    st = levels.stop_targets(df, 'long', market)
    refs = {'atr': num(st['atr'], 4), 'atr_n': levels.ATR_N,
            'atr_pct': num(st['atr_pct'], 4), 'long': L, 'short': both['short'],
            'day_low': num(st['day_low']), 'day_high': num(st['day_high']),
            'floored': st['floored'], 'method': st['method']}
    why = ('ATR%d = %s（占价 %s%%，Wilder 递推、TR 含跳空）；long 口径：入场 %s、'
           '止损 %s（风险 %s = 价的 %s%%%s）、目标1 %s（%.1f×ATR）、目标2 %s（%.1f×ATR）、'
           '盈亏比 %.2f；short 口径：止损 %s、目标1 %s、目标2 %s、盈亏比 %.2f（镜像）'
           % (levels.ATR_N, fmt(st['atr'], 4), fmt((st['atr_pct'] or 0) * 100, 3),
              fmt(L['entry']), fmt(L['stop']), fmt(L['risk']),
              fmt((L['risk_pct'] or 0) * 100, 3),
              '，被 0.3% 风险地板顶上来' if st['floored'] else '',
              fmt(L['target1']), levels.ATR_T1_MULT, fmt(L['target2']), levels.ATR_T2_MULT,
              L['rr'] or 0, fmt(both['short']['stop']), fmt(both['short']['target1']),
              fmt(both['short']['target2']), both['short']['rr'] or 0))
    return [_sig('atr', period, 'flat', 0.0, why, refs)]


# ── 10. 时段效应 ───────────────────────────────────────────────────────
# 算法出处：日内成交量/波动率的 **U 型**（开盘与收盘两头高、午盘低）——
#   Admati & Pfleiderer (1988) 的日内交易量模型与随后的实证文献都记这个形状；
#   本仓库在 `sessions.py` 与 `volume_profile.rvol_by_tod` 里两处都用到它。
#   ★这一条算的是**样本内的描述统计**（相位均值/密度比），不是统计推断 ——
#   分钟线一次只取到几天，`why` 里把天数写出来就是为了不让它被读成"规律"。
# ★样本与来源：**本帧自己的分钟线**（默认 5 个交易日），不是外部统计。
#   经典结论（成交量 U 型：开盘/收盘两头大、午盘最小）在这里只作为**对照**，
#   报出去的每一个数都是这一帧里算出来的。5 个交易日是"能看出形状"的最小样本，
#   不足以做统计推断 —— 所以 `why` 里明写"样本 n 天，是描述不是推断"。
SESSION_MIN_DAYS = 3      # 少于 3 天不给（1 天"均值"就是那天本身）
SESSION_RET_MIN = 0.001   # 相位日均收益 ±0.1% 才算有方向（否则 noise 就是方向）
SESSION_RET_FULL = 0.004  # 相位日均收益 0.4% 记满方向分
SESSION_VOL_FULL = 2.0    # 量能密度到 2.0（= 全天均速的 2 倍）记满
SESSION_DENS_FULL = 2.0   # 波动密度同理
SESSION_STRENGTH_W = (0.50, 0.50)   # (量能密度, 波动密度)


def session_sig(df, period, market='cn_stock'):
    """时段效应：开盘 30 分 / 午盘 / 尾盘的量能与波动差异（描述性统计）。

    `phase` 的切分口径见 `sessions.phase_of`（以 K 线**结束**时刻算）。
    """
    m = sessions.norm_market(market)
    if m == 'crypto':
        raise StrategySkipped('加密 7×24 没有开盘/收盘 ⇒ "开盘 30 分钟"没有定义，'
                             '时段效应这条策略不适用（phase 恒为 all）')
    d = pd.DataFrame(index=df.index)
    d['tod'] = sessions.tod_minutes(df['dt'], market).values
    d['day'] = pd.to_datetime(df['dt']).dt.normalize().values
    c = pd.to_numeric(df['close'], errors='coerce')
    o = pd.to_numeric(df['open'], errors='coerce')
    v = pd.to_numeric(df['volume'], errors='coerce')
    d['ret'] = (c / o - 1.0).values
    d['vol'] = v.values
    d['phase'] = [sessions.phase_of(market, t) for t in d['tod']]
    d = d.dropna(subset=['ret', 'vol'])
    if not len(d):
        raise StrategySkipped('没有可用的收盘/开盘/成交量行')
    ndays = int(d['day'].nunique())
    if ndays < SESSION_MIN_DAYS:
        raise StrategySkipped('时段效应需要至少 %d 个交易日，实到 %d 天 '
                             '—— 1~2 天的"相位均值"就等于那两天本身' % (SESSION_MIN_DAYS, ndays))
    last_phase = sessions.phase_of(market, int(d['tod'].iloc[-1]))
    # ★密度比 = 该相位的"每分钟量（或 |收益|）" ÷ 全天的"每分钟量（或 |收益|）"。
    #   必须按**分钟**归一，否则相位长短不同（开盘 30 分 vs 午盘 120 分）直接不可比 ——
    #   直接比总量的话，午盘永远最大，而这正是"U 型"要被看出来的地方。
    day_rate_v = float(d['vol'].mean())
    day_rate_r = float(d['ret'].abs().mean())
    rows = {}
    for ph in sessions.PHASE_KEYS:
        sub = d[d['phase'] == ph]
        if not len(sub):
            continue
        per_day_ret = sub.groupby('day')['ret'].apply(lambda s: float((1 + s).prod() - 1))
        rows[ph] = {
            'n_bars': int(len(sub)), 'n_days': int(sub['day'].nunique()),
            'vol_density': float(sub['vol'].mean() / day_rate_v) if day_rate_v else None,
            'ret_density': float(sub['ret'].abs().mean() / day_rate_r) if day_rate_r else None,
            'mean_bar_ret': float(sub['ret'].mean()),
            'mean_day_ret': float(per_day_ret.mean()),
            'day_ret_std': float(per_day_ret.std(ddof=1)) if len(per_day_ret) > 1 else None,
            'window': sessions.phase_windows(market).get(ph),
        }
    if last_phase not in rows:
        raise StrategySkipped('当前相位（%s）在这一帧里没有样本' % last_phase)
    r = rows[last_phase]
    w = SESSION_STRENGTH_W
    v_f = ramp(r['vol_density'], 1.0, SESSION_VOL_FULL) if r['vol_density'] is not None else 0.0
    r_f = ramp(r['ret_density'], 1.0, SESSION_DENS_FULL) if r['ret_density'] is not None else 0.0
    strength = w[0] * v_f + w[1] * r_f
    others = '；'.join('%s 量能密度 %s/波动密度 %s（%d 根）'
                       % (k, fmt(rows[k]['vol_density'], 2), fmt(rows[k]['ret_density'], 2),
                          rows[k]['n_bars']) for k in sessions.PHASE_KEYS
                       if k in rows and k != last_phase)
    refs = {'phase': last_phase, 'window': r['window'], 'n_days': ndays,
            'n_bars': r['n_bars'], 'vol_density': num(r['vol_density'], 3),
            'ret_density': num(r['ret_density'], 3),
            'mean_bar_ret': num(r['mean_bar_ret'], 5),
            'mean_day_ret': num(r['mean_day_ret'], 5),
            'day_ret_std': num(r['day_ret_std'], 5),
            'phases': {k: {kk: num(vv, 4) if isinstance(vv, float) else vv
                           for kk, vv in rows[k].items()} for k in rows}}
    head = ('当前相位 %s（%s，以 K 线结束时刻算）；本帧 %d 个交易日、该相位 %d 根：'
            '量能密度 %s×（相位每分钟量 ÷ 全天每分钟量）、波动密度 %s×、'
            '单根均收益 %s%%、日均收益 %s%%（日间 σ %s%%）'
            % (last_phase, r['window'], ndays, r['n_bars'], fmt(r['vol_density'], 2),
               fmt(r['ret_density'], 2), fmt(r['mean_bar_ret'] * 100, 3),
               fmt(r['mean_day_ret'] * 100, 3),
               fmt((r['day_ret_std'] or 0) * 100, 3)))
    # ★"一次取几天"是**按周期算出来的**（`__init__._days_for`：天数 = 指标所需根数 ÷
    #   每日根数，下限 5 天），粗周期拿到的天数本来就比细周期多（30 分钟 12 天 vs
    #   5 分钟 5 天 —— 一次请求 5 天的话 30 分钟只有 40 根，连 BOLL20 都凑不出）。
    #   所以这里**不能写死"5 天"**：写死之后同一句 why 里会出现"本帧 12 个交易日
    #   （分钟线一次取 5 天）"这种自相矛盾的话，而两个数都在同一行、只差几个字。
    note = ('；**样本只有 %d 个交易日**（分钟线一次取 %d 天，按周期反算），'
            '这是对"这一帧"的描述，不是统计推断 —— 经典的口径是成交量 U 型'
            '（开盘/收盘两头高、午盘低），本帧的密度比就是拿它当对照看的' % (ndays, ndays))
    if abs(r['mean_day_ret']) < SESSION_RET_MIN:
        return [_sig('session', period, 'flat', strength,
                     head + note + '，|日均收益| < %.1f%% ⇒ 这个相位的方向就是噪声，不给方向'
                     % (SESSION_RET_MIN * 100), refs)]
    side = 'long' if r['mean_day_ret'] > 0 else 'short'
    return [_sig('session', period, side, strength,
                 head + note + ' ⇒ 相位历史偏%s（%s）' % ('涨' if side == 'long' else '跌', side)
                 + ('；其余相位：' + others if others else ''), refs)]
