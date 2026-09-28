#!/usr/bin/env python3
"""建议体检：把一段理财建议拆成「它承诺了什么 / 红旗 / 该问的问题 / 判不了的」。

★为什么规则源是**图谱词条**，不是另写一张规则表：
  另写一张表就有了两份真源 —— 词条改了、表没改，两边说的不一样，而没有任何判据
  守得住（读者读哪份取决于他先 grep 到什么）。这里每条规则都带 `term`，它必须是
  图谱里**真实存在的词条名**；判据拿它去查图谱，查不到就红。于是"不编"这件事
  是**结构上**成立的，不是靠自觉。

★三条纪律（照 `agent/suggest.py`，那边已经证明这套写法成立）：
  1. **只从图谱真有的词条取。** 每条红旗都带 `term`。
  2. **永不空。** 没检出红旗时**明说**"没检出"，不是返回空数组让页面自己猜 ——
     空白和"查过了、干净"在用户眼里长得一模一样。
  3. **取不到就降级，不报错。** 图谱加载失败 ⇒ 返回一个说清了的状态，不抛异常。

★豁免是这一页的灵魂（见 `_exempt`）：
  `保本保收益承诺` 词条原文写着"（银行存款除外）"。**见到"保本"就报红的工具是玩具**
  —— 只会报红的检查器等于一个恒真的告警。能说"这句不是红旗，因为银行存款除外"，
  才是"检验层"。这与 `agent/score.py` 的 `verdict()` 同源：**"无从判断"用 None，
  不用 False** —— 判不了和判无罪是两件事。

★判定走规则不走 LLM：
  LLM 只用来把这里算出的结论翻成人话（可选，没有密钥时本模块照出完整结果）。
  理由同 `/agents` 的风控官 —— 模型不能给自己发免责声明。
"""

import re

# ── 判定分三层，因为它们的确定性完全不同 ────────────────────────────────
#
#   ① RULES    —— 文本特征明确可判（"保本""老师带单"这类词出现在句子里就是证据）
#   ② 该问的   —— 收益率异常、底层不明。**不是罪证，是问题** —— 检验层不判罪，只提示该问什么
#   ③ 判不了的 —— 要不要查工商、牌照、有没有备案。这些**我手上没有事实**，明说判不了
#
#   ★第三层不是"暂时没实现"，是**设计**：一个声称能判断"这是不是非法集资"的工具
#     必须能查牌照，而本站没有那个数据源。硬判就是编。

# ① 可判规则。触发短语**全部取自词条自己的「例子」「提醒」字段**（见 `解释.md` N 类），
#    不是我编的 —— 出处写在每条的 `from` 里，判据可查。
RULES = (
    {
        'term': '保本保收益承诺',
        'triggers': ('保本', '保息', '保收益', '保证收益', '承诺收益',
                     '稳赚不赔', '稳赚', '无风险', '零风险', '绝对安全'),
        # ★豁免词表就是词条「辨析」那句："现在唯一能说'保本'的，只有银行存款
        #   （50 万以内）和国债。" —— 少一个豁免词，就是把合法产品误报成违规。
        'exempt': ('银行存款', '定期存款', '活期存款', '大额存单', '存单',
                   '国债', '储蓄国债', '存款保险', '结构性存款'),
        'why': '资管新规之后，任何**理财产品**说"保本保息"都是违规的。',
        'exempt_why': '银行存款（50 万以内）和国债是**唯二**能说"保本"的。',
        'from': 'N 类词条「保本保收益承诺」·辨析',
    },
    {
        'term': '杀猪盘',
        'triggers': ('稳赚', '内部渠道', '内部消息', '老师带单', '跟着操作就行',
                     '带你赚钱', '带你做单', '加我微信', '加个微信', '助理带'),
        'exempt': (),
        'why': '凡是"稳赚""内部渠道""老师带单""跟着操作就行"的，一律是骗局。',
        'exempt_why': None,
        'from': 'N 类词条「杀猪盘」·提醒',
    },
    {
        'term': '庞氏骗局',
        'triggers': ('拉人头', '发展下线', '层级返利', '推荐奖', '动态收益',
                     '静态收益', '介绍费', '下线'),
        'exempt': (),
        'why': '用后来人的钱付前面人的利息 —— 只要新人断档就立刻崩盘。',
        'exempt_why': None,
        'from': 'N 类词条「庞氏骗局」·提醒（"高息 + 拉人头 + 层级返利"）',
    },
    {
        'term': '飞单',
        'triggers': ('打给个人账户', '转到个人账户', '转给个人', '私人账户',
                     '私下转账', '现金给他', '打到我卡上'),
        'exempt': (),
        'why': '钱只能打给银行对公账户 —— 打给个人账户的，出了事银行不认账。',
        'exempt_why': None,
        'from': 'N 类词条「飞单」·提醒',
    },
)

# ③ 判不了的。**每条的 `why` 都要说清"我缺哪个事实"**，不是一句"暂不支持"。
CANNOT_VERIFY = (
    {'term': '非法集资',
     'why': '要看它**有没有吸收公众存款的资质**。本站查不到牌照，判不了 —— '
            '词条说得很清楚：营业执照≠金融牌照。',
     'from': 'N 类词条「非法集资」·误区'},
    {'term': '自融',
     'why': '要查借款方和平台**是不是同一批人**。这要工商信息层层穿透，本站没有这个数据源。',
     'from': 'N 类词条「自融」·误区（"最难识别的坑"）'},
    {'term': '资金池',
     'why': '要看合同里**有没有明确的底层资产**。这句得你自己翻合同 —— 本模块看不到它。',
     'from': 'N 类词条「资金池」·提醒'},
)

# ② 该问的问题。★这里**不设绝对阈值** —— 阈值一旦是我拍的数，就成了"我编的一个标准"。
#    用的是词条自己举过的例子当锚点：非法集资那条的例子是"年化 12%、保本保息"，
#    飞单那条的例子是"年化 9%"。到了这个量级，词条的原文就替我说了话。
RATE_ANCHORS = (
    (12.0, '非法集资', '年化 12% 保本保息'),
    (8.0, '飞单', '年化 9%'),
)

# 收益率抽取。中文理财话术里收益率的写法就这几种，逐个收全 ——
# ★漏一种的后果不是"少抽一条"，是**体检报告上少一行**，而用户看不出少了。
_RATE_PATTERNS = (
    (r'年化\s*(?:收益|利率|收益率)?\s*(\d+(?:\.\d+)?)\s*%', 1.0, '年化'),
    (r'年(?:息|利率|收益)\s*(\d+(?:\.\d+)?)\s*%', 1.0, '年化'),
    (r'半年(?:息|收益|利率)?\s*(\d+(?:\.\d+)?)\s*%', 2.0, '半年'),
    (r'月(?:息|收益|利率)\s*(\d+(?:\.\d+)?)\s*%', 12.0, '月息'),
    (r'(\d+(?:\.\d+)?)\s*分(?:利|息)', 12.0, '月息（几分利）'),
    (r'(\d+(?:\.\d+)?)\s*厘', 12.0, '月息（几厘）'),
    (r'日(?:息|收益|利率)\s*(\d+(?:\.\d+)?)\s*[%％]', 365.0, '日息'),
)

_SENT_SPLIT = re.compile(r'[。！？；;\n\r]+')


def _sentences(text):
    """切句。★必须切 —— 一整段一起判的话，"银行存款保本"会把"隔壁项目保本"洗白。"""
    return [s.strip() for s in _SENT_SPLIT.split(text or '') if s.strip()]


def _exempt(sent, rule):
    """这一句里有没有豁免词。返回命中的那个词，没有就 None。

    ★豁免只在**同一句内**生效 —— 跨句豁免等于把整段文本洗白。
    """
    for w in rule['exempt']:
        if w in sent:
            return w
    return None


def _graph():
    """拿 KB。★加载失败返回 None —— 体检是附加能力，**不许**因为它把整页搞崩。"""
    try:
        import graph_kb
        return graph_kb.kb()
    except Exception:                                    # noqa: BLE001
        return None


def rates(text):
    """把所有收益率抽出来，统一折算成年化。

    返回 [{'raw','pct','annual','kind'}...]，按原文出现顺序，同一条去重。
    """
    out, seen = [], set()
    for pat, mult, kind in _RATE_PATTERNS:
        for m in re.finditer(pat, text or ''):
            raw = m.group(0).strip()
            if raw in seen:
                continue
            seen.add(raw)
            try:
                pct = float(m.group(1))
            except (TypeError, ValueError):
                continue
            out.append({'raw': raw, 'pct': pct, 'annual': round(pct * mult, 2), 'kind': kind})
    return out


def promise(text):
    """它承诺了什么。**只陈述、不判罪** —— 判断在 flags / questions 那两层做。"""
    rs = rates(text)
    word_hits = [w for w in ('保本', '保息', '保收益', '保证收益', '稳赚', '稳赚不赔',
                             '无风险', '零风险', '绝对安全', '高收益', '高回报')
                 if w in (text or '')]
    return {'rates': rs,
            'annual_max': max([r['annual'] for r in rs], default=None),
            'words': word_hits}


def inspect(text, k=None):
    """主函数。返回结构化结论 —— ★不是一句话。

    打印成中文和渲染成页面是两种介质，各写各的措辞没问题；但**走哪个分支必须
    两边一致**（同 `score.verdict()` 的注释：否则同一份 JSON 会得出两个相反的结论）。
    """
    text = (text or '').strip()
    if not text:
        return {'ok': False, 'hint': '没给内容', 'source': 'rule'}

    k = k if k is not None else _graph()
    sents = _sentences(text)

    flags, notes = [], []
    for rule in RULES:
        # 词条必须真在图谱里 —— 拿不到图谱就跳过"查得到吗"这一步，但 term 照报。
        if k is not None and rule['term'] not in k.terms:
            notes.append({'kind': 'rule_orphan', 'term': rule['term'],
                          'why': '这条规则指的「%s」不在图谱里 —— 规则和词表脱节了。' % rule['term']})
            continue
        for s in sents:
            hit = [t for t in rule['triggers'] if t in s]
            if not hit:
                continue
            ex = _exempt(s, rule)
            if ex:
                # ★豁免生效 —— 这一句不报红旗。记成 note，页面上要**显示出来**：
                #   用户得看见"我们查了、这句不算"，而不是以为我们漏了。
                notes.append({'kind': 'exempt', 'term': rule['term'], 'sentence': s,
                              'exempt': ex, 'why': rule['exempt_why']})
                continue
            flags.append({'term': rule['term'], 'sentence': s,
                          'matched': hit, 'why': rule['why'], 'from': rule['from']})
            break        # 同一条规则在同一段里只报一次（取第一句命中的）

    # ③ 判不了的：★**只在检出红旗时才报**。
    #    「我这几件判不了」在一段干净文本上是纯噪音；而在有红旗的文本上，它是一句
    #    必要的诚实 —— 用户会以为"工具查过了、没事"，这一块正是用来打断那个错觉的。
    cannot = [dict(c) for c in CANNOT_VERIFY] if flags else []

    # ② 该问的问题：收益率到了词条举过的量级，就把词条那句话摆出来。
    #    ★这一层**跟着红旗走**：收益率高不高，只有在它同时说了"保本"之类的承诺时
    #      才是个问题 —— 单说"年化 2.6%"（银行存款）不需要问它凭什么（我们知道凭什么）。
    pr = promise(text)
    questions = []
    if flags and pr['annual_max'] is not None:
        for thr, src, example in RATE_ANCHORS:
            if pr['annual_max'] >= thr:
                questions.append({
                    'ask': '这个收益率凭什么这么高？钱从哪赚来的？',
                    'because': '你写的最高年化约 %.1f%%。图谱词条「%s」举的例子正是"%s"。'
                               % (pr['annual_max'], src, example),
                    'term': src,
                })
                break
        questions.append({
            'ask': '它写清楚了钱投到哪里去吗？',
            'because': '词条「资金池」的提醒：只写"投资于优质项目""由专业团队管理"'
                       '而不写具体投向的，都要警惕。',
            'term': '资金池',
        })
    for q in questions:
        if k is not None and q['term'] not in k.terms:
            q['term'] = None       # 词条不在图谱里就别链过去，但问题本身照问

    # 对照基准：★数字**不重算**，直接引用回测产物 —— 唯一实现，一个数只能有一个出处。
    baseline = None
    if any(w in text for w in ('预测', '会涨', '会跌', '接下来', '走势', '推荐买', '建议买', '荐股')):
        baseline = _baseline()

    # 空态文案**分三档**，不是一个兜底句。
    exempted = [n for n in notes if n.get('kind') == 'exempt']
    if flags or questions or cannot:
        empty_reason = None
    elif exempted:
        # ★第二档是**最容易漏掉**的一档：看见了、但判它不违规。
        #   它绝不能沿用第三档那句 —— 页面上会一边写"没检出红旗"、一边列着
        #   "「保本」因银行存款而豁免"，两句话当场打架；更糟的是它把这一页的
        #   灵魂说反了：豁免是**主动的判断**，不是没看见。
        #   末尾那句提醒是必要的诚实：豁免只看措辞，它证明不了对方真是银行存款。
        empty_reason = ('这段里有可疑措辞**被判成不算红旗** —— 命中的词落进了豁免'
                        '条件，下面列了是哪个词、因为什么。'
                        '★豁免只按**同一句**生效、也只看措辞：它证明不了对方真是'
                        '银行存款，这一点要你自己去核。')
    else:
        empty_reason = ('没检出红旗 —— 这**不等于**这条建议没问题，只等于它的措辞里'
                        '没有本站规则覆盖的那几类特征。')

    return {
        'ok': True,
        'source': 'rule',        # ★照 /agents 的风控官：规则算出来的，不假装是模型说的
        'promise': pr,
        'flags': flags,
        'exempt_notes': notes,
        'questions': questions,
        'cannot_verify': cannot,
        'baseline': baseline,
        'read_next': _read_next([f['term'] for f in flags] + [q.get('term') for q in questions
                                                             if q.get('term')]),
        'empty_reason': empty_reason,
    }


def _baseline():
    """不用模型会怎样。★读产物，不重算 —— 重算就是第二份真源。"""
    try:
        import json
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(os.path.dirname(here), 'kronos_accuracy.json'), encoding='utf-8') as f:
            d = json.load(f)
        t = d.get('total') or {}
        return {
            'model_hit_rate': (t.get('dir_band') or {}).get('hit_rate'),
            'model_ci95': (t.get('dir_band') or {}).get('ci95'),
            'always_up': (t.get('baseline_always_up') or {}).get('hit_rate'),
            'momentum': (t.get('baseline_momentum') or {}).get('hit_rate'),
            'n_origins': t.get('n_origins'),
            'measured_at': d.get('measured_at'),
            'why': '本站自己跑的模型（Kronos-small，%s 个起点）实测方向命中率 %s%%，'
                   '而"无脑喊涨"基线是 %s%% —— 统计上分不开。**任何声称能预测涨跌的'
                   '建议，都该拿这条对照来看。**' % (
                       t.get('n_origins'),
                       (t.get('dir_band') or {}).get('hit_rate'),
                       (t.get('baseline_always_up') or {}).get('hit_rate')),
        }
    except Exception:                                    # noqa: BLE001
        return None


def _read_next(terms):
    """该看哪几个词 —— 走 suggest 的现成通路，不另写一份推荐逻辑。"""
    try:
        import suggest
        return suggest.items({'terms': [t for t in terms if t]})
    except Exception:                                    # noqa: BLE001
        return []


if __name__ == '__main__':
    import json
    import sys
    demo = ' '.join(sys.argv[1:]) or (
        '隔壁老王介绍了一个项目，年化 12%、保本保息，拉人头还有返利，'
        '老师带单稳赚。钱打给个人账户就行。'
    )
    print('=== 正例 ===')
    print(json.dumps(inspect(demo), ensure_ascii=False, indent=1))
    print('\n=== 负例（银行存款，必须不报保本红旗）===')
    print(json.dumps(inspect('银行三年定期存款，年化 2.6%，保本。'), ensure_ascii=False, indent=1))
    print('\n=== 空态 ===')
    print(json.dumps(inspect('今天天气不错。'), ensure_ascii=False, indent=1))
