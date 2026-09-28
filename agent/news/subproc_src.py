"""层2/层3：crawl4ai 与 MediaCrawler —— 全部隔在**独立环境 + subprocess** 里。

协议（两个 worker 相同）：
    stdin  ← 一行 JSON 请求
    stdout → 一行 JSON 结果 {"ok": bool, "items": [...] , "error": "..."}
    超时（默认 60s，网页栈比 akshare 慢）→ 杀进程 → 结构化降级。

为什么是 subprocess 而不是 import：
  - crawl4ai 拉 playwright + chromium，和主 venv（uv 建的，没有 pip 模块）的
    torch/akshare 依赖树搅在一起只会互相顶版本；
  - MediaCrawler 不在 PyPI，是 clone 到 third_party/ 的一整个应用；
  - worker 崩了/被反爬挡了/cookies 过期了，主进程只看到一个 {ok:false}，
    落进 degraded[] —— 采集层的任何故障都不许变成对话链路的故障。

env：
  LICAI_COLLECTOR_VENV   覆盖 .venv-collector 的 python 路径（判据用假 worker 打靶）
  LICAI_MC_HOME          覆盖 third_party/MediaCrawler 路径（同上）
"""

import os
import json
import subprocess

from . import http_src                        # 复用 _ts_from_url（URL 里的时间）

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..'))
COLLECTOR_PY = os.path.join(_ROOT, '.venv-collector', 'bin', 'python')
MC_HOME = os.path.join(_ROOT, 'third_party', 'MediaCrawler')
WORKER_TIMEOUT = 60

# available() 的进程内小缓存（文件系统探测，没必要每次 /api/status 都摸一遍）
_PROBE = {}


def _collector_python():
    return os.environ.get('LICAI_COLLECTOR_VENV') or COLLECTOR_PY


def _mc_home():
    return os.environ.get('LICAI_MC_HOME') or MC_HOME


def crawl4ai_available():
    """{installed: bool, python: path|None}。installed = venv python 在且能 import。"""
    py = _collector_python()
    if not os.path.exists(py):
        return {'installed': False, 'python': None}
    key = ('c4a', py)
    if key in _PROBE:
        return _PROBE[key]
    try:
        r = subprocess.run([py, '-c', 'import crawl4ai'],
                           capture_output=True, timeout=30)
        val = {'installed': r.returncode == 0, 'python': py}
    except Exception:                            # noqa: BLE001
        val = {'installed': False, 'python': py}
    _PROBE[key] = val
    return val


def mediacrawler_available():
    """{installed: bool, home, cookies: {platform: bool}}。

    cookies 的判据是 MediaCrawler 的约定：`browser_data/<platform>_cookies.txt`
    存在且非空（它自己登录后就写在那）。没有 cookies 的 MediaCrawler
    对大多数平台跑不起来 —— 所以这里如实分平台报告，页面上能显示到粒度。
    """
    home = _mc_home()
    if not os.path.isdir(home):
        return {'installed': False, 'home': None, 'cookies': {}}
    cookies = {}
    bdir = os.path.join(home, 'browser_data')
    try:
        for name in os.listdir(bdir):
            if name.endswith('_cookies.txt'):
                plat = name[:-len('_cookies.txt')]
                cookies[plat] = os.path.getsize(os.path.join(bdir, name)) > 10
    except OSError:
        pass
    return {'installed': True, 'home': home, 'cookies': cookies}


def _run_worker(argv, payload, timeout=WORKER_TIMEOUT):
    """跑一个 worker：stdin 送 JSON、stdout 收 JSON。任何失败都变成 raise。"""
    try:
        r = subprocess.run(argv, input=json.dumps(payload, ensure_ascii=False),
                           capture_output=True, text=True, timeout=timeout,
                           cwd=_ROOT)
    except subprocess.TimeoutExpired:
        raise RuntimeError('worker 超时（%ds）' % timeout) from None
    if r.returncode != 0:
        raise RuntimeError('worker 退出码 %d：%s'
                           % (r.returncode, (r.stderr or '')[-200:]))
    out = (r.stdout or '').strip()
    # worker 可能往 stdout 混进日志行：只认**最后一行**合法 JSON
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                val = json.loads(line)
                if val.get('ok'):
                    return val.get('items') or []
                raise RuntimeError(val.get('error') or 'worker 报告失败')
            except ValueError:
                continue
    raise RuntimeError('worker 没有输出合法 JSON：%s' % out[-160:])


def _post(src, items, limit):
    """worker 的原始行 → 与其它层**同一个 item 形状**。

    ★为什么形状统一这么重要：`_merge_dedup` 要跨层合并，`sentiment` 要按字段名
      建摘要 —— 少一个键就会在下游变成一句 `.get(k, default)`，而 default
      和"上游真的给了空"从此长得一样（本项目吃过好几次这个亏）。
    ★`ts_from_url`：金融界这类站的详情页 URL 里带 `YYYY/MM/DDHHMMSS`，那是
      **上游生成的时间**，比"采集时刻"有用得多，也比让 LLM 从正文里读时间可靠。
      正则写在源清单里（配置是数据），具名组见 http_src._ts_from_url。
    """
    rex = src.get('ts_from_url')
    minlen = int(src.get('min_title_len') or 0)
    out = []
    for raw in (items or []):
        it = dict(raw)
        it.setdefault('excerpt', '')
        it.setdefault('url', None)
        if not it.get('ts') and rex:
            it['ts'] = http_src._ts_from_url(it.get('url'), rex)
        it['ts'] = it.get('ts') or None
        it.setdefault('ts_kind', 'upstream' if it['ts'] else 'none')
        it.setdefault('tags', [])
        it.setdefault('related', [])
        it.setdefault('sentiment_raw', None)
        if minlen and len(it.get('title') or '') < minlen:
            continue
        if not (it.get('title') or '').strip():
            continue
        out.append(it)
    return out[:max(1, int(limit or 10))]


def fetch_crawl4ai(src, terms=None, limit=10):
    """层2：任意网页 → 条目。请求形状见 _c4a_worker.py 的 docstring。"""
    avail = crawl4ai_available()
    if not avail['installed']:
        raise RuntimeError('crawl4ai 未安装（.venv-collector 不存在或缺包）—— 见 README 附录')
    payload = {'url': src.get('url'), 'css': src.get('css'),
               'schema': src.get('schema'), 'terms': sorted(terms or []),
               'limit': limit, 'source_id': src.get('id'),
               'source_label': src.get('label') or src.get('id')}
    worker = os.path.join(_HERE, '_c4a_worker.py')
    return _post(src, _run_worker([avail['python'], worker], payload), limit)


def fetch_mediacrawler(src, terms=None, limit=10):
    """层3：国内社媒关键词搜索。没有对应平台 cookies 就明说（不静默空转）。"""
    avail = mediacrawler_available()
    if not avail['installed']:
        raise RuntimeError('MediaCrawler 未安装（third_party/MediaCrawler 不存在）—— 见 README 附录')
    plat = src.get('platform')
    if not avail['cookies'].get(plat):
        raise RuntimeError('MediaCrawler 缺 %s 的 cookies（browser_data/%s_cookies.txt）'
                           % (plat, plat))
    payload = {'platform': plat, 'mode': src.get('mode', 'search'),
               'keywords': sorted(terms or []), 'limit': limit,
               'source_id': src.get('id'),
               'source_label': src.get('label') or src.get('id')}
    worker = os.path.join(_HERE, '_mc_worker.py')
    return _post(src, _run_worker([sys_python_for_mc(), worker], payload,
                                 timeout=120), limit)


def sys_python_for_mc():
    """MediaCrawler 有自己的依赖，用它自己目录里的 venv；没有就用主 venv 试。"""
    cand = os.path.join(_mc_home(), '.venv', 'bin', 'python')
    if os.path.exists(cand):
        return cand
    return os.environ.get('LICAI_MC_PYTHON') or os.path.join(_ROOT, '.venv', 'bin', 'python')
