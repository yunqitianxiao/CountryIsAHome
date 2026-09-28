"""舆情层：把「采集」变成**结构化数据源**，而不是一坨文本。

定位（用户原话的落点：「这个注重通讯的采集，弱化分析」）：
  采集是主角 —— 本模块**自己不打任何网络**，它只组合 `news.collect()` 的结果，
  把它整理成**多个 Agent 能直接消费**的结构（items / heat / timeline / digest）。
  分析只到"数数"这一层：数了几个源、数了几条、数了几个上游标签。
  任何需要"判断"的东西（这条是利好还是利空、该不该买）**一律不做**，
  也不留字段暗示。

★★★sentiment_raw 的红线（本模块最重要的一条纪律）：
    `sentiment_raw` **只允许**是上游自己给的分值/标签，原样搬运；
    上游没给就是 `None`。**绝对不许自己算一个情绪分填进去。**
    原因不是"算不准"，是**事后分不出来**：我们算的 -0.30 和上游给的 -0.30
    在结构里长得一模一样，连 `upstream_def` 都救不了 —— 下游消费者没有办法
    把"别人测的"与"我们编的"分开，于是整条链的可信度由最不可信的那个数决定。
    而"没有情绪数据"是一个**可以被接受**的诚实结论，"有一个来路不明的情绪分"不是。
    ⇒ 本模块只搬运和转述：`sentiment_raw` 原样带出（`_shape` 一个字都不改）；
      `digest()` 只报"哪些上游提供了分值"，**不报任何聚合后的情绪结论**。
    ⇒ 这条红线由 `selftest()` 的第 2 条守着（把上游给的值篡改/丢弃都会红）。

★两个"热度"不许混（同 `adata_src` 的约定）：
    item['heat']      = **我们数的**：同一事件被几个源报道（多源共振）← heat() 的主角
    item['hot_value'] = **上游给的**：榜单自己的热度值（同花顺 482886.0 那种）
    item['rank']      = **上游给的**：榜内名次
  三个字段各自带来源，谁也不许顶替谁。

★时间戳三种性质都在场上，所以每条都带 `ts_kind`：
    'upstream'  上游给的事件时间（快讯/公告）—— 可以进时间轴排序
    'collected' 我们盖的采集时刻（榜单/舆情快照，上游不给条目时间）
    'none'      上游没给、我们也取不到
  `timeline()` 把三种都排在一条线上但**逐条标出是哪一种**。混排而不标 =
  让一个两分钟前采集到的榜单看起来像两分钟前发生的事。这是本模块最容易做歪的一处。

★token 预算：`digest()` 是给大模型读的那条通道，体量**硬约束**在 `DIGEST_BUDGET`
  字节（与 `tools.SUMMARY_BUDGET` 对齐，见 `_tools_budget()`）。超预算的处理是
  "按热度剪掉尾巴并**报出剪了几条**"，不是截字符串 —— 截字符串会把某条的标题切成
  半句话，那半句看起来还像一条真新闻；少给几条是**可见的**（`dropped_n`）。

★本模块的公开面：`collect` / `heat` / `timeline` / `digest` / `health` / `selftest`。
"""
import ast
import hashlib
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_HERE)
# ★本仓库的导入约定是"平铺"（sys.path 里放 `agent/`，然后 `import news`），
#   且 `agent/__init__.py` 不存在 ⇒ 本包是以顶层包身份被导入的。这里自己补一次
#   路径，重复插入无害（先查再插），所以被谁导入都成立（同 intraday 的做法）。
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import feed   # noqa: E402  _num()：NaN/Inf/numpy 标量 → None（AGENT-SPEC §0.7）
import news   # noqa: E402  采集层（四层降级 + 三级去重）；本模块只调它

# 摘要通道预算（字节，不是字符 —— 与 tools.SUMMARY_BUDGET 同一把尺）。
# ★不能 `import tools`：它 `import runner`，而 runner 拉 torch。
#   所以那个常量只能用 **ast 读源码**核对，见 `_tools_budget()` 与 `selftest()` #1。
DIGEST_BUDGET = 4000
_TOOLS_PY = os.path.join(_AGENT_DIR, 'tools.py')

# 热度分数的权重。★三个分量都**返回**（见 `_heat_from`），所以没人需要"相信"这个和：
#   任何消费者都可以拿分量自己重算，或者换一组权重。
#   45/30/25 的分配理由：共振是**这个模块存在的理由**（多源同题是本项目最独特的信号），
#   所以它拿大头；体量次之（热闹程度）；完整度再次之（它量的是"我们看得全不全"，
#   是数据质量的读数，不是舆情强度 —— 给它最高权重会把"今天源都通"算成"今天很热"）。
HEAT_W = {'resonance': 0.45, 'volume': 0.30, 'breadth': 0.25}

# 体量分量的"满分"条数。★实测（2026-09-22 本机，22 个启用源、默认 10 条/源）：
#   去重后 `count_raw` 落在 60~110 之间。取 40 当满分而不是 100，是因为
#   体量分量的用途是"区分冷清与热闹"，而 40 条以下才是真正冷清的日子（周末/盘前）；
#   40 往上再涨，边际信息量已经很小（都是同一批快讯的转述）。
VOLUME_FULL = 40

# 统计窗口的条数上限。★它是"heat/timeline/digest 要看的条数"，与调用方的
# `limit`（**展示**要几条）**解耦**，理由是本模块最容易犯的一个错：
#   拿"展示用的那 14 条"去算 sources_n / multi_n / top_terms，
#   于是同一分钟的市场，limit=14 的人量到 sources_n=2、limit=60 的人量到 sources_n=15。
#   那正是"尺子跟着读数走"（同一个病在 volume 上已经修过一次，见 `_heat_from`）。
# 400 的依据：源清单里 `max_items_per_source=10`、当前 22 个启用源 ⇒ 上限 220 条原始条目，
#   去重后只会更少。400 稳稳高于任何可能的上限，所以它等价于"全量"。
HEAT_SCAN = 400

DIGEST_TOP_MAX = 20      # 摘要里最多带几条明细（还会被预算进一步剪）
DIGEST_TERMS_MAX = 10    # 摘要里最多带几个上游标签
RESONANCE_MAX = 8        # heat() 里最多列几件"多源共振"的事
TIMELINE_MAX = 60        # timeline() 默认最多排几条

# ★为什么摘要里必须有无条件出现的这句话：部分条目（adata 热度榜/龙虎榜、
#   千股千评）的标题里带上游的涨跌幅与上榜原因 —— 那是**上游事实**，但读者
#   很容易把它读成方向性信号。摘要层不提供判断，就必须明说它不提供。
RISK_NOTE = ('本摘要仅汇总公开资讯的采集结果（条数 / 来源数 / 上游标签）与上游自带的分值，'
             '不含任何方向性判断，也不构成投资建议。'
             '部分条目的标题含上游的涨跌幅、上榜原因等字段，那是上游事实、不是本系统的观点。')

_NOTES = [
    'sentiment_raw 只来自上游（金十微博舆情 / 东财千股千评），没有该字段的源一律 None，'
    '本模块不生成任何情绪分。',
    "item['heat'] 是**我们数的**多源报道数（同一事件被几个源报道），"
    "上游自己的热度在 item['hot_value']/item['rank']。",
    "ts_kind='collected' 的条目（榜单/舆情快照）的 ts 是采集时刻，不是事件时刻。",
]


def _num(v, nd=4):
    return feed._num(v, nd)


def _bytes(d):
    """JSON 序列化后的**真实字节数**（allow_nan=False：有 NaN 直接抛，不悄悄写 null）。"""
    return len(json.dumps(d, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _tools_budget():
    """从 `tools.py` **源码**里 ast 读出 SUMMARY_BUDGET（不能 import，见文件头）。

    读不到返回 None —— "读不到"和"读到别的数"必须能被区分，所以不返回默认值。
    """
    try:
        with open(_TOOLS_PY, encoding='utf-8') as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == 'SUMMARY_BUDGET':
                    try:
                        return ast.literal_eval(node.value)
                    except ValueError:
                        return None
    return None


# ── subject 路由 ──────────────────────────────────────────────────────

def _route(subject, subject_name=None):
    """`subject` → 采集层的 (symbol, query, keywords)。subject 是本层对外的统一名字。

    ★为什么要在这里做路由：采集层的两个参数**含义不同** —— `symbol` 会**启用
      个股域的源**（东财个股新闻/公告/千股千评/热门关键词），`query` 只做
      **全局源的过滤词**。把 6 位代码误当 query 传，个股源会整批
      `skipped='needs_symbol'`，而**跳过是安静的**（不报错、不降级）。
    ★判据是"6 位纯数字"，这是一条**可复核的格式规则**，不是"像不像股票代码"。
    ★`subject_name`：给了才用它过滤全局源。只给代码时全局源**不加过滤词**
      ⇒ 回来的全局条目是**市场面**（不是这只票的），所以每条都带 `scope`
      让消费者自己分（见 `collect()` 的 notes）。
    """
    s = str(subject or '').strip()
    if s and s.isdigit() and len(s) == 6:
        kw = [str(subject_name).strip()] if subject_name else None
        return s, None, kw
    # 非代码 subject：当全局过滤词用（不启用个股源）
    return None, (s or None), ([str(subject_name).strip()] if subject_name else None)


def _needles(subject, query, subject_name=None):
    """判断"这条跟 subject 有没有关系"用的字面针。全是**调用方给的原话**，不猜。"""
    out = []
    for v in (subject, query, subject_name):
        v = str(v or '').strip()
        if v and v not in out:
            out.append(v)
    return out


def _relevant(it, needles):
    """字面相关：针出现在标题/摘要/标签里，或命中了上游给的 related 代码/名称。

    ★这是**子串匹配**，不是语义相关 —— 它的价值在于确定性（同样的输入永远同样的
      结果，可以复核）。它会把"贵州茅台镇"错认成"贵州茅台"，这个已知误差
      写在这里，而不是用一个更聪明的规则把它藏起来。
    ★没有针（subject=None）⇒ 全部相关（市场面采集）。
    """
    if not needles:
        return True
    hay = ' '.join([str(it.get('title') or ''), str(it.get('excerpt') or '')]
                   + [str(t) for t in (it.get('tags') or [])])
    for r in (it.get('related') or []):
        if isinstance(r, dict):
            hay += ' ' + str(r.get('code') or '') + ' ' + str(r.get('name') or '')
    return any(n in hay for n in needles)


# ── item 整形 ─────────────────────────────────────────────────────────

def _id(it):
    """条目标识：**跨轮稳定**（同一事件两轮采集得到同一个 id）。

    ★不是随机 uuid：id 的用途是让下游（前端/缓存/引用）指得动"就是这一条"，
      每次采集都变的 id 等于没有 id。
    ★只用上游给的两样（url、source）+ 归一标题；**不含采集时刻** ——
      把时刻算进 id 就等于每轮一个新 id，那就回到随机 uuid 了。
    """
    key = '|'.join([str(it.get('url') or ''), str(it.get('source') or ''),
                    news.norm_title(it.get('title'))])
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]


def _shape(it, scope=None):
    """采集层 item → 舆情层 item。

    ★`sentiment_raw` 是**原样带出**（`it.get` 一次，不复制、不换算、不补默认分）。
      上游没这个字段 ⇒ None。见文件头的红线。
    ★`scope` 来自源清单（symbol/global），标出这条是"关于这只票"还是"市场面"。
    """
    return {
        'id': _id(it),
        'title': it.get('title'),
        'source': it.get('source'),
        'source_label': it.get('source_label'),
        'scope': scope,
        'ts': it.get('ts'),
        'ts_last': it.get('ts_last'),
        'ts_kind': it.get('ts_kind'),
        'url': it.get('url'),
        'heat': _num(it.get('heat'), 0),          # 我们的多源计数（int）
        'hot_value': it.get('hot_value'),         # 上游热度（没有这个源的条目就没有）
        'rank': it.get('rank'),                   # 上游名次
        'sentiment_raw': it.get('sentiment_raw'),  # ★上游给的或 None —— 不许动
        'tags': [str(t) for t in (it.get('tags') or []) if t],
        'related': list(it.get('related') or []),
        'sources': list(it.get('sources') or []),
        'seen_in': list(it.get('seen_in') or []),
        'excerpt': it.get('excerpt') or '',
        'merged_by': it.get('merged_by'),
    }


def _shape_all(res):
    """把一轮采集结果整份整形，并把源清单里的 scope 摊到每条上。"""
    scope_by_sid = {r.get('id'): r.get('scope') for r in (res.get('sources') or [])}
    return [_shape(it, scope=scope_by_sid.get(it.get('source'))) for it in (res.get('items') or [])]


# ── heat：多源共振 ────────────────────────────────────────────────────

def _top_terms(items, n=DIGEST_TERMS_MAX):
    """上游标签的词频。★**只数上游给的标签**（同花顺 tags[].name、新浪 tag[].name、
    adata concept_tag/pop_tag、akshare 概念名称/公告类型），**绝不从标题里抽词**。

    理由与文件头的 sentiment_raw 红线同源：抽出来的词和上游给的词在结构里
    长得一模一样（都是一个字符串），事后**分不出来**是谁给的。于是"上游说今天
    市场在聊人工智能"和"我们从标题里提出'人工智能'"会变成同一条数据，
    而后者是**分析**（弱化分析是本模块的定位），且它会随标题措辞漂移。
    """
    cnt = {}
    for it in items:
        for t in (it.get('tags') or []):
            t = str(t).strip()
            if t:
                cnt[t] = cnt.get(t, 0) + 1
    rows = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{'term': k, 'n': v, 'from': 'upstream_tag'} for k, v in rows[:max(1, int(n))]]


def _resonance_top(items, n=RESONANCE_MAX):
    """按多源报道数排前几件事。这是 heat 的主角：**同一个事件被几个源报道**。"""
    rows = [it for it in items if (_num(it.get('heat'), 0) or 0) >= 2]
    rows.sort(key=lambda x: (-(_num(x.get('heat'), 0) or 0), str(x.get('ts') or '')))
    return [{'id': it.get('id'), 'title': it.get('title'), 'ts': it.get('ts'),
             'heat': _num(it.get('heat'), 0),
             'sources': [s.get('id') for s in (it.get('sources') or [])]}
            for it in rows[:max(1, int(n))]]


def _sentiment_prov(items):
    """情绪侧账本：**谁提供了上游分值**，提了几条。

    ★只报"有/没有"和出处，**不报任何聚合值**（不报均值、不报正负比）——
      聚合值是一个结论，而本模块不做结论；更重要的是一旦报了均值，
      下游就会把它当"情绪分"用，那时它和上游给的分数就真的分不开了。
    """
    by_src = {}
    for it in items:
        raw = it.get('sentiment_raw')
        if isinstance(raw, dict):
            name = str(raw.get('src') or it.get('source') or '?')
            by_src[name] = by_src.get(name, 0) + 1
    return {'items_with_raw_n': sum(by_src.values()),
            'upstream': [{'src': k, 'n': v} for k, v in sorted(by_src.items())]}


def _heat_from(items, srcs, total_n=None, n_terms=DIGEST_TERMS_MAX):
    """算热度。★**每个分量都返回**，所以 score 是可复核的，不是"信我"。

    三个分量（都在 [0,1]）：
      resonance  多源共振：被最多源报道的那件事，覆盖了多少比例的**活跃源**。
                 定义 `(max_heat - 1) / (活跃源数 - 1)`，`max_heat < 2` 时为 0。
                 ★用 (x-1) 是因为"1 个源报道"是**没有共振**的基线，不是满分：
                   只有 1 个活跃源时 max_heat 只能是 1 ⇒ resonance 恒 0（正确：
                   一个源自说自话不是共振）。若用 max_heat/活跃源数，单源场景
                   会得到 resonance=1.0 的假高峰。
      volume     体量：`min(1, 条数 / VOLUME_FULL)`。★`total_n` 不传就取 `len(items)`，
                 而调用方喂进来的 `items` 是**统计窗口**（`HEAT_SCAN` 条、未经 `limit`
                 截断）⇒ 同一分钟的市场不会因为调用方的 limit 大小而量出冷热差。
                 传 `total_n` 只给"手上有更权威的计数"的场景用（自检打靶）。
      breadth    完整度：`能出数的源 / 该出数的源`（被角色过滤/缺 symbol 而
                 **跳过**的源不算分母）。★它量的是"我们看得全不全"，是数据质量，
                 不是舆情强度 —— 看的是它掉下去（源大面积挂了）时的告警价值。
    """
    srcs = list(srcs or [])
    active = sorted({str(it.get('source')) for it in items if it.get('source')})
    heats = [_num(it.get('heat'), 0) or 0 for it in items]
    max_heat = max(heats) if heats else 0
    resonance = ((max_heat - 1) / float(len(active) - 1)) if (max_heat >= 2 and len(active) > 1) else 0.0
    ran = [s for s in srcs if not s.get('skipped')]
    ok_n = len([s for s in ran if s.get('ok')])
    n_items = int(total_n if total_n is not None else len(items))
    volume = min(1.0, n_items / float(VOLUME_FULL))
    breadth = (ok_n / float(len(ran))) if ran else 0.0
    score = (HEAT_W['resonance'] * resonance + HEAT_W['volume'] * volume
             + HEAT_W['breadth'] * breadth)
    return {
        'score': _num(score, 4),
        'components': {'resonance': _num(resonance, 4),
                       'volume': _num(volume, 4),
                       'breadth': _num(breadth, 4)},
        'weights': dict(HEAT_W),
        'sources_n': len(active),                       # 本轮真正出了数的源数
        'sources_active': active,
        'sources_ok_n': ok_n,
        'sources_ran_n': len(ran),
        'sources_skipped_n': len([s for s in srcs if s.get('skipped')]),
        'items_n': len(items),
        'count_raw': n_items,                           # volume 用的那个计数（可复核）
        'multi_n': len([h for h in heats if h >= 2]),   # 被 ≥2 个源报道的事件数
        'max_heat': max_heat,
        'resonance_top': _resonance_top(items),
        'top_terms': _top_terms(items, n_terms),
    }


# ── 公开面 ────────────────────────────────────────────────────────────

def _notes_for(needles, res):
    """这一轮的**动态**说明（静态纪律在 `_NOTES` 里，两条都出）。"""
    out = list(_NOTES)
    if needles:
        out.append('相关性判据是**字面**子串匹配（针=%s），不是语义相关；'
                   '例如「贵州茅台镇」会被算成命中「贵州茅台」。' % ('/'.join(needles),))
    else:
        out.append('本轮没有 subject/query ⇒ items 是**市场面**全量，'
                   '不含"与某标的有关"的筛选。')
    n_skip = len([s for s in (res.get('sources') or []) if s.get('skipped')])
    if n_skip:
        out.append('%d 个源被**跳过**（缺 symbol 或被 roles 过滤），'
                   '跳过既不算成功也不算失败，明细在 collected.sources[].skipped。' % n_skip)
    if res.get('degraded'):
        out.append('%d 个源本轮失败，明细在 degraded[]。' % len(res['degraded']))
    return out


def _view_order(items):
    """`collect().items` 与 `digest().top` 共用的**一种**展示顺序：
    共振多的在前 → 其次事件时间新的在前 → `ts_kind='collected'` 的沉到同段末尾。

    ★为什么不按 `ts` 倒序（实测 2026-09-22 本机 17:57，`collect(limit=12)`）：
      纯 ts 倒序时前 12 条是 **6 条巨潮公告 + 6 条同花顺热股榜**，一条真新闻都没有 ——
      而当晚被 **5 个源**同时报道的那条（`heat=5`，亚虹医药 APL-2501）没进前 12。成因是
      `ts` 这一列里混着两种东西：①上游给的事件时间；②**我们看表的时间**
      （`ts_kind='collected'` 的榜单是快照，`ts`＝采集时刻 17:57:35）；③巨潮把盘后
      公告定在**次日 00:00:00**。按它排 ⇒ "我们几点看的"压过了"市场在发生什么"。
    ★排序键全是**事实**，没有一处打分：`heat` 是我们数出来的源数（本层的主角信号），
      `ts`/`ts_kind` 是上游给的。**没有"重要性"这种主观量**。
    ★`ts_kind='collected'` 沉底只发生在**同 heat 段内**：它的 ts 不是事件时间，
      但"这个源此刻在榜上"本身仍是有信息的 —— 沉底不是丢掉。
    ★`timeline()` **不**用这个顺序（它按时间轴升/降序，见 `_timeline_from`）。
    """
    out = sorted(items, key=lambda it: str(it.get('ts') or ''), reverse=True)
    # 稳定排序：上面先按 ts 倒序，这一趟只在 heat/ts_kind 上再分档，ts 序被保留
    out.sort(key=lambda it: (-_num(it.get('heat'), 0),
                             1 if it.get('ts_kind') == 'collected' else 0))
    return out


def _run(subject=None, query=None, limit=12, subject_name=None, roles=None, **kw):
    """一轮采集 + 整形，返回**统计窗口**（不受 `limit` 影响）。

    ★所有出口（collect/heat/timeline/digest/health）都走这里 ⇒ 它们口径一致，
      而且"统计窗口"这个词只在这一处定义（别处再写一遍必然漂）。
    """
    lim = max(1, int(limit or 12))
    symbol, q, kwds = _route(subject, subject_name)
    if query:
        kwds = list(kwds or []) + [str(query).strip()]
    res = news.collect(symbol=symbol, query=q, limit=max(lim, HEAT_SCAN),
                       keywords=kwds or None, roles=roles, **kw)
    all_items = _shape_all(res)
    needles = _needles(subject, query, subject_name)
    # ★排序只在这里做一次（`_view_order`）；统计（heat/sentiment/时间轴）都与顺序无关，
    #   而 timeline/digest 各自会重排 ⇒ 改这里只影响"展示哪几条在前"。
    subj = _view_order(_items_of(all_items, needles))
    return {'shown_n': lim, 'all': all_items, 'subj': subj, 'res': res,
            'srcs': res.get('sources') or [], 'needles': needles,
            'count_raw': res.get('count_raw'), 'meta': _meta(res, subject, subject_name, query)}


def collect(subject=None, query=None, limit=12, subject_name=None, roles=None, **kw):
    """采集 + 整形。本层的主入口；返回结构见文件头的契约。

    参数
      subject       标的（6 位代码 → 启用个股源）或自由文本（→ 全局源过滤词）。
      query         额外的全局过滤词（与 subject 同时给时，两个都用）。
      limit         **展示**条数上限。★它**不影响** heat（heat 走统计窗口，见 `_run`）。
      subject_name  标的名称。★只给代码时全局源**没有过滤词**，回来的全局条目
                    是市场面而不是这只票的 —— 所以每条带 `scope`，notes 里也说了。
      roles         只打这些角色的源（'news'|'announcement'|'heat'|'sentiment'）。
      **kw          透传给 `news.collect`（concurrency / budget_ms / keywords）。

    返回（用户契约，本模块保证这些键**永远都在**）：
      {'ok', 'ts', 'subject', 'items': [...], 'heat': {...},
       'collected': {'sources': [{'id','ok','n','ms','err', ...}]},
       'degraded', 'notes'}
      heat 是 `_heat_from()` 的**超集**：契约要求的 score/sources_n/top_terms
      全在里面，另有 components/weights/resonance_top 等可复核字段。
      ★`items` 是**展示切片**（前 `limit` 条），`items_scanned_n` 是它背后
        真正被统计的条数 —— 两者分开报，免得读者以为 heat 只看了 items 那几条。
    """
    r = _run(subject=subject, query=query, limit=limit, subject_name=subject_name,
             roles=roles, **kw)
    out = dict(r['meta'])
    out.update({
        'items': r['subj'][:r['shown_n']],
        'items_scanned_n': len(r['subj']),
        'subject_items_n': len(r['subj']),
        'heat': _heat_from(r['subj'], r['srcs']),
        'sentiment': _sentiment_prov(r['subj']),
        'collected': {'sources': [_src_row(s) for s in r['srcs']]},
    })
    out['notes'] = _notes_for(r['needles'], r['res'])
    return out


def _src_row(s):
    """源账本的一行（对外形状）。★`skipped` 单独一栏：跳过既不算成功也不算失败，
    折进 ok 或不报是两种都见过的假绿。"""
    return {'id': s.get('id'), 'label': s.get('label'), 'kind': s.get('kind'),
            'role': s.get('role'), 'scope': s.get('scope'), 'ok': s.get('ok'),
            'n': s.get('n'), 'ms': s.get('ms'), 'err': s.get('err'),
            'cached': s.get('cached'), 'skipped': s.get('skipped')}


def heat(subject=None, query=None, limit=TIMELINE_MAX, subject_name=None, roles=None, **kw):
    """多源共振读数：**同一件事被几个源报道** + 上游标签词频 + 综合热度分。

    ★与 `collect()` 里那个 `heat` 键是**同一个函数算的同一个东西**（`_heat_from`），
      这里只是把它单独提出来当一个公开入口，省掉调用方去 `collect()` 的返回值里挖。
    ★它看的是**统计窗口**（`HEAT_SCAN` 条）而不是 `limit` 那几条，理由见 `_run`。
    """
    r = _run(subject=subject, query=query, limit=limit, subject_name=subject_name,
             roles=roles, **kw)
    out = dict(r['meta'])
    out.update(_heat_from(r['subj'], r['srcs']))
    out.update({'items_scanned_n': len(r['subj']),
                'sentiment': _sentiment_prov(r['subj'])})
    out['notes'] = _notes_for(r['needles'], r['res'])
    return out


def _meta(res, subject, subject_name, query):
    """三个出口（collect/heat/timeline/digest）共用的头部字段。"""
    return {'ok': res.get('ok'), 'ts': res.get('as_of'), 'subject': subject,
            'subject_name': subject_name, 'query': query,
            'degraded': list(res.get('degraded') or []),
            'notes': list(res.get('notes') or [])}


def _items_of(items, needles):
    """从整形后的条目里挑出与 subject 相关的（没有针就是全量 = 市场面）。"""
    return [it for it in items if _relevant(it, needles)] if needles else list(items)


# ── timeline ──────────────────────────────────────────────────────────

def _timeline_from(items, limit, asc, meta):
    """时间轴的**纯函数内核**（不采网络）。`timeline()` 与 `selftest()` 共用它，
    ⇒ 自检测的就是线上那条路，不存在"自检测的是另一份实现"。"""
    dated = [it for it in items if it.get('ts')]
    undated = [it for it in items if not it.get('ts')]
    dated.sort(key=lambda x: str(x['ts']), reverse=not asc)
    dated = dated[:max(1, int(limit))]
    events = [{'id': it['id'], 'ts': it['ts'], 'ts_kind': it.get('ts_kind'),
               'title': it['title'], 'source': it.get('source'),
               'source_label': it.get('source_label'), 'scope': it.get('scope'),
               # ★这一行叫 `heat` 不叫 `sources_n`：`sources_n` 在**顶层**已经是
               #   "这一轮有几个源出了数"（`_heat_from` 的字段），同名字段在同一个
               #   包里表示两个量＝迟早有人读错，而且读错时不报错。
               #   与 `collect().items[].heat`（我们数的多源报道数）保持同一口径。
               'heat': _num(it.get('heat'), 0),
               'seen_in': it.get('seen_in') or [], 'url': it.get('url'),
               'tags': it.get('tags') or []}
              for it in dated]
    kinds = {}
    for it in items:
        k = str(it.get('ts_kind') or 'none')
        kinds[k] = kinds.get(k, 0) + 1
    span = ({'from': events[0]['ts'], 'to': events[-1]['ts']} if events else None)
    out = dict(meta)
    out.update({'n': len(events), 'undated_n': len(undated),
                'asc': bool(asc), 'span': span, 'ts_kind_n': kinds,
                'events': events,
                'undated': [{'id': it['id'], 'title': it['title'],
                             'source': it.get('source'), 'ts_kind': it.get('ts_kind')}
                            for it in undated]})
    return out


def timeline(subject=None, query=None, limit=TIMELINE_MAX, subject_name=None,
             roles=None, asc=True, **kw):
    """时间轴：**按时间排好序的事件流**（默认升序 = 从头读事情的先后）。

    ★每条带 `ts_kind`：`'collected'` 的条目（榜单/舆情快照）的 ts 是**采集时刻**，
      不是事件时刻。混排而不标，就等于把"两分钟前我们抓了一次榜"讲成
      "两分钟前发生了这件事" —— 那是一条凭空造出来的时间线。
    ★没有 ts 的条目**不进主序列**（无从排起），单独放 `undated` 并计数。
      把它们塞到某处（比如按采集顺序）会让"时间轴"这个承诺变成假的。
    ★`heat` 带着走（= 同一件事被几个源报道）：那是时间轴上最该看到的形状，
      与 `collect().items[].heat` 同口径。★**不叫** `sources_n` —— 那个名字在顶层
      已经是"这一轮有几个源出了数"，同一个包里两义必然被读错。
    """
    r = _run(subject=subject, query=query, limit=limit, subject_name=subject_name,
             roles=roles, **kw)
    out = _timeline_from(r['subj'], limit, asc, r['meta'])
    out['notes'] = _notes_for(r['needles'], r['res'])
    return out


# ── digest ────────────────────────────────────────────────────────────

def _digest_from(items, srcs, total_n, budget, meta):
    """摘要的**纯函数内核**（不采网络）。`digest()` 与 `selftest()` 共用它。"""
    h = _heat_from(items, srcs, total_n)
    # ★`top` 的取材顺序 = `_view_order`（同 collect 的展示序）⇒ `_fit` 从**尾部**弹掉的
    #   是"共振最少、时间最旧"的那些，`_fit` docstring 里"按热度剪尾巴"这句才是真的。
    #   （此前这里按 ts 倒序取材，实际剪的是**最老**的尾巴，与 docstring 打架 ——
    #    同一件事两处说法不一致，读的人只信他先读到的那处。）
    ordered = [it for it in _view_order(items) if it.get('ts')]
    top = [{'ts': it['ts'], 'ts_kind': it.get('ts_kind'),
            'title': str(it.get('title') or '')[:80],
            'source': it.get('source'),
            # ★同 `_timeline_from`：这一行是 `heat`（该条被几个源报道），不是 `sources_n`
            'heat': _num(it.get('heat'), 0)}
           for it in ordered[:DIGEST_TOP_MAX]]
    d = dict(meta)
    d.update({
        # ★这四个字段是**自指/收尾**字段：`_fit()` 量体量时它们必须已经**在位**
        #   （哪怕值还是占位），否则 `_fit` 量的是"另一份 payload" —— 它判绿之后
        #   我们再写 `dropped_n`/`bytes`/`over_budget` 会把体量顶出预算。
        #   （这不是假设的：把它放进 `_fit()` **之后**，预算 2000 时实测落出 2006 字节。
        #   是 `selftest` 的故障注入抓到的，见交付说明。）
        'budget_bytes': int(budget), 'bytes': 0, 'dropped_n': 0,
        'truncated': False, 'over_budget': False,
        # ★`headline` 必须在 `_fit()` **之前**放进字典（体量要算准，理由同上），
        #   但它的**取值**要在 `_fit()` **之后**重指一次 —— 见下面那两行注释。
        'headline': (top[0] if top else None),
        'top': top,
        'heat': {'score': h['score'], 'components': h['components'], 'weights': h['weights'],
                 'sources_n': h['sources_n'], 'max_heat': h['max_heat'],
                 'multi_n': h['multi_n'], 'count_raw': h['count_raw'],
                 'sources_ok_n': h['sources_ok_n'], 'sources_ran_n': h['sources_ran_n'],
                 'resonance_top': h['resonance_top'][:3],
                 'top_terms': h['top_terms'][:DIGEST_TERMS_MAX]},
        'sentiment': _sentiment_prov(items),
        'collected': {'sources_n': len(srcs),
                      'ok_n': h['sources_ok_n'],
                      'fail_n': len(meta.get('degraded') or []),
                      'skipped_n': h['sources_skipped_n'],
                      'degraded': (meta.get('degraded') or [])[:5]},
        'risk': RISK_NOTE,
        'notes': (meta.get('notes') or [])[:3],
    })
    d['dropped_n'], d['truncated'] = _fit(d, int(budget))
    # ★`headline` 重指（实测 2026-09-22 本机抓到）：`_fit()` 从 `top` **尾部**弹条目，
    #   预算紧时能把 `top` 弹空 —— 而 `headline` 若还指着弹出前的那一条，摘要就
    #   自相矛盾：它宣称的头条**不在**自己的明细里（预算 2000 时实测 top_n=0、
    #   headline 仍是一个具体标题）。读的人/大模型会引一条明细里查不到的事件。
    #   取值重指**只会变小或不变**（`top` 只被砍尾，`top[0]` 要么还是原来那条、
    #   要么整条没有 ⇒ headline=None），所以不会顶破 `_fit` 量过的体量。
    d['headline'] = (d['top'][0] if d['top'] else None)
    # ★剪到底还超预算（items/notes 都空了仍然塞不下）要**如实报出来**，不能当没发生：
    #   那时唯一的成因是常量太小（或字段被加得太多），而"摘要超预算"这件事
    #   没有第二个地方看得见 —— 调用方拿到 5000 字节的摘要会以为预算是 4000，一切正常。
    d['over_budget'] = bool(_bytes(d) > int(budget))
    d['bytes'] = _bytes(d)
    return d


def digest(subject=None, query=None, limit=TIMELINE_MAX, subject_name=None,
           roles=None, budget=DIGEST_BUDGET, **kw):
    """给大模型读的**短摘要**：体量硬约束在 `budget` 字节内。

    ★预算是硬的：超了就按热度**剪掉尾巴**（`top` 从尾部弹），并报出 `dropped_n`。
      不截字符串 —— 切标题会产出半句话，而半句话看起来像一条真新闻。
    ★摘要里**没有情绪结论**，只有"哪些上游提供了分值、各几条"
      （见 `_sentiment_prov` 与文件头的红线）。
    ★`risk` 无条件存在且含「不构成投资建议」：条目标题里带上游的涨跌幅，
      读者很容易把它读成方向（见 `RISK_NOTE`）。
    """
    r = _run(subject=subject, query=query, limit=limit, subject_name=subject_name,
             roles=roles, **kw)
    return _digest_from(r['subj'], r['srcs'], r['count_raw'], budget, r['meta'])


def _fit(d, budget):
    """按热度剪尾巴直到塞进 `budget`。返回 (剪掉的明细条数, 是否动过)。

    ★自指字段 `bytes` 的处理：量的时候把它按 0 算（`_MARGIN` 留出真实数字的宽度），
      否则"测量本身改变被测量"—— 那是本仓库吃过亏的经典形状（尺子量自己）。
    """
    dropped, touched = 0, False
    # ★顺序是**一类一类地砍**（明细 → 标签 → 共振对 → 注解），不是交叉着各砍一点。
    #   实测后果（2026-09-22 本机，78 条夹具）：预算 2000 时 `dropped_n=20 / top_n=0`，
    #   即**明细先被砍光**，标签/共振对才开始减。所以紧预算下拿到的是"账本没有明细"的摘要。
    #   这是有意的（明细是最长的一块，砍它最省字节），`dropped_n` 也如实报了出来；
    #   但使用者若需要"宁可少几条标签也要留明细"，那是**改这里的顺序**，不是改判据。
    while _bytes(d) + _MARGIN > budget and d['top']:
        d['top'].pop()
        dropped += 1
        touched = True
    while _bytes(d) + _MARGIN > budget and d['heat']['top_terms']:
        d['heat']['top_terms'].pop()
        touched = True
    while _bytes(d) + _MARGIN > budget and d['heat']['resonance_top']:
        d['heat']['resonance_top'].pop()
        touched = True
    while _bytes(d) + _MARGIN > budget and d['notes']:
        d['notes'].pop()
        touched = True
    return dropped, touched


# `bytes` 字段自己占的字节数的上界（键名 9 字节 + 4 位数字 + 分隔符）。
# 取 16 是保守上界；它保证 `_fit` 判绿的同时，落盘后的真实体量仍在预算内。
_MARGIN = 16


def health(subject=None, roles=None, limit=12, **kw):
    """体检：**每个源真实发生了什么**（ok/n/ms/err/skipped）+ 情绪侧账本。

    ★调两次采集层：`news.health()`（它自带源账本）与 `_run()`（要 items 才能
      数情绪覆盖、算 heat）。第二次走的是**同一个缓存**（键相同、TTL 未到）
      ⇒ 实测零网络、亚毫秒。TTL 被改小时两者都会真打，那时耗时翻倍 —— 已知代价。
    ★`skipped` 单独一栏：跳过（缺 symbol / 被 roles 过滤）既不是成功也不是失败。
      折进 ok 或不报是两种都见过的假绿。
    """
    h = news.health(symbol=_route(subject)[0], roles=roles, limit=limit, **kw)
    r = _run(subject=subject, limit=limit, roles=roles, **kw)
    srcs = h.get('sources') or []
    ran = [s for s in srcs if not s.get('skipped')]
    heat_d = _heat_from(r['subj'], r['srcs'])
    return {
        'ok': h.get('ok'),
        'ts': h.get('ts'),
        'subject': subject,
        'items_n': h.get('items_n'),
        'items_scanned_n': len(r['subj']),
        'sources': [_src_row(s) for s in srcs],
        'summary': {'sources_n': len(srcs),
                    'ran_n': len(ran),
                    'ok_n': len([s for s in ran if s.get('ok')]),
                    'fail_n': len([s for s in ran if s.get('ok') is False]),
                    'cached_n': len([s for s in ran if s.get('cached')]),
                    'skipped_n': len([s for s in srcs if s.get('skipped')]),
                    'ms_total': sum(s.get('ms') or 0 for s in srcs),
                    'ms_max': max([s.get('ms') or 0 for s in srcs] or [0])},
        'sentiment': _sentiment_prov(r['subj']),   # ★只报"谁给了上游分值"，不给结论
        'heat': {'score': heat_d['score'], 'components': heat_d['components'],
                 'count_raw': heat_d['count_raw'], 'max_heat': heat_d['max_heat'],
                 'multi_n': heat_d['multi_n'], 'sources_n': heat_d['sources_n'],
                 'top_terms': heat_d['top_terms'][:DIGEST_TERMS_MAX]},
        'layers': h.get('layers'),
        'notes': _notes_for(r['needles'], r['res']),
    }


# ── 自检（无网络、无副作用、可当判据跑）─────────────────────────────────
# ★这里**只有一个** selftest（曾经留过一份重构前的旧版在它上面，被同名覆盖成死代码，
#   且它引用的 `_selftest_timeline`/`_digest_pure` 早已不存在 —— 谁去改那份都得踩空）。
#   判据本身要有判据守：下面 `_fixture` 等是它的夹具，线上内核与它共用同一份实现。

def _fixture(n):
    """造 n 条确定性的假 item（已整形）。标题带序号，方便判"有没有被切"。"""
    out = []
    for i in range(n):
        out.append(_shape({
            'title': '测试条目编号%03d 关于某个事件的完整标题' % i,
            'excerpt': 'x' * 40, 'source': 'src%d' % (i % 5),
            'source_label': '源%d' % (i % 5),
            'ts': '2026-09-%02d 10:%02d:00' % (1 + i % 28, i % 60),
            'ts_kind': 'upstream', 'url': 'http://e/%d' % i,
            'heat': 1 + (i % 3), 'tags': ['标签%d' % (i % 4)],
            'related': [], 'sources': [], 'seen_in': ['src%d' % (i % 5)],
            'sentiment_raw': None}))
    return out


def _fake_meta():
    return {'ok': True, 'ts': '2026-09-22T18:00:00+0800', 'subject': None,
            'subject_name': None, 'query': None,
            'degraded': [{'source': 'x', 'reason': 'y'}], 'notes': list(_NOTES)}


def _fake_srcs(n=5):
    return [{'id': 'src%d' % i, 'label': '源%d' % i, 'kind': 'akshare', 'role': 'news',
             'scope': 'global', 'ok': True, 'n': 10, 'ms': 100, 'err': None,
             'cached': False, 'skipped': None} for i in range(n)]


def selftest():
    """不碰网络的形状自检。返回 (ok, [失败说明...])。

    ★为什么要有它：本模块的契约（键名、sentiment_raw 不许动、摘要不超预算）
      全都是"结构上的承诺"，而结构上的承诺**没有回归判据就会漂**。
    ★为什么是"形状自检"而不是"采集自检"：采集要真网络，判据不能依赖网络
      （本仓库对判据的纪律）。网络那部分靠 `health()` 如实报，不在这里假装。
    ★`_timeline_from` / `_digest_from` 是线上 `timeline()` / `digest()` 的**同一个内核**
      ⇒ 这里测的就是线上那条路，不存在"自检测的是另一份实现"。
    """
    fails = []

    def chk(cond, msg):
        if not cond:
            fails.append(msg)

    def section(name, fn):
        """★每节各自兜异常：自检**自己**崩掉（比如某个键被人删了导致 KeyError）
          必须变成一条**红的失败行**，不能让异常冒出去。
          冒出去的后果是调用方拿到 traceback 而不是 (False, [...]) ——
          那看起来像"自检工具坏了"，而不是"被测代码坏了"，归因就错了一层。"""
        try:
            fn()
        except Exception as e:            # noqa: BLE001 —— 见上
            fails.append('自检第 %s 节抛异常：%s: %s' % (name, type(e).__name__, e))

    def s1():
        tb = _tools_budget()
        chk(tb is not None, '读不到 tools.SUMMARY_BUDGET（tools.py 路径或 AST 形状变了？）')
        chk(tb is None or tb == DIGEST_BUDGET,
            'DIGEST_BUDGET=%r 与 tools.SUMMARY_BUDGET=%r 漂了' % (DIGEST_BUDGET, tb))

    def s2():
        # sentiment_raw 红线：上游给的值必须**原样**带出；上游没给必须 None
        raw = {'src': 'jin10.weibo_nlp', 'value': 1.23, 'label': None, 'upstream_def': 'x'}
        a = _shape({'title': 'T', 'source': 's1', 'sentiment_raw': raw})
        chk(a.get('sentiment_raw') == raw, 'sentiment_raw 被改写了（红线）')
        chk(a.get('sentiment_raw') is raw, 'sentiment_raw 不是同一个对象（被复制/换算了）')
        b = _shape({'title': 'T', 'source': 's1'})
        chk(b.get('sentiment_raw') is None, '没有上游分值却生成了一个（红线）')
        # 上游给了 0 这类"假值"也不能被当成"没给"而丢掉
        z = _shape({'title': 'T', 'source': 's1', 'sentiment_raw': {'value': 0}})
        chk(isinstance(z.get('sentiment_raw'), dict), '上游给的 0 值被当成"没给"丢掉了')

    def s3():
        # 契约的键**永远都在**（哪怕一个源都没配上）
        empty = _empty_collect()
        for k in ('ok', 'ts', 'subject', 'items', 'heat', 'collected', 'degraded', 'notes'):
            chk(k in empty, 'collect() 少键：%s' % k)
        for k in ('score', 'sources_n', 'top_terms'):
            chk(k in (empty.get('heat') or {}), 'collect().heat 少键：%s' % k)
        for k in ('id', 'ok', 'n', 'ms', 'err'):
            chk(k in _fake_srcs(1)[0], 'collected.sources[] 少键：%s' % k)
        # 契约要求 item 上必须有这 8 个字段（用户原话的结构）
        for k in ('id', 'title', 'source', 'ts', 'url', 'heat', 'sentiment_raw', 'tags'):
            chk(k in _fixture(1)[0], 'item 少键：%s' % k)

    def s4():
        # heat 的 score 必须等于分量加权和（可复核），且分量都在 [0,1]
        h = _heat_from([_shape({'title': 'A', 'source': 's1', 'heat': 2, 'tags': ['x']}),
                        _shape({'title': 'B', 'source': 's2', 'heat': 2, 'tags': ['x']})],
                       _fake_srcs(2), total_n=2)
        comp = h.get('components') or {}
        for k in ('resonance', 'volume', 'breadth'):
            chk(k in comp, 'heat.components 少键：%s' % k)
        if all(k in comp for k in HEAT_W):
            want = sum(HEAT_W[k] * comp[k] for k in HEAT_W)
            chk(abs((h.get('score') or 0) - want) < 1e-3,
                'heat.score != 分量加权和（%r vs %r）' % (h.get('score'), want))
        for k, v in comp.items():
            chk(isinstance(v, (int, float)) and 0.0 <= v <= 1.0,
                'heat 分量 %s=%r 不在 [0,1]' % (k, v))
        # ★单源场景不许出现"假共振"
        one = _heat_from([_shape({'title': 'A', 'source': 's1', 'heat': 1})],
                         _fake_srcs(1), total_n=1)
        chk((one.get('components') or {}).get('resonance') == 0.0,
            '只有一个源却报出了共振（假高峰）')
        # ★volume 必须跟着 count_raw 走，不跟着传入的 items 条数走（尺子不许跟着读数走）
        v2 = _heat_from([], _fake_srcs(1), total_n=VOLUME_FULL)
        chk((v2.get('components') or {}).get('volume') == 1.0, 'volume 没跟着 count_raw 走')
        chk(v2.get('sources_n') == 0, 'sources_n 数的应该是"真正出数的源"，不是"配了几个源"')
        # ★只有一个源在跑时 resonance 恒 0（自说自话不是共振）
        chk((_heat_from([_shape({'title': 'A', 'source': 's1', 'heat': 3})],
                        _fake_srcs(1), 3).get('components') or {}).get('resonance') == 0.0,
            '单源多条目被算成了共振')

    def s5():
        # 时间轴：升序、无 ts 的不进主序列且被计数、ts_kind 带出来
        mix = _fixture(3) + [_shape({'title': '没有时间的条目', 'source': 'src9'})]
        tl = _timeline_from(mix, TIMELINE_MAX, True, _fake_meta())
        chk(tl.get('n') == 3, '时间轴条数错：%r（应为 3，无 ts 的不进主序列）' % (tl.get('n'),))
        chk(tl.get('undated_n') == 1, 'undated_n 错：%r' % (tl.get('undated_n'),))
        ev = tl.get('events') or []
        ts_seq = [e.get('ts') for e in ev]
        chk(ts_seq == sorted(ts_seq), '升序时间轴不是升序：%r' % (ts_seq,))
        chk(all('ts_kind' in e for e in ev), '时间轴条目没带 ts_kind')
        # ★行上的多源计数只能叫 `heat`：`sources_n` 在顶层已是"几个源出了数"，
        #   两义同名读错了不报错（这条判据钉住名字，钉的不是值）。
        chk(all('heat' in e for e in ev), '时间轴条目缺 heat')
        chk(not any('sources_n' in e for e in ev),
            '时间轴条目用了 sources_n —— 与顶层的 sources_n（源数）同名不同义')
        span = tl.get('span') or {}
        chk(span.get('from') and span.get('from') <= span.get('to'), 'span 不对：%r' % (span,))

    def s6():
        # 摘要预算：小输入不剪；大输入必须被剪到预算内；risk 在；没有情绪结论
        d_small = _digest_from(_fixture(3), _fake_srcs(2), 3, DIGEST_BUDGET, _fake_meta())
        chk(d_small.get('dropped_n') == 0, '小输入被剪了（%r 条）' % (d_small.get('dropped_n'),))
        chk(not d_small.get('over_budget'), '小摘要报 over_budget')
        big = _fixture(78)
        # ★预算**扫一遍**，只赌一个预算值等于没量（同一个常量可能恰好在某一点对）：
        #   契约是"要么塞进预算，要么把 over_budget 标出来"，两件事必须有一件发生。
        for b in (400, 800, 1500, 2000, 3000, DIGEST_BUDGET):
            d = _digest_from(big, _fake_srcs(5), 78, b, _fake_meta())
            n = d.get('bytes') or 0
            chk(n <= b or d.get('over_budget'),
                '预算 %d：落出 %d 字节却**没标** over_budget（尺子量的是另一份 payload）' % (b, n))
            if b >= 1500:
                chk(not d.get('over_budget'),
                    '预算 %d 就塞不下了（%d 字节）—— 该调的是常量，不是判据' % (b, n))
        d_big = _digest_from(big, _fake_srcs(5), 78, 2000, _fake_meta())
        chk((d_big.get('dropped_n') or 0) > 0,
            '大输入一条没剪 ⇒ 预算没起作用（这条判据是空的）')
        chk('不构成投资建议' in str(d_big.get('risk')), 'risk 里没有「不构成投资建议」')
        chk(set(d_big.get('sentiment') or {}) == {'items_with_raw_n', 'upstream'},
            'sentiment 长出了结论性字段：%r（本层不许给情绪结论）'
            % (sorted(d_big.get('sentiment') or {}),))
        # ★剪的是**尾巴**不是字符串：留下的每条标题都必须与原文逐字相同。
        #   ★★这一段必须**非空**才有意义：预算紧到把 `top` 弹光时，`all(... for row in [])`
        #   恒真 —— 判据会当着人的面空转（实测：预算 2000 时 top_n=0，那条断言恒绿，
        #   我拿"把标题截成半句"的注入打它，**打不红**）。所以：
        #   ①用一个"一定剪过、又一定还剩明细"的预算（实测本机 2026-09-22：
        #     budget 3000 ⇒ dropped 16 / top_n 4；budget ≥4000 ⇒ top_n 10）；
        #   ②先断言"确实剪过"和"确实还剩"，③再断言剩下的标题完好。
        #   预算/夹具若变了，先红的是①②，红字会说明是"这段判据空转了"而不是"代码坏了"。
        d_tail = _digest_from(big, _fake_srcs(5), 78, 3000, _fake_meta())
        top = d_tail.get('top') or []
        chk((d_tail.get('dropped_n') or 0) > 0, '这段判据要求"剪过"，却一条没剪 ⇒ 它测不到剪的行为')
        chk(len(top) > 0, 'top 被剪光了 ⇒ 下面"标题完好"是空转的（预算或夹具变了？）')
        titles = set(it['title'] for it in big)
        chk(all(row.get('title') in titles for row in top),
            '摘要里的标题被切过（半句话看起来像一条真新闻）')
        chk(len(top) <= DIGEST_TOP_MAX, '摘要明细超过 DIGEST_TOP_MAX')
        chk(all(row.get('ts') for row in top), '摘要明细里有不带 ts 的条目')
        # ★`headline` 必须**在自己明细里**：`_fit` 砍尾后若还指着被弹掉的那条，
        #   摘要就自相矛盾（实测预算 2000 时 top_n=0 而 headline 仍是个具体标题）。
        for b in (2000, 3000, DIGEST_BUDGET):
            dd = _digest_from(big, _fake_srcs(5), 78, b, _fake_meta())
            hl = dd.get('headline')
            chk(hl is None or any(hl is r for r in (dd.get('top') or [])),
                '预算 %d：headline 不在自己的 top 里 ⇒ 摘要自相矛盾' % b)

    section('1 预算漂移', s1)
    section('2 sentiment_raw 红线', s2)
    section('3 契约键齐全', s3)
    section('4 heat 可复核', s4)
    section('5 时间轴', s5)
    section('6 摘要预算', s6)
    return (not fails), fails


def _empty_collect():
    """selftest 用：`collect()` 的键集合，不采网络（形状契约的空态）。"""
    return {'ok': True, 'ts': '', 'subject': None, 'subject_name': None, 'query': None,
            'items': [], 'subject_items_n': 0,
            'heat': _heat_from([], [], 0), 'sentiment': _sentiment_prov([]),
            'collected': {'sources': []}, 'degraded': [], 'notes': []}

