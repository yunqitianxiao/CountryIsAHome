#!/usr/bin/env python3
"""「问问看」那排按钮的内容：**每轮现推**，不是写死的四条。

原来是 `ChatCard.tsx` 里一个 `QUICK` 常量，从打开到关掉一个样。问题不是"不好看"，
是**它跟用户刚问的东西没有任何关系** —— 用户问完可转债，下面还是"茅台最近走势怎么样"。

★为什么这件事必须在**服务端**做（三条路都试过，只有这条成立）：

  - **让大模型生成**：多一次调用，而且它**会编**（编一个词表里根本没有的词，
    用户点进去图谱里查不到）—— 这一站最忌讳的就是编。
  - **前端自己算**：要拉 `graph_data.json`（256KB）而且**是异步的**。而渲染层探针
    只在**同一个同步 tick** 里量得到东西：异步不是让判据变红，是让判据**消失**
    （这条纪律从 index.html 时代立到现在，换 SPA 时原样继承了）。
  - **服务端算**：KB 本来就在进程内存里（`tools.search_terms` 用的就是它），
    结果是**同步**流进 SSE 的，判据可以直接读事件流 —— 和 `.tchip` 是同一种形状。

★三条纪律（每条都有判据，不是口号）：

  1. **只从这一轮真发生过的事里取。** 候选只来自工具真查过的词条/标的。
     不生成自由文本 —— 所以每条**都带来源**（`terms` / `symbol`），
     判据靠它查"这词在图谱里真有吗""标签和来源对不对得上"。
  2. **永不空。** 推不出更好的就用兜底那四条补齐。按钮行空着比"不更新"更坏：
     用户没得点，而且是静默的。
  3. **取不到就降级，不报错。** 图谱没加载、词条查不到 —— 一律退回兜底。
"""

import graph_kb

MAX = 4

# 兜底：就是原来 `ChatCard.tsx` 里那份常量，一个字没改（前端 `web/src/quick.ts` 同文案）。
# ★这是**唯一**不参与"来源核对"的一批 —— 它们是固定文案，不是从数据里推的。
DEFAULT = [
    {'label': '什么是可转债', 'ask': '什么是可转债'},
    {'label': '基金和股票有什么区别', 'ask': '基金和股票的区别'},
    {'label': '茅台最近走势怎么样', 'ask': '茅台最近走势'},
    {'label': '沪深300接下来会怎么走', 'ask': '沪深300接下来会怎么走'},
]


def _graph():
    """拿 KB。★加载失败就返回 None —— 建议是锦上添花，**不许**因为它把整轮回答搞崩。"""
    try:
        return graph_kb.kb()
    except Exception:                            # noqa: BLE001
        return None


def _contrast(k, term):
    """词条在「辨析」里对着谁。只收图谱里真有的名字。"""
    b = k.brief(term)
    if not b:
        return None
    for other in (b.get('contrast_with') or []):
        if isinstance(other, str) and other in k.terms:
            return other
    return None


def items(used=None):
    """used = {'terms': [...], 'symbols': [{'name','code'}], 'predicted': bool}。

    返回 [{'label','ask', 可选 'terms': [...], 可选 'symbol': ...}, ...]，长度 1..MAX。
    """
    used = used or {}
    k = _graph()
    out = []

    def add(label, ask, **src):
        key = ask.strip()
        if not key or any(x['ask'] == key for x in out):
            return
        item = {'label': label, 'ask': ask}
        item.update({kk: vv for kk, vv in src.items() if vv})
        out.append(item)

    # ① 这一轮跑过预测 —— 用户最可能接着问的就是"这数怎么来的""它准吗"。
    #    ★这两条正是页面上已经写着答案的东西（实测数字就在风险提示里），
    #      所以它们不是随口一问，是**能把用户引到那句实测数字上**的入口。
    if used.get('predicted'):
        add('这条预测是怎么算出来的', '这条预测是怎么算出来的')
        add('Kronos 历史上准不准', 'Kronos 历史上准不准')

    # ② 这一轮查过标的，**且没跑预测**。名字用工具返回的那个（`summary['name']`），
    #    不是用户敲的串 —— 用户可能敲的是 "600519"，而建议里该出现的是"贵州茅台"。
    #    ★"没预测"这个条件是必要的：刚预测完同一个标的，"它接下来会怎么走"就是
    #      用户**刚刚看到的那条答案**，再摆一个按钮问一遍是纯噪音 ——
    #      而且会把 4 个位置全占满，把下面更有信息量的词条追问挤掉。
    for s in ([] if used.get('predicted') else (used.get('symbols') or [])):
        nm = (s or {}).get('name') or (s or {}).get('code')
        if not nm:
            continue
        code = (s or {}).get('code') or nm
        add('%s接下来会怎么走' % nm, '%s接下来会怎么走' % nm, symbol=code)
        add('%s最近波动大不大' % nm, '%s最近半年的波动' % nm, symbol=code)

    # ③ 这一轮查过词条 —— ★走图谱的「辨析」边，这正是知识图谱存在的意义：
    #    名词解释答完了，下一个真问题通常就是"它和那个有啥区别"。
    for t in (used.get('terms') or []):
        if k is None or t not in k.terms:
            continue                           # 图谱里没有 ⇒ 不当建议（也不编）
        other = _contrast(k, t)
        if other:
            add('%s和%s有什么区别' % (t, other), '%s和%s的区别' % (t, other),
                terms=[t, other])
        else:
            add('%s是什么' % t, t, terms=[t])

    # ④ 补齐：不够 MAX 条就用兜底填满。★**永不空** —— 这一步是纪律 2 的兑现处。
    for d in DEFAULT:
        if len(out) >= MAX:
            break
        add(d['label'], d['ask'])

    return out[:MAX]


if __name__ == '__main__':
    import json
    import sys
    demo = {'terms': ['可转债', '债券', '国债', '股票'],
            'symbols': [{'name': '沪深300', 'code': 'sh000300'}],
            'predicted': True}
    if len(sys.argv) > 1:
        demo = json.loads(sys.argv[1])
    print(json.dumps(items(demo), ensure_ascii=False, indent=2))
    print('--- 空输入（必须不空） ---')
    print(json.dumps(items({}), ensure_ascii=False))
