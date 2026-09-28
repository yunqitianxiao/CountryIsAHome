#!/usr/bin/env python3
"""目标达成概率 —— 分布来自**历史重排**（moving block bootstrap），不是模型预测。

★ 为什么收益分布**必须**来自历史 bootstrap、而不是 Kronos 采样路径：

  这是本仓库已经跑出来的结论的直接推论。`backtest_kronos.py` 实测 30 天视野上
  方向命中 52.3%（无脑喊涨 50.6%）、MAPE 9.0%（假设不动 4.9%）—— **没有超额能力**。
  而"5 年后能不能攒到 50 万"要的是一条 60 个月的价格路径：拿一个在 30 天上都没赢过
  "不动"的模型，把它外推到 60 个月，得到的是一个**看着最像结论、却一点依据都没有**的数。
  所以这里换一把尺子：**把历史上真实发生过的那些月份重排一遍**。
  它不预测未来（历史分布不等于未来分布，这句必须原样出现在页面上），
  但它给出的是"在**曾经真实出现过**的波动与收益结构下，这件事的概率大概是多少"。

★ 三条硬规矩，每一条都对应一种"看着像结论、其实不可复算"的坏法：

1. **区间档位绝不取中点。**「20-50万」「3000-1万」这类档位是用户自己选的**区间**，
   取中点算一个数出来 = 替用户编了一个他没说过的数（`profile.ts` 里那几档的
   上界甚至是不封顶的，"100万以上"没有中点）。⇒ 一律在档位的**两端**各算一次，
   把结果当成一个**区间**报出去；不封顶的那一档只给**单边下界**，并写明是下界。

2. **同一个问题永远给同一个答案。** 蒙特卡洛默认每次抽的路径不同，页面上刷新两次
   就是两个概率 —— 那读起来像"模型又变了"。⇒ 种子由 `(标的, 月数)` 推出来
   （`zlib.crc32`，不用内置 `hash()`：它对 str 每进程加盐，见 `feed._stub_frame`
   里那段），同一个问题永远重排出**同一批**路径。这也让"月投入越多概率越高"
   变成一条**逐路径严格单调**的性质，而不是两次独立抽样之间的噪声。

3. **样本太短就说太短。** `feed.fetch` 最多给 1200 根日线 ⇒ 约 57 个月收益。
   57 个月里能抽到的"起点"只有 40 来个，所以重排出来的分布**很粗**。
   这一点连同样本月数一起报出去 —— 不给"反正算出来了"的假精度。
"""

import zlib

import numpy as np
import pandas as pd

import feed

# ── 画像档位表 ────────────────────────────────────────────────────────
# ★这几张表与 `web/src/profile.ts` 的 `ASSETS` / `INCOMES` / `HORIZONS`
#   是**同一份东西的两层**。那边是页面上给用户点的选项，这边是把选项翻成数。
#   两边一旦分叉，症状是"用户点了某个档位，算出来的却是另一个区间" ——
#   而且看起来完全正常。所以有一条**双向判据**钉着它们
#   （`verify_agent.check_goal_bands`）：名字必须在两边都在，且一个不多一个不少。
#
# ★`None` = 不封顶。它**不是**一个可以随便填的数：碰到 None 就只算下界，
#   并在结果里标出来（见模块开头第 1 条）。
BANDS_ASSETS = [
    ('1万以下', 0.0, 10000.0),
    ('1-5万', 10000.0, 50000.0),
    ('5-20万', 50000.0, 200000.0),
    ('20-50万', 200000.0, 500000.0),
    ('50-100万', 500000.0, 1000000.0),
    ('100万以上', 1000000.0, None),
]
BANDS_INCOMES = [
    ('1000以下', 0.0, 1000.0),
    ('1000-3000', 1000.0, 3000.0),
    ('3000-1万', 3000.0, 10000.0),
    ('1万以上', 10000.0, None),
]
BANDS_HORIZONS = [
    ('1年以内', 0.0, 1.0),
    ('1-3年', 1.0, 3.0),
    ('3-5年', 3.0, 5.0),
    ('5年以上', 5.0, None),
]

# ── 本站在"怎么重排"上的取舍（不是从哪张表推出来的）──────────────────
# ★包长 12 个月：这是**取的一个值，不是拟合出来的**。理由要能说出来：
#   月收益本身几乎没有自相关，包的意义是把"连续几个月同涨同跌"那段结构带进来；
#   包太短（1）退化成独立同分布抽样、把长期回撤抹平，包太长（>样本/3）就没得抽了。
#   ★换一个包长结论会变——所以它连同样本月数一起写进结果里，不藏在代码里。
BLOCK_MONTHS = 12
# 路径条数。2000 条跑一次是毫秒级（纯 numpy，不碰模型），
# 概率的标准误在 1% 上下 —— 再往上加只是让小数点后两位好看，不改变结论。
DEFAULT_PATHS = 2000
# 少于这么多个月的历史，重排出来的分布不报（3 年）。
MIN_MONTHS = 36
# 代理标的：目标推演要一条"市场"的收益序列。★这是一个**假设**，
#   不是用户的组合 —— 结果里必须原样带上它（`proxy`），页面上也要写。
PROXY = ('cn_index', '000300', '沪深300')
# 确定性推演那几档年化 —— **标称情景**，不是从历史里估出来的（结果里明说是假设）。
ANNUAL_SCENARIOS = (0.0, 0.03, 0.06)
DAYS = 1200                      # feed.fetch 的上限，约 57 个月收益


# ── 档位 → 数 ─────────────────────────────────────────────────────────

def band(label, table):
    """档位名 → `(下界, 上界)`（上界可能是 None）。认不出返回 None。

    ★认不出就返回 None、由调用方明说"不认识这个档位"，**绝不按最近的一档猜** ——
      猜错一档，算出来的是另一个区间，而结果看起来同样正常。
    """
    s = (label or '').strip()
    for name, lo, hi in table:
        if name == s:
            return lo, hi
    return None


def band_names(table):
    return [n for n, _lo, _hi in table]


# ── 历史收益 → 重排 ───────────────────────────────────────────────────

def monthly_returns(df):
    """日线 → 月末收盘的月度收益率（第一条 NaN 丢掉）。

    ★用月末净值算，不是把日收益加起来：分红除权后的价格跳空在日频上是假收益，
      在月频上同一个跳空也只是同一个月里的事，不影响月度口径。
    """
    s = df.set_index('dt')['close'].astype(float).resample('ME').last().dropna()
    return s.pct_change().dropna().to_numpy(dtype=float)


def segments(n_hist, months, block, rng):
    """抽出一串 `(起点, 长度)`，每段都落在 `[0, n_hist)` 内、**不环绕**。

    ★不环绕是刻意的：环形重排会把"最后一个月接回第一个月"这种从未发生过的
      相邻关系造出来，而"连续"正是这里唯一要保住的性质。
    """
    if n_hist <= block:
        raise ValueError('历史只有 %d 个月，装不下 %d 个月的包' % (n_hist, block))
    hi = n_hist - block
    segs, filled = [], 0
    while filled < months:
        L = min(block, months - filled)
        segs.append((int(rng.integers(0, hi + 1)), L))
        filled += L
    return segs


def build_path(rets, segs, months):
    """把抽到的段拼成一条路径。

    ★每一段都是 `rets` 上**连续**的一段 —— "分布来自历史"这句话的全部内容
      就是这条性质。判据直接在产物上验它（把每条路径重新拆回连续段），
      换成任何别的来源（模型采样、拟合的正态、打乱重抽）都会当场变红。
    """
    return np.concatenate([rets[s:s + L] for s, L in segs])[:months]


def sample_paths(rets, months, n_paths=DEFAULT_PATHS, block=BLOCK_MONTHS, seed=None):
    """`(n_paths, months)` 的月度收益率矩阵。种子相同 ⇒ 结果逐位相同。"""
    rng = np.random.default_rng(seed)
    out = np.empty((n_paths, months), dtype=float)
    for k in range(n_paths):
        segs = segments(len(rets), months, block, rng)
        out[k] = build_path(rets, segs, months)
    return out


def seed_of(symbol, months):
    """由 `(标的, 月数)` 定种子。

    ★不用内置 `hash()`：它对 str 每进程加盐（PYTHONHASHSEED），同一份输入在两个
      进程里会重排出两批不同的路径 —— 判据那边就成了"拿 A 去比 B"。
      这条坑 `feed._stub_frame` 里踩过一次，照抄它的解法。
    ★种子里**不含月投入**：同一期限下投入不同，走的必须是**同一批**路径，
      这样"投得多 ⇒ 概率不会更低"是逐路径严格成立的，而不是两次抽样的噪声。
    """
    return zlib.crc32(('%s|%d' % (symbol, months)).encode('utf-8'))


def finals(rets, months, lump, monthly, n_paths, seed):
    """重排 + 按月复利 → 期末金额数组。"""
    R = sample_paths(rets, months, n_paths=n_paths, seed=seed)
    vals = np.full(R.shape[0], float(lump))
    for m in range(months):
        vals = vals * (1.0 + R[:, m]) + float(monthly)
    return vals


def deterministic_fv(lump, monthly, months, annual):
    """按**给定年化假设**推期末金额（确定性，不是概率）。"""
    rm = (1.0 + annual) ** (1.0 / 12.0) - 1.0
    f = float(lump) * (1.0 + rm) ** months
    if abs(rm) < 1e-12:
        f += float(monthly) * months
    else:
        f += float(monthly) * (((1.0 + rm) ** months - 1.0) / rm)
    return f


# ── 主入口 ────────────────────────────────────────────────────────────

def _months_of(horizon_label):
    """期限档位 → `(月数, 是不是单边下界)`。

    ★取档位的**上界**："1-3年"问的是"到 3 年的时候够不够"，不是"到 1 年"。
      不封顶的那档（"5年以上"）取它写明的那个下界，并**标成下界** ——
      时间更长只会让概率更高，所以按 5 年算出来的是保守值。
    ★"1年以内"的上界是 1 年，正好；下界 0 年不是个期限，所以这条规则不用下界。
    """
    b = band(horizon_label, BANDS_HORIZONS)
    if not b:
        return None
    lo, hi = b
    years, open_end = (hi, False) if hi is not None else (lo, True)
    return int(round(years * 12)), open_end


def plan(target, profile, symbol=None, n_paths=DEFAULT_PATHS, fetch=None):
    """算一次目标推演。返回 `{'ok': True, ...}` 或 `{'ok': False, 'reason', 'hint'}`。

    ★`fetch` 是取数口的注入点（`(symbol) -> df`），判据靠它把网络摘掉。
    """
    try:
        tgt = float(target)
    except (TypeError, ValueError):
        tgt = 0.0
    if not (tgt > 0):
        return {'ok': False, 'reason': '没给目标金额（或者给的不是一个正数）',
                'hint': '问用户"你想攒到多少钱"，拿到一个数再算'}

    p = profile if isinstance(profile, dict) else {}
    a = band(p.get('assets'), BANDS_ASSETS)
    i = band(p.get('income'), BANDS_INCOMES)
    h = _months_of(p.get('horizon'))
    if not a or not i or not h:
        miss = [k for k, v in (('可投资资产', a), ('每月可投入', i), ('打算放多久', h))
                if not v]
        return {'ok': False,
                'reason': '画像里这几项没填或认不出来：%s' % '、'.join(miss),
                'hint': '让用户去「我的画像」把这几项选一遍（那是单选档位，'
                        '本站不接受现编的数字）；**不要**替他假设一个金额'}
    months, months_open = h

    sym = (symbol or PROXY[1]).strip()
    get = fetch or _fetch_series
    try:
        df = get(sym)
    except Exception as e:                                   # noqa: BLE001
        return {'ok': False, 'reason': '取不到 %s 的行情：%s' % (sym, str(e)[:80]),
                'hint': '行情源临时挂了，稍后再试；**不要**拿记忆里的行情凑一个概率'}

    rets = df if isinstance(df, np.ndarray) else monthly_returns(df)
    hist_n = len(rets)
    # ★这是**唯一**一道"历史太短就不算"的闸。第一版在取数层那里还有一道
    #   （拿 `_fetch_series` 顺手报回来的月数），两道判的是同一件事 ——
    #   而两道都有的时候，注入只拆掉一道、判据照样绿，等于那条注入是装饰。
    #   ⇒ 一道闸、就地问 `rets`。多出来的那一道不是安全冗余，是**让判据失效**。
    if hist_n < MIN_MONTHS:
        return {'ok': False,
                'reason': '历史只有 %d 个月收益，少于 %d 个月，重排出来的分布没有意义'
                          % (hist_n, MIN_MONTHS),
                'hint': '如实说"这只的历史太短，算不了"，不要用别的标的顶替'}

    seed = seed_of(sym, months)
    # 两端各算一次；不封顶的那一端**不存在**，就用它写明的那个下界，并逐维标出来
    # （见模块开头第 1 条）。逐维标是因为三档可能只有一档不封顶 ——
    # 合成一个总的 open_end 会让"只是投入那档不封顶、资产那档两端都有"
    # 这种最常见的组合丢掉一半区间。
    m_lo, m_hi = i[0], (i[1] if i[1] is not None else i[0])
    a_lo, a_hi = a[0], (a[1] if a[1] is not None else a[0])
    open_end = {'monthly': i[1] is None, 'assets': a[1] is None,
                'months': months_open}
    per = []
    for lump, mon in ((a_lo, m_lo), (a_hi, m_hi)):
        vals = finals(rets, months, lump, mon, n_paths, seed)
        p10, p50, p90 = (float(np.percentile(vals, q)) for q in (10, 50, 90))
        per.append({'lump': round(lump), 'monthly': round(mon),
                    'p_attain': round(float((vals >= tgt).mean()) * 100, 1),
                    # ★把**命中条数**也报出来：概率是 0.0% 时，"2000 条里一条都没到"
                    #   和"算出来正好是 0"是两句话，而 `p_attain` 一个数分不出它们。
                    'n_ok': int((vals >= tgt).sum()),
                    'final_p10': round(p10), 'final_p50': round(p50),
                    'final_p90': round(p90)})

    # 敏感性表：月投入 × **假设**年化。★它和上面那个概率是两回事 ——
    #   这一半是确定性推演（"如果每年正好 3%"），不是概率，标签必须分开。
    # ★本金固定在资产档位的**下界**（`a_lo`），并把这一格用的本金一起写进每行 ——
    #   不写的话，这张表看着像"用户的资产"，其实是三个假设叠在一起
    #   （下界本金 + 指定月投入 + 指定年化），而表里只写得出后两个。
    sens = []
    for mon in sorted({m_lo, m_hi}):
        for ann in ANNUAL_SCENARIOS:
            sens.append({'lump': round(a_lo), 'monthly': round(mon),
                         'annual_pct': round(ann * 100, 1),
                         'final': round(deterministic_fv(a_lo, mon, months, ann))})

    return {'ok': True, 'target': round(tgt), 'proxy': sym,
            'proxy_name': PROXY[2] if sym == PROXY[1] else '',
            'months': months, 'monthly': [round(m_lo), round(m_hi)],
            'assets': [round(a_lo), round(a_hi)],
            # ★两端一对，**不是**一个数：档位是区间，概率也是区间。
            'p_attain_pct': [per[0]['p_attain'], per[1]['p_attain']],
            'open_end': open_end, 'corners': per,
            # ★把"哪一维是单边的"翻成人话再发出去。留一堆布尔值让大模型自己拼，
            #   它八成会漏说 —— 而这一句漏掉，用户读到的就是"概率就是这么多"。
            'bound_note': (
                '★这几项是按**档位写明的下界**算的：%s（%s）。'
                '投入更多、时间更长只会让它更容易达成 ⇒ 上面这个概率是**下界**，'
                '不是"就是这么多"。'
                % ('、'.join(n for k, n in (('monthly', '每月可投入'),
                                            ('assets', '可投资资产'),
                                            ('months', '期限')) if open_end.get(k)),
                   '；'.join('%s ≥ %s' % (n, v) for k, n, v in (
                       ('monthly', '每月', '%d 元' % m_lo),
                       ('assets', '资产', '%d 元' % a_lo),
                       ('months', '期限', '%d 个月' % months)) if open_end.get(k)))
                if any(open_end.values()) else None),
            'hist_months': hist_n, 'block_months': BLOCK_MONTHS,
            'paths': int(n_paths), 'sensitivity': sens}


def _fetch_series(symbol):
    """认代码 → 取日线。返回日线表；认不出就抛。"""
    r = feed.resolve(symbol)
    if not r.get('ok'):
        raise ValueError(r.get('hint') or '认不出这个标的')
    if r.get('ambiguous'):
        raise ValueError('「%s」有歧义，得先问清是哪一个' % symbol)
    return feed.fetch(r['market'], r['code'], days=DAYS)


if __name__ == '__main__':
    import json
    import sys
    prof = {'assets': '5-20万', 'income': '1000-3000', 'horizon': '1-3年'}
    print(json.dumps(plan(float(sys.argv[1]), prof), ensure_ascii=False, indent=1))
