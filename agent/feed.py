"""行情数据层：把「用户随口说的一个代码」变成喂给 Kronos 的 OHLCV 表。

两个职责分开：
  1. `resolve()` —— 认代码。纯规则、不联网，所以能被验证脚本表驱动地测。
  2. `fetch()`   —— 取数。akshare 为主，加密留了公开接口的降级路径。

★列名映射按**名字**找，不按位置取。akshare 各接口返回的是中文列名（日期/开盘/收盘/最高/
最低/成交量/成交额），不同接口的列顺序和多余列（振幅/涨跌幅/换手率）都不一样；按位置取
一旦上游调序就静默错位 —— 那是「红的变成绿的」那类最难查的错。
"""

import re
import os
import json
import math
import time
import zlib
import random
import datetime as dt

import pandas as pd

# canonical 列 → 可能的上游叫法（大小写不敏感，子串匹配）
_COLMAP = {
    'dt':     ['日期', 'date', 'time', 'datetime', 'candle_begin_time', 'ts'],
    'open':   ['开盘', 'open'],
    'high':   ['最高', 'high'],
    'low':    ['最低', 'low'],
    'close':  ['收盘', 'close'],
    'volume': ['成交量', 'volume', 'vol', 'amount_vol'],
    'amount': ['成交额', 'amount', 'turnover', 'quote_volume'],
}
# 匹配顺序要紧：'收盘' 必须先于 '开盘' 之类互不为子串，但 'close' 是 'closes' 的子串无所谓；
# 真正会打架的是 amount/volume —— '成交额' 里没有 '成交量'，安全。
CANON = ['dt', 'open', 'high', 'low', 'close', 'volume', 'amount']

# ── 认代码 ─────────────────────────────────────────────────────────────
# 指数：裸 6 位数字里，这几个是「指数」比「股票」更常见的。交易所前缀能唯一确定，
# 所以带前缀的一律按前缀走，只有裸代码才查这张表。
KNOWN_INDEX = {
    '000001': ('sh', '上证指数'),
    '000300': ('sh', '沪深300'),
    '000016': ('sh', '上证50'),
    '000905': ('sh', '中证500'),
    '000688': ('sh', '科创50'),
    '399001': ('sz', '深证成指'),
    '399006': ('sz', '创业板指'),
    '399005': ('sz', '中小100'),
}
# 几个「说名字比说代码多」的常客，纯粹为了体验，不影响规则本身的正确性
NAME2CODE = {
    '茅台': ('cn_stock', '600519', '贵州茅台'), '贵州茅台': ('cn_stock', '600519', '贵州茅台'),
    '平安银行': ('cn_stock', '000001', '平安银行'),
    '宁德时代': ('cn_stock', '300750', '宁德时代'), '比亚迪': ('cn_stock', '002594', '比亚迪'),
    '沪深300': ('cn_index', '000300', '沪深300'), '上证指数': ('cn_index', '000001', '上证指数'),
    '腾讯': ('hk_stock', '00700', '腾讯控股'), '腾讯控股': ('hk_stock', '00700', '腾讯控股'),
    '苹果': ('us_stock', 'AAPL', 'Apple'), '特斯拉': ('us_stock', 'TSLA', 'Tesla'),
    '比特币': ('crypto', 'BTC', 'Bitcoin'), '以太坊': ('crypto', 'ETH', 'Ethereum'),
}
CRYPTO = {'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'ADA', 'TON', 'AVAX', 'LTC', 'BCH', 'TRX'}


def _norm(raw):
    return re.sub(r'\s+', '', str(raw or '')).strip()


def resolve(raw):
    """把用户输入认成 (market, code, name)。认不出就明说认不出 —— 不许猜一个。

    返回 dict：{ok, market, code, name, ambiguous, candidates, hint}
    """
    s = _norm(raw)
    if not s:
        return {'ok': False, 'hint': '没给代码'}
    up = s.upper()

    if s in NAME2CODE:
        m, c, n = NAME2CODE[s]
        return {'ok': True, 'market': m, 'code': c, 'name': n, 'ambiguous': False}

    # 带交易所前缀：sh600519 / sz000001 / sh000300
    m = re.fullmatch(r'(sh|sz|bj)(\d{6})', s, re.I)
    if m:
        ex, code = m.group(1).lower(), m.group(2)
        if ex == 'sh' and code.startswith('000') and code in KNOWN_INDEX:
            return {'ok': True, 'market': 'cn_index', 'code': code,
                    'name': KNOWN_INDEX[code][1], 'ambiguous': False}
        if ex == 'sz' and code.startswith('399'):
            return {'ok': True, 'market': 'cn_index', 'code': code,
                    'name': KNOWN_INDEX.get(code, (ex, code))[1], 'ambiguous': False}
        return {'ok': True, 'market': 'cn_stock', 'code': code, 'name': code, 'ambiguous': False}

    # 加密：BTC / BTCUSDT / BTC-USDT
    c = re.fullmatch(r'([A-Z]{2,6})(USDT|USDC|USD)?', up)
    if c and c.group(1) in CRYPTO:
        return {'ok': True, 'market': 'crypto', 'code': c.group(1), 'name': c.group(1), 'ambiguous': False}
    c = re.fullmatch(r'([A-Z]{2,6})[-/](USDT|USDC|USD)', up)
    if c and c.group(1) in CRYPTO:
        return {'ok': True, 'market': 'crypto', 'code': c.group(1), 'name': c.group(1), 'ambiguous': False}

    # 纯 6 位数字：A 股（沪 6/9、深 0/3、北 4/8）
    if re.fullmatch(r'\d{6}', s):
        if s in KNOWN_INDEX:
            ex, nm = KNOWN_INDEX[s]
            # ★只有 000001 是真的两义（沪市上证指数 / 深市平安银行）—— 它是唯一
            #   「指数代码同时也是在交易的深市股票」的常客。别的裸指数代码（000300 等）
            #   在深市没有对应股票，硬报歧义只会让智能体每次都多问一句废话。
            if s == '000001':
                cands = [{'market': 'cn_index', 'code': s, 'name': nm},
                         {'market': 'cn_stock', 'code': s, 'name': '平安银行'}]
                return {'ok': True, 'market': 'cn_index', 'code': s, 'name': nm,
                        'ambiguous': True, 'candidates': cands,
                        'hint': '000001 既可能指上证指数，也可能指平安银行，请先问清用户'}
            return {'ok': True, 'market': 'cn_index', 'code': s, 'name': nm, 'ambiguous': False}
        if s[0] in '69':
            return {'ok': True, 'market': 'cn_stock', 'code': s, 'name': s, 'ambiguous': False}
        if s[0] in '03':
            return {'ok': True, 'market': 'cn_stock', 'code': s, 'name': s, 'ambiguous': False}
        if s[0] in '48':
            return {'ok': True, 'market': 'cn_stock', 'code': s, 'name': s, 'ambiguous': False}
        # 场内基金：沪 51/56/58 开头、深 15/16/18 开头 —— 都是 6 位，不加这条会被当成
        # 认不出的代码（ETF 是小白最常买的东西之一，不能漏）。
        if s[0] == '5' or s[:2] in ('15', '16', '18'):
            return {'ok': True, 'market': 'cn_etf', 'code': s, 'name': s, 'ambiguous': False}

    # 港股：3~5 位数字补零到 5 位（腾讯就是 700 → 00700）。1~2 位太容易和别的意思撞，
    # 要求显式写 hk 前缀（在 resolve 的上游没实现，这里直接不受理）。
    if re.fullmatch(r'\d{3,5}', s):
        return {'ok': True, 'market': 'hk_stock', 'code': s.zfill(5), 'name': s.zfill(5), 'ambiguous': False}

    # 美股：1~5 个大写字母（可带 . 或 -，如 BRK.B）
    if re.fullmatch(r'[A-Z]{1,5}([.\-][A-Z]{1,2})?', up):
        return {'ok': True, 'market': 'us_stock', 'code': up, 'name': up, 'ambiguous': False}

    # ★认不出就说认不出。这里绝不能「大概是美股吧」地兜一个 —— 猜错的代价是拿一个
    #   完全不相关的标的去预测，而用户看到的是一张像模像样的 K 线图。
    return {'ok': False, 'hint': '认不出这个标的：「%s」。试试 600519 / sh000300 / 00700 / AAPL / BTC' % s}


# ── 取数 ───────────────────────────────────────────────────────────────

class SourceError(RuntimeError):
    """取数失败。带 code，好让工具层把它翻译成「数据没取到」而不是编一个数字。"""

    def __init__(self, code, msg, hint=''):
        super().__init__(msg)
        self.code, self.hint = code, hint


def proxy_env_fix():
    """摘掉 `socks://` 型的 ALL_PROXY。

    本机的环境里 `ALL_PROXY=socks://127.0.0.1:7897/`：scheme 少了 `5`，requests/urllib3
    不认，于是「本该走代理」的请求会以一个和网络无关的 ProxyError 炸掉 —— 而 curl 认这个
    scheme，所以症状是「curl 通、python 不通」。摘掉它不影响 http_proxy/https_proxy
    （那两条是对的），境外流量照样走代理。
    """
    import os as _os
    for k in ('ALL_PROXY', 'all_proxy'):
        v = _os.environ.get(k, '')
        if v.lower().startswith('socks://'):
            _os.environ.pop(k, None)


def _with_timeout(fn, seconds, label):
    """akshare 有些接口没有超时，网络一抖就永久挂住 —— 服务里挂住比报错糟糕得多。"""
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=seconds)
        except cf.TimeoutError:
            raise SourceError('TIMEOUT', '%s 超过 %ds 没返回' % (label, seconds)) from None


def _ak():
    proxy_env_fix()                              # ★必须早于 requests 的第一次 import
    try:
        import akshare as ak
        return ak
    except Exception as e:                       # noqa: BLE001 —— 装没装上是运行期事实
        raise SourceError('NO_AKSHARE', 'akshare 不可用：%s' % e,
                          'pip install -i https://mirrors.aliyun.com/pypi/simple/ akshare') from e


def to_canon(df):
    """上游 DataFrame → canonical 列。找不到必需列就报错，不猜。"""
    if df is None or len(df) == 0:
        raise SourceError('EMPTY', '数据源返回了空表')
    lower = {str(c).strip().lower(): c for c in df.columns}
    out = {}
    for canon, aliases in _COLMAP.items():
        for a in aliases:                        # 先精确、再子串
            hit = lower.get(a.lower())
            if hit is not None:
                out[canon] = hit
                break
            cands = [orig for low, orig in lower.items() if a.lower() in low]
            if cands:
                out[canon] = cands[0]
                break
    for need in ('open', 'high', 'low', 'close'):
        if need not in out:
            raise SourceError('BAD_COLUMNS',
                              '数据源没给「%s」列，实际列：%s' % (need, list(df.columns)[:12]))
    r = pd.DataFrame()
    for canon in CANON:
        if canon in out:
            r[canon] = df[out[canon]]
        else:
            r[canon] = 0.0                       # volume/amount 允许缺，predictor 自己会补
    r['dt'] = pd.to_datetime(r['dt'], errors='coerce')
    for c in ('open', 'high', 'low', 'close', 'volume', 'amount'):
        r[c] = pd.to_numeric(r[c], errors='coerce')
    r = r.dropna(subset=['dt', 'open', 'high', 'low', 'close']).reset_index(drop=True)
    if len(r) == 0:
        raise SourceError('EMPTY', '清洗后没有有效行（时间或价格列全是坏值）')
    return r.sort_values('dt').reset_index(drop=True)


def _sina_prefix(code, market):
    """新浪的代码要带交易所前缀。"""
    if market == 'cn_index':
        return ('sh' if code.startswith('000') else 'sz') + code
    if code[0] == '6' or code[0] == '9' or code[0] == '5':
        return 'sh' + code
    return 'sz' + code


def _http_json(url, timeout=20, via_proxy=True):
    """直连优先、失败再走本机代理。★反过来的顺序会让「代理没开」也变成失败。"""
    import urllib.request
    proxies = {'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}
    handlers = [urllib.request.ProxyHandler(proxies)] if via_proxy else [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 licai-agent'})
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _fetch_crypto(code, limit, end):
    """akshare 对加密日 K 的支持不确定 ⇒ 直接走 OKX / Binance 公开接口。"""
    inst = '%s-USDT' % code
    errs = []
    for via_proxy in (False, True):
        try:
            j = _http_json('https://www.okx.com/api/v5/market/candles?instId=%s&bar=1D&limit=%d'
                           % (inst, min(limit, 300)), via_proxy=via_proxy)
            rows = j.get('data') or []
            if rows:
                rows = list(reversed(rows))
                return pd.DataFrame({
                    'dt': [pd.to_datetime(int(r[0]), unit='ms') for r in rows],
                    'open': [float(r[1]) for r in rows], 'high': [float(r[2]) for r in rows],
                    'low': [float(r[3]) for r in rows], 'close': [float(r[4]) for r in rows],
                    'volume': [float(r[5]) for r in rows],
                    'amount': [float(r[7]) if len(r) > 7 else 0.0 for r in rows]})
        except Exception as e:                   # noqa: BLE001
            errs.append('OKX(%s): %s' % ('代理' if via_proxy else '直连', str(e)[:60]))
    for via_proxy in (True, False):
        try:
            j = _http_json('https://api.binance.com/api/v3/klines?symbol=%sUSDT&interval=1d&limit=%d'
                           % (code, min(limit, 1000)), via_proxy=via_proxy)
            if j:
                return pd.DataFrame({
                    'dt': [pd.to_datetime(r[0], unit='ms') for r in j],
                    'open': [float(r[1]) for r in j], 'high': [float(r[2]) for r in j],
                    'low': [float(r[3]) for r in j], 'close': [float(r[4]) for r in j],
                    'volume': [float(r[5]) for r in j], 'amount': [float(r[7]) for r in j]})
        except Exception as e:                   # noqa: BLE001
            errs.append('Binance(%s): %s' % ('代理' if via_proxy else '直连', str(e)[:60]))
    raise SourceError('CRYPTO_SOURCE_DOWN', '加密行情两个来源都没取到：%s' % '；'.join(errs))


# 每个市场一条**源链**，按顺序试。
# ★为什么必须多源：实测东财系接口在本机是「时通时不通」—— 同一个域名，前一次
#   `stock_zh_a_hist` 成功、下一次 `stock_zh_index_daily_em` 就 ProxyError；
#   而新浪系四个接口（指数/港股/ETF/美股）全部稳定返回全历史。所以东财在前（它有前复权
#   和成交额，数据更全），新浪兜底；哪个能用用哪个，全挂了才报错并带上全部原因。
SOURCES = {
    'cn_stock': [
        # ★新增源「腾讯」：直连上游（腾讯→新浪→同花顺→akshare 的链在
        #   agent/datasource/ 里，源名与理由见那个包的 __init__.py）。
        #   ★惰性 import 是必须的：datasource/_base.py 反过来要 import feed，
        #   在模块顶层 import 会成环。★`__name__` 判一下是因为 feed.py 被两种
        #   姿势 import（顶层 `feed` / 包内 `agent.feed`），两种下 sys.path 不同。
        ('腾讯', lambda ak, c, s8, e8, adj, n: __import__('importlib').import_module(
            'datasource' if '.' not in __name__ else 'agent.datasource'
        ).feed_frame('cn_stock', c, days=n, adjust=adj)),
        ('东财', lambda ak, c, s8, e8, adj, n: ak.stock_zh_a_hist(
            symbol=c, period='daily', start_date=s8, end_date=e8, adjust=adj)),
        ('新浪', lambda ak, c, s8, e8, adj, n: ak.stock_zh_a_daily(
            symbol=_sina_prefix(c, 'cn_stock'), adjust=adj)),
    ],
    'cn_index': [
        ('腾讯', lambda ak, c, s8, e8, adj, n: __import__('importlib').import_module(
            'datasource' if '.' not in __name__ else 'agent.datasource'
        ).feed_frame('cn_index', c, days=n, adjust=adj)),
        ('新浪', lambda ak, c, s8, e8, adj, n: ak.stock_zh_index_daily(
            symbol=_sina_prefix(c, 'cn_index'))),
        ('东财', lambda ak, c, s8, e8, adj, n: ak.index_zh_a_hist(
            symbol=c, period='daily', start_date=s8, end_date=e8)),
    ],
    'cn_etf': [
        ('腾讯', lambda ak, c, s8, e8, adj, n: __import__('importlib').import_module(
            'datasource' if '.' not in __name__ else 'agent.datasource'
        ).feed_frame('cn_etf', c, days=n, adjust=adj)),
        ('东财', lambda ak, c, s8, e8, adj, n: ak.fund_etf_hist_em(
            symbol=c, period='daily', start_date=s8, end_date=e8, adjust=adj)),
        ('新浪', lambda ak, c, s8, e8, adj, n: ak.fund_etf_hist_sina(
            symbol=_sina_prefix(c, 'cn_etf'))),
    ],
    'hk_stock': [
        ('腾讯', lambda ak, c, s8, e8, adj, n: __import__('importlib').import_module(
            'datasource' if '.' not in __name__ else 'agent.datasource'
        ).feed_frame('hk_stock', c, days=n, adjust=adj)),
        ('东财', lambda ak, c, s8, e8, adj, n: ak.stock_hk_hist(
            symbol=c, period='daily', start_date=s8, end_date=e8, adjust=adj)),
        ('新浪', lambda ak, c, s8, e8, adj, n: ak.stock_hk_daily(symbol=c, adjust=adj)),
    ],
    'us_stock': [
        ('腾讯', lambda ak, c, s8, e8, adj, n: __import__('importlib').import_module(
            'datasource' if '.' not in __name__ else 'agent.datasource'
        ).feed_frame('us_stock', c, days=n, adjust=adj)),
        ('新浪', lambda ak, c, s8, e8, adj, n: ak.stock_us_daily(symbol=c, adjust=adj)),
    ],
}

_PREF = {}          # market → 上次成功的源名。进程内记忆，重启即忘（故意的：源恢复了自己会回来）


# ── 离线桩：让「行情总览」那页在无网下也能被确定性地验 ────────────────────
# 为什么需要它：那一页有一条**对照判据** —— 改掉服务端的返回，页面上的数字必须
# 跟着变。真去改外网做不到：今天东财通、明天不通，判据就成了看运气的东西
# （这条链上的判据一旦"有时跑有时不跑"，它守的门就时开时关）。
# 桩把这件事变成确定的：桩文件里写什么，页面上就必须是什么。
#
# ★只在服务端设了 `LICAI_FEED_STUB` 时才生效，**不接受客户端开关** ——
#   一个"客户端能打开的桩"就是一台伪造行情的机器，"这数是哪来的"这件事就没了。
STUB_ENV = 'LICAI_FEED_STUB'


def stub_active():
    return bool(os.environ.get(STUB_ENV))


def _stub_frame(days, end, key=''):
    """按桩文件造一段**可复现**的行情。

    复现这条是硬要求：同一次判据里，判据自己打 `/api/kline` 拿到的数、
    和页面上渲染出来的数，必须来自同一份帧 —— 两次调用之间只要有一个比特不同，
    对照就变成了"拿 A 去比 B"，绿了也说明不了任何事。

    ★所以随机数用 `random.Random(种子)` **显式播种**，种子由 `zlib.crc32` 算 ——
      不能用内置 `hash()`：它对 str 每进程加盐（PYTHONHASHSEED），
      同一份输入在两个进程里会给出两段完全不同的行情。
    """
    spec = {}
    path = os.environ.get(STUB_ENV) or ''
    if path and path != '1' and os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            spec = json.load(f)
    # 桩里点名让某个标的失败 —— 专门用来验"一个标的取不到、只坏那一行"。
    # ★这条失败得**从取数层真地抛出去**，不能只在页面上摆一个错误样子：
    #   页面那半的降级逻辑要真的走到 except 分支才算验过。
    code = key.split('/')[-1]
    if code and code in (spec.get('fail') or []):
        raise SourceError('STUB_FAIL', '桩里点名让 %s 失败（用来验逐标的降级）' % code)

    base = float(spec.get('base', 100.0))
    n = int(spec.get('days', max(60, int(days))))
    n = max(30, min(n, 1200))
    amp = float(spec.get('amp', 0.012))          # 每日波动幅度

    dates = pd.bdate_range(end=pd.Timestamp(end), periods=n)   # 交易日 ≈ 工作日
    # ★种子必须**带上标的**：不带的话每个代码发的是同一段行情，总览页五行一个数
    #   —— 页面看着"有数据"，其实什么也没在比。这条是肉眼看出来的，判据不会替你发现
    #   （判据只问"数字对不对得上接口"，五行恰好相同它照样绿）。
    seed = int(spec.get('seed', 7)) + zlib.crc32(('%s|%s' % (spec.get('key', ''), key)).encode('utf-8'))
    rnd = random.Random(seed)

    rows, prev = [], base
    for i in range(n):
        o = prev
        c = round(o * (1 + rnd.uniform(-amp, amp)), 4)
        hi = round(max(o, c) * (1 + abs(rnd.uniform(0, amp / 2))), 4)
        lo = round(min(o, c) * (1 - abs(rnd.uniform(0, amp / 2))), 4)
        rows.append({'dt': dates[i], 'open': o, 'high': hi, 'low': lo,
                     'close': c, 'volume': 100000.0 + (i * 137) % 90000, 'amount': 0.0})
        prev = c
    df = pd.DataFrame(rows, columns=CANON)
    df.attrs['source'] = spec.get('source', 'STUB')
    return df.tail(days).reset_index(drop=True)


def fetch(market, code, days=400, adjust='qfq'):
    """取日 K。返回 canonical 列、按时间升序、截到最后 days 根。"""
    days = max(60, min(int(days or 400), 1200))
    end = dt.date.today()
    if stub_active():
        return _stub_frame(days, end, key='%s/%s' % (market, code))
    start = end - dt.timedelta(days=int(days * 1.7) + 30)   # 交易日 < 自然日，留足余量
    s8, e8 = start.strftime('%Y%m%d'), end.strftime('%Y%m%d')

    if market == 'crypto':
        return _fetch_crypto(code, days, end).tail(days).reset_index(drop=True)

    chain = SOURCES.get(market)
    if not chain:
        raise SourceError('BAD_MARKET', '不认识的市场：%s' % market)
    ak = _ak()
    # 粘性：把上次成功的那条源提到最前。东财在本机是「时通时不通」，一个挂掉的源
    # 要走完 urllib3 的重试才失败（实测 2 秒级），每次都白等；但**失败时仍然全链都试**，
    # 所以这只是省时间，不会把「源恢复了」挡在外面。
    pref = _PREF.get(market)
    if pref:
        chain = sorted(chain, key=lambda x: 0 if x[0] == pref else 1)
    errs = []
    for name, fn in chain:
        try:
            raw = _with_timeout(lambda: fn(ak, code, s8, e8, adjust, days), 45, '%s/%s' % (name, code))
            out = to_canon(raw)
            if len(out) < 30:
                raise SourceError('TOO_SHORT', '%s 只回了 %d 行' % (name, len(out)))
            _PREF[market] = name
            out.attrs['source'] = name
            return out.tail(days).reset_index(drop=True)
        except Exception as e:                   # noqa: BLE001
            errs.append('%s: %s' % (name, str(e)[:90]))
    raise SourceError('ALL_SOURCES_FAILED', '%s 取数失败 —— %s' % (code, '；'.join(errs)),
                      '东财接口在本机不稳定，若全部失败请稍后重试或换一个标的')


# ── 基本面（估值 / 财务比率 / 行业）──────────────────────────────────────
# ★与上面那条行情链**结构不同，别照抄**：行情是「同一个东西有多个源，谁通用谁」，
#   所以是全链备选；这里是「三块互不相干的数据，各来自一个接口」——
#   **逐块降级**：一块挂了只少一块，不该拖垮另外两块。缺的那块进 errors，
#   取值一律 None（**绝不补 0**：0 是个会被人当真的数）。
#
# ★三个接口都不是那台常挂的 push2 实时行情主机：
#     估值   stock_value_em        东财 datacenter（存活 ✅）
#     财务   stock_financial_analysis_indicator   新浪（存活 ✅）
#     行业   stock_profile_cninfo  巨潮资讯（存活 ✅）
#   "东财接口本机不稳"这句话**只对 push2 那台成立**（实测 stock_zh_a_spot_em
#   在 82.push2 上 ProxyError，而 stock_value_em 好端端回了 2116 行）——
#   别把整家东财一棍子打死。
#
# ★只对 **A 股个股** 成立。ETF/指数上这两个接口的失败方式不一样，都要接住：
#     stock_profile_cninfo('510300') → **空表**（0 行，不报错，最阴）
#     stock_value_em('510300')       → **TypeError: 'NoneType' object is not subscriptable**
#                                      （akshare 内部拿 None 下标，直接崩）
#   后者不接住的话会一路冒到工具层变成 500。

def _num(x, nd=4):
    """→ JSON 安全的数，或 None。

    三个坑一起堵：
      - **NaN / inf**：`json.dumps` 会写出裸 `NaN`，那不是合法 JSON，
        前端 `JSON.parse` 当场炸（Python 自己读得回来，所以本地测不出来）。
      - **numpy 标量**：`np.int64` 不是 int 的子类，`json.dumps` 报 TypeError。
      - **空值**：一律 None —— 不是 0。
    """
    try:
        if x is None:
            return None
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def _s(row, key, limit=0):
    """取一个字符串字段。取不到/是 NaN 就 None（空串也算没有 —— 空串会被当成"有值"）。"""
    try:
        v = row.get(key)
    except AttributeError:
        return None
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass                                     # 非标量时 pd.isna 会返回数组，忽略
    s = ' '.join(str(v).split())
    if not s:
        return None
    return (s[:limit] + '…') if limit and len(s) > limit else s


# ★分位窗口。**必须是一个常量**：分位和底下那条走势线要用同一个窗口，
#   两处各写各的数字迟早会漂（原来就是：分位 tail(1250)≈5 年，线 tail(250)≈1 年，
#   于是卡头写"跟它自己的近 5 年比"、底下画的是 1 年 —— 一张卡两个窗口）。
PCTILE_WINDOW = 1250                             # ≈5 年交易日


def _pctile(series, value, window=PCTILE_WINDOW):
    """当前值在**它自己历史**里的分位（0~100 表示"比历史上 X% 的时候贵"）。

    ★只用正值：PE 为负是「亏损」，把它混进分位里算出来的数没有意义。
    ★样本不够就返回 None —— 拿 20 个点算出来的"分位"是假精确，宁可说不知道。
    ★这是**时序**分位（这只票跟自己的过去比），不是**截面**分位（跟同行比）。
      两者会给出相反结论，所以名字里必须带清楚，页面上也得写清楚。
    """
    # ★顺序有讲究：先切窗口**再**滤正值。
    #   反过来（先滤后切）窗口会变成"最近 1250 个正值"，亏损年份被跳过之后
    #   窗口实际往前多吃了好几年 —— 那算出来的就不是"过去 5 年的分位"了。
    s = pd.to_numeric(series, errors='coerce').dropna().tail(window)
    s = s[s > 0]
    if value is None or value <= 0 or len(s) < 100:
        return None
    return round(float((s <= value).mean() * 100), 1)


def fetch_fundamentals(code, years_back=2):
    """A 股个股基本面三块。★只对 A 股个股成立（见上）。

    返回 {'ok', 'name', 'industry', 'valuation', 'financial', 'errors', ...}。
    ok 表示「至少拿到一块」；三块全挂才 False。errors 里是人话，会进摘要通道。
    """
    ak = _ak()
    out = {'code': code, 'errors': []}
    hist = None                                  # 估值历史，留着给前端画分位图

    # ① 行业 + 名称 + 主营（巨潮资讯）
    try:
        df = _with_timeout(lambda: ak.stock_profile_cninfo(symbol=code), 30,
                           '巨潮概况/%s' % code)
        if df is None or not len(df):
            # ★空表不是异常，是「这个代码没有公司概况」（ETF/指数就是）
            out['errors'].append('巨潮概况：返回空表（ETF/指数没有公司概况）')
        else:
            r = df.iloc[0]
            out['name'] = _s(r, 'A股简称') or _s(r, '公司名称')
            out['full_name'] = _s(r, '公司名称')
            out['industry'] = _s(r, '所属行业')
            out['board'] = _s(r, '所属市场')
            out['listed'] = _s(r, '上市日期')
            out['business'] = _s(r, '主营业务', limit=120)
            out['industry_src'] = '巨潮资讯·所属行业'
    except Exception as e:                       # noqa: BLE001 —— 源边界，逐块降级
        out['errors'].append('巨潮概况：%s: %s' % (type(e).__name__, str(e)[:80]))

    # ② 估值（东财 datacenter）
    try:
        df = _with_timeout(lambda: ak.stock_value_em(symbol=code), 30, '估值/%s' % code)
        if df is None or not len(df):
            raise SourceError('EMPTY', '估值接口回了空表')
        d = df.copy()
        d['_dt'] = pd.to_datetime(d['数据日期'], errors='coerce')
        d = d.dropna(subset=['_dt']).sort_values('_dt').reset_index(drop=True)
        if not len(d):
            raise SourceError('EMPTY', '估值历史没有一行可解析的日期')
        last = d.iloc[-1]
        pe, pb = _num(last.get('PE(TTM)')), _num(last.get('市净率'))
        cap, fcap = _num(last.get('总市值'), 2), _num(last.get('流通市值'), 2)
        out['valuation'] = {
            'date': last['_dt'].strftime('%Y-%m-%d'),
            'close': _num(last.get('当日收盘价')),
            'pe_ttm': pe, 'pe_static': _num(last.get('PE(静)')), 'pb': pb,
            'peg': _num(last.get('PEG值')), 'ps': _num(last.get('市销率')),
            'pcf': _num(last.get('市现率')),
            'mkt_cap_yi': round(cap / 1e8, 1) if cap else None,
            'float_cap_yi': round(fcap / 1e8, 1) if fcap else None,
            # 分位要有历史才算得出来。这只票自己的过去，不是同行之间。
            'pe_pctile_5y': _pctile(d['PE(TTM)'], pe),
            'pb_pctile_5y': _pctile(d['市净率'], pb),
            'history_rows': len(d),
        }
        out['valuation_src'] = '东财·估值指标'
        hist = d
    except Exception as e:                       # noqa: BLE001 —— 含 akshare 那个 TypeError
        out['errors'].append('估值：%s: %s' % (type(e).__name__, str(e)[:80]))

    # ③ 财务比率（新浪，86 列）
    try:
        yr = str(dt.date.today().year - max(1, int(years_back)))
        df = _with_timeout(lambda: ak.stock_financial_analysis_indicator(
            symbol=code, start_year=yr), 30, '财务指标/%s' % code)
        if df is None or not len(df):
            raise SourceError('EMPTY', '财务指标回了空表')
        d = df.copy()
        d['_dt'] = pd.to_datetime(d['日期'], errors='coerce')
        d = d.dropna(subset=['_dt']).sort_values('_dt').reset_index(drop=True)
        if not len(d):
            raise SourceError('EMPTY', '财务指标没有一行可解析的报告期')
        last = d.iloc[-1]
        out['financial'] = {
            'report_date': last['_dt'].strftime('%Y-%m-%d'),
            'roe': _num(last.get('净资产收益率(%)'), 2),
            'roe_weighted': _num(last.get('加权净资产收益率(%)'), 2),
            'debt_ratio': _num(last.get('资产负债率(%)'), 2),
            'rev_growth': _num(last.get('主营业务收入增长率(%)'), 2),
            'profit_growth': _num(last.get('净利润增长率(%)'), 2),
            'gross_margin': _num(last.get('销售毛利率(%)'), 2),
            'current_ratio': _num(last.get('流动比率'), 2),
            'quick_ratio': _num(last.get('速动比率'), 2),
            # ★这里原本还有一行 'dividend_payout': _num(last.get('股息发放率(%)'), 2)。
            #   **删掉了**，原因是实测（2026-09-19）发现这一列不可用：
            #     ① 整类股票全空 —— 工商银行 0/10、贵州茅台 0/6。组合里最常出现的
            #        银行股恰好没有，恰好是最需要股息数据的那一类。
            #     ② 有值的地方定义不自洽 —— 列名写着"(%)"，长江电力同一只票跨报告期
            #        在 0.0018 ~ 2.2016 之间跳（2023-09-30 = 0.0018，2024-06-30 = 1.8455），
            #        跟每股收益对不上任何一种一致的算法。0.0018% 和 184% 都不是发放率。
            #   一个说不清定义、又在整类标的上为空的列，摆进摘要就是让模型去引用一个
            #   **我解释不了**的数 —— 那正是本项目最不能出的东西。宁可明说没有。
            # ★这一行是**报告期**数据，不是一个"当前的"数。银行没有"主营业务收入
            #   增长率"这类字段（回 NaN ⇒ None），是口径问题，不是取数失败 ——
            #   所以不把 NaN 塞进 errors，页面上也不会显示成一个 0。
            'reports': len(d),
        }
        out['financial_src'] = '新浪·财务分析指标'
    except Exception as e:                       # noqa: BLE001
        out['errors'].append('财务指标：%s: %s' % (type(e).__name__, str(e)[:80]))

    out['ok'] = bool(out.get('valuation') or out.get('industry'))
    # 给前端：估值历史（画"PE 在自己历史里的位置"那张图用）
    # ★窗口与 `_pctile` **同一个**（PCTILE_WINDOW ≈ 5 年）。原来这里是 tail(250)，
    #   卡上写着"近 5 年分位"、线只画 1 年 —— 用户看着 1 年的线读"近 5 年"，
    #   于是"这条线在低位 ⇒ 便宜"（1 年的话）和"分位 3.4%"（5 年的话）是两句不同的话。
    # ★按月取**最后一个交易日**抽稀：1250 个点画不下也不需要（这条线只回答
    #   "现在落在历史的哪个位置"），抽到 ~60 点，但**跨度一天不缩**。
    # ★区间(lo/hi)用**整窗口**算，不是抽样点上的 —— 否则"区间"是抽样的区间、
    #   分位是整窗口的分位，又变成两个口径。（月末抽样会漏掉月中的极值。）
    if hist is not None and len(hist):
        w = hist.tail(PCTILE_WINDOW).copy()
        w['_ym'] = w['_dt'].dt.strftime('%Y-%m')
        # ★这里**不能**用 `groupby(...).last()` —— 它是"每列最后一个**非空**值"，
        #   某月最后一个交易日 PE 为空时会悄悄拿月中的值，而 `_dt` 照样是月末，
        #   于是"日期↔数值"错位。（`.tail(1)` 取的才是每组的最后**一行**。）
        m = w.groupby('_ym', as_index=False).tail(1)
        out['series'] = {
            't': [x.strftime('%Y-%m-%d') for x in m['_dt']],
            'pe': [_num(v) for v in m.get('PE(TTM)', [])],
            'pb': [_num(v) for v in m.get('市净率', [])],
            'span': '近 5 年（按月取月末）',
        }
        for col, key in (('PE(TTM)', 'pe'), ('市净率', 'pb')):
            if col not in w.columns:                 # 上游少一列就别硬算区间
                continue
            s = pd.to_numeric(w[col], errors='coerce').dropna()
            s = s[s > 0]                             # 与 `_pctile` 同一条正值规则
            if len(s) >= 100:                        # 样本不够就不给区间（别假精确）
                out[key + '_lo5y'] = round(float(s.min()), 2)
                out[key + '_hi5y'] = round(float(s.max()), 2)
    return out


if __name__ == '__main__':
    # 探针：把每条 route 的真实列名和行数打出来。akshare 的接口名/列名不能凭记忆写，
    # 这个脚本就是把它变成实测事实的那一步。
    print('=== resolve 表 ===')
    for probe in ['600519', 'sh600519', 'sh000300', '000300', '000001', 'sh000001',
                  '00700', '700', 'AAPL', 'BRK.B', 'BTC', 'BTCUSDT', '茅台', '沪深300',
                  '今天天气不错', '']:
        r = resolve(probe)
        print('  %-14s → %s' % (probe or '(空)', json.dumps(r, ensure_ascii=False)))
    print('\n=== 取数探针 ===')
    for market, code in [('cn_stock', '600519'), ('cn_index', '000300'), ('hk_stock', '00700'),
                         ('us_stock', 'AAPL'), ('crypto', 'BTC')]:
        t0 = time.time()
        try:
            df = fetch(market, code, days=60)
            print('  ✓ %-9s %-8s %3d 行  末行: %s  O=%.2f C=%.2f  (%.1fs)'
                  % (market, code, len(df), df['dt'].iloc[-1].date(),
                     df['open'].iloc[-1], df['close'].iloc[-1], time.time() - t0))
        except Exception as e:                   # noqa: BLE001
            print('  ✗ %-9s %-8s %s: %s' % (market, code, type(e).__name__, e))
    print('\n=== 基本面探针（个股 / ETF / 指数 三种都要看）===')
    for code in ('600519', '000001', '510300', '000300'):
        t0 = time.time()
        try:
            f = fetch_fundamentals(code)
            v = f.get('valuation') or {}
            print('  %-7s ok=%-5s 行业=%-12s PE=%s PB=%s 市值=%s亿  (%.1fs)'
                  % (code, f['ok'], f.get('industry'), v.get('pe_ttm'), v.get('pb'),
                     v.get('mkt_cap_yi'), time.time() - t0))
            for e in f['errors']:
                print('        ⚠', e)
        except Exception as e:                   # noqa: BLE001
            print('  ✗ %-7s %s: %s' % (code, type(e).__name__, e))
        # ★摘要通道的硬要求：不能有 NaN。allow_nan=False 会让它当场抛。
        try:
            json.dumps(f, ensure_ascii=False, allow_nan=False)
        except ValueError as e:
            print('        ✗✗ 摘要里有非法 JSON 值（NaN/inf）：%s' % e)
