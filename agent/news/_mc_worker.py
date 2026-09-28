#!/usr/bin/env python3
"""MediaCrawler worker —— 在 third_party/MediaCrawler 里跑一次关键词搜索，
把它落盘的 JSON 结果读回来、归一成本站的 item 形状。

请求（stdin 一行 JSON）：
    {"platform": "weibo", "mode": "search", "keywords": ["茅台"], "limit": 10,
     "source_id": "mc-weibo", "source_label": "微博·关键词搜索"}
响应（stdout 最后一行 JSON）：
    {"ok": true, "items": [...]} / {"ok": false, "error": "..."}

★MediaCrawler 是**一整个应用**（自己的 CLI、自己的依赖、cookies 存
  browser_data/），不同版本落盘的字段名不一样 —— 所以这里全部按
  「候选字段名逐个试」的防御式读法，读不出标题的行直接丢。
★它跑一次要起浏览器，超时给 120s（在 subproc_src 那侧）。
"""

import os
import sys
import json
import glob
import subprocess


def _pick(row, names):
    for n in names:
        v = row.get(n)
        if v not in (None, ''):
            return v
    return None


def _read_latest_json(mc_home, platform):
    pat = os.path.join(mc_home, 'data', platform, 'json', '*.json')
    files = sorted(glob.glob(pat), key=os.path.getmtime)
    if not files:
        return []
    with open(files[-1], encoding='utf-8') as f:
        rows = json.load(f)
    return rows if isinstance(rows, list) else [rows]


def main():
    try:
        req = json.loads(sys.stdin.read())
        mc_home = os.environ.get('LICAI_MC_HOME') or req.get('mc_home')
        if not mc_home or not os.path.isdir(mc_home):
            raise RuntimeError('找不到 MediaCrawler 目录（LICAI_MC_HOME）')
        platform = req['platform']
        keywords = ','.join(req.get('keywords') or []) or '股市'
        py = os.path.join(mc_home, '.venv', 'bin', 'python')
        if not os.path.exists(py):
            py = sys.executable
        cmd = [py, 'main.py', '--platform', platform, '--lt', 'qrcode',
               '--type', req.get('mode', 'search'), '--keywords', keywords,
               '--start', '1', '--save_data_option', 'json']
        r = subprocess.run(cmd, cwd=mc_home, capture_output=True, text=True,
                           timeout=int(req.get('run_timeout') or 300))
        rows = _read_latest_json(mc_home, platform)
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = _pick(row, ['title', 'desc', 'description', 'content', 'text'])
            if not title:
                continue
            items.append({
                'title': str(title).strip()[:120],
                'excerpt': str(_pick(row, ['desc', 'description', 'content']) or '')[:300],
                'url': _pick(row, ['note_url', 'url', 'link']) or None,
                'ts': _pick(row, ['create_time', 'created_time', 'publish_time', 'last_modify_time']),
                'source': req.get('source_id'),
                'source_label': req.get('source_label'),
            })
        if not items and r.returncode != 0:
            raise RuntimeError('MediaCrawler 退出码 %d：%s' % (r.returncode, (r.stderr or '')[-200:]))
        print(json.dumps({'ok': True, 'items': items[:int(req.get('limit') or 10)]},
                         ensure_ascii=False))
    except Exception as e:                       # noqa: BLE001
        print(json.dumps({'ok': False, 'error': '%s: %s' % (type(e).__name__, str(e)[:300])},
                         ensure_ascii=False))
        sys.exit(0)


if __name__ == '__main__':
    main()
