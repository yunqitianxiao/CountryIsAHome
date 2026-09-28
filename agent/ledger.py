#!/usr/bin/env python3
"""预测台账：每一次预测、以及它到期的真实结果，只追加地记在一个 JSONL 里。

★ 这是本站**第二处**业务落盘（第一处是 `apikey.write_env` 写 `.env`）。
  全仓没有 sqlite、没有日志、没有会话存储 —— 所以"服务端零用户状态"那句承诺，
  唯一被撕开的口子就是这里。README 把这条边界写清楚了，那个承诺仍然成立，
  靠的是下面两条**硬边界**，各有判据守着：

    ① 台账里**只有模型输出**，一个字的用户输入都没有。
       用户问了什么、姓什么、画像填了什么，一概不进来。symbol / market / name
       是 `feed.resolve()` 解析出来的，本身就是工具自己的产物。
    ② 台账**只追加**。没有删除、没有改写、没有"清空"接口。

★ 为什么是"只追加"而不是"一个 JSON 文件反复改写"：

  改写型的状态文件有个不好查的坏法 —— 一次写入被打断（磁盘满、进程被 kill、
  两个线程同时写），整个文件就废了，而且是**过去全部记录一起废**。
  只追加的日志最坏只丢最后一行，前面的一律还在。台账的全部价值就是"它是真的
  发生过的事"，所以它必须是这一种。

⇒ 于是"这条预测打分了没有"不是一个字段，而是**日志里的第二种记录**：

      {"kind": "pred",  "id": ..., ...}     预测发出时写
      {"kind": "score", "id": ..., ...}     到期回填时写

  当前状态 = 把日志读一遍折出来（`state()`）。这也意味着任何一条历史记录
  都可以被后来的记录**补充**，但永远改不掉。

★ 一条纪律：**打分不许在这里实现**。
  打分全部走 `agent/score.py`，和离线回测共用同一把尺子。两边各写一份的话，
  "回测 52.3%"和"台账 60%"会同时挂在站上，而且没人看得出哪个对（两个都是"算出来的"）。
  所以这里存的是**打分的原始输入**（模型给的收盘价路径、真实收盘价路径、基准价），
  不是算好的分。这个选择还有一个白拿的好处：`score.py` 以后修好了，
  **整个历史台账跟着一起修**，不用重新跑模型。
"""

import hashlib
import json
import os
import threading
import time
import uuid

import score

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 模型权重放在这儿（`runner.MODEL_DIR` 的同一条路径 —— 这里不 import runner，
# 那个模块一进来就要拖 torch，台账读一条记录不该有那个代价）。
MODEL_DIR = os.path.join(ROOT, 'models', 'Kronos-small')

DEFAULT_PATH = os.path.join(ROOT, 'data', 'predictions.jsonl')

# ★ 隔离口。照 `apikey.env_file()` 的 `LICAI_ENV_FILE` 先例。
#
#   没有它，每跑一次 `verify_agent.py`（它会真的起服务、真的发预测请求）
#   就往 `data/predictions.jsonl` 里灌测试数据 —— **入库的台账被测试数据污染**，
#   而症状是"战绩曲线莫名其妙变难看"，没人会想到是测试写的。
#   `Server.__enter__` 起测试服务时会把它指到临时目录。
LEDGER_ENV = 'LICAI_LEDGER'

# 写锁：flask 是 threaded=True。只追加 + 单行单次 write 已经躲掉了大部分撕裂，
# 但"两行交错"依然可能发生，而交错的 JSONL 会读出**语法错误**而不是"少一条"。
_LOCK = threading.Lock()

# `model_sha` 缓存（算一次 sha256 要读 99 MB）
_SHA = {}


def path():
    """台账文件路径。`LICAI_LEDGER` 可以整个换掉它（判据靠这个不碰真台账）。"""
    return os.environ.get(LEDGER_ENV) or DEFAULT_PATH


def model_sha():
    """当前模型权重的指纹（前 12 位）。

    ★为什么要它：台账会跨模型版本攒很长时间。**换了权重而台账不说**，
      就会出现"历史命中率和今天的命中率其实来自两个模型"这种没法从页面上看出来的事。
      `Kronos-small` 的权重是 `models/` 下的（不入库，见 .gitignore），
      读不到就**如实返回 None + 原因**，不编一个假的指纹。
    """
    p = os.path.join(MODEL_DIR, 'model.safetensors')
    if p in _SHA:
        return _SHA[p]
    try:
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        v = {'sha': h.hexdigest()[:12], 'why': None}
    except OSError as e:
        v = {'sha': None, 'why': '读不到权重文件（%s）：%s' % (p, e.__class__.__name__)}
    _SHA[p] = v
    return v


def _line(rec):
    """一条记录 → 一行。`ensure_ascii=False` + 紧凑分隔符：台账是要入库的文本，
    中文写成 \\uXXXX 会让它没法用眼睛看、也没法 grep。"""
    return json.dumps(rec, ensure_ascii=False, separators=(',', ':')) + '\n'


def append(rec, p=None):
    """追加一条。**返回 (ok, 错误说明)**，不抛。

    ★为什么用返回值而不是抛异常：落盘失败不该把用户等了几十秒才拿到的那条预测
      一起弄丢。但**也不许静默** —— 调用方必须把这个结果摆到台面上（"这次没记上，
      原因是……"）。`game._fail()` 那句"抓不到就明确报错，绝不编数字"是同一个道理：
      记不上不是"少一条记录"，是"这条战绩以后查不到了"。
    """
    p = p or path()
    rec = dict(rec)
    rec.setdefault('ts', time.strftime('%Y-%m-%d %H:%M:%S'))
    try:
        os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
        with _LOCK:
            with open(p, 'a', encoding='utf-8') as f:
                f.write(_line(rec))
                f.flush()
                os.fsync(f.fileno())      # 崩溃前落盘，别留在页缓存里
        return True, None
    except OSError as e:
        return False, '%s: %s' % (e.__class__.__name__, e)


# ── 读 ────────────────────────────────────────────────────────────────

def read_all(p=None):
    """把日志读成记录列表 + 坏行计数。

    ★坏行**不许静默跳过**。这里返回 `bad` 的条数和头几行行号，调用方要把它摆到页面上：
      一个读不动的台账看起来和"空台账"一模一样，而"战绩是空的"和"你的记录坏了"
      是两回事。半行 JSON（进程被 kill 在写一半）是最常见的成因。
    """
    p = p or path()
    recs, bad = [], []
    if not os.path.exists(p):
        return recs, bad
    try:
        with open(p, encoding='utf-8') as f:
            for i, ln in enumerate(f, 1):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                    if isinstance(r, dict):
                        recs.append(r)
                    else:
                        bad.append(i)
                except ValueError:
                    bad.append(i)
    except OSError:
        return recs, [-1]
    return recs, bad


def state(p=None):
    """日志 → 当前状态：每条预测 + 它的分数（如果已回填）。

    排序按预测发出的时间倒序（最近的在最前）—— 页面要的就是这个顺序。
    """
    recs, bad = read_all(p)
    preds, scores, skips = {}, {}, {}
    for r in recs:
        k = r.get('kind')
        if k == 'pred' and r.get('id'):
            preds[r['id']] = r
        elif k == 'score' and r.get('id'):
            scores[r['id']] = r
        elif k == 'skip' and r.get('id'):
            skips[r['id']] = r
    out = []
    for i, pr in preds.items():
        e = dict(pr)
        e['score_rec'] = scores.get(i)
        e['skip_rec'] = skips.get(i)
        out.append(e)
    out.sort(key=lambda e: e.get('ts') or '', reverse=True)
    return {'entries': out, 'bad_lines': bad, 'path': p or path()}


# ── 打分（回填）──────────────────────────────────────────────────────

def _bars_after(df, asof, n):
    """`asof`（含）之后的前 n 根真实 K 线。不够 n 根 → None（还没到期）。

    ★对齐口径**和离线回测完全一样**：都是"起点之后的第 1..n 根真实交易 bar"。
      模型的未来时间戳是 `pd.bdate_range`（工作日），碰上节假日会比真实交易日多，
      但两边取的都是"真实 bar 的第 i 根"，所以尺子是同一把。这一点必须一致 ——
      不一致的话台账和回测的命中率就不可比了，而页面上看不出差别。
    """
    dts = [str(x)[:10] for x in df['dt']]
    idx = [i for i, d in enumerate(dts) if d > str(asof)[:10]]
    if len(idx) < n:
        return None
    take = idx[:n]
    return [float(df['close'].iloc[i]) for i in take], [dts[i] for i in take]


def pending(st):
    """还没打分的预测，按标的归堆 —— 一个标的一次取数能打完它名下所有条目。

    ★已经判过"这条永远算不了"的（`skip_rec`）**不再排队**。不排掉的话，
      它们会永远挂在"还有 N 条在路上"，而那个 N 永远不减小 ——
      一个不会动的数比一个错的数更坏，因为它看起来像是"还在等"。
    """
    by = {}
    for e in st['entries']:
        if e.get('score_rec') or e.get('skip_rec'):
            continue
        by.setdefault((e.get('market'), e.get('code')), []).append(e)
    return by


def _note_skip(eid, why, p):
    """记一笔"这条永远算不了，别等了"。**只追加**，和别的记录一样。

    ★必须落盘而不是只在内存里判：那个判断依赖当次取到的行情窗口，
      不记下来的话，下次请求又要重新取一遍才能得出同一个结论 ——
      而每次都会在页面上闪过一次"排队中"。判断一次、记下来、以后读它。
    ★只给**永久性**的失败记：取不到行情是**暂时**的（下次可能就通了），
      那种不记，留着下次重试。
    """
    ok, err = append({'kind': 'skip', 'id': eid, 'why': why,
                      'skipped_ts': time.strftime('%Y-%m-%d %H:%M:%S')}, p)
    return ok, err


def backfill(st=None, fetch=None, p=None, limit=None):
    """到期的取真实 K 线打分，落一条 `score` 记录。惰性调用（请求时 + 启动时）。

    `fetch` 不给就现 import `feed` —— 参数化是为了让判据能塞一个假行情进来，
    不必真的上网（本仓库的老规矩：判据不该靠"今天网通不通"）。

    返回一份**报告**，逐条说明发生了什么。**每一类失败都要有名字**：
    取不到数 / 还没到期 / 行情窗口覆盖不到起点 / 打分不成立 —— 少一类，
    页面上就会出现"这一条为什么不打分"的空白。
    """
    if st is None:
        st = state(p)
    if fetch is None:
        import feed as fetch                                    # noqa: PLW0127

    rep = {'scored': 0, 'fetched': 0, 'not_mature': 0, 'no_data': 0,
           'out_of_window': 0, 'bad': 0, 'written': 0, 'write_failed': [],
           'notes': []}
    for (market, code), entries in pending(st).items():
        if limit and rep['fetched'] >= limit:
            rep['notes'].append('到取数上限 %d 只，剩下的下次再说' % limit)
            break
        try:
            df = fetch.fetch(market, code, days=1200)
        except Exception as e:                                   # noqa: BLE001
            rep['no_data'] += len(entries)
            rep['notes'].append('%s(%s) 取不到行情：%s' % (code, market, str(e)[:80]))
            continue
        rep['fetched'] += 1
        if df is None or not len(df):
            rep['no_data'] += len(entries)
            rep['notes'].append('%s(%s) 回了个空表' % (code, market))
            continue
        # ★起点必须落在这次取的窗口里。落不进去有两种可能：这条太老，
        #   或者窗口的第一根就已经晚于起点 —— 后者会让我们把"第 3 根"当成"第 1 根"，
        #   算出一个看着正常、其实错位的结果。宁可说"这条算不了"。
        span = [str(x)[:10] for x in df['dt']]
        for e in entries:
            n = int(e.get('pred_len') or 0)
            if str(e.get('asof'))[:10] < span[0]:
                # 永久性：这条比行情窗口还老，**再等也不会变**，记一笔别再排队
                rep['out_of_window'] += 1
                _note_skip(e.get('id'),
                           '行情窗口覆盖不到它的起点（%s，这次取到的最早是 %s）'
                           % (e.get('asof'), span[0]), p)
                continue
            got = _bars_after(df, e.get('asof'), n)
            if got is None:
                rep['not_mature'] += 1        # 暂时性：还在等，**不记 skip**
                continue
            actual, adts = got
            pred = e.get('pred_close') or []
            if len(pred) != n or not n:
                rep['bad'] += 1
                why = '这条的预测路径是 %d 根、说的是 %d 根' % (len(pred), n)
                rep['notes'].append('%s %s' % (e.get('id'), why))
                _note_skip(e.get('id'), why, p)
                continue
            rec = {'kind': 'score', 'id': e.get('id'),
                   'actual_close': actual, 'actual_last_date': adts[-1],
                   'scored_ts': time.strftime('%Y-%m-%d %H:%M:%S')}
            ok, err = append(rec, p)
            if ok:
                rep['scored'] += 1
                rep['written'] += 1
            else:
                rep['write_failed'].append('%s: %s' % (e.get('id'), err))
    return rep


def rows_of(entries, source=None, pred_len=None):
    """台账条目 → `score.py` 的打分行。**打分只在这里发生，且只走 score.py。**

    只有**已经回填、但两份路径对不上**的条目会进 `skipped`（那才是真问题：
    数据坏了或者写岔了）。"还没到期"**不算 skipped** —— 它是 `n_pending`，
    两者混成一个数的话，页面上"78 条在排队"会看起来像"78 条坏了"。
    """
    rows, skipped = [], []
    for e in entries:
        if source is not None and e.get('source') != source:
            continue
        if pred_len is not None and int(e.get('pred_len') or 0) != pred_len:
            continue
        sc = e.get('score_rec')
        if not sc:
            continue                                   # 还没到期，见上面的 ★
        try:
            r = score.outcome_row(e['close_last'], e['pred_close'], sc['actual_close'],
                                  window_mean=e.get('window_mean'))
        except (ValueError, KeyError, TypeError) as ex:
            skipped.append('%s：%s' % (e.get('id'), str(ex)[:60]))
            continue
        if e.get('past_chg') is not None:
            score.with_momentum(r, e['past_chg'])
        rows.append(r)
    return rows, skipped


def group_keys(entries):
    """台账里现存的 (source, pred_len) 组合，各多少条 —— 页面按这个分区。"""
    g = {}
    for e in entries:
        k = (e.get('source') or 'live', int(e.get('pred_len') or 0))
        g[k] = g.get(k, 0) + 1
    return sorted(g.items(), key=lambda kv: -kv[1])


def coverage(entries, source=None, pred_len=None):
    """名义区间的**经验覆盖率**：到期之后，真实**终点**落进区间的比例。★没有真区间时**一个数字都不给**。

    只有 `interval_kind == 'sample_quantile'`（多采样出的极差区间，阶段 3 才有）
    的条目才算得出来。单次采样的那条路径的 min/max **不是区间** ——
    拿它算"覆盖率"会得到一个看着像结论、其实在量别的东西的数。
    和 `accuracy.py` 那条"要么连基线一起给，要么整句不给"是同一条纪律。

    ★口径是**终点**（`actual_close[-1]`），与 `interval_level` 的名义含义对齐；
      理由见下面那一步的注释 —— 按"每一根"算会把数系统性抬高。
    """
    elig = [e for e in entries
            if e.get('interval_kind') == 'sample_quantile'
            and (source is None or e.get('source') == source)
            and (pred_len is None or int(e.get('pred_len') or 0) == pred_len)]
    if not elig:
        return {'n': 0, 'level': None, 'covered': None, 'rate': None,
                # ★这句话会**原样渲染到页面上**（`Ledger.tsx` 的覆盖率那一栏），
                #   而且 `verify_agent.py` ⑤ 拿它和页面上的字**逐字比**（抹掉空白后）
                #   —— 所以它是**用户文案**，不是给维护者看的注释：
                #   不许出现"多采样""min/max""校准"这类词，也不许出现 `%`
                #   （判据要求这一栏一个百分号都没有）。
                #   ★改这里就是改页面。别在 `agent/uicopy.py` 里另写一份替换 ——
                #     判据比的是**服务端这一句**，两处会分叉，而分叉的那一侧是静默的。
                'why': '这个数现在算不出来：每条预测只跑了一次，'
                       '一次跑出来的最高价和最低价算不上一个"区间"，'
                       '拿它算覆盖率量到的是别的东西。'
                       '没核对过的覆盖率只是个好看的数，所以这里不给'}
    hit = tot = 0
    for e in elig:
        sc = e.get('score_rec')
        if not sc or not sc.get('actual_close'):
            continue
        lo, hi = e.get('lo'), e.get('hi')
        if lo is None or hi is None:
            continue
        # ★比的是**终点**那一根，不是视野里每一根 —— 这是 `interval_level` 说得通
        #   的前提。`lo`/`hi` 是 N 条采样路径**终点**的极差，它的名义水平来自
        #   顺序统计量：一个新样本落在 N 个样本极差内的概率是 (N-1)/(N+1)。
        #   拿它去比视野里每一根，量到的就变成"实际路径有多少根待在带子里" ——
        #   那是别的问题（而且早期几根离基准价近，会把这个数系统性抬高，
        #   于是页面上会出现一个"覆盖率 92%"而名义水平只有 80% 的假结论）。
        tot += 1
        hit += 1 if lo <= sc['actual_close'][-1] <= hi else 0
    if not tot:
        return {'n': 0, 'level': None, 'covered': None, 'rate': None,
                'why': '区间有了，但一条到期的都还没有'}
    return {'n': tot, 'level': elig[0].get('interval_level'),
            'covered': hit, 'rate': round(100.0 * hit / tot, 1), 'why': None}


def summarise(entries, source, pred_len):
    """一个分区 → 页面上那块数字。**算数一律走 `score.py`。**

    返回 None 表示"这一区还没有到期的条目"—— 页面要照实说"还没到期"，
    而不是显示一个 0% 的命中率（那是两回事）。
    """
    rows, skipped = rows_of(entries, source=source, pred_len=pred_len)

    def _mine(e):
        return (e.get('source') == source
                and int(e.get('pred_len') or 0) == pred_len)

    n_all = sum(1 for e in entries if _mine(e))
    n_pending = sum(1 for e in entries
                    if _mine(e) and not e.get('score_rec') and not e.get('skip_rec'))
    n_skip = sum(1 for e in entries if _mine(e) and e.get('skip_rec')
                 and not e.get('score_rec'))
    out = {'source': source, 'pred_len': pred_len, 'n_total': n_all,
           # ★这三个数必须是**三个**：`n_pending`（还在等，正常的）、
           #   `n_skip`（永远算不了，已经判过了）、`skipped`（到期了却对不上，坏的）。
           #   合成一个的话，"还有 78 条在路上"会读成"78 条坏了"。
           'n_scored': len(rows), 'n_pending': n_pending, 'n_skip': n_skip,
           'skip_reasons': sorted({e['skip_rec'].get('why') or '（没写原因）'
                                   for e in entries
                                   if _mine(e) and e.get('skip_rec')
                                   and not e.get('score_rec')}),
           'skipped': skipped,
           'coverage': coverage(entries, source=source, pred_len=pred_len)}
    if not rows:
        out['total'] = None
        out['verdict'] = None
        return out
    t = score.summarise(rows, pred_len=pred_len)
    out['total'] = t
    # ★"算不算赢"走唯一实现（`score.verdict`），和回测那句话同一个判断。
    out['verdict'] = score.verdict(t)
    return out


def blocks(entries):
    """台账 → 页面上那几个分区。**live 与 backfill 绝不混成一个数。**

    ★为什么非分不可：冷启动回填是把离线回测的滚动窗口灌进台账（几百条），
      而 live 是用户在页面上一次次点出来的（几条）。混在一起汇总的话，
      "台账战绩"这个数**几乎全部由回测窗口决定**，而它看起来像是"这个站在线
      跑出来的成绩"。那不是统计口径之争，那是拿旧数据冒充新战绩。
    """
    return [dict(summarise(entries, s, n), n_raw=c)
            for (s, n), c in group_keys(entries)]


def import_backtest(rows, p=None, sha=None):
    """把 `backtest_kronos.py` 的滚动窗口灌进台账（**冷启动回填**）。

    ★为什么需要它：站刚起来时台账是空的，"滚动战绩"那块就一直是"还没到期"，
      要等三十个交易日才有第一条数字。把离线回测已经跑出来的那些窗口灌进来，
      页面第一天就有东西看 —— 而且它们是**已经到期、已经知道答案的**，
      不存在"用未来数据"的问题。

    ★`source='backfill'`，和 live **分区显示**。混成一个数的话，"台账战绩"
      几乎全部由回测窗口决定，而它看起来像"这个站在线跑出来的成绩" ——
      那不是口径之争，那是拿旧数据冒充新战绩。

    ★`id` 是**按内容算的**（code+asof+pred_len），不是随机的：
      于是重复导入是幂等的（`state()` 按 id 去重，后写的覆盖先写的、内容一样）。
      随机 id 的话，重跑一次回测台账就翻一倍，而页面上只是"样本数变多了"。

    `rows` 里每行要带 `_raw`（见 `backtest_kronos.one_origin`）和
    `date` / `_sym`（见 `run_symbol` 与 `main`）。
    """
    n_new = n_bad = 0
    fails = []
    for r in rows:
        raw, sym = r.get('_raw'), r.get('_sym')
        if not raw or not sym:
            n_bad += 1
            continue
        market, code, name = sym
        pc, ac = raw['pred_close'], raw['actual_close']
        if len(pc) != len(ac) or not pc:
            n_bad += 1
            continue
        eid = 'bf-%s-%s-%s' % (code, r['date'], len(pc))
        base = raw['close_last']
        ok1, e1 = append({
            'kind': 'pred', 'id': eid, 'source': 'backfill',
            'symbol': code, 'code': code, 'market': market, 'name': name,
            'asof': r['date'], 'pred_len': len(pc),
            'lookback': raw.get('lookback'),
            'sample_count': 1,
            'direction': score.sign_of(r.get('p_chg'), score.DEADBAND) or 'flat',
            'close_last': base, 'pred_close_last': pc[-1],
            'pred_close': [round(float(x), 6) for x in pc],
            'interval_kind': 'path_range',
            'lo': round(min(pc), 4), 'hi': round(max(pc), 4),
            'window_mean': raw.get('window_mean'),
            'past_chg': round(r['past_chg'], 6) if r.get('past_chg') is not None else None,
            'model_sha': sha if sha is not None else model_sha()['sha'],
            'repaired_bars': None,      # 回测那条路没记这个数，就不编一个
        }, p)
        ok2, e2 = append({
            'kind': 'score', 'id': eid,
            'actual_close': [round(float(x), 6) for x in ac],
            'actual_last_date': raw.get('actual_last_date'),
            'scored_ts': time.strftime('%Y-%m-%d %H:%M:%S'),
            'origin': 'backfill_import',
        }, p)
        if ok1 and ok2:
            n_new += 1
        else:
            fails.append('%s: %s' % (eid, e1 or e2))
    return {'imported': n_new, 'bad': n_bad, 'write_failed': fails}


def health(p=None):
    """页眉那条：台账在哪、多少条、有没有坏行。★路径要露出来 ——
    用户有资格知道这个站往磁盘上写了什么、写在哪儿。"""
    st = state(p)
    n_scored = sum(1 for e in st['entries'] if e.get('score_rec'))
    return {'path': st['path'], 'n': len(st['entries']), 'n_scored': n_scored,
            'bad_lines': st['bad_lines'],
            'exists': os.path.exists(st['path'])}


if __name__ == '__main__':
    import sys
    st = state()
    print('台账：%s' % st['path'])
    print('%d 条预测（%d 条已回填），坏行 %s'
          % (len(st['entries']), sum(1 for e in st['entries'] if e.get('score_rec')),
             st['bad_lines'] or '无'))
    for b in blocks(st['entries']):
        print('  [%s] pred_len=%d  %d 条（到期 %d）'
              % (b['source'], b['pred_len'], b['n_total'], b['n_scored']))
    sys.exit(0)
