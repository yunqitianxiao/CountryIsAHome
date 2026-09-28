"""akshare 1.18.88 —— 链条最后一环，**只留还活着的那几条**。

## 实测事实（本机 Linux 6.8.0-138 / Python 3.10.12 / akshare 1.18.88，2026-09-22）

### 活着的（★全部是**新浪后端**的）
| 函数 | 实测 | 返回列**逐字** |
|---|---|---|
| `stock_zh_a_daily(symbol='sh600519', adjust='qfq')` | **815ms / 6010 行** | `date, open, high, low, close, volume, amount, outstanding_share, turnover` |
| `stock_zh_index_daily(symbol='sh000300')` | **241ms / 5998 行** | `date, open, high, low, close, volume` |
| `fund_etf_hist_sina(symbol='sh510300')` | **268ms / 3482 行** | `date, open, high, low, close, volume, amount, postVol, postAmt` |
| `stock_hk_daily(symbol='00700')` | **327ms / 5477 行** | `date, open, high, low, close, volume, amount` |
| `stock_us_daily(symbol='AAPL')` | **462ms / 10036 行** | `date, open, high, low, close, volume` ← **没有成交额** |
| `tool_trade_date_hist_sina()` | **153ms / 8797 行** | `trade_date` |
| `stock_board_concept_name_ths()` | **7108ms / 375 行** | `name, code` |
| `stock_info_a_code_name()` | **22564ms / 5567 行** | `code, name` |

### 死掉的（★东财，全部 `ConnectionError: RemoteDisconnected`）
`stock_zh_a_spot_em`（5.1s 后断）、`stock_zh_a_hist`（0.2s 断）、`stock_hk_spot_em`（5.4s 断）、
`stock_us_spot_em`（5.7s 断）。与协调者的可达性矩阵一致：**东财 API 主机整片不可达**。

### 版本差异
- `stock_board_concept_cons_ths` 在 1.18.88 **不存在**（`AttributeError`）。
  同花顺概念成分股要走 `ths.py` 直接抓，或 `adata.concept_constituent_ths`。

## 这一环为什么必须留着（不许删）
1. **它是本机最深的日线**：A股 6010 根（腾讯 641、新浪 `scale` 1023、同花顺 140）。
   `days > 1023` 的请求只有它能满足。
2. **它有成交额**，而腾讯/新浪 `scale` 都没有（沪指/ETF/港股/美股的成交额列）。
3. **它是唯一带复权参数**的兜底（`adjust='qfq'/'hfq'/''`），
   而 `feed.py` 那条链就靠它。
4. `tool_trade_date_hist_sina()` 的 **8797 行**比 adata 的 365 行长得多 ——
   要算跨年分位数/波动率时，短日历会让"5 年前"这种窗口直接取不到。

## 纪律
akshare 内部同样是裸 `requests`，**没有 timeout**（实测 `stock_info_a_code_name`
要 22.6 秒，网络一抖就是永久挂住）。所以每个调用都过 `_base.call` 包超时。
"""

import pandas as pd

from . import _base

SRC = 'akshare'


def _ak():
    """拿 akshare。装不上就抛 —— 它是可选依赖，不能拖垮整个包。"""
    try:
        import akshare as ak                     # noqa: PLC0415
        return ak
    except Exception as e:                       # noqa: BLE001
        raise _base.SourceError('NO_AKSHARE', 'akshare 导不进来：%s: %s'
                                % (type(e).__name__, e)) from None


def prefix(code, market):
    """新浪风格前缀。★`920xxx` 是北交所（`bj`）不是沪市 —— 按首位 `9→sh` 会错。"""
    c = str(code).strip()
    if market == 'cn_index':
        return ('sh' if c.startswith(('000', '88', '99')) else 'sz') + c
    if c.startswith(('43', '83', '87', '88', '92')):
        return 'bj' + c
    if c.startswith('900'):
        return 'sh' + c
    if c.startswith(('15', '16', '18')):
        return 'sz' + c
    if c.startswith(('50', '51', '52', '56', '58')):
        return 'sh' + c
    if c[:1] in ('6', '9'):
        return 'sh' + c
    return 'sz' + c


def kline_daily(market, code, days=400, adjust='qfq', timeout=60):
    """日 K → CANON 帧。按市场分派到 akshare 里**还活着**的那条。

    ★`adjust` 只在 A股/港股上有意义：`stock_zh_index_daily`、`fund_etf_hist_sina`
      和 `stock_us_daily` **没有复权参数**（指数和 ETF 本来就不复权，
      美股那条 akshare 给的就是原始价）。所以传 `adjust` 给它们会 `TypeError` ——
      这不是 bug，是"这个市场没有复权概念"，直接不传。
    ★返回的列名**不硬编码**：四条的列名各不一样（见模块头），统一交给
      `feed.to_canon()` 去映射（它就是干这个的），这里只把 `date` 改名成 `日期`
      让 `to_canon` 认得出时间列（原因见 `__init__.feed_frame` 的注释）。
    """
    c = str(code).strip()
    m = str(market)

    def go():
        ak = _ak()
        if m == 'cn_stock':
            return ak.stock_zh_a_daily(symbol=prefix(c, m), adjust=adjust)
        if m == 'cn_index':
            return ak.stock_zh_index_daily(symbol=prefix(c, m))
        if m == 'cn_etf':
            return ak.fund_etf_hist_sina(symbol=prefix(c, m))
        if m == 'hk_stock':
            return ak.stock_hk_daily(symbol=c, adjust=adjust)
        if m == 'us_stock':
            return ak.stock_us_daily(symbol=c, adjust=adjust)
        raise _base.SourceError('BAD_MARKET', 'akshare 源不支持 %s' % m)

    df = _base.timed('%s.kline.%s' % (SRC, m), go, timeout=timeout,
                     label='akshare.kline.%s' % c)
    if not isinstance(df, pd.DataFrame) or not len(df):
        raise _base.SourceError('EMPTY', 'akshare %s/%s 回了空表' % (m, c))
    df = df.copy()
    if 'date' in df.columns:
        df = df.rename(columns={'date': '日期'})
    elif 'trade_date' in df.columns:
        df = df.rename(columns={'trade_date': '日期'})
    df.attrs['source'] = '%s.kline.%s' % (SRC, m)
    if 'amount' not in df.columns:
        # ★美股那条没有成交额。**置 NaN 不置 0**（与本层其它地方一致）
        df['amount'] = float('nan')
        df.attrs['amount_missing'] = True
    return df


def board_list_ths(timeout=60):
    """同花顺概念清单 → `[{'code','name'}]`。实测 7.1s / **375 行**。

    ★与 `ths.board_list`（直抓 361 个）和腾讯（803 个）**三个数都不一样**，
      这是口径差异不是 bug，如实交出去。375 是 akshare 认到的同花顺概念数。
    ★这个函数会往 **stderr 打 tqdm 进度条**（39 次请求），日志里会有一串噪声，
      不是错误。
    """
    def go():
        ak = _ak()
        df = ak.stock_board_concept_name_ths()
        if not isinstance(df, pd.DataFrame) or not len(df):
            raise _base.SourceError('EMPTY', 'akshare stock_board_concept_name_ths 回了空表')
        return df
    df = _base.timed('%s.boards.ths' % SRC, go, timeout=timeout, label='akshare.boards')
    ccol = 'code' if 'code' in df.columns else df.columns[0]
    ncol = 'name' if 'name' in df.columns else df.columns[-1]
    return [{'code': str(r.get(ccol)).strip(), 'name': str(r.get(ncol)).strip()}
            for _, r in df.iterrows() if str(r.get(ccol)).strip()]


def code_list(timeout=60):
    """A股全代码表 → `[{'code','name'}]`。实测 **22.6s / 5567 行**（慢，别频繁调）。

    ★5567 vs 新浪 `getHQNodeStockCount` 的 5566 —— 差 1 是正常的（含退市/停牌口径），
      不要"修"成一样。
    """
    def go():
        ak = _ak()
        df = ak.stock_info_a_code_name()
        if not isinstance(df, pd.DataFrame) or not len(df):
            raise _base.SourceError('EMPTY', 'akshare stock_info_a_code_name 回了空表')
        return df
    df = _base.timed('%s.code_list' % SRC, go, timeout=timeout, label='akshare.code_list')
    return [{'code': str(r.get('code')).strip().zfill(6),
             'name': str(r.get('name')).strip()}
            for _, r in df.iterrows() if str(r.get('code')).strip()]


def trade_calendar(days=None, timeout=40):
    """交易日历 → `['YYYY-MM-DD', ...]`（升序）。实测 **153ms / 8797 行**。

    ★★**这里返回全量，不按 `days` 截** —— 截取在 `__init__.trade_calendar`
      的出口做，因为那里还要**砍掉未来日期**（上游日历排到年底）。
      若在这里先 `out[-days:]`，`days=20` 会先切出 20 个**未来**交易日，
      再到出口被砍光 ⇒ 变成"日历空了"。剪切只在一处做。
    ★比 adata 的 365 行长得多 ⇒ 链上把它放在前面：要算跨年窗口时分位数
      取不到 5 年，会静默变成"用 1 年的样本算 5 年分位"。
    """
    def go():
        ak = _ak()
        df = ak.tool_trade_date_hist_sina()
        if not isinstance(df, pd.DataFrame) or not len(df):
            raise _base.SourceError('EMPTY', 'akshare tool_trade_date_hist_sina 回了空表')
        return df
    df = _base.timed('%s.trade_calendar' % SRC, go, timeout=timeout, label='akshare.calendar')
    col = 'trade_date' if 'trade_date' in df.columns else df.columns[0]
    s = pd.to_datetime(df[col], errors='coerce').dropna()
    return sorted({d.strftime('%Y-%m-%d') for d in s})
