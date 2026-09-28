#!/usr/bin/env python3
"""crawl4ai worker —— 跑在 `.venv-collector` 里，主进程只经 stdin/stdout 和它说话。

请求（stdin 一行 JSON）：
    {"url": "https://...", "css": null, "schema": null, "terms": ["茅台"],
     "limit": 10, "source_id": "c4a-cls-web", "source_label": "财联社·网页电报"}
响应（stdout 最后一行 JSON）：
    {"ok": true, "items": [{"title","excerpt","url","ts","source","source_label"}]}
    {"ok": false, "error": "..."}

条目化策略（保持"采集不分析"）：
  - 给了 `schema`（crawl4ai 的 JsonCssExtractionStrategy 形状）就按 schema 抽；
  - 否则退化到「页面里的链接文本当标题」：取 markdown 生成结果里的 links，
    文本 ≥8 字的当条目；给了 terms 就只留命中的。
  - **不做正文摘要生成、不过 LLM** —— excerpt 就是链接周围或 schema 抽到的原文。

★这个文件不被主 venv import，`agent/news` 判据也不直接跑它（跑的是
  subproc_src 的协议层 + 假 worker）——它只在真装了 .venv-collector 时被用到。
"""

import sys
import json
import asyncio


def _md_text(result):
    md = getattr(result, 'markdown', None)
    if md is None:
        return ''
    raw = getattr(md, 'raw_markdown', None)
    return str(raw if raw is not None else md)


def _items_from_links(result, req):
    out = []
    links = getattr(result, 'links', None) or {}
    terms = [t for t in (req.get('terms') or []) if t]
    for group in ('internal', 'external'):
        for a in links.get(group) or []:
            text = (a.get('text') or '').strip()
            href = (a.get('href') or '').strip()
            if len(text) < 8 or not href.startswith('http'):
                continue
            if terms and not any(t in text for t in terms):
                continue
            out.append({'title': text[:120], 'excerpt': '', 'url': href,
                        'ts': None, 'source': req.get('source_id'),
                        'source_label': req.get('source_label')})
    return out


async def _crawl(req):
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
    kwargs = {'cache_mode': CacheMode.BYPASS, 'exclude_external_links': False}
    # ★原来这里是 `req.get('css') and req.get('schema')` —— 于是**只给 schema 的源
    #   会静默走链接回退**（拿到一页页脚备案链接却报 ok:true）。schema 是"要抽什么"，
    #   css 是"从哪儿抽"，两件事，不该绑在一起：有 schema 就用 schema。
    if req.get('schema'):
        from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
        kwargs['extraction_strategy'] = JsonCssExtractionStrategy(req['schema'])
    if req.get('css') and not req.get('schema'):
        kwargs['css_selector'] = req['css']       # 只要 scoping 的话，它才是对的那个参数
    cfg = CrawlerRunConfig(**kwargs)
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=req['url'], config=cfg)
    if not getattr(result, 'success', True):
        raise RuntimeError('抓取失败：%s' % getattr(result, 'error_message', '?'))
    extracted = getattr(result, 'extracted_content', None)
    if extracted:
        try:
            rows = json.loads(extracted) if isinstance(extracted, str) else extracted
        except ValueError:
            rows = []
        items = []
        for row in rows if isinstance(rows, list) else []:
            items.append({'title': str(row.get('title') or row.get('headline') or '')[:120],
                          'excerpt': str(row.get('excerpt') or row.get('content') or '')[:300],
                          'url': row.get('url') or req['url'],
                          'ts': row.get('ts') or row.get('time') or None,
                          'source': req.get('source_id'),
                          'source_label': req.get('source_label')})
        if items:
            return items
    return _items_from_links(result, req)


def main():
    try:
        req = json.loads(sys.stdin.read())
        items = asyncio.run(_crawl(req))
        limit = int(req.get('limit') or 10)
        print(json.dumps({'ok': True, 'items': items[:limit]}, ensure_ascii=False))
    except Exception as e:                       # noqa: BLE001 —— 协议要求结构化失败
        print(json.dumps({'ok': False, 'error': '%s: %s' % (type(e).__name__, str(e)[:300])},
                         ensure_ascii=False))
        sys.exit(0)                              # 退出码 0：失败信息在 JSON 里，不是崩溃


if __name__ == '__main__':
    main()
