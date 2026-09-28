#!/usr/bin/env python3
"""把 词表-v1.md 解析成图层数据，并检查它作为「智能体能走的图」是否成立。

md 是唯一真源（人在这里砍词），本脚本是它到图数据的单向投影。
两份东西必须同源：在 md 里删一个词，而别的词还用「对比/前提」指着它，
图上就多一条断边 —— 这个脚本的工作就是把那种情况喊出来。

三层节点，是从数据里**推导**的，不是我手写白名单：
  词条    = md 表格的每一行（要写小白解释）
  分类节点 = 只出现在「属于」列的非常词条目标（是骨架，需要一句话说明，不写长解释）
  断边    = 「对比」「前提」指向了上面两者都不是的东西 —— 一律是 bug

四项检查：
  1. 分类小计 vs 实际行数      （md 内部一致）
  2. 重名
  3. 对比/前提 的断边          （图能不能走通）
  4. 前提图 / 属于图 是否有环  （教学路径必须是 DAG，有环就没有起点）
"""
import json
import re
import sys
from collections import defaultdict, Counter

SRC = '词表-v1.md'
CAT_RE = re.compile(r'^##\s+([A-Z])\.\s*(.+?)（(\d+)）\s*$')


def split_cell(c):
    c = c.replace('**', '').strip()
    if c in ('—', '-', ''):
        return []
    return [p.strip() for p in re.split(r'[、，,]', c) if p.strip() and p.strip() != '—']


def parse(path):
    cats, rows, cat = [], [], None
    for line in open(path, encoding='utf-8'):
        line = line.rstrip('\n')
        m = CAT_RE.match(line)
        if m:
            cat = {'code': m.group(1), 'name': m.group(2), 'claimed': int(m.group(3))}
            cats.append(cat)
            continue
        if not line.startswith('| '):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) < 5 or cells[0] == '词' or set(cells[0]) <= set('-: '):
            continue
        rows.append({'name': cells[0], 'cat': cat['code'], 'cat_name': cat['name'],
                     'belongs': split_cell(cells[1]), 'contrast': split_cell(cells[2]),
                     'prereq': split_cell(cells[3]), 'why': cells[4]})
    return cats, rows


def find_cycles(edges, nodes):
    """edges: {节点: [它指向的节点]}。返回环列表（每个环是一串节点）。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    stack, cycles = [], []

    def dfs(u):
        color[u] = GRAY
        stack.append(u)
        for v in edges.get(u, ()):
            if v not in color:
                continue
            if color[v] == GRAY:                       # 回边 ⇒ 环
                cycles.append(stack[stack.index(v):] + [v])
            elif color[v] == WHITE:
                dfs(v)
        stack.pop()
        color[u] = BLACK

    for n in nodes:
        if color[n] == WHITE:
            dfs(n)
    return cycles


def main():
    cats, rows = parse(SRC)
    names = {r['name'] for r in rows}
    print(f'词条 {len(rows)}   分类 {len(cats)}')

    # ── 1. 分类小计 ────────────────────────────────────────────
    cnt = Counter(r['cat'] for r in rows)
    bad = [c for c in cats if cnt[c['code']] != c['claimed']]
    print(f'\n[1] 分类小计  不符 {len(bad)} 处'
          + ('' if not bad else '  ' + '; '.join(
              f'{c["code"]} 标题{c["claimed"]}/实际{cnt[c["code"]]}' for c in bad)))

    # ── 2. 重名 ───────────────────────────────────────────────
    dup = [n for n, k in Counter(r['name'] for r in rows).items() if k > 1]
    print(f'[2] 重名      {len(dup)} 个' + (f'  {dup}' if dup else ''))

    # ── 3. 三层节点：分类层从「属于」列推导 ──────────────────────
    belongs_targets = {t for r in rows for t in r['belongs']}
    categories = sorted(belongs_targets - names)          # 只当骨架用，不写解释
    all_nodes = names | set(categories)

    print(f'\n[3] 节点分层')
    print(f'    词条       {len(names):>4}  （写小白解释）')
    print(f'    分类节点   {len(categories):>4}  （只说一句"这是什么类"，不写长解释）')
    print(f'    {"─" * 46}')

    broken = defaultdict(list)
    for r in rows:
        for col, label in (('contrast', '对比'), ('prereq', '前提')):
            for t in r[col]:
                if t not in all_nodes:
                    broken[(label, t)].append(r['name'])
    n_broken = sum(len(v) for v in broken.values())
    stats = {}
    for col, label in (('belongs', '属于'), ('contrast', '对比'), ('prereq', '前提')):
        tot = sum(len(r[col]) for r in rows)
        ok = sum(1 for r in rows for t in r[col] if t in all_nodes)
        stats[label] = (ok, tot)
        print(f'    {label}  引用 {tot:>4}  可落地 {ok:>4} ({ok/tot:>4.0%})')

    # 分类节点按被引用次数排序 —— 被指得多的，那个"一句话说明"越该先写
    ref = Counter(t for r in rows for t in r['belongs'] if t in categories)
    print(f'\n    分类节点被引用次数 top10: '
          + ', '.join(f'{t}({k})' for t, k in ref.most_common(10)))

    # ── 4. 断边 ───────────────────────────────────────────────
    print(f'\n[4] 断边：对比/前提 指向了既非词条也非分类节点的东西')
    if not broken:
        print('    无 ✓')
    else:
        print(f'    {n_broken} 条，涉及 {len(broken)} 个目标：')
        for (label, t), srcs in sorted(broken.items(), key=lambda kv: -len(kv[1])):
            print(f'      [{label}] {t:<14} ← {", ".join(srcs)}')

    # ── 5. 环 ─────────────────────────────────────────────────
    print(f'\n[5] 环检测（有环 = 智能体顺着走会绕圈，找不到起点）')
    for col, label in (('prereq', '前提'), ('belongs', '属于')):
        edges = {r['name']: [t for t in r[col] if t in all_nodes] for r in rows}
        cyc = find_cycles(edges, list(names))
        if not cyc:
            print(f'    {label}图 无环 ✓')
        else:
            print(f'    {label}图 {len(cyc)} 个环：')
            for c in cyc:
                print('      ' + ' → '.join(c))

    json.dump({'cats': cats, 'terms': rows, 'category_nodes': categories},
              open('terms.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'\n写出 terms.json')

    return 1 if (dup or bad or broken) else 0


if __name__ == '__main__':
    sys.exit(main())
