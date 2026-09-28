"""文件缓存 + 惰性清理。

- 键：`sha1(source_id | symbol | sorted(terms))` —— 与查询内容绑定，
  同一个源对不同标的/不同关键词是**不同的缓存条目**。
- 路径：`<CACHE_DIR>/<yyyymmdd>/<key>.json`（按天分目录，清理就是删目录）。
- TTL 由调用方传（源清单里的 `ttl_sec`）；**过期返回 None**，
  由调用方重打并覆盖 —— 不在读路径上做后台清理线程（本站没有常驻线程的纪律）。
- >7 天的日期目录在 `put()` 时惰性顺手删（一次 listdir，不心疼）。

env 覆盖：`LICAI_NEWS_CACHE`（判据指临时目录，不脏仓库；LEDGER_ENV 同款纪律）。
"""

import os
import json
import time
import hashlib
import datetime as dt

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..'))
CACHE_ENV = 'LICAI_NEWS_CACHE'
KEEP_DAYS = 7


def cache_dir():
    return os.environ.get(CACHE_ENV) or os.path.join(_ROOT, 'data', 'news_cache')


def make_key(source_id, symbol=None, terms=None):
    h = hashlib.sha1()
    h.update((source_id or '').encode('utf-8'))
    h.update(b'|')
    h.update((str(symbol) if symbol else '').encode('utf-8'))
    h.update(b'|')
    h.update(','.join(sorted(t for t in (terms or []) if t)).encode('utf-8'))
    return h.hexdigest()


def _day_dir(day=None):
    day = day or dt.date.today().strftime('%Y%m%d')
    return os.path.join(cache_dir(), day)


def get(key, ttl_sec):
    """命中且未过期 → list；否则 None。读坏文件当未命中（缓存不是真源）。"""
    today = _day_dir()
    path = os.path.join(today, key + '.json')
    try:
        age = time.time() - os.path.getmtime(path)
        if age > ttl_sec:
            return None
        with open(path, encoding='utf-8') as f:
            val = json.load(f)
        return val if isinstance(val, list) else None
    except (OSError, ValueError):
        # 昨天的目录也可能有未过期的条目（跨午夜），补查一次
        yday = _day_dir((dt.date.today() - dt.timedelta(days=1)).strftime('%Y%m%d'))
        path2 = os.path.join(yday, key + '.json')
        try:
            if time.time() - os.path.getmtime(path2) > ttl_sec:
                return None
            with open(path2, encoding='utf-8') as f:
                val = json.load(f)
            return val if isinstance(val, list) else None
        except (OSError, ValueError):
            return None


def put(key, items):
    d = _day_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, key + '.json')
    # 原子落盘（tmp + replace）：本仓库吃过非原子写被并发读者读到半截的亏。
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, path)
    _sweep()


def _sweep():
    """惰性清理 >KEEP_DAYS 天的日期目录。只删**形如 8 位数字**的目录，别的一律不碰。"""
    try:
        root = cache_dir()
        cutoff = dt.date.today() - dt.timedelta(days=KEEP_DAYS)
        for name in os.listdir(root):
            if len(name) != 8 or not name.isdigit():
                continue
            try:
                if dt.datetime.strptime(name, '%Y%m%d').date() < cutoff:
                    import shutil
                    shutil.rmtree(os.path.join(root, name), ignore_errors=True)
            except ValueError:
                continue
    except OSError:
        pass
