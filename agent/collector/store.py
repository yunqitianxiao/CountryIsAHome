"""落盘层：**只追加**的索引日志 + 快照载荷。整份重写在这里是被禁止的。

盘上长这样（`data/collect/`，`LICAI_COLLECT_DIR` 可以改根）：

    data/collect/
      index.jsonl                      # 每采完一轮追加**一行**（这就是 `ring()` 读的东西）
      frames/2026-09-22.cn.jsonl       # 每个市场每天一个文件，每轮追加一行载荷

## 为什么是"每天一个文件"而不是"每轮一个文件"

每轮一个文件的话，`frames/` 目录下几小时就是几百个文件，而"某一天的某一轮"要
靠文件名去拼 —— 拼错了没人告诉你。按天分文件，一个文件里的行天然按时间有序，
`tail` 一下就能看最近几轮采了什么。**代价是找不到"按天清理"以外的剪枝办法**
—— 而"按天清理"正是 `prune()` 现在做的事（见下面「保留期」那一节）。

## 保留期（`prune()`）

★没有保留期之前，这里是一台会把盘写满的装置：一轮 1.36 MB、一天 1.91 GB
（README 里那次实测），而落盘是只追加 ⇒ 一周 13 GB。所以现在有两条闸：

- **天数**（`LICAI_COLLECT_RETAIN_DAYS`，默认 3）：删掉**文件名上的日子**早于
  `今天 - 3 天` 的帧文件。为什么是 3：本站实测速率 1.91 GB/天 ⇒ 稳态上限
  5.7 GB（一块笔记本盘放得下）；而 3 天是"周一还能翻到上周五"的**最小值** ——
  再短就跨不过一个周末，页面上"上周同期"就没了。
  ★这里**必须**用文件名上的日子、不用 mtime：mtime 会被任何一次追加刷新，
  而"这条数据是哪一天的"是文件名说的（同 `frame_path` 的取法）。
- **字节**（`LICAI_COLLECT_MAX_BYTES`，默认 8 GiB）：从最老的一天开始删到总量
  不超上限。这条是**兜底**，因为天数闸挡不住"速率变了"：常驻间隔是可配的
  （`LICAI_COLLECT_INTERVAL=1` ⇒ 速率 ×60 ⇒ 3 天就是 340 GB）。

四条边界，每条都对应一种"删错了没法回滚"：

1. **只在写入路径上被调**（`__init__.once`），**不在** `status()` 里。只读接口
   顺手清一下盘是最坏的一种脏 —— "看一眼状态"变成了删除。
2. **只碰 `frames/` 里匹配 `YYYY-MM-DD.<market>.jsonl` 的普通文件**。名字不像
   帧文件的一律不碰（那是"别人的"文件）；`index.jsonl` 是页面的时间线、不是
   载荷，本函数**从不看它**（它是"只追加"那条纪律的锚，`_probe.py` C8 钉着）。
3. **删之前先做路径包含检查**（`_inside`：候选得在 `root()` 里面）。正常永远为真
   —— 记进 `denied`，不靠"反正不会发生"。
4. **不删"刚被写过"的**（`LICAI_COLLECT_PRUNE_MIN_AGE`，默认 600s）。`frame_path()` 取的是
   `date.today()`，所以正常只有**今天**那个文件在被追加；但跨午夜的那一瞬间，
   另一个进程可能在往**昨天**那个文件里追加 —— 而 `unlink` 一个仍被
   `open(...,'a')` 持有的文件，会让它那一轮**静默写进空气**（写进了没人看得见
   的 inode，`fsync` 还成功）。这条闸只是**把窗口从秒压到 10 分钟**，不是保证；
   它只会**推迟**删除（下一轮会再试），不会阻止 —— 唯一"永远删不掉"的是
   "一直有人在写它"的文件，而按定义那只有今天那个。

## 三条纪律（每一条都对应一种踩过的坏法）

1. **只追加**。`open(p, 'a')` + `flush` + `fsync`。绝不 `open(p, 'w')` ——
   这个仓库是多写者共用的：进程 A 读全量、追加一条、整份写回，就会把进程 B
   在这中间追加的那条**静默抹掉**，而且事后任何内容比对都看不出来
   （只有 mtime 记住了，而 mtime 本身不是内容证据）。
   `_probe.py` 里 C8 专门验这条：**在两轮之间往索引文件里塞一行"别人的"记录，
   下一轮采完它必须还在。**

2. **坏行不许静默跳过**。`read_index()` 返回 `(recs, bad)`，`bad` 是读不动的行号。
   半行 JSON（进程被 kill 在写一半）是最常见的成因，而"索引是空的"和
   "你的索引坏了"在页面上长得一模一样 —— 所以 `status()` 必须把 `bad` 摆出来。

3. **数一律过 `json_safe()` 再落盘**。上游（`datasource`）给的是 pandas/numpy
   的标量，`np.int64` 不是 `int` 的子类 ⇒ `json.dumps` 抛 TypeError；
   而 `NaN` 会被 `json.dumps` 写成**裸 `NaN`**，那不是合法 JSON，
   前端 `JSON.parse` 当场炸（Python 自己读得回来，所以本地测不出来）。
   `np.float64('nan')` 更坏：它进 `float` 分支、`math.isfinite` 拦得住，
   但 `np.int64` 走的是 `.item()` 那条路 —— 两条路都要有，见 `json_safe`。
"""

import datetime as dt
import json
import math
import os
import re
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT = os.path.dirname(_HERE)
_REPO = os.path.dirname(_AGENT)

DIR_ENV = 'LICAI_COLLECT_DIR'          # 落盘根目录（探针靠它把数据引到临时目录）
FRAMES_ENV = 'LICAI_COLLECT_FRAMES'    # '0' = 不写快照载荷（只记索引）
RETAIN_DAYS_ENV = 'LICAI_COLLECT_RETAIN_DAYS'   # 帧文件的保留期（天）
MAX_BYTES_ENV = 'LICAI_COLLECT_MAX_BYTES'       # 帧文件总量的上限（字节）
MIN_AGE_ENV = 'LICAI_COLLECT_PRUNE_MIN_AGE'     # 多久以内被写过的帧文件先不删（秒）

INDEX_NAME = 'index.jsonl'
FRAMES_DIR = 'frames'
INDEX_V = 1

# 保留期的两个默认值 —— 理由写在模块头「保留期」那一节（1.91 GB/天 ⇒ 5.7 GB
# 稳态 / 3 天是跨得过一个周末的最小值；8 GiB 是挡"间隔被人调小了"的兜底）。
DEFAULT_RETAIN_DAYS = 3
DEFAULT_MAX_BYTES = 8 * 1024 ** 3
DEFAULT_MIN_AGE = 600.0

# 帧文件名：`YYYY-MM-DD.<market>.jsonl`（见 `frame_path`）。★严格锚定整串 ——
# 它的作用**不是**"找出要删的"，是"排除不许删的"：任何一条不匹配的东西都算
# "别人的文件"，一个也不碰。
_FRAME_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\.([A-Za-z0-9_]+)\.jsonl$')

# 只保证**同进程**内两个线程不会把两行的字节交错在一起 —— 跨写者的安全
# 靠的是"只追加"这个动作本身（`open(p,'a')` 的单次 write 在同一文件系统上
# 对 < PIPE_BUF 的写是原子的；本项目的行都在 1KB 上下，够用）。
# 不用文件锁：锁文件本身又是一份要在多写者之间共享的状态。
_LOCK = threading.Lock()


def root(p=None):
    """落盘根目录。★**每次调用现读 env**，不在 import 时快照 —— 快照的话
    探针就没法把数据引到临时目录里跑，只能往真仓库的 `data/collect/` 里灌测试数据。"""
    if p:
        return p
    return os.path.abspath(os.environ.get(DIR_ENV) or os.path.join(_REPO, 'data', 'collect'))


def index_path(p=None):
    return os.path.join(root(p), INDEX_NAME)


def frames_dir(p=None):
    return os.path.join(root(p), FRAMES_DIR)


def frame_path(market, day=None, p=None):
    day = day or dt.date.today().strftime('%Y-%m-%d')
    return os.path.join(frames_dir(p), '%s.%s.jsonl' % (day, market))


def frames_enabled():
    """载荷落盘开关，默认开。关掉只影响快照载荷，索引照记
    （索引是页面的数据源，"关掉载荷"不该把页面的时间线也一起关掉）。"""
    return (os.environ.get(FRAMES_ENV) or '1').strip().lower() not in ('0', 'false', 'no', 'off')


# ── JSON 安全 ──────────────────────────────────────────────────────────

def json_safe(v):
    """任意上游值 → 一定能过 `json.dumps(..., allow_nan=False)` 的值。

    ★这不是"顺手清洗一下"：`datasource.realtime()` 原样给出的是上游的东西
      （pandas/numpy 标量、NaN、None 混在一起），直接 `json.dumps` 写盘的话
      ①`np.int64` → TypeError（整个 `once()` 崩），
      ②NaN → 写出一份**别的 JSON 解析器读不动**的文件（本地还测不出来）。
    ★非有限数一律 None，**不是 0**，也不做 round —— 见 `_num` 的分工：
      `_num` 管"浮点要不要保留 4 位"，这里只管"这个值能不能进 JSON"。
    """
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [json_safe(x) for x in v]
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    item = getattr(v, 'item', None)          # numpy 标量 / pandas 标量
    if callable(item):
        try:
            return json_safe(item())
        except Exception:                    # noqa: BLE001 —— 有些 .item() 要参数
            pass
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return str(v)                        # Timestamp 之类：字符串化不丢信息，不编数


def _line(rec):
    """一条记录 → 一行。`ensure_ascii=False` + 紧凑分隔符：中文写成 \\uXXXX
    会让日志没法用眼睛看、也没法 grep（照抄 `ledger._line` 的理由）。"""
    return json.dumps(json_safe(rec), ensure_ascii=False, separators=(',', ':'),
                      allow_nan=False) + '\n'


# ── 写（只追加）────────────────────────────────────────────────────────

def _append_line(path, rec):
    """→ (ok, err)。**永不抛**：落盘失败不该把这一轮采到的东西一起弄丢，
    但也**不许静默** —— 调用方必须把这个结果摆到台面上（见 `__init__.once`）。

    ★`_line(rec)`（也就是 `json_safe` + `json.dumps(allow_nan=False)`）**必须写在
      try 里面**，不是在调用方那里先算好再传进来：`allow_nan=False` 碰到 NaN 抛
      ValueError、`json_safe` 漏掉一个 numpy 标量抛 TypeError —— 这两种都得走
      "落盘失败，但这一轮采到的东西不丢"那条路。写在调用方的话，一个脏字段
      会把整轮的返回值一起带走（`_probe.py` C9 就钉在这条路上：注入把
      `json_safe` 变成恒等函数，脏载荷必须**变成一条 red 的落盘错误**，不是崩）。
    """
    try:
        line = _line(rec)
    except (ValueError, TypeError) as e:
        return False, '%s: %s' % (e.__class__.__name__, e)
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with _LOCK:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())         # 崩溃前落盘，别留在页缓存里
        return True, None
    except OSError as e:
        return False, '%s: %s' % (e.__class__.__name__, e)


def append_index(rec, p=None):
    """追加一条索引。→ `(ok, err)`，不抛。"""
    return _append_line(index_path(p), rec)


def append_frame(market, rows, ts, kind='realtime', p=None, day=None, extra=None):
    """追加一轮某个市场的载荷。→ `(ok, err, n)`，不抛。

    `rows` 会被 `json_safe()` 逐行过一遍（**必须**：见模块头第 3 条）。
    关掉载荷（`LICAI_COLLECT_FRAMES=0`）时返回 `(True, None, n)` ——
    "没写"不是"写失败"，这两件事在页面上要分得开。
    """
    rows = list(rows or [])
    if not frames_enabled():
        return True, None, len(rows)
    body = {'v': INDEX_V, 'ts': ts, 'market': market, 'kind': kind,
            'n': len(rows), 'rows': [json_safe(r) for r in (rows or [])]}
    if extra:
        body.update(json_safe(extra))
    ok, err = _append_line(frame_path(market, day, p), body)
    return ok, err, len(rows)


# ── 读 ────────────────────────────────────────────────────────────────

def read_index(p=None, limit=None):
    """→ `(recs, bad)`。`bad` = 读不动的**行号**列表（不是条数：要能去 tail 那一行）。

    `limit` 给的是**最近 N 条**（升序返回）。★`limit <= 0` 返回空表 ——
    这一条是显式写出来的，不是"顺手"：`recs[-0:]` 在 Python 里等于 `recs[0:]`，
    也就是**整个文件**，于是"最近 0 轮"会返回全部。这个坑 `_probe.py` 的 C5 钉着。
    """
    path = index_path(p)
    recs, bad = [], []
    if not os.path.exists(path):
        return recs, bad
    try:
        with open(path, encoding='utf-8') as f:
            for i, ln in enumerate(f, 1):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    bad.append(i)
                    continue
                if isinstance(r, dict):
                    recs.append(r)
                else:
                    bad.append(i)            # 合法 JSON 但不是对象：形状不对，也算坏行
    except OSError:
        return recs, [-1]
    if limit is not None:
        n = int(limit)
        if n <= 0:
            return [], bad
        recs = recs[-n:]
    return recs, bad


def inventory(p=None):
    """盘上每个文件的 `{相对路径: 字节数}`。**只读**。

    给两个地方用：①探针的"只追加"判据（前缀比对）；②`status()`...
    不，`status()` **不用它** —— 那会把每个请求变成一次全目录扫描。
    它只给探针和排查用。
    """
    base = root(p)
    out = {}
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            try:
                out[os.path.relpath(fp, base)] = os.path.getsize(fp)
            except OSError:
                out[os.path.relpath(fp, base)] = -1
    return out


def bytes_of(p=None):
    """`{相对路径: 原始字节}`。判据要的是**字节**，不是大小 ——
    大小一样而内容不同（改一个字符再改回来、或者整份重写成了另一份同样长的东西）
    是这条纪律最想抓的形态之一。只在探针/排查里用，别在请求路径上调。"""
    base = root(p)
    out = {}
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, 'rb') as f:
                    out[os.path.relpath(fp, base)] = f.read()
            except OSError:
                out[os.path.relpath(fp, base)] = b''
    return out


def now_str():
    return time.strftime('%Y-%m-%d %H:%M:%S')


# ── 保留期（轮转）─────────────────────────────────────────────────────
# ★这一节的四条边界写在**模块头**「保留期」那一节里（为什么默认 3 天 / 8 GiB、
#   为什么只在写路径上、为什么不删"刚被写过"的）。这里只放实现。

def _env_int(name, default, lo, hi):
    """env 里一个整数，夹在 `[lo, hi]`。★空串/缺失才走默认 —— **合法的 0 不是
    "没给"**（`RETAIN_DAYS=0` 是"只留今天"这个明确诉求，被 `or` 吞掉就成了别的意思）。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return int(default)
    try:
        return max(int(lo), min(int(hi), int(float(raw))))
    except (TypeError, ValueError):
        return int(default)


def _retain_days():
    """帧文件的保留期（天）。★上限 3650 不是"顺手夹一下"：`date - timedelta(999999999)`
    会 OverflowError，而 `prune()` 是**不许抛**的。夹过的值如实出现在返回值的
    `retain_days` 里（不是静默改口 —— 那和 `ring()` 里"给我 5 轮变 120 轮"是同一个病）。"""
    return _env_int(RETAIN_DAYS_ENV, DEFAULT_RETAIN_DAYS, 0, 3650)


def _max_bytes():
    """帧文件总量的上限（字节）。只读，给排查用。0 = 不按字节清。"""
    return _env_int(MAX_BYTES_ENV, DEFAULT_MAX_BYTES, 0, 1 << 50)


def _min_age():
    """多久以内被写过的帧文件本轮不删（秒）。只读，给排查用。"""
    return _env_int(MIN_AGE_ENV, DEFAULT_MIN_AGE, 0, 86400)


def _inside(path, base):
    """`path` 在 `base` 里面吗。按 `realpath` 比（挡 symlink 绕出去）。只读。"""
    try:
        rp, rb = os.path.realpath(path), os.path.realpath(base)
    except OSError:
        return False
    return rp == rb or rp.startswith(rb.rstrip(os.sep) + os.sep)


def _day_of(name):
    """帧文件名 → `datetime.date`。名字不像帧文件（或日子非法）→ `None`。"""
    m = _FRAME_RE.match(name)
    if not m:
        return None
    try:
        y, mo, d = (int(x) for x in m.group(1).split('-'))
        return dt.date(y, mo, d)
    except ValueError:
        return None            # `2026-13-01` / `2026-02-30`：看着像，日子不是


def _mb(n):
    return '%.2f MB' % ((n or 0) / 1048576.0)


def _prune_note(o):
    """清理结果的**人话**。★它存在的唯一理由就是"不许静默删东西" ——
    ★"没删"至少有**四种**原因（目录空的 / 都还在保留期内 / 刚被写过 / 名字不认识），
    它们在页面上不许长得一样。"""
    tail = []
    if o['foreign']:
        tail.append('frames/ 里还有 %d 个不像帧文件的条目，一个也没碰' % o['foreign'])
    if o['denied']:
        tail.append('%d 个候选没通过路径包含检查（不在 %s 里面）' % (o['denied'], FRAMES_DIR))
    if o['guarded']:
        tail.append('%d 个已过期但 %ds 内被写过 —— 本轮不删（另一个进程可能正追加它），'
                    '下一轮会再试' % (o['guarded'], o['min_age']))
    if o['failed']:
        tail.append('%d 个删失败（见 failed）' % len(o['failed']))
    if o['removed']:
        why = {}
        for r in o['removed']:
            why[r['why']] = why.get(r['why'], 0) + 1
        head = ('清掉了 %d 个过期帧文件、共 %s（%s）；frames/ 还剩 %d 个、%s'
                % (len(o['removed']), _mb(o['removed_bytes']),
                   '、'.join('%s×%d' % (k, v) for k, v in sorted(why.items())),
                   o['kept'], _mb(o['kept_bytes'])))
    elif not o['scanned']:
        head = '没有要清的：这个根下面还没有任何帧文件'
    else:
        head = ('没有要清的：%d 个帧文件里最早的 %s 还在保留期（%d 天，cutoff %s）内'
                % (o['scanned'], o['oldest'], o['retain_days'], o['cutoff']))
    return '；'.join([head] + tail)


def prune(p=None, now=None, retain_days=None, max_bytes=None, min_age=None):
    """**保留期**：删掉 `frames/` 里过期的按天帧文件。→ 一条记账 dict，**永不抛**。

    ★只在**写入路径**上被调（`__init__.once`）。`status()` 不许调它 —— 那是只读
      接口，"看一眼状态"顺手删盘是最坏的一种脏。
    ★"永不抛"的理由同 `_append_line`：删不掉旧数据是小事，把这一轮**采到的**
      东西一起带走才是大事（`_append_line` 的 docstring 记着同一个坑）。
    ★**只删 `frames/` 里的帧文件**：`index.jsonl` 是页面的时间线、不是载荷，
      本函数从不看它 —— 它是"只追加"那条纪律的锚（`_probe.py` C8 钉着）。

    三个可选参数只为**能被单独测**存在（`now` 尤其：判据要能在不伪造 mtime 的
    前提下说"今天是哪天"），生产走 env（`LICAI_COLLECT_RETAIN_DAYS` /
    `LICAI_COLLECT_MAX_BYTES` / `LICAI_COLLECT_PRUNE_MIN_AGE`）。

    → `{ok, dir, today, retain_days, max_bytes, min_age, cutoff, scanned, removed,
        removed_bytes, kept, kept_bytes, protected, oldest, foreign, denied,
        guarded, failed, bytes_before, bytes_after, note}`。
      `removed` 每项是 `{file, day, bytes, why}`、`why ∈ {expired, over_size_cap}`
      —— **删了哪一条、按哪条闸删的**都要看得见（含 `file`，不是只给个计数）。
    """
    base, fdir = root(p), frames_dir(p)
    days = _retain_days() if retain_days is None else int(retain_days)
    cap = _max_bytes() if max_bytes is None else int(max_bytes)
    age = _min_age() if min_age is None else float(min_age)
    today = now if isinstance(now, dt.date) else dt.date.today()
    cutoff = today - dt.timedelta(days=days)

    out = {'ok': True, 'dir': fdir, 'today': today.isoformat(),
           'retain_days': days, 'max_bytes': cap, 'min_age': age,
           'cutoff': cutoff.isoformat(),
           'scanned': 0, 'removed': [], 'removed_bytes': 0,
           'kept': 0, 'kept_bytes': 0, 'protected': 0, 'oldest': None,
           'foreign': 0, 'denied': 0, 'guarded': 0, 'failed': [],
           'bytes_before': 0, 'bytes_after': 0, 'note': ''}

    try:
        names = sorted(os.listdir(fdir))
    except FileNotFoundError:
        out['note'] = '没有要清的：%s 还不存在（这个根下还没采过）' % FRAMES_DIR
        return json_safe(out)
    except OSError as e:
        out['ok'] = False
        out['note'] = '没清成：读不了 %s（%s: %s）' % (fdir, e.__class__.__name__, e)
        return json_safe(out)

    # 1) 扫描：谁**长得像**帧文件。★这一步是白名单，不是黑名单 ——
    #    名字不认识的东西一个也不进候选（`_FRAME_RE` 那条注释）。
    recs = []
    for name in names:
        day = _day_of(name)
        if day is None:
            out['foreign'] += 1
            continue
        fp = os.path.join(fdir, name)
        if not _inside(fp, base):
            out['denied'] += 1          # 正常永远是 0；发生了要记账，不是"反正不会"
            continue
        try:
            st = os.stat(fp)
        except OSError:
            out['foreign'] += 1
            continue
        if not os.path.isfile(fp):
            out['foreign'] += 1         # 目录/fifo 之类：不是帧文件
            continue
        out['scanned'] += 1
        out['bytes_before'] += st.st_size
        recs.append({'file': os.path.relpath(fp, base), 'day': day.isoformat(),
                     'day_obj': day, 'bytes': int(st.st_size), 'mtime': st.st_mtime,
                     'path': fp, 'action': 'keep', 'why': 'in_window'})

    # 2) 天数闸：★比的是**文件名上的日子**，不是 mtime（mtime 被任何一次追加刷新）。
    for r in recs:
        if r['day_obj'] < cutoff:
            r['action'], r['why'] = 'delete', 'expired'

    # 3) 新鲜度闸：刚被写过的先不删（跨进程那个窗口，见模块头第 4 条）。
    nowt = time.time()
    for r in recs:
        if r['action'] == 'delete' and (nowt - r['mtime']) < age:
            r['action'], r['why'] = 'keep', 'expired_but_just_written'
            out['guarded'] += 1

    # 4) 字节闸（兜底）：从最老的一天起删到不超上限。★候选里**排除今天**
    #    —— `frame_path()` 只写今天那个文件，它此刻可能正被另一个进程追加。
    if cap > 0:
        live = sum(r['bytes'] for r in recs if r['action'] == 'keep')
        evictable = [r for r in recs if r['action'] == 'keep'
                     and r['why'] == 'in_window' and r['day_obj'] < today]
        evictable.sort(key=lambda r: (r['day_obj'], r['file']))
        for r in evictable:
            if live <= cap:
                break
            r['action'], r['why'] = 'delete', 'over_size_cap'
            live -= r['bytes']

    # 5) 执行（先查包含再删；删不掉的进 `failed`，绝不抛）。
    for r in recs:
        if r['action'] != 'delete':
            continue
        if not _inside(r['path'], base):
            out['denied'] += 1
            continue
        try:
            os.remove(r['path'])
        except OSError as e:
            out['failed'].append({'file': r['file'], 'day': r['day'],
                                  'bytes': r['bytes'], 'why': r['why'],
                                  'err': '%s: %s' % (e.__class__.__name__, e)})
            continue
        out['removed'].append({'file': r['file'], 'day': r['day'],
                               'bytes': r['bytes'], 'why': r['why']})
        out['removed_bytes'] += r['bytes']

    keep = [r for r in recs if r['action'] == 'keep']
    out['kept'] = len(keep)
    out['kept_bytes'] = sum(r['bytes'] for r in keep)
    out['protected'] = sum(1 for r in recs if r['day_obj'] >= today)
    out['oldest'] = min((r['day'] for r in keep), default=None)
    out['bytes_after'] = out['kept_bytes']
    out['note'] = _prune_note(out)
    return json_safe(out)
