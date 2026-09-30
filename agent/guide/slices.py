#!/usr/bin/env python3
"""一份站点目录 → 每个导览员**只拿自己那一块**。

★ 这一层是「导览团」与「一个智能体把整份目录塞进提示词」**唯一的结构差别**。

  差别不在措辞。把整份目录发给所有人、再嘱咐一句"你只答你负责的那块" ——
  那是**嘱咐**：模型可以不照做，而且**判据看不出来**（一个越界回答了别人那块问题的
  导览员，在日志里和正常的一模一样）。这里改成**物理上不给**：
  `part(d, role)` 只返回这个角色的那几个顶层键，别人的数字**根本没进那份 prompt**。

  判据就落在这句话上：某个导览员那次调用的 payload 里，**不许出现别块的数**
  （见 `verify_agent.py` 里"导览员只看见自己那块"那一条）。这条判据能成立，
  正是因为切片发生在这里、而不是发生在提示词里。

★ 目录只有一份，由编排层取一次（`siteguide.raw()`），六个导览员分着看。
  这对应议会那边的"一份证据九个人看"（`agents/evidence.py`）——
  同样一件事：**事实取一次，观点分头给**，不各取各的（各取各的就会互相矛盾）。
"""

import re

# 每个导览员负责的顶层键。★这份名单是**排他的**：一个键只归一个人。
#   重复归属会让"只看见自己那块"这条判据失去意义（两块都合法地有同一个数）。
OWNED = {
    'nav':      ('site', 'pages', 'home_cards', 'feature_nav', 'client_only', 'api'),
    'council':  ('council',),
    'agent':    ('agent',),
    'graph':    ('kb',),
    'strategy': ('intraday', 'news'),
    'accuracy': ('accuracy',),
}

# 给界面看的"这条是从目录的哪几块来的"。★手写、但不含任何数字 ——
#   所以它不会成为第二个事实来源（这条线上唯一的事源永远是 site_map.json）。
LABELS = {
    'nav':      ('站点信息', '页面地图', '主页卡', '页头导航', '客户端专有路由'),
    'council':  ('多智能体议会',),
    'agent':    ('智能体工具清单',),
    'graph':    ('知识图谱统计',),
    'strategy': ('分钟级策略', '资讯采集'),
    'accuracy': ('回测实测口径',),
}

_NUM = re.compile(r'\d+(?:\.\d+)?')

# 目录里**不作为事实**的块：这两个是账目（sha256 / 字节数 / mtime），
# 不是站上任何一个功能会引用的数。把它们排除，是为了让"这个数在不在目录里"
# 这句话的含义收窄到"它是不是一个站上的事实"。
_SKIP = ('sources', 'missing_sources')


def part(d, role):
    """这个导览员负责的那几个顶层键。★缺的就缺（不补默认值 —— 补了就是编）。

    ★★这是**同一扇门上的两把锁之一**，别把它当成唯一的那把：
      `part()` 决定"递过去的那本册子里有几页"，而各个 `_*_text` 里的手写
      `d.get(...)` 决定"它实际翻开哪几页"。**任一把单独都在**时，别人的数
      就进不了那份 prompt ⇒ 只放松这一把（让它返回整份目录）**在 payload 上
      看不出任何变化**，动态判据（"payload 里不许有别块的数"）不会红。

      判别句：**"我改的这一处，是这件事的唯一决定者吗？"** 不是的话，
      注入要打在"两把一起失效"的那一步上 —— 在 `verify_agent.py` 里就是 GG3
      （把整份目录直接当目录块发下去，绕过切片器），不是"放松 `part`"。

      所以这一把的**否证分担在两处**：`_TEXT` 那一把由静态判据守（⑧，读的字面量
      必须属于 `OWNED[role]`），"两把一起失效"由 ③ + GG3 守。
      单说 `part()` 自己，没有哪条注入能证明它还在 —— 这一点写在明处。
    """
    if not isinstance(d, dict) or role not in OWNED:
        return {}
    return {k: d[k] for k in OWNED[role] if k in d and d[k] not in (None, [], {}, '')}


def sources_of(role):
    return list(LABELS.get(role) or ())


# ── 每个导览员那份紧凑文本 ────────────────────────────────────────────
#
# ★格式纪律（照抄 `siteguide.digest()`）：每个数都跟一个**口径词**
#   （"N 个词条""N 类""命中 N%"）。裸数字进了提示词，模型就会把它们串成
#   一句看起来专业的错话，而这没有任何判据拦得住。

def _pages_text(d):
    L = []
    site = d.get('site') or {}
    if site.get('name'):
        L.append('站点：%s —— %s' % (site.get('name'), site.get('subtitle') or ''))
    if site.get('banner'):
        L.append('定位条：%s' % site['banner'])
    if site.get('lead'):
        L.append('一句话：%s' % site['lead'])
    cards = d.get('home_cards') or []
    if cards:
        L.append('主页卡（%d 张，从上到下的顺序）：%s'
                 % (len(cards), '、'.join('%s→%s' % (c.get('kicker'), c.get('href'))
                                          for c in cards)))
    nav = d.get('feature_nav') or []
    if nav:
        L.append('页头导航条（%d 条）：%s'
                 % (len(nav), '、'.join('%s %s' % (r.get('label'), r.get('href'))
                                        for r in nav)))
    pages = d.get('pages') or []
    if pages:
        L.append('页面（%d 页）：%s' % (len(pages), '、'.join(
            (p['path'] if (p.get('page') or p['path']) == p['path']
             else '%s %s' % (p.get('page'), p['path'])) for p in pages)))
    if d.get('client_only'):
        L.append('★只有客户端路由、服务端还没有这几条：%s —— '
                 '直接粘地址会回到主页，**别把它们说成"打开就能进的页"**。'
                 % '、'.join(d['client_only']))
    api = d.get('api') or []
    if api:
        L.append('接口（%d 个，页面之外给机器/判据用的）：%s'
                 % (len(api), '、'.join(a['path'] for a in api)))
    return L


def _council_text(d):
    cs = d.get('council') or {}
    roles = cs.get('roles') or []
    if not roles:
        return []
    L = ['多智能体议会（%s 个角色）：' % (cs.get('role_count') or len(roles))]
    for r in roles:
        L.append('- %s（%s）%s' % (r.get('name'), r.get('key'),
                                  '有方向：会给涨/跌这类判断' if r.get('directional')
                                  else '**不给方向**：只解释或只把关，不喊涨跌'))
    dirn = [r['name'] for r in roles if r.get('directional')]
    nodir = [r['name'] for r in roles if not r.get('directional')]
    if dirn:
        L.append('有方向的是这几个：%s。' % '、'.join(dirn))
    if nodir:
        L.append('不给方向的是这几个：%s —— ★这一条最容易被用户误解，'
                 '被问到时要说清楚：不是所有角色都在喊涨喊跌。' % '、'.join(nodir))
    return L


def _agent_text(d):
    a = d.get('agent') or {}
    s = a.get('tools') or []
    if not s:
        return []
    L = ['智能体工具（%s 把）：' % (a.get('tool_count') or len(s))]
    for x in s:
        desc = (x.get('description') or '').replace('\n', ' ')
        head = desc.split('★')[0].split('。')[0]
        L.append('- %s：%s。' % (x.get('name'), head[:110]))
    return L


def _graph_text(d):
    kb = d.get('kb') or {}
    if not kb.get('terms'):
        return []
    cats = kb.get('cats') or []
    rel = kb.get('relations') or {}
    L = ['知识图谱：%s 个词条 / %d 类 / %s 个族 / %s 个节点 / %s 条边。'
         % (kb.get('terms'), len(cats), kb.get('family_count'),
            kb.get('nodes'), kb.get('links'))]
    if cats:
        L.append('分类（每类的词条数）：%s'
                 % '、'.join('%s %s（%s 个）' % (c.get('code'), c.get('name'), c.get('claimed'))
                             for c in cats))
    if kb.get('families'):
        L.append('族：%s' % '、'.join(kb['families']))
    if rel:
        L.append('关系边只有这几种（名字是有限的，所以"它连着谁"答得出来）：%s'
                 % '、'.join('%s %d 条' % (k, v) for k, v in sorted(rel.items())))
    return L


def _strategy_text(d):
    L = []
    it = d.get('intraday') or {}
    sts = it.get('strategies') or []
    if sts:
        L.append('分钟级策略（时钟周期 %s 分钟，共 %s 条）：'
                 % ('/'.join(str(p) for p in it.get('periods') or []),
                    it.get('strategy_count') or len(sts)))
        for x in sts:
            L.append('- %s' % x.get('name'))
        L.append('★这些是**当日形态的描述**（开盘区间破没破、量价背不背离），'
                 '不是买卖信号；页面上也不给"该买该卖"。')
    n = d.get('news') or {}
    if n.get('total'):
        L.append('资讯采集：配置 %s 个源、启用 %s 个，缓存 %s 秒。源：%s。'
                 % (n.get('total'), n.get('enabled'), n.get('ttl_sec'),
                    '、'.join(n.get('names') or [])))
        L.append('★资讯**只采不析** —— 不做情绪打分、不替用户判断利好利空。')
    return L


def _accuracy_text(d):
    a = d.get('accuracy') or {}
    if not a.get('hit'):
        return []
    L = ['回测实测（%s 测的，模型 %s）：' % (a.get('measured_at'), a.get('model'))]
    L.append('- 起点 %s 个、视野 %s 根、回看 %s 根。'
             % (a.get('starts'), a.get('horizon_bars'), a.get('lookback_bars')))
    L.append('- 方向命中 %s%%（95%% 区间 %s–%s%%）—— ★区间跨过 50%%，'
             '所以能说的是"没证明它更强"，不是"它更强"。'
             % (a.get('hit'), (a.get('hit_ci') or [0, 0])[0], (a.get('hit_ci') or [0, 0])[1]))
    L.append('- 同期「无脑喊涨」基线就有 %s%%，动量基线 %s%%。'
             % (a.get('base_up'), a.get('base_momentum')))
    L.append('- 收盘价平均误差 %s%%，而「假设不动」只有 %s%% —— '
             '误差比不动还大一倍 ⇒ 它没有增加关于价格水平的信息。'
             % (a.get('mape'), a.get('mape_flat')))
    if a.get('cov_nominal') and a.get('cov_empirical') is not None:
        L.append('- 区间名义 %.0f%%、实测只覆盖 %s%%（%s 个起点算的）—— '
                 '它对自己太有把握了。'
                 % (a['cov_nominal'], a['cov_empirical'], a.get('cov_n')))
    L.append('★结论只有一句：**这份回测没能证明它比基线强**。'
             '谁问"准不准"，都从这句开始答。')
    return L


_TEXT = {
    'nav': _pages_text, 'council': _council_text, 'agent': _agent_text,
    'graph': _graph_text, 'strategy': _strategy_text, 'accuracy': _accuracy_text,
}


def text_of(d, role):
    """这个导览员这一趟看到的那段目录。★拿不到目录 → 空串（等于告诉它"你没有材料"）。

    ★★**必须经过 `part()`**，不能把整份目录直接递给 `_pages_text` 们。
      这一行是"切片是结构、不是嘱咐"这句话的落点，而它原来是断的：`part` 写好了
      却没人调用，真正决定导览员看见什么的是各个 `_*_text` 里**手写的**
      `d.get('xxx')` —— 也就是说切片由两处共同决定（`OWNED` 是声明、`_TEXT`
      里的字面量是实际），而没有任何东西保证这两处一致。往 `_pages_text` 里
      加一行 `d.get('council')`，切片的声明不动、导览员却看见了别人的块，
      **动态判据（读 payload 里有没有别块的数）也抓不到** —— 因为那样的
      `part()` 一致，是"哪几个键被读"漂了。
      ⇒ 现在 `OWNED` 是唯一声明：读一个不在 `OWNED[role]` 里的键，`part()` 里
        根本没有它 ⇒ 那行代码静默地得到 `None`。配套的静态判据
        （`verify_agent.py` 里"`_TEXT` 只读 `OWNED` 里那几个键"）守的就是这件事。
    """
    fn = _TEXT.get(role)
    if not fn or not isinstance(d, dict):
        return ''
    try:
        return '\n'.join(x for x in fn(part(d, role)) if x and str(x).strip())
    except (KeyError, TypeError, ValueError, IndexError):
        return ''


def overview_text(d):
    """给**主持**的定位块：这个站是干什么的、有哪些页。

    ★刻意**不含任何计数** —— 连"20 页"都不给。主持手上一个数都不该有，
    它要用的每一个数都必须来自某个导览员那句话。这样"主持说的数从哪来"
    才有唯一答案，也才审得了（`audit.check` 就是照这条设计的）。
    """
    if not isinstance(d, dict):
        return ''
    site = d.get('site') or {}
    L = []
    if site.get('name'):
        L.append('站点：%s —— %s' % (site.get('name'), site.get('subtitle') or ''))
    if site.get('lead'):
        L.append('它自己的定位：%s' % site['lead'])
    pages = d.get('pages') or []
    if pages:
        L.append('站上有这些页：%s' % '、'.join(
            (p['path'] if (p.get('page') or p['path']) == p['path']
             else '%s（%s）' % (p.get('page'), p['path'])) for p in pages))
    return '\n'.join(L)


# ── 数字池（校对位用）──────────────────────────────────────────────────

def _canon_num(v):
    """一个 JSON 数字值 → 它可能被写成的那几种字面。"""
    out = {'%g' % v}
    if float(v).is_integer():
        out.add('%d' % int(v))
        out.add('%.1f' % v)          # 模型爱写 46.0
    return out


def catalog_numbers(d):
    """目录里出现过的全部数字 —— 包括**表的条数**。校对位拿它当"有出处"的那一池。

    ★这是"有出处"的定义，两半都要，缺一半就会误报：

      ① `site_map.json` 里**字面出现过**的数（`"terms": 255`、`"hit": 52.3`）；
      ② **每张表的长度**（`home_cards` 表里 14 条 ⇒ 14 也算有出处）。

    ★② 不是放宽，是补漏 —— 第一次跑就撞上了：站点导览员说"主页卡 14 张"，
      而 `site_map.json` 里**只有那张表、没有"14"这个字面**（条数是 `len()` 算的）。
      于是校对位报了一个假红。这类假红的危害是**它会诱使人去改判据**：
      照着读数把 14 塞进白名单，判据就退化成"我曾经见过这些数"。

      正确的修法是**把定义补全**：一张 N 条的表，说"N 张"是从目录里读出来的事实，
      不是编的。所以长度和字面一样，都是"有出处"。
      判别句：**"这个数是编的，还是从目录里算出来的？"** —— 从目录里算出来的
      （`len()`），和从目录里念出来的（字面），在这一件事上是同一类。

    ★它**不说**"这个数用对了"（243 个起点写成 234，字面在目录里，这条查不出来）。
      一句话能证伪的事只有这一件，所以只做这一件 —— 射程写在 `audit.py` 顶上。
    """
    out = set()

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            out.update(_canon_num(v))
        elif isinstance(v, str):
            for m in _NUM.findall(v):
                out.add(m)
                try:
                    out.update(_canon_num(float(m)))
                except ValueError:
                    pass
        elif isinstance(v, dict):
            for k, x in v.items():
                if k in _SKIP:
                    continue
                walk(x)
        elif isinstance(v, (list, tuple)):
            out.update(_canon_num(len(v)))          # ★② 表的条数
            for x in v:
                walk(x)

    walk(d)
    return {x for x in out if x}


def numbers_in(text):
    """一段人话里出现的数字字面。"""
    return _NUM.findall(text or '')
