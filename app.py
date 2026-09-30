#!/usr/bin/env python3
"""本地站点：主页面（智能体） + 辅页面（知识图谱） + 新手引导。

★静态文件走**白名单**，不是把仓库根目录整个挂出去。这不是洁癖：
本目录下有 `.env`（里面是 API key）、`models/`（几百 MB 权重）、`.venv/`。
用 `Flask(__name__, static_folder=ROOT)` 一行就能跑起来，但那样 `/.env`
就是一个任何人都能下载的 URL。宁可多写十行列个名单。
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'agent'))

# ★apikey 必须在 load_env 之前 import：它在模块级快照"进程启动时的环境变量"，
#   用来判断 shell 里那把 key 有没有被 .env 盖住（见 apikey.py 的 _INHERITED）。
#   先 load_env 再 import 的话，快照到的已经是 .env 里那把，那句提醒就永远不出现。
import apikey                                                              # noqa: E402

apikey.load_env()

from flask import Flask, Response, jsonify, request, send_from_directory   # noqa: E402

import accuracy                                                            # noqa: E402
import checkup                                                             # noqa: E402
import game                                                                # noqa: E402
import graph_kb                                                            # noqa: E402
import guide                                                               # noqa: E402
import ledger                                                              # noqa: E402
import llm                                                                 # noqa: E402
import news                                                                # noqa: E402
import siteguide                                                           # noqa: E402
import tools                                                               # noqa: E402
import uicopy                                                              # noqa: E402
# ★`uicopy.humanize` 只用在**发给浏览器的 JSON 出口**上：把写给智能体的散文
#   （`**星号**` / `skill 的档位` / `cn_etf` / 纯指令 hint）换成人话。
#   `/api/chat` **不过**它 —— 对话正文的 markdown 加粗是故意给前端的。
from runner import ENGINE                                                  # noqa: E402

app = Flask(__name__)

# 只放这些出去。不在名单里的一律 404 —— 包括 .env、models/、.venv/。
# ★`index.html` 已经**不在这里**了：它现在是 SPA 的壳，从 web/dist 发（见 SPA_PAGES）。
#   根目录那个 index.html（旧页面）改从 `/legacy` 发 —— 文件没动、也没删，
#   只是换了条路由，迁移期里两边都能开。
# ★`site_map.json` 进白名单的理由和 `graph_data.json` 一样，不是图省事：
#   引导智能体讲的每一句"站上有几页 / 几个角色 / 多少词条"都出自这份文件，
#   那就得让用户**自己能把它下载下来看**。一份没人能核的介绍，和一句
#   无法证伪的免责声明是同一种东西。
WHITELIST = {'guide.html', 'graph.html', 'graph_data.json', 'site_map.json',
             'favicon.ico'}

# ★新前端（React + Tremor）的构建产物。**故意不和 WHITELIST 混在一起**：
#   WHITELIST 里的名字是从 HERE（仓库根）发的，而这两个只能从 web/dist 发。
#   混成一个集合，路由就得回头判断"这个名字该从哪个目录发"，那是把两件事塞进一个名字。
#
#   ★名字必须是**扁平**的（没有 `/`）：dist 里出现 `assets/xxx.js` 这类带目录的名字时，
#   下面这条路由一律 404 —— 这是故意的，因为 verify_agent.py 里那条结构性判据
#   （读白名单本身、与文件在不在无关）把带 `/` 的条目一律判为可疑形状。
#   ⇒ 阶段 5 要给 SPA_ASSETS 补上**同一条**判据（同样禁止 `/`、`.py`、`.sh`、`env`、`model`），
#   否则这里就成了那条判据旁边的一个洞。
SPA_ASSETS = {'app.js', 'app.css'}

# ★**从 web/dist 发的页面**，和 SPA_ASSETS 一样只放扁平名字，理由同上。
#   `index.html` 从 WHITELIST 搬到了这里：它现在是 SPA 的壳，不再是根目录那个旧页面。
SPA_PAGES = {'index.html'}

# 新前端在追平功能之前挂在 /next 上。
# ★`/` 已经翻过来了（2026-09-18：渲染层判据在新页面上全绿、旧页面的 17 条注入
#   全部重锚完毕之后才翻的）。`/next` 这条路由**留着不删** —— 它是旧链接和
#   书签的落点，删掉等于让已经开着的页面 404；留着零成本。
SPA_ROUTE = 'next'
DIST = os.path.join(HERE, 'web', 'dist')


def page(name, base=None):
    """发一个 HTML 页，**顺手把回测数字填进占位符**。

    ★两个入口（`/` 和 `/index.html`）必须走**同一个**函数。分开写的话，
    一边填了、另一边没填，而"从哪个门进来"用户是看不出来的 ——
    这正是那种"两条路走出两个页面"的坏法，判据也专门测这条（两个门都要有数字）。
    ★`no-store`：页面内容取决于 `kronos_accuracy.json`，浏览器缓存一份旧的
    就等于把"数字会跟着回测变"这件事废掉了。

    ★新前端的壳子也走**这一个**函数（base=web/dist）。这不是复用癖：SPA 壳里那个
    `<div id="acc-boot" hidden>` 同样装着 `<!--ACCURACY-->`，数字仍然只有服务端
    现读现填这一个出口。React 只负责把它搬进可见节点。
    """
    with open(os.path.join(base or HERE, name), encoding='utf-8') as f:
        html = accuracy.fill(f.read())
    return Response(html, mimetype='text/html',
                    headers={'Cache-Control': 'no-store'})


@app.route('/')
def home():
    """主页面 = 新前端（React + Tremor）。走的还是 page()，数字还是服务端现读现填。"""
    return page('index.html', base=DIST)


@app.route('/' + SPA_ROUTE)
def spa_shell():
    """旧链接 `/next` 留在原地，和 `/` 发的是同一份东西。"""
    return page('index.html', base=DIST)


@app.route('/dash')
def dash():
    """行情总览。发的还是**同一份** SPA 壳，路由在客户端（见 web/src/main.tsx）。

    ★发同一份壳不是图省事：壳里那个 `<div id="acc-boot" hidden>` 是**数字的唯一出口**，
      服务端每次请求现读 `kronos_accuracy.json` 填进去。另发一份 HTML 就等于
      另开一个出口，两页迟早会说两套话。
    ★这条路由**不进白名单**：`/dash` 不是磁盘上的文件，是路由。
      白名单管的只有"哪些文件可以按名字取走"，两件事别混。
    """
    return page('index.html', base=DIST)


# ── SPA 的其余四条页面路由（主页变成导航层之后才有的）────────────────────
#
# ★四条**都发同一份壳**，理由和 /dash 一样：数字的唯一出口在壳里。
#   路由是客户端的事（web/src/main.tsx 那张 ROUTES 表）。
#
# ★它们**一律不进任何白名单**。白名单管的是"哪些**文件**可以按名字取走"，
#   而这几条是**路由**，磁盘上没有叫 `agent` 的文件。混进去的第一个后果是
#   verify_agent 那条结构性判据（读名单本身、禁止带 `/` 的名字）当场红；
#   更根本的是，把路由名塞进文件名单，等于让"以后有人往这加个真文件"变得
#   看不出来 —— 那正是白名单这个机制存在的意义。
#
# ★**服务端真有的路由**，不是靠 JS 骗出来的：直接粘链接、刷新、被搜索引擎抓，
#   三种情况都要落在同一页上。前端漏一条路由的话，用户看到的是主页
#   （`currentPage()` 的默认值），而不是一片空白 —— 但那是兜底，不是设计。
@app.route('/agent')
def agent_page():
    """智能体页。★搬迁之前 `/` 就是这么一页，内容一字未改，只是换了个地址。"""
    return page('index.html', base=DIST)


@app.route('/siteguide')
def siteguide_page():
    """引导智能体。★条目、卡片、提示词块**全部**来自 `site_map.json` ——
    那份 JSON 是 `build_sitemap.py` 反向解析本站源码现算的，这里一个字都不手写。

    ★这条路由**必须**是真路由：主页第一张卡指向它，而"能从主页点到的页
      都要有服务端路由"是本站的纪律（判据在 `build_sitemap.py` 的端到端那条、
      以及 `verify_agent.py` 的 HOME_LINKS 表）。少了它，用户点第一张卡
      会跳到主页 —— 而且看起来像"点错了"，不像"少写了一条路由"。
    """
    return page('index.html', base=DIST)


@app.route('/onboarding')
def onboarding_page():
    """新手引导（交互式分步）。"""
    return page('index.html', base=DIST)


@app.route('/profile')
def profile_page():
    """投资画像表单。★服务端**不存**任何画像 —— 它住在浏览器的 localStorage 里。"""
    return page('index.html', base=DIST)


@app.route('/portfolio')
def portfolio_page():
    """组合体检。发的还是**同一份** SPA 壳，路由在客户端（见 web/src/main.tsx）。

    ★和 /dash 一样，这条路由**不进白名单**：`/portfolio` 是路由，不是磁盘上的文件。
    """
    return page('index.html', base=DIST)


@app.route('/ledger')
def ledger_page():
    """预测台账。同上一份壳，路由在客户端。

    ★这是本站唯一一处"记着过去发生过什么"的页面，所以它**必须自己说清楚
      记在哪儿**（页面顶部露路径）—— 用户有资格知道这个站往磁盘上写了什么。
    """
    return page('index.html', base=DIST)


@app.route('/game')
def game_page():
    """模拟投资游戏。★行情是真抓的；抓不到就明确报错，绝不编数字。"""
    return page('index.html', base=DIST)


@app.route('/research')
def research_page():
    """行情特征研判。**从 `/game` 点进来**，是那张"K线预测"卡的归位。

    ★这一页与游戏的纪律是同一条，所以它才挂在这儿：**只描述，不预测**
      （`agent/game.py:39`）。材料只到"买入日"那天为止 —— 涨跌由玩家自己判断，
      再由 `game.quote` 揭晓"那之后真实发生了什么"。系统侧不出现任何未来读数。
    """
    return page('index.html', base=DIST)


# ── 第二批页面路由（多智能体 / 分钟策略 / 关联检测 / 采集 / 心理）─────────
#
# ★五条与上面七条**完全同构**：同一份 SPA 壳、同一个数字出口、路由在客户端。
#   单独写一段注释而不是混进上面那批，是因为它们的**上线时间不同** ——
#   上面那批的页面已经跑通了，这五条对应的后端还在施工。
#   一页"壳在、后端不在"时，页面必须能说出"这条通道还没接上"，
#   而不是显示成"这个功能做坏了"（那句话的判据在 `web/src/api2.ts` 的 FailKind）。
@app.route('/agents')
def agents_page():
    """多智能体博弈页。"""
    return page('index.html', base=DIST)


@app.route('/intraday')
def intraday_page():
    """分钟级策略页（5/15/30）。"""
    return page('index.html', base=DIST)


@app.route('/linkage')
def linkage_page():
    """关联检测页。**重采集、弱分析**。"""
    return page('index.html', base=DIST)


@app.route('/collect')
def collect_page():
    """常驻采集器监视页。"""
    return page('index.html', base=DIST)


@app.route('/psych')
def psych_page():
    """投资心理页。"""
    return page('index.html', base=DIST)


# ── 风险测评页（与上一条**不同构**，所以单独一段）───────────────────────
#
# ★它和上面那五条长得一样（同一份 SPA 壳），但**性质不同**：那五条各自在等一个
#   后端通道上线，这一页**没有、也不会有**后端 —— 十道题的答案住在浏览器的
#   `localStorage`（`licai-assess`），和画像（`licai-profile`）同一个待遇。
#
#   这不是"还没做服务端存储"，是**不能**：本站服务端零用户状态那三条硬边界
#   （不存身份 / 不存用户写的内容 / 落盘一律 append-only）被测评结果同时撞上 ——
#   它是有身份的用户内容，还要能覆盖更新。存下来等于撕一个性质比预测台账更差的口子。
@app.route('/assess')
def assess_page():
    """投资者风险测评页。★结果只住在浏览器里，服务端不存任何一份。"""
    return page('index.html', base=DIST)


@app.route('/inspect')
def inspect_page():
    """建议体检。贴一段别人给你的理财建议，它告诉你哪里不对劲、该问什么。

    ★这一页和 `/ledger` 是**同一套方法的两个对象**，不是两套逻辑：
      `/ledger` 检验的是**本站自己的**预测（命中率永远和无脑喊涨并排摆着），
      这一页检验的是**别人给的**建议。两处都配对照基准、都明说"我判不了哪一部分"。
      判定都走规则不走大模型 —— 理由也同源：模型的输出**钉不住**，而这一页
      存在的全部意义就是它的结论能被钉住（`agent/checkup.py` 抬头写了三条）。
    """
    return page('index.html', base=DIST)


@app.route('/legacy')
def legacy():
    """换栈之前的那个单页（根目录 index.html）。**文件没动、也没删。**

    ★留着它的理由不是怀旧：新页面是一整套构建产物，万一构建坏了、或者某条判据
    在新页面上说不清是"页面坏了"还是"判据过时了"，`/legacy` 是那个**参照物** ——
    同一台服务器、同一个数字出口，两边一比就知道问题出在哪一层。

    ★★但"参照物"**必须带轴**：它只是**在判据量过的那些轴上**已知为真，不担保
    没量过的轴。这话不是抠字眼 —— 2026-09-21 的 AA 那一轮里，这个页面在一整条
    轴上其实是**假的**（`--ink-3` 正文对比度、以及白字压在 accent 上只有 2.08:1），
    而它当时就已经挂着"已知为真"四个字。判据的覆盖面**是会变的**：同一份文件在
    判据扩到三张页面之前为"真"、之后为"假"，文件一个字节没动。
    ⇒ 用它做对照时，**先确认要比的那条轴真的在判据覆盖面里**（`verify_page.py`
    的 `[4]` 就是覆盖面本身），否则你比的不是"新页面坏没坏"，是"两个页面都坏
    但坏得一样"。
    """
    return page('index.html')


@app.route('/<path:name>')
def static_file(name):
    # 新前端的构建产物：名字**必须**落在 SPA_ASSETS / SPA_PAGES 里，其余一律 404。
    # 注意这里没有"按扩展名放行"，也没有 "/" 通路 —— dist 里将来若出现
    # assets/ 子目录，必须在 SPA_ASSETS 里显式列名并保持扁平。
    if name in SPA_ASSETS:
        return send_from_directory(DIST, name)
    if name in SPA_PAGES:
        return page(name, base=DIST)
    if name not in WHITELIST:
        return jsonify({'error': 'not found'}), 404
    if name.endswith('.html'):
        return page(name)
    return send_from_directory(HERE, name)


@app.route('/api/accuracy')
def accuracy_api():
    """回测数字的机器可读出口（页面是服务端填的，这个给外部/判据用）。"""
    n = accuracy.numbers()
    if not n:
        return jsonify({'ok': False, 'reason': accuracy.FALLBACK_MARK,
                        'source': os.path.basename(accuracy.JSON_PATH)}), 404
    return jsonify({'ok': True, 'source': os.path.basename(accuracy.JSON_PATH), **n})


@app.route('/api/status')
def status():
    """状态徽章用：模型加载了没、key 配了没、词表多大。"""
    try:
        kb_stats = graph_kb.kb().stats()
    except Exception as e:                       # noqa: BLE001
        kb_stats = {'error': str(e)}
    return jsonify({'engine': ENGINE.status(),
                    'llm': {'configured': llm.configured(), 'model': llm.config()['model']},
                    # ★key 的状态也从这儿发一份：页头那枚徽章走 /api/status、
                    #   侧栏那张卡走 /api/key，两个口说的必须是同一件事。
                    #   有判据钉着（configured 两边不一致就红）。
                    'key': apikey.state(),
                    'kb': kb_stats,
                    # 资讯采集能力探测（akshare 装没装 / crawl4ai / MediaCrawler+cookies）。
                    # 只报事实、结果在 news.available() 里缓存 5 分钟 —— 探测要摸
                    # 文件系统甚至起 subprocess，不能每次 /api/status 都现摸。
                    'news': news.available(),
                    'families': graph_kb.kb().by_family() if 'error' not in kb_stats else []})


@app.route('/api/key', methods=['GET', 'POST', 'DELETE'])
def api_key():
    """网页上填 key 的那条路 —— 写完立刻生效，不用重启，也不用去编辑 .env。

    ★三条纪律，每条都配了判据：

    1. **响应里永远只有掩码。** 完整 key 一个字符都不许出现。判据拿一把哨兵 key
       把 `/`、`/api/status`、`/api/key` 全扫一遍去找它 —— 只断言"有个 mask 字段"
       是不够的，那拦不住"顺手把原文也带上"。

    2. **只认 JSON 体。** 浏览器里**任意**网页都能往 127.0.0.1 发一个
       `<form method=post>` —— 表单 POST 属于"简单请求"，不触发预检，服务端拦不到，
       CORS 也管不着（CORS 管的是读响应，不是发）。挡它的其实是
       `request.get_json(silent=True)`：非 JSON 的 Content-Type 直接返回 None，
       于是 body 是空 dict，落到"key 不能为空"那一支，改不动任何东西。
       ★所以**别顺手加 `or request.form.to_dict()`** —— 那等于给任意网页开了一个
       改你 key 的入口。有一条判据专门钉这件事（表单体 + 非空值，必须被挡）。
       DELETE 不走这条路，但浏览器本来就发不出跨站 DELETE（表单只有 GET/POST，
       fetch 发 DELETE 要预检），所以它天然安全。

    3. 改完**就地生效**（见 apikey.save）。只写文件不更新 os.environ 的话，
       症状是"保存成功、但这次对话还是说没配 key，重启一下就好"。
    """
    if request.method == 'GET':
        return jsonify(apikey.state())
    body = request.get_json(silent=True) or {}
    try:
        if request.method == 'POST':
            apikey.save(body.get('key'))
        else:
            apikey.clear()
    except ValueError as e:
        return jsonify({'error': str(e), **apikey.state()}), 400
    return jsonify(apikey.state())


@app.route('/api/chat', methods=['POST'])
def chat():
    body = request.get_json(silent=True) or {}
    q = (body.get('question') or '').strip()
    if not q:
        return jsonify({'error': '没问题内容'}), 400
    # ★画像是**这一轮的参数**，不是服务端状态：它随每次请求从浏览器的
    #   `localStorage` 带上来，拼进这一轮的系统提示词，这一轮结束就没了。
    #   `body.get('profile')` 是**客户端送来的**，一律当不可信输入 ——
    #   清洗在 `llm.profile_block()` 里（去换行、截长度、只认白名单字段），
    #   不在这里顺手做一半：两处各清一半，两边都会以为对方清干净了。
    msgs = llm.build_messages(q, body.get('history'), profile=body.get('profile'))
    # ★持仓同理，是**这一轮的参数**。它不拼进提示词，而是交给 `analyze_portfolio`
    #   当请求上下文用 —— 好让工具拿到用户**真实**的持仓，而不是让大模型把它抄进
    #   工具参数（抄漏一只 = 一份看着合理、整个错的报告，见 tools.py 里那段）。
    #   清洗也在 `tools.clean_holdings()` 里，不在这里做一半。
    # ★不能在这里 set/reset：`sse_stream` 是**另一个线程**在跑 run_turn，
    #   而 ContextVar 是**每线程一份**的 —— 在这里设，工具那边根本看不见。
    #   所以设和复位都在 worker 线程里做（见 llm.sse_stream）。
    # ★画像**一路两用**：既拼进这一轮的提示词（上面那行），也当**请求上下文**
    #   交给 `plan_goal` —— 目标达成概率要月投入/期限/资产三档，那三档是用户在
    #   画像里选的**区间**，让大模型抄进工具参数就等于让它替用户取中点。
    #   同一份 `body['profile']` 喂两处，但**各清各的**：提示词那份要能塞文本、
    #   工具这份只要那三个档位键（`tools.clean_profile`），所以不共用清洗函数。
    # ★同样不能在这里 set/reset（线程），见下面。
    return Response(llm.sse_stream(msgs,
                                   holdings=tools.clean_holdings(body.get('holdings')),
                                   profile=tools.clean_profile(body.get('profile'))),
                    mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',               # 有反代时别攒着，攒了心跳就白发了
        'Connection': 'keep-alive',
    })


@app.route('/api/siteguide', methods=['GET'])
def api_siteguide():
    """引导智能体选项卡的内容 + 这份结构此刻还准不准。

    ★`freshness` 是**每次请求现算**的（把 JSON 里存的 `sha256` 跟磁盘现读的重算值
      逐个比），不是读 `generated_at`。源码改过而没重跑 `build_sitemap.py` 时，
      `generated_at` 照样是"刚刚"，只有 sha 比得出来。
    ★它跟内容一起发出去，前端据此在页面上挂一条"这份目录可能过时"的提示 ——
      **不静默降级**：智能体的介绍跟着源码漂了，用户得看得见。
    """
    secs = siteguide.sections()
    fw = siteguide.freshness()
    avail = siteguide.available()
    # ★`ok` 取 **`available()`**，不取 `bool(secs)` —— 这两件事必须分开，
    #   因为它们指向两句不同的人话：
    #     · JSON 读不到（`available()` False）⇒ **这条通道取不到**，
    #       前端显示 `Degraded`（"后端这条通道还没接上"）；
    #     · JSON 读到了、但一张卡都拼不出来（`sections()` 空）⇒
    #       **通道通着，是内容空**，前端显示 `Empty`。
    #   取 `bool(secs)` 的话第二种会被说成"通道没接上" ——
    #   那是把"我们读到了但没拼出东西"这句话，栽给一个根本没坏的后端。
    #   （`api2.call` 的 `fromBody()` 把 `ok:false` 直接判成 `kind:'server'`。）
    return jsonify({
        'ok': avail,
        'sections': secs,
        'freshness': fw,
        'stale_hint': ('' if fw['ok'] else
                       ('这份站点目录与现在的页面已经不一致（有 %d 处改动还没收进目录）'
                        ' —— 下面的话可能过时。' % len(fw['stale'])
                        if fw['stale'] else
                        '这份站点目录读不到，下面的话可能不是最新的。')),
        # ★"这份知识在不在"这件事本身要发出去：拿不到时前端该说
        #   "目录读不到"，而不是渲染一堆空卡（空卡看着像"这个站没有内容"）。
        'available': avail,
        # ★`ok:false` 时前端那句原因**优先用服务端给的这个**（`fromBody`）。
        #   不给的话它会回落到"服务端说这条取不到" —— 那句话没告诉用户
        #   到底缺的是哪个文件。
        'msg': ('' if avail else
                '站点目录读不到 —— 重新生成一次目录就好了。'),
    })


@app.route('/api/siteguide/chat', methods=['POST'])
def api_siteguide_chat():
    """站内引导的对话 —— 由**导览团**答，不是一个智能体一个人答完。

    ★这一条路由的引擎换成了 `guide`（`agent/guide/`），但**出口协议没变**：
      正文仍然以 `token` 事件出去，所以前端那条气泡一个字都不用改；
      多出来的是 `guide_*` 那一族事件（谁在答、答了什么、多久、校对位查到什么），
      前端拿它们铺"导览团"那一栏。**未知事件旧前端会静默忽略**
      （`chat.ts` 的 `applyEvent` 落到最后一行原样返回），所以这是加法不是改协议。

    ★为什么不是"`/api/chat` 多带一块 `extra=prompt_block()`"（上一版就是这么写的）：
      那样写，"只答目录里有的"是一句**嘱咐**，模型可以不照做，而且不照做的时候
      看不出来。换成导览团之后它是**结构** —— 每位导览员物理上只拿到自己那块
      （`guide/slices.py`），主持手上一个数都没有，成稿里的数字由一个非 LLM 的
      校对位（`guide/audit.py`）拿去目录里查出处。理由写在 `guide/__init__.py` 顶上。

    ★`profile` 照旧不带：这一页回答的是"这个站有什么"，
      个性化画像在这里只是噪声，还会让用户以为它在拿他的资产说事。
    """
    body = request.get_json(silent=True) or {}
    q = (body.get('question') or '').strip()
    if not q:
        return jsonify({'error': '没问题内容'}), 400
    return Response(guide.sse_stream(q, history=body.get('history')),
                    mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
    })


@app.route('/api/predict', methods=['POST'])
def predict():
    """绕开大模型直调模型链路。验证脚本靠它打真靶子。"""
    b = request.get_json(silent=True) or {}
    r = tools.call('predict_kline', {'symbol': b.get('symbol'),
                                     'pred_len': b.get('pred_len'),
                                     'sample_count': b.get('sample_count')})
    code = 200 if r['summary'].get('ok') else 400
    return jsonify(uicopy.humanize(r)), code


@app.route('/api/kline', methods=['POST'])
def kline():
    b = request.get_json(silent=True) or {}
    r = tools.call('get_kline', {'symbol': b.get('symbol'), 'days': b.get('days')})
    return jsonify(uicopy.humanize(r)), (200 if r['summary'].get('ok') else 400)


@app.route('/api/news', methods=['POST'])
def news_api():
    """资讯采集：绕开大模型直调 get_news。页面资讯卡与判据都走这里。

    ★形状照抄 /api/kline：纯函数、失败 **400**（"200 但里面 ok:false"
      在只看状态码的地方看着一切正常）。
    ★symbol 与 query 至少给一个由**工具**把关（BAD_ARGS），这里不重复校验 ——
      两处各校验一半，两边都会以为对方校验过了。
    """
    b = request.get_json(silent=True) or {}
    r = tools.call('get_news', {'symbol': b.get('symbol'), 'query': b.get('query'),
                                'limit': b.get('limit')})
    return jsonify(uicopy.humanize(r)), (200 if r['summary'].get('ok') else 400)


@app.route('/api/portfolio', methods=['POST'])
def portfolio_api():
    """组合体检：绕开大模型直调。页面靠它，探针也靠它打真靶子。

    ★持仓是**这一轮的参数**：页面从 `localStorage`（游戏页买的那些）归一化成
      权重传上来，服务端不留。所以这里没有任何"当前用户"的概念，
      和 `/api/kline` 一样是纯函数。
    ★失败是 **400**：状态码本身是一条判据的落点（"200 但里面写着 ok:false"
      在只看状态码的地方看着一切正常），而这一页的纪律是**算不了就说算不了**。
    """
    b = request.get_json(silent=True) or {}
    r = tools.call('analyze_portfolio', {'holdings': tools.clean_holdings(b.get('holdings')),
                                         'risk': b.get('risk')})
    return jsonify(uicopy.humanize(r)), (200 if r['summary'].get('ok') else 400)


@app.route('/api/inspect', methods=['POST'])
def inspect_api():
    """建议体检：绕开大模型直调规则引擎。

    ★**不过 `uicopy.humanize`**，和 `/api/portfolio` 不同 —— 那个换的是"写给智能体
      的散文"（`cn_etf`、纯指令 hint、`skill 的档位`），而这一页的每个字都是我为了
      **给人看**手写的，套一层只会把措辞改坏。
    ★**不落盘**。这是全站唯一一个"用户会往里贴一整段**自己写的**文本"的接口 ——
      比 `/assess` 更进一步（那份至少还只是选项）。服务端零用户状态那三条边界
      （不存身份 / 不存用户写的内容 / 落盘一律 append-only）它一条都不该撞，
      所以 `checkup.inspect()` 是个纯函数，调完即弃。
    ★失败是 **400**：理由同 `/api/portfolio` —— "200 但里面写着 ok:false"
      在只看状态码的地方看着一切正常。
    """
    b = request.get_json(silent=True) or {}
    r = checkup.inspect(b.get('text'))
    return jsonify(r), (200 if r.get('ok') else 400)


# ── 预测台账 ─────────────────────────────────────────────────────────
# ★这是本站唯一一处「记着过去」的服务端状态。两条边界写在 `agent/ledger.py` 头上：
#   只记模型输出（一个字的用户输入都没有）、只追加（没有删除接口）。
@app.route('/api/ledger', methods=['GET', 'POST'])
def ledger_api():
    """台账：先惰性回填到期的，再把分区汇总发出去。

    ★**回填放在这里**（而不是只在启动时跑一次）：站可能几天不重启，
      而"到期"是随时间发生的。请求时顺手补一次，页面上的战绩就会自己往前走。
      启动时那一次（见 `_boot_backfill`）是为了让第一屏就有东西，两者都要。

    ★汇总**按 source 分区**（live / backfill），前端也分区渲染。
      合成一个数的话，"台账战绩"几乎全部由回填的离线窗口决定，
      而它看上去像"这个站在线跑出来的成绩"。

    ★`refresh=0` 可以跳过回填（探针量几何时要的是快和确定，不是新）。
    ★**缺省是不回填**。一个裸 GET 不该干重活：`check_links` 那种"挨个路由打一下"
      的判据会因此变慢，而且**顺带改了状态**（回填会往台账里追加记录）。
      "读一个接口"和"让它去联网取数并写盘"是两件事，不该由同一个缺省值决定。
      用户那条路（页面上的 `loadLedger(true)`）显式带 `refresh: 1`。
    """
    body = request.get_json(silent=True) or {}
    raw = body.get('refresh', request.args.get('refresh', '0'))
    do_bf = str(raw) == '1'
    st = ledger.state()
    bf = None
    if do_bf:
        # ★只在**真有到期的**时候才去取行情：一个空台账不该让这个接口去联网。
        bf = ledger.backfill(st) if ledger.pending(st) else None
        if bf and bf['scored']:
            st = ledger.state()          # 打完分重读一次，页面看到的就是刚补上的
    summ = ledger.health()
    # ★服务器的绝对路径不出 HTTP 边界：界面上显示过 `data/predictions.jsonl`，
    #   对用户是天书，对外是一次不必要的信息暴露。隔离判据读的是进程内
    #   `ledger.path()`，不读这个接口，所以拿掉不影响任何判据。
    summ.pop('path', None)
    # ★★下面这一行的**形状是被判据钉住的**：`verify_agent.py` 里台账那组有
    #   **五条注入**（丢空分区 / 合成一块 / 写死命中率 / 基线抄成命中率 /
    #   没校准就给覆盖率）的锚点都是那一整行字面量
    #   `'backfill': bf, 'blocks': ledger.blocks(st['entries'])})`。
    #   我第一版把它拆成了两行 ⇒ 五条锚点一起漂掉（`check_fault_anchors` 当场数出 6 条）。
    #   ⇒ 纪律：**别把这一行折行**。要加东西就在它上面加。
    return jsonify(uicopy.humanize({'ok': True, 'summary': summ,
                    'backfill': bf, 'blocks': ledger.blocks(st['entries'])}))


@app.route('/api/game/quote', methods=['POST'])
def game_quote():
    """模拟投资：按真实历史行情算一次「如果那天买了，现在赚多少」。

    ★失败时是 **400**，不只是 `summary.ok = false`。状态码本身也是一条判据的落点：
      「200 但里面写着 ok:false」在只看状态码的地方看起来一切正常，
      而这一页的硬纪律是**抓不到行情就明确报错、绝不编数字**。
    ★`buy_date` 不传就是今天（"今天按真价建仓"那条路）。
    """
    b = request.get_json(silent=True) or {}
    r = game.quote(b.get('symbol'), b.get('amount'), b.get('buy_date'))
    return jsonify(uicopy.humanize(r)), (200 if r['summary'].get('ok') else 400)


@app.route('/api/game/recommend', methods=['POST'])
def game_recommend():
    """按投资兴趣给一份**建议**列表 —— 只推荐，不限制。

    ★返回的是一份"不知道从哪下手时点一下"的清单，不是白名单：
      页面上的输入框照样能搜任意标的（判据里专门有一条钉这个）。
    """
    b = request.get_json(silent=True) or {}
    r = game.recommend(b.get('interests'))
    return jsonify(uicopy.humanize(r)), (200 if r['summary'].get('ok') else 400)


@app.route('/api/warmup', methods=['POST'])
def warmup():
    """后台预热模型，把第一次的加载成本挪到用户提问之前。"""
    import threading
    if ENGINE.status()['loaded']:
        return jsonify({'ok': True, 'already': True})
    threading.Thread(target=ENGINE.warmup, daemon=True).start()
    return jsonify({'ok': True, 'started': True})


@app.route('/api/game/research', methods=['POST'])
def game_research():
    """行情特征研判：给**截至买入日**的量价材料，一个字的未来都不给。

    ★与同前缀的 `/api/game/quote`、`/api/game/recommend` **形状不同**，是故意的：
      那两条返回的是**游戏的** dict（`{summary, data}`，`summary.ok` 里带中文措辞，
      失败键只有 {ok, error, msg, hint}），这一条返回的是**量价引擎的** dict
      （顶层 `ok`/`regime`/`signals`/`composite`/`errors`，与 `/api/intraday` 同形）。
      理由：它的内容是研判材料，不是一笔交易的盈亏 —— 硬塞进游戏的壳里只会让两边
      都要多一层翻译。所以下面用 `_flat()`（照 `/api/intraday`），不是 `jsonify` +
      `summary`。⇒ 下一个人别把它当"漏了包装"。
    ★失败一律 **400**（`_flat` 就是按 `ok` 定的）：只把 `ok:false` 放进 200 的话，
      在只看状态码的地方看着一切正常，而这一页的硬纪律是**取不到就明确报错**。
    ★`as_of` 只在日线口径那条路上有实现，而且**不许静默忽略**（那会产出一份
      含未来、却自称历史的研判）—— 见 `daily_short.analyze_auto` 那段。
    ★`buy_date` 不传就是"到今天为止"（= 玩法里的"今天建仓"）：那时最后一根 K
      可能还没收盘，`analyze` 会在 `errors` 里如实标出来，这里不替它粉饰。
    """
    b = request.get_json(silent=True) or {}
    code = (b.get('symbol') or b.get('code') or '').strip()
    if not code:
        return _bad('要给我标的代码（symbol）')
    try:
        market, code, name = _need_sym(code, b.get('market'), b.get('name'))
    except tools.ToolError as e:
        return _bad(str(e), e.code)
    try:
        from daily_short import analyze as _analyze
    except Exception as e:                                        # noqa: BLE001
        return _bad('日线口径那条路没接上：%s: %s' % (type(e).__name__, e), 'RESEARCH_FAIL')
    try:
        res = _analyze(market, code, name=name,
                       as_of=(b.get('buy_date') or b.get('as_of') or None))
    except Exception as e:                                        # noqa: BLE001
        return _bad('研判没跑成：%s: %s' % (type(e).__name__, str(e)[:180]), 'RESEARCH_FAIL')
    # 与 `/api/intraday` 同一处补法：`api2.ts` 的约定里 `synthetic` 与 `data_source`
    #   两个键前端都读，而包本身只给后者 —— 判法从 `tools.is_synthetic` 取，
    #   同一个判断只许有一处定义（理由见那个函数）。
    if isinstance(res, dict):
        res.setdefault('synthetic', tools.is_synthetic(res.get('data_source')))
    return _flat(res, market=market, code=code, name=name)


# ── 渲染层探针通道 ────────────────────────────────────────────────────
# 为什么要这条通道：无头 firefox 拿不到页面里的 JS 结果 ——
#   * `--dump-dom` 在 Firefox 155 上直接挂死（实测：一个最简本地文件也超时，只出 72 字节）；
#   * `--screenshot` 只给一张 PNG，而**像素读不出 getBoundingClientRect**；
#   * 仓库里 verify_page.py 那条路要 pillow，本机没装，它那一层实际上是被跳过的。
# 所以让页面把量好的几何 POST 回它自己来的那个服务端。★只在 LICAI_PROBE=1 时开，
# 否则这就是一个任何人都能写的公开端点。
_PROBE = {'data': None}


def _boot_backfill():
    """启动时补一次到期的台账（放在后台线程里，**不挡服务起来**）。

    ★为什么要启动时也补：站可能开着一整天没人点预测，而"到期"是**随时间**发生的 ——
      只在请求时补的话，用户点开台账页那一刻才会发现要等几十秒取数。
    ★为什么必须放线程里：回填要联网取行情（每只 1–3 秒）。挡在 `app.run` 前面的话，
      启动横幅之后要黑屏十几秒，而页面上完全没有迹象说明在等什么。
    ★`LICAI_LEDGER_NOBOOT=1` 可以整个关掉 —— **验证脚本起的测试服务必须关掉它**：
      一个后台线程在写台账，跟前台正在数条数的判据是**同一个文件的读写竞争**，
      而那类失败是"偶尔红一次"，最难查。测试服务要的是确定，不是新。
    """
    if os.environ.get('LICAI_LEDGER_NOBOOT'):
        return
    import threading

    def work():
        try:
            st = ledger.state()
            if not ledger.pending(st):
                return
            rep = ledger.backfill(st)
            if rep['scored'] or rep['no_data'] or rep['out_of_window']:
                print('台账回填：补了 %d 条（没到期 %d / 取不到 %d / 窗口外 %d）'
                      % (rep['scored'], rep['not_mature'], rep['no_data'],
                         rep['out_of_window']))
        except Exception as e:                                    # noqa: BLE001
            # 回填失败**不许把站弄挂**，但也不许静默 —— 说一句，然后照常服务。
            print('台账回填没跑成：%s: %s（不影响用站）' % (e.__class__.__name__, e))

    threading.Thread(target=work, daemon=True).start()


# ── 第二批接口：多智能体 / 分钟策略 / 关联检测 / 常驻采集器 ──────────────
#
# ★这一批与前七条接口有**一处刻意的不一样**，值得写在最前面：
#   前七条走 `tools.call(...)` → `{'summary','data'}` 两通道 → `uicopy.humanize`；
#   这一批**直接调能力包**，返回它们自己那个**扁平**结构（顶层就有 `ok`）。
#
#   为什么不合流：这一批的读者不是大模型，是页面。契约在前端已经按扁平形状
#   冻结了（`web/src/api2.ts` 的 `IntradayResp` / `LinkageResp` / `CollectorStatus`），
#   再套一层 `{summary,data}` 就得在 `api2.ts` 里拆包 —— 而拆包那一步正是
#   "`ok` 到底看哪一层"最容易出错的地方（外层 `ok:true`、里层 `ok:false`，
#   页面会显示成"成功了，但数据是空的"）。
#   ⇒ 两通道那条纪律管的是**对话**（摘要进上下文、原始数据进浏览器）；
#     页面直连这条路本来就只有数据一条通道，没有第二通道可拆。
#
# ★失败一律 **400**（同上面前七条的理由：200 但里面 `ok:false`，在只看状态码的
#   地方看着一切正常）。`api2.ts: fromBody()` 正是按这个约定读的：除了状态码，
#   它还要 `msg` / `error` / `hint` 三个键里的那句原因。

def _cap(modname, label, need=None):
    """取一个**分批上线**的能力包；没就绪就给出 `(None, 400 响应)`。

    ★复用 `tools._lazy` 而**不在 app.py 里重写一遍**："包在不在、入口在不在"
      这条规则只能有一份定义。两份的话，一边放宽了另一边不知道 ——
      同一个包会在对话里报「还没就绪」、在页面上报 500。而这两句话对用户
      完全不同：前者是"还没做"，后者是"做坏了"。
    """
    try:
        return tools._lazy(modname, label, need), None
    except tools.ToolError as e:
        return None, (jsonify({'ok': False, 'error': e.code, 'msg': str(e),
                               'hint': e.hint}), 400)


def _flat(res, **fill):
    """能力包的返回值 → HTTP 响应。`ok` 已经在返回值里，不再包一层。

    `fill` 是**兜底**：包自己给了的键以包为准（`setdefault`），
      只在包漏了契约要求的键时才补上。契约（`api2.ts`）里 `market`/`code`/`ts`
      这些是必填的，包漏一个页面就读不出来 —— 但**补的是身份字段，不是数据**：
      数字一律不许在这里补（补 0 正是这个项目最不能接受的坏法）。
    """
    body = dict(res or {})
    for k, v in fill.items():
        body.setdefault(k, v)
    return jsonify(uicopy.humanize(body)), (200 if body.get('ok') else 400)


def _bad(msg, code='BAD_ARGS'):
    return jsonify({'ok': False, 'error': code, 'msg': msg}), 400


def _need_sym(code, market=None, name=None):
    """把一份 (code, market) 归一化成 (market, code, name)。

    ★前端给了 `market` 就以它为准（页面自己的标的选择器比反推可靠）；没给才整体反推。

    ★但**歧义必须照样抛**，`market` 给了也不能吞 —— 这一条是踩出来的：
      `_need_symbol` 对 `000001` 会同时给出「上证指数(000001)」和「平安银行(000001)」，
      而前端那个 `market` 字段两边都是 `'cn'`，**它分不开这一对**。
      本来写成"给了 market 就吞掉所有 ToolError"，结果是 `000001` 被静默放行，
      下游拿指数当股票算出一份**看起来正常**的分析 —— 错得毫无症状。
      ⇒ 吞的只许是 `BAD_SYMBOL`（"我认不出"）。那一种确实可能是**前端知道而
        `feed.resolve` 不认识的代码**（港股 `00700`、美股 `AAPL`、各类指数代码），
        前端既然点明了市场，就按它说的走；认不出来由下游自己报。

    ★判别的分野是「**谁有能力区分**」：歧义要靠语义知识分，`feed.resolve` 有、
      前端的 market 字段没有 ⇒ 必须问回去；认不认得出要靠代码库分，
      前端说得出市场就够 ⇒ 放行。
    """
    if market:
        try:
            return market, tools._need_symbol(code)['code'], (name or None) or code
        except tools.ToolError as e:
            if e.code == 'AMBIGUOUS':
                raise
            return market, code, (name or None) or code
    sym = tools._need_symbol(code)
    return sym['market'], sym['code'], name or sym['name']


# §3.2 分钟级策略
@app.route('/api/intraday', methods=['POST'])
def intraday_api():
    b = request.get_json(silent=True) or {}
    # ★顺序是**先校验参数、再取能力包**，不是反过来。理由不是省事：
    #   反过来的话，一个把 `periods` 打成字符串的用户会收到"分钟级策略分析
    #   这个能力还没就绪" —— 一句把**他自己的手误**说成**服务端的缺失**的话，
    #   他会去等一个不会来的东西，而真正要改的那个字段没人提。
    #   （`_cap` 里那次 import 也很便宜，所以这不是性能考量。）
    code = (b.get('code') or b.get('symbol') or '').strip()
    if not code:
        return _bad('要给我标的代码（code）')
    try:
        market, code, name = _need_sym(code, b.get('market'), b.get('name'))
    except tools.ToolError as e:
        return _bad(str(e), e.code)
    periods = b.get('periods') or [5, 15, 30]
    try:
        pl = tuple(int(p) for p in periods)
    except (TypeError, ValueError):
        return _bad('periods 必须是数字数组，收到 %r' % (periods,))
    mod, err = _cap('intraday', '分钟级策略分析', need='analyze')
    if err:
        return err
    # ★港美股/加密在本机**没有分钟线源**（2026-09-22 实测：腾讯 fqkline 对港美股的 m5
    #   只回一根聚合日线、新浪 getKLineData 只服务 A 股、东财 push2his 直连与走 Clash
    #   都 RemoteDisconnected）。这些市场改走**日线口径**的短期策略。
    #   分流只在这一层判一次「要不要走日线」，具体走哪条由 `daily_short.analyze_auto`
    #   定 —— 那边是唯一的真源（见它的模块头：为什么不在这里写 if、以及为什么另开
    #   一个模块而不是改 `agent/intraday/`）。
    #   ★A 股那条路**逐字节没动**（下面分支为假时仍是 `mod.analyze`）：日线降级是
    #   纯增益，不该让原来能跑的路径经过任何新代码。
    #   ★lazy import + 兜底：拿不到 `daily_short` 就按原路走（行为与本改动之前一致），
    #   不让"降级"本身变成一个新的失败点。
    try:
        from daily_short import analyze_auto as _auto, needs_daily as _needs
        _daily = _needs(market)
    except Exception:                                             # noqa: BLE001
        _auto, _daily = None, False
    try:
        if _daily:
            res = _auto(market, code, periods=pl, name=name)
        else:
            res = mod.analyze(market, code, periods=pl, name=name)
    except Exception as e:                                        # noqa: BLE001
        return _bad('分钟级分析没跑成：%s: %s' % (type(e).__name__, str(e)[:180]), 'INTRADAY_FAIL')
    # ★这一条不是装饰：`api2.ts` 的 `IntradayResp` 里写着「`synthetic` 为真 **或**
    #   `data_source` 看着像合成」，也就是前端两个键都读。包本身只给 `data_source`，
    #   所以在这里把 `synthetic` 补上，让**两条路给的东西一致**（聊天那条走
    #   `tools.analyze_intraday`，那里也是这么标的）。
    #   判法从 `tools.is_synthetic` 取 —— 同一个判断只许有一处定义，理由见那个函数。
    if isinstance(res, dict):
        res.setdefault('synthetic', tools.is_synthetic(res.get('data_source')))
    return _flat(res, market=market, code=code, name=name)


# §3.3 关联检测
@app.route('/api/linkage', methods=['POST'])
def linkage_api():
    b = request.get_json(silent=True) or {}
    scope = b.get('scope') or 'all'
    codes = b.get('codes') or None
    if isinstance(codes, str):
        codes = [codes]
    # ★这一条不是多写的：不拦的话 `codes: 123` 会一路走到 `linkage.scan()` 里
    #   撞成 `TypeError: 'int' object is not iterable`，被下面那个 `except` 包成
    #   `LINKAGE_FAIL` + 一句 Python 异常文本 —— 读起来像**服务端坏了**，
    #   实际是用户把字段填错了。同一个仓库里的纪律：在**边界**上认出用户的手误，
    #   别把它报成自己的故障（否则他会去等一个不会来的修复）。
    if codes is not None and (not isinstance(codes, (list, tuple))
                              or not all(isinstance(x, str) for x in codes)):
        return _bad('codes 要是代码数组（或单个字符串），收到 %r' % (codes,))
    try:
        top = int(b.get('top') or 20)
    except (TypeError, ValueError):
        return _bad('top 必须是数字，收到 %r' % (b.get('top'),))
    mod, err = _cap('linkage', '关联检测', need='scan')
    if err:
        return err
    try:
        res = mod.scan(scope=scope, codes=codes, top=top)
    except Exception as e:                                        # noqa: BLE001
        return _bad('关联检测没跑成：%s: %s' % (type(e).__name__, str(e)[:180]), 'LINKAGE_FAIL')
    return _flat(res, scope=scope)


@app.route('/api/linkage/anomaly', methods=['POST'])
def linkage_anomaly_api():
    b = request.get_json(silent=True) or {}
    # ★`symbol` 也收 —— 不是发明，是照 `/api/intraday` 那行
    #   `b.get('code') or b.get('symbol')` 补一致：本站 5 个单标的接口
    #   （kline / news / predict / game.quote）用的名字都是 `symbol`，
    #   只有这一个用 `code`。不一致本身不至于静默（那句 400 会把 `code`
    #   念出来），但一个从 `/api/kline` 学会 `symbol` 的手写客户端要为
    #   同一个概念多挨一次往返 —— 而这次放宽的代价是零。
    code = (b.get('code') or b.get('symbol') or '').strip()
    if not code:
        return _bad('要给我标的代码（code 或 symbol）')
    mod, err = _cap('linkage', '关联检测', need='anomaly_of')
    if err:
        return err
    try:
        res = mod.anomaly_of(code)
    except Exception as e:                                        # noqa: BLE001
        return _bad('取关联没跑成：%s: %s' % (type(e).__name__, str(e)[:180]), 'LINKAGE_FAIL')
    return _flat(res, code=code)


# §3.5 常驻采集器
# ★三条都收 GET 与 POST：前端 `getCollectorStatus()` 是**不带 body** 调的
#   （`call(url)` → GET），另两条带 body（→ POST）。只收一种的话，
#   前端那一条会撞上 405，而页面上显示的会是"这条路由服务端还没有" ——
#   一句把"没实现"和"方法收窄了"混起来的话。
@app.route('/api/collect/status', methods=['GET', 'POST'])
def collect_status_api():
    mod, err = _cap('collector', '常驻采集器', need='status')
    if err:
        return err
    try:
        return _flat(mod.status() or {'ok': True})
    except Exception as e:                                        # noqa: BLE001
        return _bad('取采集器状态没跑成：%s: %s' % (type(e).__name__, str(e)[:180]), 'COLLECT_FAIL')


@app.route('/api/collect/ring', methods=['GET', 'POST'])
def collect_ring_api():
    b = request.get_json(silent=True) or {}
    # ★`limit = int(b.get('limit') or 120)` 是错的，实测：`limit=0` 被 `or` 当成
    #   "没给" ⇒ 变成 120 ⇒ "最近 0 轮"返回**全部**记录（`ring(limit=0)` 实测回了
    #   2 条全部）。`or` 只该用来兜 `None`，不该用来兜一个**合法的 0**。
    raw = b.get('limit')
    if raw is None:
        # ★本路由**声明了 GET**（`methods=['GET', 'POST']`），而 GET 唯一的传参
        #   口就是 query string —— 只读 `get_json()` 的话 `?limit=0` 会被整个
        #   丢掉、静默退回 120，**和上面那个 `or` 是同一个病**：用户要 0 条，
        #   拿到全部。照本文件已有的先例（`/api/ledger` 那行
        #   `body.get('refresh', request.args.get('refresh', '0'))`）body 优先、
        #   query 兜底。
        raw = request.args.get('limit')
        if raw == '':        # `?limit=` 是**表单里留空**的通用写法 ⇒ 当"没给"；
                             # 而 `'abc'`/`''`（JSON 体里）是垃圾 ⇒ 照旧 400。
            raw = None
    try:
        limit = 120 if raw is None else int(raw)
    except (TypeError, ValueError):
        return _bad('limit 必须是数字，收到 %r' % (raw,))
    mod, err = _cap('collector', '常驻采集器', need='ring')
    if err:
        return err
    try:
        items = mod.ring(limit=limit)
    except Exception as e:                                        # noqa: BLE001
        return _bad('取采集环没跑成：%s: %s' % (type(e).__name__, str(e)[:180]), 'COLLECT_FAIL')
    # `ring()` 返回的是一个**列表**，而 HTTP 这一层要一个带 `ok` 的对象
    # （`api2.ts` 收 `{ok, items|ring}`，两个键名都认）。
    return _flat({'ok': True, 'items': items or []})


@app.route('/api/collect/once', methods=['POST'])
def collect_once_api():
    b = request.get_json(silent=True) or {}
    mod, err = _cap('collector', '常驻采集器', need='once')
    if err:
        return err
    try:
        return _flat(mod.once(scope=b.get('scope') or 'all'))
    except Exception as e:                                        # noqa: BLE001
        return _bad('采一轮没跑成：%s: %s' % (type(e).__name__, str(e)[:180]), 'COLLECT_FAIL')


# §3.4 多智能体博弈（SSE）
# ★这里**曾经**有一个本地版 `_agents_stream`（队列 + 心跳 + 一个 worker 线程），
#   明知与 `agents.sse_stream` 是同一件事，还是写了两份。代价实测出来了：
#     - 它**从来没有**设过 holdings/profile。而 `ContextVar` 是每线程一份的，
#       只有在这个 worker 线程里设才管用 ⇒ 风控的集中度/前五大那两条 veto
#       条件在生产里**结构上永远"没取到"**（不报错，静默走降级分支）。
#     - 它的 `except` 只发 `error`、**不发 `done`** ⇒ 真出异常时前端一直转圈。
#   两处都只有 `agents.sse_stream` 那一份做对了。同一件事留两份实现，
#   就是"一处做对了、另一处没人发现"的标准形状 —— 所以**删掉本地那份**、
#   直接调它。（`verify_agent.py` 里也没有别的调用点，删了不留悬空引用。）
@app.route('/api/agents', methods=['POST'])
def agents_api():
    b = request.get_json(silent=True) or {}
    q0 = (b.get('question') or '').strip()
    if not q0:
        return _bad('没问题内容')
    # ★`need='sse_stream'` 而不是 `'run'`：现在真正被调的是它。
    #   还写 `'run'` 的话，一个只落了 `run` 没落 `sse_stream` 的半成品包
    #   会通过这道检查、然后 AttributeError 成 500 —— 那就是把"还没做"
    #   报成"做坏了"（`_cap` 的 docstring 里那两句话的区别）。
    mod, err = _cap('agents', '多智能体博弈', need='sse_stream')
    if err:
        return err
    # ★画像是**这一轮的参数**，清洗复用 `tools.clean_profile`，理由同 `/api/chat`：
    #   两处各清一半的话，两边都会以为对方清干净了。
    #   ★持仓同理走 `tools.clean_holdings`，且**必须**由 `sse_stream` 在线程内设
    #   （见它的 docstring），不在这里设 —— 在这里设等于没设。
    #   ★`heartbeat` 显式传 `llm.HEARTBEAT`：这个间隔只能有一份定义，
    #     让它去用自己那个默认值就等于又开了第二处。
    return Response(mod.sse_stream(q0, history=b.get('history'),
                                   profile=tools.clean_profile(b.get('profile')),
                                   holdings=tools.clean_holdings(b.get('holdings')),
                                   heartbeat=llm.HEARTBEAT),
                    mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
    })


@app.route('/api/probe', methods=['GET', 'POST', 'DELETE'])
def probe():
    if not os.environ.get('LICAI_PROBE'):
        return jsonify({'error': 'probe channel not enabled'}), 404
    if request.method == 'POST':
        _PROBE['data'] = request.get_json(silent=True)
        return jsonify({'ok': True})
    if request.method == 'DELETE':
        _PROBE['data'] = None
        return jsonify({'ok': True})
    return jsonify({'data': _PROBE['data']})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8848'))
    print('引擎：', json.dumps(ENGINE.status(), ensure_ascii=False))
    ks = apikey.state()
    print('大模型：%s（key %s）' % (
        llm.config()['model'],
        ('%s 来自%s' % (ks['mask'], ks['source_text'])) if ks['configured']
        else '★未配置，对话会降级 —— 可以在网页右侧栏「大模型 Key」里填'))
    print('台账：%s' % ledger.path())
    _boot_backfill()
    print('打开 http://127.0.0.1:%d/' % port)
    app.run(host='127.0.0.1', port=port, threaded=True, debug=False)
