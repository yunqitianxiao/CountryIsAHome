"""层1.5：**直连 HTTP** 的资讯源（免登录、零新依赖，requests 就够）。

为什么在 akshare 之外还要这一层：akshare 的 `stock_info_global_*` 只覆盖四家
（财联社/东财/新浪/同花顺 各一条），而"多源共振"这个信号是**源越多越准**——
同一个事件被 6 家报道和被 2 家报道，是完全不同的量级。这一层把交易所/巨潮的
**公告**、东财/同花顺的**快讯**直接接进来，全部是公开 JSON 接口，不需要 cookie。

★为什么不用 crawl4ai 抓这些：能用 JSON 接口的地方用网页解析是**自找脆弱**——
网页改一次版就静默少几条（标题抽取的失败长得很像"今天没新闻"）。
JSON 接口挂了会抛，抛了会进 degraded[]，那是看得见的。

★国内站直连（AGENT-SPEC §0.5）：本机 env 里 `http_proxy=127.0.0.1:7897`（Clash）。
  实测（2026-09-22 本机）这四家走代理**也通**（Clash 对国内域名走 DIRECT），
  但那是代理规则的偶然，不是我们的保证 —— 所以这里一律先 `direct_env()` 摘干净，
  把"能不能通"变成我们自己的事，而不是别人 config 的事。

★时间戳：一律来自**上游字段**（`showTime` / `ctime` / `publishTime` / 公告日期）。
  上游没给时间的条目，`ts=None` 并**保留**（标题本身有信息量），排序时沉底 ——
  绝不用"采集时刻"冒充事件时刻：那会让一条三个月前的公告看起来像刚刚发生。
"""
import json
import os
import re
import time

import feed          # 复用 _num（NaN/numpy 标量 → None）与 _with_timeout

DEFAULT_TIMEOUT = 12            # 比 akshare 的 45s 短：这几个接口实测都在 1.5s 内

_PROXY_KEYS = ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
               'ALL_PROXY', 'all_proxy')

_UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')


def direct_env():
    """摘掉全部代理变量，让国内站点直连。返回被摘掉的名字（给 health 留证据）。

    ★和 `feed.proxy_env_fix()` 不是一回事：那个只摘 `socks://` 型的 ALL_PROXY
      （治的是 requests 不认 socks scheme），http_proxy 是**故意留着**的。
      这一层是"国内直连"，所以连 http_proxy/https_proxy 一起摘。
    """
    gone = []
    for k in _PROXY_KEYS:
        if os.environ.pop(k, None) is not None:
            gone.append(k)
    return gone


def _get_json(url, params=None, headers=None, method='GET', data=None, timeout=None):
    """直连取 JSON。任何异常原样抛（调用方会结构化成 degraded）。"""
    direct_env()
    import requests
    h = {'User-Agent': _UA}
    h.update(headers or {})
    kw = {'headers': h, 'timeout': timeout or DEFAULT_TIMEOUT}
    if method == 'POST':
        if data is not None:
            kw['json'] = data
        r = requests.post(url, params=params, **kw)
    else:
        r = requests.get(url, params=params, **kw)
    r.raise_for_status()
    return r.json()


def _item(title, excerpt, url, ts, src, tags=None, related=None, ts_kind='upstream'):
    return {
        'title': str(title or '').strip()[:120],
        'excerpt': str(excerpt or '').strip()[:300],
        'source': src.get('id'),
        'source_label': src.get('label') or src.get('id'),
        'ts': ts,
        'ts_kind': ts_kind,
        'url': (str(url).strip() or None) if url else None,
        'tags': [str(t) for t in (tags or []) if t],
        'related': related or [],
        # ★这一层**没有**上游情绪分值：快讯/公告里没有可搬的分数，所以它是 None。
        #   写出来是为了让"这个源不提供"与"这个字段是空"长得一样（见 adata_src 同款）。
        #   谁都不许在这里填一个自己算的数 —— 那会与上游给的数长得一模一样。
        'sentiment_raw': None,
    }


# ── 各源的解析器：按**名字**取字段，不按位置（feed.to_canon 的同款纪律） ──────

def _p_em_724(js, src, ctx):
    """东方财富 7x24 全球快讯。

    实测（2026-09-22 本机）：358ms / 20 条。字段：title, summary, showTime, code。
    ★`code` 不是 URL —— 快讯是流水，没有逐条详情页，所以 url 留 None（不拼假链接）。
    """
    out = []
    for row in ((js.get('data') or {}).get('fastNewsList') or []):
        related = [{'code': c} for c in (row.get('stockList') or []) if c]
        out.append(_item(row.get('title'), row.get('summary'), None,
                         str(row.get('showTime') or '') or None, src,
                         related=related))
    return out


def _p_ths_push(js, src, ctx):
    """同花顺·快讯。

    实测（2026-09-22 本机）：1484ms / 100 条。`ctime` 是**秒级 unix 时间戳**
    （1790070107 ↔ 2026-09-22），不是字符串，必须自己转。
    `tags[].name` 是上游分类标签（美股/港股/A股/人工智能…）→ 进 item.tags，
    这是上游给的词，不是我们抽的（top_terms 只用上游标签，见 sentiment 模块）。
    """
    out = []
    for row in ((js.get('data') or {}).get('list') or []):
        ts = None
        c = row.get('ctime') or row.get('rtime')
        try:
            if c:
                ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(c)))
        except (TypeError, ValueError, OSError):
            ts = None
        tags = [t.get('name') for t in (row.get('tags') or []) if isinstance(t, dict)]
        related = [{'code': s.get('stockCode'), 'name': s.get('name')}
                   for s in (row.get('stock') or []) if isinstance(s, dict)]
        out.append(_item(row.get('title'), row.get('digest'), row.get('url'),
                         ts, src, tags=tags, related=related))
    return out


def _p_cninfo_ann(js, src, ctx):
    """巨潮资讯·全市场公告（POST /new/hisAnnouncement/query）。

    实测（2026-09-22 本机）：288ms / 30 条。`announcementTime` 是**毫秒**时间戳。
    PDF 地址 = `http://static.cninfo.com.cn/<adjunctUrl>`（实测 HEAD 200）。
    `columnId` 是上游的公告分类码 → 进 tags（码不是人能读的，但同时给了
    `announcementType` 的分类型；这里只保留分类码，正文分类要再查一次，不值当）。
    """
    out = []
    for row in (js.get('announcements') or []):
        ts = None
        t = row.get('announcementTime')
        try:
            if t:
                ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(t) / 1000))
        except (TypeError, ValueError, OSError):
            ts = None
        adj = row.get('adjunctUrl')
        out.append(_item(
            row.get('announcementTitle'),
            '%s %s' % (row.get('secName') or '', row.get('secCode') or ''),
            ('http://static.cninfo.com.cn/' + str(adj)) if adj else None,
            ts, src,
            related=[{'code': row.get('secCode'), 'name': row.get('secName')}]))
    return out


def _p_szse_ann(js, src, ctx):
    """深交所·上市公司公告（POST /api/disc/announcement/annList）。

    实测（2026-09-22 本机）：1487ms / 30 条。`publishTime` 已经是
    'YYYY-MM-DD HH:MM:SS' 字符串，直接用。
    PDF = `https://disc.szse.cn/download<attachPath>`（实测 HEAD 200）。
    `secCode`/`secName` 都是**列表**（一份公告可含多只票），这里全保留。
    """
    out = []
    for row in (js.get('data') or []):
        codes = row.get('secCode') or []
        names = row.get('secName') or []
        related = [{'code': codes[i] if i < len(codes) else None,
                    'name': names[i] if i < len(names) else None}
                   for i in range(max(len(codes), len(names)))]
        ap = row.get('attachPath')
        out.append(_item(
            row.get('title'), ' '.join(str(n) for n in names),
            ('https://disc.szse.cn/download' + str(ap)) if ap else None,
            str(row.get('publishTime') or '') or None, src, related=related))
    return out


def _p_headline_news(js, src, ctx):
    """爬虫层用：把 crawl4ai 抽出来的行（已经是 {title,excerpt,url,ts} 形状）
    统一成 item。这里不碰 HTML —— HTML 那部分在 `_c4a_worker.py` 的 schema 里。"""
    out = []
    for row in (js if isinstance(js, list) else []):
        url = row.get('url')
        ts = row.get('ts') or None
        if not ts and src.get('ts_from_url'):
            ts = _ts_from_url(url, src['ts_from_url'])
        out.append(_item(row.get('title'), row.get('excerpt'), url, ts, src,
                         tags=row.get('tags') or []))
    return out


_PARSERS = {
    'em_724': _p_em_724,
    'ths_push': _p_ths_push,
    'cninfo_ann': _p_cninfo_ann,
    'szse_ann': _p_szse_ann,
    'headline_news': _p_headline_news,
}


def _ts_from_url(url, rex):
    """从 URL 里抠出时间（金融界的详情页 URL 里带 `YYYY/MM/DDHHMMSS`）。

    ★这是**上游给的**时间（URL 是上游生成的），不是我们编的；抠不到就 None。
      正则用**具名组**（y/mo/d/h/mi/s），少一个组就当没抠到 —— 宁可让条目沉底，
      也不要一个拼错的时间去争排序。
    """
    if not url or not rex:
        return None
    m = re.search(rex, url)
    if not m:
        return None
    g = m.groupdict()
    if not all(g.get(k) for k in ('y', 'mo', 'd', 'h', 'mi', 's')):
        return None
    return '%s-%s-%s %s:%s:%s' % (g['y'], g['mo'], g['d'], g['h'], g['mi'], g['s'])


def parse_rows(parser, rows, src):
    """给爬虫层复用：把已抽好的行过一遍同一个归一化器。"""
    fn = _PARSERS.get(parser)
    if fn is None:
        raise ValueError('未登记的 http 解析器：%s（先在 _PARSERS 里写清字段名）' % parser)
    return fn(rows, src, {})


def _resolve(v):
    """源清单里的动态值占位符 → 真值。**配置是数据**：日期/时间戳这种每轮都变的
    参数写在 JSON 里（`"$today"`），而不是在代码里给某个源开 if。

    支持的占位符（就这四个，多一个都要在这里写清语义）：
      `$now_ms`        毫秒时间戳（东财的 req_trace 要它防缓存）
      `$today`         YYYY-MM-DD
      `$today_compact` YYYYMMDD
      `$days_ago:N`    N 天前的 YYYY-MM-DD
    """
    if isinstance(v, dict):
        return {k: _resolve(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve(x) for x in v]
    if isinstance(v, str) and v.startswith('$'):
        import datetime as _dt
        if v == '$now_ms':
            return str(int(time.time() * 1000))
        if v in ('$today', '$today_compact'):
            return time.strftime('%Y%m%d' if v.endswith('compact') else '%Y-%m-%d')
        if v.startswith('$days_ago:'):
            n = int(v.split(':', 1)[1])
            return (_dt.date.today() - _dt.timedelta(days=n)).strftime('%Y-%m-%d')
    return v


def fetch(src, symbol=None, terms=None, limit=10):
    """打一个直连源，返回归一化 item 列表（已按 terms 过滤、已截断）。"""
    parser = src.get('parser')
    fn = _PARSERS.get(parser)
    if fn is None:
        raise ValueError('未登记的 http 解析器：%s（先在 _PARSERS 里写清字段名）' % parser)
    timeout = int(src.get('timeout_sec') or DEFAULT_TIMEOUT)
    started = time.time()
    js = feed._with_timeout(
        lambda: _get_json(src['url'], params=_resolve(src.get('params')),
                          headers=src.get('headers'), method=src.get('method', 'GET'),
                          data=_resolve(src.get('body')), timeout=timeout),
        timeout + 3, src.get('id') or parser)
    out = fn(js, src, {})
    out = [it for it in out if it['title']]
    if src.get('scope') != 'symbol' and terms:
        joined = [t for t in terms if t]
        out = [it for it in out
               if any(t and (t in it['title'] or t in (it['excerpt'] or '')
                             or any(t in tg for tg in it['tags']))
                      for t in joined)]
    src['_last_ms'] = int((time.time() - started) * 1000)     # 给 health 用
    return out[:max(1, int(limit))]


def json_safe(obj):
    """调试/落盘用：确认没有 NaN/inf（AGENT-SPEC §0.7）。"""
    return json.dumps(obj, ensure_ascii=False, allow_nan=False)
