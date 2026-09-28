"""腾讯行情源 —— **本机唯一同时覆盖国内+国外+实时的上游**，所以是主源。

## 为什么是腾讯（2026-09-22 全量实测后的结论，推翻了初版选型）

`docs/AGENT-SPEC.md` §2.1 那张可达性表是逐条打真实端点量出来的。摘要：

| 上游 | 结果 |
|---|---|
| 腾讯 `qt.gtimg.cn` / `web.ifzq.gtimg.cn` | ✅ 0.3~0.4s |
| 新浪 `hq.sinajs.cn` / `vip.stock.finance.sina.com.cn` | ✅ 0.2~0.3s |
| 同花顺 `d.10jqka.com.cn` | ✅ |
| **东财 API `push2*` 族** | ❌ `RemoteDisconnected`，**0.2s 就被断连** |
| 东财 `datacenter*.eastmoney.com` | ✅ **活的**（269ms 回真实 JSON）—— 分界线是主机族，不是"东财整站"，见 `adata_src.py` 模块头 |
| 百度股市通 `finance.pae.baidu.com` | ❌ 403（`isCaptchaEnabled: true`，风控） |
| 雪球 / Yahoo | ❌ 400 / 403 |

⇒ `efinance` 整个包是纯东财后端，**在本机完全不可用**，本包不 import 它。
⇒ adata 的 `get_market`（日K）也是纯东财 `push2his`，同样不可用（见 `adata_src.py` 的说明）。

## 实测事实（本机 Linux 6.8.0-138 / Python 3.10.12，2026-09-22）

### ① 实时批量 `https://qt.gtimg.cn/q=<code1>,<code2>,...`
- **GBK 编码**（不解码会把「贵州茅台」读成乱码，而乱码是合法 JSON —— 前端只会
  显示一串看不懂的字，不报错，属于"错了但没人知道"）。
- 一个请求混装 `sh600519,hk00700,usAAPL` **可以**，一次请求拿三个市场。
- **美股用裸代码**：`usAAPL` ✅。带后缀的 `usAAPL.OQ` 只在**返回的第 3 段**里出现，
  请求时也给 `usAAPL.OQ` 是通的，但裸码更省事且实测同样返回。
- 实测批量上限：60 码 → 回 47 行、200 码 → 177 行、600 码 → 513 行。
  **不存在的代码静默缺条** ⇒ 永远**按代码对齐**，绝不按位置取。
- 字段（`~` 分隔，0-based，**cn/hk/us 三套布局不同**，见下表）。

### ② K 线 `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=<code>,<period>,,,<n>,<fq>`
- `period` = `day` / `week` / `month`；`fq` = `qfq` / `hfq` / 空。
- ★**两级嵌套**：数组在 `data[<腾讯代码>][<key>]`，**不是** `data[<key>]`。
  少下一层的话拿到的是 `{'sh600519': {...}}`（值是 dict 不是 list），
  然后**静默当空表** —— 我第一次就这么写的，A股日线全空却看不出错在哪。
- **返回的 key 名随市场和复权变**：A股/ETF 前复权是 `qfqday`、港股/美股/指数是 `day`。
  按 key 猜会静默拿到空表，所以 `_pick_series()` 是"先按已知序找、再扫所有
  list-of-list 型的 key"。
- **行格式是 `[日期, 开, 收, 高, 低, 量]` —— 第 2 个是收盘不是最高**。
  这是腾讯的历史包袱（新浪与同花顺都是 `[日期,开,高,低,收,量]`，三家不一致）。
  按位置抄错的话，K 线里的 high/low 会互换 —— 图上"最高价低于收盘价"，
  但每一个数字单看都像模像样。**这是本源最容易做错的一处。**
  港股那根还可能多带第 7 个元素（一个分红/回购信息的 dict），所以只取前 6 个。
- ★★**美股必须用带后缀的代码**：实测 `usAAPL,day,,,320,` → 数组长度 **2**
  （一根 `2011-06-02`、一根今天），而 `usAAPL.OQ,day,,,320,` → **320 根**。
  裸码那 2 根**不报错、不空表，看起来就是正常数据** —— 直接画出来是一条
  "从 2011 年跳到今天"的直线。`_us_real_code()` 从回包的 `qt` 段里挖出真代码
  （`qt[<code>][2]` = `AAPL.OQ`）再重试一次，并且**不足 30 根一律报 `TOO_SHORT`**，
  宁可失败也不交一条画得像样的假线。
- ★★**实时端点与 K 线端点的美股代码形态正好相反**：
  实时要**裸码** `usAAPL`（带后缀回 `v_pv_none_match="1"`，见 ①），
  K 线要**带后缀** `usAAPL.OQ`。同一个市场、两个端点、两种写法，别互相套用。
- 深度：请求 400 → **实到 401**；800 → 801；**2000 → 被服务端截到 641**。
  ⇒ `days > 640` 时腾讯给不满，链上会让新浪 next（新浪 1023 根，更深）。
- **没有成交额列**。`amount` 一律置 **NaN**，并在 `attrs['amount_missing']=True`
  上写清楚。★**这里与 `agent/feed.py: to_canon` 的做法不同**：feed.py 对缺列
  是 `r[canon] = 0.0`（见该文件 211 行的注释"predictor 自己会补"），本层故意
  **不跟**——0 是"成交额为零"，NaN 是"上游不给这一列"，两者在图上没区别，
  但在"这个数字能不能用"这个判断上是相反的答案。要 0 的调用方自己 `fillna(0)`。
  （这是一处**已知的口径分歧**，写在交付说明里了。）

### ③ 单位（★三个市场三套，实测对照新浪/自己算过）
| 市场 | `volume` | `amount` |
|---|---|---|
| A股/ETF/指数 | **手**（×100 = 股） | 万元（×10000 = 元） |
| 港股 | 股 | 元 |
| 美股 | 股 | 元 |
本模块**统一归一到「股 / 元」**再交出去（与 `feed.py` 走新浪时的口径一致）。
对照数据：sh600519 2026-09-22 腾讯 `24573` 手、新浪 `2457294` 股、成交额
腾讯 `308853` 万 = 新浪 `3088526148` 元。

### ④ 板块 `https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank`
- `board_type=gn`（概念）**total=803**；`board_type=hy`（行业）**total=31**。
- `count` **上限 100**：实测 count=100 → 100 行；count=500/1000/2000 → **0 行、total=None**
  （不是报错，是静默空。所以分页只能按 100 走）。
- 每条字段（逐字，照抄回包）：`code`(pt02GN2170) `name` `zdf`(涨跌幅%) `turnover`(成交额)
  `lb`(量比) `hsl`(换手%) `ltsz`(流通市值) `zsz`(总市值) `speed` `stock_type`(BK-GN-FG/BK-HY-1)
  `volume` `zd` `zdf_d5/d20/d60/w52/y` `zljlr`(主力净流入) `zllr` `zllc`
  **`lzg{code,name,zd,zdf,zxj}`(领涨股)**。
  ★★领涨股那个键是 **`lzg`**（领涨股），不是 `zlg`。我第一版按 `zlg` 读，
  于是 803 条板块的 `lead_code`/`lead_name` **一条不落地全是 None** ——
  不报错、字段也在、就是永远是空的，看着像"上游没给这个字段"。
  这种"名字打错一个字母 → 整列恒空"的错，只有拿原始回包逐字对才能发现。
- ★`turnover` 的单位是 **万元**（不是元）。实测校验法：把 31 个行业的 `turnover`
  求和得 `212691139`，同日全市场 5566 只个股的成交额之和是 `2153408411629` 元，
  比值 **0.9877** ⇒ 乘 10000 才对齐（差 1.2% 是行业口径覆盖度，不是单位错）。
  本模块把它乘 10000 变成**元**再交给契约（与本层其它地方的口径一致）。
- `volume` 实测是**手**（同样按上面那个比值对得上），但契约的 `boards()` 没有
  `volume` 字段，所以这里不用它，也就不做换算 —— **不用就不换算**，
  免得引入一个没人校过的数。

### ⑤ 板块成分股 `.../cgi/cgi-bin/rank/hs/getBoardRankList?board_code=<pt...>`
- 实测 `pt01801120`（食品饮料）→ total=**122**；`count` 上限同样是 **100**。
- 每条字段：`code`(带 sh/sz 前缀) `name` `zxj` `zdf` `lb` `hsl` `pe_ttm` `zsz` `ltsz` `pn`。

### ⑥ 分钟线 —— **A股是坏的；港股/美股的上游只回一根聚合日线**
- A股：`fqkline` 的 `m5/m15/m30` 一律返回 `{"code":1,"msg":"bad params"}`
  （实测 sh600519，`param=sh600519,m5,,,320,qfq`）。
- 换 `appstock/app/kline/mkline`：**301 跳 `web3.ifzq.gtimg.cn`，该主机 DNS 不解析**
  （实测 `socket.gethostbyname('web3.ifzq.gtimg.cn')` → `Name or service not known`）。
- 港股/美股：`fqkline` 的 `m5` **HTTP 200、`code=0`，但 `m5` 数组长度恒为 1**，
  且那一根的时间戳是**日期**不是分钟（`["2026-09-22","442.800","451.600","463.400","442.000","70723438.0"]`
  = 当天一整天的聚合）。count 给 1/100/1000/3200 实测都是 1 根 ⇒ **不是参数问题，
  是这个端点不提供分钟序列**。`m1/m15/m30/m60` 同样各 1 根。
  ⇒ **本包不从腾讯取分钟线**（A股走新浪 `scale=`，港美股分钟上游都没有，见 `sina.py`）。
"""

import datetime as dt

import pandas as pd

from . import _base

BASE_RT = 'https://qt.gtimg.cn/q='
BASE_KLINE = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
BASE_RANK = 'https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank'
BASE_BOARD_MEMBERS = 'https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList'

HEADERS = {'Referer': 'https://gu.qq.com/'}

SRC = 'tencent'

# 腾讯一次批量最多塞多少个代码。实测 600 码回 513 行（丢的都是不存在的），
# 但 URL 会到 5400 字符 —— 保守取 60，和 spec §2.2 记的"一次 60 码"一致。
BATCH = 60
# 板块榜/成分股的 count 硬上限，实测 100 以上静默回空表
RANK_PAGE = 100


def tcode(market, code, for_kline=False):
    """canonical 代码 → 腾讯代码。

    A股前缀规则（★`920xxx` 是**北交所**不是沪市 —— 按首位字符 `9→sh` 判会把它
    错送进沪市，然后静默拿不到数据；实测北交所 `bj920000` 在新浪列表里就是 bj 前缀）：
      43/83/87/88/92 → bj     900 → sh(B股)      6/9 → sh
      15/16/18 → sz(深 ETF)   50/51/52/56/58 → sh(沪 ETF)
      0/1/2/3 → sz            000xxx 指数 → sh   399xxx 指数 → sz

    `for_kline=True` 时返回 fqkline 用的形态；两者对腾讯其实一样，
    参数留着是为了将来万一腾讯给指数/K线换了前缀时有地方改。
    """
    code = str(code).strip()
    if market in ('hk_stock', 'hk'):
        return 'hk' + code.zfill(5)
    if market in ('us_stock', 'us'):
        return 'us' + code.upper()
    if market in ('cn_index',):
        return ('sh' if code.startswith(('000', '88', '99')) else 'sz') + code
    # A股个股 / ETF
    if code.startswith(('43', '83', '87', '88', '92')):
        return 'bj' + code
    if code.startswith('900'):
        return 'sh' + code
    if code.startswith(('15', '16', '18')):
        return 'sz' + code
    if code.startswith(('50', '51', '52', '56', '58')):
        return 'sh' + code
    if code[:1] in ('6', '9'):
        return 'sh' + code
    return 'sz' + code


def from_tcode(tc):
    """腾讯代码 → (market, code)。用于按代码对齐（**不许按位置取**）。"""
    tc = str(tc)
    if tc.startswith('hk'):
        return 'hk_stock', tc[2:]
    if tc.startswith('us'):
        return 'us_stock', tc[2:]
    if tc.startswith('bj'):
        return 'cn_stock', tc[2:]
    if tc.startswith(('sh', 'sz')):
        return 'cn_stock', tc[2:]
    return 'cn_stock', tc


# ── 实时 ───────────────────────────────────────────────────────────────

def _ts(txt):
    """把三个市场三套时间格式归一成 `YYYY-MM-DD HH:MM`。
    A股 `20260922161442`、港股 `2026/09/22 16:08:18`、美股 `2026-09-21 16:00:01`。"""
    s = str(txt or '').strip()
    if len(s) == 14 and s.isdigit():                       # A股
        return '%s-%s-%s %s:%s' % (s[0:4], s[4:6], s[6:8], s[8:10], s[10:12])
    s = s.replace('/', '-')
    return s[:16] or None


def parse_rt_line(line):
    """一条 `v_sh600519="..."` → 契约字段的 dict。解析不了就返回 None。

    ★三套布局差异只在这一处收口：
      - `[38]` 换手率：A股有值；**港股恒为 0**（上游没算）⇒ 归 None；
        美股有值（AAPL 0.24%）。
      - `[46]`：A股是市净率、港美股是**公司英文名** ⇒ 不映射，免得把
        `Apple Inc.` 塞进一个叫 pb 的字段里。
      - `[6]` 成交量：A股是**手**，港美股是股 ⇒ A股 ×100。
    """
    if '="' not in line:
        return None
    try:
        tc = line.split('=')[0].strip().split('_')[-1]     # v_sh600519 → sh600519
        body = line.split('="', 1)[1].rstrip('";\n')
        f = body.split('~')
        if len(f) < 40:
            return None
        market, code = from_tcode(tc)
        is_cn = market.startswith('cn')
        price, prev, open_ = _base.safe_float(f[3]), _base.safe_float(f[4]), _base.safe_float(f[5])
        vol = _base.safe_float(f[6])
        amt = _base.safe_float(f[37]) if len(f) > 37 else None
        turnover = _base.safe_float(f[38]) if len(f) > 38 else None
        pe = _base.pe(f[39]) if len(f) > 39 else None
        if is_cn:
            if vol is not None:
                vol = vol * 100                            # 手 → 股
            if amt is not None:
                amt = amt * 10000                          # 万元 → 元
        elif market == 'hk_stock' and turnover == 0:
            turnover = None                                # 港股上游不填，0 不是真值
        return {
            'code': code, 'name': _base._num(f[1]) or str(f[1]).strip() or None,
            'market': market, 'price': price, 'open': open_,
            'high': _base.safe_float(f[33]) if len(f) > 33 else None,
            'low': _base.safe_float(f[34]) if len(f) > 34 else None,
            'prev_close': prev,
            'pct': _base.safe_float(f[32]) if len(f) > 32 else None,
            'volume': vol, 'amount': amt, 'turnover': turnover, 'pe': pe,
            'ts': _ts(f[30] if len(f) > 30 else None),
        }
    except (IndexError, ValueError, AttributeError):
        return None


def realtime_batch(tcodes, timeout=None):
    """批量实时。返回 `(rows, missing)` —— `missing` 是**请求了但没回来**的腾讯代码。

    ★`missing` 必须往上报：腾讯对不存在的代码**静默缺条**（实测 60 码只回 47 行），
      不报的话"这个代码没数据"和"这个代码今天停牌"在调用方眼里长得一模一样。
    """
    out, missing = [], []
    for i in range(0, len(tcodes), BATCH):
        chunk = list(tcodes[i:i + BATCH])
        txt = _base.http_get(BASE_RT + ','.join(chunk), headers=HEADERS,
                             encoding='gbk', timeout=timeout or _base.DEFAULT_TIMEOUT,
                             label='tencent.rt')
        got = set()
        for line in txt.split(';'):
            row = parse_rt_line(line)
            if row:
                out.append(row)
                got.add(tcode(row['market'], row['code']))
        missing.extend(t for t in chunk if t not in got)
    return out, missing


# ── K 线 ───────────────────────────────────────────────────────────────

_KEY_ORDER = ('qfqday', 'day', 'hfqday', 'qfqweek', 'week', 'qfqmonth', 'month')


def _pick_series(d):
    """从 `data[code]` 里挑出真正那根 K 线数组。

    ★**两级嵌套**：真正的数组在 `data[<腾讯代码>][<key>]`，不是 `data[<key>]`。
      少下一层的话拿到的是 `{'sh600519': {...}}` 这种"值是 dict 不是 list"的东西，
      然后**静默当空表** —— 我第一次就是这么写的，A股日线全空却看不出哪里错。
    ★不能写死 key：A股/ETF 前复权叫 `qfqday`，港股/美股/指数叫 `day`
      （实测当日）。写死一个的话另一个市场会**静默拿到空表**。
      先按已知优先序找，再退化成"扫所有 list-of-list 型的 key"。
    """
    data = d.get('data') or {}
    if not isinstance(data, dict):
        return None, None, None
    tc = next((k for k, v in data.items() if isinstance(v, dict)), None)
    inner = data.get(tc) if tc else None
    if not isinstance(inner, dict):
        # 有些指数/特殊标的直接把数组挂在 data 下（没有第二层）
        inner, tc = data, tc
    for k in _KEY_ORDER:
        v = inner.get(k)
        if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
            return k, v, inner.get('qt')
    for k, v in inner.items():
        if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
            return k, v, inner.get('qt')
    return None, None, inner.get('qt')


def _us_real_code(qt, tc):
    """从回包的 `qt` 段里挖出**带后缀**的美股真代码（`AAPL.OQ`）。

    ★为什么非要它：腾讯的 K 线端点对美股**只认带后缀的代码**。
      实测 `usAAPL,day,,,320,` → 数组长度 **2**（一根 2011-06-02、一根今天），
      而 `usAAPL.OQ,day,,,320,` → **320 根**。
      裸码那 2 根**看起来像正常数据**，不报错、不空表 —— 直接画出来就是一条
      "从 2011 年直接跳到今天"的直线。**这是本源最阴的一处。**
    ★顺带记住：**实时端点正好相反** —— `qt.gtimg.cn/q=usAAPL` 要裸码，
      带后缀的回 `v_pv_none_match="1"`（见模块头 ① 和 `parse_rt_line`）。
      同一个市场、两个端点、两种代码形态，别互相套用。
    """
    if not isinstance(qt, dict):
        return None
    for v in qt.values():
        if isinstance(v, (list, tuple)) and len(v) > 2:
            s = str(v[2]).strip()
            if '.' in s:                         # `AAPL.OQ` / `BABA.N`
                return s
    return None


def _kline_raw(tc, period, n, fq, timeout=None):
    """打一次 fqkline，返回 `(key, series, qt)`。★`series` 可能是 None（空表），
    由调用方决定这算失败还是"再换一种代码形态试一次"。"""
    txt = _base.http_get(BASE_KLINE, params={'param': '%s,%s,,,%d,%s' % (tc, period, n, fq)},
                         headers=HEADERS, timeout=timeout or _base.DEFAULT_TIMEOUT,
                         label='tencent.kline.%s' % tc)
    j = _base.json_or_text(txt, 'tencent.kline.%s' % tc)
    if j.get('code') not in (0, '0'):
        raise _base.SourceError('TENCENT_KLINE', '%s 取 K 线失败：code=%s msg=%s'
                                % (tc, j.get('code'), j.get('msg')))
    return _pick_series(j)


def kline(market, code, days=400, adjust='qfq', period='day', timeout=None):
    """日/周/月 K。返回 CANON 的 DataFrame。

    ★列序按**名字**映射：腾讯是 `[日期,开,收,高,低,量]`（收盘在第 2 位！），
      新浪是 `[day,open,high,low,close,volume]`。两边都按名字取，
      任何一边调序都不会静默错位。
    ★美股会自动补一次后缀重试（见 `_us_real_code`）。
    """
    tc = tcode(market, code, for_kline=True)
    fq = {'qfq': 'qfq', 'hfq': 'hfq', 'none': '', '': '', None: ''}.get(adjust, 'qfq')
    n = max(10, min(int(days or 400) + 1, 700))         # 实测 >640 会被服务端截断
    key, series, qt = _kline_raw(tc, period, n, fq, timeout)
    # ★美股：裸码只回 2 根 ⇒ 用回包里的真代码（带 .OQ/.N）重试一次
    if market in ('us_stock', 'us') and len(series or []) < 30 and '.' not in tc:
        real = _us_real_code(qt, tc)
        if real and real != tc:
            key2, series2, _ = _kline_raw('us' + real, period, n, fq, timeout)
            if len(series2 or []) > len(series or []):
                tc, key, series = 'us' + real, key2, series2
    if not series:
        raise _base.SourceError('EMPTY', '%s 的 K 线数组是空的' % tc)
    if len(series) < 30:
        # ★宁可报错也不交一条 2 根还画得像样的线（见 `_us_real_code`）
        raise _base.SourceError('TOO_SHORT', '%s 只回了 %d 根 K 线（腾讯对美股裸码就是这样）'
                                % (tc, len(series)))
    rows = []
    for r in series:
        if not isinstance(r, (list, tuple)) or len(r) < 6:
            continue
        rows.append({'dt': r[0], 'open': r[1], 'close': r[2], 'high': r[3],
                     'low': r[4], 'volume': r[5], 'amount': None})
    df = pd.DataFrame(rows)
    df['dt'] = pd.to_datetime(df['dt'], errors='coerce')
    for c in ('open', 'high', 'low', 'close', 'volume'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['dt', 'open', 'high', 'low', 'close']).sort_values('dt')
    if not len(df):
        raise _base.SourceError('EMPTY', '%s 的 K 线清洗后没有有效行' % tc)
    # ★单位归一：A股/ETF/指数是「手」，港美股是「股」。与新浪口径一致。
    if market.startswith('cn'):
        df['volume'] = df['volume'] * 100
    df['amount'] = float('nan')                  # ★NaN 不是 0：上游不给这一列
    df = df[list(_base.CANON)].reset_index(drop=True)
    df.attrs['source'] = '%s.kline.%s' % (SRC, key)
    df.attrs['amount_missing'] = True            # 理由见模块头 ② 最后一条
    return df


# ── 板块 ───────────────────────────────────────────────────────────────

def board_list(kind='concept', offset=0, count=RANK_PAGE, timeout=None):
    """板块榜。返回 `(rows, total)`；`total` 是上游自报的总数（用来翻页）。

    实测 total：概念 `gn`=803、行业 `hy`=31。
    ★`count > 100` 上游**静默回空表**（不是报错），所以硬夹到 100。
    """
    bt = 'gn' if kind == 'concept' else 'hy'
    txt = _base.http_get(BASE_RANK,
                         params={'board_type': bt, 'sort_type': 'price', 'direct': 'down',
                                 'offset': int(offset), 'count': min(int(count), RANK_PAGE)},
                         headers=HEADERS, timeout=timeout or _base.DEFAULT_TIMEOUT,
                         label='tencent.board.%s' % bt)
    j = _base.json_or_text(txt, 'tencent.board.%s' % bt)
    d = j.get('data') or {}
    if not isinstance(d, dict):
        raise _base.SourceError('EMPTY', '腾讯板块榜返回空 data（total=%s）' % d)
    return (d.get('rank_list') or []), _base.safe_int(d.get('total'))


def board_members(board_code, kind='concept', offset=0, count=RANK_PAGE, timeout=None):
    """板块成分股。返回 `(rows, total)`。`count` 上限同样 100。"""
    txt = _base.http_get(BASE_BOARD_MEMBERS,
                         params={'board_code': board_code, 'sort_type': 'price',
                                 'direct': 'down', 'offset': int(offset),
                                 'count': min(int(count), RANK_PAGE)},
                         headers=HEADERS, timeout=timeout or _base.DEFAULT_TIMEOUT,
                         label='tencent.members.%s' % board_code)
    j = _base.json_or_text(txt, 'tencent.members.%s' % board_code)
    d = j.get('data') or {}
    if not isinstance(d, dict):
        raise _base.SourceError('EMPTY', '腾讯成分股返回空 data：%s' % str(j)[:120])
    return (d.get('rank_list') or []), _base.safe_int(d.get('total'))


def _row_board(r, kind, now):
    """腾讯板块榜一行 → 契约字段。★字段名按**实测**对应，不是猜的：
    `zdf`=涨跌幅 `turnover`=成交额(**万元，这里 ×10000 转元**) `lb`=量比
    **`lzg`**=领涨股（内含 code/name）。

    ★缺值是 None 不是 0：`lb` 在上游偶尔给 0（没成交时量比算不出来），
      量比 0 是个**不可能的真值**，所以走 `safe_float` 后单独判 `lb == 0`。
    """
    # ★★键名是 `lzg` 不是 `zlg`（见模块头 ④）。写错的表现是**整列恒 None**。
    lzg = r.get('lzg') or {}
    if not isinstance(lzg, dict):
        lzg = {}
    lb = _base.safe_float(r.get('lb'))
    amt = _base.safe_float(r.get('turnover'))
    return {'code': str(r.get('code') or '').strip() or None,
            'name': str(r.get('name') or '').strip() or None,
            'kind': kind,
            'pct': _base.safe_float(r.get('zdf')),
            # 腾讯板块的 turnover 是**万元**，契约的 amount 是**元**（见模块头 ④）
            'amount': None if amt is None else amt * 10000.0,
            'vol_ratio': None if lb == 0 else lb,
            'lead_code': str(lzg.get('code') or '').strip() or None,
            'lead_name': str(lzg.get('name') or '').strip() or None,
            'ts': now}


def board_list_all(kind='concept', timeout=None):
    """板块清单**全部页** → `[{code,name,kind,pct,amount,vol_ratio,lead_code,lead_name,ts}]`。

    ★必须翻页：`count` 上限实测 100，而概念有 **803** 个 ⇒ 9 页。
      `count=500/1000/2000` 实测**静默回 0 行 + total=None**（不报错！），
      所以"一次拿完"这条路是封死的，只能按 100 翻。
    ★翻页用 `offset`，且**每页都重新读 total** —— 上游的 total 会随行情变
      （板块会新增/合并），拿第一页的 total 去算页数会在中途漏掉尾巴。
    """
    now = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
    out, offset, seen = [], 0, set()
    for _ in range(30):                          # 硬上限，防 total 撒谎时死循环
        rows, total = board_list(kind, offset=offset, count=RANK_PAGE, timeout=timeout)
        if not rows:
            break
        for r in rows:
            b = _row_board(r, kind, now)
            if b['code'] and b['code'] not in seen:
                seen.add(b['code'])
                out.append(b)
        offset += len(rows)
        if total is not None and offset >= total:
            break
        if len(rows) < RANK_PAGE:
            break
    if not out:
        raise _base.SourceError('EMPTY', '腾讯 %s 板块榜一页都没取到' % kind)
    return out


def board_members_all(board_code, kind='concept', timeout=None):
    """板块成分股**全部页** → `[code, ...]`（6 位）。

    ★同样必须翻页（上限 100/页）。实测 `pt01801120` total=122 ⇒ 2 页。
    ★成分股列表按**代码**去重 —— 翻页时上游会因行情变动把某只票挪到下一页，
      不去重会出现重复代码，下游按代码聚合时同一只票会被算两次。
    """
    out, offset, seen = [], 0, set()
    for _ in range(40):
        rows, total = board_members(board_code, kind=kind, offset=offset,
                                    count=RANK_PAGE, timeout=timeout)
        if not rows:
            break
        for r in rows:
            c = str(r.get('code') or '').strip()
            c = c[2:] if c[:2] in ('sh', 'sz', 'bj') else c   # 上游给的是带前缀的
            c = c.zfill(6) if c.isdigit() else ''
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        offset += len(rows)
        if total is not None and offset >= total:
            break
        if len(rows) < RANK_PAGE:
            break
    if not out:
        raise _base.SourceError('EMPTY', '腾讯成分股 %s 一页都没取到' % board_code)
    return sorted(out)
