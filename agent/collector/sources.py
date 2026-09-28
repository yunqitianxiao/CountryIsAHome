"""去采：数据层选择 + 逐源记账 + 预算/超时。**采完就把载荷追加到盘上。**

三条纪律，每条都对应一种"看着像成功、其实没采到"的坏法：

1. **「包还没落地」和「包落地了但坏了」必须分开报。** 规格 §3.1 的
   `agent/datasource/` 是另一个代理的活。Python 3 的**命名空间包**让一个只有目录、
   没有 `__init__.py` 的包也能 `import` 成功 —— 于是 `datasource.realtime` 抛
   AttributeError，看起来像"包坏了"，实际只是"还没写到这里"。
   ★把"还没做"报成"做坏了"，会让人去修一个根本不存在的东西；
     把"做坏了"报成"还没做"，会让一个真 bug 永远没人管（照抄 `tools._lazy` 的理由）。
   ⇒ `NOT_READY`（施工期间的**预期**状态）与 `DATASOURCE_BROKEN`（**真 bug**）两个码。

2. **每一次外部调用都要有超时，失败要如实进账。** 服务里"挂住"比"报错"糟糕得多：
   挂住时页面上是空白，而空白和"还没采"长得一模一样。所以每次调用都过
   `call_with_timeout`，超了记 `TIMEOUT` 再往下走。
   ★被放弃的那个线程**还在跑**（Python 杀不掉线程），这件事在 err 里明说 ——
     不说的话，一个每轮泄漏一个线程的采集器看起来和正常的一模一样。

3. **真源取不到 ⇒ `ok=False` + 人话原因，绝不退化到桩。** 桩只认
   `LICAI_COLLECT_SYNTH`（服务端 env）。"取不到就自动用合成数据顶上"是
   一台印行情的机器，而用户看不出屏幕上那个数是为了让你别看到空白而编的。
   `_probe.py` 的 C3 专门拿**"让 backend() 回落到桩"**这条注入打它。

★`feed.proxy_env_fix()` 在**碰数据层之前**调。本机 `ALL_PROXY=socks://127.0.0.1:7897/`
  （scheme 少了 `5`），requests/urllib3 不认 ⇒ 一个和网络无关的 ProxyError，
  而 curl 认 ⇒ 症状是"curl 通、python 不通"。摘它必须早于 `requests` 的第一次
  import（`feed._ak()` 顶上那条注释就是为这件事写的）。
  国内站点的**直连**（摘 `http_proxy`）由 `datasource/_base.drop_proxy()` 在它自己
  import 时做 —— 那是它那一层的职责，本层不代劳、也不重复实现一套。
"""

import os
import threading
import time

try:                                                    # 同目录的包内引用
    from . import store
    from . import synth
except ImportError:                                     # 直接 `python sources.py` 自检时
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from collector import store                     # noqa: F401
    from collector import synth                     # noqa: F401

SYNTH_ENV = synth.SYNTH_ENV
FAIL_ENV = synth.FAIL_ENV
TIMEOUT_ENV = 'LICAI_COLLECT_TIMEOUT'          # 单次外部调用的超时（秒）
BUDGET_ENV = 'LICAI_COLLECT_BUDGET_MS'         # 一整轮的墙钟预算（毫秒）
NEWS_ENV = 'LICAI_COLLECT_NEWS'                # '0' = scope=all 也不采资讯

DEFAULT_TIMEOUT = 8.0
DEFAULT_BUDGET_MS = 25000
NEWS_LIMIT = 6

MARKETS = ('cn', 'hk', 'us', 'crypto')
BOARD_KINDS = ('concept', 'industry')
# 数据层必须有的函数（规格 §3.1）。少了任何一个都算"这层不能用" ——
# 一半能跑、另一半 AttributeError 比整层缺席更难查。
DS_NAMES = ('realtime', 'boards')

ALL_TOKENS = tuple(MARKETS) + ('boards', 'news')

# scope 的可选项清单。★**只写一份**：这份清单在 err 和 hint 两处都要出现，
# 而"同一个清单写两遍"的下场是改了一处、另一处留着一句**错的说明** ——
# 用户是照着说明敲的（`_probe.py` C19 拿它当锚：说明里点名的词，parser 必须真认）。
SCOPE_HELP = 'all / markets / %s / boards / news' % ' / '.join(MARKETS)


class SourceError(RuntimeError):
    """本层的取数错误。与 `feed.SourceError` 同名同形但不是同一个类（理由见
    `linkage/collect.py`）：上层只 catch 一个，不该被数据层换实现绑死。"""

    def __init__(self, code, msg, hint=''):
        super().__init__(msg)
        self.code, self.hint = code, hint


# ── feed.py 的三条桥（_num / proxy_env_fix）──────────────────────────────

_FEED = {'mod': None, 'tried': False}


def _feed():
    """按三种姿势找 `feed.py` —— 调用方怎么 import 这个包的都有，别假设 sys.path
    （照抄 `datasource/_base._load_feed`，那边踩过同一个坑）。"""
    if _FEED['tried']:
        return _FEED['mod']
    _FEED['tried'] = True
    try:
        import feed as m                                        # noqa: PLC0415
        _FEED['mod'] = m
        return m
    except ImportError:
        pass
    try:
        from agent import feed as m                             # noqa: PLC0415
        _FEED['mod'] = m
        return m
    except ImportError:
        pass
    try:
        import importlib.util                                   # noqa: PLC0415
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'feed.py')
        spec = importlib.util.spec_from_file_location('_licai_feed_for_collector', path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _FEED['mod'] = m
        return m
    except Exception:                                           # noqa: BLE001
        _FEED['mod'] = None
        return None


def proxy_env_fix():
    """摘 `socks://` 型 ALL_PROXY。★**只调 `feed.proxy_env_fix()` 这一个对象**，
    不在这里抄一份等价实现：抄一份的话，哪天那边补上了别的键，这里不知道。"""
    m = _feed()
    fn = getattr(m, 'proxy_env_fix', None) if m else None
    if callable(fn):
        fn()


def _num(x, nd=4):
    """→ JSON 安全的数，或 None。用 `feed._num()` **那一个对象**；拿不到才退回
    本地等价实现，且 `_probe.py` C17 有一条**对照判据**钉着两者在一串边界值上
    给出同一个结果（"复用了别人的实现"和"我抄了一份像的"从外面看是一样的）。"""
    m = _feed()
    fn = getattr(m, '_num', None) if m else None
    if callable(fn):
        return fn(x, nd)
    import math
    try:
        if x is None:
            return None
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _env_num(name, default, lo, hi, cast=float):
    try:
        return max(lo, min(hi, cast(os.environ.get(name) or default)))
    except (TypeError, ValueError):
        return cast(default)


def _timeout():
    return _env_num(TIMEOUT_ENV, DEFAULT_TIMEOUT, 0.05, 600.0, float)


def _budget_ms():
    return _env_num(BUDGET_ENV, DEFAULT_BUDGET_MS, 50.0, 600000.0, float)


# ── 带超时的调用 ───────────────────────────────────────────────────────

def call_with_timeout(fn, seconds, label):
    """跑一次外部调用，超时抛 `SourceError('TIMEOUT')`。

    ★是**线程 + join** 而不是信号：信号只能在主线程用，而这里随时可能从
      Flask 的工作线程里调。代价是超时之后那个线程**还活着** ——
      err 里必须写明这一点，否则"每轮泄漏一个线程"这件事在盘上没有任何痕迹。
    """
    box = {}

    def _run():
        try:
            box['v'] = fn()
        except BaseException as e:                               # noqa: BLE001
            box['e'] = e

    t = threading.Thread(target=_run, daemon=True, name='collector:%s' % label)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise SourceError('TIMEOUT', '%s 超过 %gs 没返回（已放弃它，但那个线程还在跑）'
                          % (label, seconds))
    if 'e' in box:
        raise box['e']
    return box.get('v')


# ── 数据层选择 ─────────────────────────────────────────────────────────

def _not_ready(why, hint):
    """"包还没落地 / 入口还没写" —— **施工期间的预期状态**。"""
    return {'ok': False, 'mod': None, 'name': None, 'code': 'NOT_READY',
            'err': why, 'hint': hint}


def _broken(e):
    """"包在，但 import 本身就炸了" —— **这是真 bug**。
    ★与 `_not_ready` 分开成两个函数，是因为这两句话**必须**长得不一样：
      把"还没做"报成"做坏了"会让人去修一个根本不存在的东西；
      把"做坏了"报成"还没做"会让一个真 bug 永远没人管。
      分成两个函数还让"把它们混起来"这件事可以被一条注入打红（`_probe.py` C4）。"""
    return {'ok': False, 'mod': None, 'name': None, 'code': 'DATASOURCE_BROKEN',
            'err': '行情数据层在 import 的时候就炸了：%s: %s'
                   % (type(e).__name__, str(e)[:200]),
            'hint': '这不是"还没做"，是 agent/datasource/ 里有真的 bug —— 别去重写它，去修它'}


def _module_found(name):
    """包在不在。★抽成函数是为了让"包没落地"这条支路**能被独立测**：
    探针把它替换掉就行，**不必去动 `agent/datasource/`** ——
    那是别人的文件，而且正在被另一个代理写着，动它是一次跨写者的破坏。"""
    import importlib.util                                       # noqa: PLC0415
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:                                            # noqa: BLE001
        return False


def _import_module(name):
    """真的把包装进来。同样抽出来，好让"import 炸了"这条支路能被独立测。"""
    import importlib                                            # noqa: PLC0415
    return importlib.import_module(name)


def backend(force=None):
    """→ `{'ok','mod','name','code','err','hint'}`。**取不到就如实说，绝不退化成桩。**

    `force` 是给探针/判据用的**显式**注入口（`_probe.py` 拿它塞一个"脏数据源"
    进来验 NaN/numpy 的清洗）。它不是"客户端开关"：只有本进程里能调它的人能传。
    """
    if force is not None:
        return force
    if synth.active():
        return {'ok': True, 'mod': synth, 'name': synth.NAME, 'code': None,
                'err': None, 'hint': ''}
    # ★必须早于数据层的第一次 import（见模块头）。
    proxy_env_fix()
    if not _module_found('datasource'):
        return _not_ready(
            '行情数据层还没就绪（agent/datasource/ 这个包还没落地）。'
            '**这是并行施工期间的预期状态，不是采集器坏了**。',
            '等 agent/datasource/ 落地，或显式设 %s=1 跑合成数据（仅供自测）' % SYNTH_ENV)
    try:
        m = _import_module('datasource')
    except Exception as e:                                       # noqa: BLE001
        # ★import 本身就抛 ⇒ 这是**真有 bug**，不是"还没做"。
        #   绝不能报成 NOT_READY：那会让一个坏掉的包伪装成"还没写"，永远没人去修。
        return _broken(e)
    missing = [n for n in DS_NAMES if not callable(getattr(m, n, None))]
    if missing:
        return _not_ready(
            '行情数据层在，但入口还没落地（datasource 少了 %s）。'
            '**这是并行施工期间的预期状态，不是采集器坏了**。' % ', '.join(missing),
            '等 agent/datasource/ 把 §3.1 的冻结接口写完')
    return {'ok': True, 'mod': m, 'name': 'datasource', 'code': None, 'err': None, 'hint': ''}


def backend_name():
    b = backend()
    return b.get('name') or ('（数据层不可用：%s）' % (b.get('code') or '?'))


# ── scope ─────────────────────────────────────────────────────────────

def _news_on():
    return (os.environ.get(NEWS_ENV) or '1').strip().lower() not in ('0', 'false', 'no', 'off')


def parse_scope(scope):
    """→ `(plan, err)`。`plan` = `{'markets': [...], 'boards': [...], 'news': bool}`。

    ★不认识的 token **报错**，不静默丢掉。"scope=cn,typo" 静默只采 cn 的话，
      用户看到的是一个正常的页面，而他要的那个市场**一次都没采过** ——
      这种错在盘上没有任何痕迹。
    """
    raw = (scope if isinstance(scope, str) else 'all') or 'all'
    toks = [t.strip().lower() for t in raw.replace(' ', ',').split(',') if t.strip()]
    if not toks:
        return None, ('scope 是空的（要 %s 之一，或逗号组合）' % SCOPE_HELP)
    bad = [t for t in toks if t not in ALL_TOKENS and t not in ('all', 'markets', 'quotes')]
    if bad:
        return None, 'scope 里有我不认识的词：%s（认得的只有 %s）' % (', '.join(bad), SCOPE_HELP)
    plan = {'markets': [], 'boards': [], 'news': False}
    for t in toks:
        if t in ('all', 'markets', 'quotes'):
            plan['markets'] = list(MARKETS)
            if t == 'all':
                plan['boards'] = list(BOARD_KINDS)
                plan['news'] = True
        elif t in MARKETS:
            if t not in plan['markets']:
                plan['markets'].append(t)
        elif t == 'boards':
            plan['boards'] = list(BOARD_KINDS)
        elif t == 'news':
            plan['news'] = True
    # ★资讯**只被 'all' 或点名 'news' 带出来**，市场/板块那几个 token 不带它：
    #   它那一轮要打 22 个源（含 crawl4ai 子进程），"看一眼行情"不该顺手把它拉起来。
    #   `LICAI_COLLECT_NEWS=0` 只关掉 `scope=all` 这一条路；有人明确写 `news` 时
    #   以他为准 —— 环境变量是"默认值"，不是"禁掉这个能力"。
    if 'all' in toks and not _news_on():
        plan['news'] = False
    if 'news' in toks:
        plan['news'] = True
    # 市场顺序固定成规格里的顺序，不跟用户敲的顺序走：
    # `rows_by_market` / `market` 那一栏要能跨轮对照，"这一轮 cn 在前、下一轮 hk 在前"
    # 会让同一个采集器看起来像两台。
    plan['markets'] = [m for m in MARKETS if m in plan['markets']]
    return plan, None


# ── 一轮采集 ───────────────────────────────────────────────────────────

class _Ledger:
    """一轮的采集账。每一**次外部调用**一条，成功失败都进账。"""

    def __init__(self, budget_ms, timeout):
        self.items = []
        self.skipped = []
        self.notes = []
        self.t0 = time.perf_counter()
        self.budget_ms = float(budget_ms)
        self.timeout = float(timeout)
        self.rows_by = {}
        self.frames = 0
        self.frame_err = []

    def rec(self, sid, ok, n=None, ms=None, err=None):
        self.items.append({'id': str(sid), 'ok': bool(ok), 'n': _int(n),
                           'ms': _num(ms, 1), 'err': (str(err)[:200] if err else None)})
        return ok

    def note(self, s):
        if s not in self.notes:
            self.notes.append(s)

    def skip(self, sid, why):
        self.skipped.append({'id': str(sid), 'why': str(why)[:160]})

    def left_ms(self):
        return self.budget_ms - (time.perf_counter() - self.t0) * 1000.0

    def out_of_budget(self):
        return self.left_ms() <= 0

    def call(self, sid, fn, rows_key=None, tag=None, kind=None):
        """调数据层一次。→ `(ok, value)`。**永远不抛**：失败的形状是 `(False, None)`
        加账本里的一条红，上层不写 try/except 也能做到"如实交代"。

        `tag` 给了就把返回值当**载荷**追加到盘上（`store.append_frame`）。
        """
        t0 = time.perf_counter()
        if self.out_of_budget():
            self.skip(sid, '这一轮的墙钟预算 %dms 用完了（%s），没去采'
                      % (int(self.budget_ms), BUDGET_ENV))
            return False, None
        if not callable(fn):
            self.rec(sid, False, ms=(time.perf_counter() - t0) * 1000,
                     err='数据层没有这个函数')
            return False, None
        try:
            v = call_with_timeout(fn, self.timeout, sid)
        except SourceError as e:
            self.rec(sid, False, ms=(time.perf_counter() - t0) * 1000, err='%s: %s' % (e.code, e))
            return False, None
        except Exception as e:                                   # noqa: BLE001 —— 源边界
            self.rec(sid, False, ms=(time.perf_counter() - t0) * 1000,
                     err='%s: %s' % (type(e).__name__, str(e)[:160]))
            return False, None
        n = len(v) if hasattr(v, '__len__') else None
        if tag is not None:
            wok, werr, _wn = store.append_frame(tag, _as_rows(v), store.now_str(),
                                                kind=kind or tag)
            if wok:
                self.frames += 1
            else:
                # 落盘失败**不能静默**：这一轮"采到了"但盘上没有，页面上那一栏
                # 却会显示成功。`_probe.py` 的 C9 也钉着这条路径不许编数出来。
                self.frame_err.append('%s: %s' % (tag, werr))
        self.rec(sid, True, n=n, ms=(time.perf_counter() - t0) * 1000)
        if rows_key:
            self.rows_by[rows_key] = _int(n)
        return True, v


def _as_rows(v):
    """载荷 → list[dict]（要落盘的那份）。★`v or []` 不能用在 DataFrame 上
    （`bool(df)` 抛 "truth value is ambiguous"），而 §3.1 的接口不保证
    `boards()`/`realtime()` 回的一定是 list。"""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, tuple):
        return list(v)
    to_dict = getattr(v, 'to_dict', None)
    if callable(to_dict):
        try:
            return list(to_dict('records'))
        except Exception:                                        # noqa: BLE001
            return []
    try:
        return list(v)
    except TypeError:
        return []


def total_rows(items):
    """**行情/板块**的数据行数合计。→ int 或 None。

    ★"没有任何一个行情/板块源成功" ⇒ **None**（"没采到"），**不是 0**（"采到了 0 行"）。
      前端 `numOrNull()` 专门堵这一手：`Number(null)` 是 0，直接 `Number(x)`
      会把"没采到"静默变成"采到 0"。这一条是这块最容易犯的错，
      `_probe.py` C3（降级路径）与 C7（空目录）各钉一头。
    ★资讯的 `n` 是**条数**不是行数，量纲不同，不并进这个和 —— 并了的话
      "采了 6 条新闻"会显示成"采了 6 行行情"，而两个数看着一样。
    """
    got = [it for it in items if it['ok'] and not str(it['id']).startswith('news')]
    if not got:
        return None
    return sum(int(it['n'] or 0) for it in got)


def _round_ok(led, rows):
    """这一轮算不算"采到东西了"。★抽成命名函数，因为它是 `ok` 的**唯一定义**，
    而这条定义有一个反直觉的地方值得单独写下来（也让它能被一条注入打红）：

    ★`ok` **不许**写成 `rows is not None`。`rows` 记的是行情/板块的行数，
      资讯的条数**不并进去**（量纲不同，见 `total_rows`）⇒ 一轮"只采了资讯"
      的采集：`rows` 是 None 而它**确实采到了东西**。拿 rows 当 ok 的话，
      那一轮会被报成"什么都没采到"并回 400，而盘上明明躺着 6 条资讯 ——
      这和"编数据"是同一类错：**说出来的一轮和真发生的那一轮对不上**。
    """
    if rows is not None:
        return True
    return any(it['ok'] for it in led.items)


def _collect_news(led, timeout):
    """资讯。★它自己一层源链（akshare/crawl4ai/MediaCrawler），是最慢的一块，
    所以单独一个超时 + 单独一条账，且**失败不影响别的源**。"""
    t0 = time.perf_counter()
    if led.out_of_budget():
        led.skip('news', '这一轮的墙钟预算用完了（%s），没去采' % BUDGET_ENV)
        return
    try:
        import importlib.util                                   # noqa: PLC0415
        if importlib.util.find_spec('news') is None:
            led.rec('news', False, ms=(time.perf_counter() - t0) * 1000,
                    err='资讯包 agent/news/ 不在')
            return
        import news as nm                                       # noqa: PLC0415
    except Exception as e:                                       # noqa: BLE001
        led.rec('news', False, ms=(time.perf_counter() - t0) * 1000,
                err='资讯包 import 失败：%s: %s' % (type(e).__name__, str(e)[:120]))
        return
    fn = getattr(nm, 'collect', None)
    if not callable(fn):
        led.rec('news', False, ms=(time.perf_counter() - t0) * 1000,
                err='资讯包没有 collect() 入口')
        return
    try:
        res = call_with_timeout(lambda: fn(limit=NEWS_LIMIT,
                                           budget_ms=int(max(200.0, min(timeout * 1000.0, led.left_ms())))),
                                timeout, 'news')
    except SourceError as e:
        led.rec('news', False, ms=(time.perf_counter() - t0) * 1000, err='%s: %s' % (e.code, e))
        return
    except Exception as e:                                       # noqa: BLE001
        led.rec('news', False, ms=(time.perf_counter() - t0) * 1000,
                err='%s: %s' % (type(e).__name__, str(e)[:160]))
        return
    if not isinstance(res, dict):
        led.rec('news', False, ms=(time.perf_counter() - t0) * 1000,
                err='news.collect() 回了 %s，不是一个字典' % type(res).__name__)
        return
    items = res.get('items') or []
    ok = res.get('ok') is True
    err = None
    if not ok:
        deg = res.get('degraded') or []
        why = deg[0].get('reason') if deg and isinstance(deg[0], dict) else None
        err = '资讯这一轮没成：%s' % (str(why)[:140] if why else '每个源都没出东西')
        for d in deg[:4]:
            if isinstance(d, dict):
                led.note('资讯源 %s 降级：%s' % (d.get('source'), str(d.get('reason'))[:80]))
    wok, werr, _n = store.append_frame('news', items, store.now_str(), kind='news')
    if wok:
        led.frames += 1
    else:
        led.frame_err.append('news: %s' % werr)
    led.rec('news', ok, n=len(items), ms=(time.perf_counter() - t0) * 1000, err=err)


def _safe_news(led, timeout):
    """把资讯那一层围起来。**它是整轮里唯一一块会起子进程/开浏览器的地方**
    （`news` 自己有 crawl4ai / MediaCrawler 两条子进程通路），
    它炸了不该把这一轮里已经采到的三个市场一起带走。

    ★抽成**命名函数**而不是就地写个 try：这样"去掉这层保护"能被一条注入打红
      （`_probe.py` C15），就地写的话那条注入无处可落。
    ★失败**照样进账**（`_collect_news` 自己记一条红的），不是静默跳过 ——
      "资讯没采"和"资讯这一层炸了"在页面上是两句不同的话。
    """
    try:
        _collect_news(led, timeout)
    except Exception as e:                                       # noqa: BLE001
        led.rec('news', False, err='资讯这一层抛了：%s: %s' % (type(e).__name__, str(e)[:160]))
        led.note('资讯那一层抛了异常（已经围住，别的源不受影响）')


def collect_round(scope='all', backend_res=None):
    """采一轮，**不写索引**（索引由 `__init__.once` 写，见那边的理由）。

    → 一条索引记录（也是 `ring()` 吐出来的那个东西）。**永不抛**：
    采不到的形状是 `ok=False` + `err` 里一句人话，不是异常。
    """
    t0 = time.perf_counter()
    ts = store.now_str()
    timeout, budget = _timeout(), _budget_ms()
    plan, perr = parse_scope(scope)
    led = _Ledger(budget, timeout)

    rec = {'v': store.INDEX_V, 'ts': ts, 'end': None,
           # ★空串按 'all' 采（路由那边 `b.get('scope') or 'all'` 是同一个意思），
           #   所以记录里也要写 'all'：写 '' 的话这条索引等于在说"我采了一个
           #   没有名字的 scope"，而它其实采了全部。
           'scope': ((scope or 'all') if isinstance(scope, str) else str(scope)),
           'ok': False, 'market': '', 'markets': [], 'rows': None, 'ms': None, 'err': None,
           'sources': [], 'rows_by_market': {}, 'skipped': [], 'notes': [],
           'frames': 0, 'backend': None}

    if perr:
        rec.update(end=store.now_str(), err=perr,
                   ms=_num((time.perf_counter() - t0) * 1000, 1))
        rec['hint'] = 'scope 用 %s，可逗号组合' % SCOPE_HELP
        return rec

    rec['markets'] = list(plan['markets'])
    rec['market'] = ','.join(plan['markets']) if plan['markets'] else \
        ('boards' if plan['boards'] else 'news')

    bk = backend(force=backend_res)
    rec['backend'] = bk.get('name')
    rec['backend_code'] = bk.get('code')        # NOT_READY / DATASOURCE_BROKEN / None
    if not bk.get('ok'):
        # ★降级路径：**不许**因为"真源取不到"就换一份数据出来（见模块头第 3 条）。
        rec.update(end=store.now_str(), ok=False, err=bk.get('err'), hint=bk.get('hint'),
                   rows=None, ms=_num((time.perf_counter() - t0) * 1000, 1))
        # 失败**也要进 sources**：页面那张"哪个源在挂"的表要看得见它，
        # 不然一个源都没试过的页面和"全都成了但都是 0 行"长得一样。
        led.rec('datasource', False, err='%s: %s' % (bk.get('code'), str(bk.get('err'))[:150]))
        rec['sources'] = list(led.items)
        return rec

    mod = bk['mod']
    led.note('数据层：%s' % (bk.get('name') or '?'))
    for m in plan['markets']:
        led.call('realtime:%s' % m, lambda m=m: mod.realtime(m), rows_key=m, tag=m, kind='realtime')
    for k in plan['boards']:
        led.call('boards:%s' % k, lambda k=k: mod.boards(k), tag='boards_%s' % k, kind='boards')
    if plan['news']:
        _safe_news(led, timeout)

    rec.update(end=store.now_str(),
               rows=total_rows(led.items),
               ms=_num((time.perf_counter() - t0) * 1000, 1),
               sources=list(led.items),
               rows_by_market=dict(led.rows_by),
               skipped=list(led.skipped),
               notes=[],
               frames=led.frames)
    # `ok` = "这一轮采到东西了吗"（定义与理由见 `_round_ok`）。
    # 只要有**一个**源成了就算成 —— 某个源挂了要看 `sources` 里那一条，
    # 不该把整轮判成失败（那样页面的时间线会全红，而"3 个源挂了 1 个活着"
    # 和"一个都没通"是两件事）。
    rec['ok'] = _round_ok(led, rec['rows'])
    if rec['ok'] and rec['rows'] is None:
        led.note('这一轮只有资讯成了：`rows` 记的是行情/板块的行数，所以是 None')
    if led.frame_err:
        led.note('落盘出错：%s' % '；'.join(led.frame_err[:3]))
    if not rec['ok']:
        n_fail = len([it for it in led.items if not it['ok']])
        rec['err'] = ('这一轮什么都没采到：%d 个源全都没成（第一个失败：%s）'
                      % (n_fail, _first_err(led.items) or '没有源被调用过'))
        rec['hint'] = '看 sources 里每一个源的 err；数据层本身能不能用看 backend 这一栏'
    rec['notes'] = list(led.notes)
    # 上游自己的健康度**采完就存进记录**，好让 `status()` 保持成一个纯读盘接口
    # （见 `__init__.status` 的说明：只读接口不许 import 别的包，那本身是一次写盘）。
    uh = health()
    if uh:
        rec['upstream_health'] = uh
    return rec


def _first_err(items):
    for it in items:
        if not it['ok'] and it.get('err'):
            return '%s → %s' % (it['id'], it['err'])
    return None


def health():
    """上游数据层自己的健康度（§3.1 的 `health()`，前端两页共用同一个形状）。
    ★取不到就回 `None` —— **不编一个空的 `{'sources': []}`**：
    "数据层说它没事"和"数据层根本没答"是两句不同的话。"""
    if synth.active():
        try:
            return synth.health()
        except Exception:                                        # noqa: BLE001
            return None
    bk = backend()
    if not bk.get('ok'):
        return None
    fn = getattr(bk['mod'], 'health', None)
    if not callable(fn):
        return None
    try:
        h = call_with_timeout(fn, 2.0, 'datasource.health')
    except Exception:                                            # noqa: BLE001
        return None
    return h if isinstance(h, dict) else None
