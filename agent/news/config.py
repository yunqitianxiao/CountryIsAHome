"""源清单：`data/news_sources.json` 的读取层。

env 覆盖：`LICAI_NEWS_SOURCES`（判据用临时清单打靶，不碰仓库里那份）。
文件坏了（不存在/JSON 解析失败）不抛 —— 返回一份**空清单**并由调用方
把"没有可用源"如实报出去。配置是数据，不是代码：坏数据要变成看得见的降级，
不是变成 traceback。
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..'))
DEFAULT_PATH = os.path.join(_ROOT, 'data', 'news_sources.json')
SOURCES_ENV = 'LICAI_NEWS_SOURCES'

_CACHE = {'path': None, 'mtime': 0.0, 'val': None}


def sources_path():
    return os.environ.get(SOURCES_ENV) or DEFAULT_PATH


def load():
    """读清单（带 mtime 缓存：文件一改，下一次 load 立刻看到新内容）。"""
    path = sources_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {'ttl_sec': 900, 'max_items_per_source': 10, 'sources': []}
    if _CACHE['val'] is not None and _CACHE['path'] == path and _CACHE['mtime'] == mtime:
        return _CACHE['val']
    try:
        with open(path, encoding='utf-8') as f:
            val = json.load(f)
    except (OSError, ValueError):
        val = {'ttl_sec': 900, 'max_items_per_source': 10, 'sources': []}
    _CACHE.update(path=path, mtime=mtime, val=val)
    return val


def enabled_sources():
    return [s for s in load().get('sources', []) if s.get('enabled')]
