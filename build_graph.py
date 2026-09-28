#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""词表 + 解释 → 图层数据 + 单文件交互页。

唯一真源仍是 md：`词表-v1.md`（骨架与关系）、`解释.md`（五栏内容）。
本脚本只做投影和检查，任何一处对不上就非零退出。

布局在本脚本里用 numpy 算好烘焙进数据，理由有三：
  1. 确定性 —— 同一个种子永远出同一张图，不随浏览器抖动
  2. 可验证 —— 我在没有浏览器的地方也能用数字检查布局（不重叠、不成团、成簇）
  3. 首屏即静止 —— 页面打开就是settled 的样子，没有一堆节点乱飞的动画
"""
import collections
import json
import os
import re
import subprocess
import sys

import numpy as np

SRC_EXPL = '解释.md'
TPL = 'graph_template.html'
OUT_JSON = 'graph_data.json'
OUT_HTML = 'graph.html'

FIELD_RE = re.compile(r'^- \*\*(一句话|例子|误区|辨析|提醒)\*\*\s*(（[^）]*）)?\s*[：:]\s*(.*)$')
VS_RE = re.compile(r'^- \*\*辨析\*\*\s*（vs\s*(.+?)）')
HEAD_RE = re.compile(r'^### (.+?)\s*$')

# 16 个二级类 → 7 个色族。16 种颜色人眼分不开，7 种可以；
# 具体类别仍然逐个用字母标在节点和面板上，颜色只负责"一眼看出这是哪一大片"。
FAMILY = {
    'A': 0, 'B': 0,                    # 地基与指标
    'C': 1, 'D': 1, 'E': 1,            # 存款与理财
    'F': 2, 'G': 2,                    # 基金与股票
    'H': 3, 'I': 3,                    # 保险与养老
    'J': 4, 'K': 4, 'M': 4,            # 负债·税·机构
    'L': 5, 'N': 5,                    # 策略与防坑
    'O': 6, 'P': 6,                    # 家庭与县域
}
FAMILY_NAME = ['地基与指标', '存款与理财', '基金与股票', '保险与养老', '负债·税·机构', '策略与防坑', '家庭与县域']

# 三种关系的期望边长（乘在理想边长 K 上）和画法
REL = {
    '属于': {'mult': 1.00, 'css': 'belong'},
    '前提': {'mult': 1.45, 'css': 'prereq'},
    '对比': {'mult': 1.75, 'css': 'contrast'},
}


# ─────────────────────────── 1. 骨架：跑 parse_terms.py ───────────────────────────
def load_terms():
    """复用已有的词表校验器。它非零退出 = 词表本身有问题，这里直接跟着退。"""
    subprocess.run([sys.executable, 'parse_terms.py'], check=True)
    return json.load(open('terms.json', encoding='utf-8'))


# ─────────────────────────── 2. 内容：解析 解释.md ───────────────────────────
def load_explanations():
    exp, cur = {}, None
    for line in open(SRC_EXPL, encoding='utf-8'):
        m = HEAD_RE.match(line)
        if m:
            cur = m.group(1)
            exp[cur] = {'f': {}, 'vs': []}
            continue
        if cur is None:
            continue
        m = VS_RE.match(line)
        if m:
            exp[cur]['vs'] = [x.strip() for x in re.split(r'[、，,]', m.group(1))]
        m = FIELD_RE.match(line)
        if m:
            exp[cur]['f'][m.group(1)] = ((m.group(2) or '') + m.group(3)).strip()
    return {k: v for k, v in exp.items() if v['f']}


# ─────────────────────────── 3. 检查：两层必须对得上 ───────────────────────────
def check(terms, exp, cat_names):
    names = {t['name'] for t in terms}
    cats = set(cat_names)          # 分类节点也是合法目标（和 parse_terms.py 的判据保持一致）
    contrast = {t['name']: t['contrast'] for t in terms}
    bad = []

    for n in names:
        if n not in exp:
            bad.append(f'词表有「{n}」但解释.md 里没有')
            continue
        miss = {'一句话', '例子', '误区'} - set(exp[n]['f'])
        if miss:
            bad.append(f'「{n}」缺字段 {"、".join(sorted(miss))}')
        if not re.search(r'\d', exp[n]['f'].get('例子', '')):
            bad.append(f'「{n}」例子栏没有数字')
        if contrast.get(n) and not (set(contrast[n]) & set(exp[n]['vs'])):
            bad.append(f'「{n}」表里声明对比 {contrast[n]}，正文辨析却写了 {exp[n]["vs"] or "（无）"}')
        for v in exp[n]['vs']:
            if v not in names and v not in cats:
                bad.append(f'「{n}」的辨析指向图谱外的「{v}」')
            elif v not in contrast.get(n, []):
                bad.append(f'「{n}」的辨析点名「{v}」，但表里没这条对比边')
    for n in exp:
        if n not in names:
            bad.append(f'解释.md 有「{n}」但词表里没有')
    return bad


# ─────────────────────────── 4. 布局 ───────────────────────────
def layout(pos0, edges, mass, seed=7, iters=1200):
    """Fruchterman-Reingold：FR 斥力 + 带自然长度的弹簧引力 + 向心重力。

    边用 (i, j, 期望边长) 表示。mass 大的节点（枢纽、分类节点）斥力更强。
    """
    rng = np.random.default_rng(seed)
    pos = pos0 + rng.normal(0, 1.0, pos0.shape)
    n = len(pos)
    K = np.sqrt(4_000_000.0 / n)          # 理想边长
    ei = np.array([e[0] for e in edges])
    ej = np.array([e[1] for e in edges])
    rest = np.array([e[2] for e in edges]) * K

    t = K * 0.9                            # 初始步长（温度）
    for _ in range(iters):
        delta = pos[:, None, :] - pos[None, :, :]
        d2 = (delta ** 2).sum(-1)
        np.fill_diagonal(d2, np.inf)
        d = np.sqrt(d2)
        # 斥力：k²/d，按质量加权；超过 4K 的截断（远距离不影响观感，省一半算力）
        unit = delta / d[:, :, None]                     # 单位方向，必须显式补轴
        rep = (K * K / d)[:, :, None] * unit * mass[:, None, None]
        rep[d > K * 4] = 0.0
        disp = rep.sum(1)
        # 引力：弹簧，有自然长度，所以边不会无限收缩
        dd = pos[ei] - pos[ej]
        dl = np.linalg.norm(dd, axis=1, keepdims=True) + 1e-9
        f = (dl - rest[:, None]) * 0.10 * (dd / dl)
        np.add.at(disp, ei, -f)
        np.add.at(disp, ej, f)
        # 重力：防止连通性弱的部分飘走
        disp -= pos * (0.012 * mass)[:, None]
        # 限幅（模拟退火）
        dlen = np.linalg.norm(disp, axis=1, keepdims=True) + 1e-9
        pos += (disp / dlen) * np.minimum(dlen, t)
        t *= 0.997
    return pos


def initial_positions(terms, cat_nodes):
    """按色族撒成 6 个簇，给迭代一个好起点（纯随机会慢很多且容易卡在坏局部解）。"""
    n = len(terms) + len(cat_nodes)
    pos = np.zeros((n, 2))
    for fam in range(len(FAMILY_NAME)):
        idx = [i for i, t in enumerate(terms) if FAMILY[t['cat']] == fam]
        if not idx:
            continue
        ang = 2 * np.pi * fam / len(FAMILY_NAME)
        c = np.array([np.cos(ang), np.sin(ang)]) * 900.0
        # 簇内按二级类再分小瓣，避免同类词条一开始就叠在一起
        sub = collections.defaultdict(list)
        for i in idx:
            sub[terms[i]['cat']].append(i)
        for j, (code, members) in enumerate(sorted(sub.items())):
            a2 = 2 * np.pi * j / max(len(sub), 1)
            cc = c + np.array([np.cos(a2), np.sin(a2)]) * 260.0
            pos[members] = cc
    # 分类节点放在"引用它的词条"的重心附近，让骨架一开始就在正确的位置
    for k, cn in enumerate(cat_nodes):
        pos[len(terms) + k] = cn['centroid']
    return pos


# ─────────────────────────── 5. 主流程 ───────────────────────────
def main():
    data = load_terms()
    terms, cat_names = data['terms'], data['category_nodes']
    exp = load_explanations()

    bad = check(terms, exp, cat_names)
    print(f'两层一致性检查：{"全绿 ✓" if not bad else f"★ {len(bad)} 处不通过"}')
    for b in bad[:20]:
        print('   ', b)
    if bad:
        return 1

    tnames = {t['name'] for t in terms}
    links = []
    for t in terms:
        for col, rel in (('belongs', '属于'), ('contrast', '对比'), ('prereq', '前提')):
            for tgt in t[col]:
                links.append((t['name'], tgt, rel))

    deg = collections.Counter()
    for s, t, _ in links:
        deg[s] += 1
        deg[t] += 1

    # 分类节点：不是新造的概念，而是"引用它的那些词条"的骨架节点
    members = collections.defaultdict(list)
    for t in terms:
        for b in t['belongs']:
            if b in cat_names:
                members[b].append(t['name'])

    # 索引：词条 0..T-1，分类节点 T..
    T = len(terms)
    idx = {t['name']: i for i, t in enumerate(terms)}
    for k, cn in enumerate(cat_names):
        idx[cn] = T + k

    # 分类节点的初值：放在它的大多数成员所属色族的中心
    cat_nodes = []
    for cn in cat_names:
        ms = members.get(cn, [])
        fams = [FAMILY[t['cat']] for t in terms if t['name'] in set(ms)]
        fam = collections.Counter(fams).most_common(1)[0][0] if fams else 0
        ang = 2 * np.pi * fam / len(FAMILY_NAME)
        cat_nodes.append({'id': cn, 'refs': len(ms),
                          'centroid': np.array([np.cos(ang), np.sin(ang)]) * 900.0})

    edges = [(idx[s], idx[t], REL[r]['mult']) for s, t, r in links]
    mass = np.ones(T + len(cat_names))
    for i in range(T):
        mass[i] = 1.0 + 0.06 * np.sqrt(deg[terms[i]['name']])
    for k in range(len(cat_names)):
        mass[T + k] = 2.2

    pos0 = initial_positions(terms, cat_nodes)
    pos = layout(pos0, edges, mass)

    # ── 布局数值自检：看不见图，就用数字判断它是不是一张能看的图 ──
    d = np.sqrt(((pos[:, None, :] - pos[None, :, :]) ** 2).sum(-1))
    np.fill_diagonal(d, np.inf)
    nn = d.min(1)
    fam_of = np.array([FAMILY[t['cat']] for t in terms] + [-1] * len(cat_names))
    intra, inter = [], []
    for a in range(len(pos)):
        for b in range(a + 1, len(pos)):
            if fam_of[a] < 0 or fam_of[b] < 0:
                continue
            (intra if fam_of[a] == fam_of[b] else inter).append(d[a, b])
    w, h = np.ptp(pos[:, 0]), np.ptp(pos[:, 1])
    # ★退化输入（坐标里有 NaN/Inf）必须单独认出来，别让它伪装成"有节点叠在一起"：
    #   NaN 让**所有比较返回 False**，于是上面那两行打印（写成"以好为真"）会印 ★，
    #   而下面那道闸若写成"以坏为真"（`if nn.min() <= 8 or ... : return 1`）读到的也是 False
    #   ⇒ **闸门 fail-open**：打印说"不合格、不输出"，它却照常输出。
    #   同一份数据、同一处代码，闸门与打印给出相反结论（实测：`nn.min() <= 8` 与
    #   `nn.min() > 8` 在 NaN 上**同时为 False**）。⇒ 判别法一行：**打印说 ★ ≠ 闸门拦住了。**
    #   ⇒ 闸门一律写成"以好为真"，并显式要求三个统计量都是有限数。
    #   （阈值一个没动 —— 这不是改尺子，是让尺子在退化输入上别与自己的打印打架。）
    finite = bool(np.isfinite(nn.min()) and np.isfinite(np.mean(intra))
                  and np.isfinite(np.mean(inter)))
    print(f'\n布局自检（{len(pos)} 个节点，{len(edges)} 条边）')
    print(f'  包围盒            {w:.0f} × {h:.0f}   宽高比 {w / h:.2f}   （只打印，不是闸）')
    print(f'  最近邻距离        中位 {np.median(nn):.1f}   最小 {nn.min():.1f}'
          + ('   ★ 不是有限数（坐标里有 NaN/Inf）' if not finite
             else '   ✓ 无重叠' if nn.min() > 8 else '   ★ 有节点叠在一起'))
    print(f'  同族平均距离      {np.mean(intra):.0f}')
    print(f'  异族平均距离      {np.mean(inter):.0f}'
          + ('   ★ 不是有限数' if not finite
             else '   ✓ 同类更近' if np.mean(intra) < np.mean(inter) else '   ★ 没有聚成簇'))
    # ── 闸门：只有下面这两条。上面那个宽高比、以及"中位最近邻"都**只是读数**，不是闸。──
    if not (finite and nn.min() > 8 and np.mean(intra) < np.mean(inter)):
        print('  ★ 布局不合格，不输出'
              + ('' if finite else '（最近邻/同异族距离不是有限数 —— 先查坐标里有没有 NaN/Inf）'))
        return 1

    nodes = []
    for i, t in enumerate(terms):
        e = exp[t['name']]
        nodes.append({
            'id': t['name'], 'kind': 'term', 'fam': FAMILY[t['cat']],
            'cat': t['cat'], 'catName': t['cat_name'],
            'deg': deg[t['name']], 'root': not t['prereq'],
            'x': round(float(pos[i, 0]), 2), 'y': round(float(pos[i, 1]), 2),
            'f': e['f'], 'vs': e['vs'],
        })
    for k, cn in enumerate(cat_nodes):
        nodes.append({
            'id': cn['id'], 'kind': 'cat', 'refs': cn['refs'],
            'x': round(float(pos[T + k, 0]), 2), 'y': round(float(pos[T + k, 1]), 2),
            'members': members.get(cn['id'], []),
        })

    out = {
        'families': [{'name': nm} for nm in FAMILY_NAME],
        'cats': [{'code': c['code'], 'name': c['name'], 'fam': FAMILY[c['code']]} for c in data['cats']],
        'nodes': nodes,
        'links': [{'s': s, 't': t, 'r': r} for s, t, r in links],
    }
    json.dump(out, open(OUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))

    tpl = open(TPL, encoding='utf-8').read()
    assert '/*__DATA__*/' in tpl, '模板里找不到 /*__DATA__*/ 占位'
    html = tpl.replace('/*__DATA__*/', json.dumps(out, ensure_ascii=False, separators=(',', ':')))
    open(OUT_HTML, 'w', encoding='utf-8').write(html)

    print(f'\n{len(terms)} 词条 + {len(cat_names)} 分类节点，{len(links)} 条边')
    # 报字节数，不报字符数：中文一个字符 3 字节，混着报会把体积说小三倍
    nbytes = len(html.encode('utf-8'))
    print(f'写出 {OUT_JSON}（{os.path.getsize(OUT_JSON) / 1024:.0f} KB）'
          f' 和 {OUT_HTML}（{nbytes / 1024:.0f} KB / {len(html)} 字）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
