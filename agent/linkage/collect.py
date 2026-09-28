"""采集账本 + 数据层选择。★这是本模块的重点（用户原话：「注重信息的采集，弱化分析」）。

三条纪律，每一条都对应一种"看着像结论、其实不可复算"的坏法：

1. **每一次外部调用都要留痕**：源 id / 成没成 / 几行 / 几毫秒 / 什么错。
   失败**如实进账**，绝不静默吞成空。静默吞掉的后果不是"少了一行日志"——
   是页面上出现「今天没有异动」，而真相是「今天一个源都没通」。
   这两句话对用户是完全不同的事，而屏幕上长得一模一样。

2. **深挖预算必须显式，超了要说出来。** 截面（全市场实时）一次调用就够；
   而"量比 / 相关 / 领先-滞后"全都要**历史**，历史是**一只票一只票**取。
   5000 只就是 5000 次调用 —— 所以必须设上限，且**上限用尽本身要进账本**
   （`skipped` + `note`）。不说的话，"算不出相关"会被读成"没有相关"。
   ★宁可少算，不可假算。

3. **取不到就是 None，不是 0。** 一律过 `feed._num()`（规格 §0.7 点名的那一个）。

★数据层的选择**不接受"自动兜底"**：
  `agent/datasource/` 取不到时**不会**悄悄换成合成市场 —— 那等于一台印行情的机器。
  桩只在**服务端**环境变量 `LICAI_LINKAGE_SYNTH` 显式打开时生效（照抄
  `agent/feed.py: LICAI_FEED_STUB` 与 `agent/news/__init__.py` 那条纪律：
  一个客户端能打开的桩就是一台伪造行情的机器）。datasource 缺席 ⇒ `ok=False` + 人话原因。
"""

import os
import time
import threading

import pandas as pd

# ★规格 §0.7 说"一律过 `agent/feed.py: _num()`"。这里就用**那一个对象**，
#   不是抄一份等价实现 —— 抄一份的话，两边的 NaN/inf 规则哪天分叉了，
#   没有任何东西会告诉你。抓不到（`agent/` 不在 sys.path 上，比如单测直接跑本文件）
#   才退回本地等价实现，且 `_probe.py` 里有一条**对照判据**钉着两者在边界值上一致。
try:
    import feed as _feed
    _num = _feed._num
    NUM_SRC = 'feed._num'
except Exception:                                            # noqa: BLE001
    import math
    NUM_SRC = 'linkage.collect._num_fallback'

    def _num(x, nd=4):
        try:
            if x is None:
                return None
            f = float(x)
        except (TypeError, ValueError):
            return None
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, nd)


class SourceError(RuntimeError):
    """取数失败。★与 `feed.SourceError` **同名同形但不是同一个类**：
    本模块只 catch 自己这一层的取数错误，不该因为数据层换了实现就把异常类型也绑死。
    合成市场（`_synthmarket.py`）用的是**这个**类，所以调用方只要 catch 一个。
    """

    def __init__(self, code, msg, hint=''):
        super().__init__(msg)
        self.code, self.hint = code, hint


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


# ── 数据层选择 ──────────────────────────────────────────────────────────
SYNTH_ENV = 'LICAI_LINKAGE_SYNTH'
# 本模块真正用到的 7 个名字（规格 §3.1 的冻结接口）。少了任何一个都算"这层不能用"，
# 因为一半的函数能跑、另一半 KeyError，比整层缺席更难查。
DS_NAMES = ('realtime', 'snapshot', 'boards', 'board_members', 'stock_boards', 'daily', 'minute')

_BK = None
_BK_LOCK = threading.Lock()
_BK_INFO = {'name': None, 'err': None}


def synth_requested():
    """桩是否**被显式要求**。只认服务端 env（见文件头）。"""
    return bool(os.environ.get(SYNTH_ENV))


def backend():
    """→ 数据层模块对象（有那 7 个函数）。取不到就抛 SourceError，**不兜底**。"""
    global _BK
    if _BK is not None:
        return _BK
    with _BK_LOCK:
        if _BK is not None:
            return _BK
        if synth_requested():
            from . import _synthmarket as m
            _BK_INFO['name'] = 'SYNTH（%s=1，合成市场，不是真行情）' % SYNTH_ENV
            _BK = m
            return _BK
        try:
            import datasource as m
        except Exception as e:                               # noqa: BLE001
            raise SourceError(
                'NO_DATASOURCE',
                '取不到数据层：没有 `agent/datasource/`（%s）。'
                '本模块**不会**用合成数据顶替 —— 那会让你看到的数不是真行情。'
                '要么等它建好，要么显式设 %s=1 跑合成市场（仅供自测）。'
                % (type(e).__name__, SYNTH_ENV)) from e
        missing = [n for n in DS_NAMES if not callable(getattr(m, n, None))]
        if missing:
            raise SourceError('BAD_DATASOURCE',
                              'datasource 少了这几个函数：%s（规格 §3.1 是冻结接口）'
                              % ', '.join(missing))
        _BK_INFO['name'] = 'datasource'
        _BK = m
        return _BK


def backend_name():
    """→ 人话的后端名。★**主动去选一次**，不是"选过才报"：
    桩的要求是环境的函数（`synth_requested()`），只看"当前选没选"的话，
    在还没调过任何源之前问它就永远得到"（不可用）"—— 而那时候桩明明是好的。
    这是"报告的名字"与"真正会去用的那个"分叉的老病，修法是让它走同一个入口。"""
    if _BK_INFO['name'] is None:
        try:
            backend()
        except SourceError as e:
            _BK_INFO['err'] = str(e)
    return _BK_INFO['name'] or '（不可用）'


def backend_err():
    """后端选不上时**为什么**（人话，可空）。★单独一个出口：
    `backend_name()` 返回的是一张给用户看的标签，而"为什么没有后端"是一句诊断，
    两件事塞进同一个字符串的话，成功路径上也会拖着一句没用的诊断。"""
    if _BK_INFO['name'] is None:
        backend_name()
    return _BK_INFO['err']


def reset_backend():
    """判据/探针用：换了 env 之后要能重选。"""
    global _BK
    with _BK_LOCK:
        _BK = None
        _BK_INFO.update({'name': None, 'err': None})


# ── 超时 ────────────────────────────────────────────────────────────────
# ★为什么是 daemon 线程 + join(timeout)，而不是 `feed._with_timeout` 那种
#   `with ThreadPoolExecutor(...)` 写法：那个 `with` 退出时会 `shutdown(wait=True)`，
#   于是**一个卡住的任务会把 `__exit__` 永久卡住** —— "超时"就地变成"挂死"，
#   比不设超时更坏（调用方以为 15 秒会回来，实际是永远不回来，而且没有任何日志）。
#   daemon 线程不阻塞解释器退出；超时后我们放弃它、如实记账。
#   （`agent/feed.py: _with_timeout` 就是这个形状，早于本模块，不是本模块的事。）
def call_with_timeout(fn, seconds, label):
    box = {}

    def _run():
        try:
            box['v'] = fn()
        except BaseException as e:                           # noqa: BLE001
            box['e'] = e

    t = threading.Thread(target=_run, daemon=True, name='linkage:%s' % label)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise SourceError('TIMEOUT', '%s 超过 %gs 没返回（已经放弃它，但那个线程还在跑）'
                          % (label, seconds))
    if 'e' in box:
        raise box['e']
    return box.get('v')


# ── 账本 ────────────────────────────────────────────────────────────────
class Ledger:
    """一次 scan 的采集账。**这是本模块要交付的主要产物之一**，不是日志。"""

    def __init__(self, budget=60, timeout=25.0):
        self.t0 = time.perf_counter()
        self.items = []          # 每次调用一条，按发生顺序
        self.skipped = []        # 因为预算/其他原因**没去采**的，也要记账
        self.budget = int(budget)
        self.budget_used = 0
        self.timeout = float(timeout)
        self.notes = []
        self._memo = {}          # (fnname, args) → 已经取到的值（只存成功的）
        self.memo_hits = []      # 复用了哪几次（**要如实进账**，见 `call` 的 memo）

    # — 记账 —
    def rec(self, sid, ok, n=None, ms=None, err=None):
        self.items.append({'id': str(sid), 'ok': bool(ok), 'n': _int(n),
                           'ms': _num(ms, 1), 'err': (str(err)[:160] if err else None)})
        return ok

    def note(self, s):
        if s not in self.notes:
            self.notes.append(s)

    def skip(self, sid, why):
        self.skipped.append({'id': str(sid), 'why': str(why)[:120]})

    # — 预算 —
    def can_spend(self, k=1):
        return self.budget_used + k <= self.budget

    def spend(self, k=1):
        self.budget_used += k

    # — 调用 —
    def call(self, sid, fnname, *args, memo=False, **kw):
        """调数据层的一个函数。→ (ok, value)。**永远不抛**：失败的形状是 (False, None)
        加上账本里的一条红 —— 上层不需要 try/except 也能做到"如实交代"。

        `memo=True`：**同一轮 scan 内**取过的同一份数据不再取第二遍，只看成功过的。
        它只该用在"同一次 scan 里答案不会变"的调用上（板块清单、板块成分股、
        个股的板块归属）：一轮 scan 里 9 只异动票会各自去要一遍**同一个板块**的成分股，
        那 25 次调用买回来的是**同一份数据**，而预算买的是"这一轮我能取多少次"。
        省下来的额度直接变成多深挖几只票 —— 而"多深挖一只"是这个模块的正事。

        ★两条安全边界（不遵守会出事）：
          1. **失败不进 memo**。一次超时/一次源抽风不该被记成"这只票永远没有日线"。
          2. **不跨 scan**：memo 挂在 Ledger 上，而 Ledger 是每次 scan 新建的。
             跨 scan 复用等于把上一轮的行情当这一轮的。
        ★复用了多少次**要进账**（`memo_hits` 进 collected）—— 省了调用这件事
          本身也是"这一轮采到了什么"的一部分，不许无声。
        """
        if memo:
            key = (fnname, args, tuple(sorted(kw.items())))
            if key in self._memo:
                self.memo_hits.append(str(sid))
                return True, self._memo[key]
        t0 = time.perf_counter()

        def _fail(msg):
            self.rec(sid, False, ms=(time.perf_counter() - t0) * 1000, err=msg)
            return False, None

        try:
            mod = backend()
        except SourceError as e:
            return _fail('%s: %s' % (e.code, e))
        fn = getattr(mod, fnname, None)
        if not callable(fn):
            return _fail('数据层没有 %s()' % fnname)
        try:
            v = call_with_timeout(lambda: fn(*args, **kw), self.timeout, sid)
        except SourceError as e:
            return _fail('%s: %s' % (e.code, e))
        except Exception as e:                               # noqa: BLE001
            return _fail('%s: %s' % (type(e).__name__, str(e)[:120]))
        n = None
        if isinstance(v, pd.DataFrame):
            n = len(v)
        elif hasattr(v, '__len__'):
            n = len(v)
        self.rec(sid, True, n=n, ms=(time.perf_counter() - t0) * 1000)
        if memo:
            self._memo[key] = v
        return True, v

    # — 汇总 —
    def n_fail(self):
        return sum(1 for it in self.items if not it['ok'])

    def collected(self, ts, rows, markets, rows_by_market=None, health=None):
        """→ 规格 §3.3 的 `collected` 块。★详实是硬要求，不是"够用就行"。"""
        out = {
            'sources': list(self.items),
            # ★折叠账（摘要通道用）：见 `brief_sources` 的文件说明。
            'sources_brief': brief_sources(self.items),
            'ts': ts,
            'rows': _int(rows),
            'markets': list(markets),
            'rows_by_market': dict(rows_by_market or {}),
            'calls': len(self.items),
            'failed': self.n_fail(),
            'ms': _num((time.perf_counter() - self.t0) * 1000, 0),
            'backend': backend_name(),
            'budget': {'limit': self.budget, 'used': self.budget_used,
                       'left': max(0, self.budget - self.budget_used)},
            'memo_hits': len(self.memo_hits),
            'skipped': list(self.skipped),
            'notes': list(self.notes),
        }
        if health is not None:
            # 数据层自己的健康度，**如实转述**（不加工、不合并两个口径）
            out['upstream_health'] = health
        return out


def snapshot_ts(rows):
    """从快照行里取时间戳。★取**众数**而不是第一行：跨市场拼起来时第一行是谁
    取决于拼接顺序，而"这份快照是哪一刻的"是给用户看的，不能随顺序漂。"""
    from collections import Counter
    c = Counter(r.get('ts') for r in rows if r.get('ts'))
    return c.most_common(1)[0][0] if c else None


def brief_sources(items, max_fail=6):
    """逐次调用的账 → 折叠账：**按源聚合成功、失败逐条原样**。

    ★为什么必须有这一份（不是"顺手加的"）：
      `collected.sources` 是**逐次调用**的，一轮 scan 就有 `O(预算)` 条
      （实测一轮 123 条 / 8.6 KB）。它是给**页面和审计**看的，本来就该这么细。
      但摘要通道只有 4000 字节（`agent/tools.py: SUMMARY_BUDGET`），而
      `tools.scan_linkage` 把 `collected.sources` **原样**塞进 summary ——
      逐次清单进摘要通道，第一次调用就会 `SUMMARY_TOO_BIG`。
      所以这里把折叠版准备好，上层改一个字段名就行。

    ★折叠的规矩：**成功的可以聚合，失败的必须逐条**。
      把失败聚合掉（"3 次失败"）就把"哪个源、什么错"弄丢了 ——
      而失败恰恰是这个模块最要留痕的东西（文件头第 1 条纪律）。
    """
    agg, fails = {}, []
    for it in items or []:
        fn = str(it.get('id') or '').split(':')[0]
        a = agg.setdefault(fn, {'fn': fn, 'calls': 0, 'ok': 0, 'failed': 0,
                                'n': 0, 'ms': 0.0})
        a['calls'] += 1
        a['ok' if it.get('ok') else 'failed'] += 1
        a['n'] += int(it.get('n') or 0)
        a['ms'] = round(a['ms'] + float(it.get('ms') or 0.0), 1)
        if not it.get('ok'):
            fails.append(it)
    return {
        'n_calls': len(items or []),
        'by_fn': [agg[k] for k in sorted(agg)],
        'failed_n': len(fails),
        # 失败逐条；条数本身也可能是被截断的，所以**截断要说出来**（"n 条未列出"）
        'failed': fails[:max_fail],
        'failed_omitted': max(0, len(fails) - max_fail),
        'note': '完整逐次清单在 collected.sources（给页面/审计用，不要整份塞进摘要通道）',
    }
