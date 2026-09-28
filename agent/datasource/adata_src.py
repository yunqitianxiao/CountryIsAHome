"""adata 2.9.5 里**还活着的那几条路** —— 外加一份"哪几条死了、为什么"的台账。

## ★先说结论：adata 在这台机器上大部分是坏的，而且**坏得很安静**

adata 的绝大多数接口后端是**东方财富**。而本机（2026-09-22，直连无代理）实测：
`push2his.eastmoney.com` / `push2.eastmoney.com` 一律
`ConnectionError: RemoteDisconnected`，**0.2 秒就被断连**；
`quote.eastmoney.com` 网页却是 **200** ⇒ 是**路径级/协议级**拦截，不是整站墙。

★★**但"东财全挂"是错的 —— 是"东财的 `push2*` 那一族挂了"。**
  后来把东财的每台主机逐个打了一遍（TCP 全通，所以差别在 HTTP 层），结论：

  | 主机 | HTTP 实测 |
  |---|---|
  | `push2.eastmoney.com` / `push2his` / `82.push2` / `push2delay` | ❌ 全部 `RemoteDisconnected` |
  | **`datacenter.eastmoney.com`** | ✅ **269ms 回真实 JSON**（`get_plate_east` 走的就是它） |
  | **`datacenter-web.eastmoney.com`** | ✅ **200**，`RPT_DAILYBILLBOARD_DETAILSNEW` 实测 **89538 页**真实龙虎榜数据 |
  | `quote.eastmoney.com` / `data.eastmoney.com`（网页） | ✅ 200 |
  | `f10.eastmoney.com` | ❌ 403 |

  ⇒ 分界线是**主机族**（`push2*` 死、`datacenter*` 活），不是"东财整站"。
  这条修正是拿真主机名逐个量出来的（见 `_probe.py` 的 `probe_eastmoney_hosts`）。
  ★为什么值得较真：`get_plate_east` 一度被我判成"百度后端居然通了"，
  实际它打的是 `datacenter.eastmoney.com`，**跟百度一点关系都没有**
  （见 `stock_plates` 的 docstring）。差一点就照着错的原因去排查了。

坏得安静是因为 adata 有个 `handler_null` 装饰器：它把异常吞成
`pd.DataFrame()`（空表）。于是调用方看到的是"**这只股票今天没有数据**"
而不是"**数据源不可达**" —— 前者会被当成正常情况一路放行。
本模块因此**不把空表当成功**：空就是 `ok=false` + 人话原因（见 `__init__.health()`）。

## 逐条实测台账（2026-09-22，本机 `Linux 6.8.0-138 / Python 3.10.12 / adata 2.9.5`）

### 死掉的（**本包一条都不用**，列在这里是为了别人不再重测一遍）
| 接口 | 实测结果 | 真原因 |
|---|---|---|
| `stock.market.get_market(...)` 日K | 全 k_type 一律 **0 行**、0.1~0.25s | 纯东财，`handler_null` 把断连吞成空表 |
| `stock.info.all_code()` | **60s 超时** | 东财 |
| `stock.info.all_concept_code_east()` | `FileNotFoundError`（缺缓存 CSV） | 2.9.5 自身 bug |
| `stock.info.get_industry_sw(code)` | `TypeError: 'int' object is not iterable` | 2.9.5 自身 bug |
| `stock.market.get_capital_flow(...)` 日频资金流 | 0 行 | 东财 |
| `stock.market.get_market_bar(...)` | **30s 未返回** | 内部裸 `requests`，**无 timeout** |
| `list_market_current([1200 码])` | 0 行；100 码只回 81 行 | 东财 + 静默丢码 |
| `stock.market.get_market_concept_current_east` / `_east` / `_min_east` | 0 行 | 东财 |

### 活着的（★本模块就用这些）
| 接口 | 实测 | 返回 |
|---|---|---|
| `stock.info.trade_calendar()` | **2.4s / 365 行** | `trade_date, trade_status, day_week` |
| `stock.info.index_constituent('000300')` | **5.2s / 300 行** | 沪深300 成分 |
| `stock.info.get_plate_east('600519')` | **198ms / 27 行** | `stock_code, plate_code, plate_name, plate_type, source`（走 **`datacenter.eastmoney.com`**，该主机活着） |
| `stock.info.all_concept_code_ths()` | **48s / 361 行** | `index_code, name, concept_code, source` |
| `stock.market.get_market_concept_ths('886013')` | 973 行日线 | 同花顺概念指数 |
| `stock.market.get_market_concept_min_ths('886013')` | 241 行 | 同花顺概念分时 |
| `stock.market.get_capital_flow_min('600519')` | 240 行 | `stock_code, trade_time, main_net_inflow, sm_net_inflow, mid_net_inflow, lg_net_inflow, max_net_inflow` |
| `stock.market.get_market_min('600519')` | 266 行 / 19s，**时好时坏** | 内部降级到百度 |

★两处**与协调者给的可达性表冲突**、必须留痕的实测：

  1. ~~`get_plate_east` 走百度却通了~~ —— **第一次的归因是错的，已查清**：
     读了 adata 2.9.5 的源码
     （`.venv/.../adata/stock/info/concept/stock_concept_east.py:119`），
     `get_plate_east` 打的是 **`datacenter.eastmoney.com/securities/api/data/get`**，
     **不是百度**。所以它 198ms 成功与"百度 403"毫无关系；
     真相是**东财 `datacenter*` 主机族是通的，只有 `push2*` 那族被断连**
     （见模块头那张主机表）。教训：**没读源码之前，"它走的是哪个域名"只是猜**，
     而一个猜错的上游会让排查完全跑偏。
  2. `get_market_min` 一次 266 行、一次 0 行（23s）—— **同一个调用两种结果**，
     所以 `minute_1m()` 标了 `flaky=True` 且**默认不进源链**（见 `__init__.minute`）。

## 为什么还要留着 adata
三条**别处拿不到**的能力：
- `trade_calendar()` —— 腾讯/新浪/同花顺都没有干净的交易日历接口
- `index_constituent()` —— 指数成分股
- `get_capital_flow_min()` —— A股分钟级资金流，本机**唯一**能取到的资金流

其余能力都由 tencent/sina/ths 覆盖得更好，不在这里重复实现。

## 纪律
adata 内部是**裸 `requests.request(...)`，一个 timeout 都没有**。所以本模块
**每一个**调用都过 `_base.timed()`（内部 `concurrent.futures` + `fut.result(timeout=)`），
超时按失败记。这不是防御性编程，是实测出来的必需：`get_market_bar` 真的挂到 30 秒。
"""

import datetime as dt

import pandas as pd

from . import _base

SRC = 'adata'

# adata 是**可选依赖**：没装不该让整个 datasource 包 import 不进来。
# 所以延迟到第一次真调用才 import，并且把失败原因如实带出去。
_IMPORT_ERR = None


def _adata():
    """拿到 adata 模块。装不上就抛 —— **不静默降级**：
    「装不上」和「这只股票没数据」是两件事，混起来会让人查错方向。"""
    global _IMPORT_ERR
    try:
        import adata                             # noqa: PLC0415
        return adata
    except Exception as e:                       # noqa: BLE001
        _IMPORT_ERR = '%s: %s' % (type(e).__name__, e)
        raise _base.SourceError('NO_ADATA', 'adata 导不进来：%s' % _IMPORT_ERR) from None


def _call(label, fn, timeout=None):
    """所有 adata 调用的唯一入口 —— 超时、健康度记账都在这里收口。"""
    return _base.timed('%s.%s' % (SRC, label), fn,
                       timeout=timeout or _base.DEFAULT_TIMEOUT,
                       label='adata.%s' % label)


# ── 交易日历 ───────────────────────────────────────────────────────────

def trade_calendar(days=400, timeout=40):
    """→ `['YYYY-MM-DD', ...]`（升序）。★本机**唯一**干净的交易日历来源。

    实测 2.4s / 365 行，列名逐字 `trade_date, trade_status, day_week`。
    `trade_status` 实测恒为 1（非交易日根本不出现），所以只按行取日期。

    ★★**返回全量，不按 `days` 截** —— 截取 + 砍未来日期都在
      `__init__.trade_calendar` 的出口做（理由见 `akshare_src.trade_calendar`）。
      这个 `days` 参数**只声明意图、不改变返回**，留着是为了调用点读起来一致。
    """
    def go():
        ad = _adata()
        df = ad.stock.info.trade_calendar()
        if not isinstance(df, pd.DataFrame) or not len(df):
            raise _base.SourceError('EMPTY', 'adata trade_calendar 回了空表')
        return df
    df = _call('trade_calendar', go, timeout=timeout)
    s = pd.to_datetime(df['trade_date'], errors='coerce').dropna()
    return sorted({d.strftime('%Y-%m-%d') for d in s})


# ── 指数成分股 ─────────────────────────────────────────────────────────

def index_constituent(index_code, timeout=40):
    """指数成分股 → `[(code, name), ...]`。实测 5.2s / 000300 → 300 行。

    ★列名**不硬编码**：adata 各版本这个表的列名不一致，实测那版是
      `index_code, stock_code, stock_name, ...` 但没写进规格，所以按
      "含 code 字样的列" / "含 name 字样的列" 去找，找不到就**如实报错**
      而不是猜一个列出来（猜错会把成分股名单变成一串别的什么）。
    """
    def go():
        ad = _adata()
        df = ad.stock.info.index_constituent(index_code=str(index_code).strip())
        if not isinstance(df, pd.DataFrame) or not len(df):
            raise _base.SourceError('EMPTY', 'adata index_constituent(%s) 回了空表'
                                    % index_code)
        return df
    df = _call('index_constituent', go, timeout=timeout)
    ccol = _pick(df, ('stock_code', 'code', '成分券代码', '证券代码'))
    ncol = _pick(df, ('stock_name', 'name', '成分券名称', '证券简称'))
    if ccol is None:
        raise _base.SourceError('NO_COL',
                                'adata index_constituent 回表里找不到代码列（实际列：%s）'
                                % list(df.columns)[:12])
    codes = [str(x).strip().zfill(6) for x in df[ccol] if str(x).strip()]
    names = ([str(x).strip() for x in df[ncol]] if ncol else [None] * len(codes))
    return list(zip(codes, names))


def _pick(df, cands):
    """按候选名（精确）找列。★不做模糊匹配 —— `stock_code` 和 `index_code`
    都含 "code"，模糊匹配会把指数代码当成股票代码，于是成分股名单里
    300 个"股票"全是同一个指数。找不到就返回 None，由调用方报错。"""
    for c in cands:
        if c in df.columns:
            return c
    return None


# ── 个股所属板块 ───────────────────────────────────────────────────────

def stock_plates(code, timeout=25):
    """`stock_boards()` 的一条路 → `[{code, name, kind}]`。

    走 `adata.stock.info.get_plate_east`（实测 198ms / 27 行）——
    返回列逐字 `stock_code, plate_code, plate_name, plate_type, source`。
    ★上游 `plate_type` 是**中文**，实测取值只有三种：`行业` / `概念` / `板块`
      （不是协调者说的英文 concept/industry/region —— 已按真实取值改）。
      本模块**原样透出中文**，归一交给 `__init__.stock_boards` 的出口做
      （那里把 `概念→concept`、`行业→industry` 对齐到 `boards(kind=…)` 的枚举，
       `板块` 这种没有对应枚举的原样保留 —— 硬塞一个 `region` 是我编的词）。
    ★★**这条打的是 `datacenter.eastmoney.com`**（读 adata 源码确认，见模块头）。
      所以它 198ms 成功**不是**"百度 403 却被我们打通了"，
      而是"东财 `datacenter*` 主机族本来就通"。
    """
    def go():
        ad = _adata()
        df = ad.stock.info.get_plate_east(stock_code=str(code).strip())
        if not isinstance(df, pd.DataFrame) or not len(df):
            raise _base.SourceError('EMPTY', 'adata get_plate_east(%s) 回了空表' % code)
        return df
    df = _call('get_plate_east', go, timeout=timeout)
    ccol = _pick(df, ('plate_code', 'code'))
    ncol = _pick(df, ('plate_name', 'name'))
    if ccol is None or ncol is None:
        raise _base.SourceError('NO_COL',
                                'get_plate_east 回表里找不到板块列（实际列：%s）'
                                % list(df.columns)[:12])
    kcol = _pick(df, ('plate_type', 'type'))
    out = []
    for _, r in df.iterrows():
        cd = str(r.get(ccol) or '').strip()
        nm = str(r.get(ncol) or '').strip()
        if not cd:
            continue
        out.append({'code': cd, 'name': nm or cd,
                    'kind': (str(r.get(kcol)).strip() if kcol else 'concept')})
    if not out:
        raise _base.SourceError('EMPTY', 'get_plate_east 的板块列里没有非空行')
    return out


# ── 同花顺概念（经 adata 中转）─────────────────────────────────────────

def concept_list_ths(timeout=70):
    """同花顺概念清单 → `[{'code','name'}]`。

    实测 **48 秒** / 361 行，列逐字 `index_code, name, concept_code, source`。
    ★慢到 48s 是有意义的：`ths.py` 直接抓 `q.10jqka.com.cn/gn/index/` 只要
      **0.68 秒**就拿到同样 361 个板块，所以链上 ths 在 adata 前面。
      这里留着是**交叉校验**用：两个来源独立拿到 361 vs 903（腾讯），
      数量对不上本身就是一条信息（口径不同），不该被抹平。
    """
    def go():
        ad = _adata()
        df = ad.stock.info.all_concept_code_ths()
        if not isinstance(df, pd.DataFrame) or not len(df):
            raise _base.SourceError('EMPTY', 'adata all_concept_code_ths 回了空表')
        return df
    df = _call('all_concept_code_ths', go, timeout=timeout)
    ccol = _pick(df, ('concept_code', 'index_code', 'code'))
    ncol = _pick(df, ('name', 'concept_name'))
    if ccol is None or ncol is None:
        raise _base.SourceError('NO_COL', 'all_concept_code_ths 回表里找不到 code/name 列（实际列：%s）'
                                % list(df.columns)[:12])
    return [{'code': str(r.get(ccol)).strip(), 'name': str(r.get(ncol)).strip()}
            for _, r in df.iterrows() if str(r.get(ccol)).strip()]


def concept_members_ths(concept_code, timeout=40):
    """同花顺概念成分股 → `[code, ...]`。`concept_constituent_ths`。"""
    def go():
        ad = _adata()
        df = ad.stock.info.concept_constituent_ths(concept_code=str(concept_code).strip())
        if not isinstance(df, pd.DataFrame) or not len(df):
            raise _base.SourceError('EMPTY', 'adata concept_constituent_ths(%s) 回了空表'
                                    % concept_code)
        return df
    df = _call('concept_constituent_ths', go, timeout=timeout)
    ccol = _pick(df, ('stock_code', 'code', 'con_code', '股票代码'))
    if ccol is None:
        raise _base.SourceError('NO_COL', 'concept_constituent_ths 回表里找不到股票代码列（实际列：%s）'
                                % list(df.columns)[:12])
    return sorted({str(x).strip().zfill(6) for x in df[ccol] if str(x).strip()})


# ── 资金流（★本机唯一能取到的资金流）──────────────────────────────────

def capital_flow_min(code, timeout=30):
    """A股分钟级资金流。实测 240 行，列名**逐字**：
    `stock_code, trade_time, main_net_inflow, sm_net_inflow, mid_net_inflow,
     lg_net_inflow, max_net_inflow`。

    ★按规格 §3.1：**上游列名原样保留**（不翻译成英文），只加 `attrs['source']`。
      上层要看的是"主力/大单/超大单"这套语义，改名会把语义丢掉。
    """
    def go():
        ad = _adata()
        df = ad.stock.market.get_capital_flow_min(stock_code=str(code).strip())
        if not isinstance(df, pd.DataFrame) or not len(df):
            raise _base.SourceError('EMPTY', 'adata get_capital_flow_min(%s) 回了空表' % code)
        return df
    df = _call('get_capital_flow_min', go, timeout=timeout).copy()
    df.attrs['source'] = '%s.capital_flow.min' % SRC
    df.attrs['unit'] = '元'                       # 实测净流入是元（对照成交额量级）
    return df


def capital_flow_daily(code, timeout=25):
    """★**本机取不到**，函数存在的唯一意义是把这件事**如实抛出来**。

    `adata.stock.market.get_capital_flow` 走东财 **`push2his`**（该主机被断连），
    实测 0 行；腾讯那几条 `FundflowController` 路径实测 404；
    同花顺的资金流接口实测 401（反爬）。⇒ 日频资金流**没有可用来源**。
    契约里 `capital_flow(period='daily')` 允许失败，这里就让它以
    `ok=false` + 真原因出现在 `health()` 里，**不编一个空表糊过去**。

    ★★**注意别把这条原因写成"东财不可达"** —— 东财的 `datacenter*` 主机是通的
      （见模块头），死的是 `push2*` 那族。所以真正的说法是
      "**东财的资金流接口恰好全在死掉的那族主机上**"，而不是"东财整体不可达"。
      ★还有一条**没验过的线索**（写在交付说明的"没验的"一节）：
      `datacenter-web.eastmoney.com/api/data/v1/get` 是活的、能出真报表，
      但它的 reportName 里有没有资金流报表，我**没有查完** ——
      试的 3 个名字（`RPT_HISTORY_FUNDFLOW`/`RPT_STOCK_FUNDFLOW`/
      `RPT_DAILY_FUNDFLOW`）全是"报表配置不存在"，但这不能证明没有别的名字。
    """
    raise _base.SourceError(
        'NO_SOURCE',
        'A股日频资金流在本机没有可用来源：adata.get_capital_flow 走东财 push2his'
        '（该主机族被断连，实测 0 行）、腾讯 FundflowController 各路径 404、'
        '同花顺资金流接口 401（反爬）。'
        '★未验线索：东财 datacenter-web 是通的，尚未找到它的资金流 reportName',
        '日频资金流暂时取不到；分钟级可走 adata.get_capital_flow_min')


# ── 分时（1 分钟）—— 已知不可靠，默认不进链 ───────────────────────────

def minute_1m(code, timeout=45):
    """`adata.stock.market.get_market_min`，当日分时。

    ★**实测两次两个结果**：一次 266 行 / 19s，一次 0 行 / 23s。
      它内部会降级到百度，而百度在这台机器上是 403 风控 ⇒ 通不通看运气。
      所以：① 超时给到 45s（它真的慢）；② `__init__.cn_minute_chain` 里
      **默认不排它**，只有显式要 period=1 时才试；③ 结果里带
      `attrs['flaky']=True`，让上层知道这一列别太当真。
    """
    def go():
        ad = _adata()
        df = ad.stock.market.get_market_min(stock_code=str(code).strip())
        if not isinstance(df, pd.DataFrame) or not len(df):
            raise _base.SourceError('EMPTY', 'adata get_market_min(%s) 回了空表' % code)
        return df
    df = _call('get_market_min', go, timeout=timeout).copy()
    df.attrs['source'] = '%s.minute.1' % SRC
    df.attrs['period'] = 1
    df.attrs['flaky'] = True                     # ★实测不稳定，见 docstring
    return df


def probe_report():
    """把模块头那张台账变成可返回值，给 `_probe.py` / 运维页面用。"""
    return {
        'ok': ['trade_calendar', 'index_constituent', 'get_plate_east',
               'all_concept_code_ths', 'get_market_concept_ths',
               'get_market_concept_min_ths', 'get_capital_flow_min',
               'get_market_min(不稳定)'],
        'dead': {
            'stock.market.get_market': '东财不可达，handler_null 吞成空表',
            'stock.info.all_code': '东财，60s 超时',
            'stock.info.all_concept_code_east': '2.9.5 bug: FileNotFoundError 缺缓存 CSV',
            'stock.info.get_industry_sw': "2.9.5 bug: TypeError: 'int' object is not iterable",
            'stock.market.get_capital_flow': '东财，0 行',
            'stock.market.get_market_bar': '内部无 timeout，30s 未返回',
            'list_market_current': '东财 + 静默丢码（1200 码→0 行，100 码→81 行）',
            'get_market_concept_*_east': '东财，0 行',
        },
        'checked_at': dt.date.today().strftime('%Y-%m-%d'),
    }
