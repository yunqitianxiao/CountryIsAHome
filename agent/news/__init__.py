"""新闻/资讯采集子系统：**四层降级**，主进程零重依赖。

    层1    akshare 基线（stock_news_em / stock_info_global_cls / _em / _sina + 财新/新闻联播/千股千评）
           —— 随主 .venv 就有，永远可用的地板。
    层1.5  **直连 HTTP**（东财 7x24 / 同花顺快讯 / 巨潮公告 / 深交所公告）
           —— 公开 JSON 接口，requests 就够，不需要 cookie。
    层1.6  **adata.sentiment**（同花顺热股榜 / 东财人气榜 / 热门概念 / 龙虎榜）
           —— 热度与舆情榜，进同一个 item 形状好参与跨源共振计数。
    层2    crawl4ai（任意国内外网页 → 干净 markdown/结构化条目）
           —— 装在**独立的 `.venv-collector`** 里，经 subprocess JSON worker 调用。
    层3    MediaCrawler（微博/知乎/B站等国内社媒）
           —— clone 在 `third_party/MediaCrawler`，同样走 subprocess worker。

★为什么层2/层3不进主 venv：crawl4ai 会拉 playwright + chromium（数百 MB），
  MediaCrawler 根本不在 PyPI 上；而主 venv 是 uv 建的（**没有 pip 模块**），
  torch/akshare 的依赖树也不该和浏览器栈搅在一起。subprocess + stdin/stdout
  的 JSON 协议把两边彻底隔开：worker 崩了、超时了、没装，主进程都只看到
  一个结构化的 `{ok: false, error: ...}`，落进 `degraded[]`，绝不抛异常。

★纪律（照抄 feed.py 的 LICAI_FEED_STUB 那条）：
  `LICAI_NEWS_STUB` 只认**服务端**环境变量，不接受任何客户端开关 ——
  一个客户端能打开的桩就是一台伪造新闻的机器。
  桩开着时**绕开缓存**（夹具的意义就在于改了就立刻生效，同 tools._get_kline）。

★"注重采集、弱化分析"（用户原话的落点）：本模块只做 收集→归一→去重→按时间排序，
  **不做任何观点性加工**。多源同题保留 `sources`/`seen_in` 两个列表 ——
  同一条新闻出现在几个源里本身就是热度信号，怎么用是上层（舆情 Agent / 雷达）的事。

★★多源共振**只在去重里体现**，所以去重规则必须"宁可少合、不可错合"：
  合错了 = 两条不同的事被算成 2 源共振，凭空造出一个信号，且事后查不出来。
  三级判据（URL／归一标题／近似标题）里，只有前两级是"确定同一"，
  第三级是**猜测**，所以它单独记 `merged_by='near'`，并且门槛写死在常量里。

★并发：源多了之后串行打会把一次 `get_news` 拖到十几秒（实测 2026-09-22 本机
  四层全开、串行 ≈13s、并发 4 ≈3.5s）。所以这里默认并发 `DEFAULT_CONCURRENCY`。
  结果**按源清单顺序**归位后再合并（不按完成顺序）—— 并发不许改变输出的确定性。
"""
import concurrent.futures as cf
import json
import os
import re
import time

from . import config as _config
from . import cache as _cache
from . import akshare_src
from . import adata_src
from . import http_src
from . import subproc_src

STUB_ENV = 'LICAI_NEWS_STUB'

# 并发打源的线程数。★4 是"够快"与"别把国内站点打出限流"的交点：
# 实测 2026-09-22 本机，四层全开时串行 13s / 并发 4 → 3.5s；再往上收益就靠
# 支付"被上游当爬虫"的风险了，而东财/同花顺是我们要长期用的源。
# 串行语义 = 1（要完全复现旧行为就设 1，或调 `collect(concurrency=1)`）。
DEFAULT_CONCURRENCY = 4

# 近似标题去重的门槛（第三级判据，唯一一条"猜"的）。见 `_merge_dedup`。
NEAR_MIN_LEN = 12        # 归一化后短于此长度**不参与**近似合并（短标题撞车率太高）
NEAR_CONTAIN = 0.80      # 短的那条的 bigram 要有 80% 被长的那条盖住
NEAR_MIN_SHARED = 8      # 且公共 bigram 至少 8 个（太短的两条碰巧盖住不算）

# available() 的探测结果缓存（进程内，5 分钟）——探测本身要摸文件系统，
# 而 /api/status 每次页面加载都会打一次。
_AVAIL = {'at': 0.0, 'val': None}
_AVAIL_TTL = 300


def stub_active():
    """桩是否生效。只在服务端 env 里认（见模块 docstring）。"""
    return bool(os.environ.get(STUB_ENV))


def _stub_payload():
    path = os.environ.get(STUB_ENV)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def available():
    """各层采集能力的探测结果（给 /api/status 和页面徽章用）。

    只报**事实**（装没装、cookies 有没有），不报观点。结果缓存 5 分钟。
    """
    now = time.time()
    if _AVAIL['val'] is not None and now - _AVAIL['at'] < _AVAIL_TTL:
        return _AVAIL['val']
    srcs = _config.load().get('sources', [])
    val = {
        'akshare': {'installed': akshare_src.installed()},
        'adata': {'installed': adata_src.installed()},
        'http': {'sources': len([s for s in srcs if s.get('kind') == 'http'])},
        'crawl4ai': subproc_src.crawl4ai_available(),
        'mediacrawler': subproc_src.mediacrawler_available(),
        'sources_total': len(srcs),
        'sources_enabled': len(_config.enabled_sources()),
    }
    _AVAIL['at'], _AVAIL['val'] = now, val
    return val


def invalidate_availability():
    """判据/测试用：装了新东西之后立刻重探，不等 TTL。"""
    _AVAIL['at'], _AVAIL['val'] = 0.0, None


def _sources_for(symbol, roles):
    """挑出这一轮要打的源，并为每个源记一条**采集账**（跳过也要记账）。

    ★返回 `(picked, skipped)` 两份 —— 跳过的**也要交出去**，不能就地丢掉。
      理由（实测 2026-09-22 本机）：本机 22 个启用源里 7 个是 `scope='symbol'`，
      全局查询下它们全部跳过。旧写法只把未跳过的放进 `picked`，于是
      `collect()['sources']` 只有 15 条、**没有一条 `skipped`** ⇒
      ①`health().summary.skipped_n` 恒等于 0（一个在任何输入下都取同一个值的
        字段，结构上量不出"有没有源被跳过"——这是最容易被当成判据的假读数）；
      ②`_heat_from` 的 `ran`/`skipped` 分档、`_src_row` 的 `skipped` 栏、
        本模块与 `sentiment` 里"跳过既不算成功也不算失败"的说明**全是空转的**；
      ③交付要求的"每个源的真实可用性"里，跳过的源会**静默消失**而不是报成"跳过"。
      加回这两份是**加行不改结构**：`picked` 那条路（真打、记账、degraded）一行没动，
      `sources` 只是多了带 `skipped` 的条目。要回退就是把 `skipped` 那几行并回
      `picked` 且不追加（一行的事）。
    """
    picked, skipped = [], []
    for src in _config.enabled_sources():
        rec = {'id': src.get('id'), 'label': src.get('label') or src.get('id'),
               'kind': src.get('kind'), 'role': src.get('role') or 'news',
               'scope': src.get('scope', 'global'),
               'ok': None, 'n': 0, 'ms': None, 'err': None, 'cached': False,
               'skipped': None}
        if roles and rec['role'] not in roles:
            rec['skipped'] = 'role_filtered'
        elif rec['scope'] == 'symbol' and not symbol:
            rec['skipped'] = 'needs_symbol'      # 个股源没给 symbol 就不打，不算降级
        if rec['skipped']:
            skipped.append(rec)
        else:
            picked.append((src, rec))
    return picked, skipped


def _fetch_one(src, rec, symbol, terms, limit, ttl):
    """单源采集（在**工作线程里**跑）：先查缓存，未命中就真打，成功后写缓存。

    ★缓存读写在并发下是安全的：键按源算（不同源不同文件），落盘是 tmp+os.replace。
    ★这一函数**只记账、不抛**：任何异常都变成 rec 里的 err，绝不冒泡到线程池。
    """
    t0 = time.time()
    sid = src.get('id')
    try:
        key = _cache.make_key(sid, symbol=symbol, terms=terms)
        hit = _cache.get(key, int(src.get('ttl_sec') or ttl))
        if hit is not None:
            rec.update(ok=True, n=len(hit), ms=0, cached=True)
            return [dict(it, cached=True) for it in hit]
        got = _dispatch(src, symbol=symbol, terms=terms, limit=limit)
        _cache.put(key, got)
        rec.update(ok=True, n=len(got), ms=int((time.time() - t0) * 1000))
        return got
    except Exception as e:            # noqa: BLE001 —— 单源失败必须结构化
        rec.update(ok=False, n=0, ms=int((time.time() - t0) * 1000),
                   err='%s: %s' % (type(e).__name__, str(e)[:160]))
        return []


def _dispatch(src, symbol, terms, limit):
    kind = src.get('kind')
    if kind == 'akshare':
        return akshare_src.fetch(src, symbol=symbol, terms=terms, limit=limit)
    if kind == 'http':
        return http_src.fetch(src, symbol=symbol, terms=terms, limit=limit)
    if kind == 'adata':
        return adata_src.fetch(src, symbol=symbol, terms=terms, limit=limit)
    if kind == 'crawl4ai':
        return subproc_src.fetch_crawl4ai(src, terms=terms, limit=limit)
    if kind == 'mediacrawler':
        return subproc_src.fetch_mediacrawler(src, terms=terms, limit=limit)
    raise ValueError('未知的源类型：%s' % kind)


def collect(symbol=None, query=None, limit=6, keywords=None, roles=None,
            concurrency=None, budget_ms=None):
    """统一采集入口。

    参数
      symbol      标的代码（可选）。给了就启用 symbol 域的源（个股新闻），
                  并把标的的 名称/代码 加进全局源的过滤词。
      query       自由文本查询（可选）。用于全局源的关键词过滤。
      limit       合并去重后最多返回几条。
      keywords    额外的过滤词列表（雷达会用：股票名+代码）。
      roles       只打这些角色的源（None = 全部启用源）。角色由源清单的 `role` 给：
                  'news' | 'announcement' | 'heat' | 'sentiment'。被过滤掉的源
                  在 `sources[].skipped` 里留痕（**不是**静默不出现）。
      concurrency 并发线程数（默认 DEFAULT_CONCURRENCY=4；1 = 完全串行）。
      budget_ms   整轮**墙钟**预算（毫秒）。超了的不等，记 `err='budget_exceeded'`。
                  ★注意：akshare 层的 `feed._with_timeout` 只把超时变成异常，
                    它的线程池退出时会 join —— 所以对 akshare 源这个预算**兜不住**，
                    能兜住的是自建 HTTP/adata 源（requests 的 timeout 是真的）。

    返回 dict：
      {'ok': bool, 'items': [...], 'degraded': [{'source','reason'}],
       'count': int, 'as_of': 'ISO 时间',
       'sources': [{'id','label','kind','role','scope','ok','n','ms','err',
                    'cached','skipped'}]}
    items 单条形如：
      {'title', 'excerpt', 'source', 'source_label', 'ts', 'ts_kind', 'url',
       'tags', 'related', 'sentiment_raw',
       'seen_in': [...], 'sources': [{'id','label','url','ts'}],
       'ts_last', 'heat', 'merged_by'}

    ★失败语义：**部分源挂了整体仍然 ok**（有几条给几条，挂掉的进 degraded）；
      只有**全部**源都失败才 ok=false —— 那时宁可明说没拿到，不拿旧缓存充数。
    """
    if stub_active():
        return _stub_collect(symbol=symbol, query=query, limit=limit)

    cfg = _config.load()
    ttl = int(cfg.get('ttl_sec', 900))
    per_source = int(cfg.get('max_items_per_source', 10))
    conc = max(1, int(concurrency or DEFAULT_CONCURRENCY))

    # 过滤词：query 本身 + symbol 解析出的名称/代码 + 调用方给的 keywords
    terms = set()
    if query:
        terms.add(query.strip())
    for k in (keywords or []):
        if k:
            terms.add(str(k).strip())
    if symbol and not terms:
        terms.add(str(symbol).strip())

    picked, skipped = _sources_for(symbol, roles)
    items, degraded = [], []
    if picked:
        # ★akshare 的 import 必须在**起线程之前**在主线完成：让它进 sys.modules，
        #   否则 N 个线程同时首次 import 一个 300MB 的包，会各自走一遍导入锁。
        if any(s.get('kind') == 'akshare' for s, _ in picked):
            try:
                akshare_src.installed()
            except Exception:                     # noqa: BLE001 —— 装没装是运行期事实
                pass
        _run_all(picked, items, symbol, terms, per_source, ttl, conc, budget_ms)

    for _, rec in picked:
        if rec['ok'] is False:
            degraded.append({'source': rec['id'], 'reason': rec['err'] or 'unknown'})

    merged = _merge_dedup(items)
    merged.sort(key=lambda x: x.get('ts') or '', reverse=True)
    total = len(merged)                       # ★截断**之前**的条数：给 heat 当"体量"用
    merged = merged[:max(1, int(limit or 6))]
    # 全挂才 false：有货（哪怕带着降级）就是 true；一个源都没配上也是 true（诚实的 0 条）
    ok = bool(items) or not degraded
    return {'ok': ok, 'items': merged, 'degraded': degraded,
            'count': len(merged),
            # ★`count_raw` 是去重后、`limit` 截断**之前**的条数。为什么要分开：
            #   体量类指标（见 sentiment.heat 的 volume 分量）若拿 `count` 算，
            #   就会被**调用方的 limit 参数**决定 —— 同一个市场，limit=6 的人
            #   量到"冷"、limit=30 的人量到"热"。那是尺子跟着读数走。
            'count_raw': total,
            'as_of': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            # ★跳过的源**也在账上**（`skipped` 非空：'needs_symbol' / 'role_filtered'）。
            #   顺序 = 源清单顺序：真打的按清单序，跳过的排在后面，符合"账要读得出来"。
            'sources': [rec for _, rec in picked] + skipped}


def _run_all(picked, items, symbol, terms, per_source, ttl, conc, budget_ms):
    """并发（或串行）打源。★结果**按源清单顺序**落回 items，不按完成顺序 ——
    并发不许改变输出的确定性（同一批数据两次采集要得到同样的顺序）。"""
    if conc <= 1 or len(picked) <= 1:
        for src, rec in picked:
            items.extend(_fetch_one(src, rec, symbol, terms, per_source, ttl))
        return
    deadline = (time.time() + budget_ms / 1000.0) if budget_ms else None
    ex = cf.ThreadPoolExecutor(max_workers=min(conc, len(picked)))
    try:
        futs = [(src, rec, ex.submit(_fetch_one, src, rec, symbol, terms,
                                     per_source, ttl)) for src, rec in picked]
        for src, rec, fut in futs:
            wait = None if deadline is None else max(0.0, deadline - time.time())
            try:
                items.extend(fut.result(timeout=wait))
            except cf.TimeoutError:
                rec.update(ok=False, err='budget_exceeded',
                           ms=int(budget_ms or 0))
    finally:
        # ★不许 wait=True：那会把"预算"变成摆设（`_with_timeout` 的那种坑）。
        #   每个源自己有超时，孤儿线程会自己收尾，只是结果被丢掉。
        ex.shutdown(wait=False, cancel_futures=True)


def _norm_title(t):
    """标题归一化（去重用）：去空白与常见标点，小写。"""
    return re.sub(r'[\s【】\[\]（）()「」『』:：!！?？。，,\.、;；"\'“”‘’]+',
                  '', (t or '')).lower()


# ★公开别名：下游（`agent/sentiment/`）要按**同一把尺**判断"两条标题是不是同一条"。
#   两份各写一遍必然漂移，而漂移的表现是"去重结果对不上"这种最难查的静默错。
norm_title = _norm_title


def _bigrams(s):
    """字符二元组集合。中文没有空格分词，bigram 是"不改上游文本就能做"的最稳的近似。"""
    return set(s[i:i + 2] for i in range(len(s) - 1))


def _is_near(a, b):
    """近似标题：短的那条要被长的那条**盖住** 80%。

    ★为什么用"包含率"而不是 Jaccard：同一事件的多源标题长度差很大
      （"茅台：关于召开业绩说明会的公告" vs "贵州茅台:贵州茅台关于召开2026年半年度
      业绩说明会的公告"），Jaccard 会被长度差异压得很低，而包含率问的是
      "短标题的话是不是都被长标题说了" —— 那才是"同一件事"的形状。
    ★为什么两个门槛都要：光有比例，"公告"这种短标题随便撞；光有绝对值，
      长标题之间碰巧共享 8 个 bigram 就合了。两个一起才挡得住。
    """
    if len(a) < NEAR_MIN_LEN or len(b) < NEAR_MIN_LEN:
        return False
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return False
    if A <= B or B <= A:                          # 一条完全包含另一条：确定性的强信号
        return True
    short, long_ = (A, B) if len(A) <= len(B) else (B, A)
    shared = len(short & long_)
    return shared >= NEAR_MIN_SHARED and shared / len(short) >= NEAR_CONTAIN


def _merge_dedup(items):
    """三级去重：URL 精确 → 归一标题精确 → 近似标题（唯一一"猜"）。

    同题多源并进 `sources`（完整来源清单）与 `seen_in`（源 id 列表，兼容旧消费方）；
    `heat` = 不同源的个数。

    ★三个时间字段各说各的，不许互相顶替：
        ts       = 各来源里**最早**的那个时间（事件发生）
        ts_last  = 各来源里**最晚**的那个时间（最后被报道）
        ts_kind  = 'upstream'（上游给的）| 'collected'（我们盖的采集时刻）
      取最早而不是"第一个遇到的"：后者取决于源的遍历顺序，那是**顺序的产物**，
      不是数据的性质（并发下更是随机的）。
    """
    by_url, by_title, out = {}, {}, []
    for it in items:
        url = (it.get('url') or '').strip()
        nt = _norm_title(it.get('title'))
        target, how = None, None
        if url and url in by_url:
            target, how = by_url[url], 'url'
        elif nt and nt in by_title:
            target, how = by_title[nt], 'title'
        else:
            for cand in out:                       # 近似：只在没精确命中时才扫
                if _is_near(nt, cand['_nt']):
                    target, how = cand, 'near'
                    break
        if target is not None:
            src_id = it.get('source')
            if src_id and src_id not in target['seen_in']:
                target['seen_in'].append(src_id)
                target['sources'].append({'id': src_id,
                                          'label': it.get('source_label'),
                                          'url': it.get('url'),
                                          'ts': it.get('ts')})
            target['heat'] = len(target['seen_in'])
            _absorb_times(target, it)
            # 留下更长的摘录（信息只增不减）
            if len(it.get('excerpt') or '') > len(target.get('excerpt') or ''):
                target['excerpt'] = it['excerpt']
            if how == 'near' and target.get('merged_by') != 'near':
                target['merged_by'] = 'near'        # 记下这一条是"猜"来的
            continue
        rec = dict(it)
        rec['seen_in'] = [it.get('source')] if it.get('source') else []
        rec['sources'] = ([{'id': it.get('source'), 'label': it.get('source_label'),
                            'url': it.get('url'), 'ts': it.get('ts')}]
                          if it.get('source') else [])
        rec['heat'] = len(rec['seen_in'])
        rec['merged_by'] = None
        rec['ts_last'] = it.get('ts')
        rec['_nt'] = nt
        if url:
            by_url[url] = rec
        if nt:
            by_title[nt] = rec
        out.append(rec)
    for rec in out:
        rec.pop('_nt', None)
    return out


def _absorb_times(target, it):
    """合并时把时间收敛成 min/max 两端（见 `_merge_dedup` 的说明）。"""
    t = it.get('ts')
    if not t:
        return
    if not target.get('ts') or t < target['ts']:
        target['ts'] = t
        target['ts_kind'] = it.get('ts_kind') or target.get('ts_kind')
    if not target.get('ts_last') or t > target['ts_last']:
        target['ts_last'] = t


def health(symbol=None, roles=None, limit=5, concurrency=None):
    """每个源的真实采集情况：ok / n / ms / err（**跑真网络，不是查配置**）。

    ★这是"通信采集"的体检：它只报**这一轮实际发生了什么**，包括
      跳过的源（`skipped`：缺 symbol / 被角色过滤）—— 跳过不算失败，也不算成功，
      它单独一栏。把跳过折进 ok 或不报是两种都见过的假绿。
    """
    res = collect(symbol=symbol, limit=limit, roles=roles, concurrency=concurrency)
    srcs = res.get('sources') or []
    ok_n = len([s for s in srcs if s.get('ok')])
    skipped_n = len([s for s in srcs if s.get('skipped')])
    fail_n = len([s for s in srcs if s.get('ok') is False and not s.get('skipped')])
    return {'ok': res.get('ok'), 'ts': res.get('as_of'),
            'items_n': res.get('count'),
            'sources': srcs,
            'summary': {'sources_n': len(srcs), 'ok_n': ok_n, 'fail_n': fail_n,
                        'skipped_n': skipped_n,
                        'ms_total': sum(s.get('ms') or 0 for s in srcs)},
            'layers': available()}


def _stub_collect(symbol=None, query=None, limit=6):
    """桩：整份来自 LICAI_NEWS_STUB 指的 JSON 文件，逐字节确定。

    桩文件格式（判据用它造确定场景）：
      {"items": [...], "degraded": [{"source": "em-global", "reason": "..."}]}
    ★桩里写什么就回什么（只做 limit 截断）——判据比的就是"两次采集逐字节相同"，
      这里任何一点现场发挥（比如顺手补个 as_of=now）都会把那条判据变成噪声。
      as_of 固定为 'stub'。
    """
    payload = _stub_payload()
    items = list(payload.get('items') or [])[:max(1, int(limit or 6))]
    return {'ok': payload.get('ok', True),
            'items': items,
            'degraded': payload.get('degraded') or [],
            'count': len(items),
            'as_of': 'stub',
            'stub': True}
