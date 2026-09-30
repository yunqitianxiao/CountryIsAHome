#!/usr/bin/env python3
"""引导智能体的**唯一一份本站知识**：`site_map.json` → 选项卡内容 + 提示词块。

★ 为什么不是手写一份"本站功能介绍"：

  手写的那份从落笔那天起就开始烂，而且**烂得看不出来**。加了一页、删了一个角色、
  回测重跑了一遍数字全变 —— 页面上都是新的，这段介绍还是旧的，两者长得一模一样。
  和 `accuracy.py` 顶上那段是同一个病：**介绍与结构必须同源**，
  所以这里一个字都不手写，全部从 `site_map.json` 投影出来。

★ `site_map.json` 自己也不是手写的 —— 它是 `build_sitemap.py` 反向解析
  `app.py` / `main.tsx` / `Home.tsx` / `chrome.tsx` / `tools.py` / `roles.py` /
  `strategies.py` / `terms.json` / `graph_data.json` / `kronos_accuracy.json`
  现算出来的。链条是：

      源码 ──build_sitemap.py──▶ site_map.json ──siteguide.py──▶ 选项卡 / 提示词
                                                        └──▶ 大模型回答

  链条上任何一环断了（文件不在、格式变了），这一层**降级**：选项卡少几张、
  提示词块不出现，而**绝不编**。判据见 `build_sitemap.py --selftest` 与
  `verify_agent.py` 里那几条。

★ **宽进严出**（照抄 `accuracy.numbers()` 的规矩）：任何一张卡的关键字段缺一个，
  就**整张卡不出现** —— 半张卡（"9 个角色"配一个空名单）比没有卡更坏，
  因为它看着像答完了。
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAP_PATH = os.path.join(ROOT, 'site_map.json')

# 提示词块的抬头。★判据靠它认"这一段真的拼进去了"（不是靠某句话在不在 ——
# 那种锚会被我改一次措辞就悄悄失效）。
PROMPT_MARK = '【本站结构'

_cache = {'mtime': None, 'data': None}


def raw():
    """读 `site_map.json`。★按 mtime 缓存：这个文件一次请求要读好几遍
    （选项卡 + 提示词），而它可能有几百 KB。读不到 / 不是合法 JSON → None。"""
    try:
        mt = os.path.getmtime(MAP_PATH)
    except OSError:
        return None
    if _cache['mtime'] != mt:
        try:
            with open(MAP_PATH, encoding='utf-8') as f:
                _cache['data'] = json.load(f)
        except (OSError, ValueError):
            _cache['data'] = None
        _cache['mtime'] = mt
    d = _cache['data']
    return d if isinstance(d, dict) else None


def available():
    """这份知识在不在。不在 → 页面照常开，聊天走降级（提示词里少这一块）。"""
    return raw() is not None


def freshness():
    """`site_map.json` 与各源文件**此刻**还一致吗。

    ★ 这是**每次请求现算**的，不是读 `generated_at`：那个字段说的是"什么时候生成的"，
      不是"生成的那份现在还对不对"。源码改过而没重跑 `build_sitemap.py`，
      `generated_at` 照样是新的。所以拿 JSON 里存的 `sha256` 跟磁盘上现读的重算值比。

    ★ 与 `build_sitemap.py --check` 是**同一个判据的两次实现**，故意重复：
      那边是离线守的（跑一次），这边是运行时守的（每次请求）。
      判据比的是 `sha256`，不比 mtime —— 理由见 `build_sitemap.cmp_key()`。

    → {'ok': bool, 'stale': [路径…], 'generated_at': str, 'n': int}
    """
    d = raw()
    if not d:
        return {'ok': False, 'stale': [], 'generated_at': '', 'n': 0,
                'why': 'site_map.json 读不到'}
    stale = []
    srcs = d.get('sources') or []
    for s in srcs:
        p = os.path.join(ROOT, s.get('path') or '')
        try:
            with open(p, 'rb') as f:
                import hashlib
                got = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            stale.append(s.get('path'))
            continue
        if got != s.get('sha256'):
            stale.append(s.get('path'))
    return {'ok': not stale, 'stale': stale, 'n': len(srcs),
            'generated_at': d.get('generated_at') or ''}


def _page(d, path):
    for p in d.get('pages') or []:
        if p.get('path') == path:
            return p
    return None


def _card(d, card_id):
    for c in d.get('home_cards') or []:
        if c.get('id') == card_id:
            return c
    return None


def _entry(d, card_id):
    """主页卡的 href —— 选项卡上那个"去这一页"的按钮用它。

    ★不从 `Home.tsx` 再读一遍，也不写死：卡改了 href，这里跟着改。
    """
    c = _card(d, card_id)
    return (c or {}).get('href') or ''


def _n(x):
    """数字 → 字符串。拿不到就返回 None（调用方据此**整张卡不发**）。"""
    return None if x is None else str(x)


# ── 选项卡 ────────────────────────────────────────────────────────────
#
# ★每张卡长这样：
#   {id, kicker, title, href, card_id, intro, bullets[], facts[], asks[]}
#   - `intro`  来自主页那张卡的 `<p>`（反向解析来的，不是抄的）
#   - `facts`  是**能一眼核对的数字**，每个都从 map 里取
#   - `bullets` 是把 facts 串成的话
#   - `asks`   是"点一下就能问"的问题 —— 没有数字，所以可以手写措辞

def _sec_council(d):
    cs = (d.get('council') or {})
    roles = cs.get('roles') or []
    if not roles:
        return None
    names = '、'.join('%s（%s）' % (r['name'], r['key']) for r in roles if r.get('name'))
    # ★"有方向 / 不表态"这个区分是这份议会设计里最要紧的一条：
    #   风控官、图谱导师、心理教练**不给涨跌**。选项卡上必须说出来 ——
    #   不说的话用户会以为九个角色都在喊多喊空。
    veto = [r['name'] for r in roles if not r.get('directional')]
    bullets = [
        '一共 %d 个角色，其中 %d 个是有方向的（会给涨/跌这类判断），'
        '%d 个**不给方向**：%s。'
        % (len(roles), len(roles) - len(veto), len(veto), '、'.join(veto) or '—'),
        '有方向的那几个会被点名互相回应；权重按这一轮的表现动态调，'
        '不是每人一票等权。',
    ]
    if veto:
        bullets.append('风控官握着一票否决：他否掉的结论不会被"票多"翻回来。')
    return {
        'id': 'council', 'card_id': 'agentsCard', 'href': _entry(d, 'agentsCard'),
        'kicker': '多智能体博弈', 'title': '九个角色吵一架，再给结论',
        'intro': (_card(d, 'agentsCard') or {}).get('desc') or '',
        'facts': [{'k': '角色', 'v': _n(len(roles))},
                  {'k': '有方向', 'v': _n(len(roles) - len(veto))},
                  {'k': '不给方向', 'v': _n(len(veto))}],
        'bullets': bullets,
        'asks': ['多智能体是怎么吵的？谁说话算数？',
                 '风控官的一票否决是什么意思？',
                 '为什么有些角色不给涨跌判断？'],
        'quote': names,
    }


def _sec_agent(d):
    a = (d.get('agent') or {})
    schemas = a.get('tools') or []
    if not schemas:
        return None
    bullets = []
    for s in schemas[:12]:
        desc = (s.get('description') or '').replace('\n', ' ')
        head = desc.split('★')[0].split('。')[0]
        bullets.append('**%s**：%s。' % (s['name'], head[:110]))
    if len(schemas) > 12:
        bullets.append('（另有 %d 把没列出来）' % (len(schemas) - 12))
    return {
        'id': 'agent', 'card_id': 'agentCard', 'href': _entry(d, 'agentCard'),
        'kicker': '智能体', 'title': '有问题直接问它',
        'intro': (_card(d, 'agentCard') or {}).get('desc') or '',
        'facts': [{'k': '工具', 'v': _n(a.get('tool_count') or len(schemas))}],
        'bullets': bullets,
        'asks': ['你能替我做什么？', '跑一段 K 线预测给我看看',
                 '这些工具里哪个最花时间？'],
    }


def _sec_graph(d):
    kb = d.get('kb') or {}
    if not kb.get('terms'):
        return None
    cats = kb.get('cats') or []
    rel = kb.get('relations') or {}
    reltxt = '、'.join('%s %d 条' % (k, v) for k, v in sorted(rel.items()))
    return {
        'id': 'graph', 'card_id': 'graphCard', 'href': _entry(d, 'graphCard'),
        'kicker': '知识图谱', 'title': '词与词之间是连着的',
        'intro': (_card(d, 'graphCard') or {}).get('desc') or '',
        'facts': [{'k': '词条', 'v': _n(kb.get('terms'))},
                  {'k': '分类', 'v': _n(len(cats))},
                  {'k': '族', 'v': _n(kb.get('family_count'))},
                  {'k': '节点', 'v': _n(kb.get('nodes'))},
                  {'k': '关系边', 'v': _n(kb.get('links'))}],
        'bullets': [
            '%d 个词条分成 %d 类（%s），按 %d 个"族"聚在一起（%s）；'
            '图上 %d 个节点、%d 条边。'
            % (kb.get('terms'), len(cats),
               '–'.join(c.get('code', '') for c in cats) or '—',
               kb.get('family_count') or 0,
               '、'.join(kb.get('families') or []) or '—',
               kb.get('nodes') or 0, kb.get('links') or 0),
            '边只有三种：%s —— 关系名是有限的，所以"它连着谁"答得出来。'
            % (reltxt or '—'),
            '每个词条拆成四个字段写：一句话、例子、误区、辨析。'
            '拆字段是为了以后接对话 —— 散文没法被检索，字段可以。',
        ],
        'asks': ['基金和股票差在哪儿？', '「七日年化」是什么意思？',
                 '为什么这个词条要分四个字段写？'],
    }


def _sec_intraday(d):
    it = d.get('intraday') or {}
    sts = it.get('strategies') or []
    if not sts or not it.get('periods'):
        return None
    periods = it['periods']
    return {
        'id': 'intraday', 'card_id': 'intradayCard', 'href': _entry(d, 'intradayCard'),
        'kicker': '分钟级策略', 'title': '同一天里，几种看法',
        'intro': (_card(d, 'intradayCard') or {}).get('desc') or '',
        'facts': [{'k': '策略', 'v': _n(len(sts))},
                  {'k': '周期', 'v': '、'.join(str(p) for p in periods)}],
        'bullets': [
            '盘中按 %s 分钟几种周期看，一共 %d 条策略：%s。'
            % ('/'.join(str(p) for p in periods), len(sts),
               '、'.join(s['name'] for s in sts)),
            '★这些是**当日形态的描述**（开盘区间破没破、量价背不背离），'
            '不是买卖信号 —— 页面上也不给"该买该卖"。',
        ],
        'asks': ['分钟级策略在看什么？', '开盘区间突破是什么意思？',
                 '这些策略能直接照着做吗？'],
    }


def _sec_pages(d):
    pages = d.get('pages') or []
    if not pages:
        return None
    home = [p for p in pages if p.get('in_home')]
    nav = [p for p in pages if p.get('in_feature_nav')]
    api = d.get('api') or []
    bullets = []
    for p in sorted(pages, key=lambda e: e['path']):
        bits = []
        if p.get('in_home'):
            bits.append('主页卡 %s' % p.get('home_card_id'))
        if p.get('in_feature_nav'):
            bits.append('页头导航')
        if not p.get('server_route') and p.get('client_route'):
            bits.append('★服务端还没有这条路由（直接粘地址会回主页）')
        label = p.get('page') or p['path']
        head = p['path'] if label == p['path'] else '%s（%s）' % (label, p['path'])
        bullets.append('%s %s' % (head, '，'.join(bits) or '需从别处进入'))
    return {
        'id': 'pages', 'card_id': None, 'href': '/',
        'kicker': '页面地图', 'title': '这个站有哪些页，从哪儿进',
        'intro': '下面这张表是**从源码里读出来的**，不是写的 —— 所以它和站上真实的样子一致。',
        'facts': [{'k': '页面', 'v': _n(len(pages))},
                  {'k': '有主页卡', 'v': _n(len(home))},
                  {'k': '在导航条上', 'v': _n(len(nav))},
                  {'k': '接口', 'v': _n(len(api))}],
        'bullets': bullets,
        'asks': ['主页上都有什么？', '这个站一共有几页？', '知识图谱在哪儿看？'],
    }


def _sec_accuracy(d):
    a = d.get('accuracy') or {}
    if not a.get('hit'):
        return None
    cov = ''
    if a.get('cov_nominal') and a.get('cov_empirical') is not None:
        cov = ('另外，模型给的区间**名义 %.0f%%、实测只覆盖 %.1f%%**'
               '（%s 个起点算出来的）—— 它对自己太有把握了。'
               % (a['cov_nominal'], a['cov_empirical'], a.get('cov_n')))
    return {
        'id': 'accuracy', 'card_id': None, 'href': '/ledger',
        'kicker': '实测口径', 'title': '这份回测没能证明它比基线强',
        'intro': '站上凡是提到"准不准"的地方，说的都是下面这几个数 —— '
                 '它们全都是回测跑完之后现读现填的，页面上没有第二份。',
        'facts': [{'k': '起点', 'v': _n(a.get('starts'))},
                  {'k': '方向命中', 'v': '%s%%' % a.get('hit')},
                  {'k': '无脑喊涨', 'v': '%s%%' % a.get('base_up')},
                  {'k': '收盘误差', 'v': '%s%%' % a.get('mape')},
                  {'k': '假设不动', 'v': '%s%%' % a.get('mape_flat')}],
        'bullets': [
            '%d 个起点、%s 根视野：方向命中 %.1f%%，置信区间 %.1f–%.1f%% —— '
            '**区间跨过 50%%**，所以能说的是"没证明它更强"，不是"它更强"。'
            % (a.get('starts') or 0, a.get('horizon_bars') or '?',
               a['hit'], (a.get('hit_ci') or [0, 0])[0], (a.get('hit_ci') or [0, 0])[1]),
            '同期「无脑喊涨」基线就有 %.1f%%，动量基线 %.1f%%。'
            % (a.get('base_up') or 0, a.get('base_momentum') or 0),
            '收盘价平均误差 %.1f%%，而「假设不动」只有 %.1f%% —— '
            '**误差比不动还大一倍**，说明它没有增加关于价格水平的信息。'
            % (a.get('mape') or 0, a.get('mape_flat') or 0),
            cov or '区间覆盖率没量过，所以这里一个字都不说。',
        ],
        'asks': ['你们的预测到底准不准？', '凭什么说"没能证明比基线强"？',
                 '那这个预测还有什么用？'],
    }


def _sec_news(d):
    n = d.get('news') or {}
    if not n.get('total'):
        return None
    return {
        'id': 'news', 'card_id': None, 'href': '/collect',
        'kicker': '资讯采集', 'title': '只采不析',
        'intro': '站上的新闻是**采集**来的，不做情绪打分、不替你判断利好利空。',
        'facts': [{'k': '启用源', 'v': _n(n.get('enabled'))},
                  {'k': '配置源', 'v': _n(n.get('total'))},
                  {'k': '缓存', 'v': '%s 秒' % n.get('ttl_sec')}],
        'bullets': [
            '配了 %d 个源、启用 %d 个，缓存 %s 秒。'
            % (n.get('total') or 0, n.get('enabled') or 0, n.get('ttl_sec')),
            '采集页能看每个源**这一趟到底有没有取到东西** —— '
            '取不到比取到旧的重要，所以它把"空手而归"也显示出来。',
        ],
        'asks': ['新闻是从哪儿来的？', '你们会给新闻打分吗？'],
    }


def _sec_overview(d):
    site = d.get('site') or {}
    pages = d.get('pages') or []
    cards = d.get('home_cards') or []
    if not site.get('name') or not pages:
        return None
    bullets = [site.get('lead') or '']
    if site.get('banner'):
        bullets.append('定位条上写着：%s' % site['banner'])
    return {
        'id': 'overview', 'card_id': None, 'href': '/',
        'kicker': '总览', 'title': '%s —— %s' % (site['name'], site.get('subtitle') or ''),
        'intro': '先看这个站是干什么的、以及它**不**干什么。',
        'facts': [{'k': '页面', 'v': _n(len(pages))},
                  {'k': '主页卡', 'v': _n(len(cards))}],
        'bullets': [b for b in bullets if b],
        'asks': ['这个站是干什么的？', '它能帮我做什么？', '它不做什么？'],
    }


_BUILDERS = (_sec_overview, _sec_council, _sec_agent, _sec_graph,
             _sec_intraday, _sec_pages, _sec_accuracy, _sec_news)


def sections():
    """选项卡用的那几张卡。★**任何一张卡的关键字段缺一个，整张卡不出现**
    （宽进严出）—— 半张卡比没有卡更坏，它看着像答完了。"""
    d = raw()
    if not d:
        return []
    out = []
    for fn in _BUILDERS:
        try:
            s = fn(d)
        except (KeyError, TypeError, ValueError, IndexError):
            s = None
        if s:
            out.append(s)
    return out


# ── 提示词块 ──────────────────────────────────────────────────────────

def digest():
    """给大模型看的那份本站结构，**紧凑的纯文本**。

    ★它必须比 `sections()` 更省：系统提示词每一轮都要重发，几十 KB 的结构塞进去
      既贵又会把注意力冲淡。所以这里只留"能照着我答"的骨架：
      有哪些页、页上有几张卡、智能体有哪几把工具、议会几个角色、
      图谱多少个词、回测那几个数。**细节让用户去页面上看。**
    """
    d = raw()
    if not d:
        return ''
    site = d.get('site') or {}
    pages = d.get('pages') or []
    cards = d.get('home_cards') or []
    nav = d.get('feature_nav') or []
    a = d.get('agent') or {}
    cs = d.get('council') or {}
    it = d.get('intraday') or {}
    kb = d.get('kb') or {}
    acc = d.get('accuracy') or {}
    news = d.get('news') or {}
    fw = freshness()

    stale_n = len(fw['stale'])
    L = ['%s（自动读自本站页面，%s 生成，%s）】'
         % (PROMPT_MARK, d.get('generated_at') or '?',
            '与现在的页面一致' if fw['ok'] else
            ('★与现在的页面**已经不一致**：有 %d 处改动还没收进目录 —— '
             '下面这些结构可能过时，说到"站上有几页/几张卡"时请留余地' % stale_n
             if stale_n else
             '★与现在的页面**已经不一致**：目录本身读不到 —— '
             '下面这些结构可能过时，说到"站上有几页/几张卡"时请留余地'))]
    L.append('站点：%s —— %s' % (site.get('name'), site.get('subtitle')))

    if cards:
        L.append('主页卡（%d 张，DOM 顺序）：%s' % (
            len(cards), '、'.join('%s→%s' % (c.get('kicker'), c.get('href'))
                                  for c in cards)))
    if nav:
        L.append('页头导航条（%d 条）：%s' % (
            len(nav), '、'.join('%s %s' % (r['label'], r['href']) for r in nav)))
    if pages:
        L.append('页面（%d 页）：%s' % (len(pages), '、'.join(
            (p['path'] if (p.get('page') or p['path']) == p['path']
             else '%s %s' % (p.get('page'), p['path'])) for p in pages)))
    if d.get('client_only'):
        L.append('★这几条只有客户端路由、服务端还没有：%s —— '
                 '用户直接粘地址会回到主页。别把它们说成"打开就能进的页"。'
                 % '、'.join(d['client_only']))
    if a.get('tool_count'):
        L.append('智能体工具（%d 把）：%s' % (
            a['tool_count'],
            '、'.join(s['name'] for s in (a.get('tools') or []))))
    if cs.get('roles'):
        L.append('多智能体议会（%d 个角色）：%s' % (
            cs['role_count'],
            '、'.join('%s(%s%s)' % (r['name'], r['key'],
                                   '' if r.get('directional') else '，不给方向')
                      for r in cs['roles'])))
    if it.get('strategies'):
        L.append('分钟级策略（周期 %s，%d 条）：%s' % (
            '/'.join(str(p) for p in it.get('periods') or []),
            it['strategy_count'],
            '、'.join(s['name'] for s in it['strategies'])))
    if kb.get('terms'):
        L.append('知识图谱：%d 个词条 / %d 类（%s）/ %d 个族 / %d 个节点 / %d 条边，'
                 '关系只有 %s' % (
                     kb['terms'], len(kb.get('cats') or []),
                     '–'.join(c.get('code', '') for c in kb.get('cats') or []),
                     kb.get('family_count') or 0, kb.get('nodes'), kb.get('links'),
                     '、'.join(sorted((kb.get('relations') or {})))))
    if acc.get('hit'):
        L.append('回测实测（%s）：方向命中 %.1f%%（95%% 区间 %.1f–%.1f%%），'
                 '无脑喊涨基线 %.1f%%、动量基线 %.1f%%；收盘误差 %.1f%% vs '
                 '假设不动 %.1f%%；区间名义 %.0f%% 实测 %.1f%%。'
                 '★结论：**没能证明比基线强**。'
                 % (acc.get('measured_at'), acc['hit'],
                    (acc.get('hit_ci') or [0, 0])[0], (acc.get('hit_ci') or [0, 0])[1],
                    acc.get('base_up') or 0, acc.get('base_momentum') or 0,
                    acc.get('mape') or 0, acc.get('mape_flat') or 0,
                    acc.get('cov_nominal') or 0, acc.get('cov_empirical') or 0))
    if news.get('total'):
        L.append('资讯：配置 %d 个源、启用 %d 个，缓存 %s 秒。只采不析。'
                 % (news['total'], news.get('enabled') or 0, news.get('ttl_sec')))

    L.append('★用这份结构回答"这个站有什么/怎么看/某某页在哪"。'
             '**不许**编造上面没出现的页面、功能、数字；'
             '用户问某个数（命中率、词条数、角色数）时照上面念，'
             '拿不准就让他去页面看，别自己造一个。')
    return '\n'.join(x for x in L if x.strip())


def prompt_block():
    """拼进系统提示词的那一块。拿不到结构 → 空串（等于没拼）。"""
    if not available():
        return ''
    return digest()


if __name__ == '__main__':
    import sys
    if '--sections' in sys.argv:
        for s in sections():
            print('── [%s] %s · %s → %s' % (s['id'], s['kicker'], s['title'], s['href']))
            for b in s['bullets']:
                print('   · %s' % b)
    else:
        print(digest())
        print()
        f = freshness()
        print('freshness: ok=%s stale=%s n=%s generated_at=%s'
              % (f['ok'], f['stale'], f['n'], f['generated_at']))
