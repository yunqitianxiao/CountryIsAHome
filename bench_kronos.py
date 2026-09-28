#!/usr/bin/env python3
"""实测 Kronos 到底要跑多久 —— 默认参数不是拍脑袋定的，是这张表定的。

为什么要单独一个脚本：`pred_len` 直接决定用户等多久。Kronos **没有 KV cache**，
每一步都要把整段上下文重算一遍，所以耗时基本随 `pred_len` 线性涨（不是常数摊销）。
"预测 30 根要几秒"这件事没有任何办法从参数里推算出来，只能真跑一遍量。

    .venv/bin/python bench_kronos.py            # 默认扫 pred_len × 路径条数
    .venv/bin/python bench_kronos.py --quick    # 只跑两个点（约 1 分钟）

输出 `kronos_perf.json`，README 里的数字就是它。
"""

import argparse
import json
import os
import platform
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'agent'))

import runner                                                          # noqa: E402

OUT = os.path.join(HERE, 'kronos_perf.json')

# 扫这几个点。pred_len 覆盖「UI 可能让用户选的档位」；第二个数是**路径条数**。
#
# ★这一列从 `sample_count` 改成了 `n_paths`（阶段 3）：站上发的多路径区间走的是
#   `runner.sample_paths`，它是 **N 次独立调用**（上游把一次采 N 条当场平均掉了，
#   个体路径拿不回来，见那边的 docstring）。所以"多采几条的代价"必须照**这条**路量 ——
#   量 `predict(sample_count=N)` 得到的是另一个东西的数，而它看起来一样能用。
#   (30, 9) 就是线上默认（`tools.DEFAULT_SAMPLES`），是用户真正要等的那一格。
GRID = [(10, 1), (20, 1), (30, 1), (60, 1), (120, 1), (30, 3), (30, 9)]
QUICK = [(20, 1), (60, 1)]

# 耐心线：用户还愿意等的上限。SSE 的心跳撑着不至于"看起来卡死"，但再久人就走了。
PATIENCE_MS = 60000
# 耗时没到这条线的话，"按耗时挑 pred_len"就是空话 —— 默认值该按预测视野选。
DEFAULTS_PRED_LEN = runner.DEFAULTS['pred_len']


def one(pred_len, n_paths, lookback, repeats=2):
    """跑 repeats 次取中位数。

    ★取中位数而不是最小值，也不是平均：第一次跑常被 cuDNN 选核/显存分配拖慢，
    最小值会把这份一次性成本藏起来（报出一个用户永远遇不到的乐观数）；
    平均值又会被同一个离群点拉高。中位数对两头都不敏感。
    """
    df, xts, yts = runner.synthetic_ohlcv(lookback=lookback, pred_len=pred_len)
    times, rows = [], None
    for _ in range(repeats):
        t0 = time.time()
        out, _info = runner.ENGINE.sample_paths(df, xts, yts, pred_len=pred_len,
                                                n=n_paths)
        times.append((time.time() - t0) * 1000)
        rows = len(out)
    return {'pred_len': pred_len, 'n_paths': n_paths, 'lookback': lookback,
            'api': 'sample_paths', 'paths': len(out),
            'ms_median': int(statistics.median(times)),
            'ms_min': int(min(times)), 'ms_max': int(max(times)),
            'rows': rows, 'repeats': repeats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--lookback', type=int, default=runner.DEFAULTS['lookback'])
    ap.add_argument('--repeats', type=int, default=2)
    ap.add_argument('--out', default=OUT)
    a = ap.parse_args()

    st = runner.ENGINE.status()
    print('环境：torch=%s  cuda=%s  gpu=%s' % (st['torch'], st['cuda'], st['gpu']))
    if not st['torch']:
        print('★没有 torch —— 在 .venv 里跑（./run.sh 用的是它）。这条尺子现在量不了。')
        return 2

    t0 = time.time()
    try:
        runner.ENGINE.load()
    except runner.EngineError as e:
        print('加载失败 [%s] %s\n  → %s' % (e.code, e, e.hint))
        return 1
    load_ms = int((time.time() - t0) * 1000)
    st = runner.ENGINE.status()
    print('加载 %dms → device=%s' % (load_ms, st['device']))

    # ★先跑一次丢掉：第一次调用包含 cuDNN 选核、显存池首次分配、kernel 编译，
    #   这份成本只发生一次，混进表里会把第一个点量得比后面都慢，看起来像"pred_len 越小越慢"。
    print('预热（这一次的耗时不计入表）…')
    runner.ENGINE.warmup()

    grid = QUICK if a.quick else GRID
    rows = []
    for pred_len, np_ in grid:
        r = one(pred_len, np_, a.lookback, a.repeats)
        rows.append(r)
        print('  pred_len=%-4d 路径条数=%d  →  %5d ms  (min %d / max %d, %d 行, %d 条路径)'
              % (pred_len, np_, r['ms_median'], r['ms_min'], r['ms_max'],
                 r['rows'], r['paths']))

    # 每根的成本：没有 KV cache，所以这个数应当**大致是个常数**；
    # 若它随 pred_len 明显上升，说明瓶颈不在逐步生成，而在别处（上下文重算/显存）。
    per_bar = [(r['pred_len'], round(r['ms_median'] / r['pred_len'], 1))
               for r in rows if r['n_paths'] == 1]
    print('\n每根耗时（ms/根）：%s' % per_bar)

    sc1 = [r for r in rows if r['n_paths'] == 1]
    rec = None
    if sc1:
        # ★这条规则本身要经得起"它到底约束住了什么"这一问。
        #   原规则：取「中位耗时 ≤ 60 秒」里最大的那个 pred_len。写的时候假设的是
        #   "几十秒一分钟"的量级；实测下来**每根只要约 7ms**（RTX 4060），
        #   pred_len=120 也才 0.9 秒 —— 于是这条规则**一个都没约束住**，
        #   退化成"取最大值"，看着像个结论，其实什么都没说。
        #   一条永远不生效的规则和没有规则一样，所以这里改成**如实报告它没生效**，
        #   并把默认值交还给"预测视野该多长"这个真问题（耗时在这个量级上不构成理由）。
        near = [r for r in sc1 if r['ms_median'] >= 0.3 * PATIENCE_MS]
        okk = [r for r in sc1 if r['ms_median'] <= PATIENCE_MS]
        if not near:
            rec = DEFAULTS_PRED_LEN
            print('\n★耗时在 %dms 量级上**完全没构成约束**（最长的 pred_len=%d 也只有 %dms，'
                  '离 %.0f 秒的耐心线差得远）。'
                  % (sc1[-1]['ms_median'] // max(1, sc1[-1]['pred_len']),
                     sc1[-1]['pred_len'], sc1[-1]['ms_median'], PATIENCE_MS / 1000))
            print('  所以默认 pred_len **不该由耗时决定**，应由"预测视野要多长"决定 ——'
                  ' 视野越长，外推越不可信。保持 %d。' % rec)
        else:
            rec = (okk or sc1)[-1]['pred_len']
            print('建议默认 pred_len = %d（中位 %d ms）'
                  % (rec, next(r['ms_median'] for r in sc1 if r['pred_len'] == rec)))

    report = {
        'measured_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'machine': {'platform': platform.platform(), 'gpu': st['gpu'],
                    'torch': st['torch'], 'cuda': st['cuda']},
        'load_ms': load_ms, 'device': str(st['device']),
        'lookback': a.lookback, 'model': 'Kronos-small 24.7M / max_context 512',
        'note': 'Kronos 无 KV cache，每步重算整段上下文 ⇒ 耗时随 pred_len 近似线性；'
                '★路径条数那一列走 `sample_paths`（N 次独立调用），不是一次采 N 条',
        'rows': rows, 'ms_per_bar': per_bar, 'recommended_pred_len': rec,
    }
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('写出 %s' % a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
