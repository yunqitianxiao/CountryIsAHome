"""关联检测（规格 §3.3）：**采集是主角，分析只做最朴素的一层**。

用户原话：「还要做关联检测，有异动了列出与它关联的版块、个股的情况，做简单的分析，
注意：这个注重信息的采集，弱化分析。」

这句话落成三条可检查的约束：

1. **`collected` 要详实**：哪个源、几行、几毫秒、什么错、快照时间戳、覆盖哪些市场、
   哪些**没去采**（预算用尽 / 代码不在池子里）。这是本模块交付的主要产物之一，
   不是日志 —— 用户要的是"信息采集"，那"采到了什么"就是产品本身。
2. **分析只到这一层为止**：横截面 z、量比、滚动相关、lead-lag。**不**预测、
   **不**给买卖建议、**不**写长论述。`note` 只说事实，长度压在一两行。
3. **没算的数不许有形状**：样本不够的相关性 ⇒ `rel_value: None` + 一句为什么，
   不是 0、也不是一个用 12 个点算出来的 0.87。取不到的字段一律 None（规格 §0.1）。

两个出口（规格 §3.3 逐字）：
    scan(scope='all', codes=None, top=20) -> dict
    anomaly_of(code) -> dict

★数据层按冻结接口 `agent/datasource/`（规格 §3.1）调用；它缺席时**不兜底**
  （兜底 = 一台印行情的机器），只在 `LICAI_LINKAGE_SYNTH=1` 时用合成市场自测。

★预算：深挖（日线/分钟线）是**逐只**调用，必须封顶，超了要**说出来**。
  `LICAI_LINKAGE_BUDGET`（默认 60 次）调上限 —— 它就是"这一轮我愿意花多少次取数"
  这个决定本身。
"""

import os
import datetime as dt

from . import anomaly
from . import relate
# `SourceError` **故意在这里再导出一次**：上层（tools.py / app.py）要 catch 取数失败，
# 而又不该知道它住在 `linkage.collect` 里 —— 模块的对外面只有 `linkage/__init__.py`。
from .collect import Ledger, SourceError, _num, backend_name, backend_err   # noqa: F401

# scope → 去哪几个市场采。★顺序是**固定的**：它是 `collected.markets` 的顺序、
# 也是"哪一行是主快照"的判据。让 dict 的顺序决定输出顺序，跨版本是不保证的。
SCOPE_MARKETS = {'all': ('cn', 'hk', 'us'), 'cn': ('cn',), 'hk': ('hk',), 'us': ('us',)}

BUDGET_ENV = 'LICAI_LINKAGE_BUDGET'
DEFAULT_BUDGET = 60          # 深挖调用次数上限（日线 + 分钟线）
TIMEOUT_ENV = 'LICAI_LINKAGE_TIMEOUT'
DEFAULT_TIMEOUT = 25.0       # 单次调用挂钟上限（秒）
MAX_CODES = 200              # `codes=` 一次最多指定多少个（防手滑传全市场）

RISK_NOTE = '以上是行情快照的采集与最朴素的统计口径，不构成投资建议。'


def _env_num(name, default, lo, hi, cast=float):
    try:
        return max(lo, min(hi, cast(os.environ.get(name) or default)))
    except (TypeError, ValueError):
        return cast(default)


def _now():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M')


# ── 量比：日线到手之后补算（★这是唯一一处"日期对不对齐"的判据）──────────

def _attach_rvol(a, df, row):
    """把量比补进已经检出的异动条目。→ 一句说明（进证据链），算不出就是"为什么"。

    ★**对齐是这里唯一的风险**：量比的分母是"今天之前"的日成交量。如果日线的最后
      一根其实是**今天**、而我们把它也当成基准，量比会被系统性压到 1 以下；
      反过来（最后一根是昨天而我们当成今天）会凭空多算一天。
      两种错法给出的量比都**看着像个正常的数**（0.8 / 2.1），所以必须显式比日期。
    ★日线还没包含今天（盘中常见）时：把整段当日线基准，并**标 `partial`**。
      那是"拿今天没走完的量比一整天的均量"，**天然偏低**，必须写在证据里 ——
      悄悄给个数比明说偏低糟糕得多。
    ★日期对齐与算术**都在 `anomaly.rvol_from_daily` 里**（唯一实现）：
      异动票自己和它的每个 peer 走同一条，见那个函数的说明。
    """
    m = a['metrics']
    r = anomaly.rvol_from_daily(df, row.get('volume'), row.get('ts'))
    m['vol_ratio'] = _num(r['value'], 2)
    m['vol_ratio_days'] = r['n_used']
    m['vol_ratio_partial'] = r['partial']
    if r['value'] is None:
        return '量比没算：%s' % r['why']
    if r['value'] >= anomaly.RVOL_HOT and 'volume_spike' not in a['kinds']:
        a['kinds'] = [k for k in anomaly.KIND_ORDER if k in (a['kinds'] + ['volume_spike'])]
    return ('当日量比 %.2f（当日量 ÷ 前 %d 个交易日均量）%s'
            % (r['value'], r['n_used'],
               '；★今天还没收盘，当日量不完整，这个数**偏低**' if r['partial'] else ''))


# ── 出口 ① ──────────────────────────────────────────────────────────────

def scan(scope='all', codes=None, top=20):
    """全市场扫一遍 → 异动榜 + 关联。返回结构见规格 §3.3。

    `codes`：只看这几只（不在全市场快照里的会用 `snapshot()` 单独补采）。
    `top`：异动榜留前几名（也只对前几名做深挖）。
    """
    lg = Ledger(budget=_env_num(BUDGET_ENV, DEFAULT_BUDGET, 8, 500, int),
                timeout=_env_num(TIMEOUT_ENV, DEFAULT_TIMEOUT, 3.0, 120.0))
    ts = _now()
    try:
        top = max(1, min(int(top or 20), 200))
    except (TypeError, ValueError):
        top = 20

    if scope not in SCOPE_MARKETS:
        return {'ok': False, 'ts': ts, 'scope': scope,
                'reason': '不认识的 scope：%r（只有 %s）' % (scope, list(SCOPE_MARKETS))}
    markets = SCOPE_MARKETS[scope]

    # ── ① 全市场快照（每市场一次调用）──────────────────────────────────
    universe, by_mkt = [], {}
    for mk in markets:
        ok, rows = lg.call('realtime:%s' % mk, 'realtime', mk)
        if ok and rows:
            universe.extend(rows)
            by_mkt[mk] = len(rows)
        elif ok:
            lg.note('realtime(%s) 回了 0 行（市场今天没开？源没数据？）' % mk)
    snap_ok = bool(universe)

    # `codes` 里不在快照里的，单独补采。★补采的行**不进横截面** —— 横截面的分布
    # 必须来自全市场，把几只补采的票混进去算 sd，等于用"我要测的东西"去定义"正常"。
    extra = []
    if codes:
        want = [str(c) for c in list(codes)[:MAX_CODES]]
        have = {r.get('code') for r in universe}
        miss = [c for c in want if c not in have]
        if miss:
            ok, rows = lg.call('snapshot:%s' % ','.join(miss[:6]), 'snapshot', miss)
            if ok and rows:
                extra.extend(rows)
            else:
                lg.note('%d 只指定代码不在快照里，补采也没取到' % len(miss))
    elif not snap_ok:
        # ★"取不到"的**原因**要跟着一起出去（数据层没建好 / 源挂了 / 超时，
        #   三件事的处理完全不同），而它现在只躺在 collected.sources[0].err 里 ——
        #   上层只读 reason 的话，看到的是一句"没取到"，那和"源今天没数据"分不开。
        return {'ok': False, 'ts': ts, 'scope': scope, 'backend': backend_name(),
                'reason': '一个市场的实时快照都没取到（%d 次调用全失败）—— 没有数据就不出榜单，'
                          '一个空榜单会被读成"今天没有异动"。原因：%s'
                          % (lg.n_fail(), backend_err() or '见 collected.sources'),
                'collected': lg.collected(ts, 0, list(markets), by_mkt)}

    scan_rows = universe + extra

    # ── ② 横截面 z（分市场，见 anomaly 文件头）────────────────────────
    cs = anomaly.cross_section(universe)
    if codes:
        want = {str(c) for c in list(codes)[:MAX_CODES]}
        scan_rows = [r for r in scan_rows if r.get('code') in want
                     or r.get('code') in {e.get('code') for e in extra}]
    all_anom = anomaly.detect(scan_rows, cs)
    anom_codes = {a['code'] for a in all_anom}
    out_top = all_anom[:top]
    row_of = {r.get('code'): r for r in scan_rows}

    # ── ③ 板块清单（每种 kind 一次调用，服务整个 scan）──────────────────
    board_idx, berrs = relate.board_index(lg) if out_top else ({}, [])
    for e in berrs:
        lg.note(e)

    # ── ④ 深挖：一只一只取日线/分钟线，预算用尽就停（并说出来）──────────
    links, ll_used = {}, 0
    for a in out_top:
        code = a['code']
        if not lg.can_spend(1):
            lg.skip('links:%s' % code, '深挖预算用尽（%s=%d）' % (BUDGET_ENV, lg.budget))
            continue
        lg.spend(1)
        row = row_of.get(code) or {}
        ll_ok = ll_used < relate.LL_CODES_MAX
        rvol_msg = []
        try:
            links[code] = relate.links_for(
                code, row, lg, board_idx, anom_codes, ll_ok, universe_rows=universe,
                on_self_daily=lambda c, df: rvol_msg.append(_attach_rvol(a, df, row)))
        except Exception as e:                               # noqa: BLE001
            # ★单只票的深挖炸了，不该把整轮 scan 带走 —— 但要留痕（进 collected.notes）
            lg.note('%s 的关联挖取出错：%s: %s' % (code, type(e).__name__, str(e)[:80]))
        else:
            if ll_ok and links[code].get('peers'):
                ll_used += 1
        # 量比那一句进证据链：它是个数，而"这个数是怎么算的/为什么没算"必须跟着它走。
        if rvol_msg and links.get(code):
            links[code]['evidence'].append(rvol_msg[0])

    # ── ⑤ 账 ────────────────────────────────────────────────────────────
    ok_h, health = lg.call('health', 'health')
    rows_total = len(scan_rows)
    collected = lg.collected(ts, rows_total, list(markets), by_mkt,
                             health=(health if ok_h else None))
    if not snap_ok:
        collected['notes'].append('全市场快照没取到，只有指定的那几只（快照行 %d）' % len(extra))

    links_n = sum(1 for c in links if links[c].get('peers') or links[c].get('boards'))
    note = ('采到 %d 行快照（%s），用时 %s ms；检出 %d 只异动，其中 %d 只算了关联。'
            % (rows_total, '、'.join('%s %d 行' % (m, n) for m, n in by_mkt.items()) or '无',
               collected['ms'], len(all_anom), links_n))
    if lg.n_fail():
        note += '%d 次采集失败（见 collected.sources）。' % lg.n_fail()
    if len(all_anom) > len(out_top):
        note += '榜单只留前 %d 只。' % len(out_top)
    if lg.skipped:
        # ★"只" vs "次"：`skipped` 里记的是**调用**（一个 peer 一次），不是票。
        #   写成"有 122 只没做深挖"会把 6 个 peer × 几只票说成 122 只票 ——
        #   一个数错了单位的账本比没有账本更容易被当真。
        note += '有 %d 次深挖没做（%s，逐条见 collected.skipped）。' % (
            len(lg.skipped), lg.skipped[0]['why'])
    if cs:
        note += ' z 用的是当日横截面分布（%s），不是各票自己的历史。' % '、'.join(sorted(cs))

    return {
        'ok': True, 'ts': ts, 'scope': scope, 'backend': backend_name(),
        'snapshot': {'ts': _snap_ts(scan_rows), 'market': markets[0] if len(markets) == 1 else 'multi',
                     'rows': rows_total, 'markets': list(markets), 'by_market': by_mkt},
        'collected': collected,
        'cross_section': _cs_brief(cs),
        'anomalies': out_top,
        'links': links,
        'note': note,
        'risk_note': RISK_NOTE,
    }


# ── 出口 ② ──────────────────────────────────────────────────────────────

def anomaly_of(code):
    """单只票的异动 + 关联。**z 仍然按它所在市场的当日横截面算** ——
    单看一只票是判不出"异动"的（见 anomaly 文件头的理由）。
    """
    lg = Ledger(budget=_env_num(BUDGET_ENV, DEFAULT_BUDGET, 8, 500, int),
                timeout=_env_num(TIMEOUT_ENV, DEFAULT_TIMEOUT, 3.0, 120.0))
    ts = _now()
    code = str(code or '').strip()
    if not code:
        return {'ok': False, 'ts': ts, 'reason': '没给代码'}

    ok, rows = lg.call('snapshot:%s' % code, 'snapshot', [code])
    if not ok or not rows:
        return {'ok': False, 'ts': ts, 'code': code, 'backend': backend_name(),
                'reason': '取不到 %s 的实时快照（%s）。原因：%s'
                          % (code, '源失败' if not ok else '源回了空',
                             backend_err() or '（源失败，见 collected.sources）'),
                'collected': lg.collected(ts, 0, [])}
    row = rows[0]
    market = row.get('market') or 'cn'

    # 横截面：拿它所在市场的全市场快照，z 才有分母
    universe = list(rows)
    cs, cs_why = {}, 'scope 里没有这个市场'
    ok_u, uni_rows = lg.call('realtime:%s' % market, 'realtime', market)
    if ok_u and uni_rows:
        universe = uni_rows
        cs = anomaly.cross_section(uni_rows)
        cs_why = None
    else:
        cs_why = '该市场的全市场快照没取到，z 分数算不了（只有一个价格，没有"相对谁"）'

    det = anomaly.detect([row], cs)
    a = det[0] if det else None
    # ★`anom_codes` 必须是**整个市场那一批**算出来的异动集合，不能只是"我自己"。
    #   它进的是 peer 排序（`relate._peer_rank` 第 2 个键：自己也异动的排前面），
    #   于是"这一批里谁异动了"会改变"先深挖哪几个 peer"。
    #   只放自己进去的话，`scan()` 与 `anomaly_of(code)` 会对**同一只票**给出不同的
    #   peer 顺序、甚至不同的 peer 集合，而两边都长得像"算过了"。
    #   这里手上就有全市场快照（横截面那一步已经取了），重算一遍是本地计算、零次调用。
    anom_codes = {code}
    if ok_u and uni_rows:
        try:
            anom_codes = {d['code'] for d in anomaly.detect(uni_rows, cs)}
        except Exception as e:                               # noqa: BLE001
            lg.note('全市场异动集合没算出来（%s: %s），peer 排序退回只看本票'
                    % (type(e).__name__, str(e)[:60]))
        anom_codes.add(code)
    board_idx, berrs = relate.board_index(lg)
    for e in berrs:
        lg.note(e)
    links, rvol_msg = {}, []
    if a is not None:
        lg.spend(1)
        try:
            links[code] = relate.links_for(
                code, row, lg, board_idx, anom_codes, True, universe_rows=universe,
                on_self_daily=lambda c, df: rvol_msg.append(_attach_rvol(a, df, row)))
        except Exception as e:                               # noqa: BLE001
            lg.note('关联挖取出错：%s: %s' % (type(e).__name__, str(e)[:80]))
        if rvol_msg:
            links[code]['evidence'].append(rvol_msg[0])

    ok_h, health = lg.call('health', 'health')
    note = ('采集 %d 次调用（%s），快照 %s %s；%s'
            % (len(lg.items), backend_name(), market, code,
               '检出：%s' % '、'.join(a['kinds']) if a else '没有命中任何异动标签'))
    if cs_why:
        note += '；%s' % cs_why
    if lg.n_fail():
        note += '；%d 次采集失败（见 collected.sources）' % lg.n_fail()
    return {
        'ok': True, 'ts': ts, 'code': code, 'name': row.get('name'), 'market': market,
        'backend': backend_name(),
        'snapshot': {'ts': row.get('ts'), 'market': market, 'rows': len(universe)},
        'collected': lg.collected(ts, len(universe), [market], {market: len(universe)},
                                  health=(health if ok_h else None)),
        'cross_section': _cs_brief(cs),
        'anomaly': a,
        'links': links,
        'note': note,
        'risk_note': RISK_NOTE,
    }


# ── 小工具 ──────────────────────────────────────────────────────────────

def _snap_ts(rows):
    from .collect import snapshot_ts
    return snapshot_ts(rows)


def _cs_brief(cs):
    """横截面统计的**压缩**版：给前端看"z 是拿什么分布算的"。
    ★只留 n/mu/sd 三个数：完整分布不该进任何摘要通道（规格 §0 的体量纪律）。"""
    out = {}
    for mk, d in (cs or {}).items():
        out[mk] = {k: {'n': v['n'], 'mu': _num(v['mu'], 3), 'sd': _num(v['sd'], 3)}
                   for k, v in d.items()}
    return out
