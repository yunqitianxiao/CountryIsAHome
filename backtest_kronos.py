#!/usr/bin/env python3
"""实测 Kronos 的预测**准确率** —— 页面上那个"只做参考"必须由这张表撑着。

为什么非要单独一个脚本、而不是在页面上写句"仅供参考"了事：
**"仅供参考"是一句无法证伪的话**，写到哪儿都不担责任，用户看完还是不知道该信几分。
把它换成"方向命中率 X%，而同期'总猜涨'就有 Y%"才是负责任的说法 ——
用户能拿这个数字去决定要不要看这条预测。

★ 这个脚本有三条**不许省**的规矩，它们决定了这个数字是不是真的：

1. **必须有基线，而且基线要对等。** 方向命中率单独看会骗人：
   如果回测窗口里 60% 的交易日是涨的，那么一个**永远喊涨**的哑巴策略就有 60% 命中率。
   所以并排报三条：
     - 模型：Kronos 预测 h 根后的方向
     - **总猜涨**：无脑喊涨（衡量"市场本身偏多"贡献了多少）
     - **跟随趋势**：拿过去 h 根的涨跌当预测（一个真正的朴素对手）
   同理，收盘价的误差要和 **"假设不动"**（随机游走的期望）比 ——
   如果模型路径的 MAPE 还不如"就按今天的价"，那它**没有增加任何信息**。

2. **不许挑窗口。** 起点按固定步长等距取，覆盖整段历史；不许只挑"预测得准"的时段。
   跑出来多难看都照报 —— 难看本身就是"只做参考"的**证据**。

3. **方向要带死区。** 预测 +0.05% 和实际 +0.05% 撞上纯属巧合，
   所以同时报"严格符号"和"±0.3% 死区"两套（死区那套是工具层 `direction` 用的口径）。

★ 4. **区间给出去之前先校准它。** 阶段 3 起，站上会发一个"名义 80%"的多采样区间
   （N 条独立采样路径的极差）。那个 80% 是**定义出来的**（顺序统计量：(N−1)/(N+1)，
   不需要任何分布假设），所以"它实测到底覆盖了多少"是一个**可否证的**问题 ——
   而**测出来之前，页面上一个覆盖率数字都不许写**。`--coverage` 就是量它的那一遍，
   慢 9 倍（每个起点多 9 次独立推理）。

    .venv/bin/python backtest_kronos.py                 # 默认（约 2 分钟）
    .venv/bin/python backtest_kronos.py --coverage      # 连覆盖率一起量（约 17 分钟）
    .venv/bin/python backtest_kronos.py --quick         # 少跑几个起点
    .venv/bin/python backtest_kronos.py --stride 20

输出 `kronos_accuracy.json`，页面与 README 里的数字就是它。
"""

import argparse
import json
import os
import statistics
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'agent'))

import feed                                                            # noqa: E402
import ledger                                                          # noqa: E402
import runner                                                          # noqa: E402
import score                                                           # noqa: E402

OUT = os.path.join(HERE, 'kronos_accuracy.json')

# 四个市场各一个代表，避免结论只是"某一只票恰好"。
# 加密只有 300 根（OKX 上限），起点会少很多 —— 照报，不因为它样本少就丢掉。
SYMBOLS = [('cn_stock', '600519', '贵州茅台'),
           ('hk_stock', '00700', '腾讯控股'),
           ('us_stock', 'AAPL', '苹果'),
           ('crypto', 'BTC', '比特币')]

LOOKBACK = 400            # 与 runner.DEFAULTS 一致：页面默认就是这个配置
PRED_LEN = 30
# ★死区不在这里定义 —— 它住在 `agent/score.py`，和工具层的 direction 口径是**同一个常量**。
#   这个脚本和在线台账共用那把尺子，理由见 score.py 的文件头。
DEADBAND = score.DEADBAND

# ── 覆盖率校准 ────────────────────────────────────────────────────────
# ★量的是**站上真正发出去的那个区间**：`score.envelope` 在 N 条独立采样路径上的极差。
#   不是单次采样那条路径的 min/max —— 那个不是区间（`ledger.coverage` 那边有判据守）。
#   走 `score.envelope` 而不是在这里自己求 min/max：各写一份的话，校准的就是**另一个
#   区间**，而两个数长得一模一样。
#
# ★`COVER_SAMPLES` 必须等于 `tools.DEFAULT_SAMPLES` —— 那是线上默认发出的 N。判据钉着
#   这两层（`verify_agent.check_coverage_calibration`），改一处漏一处就会出现
#   "校准的是 N=9、发出去的是 N=5"，而两份数字都看不出对方改过。
COVER_SAMPLES = 9
# ★这一对必须**一起**改：1.2816 就是"双侧 80%"的正态分位。改了 z 而不改名义值，
#   对照带的名义水平就变成一个没人算得出来的数。
HIST_BAND_PCT, COVER_Z = 80.0, 1.2816


def one_origin(df, t, lookback, pred_len, market, cover=False):
    """在 df 的第 t 根上"站住"，用前 lookback 根预测后 pred_len 根，再和真实的比。

    `cover=True` 时**多花 9 次独立推理**，把这个起点上那个多采样区间也量出来
    （见 `cover_of`）。★两条路都留着、不复用同一次调用：点预测那一半必须和线上
    `predict_kronos` 一模一样的口径（单条路径），换口径就等于把已跑出来的
    方向命中率/MAPE 悄悄替掉。
    """
    hist = df.iloc[t - lookback + 1:t + 1].reset_index(drop=True)
    actual = df.iloc[t + 1:t + 1 + pred_len].reset_index(drop=True)
    if len(actual) < pred_len:
        return None                                    # 后面不够长了
    base = float(hist['close'].iloc[-1])
    x_ts = pd.Series(pd.to_datetime(hist['dt']))
    last = pd.Timestamp(hist['dt'].iloc[-1])
    if market == 'crypto':
        y_dts = pd.date_range(last + pd.Timedelta(days=1), periods=pred_len, freq='D')
    else:
        y_dts = pd.bdate_range(last + pd.Timedelta(days=1), periods=pred_len)
    y_ts = pd.Series(y_dts)

    pred, _info = runner.ENGINE.predict(hist, x_ts, y_ts, pred_len=pred_len,
                                        sample_count=1, use_cache=False)

    # 打分全部走唯一出口；这里只负责"把模型和真实摆到一起"
    row = score.outcome_row(base, pred['close'], actual['close'], deadband=DEADBAND,
                            window_mean=float(hist['close'].mean()))
    # 过去的 h 根涨跌 → "跟随趋势"这个朴素对手
    past = float(hist['close'].iloc[-1]) / float(hist['close'].iloc[-min(pred_len,
                                                                       len(hist))]) - 1
    row = score.with_momentum(row, past * 100, deadband=DEADBAND)
    # ★给台账留的**打分原始输入**（`--ledger` 时用）。存在这里而不是另算一份：
    #   台账和回测必须是同一把尺子量出来的同一个东西，各算一次就又分家了。
    #   `score.summarise` 只读它认识的那些键，多这一个 `_raw` 不影响任何汇总。
    row['_raw'] = {
        'pred_close': [float(x) for x in pred['close']],
        'actual_close': [float(x) for x in actual['close']],
        'close_last': base,
        'window_mean': float(hist['close'].mean()),
        'lookback': lookback,
        'actual_last_date': str(actual['dt'].iloc[-1])[:10],
    }
    if cover:
        row['_cover'] = cover_of(hist, x_ts, y_ts, actual, base, pred_len)
    return row


def cover_of(hist, x_ts, y_ts, actual, base, pred_len, samples=COVER_SAMPLES):
    """这一个起点上：名义 80% 的多采样区间**兜住真实终点了没有**。

    ★比的是**终点**那一根（`actual_close[-1]`），与 `score.level_of` 给出名义水平的
      前提对齐 —— "一个新样本落在 N 个样本极差内"说的就是同一个落点上的事。
      按"视野里每一根"算量到的是另一个东西，而且会系统性偏高（早期几根离基准价近）。

    ★对照带不是装饰：单说"覆盖 62%"没有参照 —— 62% 是坏还是好？同期拿回看窗口的
      日波动拼一条**同样名义 80%** 的带子（base×(1±1.2816σ√h)，不用模型），
      两条并排才知道这个区间比"什么都不做"强在哪。和 MAPE 必须配"假设不动"
      是同一条纪律。
    """
    paths, _info = runner.ENGINE.sample_paths(hist, x_ts, y_ts, pred_len=pred_len,
                                              n=samples)
    plo, phi = score.envelope([list(p['close'].astype(float)) for p in paths])
    lo, hi = float(plo[-1]), float(phi[-1])
    act = float(actual['close'].iloc[-1])
    # 日波动 → 同长度的自然波动幅度。波动算不出来（全平的序列）就**不给对照带**，
    # 不编一个 0 出来（0 宽度的带子覆盖率必然接近 0，那会是一个假结论）。
    half = 0.0
    try:
        sd = float(hist['close'].pct_change().std() or 0)
        if sd > 0:
            half = COVER_Z * sd * (pred_len ** 0.5) * base
    except Exception:                                        # noqa: BLE001
        half = 0.0
    return {
        'lo': lo, 'hi': hi, 'act': act,
        'covered': bool(lo <= act <= hi),
        'width_pct': (hi - lo) / base * 100.0 if base else 0.0,
        'h_lo': base - half, 'h_hi': base + half,
        'h_covered': bool(half and (base - half) <= act <= (base + half)),
        'h_width_pct': (2 * half / base * 100.0) if (base and half) else 0.0,
    }


def cover_summary(items, samples=COVER_SAMPLES):
    """一批起点的覆盖率汇总（模型区间 + 不用模型的对照带）。

    ★报 `ci95`（Wilson）而不只是点估计：n=483 时 ±2 个百分点就换一个结论，
      拿点估计去写页面就是过度解读 —— 与 `verdict()` 判"算不算赢"同一条纪律。
    ★报 `zero_width`：那一条是"缓存没关 ⇒ N 条路径其实同一条"的**哨兵**。
      它一旦非零，这一整份覆盖率量的就是别的东西（见 main 里的处理）。
    """
    n = len(items)
    if not n:
        return None
    cov = sum(1 for x in items if x['covered'])
    okh = [x for x in items if x.get('h_width_pct')]
    hcov = sum(1 for x in okh if x['h_covered'])
    return {
        'n': n, 'n_paths': samples,
        # ★名义水平由 N 算出来（`score.level_of`），不写死 —— 写死就会在 N 变了之后撒谎
        'nominal_pct': score.level_of(samples),
        'covered': cov, 'empirical_pct': round(100.0 * cov / n, 1),
        'ci95': score.wilson(cov, n),
        'median_width_pct': round(statistics.median([x['width_pct'] for x in items]), 2),
        'zero_width': sum(1 for x in items if x['width_pct'] <= 0.0),
        'hist_band': ({
            'n': len(okh), 'assumed_pct': HIST_BAND_PCT, 'covered': hcov,
            'empirical_pct': round(100.0 * hcov / len(okh), 1),
            'ci95': score.wilson(hcov, len(okh)),
            'median_width_pct': round(statistics.median([x['h_width_pct']
                                                         for x in okh]), 2),
            'how': '日波动率 ×√h ×%.4f（假设正态的同样名义 %.0f%% 带子，不用模型）'
                   % (COVER_Z, HIST_BAND_PCT),
        } if okh else None),
    }


def run_symbol(market, code, name, lookback, pred_len, stride, budget_s, cover=False):
    df = feed.fetch(market, code, days=1200)
    if len(df) < lookback + pred_len + 10:
        return None, '历史只有 %d 根，不够 %d+%d' % (len(df), lookback, pred_len)
    origins = list(range(lookback - 1, len(df) - pred_len, stride))
    rows, t0 = [], time.time()
    for t in origins:
        r = one_origin(df, t, lookback, pred_len, market, cover=cover)
        if r is None:
            continue
        r['date'] = str(df['dt'].iloc[t])[:10]
        r['_sym'] = (market, code, name)      # 台账要（--ledger）
        rows.append(r)
        if time.time() - t0 > budget_s:
            print('    （到时间预算 %ds，这一只用 %d 个起点收工）' % (budget_s, len(rows)))
            break
    return rows, None


def summarise(all_rows):
    """★汇总**全部**走 `agent/score.py` —— 这里一个字都不许自己算。

    在线台账打的是同一把尺子；两边各写一份的话，"回测 52.3%"和"页面台账 60%"
    会同时挂在站上，而且没人看得出哪个对（两个都是"算出来的"）。
    """
    return score.summarise(all_rows, pred_len=PRED_LEN, lookback=LOOKBACK)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stride', type=int, default=12, help='起点间隔（越小越密、越慢）')
    ap.add_argument('--quick', action='store_true')
    # ★这个上限在 `--coverage` 时自动乘 (N+1)：每个起点从 1 次推理变成 N+1 次，
    #   不跟着放大的话，覆盖率那一半会在跑到一半时被截断 —— 而截断是**静默**的
    #   （只印一行字），量出来的 n 比点预测那一半小，两半就对不上了。
    ap.add_argument('--budget', type=int, default=180,
                    help='每只标的的秒数上限（--coverage 时自动乘 %d）' % (COVER_SAMPLES + 1))
    ap.add_argument('--coverage', action='store_true',
                    help='量名义区间的经验覆盖率（每个起点多 %d 次独立推理，慢约 %d 倍）'
                         % (COVER_SAMPLES, COVER_SAMPLES + 1))
    ap.add_argument('--out', default=OUT)
    # ★冷启动回填：把这些滚动窗口也写成台账条目。`source='backfill'`，
    #   在页面上和 live **分区显示** —— 混成一个数就是拿旧数据冒充新战绩。
    #   `id` 按内容算，所以重复跑是幂等的（不会让样本数凭空翻倍）。
    ap.add_argument('--ledger', action='store_true',
                    help='同时把这些窗口写进预测台账（source=backfill）')
    a = ap.parse_args()
    stride = 30 if a.quick else a.stride
    budget = a.budget * (COVER_SAMPLES + 1) if a.coverage else a.budget

    st = runner.ENGINE.status()
    print('环境：torch=%s cuda=%s gpu=%s' % (st['torch'], st['cuda'], st['gpu']))
    if not st['torch']:
        print('★没有 torch —— 在 .venv 里跑。这条尺子现在量不了。')
        return 2
    t0 = time.time()
    try:
        runner.ENGINE.load()
    except runner.EngineError as e:
        print('加载失败 [%s] %s' % (e.code, e))
        return 1
    print('加载 %dms → %s\n' % (int((time.time() - t0) * 1000), runner.ENGINE.status()['device']))

    # 这一轮的"同一时刻"：报告头和覆盖率块**用同一个时间戳和同一个权重指纹** ——
    # 各取各的话，会出现"报告是 11:00 量的、覆盖率块是 11:20 量的"而谁也没说。
    report_ts = time.strftime('%Y-%m-%d %H:%M:%S')
    sha_now = ledger.model_sha()['sha']

    print('回测配置：lookback=%d  horizon=%d  起点间隔=%d'
          % (LOOKBACK, PRED_LEN, stride))
    print('★每个起点都是一次完整推理，所以这一步就是"把页面上的功能跑很多遍"')
    if a.coverage:
        print('★这一轮连覆盖率一起量：每个起点再加 %d 次**独立**采样（N 条必须各自独立，'
              '见 runner.sample_paths）⇒ 慢约 %d 倍，单只上限 %ds（自动放大过）\n'
              % (COVER_SAMPLES, COVER_SAMPLES + 1, budget))
    else:
        print('（没有 --coverage ⇒ 这一轮不量覆盖率，JSON 里 coverage 写 null）\n')

    per_symbol, all_rows, skipped = {}, [], []
    per_cover = {}
    for market, code, name in SYMBOLS:
        print('── %s %s(%s)' % (name, code, market))
        try:
            rows, err = run_symbol(market, code, name, LOOKBACK, PRED_LEN, stride,
                                   budget, cover=a.coverage)
        except Exception as e:                                   # noqa: BLE001
            rows, err = None, str(e)[:110]
        if err or not rows:
            print('   取数/回测跳过：%s' % err)
            skipped.append('%s(%s): %s' % (name, code, err))
            continue
        s = summarise(rows)
        per_symbol[code] = dict(s, name=name, market=market,
                                first=r_date(rows[0]), last=r_date(rows[-1]))
        all_rows.extend(rows)
        if a.coverage:
            c = cover_summary([r['_cover'] for r in rows if r.get('_cover')])
            if c:
                per_cover[code] = c
        print('   %d 个起点（%s → %s）' % (s['n_origins'], r_date(rows[0]), r_date(rows[-1])))
        print('   方向命中率 严格 %.1f%%（%d 次表态） / 死区 ±%.1f%% %.1f%% [%.1f, %.1f]（%d 次）'
              % (s['dir_strict']['hit_rate'] or 0, s['dir_strict']['n_called'], DEADBAND,
                 s['dir_band']['hit_rate'] or 0,
                 s['dir_band']['ci95'][0], s['dir_band']['ci95'][1],
                 s['dir_band']['n_called']))
        print('   对照：无脑喊涨 %.1f%%   跟随趋势 %.1f%%'
              % (s['baseline_always_up']['hit_rate'],
                 s['baseline_momentum']['hit_rate'] or 0))
        print('   收盘价误差：模型 %.3f%%   "假设不动" %.3f%%'
              % (s['mape_close_model'], s['mape_close_flat']))
        if per_cover.get(code):
            c = per_cover[code]
            print('   名义 %.1f%% 区间：实测覆盖 %.1f%%（%d/%d）  '
                  '对照波动带 %.1f%%'
                  % (c['nominal_pct'], c['empirical_pct'], c['covered'], c['n'],
                     (c['hist_band'] or {}).get('empirical_pct') or 0))

    if not all_rows:
        print('\n★一个起点都没跑成 —— 全被取数挡在外面了，这份表不能用来下结论。')
        return 1

    total = summarise(all_rows)
    print('\n' + '=' * 62)
    print('合计 %d 个起点（%d 只标的）' % (total['n_origins'], len(per_symbol)))
    print('=' * 62)
    print('方向命中率（严格符号）    模型 %5.1f%%   （%d 次表态）'
          % (total['dir_strict']['hit_rate'], total['dir_strict']['n_called']))
    print('方向命中率（±%.1f%% 死区）  模型 %5.1f%%   95%%CI [%.1f, %.1f]  （%d 次表态）'
          % (DEADBAND, total['dir_band']['hit_rate'],
             total['dir_band']['ci95'][0], total['dir_band']['ci95'][1],
             total['dir_band']['n_called']))
    print('  对照 · 无脑喊涨        %5.1f%%' % total['baseline_always_up']['hit_rate'])
    print('  对照 · 跟随趋势        %5.1f%%   （%d 次表态）'
          % (total['baseline_momentum']['hit_rate'] or 0,
             total['baseline_momentum']['n_called']))
    print('收盘价路径误差（MAPE）    模型 %5.3f%%   vs  "假设不动" %5.3f%%'
          % (total['mape_close_model'], total['mape_close_flat']))
    bs = total['mape_by_step']
    print('  按预测步长拆开（第 N 根）：第1根 模型 %.2f%% / 不动 %.2f%%   '
          '第10根 %.2f%% / %.2f%%   第30根 %.2f%% / %.2f%%'
          % (bs[0]['model'], bs[0]['flat'], bs[9]['model'], bs[9]['flat'],
             bs[-1]['model'], bs[-1]['flat']))

    # ★结论必须**从数字里读出来**，不许预先写死一句话。
    #   判"算不算赢"用的是**置信区间下界**，不是点估计 ——
    #   点估计高 2 个百分点在 n=100 时说明不了任何事，拿它去写页面就是过度解读。
    #   ★"走哪个分支"这件事也只在 `score.verdict()` 里判一次：页面上的台账读同一个
    #     函数。这里只负责把结论**说成中文**，措辞是介质相关的事。
    v = score.verdict(total)
    d, lo, hi = v['hit_rate'], (v['ci95'] or [None, None])[0], (v['ci95'] or [None, None])[1]
    b = v['best_baseline']
    m, f = v['mape']['model'], v['mape']['flat']
    print()
    if d is None:
        print('★模型一次方向都没表过态（全落进死区），方向命中率无从谈起。')
    elif not v['beats_coin']:
        print('★方向命中率 %.1f%%，但 95%% 置信区间 [%.1f, %.1f] **跨过了 50%%** ——'
              % (d, lo, hi))
        print('  也就是说：以这个样本量，**"它比抛硬币强"这句话在统计上站不住**。')
        print('  最强的朴素基线是 %.1f%%。这正是页面上必须写"只做参考"的**实证依据**，不是客套话。' % b)
    else:
        print('方向命中率 %.1f%%，95%%CI [%.1f, %.1f] 下界高于 50%%，'
              '比最强的朴素基线 %.1f%% 高 %.1f 个百分点。' % (d, lo, hi, b, v['margin']))
        print('  （这仍不等于"能拿来做决策" —— 但它至少说明方向这一项不是瞎猜。）')
    if v['mape']['wins']:
        print('收盘价路径误差 %.3f%% 好于"假设不动"的 %.3f%%。' % (m, f))
    else:
        print('★收盘价路径误差 %.3f%% **不如"假设不动"的 %.3f%%**（差 %.1f 倍）——'
              % (m, f, m / f))
        print('  连"就按今天的价不动"都赢不了，说明这条预测路径的**点位**没有可用信息。')
        gm, gf = v['mape']['growth_model'], v['mape']['growth_flat']
        print('  第 1 根误差 %.2f%%（不动 %.2f%%），第 30 根 %.2f%%（不动 %.2f%%）'
              % (bs[0]['model'], bs[0]['flat'], bs[-1]['model'], bs[-1]['flat']))
        # ★这两句话只能二选一，选错了就是给用户一个假解释：
        #   比值接近 ⇒ 每一步都稳定地差这么多（系统性偏差）；
        #   比值大得多 ⇒ 越推越离谱（盲推）。两者对"该不该看这条曲线"的含义完全不同。
        if v['mape']['synchronised']:
            print('  两条误差曲线**同步放大**（模型 %.1f 倍 / 不动 %.1f 倍）——'
                  '所以它不是"越推越离谱"，而是**每一步都稳定地差 %.1f 倍**：'
                  % (gm, gf, m / f))
            print('  这不是随机游走的漂移，是系统性的点位偏差。')
        else:
            print('  模型误差涨 %.1f 倍，远快于"不动"的 %.1f 倍 —— **越推越离谱**。' % (gm, gf))
        print('  第一根：模型隐含涨跌 |%.2f%%| vs 真实 |%.2f%%|（差 %.1f 倍）；'
              '朝回看窗口均值回归的比例 %s'
              % (total['jump1_abs_mean'], total['true1_abs_mean'],
                 v['mape']['first_bar_ratio'], total['revert_mean']))
        print('  → 连"下一根"都没接住，说明问题不在"推得远"，在**起点就没接上**。'
              if v['mape']['first_bar_lag'] else
              '  → 第一根接得上，误差是后面累积的。')

    # ── 覆盖率校准结果 ────────────────────────────────────────────────
    cover_block = None
    if a.coverage:
        cover_block = cover_summary([r['_cover'] for r in all_rows if r.get('_cover')])
        if not cover_block:
            print('\n★一个覆盖率样本都没量到 —— 这一轮不写 coverage，也不许写任何覆盖率数字。')
            return 1
        # ★哨兵：宽度为 0 ⇒ N 条"独立"采样其实拿到了同一条路径（`use_cache` 没关？）。
        #   那样算出来的覆盖率是个**看着像结论的假数**（区间宽 0 ⇒ 几乎必然不覆盖），
        #   所以这里**不写 JSON、直接退出**：宁可这份文件停在上一轮，也不给它灌一个假数。
        if cover_block['zero_width']:
            print('\n★%d / %d 个起点的区间宽度是 0 —— N 条采样拿到了同一条路径'
                  '（`runner.sample_paths` 里 `use_cache=False` 是硬要求）。'
                  '这一轮量的是别的东西，**不写 JSON**。'
                  % (cover_block['zero_width'], cover_block['n']))
            return 1
        cover_block.update({'measured_at': report_ts, 'model_sha': sha_now,
                            'pred_len': PRED_LEN, 'lookback': LOOKBACK, 'stride': stride,
                            'per_symbol': {k: {'n': c['n'], 'covered': c['covered'],
                                               'empirical_pct': c['empirical_pct'],
                                               'median_width_pct': c['median_width_pct'],
                                               'hist_pct': (c['hist_band'] or {}).get('empirical_pct')}
                                           for k, c in sorted(per_cover.items())}})
        cl, ch = cover_block['ci95']
        hb = cover_block['hist_band']
        print('\n' + '-' * 62)
        print('覆盖率校准：名义 %.1f%% 的区间（N=%d 条**独立**采样，pred_len=%d）'
              % (cover_block['nominal_pct'], COVER_SAMPLES, PRED_LEN))
        print('  实测覆盖 %.1f%%（%d/%d，95%%CI [%.1f, %.1f]）  区间宽度中位数 %.2f%%'
              % (cover_block['empirical_pct'], cover_block['covered'], cover_block['n'],
                 cl, ch, cover_block['median_width_pct']))
        if hb:
            print('  对照 · 不用模型的波动带（同样名义 %.0f%%）覆盖 %.1f%%（%d/%d），'
                  '宽度中位数 %.2f%% —— %s'
                  % (hb['assumed_pct'], hb['empirical_pct'], hb['covered'], hb['n'],
                     hb['median_width_pct'], hb['how']))
            print('  → 模型的带子比它%s %.2f 倍，覆盖率%s %.1f 个百分点'
                  % ('宽' if cover_block['median_width_pct'] >= hb['median_width_pct'] else '窄',
                     (cover_block['median_width_pct'] / hb['median_width_pct'])
                     if hb['median_width_pct'] else 0,
                     '高' if cover_block['empirical_pct'] >= hb['empirical_pct'] else '低',
                     abs(cover_block['empirical_pct'] - hb['empirical_pct'])))
        # ★这一句必须**从 CI 读出来**，不许预先写死"所以名义水平是乐观的"：
        #   CI 跨过名义值 ⇒ 这个样本量下说不了它偏窄还是偏宽。
        if ch < cover_block['nominal_pct']:
            print('★区间上界 %.1f%% 都低于名义 %.1f%% ⇒ 这个区间**系统性偏窄**：'
                  '名义水平是乐观的。页面上那句"名义 %.0f%%"必须**连着实测覆盖率一起说**，'
                  '不许单独挂着。' % (ch, cover_block['nominal_pct'], cover_block['nominal_pct']))
        elif cl > cover_block['nominal_pct']:
            print('★下界 %.1f%% 高于名义 %.1f%% ⇒ 区间**偏宽**（带子太保守，覆盖率高于名义）。'
                  % (cl, cover_block['nominal_pct']))
        else:
            print('实测覆盖率与名义 %.1f%% 没有显著差别（CI 跨过它）—— 这个名义水平站得住，'
                  '但仍然**不是**"价格有 %.1f%% 的概率落在里面"（模型没有超额能力，'
                  '区间宽窄只反映它自己的不确定）。'
                  % (cover_block['nominal_pct'], cover_block['nominal_pct']))

    report = {
        'measured_at': report_ts,
        'machine': {'gpu': st['gpu'], 'torch': st['torch'], 'cuda': st['cuda']},
        'model': 'Kronos-small 24.7M / max_context 512',
        # ★权重指纹（前 12 位）。回测会跨模型版本攒很久，**换了权重而报告不说**，
        #   就会出现"两次回测的数字其实来自两个模型"这种从数字上看不出来的事。
        'model_sha': sha_now,
        'config': {'lookback': LOOKBACK, 'pred_len': PRED_LEN, 'stride': stride,
                   'deadband_pct': DEADBAND, 'sample_count': 1,
                   'coverage_samples': COVER_SAMPLES,
                   'note': '每个起点一次完整推理；起点等距取，不挑窗口'
                           '；★单次采样 ⇒ 这个脚本**不是可复现的**（top_p=0.9 采样），'
                           '重跑一遍数字会小幅变（同一批起点实测两次跑出不同路径）'},
        'total': total,
        'per_symbol': per_symbol,
        'coverage': cover_block,
        'skipped': skipped,
    }
    # ★没带 `--coverage` 时**不写 coverage=null 就完事**：覆盖率是"这个方法"的性质，
    #   不是"这一轮点预测"的性质，所以上一轮量到的可以沿用 —— 但**必须模型指纹和配置
    #   都对得上**。对不上就写 null（页面回到"不给数字"），绝不静默沿用一份别人量的数。
    if not a.coverage:
        prev = (load_prev(a.out) or {}).get('coverage')
        if (isinstance(prev, dict) and prev.get('model_sha') == sha_now
                and prev.get('n_paths') == COVER_SAMPLES
                and prev.get('pred_len') == PRED_LEN and prev.get('lookback') == LOOKBACK):
            report['coverage'] = prev
            print('\n覆盖率沿用上一轮量的那份（%s，模型指纹与配置都对得上）'
                  '；要重新量就带 --coverage。' % prev.get('measured_at'))
        else:
            report['coverage'] = None
            print('\n★这一轮没量覆盖率（没带 --coverage），上一轮那份的模型指纹/配置'
                  '对不上 ⇒ `coverage` 写成 null，页面回到"不给数字"。')
    with open(a.out, 'w', encoding='utf-8') as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)
    print('\n写出 %s' % a.out)

    if a.ledger:
        # ★用**这份报告里**记着的模型指纹，不是"现在现算一个" —— 两者在同一轮里
        #   确实一样，但写成显式的一笔，"台账里每一条都能追到是哪次回测灌的"才成立。
        imp = ledger.import_backtest(all_rows, sha=report.get('model_sha'))
        print('台账：灌入 %d 条（source=backfill，和 live 分区显示）→ %s'
              % (imp['imported'], ledger.path()))
        if imp['bad'] or imp['write_failed']:
            print('★其中 %d 条对不上、%d 条没写进去：%s'
                  % (imp['bad'], len(imp['write_failed']), imp['write_failed'][:3]))
            return 1
    return 0


def r_date(r):
    return r['date']


def load_prev(path):
    """读上一轮写下的 JSON（读不到/不是合法 JSON → None）。只用来决定"覆盖率能不沿用"。"""
    try:
        with open(path, encoding='utf-8') as fp:
            d = json.load(fp)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


if __name__ == '__main__':
    sys.exit(main())
