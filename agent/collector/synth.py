"""合成数据源：**只在 `LICAI_COLLECT_SYNTH` 被显式设上时**才当数据层用。

★这个文件**不是**"真源取不到时的兜底"。兜底是 `datasource` 内部源链的事。
  这里是一台**印行情的机器**，而"取不到就自动用它顶上"意味着页面上那个
  `rows: 8272` 是编的、而用户看不出来 —— 那是本项目最不能出的东西。
  所以：`sources.backend()` 里，桩只认服务端 env（照抄 `feed.py: LICAI_FEED_STUB`
  与 `linkage/collect.py: LICAI_LINKAGE_SYNTH` 的先例），
  而"真源取不到"走的是 `ok=False` + 人话原因那条路，两条路**在代码里不相交**。

存在的理由有两条，第二条才是真的理由：

① 并行施工：`agent/datasource/` 还没落地（现在只有目录、没有 `__init__.py`）。
   本模块按冻结的 §3.1 接口写，不等它。
② ★**真数据上"这一轮采到了"这句话是无法证伪的。** "今天采了 8272 行"
   —— 真相没人知道，所以一个采得乱七八糟的采集器在真数据上照样能输出一份
   看着很像样的索引，而没有任何人能说你错。合成数据把这件事变成**可断言**的：
   行数、每个市场的代码、哪些字段是 None，全都写死在下面的常量里，
   探针拿它们当标准答案。

★确定性：`random.Random(zlib.crc32(种子))`。不用内置 `hash()` —— 它对 str
  **每进程加盐**（PYTHONHASHSEED），同一份输入在两个进程里会给出两段不同的行情，
  那样"同参同帧"的判据就变成了"拿 A 去比 B"（照抄 `linkage/_synthmarket.py` 的理由）。

★None 语义：规格 §3.1 写着"取不到的字段是 None，**不是 0**"。这里**故意**
  让一部分标的的 `turnover`/`pe` 为 None —— 不然上层那条"None 不许当 0"的
  代码路径在桩上永远走不到，也就永远没被验过。

★故障注入：`LICAI_COLLECT_SYNTH_FAIL='realtime:hk,boards'` → 这几类调用抛
  `SourceError`。匹配规则是**前缀**（写 `realtime` 就把四个市场全打掉）。
  这是**服务端** env，没有客户端开关。
"""

import os
import random
import time
import zlib

SYNTH_ENV = 'LICAI_COLLECT_SYNTH'
FAIL_ENV = 'LICAI_COLLECT_SYNTH_FAIL'

NAME = 'SYNTH（%s 已设，**合成数据，不是真行情**）' % SYNTH_ENV

# 桩里各市场的行数。三个不同的数**不是随手写的**：同一个数的话，
# "把三个市场的行数求和"和"拿其中一个乘以 3"这两件事在桩上分不出来。
ROWS = {'cn': 41, 'hk': 17, 'us': 23, 'crypto': 7}
BOARDS = {'concept': 12, 'industry': 6}

_CONCEPT = [('BK0500', '白酒'), ('BK0501', '消费50'), ('BK0502', '新能源'), ('BK0503', '光伏'),
            ('BK0504', '半导体'), ('BK0505', '人工智能'), ('BK0506', '医药'), ('BK0507', '券商'),
            ('BK0508', '军工'), ('BK0509', '房地产'), ('BK0510', '汽车'), ('BK0511', '银行')]
_INDUSTRY = [('BK1000', '食品饮料'), ('BK1001', '电力设备'), ('BK1002', '电子'),
             ('BK1003', '医药生物'), ('BK1004', '非银金融'), ('BK1005', '机械设备')]

# 调用流水，给 health() 用。只留最近 200 条 —— 这是个桩，不是账本；
# 真正的账本在索引记录里（从调用方那一侧记的，更全）。
_LOG = []


class SourceError(RuntimeError):
    """与 `feed.SourceError` 同名同形**但不是同一个类**：本模块只 catch 自己这一层的
    取数错误，不该因为数据层换了实现就把异常类型也绑死（照抄
    `linkage/collect.py: SourceError` 的理由）。"""

    def __init__(self, code, msg, hint=''):
        super().__init__(msg)
        self.code, self.hint = code, hint


def active():
    """桩是否**被显式要求**。只认服务端 env。"""
    return bool(os.environ.get(SYNTH_ENV))


def spec():
    """桩的参数。`LICAI_COLLECT_SYNTH=1` → 全默认；指一个 JSON 文件 → 读它。"""
    raw = (os.environ.get(SYNTH_ENV) or '').strip()
    if not raw or raw.lower() in ('1', 'true', 'yes', 'on'):
        return {}
    try:
        import json
        with open(raw, encoding='utf-8') as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:                                            # noqa: BLE001
        return {}                     # 读不到就退回默认值 —— 桩的参数不影响"数是真的假的"


def _fail_list():
    return [s.strip() for s in (os.environ.get(FAIL_ENV) or '').split(',') if s.strip()]


def _maybe_fail(sid):
    for tok in _fail_list():
        if str(sid).startswith(tok):
            raise SourceError('SYNTH_FAIL', '桩里点名让 %s 失败（%s=%s）' % (sid, FAIL_ENV, tok))


def _seed(sp, key):
    return zlib.crc32(('collect|%s|%s' % (sp.get('seed', 'default'), key)).encode('utf-8'))


def _log_rec(sid, ok, n, ms, err=None):
    _LOG.append({'id': sid, 'ok': bool(ok), 'n': n, 'ms': round(ms, 1), 'err': err})
    del _LOG[:-200]


def _prefix(market):
    return {'cn': 'sh', 'hk': 'hk', 'us': 'us', 'crypto': ''}.get(market, '')


def realtime(market='cn'):
    """全市场实时快照。字段与规格 §3.1 的 `realtime()` 契约逐字一致。"""
    sid = 'realtime:%s' % market
    t0 = time.time()
    try:
        _maybe_fail(sid)
    except SourceError as e:
        _log_rec(sid, False, None, (time.time() - t0) * 1000, str(e))
        raise
    if market not in ROWS:
        _log_rec(sid, False, None, (time.time() - t0) * 1000, '不认识的 market=%r' % market)
        raise SourceError('BAD_MARKET', '桩里没有 market=%r（只有 %s）'
                          % (market, '/'.join(sorted(ROWS))))
    sp = spec()
    n = int(sp.get('rows', {}).get(market, ROWS[market])) if isinstance(sp.get('rows'), dict) \
        else int(sp.get('rows') or ROWS[market])
    rnd = random.Random(_seed(sp, 'rt|%s|%d' % (market, n)))
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    pre = _prefix(market)
    out = []
    for i in range(n):
        base = 10.0 + rnd.uniform(1, 500)
        pct = round(rnd.uniform(-8, 8), 2)
        price = round(base * (1 + pct / 100.0), 3)
        out.append({
            'code': ('%s%06d' % (pre, 600000 + i)) if market != 'crypto'
                    else ['BTC', 'ETH', 'SOL', 'BNB'][i % 4] + str(i),
            'name': '桩标的%03d' % i, 'market': market,
            'price': price, 'open': round(base, 3), 'high': round(base * 1.02, 3),
            'low': round(base * 0.98, 3), 'prev_close': round(base, 3),
            'pct': pct, 'volume': float(rnd.randrange(1000, 10 ** 7)),
            'amount': float(rnd.randrange(10 ** 5, 10 ** 9)),
            # ★故意留 None：桩是判据的输入，"某一列缺失"必须在桩里就存在，
            #   否则"缺值变 null 而不是 0"那条判据在桩上永远只看到有值的行。
            'turnover': None if i % 7 == 0 else round(rnd.uniform(0.1, 12), 2),
            'pe': None if i % 5 == 0 else round(rnd.uniform(-30, 90), 2),
            'ts': now,
        })
    _log_rec(sid, True, len(out), (time.time() - t0) * 1000)
    return out


def boards(kind='concept'):
    """板块清单。字段与 §3.1 契约逐字一致。"""
    sid = 'boards:%s' % kind
    t0 = time.time()
    try:
        _maybe_fail(sid)
    except SourceError as e:
        _log_rec(sid, False, None, (time.time() - t0) * 1000, str(e))
        raise
    defs = {'concept': _CONCEPT, 'industry': _INDUSTRY}.get(kind)
    if defs is None:
        _log_rec(sid, False, None, (time.time() - t0) * 1000, '不认识的 kind=%r' % kind)
        raise SourceError('BAD_KIND', '桩里没有 kind=%r（只有 concept/industry）' % (kind,))
    sp = spec()
    n = int(sp.get('boards', {}).get(kind, BOARDS[kind])) if isinstance(sp.get('boards'), dict) \
        else int(sp.get('boards') or BOARDS[kind])
    rnd = random.Random(_seed(sp, 'bd|%s|%d' % (kind, n)))
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    out = []
    for code, name in defs[:max(0, n)]:
        out.append({
            'code': code, 'name': name, 'kind': kind,
            'pct': round(rnd.uniform(-6, 6), 2),
            'amount': float(rnd.randrange(10 ** 7, 10 ** 11)),
            'vol_ratio': round(rnd.uniform(0.4, 3.2), 2),
            'lead_code': '%s%06d' % (_prefix('cn'), 600000 + rnd.randrange(0, 40)),
            'lead_name': '桩领涨%02d' % rnd.randrange(0, 40),
            'ts': now,
        })
    _log_rec(sid, True, len(out), (time.time() - t0) * 1000)
    return out


def health():
    """§3.1 的 `health()` 形状：`{'sources': [{'id','ok','ms','n','err','ts'}]}`。"""
    rows = [dict(r) for r in _LOG]
    for r in rows:
        r.setdefault('ts', time.time())
    rows.sort(key=lambda r: r['id'])
    return {'sources': rows, 'ts': time.time()}


def forget_health():
    _LOG.clear()
