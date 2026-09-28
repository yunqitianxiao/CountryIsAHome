"""同花顺行情源 —— 板块清单/成分股的副源，兼 A股日线的深历史兜底。

## 实测事实（本机 Linux 6.8.0-138 / Python 3.10.12，2026-09-22）

### ① A股日线 `http://d.10jqka.com.cn/v6/line/hs_<code>/01/last.js`
- 实测 `hs_600519` → HTTP 200、0.2~1.2s、**11434 字节**。回的是 JSONP：
  `quotebridge_v6_line_hs_600519_01_last({...})`，剥壳后是 JSON。
- 顶层键**逐字**：`year, total, num, rt, start, name, data, marketType, issuePrice, today`
  - `total='6009'`（该股全部可用根数）、`num=140`（**本次给 140 根**）、
    `year={'2001':86,...,'2026':...}`（每年根数）、`today='20260922'`、
    `rt='0930-1130,1300-1500'`（交易时段）、`marketType='HS_stock_sh'`。
  - `data` 是**一个字符串**，用 `;` 分根、`,` 分列（不是 JSON 数组）。
- 每根列序**逐字**：`日期,开,高,低,收,量(股),额(元),换手率,?,?,?`
  实测最后一根 `20260921,1259.00,1259.95,1250.80,1252.57,2501689,3135910000.00,0.200,`
  —— **`[1]开 [2]高 [3]低 [4]收`**，与新浪同序，与腾讯（开/收/高/低）**不同**。
  ★**它带成交额**（`[6]`，元）—— 这是腾讯 K 线缺的那一列，所以 THS 有独立价值。
- ★`bk_301558`（板块）与 `zs_000300`（指数）实测 **404** ⇒ **这条只服务 A股个股**。
  `last.js` 一次 140 根；想更深要走按年文件（`.../01/2024.js`，本包**没实现也没验**，
  写在交付说明的"没验的"一节）。

### ② 概念板块清单 `http://q.10jqka.com.cn/gn/index/`（HTML，GBK）
- 实测 HTTP 200、0.5~0.6s、**101403 字节**。页面里 **361 个** `/gn/detail/code/<code>/`
  链接，去重后仍是 361 —— **一份页面就够，不用翻页**（页脚写 `1/39`，
  那是"每页 10 行的表格视图"的页数，板块全量在导航区里铺开了）。
- code+name 成对可取：`308614=阿尔茨海默概念`、`309121=AI PC`、`301558=阿里巴巴概念`。
- ★**只有 code 和 name**，页面里的涨跌幅是那 10 行"驱动事件"表的、不是板块的
  ⇒ 本源的 `boards()` **`pct`/`amount`/`vol_ratio` 一律 None**，不许拿别处的数顶替。
  （腾讯那份有完整字段，所以链上腾讯在前。）

### ③ 行业板块清单 `http://q.10jqka.com.cn/thshy/index/`（HTML，GBK）
- 实测 HTTP 200、62595 字节，`/thshy/detail/code/<code>/` 去重 **90 个**
  （网航区里 code+name 对出现 140 次，是因为含重复链接，去重才是真实个数）。
- 同样是**只有 code+name**。

### ④ 成分股 `http://q.10jqka.com.cn/gn/detail/code/<board_code>/`（HTML，GBK）
- ★**必须用这条不带 `ajax/1` 的路径**。带 `ajax/1` 的
  `.../field/264648/order/desc/page/1/ajax/1/code/301558` 实测 **HTTP 401**，
  回的是一个 JS 跳转页（`chameleon` 反爬），**不是数据**。
- 不带 ajax 的详情页 200、**73438 字节**，表格列头逐字：
  `序号, 代码, 名称, 现价, 涨跌幅(%), 涨跌, 涨速(%), 换手(%), 量比, 振幅(%)`。
- ★**每页只给 10 行**（表格里就是 10 个 `<tr>`）。翻页同样要 cookie，
  ⇒ 本源一次只回 **10 个成分股**，且**明确回报"只拿到 10 个"**，不假装是全量。

### ⑤ 反爬边界（别在这上面浪费时间）
- 所有 `.../ajax/1/` 路径 → **401 + JS 跳转**；`d.10jqka.com.cn` 的直接 JSON 接口
  （`/v6/realhead/...`、`/v6/line/bk_.../all.js`）→ **404**。
- ②③④ 三条都是"网页里本来就有、正则捞出来"的形态，**没有 cookie 也能拿**。
"""

import re

import pandas as pd

from . import _base

BASE_LINE = 'http://d.10jqka.com.cn/v6/line/hs_%s/01/last.js'
BASE_GN = 'http://q.10jqka.com.cn/gn/index/'
BASE_HY = 'http://q.10jqka.com.cn/thshy/index/'
BASE_GN_MEMBER = 'http://q.10jqka.com.cn/gn/detail/code/%s/'
BASE_HY_MEMBER = 'http://q.10jqka.com.cn/thshy/detail/code/%s/'

HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
           'Referer': 'http://q.10jqka.com.cn/'}

SRC = 'ths'

# THS 的 HTML 是 GBK（不显式指定的话 requests 会按 ISO-8859-1 猜，
# 中文板块名会变成乱码 —— 而乱码是合法字符串，不报错，只让页面显示看不懂的字）
ENC = 'gbk'

_TAG = re.compile(r'<[^>]+>')
# `last.js` 的 JSONP 回调名随代码变，所以按第一个 `(` 和最后一个 `)` 剥
_ROWS_PER_PAGE = 10                            # 实测成分股表每页就是 10 行


def _text(html):
    """按 GBK 取页面。走 `_base.http_get` 以复用超时/重试/去代理那套。"""
    return _base.http_get(html, headers=HEADERS, encoding=ENC,
                          timeout=_base.DEFAULT_TIMEOUT, label='ths')


# ── 日线（★唯一带成交额的 A股日线源）──────────────────────────────────

def kline_daily(code, timeout=None):
    """A股个股日线，**最多 140 根**（`last.js` 的 `num`）。带成交额。

    ★只支持 A股个股/ETF（`hs_` 前缀）：`bk_`（板块）和 `zs_`（指数）实测 404。
    """
    code = str(code).strip()
    if not code.isdigit():
        raise _base.SourceError('BAD_CODE', '同花顺 `hs_` 只吃纯数字 A股代码，收到 %r' % code)
    txt = _base.http_get(BASE_LINE % code, headers=HEADERS, timeout=timeout
                         or _base.DEFAULT_TIMEOUT, label='ths.kline.%s' % code)
    j = _base.json_or_text(txt, 'ths.kline.%s' % code)
    if not isinstance(j, dict):
        raise _base.SourceError('BAD_JSON', '同花顺 %s 的回包不是对象：%s'
                                % (code, str(j)[:100]))
    raw = j.get('data') or ''
    if not raw:
        raise _base.SourceError('EMPTY', '同花顺 %s 的 data 是空的（回包键：%s）'
                                % (code, list(j.keys())[:10]))
    rows = []
    for line in raw.split(';'):
        f = line.split(',')
        if len(f) < 7:
            continue
        # ★列序 开/高/低/收 —— 与新浪同、与腾讯（开/收/高/低）**不同**
        rows.append({'dt': f[0], 'open': f[1], 'high': f[2], 'low': f[3], 'close': f[4],
                     'volume': f[5], 'amount': f[6]})
    df = pd.DataFrame(rows)
    if not len(df):
        raise _base.SourceError('EMPTY', '同花顺 %s 的 data 一行都没解析出来' % code)
    df['dt'] = pd.to_datetime(df['dt'], format='%Y%m%d', errors='coerce')
    for c in ('open', 'high', 'low', 'close', 'volume', 'amount'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['dt', 'open', 'high', 'low', 'close']).sort_values('dt')
    if not len(df):
        raise _base.SourceError('EMPTY', '同花顺 %s 清洗后没有有效行' % code)
    df = df[list(_base.CANON)].reset_index(drop=True)
    df.attrs['source'] = '%s.kline.daily' % SRC
    # ★这一源**有**成交额，所以不设 amount_missing（与 tencent/sina 相反）
    return df


# ── 板块清单 ───────────────────────────────────────────────────────────

def _pairs(html, seg):
    """从板块页里捞 `code + name`。`seg` 是 `gn` 或 `thshy`。

    ★去重按 code（页面里同一个 code 会出现多次：导航区一份、表格里一份）。
      顺序按页面出现顺序，**不排序** —— 上游的顺序（字母/拼音）本身有信息，
      我们重排会让"同一板块在两次调用里位置不同"变成一条假的不一致。
    """
    seen, out = set(), []
    for code, name in re.findall(r'/%s/detail/code/(\d+)/[^>]*>([^<]{1,24})<' % seg, html):
        if code in seen:
            continue
        nm = _TAG.sub('', name).strip()
        if not nm:
            continue
        seen.add(code)
        out.append({'code': code, 'name': nm})
    return out


def board_list(kind='concept', timeout=None):
    """板块清单。**只有 code 和 name**（见模块头 ②③）⇒ 其余字段一律 None。"""
    if kind not in ('concept', 'industry'):
        raise _base.SourceError('BAD_KIND', '同花顺只区分 concept/industry，收到 %r' % kind)
    url, seg = (BASE_GN, 'gn') if kind == 'concept' else (BASE_HY, 'thshy')
    txt = _base.http_get(url, headers=HEADERS, encoding=ENC,
                         timeout=timeout or _base.DEFAULT_TIMEOUT, label='ths.boards.%s' % kind)
    pairs = _pairs(txt, seg)
    if not pairs:
        raise _base.SourceError('EMPTY', '同花顺 %s 板块页里一个 code 都没捞到（页面 %d 字节）'
                                % (kind, len(txt or '')))
    return pairs, {'total': len(pairs)}


def board_members(board_code, kind='concept', timeout=None):
    """板块成分股。★**每页只有 10 个**（模块头 ④），翻页要 cookie ⇒ 只回 10 个，
    并把 `complete=False` 如实交给上层 —— 绝不假装这是全量。
    """
    seg = 'gn' if kind == 'concept' else 'thshy'
    url = (BASE_GN_MEMBER if kind == 'concept' else BASE_HY_MEMBER) % board_code
    txt = _base.http_get(url, headers=HEADERS, encoding=ENC,
                         timeout=timeout or _base.DEFAULT_TIMEOUT, label='ths.members')
    tb = re.search(r'<table[^>]*>.*?</table>', txt or '', re.S)
    if not tb:
        raise _base.SourceError('EMPTY', '同花顺 %s/%s 页里没有表格' % (seg, board_code))
    codes, rows = [], 0
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', tb.group(0), re.S):
        cells = [_TAG.sub('', c).replace('&nbsp;', '').strip()
                 for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S)]
        if len(cells) < 3 or not cells[1].isdigit():
            continue                              # 表头 / 空行
        codes.append(cells[1].zfill(6))
        rows += 1
    if not codes:
        raise _base.SourceError('EMPTY', '同花顺 %s/%s 的表里没有成分股行' % (seg, board_code))
    return codes, {'rows': rows, 'per_page': _ROWS_PER_PAGE, 'complete': False}
