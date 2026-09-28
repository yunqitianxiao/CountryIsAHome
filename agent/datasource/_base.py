"""datasource 的地基：直连 HTTP、超时、缓存、健康度、以及到 `agent/feed.py` 的桥。

**为什么这个文件存在**：上层那几个源模块（tencent/sina/ths/adata/akshare）共用同一套
纪律 —— 去代理、每次调用超时、失败如实记进 health、返回前所有数字过 `feed._num()`。
把这些各写一遍的话，早晚有一个源漏掉其中一条，而漏掉的那条恰好是"看起来正常、
数字是假的"那一类。

三条纪律逐条对应 `docs/AGENT-SPEC.md` §0：

1. **国产站点直连。** 本机 env 有 `http_proxy=127.0.0.1:7897`（Clash），国内数据站
   走它实测一律 **502**（2026-09-22：腾讯/新浪/东财都试过）。所以 `drop_proxy()`
   要在**任何网络库第一次 import 之前**调用 —— requests 在 import 期就可能读 env
   建默认 session。
2. **每个外部调用都要有超时。** 第三方库（adata/akshare）内部是裸 `requests.get(...)`，
   **没有 timeout**。实测 `adata.stock.market.get_market_bar` 一次调用挂到 30 秒还没返回
   （本机 2026-09-22）。`call()` 用 `concurrent.futures` 包一层，超时就是失败。
3. **JSON 安全。** NaN/inf/numpy 标量一律过 `feed._num()` → None。缺值是 None 不是 0。

★ 这个文件**不 import adata / efinance / akshare**：那三个都是"可选依赖"，
   没装或者装坏了不该让整个包 import 不进来。
"""

import concurrent.futures as cf
import importlib.util
import json
import math
import os
import re
import threading
import time

# canonical 列 —— 与 `agent/feed.py: CANON` 逐字一致（上层要能直接喂 Kronos）
CANON = ['dt', 'open', 'high', 'low', 'close', 'volume', 'amount']

DEFAULT_TIMEOUT = 25


def drop_proxy():
    """摘掉本机 Clash 代理。**必须在 import requests 之前调**。

    实测（2026-09-22，本机）：带着 `http_proxy=127.0.0.1:7897` 打
    `push2his.eastmoney.com` 得 **502**；`curl --noproxy '*'` 直连同一台是通的。
    摘掉不影响境外请求 —— 走代理的那几条（OKX/Binance）自己显式传 proxies。
    """
    for k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
              'ALL_PROXY', 'all_proxy'):
        os.environ.pop(k, None)


drop_proxy()                                     # ★早于本模块下面所有第三方 import


# ── 到 agent/feed.py 的桥 ───────────────────────────────────────────────
# ★`_num()` / `stub_active()` / `SourceError` **必须**复用 feed.py 那一套，
#   不许在这里重写一个长得差不多的：上层（tools.py）`except feed.SourceError`
#   是按类型抓的，这里自造一个异常类会从那个 except 底下溜过去，
#   变成 500 而不是"数据没取到"。

def _load_feed():
    """按三种姿势找 feed.py —— 调用方怎么 import 这个包的都有，别假设 sys.path。

    ① `import feed`（agent/ 在 sys.path 上，用具名探针那样跑）
    ② `from agent import feed`（当成包用）
    ③ 按文件路径直接 exec（前两条都失败时兜底）
    """
    try:
        import feed as m                        # noqa: PLC0415
        return m
    except ImportError:
        pass
    try:
        from agent import feed as m             # noqa: PLC0415
        return m
    except ImportError:
        pass
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'feed.py')
    spec = importlib.util.spec_from_file_location('_licai_feed_standalone', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


feed = _load_feed()

SourceError = feed.SourceError
_num = feed._num
stub_active = feed.stub_active
STUB_ENV = feed.STUB_ENV


# ── 超时 ───────────────────────────────────────────────────────────────

def call(fn, timeout=DEFAULT_TIMEOUT, label='call'):
    """跑一次外部调用，超时就抛 `SourceError('TIMEOUT')`。

    ★超时抛的是**异常**不是返回值：调用方必须能区分"取到空"和"没取到"。
      上游库（adata/akshare）内部没有超时，一个卡住的 socket 会把整趟请求挂死，
      而服务里"挂住"比"报错"糟糕得多（先例 `agent/feed.py: _with_timeout`）。
    """
    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except cf.TimeoutError:
            raise SourceError('TIMEOUT', '%s 超过 %ds 没返回' % (label, timeout)) from None


# ── HTTP（直连）─────────────────────────────────────────────────────────
# 只有这一层碰网络。所有源模块都走它，于是"去代理/超时/重试/解码"只有一份实现。

def http_get(url, params=None, headers=None, timeout=DEFAULT_TIMEOUT, encoding=None,
             via_proxy=False, retries=2, label=None):
    """直连 GET，返回 `requests.Response` 的 text。

    `encoding` 给定时按它解码（腾讯实时是 **GBK**：不解码会把「贵州茅台」读成乱码，
    而乱码进 JSON 是合法的 —— 前端只会显示一串看不懂的字，不会报错，
    属于"错了但没人知道"那一类）。
    `via_proxy=True` 时走本机 Clash（境外站如 OKX/Binance 用）。
    ★`retries` 是必要的：本机打腾讯/新浪偶发 `RemoteDisconnected`（实测 ~1/20），
      不重试的话会把它报成"源挂了"。
    """
    import requests                             # 延迟 import，让 drop_proxy() 先生效

    label = label or url.split('?')[0].rsplit('/', 1)[-1]
    proxies = ({'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}
               if via_proxy else {})
    hdr = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) licai-agent/1.0'}
    hdr.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=hdr, timeout=timeout,
                             proxies=proxies or None)
            if r.status_code >= 400:
                raise SourceError('HTTP_%d' % r.status_code,
                                  '%s 回了 HTTP %d' % (label, r.status_code))
            if encoding:
                r.encoding = encoding
            return r.text
        except SourceError:
            raise
        except Exception as e:                   # noqa: BLE001 —— 源边界，如实记
            last = e
            if attempt < retries:
                time.sleep(0.25 * (attempt + 1))
    raise SourceError('HTTP_ERROR', '%s 请求失败：%s: %s'
                      % (label, type(last).__name__, str(last)[:120]))


def json_or_text(txt, label=''):
    """把响应体解成 JSON。**JSONP 也能吃**：扒掉外层回调再解。

    为什么要这一步：新浪好几个接口回的是 `IO.XSRV2.CallbackList['x']({...})`
    这种 JSONP（网页里用得上，我们不需要）。直接 `json.loads` 会抛，
    而抛出来的消息（"Expecting value: line 1 column 1"）完全指向不了真原因。
    """
    s = (txt or '').strip()
    m = re.match(r"^[^(]*\(\s*([\[{].*[\]}])\s*\)\s*;?\s*$", s, re.S)
    if m:
        s = m.group(1)
    try:
        return json.loads(s)
    except ValueError as e:
        raise SourceError('BAD_JSON', '%s 返回的不是 JSON（%s）：%s'
                          % (label, e, s[:100])) from None


# ── 健康度 ─────────────────────────────────────────────────────────────
# ★spec §2.3 明确要求："health() 必须如实反映东财不可达，不许静默吞；前端要显示。"
#   所以每个源每次调用（成功或失败）都往这里写一条 —— 失败**不覆盖**成成功，
#   而是把最近一次的结果连同 err 一起留着。

_HEALTH = {}
_HEALTH_LOCK = threading.Lock()


def note(src_id, ok, ms, n=None, err=None):
    """记一条源健康度。`id` 的形态是 `源.能力`，例如 `tencent.realtime.cn`。"""
    with _HEALTH_LOCK:
        _HEALTH[src_id] = {'id': src_id, 'ok': bool(ok), 'ms': int(ms), 'n': n,
                           'err': (str(err)[:200] if err else None), 'ts': time.time()}


def health():
    """`{"sources": [{"id","ok","ms","n","err","ts"}]}`，按 id 排序（前端好对照）。"""
    with _HEALTH_LOCK:
        rows = [dict(v) for v in _HEALTH.values()]
    rows.sort(key=lambda r: r['id'])
    return {'sources': rows, 'ts': time.time()}


def forget_health():
    """清空健康度。探针和判据要一个干净的起点时用。"""
    with _HEALTH_LOCK:
        _HEALTH.clear()


def timed(src_id, fn, timeout=DEFAULT_TIMEOUT, label=None, count=None):
    """跑一次带健康度记账的调用。

    ★失败**抛出去**，不在这里吞掉 —— 这条函数只负责"记"，不负责"兜"。
      逐源降级是调用方（`__init__.py` 的源链）的事，写在源模块里会把
      "这个源为什么没用上"的顺序信息丢光。
    `count` 是一个 `lambda result: int`，用来算 n；不传就记 None。
    """
    t0 = time.time()
    try:
        out = call(fn, timeout=timeout, label=label or src_id)
    except Exception as e:                       # noqa: BLE001 —— 源边界
        note(src_id, False, (time.time() - t0) * 1000, None, '%s: %s' % (type(e).__name__, e))
        raise
    n = None
    if count is not None:
        try:
            n = int(count(out))
        except Exception:                        # noqa: BLE001 —— 数不出来不影响结果
            n = None
    elif hasattr(out, '__len__'):
        n = len(out)
    note(src_id, True, (time.time() - t0) * 1000, n, None)
    return out


# ── 缓存 ───────────────────────────────────────────────────────────────
# 键**带全参数**（市场+代码+周期+天数+复权…），抄 `agent/tools.py: _get_kline` 的做法。
# ★桩开着时一律绕开缓存：桩是测试夹具，夹具的意义就是"改了就立刻生效"，
#   走缓存会让"换掉桩文件、页面跟着变"那条对照静默地拿旧帧去比

_CACHE = {}
_CACHE_LOCK = threading.Lock()


def cached(kind, key, ttl, producer):
    """`(kind, key)` 命中且没过期就返回副本，否则调 `producer()` 并落缓存。

    ★返回 DataFrame 时给的是**副本**并保留 attrs —— 调用方改了返回值不该污染缓存
      （`tools.py: _get_kline` 踩过：`attrs['source']` 会被下游改掉）。
    """
    if stub_active():
        return producer()
    ck = (kind, key)
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(ck)
    if hit and now - hit[0] < ttl:
        val = hit[1]
        if hasattr(val, 'copy') and hasattr(val, 'attrs'):
            out = val.copy()
            out.attrs.update(val.attrs)
            out.attrs['cached'] = True
            return out
        return val
    val = producer()
    with _CACHE_LOCK:
        _CACHE[ck] = (now, val.copy() if hasattr(val, 'copy') else val)
    if hasattr(val, 'attrs'):
        val.attrs['cached'] = False
    return val


def cache_clear():
    with _CACHE_LOCK:
        _CACHE.clear()


# ── 小工具 ─────────────────────────────────────────────────────────────

def num(x, nd=4):
    """别名，只是为了让源模块读起来短一点。语义**就是** `feed._num`。"""
    return _num(x, nd)


def safe_int(x, default=None):
    """字符串/浮点 → int，坏值给 default。腾讯把数字全塞在字符串里。"""
    try:
        return int(float(str(x).strip()))
    except (TypeError, ValueError):
        return default


def safe_float(x):
    """→ float 或 None。**不返回 0**：0 是个会被人当真的数。"""
    try:
        f = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def pe(x):
    """市盈率专用：`safe_float` 之后再把 **0 折成 None**。

    ★为什么要单独一个：实测（2026-09-22）新浪港股 `pe_ratio="0"`、腾讯 `[39]="0"`
      都给的是**"这一格没填"**，不是"市盈率等于零"。
      一只**盈利**的股票 PE 不可能是 0；**亏损**的股票 PE 是负数（这个要保留，
      那是真值）。所以 0 这一格只能是"上游没给" —— 留着 0 会让前端显示
      「市盈率 0.00 倍」，看着像个正经数字。
      注意 **负数保留**：亏钱的票 PE 本来就是负的，那是信息不是缺失。
    """
    f = safe_float(x)
    if f is not None and f == 0:
        return None
    return f


def is_nan(x):
    try:
        return x is None or (isinstance(x, float) and math.isnan(x))
    except (TypeError, ValueError):
        return False


def _s(row, key):
    """取一个字符串字段并压成 `YYYY-MM-DD HH:MM`（上游给的是
    `2026/09/22 16:08:16` 或 `2026-09-22 16:08:16` 两种，都要能吃）。

    ★**取不到就是 None**，不许拿别的字段顶替 —— 时间戳错位比没有更坏：
      页面上会显示一个像模像样的时间，而那一刻根本没发生任何交易。
    """
    if not isinstance(row, dict):
        return None
    v = row.get(key)
    if v is None:
        return None
    s = str(v).strip().replace('/', '-')
    if not s:
        return None
    return s[:16] if len(s) >= 16 else s
