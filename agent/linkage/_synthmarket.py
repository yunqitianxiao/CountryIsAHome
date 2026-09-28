"""合成市场：在 `agent/datasource/` 还不存在的时候，把 linkage 的**逻辑**跑对。

为什么要有这个文件（两条理由，第二条才是真的理由）：

① 并行施工。`datasource` 是另一个代理的活，本模块按**冻结的接口**（规格 §3.1）写，
   谁也不等谁。这里逐字实现那 7 个函数（realtime/snapshot/boards/board_members/
   stock_boards/daily/minute），外加一个 health()。

② ★**真数据上"检出了异动"这句话是无法证伪的。** "这 5 只今天确实异动了"
   "这两只确实是领先-滞后关系" —— 真相没有人知道，所以一个错得离谱的检测器
   在真数据上照样能输出一份看着很像样的榜单，而没有任何人能说你错。
   合成市场把这两件事变成**可断言**的：异动和领先-滞后都写死在下面的常量里
   （`ANOMALY_SPEC` / `LEADLAG` / `BOARD_SHOCK`），探针拿它们当标准答案。
   ⇒ **改这个文件里的注入表，就等于改"标准答案"**，探针会跟着一起改，别只改一边。

★不要把这个模块当成"数据源坏了时的兜底"。兜底是 datasource 内部三层链的事。
  这里是**桩**，只在显式要求时生效（见 `collect.synth_requested()`）——
  一个"数据源取不到就自动用合成数据顶上"的设计，就是一台印行情的机器，
  而用户看不出屏幕上那个 +9.2% 是假的。那是本项目最不能出的东西。

★确定性：随机数一律 `np.random.RandomState(种子)` 且种子由 `zlib.crc32` 算。
  两条都是为了**可复现**：`hash()` 对 str 每进程加盐（PYTHONHASHSEED），
  同一份输入在两个进程里会给出两段完全不同的行情，那样"对照判据"就变成了
  "拿 A 去比 B"。用 legacy 的 `RandomState` 而不是 `default_rng`：NumPy 对
  `RandomState` 的随机流有**永久兼容承诺**（`Generator` 没有），
  ⇒ 换一台机器/升一次 numpy，同一种子给的是同一段行情。
  （实测/写作机器：numpy 2.2.6 + pandas 2.3.3，2026-09-22。）

★None 语义：规格 §3.1 写着"取不到的字段是 None，**不是 0**"。这里是**故意**
  让一部分票的 pe / turnover 为 None 的 —— 不然上层那条"None 不许当 0 用"的
  代码路径在桩上永远走不到，也就永远没被验过。
"""

import os
import time
import zlib
import datetime as dt

import numpy as np
import pandas as pd

try:
    from .collect import SourceError      # ★同一个类，调用方只需要 catch 一个
except ImportError:                       # 直接 `python agent/linkage/_synthmarket.py` 自检时
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from linkage.collect import SourceError

CANON = ['dt', 'open', 'high', 'low', 'close', 'volume', 'amount']

# ── 因子强度（决定"同板块相关性"落在哪）──────────────────────────────────
# 算一遍给自己看：r = beta_m*F_mkt + beta_b*F_brd + e，beta≈0.95（uniform(0.7,1.2)）
#   Var   ≈ 0.9*0.0090² + 0.9*0.0130² + 0.0095² = 3.15e-4  → 截面 sd ≈ 1.78%
#   Cov(同板块两只) = 0.9*0.0090² + 0.9*0.0130² = 2.25e-4 → corr ≈ 0.71
#   Cov(不同板块)   = 0.9*0.0090²               = 7.29e-5 → corr ≈ 0.23
# ⇒ 0.71 稳稳高于 `relate.HIGH_CORR=0.6`（同板块的会被标出来），
#   0.23 稳稳低于它（不同板块的不会被误标）。这两个数是**调过的**，不是随手写的：
#   调低了"高相关"标签全市场乱飞，调高了合成市场里一个都测不出来。
_SIG_MKT = {'cn': 0.0090, 'hk': 0.0105, 'us': 0.0100}
_SIG_BRD = 0.0130
_SIG_IDIO = 0.0095

_CANON_DAYS = 420            # 所有生成都走这一条**固定的**日期轴（见下 `_dates()`）

# ── 板块定义 ────────────────────────────────────────────────────────────
_CONCEPT = [('BK0500', '白酒'), ('BK0501', '消费50'), ('BK0502', '新能源'), ('BK0503', '光伏'),
            ('BK0504', '半导体'), ('BK0505', '人工智能'), ('BK0506', '医药'), ('BK0507', '券商'),
            ('BK0508', '军工'), ('BK0509', '房地产'), ('BK0510', '汽车'), ('BK0511', '银行')]
_INDUSTRY = [('BK1000', '食品饮料'), ('BK1001', '电力设备'), ('BK1002', '电子'),
             ('BK1003', '医药生物'), ('BK1004', '非银金融'), ('BK1005', '机械设备')]
_CONCEPT_CODES = {b for b, _ in _CONCEPT}
_BOARD_NAME = dict(_CONCEPT + _INDUSTRY)

# ── ★注入清单：探针的标准答案 ────────────────────────────────────────────
# code 是**合成**的，不对应任何真票（600001 在真实市场里早就没有了）——
# 这是刻意的：让人一眼看出这批数据不是真的。
# `pct` 是最后一天**强行设定**的涨跌幅；`vol_x` 是最后一天成交量的倍数。
ANOMALY_SPEC = [
    dict(code='600001', board='BK0500', pct=+9.20, vol_x=6.5, why='放量涨停'),
    dict(code='600002', board='BK0501', pct=+6.40, vol_x=4.2, why='放量上冲'),
    dict(code='000001', board='BK1004', pct=-8.50, vol_x=5.1, why='放量下挫'),
    dict(code='300001', board='BK0504', pct=+5.10, vol_x=3.6, why='温和放量'),
    # 下面两只是一对**领先-滞后**（分钟线上滞后 15 分钟，见 LEADLAG）：
    # 领先股放量最大（8 倍），滞后股放量小一些、涨得晚一些。
    dict(code='600010', board='BK0500', pct=+4.60, vol_x=8.0, why='★领先股'),
    dict(code='600011', board='BK0500', pct=+5.60, vol_x=3.2, why='★滞后股'),
]
# 板块级冲击（最后一天）：让板块**其他成员**也跟着动。
# ★没有这一条的话，异动票是一个人在涨、同板块peer不动 —— 那"高相关"就测不出来，
#   而真实市场里板块异动几乎总是成片的。这是**结构性**的，不是为了让数字好看。
BOARD_SHOCK = {'BK0500': +0.018, 'BK0501': +0.010, 'BK1004': -0.012, 'BK0504': +0.008}

# 领先-滞后：600010 领先 600011 正好 LAG_MIN 分钟。
# ★滞后用**分钟**定义、再按 period 折算成 bar —— 直接写 bar 数的话，
#   同一个"标准答案"在 period=5（3 bar=15min）和 period=15（1 bar=15min）下
#   会变成两个不同的真值，探针的断言就跟着漂了。
LEADLAG = dict(leader='600010', follower='600011', lag_min=15)

_MARKETS = ('cn', 'hk', 'us')
_SESSIONS = {                      # 各市场连续竞价时段（分钟级）
    'cn': [('09:30', '11:30'), ('13:00', '15:00')],
    'hk': [('09:30', '12:00'), ('13:00', '16:00')],
    'us': [('09:30', '16:00')],
}
_N_CN, _N_HK, _N_US = 320, 40, 40


# ── 确定性工具 ──────────────────────────────────────────────────────────

def _seed(*parts):
    """★`zlib.crc32` 而不是内置 `hash()`：后者对 str 每进程加盐（见文件头）。"""
    return int(zlib.crc32('|'.join(str(p) for p in parts).encode('utf-8'))) & 0x7fffffff


def _rng(*parts):
    return np.random.RandomState(_seed(*parts))


# ── 故障注入（验"采集失败要如实进账"，见 collect.Ledger）────────────────
# `LICAI_LINKAGE_SYNTH_FAIL='realtime:hk,daily'` → 这几类调用抛 SourceError。
# 匹配规则是**前缀**：写 `realtime` 就把三个市场的都打掉，写 `realtime:hk` 只打港股。
# ★这是**服务端**环境变量，没有客户端开关（照抄 feed.py: LICAI_FEED_STUB 那条纪律）。
FAIL_ENV = 'LICAI_LINKAGE_SYNTH_FAIL'
SYNTH_ENV = 'LICAI_LINKAGE_SYNTH'


def _fail_list():
    return [s.strip() for s in (os.environ.get(FAIL_ENV) or '').split(',') if s.strip()]


def _maybe_fail(sid):
    for tok in _fail_list():
        if sid.startswith(tok):
            raise SourceError('SYNTH_FAIL', '桩里点名让 %s 失败（%s=%s）' % (sid, FAIL_ENV, tok))


# 调用流水，给 health() 用。★只留最近 200 条 —— 这是个桩，不是账本，
# 真正的账本在 `collect.Ledger`（从调用方那一侧记的，更全）。
_LOG = []


def _log(sid, ok, n, ms, err=None):
    _LOG.append({'id': sid, 'ok': bool(ok), 'n': n, 'ms': round(ms, 1), 'err': err})
    del _LOG[:-200]


def _wrap(sid, fn):
    t0 = time.perf_counter()
    try:
        _maybe_fail(sid)
        v = fn()
    except SourceError as e:
        _log(sid, False, None, (time.perf_counter() - t0) * 1000, str(e)[:120])
        raise
    except Exception as e:                                   # noqa: BLE001
        _log(sid, False, None, (time.perf_counter() - t0) * 1000,
             '%s: %s' % (type(e).__name__, str(e)[:100]))
        raise
    n = len(v) if hasattr(v, '__len__') else None
    _log(sid, True, n, (time.perf_counter() - t0) * 1000)
    return v


# ── 日期轴 ──────────────────────────────────────────────────────────────
# ★**一条固定的日期轴**，不是"每次调用按 days 重新取一段"。
#   板块因子按 (板块, 日期) 索引 —— 日期轴一变，因子就整段错位，
#   于是 `daily(code, days=120)` 和 `daily(other, days=120)` 之间、
#   以及和 `realtime()`（走的是最后一根）之间就**对不上**了。
#   所有生成都走这条轴，`daily(days=k)` 只是 `tail(k)`。
_DATES = None


def _dates():
    global _DATES
    if _DATES is None:
        _DATES = pd.bdate_range(end=pd.Timestamp(dt.date.today()), periods=_CANON_DAYS)
    return _DATES


def _ndays():
    return len(_dates())


# ── 股票池 / 板块成员 ───────────────────────────────────────────────────

_UNIVERSE = None
_MEM = None                  # (code → [板块]), (板块 → [codes])


def _cn_codes():
    out = []
    out += ['%06d' % (600000 + i) for i in range(100)]
    out += ['%06d' % (1 + i) for i in range(80)]
    out += ['%06d' % (300001 + i) for i in range(100)]
    out += ['%06d' % (2001 + i) for i in range(40)]
    return out


def universe():
    """→ {'cn': [...], 'hk': [...], 'us': [...]}。"""
    global _UNIVERSE
    if _UNIVERSE is None:
        _UNIVERSE = {
            'cn': _cn_codes(),
            'hk': ['%05d' % (1 + i) for i in range(_N_HK)],
            'us': ['SY%02d' % i for i in range(_N_US)],
        }
    return _UNIVERSE


def market_of(code):
    """按**代码形状**认市场（合成市场里没有别的信息可用）。认不出抛。"""
    c = str(code).strip().upper()
    if len(c) == 6 and c.isdigit():
        return 'cn'
    if len(c) == 5 and c.isdigit():
        return 'hk'
    if 2 <= len(c) <= 6 and c.isalpha():
        return 'us'
    raise SourceError('UNKNOWN_CODE', '合成市场认不出这个代码：%r' % code)


def memberships():
    """→ (code → [板块代码]), (板块代码 → [codes])。

    ★概念板块**刻意**让一只票最多挂 2 个（真实市场就是这样，也正好对上
      规格里那句证据样例「同属 白酒/消费50 两个概念板块」—— 3 个以上这句就不像话了）。
      行业板块每只票正好 1 个，按轮转分配（320/6 ≈ 53 只一个，均衡）。
    """
    global _MEM
    if _MEM is not None:
        return _MEM
    cn = list(_cn_codes())
    rnd = _rng('members')
    forced = {}
    for s in ANOMALY_SPEC:
        forced.setdefault(s['board'], []).append(s['code'])
    c2b, b2c = {}, {}

    def _add(code, bc):
        c2b.setdefault(code, [])
        if bc not in c2b[code]:
            c2b[code].append(bc)
        b2c.setdefault(bc, [])
        if code not in b2c[bc]:
            b2c[bc].append(code)

    # ★被点名的票**先挂上**，再让随机那一轮去挑 —— 反过来（随机先跑、点名后补）的话
    #   一只票会先在两个随机概念里、再被强行塞进第三个，"一只票最多 2 个概念板块"
    #   这条不变量就被打破了，而 `stock_boards` 的输出会跟着变成一个谁也没预期的形状。
    for bc, mem_codes in forced.items():
        for c in mem_codes:
            _add(c, bc)
    for bc, _nm in _CONCEPT:
        must = forced.get(bc, [])
        pool = [c for c in cn
                if c not in must
                and len([x for x in c2b.get(c, []) if x in _CONCEPT_CODES]) < 2]
        k = min(len(pool), 16 + int(rnd.randint(0, 18)))
        mem = must + list(rnd.choice(pool, size=k, replace=False))
        for c in mem:
            _add(str(c), bc)
    # 兜一网：上面每块只抓了 16~34 只，会有票一个概念板块都没挂上。补一个，
    # 好让"每只票都有概念板块"这条不变量成立（否则 `stock_boards` 会给出空列表，
    # 探针就分不清"这只票真的没有板块"和"生成器漏了"）。
    for c in cn:
        if [x for x in c2b.get(c, []) if x in _CONCEPT_CODES]:
            continue
        _add(c, _CONCEPT[int(rnd.randint(0, len(_CONCEPT)))][0])
    # 行业：轮转均衡分配。★被注入清单点名到某个行业板块的票，要用**它**而不是轮转那个
    #   （不然 000001 会被分到食品饮料，而它的注入冲击挂在非银金融上 —— 冲击就落空了，
    #   "放量下挫"的票照样显示 -8.5%，但它的板块一点没动，看着像 generator 坏了）。
    forced_ind = {s['code']: s['board'] for s in ANOMALY_SPEC if s['board'] in
                  {b for b, _ in _INDUSTRY}}
    for i, c in enumerate(cn):
        _add(c, forced_ind.get(c) or _INDUSTRY[i % len(_INDUSTRY)][0])

    for bc in b2c:
        b2c[bc] = sorted(b2c[bc])
    _MEM = (c2b, b2c)
    return _MEM


# ── 因子 ────────────────────────────────────────────────────────────────
_F_CACHE = {}


def _f_mkt(market):
    k = ('m', market)
    if k not in _F_CACHE:
        _F_CACHE[k] = _rng('mkt', market).normal(0.0, _SIG_MKT.get(market, 0.009), _ndays())
    return _F_CACHE[k]


def _f_brd(bc):
    k = ('b', bc)
    if k not in _F_CACHE:
        _F_CACHE[k] = _rng('brd', bc).normal(0.0, _SIG_BRD, _ndays())
    return _F_CACHE[k]


def _beta(code, tag):
    return float(_rng('beta', code, tag).uniform(0.7, 1.2))


# ── 日线 ────────────────────────────────────────────────────────────────
_DAILY = {}
_SPEC_BY_CODE = {s['code']: s for s in ANOMALY_SPEC}


def _daily_full(market, code):
    """整条日期轴的 OHLCV。★缓存：一次 scan 会对同一只票取好几遍（日线给 RVOL/相关、
    实时快照给截面），缓存让"同一次 scan 里两次读到同一只票"必然是同一份数据。"""
    key = (market, code)
    if key in _DAILY:
        return _DAILY[key]
    n = _ndays()
    c2b, _ = memberships()
    r = _beta(code, 'mkt') * _f_mkt(market)
    for bc in c2b.get(code, []):
        r = r + _beta(code, bc) * _f_brd(bc)
    r = r + _rng('idio', code).normal(0.0, _SIG_IDIO, n)

    # 板块冲击（最后一天）+ 点名票的强行设定
    last = n - 1
    for bc, shock in BOARD_SHOCK.items():
        if bc in c2b.get(code, []) and code not in _SPEC_BY_CODE:
            r[last] += shock
    spec = _SPEC_BY_CODE.get(code)
    if spec and spec['board'] in c2b.get(code, []):
        r[last] = spec['pct'] / 100.0

    # 成交量：对数正态 + "涨得多的日子放量"（真实的量价联动，不是装饰）
    rv = _rng('vol', market, code)
    base_v = float(rv.uniform(4e6, 4e7))
    vol = base_v * np.exp(rv.normal(0.0, 0.32, n)) * (1.0 + 6.0 * np.abs(r))
    if spec:
        vol[last] *= spec['vol_x']
    float_sh = base_v * float(rv.uniform(25.0, 150.0))        # 流通股，决定换手率量级

    # 价格路径
    base_px = float(_rng('px', code).uniform(6.0, 80.0))
    re = _rng('ohlc', code)
    prev = np.empty(n)
    prev[0] = base_px
    close = np.empty(n)
    close[0] = round(base_px * (1.0 + r[0]), 2)
    for i in range(1, n):
        prev[i] = close[i - 1]
        close[i] = round(max(0.5, close[i - 1] * (1.0 + r[i])), 2)
    openp = np.array([round(max(0.5, prev[i] * (1.0 + re.normal(0, 0.003))), 2)
                      for i in range(n)])
    wid = np.abs(re.normal(0, 0.007, n))
    high = np.round(np.maximum(openp, close) * (1.0 + wid), 2)
    low = np.round(np.minimum(openp, close) * (1.0 - wid), 2)
    if spec:                                   # 点名票的日内振幅也要拉开
        high[last] = round(max(openp[last], close[last]) * 1.012, 2)
        low[last] = round(min(openp[last], close[last]) * 0.986, 2)

    df = pd.DataFrame({
        'dt': _dates(), 'open': openp, 'high': high, 'low': low, 'close': close,
        'volume': np.round(vol, 0), 'amount': np.round(np.maximum(vol, 0) * close, 2),
        '_prev_close': prev, '_float_sh': float_sh,
    })
    df.attrs['source'] = 'SYNTH'
    _DAILY[key] = df
    return df


def _prev_close(df, i=-1):
    return float(df['_prev_close'].iloc[i])


# ── 场内/场外两条实时快照 ────────────────────────────────────────────────
_ROWS = {}


def _row(market, code):
    df = _daily_full(market, code)
    i = len(df) - 1
    px, pc = float(df['close'].iloc[i]), _prev_close(df, i)
    vol = float(df['volume'].iloc[i])
    rnd = _rng('meta', code)
    pe = float(rnd.uniform(8, 70))
    turn = vol / float(df['_float_sh'].iloc[i]) * 100.0
    # ★故意留 None：见文件头"None 语义"那段
    keep = _rng('nulls', code).rand()
    return {
        'code': code, 'name': _name_of(code), 'market': market,
        'price': round(px, 2), 'open': round(float(df['open'].iloc[i]), 2),
        'high': round(float(df['high'].iloc[i]), 2),
        'low': round(float(df['low'].iloc[i]), 2),
        'prev_close': round(pc, 2),
        'pct': round((px / pc - 1.0) * 100.0, 2) if pc else None,
        'volume': vol,
        'amount': round(vol * px, 2),
        'turnover': None if keep < 0.06 else round(turn, 2),
        'pe': None if keep > 0.92 else round(pe, 1),
        'ts': _dates()[-1].strftime('%Y-%m-%d 15:00'),
    }


_NAMES = {}


def _name_of(code):
    global _NAMES
    if not _NAMES:
        c2b, b2c = memberships()
        for bc, mem in b2c.items():
            short = _BOARD_NAME.get(bc, '沪深')[:2]
            for j, c in enumerate(mem):
                _NAMES.setdefault(c, '%s%02d' % (short, j + 1))
        for mk in ('hk', 'us'):
            for j, c in enumerate(universe()[mk]):
                _NAMES.setdefault(c, '%s合成%02d' % (mk.upper(), j + 1))
    return _NAMES.get(code, str(code))


# ── 冻结接口（规格 §3.1）────────────────────────────────────────────────

def realtime(market='cn'):
    """全市场实时快照。"""
    def _go():
        if market not in _MARKETS:
            raise SourceError('BAD_MARKET', '合成市场没有 %r（只有 %s）' % (market, list(_MARKETS)))
        if market not in _ROWS:
            _ROWS[market] = [_row(market, c) for c in universe()[market]]
        # ★返回**副本**：dict 是共享的，上层随手往行里塞个字段（比如挂上 z 分数）
        #   就会污染缓存，于是同一份快照在两次 scan 里给出不同的数。
        return [dict(r) for r in _ROWS[market]]
    return _wrap('realtime:%s' % market, _go)


def snapshot(codes):
    """指定代码的快照（可跨市场）。"""
    codes = list(codes or [])

    def _go():
        out = []
        for c in codes:
            mk = market_of(c)
            out.append(_row(mk, str(c)))
        return out
    return _wrap('snapshot:%s' % (','.join(str(c) for c in codes[:4]) or '-'), _go)


def daily(market, code, days=400, adjust='qfq'):
    """日 K。CANON 列、dt 升序、attrs['source']。"""
    def _go():
        if market not in _MARKETS:
            raise SourceError('BAD_MARKET', '合成市场没有 %r' % market)
        out = _daily_full(market, code)[CANON].tail(max(30, min(int(days or 400), _CANON_DAYS)))
        out = out.reset_index(drop=True)
        out.attrs['source'] = 'SYNTH'
        return out
    return _wrap('daily:%s:%s' % (market, code), _go)


def boards(kind='concept'):
    """板块清单（合成市场只有 cn 有板块结构）。"""
    def _go():
        _, b2c = memberships()
        defs = _CONCEPT if kind == 'concept' else _INDUSTRY
        out = []
        for bc, nm in defs:
            mem = [c for c in b2c.get(bc, []) if c in set(universe()['cn'])]
            if not mem:
                continue
            ps = [_row('cn', c) for c in mem]
            best = max(ps, key=lambda p: abs(p['pct'] or 0.0))
            # 板块量比 = **成员各自量比的平均**，不是"板块成交量 / 某一只票的基准量"
            # （后者量纲都不对：拿成员 A 的 20 日均量去除全板块的量，得到的数随
            #   成员数量线性膨胀，看着像个量比，其实什么都不是）。
            rs = []
            for c in mem:
                v = _daily_full('cn', c)['volume']
                b = float(v.tail(21)[:-1].mean())
                if b > 0:
                    rs.append(float(v.iloc[-1]) / b)
            out.append({
                'code': bc, 'name': nm, 'kind': kind,
                'pct': round(float(np.mean([p['pct'] or 0.0 for p in ps])), 2),
                'amount': float(np.sum([p['amount'] or 0.0 for p in ps])),
                'vol_ratio': round(float(np.mean(rs)), 2) if rs else None,
                'lead_code': best['code'], 'lead_name': best['name'],
                'ts': _dates()[-1].strftime('%Y-%m-%d 15:00'),
            })
        return out
    return _wrap('boards:%s' % kind, _go)


def board_members(board_code, kind='concept'):
    """板块成分股代码。"""
    def _go():
        _, b2c = memberships()
        if board_code not in b2c:
            raise SourceError('NO_SUCH_BOARD', '合成市场没有板块 %r' % board_code)
        return list(b2c[board_code])
    return _wrap('board_members:%s' % board_code, _go)


def stock_boards(code):
    """一只股票所属的全部板块。"""
    def _go():
        c2b, _ = memberships()
        if str(code) not in c2b:
            return []
        return [{'code': bc, 'name': _BOARD_NAME.get(bc, bc),
                 'kind': 'concept' if bc in _CONCEPT_CODES else 'industry'}
                for bc in c2b[str(code)]]
    return _wrap('stock_boards:%s' % code, _go)


def capital_flow(code, period='daily'):
    """本模块用不到，但桩要和冻结接口同名同形 —— 免得别处 import 时才发现缺一个。"""
    raise SourceError('NOT_IMPLEMENTED', '合成市场没有资金流（linkage 不用它）')


def trade_calendar():
    return [d.strftime('%Y-%m-%d') for d in _dates()]


def health():
    return {'sources': list(_LOG)}


# ── 分钟线：这里才装着 lead-lag 的真值 ──────────────────────────────────

def _bar_ts(day, market, period):
    """一个交易日的 bar **结束**时间戳列表（09:35, 09:40, … / 11:30 / 13:05 …）。"""
    out = []
    for a, b in _SESSIONS[market]:
        t = pd.Timestamp('%s %s:00' % (day.strftime('%Y-%m-%d'), a))
        end = pd.Timestamp('%s %s:00' % (day.strftime('%Y-%m-%d'), b))
        while t + pd.Timedelta(minutes=period) <= end:
            t = t + pd.Timedelta(minutes=period)
            out.append(t)
    return out


def _leader_intraday(day_idx, nbar, market):
    """领先股那一天的**原始**分钟收益。滞后股要从这里取（见下），所以必须能复算。"""
    return _rng('leadmin', market, day_idx, nbar).normal(0.0, 1.0, nbar)


def _minute_shape(market, code, day_idx, nbar, period):
    """→ 那一天的原始分钟收益（未标定到日收益）。

    ★lead-lag 就装在这一行里：滞后股在第 t 根上用的是**领先股第 t-lag 根**的收益。
      这是本模块唯一一处"造出来的因果"，别把它当成噪声随手改掉 ——
      探针的断言钉在它上面。
    ★滞后按**分钟**给定、在这里折算成 bar：`lag_min=15` 在 period=5 时是 3 根、
      在 period=15 时是 1 根 —— 两种周期下"真值"都是 15 分钟。
    """
    lag_bar = (max(1, int(LEADLAG['lag_min']) // period)
               if code == LEADLAG['follower'] else 0)
    if code == LEADLAG['leader']:
        return _leader_intraday(day_idx, nbar, market)
    base = _rng('min', market, code, day_idx, nbar).normal(0.0, 1.0, nbar)
    c2b, _ = memberships()
    bc = next((b for b in c2b.get(code, []) if b in _CONCEPT_CODES), None)
    if bc:                                       # 同板块的分钟线也不是各走各的
        base = base + 0.6 * _rng('minbrd', bc, day_idx, nbar).normal(0.0, 1.0, nbar)
    if code == LEADLAG['follower']:
        lead = _leader_intraday(day_idx, nbar, market)
        x = np.zeros(nbar)
        x[lag_bar:] = 0.75 * lead[:nbar - lag_bar]
        return x + 0.45 * base
    return base


def minute(market, code, period=5, days=5):
    """分钟 K。★bar 的收盘价**逐日标定到当天日线的收盘**（见下），
    所以分钟线加起来和日线说的是同一件事 —— 不然"日线说涨 4.6%、
    分钟线加起来涨 1%"这种事会静默地毒掉所有跨层对照。"""
    period = int(period or 5)
    days = max(1, int(days or 5))

    def _go():
        if market not in _MARKETS:
            raise SourceError('BAD_MARKET', '合成市场没有 %r' % market)
        if period not in (1, 5, 15, 30, 60):
            raise SourceError('BAD_PERIOD', '合成市场只支持 1/5/15/30/60 分钟')
        d = _dates()
        tail = min(days, len(d))
        day_idx = [len(d) - tail + k for k in range(tail)]
        frames = []
        for k, di in enumerate(day_idx):
            day = d[di]
            tss = _bar_ts(day, market, period)
            nbar = len(tss)
            shape = _minute_shape(market, code, di, nbar, period)
            ref = _daily_full(market, code)
            o = float(ref['open'].iloc[di])
            c = float(ref['close'].iloc[di])
            tot = float(np.sum(shape))
            target = float(np.log(max(c, 1e-9) / max(o, 1e-9)))
            scale = (target / tot) if abs(tot) > 1e-12 else 0.0
            path = o * np.exp(np.cumsum(shape * scale))
            cl = np.round(path, 2)
            prev = np.concatenate([[o], cl[:-1]])
            op = np.round(prev, 2)
            hi = np.round(np.maximum(op, cl) * 1.0015, 2)
            lo = np.round(np.minimum(op, cl) * 0.9985, 2)
            dv = float(ref['volume'].iloc[di]) / nbar
            # U 型日内量：开盘尾盘多、中午少（乘 3 让均值为 1）
            w = np.linspace(0, 1, nbar)
            prof = 0.6 + 1.8 * (2 * w - 1) ** 2
            vb = np.round(dv * prof, 0)
            frames.append(pd.DataFrame({
                'dt': tss, 'open': op, 'high': hi, 'low': lo, 'close': cl,
                'volume': vb, 'amount': np.round(vb * cl, 2)}))
        out = pd.concat(frames, ignore_index=True).reset_index(drop=True)
        out.attrs['source'] = 'SYNTH'
        out.attrs['period'] = period
        return out
    return _wrap('minute:%s:%s:%s' % (market, code, period), _go)


if __name__ == '__main__':
    # 自检：把注入点和它们的实际取值打出来。合成市场的"标准答案"漂了，这里看得见。
    print('日期轴 %s … %s（%d 根）' % (_dates()[0].date(), _dates()[-1].date(), _ndays()))
    print('股票池 %s' % {k: len(v) for k, v in universe().items()})
    c2b, b2c = memberships()
    print('板块数 concept=%d industry=%d' % (len(_CONCEPT), len(_INDUSTRY)))
    for s in ANOMALY_SPEC:
        r = _row('cn', s['code'])
        v = _daily_full('cn', s['code'])['volume']
        # 量比按**模块自己的定义**算（当日量 ÷ 前 20 日均量），不是"除以昨天"——
        # 昨天自己也是随机的一天，拿它当分母量出来的是别的东西。
        rv = float(v.iloc[-1]) / float(v.tail(21)[:-1].mean())
        nc = len([x for x in c2b[s['code']] if x in _CONCEPT_CODES])
        print('  %-7s %-6s 期望 %+6.2f%% 实际 %+6.2f%%  量比期望 %.1f 实际 %.2f  概念板块 %d 个 %s'
              % (s['code'], r['name'], s['pct'], r['pct'], s['vol_x'], rv, nc, c2b[s['code']]))
    assert all(len([x for x in b if x in _CONCEPT_CODES]) <= 2 for b in c2b.values()), \
        '一只票最多 2 个概念板块这条不变量被打破了'
    print('不变量：每只 cn 票 ≤2 个概念板块 ✓')
    df = minute('cn', LEADLAG['follower'], 5, 1)
    print('分钟线 %d 根/日  %s' % (len(df), list(df.columns)))
