"""常驻采集器（规格 §3.5）—— **这个模块是"通信采集"装置，不是分析装置**。

用户对这个能力的口径原话是「**这个注重通讯的采集，弱化分析**」。所以这里只做三件事：
**采、存、记健康度**。不判断、不给方向、不算关联 —— 那是 `agent/linkage/` 的事。

`agent/linkage/` 是"用户问一次就扫一轮"，本模块是**常驻**：一轮一轮不停地采，
把每一轮的索引追加到盘上，让页面上能看"采集器在不在跑、最近几轮各采了多少行、
哪个源在挂"。

## 冻结接口（`app.py` 的三条路由逐字按这三个签名调，别改名）

    status() -> dict          采集器当前状态。**只读**：不许触发任何采集，也不许写盘。
    ring(limit=120) -> list   最近 N 次的**索引**（是索引，不是快照本身）。→ **裸 list**
    once(scope='all') -> dict 采一轮，同步做完再返回。

外加两个**非冻结**的：`start()` / `stop()`。常驻与否由调用方决定；
★`once/status/ring` **不依赖** `start()` 被调过 —— `app.py` 是惰性 import 本模块的
（第一次请求才 import），它不会、也不该自己起线程。

## 三条形状纪律（每一条都对应一种"页面是空的、而服务端不报错"）

1. **`once()`/`status()` 回带 `ok` 的 dict；`ring()` 回裸 list。**
   包成 `{'ok':True,'items':[…]}` 是 `app.py` 那一层干的（见 app.py:691-713 的注释）。
   形状反了页面就是空的，而且服务端不报错 —— 最坏的一种错。
2. **`undefined` 和 `false` 不是一句话。** `running` 这一栏：`True` = 本进程里
   那个常驻线程活着；`False` = 我们知道它没在跑（停过 / 线程死了）；
   **没这一栏** = 我们没得说（从没在本进程里起过，别的进程有没有在跑我们看不见）。
   ★不许用 `False` 顶替"不知道"。
3. **`null` 不是 `0`。** `rows: null` 是"没采到"，`rows: 0` 是"采到了 0 行"。
   前端 `api2.ts: numOrNull()` 专门堵 `Number(null) === 0` 那一手，本模块不许把
   null 写成 0。`_probe.py` 的 C7 拿"文件不在时编一条空轮出来"这条注入打它
   （`status()` 在空目录上必须给 `rows: None`，不是 0）。

## import 不许有副作用

包级**不起线程、不写文件、不连网**。`LICAI_COLLECT_DIR` 每次调用现读（不在
import 时快照），所以 `import collector` 之后盘上不会多出任何东西。

## 落盘

`data/collect/`（`LICAI_COLLECT_DIR` 可改），**只追加，绝不整份重写** ——
理由见 `store.py` 模块头，`_probe.py` 的 C8 钉着（两轮之间塞一行"别人的"记录，
下一轮采完它必须还在）。落盘位置在 `status()['path']` 里如实报出来：
用户有资格知道这个站往磁盘上写了什么、写在哪儿（同 `/ledger` 那条纪律）。

★**保留期**（只有 `frames/` 里过期的按天帧文件会被删，`index.jsonl` 从不被碰）
在 `once()` 这一条**写入路径**上做，且删了什么、为什么删都挂在 `once()['prune']`
里带回来（见 `store.py`「保留期」）。`status()` **不删东西** —— 上面那条"只读"
不是形容词，是判据 C2 钉着的。
"""

import os
import threading
import time

# ★一律走"模块对象 + 属性"而不是 `from .store import read_index`：
#   后者在 import 时就把名字绑死了，判据往 `store.read_index` 上打一条注入
#   会**打不着**被测代码（症状是"注入没能把它打红"，而判据看着是好的）。
#   这一条是踩过的：`fault-injection-*` 那批账里"注入够不着"占了一大半。
from . import sources
from . import store
from . import synth

__all__ = ['once', 'status', 'ring', 'start', 'stop', 'path']

INTERVAL_ENV = 'LICAI_COLLECT_INTERVAL'     # 常驻循环的间隔（秒）
LOOP_SCOPE_ENV = 'LICAI_COLLECT_SCOPE'      # 常驻循环每轮采什么

DEFAULT_INTERVAL = 60.0
DEFAULT_RING = 120

# 本进程里那个常驻线程的状态。★`state='never'` 是一个**独立**的状态，
# 不是"stopped 的初值"：它对应页面上那句"服务端没说（running 这一栏没给）"，
# 和"说了没跑"是两句不同的话（见模块头第 2 条）。
_RT = {'state': 'never', 'thread': None, 'stop': None, 'interval': None,
       'next_ts': None, 'started': None, 'rounds': 0, 'last_err': None}
_RT_LOCK = threading.RLock()


def path():
    """落盘根目录（绝对路径）。**只读**。"""
    return store.root()


def _env_num(name, default, lo, hi, cast=float):
    try:
        return max(lo, min(hi, cast(os.environ.get(name) or default)))
    except (TypeError, ValueError):
        return cast(default)


def _fmt(epoch=None):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(epoch or time.time()))


# ── 冻结接口 1/3：采一轮 ───────────────────────────────────────────────

def _indexed(rec, iok, ierr):
    """采到的一轮 + 索引落盘的结果 → **对外那一条**（`once()` 的返回值）。

    ★抽成命名函数，两个理由都不是"为了好看"：

    ① `store.json_safe` 必须**在返回值上**再做一遍，不能只靠"落盘时已经洗过"：
       "索引没落上盘"那条路上记录压根没进过 `json_safe`；而 `json.dumps` 默认
       **允许** NaN（写成裸 `NaN`），于是 `jsonify` 会写出一个别的解析器读不动的
       body，前端 `JSON.parse` 当场炸。`_probe.py` C13 拿"把 `json_safe` 变成
       恒等函数"这条注入打它。
    ② "索引落上盘了吗"这件事的处理必须能被独立验（`_probe.py` C16：注入把落盘
       失败按成功处理）—— 就地写进 `once()` 的话那条注入无处可落。
    """
    out = store.json_safe(dict(rec))          # ★走模块属性：注入要够得着
    out['path'] = store.root()
    out.setdefault('hint', None)
    rows = out.get('rows')
    if rows is None and rec.get('rows') is not None:
        # 上游给了一个非有限数（NaN/inf），被 json_safe 洗成了 None。
        # ★这件事要**说出来**：静默地把它降级成"没采到"的话，"上游给了个脏数"
        #   和"上游确实没给数"在页面上长得一模一样 —— 那是两件事。
        out['ok'] = False
        out['err'] = ('这一轮的行数是个非有限数（上游给了 NaN/inf），已按"没采到"处理；'
                      'sources 里那几条的 n 是原始值')
    if not iok:
        out['ok'] = False
        out['err'] = ('数据采到了，但索引没落上盘（%s）—— 这一轮在页面上不会出现，'
                      '下次采之前请先看盘还有没有空间 / 有没有写权限' % ierr)
    out['msg'] = _round_msg(out)
    return out


def _round_msg(out):
    """一轮**成了**的时候，页面上显示的那句话。★抽成命名函数，因为它是
    "这一轮成了"这句话的**唯一定义**，而它有一个反直觉的分支值得单独写下来
    （也让它能被一条注入打红）：

    ★`ok=True` 而 `rows is None` —— 也就是**只采到资讯**的那一轮
      （`rows` 只记行情/板块的行数，资讯的条数不并进去，见 `sources._round_ok`）。
      这里写成"这一轮什么都没采到"的话，就是**在页面上说了一句与盘上事实相反的话**：
      盘上明明躺着 6 条资讯、`sources` 里 news 那条是绿的。说出来的和真发生的对不上，
      与"编数据"是同一类错。**拿 `rows` 单独判这一轮的成不成，两处都会错**
      （`ok` 那处和这句话这处），`_probe.py` C20 两处都钉着。

    ★"这一轮成了"这句话**不许**用兜底措辞编（比如"采完一轮"就完了）：页面上
      这句话是用户判断"今天到底采到没有"的唯一依据，它必须说清采到了什么。
    """
    if not out.get('ok'):
        # ★失败时页面上只显示这一句（api2.ts: fromBody 在 ok:false 时只取
        #   msg/error + hint，err/sources/rows 全丢）⇒ 必须带上真原因。
        return out.get('err') or '这一轮没成（而它没给原因）'
    n = out.get('rows')
    if n is not None:
        return '采完一轮：%s 行' % n
    ids = [it.get('id') for it in (out.get('sources') or []) if it.get('ok')]
    if ids:
        return ('采完一轮：只采到了 %s（`rows` 记的是行情/板块的行数，所以是 None）'
                % '、'.join(str(i) for i in ids))
    # ok=True 却既没有行数、也没有一个成功的源：说不清就不能编一句"成了"。
    return '采完一轮（这一轮没给出行数，也没说采到了什么）'


def once(scope='all'):
    """采一轮，落盘（索引 + 各市场载荷），同步做完再返回。

    → 一条索引记录的**超集**：`{ok, ts, end, scope, market, markets, rows, ms, err,
      sources, rows_by_market, skipped, notes, frames, backend, path, hint, msg,
      prune}`。

    ★`ok` 的语义是"**这一轮采到东西了吗**"，不是"有没有源失败"：
      4 个市场挂了 1 个、另外 3 个活着 ⇒ `ok=True`，挂掉那个在 `sources` 里是红的。
      全挂 ⇒ `ok=False` + `rows=None` + 一句人话原因。
      （全红的时间线和"部分红"的时间线在页面上是两回事，别把它们压成一个。）
    ★落盘失败**不当成没采到**：`rows` 保持真值，只是 `ok` 变 False 并在 `err` 里
      说清"数据采到了，但索引没落上盘"。反过来（rows 置 None）等于说数据没采到，
      那是另一件事，而且会让用户以为源坏了。
    ★`prune` 是这一轮的**保留期**记账（`store.prune()` 的原样返回：删了哪些文件、
      按哪条闸删的、为什么没删）。挂在 `once()` 上是**故意的**：清理只许发生在
      写入路径上，`status()` 是只读接口（见 store.py 模块头「保留期」）。真删了
      东西时 `msg` 里也会多说一句 —— **不许静默删**。
    """
    # ★保留期**先**做：盘满了（旧帧文件占着）的时候，先把地方腾出来，这一轮
    #   才写得进去。反过来（先采后清）的话，ENOSPC 那一轮的数据就白采了。
    pr = store.prune()
    rec = sources.collect_round(scope)
    iok, ierr = store.append_index(rec)
    out = _indexed(rec, iok, ierr)
    out['prune'] = pr
    if pr.get('removed'):
        # ★删了东西就**必须**说出来。"静默删除"比"不删"坏：用户看到盘上的文件
        #   少了而没有任何一句话解释，只有两种结论 —— 采集器坏了，或者有人删了
        #   他的东西。★只在真删了时拼接：没删的那一轮 `msg` 与 `_round_msg` 逐字
        #   相同（`_probe.py` C21 拿 `_round_msg` 的返回值当锚）。
        out['msg'] = '%s；%s' % (out.get('msg'), pr.get('note'))
    return out


# ── 冻结接口 2/3：状态 ─────────────────────────────────────────────────

def status():
    """采集器当前状态。★**只读** —— 不采集、不 import 别的包、不写盘。

    ★为什么这里连 `datasource.health()` 都不去调（页面上有 `health` 这一栏）：
      那一次调用要么 import 一个**别的包**（`__pycache__` 就是一次写盘），
      要么在网络边上站着。而"只读接口偷偷写盘"正是这块最容易犯的错，也是
      判据 C2 唯一钉着的东西。所以上游健康度由 `once()` 采完就**存进索引记录**
      （`upstream_health`），这里只把最近一条读出来 —— 一个只读接口，
      它的全部输入就是那个只读的文件。
    """
    now = _fmt()
    try:
        recs, bad = store.read_index()
        root = store.root()
    except Exception as e:                                       # noqa: BLE001
        return {'ok': False, 'ts': now, 'err': '读采集索引失败：%s: %s'
                                               % (type(e).__name__, str(e)[:160]),
                'path': store.root(), 'hint': '先看 %s 在不在、读不读得动' % store.root(),
                'running': _running_field()}
    last = recs[-1] if recs else None
    lr = last or {}
    out = {
        'ok': True,
        'ts': now,
        # ★落盘位置要显示（同 `/ledger` 纪律）
        'path': root,
        'ticks': len(recs),                       # 累计采了多少轮（= 索引里的行数）
        'last_ts': lr.get('ts'),
        'next_ts': None,                          # 下面按有没有在跑填
        'rows': lr.get('rows'),                   # ★None 保持 None，不许折成 0
        'markets': list(lr.get('markets') or []),
        'err': lr.get('err'),                     # 最近一轮的错（页面那块告警用它）
        'backend': lr.get('backend'),
    }
    r = _running_field()
    if r is not _UNSAID:
        out['running'] = r
    with _RT_LOCK:
        out['next_ts'] = _RT['next_ts']
        st = _RT['state']
        rounds = _RT['rounds']
        lerr = _RT['last_err']
        started = _RT['started']
    if started:
        out['since'] = started
    if st == 'running':
        out['rounds'] = rounds
    if lerr:
        out['last_err'] = lerr                  # 常驻循环里的异常（`once()` 自己吞不掉的那些）
    if last is not None:
        out['last'] = {'ts': lr.get('ts'), 'market': lr.get('market'),
                       'rows': lr.get('rows'), 'ms': lr.get('ms')}
        if lr.get('sources'):
            out['sources'] = lr['sources']
        if lr.get('upstream_health'):
            out['health'] = lr['upstream_health']
    # 坏行**不许静默**：半行 JSON 的最常见成因是进程被 kill 在写一半，
    # 而"索引是空的"和"你的索引坏了"在页面上长得一模一样。
    msgs = []
    if bad:
        out['index_bad'] = len(bad)
        msgs.append('索引日志里有 %d 行读不动（行号 %s…）—— 通常是写到一半被打断；'
                    '`ring()` 与上面的 ticks 都**不含**这些行'
                    % (len(bad), ','.join(str(x) for x in bad[:4])))
    if not recs:
        msgs.append('还没采过任何一轮（索引是空的）')
    if msgs:
        out['msg'] = '；'.join(msgs)
    if r is _UNSAID:
        out['hint'] = ('本进程里没有常驻采集线程（%s=%gs 的那个循环没起过）。'
                       '要它常驻就显式调 collector.start()；不调也能用 `once()` 手动采。'
                       % (INTERVAL_ENV, _interval()))
    return out


class _Unsaid:
    """`running` 的第三态：**我们没得说**。用一个独立对象而不是 `None`
    ——`None` 会被 `jsonify` 写成 `null`，而前端的判据是**键在不在**
    （`api2.ts`：`undefined` 是"没说"，`false` 是"说了没跑"）。"""
    __slots__ = ()

    def __repr__(self):
        return '<未说>'


_UNSAID = _Unsaid()


def _running_field():
    """→ True / False / `_UNSAID`。语义见模块头第 2 条。"""
    with _RT_LOCK:
        st = _RT['state']
        th = _RT['thread']
    if st == 'running':
        return bool(th is not None and th.is_alive())
    if st in ('stopped', 'stopping', 'died'):
        return False
    return _UNSAID


# ── 冻结接口 3/3：索引环 ───────────────────────────────────────────────

def ring(limit=DEFAULT_RING):
    """最近 N 次的**索引**（升序：最老的在前、最新的在最后 —— 和
    `api2.ts` 里那两张图从左到右读时间的画法一致）。→ **裸 list**。

    ★`limit <= 0` ⇒ 空表。这一条是**显式**的，不是顺手：`recs[-0:]` 在 Python 里
      等于 `recs[0:]` = **整个文件**，于是"最近 0 轮"会返回全部。C5 钉着它。
    ★`limit` 是垃圾（`'abc'`）就抛 `TypeError`，**不静默退回 120**：
      把"给我看 5 轮"悄悄变成"给你 120 轮"是对用户那句话的篡改。
      `app.py` 那条路由自己会接住它并回一句 400。
    """
    if limit is None:
        limit = DEFAULT_RING
    try:
        n = int(limit)
    except (TypeError, ValueError):
        raise TypeError('limit 必须是数字，收到 %r' % (limit,)) from None
    recs, _bad = store.read_index(limit=n)
    return list(recs)


# ── 常驻循环（非冻结接口；once/status/ring 不依赖它）───────────────────

def _interval():
    return _env_num(INTERVAL_ENV, DEFAULT_INTERVAL, 1.0, 86400.0, float)


def _loop_scope():
    return os.environ.get(LOOP_SCOPE_ENV) or 'all'


def _loop(iv, stop_ev):
    """常驻循环。★一轮出错**绝不能把这个线程带走**：一个因为某个源抽风就静默
    退出的采集器，在页面上和"还在跑但一直没数据"长得一模一样。
    所以异常在这里被接住、写进 `_RT['last_err']`，循环继续。"""
    try:
        while not stop_ev.is_set():
            with _RT_LOCK:
                _RT['next_ts'] = _fmt(time.time() + iv)
            t0 = time.time()
            try:
                once(scope=_loop_scope())
                with _RT_LOCK:
                    _RT['rounds'] += 1
                    _RT['last_err'] = None
            except BaseException as e:                           # noqa: BLE001
                with _RT_LOCK:
                    _RT['last_err'] = '常驻循环第 %d 轮抛了：%s: %s' \
                                      % (_RT['rounds'] + 1, type(e).__name__, str(e)[:160])
            with _RT_LOCK:
                _RT['next_ts'] = _fmt(time.time() + iv)
            rest = iv - (time.time() - t0)
            if stop_ev.wait(max(0.0, rest)):
                break
    finally:
        with _RT_LOCK:
            if _RT['state'] == 'running':
                _RT['state'] = 'died'      # 不是被 stop() 停的 ⇒ 它自己退了，要说出来
            _RT['next_ts'] = None


def start(interval=None):
    """起常驻循环（**额外**接口，不是冻结的那三个）。

    ★第一轮**立刻**跑，不等一个 interval：等 60 秒才出现第一条记录的话，
      页面上"采集器起来了没有"这件事在头一分钟里没法回答。
    """
    iv = _interval() if interval is None else max(1.0, float(interval))
    with _RT_LOCK:
        th = _RT['thread']
        if _RT['state'] == 'running' and th is not None and th.is_alive():
            return {'ok': True, 'running': True, 'interval': _RT['interval'],
                    'next_ts': _RT['next_ts'], 'msg': '已经在跑了，没重复起'}
        ev = threading.Event()
        _RT.update(state='running', stop=ev, interval=iv, started=_fmt(),
                   next_ts=_fmt(time.time() + iv), rounds=0, last_err=None)
        t = threading.Thread(target=_loop, args=(iv, ev), daemon=True, name='collector-loop')
        _RT['thread'] = t
        t.start()
    return {'ok': True, 'running': True, 'interval': iv, 'scope': _loop_scope(),
            'path': store.root(), 'next_ts': _RT['next_ts'],
            'msg': '起了一个每 %gs 采一轮的常驻线程（第一轮立刻开跑）' % iv}


def stop(timeout=15.0):
    """停常驻循环。**已经停了也算成功**（`ok=True`），但会说明它本来就没在跑。"""
    with _RT_LOCK:
        th, ev, st = _RT['thread'], _RT['stop'], _RT['state']
        if ev is None or st != 'running':
            _RT['state'] = 'stopped' if st != 'died' else 'died'
            _RT['next_ts'] = None
            return {'ok': True, 'running': False, 'msg': '本来就没在跑（没起过，或者已经停了）'}
        _RT['state'] = 'stopping'
    ev.set()
    if th is not None:
        th.join(timeout)
    alive = bool(th is not None and th.is_alive())
    with _RT_LOCK:
        # ★没停下来就说没停下来：`running` 报的是**事实**（thread 还活着），
        #   不是"我调用过 stop 了"。
        _RT['state'] = 'running' if alive else 'stopped'
        _RT['thread'] = th if alive else None
        _RT['next_ts'] = _RT['next_ts'] if alive else None
    return {'ok': not alive, 'running': alive, 'path': store.root(),
            'msg': '循环没在 %gs 内退出（它多半正卡在一次外部调用里），线程还活着' % timeout
                   if alive else '停好了'}


# 显式说明这个模块**不**导出什么：`synth` 只是被 `sources` 用来做桩，
# 外面别拿它当数据源用（它只在 env 显式要求时才是数据源，见 synth.py 模块头）。
_ = synth
