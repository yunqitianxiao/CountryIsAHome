"""知识图谱检索层 —— 给智能体当「术语库」用的那一半。

数据来源是**现有产物** `graph_data.json`（build_graph.py 烤出来的），不碰 md 真源、
不重跑管线：改词表仍然是「只改 md、重跑脚本」那一条路，这里只是读。

为什么单独一个模块：智能体回答「什么是可转债」和「预测一下 600519」是两种活。
后者要跑模型，前者只要查表 —— 而且查表必须是**零成本、零等待**的（不能为了解释一个词
去调一次大模型或跑一次推理）。
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DATA = os.path.join(ROOT, 'graph_data.json')

# 用户提问里常见的「包装词」：问「什么是市盈率」和问「市盈率」，要落到同一个词条上。
# 不做分词——词表只有 255 个中文词，剥掉这些前后缀做子串匹配就够，且不会像分词那样引入新依赖。
_NOISE = re.compile(
    r'^(请问|请|帮我|我想|我要|能不能|可以|麻烦)?'
    r'(解释一下|讲讲|说说|介绍一下|科普一下|查一下|搜索)?'
    r'(什么是|什么叫|啥是|啥叫|何谓)?'
)
_TAIL = re.compile(r'(是什么意思|是啥意思|什么意思|是什么|是啥|呢|吗|吧|\?|？|。|！|!)+$')


def normalize(q):
    """把一句口语提问收敛成一个候选词串。"""
    s = (q or '').strip()
    s = re.sub(r'[\s　]+', '', s)
    s = _NOISE.sub('', s, count=1)
    s = _TAIL.sub('', s, count=1)
    return s


class GraphKB:
    """读一次，常驻内存。255 个词条，整份 terms.json 才 64KB，没有懒加载的必要。"""

    def __init__(self, path=DEFAULT_DATA):
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        self.families = [f['name'] for f in d['families']]
        nodes = d['nodes']
        self.terms = {n['id']: n for n in nodes if n['kind'] == 'term'}
        self.cats = {n['id']: n for n in nodes if n['kind'] == 'cat'}
        self.links = d['links']

        # 邻接表：两个方向都存。图是有向的（对比/前提指出去），但「谁提到我」对回答
        # 「还有哪些相关的」同样有用，所以反向边也留着，并记住关系名。
        self.out, self.inn = {}, {}
        for l in self.links:
            self.out.setdefault(l['s'], []).append((l['t'], l['r']))
            self.inn.setdefault(l['t'], []).append((l['s'], l['r']))

    # ── 检索 ────────────────────────────────────────────────────────────

    def search(self, query, limit=3):
        """按「像不像用户想问的那个词」排序，返回词条（不带图坐标）。"""
        q = normalize(query)
        if not q:
            return []
        scored = []
        qs = set(q)
        for name, t in self.terms.items():
            if name == q:
                s = 1000.0
            elif name in q:
                # 词被整段包含：越长越具体（「债券基金」比「债券」更像用户想问的）
                s = 500.0 + len(name)
            elif q in name:
                s = 400.0
            else:
                # 兜底：汉字重合度。中文没有词边界，字符集交并比是最稳的近似。
                # 只用来兜「用户打了个错别字/少打一个字」这类，权重压在整段匹配之下。
                #
                # ★这里有两条闸门，缺了就会把噪声当命中：只重合**一个**字就放行的话，
                #   「zzz不存在的词zzz」会命中「整存整取」（都含「存」），
                #   于是一个查不到的词被"编"出了一个答案 —— 比查不到坏得多。
                #   要求：至少重合 2 个字（单词查询除外），且覆盖查询的一半以上。
                inter = len(qs & set(name))
                s = 0.0
                if inter and (inter >= 2 or len(qs) == 1) and inter / len(qs) >= 0.5:
                    s = 200.0 * inter / max(len(set(name)), 1)
                    if len(name) > len(q) * 2:
                        s *= 0.5      # 别让「股票基金」抢走问「股票」的人
            if s > 0:
                scored.append((s, name))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [self.brief(n) for _, n in scored[:limit]]

    def brief(self, name):
        """词条的精简版：够大模型用，又不至于把 306 字节的中位长度撑成散文。"""
        t = self.terms.get(name)
        if not t:
            return None
        return {
            'name': t['id'],
            'cat': t['catName'],
            'family': self.families[t['fam']] if 0 <= t.get('fam', -1) < len(self.families) else None,
            'is_start': t.get('root', False),      # 无前提 ⇒ 教学起点，适合当第一句解释
            'fields': t.get('f', {}),              # 一句话/例子/误区/辨析/提醒
            'contrast_with': t.get('vs', []),
        }

    # ── 走图 ────────────────────────────────────────────────────────────

    def walk(self, term, relation=None, limit=10):
        """顺着「属于 / 对比 / 前提」走一步。relation=None 表示不限关系。"""
        name = self._resolve(term)
        if not name:
            return None
        label = {'belong': '属于', 'contrast': '对比', 'prereq': '前提'}
        want = label.get(relation, relation)
        out = []
        for target, rel in self.out.get(name, []) + self.inn.get(name, []):
            if want and rel != want:
                continue
            n = self.terms.get(target)
            if n:
                out.append({'name': target, 'relation': rel, 'kind': 'term',
                            'one_line': (n.get('f', {}).get('一句话') or '')[:60]})
            elif target in self.cats:
                out.append({'name': target, 'relation': rel, 'kind': 'category',
                            'members': self.cats[target].get('members', [])[:6]})
        # 去重（同一对词可能既有正向边又有反向边被走到）
        seen, uniq = set(), []
        for x in out:
            if x['name'] not in seen:
                seen.add(x['name'])
                uniq.append(x)
        return {'term': name, 'relation': want or '全部', 'neighbors': uniq[:limit]}

    def _resolve(self, term):
        """允许传口语串：先精确，再走一次检索取第一名。"""
        if term in self.terms:
            return term
        hit = self.search(term, limit=1)
        return hit[0]['name'] if hit else None

    # ── 给页面卡片用 ────────────────────────────────────────────────────

    def stats(self):
        return {'terms': len(self.terms), 'categories': len(self.cats),
                'links': len(self.links), 'families': self.families}

    def by_family(self):
        """六个色族各挑两个「起点词」，给主页面当入口芯片（挑 root 的：不需要前置知识）。"""
        out = []
        for i, fam in enumerate(self.families):
            members = [t['id'] for t in self.terms.values()
                       if t.get('fam') == i and t.get('root')]
            if len(members) < 2:
                members = [t['id'] for t in self.terms.values() if t.get('fam') == i]
            out.append({'family': fam, 'samples': members[:2]})
        return out


_KB = None


def kb():
    """进程内单例：这份数据是只读的、构建产物，重复加载纯属浪费。"""
    global _KB
    if _KB is None:
        _KB = GraphKB()
    return _KB


if __name__ == '__main__':
    import sys
    k = kb()
    print(json.dumps(k.stats(), ensure_ascii=False))
    for q in (sys.argv[1:] or ['什么是可转债', '市盈率', '涨停', '基金和股票啥区别']):
        hits = k.search(q)
        print('\n问：%s  → %s' % (q, [h['name'] for h in hits]))
        if hits:
            w = k.walk(hits[0]['name'])
            print('   走到：%s' % [(x['name'], x['relation']) for x in w['neighbors']])
