#!/usr/bin/env python3
"""组合体检的**算数**部分：持仓 → 指标。判档一概不在这里（那是 `portfolio_rules.grade`）。

★ 这个模块存在的理由，就是"**数字必须由代码算**"这一条：

`skills/portfolio-health-check` 是纯文本框架（阈值表 + 报告模板）。把它塞进提示词、
让大模型拿着持仓去判集中度和相关性，等于让它**眼估** —— 一句话里出现"你的前五大
持仓占 61%，属于集中"，而 61% 是它看着数字猜的，用户没有任何办法分辨。
所以：**skill 给阈值和流程，这个模块算每一个数**，`score.py` 那条"只有一把尺子"
在这里的对应物是"**只有一处算法**"。

★ 三条硬规矩，每一条都对应一种"看着像结论、其实不可复算"的坏法：

1. **一条取不到，只坏那一条，而且必须报出来。**
   静默丢掉一只再重算权重，等于**悄悄改掉了组合的集中度** ——
   一份 5 只的组合丢了一只，前五大占比会凭空变好看，而页面上看不出发生过什么。
   所以 `dropped` 是要给用户看的产物，不是日志。

2. **多标的必须按交易日对齐，且不许前向填充后当真实收益。**
   沪深港美的交易日不同，停牌的日子也没有行情。前向填充会把"没交易"变成
   "涨跌 0%"，于是**波动率被系统性压低、相关性被系统性抬高** ——
   而这两样正是体检要判的东西。⇒ 取**内连接**：只保留所有标的都有行情的日子。
   代价是样本变少，那就把变少这件事**说出来**（`corr_weeks`），而不是补出来。

3. **样本不够就说不够。** 一只持仓谈不上相关性；两只也测不出"≥3 只的聚集"。
   这种情况必须走到 `clusters=None`（"**没测**"），
   **不许**退化成一个"分散良好"——那是拿"没算"冒充"没问题"。

★ 本机没有 scipy/sklearn（实测只有 numpy 2.2.6 + pandas 2.3.3），所以层次聚类
  是自己写的。N ≤ 20，写得动，也不必为此引一个包。
"""

import numpy as np
import pandas as pd

import feed
import portfolio_rules as rules

# 取数窗口。skill 要"过去 CLUSTER_YEARS 年"的周收益 ⇒ 约 250 根/年；多取一些留对齐的余量。
# ★从 `CLUSTER_YEARS` **推**出来，不写死：写在两处的同一个数必然有一处会烂
#   （改了 skill 的回看年数、这里忘了跟，症状是"算出来的相关用的是 3 年前的行情"）。
# ★`feed.fetch` 把 days 夹在 [60, 1200]，3*250+150 = 900 在范围内。
DAYS = rules.CLUSTER_YEARS * 250 + 150

BENCH = ('cn_index', '000300', '沪深300')

# ── ★本站自己的政策，不是 skill 的阈值（skill 里没有这几条）────────────────
# 放在这里而不是 `portfolio_rules.py`，是因为那个文件的每一条都要能在 skill 的 md 里
# 数到（三向比对）。这几条是本站在"数据不全时怎么办"上的取舍，md 里没有对应文字。
MIN_ROWS = 40            # 对齐后不足这么多根日线，波动/回撤不报（太少的标准差不是标准差）
# 周收益点少于这么多 ⇒ 相关系数不报（一条噪声曲线）。
# ★这是**能不能算**的底线（一年），不是 skill 要的 3 年 —— 拿不到 3 年时照样算，
#   但把实际拿到多少周报出来（`corr_weeks`），并在 `notes` 里明说差多少。
MIN_WEEKS = 52
MIN_SURVIVING = 60.0     # 活下来的权重低于这个百分比 ⇒ 整份报告不出


def _fetch_one(h, days):
    """认代码 → 取日线。返回 (ok_dict, None) 或 (None, 坏在哪)。"""
    sym = (h.get('symbol') or '').strip()
    if not sym:
        return None, '没给代码'
    r = feed.resolve(sym)
    if not r.get('ok'):
        return None, r.get('hint') or '认不出这个代码'
    if r.get('ambiguous'):
        cands = r.get('candidates') or []
        return None, ('有歧义，得指明是哪一个：%s'
                      % '、'.join(str(c.get('code') or c) for c in cands[:4]))
    try:
        df = feed.fetch(r['market'], r['code'], days=days)
    except feed.SourceError as e:
        # ★把源给的 code 一起带上：ALL_SOURCES_FAILED（源挂了，可以重试）和
        #   BAD_MARKET（我们自己写错了市场名）对用户是两件事。
        return None, '%s（%s）' % (e, e.code)
    except Exception as e:                                       # noqa: BLE001
        return None, '%s: %s' % (type(e).__name__, str(e)[:80])
    return {'symbol': sym, 'market': r['market'], 'code': r['code'],
            'name': r.get('name') or h.get('name') or sym,
            'df': df[['dt', 'close']].copy()}, None


def _align(series_by_code):
    """按交易日**内连接**对齐。返回 (宽表, 说明)。

    ★为什么是内连接而不是外连接 + 前向填充：见文件头第 2 条。这里只补一句
    "代价"：外连接会把不同市场的节假日变成一行"价格没动"，那既不是真实收益，
      又同时压低波动、抬高相关 —— 正好把体检要判的两样都污染掉。
    """
    if not series_by_code:
        return None, '一只都没取到'
    merged = None
    for code, s in series_by_code.items():
        d = s.rename(columns={'close': code})
        merged = d if merged is None else merged.merge(d, on='dt', how='inner')
    merged = merged.sort_values('dt').reset_index(drop=True)
    return merged, None


def _max_drawdown(nav):
    """净值序列 → 最大回撤（负的百分数）。"""
    peak = nav.cummax()
    return float((nav / peak - 1.0).min())


def _weekly_returns(frame):
    """对齐后的日线 → 周收益（每周最后一个交易日的收盘价，算 pct_change）。

    ★"周"不是随手选的：日收益的相关性天然偏高（同涨同跌的噪声），
      skill 特意规定用周，改成日会让几乎所有持仓看起来都"聚集"。
    """
    w = frame.set_index('dt').resample('W').last().dropna(how='any')
    return w.pct_change().dropna(how='any')


def _linkage_merges(corr):
    """平均链接层次聚类：返回**合并序列** `[(成员索引, 合并时的链接相关), …]`，从最小的簇开始。

    ★为什么手写：本机没有 scipy/sklearn（见文件头）。
    ★为什么不直接用"相关 > 门槛"的连通分量：连通分量**会链式粘连** ——
      A~B、B~C 相关都过线、A~C 不过线，它照样把三只算成一群。

    ★★为什么返回"合并序列"而不是"在某个高度切一刀的簇"：**因为那两件事不一样，
      而先切一刀会把 skill 的定义挡在门外**。实测踩到过：

          三只两两相关 0.87 / 0.661 / 0.713 ⇒ 簇内平均 0.748
          平均链接在这一步的**链接值** = mean(0.661, 0.713) = 0.687 ≤ 0.70 ⇒ 不合并
          ⇒ 只报出一个两只的簇，而"三只一组"从没被拿出来判过 ⇒ **静默少报**。

      而 skill 的定义是「≥3 个仓位**平均两两相关系数** > 0.70」——
      说的是**簇内平均**，不是链接值。两者在 2-vs-1 这一步必然不等
      （3 对里链接值只用到其中 2 对）。⇒ 让聚类只负责**给出候选分组和顺序**，
      "算不算聚集"一律由调用方按定义判（`_clusters` 里那一步）。
    """
    n = len(corr)
    clusters = [[i] for i in range(n)]
    merges = []
    while len(clusters) > 1:
        best = None
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                c = float(np.mean([corr[i][j] for i in clusters[x]
                                   for j in clusters[y]]))
                if best is None or c > best[0]:
                    best = (c, x, y)
        c, x, y = best
        merged = clusters[x] + clusters[y]
        merges.append((merged, round(c, 3)))
        clusters[x] = merged
        del clusters[y]
    return merges


def _clusters(frame_w, codes, weights):
    """周收益 → 聚集列表。★不够算就返回 (None, 原因)，**不返回 []**。

    `None`（没测）与 `[]`（测了、没发现）是两回事，`portfolio_rules.grade`
    对前者要明说"算不了"，对后者才什么都不报。
    """
    if len(codes) < rules.CLUSTER_MIN_N:
        return None, ('只有 %d 只持仓，skill 的聚集定义要求至少 %d 只'
                      % (len(codes), rules.CLUSTER_MIN_N))
    if len(frame_w) < MIN_WEEKS:
        return None, ('对齐后的周收益只有 %d 个点（要至少 %d 个）'
                      % (len(frame_w), MIN_WEEKS))
    corr = frame_w.corr()
    cm = corr.values
    # ★★判据是 skill 的**定义**，聚类只负责给候选和顺序。
    # 从最小的合并开始走：第一个满足定义的簇就收，收下后不再往上长
    # （否则"3 只已经够格"会被淹进后面更大的簇里，报出来的是 5 只而不是那 3 只）。
    claimed, out = set(), []
    for idx, _link in _linkage_merges(cm):
        if len(idx) < rules.CLUSTER_MIN_N or (claimed & set(idx)):
            continue
        pairs = [cm[i][j] for a, i in enumerate(idx) for j in idx[a + 1:]]
        avg = float(np.mean(pairs))
        if avg <= rules.CLUSTER_MIN_CORR:
            continue
        # ★注意这里的后果，是**故意**的、也是要让人看见的：宽基 ETF 与大盘股的相关
        #   普遍在 0.7 上下，所以"一只宽基 + 几只大盘股"会按定义合成一个簇。这不是
        #   算法出错 —— 它们确实一起动。但它会让"我分散了"这句话变得可疑，正是
        #   体检要说的那句话。要改的是**阈值**（skill 的事），不是这里偷偷加个例外。
        claimed |= set(idx)
        out.append({'n': len(idx),
                    'codes': [codes[i] for i in idx],
                    'avg_corr': round(avg, 3),
                    'weight': round(float(sum(weights[codes[i]] for i in idx)), 1)})
    out.sort(key=lambda c: -c['weight'])
    return out, None


def _industry(holdings):
    """行业分布。★只对 A 股个股成立，其余**明说没有**，绝不塞进某个行业充数。

    `feed.fetch_fundamentals` 在 ETF/指数上的失败方式不止一种（空表 / TypeError），
    由它自己逐块接住；这里只按 market 决定**要不要问**。
    """
    dist, missing = {}, []
    for h in holdings:
        if h['market'] != 'cn_stock':
            missing.append({'name': h['name'],
                            'why': '不是 A 股个股（%s），没有公司概况里的行业' % h['market']})
            continue
        try:
            f = feed.fetch_fundamentals(h['code'])
        except Exception as e:                                   # noqa: BLE001
            missing.append({'name': h['name'], 'why': '取行业失败：%s' % str(e)[:60]})
            continue
        ind = f.get('industry') if f.get('ok') else None
        if not ind:
            missing.append({'name': h['name'], 'why': '接口没回行业'})
            continue
        dist.setdefault(ind, []).append(h['name'])
    return dist, missing


def analyze(holdings, risk=None, days=DAYS):
    """持仓 → 指标 + 判档。返回的 `metrics` 直接喂 `portfolio_rules.grade`。

    输入 `holdings`：[{'symbol', 'name', 'weight'}]，weight 是**百分数**。
      实际调用时由前端把游戏页的 `amount` 归一化成权重传进来（见方案 1.1）。

    返回：
      ok=False 时只带 `reason`（残缺到不该给结论）
      ok=True  时带 `metrics` / `grades` / `dropped` / `notes` / `series` / `corr`
    """
    holds = [h for h in (holdings or []) if (h.get('symbol') or '').strip()]
    if not holds:
        return {'ok': False, 'reason': '没有持仓 —— 先在「模拟投资」里买几笔再来体检'}
    # 权重归一化：前端传的可能是金额，也可能已经归一化过。这里一律重算，
    # 免得"传进来的和加起来不是 100"这种事在后面每一步里各错一遍。
    tot = sum(float(h.get('weight') or 0) for h in holds)
    if tot <= 0:
        return {'ok': False, 'reason': '这些持仓的权重加起来是 0，算不了占比'}
    for h in holds:
        h['weight'] = float(h.get('weight') or 0) / tot * 100.0

    # ── ① 逐条取数 ──────────────────────────────────────────────────────
    got, dropped = [], []
    for h in holds:
        ok, why = _fetch_one(h, days)
        if ok is None:
            dropped.append({'symbol': h.get('symbol'), 'why': why})
        else:
            ok['want_weight'] = h['weight']
            got.append(ok)
    if not got:
        return {'ok': False, 'dropped': dropped,
                'reason': '一只都没取到行情，体检做不了（%d 只全被挡在外面）' % len(dropped)}

    # ── ② 对齐 ──────────────────────────────────────────────────────────
    frame, err = _align({g['code']: g['df'] for g in got})
    if frame is None:
        return {'ok': False, 'dropped': dropped, 'reason': err}
    codes = [g['code'] for g in got]
    frame = frame[['dt'] + codes]
    # 只保留所有标的都有行情的日子 —— 对齐后可能短一截，短多少下面会报出来。
    rows = len(frame)
    if len(frame) < 2:
        return {'ok': False, 'dropped': dropped,
                'reason': '对齐后只剩 %d 个共同交易日，几只标的的交易日几乎没有重叠' % rows}

    # ── ③ 活下来的权重（★丢掉的那几只必须体现在占比上，并说出来）────────
    survived = float(sum(g['want_weight'] for g in got))
    weights = {g['code']: g['want_weight'] / survived * 100.0 for g in got}
    if survived < MIN_SURVIVING:
        return {'ok': False, 'dropped': dropped,
                'reason': ('只有 %.1f%% 的权重取到了行情（低于 %.0f%%）—— '
                           '这个比例下算出来的集中度已经不是你的组合了，不出一份'
                           '看着像结论的残缺报告' % (survived, MIN_SURVIVING))}

    notes = []
    if dropped:
        notes.append('这 %d 只没取到，已从计算中**排除**（权重按剩下的重新归一化）：'
                     % len(dropped))
    if rows < len(got[0]['df']):
        notes.append('几只标的的交易日不同，按**内连接**对齐后是 %d 个共同交易日'
                     '（最短的那只 %d 根）—— 没有前向填充，停牌/休市的日子宁可不算。'
                     % (rows, min(len(g['df']) for g in got)))

    # ── ④ 净值 / 波动 / 回撤 ────────────────────────────────────────────
    rets = frame[codes].pct_change().dropna(how='any')
    w = np.array([weights[c] for c in codes])
    port_ret = pd.Series(rets.values @ w / 100.0, index=rets.index)
    nav = (1.0 + port_ret).cumprod()

    metrics = {}
    if rows >= MIN_ROWS:
        vol = float(port_ret.std(ddof=1) * np.sqrt(252) * 100)
        metrics['vol_annual'] = round(vol, 1)
        metrics['mdd'] = round(_max_drawdown(nav) * 100, 1)
        # 单只波动
        ind_vol = {c: float(rets[c].std(ddof=1) * np.sqrt(252) * 100) for c in codes}
        metrics['vol_by_code'] = {c: round(v, 1) for c, v in ind_vol.items()}
        # EDR = 各持仓波动率之和 ÷ 组合波动率
        # ★数学上恒 ≥ 1（完全正相关时取等号），所以 <1 只可能是算错了 —— 不掩盖，
        #   报出来让判据去红。
        metrics['edr'] = round(sum(ind_vol.values()) / vol, 3) if vol > 1e-9 else None
    else:
        notes.append('对齐后只有 %d 个交易日（要 %d 个），波动率/回撤/EDR 这几项没算'
                     % (rows, MIN_ROWS))

    # ── ⑤ 集中度 ────────────────────────────────────────────────────────
    metrics['weights'] = [{'symbol': g['symbol'], 'name': g['name'],
                           'weight': round(weights[c], 2)}
                          for g, c in zip(got, codes)]
    ordered = sorted((weights[c] for c in codes), reverse=True)
    metrics['top5'] = round(float(sum(ordered[:5])), 2)
    metrics['hhi'] = round(float(sum((weights[c] / 100.0) ** 2 for c in codes)), 4)
    metrics['n_holdings'] = len(codes)          # ★真正进了计算的那几只

    # ── ⑥ 相关性 + 聚集 ─────────────────────────────────────────────────
    frame_w = _weekly_returns(frame)
    metrics['corr_weeks'] = int(len(frame_w))
    # skill 要"过去 3 年"的周收益。拿不到就**照算但说出来** —— 悄悄用 1 年数据
    # 冒充 3 年，是这份报告里最容易糊过去、也最影响结论的一条。
    want_weeks = 52 * rules.CLUSTER_YEARS
    if metrics['corr_weeks'] < want_weeks:
        notes.append('相关用的是 **%d 周**收益，skill 要的是过去 %d 年（≈%d 周）—— '
                     '样本比它短，相关系数的抖动会比它大。'
                     % (metrics['corr_weeks'], rules.CLUSTER_YEARS, want_weeks))
    clusters, why_not = _clusters(frame_w, codes, weights)
    metrics['clusters'] = clusters
    if why_not:
        notes.append('相关性聚集**没测**：%s。这不是"没发现聚集"。' % why_not)

    # ── ⑦ Beta（对沪深300）──────────────────────────────────────────────
    metrics['beta'] = None
    try:
        bdf = feed.fetch(BENCH[0], BENCH[1], days=days)[['dt', 'close']]
        # ★把基准**并进 frame 再一起算收益**，不要各自 `pct_change` 完再对齐行数 ——
        #   两条序列各自 dropna 之后长度**必然差 1**（都掉首行，但起点不同），
        #   于是 `len(p2) == len(b2)` 永远为假 ⇒ Beta **恒为 None**。
        #   实测踩过：三条持仓和沪深300 有 900 个共同交易日，Beta 照样报"共同交易日不够"。
        mm = (frame.merge(bdf.rename(columns={'close': '_bench'}), on='dt', how='inner')
                   .sort_values('dt').reset_index(drop=True))
        if len(mm) >= MIN_ROWS + 1:
            rr = mm[codes + ['_bench']].pct_change().dropna(how='any')
            if len(rr) >= MIN_ROWS:
                p2 = rr[codes].values @ w / 100.0
                b2 = rr['_bench'].values
                var = float(np.var(b2, ddof=1))
                if var > 1e-12:
                    metrics['beta'] = round(float(np.cov(p2, b2, ddof=1)[0][1] / var), 3)
                    metrics['beta_days'] = int(len(b2))
        if metrics['beta'] is None:
            notes.append('Beta 没算出来：和沪深300 的共同交易日不够（要 %d 个）' % MIN_ROWS)
    except feed.SourceError as e:
        notes.append('Beta 没算出来：沪深300 取数失败（%s）' % e)
    except Exception as e:                                       # noqa: BLE001
        notes.append('Beta 没算出来：%s: %s' % (type(e).__name__, str(e)[:60]))

    # ── ⑧ 压力测试 ──────────────────────────────────────────────────────
    # ★这不是"预测会跌多少"，是一条**线性外推**：指数跌 X% ⇒ 组合大约跌 beta*X%。
    #   它假设冲击中 beta 不变，而真实市场里**跌的时候相关性上升、beta 会变大**
    #   ⇒ 这个数**偏乐观**。所以 caveat 和数一起给 —— 只给数就成了算命。
    #   beta 算不出来时**不给**，而不是拿 1.0 顶替（拿 1 顶替等于宣称"跟指数一模一样"）。
    if metrics.get('beta') is not None:
        metrics['stress'] = {
            'basis': 'beta_linear', 'beta': metrics['beta'],
            'index_shock': [-10, -20],
            'port_shock': [round(metrics['beta'] * s, 1) for s in (-10, -20)],
            'hist_worst': metrics.get('mdd'),
            'caveat': '线性外推（假设 beta 不变）。跌的时候相关性会上升、beta 会变大，'
                      '**实际可能比这个更差**；hist_worst 是回看窗口内真实发生过的最差回撤。'}
    else:
        notes.append('压力测试**没算**：Beta 没算出来，就没有"跟着指数跌多少"的依据 —— '
                     '不拿 1.0 顶替（那等于宣称组合和指数一模一样）。')

    # ── ⑨ 行业（有就报，没有就明说没有）─────────────────────────────────
    dist, ind_missing = _industry(got)
    metrics['industry'] = dist
    if ind_missing:
        notes.append('这几只没有行业（**没有硬塞进任何一个行业**）：%s'
                     % '、'.join('%s（%s）' % (m['name'], m['why']) for m in ind_missing[:4]))

    grades = rules.grade(metrics)

    # 给前端画图的两样：净值曲线（内联 SVG 用）和相关矩阵（CSS grid 用）。
    # ★这两个走 `data` 通道（不进大模型上下文），见工具层的两条通道约定。
    step = max(1, len(nav) // 240)              # 最多 240 个点，够画了
    series = {'dates': [d.strftime('%Y-%m-%d') for d in frame['dt'][1:][::step]],
              'nav': [round(float(x), 4) for x in nav[::step]]}
    corr = None
    if len(frame_w) >= MIN_WEEKS:
        cm = frame_w.corr()
        corr = {'codes': codes,
                'names': [g['name'] for g in got],
                'm': [[round(float(cm.iloc[i, j]), 3) for j in range(len(codes))]
                      for i in range(len(codes))]}

    return {'ok': True, 'metrics': metrics, 'grades': grades, 'dropped': dropped,
            'notes': notes, 'series': series, 'corr': corr, 'risk': risk,
            'rows': rows, 'survived_weight': round(survived, 1)}
