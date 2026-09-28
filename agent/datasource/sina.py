"""新浪行情源 —— **A股分钟线在本机的唯一来源**，兼实时与全市场名单的副源。

## 实测事实（本机 Linux 6.8.0-138 / Python 3.10.12，2026-09-22）

### ① 分钟线 —— `CN_MarketData.getKLineData`（★A股分钟只能走这里）
    https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
        ?symbol=sh600519&scale=<5|15|30|60|240>&ma=no&datalen=<N>
- **`scale` 直接给 5/15/30/60 分钟，不用自己从 1 分钟重采样。**
  实测 scale=5 → 0.28s、15 → 0.29s、30 → 0.22s、60 → 0.21s、240(=日K) → 0.27s。
- 返回列**逐字**：`{"day":"2026-09-22 14:40:00","open":"1254.020","high":"1255.880",
  "low":"1254.000","close":"1255.840","volume":"54600"}` —— `day` 是**字符串**
  且分钟线带时分秒、日线只有日期。**没有成交额列。**
- 深度：`datalen=1023` 实测给到 1023 根（比腾讯深，腾讯 2000 请求被截到 641）。
- **`scale=1` 返回 `null`**（实测）⇒ A股 1 分钟在这里取不到。
- ★**港美股不在这里**：`symbol=hk00700` / `symbol=AAPL`、以及 `quotes.sina.cn` 上
  同名的 `/hk/`、`/us/` 路径，实测全部回 `{"__ERROR":3,"__ERRORMSG":"Service not valid"}`
  （`vip` 路径回 `null`）。港美股分钟在本机**没有可用来源**（腾讯 `fqkline` 的
  `m5` 只回一根聚合日线，见 `tencent.py` 模块头第 ⑥ 条）。

### ② 实时 `https://hq.sinajs.cn/list=sh600519,rt_hk00700,gb_aapl`
- **需要 `Referer`**（不带的话回 403），**GBK 编码**。
- 三套布局（★位置完全不同，见下面三个 parse 函数）：
  - A股 `hq_str_sh600519`：
    `[0]名 [1]开 [2]昨收 [3]现价 [4]高 [5]低 [6]买一 [7]卖一 [8]量(股) [9]额(元) [30]日期 [31]时间`
  - 港股 `hq_str_rt_hk00700`（★**没有 `sh` 那种对齐**）：
    `[0]英文名 [1]中文名 [2]开 [3]昨收 [4]高 [5]低 [6]现价 [7]涨跌 [8]涨跌% [9]买 [10]卖 [11]额(元) [12]量(股) [13]PE [17]日期 [18]时间`
  - 美股 `hq_str_gb_aapl`：
    `[0]名 [1]现价 [2]涨跌% [3]时间 [4]涨跌额 [5]开 [6]高 [7]低 [10]量 [14]PE [26]昨收 [30]额(美元)`

### ③ 全市场名单 —— `Market_Center.*`（★这是"全市场实时"的现实解）
    .../json_v2.php/Market_Center.getHQNodeStockCount?node=hs_a     → `"5566"`
    .../json_v2.php/Market_Center.getHQNodeData?page=N&num=100&sort=symbol&asc=1&node=hs_a
    .../json_v2.php/Market_Center.getHKStockData?page=N&num=60&...&node=qbgg_hk
    .../json_v2.php/Market_Center.getHKStockCount?node=qbgg_hk     → `2498`
    http://stock.finance.sina.com.cn/usstock/api/jsonp.php/IO.XSRV2.CallbackList['x']/US_CategoryService.getList?page=N&num=40&sort=&asc=0&market=&id=
                                                                   → JSONP，`count=18165`
- **每页条数是服务端定的，`num` 传大了没用**：A股恒定 **100/页**（实测 num=500/1000/2000
  都只回 100 行）；港股 **60/页**；美股 **20/页（`num` 被完全忽略，测 num=2 也回 20 行）**。
- A股一行**逐字**字段：`symbol, code, name, trade, pricechange, changepercent, buy, sell,
  settlement, open, high, low, volume, amount, ticktime, per, pb, mktcap, nmc, turnoverratio`
  —— `settlement` 是昨收、`per` 是 PE、`nmc` 是流通市值(万元)、`turnoverratio` 是换手率。
  **这一行就是一整条实时快照**，比"拉代码表再批量打行情"少一半请求。
- 港股一行：`symbol, name, engname, lasttrade, prevclose, open, high, low, volume, amount,
  ticktime, buy, sell, pricechange, changepercent, pe_ratio, market_value`。
- 美股一行：`name, cname, category, symbol, price, diff, chg, preclose, open, high, low,
  amplitude, volume, mktcap, pe, market` —— **没有成交额**（不补 0，给 None）。

★港股/美股的页数很多（美股 18165/20 = **909 页**），所以 `fetch_market_list()` 是并发的，
  并且**如实回报"拿到几行 / 一共几行"** —— 拿不全就得让人看见拿不全。

### ④ 新浪代码的**前缀**与 `000xxx` 的歧义（2026-09-22 实测，`/intraday` 那个 bug 的现场）

- **显式前缀必须照单全收。** 调用方给的 code 自带 `sh`/`sz`/`bj` 时，那就是"哪个交易所"
  这个问题的答案。旧实现把它当普通字符、再按 market/首位粘一个 ⇒ `sh600519` →
  `szsh600519`、`sh000300` → `szsh000300`，新浪对这两个**回 null**（不报错），
  整条链就报成"所有源都失败"。见 `scodes()`。
- **`000xxx` 在代码形态上分不开**：`sz000001`=平安银行、`sh000001`=上证指数，
  **两个都真实存在**（实测两者都能取到分钟线）；`000905` 同理（深市个股 vs 中证500）。
  实测一张表（`scale=5, datalen=10`）：

  | 符号 | 结果 | | 符号 | 结果 |
  |---|---|---|---|---|
  | `sh000300` | OK | | `sz000300` | **null** |
  | `sh000001` | OK（上证指数） | | `sz000001` | OK（平安银行） |
  | `sh000905` | OK（中证500） | | `sz000905` | OK（深市个股） |
  | `sh399001` | **null** | | `sz399001` | OK（深证成指） |
  | `sh880000` | **null** | | `sh999999` | **null** |

  ⇒ `000xxx` 不许按形状硬猜（猜错不报错，只会把指数行情当股票行情显示）；
    `399xxx` 无歧义（只有 `sz` 那边有）。`880xxx`/`99xxxx` 在新浪**根本没有**
    （通达信/同花顺系列的指数码，两个前缀都回 null）。
"""

import concurrent.futures as cf
import re

import pandas as pd

from . import _base

# `2026-09-22` / `2026/09/22` 两种都吃；时间只认 `HH:MM:SS`
_DATE_RE = re.compile(r'^\d{4}[-/]\d{2}[-/]\d{2}$')
_TIME_RE = re.compile(r'^\d{2}:\d{2}(:\d{2})?$')

BASE_HQ = 'https://hq.sinajs.cn/list='
BASE_KLINE = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
              'CN_MarketData.getKLineData')
BASE_NODE = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
             'Market_Center.getHQNodeData')
BASE_NODE_HK = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                'Market_Center.getHKStockData')
BASE_COUNT = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
              'Market_Center.getHQNodeStockCount')
BASE_COUNT_HK = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                 'Market_Center.getHKStockCount')
BASE_US_LIST = ("http://stock.finance.sina.com.cn/usstock/api/jsonp.php/"
                "IO.XSRV2.CallbackList['licai']/US_CategoryService.getList")

HEADERS = {'Referer': 'https://finance.sina.com.cn'}

SRC = 'sina'

# 服务端固定的每页条数（★`num` 传大了没用，实测见模块头 ③）
PAGE_CN, PAGE_HK, PAGE_US = 100, 60, 20
# A股分钟 scale 合法值。1 不在里面 —— 实测 scale=1 回 null。
SCALES = (5, 15, 30, 60)
DAILY_SCALE = 240                             # `scale=240` 就是日 K（同一条接口）
MAX_DATALEN = 1023                            # 实测上限；再大不会更多


# 调用方给的 code 可能**自带**交易所前缀（`sh600519` / `sz000300` / `bj920000`）。
# 大小写都吃，出口统一小写。
_EXCH_PREFIX_RE = re.compile(r'^(sh|sz|bj)(\d+)$', re.I)


def scodes(market, code):
    """canonical → **候选**新浪代码（有序）。`scodes(...)[0]` 就是主判，见 `scode()`。

    ★为什么需要"候选"这个概念（2026-09-22 修，起因是 `/intraday` 永远不给结论）：

    ① 调用方给的 code 自带 `sh`/`sz`/`bj` 前缀时，**显式前缀就是答案** ——
       它回答的正是"哪个交易所"这个问题。旧实现把它当普通字符、再按 market/首位
       粘一个 ⇒ `sh600519` → `szsh600519`（实测新浪回 null）。⇒ 现在照单全收，
       不再重判，也**不给备选**（调用方已经拍过板了，替他猜第二个读法是越权）。

    ② `000xxx` 这一段**按代码形态真的分不开**：`sz000001`=平安银行、
       `sh000001`=上证指数，两个都真实存在（实测都能取到分钟线）；`000905` 同理。
       所以 market 说 `cn_stock`、code 是 `000300` 时**不许按形状硬猜** ——
       猜错不会报错，只会把指数的行情当成一只股票的行情显示（"错得毫无症状"）。
       ⇒ 这里给"主判 + 备选"，由 `minute()` 拿**上游的实际回包**来定：主判回空
         才试备选。★这不是静默降级：两个候选是**同一个标的的两种交易所读法**，
         不是"换一个数据源"，而且判定依据是上游真回了 K 线（回空=这个读法下
         这个标的**不存在**，不是"今天没行情"——后者是回包里有字段的）。

    ★否掉的替代方案（都推演过，写在这里免得下一个人重走一遍）：
      · "先剥掉已有前缀、再按形状重判"（最顺手的那种写法）—— **错**：
        `sh000300` 剥成 `000300`，再按 `cn_stock` 的形状判回 `sz000300`，
        **原样复现同一个 bug**。前缀不是噪声，是信息。
      · "`000xxx` 一律按指数拼 `sh`" —— **错**：`000001` 既是上证指数
        （`sh000001`）又是平安银行（`sz000001`），一律拼 `sh` 会把平安银行的
        页面**静默**换成上证指数的行情。
      · "在数据源里硬编码一张指数代码表" —— **否决**：那是编数据；指数代码是上游
        的事，而且一直在加（中证/国证系列）。
      · "改 `sessions.norm_market`，让 `cn` 按 code 形状映射到 `cn_index`" ——
        **否决**：`norm_market` 只拿到 market 字符串、看不到 code；而且它同时喂
        时段/涨跌停/T+1（`MARKETS`），在那里改会把"这只标的的交易规则"一起改掉
        （把股票当指数 = 丢掉涨跌停约束），影响面远超"取数"这一件事。
    ★★**已知边界（这一层修不掉，必须是调用方给提示）**：`000xxx` 里有几个码
      **两个读法都存在**，实测两个都是真的：

        `sh000905`=中证500(7828.94) / `sz000905`=厦门港务(9.34)
        `sh000001`=上证指数(3952.13)  / `sz000001`=平安银行(11.71)

      这里**故意不**把这类码判成指数：`cn_stock` 下 `000905` 的合理解释是
      "深市那只股票"（`cn_stock` 这个词就是"个股"的意思），判成指数会把个股的
      页面换成指数的行情 —— 而这类错**不报错**。真要看中证500请把 market 给
      成 `cn_index`（或 code 带前缀写 `sh000905`）。★根因在**上游**，且**已实测**：
      `tools._need_symbol('000905')` 本来就给出 `{'market': 'cn_index', 'name': '中证500'}`
      （对 `000300`/`399001` 同理），而 `app.py: _need_sym` 因为前端传了 `market='cn'`
      就**只取它的 `code`、丢掉它的 `market`**（那条 docstring 明写"前端给了 market
      就以它为准"，是别人的决定）⇒ 到这一层时"用户要的是指数"这条信息已经不存在了，
      于是 `000905` 落成 `cn_stock` ⇒ 主判 `sz000905` = **厦门港务**（实测末根
      close 9.34，而中证500 是 7828.94）—— 一个**不报错的错标的**。
      ★这一条本次**没修**（改 `_need_sym` 会同时动到 kline/news/predict 等 5 个
      接口的行为，属于协调者该排期的改动），只把边界写在这里。
      要根治得在 `app.py: _need_sym` 那一处用反推出的 market（至少当传入的是
      `cn` 这种**粗名**时），**不在本次改动范围内**，见交付说明。
      · "在 `app.py: _need_sym` 里改用 `_need_symbol` 解出来的 market" ——
        **否决**（不是不行，是不该由它管）：那条 docstring 明写"前端给了 market
        就以它为准"，是**故意**的（前端知道一些 `feed.resolve` 不认识的代码，
        如港股 00700）。改它等于推翻一条别人的决定，而且症状会从"取数失败"
        变成"市场被悄悄改写"——更难查。缺口在**符号拼法**上，就该在拼符号的
        这一层补。
    """
    code = str(code).strip()
    m = _EXCH_PREFIX_RE.match(code)
    if m:
        return ['%s%s' % (m.group(1).lower(), m.group(2))]
    primary = _scode_bare(market, code)
    # 备选只对 A 股系有意义（港美股的前缀是 `rt_hk`/`gb_`，交易所不是 sh/sz；bj 同理）。
    alt = (_swap_exch(primary)
           if str(market or '').strip().lower().startswith('cn') else None)
    return [primary] if (not alt or alt == primary) else [primary, alt]


def _swap_exch(sc):
    """`sh600519` ↔ `sz600519`。不是 sh/sz 形态的（`bj…`/`rt_hk…`/`gb_…`）没有备选。"""
    if sc[:2] == 'sh':
        return 'sz' + sc[2:]
    if sc[:2] == 'sz':
        return 'sh' + sc[2:]
    return None


def scode(market, code):
    """canonical → 新浪代码（只取**主判**那一个）。

    批量实时/快照走这里（`realtime_batch` 一次 60 个代码）：那里**不该探测** ——
    探测会把请求数翻倍，而 `hq.sinajs.cn` 对不存在的代码本来就是**静默缺条**，
    由调用方如实报 `missing`，不需要也不该在这里靠再加一次请求去猜。
    要"带备选"的（分钟线）用 `scodes()`。"""
    return scodes(market, code)[0]


def _scode_bare(market, code):
    """**不带前缀**的 code → 新浪代码。判据逐字是原来那一套（行为不变）。

    新浪的北交所前缀是 `bj`（从 `Market_Center` 的 `symbol` 字段实测得来：
    `bj920000`），**不是 `sh`** —— 92 开头按首位判会错。"""
    if market in ('hk_stock', 'hk'):
        return 'rt_hk' + code.zfill(5)
    if market in ('us_stock', 'us'):
        return 'gb_' + code.lower()
    if market in ('cn_index',):
        return ('sh' if code.startswith(('000', '88', '99')) else 'sz') + code
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


def from_scode(scode):
    """新浪代码 → `(market, code)`。港股 `rt_hk00700` 去零后仍是**5 位**（港股习惯
    补零到 5 位），A股 `sh600519` 去前缀，美股 `gb_aapl` 大写。"""
    scode = str(scode or '')
    if scode.startswith('rt_hk'):
        return 'hk_stock', scode[5:]
    if scode.startswith('gb_'):
        return 'us_stock', scode[3:].upper()
    if scode[:2] in ('sh', 'sz', 'bj'):
        return 'cn_stock', scode[2:]
    return None, scode


# ── 分钟线 ─────────────────────────────────────────────────────────────

def minute(market, code, period=5, datalen=240, timeout=None):
    """分钟 K。**只支持 A股/ETF/指数** —— 港美股在本机没有可用来源（模块头 ①）。
    返回 CANON 的 DataFrame；`amount` 是 **NaN**（上游不给这一列）并标
    `attrs['amount_missing']=True` —— 出口处 `feed._num()` 会把它变成 None。
    """
    if not market.startswith('cn'):
        raise _base.SourceError(
            'NO_MINUTE_SOURCE',
            '%s 的分钟线在本机没有可用来源：腾讯 fqkline 对港美股的 m5 只回一根聚合日线，'
            '新浪 getKLineData 只服务 A股，akshare 的 stock_hk/us_hist_min_em 走东财（不可达）' % market,
            '分钟线目前只支持 A 股')
    if int(period) not in SCALES + (DAILY_SCALE,):
        raise _base.SourceError('BAD_PERIOD',
                                '新浪 scale 只支持 %s 分钟（1 分钟实测回 null）'
                                % '/'.join(str(s) for s in SCALES),
                                '换 5/15/30/60 之一')
    n = max(10, min(int(datalen or 240), MAX_DATALEN))
    # ★符号的**解析**就在这个循环里（不是先 `scode()` 定死一个再取）：见 `scodes()`
    #   的 ② 与模块头 ④ —— `000300` 在 `cn_stock` 下有两个都真实存在的读法，
    #   只有上游的回包能定。主判回空才试备选；两个都空 = 这个标的真的没有行情。
    cands = scodes(market, code)
    j, err = None, None
    for i, sc in enumerate(cands):
        try:
            txt = _base.http_get(BASE_KLINE,
                                 params={'symbol': sc, 'scale': int(period),
                                         'ma': 'no', 'datalen': n},
                                 headers=HEADERS, timeout=timeout or _base.DEFAULT_TIMEOUT,
                                 label='sina.minute.%s' % sc)
            j = _base.json_or_text(txt, 'sina.minute.%s' % sc)
        except _base.SourceError:
            # ★传输层/解析层失败**不换读法**：那是"这个源此刻不通"，
            #   不是"这个符号不存在"，换前缀也一样不通，只会把超时翻倍。
            #   只有下面那种"上游明确回了空"才值得换（那才是符号的问题）。
            raise
        if isinstance(j, list) and j:
            break
        # ★"试过哪些符号"必须全部写进最后的报错里：只留最后一个的话，
        #   看到 `symbol=sz999999` 的人会以为我们只试了这一个、以为这个码
        #   在 `sh` 那边还有救（或者相反），于是去改一个本来就已经试过的参数。
        err = _base.SourceError(
            'EMPTY', '新浪分钟线回了 %s（scale=%s；试过 %s）'
            % ('null' if j is None else str(j)[:80], period, ' / '.join(cands)))
        if i + 1 < len(cands):
            # ★留痕：这不是"换了个源"，是"同一个源换了个符号拼法"。
            #   健康度里必须看得见它发生过（否则"为什么这个标的多花了一次请求"
            #   将无法从 health() 里回答）。
            #   ★★id **不能**用 `sina.minute.<period>`：`datasource.minute` 的 `_chain`
            #     马上就会用那个 id 记一条**成功**，`note()` 是**按 id 覆盖**的
            #     ⇒ 这条留痕会被紧随其后的成功整条冲掉，等于没记。
            #     （踩过：第一版就是那么写的，health() 里什么都看不到。）
            _base.note('sina.symbol.alt', False, 0, 0,
                       '%s/%s：%s 回空 ⇒ 改试 %s（同一标的的另一种交易所读法，'
                       '见 sina.scodes 的 ②）' % (market, code, sc, cands[i + 1]))
            j = None
    else:
        raise err
    rows = []
    for r in j:
        if not isinstance(r, dict):
            continue
        rows.append({'dt': r.get('day'), 'open': r.get('open'), 'high': r.get('high'),
                     'low': r.get('low'), 'close': r.get('close'),
                     'volume': r.get('volume'),
                     # ★NaN 不是 0 —— 上游这个接口就不给成交额（见模块头 ①）
                     'amount': float('nan')})
    df = pd.DataFrame(rows)
    df['dt'] = pd.to_datetime(df['dt'], errors='coerce')
    for c in ('open', 'high', 'low', 'close', 'volume'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['dt', 'open', 'high', 'low', 'close']).sort_values('dt')
    if not len(df):
        raise _base.SourceError('EMPTY', '新浪分钟线清洗后没有有效行')
    df = df[list(_base.CANON)].reset_index(drop=True)
    df.attrs['source'] = '%s.minute.%d' % (SRC, period)
    df.attrs['period'] = int(period)
    df.attrs['amount_missing'] = True
    return df


def daily_via_scale(market, code, days=400, timeout=None):
    """日 K 走 `scale=240`（同一个接口）。深度 1023 根，**比腾讯深**，
    所以腾讯拿不满（>640 根）时链上会落到这里。"""
    n = max(10, min(int(days or 400), MAX_DATALEN))
    return minute(market, code, period=240, datalen=n, timeout=timeout)


# ── 实时快照 ───────────────────────────────────────────────────────────

def parse_rt(txt):
    """`var hq_str_xxx="a,b,c";` 的整段文本 → [{...}]。按**前缀**分派三套布局。

    ★每行都带上 `_scode` —— 从 `var hq_str_` **后面那一段**取，即请求时用的
      新浪代码本身。批量回包**顺序和请求不一致**（实测 60 码的返回是乱序的），
      没有这个键就只能按位置对齐，那会把 A 的价配到 B 的名上 ——
      而这种错不会报错，只会让页面显示一个别人的涨跌幅。
    """
    out = []
    for line in (txt or '').split(';'):
        if '="' not in line:
            continue
        key = line.split('=')[0].strip()
        body = line.split('="', 1)[1].rstrip('";\n').strip()
        if not body or not key.startswith('var hq_str_'):
            continue
        scode = key[len('var hq_str_'):]
        f = body.split(',')
        try:
            if scode.startswith(('sh', 'sz', 'bj')):
                row = _parse_cn(f)
            elif scode.startswith('rt_hk'):
                row = _parse_hk(f)
            elif scode.startswith('gb_'):
                row = _parse_us(f)
            else:
                continue
        except (IndexError, ValueError):
            continue                             # 单条坏行不该拖垮整批
        if row:
            row['_scode'] = scode
            out.append(row)
    return out


def _parse_cn(f):
    """A股实时。★**不按下标硬取时间** —— 中间那 20 个五档字段的个数上游会变
    （实测有无五档两种形态），硬取 `f[30]`/`f[31]` 在少一个字段时会把
    `00` 当时间显示成 `"15:00:00 00"`（我第一次就是这么错的）。
    改成**扫出 `YYYY-MM-DD` 那个字段**，再取它后面一个当时间。
    """
    if len(f) < 10:
        return None
    d = t = None
    for i in range(len(f) - 1, 9, -1):           # 尾部往前找，最靠后的那个日期
        if _DATE_RE.match(f[i].strip()):
            d = f[i].strip()
            t = f[i + 1].strip() if i + 1 < len(f) else ''
            t = t if _TIME_RE.match(t) else None
            break
    pct = None
    price, prev = _base.safe_float(f[3]), _base.safe_float(f[2])
    if price is not None and prev:
        # 这个接口**不给涨跌幅**，由现价/昨收现算（两个数都是上游给的，不是编的）
        pct = round((price - prev) / prev * 100, 4)
    return {'code': None, 'name': (f[0] or '').strip() or None, 'market': 'cn_stock',
            'price': price, 'open': _base.safe_float(f[1]),
            'high': _base.safe_float(f[4]), 'low': _base.safe_float(f[5]),
            'prev_close': prev, 'pct': pct,
            'volume': _base.safe_float(f[8]), 'amount': _base.safe_float(f[9]),
            'turnover': None, 'pe': None,
            'ts': ('%s %s' % (d, t))[:16] if (d and t) else (d or t)}


def _parse_hk(f):
    if len(f) < 13:
        return None
    return {'code': None, 'name': (f[1] or f[0] or '').strip() or None, 'market': 'hk_stock',
            'price': _base.safe_float(f[6]), 'open': _base.safe_float(f[2]),
            'high': _base.safe_float(f[4]), 'low': _base.safe_float(f[5]),
            'prev_close': _base.safe_float(f[3]), 'pct': _base.safe_float(f[8]),
            'volume': _base.safe_float(f[12]), 'amount': _base.safe_float(f[11]),
            'turnover': None, 'pe': _base.pe(f[13]) if len(f) > 13 else None,
            'ts': ('%s %s' % (f[17], f[18]))[:16].replace('/', '-') if len(f) > 18 else None}


def _parse_us(f):
    if len(f) < 11:
        return None
    return {'code': None, 'name': (f[0] or '').strip() or None, 'market': 'us_stock',
            'price': _base.safe_float(f[1]), 'open': _base.safe_float(f[5]),
            'high': _base.safe_float(f[6]), 'low': _base.safe_float(f[7]),
            'prev_close': _base.safe_float(f[26]) if len(f) > 26 else None,
            'pct': _base.safe_float(f[2]), 'volume': _base.safe_float(f[10]),
            'amount': _base.safe_float(f[30]) if len(f) > 30 else None,
            'turnover': None, 'pe': _base.pe(f[14]) if len(f) > 14 else None,
            'ts': (f[3] or '')[:16] or None}


def realtime_batch(codes, timeout=None):
    """按**新浪代码**批量取实时（`codes` 是 `sh600519`/`rt_hk00700`/`gb_aapl` 形态）。

    返回 `(rows, missing)`。`missing` 是**上游没回的那些代码**（实测：不存在的代码
    静默缺条）。★按 `_scode` 对齐 —— 回包顺序和请求顺序不一致，按位置取会错位。
    ★缺条**如实报出来**，不补一行 None 当"拿到了"：上层要能分清
      "这个代码不存在"和"这个代码今天没成交"。
    """
    rows, missing = [], []
    for i in range(0, len(codes), 60):           # URL 长度考虑，60 一批
        chunk = [c for c in codes[i:i + 60] if c]
        if not chunk:
            continue
        txt = _base.http_get(BASE_HQ + ','.join(chunk), headers=HEADERS, encoding='gbk',
                             timeout=timeout or _base.DEFAULT_TIMEOUT, label='sina.rt')
        got = {}
        for r in parse_rt(txt):
            sc = r.pop('_scode', None)
            if sc:
                got[sc] = r
        for c in chunk:
            if c in got:
                rows.append(got[c])
            else:
                missing.append(c)
    return rows, missing


# ── 全市场名单（★"全市场实时"的现实解）────────────────────────────────

def market_count(market):
    """上游自报的标的总数。A股 5566、港股 2498、美股 18165（2026-09-22 实测）。"""
    if market == 'hk':
        txt = _base.http_get(BASE_COUNT_HK, params={'node': 'qbgg_hk'}, headers=HEADERS,
                             timeout=20, label='sina.count.hk')
        return _base.safe_int(str(txt).strip().strip('"'))
    if market == 'cn':
        txt = _base.http_get(BASE_COUNT, params={'node': 'hs_a'}, headers=HEADERS,
                             timeout=20, label='sina.count.cn')
        return _base.safe_int(str(txt).strip().strip('"'))
    return None                                  # 美股没有 count 接口，靠翻页直到空


def _page(market, page, timeout=None):
    """取一页。返回 list（可能为空）。美股要解 JSONP。"""
    if market == 'cn':
        txt = _base.http_get(BASE_NODE, params={'page': page, 'num': PAGE_CN,
                                                'sort': 'symbol', 'asc': 1, 'node': 'hs_a'},
                             headers=HEADERS, timeout=timeout or _base.DEFAULT_TIMEOUT,
                             label='sina.list.cn')
        j = _base.json_or_text(txt, 'sina.list.cn')
    elif market == 'hk':
        txt = _base.http_get(BASE_NODE_HK, params={'page': page, 'num': PAGE_HK,
                                                   'sort': 'symbol', 'asc': 1,
                                                   'node': 'qbgg_hk'},
                             headers=HEADERS, timeout=timeout or _base.DEFAULT_TIMEOUT,
                             label='sina.list.hk')
        j = _base.json_or_text(txt, 'sina.list.hk')
    else:
        txt = _base.http_get(BASE_US_LIST, params={'page': page, 'num': PAGE_US, 'sort': '',
                                                   'asc': 0, 'market': '', 'id': ''},
                             headers=HEADERS, timeout=timeout or _base.DEFAULT_TIMEOUT,
                             label='sina.list.us')
        j = _base.json_or_text(txt, 'sina.list.us')
        j = (j or {}).get('data') if isinstance(j, dict) else j
    return j if isinstance(j, list) else []


def market_list(market, pages=None, workers=8, timeout=None):
    """翻页取全市场名单。返回 `(rows, meta)`，`meta={'pages','rows','total','partial'}`。

    ★**部分拿到要如实说**：美股 18165/20 = 909 页，翻不完是常态。
      `meta['partial']` 为真时 `__init__` 会把这件事写进 `health()`，前端看得见。
      不许把"拿到 1200 行"当成"全市场就 1200 只"。
    """
    total = market_count(market)
    if pages is None:
        if market == 'us':
            pages = 60                          # ★909 页全翻是 3 分钟；默认只取前 1200 只
        elif total:
            pages = (total // (PAGE_US if market == 'us' else
                               PAGE_CN if market == 'cn' else PAGE_HK)) + 2
        else:
            pages = 60
    rows, ok_pages = [], 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_page, market, p, timeout): p for p in range(1, pages + 1)}
        for fut in cf.as_completed(futs):
            try:
                got = fut.result()
            except Exception:                    # noqa: BLE001 —— 一页失败不该拖垮整批
                continue
            if got:
                ok_pages += 1
                rows.extend(got)
    partial = bool(total and len(rows) < total) or (not total and len(rows) >= pages * 20)
    return rows, {'pages': pages, 'ok_pages': ok_pages, 'rows': len(rows),
                  'total': total, 'partial': partial}


def list_row_to_rt(row, market):
    """名单行 → 契约字段。★三套列名逐字对应（见模块头 ③），按**名字**取不按位置。"""
    if market == 'cn':
        return {'code': (row.get('code') or '').strip() or None,
                'name': (row.get('name') or '').strip() or None, 'market': 'cn_stock',
                'price': _base.safe_float(row.get('trade')),
                'open': _base.safe_float(row.get('open')),
                'high': _base.safe_float(row.get('high')),
                'low': _base.safe_float(row.get('low')),
                'prev_close': _base.safe_float(row.get('settlement')),
                'pct': _base.safe_float(row.get('changepercent')),
                'volume': _base.safe_float(row.get('volume')),
                'amount': _base.safe_float(row.get('amount')),
                'turnover': _base.safe_float(row.get('turnoverratio')),
                'pe': _base.pe(row.get('per')),
                'ts': _base._s(row, 'ticktime')}
    if market == 'hk':
        return {'code': (row.get('symbol') or '').strip() or None,
                'name': (row.get('name') or '').strip() or None, 'market': 'hk_stock',
                'price': _base.safe_float(row.get('lasttrade')),
                'open': _base.safe_float(row.get('open')),
                'high': _base.safe_float(row.get('high')),
                'low': _base.safe_float(row.get('low')),
                'prev_close': _base.safe_float(row.get('prevclose')),
                'pct': _base.safe_float(row.get('changepercent')),
                'volume': _base.safe_float(row.get('volume')),
                'amount': _base.safe_float(row.get('amount')),
                'turnover': None,
                'pe': _base.pe(row.get('pe_ratio')),
                'ts': _base._s(row, 'ticktime')}
    return {'code': (row.get('symbol') or '').strip() or None,
            'name': (row.get('cname') or row.get('name') or '').strip() or None,
            'market': 'us_stock',
            'price': _base.safe_float(row.get('price')),
            'open': _base.safe_float(row.get('open')),
            'high': _base.safe_float(row.get('high')),
            'low': _base.safe_float(row.get('low')),
            'prev_close': _base.safe_float(row.get('preclose')),
            'pct': None if row.get('chg') in (None, '') else _base.safe_float(row.get('chg')),
            'volume': _base.safe_float(row.get('volume')),
            # ★美股名单接口没有成交额 —— 给 None，**不补 0**（0 会被当真数读）
            'amount': None,
            'turnover': None,
            'pe': _base.pe(row.get('pe')),
            'ts': None}
