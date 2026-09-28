#!/usr/bin/env python3
"""预测打分的**唯一出口** —— 离线回测（`backtest_kronos.py`）与在线台账共用这一份。

★ 为什么非要把打分抽出来，而不是两边各写一份：

两边各写一份的那天起，它们就开始分叉，而**症状是两个数同时挂在站上打架**：
页面上那句实测写着"方向命中率 52.3%"，台账却显示"最近 60%"。两个都是"算出来的"，
**没有任何办法从页面上看出哪个是对的** —— 用户只能二选一信，或者两个都不信。

这和 `accuracy.py` 那条"回测数字只有一个出口"是同一件事的延伸：
那边管的是"数字只许有一个来源"，这边管的是"算这个数字的尺子只许有一把"。

★ 这个模块**必须保持是叶子**：不许 import feed / runner / torch。
  它要能在不加载任何模型的进程里被直接调用（判据就是这么调它的）。
"""

import statistics

# ── 方向死区（百分点） ────────────────────────────────────────────────
# ★这是**唯一**定义处。用它的一共三处，三处必须是同一个数：
#   ① 回测：`backtest_kronos.py` 的 dir_band 那一档
#   ② 工具层：`tools.py` 给 predict_kline 定 direction 的口径
#   ③ 在线台账：回填真实值后打分
# 抽出来之前，这个 0.3 是**写死在两处的字面量**，中间只有一句注释说"口径一致"——
# 注释不拦人，改一处另一处照样静默地分叉。
DEADBAND = 0.3

UP, DOWN, FLAT = 'up', 'down', 'flat'


def wilson(k, n, z=1.96):
    """命中率的 95% 置信区间（Wilson，不用 scipy —— 本机 venv 里没有）。

    ★为什么非要有它：一个 98 个起点的 50.5% 和一个 500 个起点的 50.5%，
    说的**不是同一件事**。没有区间，页面上写"命中率 50.5%、比瞎猜高"就是过度解读 ——
    真实含义可能是"这一点点优势完全在噪声里"。区间一旦跨过 50%，就必须照实说
    "和抛硬币分不出来"。
    """
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return [round(100 * (c - r) / d, 1), round(100 * (c + r) / d, 1)]


def sign_of(x, band=0.0):
    """涨跌方向。`band` 是死区：|x| <= band 算 flat（不表态）。

    ★`None` 进 `None` 出 —— 缺数据不许悄悄变成 flat，
      那会把"没测到"混进"测到了、是平的"里，两个的含义完全不同。
    """
    if x is None:
        return None
    if abs(x) <= band:
        return FLAT
    return UP if x > 0 else DOWN


def pct_change(new, base):
    """(new/base - 1) * 100。base 为 0/None → None（**不编数**）。"""
    if new is None or not base:
        return None
    return (new / base - 1) * 100


def outcome_row(base_close, pred_close, actual_close, deadband=DEADBAND,
                window_mean=None):
    """一次预测 + 真实结果 → 打分需要的那一行。

    `pred_close` / `actual_close` 必须是**等长的收盘价序列**（同一个 horizon，
    逐根对齐）。长度不等直接抛 —— 那说明调用方拿错了对象，蒙混过去只会算出一个
    看着像结论的数。

    `window_mean` 是可选的诊断输入（回看窗口的收盘价均值），给了才算 `revert`。
    """
    pc = [float(x) for x in pred_close]
    ac = [float(x) for x in actual_close]
    if len(pc) != len(ac):
        raise ValueError('预测 %d 根、真实 %d 根，对不上，不能打分' % (len(pc), len(ac)))
    if not pc:
        raise ValueError('空序列不能打分')

    base = float(base_close)
    p_last, a_last = pc[-1], ac[-1]
    p_chg = pct_change(p_last, base)
    a_chg = pct_change(a_last, base)

    # 收盘价路径的误差：模型整条路径 vs "假设一直不动"（基线的基线）
    mape_path = [abs(a - b) / b * 100 for a, b in zip(pc, ac)]
    mape_flat_path = [abs(base - b) / b * 100 for b in ac]

    # ★诊断：模型的**第一根**是不是先跳一下？
    #   "不动"的误差就是这只票自己的日波动；模型如果第一根误差反而更大，
    #   说明它给出的第一个价不是"接着最后一根"，而是往别处去了。
    #   最常见的去处就是**回看窗口的均值**（z-score 归一化 → 反归一化时
    #   如果模型输出贴近零均值，就会回到窗口中枢）。所以顺手量一下回归比例。
    revert = None
    if window_mean is not None:
        den = float(window_mean) - base
        if abs(den) > 1e-9:
            revert = (pc[0] - base) / den

    return {
        'p_chg': p_chg, 'a_chg': a_chg,
        'mape_model': statistics.mean(mape_path),
        'mape_flat': statistics.mean(mape_flat_path),
        'mape_path': mape_path, 'mape_flat_path': mape_flat_path,
        'jump1': pct_change(pc[0], base),      # 模型第一根隐含的涨跌
        'true1': pct_change(ac[0], base),      # 真实第一根的涨跌
        'revert': revert,
        'dir_model': sign_of(p_chg), 'dir_actual': sign_of(a_chg),
        'dir_model_band': sign_of(p_chg, deadband),
        'dir_actual_band': sign_of(a_chg, deadband),
    }


def with_momentum(row, past_chg, deadband=DEADBAND):
    """补上"跟随趋势"这个朴素对手的方向（它要回看窗口，不在 outcome_row 的输入里）。"""
    row['past_chg'] = past_chg
    row['dir_past_band'] = sign_of(past_chg, deadband)
    return row


def hit(rows, band):
    """方向命中率。`band=True` 走死区口径，False 走严格符号。

    死区口径下 `flat` **不算表态**，从分母里摘出去 ——
    "模型没说话"和"模型说错了"不是一回事。
    """
    k = 'dir_model_band' if band else 'dir_model'
    ka = 'dir_actual_band' if band else 'dir_actual'
    called = [r for r in rows if r[k] != FLAT]
    n_hit = sum(1 for r in called if r[k] == r[ka])
    return {
        'n_called': len(called),
        'n_hit': n_hit,
        'hit_rate': round(100.0 * n_hit / len(called), 1) if called else None,
        'ci95': wilson(n_hit, len(called)),
    }


# ── 多采样：从 N 条路径读出一个区间 + 一个分歧度 ──────────────────────────
# ★这一节是阶段 3 的。三条纪律写在这里，因为它们全都是"很容易悄悄算错"的地方：
#
# ① **区间不是一条路径的极差**。单次采样那条路径的 min/max 说的是"这一次采样
#    飘了多远"，不是"价格可能落在哪"。所以只有**多采样**才给得出区间，而且
#    台账里必须用 `interval_kind` 把两者分开（`ledger.coverage` 那边有判据守）——
#    混在一起算覆盖率，量到的会是另一件东西。
#
# ② **名义置信水平由 N 算出来，不写死**。N 条独立采样的**极差**本身就是一个
#    精确的预测区间：设 X1…XN 与新的一个 X 同分布且独立，连续分布下
#        P(X 落在 min…max 之间) = (N-1)/(N+1)
#    ⇒ N=9 恰好 80%，N=19 恰好 90%。这条是**无分布假设**的（不要求正态），
#    代价是想拿高置信就必须多采样。写死一个"80%"而 N 是 5，页面就在撒一个
#    **查不出来的谎** —— 这正是 `accuracy.py` 那条"要么连基线一起给"的形状。
#
# ③ **弃答的判据是统计口径，不是拍的阈值**：N 条路径的终点方向就是一个二项
#    样本，判据是"看涨比例的 95% 区间跨不跨过 50%"（`withhold()`）。
#    ★我第一版用的是"区间跨过基准价"，实测之后**废弃了** —— 那条规则在 6 个
#      起点上真/假各半，而它反映的是"模型把中心挪了多远"，不是"有没有一致看法"。
#      详细理由写在 `withhold()` 的 docstring 里，因为它是个很容易再犯一次的坑。

def level_of(n):
    """N 条独立采样的极差对应的名义置信水平（%）。N<2 给不出区间 → None。"""
    if not n or n < 2:
        return None
    return round(100.0 * (n - 1) / (n + 1), 1)


def central(paths):
    """N 条路径的逐根均值 —— 给页面画的**中心线**，也是 `direction` 的来源。

    ★它是"均值"这件事必须说清楚：均值路径**不是一条可能发生的路径**
      （它把 N 条各自合法的 K 线平均出一条谁也没走过的线）。所以页面上那条
      实线只能读作"采样中心"，区间的边界才是"模型认为的范围"。
    """
    if not paths:
        return None
    L = len(paths[0])
    if any(len(p) != L for p in paths):
        raise ValueError('路径长度不一致，不能取中心线（先查为什么）')
    return [sum(p[i] for p in paths) / len(paths) for i in range(L)]


def envelope(paths):
    """N 条等长路径 → 逐根的 [lo…, hi…] 带子（页面上的扇形）。

    ★带子只由**极差**构成，不做任何插值/平滑：任何平滑都会让边界不再对应
      任何一个采样点，而"这条边界是第 k 条路径走到的位置"是它唯一可验的性质。
    """
    if not paths:
        return None, None
    L = len(paths[0])
    if any(len(p) != L for p in paths):
        raise ValueError('路径长度不一致，包络无从谈起（先查为什么）')
    lo = [min(float(p[i]) for p in paths) for i in range(L)]
    hi = [max(float(p[i]) for p in paths) for i in range(L)]
    return lo, hi


def agree(paths, base, band=DEADBAND):
    """N 条路径的**终点**方向投票，外加一个"这票投出结论了吗"的置信区间。

    ★投的是**终点**，与 `direction` / 回测打分同一个落点。投逐根路径的
      "整体涨跌"会得到另一个数，而页面上两个数并排就是两把尺子。
    """
    votes = {UP: 0, DOWN: 0, FLAT: 0}
    for p in paths:
        d = sign_of(pct_change(float(p[-1]), base), band)
        votes[d if d is not None else FLAT] += 1
    called = votes[UP] + votes[DOWN]
    return {
        'n': len(paths), 'up': votes[UP], 'down': votes[DOWN], 'flat': votes[FLAT],
        # 表过态的路径数：分母。一条都没表态时下面是 None（**不是 0%**）——
        # "全都没看法"和"0% 看涨"是两回事。
        'called': called,
        # 诊断用，**不当判据**（`withhold()` 用的是区间，不是这个点估计）。
        'consensus': round(max(votes[UP], votes[DOWN]) / called, 3) if called else None,
        # 看涨比例的二项 95% 区间 —— 复用 `wilson`，与 `verdict()` 判 `beats_coin`
        # **同一把尺子**。这不是巧合：两处问的是同一个统计问题的正反两面
        #（"证明了它偏多吗" / "证明了它偏向哪边吗"）。
        'ci95': wilson(votes[UP], called),
    }


def withhold(votes):
    """投票的 95% 区间**跨过 50%** ⇒ 采样路径偏向哪边都证明不了 ⇒ 方向**弃答**。

    ★规则形式：N 条路径的终点方向就是一个二项样本，"模型有没有一致看法"问的是
      "这些路径系统地偏向一边吗"。所以判据是**看涨比例的置信区间跨不跨过 50%**，
      与 `verdict()` 判"算不算赢"用的是同一个 95% 口径。`called < 2` 时区间给不出
      （或 n=1 的区间必然跨过 50）→ 同样弃答：**"全都没表态"不是"看平"**。

    ★为什么**不**用"区间跨过基准价"当判据（我第一版就是那么写的，已废弃）：
      实测 6 个起点，那条规则真/假各半 —— 而它反映的其实是"模型把中心挪了多远"
      （同一批起点，中心线涨跌从 −8.8% 到 +14.1%），**不是**"模型有没有一致看法"。
      拿一个在量别的东西的条件去当弃答判据，症状是弃答率看着正常而含义是错的。

    ★为什么**不**拍一个"分歧度 > 0.7"之类的阈值：那是拟合出来的数，换一批标的
      就不成立，而且没人能说出它为什么是 0.7。用区间跨 50% 则自带一个可查的性质：
      **N 越大区间越窄 ⇒ 采样越多越容易给出方向**（实测 N=9 时要 8/9 或 9/9 才给方向）。
      想少弃答就多采样，方向不会反过来。

    ★实测（2026-09-20，CPU，6 个起点 × N=9，pred_len=30/lookback=400，
      见 `/tmp` 那次量测；n=6 很薄，只用来定规则**形式**）：投票比例 0.667/0.714/
      0.889/1.0/1.0/1.0 ⇒ 弃答 2/6。规则本身不依赖这个样本量（它依赖的是 95% 口径）。
    """
    if not votes:
        return True
    ci = votes.get('ci95')
    if not ci or votes.get('called', 0) < 2:
        return True
    return ci[0] <= 50.0 <= ci[1]


def summarise(rows, pred_len=None, lookback=None):
    """一组打分行 → 页面上那张表要的全部数字。

    `pred_len` 不给就从第一行推（`mape_path` 的长度）；各行的 horizon 必须一致，
    不一致要**分组之后**再调这个函数（台账里不同 pred_len 的条目不能混着汇总）。
    """
    n = len(rows)
    if not n:
        return None
    horizon = pred_len or len(rows[0]['mape_path'])
    bad = [i for i, r in enumerate(rows) if len(r['mape_path']) != horizon]
    if bad:
        raise ValueError('horizon 不一致：第 %s 行不是 %d 根，先分组再汇总'
                         % (bad[:3], horizon))

    up = sum(1 for r in rows if r['dir_actual'] == UP)
    past_call = [r for r in rows if r.get('dir_past_band') not in (None, FLAT)]
    m_hit = sum(1 for r in past_call if r['dir_past_band'] == r['dir_actual_band'])

    # ★误差随步长怎么走 —— 只看总 MAPE 会漏掉一件重要的事：
    #   模型和"假设不动"的差距是**均匀的**，还是**随着越推越远而炸开**的。
    #   后者说明它在盲推，前者说明它连第一步都没踩对。两者对用户的含义完全不同。
    by_step = [{
        'step': i + 1,
        'model': round(statistics.mean(r['mape_path'][i] for r in rows), 3),
        'flat': round(statistics.mean(r['mape_flat_path'][i] for r in rows), 3),
    } for i in range(horizon)]

    reverts = [r['revert'] for r in rows if r.get('revert') is not None]
    out = {'n_origins': n, 'horizon_bars': horizon}
    if lookback is not None:
        out['lookback_bars'] = lookback
    out.update({
        # ① 方向：严格符号
        'dir_strict': hit(rows, band=False),
        # ② 方向：±死区（与工具层 direction 同口径）
        'dir_band': hit(rows, band=True),
        # ③ 基线 A：无脑喊涨 —— 衡量"市场本身就偏多"白送了多少
        'baseline_always_up': {'hit_rate': round(100.0 * up / n, 1)},
        # ④ 基线 B：跟随趋势 —— 一个真正的朴素对手
        'baseline_momentum': {
            'n_called': len(past_call),
            'hit_rate': round(100.0 * m_hit / len(past_call), 1) if past_call else None,
            'ci95': wilson(m_hit, len(past_call)),
        },
        # ⑤ 收盘价路径误差：模型 vs "假设不动"
        'mape_close_model': round(statistics.mean(r['mape_model'] for r in rows), 3),
        'mape_close_flat': round(statistics.mean(r['mape_flat'] for r in rows), 3),
        'mape_by_step': by_step,
        'actual_up_pct': round(100.0 * up / n, 1),
        # 诊断：第一根
        'jump1_abs_mean': round(statistics.mean(abs(r['jump1']) for r in rows), 3),
        'true1_abs_mean': round(statistics.mean(abs(r['true1']) for r in rows), 3),
        'revert_mean': round(statistics.mean(reverts), 3) if reverts else None,
    })
    return out


def best_baseline(total):
    """最强的朴素基线 —— 比它才有意义，跟弱的那条比是自欺。"""
    a = (total.get('baseline_always_up') or {}).get('hit_rate')
    m = (total.get('baseline_momentum') or {}).get('hit_rate')
    cands = [x for x in (a, m) if x is not None]
    if not cands:
        return None
    return max(cands)


def verdict(total):
    """把"算不算赢"**从数字里读出来** —— ★唯一实现，不许别处再写一份判断。

    返回的是**结构化的结论**（哪个分支、差多少、比值多少），不是一句话。
    打印成中文和渲染成页面是两种介质，各写各的措辞没问题；
    但"走哪个分支"必须两边一致，否则同一个 JSON 会得出两个相反的结论。

    ★判"算不算赢"用的是**置信区间下界**，不是点估计 ——
      点估计高 2 个百分点在 n=100 时说明不了任何事，拿它去写页面就是过度解读。
    """
    d = (total.get('dir_band') or {})
    rate, ci = d.get('hit_rate'), d.get('ci95')
    lo, hi = (ci + [None, None])[:2] if ci else (None, None)
    base = best_baseline(total)
    m = total.get('mape_close_model')
    f = total.get('mape_close_flat')

    v = {
        'n_called': d.get('n_called'),
        'hit_rate': rate,
        'ci95': ci,
        'best_baseline': base,
        'margin': (round(rate - base, 1) if rate is not None and base is not None else None),
        # None = 无从判断（没表态 / 没基线），不是 False
        'beats_coin': None,
        'beats_baseline': None,
        'mape': {'model': m, 'flat': f, 'wins': None},
    }
    if rate is not None and lo is not None:
        v['beats_coin'] = lo > 50.0
    if v['margin'] is not None:
        v['beats_baseline'] = v['margin'] > 0
    if m is not None and f is not None:
        v['mape']['wins'] = m < f
        v['mape']['ratio'] = round(m / f, 3) if f else None

    # 误差随步长发散的方式：这两句话只能二选一，选错了就是给用户一个假解释
    bs = total.get('mape_by_step') or []
    if bs and v['mape']['wins'] is False:
        gm = bs[-1]['model'] / max(bs[0]['model'], 1e-9)
        gf = bs[-1]['flat'] / max(bs[0]['flat'], 1e-9)
        v['mape']['growth_model'] = round(gm, 2)
        v['mape']['growth_flat'] = round(gf, 2)
        # 比值接近 ⇒ 每一步都稳定地差这么多（系统性偏差）；
        # 比值大得多 ⇒ 越推越离谱（盲推）。两者对"该不该看这条曲线"的含义完全不同。
        v['mape']['synchronised'] = gm < gf * 1.25
        j, t = total.get('jump1_abs_mean'), total.get('true1_abs_mean')
        if j is not None and t is not None:
            v['mape']['first_bar_lag'] = j > t * 1.3
            v['mape']['first_bar_ratio'] = round(j / max(t, 1e-9), 2)
    return v
