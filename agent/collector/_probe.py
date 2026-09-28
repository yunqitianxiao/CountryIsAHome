#!/usr/bin/env python3
"""常驻采集器的判据 + **故障注入**。

用法（在项目根目录下）：

    .venv/bin/python agent/collector/_probe.py                # 干净跑 + 逐条注入
    .venv/bin/python agent/collector/_probe.py --no-inject    # 只跑干净副本
    .venv/bin/python agent/collector/_probe.py --only C2,C8    # 只跑这几条
    .venv/bin/python agent/collector/_probe.py --live          # 额外跑一轮**真源**（网络，慢，只当证据）

退出码：
    **0** 干净副本全绿 **且** 每条判据都被对应的注入打红过
    **1** 有干净判据红了 / 有注入没能打红 / 有守卫不满意
    **3** 有东西**没跑**（注入够不着锚、或者判据的前置条件不满足）—— 这是独立的一档：
          「没跑」绝不能折进「通过」。一个锚被改名的判据在旧写法下会静默变绿，
          而它守的那个洞原样还在（这条账在本项目的 `fault-injection-*` 里记了 40 多条）。

## 为什么每一条判据都要配一条注入

判据自己会坏。`--selftest` 只验"注入能打红"是不够的：判据自己坏了（比如正则太宽、
比如锚在 but 恒真的表达式上）也照样报"成功打红"。所以这里**两件事都验**：

  · 干净副本上每条判据必须是**绿的**；
  · 打上一条**能把它打红**的注入之后必须是**红的**。

★注入打在 `collector` / `collector.store` / `collector.sources` 的**模块属性**上，
  不是我抄一份逻辑来打 —— 抄一份的话，注入测的是我抄的那份（"拿 A 去比 B"）。
  为了让这件事成立，`__init__.py` 里一律走 `store.read_index(...)` 这种
  "模块 + 属性"的调法，而不是 `from .store import read_index`：后者在 import 时
  就把名字绑死了，注入**够不着**被测代码（症状是"注入没能打红"，而判据看着是好的）。

★`hasattr` 判锚在不在：锚被改名/删掉时**报 ⊘ 并 rc=3**，不报绿。
  一个"锚没了所以注入静默消失"的判据，比没有判据更坏 —— 它看起来上了保险。

## 判据一览（每条后面的括号里是它的注入）

  C1  三个入口的返回形状（`ring` 必须是**裸 list**）           （让 ring 回 {'ok':True,'items':[…]})
  C2  `status()` **只读**：跑三次前后盘上字节一个都不许变        （让 read_index 顺手写一行）
  C3  数据层没就绪 ⇒ ok=False + 人话原因，且**不许**退回桩      （让 backend 偷偷回落到合成数据）
  C4  「包在但 import 炸了」≠「包还没落地」                     （把 BROKEN 报成 NOT_READY）
  C5  `ring(0)` / `ring(-1)` = 空表（`recs[-0:]` 是**全部**）    （把 limit<=0 的守卫拿掉）
  C6  `ring(limit)` = **最近** N 条、按时间升序                  （把 [-n:] 写成 [:n]）
  C7  空目录上不编数（ticks=0 / rows=None，一个文件都不建）       （文件不在时编一条空轮出来）
  C8  **只追加**：两轮之间塞一行"别人的"，下一轮采完它必须还在     （整份重写，只写回自己的记录）
  C9  载荷落盘是**严格 JSON**（NaN/inf→null，numpy→原生数）      （把 json_safe 变成恒等函数）
  C10 每次外部调用都有超时                                     （把 call_with_timeout 变成直接调）
  C11 碰数据层之前先调 `feed.proxy_env_fix()`（国内直连）        （把这行删掉）
  C12 `running` 三态：没说 ≠ 说了没跑                            （让 _running_field 恒 False）
  C13 三个入口的返回值都过 `allow_nan=False`                     （把 json_safe 变成恒等函数）
  C14 scope 里不认识的词要报错（且 msg 要带上原因）               （静默丢掉未知 token）
  C15 资讯那一层炸了不许把整轮带走（且要在账上留一条红的）         （拆掉 _safe_news 那层围栏）
  C16 索引落盘失败 ≠ "没采到"：rows 保持真值、ok 变 False        （把落盘结果按成功处理）
  C17 `_num` 复用的是 feed 那一个对象，不是一份像的              （"取不到就当 0"）
  C19 说明里点名的 scope 词，parser 必须真认                     （往 SCOPE_HELP 里塞一个不认的词）
  C20 只采资讯的那一轮不许报成"什么都没采到"（rows=None 而 ok=True）（把 ok 退回 `rows is not None`）
  C21 **同一件事的第二个落点**：页面上那句话也不许反着说（各自拿 rows 判了一次）（把 msg 退回"rows 是 None 就说没采到"）

外加三条**守卫**（import 无副作用 / 探针没污染真仓库 / 跨进程并发追加）——
它们**没有注入**（写不出注入的判据按本项目纪律降级成证据并写明"没人替它把关"），
其中第一条自带**对照物**：因为"没量到写盘"有两种来源 —— 真没写，和**尺子量不出写**。

★假源（`LICAI_COLLECT_SYNTH`）只走**显式开关**，且**不挂在降级路径上** ——
  "真源取不到"绝不能自动变成"用假源"，那等于把编的数据当真数据发出去。
  C3 的注入就是拿这条打的。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
if AGENT not in sys.path:
    sys.path.insert(0, AGENT)

try:
    import numpy as np
except Exception:                                                # noqa: BLE001
    np = None

import collector                                                 # noqa: E402
from collector import sources, store, synth                      # noqa: E402

# ══ 小工具 ═════════════════════════════════════════════════════════════

ENVS = ('LICAI_COLLECT_DIR', 'LICAI_COLLECT_SYNTH', 'LICAI_COLLECT_SYNTH_FAIL',
        'LICAI_COLLECT_TIMEOUT', 'LICAI_COLLECT_BUDGET_MS', 'LICAI_COLLECT_NEWS',
        'LICAI_COLLECT_FRAMES', 'LICAI_COLLECT_INTERVAL', 'LICAI_COLLECT_SCOPE',
        'LICAI_NEWS_STUB', 'LICAI_FEED_STUB')


class NotRun(Exception):
    """这条**没跑**（前置条件不满足 / 注入够不着锚）。→ rc=3，**不许折进绿**。"""


@contextlib.contextmanager
def sandbox(**env):
    """给一条判据一个只属于它的落盘目录 + 一套干净的 env。

    ★必须隔离：不隔离的话，判据会把测试数据灌进仓库真有的 `data/collect/`，
      而那是"用测试数据污染交付物"（`ledger.py` 顶上那条账写了同一个坑）。
    """
    d = tempfile.mkdtemp(prefix='licai-collect-probe-')
    old = {k: os.environ.get(k) for k in ENVS}
    for k in ENVS:
        os.environ.pop(k, None)
    os.environ['LICAI_COLLECT_DIR'] = d
    os.environ['LICAI_COLLECT_SYNTH'] = '1'          # 默认走桩（确定性）
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    try:
        yield d
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(d, ignore_errors=True)


@contextlib.contextmanager
def patch(obj, attr, value):
    """把 `obj.attr` 换成 `value`，出来再换回去。**不新建对象**：
    被测代码看到的是同一个模块。"""
    if not hasattr(obj, attr):
        raise NotRun('注入够不着锚：%s 上没有 %r' % (getattr(obj, '__name__', obj), attr))
    old = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield old
    finally:
        setattr(obj, attr, old)


def raiser(exc):
    def _f(*_a, **_k):
        raise exc
    return _f


def _no_const(c):
    raise ValueError('JSON 里有裸常量 %r —— 那不是合法 JSON' % c)


def strict_dumps(obj):
    """→ None 表示过；否则一句为什么。
    ★`json.dumps` 默认**允许** NaN（写成裸 `NaN`），所以只调它是不够的。"""
    try:
        json.dumps(obj, ensure_ascii=False, allow_nan=False)
        return None
    except (ValueError, TypeError) as e:
        return '%s: %s' % (type(e).__name__, str(e)[:140])


def strict_loads(s):
    """→ `(obj, err)`。★`json.loads` 默认**接受**裸 NaN/inf，必须用 parse_constant 拒掉。"""
    try:
        return json.loads(s, parse_constant=_no_const), None
    except (ValueError, TypeError) as e:
        return None, str(e)[:140]


class Sub:
    """一条判据里的若干小断言。★全部跑完再汇总 —— 第一处失败就 return 的话，
    后面那些"顺便也想验"的事实永远测不到，而它们往往正是下一次要踩的坑。"""

    def __init__(self):
        self.bad, self.notes = [], []

    def ok(self, cond, why=''):
        if not cond:
            self.bad.append(why or '断言不成立')
        return bool(cond)

    def note(self, s):
        self.notes.append(s)

    def done(self):
        return (not self.bad), ('; '.join(self.bad) if self.bad else '; '.join(self.notes))


# ══ 判据用的假数据源 ════════════════════════════════════════════════════

class DirtySource:
    """脏数据源：上游真的会给出的那几种脏值。
    ★存在的理由：桩（`synth`）给的全是干净值，于是"脏值清洗"那条路在桩上
      **永远走不到** —— 一条走不到的代码路径等于没写。"""

    name = 'DIRTY（探针的脏数据源：NaN / ±inf / numpy 标量 / None）'

    def __init__(self):
        self.n_calls = 0

    def realtime(self, market='cn'):
        if np is None:
            raise NotRun('numpy 不在，C9 的脏值造不出来')
        self.n_calls += 1
        return [
            {'code': 'd1', 'name': '脏一', 'market': market,
             'price': float('nan'), 'open': float('inf'), 'high': np.float64('nan'),
             'low': np.int64(7), 'prev_close': None, 'pct': -float('inf'),
             'volume': np.int64(123), 'amount': np.float32(1.5), 'turnover': None,
             'pe': float('nan'), 'ts': '2026-09-22 10:00:00'},
            {'code': 'd2', 'name': '脏二', 'market': market,
             'price': 1.25, 'open': None, 'high': np.float64(2.5), 'low': None,
             'prev_close': None, 'pct': 0, 'volume': np.int64(9), 'amount': None,
             'turnover': 3, 'pe': None, 'ts': '2026-09-22 10:00:00'},
        ]

    def boards(self, kind='concept'):
        return []


class HangingSource:
    """会挂住的数据源。★存在的理由：**"挂住"和"报错"在页面上长得一模一样**
    （都是没动静），而挂住更糟 —— 它把 Flask 的工作线程占着不放。"""

    name = 'HANG（探针的会挂住的数据源）'

    def realtime(self, market='cn'):
        time.sleep(3.0)
        return [{'code': 'h1', 'price': 1.0, 'ts': '2026-09-22 10:00:00'}]

    def boards(self, kind='concept'):
        return []


def _bk(mod, name=None):
    return {'ok': True, 'mod': mod, 'name': name or getattr(mod, 'name', 'STUB'),
            'code': None, 'err': None, 'hint': ''}


# ══ 判据 ═══════════════════════════════════════════════════════════════

CRITERIA = []


def criterion(cid, title, inject=None):
    def deco(fn):
        CRITERIA.append({'id': cid, 'title': title, 'run': fn, 'inject': inject})
        return fn
    return deco


# ── C1 形状 ────────────────────────────────────────────────────────────
def _c1():
    s = Sub()
    with sandbox():
        r = collector.once('cn')
        s.ok(isinstance(r, dict), 'once() 不是 dict，是 %s（路由要的是带 ok 的 dict）'
             % type(r).__name__)
        s.ok(isinstance(r, dict) and 'ok' in r, 'once() 没带 ok')
        st = collector.status()
        s.ok(isinstance(st, dict) and 'ok' in st, 'status() 不是带 ok 的 dict')
        rg = collector.ring()
        s.ok(isinstance(rg, list), '★ring() 不是**裸 list**，是 %s —— 包成 {ok:True,items:[…]} '
             '是 app.py 那一层干的，形状反了页面就是空的' % type(rg).__name__)
        if isinstance(rg, list) and rg:
            it = rg[-1]
            for k in ('ts', 'market', 'rows', 'ms', 'ok', 'err', 'sources'):
                s.ok(k in it, 'RingItem 少了 %r' % k)
            for x in (it.get('sources') or []):
                for k in ('id', 'ok', 'n', 'ms', 'err'):
                    s.ok(k in x, 'SourceHealth 少了 %r' % k)
            s.ok(isinstance(it.get('sources'), list) and it['sources'],
                 'RingItem.sources 是空的 —— 页面上"哪个源在挂"就没有证据')
        s.ok(isinstance(r.get('sources'), list) and r['sources'],
             'once() 的 sources 是空的')
    return s.done()


# ── C2 status() 只读 ───────────────────────────────────────────────────
def _c2():
    s = Sub()
    with sandbox() as d:
        collector.once('cn')
        b1 = store.bytes_of(d)
        n1 = sorted(os.listdir(d))
        for _ in range(3):
            collector.status()
        b2 = store.bytes_of(d)
        if b1 != b2:
            diff = sorted(k for k in set(b1) | set(b2) if b1.get(k) != b2.get(k))
            s.ok(False, '★status() 动了盘（%s）—— 只读接口偷偷写盘是这块最容易犯的错' % diff)
        else:
            s.note('3 次 status() 前后 %d 个文件的字节逐字节相同' % len(b1))
        s.ok(sorted(os.listdir(d)) == n1, 'status() 之后目录里的东西变了：%s -> %s'
             % (n1, sorted(os.listdir(d))))
        collector.ring(5)
        s.ok(store.bytes_of(d) == b2, 'ring() 动了盘')
        collector.path()
        s.ok(store.bytes_of(d) == b2, 'path() 动了盘')
    return s.done()


def _c2_inject():
    real = store.read_index

    def bad(p=None, limit=None):
        store.append_index({'v': 1, 'ts': store.now_str(), 'scope': 'injected-write',
                            'ok': True, 'market': 'zz', 'rows': 0, 'ms': 0.0, 'err': None,
                            'sources': []})
        return real(p, limit)

    return (store, 'read_index', bad)


# ── C3 降级不说谎 ──────────────────────────────────────────────────────
def _c3():
    s = Sub()
    with sandbox(LICAI_COLLECT_SYNTH=None) as d:                 # ★关掉桩
        # 让"包还没落地"这条支路出现。**不去动 `agent/datasource/`** ——
        # 那是别人的文件、而且正在被另一个代理写着（见 sources._module_found 的说明）。
        with patch(sources, '_module_found', lambda name: False):
            bl = sources.backend()
            s.ok(bl.get('code') == 'NOT_READY',
                 '数据层没落地时的码不是 NOT_READY，而是 %r' % (bl.get('code'),))
            s.ok(bool(bl.get('err')), 'NOT_READY 却没给原因')
            s.ok(bool(bl.get('hint')), 'NOT_READY 却没给 hint')
            r = collector.once('cn')
            s.ok(r.get('ok') is False,
                 '★数据源没就绪时 once() 竟然 ok=%r' % (r.get('ok'),))
            s.ok(r.get('rows') is None,
                 '★rows 不是 None 而是 %r —— "没采到"被写成了别的数' % (r.get('rows'),))
            s.ok(bool(r.get('err')), 'ok=False 却没给 err（人话原因）')
            s.ok('就绪' in (r.get('err') or '') or '未落地' in (r.get('err') or ''),
                 'err 不是人话：%r' % (r.get('err'),))
            s.ok(r.get('backend_code') == 'NOT_READY',
                 'backend_code 不是 NOT_READY：%r' % (r.get('backend_code'),))
            fd = os.path.join(d, 'frames')
            files = os.listdir(fd) if os.path.isdir(fd) else []
            s.ok(not files, '★降级路径下竟然写了载荷（%s）—— 那等于编了数据' % files)
            recs, _bad = store.read_index()
            s.ok(len(recs) == 1 and recs[0].get('ok') is False,
                 '没成的那一轮没进索引 —— 页面的时间线上"这一轮没成"就看不见了')
    return s.done()


def _c3_inject():
    """★把"真源取不到就换一份数据出来"这条禁忌做出来。
    这是本模块**最不能出**的东西：页面上那个数是编的，而用户看不出来。"""

    def bad(force=None):
        if force is not None:
            return force
        return _bk(synth, 'SYNTH（注入：真源取不到就偷偷换成合成数据）')

    return (sources, 'backend', bad)


# ── C4 「包坏了」≠「还没做」 ───────────────────────────────────────────
def _c4():
    s = Sub()
    with sandbox(LICAI_COLLECT_SYNTH=None):
        with patch(sources, '_module_found', lambda name: True), \
             patch(sources, '_import_module', raiser(ImportError('boom-injected'))):
            bl = sources.backend()
            s.ok(bl.get('code') == 'DATASOURCE_BROKEN',
                 '★import 时就炸了，却报成 %r —— 一个真 bug 会伪装成"还没做"，'
                 '永远没人去修' % (bl.get('code'),))
            s.ok('不' in (bl.get('hint') or '') or 'bug' in (bl.get('hint') or ''),
                 'hint 没说清这是真 bug：%r' % (bl.get('hint'),))
            s.ok('boom-injected' in (bl.get('err') or ''),
                 '没把真异常带上来：%r' % (bl.get('err'),))
            r = collector.once('cn')
            s.ok(r.get('ok') is False and r.get('backend_code') == 'DATASOURCE_BROKEN',
                 'once() 那条路没把 BROKEN 如实带出来：%r / %r'
                 % (r.get('ok'), r.get('backend_code')))
        # 反面：同一个函数在"包没落地"时必须报 NOT_READY（两个码不是同一个）
        with patch(sources, '_module_found', lambda name: False):
            s.ok(sources.backend().get('code') == 'NOT_READY',
                 '两个分支给了同一个码 —— 那就等于没分开报')
    return s.done()


def _c4_inject():
    return (sources, '_broken',
            lambda e: sources._not_ready('（注入：把"坏了"说成"还没做"）', ''))


# ── C5 ring(limit) 的 0/负边界 ─────────────────────────────────────────
def _c5():
    s = Sub()
    with sandbox():
        for _ in range(3):
            collector.once('cn')
        s.ok(collector.ring(0) == [],
             '★ring(0) 不是空表，回了 %d 条（`recs[-0:]` 在 Python 里等于整个文件）'
             % len(collector.ring(0)))
        s.ok(collector.ring(-1) == [], 'ring(-1) 不是空表：%d 条' % len(collector.ring(-1)))
        s.ok(collector.ring(-999) == [], 'ring(-999) 不是空表')
        s.ok(len(collector.ring(1)) == 1, 'ring(1) 不是 1 条')
        s.ok(len(collector.ring(None)) == 3, 'ring(None) 没走默认值')
        try:
            collector.ring('abc')
            s.ok(False, '★ring("abc") 没抛 —— 把"给我看 5 轮"悄悄改成别的数是对用户的篡改')
        except TypeError:
            s.note('ring("abc") 抛 TypeError（路由会接住它回 400）')
        except Exception as e:                                   # noqa: BLE001
            s.ok(False, 'ring("abc") 抛的是 %s，不是 TypeError' % type(e).__name__)
    return s.done()


def _c5_inject():
    """把 `limit <= 0` 那条守卫拿掉 —— 也就是那个经典写法。"""
    real = store.read_index

    def bad(p=None, limit=None):
        recs, badl = real(p, None)
        if limit is not None:
            recs = recs[-int(limit):]            # ★limit=0 时 = recs[0:] = 全部
        return recs, badl

    return (store, 'read_index', bad)


# ── C6 ring(limit) 给的是**最近** N 条、升序 ───────────────────────────
def _c6():
    s = Sub()
    with sandbox():
        for sc in ('cn', 'hk', 'us'):            # 用 scope 把三轮区分开，不用 sleep
            collector.once(sc)
        all3 = collector.ring(999)
        s.ok(len(all3) == 3, 'limit 大于条数时没把 3 条都给出来：%d' % len(all3))
        s.ok([x.get('scope') for x in all3] == ['cn', 'hk', 'us'],
             '顺序不是按时间升序（最老在前）：%s' % [x.get('scope') for x in all3])
        two = collector.ring(2)
        s.ok([x.get('scope') for x in two] == ['hk', 'us'],
             '★ring(2) 给的不是**最近**两条：%s' % [x.get('scope') for x in two])
        s.ok(two[-1].get('ts') >= two[0].get('ts'), 'ring() 不是按时间升序')
        s.ok(len(collector.ring()) == 3, 'ring() 默认没给全')
    return s.done()


def _c6_inject():
    real = store.read_index

    def bad(p=None, limit=None):
        recs, badl = real(p, None)
        if limit is not None:
            recs = recs[:int(limit)]             # ★给了最**老**的 N 条
        return recs, badl

    return (store, 'read_index', bad)


# ── C7 空目录上不编数 ──────────────────────────────────────────────────
def _c7():
    s = Sub()
    with sandbox() as d:
        s.ok(collector.ring() == [], '空目录上 ring() 不是空表')
        s.ok(collector.ring(120) == [], '空目录上 ring(120) 不是空表')
        st = collector.status()
        s.ok(st.get('ok') is True, '空目录上 status() 不是 ok=True：%r' % (st,))
        s.ok(st.get('ticks') == 0, '空目录上 ticks=%r（应该是 0）' % (st.get('ticks'),))
        s.ok(st.get('rows') is None,
             '★空目录上 rows=%r —— 0 是"采到了 0 行"，和"一次都没采过"是两件事'
             % (st.get('rows'),))
        s.ok(st.get('last_ts') is None, '空目录上 last_ts=%r' % (st.get('last_ts'),))
        s.ok(st.get('last') is None, '空目录上给了 last：%r' % (st.get('last'),))
        s.ok(not st.get('sources'), '空目录上给了 sources：%r' % (st.get('sources'),))
        s.ok(sorted(os.listdir(d)) == [],
             '只调 status()/ring() 就凭空建了文件：%s' % sorted(os.listdir(d)))
    return s.done()


def _c7_inject():
    """文件不在时**编一条空轮出来**。这是最直白的一种"编数据"。"""
    real = store.read_index

    def bad(p=None, limit=None):
        recs, badl = real(p, limit)
        if not recs:
            recs = [{'v': 1, 'ts': store.now_str(), 'scope': 'all', 'ok': True,
                     'market': 'cn', 'markets': ['cn'], 'rows': 0, 'ms': 1.0,
                     'err': None, 'sources': []}]
        return recs, badl

    return (store, 'read_index', bad)


# ── C8 只追加 ──────────────────────────────────────────────────────────
FOREIGN = json.dumps({'v': 1, 'ts': '1999-01-01 00:00:00', 'scope': 'foreign',
                      'ok': True, 'market': 'zz', 'rows': 123, 'ms': 1.0, 'err': None,
                      'sources': [], 'writer': 'another-process'},
                     ensure_ascii=False, separators=(',', ':'))


def _c8():
    s = Sub()
    with sandbox() as d:
        collector.once('cn')
        b1 = store.bytes_of(d)
        ip = store.index_path()
        # ★模拟**另一个写者**：它在这一刻往同一个索引里追加了一行。
        #   （这个仓库是多写者共用的，这不是假设，是常态。）
        with open(ip, 'a', encoding='utf-8') as f:
            f.write(FOREIGN + '\n')
        b2 = store.bytes_of(d)
        s.ok(b2.get('index.jsonl', b'').startswith(b1.get('index.jsonl', b'')),
             '别人的那一行**之前**的内容已经不是原样了')
        collector.once('cn')
        b3 = store.bytes_of(d)
        for k, v in b2.items():
            if k in b3:
                s.ok(b3[k].startswith(v),
                     '%s 不是"只追加"：前面 %d 字节被改动了（整份重写会**静默抹掉**'
                     '别人同时写进去的东西）' % (k, len(v)))
        s.ok(FOREIGN.encode('utf-8') in b3.get('index.jsonl', b''),
             '★另一个写者那一条被抹掉了 —— 这就是"整份重写"造成的静默丢数据')
        recs, badl = store.read_index()
        s.ok(len(recs) == 3, '索引里应该是 3 条（2 轮 + 1 条别人的），实到 %d' % len(recs))
        s.ok(not badl, '正常追加产生了坏行：%s' % badl)
        s.ok(sum(1 for r in recs if r.get('writer') == 'another-process') == 1,
             '别人的记录在解析后也丢了')
    return s.done()


def _c8_inject():
    """★"整份备份写回"：读回**自己写过的**那几条，追加新的，再整份写回。
    这正是约束里点名的那个动作，也是 `ledger.py` 顶上那条账记的坑。
    后果（唯一的后果）：**别人在这中间写进去的那一行没了**，而且事后
    任何内容比对都看不出来（只有 mtime 变了，而 mtime 不是内容证据）。"""
    own = []

    def bad(rec, p=None):
        own.append(rec)
        try:
            with open(store.index_path(p), 'w', encoding='utf-8') as f:   # ★截断重写
                for r in own:
                    f.write(store._line(r))
            return True, None
        except OSError as e:
            return False, str(e)

    return (store, 'append_index', bad)


# ── C9 载荷落盘必须是严格 JSON ─────────────────────────────────────────
def _c9():
    s = Sub()
    if np is None:
        raise NotRun('numpy 不在，脏值造不出来（这条判据没跑）')
    with sandbox() as d:
        with patch(sources, 'backend', lambda force=None: _bk(DirtySource())):
            r = collector.once('cn')
            s.ok(r.get('ok') is True, '脏数据源下 once() 没成：%r' % (r.get('err'),))
            s.ok(r.get('rows_by_market', {}).get('cn') == 2,
                 '行数记账不对：%r' % (r.get('rows_by_market'),))
            fp = store.frame_path('cn')
            s.ok(os.path.exists(fp), '★载荷没落盘：%s' % fp)
            if os.path.exists(fp):
                with open(fp, encoding='utf-8') as f:
                    ln = f.readline()
                obj, err = strict_loads(ln)
                s.ok(err is None, '★载荷不是**严格 JSON**：%s（裸 NaN 会被前端 JSON.parse 炸掉，'
                                  '而 Python 自己读得回来）' % err)
                if obj:
                    row = (obj.get('rows') or [{}])[0]
                    s.ok(row.get('price') is None, 'NaN 没变成 null：%r' % (row.get('price'),))
                    s.ok(row.get('open') is None, 'inf 没变成 null：%r' % (row.get('open'),))
                    s.ok(row.get('pct') is None, '-inf 没变成 null：%r' % (row.get('pct'),))
                    s.ok(row.get('high') is None and row.get('pe') is None,
                         'np.float64(NaN) 没变成 null')
                    s.ok(row.get('low') == 7 and isinstance(row.get('low'), int)
                         and not hasattr(row.get('low'), 'item'),
                         'np.int64 没变成原生 int：%r(%s)' % (row.get('low'), type(row.get('low')).__name__))
                    s.ok(row.get('volume') == 123, 'np.int64 没变成原生 int')
                    s.ok(row.get('prev_close') is None, 'None 被改成了别的值')
                    s.ok(row.get('turnover') is None
                         and (obj['rows'][1].get('turnover') == 3),
                         '本来就有的值被清洗弄丢了')
            with open(store.index_path(), encoding='utf-8') as f:
                iln = f.readline()
            _o, ierr = strict_loads(iln)
            s.ok(ierr is None, '索引那一条不是严格 JSON：%s' % ierr)
            s.ok(r.get('ok') is True and r.get('rows') == 2, 'rows 不是真值：%r' % (r.get('rows'),))
    return s.done()


def _c9_inject():
    return (store, 'json_safe', lambda v: v)          # ★清洗变成恒等函数


# ── C10 超时 ───────────────────────────────────────────────────────────
def _c10():
    s = Sub()
    with sandbox(LICAI_COLLECT_TIMEOUT='0.4') as d:
        with patch(sources, 'backend', lambda force=None: _bk(HangingSource())):
            t0 = time.time()
            r = collector.once('cn')
            dt = time.time() - t0
            s.ok(dt < 2.0, '★once() 花了 %.1fs —— 单次调用上限设的是 0.4s，超时没生效'
                 % dt)
            items = r.get('sources') or [{}]
            it = items[0]
            s.ok(it.get('ok') is False, '★挂住的源竟然算成功：%r' % (it,))
            s.ok('TIMEOUT' in (it.get('err') or ''),
                 'err 里没说是超时：%r' % (it.get('err'),))
            s.ok('线程还在跑' in (it.get('err') or ''),
                 '没说明被放弃的那个线程还活着（不说的话，每轮泄漏一个线程看不出来）：%r'
                 % (it.get('err'),))
            s.ok(r.get('ok') is False and r.get('rows') is None,
                 '全挂住时 rows 不是 None：%r' % (r.get('rows'),))
            s.ok(dt < 2.0, '预算是按轮算的，不该等到每个源都超时')
    return s.done()


def _c10_inject():
    return (sources, 'call_with_timeout', lambda fn, seconds, label: fn())


# ── C11 国内直连：碰数据层之前先摘代理 ─────────────────────────────────
def _c11():
    s = Sub()
    calls = []

    class FakeFeed:
        @staticmethod
        def proxy_env_fix():
            calls.append('proxy')

    with sandbox(LICAI_COLLECT_SYNTH=None):
        # ★不真的去 import datasource（那会连网）：只让"包没落地"这条支路出现，
        #   这样 `backend()` 里**代理那一段**照样走到。
        with patch(sources, '_feed', lambda: FakeFeed), \
             patch(sources, '_module_found', lambda name: False):
            r = collector.once('cn')
            s.ok(calls == ['proxy'],
                 '★碰数据层之前没调 feed.proxy_env_fix()：calls=%s —— 本机 ALL_PROXY 是 '
                 '"socks://"（少了 5），requests 不认，于是本该直连的国内站点会以一个'
                 '和网络无关的 ProxyError 炸掉' % calls)
            s.ok(r.get('ok') is False, '这条路上不该有数据')
            s.ok(r.get('backend_code') == 'NOT_READY', '这条路上该报 NOT_READY')
    # 反面：桩开着的时候**不需要**碰数据层，也就**不该**去摘代理
    with sandbox() as d:                                          # synth=1
        calls2 = []
        with patch(sources, '_feed', lambda: FakeFeed):
            collector.once('cn')
        s.ok(calls2 == [], '桩开着的时候还去摘代理了（桩不走网络）：%s' % calls2)
    return s.done()


def _c11_inject():
    return (sources, 'proxy_env_fix', lambda: None)     # ★把那行删掉


# ── C12 running 三态 ───────────────────────────────────────────────────
def _c12():
    s = Sub()
    with sandbox(LICAI_COLLECT_SCOPE='cn'):
        st0 = collector.status()
        s.ok('running' not in st0,
             '★从没起过常驻循环，status() 却给了 running=%r —— 那两句是不同的话：'
             '"没说"和"说了没跑"' % (st0.get('running'),))
        s.ok(bool(st0.get('hint')), '没给 running 的时候也没给 hint，用户不知道为什么')
        st0b = collector.status()
        s.ok('running' not in st0b, '第二次 status() 就自作主张填了 running')
        r = collector.start(interval=3600)
        s.ok(r.get('running') is True, 'start() 没报 running=True：%r' % (r,))
        s.ok(r.get('interval') == 3600.0, 'start() 没把 interval 带回来：%r' % (r,))
        time.sleep(0.4)
        st1 = collector.status()
        s.ok(st1.get('running') is True,
             'start() 之后 status().running 不是 True：%r' % (st1.get('running'),))
        s.ok(bool(st1.get('next_ts')), '在跑，却没给 next_ts')
        s.ok(st1.get('last_ts') is not None, '在跑，却没采过任何一轮（第一轮应该立刻跑）')
        sp = collector.stop(timeout=10)
        s.ok(sp.get('running') is False, 'stop() 之后没报 running=False：%r' % (sp,))
        st2 = collector.status()
        s.ok(st2.get('running') is False,
             'stop() 之后 status().running 不是 False：%r' % (st2.get('running'),))
        s.ok(st2.get('next_ts') is None, '停了还给了 next_ts：%r' % (st2.get('next_ts'),))
        s.ok(st2.get('rounds') is None, '停了还在报 rounds（那是"在跑"才有的）')
    return s.done()


def _c12_inject():
    return (collector, '_running_field', lambda: False)   # ★用 False 顶替"不知道"


# ── C13 返回值的 JSON 安全 ─────────────────────────────────────────────
def _c13():
    s = Sub()
    with sandbox():
        r = collector.once('cn')
        for lbl, obj in (('once()（成功）', r), ('status()', collector.status()),
                         ('ring()', collector.ring())):
            err = strict_dumps(obj)
            s.ok(err is None, '%s 的返回值过不了 allow_nan=False：%s' % (lbl, err))
    with sandbox(LICAI_COLLECT_SYNTH=None):                       # 降级路径
        with patch(sources, '_module_found', lambda name: False):
            r2 = collector.once('cn')
            for lbl, obj in (('once()（降级）', r2), ('status()', collector.status())):
                err = strict_dumps(obj)
                s.ok(err is None, '%s 的返回值过不了 allow_nan=False：%s' % (lbl, err))
    # ★夹具（不是注入）：让"行数合计"给出一个 NaN —— 真实世界里
    #   `np.mean([])`、`x/0`、上游 DataFrame 里一列全 NaN 都会给出它。
    #   要验的是"返回值这一步有没有把它洗掉"，洗掉它的机制是 `__init__._indexed`
    #   里那次 `store.json_safe`。
    with sandbox():
        with patch(sources, 'total_rows', lambda items: float('nan')):
            r3 = collector.once('cn')
            err = strict_dumps(r3)
            s.ok(err is None,
                 '★上游给了一个非有限数时，once() 的返回值里就带着 NaN/inf：%s'
                 '（`json.dumps` 默认允许它 ⇒ `jsonify` 写出一个别的解析器读不动的 body，'
                 '前端 JSON.parse 当场炸，而本地 python 读得回来）' % err)
            s.ok(r3.get('rows') is None,
                 '非有限数没被洗成 None，而是 %r' % (r3.get('rows'),))
            s.ok(r3.get('ok') is False,
                 '行数是个 NaN，这一轮却报 ok=True —— "没采到数"和"采到了数"'
                 '被说成了同一句话（%r）' % (r3.get('ok'),))
            s.ok(bool(r3.get('err')), 'ok=False 却没给原因')
            s.ok(strict_dumps(r3) is None, '洗过一遍之后还是不安全')
    return s.done()


def _c13_inject():
    return (store, 'json_safe', lambda v: v)      # ★把清洗变成恒等函数


# ── C14 scope 里的未知词 ───────────────────────────────────────────────
def _c14():
    s = Sub()
    with sandbox():
        r = collector.once('cn,typo')
        s.ok(r.get('ok') is False,
             '★scope 里有不认识的词，却 ok=%r —— 他会看到一个正常的页面，'
             '而他要的那个市场一次都没采过' % (r.get('ok'),))
        s.ok('typo' in (r.get('err') or ''), 'err 里没点名那个词：%r' % (r.get('err'),))
        s.ok(bool(r.get('hint')), '报错了却没告诉用户认得的词有哪些')
        # ★失败时页面**只显示 msg 那一句**（`api2.ts: fromBody` 在 ok:false 时
        #   只取 msg/error + hint，err/sources/rows 全丢）⇒ msg 里必须带原因。
        s.ok('typo' in (r.get('msg') or ''),
             '「scope 打错了」和「什么都没采到」在页面上变成同一句了：msg=%r'
             % (r.get('msg'),))
        recs, _b = store.read_index()
        s.ok(len(recs) == 1 and recs[0].get('ok') is False, '"这一轮没跑成"没进索引')
        s.ok(collector.once('   ')['ok'] is False, '空白 scope 没报错')
        ok = collector.once('cn,hk')
        s.ok(ok.get('markets') == ['cn', 'hk'], '逗号组合没生效：%r' % (ok.get('markets'),))
        s.ok(ok.get('market') == 'cn,hk', "market 那一栏不是 'cn,hk'：%r" % (ok.get('market'),))
        s.ok(collector.once('hk,cn')['markets'] == ['cn', 'hk'],
             '市场顺序跟着用户敲的顺序走了 —— 同一个采集器看起来像两台')
        s.ok(collector.once('markets')['ok'] is True, "'markets' 这个 token 采不成")
        e = collector.once('')
        s.ok(e.get('ok') is True and e.get('scope') == 'all',
             "scope='' 按全部都采（对，路由也是这么顶的），但索引里写的是 %r —— "
             "那条索引等于在说'我采了一个没有名字的 scope'" % (e.get('scope'),))
    return s.done()


def _c14_inject():
    real = sources.parse_scope

    def bad(scope):
        plan, err = real(scope)
        if err:                                  # ★把不认识的词静默丢掉，"修好"它
            raw = (scope if isinstance(scope, str) else 'all') or 'all'
            keep = [t for t in raw.replace(' ', ',').split(',')
                    if t.strip().lower() in sources.ALL_TOKENS or t.strip().lower() in ('all',)]
            return real(','.join(keep))[0], None
        return plan, err

    return (sources, 'parse_scope', bad)


# ── C15 资讯那一层炸了不许带走整轮 ─────────────────────────────────────
def _c15():
    s = Sub()
    with sandbox() as d:
        stub = os.path.join(d, 'news_stub.json')
        with open(stub, 'w', encoding='utf-8') as f:
            json.dump({'ok': True, 'degraded': [],
                       'items': [{'title': '桩新闻一', 'source': 'stub', 'url': 'http://x/1',
                                  'ts': '2026-09-22 10:00:00'}]}, f, ensure_ascii=False)
        os.environ['LICAI_NEWS_STUB'] = stub
        r = collector.once('cn,news')
        ids = [x.get('id') for x in (r.get('sources') or [])]
        s.ok('news' in ids, 'sources 里没有 news 这一条账：%s' % ids)
        s.ok(r.get('ok') is True, '行情成了、资讯成了，整轮却 ok=False：%r' % (r.get('err'),))
        nw = [x for x in (r.get('sources') or []) if x.get('id') == 'news'] or [{}]
        s.ok(nw[0].get('n') == 1, 'news 的 n 不是 1：%r' % (nw[0].get('n'),))
        s.ok(r.get('rows') == 41,
             '★资讯的**条数**被并进了 rows（%r）—— 量纲不同，并了就是"采了 1 条新闻"'
             '显示成"采了 1 行行情"' % (r.get('rows'),))
        r2 = None
        with patch(sources, '_collect_news', raiser(RuntimeError('news-layer-boom'))):
            try:
                r2 = collector.once('cn,news')
            except Exception as e:                               # noqa: BLE001
                s.ok(False, '★资讯那一层抛异常，把整轮一起带走了：%s: %s'
                     % (type(e).__name__, e))
        if r2 is not None:
            s.ok(r2.get('ok') is True, '资讯炸了把整轮判成失败：%r' % (r2.get('err'),))
            nw2 = [x for x in (r2.get('sources') or []) if x.get('id') == 'news'] or [{}]
            s.ok(nw2[0].get('ok') is False, '资讯炸了却没在账上留一条红的')
            s.ok('news-layer-boom' in (nw2[0].get('err') or ''),
                 'err 里没带真异常：%r' % (nw2[0].get('err'),))
            s.ok(r2.get('rows') is not None, '行情那部分的 rows 丢了')
            s.ok('news' in [x.get('id') for x in (r2.get('sources') or [])],
                 '资讯炸了之后连账都没有了')
    return s.done()


def _c15_inject():
    return (sources, '_safe_news',
            lambda led, timeout: sources._collect_news(led, timeout))   # ★拆掉围栏


# ── C16 落盘失败 ≠ 没采到 ──────────────────────────────────────────────
def _c16():
    s = Sub()
    with sandbox():
        with patch(store, 'append_index',
                   lambda rec, p=None: (False, 'OSError: 注入：No space left on device')):
            r = collector.once('cn')
        s.ok(r.get('ok') is False,
             '索引落盘失败了却 ok=True —— 这一轮会在页面上"看起来成功了"，而盘上没有')
        s.ok(r.get('rows') == 41,
             '★落盘失败被当成了"没采到"：rows 变成 %r（数据是真的采到了，桩 cn=41）'
             % (r.get('rows'),))
        # ★断言的是**说法**不是措辞：这句话必须同时说清"数据采到了"和"问题在盘上"。
        #   原本我写的是 `'落盘' in err` —— 那是个坏尺子：真实消息是"索引没落上盘"，
        #   `落`和`盘`不挨着，于是干净副本上假红（判据红了，而代码是对的）。
        #   尺子要按"它要量什么"做，不是按"我脑子里那句话怎么写"做。
        err = r.get('err') or ''
        s.ok('采到了' in err and '盘' in err,
             'err 里没把"数据采到了、是盘的事"说清：%r' % (err,))
        s.ok('No space left' in err, 'err 里没带真原因：%r' % (err,))
        # 失败时页面只显示 msg（api2.ts:139-149），所以这句也得说清是什么事
        s.ok('No space left' in (r.get('msg') or ''),
             'msg 里没带落盘失败的真正原因（页面上只会看到这一句）：%r' % (r.get('msg'),))
    return s.done()


def _c16_inject():
    """★把落盘失败按成功处理（`iok` 看都不看）。"""
    real = collector._indexed

    def bad(rec, iok, ierr):
        return real(rec, True, None)

    return (collector, '_indexed', bad)


# ── C17 `_num`：复用的是 feed 那一个对象，不是"一份像的" ───────────────
def _c17():
    s = Sub()
    fd = sources._feed()
    s.ok(fd is not None,
         'feed.py 没找到 —— 那 `_num` 和 `proxy_env_fix` 都退回了本地实现')
    if fd is None:
        return s.done()
    fn = getattr(fd, '_num', None)
    s.ok(callable(fn), 'feed.py 里没有 _num —— 上层那个 `_num` 会静默退回本地实现')
    if not callable(fn):
        return s.done()
    vals = [0, 1, -1, 1.23456789, -0.00005, 1e-9, 2 ** 70, float('nan'),
            float('inf'), -float('inf'), None, 'x', '', '3.14', b'1', 1e308 * 10]
    diff = []
    for v in vals:
        try:
            a = sources._num(v, 4)
        except Exception as e:                                   # noqa: BLE001
            a = '<%s>' % type(e).__name__
        try:
            b = fn(v, 4)
        except Exception as e:                                   # noqa: BLE001
            b = '<%s>' % type(e).__name__
        if a != b:
            diff.append((repr(v), a, b))
    s.ok(not diff, 'sources._num 与 feed._num 在边界值上给出不同结果 '
                   '(值, sources, feed)=%r' % (diff[:4],))
    # 顺带把"None 不许变成 0"这条也在这一层钉一下（C7 钉的是 status 那一头）
    for v in (None, float('nan'), float('inf')):
        s.ok(sources._num(v) is None, '_num(%r) 给了 %r，不是 None' % (v, sources._num(v)))
    s.note('%d 个边界值上与 feed._num 逐个一致' % len(vals))
    return s.done()


def _c17_inject():
    return (sources, '_num', lambda x, nd=4: 0)     # ★"取不到就当 0"


# ── C19 说明里点名的词，parser 必须真认 ─────────────────────────────────
def _c19():
    """`SCOPE_HELP` 是**用户照着敲的那句话**。它列出的每个 token，
    都必须真的能被 `parse_scope` 接受 —— 说明里写了一个不认得的词，
    用户敲下去得到的就是一句"我不认识这个词"。"""
    s = Sub()
    toks = re.findall(r'[a-z_]{2,}', sources.SCOPE_HELP)
    s.ok(bool(toks), 'SCOPE_HELP 里一个 token 都没列出来：%r' % (sources.SCOPE_HELP,))
    unknown = [t for t in toks if sources.parse_scope(t)[1] is not None]
    s.ok(not unknown, '★说明里点名的这些词，parser 自己不认：%s（用户是照着这句话敲的）'
         % unknown)
    for t in ('all', 'boards', 'news'):
        s.ok(t in toks, '说明里漏了 %r 这个词' % t)
    for m in sources.MARKETS:
        s.ok(m in toks, '说明里漏了市场 %r' % m)
    _, err = sources.parse_scope('typo')
    s.ok(sources.SCOPE_HELP in err,
         '报错时没把可选项清单念给用户（他得猜）：%r' % (err,))
    s.note('SCOPE_HELP = %r' % sources.SCOPE_HELP)
    return s.done()


def _c19_inject():
    return (sources, 'SCOPE_HELP',
            'all / markets / cn / hk / us / crypto / futures / boards / news')


# ── C20 只采资讯的那一轮，不许报成"什么都没采到" ─────────────────────────
def _c20():
    s = Sub()
    with sandbox() as d:
        stub = os.path.join(d, 'news_stub.json')
        with open(stub, 'w', encoding='utf-8') as f:
            json.dump({'ok': True, 'degraded': [],
                       'items': [{'title': '桩新闻%d' % i, 'source': 'stub',
                                  'url': 'http://x/%d' % i, 'ts': '2026-09-22 10:00:00'}
                                 for i in range(3)]}, f, ensure_ascii=False)
        os.environ['LICAI_NEWS_STUB'] = stub
        r = collector.once('news')
        s.ok(r.get('rows') is None,
             '资讯的**条数**被并进了 rows：%r —— 量纲不同，并进去就等于说'
             '"采了 3 行行情"' % (r.get('rows'),))
        s.ok(r.get('ok') is True,
             '★只采资讯的那一轮被报成"什么都没采到"（ok=%r err=%r）—— '
             '它明明采到了 3 条，盘上也有载荷；说出来的一轮和真发生的那一轮对不上'
             % (r.get('ok'), r.get('err')))
        s.ok(any('资讯' in str(x) for x in (r.get('notes') or [])),
             'rows 是 None 而这一轮其实成了，却没说明为什么：notes=%r' % (r.get('notes'),))
        s.ok(r.get('market') == 'news', "market 那一栏不是 'news'：%r" % (r.get('market'),))
        # 反面：一个源都没成的时候，ok 必须是 False（这一条不能为了上面那条放宽）
        with patch(sources, 'backend', lambda force=None: sources._not_ready('注入', '注入')):
            r2 = collector.once('news')
            s.ok(r2.get('ok') is False and r2.get('rows') is None,
                 '一个源都没成却报成 ok=%r' % (r2.get('ok'),))
    return s.done()


def _c20_inject():
    """★把 `ok` 退回"rows 不是 None 我就算成"—— 那条看着更简单的写法。"""
    return (sources, '_round_ok', lambda led, rows: rows is not None)


# ── C21 只采资讯的那一轮，页面上那句话也不许反着说 ─────────────────────
def _c21():
    """`ok` 对了之后**同一件事的第二个落点**：页面上那句话。

    ★为什么值得单独一条：`ok` 和那句话**各自独立地拿 `rows` 判了一次成不成**
      （`sources._round_ok` 与 `collector._round_msg`）—— 修了前一处、
      后一处原样在。只钉前一处的话，"盘上躺着 3 条资讯、页面上写着
      '这一轮什么都没采到'"这种页面照样发得出去。同一件事有两个落点时，
      一条判据只盖住一个的话，读者会以为整件事都盖住了。
    """
    s = Sub()
    with sandbox():
        stub = os.path.join(os.environ['LICAI_COLLECT_DIR'], 'news_stub.json')
        with open(stub, 'w', encoding='utf-8') as f:
            json.dump({'ok': True, 'degraded': [],
                       'items': [{'title': '桩新闻%d' % i, 'source': 'stub',
                                  'url': 'http://x/%d' % i, 'ts': '2026-09-22 10:00:00'}
                                 for i in range(3)]}, f, ensure_ascii=False)
        os.environ['LICAI_NEWS_STUB'] = stub
        r = collector.once('news')
        msg = r.get('msg') or ''
        s.ok(r.get('ok') is True, '前置就不对：这一轮 ok=%r' % (r.get('ok'),))
        s.ok('什么都没采到' not in msg,
             '★这一轮 ok=True、盘上躺着 3 条资讯，页面那句却是 %r —— '
             '说出来的和真发生的对不上（和"编数据"是同一类错）' % (msg,))
        s.ok('资讯' in msg or 'news' in msg,
             '这一轮成了，可那句没说采到了**什么**（失败时 msg 是页面上唯一的依据）：%r'
             % (msg,))
        # 行数那一支不许被这次修改弄坏（"只采了行情"仍然要报出行数）
        s.ok(collector._round_msg({'ok': True, 'rows': 41, 'sources': []}) == '采完一轮：41 行',
             'rows 有值时那句话变了：%r'
             % (collector._round_msg({'ok': True, 'rows': 41, 'sources': []}),))
    return s.done()


def _c21_inject():
    """★把那句话退回"rows 是 None 就说'什么都没采到'"—— 我刚写过的那个版本。"""
    real = collector._round_msg

    def bad(out):
        if out.get('ok') and out.get('rows') is None:
            return '这一轮什么都没采到'
        return real(out)

    return (collector, '_round_msg', bad)



for _cid, _title, _run, _inj in (
    ('C1', '三个入口的返回形状（ring 是裸 list）', _c1, None),
    ('C2', 'status() 只读：前后盘上字节不许变', _c2, _c2_inject),
    ('C3', '数据层没就绪 ⇒ ok=False + 人话原因，不许退回桩', _c3, _c3_inject),
    ('C4', '「包在但 import 炸了」≠「包还没落地」', _c4, _c4_inject),
    ('C5', 'ring(0) / ring(负数) = 空表', _c5, _c5_inject),
    ('C6', 'ring(limit) = 最近 N 条、按时间升序', _c6, _c6_inject),
    ('C7', '空目录上不编数，也不凭空建文件', _c7, _c7_inject),
    ('C8', '只追加：别人写的那一行必须活下来', _c8, _c8_inject),
    ('C9', '载荷落盘是严格 JSON（NaN/inf/numpy）', _c9, _c9_inject),
    ('C10', '每次外部调用都有超时', _c10, _c10_inject),
    ('C11', '碰数据层之前先 feed.proxy_env_fix()', _c11, _c11_inject),
    ('C12', 'running 三态：没说 ≠ 说了没跑', _c12, _c12_inject),
    ('C13', '三个入口的返回值都过 allow_nan=False', _c13, _c13_inject),
    ('C14', 'scope 里的未知词要报错，不许静默丢掉', _c14, _c14_inject),
    ('C15', '资讯那一层炸了不许带走整轮', _c15, _c15_inject),
    ('C16', '索引落盘失败 ≠ 没采到（rows 保持真值）', _c16, _c16_inject),
    ('C17', '_num 复用的是 feed 那一个对象，不是一份像的', _c17, _c17_inject),
    ('C19', '说明里点名的 scope 词，parser 必须真认', _c19, _c19_inject),
    ('C20', '只采资讯的那一轮不许报成"什么都没采到"', _c20, _c20_inject),
    ('C21', '那一轮页面上那句话也不许反着说', _c21, _c21_inject),
):
    CRITERIA.append({'id': _cid, 'title': _title, 'run': _run,
                     'inject': _inj() if _inj else None})


# ══ C1 的注入（单独写，因为它要拿到原始的 ring）═════════════════════════
def _c1_inject():
    real = collector.ring
    return (collector, 'ring',
            lambda limit=collector.DEFAULT_RING: {'ok': True, 'items': list(real(limit))})


CRITERIA[0]['inject'] = _c1_inject()


# ══ 环境守卫 ═══════════════════════════════════════════════════════════
#
# ★下面两条**没有注入**，按本项目那条纪律：写不出注入的判据要**降级成证据并
#   写明"没人替它把关"**，所以它们叫"守卫"不叫"判据"，也不报 ✓ 报 ·（证据）。
#   第二条自带**对照物**（一个 import 时就写盘的模块），因为"没量到写"有两种来源：
#   ①被测对象真的没写；②**这把尺子量不出写**。没有对照物的话这两种分不开 ——
#   而那正是"判据绿得与被测代码无关"的经典形态。

_IMPORT_SNIPPET = r'''
import json, os, sys, threading
repo, tdir, ctl = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, repo)
sys.path.insert(0, tdir)
os.environ['LICAI_COLLECT_DIR'] = ctl
out = {}
# ① 对照物：先证明这把尺子量得出来"import 时就写盘"
try:
    import z_import_sideeffect as z
    out['control_wrote'] = os.path.exists(z.PATH)
except Exception as e:                                          # noqa: BLE001
    out['control_err'] = '%s: %s' % (type(e).__name__, e)
# ② 被测对象
try:
    import collector
    import collector as again
    out['imported'] = bool(collector.ring and again is collector)
except Exception as e:                                          # noqa: BLE001
    out['import_err'] = '%s: %s' % (type(e).__name__, e)
out['threads'] = sorted(x.name for x in threading.enumerate()
                        if x is not threading.main_thread())
out['dir_created'] = os.path.isdir(ctl)
out['leftover'] = sorted(os.listdir(ctl)) if os.path.isdir(ctl) else []
print(json.dumps(out, ensure_ascii=False))
'''

_CONTROL_MODULE = ("import os\nPATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),"
                   " 'control_written.txt')\nopen(PATH, 'w').write('x')\n")


def guard_import_side_effects():
    """`import collector` 不许有副作用：不起线程、不写文件、不连网（规格 §0/本块约束）。

    → `(ok, detail)`。★它**没有注入**（写不出"让 import 有副作用"的注入，
    除非去改被测文件的源码 —— 那在多写者共用的仓库里是一次破坏）。
    所以它只当证据，且自带对照物。
    """
    import subprocess
    repo = os.path.dirname(AGENT)
    tdir = tempfile.mkdtemp(prefix='licai-collect-control-')
    base = tempfile.mkdtemp(prefix='licai-collect-ctl-')
    # ★`ctl` 必须是**此刻不存在**的路径：`mkdtemp` 自己建一个再问"它建了没有"，
    #   那条断言恒真（我量的是我自己的 mkdtemp）。清单里的每一条守卫都要先问
    #   一句"这条断言在什么情况下会假"。
    ctl = os.path.join(base, 'collect-ctl')
    try:
        with open(os.path.join(tdir, 'z_import_sideeffect.py'), 'w', encoding='utf-8') as f:
            f.write(_CONTROL_MODULE)
        p = subprocess.run([sys.executable, '-c', _IMPORT_SNIPPET, AGENT, tdir, ctl],
                           capture_output=True, text=True, timeout=120, cwd=repo)
        if p.returncode != 0:
            return False, '子进程 rc=%d：%s' % (p.returncode, (p.stderr or '')[-300:])
        d = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception as e:                                       # noqa: BLE001
        return False, '守卫自己没跑成：%s: %s' % (type(e).__name__, e)
    finally:
        shutil.rmtree(tdir, ignore_errors=True)
        shutil.rmtree(base, ignore_errors=True)

    bad = []
    if d.get('control_err') or d.get('control_wrote') is not True:
        # ★尺子坏了：它量不出"import 时写盘"，所以下面"没写"那句话不算数。
        bad.append('★对照物没被量到（%r）⇒ 这把尺子量不出 import 时的写盘，'
                   '"它没写"这句话不作数' % (d.get('control_err') or d.get('control_wrote')))
    if not d.get('imported'):
        bad.append('import collector 本身失败了：%s' % d.get('import_err'))
    if d.get('threads'):
        bad.append('★import 时起了线程：%s' % d['threads'])
    if d.get('dir_created') or d.get('leftover'):
        bad.append('★import 时写了文件：%s' % (d.get('leftover'),))
    if bad:
        return False, '; '.join(bad)
    return True, ('对照物验过（那把尺子量得出来 import 时的写盘）；'
                  '`import collector` 起了 0 个线程、没建目录、没写文件')


def guard_repo_untouched(before, after, root):
    if before == after:
        return True, ('探针没往 %s 里写任何东西（%d 个文件的前后一致）' % (root, len(before)))
    return False, '★探针污染了真仓库 %s：%s -> %s' % (root, before, after)


_APPEND_SNIPPET = r'''
import sys, os
sys.path.insert(0, sys.argv[1])
os.environ['LICAI_COLLECT_DIR'] = sys.argv[2]
from collector import store
who, n = sys.argv[3], int(sys.argv[4])
for i in range(n):
    ok, err = store.append_index({'v': 1, 'ts': store.now_str(), 'scope': 'w%s-%d' % (who, i),
                                  'writer': who, 'ok': True, 'rows': i, 'ms': 0.1,
                                  'market': 'cn', 'err': None, 'sources': []})
    if not ok:
        print('APPEND_FAIL %s' % err); sys.exit(2)
'''

# 守卫 3：**多进程同时追加**。★这是整块的地基那句话("`open(p,'a')` 的单次写是原子的")
# 唯一能证伪的机会：同进程的两个线程有 `_LOCK` 兜着，跨进程只有 O_APPEND 兜着，
# 而崩溃的形态是**两行字节交错**（读回来一半是别人的），页面上表现为"有一轮数据是乱的"。
N_WRITERS, PER_WRITER = 4, 60


def guard_concurrent_append():
    """→ `(ok, detail)`。★**没有注入**（要让"两行交错"稳定复现得靠时序，
    一条有时红有时不红的判据比没有判据更坏 —— 所以它只当证据，且写明没人把关，
    对应关系：C8 的注入守的是"整份重写"，这条守的是"跨进程交错"，两者不同）。"""
    import subprocess
    d = tempfile.mkdtemp(prefix='licai-collect-conc-')
    try:
        procs = [subprocess.Popen([sys.executable, '-c', _APPEND_SNIPPET, AGENT, d,
                                   str(i), str(PER_WRITER)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i in range(N_WRITERS)]
        outs = []
        for p in procs:
            so, se = p.communicate(timeout=180)
            outs.append((p.returncode, (so or '')[-200:], (se or '')[-300:]))
        bad = [o for o in outs if o[0] != 0]
        if bad:
            return False, '有写者失败：%r' % (bad[:2],)
        path = os.path.join(d, 'index.jsonl')
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
        want = N_WRITERS * PER_WRITER
        recs, badl = store.read_index(d)
        errs = []
        if len(lines) != want:
            errs.append('行数 %d ≠ %d（有行丢了或被合并了）' % (len(lines), want))
        if badl:
            errs.append('有 %d 行读不动（字节交错）：行号 %s' % (len(badl), badl[:5]))
        if len(recs) != want:
            errs.append('能解析的只有 %d 条' % len(recs))
        if errs:
            return False, '★%s' % '；'.join(errs)
        return True, ('%d 个进程各追加 %d 条 ⇒ 盘上 %d 行、逐行可解析、没有交错'
                      % (N_WRITERS, PER_WRITER, len(lines)))
    except Exception as e:                                       # noqa: BLE001
        return False, '守卫自己没跑成：%s: %s' % (type(e).__name__, e)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ══ 跑 ═════════════════════════════════════════════════════════════════

def _apply_and_run(c, inject):
    """打上注入跑一次。→ `('red'|'green'|'raised', detail)`。"""
    obj, attr, val = inject
    with patch(obj, attr, val):
        try:
            ok, detail = c['run']()
        except NotRun as e:
            return 'notrun', str(e)
        except Exception as e:                                   # noqa: BLE001
            return 'raised', '%s: %s' % (type(e).__name__, str(e)[:140])
    return ('green' if ok else 'red'), detail


def main(argv):
    no_inject = '--no-inject' in argv
    live = '--live' in argv
    only = set()
    for i, a in enumerate(argv):
        if a.startswith('--only='):
            only |= set(a.split('=', 1)[1].upper().split(','))
        elif a == '--only' and i + 1 < len(argv):
            only |= set(argv[i + 1].upper().split(','))
    only = {x for x in only if x}

    # 守卫：**探针自己不许污染真仓库**。★这一条**没有注入**（它量的不是被测代码，
    # 是这台仪器本身）—— 按本项目那条纪律，写不出注入的判据要**降级成证据并写明
    # "没人替它把关"**，所以它叫"守卫"不叫判据。
    real_root = store.root()
    real_before = store.inventory()

    picked = [c for c in CRITERIA if not only or c['id'] in only]
    if not picked:
        print('没挑中任何判据：--only %s' % ','.join(sorted(only)))
        return 3

    n_red_clean, n_green_inj, n_notrun = [], [], []
    lines = []
    print('══ 干净副本（每条判据都必须是绿的）'.ljust(78, '═'))
    for c in picked:
        try:
            ok, detail = c['run']()
        except NotRun as e:
            ok, detail = None, str(e)
        except Exception as e:                                   # noqa: BLE001
            ok, detail = False, '判据自己抛了 %s: %s' % (type(e).__name__, str(e)[:200])
        if ok is None:
            n_notrun.append(c['id'])
            print('  ⊘ %-4s %-44s 没跑：%s' % (c['id'], c['title'], detail[:70]))
        elif ok:
            print('  ✓ %-4s %-44s %s' % (c['id'], c['title'], ('· ' + detail) if detail else ''))
        else:
            n_red_clean.append(c['id'])
            print('  ✗ %-4s %-44s 红：%s' % (c['id'], c['title'], detail))
        lines.append((c['id'], c['title'], ok, detail))

    if not no_inject:
        print('\n══ 故障注入（每条判据都必须被打红）'.ljust(78, '═'))
        for c in picked:
            if not c['inject']:
                print('  ⊘ %-4s %-44s 这条判据**没有注入**（没人替它把关）' % (c['id'], c['title']))
                n_notrun.append(c['id'])
                continue
            try:
                res, detail = _apply_and_run(c, c['inject'])
            except NotRun as e:
                res, detail = 'notrun', str(e)
            if res == 'red':
                print('  ✓ %-4s %-44s 已打红  ← %s' % (c['id'], c['title'], detail[:90]))
            elif res == 'raised':
                # 红在异常上：注入把被测代码打崩了。算打红，但**要说出来** ——
                # "判据红在断言上"和"红在崩掉上"是两种强度不同的证据。
                print('  ✓ %-4s %-44s 已打红（异常方式）：%s' % (c['id'], c['title'], detail[:80]))
            elif res == 'notrun':
                n_notrun.append(c['id'])
                print('  ⊘ %-4s %-44s 注入够不着：%s' % (c['id'], c['title'], detail[:80]))
            else:
                n_green_inj.append(c['id'])
                print('  ✗ %-4s %-44s **注入没能打红它**（判据是装饰）：%s'
                      % (c['id'], c['title'], detail[:80]))

    if live:
        print('\n══ --live：跑一轮**真源**（网络，只当证据，不当判据）'.ljust(78, '═'))
        with sandbox(LICAI_COLLECT_SYNTH=None, LICAI_COLLECT_TIMEOUT='20'):
            print('  数据层：%s' % sources.backend_name())
            t0 = time.time()
            r = collector.once('cn,boards')
            print('  once(cn,boards) ok=%r rows=%r rows_by=%r frames=%r dt=%.1fs'
                  % (r.get('ok'), r.get('rows'), r.get('rows_by_market'),
                     r.get('frames'), time.time() - t0))
            for x in (r.get('sources') or []):
                print('    %s' % json.dumps(x, ensure_ascii=False))
            if r.get('err'):
                print('    err=%s' % r['err'])
            fp = store.frame_path('cn')
            if os.path.exists(fp):
                with open(fp, encoding='utf-8') as f:
                    _o, err = strict_loads(f.readline())
                print('  真载荷 %s：%s' % (fp, '严格 JSON OK' if not err else err))
            else:
                print('  真载荷没落盘（%s）' % fp)

    real_after = store.inventory()
    print('\n══ 环境守卫（**没有注入** —— 它们量的不是被测代码，是这台仪器本身）'.ljust(78, '═'))
    g1 = guard_import_side_effects()
    print('  %s import 无副作用：%s' % ('✓' if g1[0] else '✗', g1[1]))
    g2 = guard_repo_untouched(real_before, real_after, real_root)
    print('  %s 仓库没被污染：%s' % ('✓' if g2[0] else '✗', g2[1]))
    g3 = guard_concurrent_append()
    print('  %s 跨进程并发追加：%s' % ('✓' if g3[0] else '✗', g3[1]))
    guards_ok = g1[0] and g2[0] and g3[0]

    print('\n' + '─' * 78)
    print('判据 %d 条：干净副本绿 %d / 红 %d / 没跑 %d'
          % (len(picked), len(picked) - len(n_red_clean) - len(n_notrun),
             len(n_red_clean), len(n_notrun)))
    if not no_inject:
        print('注入：打红 %d / 没打红 %d'
              % (len(picked) - len(n_green_inj) - len(n_notrun), len(n_green_inj)))
        if n_green_inj:
            print('  ★没打红的：%s' % ','.join(n_green_inj))
    if n_red_clean:
        print('  ★干净副本上就红的：%s' % ','.join(n_red_clean))
    if n_notrun:
        print('  ★没跑的（rc=3）：%s' % ','.join(n_notrun))
    print('守卫：3 条 %s（★它们没有注入，没人替它们把关；对照物在 1 里）'
          % ('全过' if guards_ok else '有不过的'))
    if n_red_clean or n_green_inj or not guards_ok:
        print('rc=1')
        return 1
    if n_notrun:
        print('rc=3（有东西没跑，不能算通过）')
        return 3
    print('rc=0')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
