#!/usr/bin/env python3
"""冒烟验证：合成市场上跑 `scan()` / `anomaly_of()`，并把**真实返回的 JSON** 打出来。

用法（在项目根目录下）：
    .venv/bin/python agent/linkage/_probe.py            # 全部场景
    .venv/bin/python agent/linkage/_probe.py --json     # 额外把完整 JSON 落盘到 /tmp

★这个脚本**必须断言**，不能只打印。只打印的脚本"跑过了"和"跑对了"是两件事，
  而且它绿的时候什么也不说明（本项目的老账：只打印不断言 = 没跑）。
  断言失败 → 退出码非 0。

它验的是**合成市场上已知的真值**（见 `_synthmarket.ANOMALY_SPEC` / `LEADLAG`）：
  ① 注入的 6 只异动票有没有被检出（含方向）；
  ② 注入的领先-滞后有没有被找出来，**滞后对不对**（而且要双向都对：
     领先股看 peer 是"我领先"，滞后股看 peer 是"它领先"）；
  ③ 采集账本：失败有没有如实进账（用 `LICAI_LINKAGE_SYNTH_FAIL` 打掉一个源）；
  ④ JSON 安全（`allow_nan=False` 必须能过）；
  ⑤ 弱分析的边界：`note` 里不许出现建议性措辞。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
ROOT = os.path.dirname(AGENT)
for p in (AGENT, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ['LICAI_LINKAGE_SYNTH'] = '1'          # ★必须在 import linkage 之前
os.environ.setdefault('LICAI_LINKAGE_BUDGET', '80')

import linkage                                   # noqa: E402
from linkage import _num as L_num                # noqa: E402
from linkage import _synthmarket as S            # noqa: E402
from linkage import collect as C                 # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print('  %s %s%s' % ('✓' if cond else '✗', name, ('  ← %s' % extra) if extra else ''))
    return bool(cond)


def dump(obj, label, limit=2600):
    s = json.dumps(obj, ensure_ascii=False, indent=1, allow_nan=False)
    print('\n──── %s（%d 字节，截断到 %d）────' % (label, len(s.encode()), limit))
    print(s[:limit] + ('\n…（截断）' if len(s) > limit else ''))


def check_json_safe(obj, label):
    try:
        json.dumps(obj, ensure_ascii=False, allow_nan=False)
        return check('%s：JSON 安全（allow_nan=False 通过）' % label, True)
    except ValueError as e:
        return check('%s：JSON 安全' % label, False, str(e)[:120])


FORBID = ('建议关注', '可考虑', '值得布局', '可以买入', '推荐', '看多', '看空', '目标价',
          '预计将', '有望', '机会较大')


def check_no_advice(txt, label):
    hit = [w for w in FORBID if w in (txt or '')]
    return check('%s：没有建议性措辞' % label, not hit, '命中 %s' % hit if hit else '')


# ══ 场景 A：全市场 scan ═════════════════════════════════════════════════
def scenario_scan():
    print('\n══ 场景 A：scan(scope="all", top=20) ══')
    r = linkage.scan(scope='all', top=20)
    dump(r, 'scan() 返回（真实 JSON）')

    check('A1 ok=True', r.get('ok') is True, json.dumps(r.get('reason'), ensure_ascii=False))
    if not r.get('ok'):
        return r, None
    check_json_safe(r, 'A')
    check('A2 backend 是合成市场', 'SYNTH' in (r.get('backend') or ''), r.get('backend'))
    check('A3 snapshot 三键齐全',
          all(k in r['snapshot'] for k in ('ts', 'market', 'rows')), str(sorted(r['snapshot'])))

    src = r['collected']['sources']
    check('A4 collected.sources 每个源都带 id/ok/n/ms/err',
          bool(src) and all(set(('id', 'ok', 'n', 'ms', 'err')) <= set(s) for s in src),
          '%d 条' % len(src))
    mk_seen = {s['id'].split(':')[-1] for s in src if s['id'].startswith('realtime:')}
    check('A5 三个市场的快照都采了（cn/hk/us）', mk_seen == {'cn', 'hk', 'us'}, str(sorted(mk_seen)))
    check('A6 collected.markets == [cn,hk,us]', r['collected']['markets'] == ['cn', 'hk', 'us'])
    check('A7 行数自洽（rows == Σ rows_by_market）',
          r['collected']['rows'] == sum(r['collected']['rows_by_market'].values()),
          '%s vs %s' % (r['collected']['rows'], r['collected']['rows_by_market']))
    check('A8 预算被记账', r['collected']['budget']['used'] > 0, json.dumps(r['collected']['budget']))

    cs = r.get('cross_section') or {}
    check('A9 横截面分了市场', 'cn' in cs and cs['cn'].get('pct', {}).get('n', 0) >= 100,
          json.dumps({k: v.get('pct', {}).get('n') for k, v in cs.items()}))

    an = r['anomalies']
    check('A10 异动榜非空', len(an) > 0, '%d 只' % len(an))
    KINDS = set(__import__('linkage.anomaly', fromlist=['x']).KIND_ORDER)
    check('A11 标签都在封闭词表里', all(set(a['kinds']) <= KINDS for a in an),
          str(sorted({k for a in an for k in a['kinds']})))
    check('A12 每条都有 metrics 且 z_pct 非空',
          all(a['metrics'].get('z_pct') is not None for a in an))
    check('A13 榜单按 hot 降序',
          all((an[i]['hot'] or 0) >= (an[i + 1]['hot'] or 0) for i in range(len(an) - 1)))

    # ① 注入的异动票有没有被检出
    found = {a['code'] for a in an}
    for spec in S.ANOMALY_SPEC:
        check('A14 注入的 %s(%s, %+.2f%%) 被检出' % (spec['code'], spec['why'], spec['pct']),
              spec['code'] in found,
              '实际榜单：%s' % ','.join('%s(%s)' % (a['code'], a['metrics']['pct']) for a in an[:8]))
    up = [a for a in an if a['code'] == '600001']
    if up:
        m = up[0]['metrics']
        check('A15 600001 方向是 up、标签含 pct_spike',
              m['side'] == 'up' and 'pct_spike' in up[0]['kinds'], json.dumps(m, ensure_ascii=False))
    dn = [a for a in an if a['code'] == '000001']
    if dn:
        check('A16 000001（注入 -8.5%）方向是 down',
              dn[0]['metrics']['side'] == 'down', json.dumps(dn[0]['metrics'], ensure_ascii=False))

    # A17 量比：深挖过的应该有（量比要日线才算得出来）
    with_vr = [a for a in an if a['code'] in r['links'] and a['metrics'].get('vol_ratio') is not None]
    check('A17 深挖过的票算出了量比', bool(with_vr),
          json.dumps({a['code']: a['metrics']['vol_ratio'] for a in with_vr}))
    lead = [a for a in an if a['code'] == '600010']
    if lead:
        vr = lead[0]['metrics'].get('vol_ratio')
        check('A18 领先股的量比接近注入的 8x（>4）', vr is not None and vr > 4.0, str(vr))

    # A19 关联必须带证据串
    all_peers = []
    for code, lk in r['links'].items():
        check('A19 %s 的关联带证据串' % code, bool(lk.get('evidence')),
              json.dumps(lk.get('evidence', [])[:2], ensure_ascii=False))
        check('A20 %s 的板块里 pct 是数或 None（不是 0 顶替）' % code,
              all((b.get('pct') is None) or isinstance(b['pct'], (int, float))
                  for b in lk['boards']))
        for p in lk['peers']:
            check('A21 %s→%s：相关系数要么 None 要么带样本数' % (code, p['code']),
                  (p['rel_value'] is None) or (p['corr_n'] and p['corr_n'] >= 60),
                  'r=%s n=%s' % (p['rel_value'], p['corr_n']))
            all_peers.append((code, p))

    # A21b ★**不许出现"够不着门槛却报了滞后"的 peer**（全局一条，不是逐条刷屏）：
    #   这条判据是那 5 个 `✗ B5/B9` 换来的 —— 先前的版本会把"峰值相关 -0.17"写成
    #   "600001 领先 600011 20 分钟"。门槛本身是政策（`LL_MIN_R`），
    #   但"报出来的 lag 必须过硬门槛"是**结构**，所以钉在这里。
    import linkage.relate as R
    bad_lag = [('%s→%s' % (c, p['code']), p['lag_min'], p['lag_r'], p['lag_n'])
               for c, p in all_peers
               if p['lag_min'] is not None and not R._lag_is_real(p['lag_r'], p['lag_n'])]
    check('A21b 报出来的 lag_min 全部过了峰值相关门槛', not bad_lag, str(bad_lag))
    # A21c 报出来的 lag_min 必须同时在证据串里有一句"领先 N 分钟"（不留孤儿数）
    ev_all = [e for lk in r['links'].values() for e in (lk.get('evidence') or [])]
    claimed = [p for _, p in all_peers if p['lag_min'] is not None]
    check('A21c 每个报出来的滞后都在证据串里有对应的一句话', all(
        any('领先' in e and p['code'] in e for e in ev_all) for p in claimed),
        '%d 个滞后' % len(claimed))

    # A25 深挖过的 peer 要带上**它自己的量比**（日线都已经取到手了，这个数是白捡的）
    with_vr = [(c, p['code'], p['vol_ratio']) for c, p in all_peers if p['vol_ratio'] is not None]
    check('A25 深挖过的 peer 算出了量比', len(with_vr) >= 5, str(with_vr[:4]))
    check('A25b 量比的 partial 标记是布尔（不是 None / 不是字符串）',
          all(isinstance(p['vol_ratio_partial'], bool) for _, p in all_peers
              if p['vol_ratio'] is not None),
          str([p['vol_ratio_partial'] for _, p in all_peers][:6]))

    # A26 结论性字段不许出现"建议"字样（证据链也一起查：它是给人看的正文）
    check_no_advice(' '.join(ev_all), 'A26 evidence 串')

    check_no_advice(r.get('note'), 'A22 note')
    check('A23 风险提示原样含「不构成投资建议」',
          '不构成投资建议' in (r.get('risk_note') or ''), r.get('risk_note'))
    check('A24 note 是短的（<220 字）', len(r.get('note') or '') < 220,
          '%d 字' % len(r.get('note') or ''))
    return r, an


# ══ 场景 B：单只票 + lead-lag 真值 ══════════════════════════════════════
def _lag_of(res, peer_code):
    for p in (res.get('links', {}).get(res.get('code'), {}) or {}).get('peers', []):
        if p['code'] == peer_code:
            return p
    return None


def scenario_anomaly_of():
    print('\n══ 场景 B：anomaly_of()（单只票，验 lead-lag 真值）══')
    leader, follower = S.LEADLAG['leader'], S.LEADLAG['follower']
    want = S.LEADLAG['lag_min']

    rl = linkage.anomaly_of(leader)
    dump(rl, 'anomaly_of(%s) 返回（真实 JSON）' % leader)
    check('B1 anomaly_of(领先股) ok=True', rl.get('ok') is True,
          json.dumps(rl.get('reason'), ensure_ascii=False))
    if not rl.get('ok'):
        return None, None
    check_json_safe(rl, 'B')
    # ★领先股命中的是**量能**，不是价格：它的注入涨跌幅是 +4.60%，而这一天
    #   合成市场的横截面 sd 是 2.5%（板块因子当天开得大），所以 z_pct 只有 1.1。
    #   这不是判据放宽 —— 判据本来就是"它有没有被检出"，而"价格 z 大不大"是
    #   市场状态说了算的；把这条断言写成"必须含 pct_spike"等于把一条
    #   **对市场的假设**混进真值检查里（合成市场不满足它，真市场也可能不满足）。
    check('B2 领先股被检出（量能族）',
          bool(rl.get('anomaly')) and 'volume_spike' in rl['anomaly']['kinds'],
          json.dumps(rl.get('anomaly'), ensure_ascii=False))
    if rl.get('anomaly'):
        m = rl['anomaly']['metrics']
        check('B2b 领先股的 z_pct 只有 %.2f（+4.6%% 的涨幅在当日截面里不算异动）' % (m.get('z_pct') or 0),
              (m.get('z_pct') or 0) < 2.5 and m.get('z_vol') is not None and m['z_vol'] >= 3.0,
              'z_pct=%s z_vol=%s' % (m.get('z_pct'), m.get('z_vol')))
    check('B3 横截面有分母（z 才算得出来）',
          rl['cross_section'].get('cn', {}).get('pct', {}).get('n', 0) >= 100)
    pl = _lag_of(rl, follower)
    check('B4 领先股的 peer 里有滞后股 %s' % follower, pl is not None)
    if pl:
        check('B5 领先股看它：lag_min == +%d（我领先）' % want,
              pl.get('lag_min') == want and pl.get('lead_side') == 'self',
              'lag_min=%s lead_side=%s r=%s n=%s'
              % (pl.get('lag_min'), pl.get('lead_side'), pl.get('lag_r'), pl.get('lag_n')))
        check('B6 这一对的峰值相关够高（>0.5）', (pl.get('lag_r') or 0) > 0.5, str(pl.get('lag_r')))
    check('B7 领先股与滞后股有日线级相关（同板块，r>0.4）',
          bool(pl) and (pl.get('rel_value') or 0) > 0.4,
          'r=%s n=%s' % (pl.get('rel_value') if pl else None, pl.get('corr_n') if pl else None))

    rf = linkage.anomaly_of(follower)
    dump(rf, 'anomaly_of(%s) 返回（真实 JSON）' % follower)
    pf = _lag_of(rf, leader)
    check('B8 滞后股的 peer 里有领先股 %s' % leader, pf is not None)
    if pf:
        check('B9 滞后股看它：lag_min == -%d（它领先）' % want,
              pf.get('lag_min') == -want and pf.get('lead_side') == 'peer',
              'lag_min=%s lead_side=%s r=%s' % (pf.get('lag_min'), pf.get('lead_side'), pf.get('lag_r')))
        check('B10 这一对带上了「领先」标签', pf.get('rel') == '领先', str(pf.get('rel')))
    ev = (rf.get('links', {}).get(follower, {}) or {}).get('evidence', [])
    # ★判据要钉**整句话**："600010 领先 600011 15 分钟"。
    #   第一版写的是 `'领先 15 分钟' in e` —— 那句话在证据串里**根本不会出现**
    #   （格式是"A 领先 B N 分钟"），于是这条判据永远红、而且理由看着像"没写出滞后"。
    #   判据钉错了形状时，它红和它绿都不说明被测代码对不对（本项目的老账）。
    want_ev = '%s 领先 %s %d 分钟' % (leader, follower, want)
    check('B11 证据串里写清了"%s"' % want_ev,
          any(want_ev in e for e in ev),
          json.dumps([e for e in ev if '领先' in e][:3], ensure_ascii=False))
    lk_l = (rl.get('links', {}).get(leader, {}) or {}).get('evidence', [])
    check('B11b 领先股那一侧的证据串是**同一句话**（方向不因谁问而变）',
          any(want_ev in e for e in lk_l),
          json.dumps([e for e in lk_l if '领先' in e][:3], ensure_ascii=False))
    check_no_advice(rf.get('note'), 'B12 note')
    return rl, rf


# ══ 场景 C：采集失败要如实进账 ══════════════════════════════════════════
def scenario_failure():
    print('\n══ 场景 C：打掉一个源（LICAI_LINKAGE_SYNTH_FAIL=realtime:hk）══')
    old = os.environ.get(S.FAIL_ENV)
    os.environ[S.FAIL_ENV] = 'realtime:hk,daily:cn:600002'
    try:
        r = linkage.scan(scope='all', top=5)
        dump({'collected': r['collected'], 'note': r.get('note')}, '打掉源之后的账')
        check('C1 整体仍然 ok=True（一个市场挂了不该带走整轮）', r.get('ok') is True,
              json.dumps(r.get('reason'), ensure_ascii=False))
        src = {s['id']: s for s in r['collected']['sources']}
        hk = src.get('realtime:hk')
        check('C2 挂掉的那个源在 sources 里 ok=False 且带 err',
              hk is not None and hk['ok'] is False and bool(hk['err']),
              json.dumps(hk, ensure_ascii=False))
        check('C3 没挂的源仍然 ok=True', src.get('realtime:cn', {}).get('ok') is True)
        check('C4 collected.failed 与 sources 里的红条数一致',
              r['collected']['failed'] == sum(1 for s in r['collected']['sources'] if not s['ok']),
              '%s vs %s' % (r['collected']['failed'],
                            sum(1 for s in r['collected']['sources'] if not s['ok'])))
        check('C5 note 里明说了有采集失败', '采集失败' in (r.get('note') or ''), r.get('note'))
        check('C6 hk 的行数不再计入（mk 里没有 hk 或为 0）',
              r['collected']['rows_by_market'].get('hk', 0) == 0,
              json.dumps(r['collected']['rows_by_market']))
        # 单只票的日线挂了 → 那条关联必须**说出来**，而不是静默变成一个没有相关的 peer
        lk = r['links'].get('600002') or {}
        check('C7 日线挂掉的票，它的关联里说明了没算',
              (not lk) or any('没取到' in e or '没算' in e for e in lk.get('evidence', [])),
              json.dumps((lk.get('evidence') or [])[:3], ensure_ascii=False))
    finally:
        if old is None:
            os.environ.pop(S.FAIL_ENV, None)
        else:
            os.environ[S.FAIL_ENV] = old


# ══ 场景 D：没有数据层时的行为（不许兜底）══════════════════════════════
def scenario_no_backend():
    print('\n══ 场景 D：关掉桩 + 没有 datasource ⇒ 必须 ok=False，不许兜底出数 ══')
    os.environ.pop('LICAI_LINKAGE_SYNTH', None)
    # ★让 `import datasource` **真的**失败，而不是"假定它会失败"。
    #   原来这里只是关掉桩、然后指望数据层不存在 —— 可 `agent/datasource/`
    #   早就建出来了（51 KB）⇒ 本场景会**去采真行情**：本机实测一轮 34 s、
    #   拉到 realtime:cn n=5466/5566 行真 A 股快照，联网时 D1/D3 红、断网时绿。
    #   于是这套自检的退出码成了"今天网通不通"的函数，不是"linkage 对不对"的
    #   函数；而 D1/D3 是**"取不到数据层就 ok=False，绝不悄悄换合成市场"**
    #   这条纪律唯一的守卫 —— 它必须量得了，不能靠运气绿。
    #   `sys.modules[name] = None` 是 Python 的官方习语：之后任何
    #   `import datasource` / `import datasource.xxx` 都抛 ImportError。
    #   ★必须**无条件**垫 `datasource` 这个名字：本场景之前 A/B/C 全走合成市场，
    #     一次都没 import 过它 ⇒ 只按"已在 sys.modules 里的键"去垫的话，
    #     垫了个空集、`import datasource` 照旧成功（第一版就踩了这个）。
    _saved = {k: v for k, v in sys.modules.items()
              if k == 'datasource' or k.startswith('datasource.')}
    sys.modules['datasource'] = None
    for k in list(_saved):
        sys.modules[k] = None
    C.reset_backend()
    try:
        r = linkage.scan(scope='cn', top=3)
        dump(r, '没有数据层时的返回')
        check('D1 ok=False', r.get('ok') is False)
        check('D2 理由是"取不到数据层"', 'datasource' in json.dumps(r, ensure_ascii=False))
        check('D3 没有 anomalies / 没有编出来的数', 'anomalies' not in r)
    finally:
        # ★先把垫进去的 None（以及任何新进来的 datasource.*）清掉，再放回原件。
        #   少了 `del` 那一步的话，`update(_saved)` 只覆盖 `_saved` 里有的键，
        #   被垫成 None 的新键会**留着**，后面场景 E 就再也 import 不到数据层了。
        for k in [k for k in sys.modules
                  if k == 'datasource' or k.startswith('datasource.')]:
            del sys.modules[k]
        sys.modules.update(_saved)
        os.environ['LICAI_LINKAGE_SYNTH'] = '1'
        C.reset_backend()


# ══ 场景 E：_num 与 feed._num 一条规则 ═════════════════════════════════
def scenario_num_agree():
    print('\n══ 场景 E：`_num` 与 `feed._num` 在边界值上一致 ══')
    if C.NUM_SRC != 'feed._num':
        check('E0 用的是 feed._num（不是本地退路）', False, '实际：%s' % C.NUM_SRC)
        return
    import numpy as np
    import feed
    vals = [None, float('nan'), float('inf'), float('-inf'), np.float64('nan'),
            np.int64(7), np.float32(1.5), '3.25', 'x', 0.123456, -0.0]
    bad = [(v, L_num(v), feed._num(v)) for v in vals if L_num(v) != feed._num(v)]
    check('E1 边界值上两个 _num 结果完全一致', not bad, str(bad))
    check('E2 NaN/inf → None（不是 0）',
          L_num(float('nan')) is None and L_num(float('inf')) is None)
    check('E3 numpy 标量不是 None', L_num(np.int64(7)) == 7)


def main():
    dump_path = None
    if '--json' in sys.argv:
        i = sys.argv.index('--json')
        dump_path = (sys.argv[i + 1] if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith('-')
                     else '/tmp/linkage_probe.json')

    print('═══ linkage 冒烟验证（合成市场，不是真行情）═══')
    print('backend = %s   预算上限 = %s'
          % (C.backend_name(), os.environ.get('LICAI_LINKAGE_BUDGET')))
    r, _an = scenario_scan()
    if dump_path and r:
        with open(dump_path, 'w', encoding='utf-8') as f:
            json.dump(r, f, ensure_ascii=False, indent=1, allow_nan=False)
        print('\n（完整 JSON 已落盘 %s，%d 字节）' % (dump_path, os.path.getsize(dump_path)))
    scenario_anomaly_of()
    scenario_failure()
    scenario_no_backend()
    scenario_num_agree()

    print('\n═══ 小计：通过 %d，失败 %d ═══' % (len(PASS), len(FAIL)))
    for f in FAIL:
        print('   ✗ %s' % f)
    print('结论：%s' % ('全部通过' if not FAIL else '★有失败项，别当成"跑过了"'))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
