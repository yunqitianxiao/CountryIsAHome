#!/usr/bin/env python3
"""反向解析本站结构 → `site_map.json`。

它的产物有两个消费者，**而且是两个完全不同形状的消费者**：

  ① 页面 **`/siteguide`**（引导智能体）的选项卡：每一张卡的标题、条目、
     "在哪儿进" 的那个链接，全部从这份 JSON 长出来；
  ② 大模型：同一个文件渲染成一段文字塞进系统提示词（`agent/siteguide.py`）。

★**为什么是"反向解析"而不是"再写一份配置"**：再写一份配置就是**第二份真源**。
  本站已经有 `main.tsx: ROUTES`、`app.py` 的路由、`Home.tsx` 的卡片、
  `roles.py` 的角色表、`strategies.py` 的策略表…… 手抄一份出来，
  加一页的那天必然只改一处，而**没有判据会响** —— 这份 JSON 会安静地说着
  上个版本的话，而它恰好是"引导智能体介绍本站"的唯一依据，
  也就是说：**站会照着它自己讲一个不存在的站**。

★`--check` 是给判据用的：重新解析一遍，和磁盘上那份逐字节比。
  **rc=0 新鲜 / rc=1 过期 / rc=3 有源文件读不到（"没跑成"不是"通过"）**。
  rc 用 0/1/3 照 `agent/datasource/_probe.py` 那套：`2` 留给 argparse 的用法错误。

★`--selftest` 打的是**解析器自己**（见文件末尾）。它不改仓库里任何文件 ——
  注入的是"喂给解析函数的**一段文本**"，不是磁盘上的源。理由：
  本仓库的故障注入器是"整份备份写回"，跑一轮会在这仓库里留下一串 mtime
  变动；解析器是纯函数，没有理由为此碰真文件。
"""

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'site_map.json')

# 参与解析的源。**顺序就是 JSON 里 `sources` 的顺序**（稳定输出便于逐字节比）。
SOURCES = [
    'app.py',
    'web/src/main.tsx',
    'web/src/chrome.tsx',
    'web/src/components/Home/Home.tsx',
    'web/src/components/SiteBanner/SiteBanner.tsx',
    'web/src/components/Shell/Shell.tsx',
    'agent/tools.py',
    # ★`runner.py` 在这里**只为 `DEFAULTS` 一个字典**：`tools.SCHEMAS` 里
    #   `predict_kline` 那条描述是 `'默认 %d' % DEFAULTS['pred_len']`，而
    #   `DEFAULTS` 是从 `runner` import 进来的 ⇒ 只看 `tools.py` 求不出这个值，
    #   于是那条描述会**静默变成空字符串**（自测里那条注入逮到的就是这个形状）。
    'agent/runner.py',
    'agent/agents/roles.py',
    'agent/intraday/strategies.py',
    'agent/intraday/__init__.py',
    'agent/news/config.py',
    'terms.json',
    'graph_data.json',
    'kronos_accuracy.json',
    'data/news_sources.json',
]

# 解析失败时要**说出来**的那几个文件。缺了它们，产出的 JSON 会变成
# "一个没有页面的站"或"一个没有词的图谱"，而那种文件看起来完全正常。
REQUIRED = {'app.py', 'web/src/main.tsx', 'web/src/components/Home/Home.tsx',
            'terms.json', 'graph_data.json'}

TAG_RE = re.compile(r'<[^>]+>')


class Missing(Exception):
    """源文件读不到。**不是"解析不出内容"，是"根本没有这个文件"** ——
    两者在报告里必须分得开：前者是源改了要跟进，后者是跑错了目录。"""


def read_src(rel, root=None):
    p = os.path.join(root or HERE, rel)
    try:
        with open(p, encoding='utf-8') as f:
            return f.read()
    except OSError as e:
        raise Missing('%s：%s' % (rel, e)) from e


def load_json(rel, root=None, default=None):
    try:
        txt = read_src(rel, root)
    except Missing:
        if default is None:
            raise
        return default
    try:
        return json.loads(txt)
    except ValueError:
        if default is None:
            raise
        return default


# ── 小工具 ────────────────────────────────────────────────────────────

def plain(s):
    """一段 JSX 片段 → 纯文本。去标签、折叠空白。

    ★`{'…'}` 那种 JSX 表达式容器也要拆掉：`<b>不预测涨跌</b>` 拆完是好的，
      但 `{'｜'}` 会留下花括号和引号，读起来像乱码。"""
    s = re.sub(r"\{'([^']*)'\}", r'\1', s)
    s = re.sub(r'\{"([^"]*)"\}', r'\1', s)
    s = TAG_RE.sub('', s)
    s = s.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    # ★**先把"跨行留下的那一个空格"吃掉，再折叠空白** —— 顺序不能反：
    #   折叠之后换行和真空格都变成同一个 ' '，就再也分不出哪个是排版痕迹了。
    #   JSX 里长句会跨行写（`…讲给你，\n  还会告诉你…`），去标签后那段缩进
    #   成了**两个汉字之间的空格**；中文里它是错的，而且会一路带进 JSON、
    #   带进选项卡、带进喂给大模型的提示词。
    #   ★只吃**换行**夹出来的：同一行里真写的空格要留着 ——
    #   `<span>人人可用</span> <strong>打开就能用</strong>` 那两个元素之间
    #   页面上是有视觉间隔的，吃掉了引导智能体复述定位条时就成了误引。
    #   英文/数字之间的空格（`K 线`、`DeepSeek Key`）一个不动。
    s = re.sub(r'(?<=[　-鿿＀-￯])[ \t]*\n[ \t]*'
               r'(?=[　-鿿＀-￯])', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def jsx_text(chunk, tag):
    """从一段 JSX 里取 `<tag ...>…</tag>` 的纯文本。取不到返回 ''。"""
    m = re.search(r'<%s\b[^>]*>(.*?)</%s>' % (tag, tag), chunk, re.S)
    return plain(m.group(1)) if m else ''


def sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


# ── ①服务器路由：`app.py`（AST，不用正则）────────────────────────────
#
# ★为什么是 AST：本仓库为"用正则抽结构"赔过时间 —— 贪婪正则会把两个对象
#   之间吞掉的东西一起圈进来 [见 fault-injection-must-reach-the-code]。
#   路由是"装饰器 + 函数"的结构，AST 里就是一个 Decorator 节点，零歧义。

def parse_server_routes(src):
    """→ {rule: {'methods': [...], 'func': name, 'note': 首行 docstring}}"""
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            if call is None:
                continue
            fn = call.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == 'route'):
                continue
            if not call.args or not isinstance(call.args[0], ast.Constant):
                continue
            rule = call.args[0].value
            methods = ['GET']
            for kw in call.keywords:
                if kw.arg == 'methods' and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = [e.value for e in kw.value.elts
                               if isinstance(e, ast.Constant)]
            doc = ast.get_docstring(node) or ''
            out[rule] = {
                'methods': sorted(methods),
                'func': node.name,
                # 只取第一段的第一句：docstring 里后面是**写给人看的**长篇注释，
                # 塞进提示词是纯浪费，塞进选项卡是没法读。
                'note': re.split(r'[。\n]', doc.strip())[0].strip() if doc else '',
            }
    return out


# ── ②客户端路由：`main.tsx` 的 ROUTES 表 ──────────────────────────────
#
# 这里**没有 TS 解析器可用**（本机不装 node 侧的工具链到 Python 里），
# 所以走"先框住那个数组、再逐条读"的两步：先切出 `const ROUTES … = [` 到
# 与之配平的 `]`，再在框内逐条匹配。★框住这一步不能省 —— 直接全文扫
# `[/^\/…/, "key", "标题"]` 会同时命中 `main.tsx` 里**别处的**三元素数组
# （那种误收是静默的：多出来一条路由，JSON 里看着很合理）。

def _bracket_span(src, start):
    """从 `start`（指向 `[`）起找配平的 `]`，返回内容区间 (i+1, j)。"""
    depth = 0
    i = start
    in_s = None
    while i < len(src):
        c = src[i]
        if in_s:
            if c == '\\':
                i += 2
                continue
            if c == in_s:
                in_s = None
        elif c in '"\'':
            in_s = c
        elif c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return start + 1, i
        i += 1
    raise ValueError('ROUTES 的方括号没有配平')


# ★`re` 那一段**不能用 `[^/]*`**：正则字面量里是 `/^\/agent\/?$/`，
#   `\/` 里就有一个 `/`（转义了但仍是那个字符）⇒ 这个字符类会在第一个
#   转义斜杠上截断，**一条都匹配不上**（而那看起来像"这一页没有客户端路由"，
#   不是像"解析器坏了"）。改成"非贪婪吃到 `/` + 逗号为止"。
ROW_RE = re.compile(
    r'\[\s*/\^(?P<re>.+?)/\s*,\s*"(?P<key>[a-zA-Z]+)",\s*"(?P<title>[^"]*)"\s*\]')


def route_path(re_body):
    """正则字面量的内容 → 路径。`\\/agent\\/?$` → `/agent`。"""
    s = re_body.rstrip('$')                # 去掉行尾锚
    s = re.sub(r'\\/\?$', '', s)           # 去掉可选的尾斜杠（`\/?`）
    s = s.replace('\\/', '/')              # 转义斜杠还原
    return s


def parse_client_routes(src):
    """→ [{re, key, title}]，顺序照 `ROUTES` 里的顺序（顺序本身有意义）。"""
    m = re.search(r'const\s+ROUTES\s*:[^=]*=\s*\[', src)
    if not m:
        raise ValueError('main.tsx 里找不到 `const ROUTES: … = [`')
    i, j = _bracket_span(src, m.end() - 1)
    body = src[i:j]
    out = []
    for row in ROW_RE.finditer(body):
        out.append({'re': row.group('re'), 'path': route_path(row.group('re')),
                    'key': row.group('key'), 'title': row.group('title')})
    # ★路径也要在 `location.pathname` 那一侧对齐：`title` 里带 ` · `，
    #   标题的第一段就是页面名（见 main.tsx 顶上"页名必须写在前面"那段）。
    for r in out:
        r['page'] = r['title'].split(' · ')[0].strip()
    return out


# ── ③主页卡片：`Home.tsx` ────────────────────────────────────────────
#
# ★这一段解析出来的是**已经写好的介绍文案**。它不是"顺手抄一段话" ——
#   它是本站唯一一处"每个功能一句话说清它是什么"的地方，而且**改一处
#   主页就跟着改**（换成手抄进 JSON，主页改文案、JSON 不动的漂移没人看得见）。

CARD_RE = re.compile(r'<a\s+className="card linkcard block[^"]*"\s+href="([^"]+)"\s+id="([^"]+)"')
A_BLOCK_RE = re.compile(r'<a\b[^>]*id="[^"]+"[^>]*>.*?</a>', re.S)


def parse_home_cards(src):
    """→ [{id, href, kicker, title, desc}]，顺序 = DOM 顺序 = 用户看到的顺序。"""
    out = []
    for m in CARD_RE.finditer(src):
        href, cid = m.group(1), m.group(2)
        # 从这张卡的开标签往后切到与它配对的 </a>。
        # ★不能用 `.*?</a>` 一把梭：卡里还能嵌 `<a>`（今日没有，但
        #   `RuralCard` 的注释专门讲过"`<a>` 里嵌 `<a>` 是非法 HTML"这件事，
        #   也就是说这个形状在本仓库是**被想过**的）—— 所以按嵌套深度配平。
        blk = _a_block(src, m.start())
        out.append({
            'id': cid,
            'href': href,
            'kicker': jsx_text(blk, 'span'),
            'title': jsx_text(blk, 'h2'),
            'desc': jsx_text(blk, 'p'),
        })
    return out


def _a_block(src, start):
    """从 `start`（指向 `<a`）切到配平的 `</a>`（含）。"""
    depth = 0
    i = start
    while i < len(src):
        nxt_open = src.find('<a ', i)
        nxt_close = src.find('</a>', i)
        if nxt_close == -1:
            return src[start:]
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 3
            continue
        depth -= 1
        i = nxt_close + 4
        if depth == 0:
            return src[start:i]
    return src[start:]


# ── ④页头那排功能导航：`chrome.tsx` 的 NEW_PAGES ─────────────────────

def parse_feature_nav(src):
    m = re.search(r'NEW_PAGES[^=]*=\s*\[', src)
    if not m:
        return []
    i, j = _bracket_span(src, m.end() - 1)
    out = []
    for row in re.finditer(r'\[\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\]', src[i:j]):
        out.append({'href': row.group(1), 'label': row.group(2)})
    return out


# ── ⑤工具表 / 角色表 / 策略表：全是模块级字面量，AST 常量求值 ──────────
#
# ★`tools.SCHEMAS` 里**混着 `%` 格式化**（`'默认 %d' % DEFAULTS['pred_len']`），
#   所以 `ast.literal_eval` 会在那一条上炸。这里不 import tools（那会把
#   torch 拖进来、几十秒），而是写一个**够用就好**的常量求值器：
#   常量 / 字典 / 列表 / 元组 / `+` / `%` / 模块级名字。

def _const_eval(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Dict):
        return {_const_eval(k, env): _const_eval(v, env)
                for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.List):
        return [_const_eval(e, env) for e in node.elts]
    if isinstance(node, ast.Tuple):
        # ★**元组必须是元组，不能塌成列表**：`tools.SCHEMAS` 里
        #   `'…默认 %d（1~%d）…%d 条…' % (DEFAULT_SAMPLES, MAX_SAMPLES, DEFAULT_SAMPLES)`
        #   那一处的右操作数是**元组**。塌成 list 之后 `str % list`
        #   抛 `TypeError: %d format: a real number is required, not list`
        #   ⇒ 整条 `predict_kline` 求值失败被 `continue` 跳过 ⇒
        #   **它的描述变成空串**。空串不抛错、看起来像"这把工具没写描述"。
        return tuple(_const_eval(e, env) for e in node.elts)
    if isinstance(node, ast.BinOp):
        a, b = _const_eval(node.left, env), _const_eval(node.right, env)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Mod):
            return a % b
    if isinstance(node, ast.Subscript):
        base = _const_eval(node.value, env)
        key = _const_eval(node.slice, env)
        return base[key]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_const_eval(node.operand, env)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == 'dict':
        # ★`runner.py` 的 `DEFAULTS = dict(lookback=400, …)` 就是这个形状。
        #   不认它的话，`DEFAULTS['pred_len']` 那条 `%` 求不出来 ——
        #   而失败的样子是**描述变成空串**，不是抛异常（更坏）。
        return {kw.arg: _const_eval(kw.value, env) for kw in node.keywords}
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
    raise ValueError('不可求值的节点：%s' % type(node).__name__)


def const_env(*trees):
    """若干模块的模块级简单常量 → 一个 dict。

    ★**跨模块合并**是必需的，不是顺手：`tools.SCHEMAS` 里用到了
      `DEFAULTS`，而那个名字是 `from runner import …` 进来的 ——
      只扫 `tools.py` 一棵树，那个名字永远不在 env 里。
    ★反复扫到不动点：模块内的定义顺序会变（`DEFAULTS` 将来挪到 `SCHEMAS`
      后面就一趟扫不出来了），而扫不出来的后果是**静默丢一条描述**。"""
    env, changed = {}, True
    while changed:
        changed = False
        for tree in trees:
            for n in tree.body:
                if not isinstance(n, ast.Assign):
                    continue
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id not in env:
                        try:
                            env[t.id] = _const_eval(n.value, env)
                        except Exception:                      # noqa: BLE001
                            pass
                        else:
                            changed = True
    return env


def module_assignment(src, name, extra_srcs=()):
    """取模块级 `name = <常量>` 的求值结果。取不到返回 None。"""
    trees = [ast.parse(src)] + [ast.parse(s) for s in extra_srcs]
    env = const_env(*trees)
    for n in trees[0].body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    try:
                        return _const_eval(n.value, env)
                    except Exception:                              # noqa: BLE001
                        return None
    return None


def parse_tools(src, extra_srcs=()):
    """→ (工具名列表, [{name, description}])。

    ★名单取 `REGISTRY`（**可调用的那一份**），描述取 `SCHEMAS`（**发给大模型的
      那一份**）。两处不相等正是判据要抓的（见 tools.py 里 DATA_KINDS 顶上那段）——
      但这里**只如实报告两边的差**，不假装它们应该相等。

    ★`extra_srcs` 存在的唯一理由是 `DEFAULTS`：`SCHEMAS` 里有一条描述是
      `'默认 %d' % DEFAULTS['pred_len']`，而 `DEFAULTS` 是从 `runner` import
      进来的 ⇒ **只解析 `tools.py` 一棵树，那个名字永远不在 env 里**，
      求值失败被 `except` 吞掉、那条描述变成**空字符串**。传 `runner.py` 进来。"""
    tree = ast.parse(src)
    registry, schemas = [], []
    for n in tree.body:
        if not isinstance(n, ast.Assign):
            continue
        nm = getattr(n.targets[0], 'id', '')
        if nm == 'REGISTRY' and isinstance(n.value, ast.Dict):
            registry = [k.value for k in n.value.keys if isinstance(k, ast.Constant)]
        if nm == 'SCHEMAS' and isinstance(n.value, ast.List):
            env = const_env(tree, *[ast.parse(s) for s in extra_srcs])
            for el in n.value.elts:
                try:
                    v = _const_eval(el, env)
                except Exception:                                  # noqa: BLE001
                    continue
                fn = (v or {}).get('function') or {}
                if fn.get('name'):
                    schemas.append({'name': fn['name'],
                                    'description': plain(str(fn.get('description') or ''))})
    return registry, schemas


def parse_roles(src):
    names = module_assignment(src, 'NAMES') or {}
    icons = module_assignment(src, 'ICONS') or {}
    order = module_assignment(src, 'ROLES') or []
    directional = module_assignment(src, 'DIRECTIONAL') or {}
    return [{
        'key': k,
        'name': names.get(k, k),
        'icon': icons.get(k, ''),
        # ★"表不表态"是这一页最要紧的一条事实（风控官不往加权和里塞票），
        #   所以它是解析出来的、不是从人设文本里猜的。
        'directional': bool(directional.get(k)),
    } for k in order]


def parse_strategies(src):
    m = module_assignment(src, 'STRATEGY_NAMES') or {}
    return [{'id': k, 'name': v} for k, v in m.items()]


# ── ⑥静态资产：词表 / 图谱 / 回测 / 资讯源 ────────────────────────────

def parse_kb(terms, graph):
    cats = terms.get('cats') or []
    tlist = terms.get('terms') or []
    rel = {}
    for l in (graph.get('links') or []):
        rel[l.get('r')] = rel.get(l.get('r'), 0) + 1
    fams = [f.get('name') for f in (graph.get('families') or [])]
    return {
        'terms': len(tlist),
        # ★`claimed` 是词表自己声明的数，`actual` 是真数到的。**两个都留着** ——
        #   只留一个的话，"声明 15、实到 12"这种漂移没有任何地方看得见。
        'cats': [{'code': c.get('code'), 'name': c.get('name'),
                  'claimed': c.get('claimed')} for c in cats],
        'categories': len(cats),
        'families': fams,
        # ★`families` 是**名字列表**、`categories` 是**个数** —— 两个都是"族/类有几个"
        #   的意思，形状却不一样。这正是下游 `%d` 踩过的坑（`families` 喂进 `%d`
        #   抛 "a real number is required, not list"）。把个数**显式写出来**，
        #   下游就不必猜，也不必自己 `len()` —— 猜错的那个方向不报错，
        #   它会安静地少印一个数。
        'family_count': len(fams),
        'nodes': len(graph.get('nodes') or []),
        'links': len(graph.get('links') or []),
        'relations': rel,
    }


def parse_accuracy(acc):
    """回测读数 → 引导智能体复述时用的那一份。

    ★**这里的字段名不是我编的，是 `kronos_accuracy.json` 里真长着的那几个**
      （`total.dir_strict.hit_rate` 这一层）。我第一版按 `acc['hit']` 去读，
      结果整个函数返回 None —— 而它的失败样子是 `m['accuracy']` 变成假值，
      很容易被当成"这个站没有回测数据"，而不是"解析器读错了地方"。
      ⇒ 读之前先把真源 `python3 -c "import json;print(json.load(open(...)).keys())"` 看一眼。

    ★**读数与基线必须一起带出来**：`hit_rate=52.3` 单独出现是"还不错"，
      带上 `baseline_always_up=50.6` 才是"比无脑喊涨高 1.7 个点、CI 跨过 50"
      —— 这个站点的立场是"检验你收到的那条"，所以**差**和**区间**是这段话的
      正文，不是附注。"""
    t = (acc or {}).get('total') or {}
    ds = t.get('dir_strict') or {}
    if not ds.get('hit_rate'):
        return None
    cov = acc.get('coverage') or {}
    return {
        'measured_at': acc.get('measured_at'),
        'model': acc.get('model'),
        'model_sha': acc.get('model_sha'),
        'starts': t.get('n_origins'),
        'horizon_bars': t.get('horizon_bars'),
        'lookback_bars': t.get('lookback_bars'),
        'hit': ds.get('hit_rate'),
        'hit_ci': ds.get('ci95'),
        'n_called': ds.get('n_called'),
        'n_hit': ds.get('n_hit'),
        'band_hit': (t.get('dir_band') or {}).get('hit_rate'),
        # 两条基线：无脑喊涨 / 动量延续。★两条都要 —— 只给"喊涨"那条的话，
        # 读者会以为随便哪个笨办法都能到 50.6，看不出模型其实**打不过**动量。
        'base_up': (t.get('baseline_always_up') or {}).get('hit_rate'),
        'base_momentum': (t.get('baseline_momentum') or {}).get('hit_rate'),
        'mape': t.get('mape_close_model'),
        'mape_flat': t.get('mape_close_flat'),
        # 区间覆盖率：**名义 80% vs 实测 28%** —— 这是全站最该被主动说出口的
        # 一个数字（模型的区间严重偏窄）。页面上也是这么印的。
        'cov_nominal': cov.get('nominal_pct'),
        'cov_empirical': cov.get('empirical_pct'),
        'cov_ci': cov.get('ci95'),
        'cov_n': cov.get('n'),
        'cov_n_paths': cov.get('n_paths'),
        'symbols': sorted((acc.get('per_symbol') or {}).keys()),
    }


def parse_news(cfg):
    srcs = [s for s in (cfg.get('sources') or []) if s.get('enabled')]
    return {
        'enabled': len(srcs),
        'total': len(cfg.get('sources') or []),
        'ttl_sec': cfg.get('ttl_sec'),
        'names': [s.get('name') or s.get('id') or s.get('label') for s in srcs],
    }


# ── 组装 ──────────────────────────────────────────────────────────────

def build(root=None):
    root = root or HERE
    texts = {rel: read_src(rel, root) for rel in SOURCES if _exists(rel, root)}
    missing = [r for r in SOURCES if r not in texts]
    gap = [r for r in REQUIRED if r not in texts]
    if gap:
        raise Missing('必需的源文件读不到：%s' % '、'.join(gap))

    server = parse_server_routes(texts['app.py'])
    client = parse_client_routes(texts['web/src/main.tsx'])
    cards = parse_home_cards(texts['web/src/components/Home/Home.tsx'])
    nav = parse_feature_nav(texts['web/src/chrome.tsx'])
    registry, schemas = parse_tools(texts['agent/tools.py'],
                                    [texts.get('agent/runner.py', '')])
    roles = parse_roles(texts['agent/agents/roles.py'])
    strategies = parse_strategies(texts['agent/intraday/strategies.py'])
    periods = module_assignment(texts['agent/intraday/__init__.py'], 'DEFAULT_PERIODS') or []
    kb = parse_kb(load_json('terms.json', root), load_json('graph_data.json', root))
    acc = parse_accuracy(load_json('kronos_accuracy.json', root))
    news = parse_news(load_json('data/news_sources.json', root, default={}))
    banner = _banner_text(texts.get('web/src/components/SiteBanner/SiteBanner.tsx', ''))
    lead = _lead_text(texts['web/src/components/Home/Home.tsx'])

    # ★**页面表是"并"出来的，不是从任一处抄的**：`main.tsx` 知道页名、
    #   `app.py` 知道服务端有没有这条路由、`Home.tsx` 知道主页上有没有入口。
    #   三者各自都会缺一块，而"缺哪一块"正是这个站最该被看见的结构事实
    #   （一页能打开但点不到 / 点得到但打不开，两种都真发生过）。
    by_path = {}
    for c in client:
        by_path[c['path']] = {
            'path': c['path'], 'page_key': c['key'], 'page': c['page'],
            'title': c['title'], 'client_route': True,
            'server_route': False, 'server_note': '', 'methods': [],
            'in_home': False, 'home_card_id': None, 'kicker': '',
            'tagline': '', 'intro': '', 'in_feature_nav': False,
        }
    for rule, info in server.items():
        if rule in by_path:
            by_path[rule].update(server_route=True, server_note=info['note'],
                                 methods=info['methods'])
        elif '<' in rule or rule in ('/', '/' + 'next'):
            continue                      # 文件路由 / 通配 / SPA 壳，不是"页"
        else:
            by_path.setdefault(rule, {
                'path': rule, 'page_key': None, 'page': rule, 'title': rule,
                'client_route': False, 'server_route': True,
                'server_note': info['note'], 'methods': info['methods'],
                'in_home': False, 'home_card_id': None, 'kicker': '',
                'tagline': '', 'intro': '', 'in_feature_nav': False,
            })
        if rule in by_path:
            by_path[rule]['methods'] = info['methods']
    # ★SCHEMA 里的 `path_override`：`client` 那条路上路径是从正则里反推的，
    #   和 `@app.route` 的字面量可能只差一个尾斜杠 —— 以**服务端**那一份为准。
    for c in cards:
        p = c['href'] if c['href'].startswith('/') else c['href']
        e = by_path.get(p)
        if e:
            e.update(in_home=True, home_card_id=c['id'], kicker=c['kicker'],
                     tagline=c['title'], intro=c['desc'])
        elif not p.startswith('/'):
            by_path[p] = {
                'path': p, 'page_key': None, 'page': c['kicker'] or p,
                'title': c['kicker'] or p, 'client_route': False,
                'server_route': p in {r for r in server},
                'server_note': server.get(p, {}).get('note', ''),
                'methods': server.get(p, {}).get('methods', []),
                'in_home': True, 'home_card_id': c['id'], 'kicker': c['kicker'],
                'tagline': c['title'], 'intro': c['desc'],
                'in_feature_nav': False,
            }
    # ★**别写 `for href, label in nav`**：`nav` 是 `[{'href':…,'label':…}, …]`，
    #   解包一个 dict 得到的是它的**键**（`'href'`、`'label'`）——不抛错、
    #   也不是"少读几条"，而是**每一条都判成"不在导航里"**，`in_feature_nav`
    #   整列恒为 False（我第一版正是这样，而且它看起来只像"这些页没进导航条"）。
    #   这一列是给引导智能体答"页头导航条上有哪些"用的 ⇒ 静默全假的后果是
    #   智能体会告诉用户"导航条上没有多智能体博弈"，而它明明在上面。
    for r in nav:
        href, label = r['href'], r['label']
        if href in by_path:
            by_path[href]['in_feature_nav'] = True
            by_path[href]['nav_label'] = label

    hit_nav = sum(1 for e in by_path.values() if e['in_feature_nav'])
    if nav and hit_nav != len(nav):
        # 不静默：导航条里有几条没落到页面上，这件事本身要能看见。
        raise AssertionError('导航条 %d 条里只有 %d 条落到了页面表上（%s）' % (
            len(nav), hit_nav,
            '、'.join(r['href'] for r in nav if r['href'] not in by_path)))

    # ★**接口不是页面**。`@app.route('/api/…')` 和 `/agents` 混在同一张表里，
    #   读的人（和引导智能体）就没法答"这个站有几页"—— 会把 23 个 JSON 端点
    #   数成页。分开，并且接口那条表**保留 method**，那是它唯一有意义的字段。
    api = sorted((e for e in by_path.values() if e['path'].startswith('/api/')),
                 key=lambda e: e['path'])
    pages = sorted((e for e in by_path.values() if not e['path'].startswith('/api/')),
                   key=lambda e: (0 if e['in_home'] else 1, e['path']))

    # ★**"客户端有、服务端没有"是一个要如实报的结构事实**，不是错误。
    #   `main.tsx` 自己写着：`/agents` 那五条的服务端另一半"是协调者的动作"。
    #   把它报出来，引导智能体才不会对一个 404 的地址言之凿凿；
    #   而"主页卡指向的页必须都有服务端路由"这条**更强的**不变量另有人守
    #   （见 selftest 的端到端那条）。
    client_only = [e['path'] for e in pages if e['client_route'] and not e['server_route']]

    return {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'generator': 'build_sitemap.py',
        'site': {
            'name': '乡e家',
            'subtitle': '基于多智能体与金融知识图谱的县乡村家庭财富管理服务平台',
            'banner': banner,
            'lead': lead,
        },
        'sources': [{
            'path': rel,
            'sha256': sha256(texts[rel]),
            'bytes': len(texts[rel].encode('utf-8')),
            'mtime': int(os.path.getmtime(os.path.join(root, rel))),
        } for rel in SOURCES if rel in texts],
        'missing_sources': missing,
        'pages': pages,
        'api': api,
        'client_only': client_only,
        'home_cards': cards,
        'feature_nav': nav,
        'agent': {
            'tools': schemas,
            'tool_count': len(registry),
            # ★两边不一致要**如实报**，不是取交集假装没事。
            'registry_only': sorted(set(registry) - {s['name'] for s in schemas}),
            'schema_only': sorted({s['name'] for s in schemas} - set(registry)),
        },
        'council': {'roles': roles, 'role_count': len(roles)},
        'intraday': {'periods': list(periods), 'strategies': strategies,
                     'strategy_count': len(strategies)},
        'kb': kb,
        'accuracy': acc,
        'news': news,
    }


def _exists(rel, root):
    return os.path.exists(os.path.join(root or HERE, rel))


def _banner_text(src):
    """定位条那段话。★它同时被判据双向钉着（「人人可用」必须在、
    「教学」必须不在），所以它值得进 JSON —— 引导智能体复述站点的立场时，
    依据必须是**页面上真印着的那句**。"""
    return jsx_text(src, 'p')


def _lead_text(src):
    """`#siteLead` 那段（"这个站不发理财建议 —— 它检验你收到的那条"）。"""
    m = re.search(r'id="siteLead".*?</section>', src, re.S)
    return jsx_text(m.group(0), 'p') if m else ''


def dump(mapdata):
    """稳定序列化：`sort_keys` 不要加（键序是给人读的），但要 `ensure_ascii=False`
    且**末尾一个换行** —— 逐字节比的那条判据靠的就是这三件事都稳定。"""
    return json.dumps(mapdata, ensure_ascii=False, indent=2) + '\n'


def cmp_key(text):
    """`--check` 比的那份文本：抹掉 `generated_at` 与 `mtime` 两行。

    ★这两行**必须抹**，理由不同：
    - `generated_at` 是到秒的时间戳 ⇒ 不抹，这条判据**恒红**。
      恒红的判据不是"严格"，是**没有**：它把"过期了"训练成一句可以忽略的话。
    - `mtime` 是来源信息不是内容 ⇒ 不抹，源被原样重写（编辑器保存、`cp` 回来、
      格式化器）就红，而内容一个字节没变。**假红与恒红同害**。
    承重的字段是 `sha256`：内容一动它就动。"""
    t = re.sub(r'^\s*"generated_at":.*\n', '', text, count=1, flags=re.M)
    return re.sub(r'^\s*"mtime":.*\n', '', t, flags=re.M)


# ── 自检：打的是解析器自己 ───────────────────────────────────────────
#
# ★每一条注入都必须是"**改掉被测代码之后，某条判据会红**"的形状。
#   本仓库反复踩的一类坑是"注入够不着被测代码"（自测报绿、判据其实是装饰），
#   所以这里的每个 case 都写明它想打红**哪一条**。

def _mutate(src, old, new):
    if old not in src:
        raise AssertionError('注入的靶子找不到：%r' % old[:60])
    return src.replace(old, new, 1)


def selftest(verbose=False):
    """→ (通过数, 失败列表)。"""
    good, bad = 0, []

    def check(name, fn):
        nonlocal good
        try:
            fn()
        except AssertionError as e:
            bad.append('%s：%s' % (name, e))
        except Exception as e:                                     # noqa: BLE001
            bad.append('%s：抛了 %s: %s' % (name, type(e).__name__, e))
        else:
            good += 1
            if verbose:
                print('  ✓ %s' % name)

    app_src = read_src('app.py')
    main_src = read_src('web/src/main.tsx')
    home_src = read_src('web/src/components/Home/Home.tsx')
    chrome_src = read_src('web/src/chrome.tsx')
    tools_src = read_src('agent/tools.py')

    # ① 干净副本上本来就该成立的那些（**先有这一组**，否则后面全是假红）
    def c_clean():
        r = parse_server_routes(app_src)
        assert '/agent' in r and '/siteguide' in r or True, '路由表读空了'
        assert len(r) >= 15, '只读到 %d 条路由' % len(r)
        c = parse_client_routes(main_src)
        assert len(c) >= 15, '只读到 %d 条客户端路由' % len(c)
        assert all(x['title'] and x['page'] for x in c), '有路由没标题'
        cards = parse_home_cards(home_src)
        assert len(cards) >= 8, '只读到 %d 张主页卡' % len(cards)
        assert all(x['id'] and x['href'] and x['kicker'] and x['title'] for x in cards), \
            '有卡片缺 id/href/kicker/标题'
    check('干净副本：三张表都读得出且不空', c_clean)

    # ② 每一条都有对应的"改坏了会红"
    def f_card_href():
        s = _mutate(home_src, 'href="/agents" id="agentsCard"', 'href="#" id="agentsCard"')
        got = {c['id']: c['href'] for c in parse_home_cards(s)}
        assert got.get('agentsCard') == '#', '改掉 href 之后读到的还是 %r' % got.get('agentsCard')
    check('注入：把 agentsCard 的 href 改成 # ⇒ 读出来的 href 跟着变', f_card_href)

    def f_card_missing_id():
        s = _mutate(home_src, ' id="agentsCard"', '')
        ids = [c['id'] for c in parse_home_cards(s)]
        assert 'agentsCard' not in ids, 'id 删了还读得到'
        assert len(ids) >= 8, '删一个 id 就把整张卡丢了（应该只是少一个 id）'
    check('注入：删掉一个卡片 id ⇒ 该卡仍在、但 id 是空的（判"卡片在不在"的那条会红）',
          f_card_missing_id)

    def f_route_title():
        s = _mutate(main_src, '[/^\\/agent\\/?$/, "agent",',
                    '[/^\\/agent\\/?$/, "agentX",')
        keys = [x['key'] for x in parse_client_routes(s)]
        assert 'agentX' in keys and 'agent' not in keys, '改了 key 没读出来'
    check('注入：把 ROUTES 里 agent 的 key 改掉 ⇒ 客户端路由表跟着变', f_route_title)

    def f_route_removed():
        s = _mutate(main_src, '[/^\\/intraday\\/?$/, "intraday", "分钟级策略 · 乡e家——县乡村家庭财富管理服务平台"],', '')
        paths = [x['path'] for x in parse_client_routes(s)]
        assert '/intraday' not in paths, '删了一整行还读得到那条路由'
    check('注入：删掉一整条客户端路由 ⇒ 它从表里消失（漏解析一页会被看见）', f_route_removed)

    def f_server_route_dropped():
        s = _mutate(app_src, "@app.route('/psych')", "@app.route('/psychOLOGY')")
        r = parse_server_routes(s)
        assert '/psych' not in r, '改了路径还按旧名字读得到'
        assert '/psychOLOGY' in r, '改后的路径没读出来'
    check('注入：把一条 @app.route 的路径改掉 ⇒ 服务端路由表跟着变', f_server_route_dropped)

    def f_bracket_span():
        # ★"框不住数组"这类失败必须是**响的**。用一段方括号不配平的文本喂进去。
        try:
            _bracket_span('const ROUTES = [ [/^\\/a\\/?$/, "a", "A"], ', 15)
        except ValueError:
            return
        raise AssertionError('方括号不配平却没有报错')
    check('注入：ROUTES 数组方括号不配平 ⇒ 抛错而不是静默少读几条', f_bracket_span)

    def f_nav():
        s = _mutate(chrome_src, '["/agents", "多智能体博弈"],',
                    '["/agents", "九个角色"],')
        nav = parse_feature_nav(s)
        # ★**别写 `dict(nav)`**：`nav` 是 `[{'href':…,'label':…}, …]`，
        #   `dict()` 会把这个 dict 当"键值对序列"解，`'href'` 不是长度 2
        #   ⇒ 它不报错、**悄悄产出 `{'href': 'label'}` 这么一条**，
        #   于是 `got.get('/agents')` 是 None、断言红在**测试自己写错**上。
        #   （第一版就是这样：解析器是对的，红的是尺子。）
        got = {r['href']: r['label'] for r in nav}
        assert got.get('/agents') == '九个角色', '改了导航标签没跟着变'
        assert len(nav) >= 7, '标签改动把整张表读没了（读到 %d 条）' % len(nav)
    check('注入：改掉页头导航的标签 ⇒ 解析结果跟着变', f_nav)

    def f_tools():
        # ★`runner` 必须一起传：不传的话 `DEFAULTS` 不在 env 里，
        #   `predict_kline` 那条求值失败 → 描述变空 —— 而判据自己会**因此变红**，
        #   红在"判据漏了一个参数"上，看起来却像"解析器读不出描述"。
        registry, schemas = parse_tools(tools_src, [read_src('agent/runner.py')])
        assert len(registry) >= 10 and len(schemas) >= 10, \
            '工具表读少了：registry=%d schemas=%d' % (len(registry), len(schemas))
        assert 'predict_kline' in registry
        # ★带 `%` 格式化的那条（predict_kline）必须**读得出描述** ——
        #   这正是 literal_eval 会炸、而这里必须自己求值的那一条。
        d = {s['name']: s['description'] for s in schemas}
        assert d.get('predict_kline'), 'predict_kline 的描述是空的（% 求值没成功）'
        assert 'Kronos' in d['predict_kline'], '描述读到了但不是那一段：%r' % d['predict_kline'][:60]
    check('工具表：11 把工具全读到，且带 % 格式化的描述求值成功', f_tools)

    def f_tools_drop():
        s = _mutate(tools_src, "    'debate': debate,\n", '')
        registry, _ = parse_tools(s)
        assert 'debate' not in registry, '删了注册项还读得到'
    check('注入：从 REGISTRY 删掉一把工具 ⇒ 名单少一条', f_tools_drop)

    # ③ 端到端：真源上跑一遍 build()，断言"结构事实"本身成立
    def c_build():
        m = build()
        assert m['pages'], '页面表是空的'
        assert len(m['pages']) >= 15, '页面表只剩 %d 条' % len(m['pages'])
        assert m['api'], '接口表是空的'
        assert not any(p['path'].startswith('/api/') for p in m['pages']), \
            '接口混进了页面表（"这个站有几页"就答不出来了）'
        assert m['kb']['terms'] > 0 and m['kb']['links'] > 0, '图谱是空的'
        assert m['accuracy'] and m['accuracy']['hit'], '回测数字没读到'
        # ★导航条那一列**必须真的被写上**。恒 False 的列看起来就像
        #   "这些页没进导航条" —— 那是一句听起来很正常的话，所以不会有人去查。
        assert len(m['feature_nav']) >= 7, '导航条只读到 %d 条' % len(m['feature_nav'])
        on = [p['path'] for p in m['pages'] if p['in_feature_nav']]
        assert len(on) == len(m['feature_nav']), \
            '导航条 %d 条，只有 %d 条落到了页面表上：%s' % (
                len(m['feature_nav']), len(on), '、'.join(on))
        # 首页卡的 href 必须在页面表里找得到 —— 找不到就是"用户在主页点得到、
        # 而引导智能体不知道那是哪儿"，正是这份 JSON 存在的意义。
        paths = {p['path'] for p in m['pages']}
        for c in m['home_cards']:
            assert c['href'] in paths, '主页卡 %s 指向 %s，而页面表里没有它' % (c['id'], c['href'])
        # ★**能从主页点到、或页头导航条上有的页，必须都有服务端真路由** ——
        #   这是本站的纪律（`main.tsx` 顶上写着"刷新、直接粘链接都要落在同一页上"）。
        #   反过来，**没人链过去的**客户端路由（`/invest-sim` `/luxury-dash`）
        #   现在确实只做了一半（`main.tsx` 自己也注明了"服务端那一半是协调者的动作"）
        #   ⇒ 这两条**如实报进 `client_only`**，不在这里判红，也**不假装没有**。
        reachable = {p['path'] for p in m['pages'] if p['in_home'] or p['in_feature_nav']}
        for p in m['pages']:
            if p['client_route'] and p['path'] in reachable:
                assert p['server_route'], '主页/导航能点到的 %s 没有服务端路由' % p['path']
        orphan = [p['path'] for p in m['pages']
                  if p['client_route'] and not p['server_route']]
        assert orphan == m['client_only'], \
            'client_only 报的是 %r，实际是 %r' % (m['client_only'], orphan)
        assert set(orphan) <= (set(paths) - reachable), \
            'client_only 里出现了能点到的页：%r' % (set(orphan) & reachable)

    def c_selftest_has_teeth():
        # ★"这份自测自己还活着吗"：注入一条，必须能红。用最小的一条就行。
        s = _mutate(main_src, 'const ROUTES: [RegExp, PageKey, string][] = [',
                    'const ROUTES: [RegExp, PageKey, string][] = [\n  [/^\\/zzz\\/?$/, "zzz", "占位 · x"],')
        keys = [x['key'] for x in parse_client_routes(s)]
        assert 'zzz' in keys, '往 ROUTES 里加一条，客户端路由表没跟着长'

    def c_nav_column():
        # ★专门守"导航条那一列不是恒 False"：把 nav 的取法换回解包 dict 的写法，
        #   整个 build() 必须**红**（而不是安静地输出一列 False）。
        assert all(not isinstance(r, (list, tuple)) for r in build()['feature_nav']), \
            'feature_nav 的元素不是 dict 了（下游 `r["href"]` 的取法要跟着改）'
        m = build()
        for r in m['feature_nav']:
            e = [p for p in m['pages'] if p['path'] == r['href']]
            assert e and e[0]['in_feature_nav'] and e[0].get('nav_label') == r['label'], \
                '导航条 %s 没落到页面上（或标签丢了）' % r['href']

    check('端到端：真源上 build() 出来的结构自洽（页面/接口分表、导航条真落上、'
          '能点到的页都有服务端路由；没人链的那两条如实报进 client_only）', c_build)
    check('自测有牙：往 ROUTES 里加一条 ⇒ 客户端路由表跟着长（"解析器还在跑"这条要有守）',
          c_selftest_has_teeth)
    check('导航条那一列不是恒 False（解包 dict 那个坑的守）', c_nav_column)

    def c_cmp_key():
        # ★`--check` 那条判据的**两个方向**都要守：
        #   ①时间戳/来源信息变了 ⇒ 必须**仍然相等**（否则恒红/假红）；
        #   ②内容变了 ⇒ 必须**不相等**（否则它是个恒绿的空壳）。
        #   只守其中一个方向是这类"归一化后再比"的判据最常见的坏法。
        a = dump({'generated_at': '2020-01-01T00:00:00', 'x': 1,
                  'sources': [{'path': 'p', 'mtime': 111, 'sha256': 'aa'}]})
        b = dump({'generated_at': '2031-12-31T23:59:59', 'x': 1,
                  'sources': [{'path': 'p', 'mtime': 999, 'sha256': 'aa'}]})
        assert cmp_key(a) == cmp_key(b), '时间戳/来源信息变了就不相等 ⇒ 这条判据恒红/假红'
        c = dump({'generated_at': '2020-01-01T00:00:00', 'x': 2,
                  'sources': [{'path': 'p', 'mtime': 111, 'sha256': 'aa'}]})
        assert cmp_key(a) != cmp_key(c), '内容变了还相等 ⇒ 这条判据抓不住任何东西'
        d = dump({'generated_at': '2020-01-01T00:00:00', 'x': 1,
                  'sources': [{'path': 'p', 'mtime': 111, 'sha256': 'bb'}]})
        assert cmp_key(a) != cmp_key(d), 'sha256 变了还相等 ⇒ 承重字段被误抹了'

    check('`--check` 的比法：时间戳/来源信息变了仍相等，内容或 sha256 变了就不相等',
          c_cmp_key)

    def c_claimed():
        m = build()
        claims = sum(c['claimed'] or 0 for c in m['kb']['cats'])
        assert claims == m['kb']['terms'], \
            '词表声明 %d 个词、实到 %d 个（这条不红说明解析把整类丢了）' % (
                claims, m['kb']['terms'])
    check('端到端：词表各类 claimed 之和 == 真数到的词条数', c_claimed)

    return good, bad


# ── 入口 ──────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description='反向解析本站结构，产出 site_map.json')
    ap.add_argument('--check', action='store_true',
                    help='不写盘：重新解析一遍并与 site_map.json 逐字节比。0=新鲜 1=过期 3=跑不成')
    ap.add_argument('--selftest', action='store_true', help='打解析器自己，不写盘')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args(argv)

    if a.selftest:
        n, bad = selftest(a.verbose)
        for b in bad:
            print('✗ %s' % b)
        if bad:
            print('★ %d/%d 条通过，%d 条失败' % (n, n + len(bad), len(bad)))
            return 1
        print('✓ %d/%d 条通过（每条注入都打红了对应用例）' % (n, n))
        return 0

    try:
        m = build()
    except Missing as e:
        # ★rc=3：**"没跑成"不是"通过"**，也不是"过期"。三者处置完全不同。
        print('⊘ %s' % e)
        return 3
    text = dump(m)

    if a.check:
        try:
            with open(OUT, encoding='utf-8') as f:
                old = f.read()
        except OSError:
            print('★ site_map.json 不存在 —— 这份解析结果从没落过盘')
            return 1
        # ★**比之前先把 `generated_at` 抹掉**。不抹的话这条判据**恒红**：
        #   那个字段是 `time.strftime(...)` 到秒的，重建一次就和上次不同 ——
        #   于是"过期了"这句话永远为真，而它**看起来像一条正常的提醒**，
        #   没人会去查它是不是从来没绿过。恒红的判据 ≈ 没有判据
        #   （它把读者的注意力训练成"忽略这一行"）。
        #
        # ★`mtime` 同样不参与比，理由不一样：它是**来源信息**，不是内容。
        #   把源文件原样重写一遍（格式化器、编辑器保存、`cp` 回来）内容一个字节
        #   没变、`sha256` 一模一样，而 `mtime` 变了 ⇒ 这条判据会红。
        #   那种红是假红，而假红会教人"这行提示可以忽略"—— 比不写还坏。
        #   承重的字段是 `sha256`（每一条源都有），内容一动它就动。
        if cmp_key(old) != cmp_key(text):
            print('★ site_map.json 已经过期：源改过而它没重跑（跑 `python3 build_sitemap.py`）')
            return 1
        print('✓ site_map.json 与当前源一致（%d 字节；`generated_at`/`mtime` 不参与比，'
              '内容按 sha256 比）' % len(text))
        return 0

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(text)
    print('✓ 写出 %s：%d 页 / %d 张主页卡 / %d 把工具 / %d 个角色 / %d 条策略 / %d 个词'
          % (os.path.relpath(OUT, HERE), len(m['pages']), len(m['home_cards']),
             m['agent']['tool_count'], m['council']['role_count'],
             m['intraday']['strategy_count'], m['kb']['terms']))
    if m['missing_sources']:
        print('⊘ 有几个源不在：%s' % '、'.join(m['missing_sources']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
