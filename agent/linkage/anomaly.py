"""异动检测：一整个市场的实时快照 → 异动榜。

★★这个模块最要紧的一个选择：**z 分数用"同日横截面"的分布定，不用这只票自己的历史。**

  为什么：

  - **横截面天然把"今天整个大盘在动"这件事归一化掉了。** 假设今天大盘跌 3%，
    那么几乎每只票的涨跌幅都会平移 -3%。用**自己的历史**算 z 的话，全市场
    5000 只票里会有 2500 只报出"显著下跌" —— 而它真正想说的其实是"大盘跌了"。
    用**今天的横截面**算，大盘那一块对每一只票都一样，减掉之后剩下的才是
    "它相对同行多动了多少"，也就是"异动"这个词本来的意思。
  - 反过来说，横截面 z **答不了**"这只票跟它自己比算不算异常"（那是一个合理的、
    但**不一样**的问题，需要它自己的历史，是 `agent/indicators.py` 那一层的事）。
    两个问题混在一个数里最危险，所以这里只做前者，名字里也只写前者。

  代价（写出来，别让人以为它免费）：
  - 横截面的 sd 里含**个股自己的**异动。一只涨停票会把 sd 抬高一点点，
    于是它自己的 z 被压低一点点。n=320 时是 1/320 量级，可以忽略；
    n 很小的时候不能忽略 ⇒ `MIN_CROSS_N` 以下**不报 z**。
  - 涨跌幅的横截面是**厚尾**的（涨跌停、停牌复牌）。均值/标准差对厚尾不稳健。
    这里仍然用均值/标准差：本模块的纪律是"最朴素的一层"，而稳健化（median/MAD）
    会引入一个"你用的是哪个估计量"的新问题。★如果哪天要换成 MAD，阈值必须跟着
    重标（正态下 sd≈1.4826·MAD，直接沿用 2.5 会变成完全不同的门槛）。

★"量比 / RVOL"**不在这里**：它要这只票自己的历史（当日量 ÷ 过去 N 日均量），
  是逐只取数的活，所以放在 `rvol()` 里由上层按预算调用（见 collect 第 2 条纪律）。
"""

import numpy as np
import pandas as pd

from .collect import _num

# ── 阈值（★这些是**政策常数**，不是实测分位）──────────────────────────────
# 为什么强调这件事：这个项目里"实测数字要带日期和机器"是硬规矩。下面这几个数
# **没有**实测来源 —— 它们是从"榜容量"倒推的（正态下 |z|≥2.5 单边 0.62%，
# 5000 只里约 31 只，一个榜单正好吃得下）。写成"实测 P99 分位"是不诚实的，
# 因为我们没在真数据上量过。取到真分位之前，这里就写"政策常数"。
Z_PCT_HOT = 2.5          # 涨跌幅 z
Z_VOL_HOT = 3.0          # 成交量 z（★见下：量能 z 天生偏弱，阈值给得比价格高）
Z_AMT_HOT = 3.0          # 成交额 z
RVOL_HOT = 2.0           # 量比（当日量 ÷ 过去 20 日均量）
TURNOVER_HOT = 5.0       # 换手率（%）
AMP_HOT = 7.0            # 振幅（%）

MIN_CROSS_N = 100        # 横截面样本少于此数，z 一律**不报**（不假精确）
AMOUNT_YI_FLOOR = 1.0    # 成交额（亿元）低于它的票不进榜：几百万成交额的票
#                          量比天然是 5 倍起步，放进榜单只会把真异动挤掉
RVOL_WIN = 20            # 量比的基准窗口（交易日）—— A 股一个月的交易日
RVOL_MIN_DAYS = 10       # 基准窗口里实际的日数少于此数 ⇒ 量比不报（半个月都不到）

KIND_ORDER = ('pct_spike', 'volume_spike', 'amount_spike', 'turnover_spike', 'amplitude_wide')


# ── 横截面分布 ──────────────────────────────────────────────────────────

def _stats(vals):
    v = np.asarray([float(x) for x in vals if x is not None and np.isfinite(float(x))])
    if len(v) < MIN_CROSS_N:
        return None
    return {'n': int(len(v)), 'mu': float(v.mean()), 'sd': float(v.std(ddof=1))}


def cross_section(rows):
    """一批快照行 → 每个字段的 (n, mu, sd)。**分市场**算，理由见文件头。

    ★返回里**只带得动的字段**：算不出的（样本不够、或该字段整列为空）不在里面。
      上层看到字段缺席就知道"这个 z 没算"，而不是看到一个 0。
    """
    by_mkt = {}
    for r in rows or []:
        by_mkt.setdefault(r.get('market') or '?', []).append(r)
    out = {}
    for mk, rs in by_mkt.items():
        d = {
            'pct': _stats([r.get('pct') for r in rs]),
            'volume': _stats([r.get('volume') for r in rs]),
            'amount': _stats([r.get('amount') for r in rs]),
            'turnover': _stats([r.get('turnover') for r in rs]),
        }
        # 振幅不在快照的冻结字段里，是**算**出来的：(high-low)/prev_close。
        # ★算不出（缺字段）就是缺席，不补 0 —— 补 0 会得到"振幅 0%，一点都不异动"，
        #   那是个看着正常的错答案。
        amp = []
        for r in rs:
            a = _amplitude(r)
            if a is not None:
                amp.append(a)
        d['amplitude'] = _stats(amp)
        out[mk] = {k: v for k, v in d.items() if v}
    return out


def _amplitude(row):
    hi, lo, pc = row.get('high'), row.get('low'), row.get('prev_close')
    try:
        hi, lo, pc = float(hi), float(lo), float(pc)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(hi) and np.isfinite(lo) and np.isfinite(pc)) or pc <= 0:
        return None
    return (hi - lo) / pc * 100.0


def z_of(value, st):
    """→ z，或 None。★样本不够/该字段没统计/值是 None ⇒ None。**不是 0**。"""
    if value is None or st is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v) or st['sd'] <= 1e-12:
        return None
    return (v - st['mu']) / st['sd']


# ── 量比（要历史，逐只取）──────────────────────────────────────────────

def rvol(hist_vols, today_vol, win=RVOL_WIN):
    """→ {'value','win','n_used','why'}。`hist_vols` 必须是**今天之前**的日成交量。

    ★"今天之前"这件事**由调用方保证**：错位一天算出来的量比看着完全正常
      （1.8 倍、2.3 倍都像个数），但那个数说的是别的日子。所以这里只做算术，
      日期对不对齐由 `__init__.scan` 判 —— 那一步有快照日期可以比。
    """
    try:
        tv = float(today_vol)
    except (TypeError, ValueError):
        return {'value': None, 'win': win, 'n_used': 0, 'why': '快照没有当日成交量'}
    h = [float(x) for x in (hist_vols or []) if x is not None and np.isfinite(float(x))]
    h = h[-win:]
    if len(h) < RVOL_MIN_DAYS:
        return {'value': None, 'win': win, 'n_used': len(h),
                'why': '基准窗口只有 %d 天（要至少 %d 天）' % (len(h), RVOL_MIN_DAYS)}
    base = float(np.mean(h))
    if base <= 0:
        return {'value': None, 'win': win, 'n_used': len(h), 'why': '基准成交量是 0'}
    return {'value': tv / base, 'win': win, 'n_used': len(h), 'why': None}


def rvol_from_daily(df, today_vol, day, win=RVOL_WIN):
    """日线 + 当日成交量 → 量比，**把日期对齐一起做完**。→ 同 `rvol()` 的 dict + `partial`。

    ★**这是"哪根 bar 是今天"的唯一实现**：异动票自己（`__init__._attach_rvol`）
      和它的每个 peer（`relate.links_for`）都走这一条。
      两处各写一份的话，"今天"迟早会分叉，而分叉之后两边算出的量比
      **都长得像个正常的数**（1.8 倍 / 2.1 倍），没有任何东西会告诉你它们说的不是同一天。

    `partial=True` 的含义：日线的最后一根**不是今天**（盘中很常见），
      于是当日量比的是"过去 win 天的均量" —— 这个数**天然偏低**（今天的量还没走完），
      必须跟着一起报出去，不许悄悄给个数。
    """
    try:
        # ★dt 和 volume 要**一起** dropna：分开 dropna 之后两列长度会不一样，
        #   于是"最后一天的成交量"其实配的是别的日子（本项目 portfolio.py 的老账：
        #   分开 dropna 让两列错位，而错位之后的读数看着完全正常）。
        d = pd.DataFrame({'dt': pd.to_datetime(df['dt'], errors='coerce'),
                          'v': pd.to_numeric(df['volume'], errors='coerce')}).dropna()
    except Exception as e:                                   # noqa: BLE001
        return {'value': None, 'win': win, 'n_used': 0, 'partial': None,
                'why': '日线读不出日期/成交量（%s）' % type(e).__name__}
    if not len(d):
        return {'value': None, 'win': win, 'n_used': 0, 'partial': None, 'why': '日线是空的'}
    dts = [x.strftime('%Y-%m-%d') for x in d['dt']]
    vols = [float(x) for x in d['v']]
    day = str(day or '')[:10]
    partial = bool(day) and dts[-1] != day
    r = rvol(vols[:-1] if not partial else vols[-win:], today_vol, win)
    r['partial'] = bool(partial) if r['value'] is not None else None
    r['last_day'] = dts[-1]
    return r


# ── 检出 ────────────────────────────────────────────────────────────────

def kinds_of(row, cs, rvol_val=None):
    """→ 命中的标签列表（可多标签）+ 算出来的 metrics。

    ★量能这一族（z_vol / z_amt）**天生比价格弱**，这一点必须写下来：
      成交量的横截面分布是重尾的（对数正态），而成交额还额外被市值驱动 ——
      "成交额 z=3"经常只说明这是只大票，不说明有人在抢。所以：
        · 价格 z 的门槛是 2.5，量能 z 的门槛是 3.0（量能给得更严）；
        · `AMOUNT_YI_FLOOR` 把微盘票挡在外面；
        · 排序时量能的权重只有价格的一半（见 `hot`）。
      这三条都是同一个判断的三个落点，不要只改一个。
    """
    st = cs.get(row.get('market') or '?') or {}
    z_pct = z_of(row.get('pct'), st.get('pct'))
    z_vol = z_of(row.get('volume'), st.get('volume'))
    z_amt = z_of(row.get('amount'), st.get('amount'))
    amp = _amplitude(row)
    turn = row.get('turnover')
    amount_yi = None
    try:
        if row.get('amount') is not None:
            amount_yi = float(row['amount']) / 1e8
    except (TypeError, ValueError):
        amount_yi = None

    ks = []
    if z_pct is not None and abs(z_pct) >= Z_PCT_HOT:
        ks.append('pct_spike')
    if (z_vol is not None and z_vol >= Z_VOL_HOT) or (rvol_val is not None and rvol_val >= RVOL_HOT):
        ks.append('volume_spike')
    if z_amt is not None and z_amt >= Z_AMT_HOT:
        ks.append('amount_spike')
    if turn is not None and float(turn) >= TURNOVER_HOT:
        ks.append('turnover_spike')
    if amp is not None and amp >= AMP_HOT:
        ks.append('amplitude_wide')

    m = {
        'pct': _num(row.get('pct'), 2),
        'vol_ratio': _num(rvol_val, 2),
        'amount_yi': _num(amount_yi, 2),
        'turnover': _num(turn, 2),
        'amplitude': _num(amp, 2),
        'z_pct': _num(z_pct, 2),
        'z_vol': _num(z_vol, 2),
        'z_amount': _num(z_amt, 2),
        'price': _num(row.get('price'), 2),
        'volume': _num(row.get('volume'), 0),
        # 方向单独给一个字段：标签 `pct_spike` 是**不带方向**的（涨和跌都叫 spike），
        # 方向靠这里带。这样标签表是闭合的、不用为方向再加两个词。
        'side': ('up' if (row.get('pct') or 0) > 0 else
                 'down' if (row.get('pct') or 0) < 0 else 'flat'),
    }
    return ks, m


def hot(m):
    """排序分。★权重是**政策**，理由：一个纯量异动（z_vol=4、z_pct=0.2）得 2.0 分，
    一个纯价格异动（z_pct=3、z_vol=0）得 3.0 分 —— 价格异动排在前面。
    量价齐动（3+2=5）再排在两者前面。这是"价格变化比成交量变化更值得看"这一条
    判断的数值化，不是拟合出来的。"""
    return (abs(m.get('z_pct') or 0.0)
            + 0.5 * max(0.0, m.get('z_vol') or 0.0)
            + 0.5 * max(0.0, m.get('z_amt') or 0.0))


def admit(row, m, ks):
    """要不要进榜。→ (bool, 理由)。

    ★成交额的"地板"只在**量得出来**的时候执行。成交额取不到的票（源没给这一列）
      不能因为这个就被一票否决 —— 但也不能凭一个弱标签混进来，
      所以对它们的要求是"必须有一个价格级的命中"。
    """
    if not ks:
        return False, '没有命中任何标签'
    if m['pct'] is None:
        return False, '快照没有涨跌幅，什么都判不了'
    if m['amount_yi'] is None:
        if abs(m.get('z_pct') or 0.0) < Z_PCT_HOT:
            return False, '成交额取不到，且没有价格级命中（只有弱标签）'
        return True, None
    if m['amount_yi'] < AMOUNT_YI_FLOOR:
        return False, '成交额 %.2f 亿，低于 %.1f 亿的地板' % (m['amount_yi'], AMOUNT_YI_FLOOR)
    return True, None


def detect(rows, cs, rvol_by_code=None):
    """快照行 → 异动条目（**未排序**）。`rvol_by_code` 是深挖阶段补上的量比。"""
    rvol_by_code = rvol_by_code or {}
    out = []
    for r in rows or []:
        code = r.get('code')
        ks, m = kinds_of(r, cs, rvol_by_code.get(code))
        ok, why = admit(r, m, ks)
        if not ok:
            continue
        # 标签按固定顺序输出：同一只票两次 scan 的 kinds 顺序必须一样，
        # 否则前端那张表的列会跳、判据里的字符串比对会假红。
        ks = [k for k in KIND_ORDER if k in ks]
        out.append({
            'code': code, 'name': r.get('name'), 'market': r.get('market'),
            'kinds': ks, 'metrics': m, 'hot': _num(hot(m), 2),
            'ts': r.get('ts'), 'why_not': None,
        })
    out.sort(key=lambda a: (-(a['hot'] or 0.0), a['code']))
    return out
