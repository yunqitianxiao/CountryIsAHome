#!/usr/bin/env python3
"""回测数字的唯一出口：`kronos_accuracy.json` → 页面上的那句话。

★ 为什么不把数字直接抄进 `index.html`：

抄进去的那天它就开始烂。`backtest_kronos.py` 再跑一次、数字全变了，页面上**还是旧的**
—— 而且**没人看得出来**：HTML 里分不出哪句是抄的、哪句是取的，两者长得一模一样。
"预测仅供参考"这句免责本身没有信息量（它无法证伪，写到哪儿都不担责任），
用户要的是**信几分**。那就只剩一条路：数字由数据算出来，页面只做投影。

所以页面里只留一个占位符 `<!--ACCURACY-->`，服务端**每次请求现读** JSON 填进去。
`sentence()` 是这个投影的唯一实现；`verify_agent.py` 里有判据把它钉在 JSON 上 ——
包括一条**对照组**：把 JSON 换成别的数，页面必须跟着变（不变就说明它是写死的）。

★ 拿到数字之前先看基线在不在。这条不是洁癖：**命中率单独看会骗人** ——
回测窗口里 60% 的日子在涨，无脑喊涨就有 60%。所以 `numbers()` 的规矩是
「要么连基线一起给，要么整句不给」：宁可说"没测过"，也不给一个没有对照的数字。

★ **区间覆盖率是同一个规矩的第二个实例，而且更尖锐。** 站上发的多采样区间是
"N 条路径的极差"，它覆盖新样本的概率是 (N−1)/(N+1) —— N=9 时**正好 80%**。
这是一个**定义出来的**数，不是量出来的：把 80% 印在页面上，读起来像"测过了"，
其实是"我们这么定义过"。所以这里多一条 `coverage()`：**只有 `--coverage` 那一遍
真把实测覆盖率写进 JSON 了，页面上才会出现覆盖率这几个字**；没量过就一句都不说。
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JSON_PATH = os.path.join(ROOT, 'kronos_accuracy.json')

# 页面上那个坑。改这里的话，`verify_agent.py` 里那条「占位符真的被替换了」的判据会红。
PLACEHOLDER = '<!--ACCURACY-->'

# ★ 判据靠这个 class 把整句**圈出来**再检查（只在那一句里数百分比，
#   不会把页面上别处的数字当成它）。所以这句话必须自带这个 span，别的都不算。
CLS = 'acc'

# 没跑过回测时的文案。★**一个数字都不许有** —— 编一个数字比不写更坏。
FALLBACK = ('回测还没跑过（<code>backtest_kronos.py</code>），所以这里不给数字 —— '
            '编一个数字比不写更坏。在那之前，请把下面的预测只当"形态示意"。')
FALLBACK_MARK = '回测还没跑过'

# 覆盖率那一句的标记。★它是"量过才准出现"这条纪律的抓手：`verify_agent.py` 把 JSON 里的
# `coverage` 那一块摘掉，页面上**这整句必须消失**；它要还在，就说明那句话不是从 JSON 来的。
COVER_MARK = '区间校准'
# ★结论**从置信区间读**，不从"实测和名义差了几个点"读：小样本下 70% 和 80% 的差
#   完全在抽样误差里，拿差值下结论等于把噪声当信号。
COVER_VERDICT = {
    'narrow': '实测覆盖率的上界都够不到名义值，说明<b>模型给自己的区间偏窄</b>'
              '（它对自己太有把握）',
    'wide': '实测覆盖率的下界就已经高于名义值，说明<b>区间偏宽</b>（偏保守）',
    'ok': '实测与名义的差还落在抽样误差里，<b>这一轮分不出偏宽还是偏窄</b>'
          '（这不等于"验证了 80%"）',
}


def raw():
    """读 JSON。读不到 / 不是合法 JSON → None。"""
    try:
        with open(JSON_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def numbers():
    """取出页面上要用的那几个数。缺任何一个（尤其基线）→ None（走降级文案）。

    这里刻意**宽进严出**：多一个字段不影响，少一个关键字段就当没有 ——
    半个证据比没有证据更坏，因为它看着像结论。
    """
    d = raw()
    if not isinstance(d, dict):
        return None
    t = d.get('total') or {}
    cfg = d.get('config') or {}
    try:
        strict = t['dir_strict']
        up = t['baseline_always_up']
        return {
            'markets': len(d.get('per_symbol') or {}),
            'origins': strict['n_called'],
            'horizon': cfg['pred_len'],
            'hit': strict['hit_rate'],
            'lo': strict['ci95'][0],
            'hi': strict['ci95'][1],
            'base_up': up['hit_rate'],
            'base_mom': t['baseline_momentum']['hit_rate'],
            'mape': t['mape_close_model'],
            'mape_flat': t['mape_close_flat'],
            'measured_at': d.get('measured_at', ''),
        }
    except (KeyError, IndexError, TypeError):
        return None


def coverage():
    """覆盖率校准那一块。★**没量过就返回 None** —— 那一句整句不出现。

    与 `numbers()` 的规矩一样宽进严出：缺任何一个关键字段都当"没量过"，
    半个证据比没有证据更坏（它看着像结论）。

    ★`nominal_pct`/`ci95` 是**必须**在场的：只有一个光秃秃的 `empirical_pct`
      发出去，读者没法判断"差了 10 个点"是模型不准还是样本太少。
    """
    d = raw()
    if not isinstance(d, dict):
        return None
    c = d.get('coverage')
    if not isinstance(c, dict):
        return None
    try:
        out = {'nominal': float(c['nominal_pct']),
               'n': int(c['n']),
               'emp': float(c['empirical_pct']),
               'lo': float(c['ci95'][0]), 'hi': float(c['ci95'][1]),
               'width': float(c['median_width_pct'])}
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    out['verdict'] = ('narrow' if out['hi'] < out['nominal']
                      else 'wide' if out['lo'] > out['nominal'] else 'ok')
    # 对照那一条（同样名义、但完全不看模型的纯波动率区间）**可有可无**：
    # 没有它这句话照样成立，只是少一个"模型是不是比瞎猜还窄"的参照。
    h = c.get('hist_band')
    out['hist'] = None
    if isinstance(h, dict):
        try:
            out['hist'] = {'emp': float(h['empirical_pct']),
                           'width': float(h['median_width_pct'])}
        except (KeyError, TypeError, ValueError):
            out['hist'] = None
    return out


def _cover_clause():
    """覆盖率那半句（没量过 → 空串）。"""
    c = coverage()
    if not c:
        return ''
    hist = ('；同样名义的纯波动率区间覆盖 %.1f%%（中位宽度 %.1f%%）'
            % (c['hist']['emp'], c['hist']['width'])) if c.get('hist') else ''
    return ('<br>%s：名义 %.0f%% 的多采样区间，实测覆盖 <b>%.1f%%</b>'
            '（%d 个起点，95%% 区间 %.1f–%.1f%%，中位宽度 %.1f%%）%s ⇒ %s。'
            % (COVER_MARK, c['nominal'], c['emp'], c['n'],
               c['lo'], c['hi'], c['width'], hist, COVER_VERDICT[c['verdict']]))


def sentence():
    """给页面的那句话（含外壳 span）。数据不全 → 降级文案。"""
    n = numbers()
    if not n:
        return '<span class="%s">%s</span>' % (CLS, FALLBACK)
    # ★ 措辞是有意这样的：模型确实比"无脑喊涨"高了几个点，但那几个点**落在置信区间里**
    #   （区间两端压着 50），所以能说的是"没证明它更强"，不是"它更强"。
    #   ★**这里原来抄着 `[48.8, 57.6]`** —— 从 2026-09-20 那次重跑起它就是错的
    #     （真源是 `[46.0, 58.5]`）。而它是**注释** ⇒ 没有任何判据够得着它，
    #     于是它烂了整整一轮没人发现，直到有人专门去查"CI 两端到底有没有判据守"。
    #     ⇒ 修法**不是把数字改对**，是**不再在这里复述**：要看两端去
    #     `kronos_accuracy.json`（唯一真源）—— 下面那句 format 印的是它，
    #     `verify_agent.py` 的 ③b 比的也是它。这就是"数字只有一个出口"：
    #     注释里第二份手抄，改对了也只是下一次烂掉的起点。
    #   收盘价误差那条更硬：比"假设不动"大一倍，说明它没有增加关于**价格水平**的信息。
    return ('<span class="%s">实测（%d 个市场 / %d 个起点 × %d 根视野）：'
            '方向命中率 <b>%.1f%%</b>，而同期「无脑喊涨」就有 <b>%.1f%%</b>'
            '（区间 %.1f–%.1f%%）；收盘价平均误差 <b>%.1f%%</b>，'
            '而「假设不动」只有 <b>%.1f%%</b>。'
            '<b>这份回测没能证明它比基线强</b> —— 请当形态参考，别当涨跌判断。%s</span>'
            % (CLS, n['markets'], n['origins'], n['horizon'],
               n['hit'], n['base_up'], n['lo'], n['hi'],
               n['mape'], n['mape_flat'], _cover_clause()))


def fill(html):
    """把页面里的占位符换成那句话。页面没写占位符 → 原样返回（判据会喊）。"""
    return html.replace(PLACEHOLDER, sentence()) if PLACEHOLDER in html else html


if __name__ == '__main__':
    print(sentence())
