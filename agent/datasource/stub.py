"""离线桩：让分钟线和板块在**无网**下也能被确定性地验。

为什么要有它：`agent/feed.py` 那套桩解决的是"行情总览那页的对照判据不能看运气"。
这一层要解决的是同一件事的**新一半** —— 分钟线（`agent/intraday` 的十个策略）和
板块（`agent/linkage` 的关联检测）根本没有真数据可依赖：本机实测（2026-09-22）
A股 1 分钟取不到、港美股分钟上游只回一根聚合日线、东财资金流整条不可达。
判据要是挂在真网络上，"这条判据今天跑不跑得起来"就变成了看运气的事 ——
而一条"有时跑有时不跑"的判据，它守的门就是时开时关的。

★三条硬要求（照抄 `agent/feed.py: _stub_frame` 的纪律，一条都不许松）：

  1. **显式播种。** 用 `random.Random(种子)`，**不用内置 `hash()`** ——
     它对 str 每进程加盐（PYTHONHASHSEED），同一份输入在两个进程里给出两段
     完全不同的行情，于是"判据自己打接口拿到的数"和"页面上渲染出来的数"
     来自两份帧，对照就变成了拿 A 比 B，绿了也说明不了任何事。
  2. **同参同帧。** 种子必须**带上全部参数**（标的 + 市场 + 周期 + 根数）；
     不带标的的话每个代码发同一段行情，页面看着"有数据"其实什么也没在比。
  3. **只在服务端设了 `LICAI_FEED_STUB` 时生效，不接受客户端开关。**
     一个"客户端能打开的桩"就是一台伪造行情的机器。

桩文件格式（JSON，路径由 `LICAI_FEED_STUB` 给出；`=1` 表示用全默认值）：

    {"seed": 7, "base": 100.0, "amp": 0.012, "days": 400,
     "fail": ["600519", "BTC"],          # 点名让这些**代码**失败（验逐标的降级）
     "source": "STUB",                   # 写进 attrs['source']
     "boards": 12,                       # /boards 造多少个板块
     "realtime": 30,                     # /realtime 造多少行
     "asof": "2026-09-22 15:00:00"}      # 可选：钉死 ts；不写=今天 15:00:00（见 _asof）

★`LICAI_FEED_STUB`、`_stub_frame` 的读取语义与 `feed.py` **共用同一个文件**：
  同一个桩文件既能驱动行情总览，也能驱动这一层，不需要维护两份夹具。
"""

import datetime as dt
import json
import os
import random
import re
import zlib

import pandas as pd

from . import _base

CANON = _base.CANON

# A股一天的分钟时段（含 11:30 / 15:00 收盘那根）：09:30-11:30、13:00-15:00
_CN_SESSIONS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60))


def spec():
    """读桩文件。读不到/不是 JSON 就给空 dict（= 全默认值），**不报错** ——
    桩文件坏了应该表现为"桩还在、只是用了默认参数"，而不是把调用方炸掉。"""
    path = os.environ.get(_base.STUB_ENV) or ''
    if path and path != '1' and os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f) or {}
        except (OSError, ValueError):
            return {}
    return {}


def is_failed(code, sp=None):
    """桩里点名让这个代码失败？用于验"一个标的取不到、只坏那一行"。

    ★这条失败要**从取数层真地抛出去**：上层那半的降级逻辑要真的走到 except
      分支才算验过，只在页面上摆一个错误样子不算。
    """
    sp = spec() if sp is None else sp
    return bool(code) and code in (sp.get('fail') or [])


def _seed(sp, key):
    """★`zlib.crc32`，不是 `hash()` —— 理由见模块头第 1 条。
    跨进程/跨机器稳定，判据和页面才能对上同一份帧。"""
    return int(sp.get('seed', 7)) + zlib.crc32(
        ('%s|%s' % (sp.get('key', ''), key)).encode('utf-8'))


# 桩的 `ts` 钉在这个钟点（A股收盘）。选一个**像样的时刻**而不是 00:00:00，
# 是因为前端会把 ts 直接格式化上屏；固定值一眼看着像行情时间，不会引出
# "这时间怎么是零点"的误报。
_ASOF_CLOCK = '15:00:00'


def _asof(sp):
    """行情/板块桩里那条 `ts`。

    ★★**不许用 `dt.datetime.now()`。** 这条是本文件唯一踩过的坑，记在这：
    我原本写的是 `dt.datetime.now().strftime(...)`，而 `boards_frame` 用的是
    **分钟**粒度、`realtime_frame` 用的是**秒**粒度。后果不是"稍微不稳"，
    是**判据的对照直接失效**：同参两次调用拿到两条不同的 `ts`，
    "同一份帧"这条硬要求（模块头第 2 条）当场破了。
    ★而且它**分两种面孔出现，很容易只抓到一半**：
      - `realtime`（秒粒度）—— 必定复现，两个进程隔一秒就不一样；
      - `boards`（分钟粒度）—— **潜伏**，同一分钟内的两次调用完全一致，
        于是"本机随手一测是绿的"，跨了分钟才红。
    ⇒ 判据必须**跨分钟/跨秒**各测一次，只测同分钟会把潜伏的那条放行。

    取值顺序：夹具点名的 `asof` 原样用（想要跨天逐字节稳定的判据就靠它）；
    没点名则**今天 + 固定钟点** —— 与 `feed._stub_frame`（`end or
    dt.date.today()`）、本文件的 `minute_frame` / `calendar_frame` 是同一个
    **日粒度**纪律：同一天内同参同帧，跨天才变，且跨天是"日期变了"这种
    谁都看得见的变化，不是同一份输入给出两段行情。
    """
    a = sp.get('asof')
    if isinstance(a, str) and a.strip():
        return a.strip()
    return '%s %s' % (dt.date.today().strftime('%Y-%m-%d'), _ASOF_CLOCK)


def _ohlc(rnd, prev, amp, vol_base):
    o = prev
    c = round(o * (1 + rnd.uniform(-amp, amp)), 4)
    hi = round(max(o, c) * (1 + abs(rnd.uniform(0, amp / 2))), 4)
    lo = round(min(o, c) * (1 - abs(rnd.uniform(0, amp / 2))), 4)
    return o, hi, lo, c, round(vol_base * (1 + rnd.uniform(-0.5, 1.5)), 0)


def frame(days=400, end=None, key=''):
    """日 K 的桩。**直接委托 `feed._stub_frame`** —— 行情总览那一页和这一层
    用同一份帧才叫"同一份真源"；在这里重写一遍，两边早晚会漂。"""
    end = end or dt.date.today()
    return _base.feed._stub_frame(max(60, int(days)), end, key=key)


def minute_frame(period=5, days=5, end=None, key='', market='cn_stock'):
    """分钟 K 的桩。**交易日 + 时段**都照真实交易时间造（不是均匀撒点）：
    策略里有时段效应那一类（开盘 30 分 / 午盘 / 尾盘），
    如果桩把 bar 均匀铺在 24 小时上，那种策略在桩下测出来的东西是假的。
    """
    sp = spec()
    period = max(1, int(period))
    days = max(1, min(int(days or 5), 30))
    amp = float(sp.get('amp', 0.012)) * (period / 240.0) ** 0.5     # 周期越短波动越小
    vol_base = float(sp.get('vol', 100000.0))
    end_ts = pd.Timestamp(end or dt.date.today()).normalize()

    dates = pd.bdate_range(end=end_ts, periods=days)
    rnd = random.Random(_seed(sp, 'min|%s|%s|%s|%s' % (market, period, days, key)))
    rows, prev = [], float(sp.get('base', 100.0))
    for d in dates:                              # 顺序：日 → 时段 → bar
        for start, stop in _CN_SESSIONS:
            t = d + pd.Timedelta(minutes=start)
            stop_t = d + pd.Timedelta(minutes=stop)
            while t <= stop_t:                   # ★含收盘那一根：<= 不是 <
                o, hi, lo, c, v = _ohlc(rnd, prev, amp, vol_base)
                rows.append({'dt': t, 'open': o, 'high': hi, 'low': lo, 'close': c,
                             'volume': v, 'amount': round(v * c, 2)})
                prev = c
                t += pd.Timedelta(minutes=period)
    df = pd.DataFrame(rows, columns=CANON)
    df.attrs['source'] = sp.get('source', 'STUB')
    df.attrs['period'] = period
    # 只要最后 days 个交易日，但**截法按天**：直接 tail(n) 会把最后一天的上午砍掉
    keep = {d for d in dates[-days:]} if days < len(dates) else set(dates)
    df = df[df['dt'].dt.normalize().isin(keep)].reset_index(drop=True)
    return df


def boards_frame(kind='concept'):
    """板块清单的桩。★每条都带 `lead_code`/`lead_name`，因为板块卡片上要显示领涨股，
    桩里不给的话，那半边的渲染逻辑在离线判据下就永远是空跑。"""
    sp = spec()
    n = int(sp.get('boards', 12))
    rnd = random.Random(_seed(sp, 'boards|%s' % kind))
    now = _asof(sp)[:16]             # 分钟粒度：'%Y-%m-%d %H:%M'（见 _asof）
    out = []
    for i in range(n):
        pre = 'BK' if kind == 'concept' else 'pt'
        out.append({
            'code': '%s%04d' % (pre, 1000 + i),
            'name': '%s板块%02d' % ('概念' if kind == 'concept' else '行业', i + 1),
            'kind': kind,
            'pct': round(rnd.uniform(-4, 6), 2),
            'amount': round(rnd.uniform(1e8, 5e10), 0),
            'vol_ratio': round(rnd.uniform(0.5, 3.0), 2),
            'lead_code': '%06d' % (600000 + (i * 7) % 3000),
            'lead_name': '领涨股%02d' % (i + 1),
            'ts': now,
        })
    return out


def members_frame(board_code, kind='concept'):
    """板块成分股的桩。数量也由板块代码决定（同参同帧），不是固定的。"""
    sp = spec()
    rnd = random.Random(_seed(sp, 'members|%s|%s' % (kind, board_code)))
    n = rnd.randint(5, 20)
    return ['%06d' % (600000 + rnd.randrange(0, 3000)) for _ in range(n)]


def realtime_frame(market='cn', limit=None):
    """全市场实时的桩。字段与 `realtime()` 的契约逐字一致 ——
    **缺值是 None 不是 0**，桩也必须守这一条，否则"缺值变 0"那条注入在桩下验不出来。"""
    sp = spec()
    n = int(limit if limit is not None else sp.get('realtime', 30))
    prefix = {'cn': 'sh', 'hk': 'hk', 'us': 'us', 'crypto': ''}.get(market, '')
    rnd = random.Random(_seed(sp, 'rt|%s|%d' % (market, n)))
    now = _asof(sp)                  # 秒粒度：'%Y-%m-%d %H:%M:%S'（见 _asof）
    out = []
    for i in range(n):
        base = 10.0 + rnd.uniform(1, 500)
        pct = round(rnd.uniform(-8, 8), 2)
        price = round(base * (1 + pct / 100.0), 3)
        out.append({
            'code': '%s%06d' % (prefix, 600000 + i) if market != 'crypto'
                    else ['BTC', 'ETH', 'SOL', 'BNB'][i % 4] + str(i),
            'name': '桩标的%03d' % i, 'market': market,
            'price': price, 'open': round(base, 3), 'high': round(base * 1.02, 3),
            'low': round(base * 0.98, 3), 'prev_close': round(base, 3),
            'pct': pct, 'volume': float(rnd.randrange(1000, 10 ** 7)),
            'amount': float(rnd.randrange(10 ** 5, 10 ** 9)),
            # ★故意留 None：桩是判据的输入，"某一列缺失"这件事必须在桩里就存在，
            #   否则"缺值变 null"那条判据在桩下永远只看到有值的行。
            'turnover': None if i % 7 == 0 else round(rnd.uniform(0.1, 12), 2),
            'pe': None if i % 5 == 0 else round(rnd.uniform(-30, 90), 2),
            'ts': now,
        })
    return out


def calendar_frame(days=365, end=None):
    """交易日的桩：工作日即交易日（不考虑节假日 —— 桩的职责是确定性，
    真日历由 `trade_calendar()` 从 adata 拿）。"""
    end = pd.Timestamp(end or dt.date.today()).normalize()
    return [d.strftime('%Y-%m-%d') for d in pd.bdate_range(end=end, periods=int(days))]


# ── 指定标的快照（★按请求的代码逐个作答）───────────────────────────────

# 桩**服务得了**哪些 (市场, 代码)：按市场的**代码形态**判。
# ★为什么不查真名单：桩是离线的，它手里没有任何真实名单可查 —— 查不了就不许装。
#   所以判据只能是"这个写法在本市场的代码空间里**形态合法**"。
# ★**没登记的 market 一律不服务**（进 `missing`），例如 `crypto`：`snapshot` 那条链
#   是腾讯/新浪批量，两家都没有 crypto 那一段（`tencent.tcode('crypto', …)` 会落到
#   A股分支去拼出一个不存在的前缀）⇒ 真跑起来必然是缺条。**桩要是替它编一行出来，
#   就是在给一个本机做不到的能力发绿灯** —— 那正是这个文件要防的那类假绿。
# ★这条口径的**边界**要写明：形状合法 ≠ 真的有这只票。所以桩下的
#   `snapshot(['600519'])` 有数据，**不能**据此说"600519 存在"。
_SERVED_SHAPE = {
    'cn_stock': re.compile(r'^\d{6}$'),
    'cn_index': re.compile(r'^\d{6}$'),
    'cn_etf': re.compile(r'^\d{6}$'),
    'hk_stock': re.compile(r'^\d{5}$'),
    'us_stock': re.compile(r'^[A-Z][A-Z0-9.\-]{0,9}$'),
}


def can_serve(market, code):
    """桩能不能给这个 (市场, 代码) 造一行？见 `_SERVED_SHAPE` 的口径与边界。"""
    rx = _SERVED_SHAPE.get(str(market or ''))
    return bool(rx) and bool(rx.match(str(code or '').strip()))


def _snap_row(sp, market, code, i):
    """快照一行。字段与 `realtime_frame` **逐字同构**（同一个合成口径，不引第二种风格）。

    ★种子**带上标的**（模块头第 2 条）：不带的话每个代码发同一段行情，
      "按代码对齐"这条判据在桩下就永远只看到一堆完全相同的行。
    """
    rnd = random.Random(_seed(sp, 'snap|%s|%s' % (market, code)))
    base = round(float(sp.get('base', 100.0)) * (1 + rnd.uniform(0, 4)), 3)
    pct = round(rnd.uniform(-8, 8), 2)
    return {
        # ★`code` 是**请求里那个代码本身**（照 `tencent.from_tcode`：真源回的就是裸码），
        #   `market` 是它自己的市场 —— 这两格对不上就是"拿别的标的顶替"。
        'code': str(code), 'name': '桩标的%03d' % i, 'market': market,
        'price': round(base * (1 + pct / 100.0), 3), 'open': base,
        'high': round(base * 1.02, 3), 'low': round(base * 0.98, 3),
        'prev_close': base, 'pct': pct,
        'volume': float(rnd.randrange(1000, 10 ** 7)),
        'amount': float(rnd.randrange(10 ** 5, 10 ** 9)),
        # ★故意留 None（同 `realtime_frame`）：桩是判据的输入，"某一列缺失"这件事
        #   必须在桩里就存在，否则"缺值变 null"那条判据在桩下只看到有值的行。
        'turnover': None if i % 7 == 0 else round(rnd.uniform(0.1, 12), 2),
        'pe': None if i % 5 == 0 else round(rnd.uniform(-30, 90), 2),
        'ts': _asof(sp),
    }


def snapshot_frame(pairs):
    """指定标的快照的桩 → `(rows, missing)`。`pairs` 是 `[(market, code), …]`。

    ★★**这条函数存在的唯一理由是"按请求的代码逐个作答"**，所以它必须逐条
      **要么给那个代码自己那一行、要么如实进 `missing`**。初版不是这样：
      `__init__.snapshot` 拿 `realtime_frame()` 造一批**通用**代码再按请求过滤，
      滤空了就回落到"造 n 行 A股"。后果（2026-09-22 实测）：
      `snapshot(['600519','000300','00700','AAPL','BTC'])` 回的是
      `['sh600000'…'sh600004']`、`market` 全是 `cn` —— **一个都不是请求的**，
      不报错、不标 `missing`、行数还"正好"对（5 行对 5 个请求）。
      而它是**所有离线判据的输入**：任何"给一组代码、看返回对不对得上代码"的
      判据当时都在拿错数据判绿 —— 正是本模块头警告的那种"看着正常的假数据"。
    ★`missing` 是**上游没回的代码**（与 `tencent.realtime_batch` 同一个口径），
      调用方（`_pad_missing`）会照契约给它补一行全 None。
    """
    sp = spec()
    rows, missing = [], []
    for market, code in (pairs or []):
        if not can_serve(market, code):
            missing.append(str(code).strip())
            continue
        rows.append(_snap_row(sp, market, str(code).strip(), len(rows)))
    return rows, missing
