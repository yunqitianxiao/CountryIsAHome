# -*- coding: utf-8 -*-
"""模拟投资游戏的后端：**虚拟本金 + 真行情**。

★这个模块**不进大模型工具表**（`tools` 的 `REGISTRY` / `SCHEMAS` 一行不动）：
  大模型不替玩家下单。行情是给玩家看的，不是给模型当工具调用的。
  这条是**故意**的 —— 一个能自己下单的模型，和一个"建议你买"的模型，
  在出错的后果上不是一回事。

★唯一一条硬纪律（用户点名要的）：**抓不到行情就明确报错，绝不编数字**。
  ⇒ 失败时 `summary` 的键**只有** `{ok, error, msg, hint}` 四个，`data` 是 `None`。
    不返回 0、不把 `None` 当占位符、不给一个"默认收益率"。
    一个编出来的 0% 收益和一个真实的 0% 收益在页面上长得一模一样，
    而前者会让玩家以为"我保本了"。

★本金与画像**无关**：虚拟本金是这一局里用户自己填的数，服务端**零用户状态**，
  `quote` 只认请求里那个 `amount`。画像里的 `assets` 是"你有多少钱"，
  虚拟本金是"这局你想拿多少练手"，两者没有任何理由相等。
"""

import datetime as dt
import math

import pandas as pd

import feed

# 一局的最小/最大本金。上下限都有理由：
#   · 下限 100：再小的话"份额 = 本金 / 股价"会小到 0.0001 股，盈亏四舍五入后
#     永远是 0.00，玩家看到的是一张不动的表；
#   · 上限 1 亿：不是风控，是**挡住手滑多打几个 0**（输入框里 1000000000 看起来
#     和 100000000 差不多）。超过就明确报错，不是悄悄截断。
MIN_STAKE, MAX_STAKE = 100.0, 100000000.0

# 一回最多推荐几只。超过 8 只之后列表就没人看了。
MAX_REC = 8

# ── 兴趣 → 标的池 ──────────────────────────────────────────────────────
#
# ★这张表的纪律：**只描述，不预测**。
#   写"跟踪沪深300的场内 ETF"可以；写"长期看好"、"稳健增值"、"适合新手"
#   都不行 —— 那是投资建议，而这一页正在教一个新手"赚了多少"这件事本身。
#
# ★取数层的市场只有 cn_stock / cn_index / cn_etf / hk_stock / us_stock / crypto。
#   所以"银行理财""存款""保险""外汇"这几类**本来就没有日 K** ——
#   它们不该被硬凑成一只股票假装有行情，而该**明说自己取不到**，
#   给不了的就在 `unmapped` 里说清楚（见 `recommend`）。
POOL = {
    '基金': [
        ('510300', '场内沪深300ETF', '跟踪沪深300，宽基里最常被拿来当基准的一只'),
        ('510500', '场内中证500ETF', '跟踪中证500，成分股比沪深300小一号'),
    ],
    '股票': [
        ('600519', '贵州茅台', 'A 股里被讨论得最多的一只，波动也大'),
        ('000858', '五粮液', '同一行业里的另一只，可以拿来对比'),
        ('00700', '腾讯控股', '港股，注意它和 A 股的交易时间不一样'),
    ],
    '债券': [
        ('511010', '国债ETF', '场内国债 ETF，波动通常比股票小得多'),
    ],
    '可转债': [
        ('511380', '可转债ETF', '一篮子可转债，不用自己去挑单只'),
    ],
    'ETF/指数': [
        ('510300', '场内沪深300ETF', '宽基，最常用的那个基准'),
        ('sz399006', '创业板指', '成分股偏成长，波动比沪深300大'),
        ('sh000905', '中证500指数', '中盘股，指数的点位不是能买卖的价格'),
    ],
    '黄金': [
        ('518880', '黄金ETF', '跟踪国内金价，和股票的相关性通常较低'),
    ],
    '养老/教育金': [
        ('511010', '国债ETF', '这类钱通常看的是"多久以后要用"，日 K 上只能挑到债券和宽基'),
        ('510300', '场内沪深300ETF', '同上，长期配置里最常见的那只宽基'),
    ],
}

# 有这类兴趣、但本站**真的取不到**日 K 的，理由写在这里。
# ★"取不到"必须说出来，不能拿别的类别硬凑一只充数 —— 那等于编。
NO_SOURCE = {
    '银行理财': '银行理财没有公开的日 K 接口，本站取数层拿不到它的净值',
    '存款': '存款利率不是行情，没有日 K 可看',
    '保险': '保险的现金价值不是行情，没有日 K 可看',
    '外汇': '本站取数层没有外汇源',
    '其他': '这一类没有对应的取数源',
}


def _money(x):
    """金额一律两位小数。**不用 `round` 之外的手段**：这里不是做账，
    只是别让页面上出现 1234.5600000000002 这种数。"""
    return round(float(x) + 0.0, 2)


def _stake(v):
    """把用户填的"这一局拿多少"变成 float；不合法就说清楚哪里不合法。

    ★`bool` 要**单独挡掉**：`isinstance(True, int)` 是 `True`，
      不挡的话 `{"amount": true}` 会变成 1 元本金，悄没声地跑通。
    ★`nan` / `inf` 也要挡：它们能通过所有的 `> 0` 比较，最后渲染成
      `NaN` 一路传到页面上。JSON 里 `NaN` 不是合法字面量，但 Python 的
      `json` 默认**会**把它写出来 —— 坏得静悄悄。
    """
    if isinstance(v, bool) or v is None:
        raise ValueError('本金要填一个数字')
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError('本金要填一个数字，「%s」不是' % (v,))
    if math.isnan(x) or math.isinf(x):
        raise ValueError('本金要是实实在在的数')
    if x <= 0:
        raise ValueError('本金要大于 0')
    if x < MIN_STAKE:
        raise ValueError('本金太小了（至少 %.0f 元）—— 太小的话份额会小到盈亏永远是 0.00' % MIN_STAKE)
    if x > MAX_STAKE:
        raise ValueError('本金超上限了（最多 %.0f 元）—— 这里拦住多半是手滑多打了几个 0' % MAX_STAKE)
    return x


def _day(s):
    """`YYYY-MM-DD` → date。认不出就说认不出。"""
    if s in (None, ''):
        return dt.date.today()
    if not isinstance(s, str):
        raise ValueError('日期要写成 2026-01-31 这样')
    try:
        return dt.datetime.strptime(s.strip(), '%Y-%m-%d').date()
    except ValueError:
        raise ValueError('日期看不懂：「%s」，要写成 2026-01-31 这样' % s)


def _days_needed(buy, today):
    """要盖住 `buy` 这一天，得往回取多少根**交易日**。

    交易日 ≈ 自然日 × 5/7，再留 10 根余量吃节假日。上限 1200 是取数层自己的上限
    （`feed.fetch` 会夹到 [60,1200]）—— 超过它的老日期**取不到就是取不到**，
    由 `NO_HISTORY` 明说，不许假装算得出来。
    """
    span = (today - buy).days
    if span <= 0:
        return 60
    return max(60, min(int(span * 5 / 7) + 10, 1200))


def _label(code, fallback):
    """ETF / 指数这些 `feed.resolve` 认得出代码、但报不出名字的，用池子里的说法补上。

    ★只在**两边对得上**时才用：`resolve` 给了真名字（比如 600519 → 贵州茅台）
      就一律以它为准。这里的名字是人写的描述，只该在"页面上本来要显示一个
      光秃秃的代码"时出场 —— 那种时候补一个准确的说法比留一个 `510300` 好读。
    """
    for rows in POOL.values():
        for sym, name, _why in rows:
            if sym == code:
                return name if fallback in ('', code) else fallback
    return fallback


def quote(symbol, amount, buy_date=None):
    """按真实历史行情算一次"如果那天买了，到现在赚多少"。

    ★买入价取 **buy_date 当日或之后第一根日 K 的收盘价**，并把**实际用的那一天**
      回给调用方（`buy_date_used` + `date_shifted`）。为什么必须回：
      用户选的多半是周末或节假日 —— 悄悄换成另一个日期、页面上却显示他选的那天，
      这一局的数字就再也对不上了。

    ★份额**允许小数**（精确到 4 位）。虚拟盘不要求整手，理由写在页面上：
      要求整手的话，1 万块买 1700 元一股的茅台就一股也买不了。
    """
    try:
        stake = _stake(amount)
    except ValueError as e:
        return _fail('BAD_AMOUNT', str(e), '填个 100 到 1 亿之间的数就行')
    try:
        buy = _day(buy_date)
    except ValueError as e:
        return _fail('BAD_DATE', str(e), '默认是三个月前，也可以自己选一天')

    today = dt.date.today()
    if buy > today:
        return _fail('FUTURE_DATE', '选的那天还没到（%s）' % buy.isoformat(),
                     '只能回看过去 —— 想知道以后，那叫预测，不叫回看')

    r = feed.resolve(symbol)
    if not r.get('ok'):
        return _fail('BAD_SYMBOL', r.get('hint') or '认不出这个标的',
                     '试试 600519 / sh000300 / 510300 / 00700 / AAPL / BTC')
    if r.get('ambiguous'):
        # ★有歧义**必须反问**，不许挑一个默认的解释继续算下去：000001 既可能是
        #   上证指数、也可能是平安银行，而这两条走势完全无关。挑错了，页面会
        #   一本正经地给出一个跟用户心里那只票毫无关系的盈亏。
        return _fail('AMBIGUOUS', '「%s」有两种解释：%s' % (
            symbol, '、'.join(c['name'] for c in (r.get('candidates') or []))),
            '写清楚一点再试：sh000001 是上证指数，sz000001 是平安银行')

    need = _days_needed(buy, today)
    try:
        df = feed.fetch(r['market'], r['code'], days=need)
    except feed.SourceError as e:
        return _fail(e.code, str(e), getattr(e, 'hint', '') or '行情源这会儿取不到，过一会儿再试')
    except Exception as e:                       # noqa: BLE001
        return _fail(type(e).__name__, str(e)[:200], '行情源这会儿取不到，过一会儿再试')

    if df is None or len(df) < 2:
        return _fail('NO_DATA', '取回来的行情不够算（%d 根）'
                     % (0 if df is None else len(df)), '换一只标的试试')

    first_dt = df['dt'].iloc[0]
    first_day = first_dt.date() if hasattr(first_dt, 'date') else first_dt
    if buy < first_day:
        # 最早的日期是**日期**不是收益数字，说出来是为了让用户知道该往前挪多少。
        return _fail('NO_HISTORY',
                     '这天太早了：这只标的的行情只回到 %s' % first_day.isoformat(),
                     '往前最近能选 %s；再早的话本站取数层最多回看 1200 个交易日'
                     % first_day.isoformat())

    hit = df[df['dt'] >= pd.Timestamp(buy)]
    if hit.empty:
        # ★这个分支就是"今天建仓"那条路：`buy` 落在最后一根日 K **之后**。
        #   今天还没收盘、今天是周末、或者源那边今天的日 K 还没出来 —— 都会走到这里。
        #   这时按**最近一根**算，并且 `date_shifted` 会是 True（见下），
        #   页面上要如实写"用的是 X 月 X 日那天的收盘价"。
        #   ★不许在这里报 NO_HISTORY：那样"今天建仓"这个玩法在最常见的时刻
        #     （盘中、周末）直接不能用了，而用户看到的是"取不到行情"。
        row = df.iloc[-1]
    else:
        row = hit.iloc[0]
    last = df.iloc[-1]

    buy_price = float(row['close'])
    last_close = float(last['close'])
    if buy_price <= 0 or last_close <= 0:
        return _fail('BAD_PRICE', '取回来的价格不是正数', '这多半是源那边的数据有问题，换一只试试')

    shares = stake / buy_price
    value = shares * last_close
    pnl = value - stake
    used = _day_of(row)

    return {
        'summary': {
            'ok': True,
            'symbol': str(symbol), 'name': _label(r['code'], r['name']),
            'code': r['code'],
            'market': r['market'],
            'amount': _money(stake),
            'buy_date': used,
            'date_shifted': used != buy.isoformat(),
            'buy_price': round(buy_price, 4),
            'last_date': _day_of(last),
            'last_close': round(last_close, 4),
            'shares': round(shares, 4),
            'value': _money(value),
            'pnl': _money(pnl),
            'pnl_pct': round(pnl / stake * 100, 2),
            'bars': int(len(df)),
            'source': df.attrs.get('source') or '?',
        },
        'data': None,
    }


def _day_of(row):
    x = row['dt']
    return (x.date() if hasattr(x, 'date') else x).isoformat()


def _fail(code, msg, hint=''):
    """失败的**唯一**出口。

    ★键只有四个、`data` 是 `None` —— 这不是节省，是**判据的形状**：
      只要这里多一个 `pnl_pct: 0` 之类的"方便字段"，页面上就会出现一个
      用户会当真的数字（"我没赚没赔"），而真实情况是**我们不知道**。
      判据 `★取数失败时一个收益数字都不许出现` 就是照着这个形状写的：
      它断言 `summary` 的键集**恰好**是这四个、且 `data is None`。
    """
    return {'summary': {'ok': False, 'error': code, 'msg': msg, 'hint': hint},
            'data': None}


def recommend(interests):
    """按投资兴趣给一份**建议列表**。

    ★只推荐，**不限制**：这份列表只是"不知道从哪下手时点一下"，
      页面上的输入框照样能搜任意标的（判据里专门有一条：不在推荐列表里的
      标的一样报得出价）。

    ★取不到的那些类别进 `unmapped`，**不拿别的类别硬凑**：
      给一个选了"存款"的人推一只股票，等于告诉他"存款就是这个"。
    """
    if isinstance(interests, str):
        interests = [interests]
    if not isinstance(interests, list):
        interests = []

    items, seen, unmapped = [], set(), []
    for raw in interests:
        name = str(raw or '').strip()
        if not name:
            continue
        if name in NO_SOURCE:
            unmapped.append({'interest': name, 'why': NO_SOURCE[name]})
            continue
        for sym, label, why in POOL.get(name, []):
            if sym in seen:
                continue
            seen.add(sym)
            items.append({'symbol': sym, 'name': label, 'why': why, 'interest': name})
    items = items[:MAX_REC]

    if not interests:
        note = '还没填投资兴趣 —— 填了之后这里会按兴趣挑几只；现在也能直接在上面搜标的。'
    elif not items:
        note = '你选的这几类本站都取不到日 K（理由见下）。上面那个框可以直接搜标的，比如 600519。'
    else:
        # ★这段是**纯文本**（页面上直接 `{note}` 渲染，不走 markdown）——
        #   写 `**粗体**` 的话用户看到的是四个星号。
        note = '这几只是按你填的兴趣挑的常见标的，不是推荐你买，也不代表会涨。'

    return {
        'summary': {'ok': True, 'count': len(items), 'unmapped_count': len(unmapped), 'note': note},
        # ★列表放 `data`、标量放 `summary`：和站里另外两条通道的规矩一致
        #   （`summary` 是能进上下文的标量，`data` 是发前端画东西的）。
        'data': {'items': items, 'unmapped': unmapped},
    }
