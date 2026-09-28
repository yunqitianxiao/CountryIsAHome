"""关联挖掘：**只做最朴素的一层**（用户原话：「弱化分析」）。

四种关联，按"证据有多硬"从软到硬排：

  ① **同板块**（走 `stock_boards` / `board_members`）—— 这是**分类**，不是统计量。
     它说的是"这两只票被同一个板块收录"，不是"它们走势像"。这两句话差别很大：
     一个板块里常年趴着不动的票多得是。所以它单独出现时，`rel='同板块'`、
     **没有 rel_value**（不许拿一个不存在的东西填 0）。
  ② **滚动收益相关性** —— 有统计量，但必须**窗口和样本数一起给**（规格 §3.3）。
     样本 < `MIN_CORR_N` **就不给这个数**：30 个点算出来的 0.87 和 250 个点算出来的
     0.87 根本不是同一个东西，只给系数不给样本数，等于把两件事说成一件。
  ③ **lead-lag**（互相关找最优滞后）—— 只在**分钟线**上做，因为日线上的"滞后"
     最小单位是 1 个交易日，而"领先"这个词在日线上几乎永远测不出东西来
     （一天的错位在今天涨明天涨之间，和噪声分不开）。
  ④ **量能同步**（peer 自己也在放量）—— 不单独成条，进 `metrics`/evidence 里。

★每条关联**必须带证据字符串**（规格原话）。"相关性强"这种话在这里是**不许出现**的：
  用户没有任何办法复核它，而复核不了的话，它和一个编出来的数没有区别。

★不预测、不建议、不排序成"值得关注"。这一层的职责是**把关系指出来**，
  以及（更重要的）**把没找到、没算的数如实标出来**。
"""

import numpy as np
import pandas as pd

from . import anomaly
from .collect import _num

# ── 常数（★同样是政策常数，不是实测阈值；理由写在各自旁边）──────────────
BOARD_KINDS = ('concept', 'industry')   # 概念在前：概念板块比行业板块更能解释异动
MAX_BOARDS_PER_CODE = 4                 # 一只票最多看几个板块的成分股（防止全市场扫一遍）
# ★成分股并集的**上限只防病态**（一个板块挂了 5000 只），**不承担筛选职责**。
#   这里原本写的是"超过 40 个就取代码序前 40 个"—— 那是个静默的灾难：
#   代码序是有偏的（'000xxx' 全排在 '600xxx' 前面），于是一次截断会把某个
#   交易所的票整批留下、另一批整批丢掉，而"留下了谁"和"哪只票有关联"毫无关系。
#   真正决定"看哪几只"的是 `_peer_rank` 排序 + `PEER_KEEP`，那两条是有理由的。
PEER_POOL = 1500
PEER_KEEP = 6                           # 真正去取日线的peer数（每个 = 一次调用，见预算）
CORR_WINDOW = 120                       # 相关窗口（交易日）≈ 半年
MIN_CORR_N = 60                         # ★样本不够就不给系数（规格 §3.3 的硬要求）
HIGH_CORR = 0.6                         # |r| ≥ 它才配得上"高相关"这个标签
LL_PERIOD = 5                           # lead-lag 用 5 分钟线
LL_DAYS = 5                             # 取 5 个交易日
LL_MAX_LAG = 30                         # 滞后搜索范围 ±30 分钟
LL_MIN_BARS = 30                        # 某个滞后下重叠 bar 少于此数，这个滞后点不算
LL_CODES_MAX = 3                        # 整个 scan 里最多对几只异动票算 lead-lag
                                        # ★这一条是**全局配额**（分钟线是逐只取的，
                                        #   一只异动票的完整 LL 要 1+N 次调用），
                                        #   而"对几个 peer 算"**不再另设隐藏上限**：
                                        #   该封顶的是预算本身，预算显式记账、超了会
                                        #   说出来（Ledger.skipped + note）。
                                        #   隐藏上限的坏处不是"省了几次调用"，是
                                        #   **它把一条可能存在的领先关系无声地筛掉了**，
                                        #   而账本上看不出发生过这件事。
LL_LEAD_MIN = LL_PERIOD                 # 滞后 ≥ 一个 bar 才配叫"领先"（否则就是同步）
LL_MIN_R = 0.35                         # 峰值 |r| 低于它 ⇒ **"领先 N 分钟"这句话不成立**（见 `_lag_is_real`）
LL_SIG_K = 3.0                          # 随样本数收紧的那条：门槛 = max(LL_MIN_R, LL_SIG_K/√n)

# ★**有板块归属源的市场白名单**。只有 A 股：`stock_boards` 唯一一条路是 adata 的
#   `get_plate_east`（东财，A 股专用）。写成**白名单**而不是"不等于港美股"：
#   将来多一个市场（crypto）时，默认落到"这个市场没有源"那一支才是对的。
#   ★取值照**快照行实测**：`cn_stock`（不是 `cn`）。`cn` 也留着，因为
#   `row.get('market') or 'cn'` 那个兜底路径会给 `cn`。
MARKETS_WITH_BOARD_SRC = ('cn', 'cn_stock')


# ── 板块 ────────────────────────────────────────────────────────────────

def board_index(lg):
    """→ ({board_code: 板块行}, 失败说明)。`boards()` 每种 kind 只要一次调用，
    却服务整个 scan 里所有票的板块行情 —— 这是最划算的一次采集。"""
    idx, errs = {}, []
    for kind in BOARD_KINDS:
        # memo：同一轮 scan 里板块清单不会变（见 `Ledger.call` 的 memo 段）
        ok, rows = lg.call('boards:%s' % kind, 'boards', kind, memo=True)
        if not ok or not rows:
            errs.append('boards(%s) 没取到' % kind)
            continue
        for b in rows:
            if b.get('code'):
                idx[str(b['code'])] = b
    return idx, errs


def boards_of(code, lg):
    """一只票所属的全部板块；**顺带把板块当日的行情贴上去**（如果拿得到）。

    ★`stock_boards` 只回答"它属于谁"，不含涨跌幅/领涨股 —— 那两个字段在
      `boards()` 那一边。所以这里做一次 join。join 不上的板块**保持 None**，
      不补 0：一个板块的涨跌幅是 0 和"不知道这个板块涨了多少"是两件事。
    """
    # memo：同一轮里同一只票的板块归属取一次就够（见 `Ledger.call` 的 memo 段）
    ok, rows = lg.call('stock_boards:%s' % code, 'stock_boards', code, memo=True)
    if not ok or not rows:
        return [], ('没取到 %s 的板块归属' % code if not ok else None)
    return [{'code': str(b.get('code')), 'name': b.get('name'),
             'kind': b.get('kind')} for b in rows if b.get('code')], None


def peer_pool(board_codes, lg, pct_of=None, limit=PEER_POOL):
    """板块成分股并集 → (codes, {'truncated':bool}, 没取到的板块列表)。

    `pct_of`：{code: 当日涨跌幅}，**只在触发上限截断时用**（见下）。
    """
    seen, info, failed = {}, {}, []
    pct_of = pct_of or {}
    for bc in list(board_codes)[:MAX_BOARDS_PER_CODE]:
        # memo：同一轮 scan 里好几个异动票会共用一个板块（合成市场实测：9 只票
        # 要了 25 次成分股，其中大部分是**同一份**），这里是省预算最划算的一处 ——
        # 省下的额度直接变成多深挖几只票，而深挖才是这个模块的正事。
        ok, mem = lg.call('board_members:%s' % bc, 'board_members', bc, memo=True)
        if not ok or mem is None:
            failed.append(bc)
            continue
        for c in mem:
            c = str(c)
            seen.setdefault(c, [])
            if bc not in seen[c]:
                seen[c].append(bc)
    if len(seen) > limit:
        # 超过上限就截断，**但截断本身要能被说出来**（见 links 里的 evidence）。
        # ★这里只在病态情况下触发（见 `PEER_POOL` 的注释），且**截断规则本身
        #   不能带方向** —— 取代码序前 N 个是有偏的（'000xxx' 全排在 '600xxx' 前），
        #   会把某个交易所的票整批留下、另一批整批丢掉，而"留下了谁"和"谁有关联"
        #   毫无关系。按 |当日涨跌幅| 排：越动的越可能相关，而这件事和代码是谁家的无关。
        keep = sorted(seen, key=lambda c: (-abs(pct_of.get(c) or 0.0), c))[:limit]
        seen = {c: seen[c] for c in keep}
        info['truncated'] = True
    return seen, info, failed


# ── 相关性 ──────────────────────────────────────────────────────────────

def _returns(df, col='close'):
    d = df[['dt', col]].copy()
    d['r'] = pd.to_numeric(d[col], errors='coerce').pct_change()
    return d[['dt', 'r']].dropna()


def corr(a_df, b_df, window=CORR_WINDOW, min_n=MIN_CORR_N):
    """两只票的收益相关。→ dict，**永远带 n / window**（规格 §3.3）。

    ★顺序有讲究：**先按 dt 内连接、再算收益**。
      反过来（各自 pct_change 完再对齐）是本项目踩过的坑（见 `portfolio.py` 的 Beta
      那一段）：各自 dropna 之后长度必然差 1，`len(a) == len(b)` 永远为假，
      于是系数**恒为 None**，而报错理由是"样本不够"—— 一个看着像数据问题的代码问题。
      另一个代价是：不同市场的交易日不同，内连接会把"没交易"的日子剔掉，
      而不是前向填充成一个 0% 的假收益（那会系统性抬高相关性）。
    """
    if a_df is None or b_df is None or not len(a_df) or not len(b_df):
        return {'r': None, 'n': 0, 'window': window, 'why': '有一只票没有日线'}
    ra, rb = _returns(a_df), _returns(b_df)
    m = ra.merge(rb, on='dt', how='inner', suffixes=('_a', '_b'))
    m = m.tail(window)
    n = len(m)
    if n < min_n:
        return {'r': None, 'n': n, 'window': window,
                'why': '对齐后只有 %d 个共同交易日（窗口 %d 日，至少要 %d 个才报系数）'
                       % (n, window, min_n)}
    if m['r_a'].std(ddof=1) <= 1e-12 or m['r_b'].std(ddof=1) <= 1e-12:
        return {'r': None, 'n': n, 'window': window, 'why': '有一只票在这个窗口里几乎没波动'}
    return {'r': float(m['r_a'].corr(m['r_b'])), 'n': n, 'window': window, 'why': None}


def xcorr(a_df, b_df, period=LL_PERIOD, max_lag_min=LL_MAX_LAG, min_bars=LL_MIN_BARS):
    """互相关找最优滞后。→ {'lag_min','r','n','period'}，算不出则 lag_min=None。

    **符号约定（★这条要被引用，别改）**：
        `lag_min > 0`  ⇒ **a 领先 b** `lag_min` 分钟
        `lag_min < 0`  ⇒ a 滞后 b
        `lag_min == 0` ⇒ 同步
    也就是说：把 a 的第 t 根和 b 的第 t+k 根配起来，r 的绝对值在 k 处最大时，
    `lag_min = k * period`。

    ★两个必须处理的细节，都不是可选的：

    1. **不能跨交易日配。** A 股一天只有 4 小时（240 根 5 分钟线），
       如果把"今天的最后一根"和"明天的第一根"配起来，那个跨越隔夜的"滞后"
       会把相关系数抬到很高（隔夜跳空是共同的），于是**最优滞后永远落在日界上**，
       而它测的是"隔夜效应"、不是领先-滞后。所以每个滞后都用日期掩码
       （`day[:-k] == day[k:]`）只保留同日内的配对。
    2. **按 |r| 选峰，不是按 r。** 逆着动的领先-滞后（A 涨完 B 跌）也是关联，
       只找最大正相关会把它整条漏掉。代价是：逆相关的峰也会被选出来，
       所以 r 的**符号**必须一起返回，不能只给一个"相关 0.8"。
    """
    if a_df is None or b_df is None or not len(a_df) or not len(b_df):
        return {'lag_min': None, 'r': None, 'n': 0, 'period': period, 'why': '有序列是空的'}
    ra = _returns(a_df).rename(columns={'r': 'ra'})
    rb = _returns(b_df).rename(columns={'r': 'rb'})
    m = ra.merge(rb, on='dt', how='inner')
    n = len(m)
    if n < min_bars:
        return {'lag_min': None, 'r': None, 'n': n, 'period': period,
                'why': '共同 bar 只有 %d 根（要至少 %d 根）' % (n, min_bars)}
    va, vb = m['ra'].values, m['rb'].values
    day = m['dt'].dt.strftime('%Y-%m-%d').values
    kmax = max(1, int(max_lag_min) // max(1, period))
    best = None
    for k in range(-kmax, kmax + 1):
        if k >= 0:
            x, y, d1, d2 = va[:n - k], vb[k:], day[:n - k], day[k:]
        else:
            x, y, d1, d2 = va[-k:], vb[:n + k], day[-k:], day[:n + k]
        mask = d1 == d2                       # ★同日才配（见 docstring 第 1 点）
        x, y = x[mask], y[mask]
        if len(x) < min_bars:
            continue
        if np.std(x) <= 1e-12 or np.std(y) <= 1e-12:
            continue
        r = float(np.corrcoef(x, y)[0, 1])
        if not np.isfinite(r):
            continue
        if best is None or abs(r) > abs(best[1]) + 1e-9:
            best = (k, r, int(len(x)))
    if best is None:
        return {'lag_min': None, 'r': None, 'n': n, 'period': period,
                'why': '所有滞后下的重叠 bar 都不够或没有波动'}
    return {'lag_min': int(best[0]) * period, 'r': _num(best[1], 3), 'n': best[2],
            'period': period, 'bars': int(best[0]), 'why': None}


# ── 一个异动票的关联 ────────────────────────────────────────────────────

def lag_threshold(n):
    """某个样本量下，"领先 N 分钟"这句话需要的峰值 |r| 门槛。
    ★两条同时生效（理由见 `_lag_is_real`）：绝对门槛 `LL_MIN_R` + 随样本数收紧的
      `LL_SIG_K/√n`。n 越小门槛越高 —— 和相关系数那条纪律是同一个方向。"""
    nn = max(1, int(n or 0))
    return max(LL_MIN_R, LL_SIG_K / (nn ** 0.5))


def _lag_is_real(r, n):
    """"领先 N 分钟"能不能说。→ bool

    ★**为什么峰值相关 0.17 的"领先 20 分钟"必须被拦下来**：那不是"一个弱一点的
      领先关系"，那是**噪声的 argmax**。k 个滞后点里挑 |r| 最大的一个，只要样本
      不够多，挑出来的那个几乎必然是噪声的高点 —— 而且它会被写成一个很具体的数
      （"领先 20 分钟"），没人能复核。这跟"12 个样本点算出的 0.87"是**同一个病**，
      规格 §3.3 已经点名不许（样本 <60 不给系数）。这条就是它在 lead-lag 上的镜子。

    ★门槛随 n 收紧：n 根 bar 下纯噪声的 |r| 大约就是 1/√n 量级，取 3/√n 当粗糙的
      显著性底线（n=219 时 ≈0.20，n=60 时 ≈0.39）。粗糙是对的：本模块只做最朴素
      的一层，宁可这条线划得保守一点（少报几条"领先"），也不报一条复核不了的。
    """
    if r is None or not n:
        return False
    return abs(float(r)) >= lag_threshold(n)


def _peer_rank(info):
    """peer 的考察顺序。★四个键，每一条都有理由：

    1. **共概念板块的排在共行业板块的前面。** 概念板块是主题式的（十几到几十只），
       行业板块是分类式的（本桩里 53 只）—— "同属白酒"比"同属非银金融"是强得多的
       证据。不区分的话，一只银行股会把异动票的深挖额度全占掉。
    2. **自己也异动的排前面**（同一天同一个故事）。
       ★这一条是**这一批里谁异动了**的函数 —— 所以 `anom_codes` 必须是**同一批
         快照算出来的那一整份**，不能是"只有我自己"。否则 `scan()` 与
         `anomaly_of(code)` 会对同一只票给出**不同的 peer 顺序**，而两边长得都像
         对的一样（这个坑在合成市场上真的踩到过：单只票那条路上，构造出来的
         领先-滞后对根本没被排进前几个）。
    3. **当日涨跌幅绝对值大的排前面**（同板块里动得最多的那只最可能是主角）。
    4. 代码序兜底：**排序必须完全确定**，否则同样输入两次跑出来两张不同的表。

    ★这是**排序**，不是判据：排在前面的照样要过相关系数那一关，
      排后面的也不是"没有关联"，只是这次没轮到（预算，见 collect 第 2 条）。
    """
    return (0 if info.get('concept_shared') else 1,
            0 if info['anom'] else 1,
            -abs(info['pct'] if info['pct'] is not None else 0.0),
            info['code'])


def links_for(code, row, lg, board_idx, anom_codes, ll_ok, universe_rows=None,
              on_self_daily=None):
    """一只异动票 → 规格 §3.3 的 `links[code]`。**预算在 lg 里**，用尽就少算并记账。

    `ll_ok`：这一只要不要花分钟线做 lead-lag（由 scan 侧的全局配额决定，
    因为是预算问题、不是这只票的属性）。
    `universe_rows`：全市场那一批快照行 —— peer 的当日涨跌幅/量比从这里取，
    而不是为每个 peer 再打一次快照（那是 6 次调用换 6 个字段）。
    `on_self_daily(code, df)`：本票日线到手时的回调。**存在的理由是预算**：
      量比要用本票的日线，而这里已经取了 —— 让上层复用同一份，
      比在别处再取一次省一次调用（而每一次调用都是要记账的）。
    """
    ev = []
    out = {'boards': [], 'peers': [], 'evidence': ev}
    market = row.get('market') or 'cn'

    # ① 板块归属
    # ★按市场短路：`stock_boards` 只有 adata 一条路（`get_plate_east`），而那是东财的
    #   **A 股**接口 —— 腾讯/同花顺都没有"给代码、反查全部所属板块"的接口（见
    #   `datasource.__init__` 的 `stock_boards` docstring）。港美股问它**必然**回空表。
    #   2026-09-22 实测：榜单 20 只港美股 → 20 条 `SourceError: … 回了空表`，
    #   把采集健康度拖到 5/26，而真正的那条失败（`realtime:cn` 超时）被淹在里面。
    #   这不是故障、是**市场对不上源**，所以短路，并且措辞必须与另两种分开：
    #   "这个市场没有源"（不适用）≠"没取到"（源挂了）≠"没有归属"（源回了空表）。
    #   ★★第一版这里写的是 `market == 'cn'`，把 **A 股也短路了**（A 股一只票都没
    #   去查板块，`peers` 全空）。真实取值是 `cn_stock`（快照行实测），
    #   而我当时的否证脚本喂的是自造的 `{'market': 'cn'}` —— 验的是我自己的假设，
    #   不是真实输入，所以三层证据全绿却全错（纪律 ㉛：自造的输入让假设自证）。
    if market in MARKETS_WITH_BOARD_SRC:
        bl, berr = boards_of(code, lg)
        if berr:
            ev.append('⚠ %s' % berr)
    else:
        bl, berr = [], None
        ev.append('⚠ %s 市场没有板块归属源（`stock_boards` 只有东财 A 股这一条路）'
                  % market)
    # ★归属（`bl`）和**行情**（`board_idx`）是两份数据：join 不上的板块照样列出来，
    #   只是 pct/领涨股是 None。丢掉它是最坏的选择 —— "它属于白酒板块"这件事
    #   和"白酒板块今天涨没涨"是两件事，前者不依赖后者是否取到。
    # ★★码空间解析（东财码 → 腾讯码）。这是上面那条"按市场短路"的**第二层**，
    #   同一个病：`stock_boards` 给的是**东财码**（`BK1202`），而 `board_idx`
    #   （`boards()`）与 `peer_pool`（`board_members()`）只认**腾讯码**（`pt...`）。
    #   ⇒ 不解析的话，`board_idx.get(东财码)` 恒为 `{}`（板块行情全 None、
    #   `quoted=False`），`peer_pool(东财码)` 恒整批 `BAD_CODE_SPACE`。
    #   2026-09-22 单票实测（`scan(scope='cn', codes=['000002'])`）：33 条归属
    #   全部 `pct=None`，`peers=0`，外加 4 条 `SourceError`。**原始代码也是这样**，
    #   只是那时榜单里没有 A 股（`realtime:cn` 超时），没暴露 —— 也就是说
    #   这条往返从来没通过，"个股关联"一直是空的。
    #   ★这里做的是**调用方拿对的码去问**，不是替上游选码空间：`datasource` 的
    #   契约（`board_members` 只认腾讯码）一个字没动。`board_members` 的 docstring
    #   把"统一码空间"列为契约级决定留给协调者，同时列了"在两处之间做一次
    #   名字→码的解析"这个备选 —— 这里走的就是那个备选。
    #   实测这条路真能取到成分股：`房地产`→`pt01801180` 95 只、
    #   `破净股`→`pt02GN2142` 418 只，0.2~0.9s。
    #   ★键用 **(名字, kind)** 而不是只按名字：`房地产` 这类名字概念/行业两边
    #   都可能有，只按名字会 join 到另一支的码上，那是个**静默的错配**。
    tx_by_name = {}
    for _c, _r in board_idx.items():
        _n = _r.get('name')
        if _n:
            tx_by_name.setdefault((str(_n), _r.get('kind')), _c)

    out['boards'] = []
    for b in bl:
        # `b['code']` = 东财码（**归属的真身，保留**）；`tx` = 腾讯码（**只用来
        # 查行情/成分股**）。两个都留着，是因为"它属于这个板块"和"这个板块今天
        # 涨了多少"是两件事 —— 解析不出腾讯码的板块照样要列出来，只是行情是 None。
        tx = tx_by_name.get((b.get('name'), b.get('kind')))
        q = (board_idx.get(tx) or {}) if tx else {}
        out['boards'].append(dict(b, tx_code=tx, pct=_num(q.get('pct'), 2),
                                  lead_code=q.get('lead_code'),
                                  lead_name=q.get('lead_name'),
                                  quoted=bool(q)))
    if out['boards']:
        cn_b = [b for b in out['boards'] if b['kind'] == 'concept']
        iv_b = [b for b in out['boards'] if b['kind'] == 'industry']
        parts = []
        if cn_b:
            parts.append('、'.join(b['name'] or b['code'] for b in cn_b)
                         + ' %d 个概念板块' % len(cn_b))
        if iv_b:
            parts.append('、'.join(b['name'] or b['code'] for b in iv_b) + ' 行业')
        ev.append('同属 %s' % '，'.join(parts))
    elif not berr and market in MARKETS_WITH_BOARD_SRC:
        ev.append('没有板块归属（源回的是空表）')

    # ② 板块成分股 → peer 候选
    # ★喂**腾讯码**（见上面的码空间解析），并且**解析不出的板块不调用**：
    #   拿东财码去问是**必然**失败，那不是"源挂了"、是"码不对口" —— 把它计进
    #   采集健康度等于往失败数里灌噪声。这正是第一层那个病的形状：2026-09-22
    #   实测，A 股每只票贡献 4 条 `BAD_CODE_SPACE`（`MAX_BOARDS_PER_CODE`），
    #   换掉了原来的 20 条港美股空表 —— 不修这一层，"修好"只是把失败挪了个地方。
    #   ★`concept_codes` 也必须用腾讯码：它要和 `peer_pool` 返回的 `bls`（腾讯码）
    #   求交集算 `concept_shared`，一边东财一边腾讯的话那个字段**恒 False**。
    uni = {r.get('code'): r for r in (universe_rows or [])}
    queried = [b['tx_code'] for b in out['boards'] if b.get('tx_code')]
    no_tx = len(out['boards']) - len(queried)
    if no_tx:
        ev.append('⚠ %d 个板块只在东财码空间里有（腾讯清单里没有同名板块），'
                  '它们没查成分股 —— 这是两个上游的码空间不同，不是源挂了' % no_tx)
    concept_codes = {b['tx_code'] for b in out['boards']
                     if b['kind'] == 'concept' and b.get('tx_code')}
    pool, pinfo, failed = peer_pool(queried, lg,
                                    pct_of={c: r.get('pct') for c, r in uni.items()})
    if pinfo.get('truncated'):
        ev.append('⚠ 成分股并集超过 %d 个，超出部分没看（按 |当日涨跌幅| 取前 %d）'
                  % (PEER_POOL, PEER_POOL))
    if failed:
        ev.append('⚠ %d 个板块的成分股没取到（%s）' % (len(failed), '、'.join(failed[:3])))
    pool = {c: v for c, v in pool.items() if c != code}
    if not pool:
        return out
    infos = []
    for c, bls in pool.items():
        u = uni.get(c) or {}
        infos.append({'code': c, 'boards': bls, 'n_boards': len(bls),
                      'concept_shared': bool(concept_codes & set(bls)),
                      'pct': u.get('pct'), 'vol_ratio': u.get('vol_ratio'),
                      'name': u.get('name'), 'anom': c in anom_codes})
    infos.sort(key=_peer_rank)

    # ③ 日线：候选票一只一只取（★这是预算花得最快的地方）
    ok_self, self_df = lg.call('daily:%s:%s' % (market, code), 'daily', market, code, 400)
    if not ok_self:
        ev.append('⚠ %s 自己的日线没取到，相关性/量比都算不了' % code)
    elif on_self_daily is not None:
        try:
            on_self_daily(code, self_df)
        except Exception as e:                               # noqa: BLE001
            ev.append('⚠ 量比没算：%s: %s' % (type(e).__name__, str(e)[:60]))
    got, dropped, off = [], [], 0
    for inf in infos:
        if len(got) >= PEER_KEEP:
            # ★"没轮到"和"取失败了"是两件完全不同的事，**必须分开说**。
            #   合成市场上踩到过：把两者混成一句"⚠ 99 个 peer 没算相关性"，
            #   读起来像"99 个候选查不了"，其实那 99 个里有 93 个是我**本来就不打算**
            #   看的（池子一百多只，设计上只取前几个）。一个把"设计"说成"故障"的
            #   账本，比一个不提这件事的账本更坏 —— 它给读者一个假警报，
            #   并且让真正的失败（下面那条）淹在里面。
            off += 1
            continue
        if not lg.can_spend(1):
            dropped.append((inf['code'], '深挖预算用尽'))
            lg.skip('daily:%s:%s' % (market, inf['code']), '深挖预算用尽')
            continue
        lg.spend(1)
        okp, pdf = lg.call('daily:%s:%s' % (market, inf['code']), 'daily', market, inf['code'], 400)
        if okp and pdf is not None and len(pdf):
            got.append((inf, pdf))
        else:
            dropped.append((inf['code'], '日线没取到'))
    # 设计取舍：如实说口径，不带"⚠"（它不是故障）
    if off:
        # ★"来自几个板块"数的是**真的给出成分股的**那些（`queried` 减去取失败的），
        #   不是 `len(out['boards'])`。上面加了码空间解析之后这两个数不再相等：
        #   2026-09-22 实测 `000002` 是 33 条归属、13 条解析到腾讯码、其中全部取到。
        #   写 33 会让读者以为那 20 个没解析出来的板块也贡献了候选 —— 一句**读起来
        #   像读数、其实是另一个量**的话，比不写更坏（它会被当成已核过的数）。
        ev.append('候选 peer %d 个（来自 %d 个板块的成分股），本次按"共概念板块优先、'
                  '当日涨跌幅大优先"取前 %d 个算相关性；其余 %d 个没算'
                  % (len(infos), len(queried) - len(failed), PEER_KEEP, off))
    # 真失败：带"⚠"
    if dropped:
        ev.append('⚠ %d 个 peer 的相关性没算出来：%s'
                  % (len(dropped), '；'.join('%s（%s）' % d for d in dropped[:4])))

    # ④ 相关系数
    for inf, pdf in got:
        c = corr(self_df if ok_self else None, pdf)
        rel = '同板块'
        if c['r'] is not None and abs(c['r']) >= HIGH_CORR:
            rel = '高相关'
        # peer 自己的量比：日线**已经取到手了**，这个数不要就是白不要
        # （用户要看的是"与它关联的个股的情况"，量比正是那个"情况"的一格）。
        pr = anomaly.rvol_from_daily(pdf, (uni.get(inf['code']) or {}).get('volume'),
                                     row.get('ts'))
        out['peers'].append({
            'code': inf['code'], 'name': inf['name'], 'rel': rel,
            'rel_value': _num(c['r'], 3), 'corr_n': c['n'], 'corr_window': c['window'],
            'lag_min': None, 'lag_r': None, 'lag_n': None, 'lead_side': None,
            'pct': _num(inf['pct'], 2),
            'vol_ratio': _num(pr['value'], 2), 'vol_ratio_partial': pr['partial'],
            'ts': row.get('ts'),
            'boards': inf['boards'], 'why_no_corr': c['why'],
        })
        tail = ''
        if pr['value'] is not None:
            tail = '；当日量比 %.2f%s' % (pr['value'], '（偏低，今天还没收盘）' if pr['partial'] else '')
        if c['r'] is not None:
            ev.append('%s(%s) 近 %d 个交易日收益相关 %+.2f（窗口 %d 日，样本 %d）%s'
                      % (inf['name'] or inf['code'], inf['code'], c['n'], c['r'],
                         c['window'], c['n'], tail))
        else:
            ev.append('%s(%s) 相关**没算**：%s' % (inf['name'] or inf['code'], inf['code'], c['why']))

    # ⑤ lead-lag（分钟线；只有拿到全局配额的那几只才做）
    ll_miss = []      # 峰值相关够不着的那些：**合成一条**报，不逐条刷屏
    if ll_ok and ok_self and out['peers']:
        okm, self_min = lg.call('minute:%s:%s:%d' % (market, code, LL_PERIOD),
                                'minute', market, code, LL_PERIOD, LL_DAYS)
        if not okm:
            ev.append('⚠ %s 的分钟线没取到，领先-滞后没算' % code)
        else:
            k = 0
            for p in out['peers']:
                if not lg.can_spend(1):
                    ev.append('⚠ 深挖预算用尽，后面 %d 个 peer 的领先-滞后没算'
                              % (len(out['peers']) - k))
                    break
                lg.spend(1)
                okp, pmin = lg.call('minute:%s:%s:%d' % (market, p['code'], LL_PERIOD),
                                    'minute', market, p['code'], LL_PERIOD, LL_DAYS)
                if not okp:
                    ev.append('⚠ %s 的分钟线没取到，这一对的领先-滞后没算' % p['code'])
                    k += 1
                    continue
                x = xcorr(self_min, pmin, LL_PERIOD)
                p['lag_min'] = x['lag_min']
                p['lag_r'] = x['r']
                p['lag_n'] = x['n']
                # ★先过硬门槛，再说"领先"（见 `_lag_is_real`）。
                #   够不着就不给 `lag_min` —— 不给一个**长得像滞后**的数，
                #   否则读者会把它当成"领先 20 分钟"，而它其实是噪声的 argmax。
                if x['lag_min'] is not None and not _lag_is_real(x['r'], x['n']):
                    ll_miss.append((p['code'], x['r'] or 0.0, x['n']))
                    p['lag_min'] = None
                    p['lead_side'] = None
                elif x['lag_min'] is not None:
                    # 符号：lag_min>0 ⇒ **本票**领先 peer（见 xcorr 的约定）
                    # ★`rel='领先'` 只给**peer 领先本票**那一支。反过来的那一支
                    #   （本票领先 peer，异动正在往外扩散）不给这个标签 ——
                    #   给了的话，"领先"两个字在榜单里就不再指向同一个东西了。
                    #   方向本身没丢：`lead_side` + lag_min 的符号 + 证据串都在。
                    if x['lag_min'] >= LL_LEAD_MIN:
                        p['lead_side'] = 'self'
                        ev.append('%s 领先 %s %d 分钟（%d 分钟线，峰值相关 %+.2f，重叠 %d 根）'
                                  % (code, p['code'], x['lag_min'], LL_PERIOD,
                                     x['r'] or 0.0, x['n']))
                    elif x['lag_min'] <= -LL_LEAD_MIN:
                        p['lead_side'] = 'peer'
                        p['rel'] = '领先'
                        ev.append('%s 领先 %s %d 分钟（%d 分钟线，峰值相关 %+.2f，重叠 %d 根）'
                                  % (p['code'], code, -x['lag_min'], LL_PERIOD,
                                     x['r'] or 0.0, x['n']))
                    else:
                        p['lead_side'] = None
                        ev.append('%s：同步（最优滞后 %d 分钟，不足一个 %d 分钟 bar）'
                                  % (p['code'], x['lag_min'], LL_PERIOD))
                elif x['why']:
                    ev.append('⚠ %s 的领先-滞后没算：%s' % (p['code'], x['why']))
                k += 1
            if ll_miss:
                # ★合成**一条**：6 条一模一样的"够不着"会把证据链淹掉，
                #   而"看过、都没够着"这个事实一条就说清了。
                #   逐条的信息没丢：`lag_r`/`lag_n` 还在各自的 peer 里。
                ev.append('领先-滞后**没算出来**：%d 个 peer 的最优滞后处峰值相关'
                          '绝对值最大只有 %.2f，低于门槛 %.2f（重叠 %d~%d 根）'
                          % (len(ll_miss), max(abs(r) for _, r, _ in ll_miss),
                             lag_threshold(min(n for _, _, n in ll_miss)),
                             min(n for _, _, n in ll_miss), max(n for _, _, n in ll_miss)))
    return out
