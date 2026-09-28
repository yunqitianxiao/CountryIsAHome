"""`agent.datasource` —— 本数据层的**唯一出口**。上层只 import 这个包，别去碰里面的源模块。

契约见 `docs/AGENT-SPEC.md` §3.1，十个函数逐字实现：
`daily / minute / realtime / snapshot / boards / board_members / stock_boards /
 capital_flow / trade_calendar / health`，外加 `CANON`。

## 为什么要有这一层（而不是直接用 `agent/feed.py`）
`feed.py` 那条链**整条都是 akshare**，而 akshare 的 A股/港股日K 主源是东财 ——
本机（2026-09-22 实测）**东财全部 API 主机 0.2 秒就被断连**。所以 feed.py 那条链
现在实际只剩新浪在扛，而且**它是按 akshare 的列名设计的**，要分钟线、板块、
全市场实时这三件事，它一件都没有。

这一层补的就是这三件，并且把"源"从 akshare 一个包换成**四个直连上游**。

## 源链（★逐源降级，全挂了才报错，且把每个源的真原因一起抛上去）

| 能力 | 链 |
|---|---|
| 日 K（A股/ETF/指数/港股/美股） | **腾讯 → 新浪 → 同花顺 → akshare(feed)** |
| 日 K（加密） | `feed.fetch('crypto', …)`（**不重写**，见 §0 铁律） |
| 分钟 K（A股） | **新浪 `scale=` → adata(minute_1m，仅 period=1)** |
| 分钟 K（港/美/加密） | **本机没有可用来源** ⇒ 如实失败（见 `sina.minute` 的说明） |
| 全市场实时 | **新浪名单接口**（A股 5566 / 港股 2580 / 美股部分）→ 腾讯批量 |
| 指定标的快照 | **腾讯批量**（一个请求混装 A股+港股+美股）→ 新浪批量 |
| 板块清单 | **腾讯**（概念 803 / 行业 31）→ 同花顺（361/90，只有 code+name） |
| 板块成分股 | **腾讯**（翻页到 total）→ 同花顺（只 10 个/页）→ adata |
| 个股所属板块 | **adata `get_plate_east`** |
| 资金流 | **adata**（分钟级能取；日频**取不到**，如实报） |
| 交易日历 | **akshare（8797 行）→ adata（365 行）**，出口砍掉未来日期 |

★为什么"多源"是硬要求而不是洁癖：见 `feed.py: SOURCES` 上面那段注释 ——
上游是"时通时不通"的，单源等于把整页数据押在一次握手成不成功上。
这里每个市场都至少两条独立的路（腾讯和新浪是两家公司、两套代码形态、
两个数据中心），实测它们**不是同时挂的**。

★**东财要区别对待**（这条推翻了初版结论，见 `_KNOWN_DEAD`）：死的只有
`push2*.eastmoney.com` 那一族（`RemoteDisconnected`），`datacenter*.eastmoney.com`
是**活的**。所以"东财不可用"这句话只对走 `push2*` 的接口成立 ——
`adata.get_plate_east` 走 `datacenter`，实测 198ms 正常。

## 三条贯穿性纪律（违反任何一条都会产出"看着正常的假数据"）

1. **缺值是 None，不是 0。** 所有出参都过 `feed._num()`；
   DataFrame 里缺的列是 **NaN**（不是 0.0）并带 `attrs['amount_missing']`。
   0 是个会被下游当真的数（"成交额为零"和"上游没给这一列"在图上没区别，
   但在"这个数能不能用"上是相反的答案）。
2. **失败不许静默。** 每个源每次调用都往 `_base.note()` 记一条（成功/失败都记），
   `health()` 把它们连同**已知不可达的上游**一起交出去。空表**不是**成功。
3. **桩只在服务端开。** `LICAI_FEED_STUB` 由 `feed.py` 读同一个文件，
   这里复用它的 `stub_active()` —— 一个"客户端能打开的桩"就是一台伪造行情的机器。

## 关于"东财不可达"这件事如何在 `health()` 里体现
见 `_KNOWN_DEAD`：它不是一条调用记录，而是一张**静态的、带日期的实测结论表**，
每次都随 `health()` 一起返回。理由：东财是**结构性**不可达（路径级拦截），
不会因为今天没人调它就从"不可达"变成"未知"。
"""

import datetime as dt
import os
import re

import pandas as pd

from . import _base

# ── 契约常量与再导出 ───────────────────────────────────────────────────
CANON = _base.CANON
SourceError = _base.SourceError
CACHE = _base.cached                                 # 给判据/运维用的把手
forget_health = _base.forget_health
cache_clear = _base.cache_clear

# 东财的板块码（`BK` + 数字，如 `BK0438`）。★只用于**认码空间**（`board_members`
# 拿它给调用方指路），不是用来查数的 —— 见那个函数的 docstring。
_DM_CODE_RE = re.compile(r'^BK\d{3,6}$')

# 缓存 TTL（秒）。★实时类要短（分钟线 2min、快照 1min），
# 名单类可以长（板块成分一天一变、日历一年一变）。
TTL = {'daily': 300, 'minute': 120, 'realtime': 60, 'snapshot': 30,
       'boards': 600, 'board_members': 3600, 'stock_boards': 3600,
       'capital_flow': 300, 'calendar': 86400}

# 市场别名 → canonical。上层（工具层/前端）三种写法都有人在用，全部认。
_MARKET_ALIAS = {
    'cn': 'cn_stock', 'a': 'cn_stock', 'ashare': 'cn_stock', 'cn_stock': 'cn_stock',
    'sh': 'cn_stock', 'sz': 'cn_stock', 'bj': 'cn_stock', '沪深a股': 'cn_stock',
    'cn_index': 'cn_index', 'index': 'cn_index', '指数': 'cn_index',
    'cn_etf': 'cn_etf', 'etf': 'cn_etf',
    'hk': 'hk_stock', 'hk_stock': 'hk_stock', '港股': 'hk_stock',
    'us': 'us_stock', 'us_stock': 'us_stock', '美股': 'us_stock',
    'crypto': 'crypto', 'coin': 'crypto', '加密': 'crypto',
}

# 实时行/快照行的**契约字段**（逐字）。做一次归一，保证每个 dict 都长这样，
# 不许有的行多一个键、有的行少一个 —— 前端按下标取值就会拿到别人的字段。
_RT_FIELDS = ('code', 'name', 'market', 'price', 'open', 'high', 'low',
              'prev_close', 'pct', 'volume', 'amount', 'turnover', 'pe', 'ts')
_BOARD_FIELDS = ('code', 'name', 'kind', 'pct', 'amount', 'vol_ratio',
                 'lead_code', 'lead_name', 'ts')

# ★静态的、带日期的上游可达性结论。和"这次调用成没成功"是两回事：
#   这是"这台机器上这条路根本走不通"，不会因为今天没人调它就变成未知。
#   ★★措辞必须**精确到主机族**：先写的"东财全部 API 主机不可达"是**错的**，
#     后来把东财每台主机逐个打了一遍才发现分界线在 `push2*` / `datacenter*` 之间
#     （见 `adata_src.py` 模块头那张表）。把范围写宽了，会让人放弃一整片还能用的接口。
_KNOWN_DEAD = {
    'eastmoney.push2': ('**东财 `push2*` 主机族**（push2 / push2his / 82.push2 / push2delay'
                        '.eastmoney.com）直连 0.2s 被断连（RemoteDisconnected）。'
                        'efinance 整包、adata 日K、akshare 的 *_em 系列、东财的资金流接口'
                        '全走这一族，因此都不可用。'
                        '★**不是"东财整站墙"**：`datacenter.eastmoney.com` 与 '
                        '`datacenter-web.eastmoney.com` 实测**是通的**（前者 269ms 回真实 JSON，'
                        '后者 RPT_DAILYBILLBOARD_DETAILSNEW 实测 89538 页），'
                        '`quote`/`data.eastmoney.com` 网页也 200；只有 `f10.eastmoney.com` 403。'),
    'eastmoney.f10': 'f10.eastmoney.com 实测 HTTP 403（同族里唯一被拒的网页主机）',
    'baidu.pae': ('百度股市通 finance.pae.baidu.com 实测 403（isCaptchaEnabled: true，风控）。'
                  '★曾误以为 adata.get_plate_east 打的是这里 —— **不是**，'
                  '它打 `datacenter.eastmoney.com`（读 adata 源码确认），'
                  '所以那 198ms 的成功与百度无关，这条 403 依旧成立'),
    'xueqiu': '雪球 stock.xueqiu.com 需 cookie，实测 400',
    'yahoo': 'Yahoo query1.finance.yahoo.com 实测 403',
}


# ── 小工具 ─────────────────────────────────────────────────────────────

def _norm_market(m):
    """市场写法 → canonical。

    ★★报错里列的必须是**键**（= 你能真传进来的写法），不是 `.values()`。
      取 values 会印出一张"看着像可用、其实答非所问"的清单：值和键**不是同一集合**
      —— 键有 `a`/`ashare`/`sh`/`sz`/`bj`/`etf`/`港股`/`美股`/`加密` 这些能传的写法，
      而 values 只有 6 个 canonical 名。更坏的是**两个集合长得极像**
      （`etf_cn` 被拒时清单里赫然一个 `cn_etf`），读者会把"我写的那个"和清单里
      那个近似项对上号，于是得不出"我写错了"这个结论。
    """
    k = str(m or 'cn').strip().lower().replace('-', '_').replace(' ', '')
    out = _MARKET_ALIAS.get(k)
    if not out:
        raise SourceError('BAD_MARKET', '不认识的市场：%r（可用写法：%s）'
                          % (m, '/'.join(sorted(_MARKET_ALIAS))))
    return out


def _split_code(item):
    """一个标的可以是 `('hk_stock','00700')` 也可以是 `'sh600519'`/`'gb_aapl'`。

    ★`snapshot()` 的契约是"codes 可以跨市场"，所以这里必须能把三种前缀写法
      都认出来 —— 认错了会把 A股的代码丢进美股查询，然后**静默缺条**
      （上游对不存在的代码不回行，不报错）。
    """
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return _norm_market(item[0]), str(item[1]).strip()
    s = str(item).strip()
    low = s.lower()
    if low.startswith('rt_hk'):
        return 'hk_stock', s[5:]
    if low.startswith('gb_'):
        return 'us_stock', s[3:].upper()
    if low[:2] in ('sh', 'sz', 'bj') and len(s) > 2 and s[2:].isdigit():
        return 'cn_stock', s[2:]
    if low.startswith('hk') and s[2:].isdigit():
        return 'hk_stock', s[2:]
    if low.startswith('us') and not s[2:].isdigit():
        return 'us_stock', s[2:].upper()
    if s.isdigit() and len(s) == 6:
        # 6 位纯数字默认按 A股（指数要用 `('cn_index','000300')` 显式指定 ——
        # `000300` 同时也是"深市某只债"的形态，靠猜会把指数当成债券）
        return 'cn_stock', s
    if s.isdigit() and len(s) == 5:
        # ★5 位纯数字 = 港股。这是**实测撞出来的**一个洞，不是补的保险：
        #   原本没有这一支，`snapshot(['600519','AAPL','00700'])` 里的 `00700`
        #   一路掉到最后那句 `return 'us_stock', ...` ⇒ 被当成美股查 ⇒
        #   上游对不存在的代码**静默缺条** ⇒ 返回里那一行
        #   `{"code":"00700","name":null,"market":"us_stock",全 None}`。
        #   看着像"这个码不存在"，其实是**我查错了市场** —— 而这一条不报错。
        #   港股就是 5 位补零（00700 / 09988），A股是 6 位，美股代码是字母，
        #   三者在纯数字形态上不重叠。
        #   ★4 位（港股的不补零写法 `700`）**故意不认**：它和"某个纯数字美股
        #     代码"分不开，宁可掉进最后那句让调用方看到 us_stock 这个错答案，
        #     也不在这里编一套没有实测支撑的推断 —— 要 4 位写法请写
        #     `('hk_stock','0700')` 或 `hk0700`。
        return 'hk_stock', s
    if s and not s.replace('.', '').isalnum():
        raise SourceError('BAD_CODE', '认不出这个标的：%r' % item)
    return 'us_stock', s.upper()


def _rt(row, market=None):
    """把任意源给的一行压成**契约字段**的 dict，所有数字过 `feed._num()`。

    ★两个"必须"：① 字段集合恒定（见 `_RT_FIELDS`）；② 缺值是 None 不是 0。
      一个漏掉的字段到了前端会变成 `undefined` 渲染成 "NaN"，比空白更坏。
    """
    src = row if isinstance(row, dict) else {}
    out = {}
    for f in _RT_FIELDS:
        v = src.get(f)
        if f in ('code', 'name', 'market', 'ts'):
            out[f] = (str(v).strip() or None) if v is not None else None
        else:
            out[f] = _base.num(v)
    if market and not out['market']:
        out['market'] = market
    return out


def _dezero(d):
    """把上游拿 **0** 当"这一格没数据"的地方翻成 None（契约：缺值是 None 不是 0）。

    ★为什么要有它：规格 §0 那条"缺值必须是 None"不是洁癖 —— 0 到前端会渲染成
      "开盘 0.00 / 最高 0.00"，页面上看着**像一条真行情**，而 None 是空白、
      一眼看出没数据。一个静默的 0 比一个显式的空危险得多。

    ★★只认**成组**出现的 0，绝不单看某一格。本机实测（2026-09-22）这两组形态：
      · `open == high == low == 0` 且 `price > 0` —— 当日没有分时行情
        （停牌 / 未开盘 / 当日无成交）。实测 cn 13 行、hk 544 行、us 80 行，
        而且**三条的计数一模一样**，是同一个占位符，不是零散噪声。
      · `price == 0` —— 价格这一格没有值（死交易对 / 长期停牌）。
        实测 crypto 746 行里 74 行（BCC、VEN、PAX 这类已下架交易对）连
        `price` 都是 0；cn/hk 里紧跟着的 6/5 行 open/high/low 也是 0，
        是**同一个占位符**，所以这一支要连 open/high/low 一起翻 ——
        否则会留下"price 空白、开盘价 0.00"这种自相矛盾的一行。
    ★判据的**误伤面为零**：一个真有成交的标的，`high >= price > 0`，
      所以 open/high/low 三条同时正好等于 0 在数学上不可能；而"某一格是 0"
      单看会把**真的 0**（0 涨跌幅、当天 0 成交量）错翻成 None —— 那是改口径，
      不是修占位符。所以这里逐字要求"三条同时"。
    ★`prev_close` 的 0 **不动**：实测 cn 0 行、hk 6 行，量太小且语义真含糊
      （"前收 0"可能是停牌前的真实态也可能是占位符，没有第二个字段能判），
      不编推断；记在交付说明的"没验的"里。
    ★`volume`/`amount` 的 0 **不动**：当天没成交，成交量就是 0，那是真值。
    """
    p = d.get('price')
    dead = (p == 0.0)
    if dead:
        d['price'] = None
    if (d.get('open') == 0.0 and d.get('high') == 0.0 and d.get('low') == 0.0
            and (dead or p is not None)):
        # `dead` 那一支：price 都是 0 了，这一整条没有报价（长期停牌/已下架），
        #   open/high/low 是**同一个占位符**，必须跟着翻 —— 不然会留下
        #   "price 是空白、开盘价 0.00"这种自相矛盾的一行。
        d['open'] = d['high'] = d['low'] = None
    return d


def _jclean(rows):
    """出口前的最后一道：确保 `json.dumps(..., allow_nan=False)` 一定过得去。

    ★为什么单独一步而不是信任上游：numpy 标量（`np.float64`）**不是** JSON 能序列化的
      类型，而 `json.dumps` 对 NaN 抛的错和"缺个字段"抛的错长得完全一样。
      规格 §0 把这一条列成硬要求，所以在这里**再**过一遍，不靠"上面应该已经转好了"。
    """
    out = []
    for r in (rows or []):
        d = {}
        for k, v in (r or {}).items():
            if isinstance(v, float):
                d[k] = _base.num(v)
            elif v is None or isinstance(v, (str, int, bool)):
                d[k] = v
            else:
                d[k] = _base.num(v) if hasattr(v, 'item') or hasattr(v, '__float__') else str(v)
        out.append(_dezero(d))
    return out


def _chain(attempts, label, want=1):
    """逐源降级跑一条链。

    `attempts` 是 `[(源 id, 无参函数, 超时秒), ...]`。
    ★全失败时抛的 `SourceError` 里**带上每一个源的真原因** —— 只报一句
      "都失败了"会让排查的人把四个源重试一遍，而原因在第一条里就写着。
    `want(n)` 可以再验一次行数（防"回了 0 行但不算错"的上游）。
    """
    errs = []
    for sid, fn, to in attempts:
        try:
            out = _base.timed(sid, fn, timeout=to, label=label)
            if want is not None:
                n = len(out) if hasattr(out, '__len__') else (1 if out else 0)
                if n < want:
                    raise SourceError('TOO_FEW', '只回了 %d 条（要 ≥%d）' % (n, want))
            return out
        except Exception as e:                   # noqa: BLE001 —— 源边界，如实记并降级
            errs.append('%s: %s' % (sid, str(e)[:110]))
    raise SourceError('ALL_SOURCES_FAILED', '%s 所有源都失败 —— %s'
                      % (label, '；'.join(errs)), '上游在本机不稳定，稍后重试或换标的')


def _stub():
    from . import stub                          # noqa: PLC0415
    return stub


# ── 1. 日 K ────────────────────────────────────────────────────────────

def daily(market, code, days=400, adjust='qfq'):
    """日 K → CANON 列的 DataFrame（dt 升序），`attrs['source']` 是**源的名字**。

    ★`days` 是**请求**根数，实到可能更少（腾讯 >640 会被截、同花顺恒定 140）：
      这不是错误，是上游深度不一样。真要长历史就把 `days` 调大让链走到新浪
      （实测 1023 根，本机最深）。
    """
    mk = _norm_market(market)
    code = str(code).strip()
    days = max(30, min(int(days or 400), 1200))
    key = '%s|%s|%d|%s' % (mk, code, days, adjust)

    def build():
        df = _daily_uncached(mk, code, days, adjust)
        # ★出口统一裁到 `days` 行（保留最新）。为什么必须在这一层裁：
        #   各源"多给几根"的习惯不一样 —— 腾讯 `kline` 故意多要 1 根
        #   （`n = days + 1`，为了第一根能算涨跌幅），新浪按 `datalen` 精确给，
        #   `feed.fetch` 自己 `tail(days)`。不在出口裁的话，同一个 `days=120`
        #   在这条链上会拿到 120/121/122 三种行数 —— **落哪个源取决于上游今天通不通**，
        #   于是"行数"这个量在下游就成了源的身份指纹，而不是它自己的含义。
        return df.tail(days).reset_index(drop=True)
    return _base.cached('daily', key, TTL['daily'], build)


def _daily_uncached(mk, code, days, adjust):
    if _base.stub_active():
        df = _stub().frame(days, key='%s/%s' % (mk, code))
        df.attrs['source'] = df.attrs.get('source', 'STUB')
        return df.tail(days).reset_index(drop=True)
    if mk == 'crypto':
        # ★不重写：加密那条链（OKX→Binance，直连优先、失败再走代理）已经在
        #   `feed.py: _fetch_crypto` 里，抄一遍等于给自己留一个会漂的副本。
        return _base.feed.fetch('crypto', code, days=days)

    from . import tencent, sina, ths, akshare_src   # noqa: PLC0415

    att = []

    def _ten():
        return tencent.kline(mk, code, days=days, adjust=adjust)
    att.append(('tencent.kline.%s' % mk, _ten, 25))

    if mk in ('cn_stock', 'cn_etf', 'cn_index'):
        def _sina():
            return sina.daily_via_scale(mk, code, days=days)
        att.append(('sina.kline.%s' % mk, _sina, 25))

    if mk == 'cn_stock':
        def _ths():
            return ths.kline_daily(code)
        att.append(('ths.kline.cn_stock', _ths, 25))

    # akshare 垫底。★**不许删**：它是本机**最深**的日线（A股 6010 根 vs 腾讯 641、
    # 新浪 scale 1023、同花顺 140），而且是唯一带成交额 + 唯一带复权参数的兜底。
    # ★这里直接调 `akshare_src`，**不走 `feed.fetch`** —— feed 那条链的第一个源是
    #   东财（本机不可达），走它会先白等 5 秒的 `RemoteDisconnected` 超时。
    #   `feed.fetch` 仍然在 `mk == 'crypto'` 那条路上用（那才是它的独有能力）。
    def _ak():
        return akshare_src.kline_daily(mk, code, days=days, adjust=adjust)
    att.append(('akshare.kline.%s' % mk, _ak, 70))

    return _chain(att, 'daily/%s/%s' % (mk, code), want=1)


# ── 2. 分钟 K ──────────────────────────────────────────────────────────

# A股一天 240 分钟 ⇒ 每个周期一天的根数
_BARS_PER_DAY = {1: 241, 5: 49, 15: 17, 30: 9, 60: 5}


def minute(market, code, period=5, days=5):
    """分钟 K → CANON 列，`attrs['period']` 是周期。

    ★**A股走新浪 `scale=` 直接取，不从 1 分钟重采样**（实测上游直接支持
      5/15/30/60，自己重采样只会把上游已经对齐好的 bar 边界弄歪）。
    ★`period=1` 走 adata（实测**时好时坏**，`attrs['flaky']=True`）。
    ★港/美/加密**本机没有可用来源**，如实抛 `SourceError` —— 见 `sina.minute` docstring。
    """
    mk = _norm_market(market)
    code = str(code).strip()
    period = int(period or 5)
    days = max(1, min(int(days or 5), 30))
    if period not in (1, 5, 15, 30, 60):
        raise SourceError('BAD_PERIOD', '周期只能是 1/5/15/30/60，收到 %r' % period)

    def build():
        if _base.stub_active():
            df = _stub().minute_frame(period=period, days=days, key='%s/%s' % (mk, code),
                                      market=mk)
            return df
        if not mk.startswith('cn'):
            raise SourceError(
                'NO_MINUTE_SOURCE',
                '%s 的分钟线在本机没有可用来源：腾讯 fqkline 对港美股的 m5 只回一根'
                '聚合日线（实测 1 根，且时间戳是日期）；新浪 getKLineData 只服务 A股；'
                'akshare 的 stock_hk/us_hist_min_em 走东财（不可达）' % mk,
                '分钟线目前只支持 A 股')
        n = min(_BARS_PER_DAY.get(period, 49) * days, 1023)
        att = []
        if period in (5, 15, 30, 60):
            from . import sina                   # noqa: PLC0415

            def _sina():
                return sina.minute(mk, code, period=period, datalen=n)
            att.append(('sina.minute.%d' % period, _sina, 25))
        else:
            from . import adata_src               # noqa: PLC0415

            def _ad():
                return adata_src.minute_1m(code)
            att.append(('adata.minute.1', _ad, 45))
        try:
            return _chain(att, 'minute/%s/%s/%d' % (mk, code, period), want=1)
        except SourceError as e:
            # ★把"这个周期在这一层根本没有源"这件事说出来。1 分钟在 A股是
            #   真实存在的周期（只是上游不给 good 数据），报错要说清是"源不稳"，
            #   不是"这个周期不合法" —— 否则上层会把它当成参数错误去改参数。
            if period == 1:
                raise SourceError(
                    'NO_STABLE_SOURCE',
                    'A股 1 分钟线没有稳定来源：新浪 scale=1 实测回 null；'
                    'adata get_market_min 实测一次 266 行、一次 0 行（内部降级到百度 403）。'
                    '原始失败：%s' % e, '用 5 分钟代替，或接受它时好时坏') from None
            raise
    return _base.cached('minute', '%s|%s|%d|%d' % (mk, code, period, days),
                        TTL['minute'], build)


# ── 3. 全市场实时 ──────────────────────────────────────────────────────

def realtime(market='cn'):
    """全市场实时快照 → `list[dict]`，字段见 `_RT_FIELDS`，缺值是 None。

    ★"全市场"在这台机器上是**有条件的**，条件写在 `health()` 里而不是藏起来：

      | 市场 | 上游自报总数 | 本层实到 | 怎么拿的 |
      |---|---|---|---|
      | A股 | 5566 | **5566（全量）** | 新浪 `Market_Center` 翻 56 页，2.3s |
      | 港股 | 2498 | **2580（全量）** | 新浪 `getHKStockData` 翻 43 页，4.1s |
      | 美股 | 18165 | **1200（部分！）** | 新浪 `US_CategoryService` 每页只给 20 行 |

      美股要 909 页才翻得完（实测每页 20 行、`num` 参数被服务端忽略）。
      默认只取前 60 页 = 1200 只，**并且把"这是部分数据"写进 health 的 err**。
      要把预算调大：环境变量 `LICAI_US_PAGES`。
      ★拿不全就说拿不全 —— 把 1200 只当成"美股全市场"会让人以为美股只有 1200 只。
    """
    mk = _norm_market(market)

    def build():
        if _base.stub_active():
            return _jclean(_stub().realtime_frame(mk))
        if mk == 'crypto':
            return _jclean(_crypto_rt())
        rows, meta = _market_rt(mk)
        if not rows:
            raise SourceError('EMPTY', '%s 全市场实时一行都没取到' % mk)
        return _jclean(rows)
    out = _base.cached('realtime', mk, TTL['realtime'], build)
    return out


def _market_rt(mk):
    """走新浪的名单接口拿全市场 —— 一行就是一条完整快照（含 PE/换手/市值）。

    ★比"先拉代码表、再按代码批量打行情"少一半请求，而且是**同一时刻**的一个截面；
      分两步取的话，两步之间市场还在动，拼出来的"全市场"是个从未存在过的状态。
    """
    from . import sina                           # noqa: PLC0415
    m = {'cn_stock': 'cn', 'hk_stock': 'hk', 'us_stock': 'us'}.get(mk)
    if not m:
        raise SourceError('BAD_MARKET', '全市场实时不支持 %s' % mk)
    pages = None
    if m == 'us':
        pages = max(20, int(os.environ.get('LICAI_US_PAGES', '60')))
    rows, meta = _base.timed('sina.market_list.%s' % m,
                             lambda: sina.market_list(m, pages=pages),
                             timeout=90, label='sina.market_list.%s' % m,
                             count=lambda r: len(r[0]))
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        rt = sina.list_row_to_rt(r, m)
        if rt.get('code'):
            out.append(rt)
    if not out:
        raise SourceError('EMPTY', '新浪 %s 名单解出来 0 行' % m)
    if meta.get('partial'):
        # ★部分数据**不冒充**全量：ok=false + 人话，前端会把这行显示成红的。
        _base.note('sina.market_list.%s' % m, False, 0, len(out),
                   '只取到 %d/%s 行（页预算 %s 页）；调大 LICAI_US_PAGES 可多取，'
                   '但美股每页只有 20 行' % (len(out), meta.get('total'), meta.get('pages')))
    return out, meta


def _crypto_rt():
    """加密全市场。★走本机 Clash 代理 —— 这是**唯一**需要代理的一组：
    实测 OKX `api/v5/market/tickers?instType=SPOT` → 1412 个交易对，
    Binance `/api/v3/ticker/24hr` → 3710 个，**直连不通、走 127.0.0.1:7897 才通**。
    """
    import json                                  # noqa: PLC0415
    from . import _base as B                     # noqa: PLC0415
    errs = []
    try:
        txt = B.http_get('https://www.okx.com/api/v5/market/tickers',
                         params={'instType': 'SPOT'}, via_proxy=True, timeout=20,
                         label='okx.tickers')
        out = []
        for r in (json.loads(txt).get('data') or []):
            inst = str(r.get('instId') or '')
            if not inst.endswith('-USDT'):
                continue
            out.append(_crypto_row(inst[:-5], r.get('last'), r.get('open24h'),
                                   r.get('high24h'), r.get('low24h'), r.get('vol24h'),
                                   r.get('volCcy24h'), r.get('ts')))
        if out:
            return out
        errs.append('OKX: 一个 -USDT 交易对都没有')
    except Exception as e:                       # noqa: BLE001
        errs.append('OKX: %s' % str(e)[:90])
    try:
        txt = B.http_get('https://api.binance.com/api/v3/ticker/24hr', via_proxy=True,
                         timeout=20, label='binance.tickers')
        rows = json.loads(txt) or []
        out = []
        for r in rows:
            sym = str(r.get('symbol') or '')
            if not sym.endswith('USDT'):
                continue
            out.append(_crypto_row(sym[:-4], r.get('lastPrice'), r.get('openPrice'),
                                   r.get('highPrice'), r.get('lowPrice'),
                                   r.get('volume'), r.get('quoteVolume'), None))
        if out:
            return out
        errs.append('Binance: 一个 *USDT 交易对都没有')
    except Exception as e:                       # noqa: BLE001
        errs.append('Binance: %s' % str(e)[:90])
    raise SourceError('CRYPTO_SOURCE_DOWN', '加密实时两个来源都没取到：%s' % '；'.join(errs))


def _crypto_row(code, price, open_, high, low, vol, amt, ts_ms):
    """加密一行。★**不补 0**：Binance 的 `openPrice` 在部分交易对上是 "0.00000000"，
    直接透出会显示成"今天开盘价 0" —— 那是上游的占位符，不是价格。

    ★★**所有数字都要过 `_base.num()`**：两家上游的 JSON 里数字是**字符串**
      （OKX：`"last":"62.03"`；Binance 也是 `"lastPrice":"62.03000000"`）。
      直接透出的话返回的是 `{"price": "62.03"}` —— 一个**字符串**混在
      全是 float 的列表里。前端 `toFixed(2)` 在某些实现上会抛，
      `pct` 那种"字符串减字符串"会变成 `"62.03" - "59.65" = 2.38` 的巧合
      或者 `NaN`，而且 `json.dumps` 照过（字符串是合法 JSON）⇒ 不报错。
    """
    price = _base.num(price)
    open_ = _base.num(open_)
    tss = None
    if ts_ms:
        try:
            tss = dt.datetime.fromtimestamp(int(ts_ms) / 1000).strftime('%Y-%m-%d %H:%M')
        except (TypeError, ValueError, OSError):
            tss = None
    # ★`pct` 自己算：OKX 的 tickers 里**没有**现成的 24h 涨跌幅字段，
    #   Binance 有 `priceChangePercent` 但走的是另一套字段名。用
    #   `(last-open24h)/open24h` 一个口径对两家，且与其它市场的 `pct` 同定义。
    pct = None
    if price is not None and open_:
        pct = _base.num((price - open_) / open_ * 100.0)
    return {'code': code, 'name': code, 'market': 'crypto', 'price': price, 'open': open_,
            'high': _base.num(high), 'low': _base.num(low), 'prev_close': open_, 'pct': pct,
            'volume': _base.num(vol), 'amount': _base.num(amt), 'turnover': None,
            'pe': None, 'ts': tss}


# ── 4. 指定标的快照（可跨市场）─────────────────────────────────────────

def snapshot(codes):
    """指定标的的实时快照。`codes` 可跨市场，写法见 `_split_code`。

    ★用**腾讯**：它一个请求能混装 `sh600519,hk00700,usAAPL`（实测），
      比按市场分三次打少两趟。失败再走新浪（也是混装，但三套布局）。
    ★**按代码对齐，绝不按位置** —— 两家上游对不存在的代码都是**静默缺条**，
      按位置取会把 A 的价配到 B 的名上，而这种错不报错、只显示别人的涨跌幅。
      取不到的代码**出现在返回里、字段全 None**，让上层能分清
      "这个码不存在"和"我漏了它"。
    ★★**桩也守这一条**（2026-09-22 修）：走 `stub.snapshot_frame(pairs)`，
      请求里每个代码**要么拿到它自己那一行、要么如实进 missing**。
      修之前桩给的是 `realtime_frame()` 造的一批通用代码，滤空后回落成
      "5 行 A股" —— 一个都不是请求的、`market` 全是 `cn`、还不报错。
    """
    pairs = [_split_code(c) for c in (codes or [])]
    if not pairs:
        return []
    if _base.stub_active():
        rows, missing = _stub().snapshot_frame(pairs)
        return _jclean(_pad_missing(rows, pairs, missing))
    key = '|'.join('%s:%s' % p for p in sorted(pairs))
    return _base.cached('snapshot', key, TTL['snapshot'],
                        lambda: _jclean(_snapshot_uncached(pairs)))


def _snapshot_uncached(pairs):
    from . import tencent, sina                 # noqa: PLC0415
    errs = []
    try:
        rows, missing = _base.timed(
            'tencent.rt.batch',
            lambda: tencent.realtime_batch([tencent.tcode(m, c) for m, c in pairs]),
            timeout=30, label='tencent.rt', count=lambda r: len(r[0]))
        if rows:
            if missing:
                _base.note('tencent.rt.batch', True, 0, len(rows),
                           '上游没回这些代码：%s' % ','.join(missing[:12]))
            return _pad_missing(rows, pairs, missing)
    except Exception as e:                       # noqa: BLE001
        errs.append('tencent: %s' % str(e)[:90])
    try:
        rows, missing = _base.timed(
            'sina.rt.batch',
            lambda: sina.realtime_batch([sina.scode(m, c) for m, c in pairs]),
            timeout=30, label='sina.rt', count=lambda r: len(r[0]))
        if rows:
            return _pad_missing(rows, pairs, missing)
    except Exception as e:                       # noqa: BLE001
        errs.append('sina: %s' % str(e)[:90])
    raise SourceError('ALL_SOURCES_FAILED', '快照所有源都失败 —— %s' % '；'.join(errs))


def _pad_missing(rows, pairs, missing):
    """上游没回的代码 → 补一行**全 None**，让"缺"这件事在返回里看得见。"""
    got = {str(r.get('code') or '').lstrip('0') or '0' for r in rows}
    out = list(rows)
    for m, c in pairs:
        if c.lstrip('0') and c.lstrip('0') not in got:
            out.append({'code': c, 'name': None, 'market': m, 'price': None, 'open': None,
                        'high': None, 'low': None, 'prev_close': None, 'pct': None,
                        'volume': None, 'amount': None, 'turnover': None, 'pe': None,
                        'ts': None})
    return out


# ── 5. 板块 ────────────────────────────────────────────────────────────

def boards(kind='concept'):
    """板块清单 → `list[dict]`，字段见 `_BOARD_FIELDS`。

    ★实测数量**对不上**，这不是 bug 而是口径不同，如实交出去：
      概念：腾讯 **803** vs 同花顺 **361**；行业：腾讯 **31** vs 同花顺 **90**。
      腾讯在前（它有涨跌幅/成交额/量比/领涨股），同花顺兜底（只有 code+name）。
      同花顺那条的 `pct`/`amount`/`vol_ratio`/`lead_*` 一律 **None** ——
      页面里那 10 行涨跌幅是"驱动事件"表的、不是板块的，拿它顶替就是编数。
    """
    k = str(kind or 'concept').strip().lower()
    if k not in ('concept', 'industry'):
        raise SourceError('BAD_KIND', '板块类型只能是 concept/industry，收到 %r' % kind)

    def build():
        if _base.stub_active():
            return _jclean(_stub().boards_frame(k))
        return _jclean(_boards_uncached(k))
    return _base.cached('boards', k, TTL['boards'], build)


def _boards_uncached(k):
    from . import tencent, ths, adata_src        # noqa: PLC0415
    att = []

    def _tx():
        return tencent.board_list_all(k)
    att.append(('tencent.boards.%s' % k, _tx, 60))

    def _th():
        rows, _meta = ths.board_list(k)
        now = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
        return [{'code': r['code'], 'name': r['name'], 'kind': k, 'pct': None,
                 'amount': None, 'vol_ratio': None, 'lead_code': None,
                 'lead_name': None, 'ts': now} for r in rows]
    att.append(('ths.boards.%s' % k, _th, 25))

    if k == 'concept':
        def _ad():
            now = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
            return [{'code': r['code'], 'name': r['name'], 'kind': k, 'pct': None,
                     'amount': None, 'vol_ratio': None, 'lead_code': None,
                     'lead_name': None, 'ts': now}
                    for r in adata_src.concept_list_ths()]
        att.append(('adata.boards.concept', _ad, 75))
    return _chain(att, 'boards/%s' % k, want=1)


def board_members(board_code, kind='concept'):
    """板块成分股 → `list[str]`（6 位代码）。

    ★三条路的**完整性不一样**，这点必须让上层看见：
      腾讯   翻页到 `total` ⇒ **完整**（实测 122 只那份是真全的）
      同花顺 每页只给 10 个、翻页要 cookie ⇒ **只有 10 个**（`ths.board_members`
             自己就带 `complete=False`）
      adata  经同花顺 ⇒ 完整但慢
      所以链的顺序是有意的：腾讯 → 同花顺 → adata。

    ★★**本函数的 `board_code` 只认腾讯码（`pt...`）/ 同花顺码，不认东财码（`BKxxxx`）。**
      而 `stock_boards()` 返回的**恰好是东财码** —— 于是最自然的那条往返
      「这只股票在哪些板块 → 看那个板块的成分股」**是断的**。本机实测（2026-09-22）：

        board_members('pt01801120')  → OK n=122   （腾讯码，通）
        board_members('pt02GN2170')  → OK n=65
        board_members('BK0438')      → ✗ 三个源全失败
        board_members('BK0153')      → ✗ 三个源全失败

      原因是**两个上游的板块码空间不同**，不是我这条链写坏了：腾讯按 `pt...` 编，
      东财/同花顺按 `BK...`（同花顺自己又用 `gn/...`）。三条支路各自拿东财码去查
      腾讯/同花顺的接口，自然是空表。
      ⇒ 现在 `_members_uncached` 出口会**认出东财码并直说是码空间的问题**，
        而不是甩一句"三个源都失败了"让人去逐个重试。
      ★这条往返要真正打通，得让 `boards()` 与 `stock_boards()` 统一到一个码空间
        （或在两处之间做一次 名字→码 的解析）。**我没有替这个做决定** —— 见交付
        说明里"需要协调者改的集成点"：选哪个码空间是契约级的选择。
    """
    code = str(board_code).strip()
    k = str(kind or 'concept').strip().lower()

    def build():
        if _base.stub_active():
            return _stub().members_frame(code, k)
        return _members_uncached(code, k)
    return _base.cached('board_members', '%s|%s' % (k, code), TTL['board_members'], build)


def _members_uncached(code, k):
    from . import tencent, ths, adata_src        # noqa: PLC0415
    att = []

    def _tx():
        return tencent.board_members_all(code, k)
    att.append(('tencent.members', _tx, 60))

    def _th():
        rows, _m = ths.board_members(code, k)
        return rows
    att.append(('ths.members', _th, 25))
    if k == 'concept':
        def _ad():
            return adata_src.concept_members_ths(code)
        att.append(('adata.members', _ad, 60))
    try:
        out = _chain(att, 'board_members/%s' % code, want=1)
    except SourceError as e:
        # ★★码空间的指路：东财码（`BK` + 数字）喂进来时，三个源**必然**全空，
        #   "全失败"这句会把人带去逐个重试腾讯/同花顺，而真正的原因是**码不对口**。
        #   在这里把话说明白 —— 报错的位置正好是调用方最需要提示的那一刻。
        if _DM_CODE_RE.match(code):
            raise SourceError('BAD_CODE_SPACE',
                              '%s 是**东财板块码**，而本函数只认腾讯码（`pt...`）'
                              '或同花顺码 —— 两个上游的板块码空间不同，所以三条路必然全空，'
                              '不是接口坏了。★`stock_boards()` 返回的正是东财码，'
                              '所以"个股 → 它所属板块 → 成分股"这条往返当前是断的。'
                              '要成分股请改用 `boards(%r)` 拿到的腾讯码；'
                              '要打通这条往返得先统一码空间（见交付说明·集成点）。'
                              '（底下三个源的原话：%s）' % (code, k, str(e)[:200])) from e
        raise
    return sorted({str(x).strip().zfill(6) for x in out if str(x).strip()})


# ── 6. 个股所属板块 ────────────────────────────────────────────────────

def stock_boards(code):
    """个股属于哪些板块 → `[{code, name, kind}]`。

    ★只有 adata 一条路（`get_plate_east`，实测 198ms / 27 行）。
      腾讯/同花顺都没有"给代码、反查全部所属板块"的接口。
      ⇒ 这条**单点**：它挂了就没有替代，`health()` 里看得见。
    ★`kind` 归一：上游给的是**中文**（实测只有 `行业`/`概念`/`板块` 三种），
      这里把前两种翻成 `industry`/`concept`，**与 `boards(kind=…)` 的枚举对齐** ——
      否则调用方拿 `boards('concept')` 的 code 去比 `stock_boards()` 的 kind 会比不上。
      `板块`（地域板块）没有对应枚举，**原样保留中文**：硬塞一个 `region`
      是我自己编的词，编出来的词会让下游以为上游有这个口径。
    ★★**本函数返回的 `code` 是东财码（`BK0438`），而 `boards()` 返回的是腾讯码
      （`pt01801120`）—— 两个码空间不通用。** 所以拿这里出来的 code 去喂
      `board_members()` 会三个源全失败（实测 `BK0438`/`BK0153` 都是）。
      `board_members` 现在会**认出东财码并直说是码空间的问题**，不会让你去
      逐个重试上游。要成分股请用 `boards()` 的 code。详见 `board_members` 的
      docstring 与交付说明·集成点。
    """
    c = str(code).strip()

    def build():
        if _base.stub_active():
            k = _stub().boards_frame('concept')[:5]
            return [{'code': b['code'], 'name': b['name'], 'kind': b['kind']} for b in k]
        from . import adata_src                  # noqa: PLC0415
        rows = _chain([('adata.stock_boards', lambda: adata_src.stock_plates(c), 30)],
                      'stock_boards/%s' % c, want=1)
        kmap = {'行业': 'industry', '概念': 'concept'}
        return [{'code': r.get('code'), 'name': r.get('name'),
                 'kind': kmap.get(str(r.get('kind') or '').strip(),
                                  str(r.get('kind') or '').strip() or None)}
                for r in rows]
    return _base.cached('stock_boards', c, TTL['stock_boards'], build)


# ── 7. 资金流 ──────────────────────────────────────────────────────────

def capital_flow(code, period='daily'):
    """资金流 → DataFrame，**上游列名原样保留**（中英混排那套语义不能翻译），
    只加 `attrs['source']` / `attrs['unit']`。

    ⚠️ **`period='daily'` 在本机取不到**，会抛 `SourceError`。这不是没实现，
       是三条路都实测死了：adata 走东财（0 行）、腾讯 `FundflowController` 各路径
       404、同花顺资金流接口 401。**不返回空表** ——
       空表会让"没有资金流数据"和"这个源挂了"看起来一样。
    `period='min'` 能取（adata，240 行），列名逐字：
    `stock_code, trade_time, main_net_inflow, sm_net_inflow, mid_net_inflow,
     lg_net_inflow, max_net_inflow`。
    """
    c = str(code).strip()
    p = str(period or 'daily').strip().lower()
    if p not in ('daily', 'min'):
        raise SourceError('BAD_PERIOD', 'period 只能是 daily/min，收到 %r' % period)

    def build():
        if _base.stub_active():
            return _stub_capital_flow(c, p)
        from . import adata_src                  # noqa: PLC0415
        if p == 'min':
            return _chain([('adata.capital_flow.min',
                            lambda: adata_src.capital_flow_min(c), 35)],
                          'capital_flow/%s/min' % c, want=1)
        return _chain([('adata.capital_flow.daily',
                        lambda: adata_src.capital_flow_daily(c), 30)],
                      'capital_flow/%s/daily' % c, want=1)
    return _base.cached('capital_flow', '%s|%s' % (c, p), TTL['capital_flow'], build)


def _stub_capital_flow(code, period):
    """资金流的桩。★**列名照抄上游**（含中文语义那几列），这样"列名被改掉"
    这件事在离线判据下也能被打红。

    ★★`ts` 那一课在这里**又犯了一次**，所以这里也要写清楚：`min` 那一支原本是
      `dt.datetime.now().replace(second=0, ...)` —— **分钟粒度**，于是同一份输入
      跨了分钟就给出两条不同的时间轴。这和 `stub.boards_frame` 是**同一个洞、
      不同文件**（那个是 `stub.py` 里的分钟粒度，这个是本文件里的）。
      ⇒ 一律走 `stub._asof(sp)` 那一个真源：默认"今天 + 固定钟点"（日粒度，与
      `feed._stub_frame` 同纪律），夹具给了 `asof` 就完全钉死。
      ★判别法记一笔：**看到 `now()` 就问一句"它是不是 `ts`"** —— 这个仓库里
      凡是"现在几点"进了返回值，同参同帧那条就断了。
    """
    import random                                # noqa: PLC0415
    import zlib                                  # noqa: PLC0415
    sp = _stub().spec()
    rnd = random.Random(int(sp.get('seed', 7)) + zlib.crc32(
        ('cap|%s|%s' % (code, period)).encode('utf-8')))
    n = 240 if period == 'min' else 60
    if period == 'min':
        base = pd.Timestamp(_stub()._asof(sp)).floor('min')
        times = [(base - pd.Timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M')
                 for i in range(n)][::-1]
    else:
        times = [d.strftime('%Y-%m-%d')
                 for d in pd.bdate_range(end=dt.date.today(), periods=n)]
    df = pd.DataFrame({
        'stock_code': [code] * n, 'trade_time': times,
        'main_net_inflow': [round(rnd.uniform(-5e7, 5e7), 2) for _ in range(n)],
        'sm_net_inflow': [round(rnd.uniform(-2e7, 2e7), 2) for _ in range(n)],
        'mid_net_inflow': [round(rnd.uniform(-2e7, 2e7), 2) for _ in range(n)],
        'lg_net_inflow': [round(rnd.uniform(-3e7, 3e7), 2) for _ in range(n)],
        'max_net_inflow': [round(rnd.uniform(-3e7, 3e7), 2) for _ in range(n)],
    })
    df.attrs['source'] = 'STUB.capital_flow.%s' % period
    return df


# ── 8. 交易日历 ────────────────────────────────────────────────────────

def trade_calendar(days=400):
    """→ `['YYYY-MM-DD', ...]`（升序，**截止到"今天"**）。

    ★**akshare 在前、adata 在后**，顺序是有理由的：
      `tool_trade_date_hist_sina()` 实测 **8797 行 / 153ms**，
      `adata.trade_calendar()` 只有 **365 行 / 2.4s**。
      用 365 行的日历去算 5 年分位（`feed.PCTILE_WINDOW = 1250`）会**静默**
      变成"拿 1 年的样本算 5 年分位" —— 数字照出，含义变了。

    ★★**上游的日历含未来日期，这里把它们砍掉。** 实测（2026-09-22）
      `tool_trade_date_hist_sina()` 一直排到**年底**（12-31），所以
      `days=20` 取"最后 20 个"会拿到 **2026-12-04 起的 20 个未来交易日** ——
      一个纯未来的"最近 20 个交易日"。这不是单位错也不是排序错，
      是"日历"和"已发生的交易日"本来是两个概念，而上游给的是前者。
      本层按契约的用途（给分位数/波动率当窗口）取后者：
      **先过滤 `<= 今天`，再取尾 `days` 个**。
      要未来日期（排期用）得另开一个函数，不在这个签名里 ——
      在这个签名里混着未来日期，会让"窗口长度"这件事变得不可推理。
    """
    days = max(1, min(int(days or 400), 2000))
    today = dt.date.today().strftime('%Y-%m-%d')

    def build():
        if _base.stub_active():
            return _stub().calendar_frame(days)
        from . import adata_src, akshare_src     # noqa: PLC0415
        raw = list(_chain(
            [('akshare.trade_calendar', lambda: akshare_src.trade_calendar(days), 45),
             ('adata.trade_calendar', lambda: adata_src.trade_calendar(days), 45)],
            'trade_calendar', want=1))
        # ★砍未来日期（见 docstring）。砍在**出口**而不是砍在各源里：
        #   源只管"上游给了什么"，口径归一在这一层收口，否则两个源各砍一次，
        #   将来加第三个源就会漏。
        past = [d for d in raw if str(d) <= today]
        if not past:
            raise SourceError('EMPTY', '交易日历过滤掉未来日期后一行不剩（上游最早 %s）'
                              % (raw[0] if raw else '空'))
        return past[-days:]
    return _base.cached('calendar', str(days), TTL['calendar'], build)


# ── 9. 健康度 ──────────────────────────────────────────────────────────

def health():
    """`{"sources": [{id, ok, ms, n, err, ts}], "known_dead": {...}, "ts": ...}`。

    ★规格 §2.2 要求"必须如实反映东财不可达、不许静默"。所以这里返回两块：
      - `sources`：**本次进程内**每个源每次调用的真实结果（成功和失败都记）。
        一个源从没被调过，它就不在这里 —— 空不是"好"，是"没测过"。
      - `known_dead`：**静态的、带日期的实测结论**（`_KNOWN_DEAD`）。
        东财是结构性不可达，不会因为今天没人调它就变成"未知"。
    """
    h = _base.health()
    h['known_dead'] = dict(_KNOWN_DEAD)
    h['cache'] = len(_base._CACHE)
    return h


# ── 10. 给 `agent/feed.py` 的 SOURCES 用的适配器 ───────────────────────
# ★这个函数存在的唯一理由：`feed.to_canon()` 的 `dt` 别名表是
#   `['日期','date','time','datetime','candle_begin_time','ts']` —— **没有 `'dt'` 本身**。
#   所以本层输出的 CANON 帧（列名就叫 `dt`）直接喂 `to_canon` 会**认不出时间列**，
#   然后 `r['dt'] = 0.0` → `pd.to_datetime(0.0)` → NaT → 全被 dropna 掉 →
#   `SourceError: 清洗后没有有效行`。**实测确认过**（2026-09-22）。
#   把 `dt` 改名成 `日期` 就通了，而且**只改名字**，不动任何数值。
#
# ★`amount` 那一列 feed.py 会原样留着（它只在**缺列**时才填 0.0，本层给了列，
#   即使全是 NaN 也算"给了"）—— 这是有意为之：NaN 表示"腾讯不给这一列"，
#   让 `to_canon` 填成 0.0 就把"不知道"变成了"是零"。

def feed_frame(market, code, days=400, adjust='qfq'):
    """`feed.py: SOURCES` 那条链要的形态。失败抛 `feed.SourceError`（链会接住降级）。

    ★★**在这里把 NaN 翻成 0.0，是给 feed.py 补它的约定，不是本层改口径。**
      理由：本层对"上游不给成交额"用 **NaN**（腾讯/新浪 K 线就是没有这一列），
      而 `feed.to_canon` 对**缺列**填 `0.0`（见该文件 211 行）。问题是它只在
      **列不存在**时才填 —— 本层给了 `amount` 这一列（哪怕全是 NaN），
      于是 NaN 会**穿过** to_canon 进到 `tools.py` 的摘要里，而那里是
      `json.dumps(sm, ensure_ascii=False)`（**没带 `allow_nan=False`**）
      ⇒ 写出来是裸 `NaN` ⇒ 前端 `JSON.parse` 当场炸。
      所以跨过这条边界时按**对方的约定**落地：填 0.0。
      ★本层对外（`datasource.daily`）照样是 NaN —— 只有喂进 feed.py 这一条路填 0。
    """
    df = daily(market, code, days=days, adjust=adjust)
    out = df.rename(columns={'dt': '日期'})
    for col in ('volume', 'amount'):
        if col in out.columns and out[col].isna().any():
            out[col] = out[col].fillna(0.0)
    out.attrs.update(df.attrs)
    out.attrs['nan_filled_for_feed'] = True      # ★留痕：这帧里的 0.0 可能是"上游没给"
    return out
