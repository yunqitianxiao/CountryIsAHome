/**
 * 预测台账（`/ledger`）—— 这个站唯一一处"记着过去发生过什么"的页面。
 *
 * 五条纪律，每条都对应一条判据：
 *
 * 1. **分区显示，绝不混成一个数。** `live`（在这个站上点出来的）和 `backfill`
 *    （离线回测的滚动窗口）各自一块，各带各的样本数。混在一起的话，"台账战绩"
 *    几乎全部由回测窗口决定，而它看起来像"这个站在线跑出来的成绩" ——
 *    那不是统计口径之争，是拿旧数据冒充新战绩。
 *
 * 2. **命中率必须和基线成对出现。** 照 `accuracy.py` 那条规矩：
 *    「要么连基线一起给，要么整句不给」。单独一个 66.7% 没有信息量 ——
 *    同期「无脑喊涨」可能就有 66.7%。所以基线是**同一块里的必填项**，
 *    不是"有就显示"的可选装饰。
 *
 * 3. **没到期就不给数。** `total === null` 时那块**一个命中率数字都不许有**，
 *    照实说"还没有到期的"。显示 0% 会被读成"算出来是 0"。
 *
 * 4. **覆盖率没校准就不给数字。** 现在是单次采样，那条路径的 min/max
 *    **不是置信区间**，拿它算覆盖率是在量别的东西。所以那一栏照实说算不了。
 *
 * 5. **台账路径露在页面上。** 用户有资格知道这个站往磁盘上写了什么、写在哪儿。
 *    这也是"服务端零用户状态"那句承诺唯一被撕开的口子，撕在哪儿要说出来。
 *
 * ★图上不引入任何图表库（同 `/portfolio`）：命中率条走 div 宽度、
 *   区间走文本。Recharts 首帧量不到尺寸，同步探针会整个量空。
 */
import * as React from "react"
import { Badge } from "../Badge/Badge"
import { Callout } from "../Callout/Callout"
import { readAccBoot } from "../../chrome"
import { Shell } from "../Shell/Shell"
import { loadLedger, sourceLabel, type LedgerBlock, type LedgerResp } from "../../ledger"
import { num } from "../../format"

/** 命中率条：div 宽度，中性色。★**不用涨跌色** —— 命中率不是盈亏。 */
function RateBar({ rate }: { rate: number }) {
  const w = Math.max(0, Math.min(100, rate))
  return (
    <div className="relative h-1.5 w-full min-w-[70px] rounded-full bg-[var(--rule-2)]">
      <div className="absolute inset-y-0 left-0 rounded-full bg-[var(--accent)]"
           style={{ width: `${w}%` }} />
    </div>
  )
}

/** 命中率**区间**（Wilson 95%）画成误差棒。
 *
 * ★定义域**固定 0–100**，不是按这一块自己的极差铺满 —— 两块必须画在
 *   **同一把尺子**上。按本块极差铺满的话，样本少的那块看起来区间更宽，
 *   而那是尺子造成的，不是数据说的。
 * ★颜色是**中性色**：命中率不是盈亏（同 `RateBar` 那句）。
 * ★`preserveAspectRatio="none"` 下横向被拉伸，三条线统统加
 *   `vectorEffect="non-scaling-stroke"` —— 不加的话竖着的点估计标记会被压成一条缝。
 * ★这一屏要回答的唯一问题是"区间跨没跨过 50%"，所以参考线是**必填的**。
 */
const CI_W = 600
const CI_H = 28
function CIBar({ lo, hi, rate }: { lo: number; hi: number; rate: number | null }) {
  // ★定义域固定 0–100。这**一行**就是"两块在同一把尺子上"的全部实现 ——
  //   所以它值得有一条判据，也值得有一条注入去打它。
  const dLo = 0, dHi = 100
  const x = (v: number) => ((Math.max(dLo, Math.min(dHi, v)) - dLo) / (dHi - dLo)) * CI_W
  const y = CI_H / 2
  return (
    <svg viewBox={`0 0 ${CI_W} ${CI_H}`} preserveAspectRatio="none"
         className="mt-1.5 h-[28px] w-full" id="lgCIBar" data-k="ciBar"
         role="img" aria-label={`命中率 95% 区间 ${lo}–${hi}%`}>
      {/* 50% 参考线：抛硬币那把尺子 */}
      <line data-k="ciMid" x1={x(50)} y1={0} x2={x(50)} y2={CI_H}
            stroke="var(--ink-3)" strokeWidth={1} strokeDasharray="4 4"
            vectorEffect="non-scaling-stroke" />
      {/* 区间本体 */}
      <line data-k="ciRange" x1={x(lo)} y1={y} x2={x(hi)} y2={y}
            stroke="var(--accent)" strokeWidth={6} strokeLinecap="round"
            vectorEffect="non-scaling-stroke" />
      {/* 点估计：画成短线而不是圆点 —— 圆点会被 `preserveAspectRatio` 压扁 */}
      <line data-k="ciRate" x1={x(rate ?? 0)} y1={y - 7} x2={x(rate ?? 0)} y2={y + 7}
            stroke="var(--ink)" strokeWidth={2} strokeLinecap="round"
            vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

/** 一块分区（live / backfill）。`data-source` 是探针分辨这两块的锚点。 */
function Block({ b }: { b: LedgerBlock }) {
  const lab = sourceLabel(b.source)
  const t = b.total
  const v = b.verdict
  const cov = b.coverage
  return (
    <section className="lg-block card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4"
             id={`lgBlock-${b.source}`} data-source={b.source} data-predlen={b.pred_len}
             aria-label={`台账分区 ${b.source}`}>
      <div className="chd mb-2 flex flex-wrap items-baseline gap-2">
        <h2 className="text-base font-semibold text-[var(--ink)]">{lab.title}</h2>
        <Badge variant="neutral">预测 {b.pred_len} 根 K 线</Badge>
        <Badge variant="neutral">{b.n_total} 条记录</Badge>
      </div>
      <p className="mb-3 text-xs text-[var(--ink-3)]">{lab.note}</p>

      {/* ★`t` 为空 = **还没有到期的**。这一屏一个命中率数字都不许有。 */}
      {t ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-2">
              <div className="text-[10.5px] text-[var(--ink-3)]">方向命中率（涨跌不到 ±0.3% 算没表态）</div>
              <div className="tnum text-base text-[var(--ink)]" data-k="hit">
                {t.dir_band.hit_rate === null ? "—" : `${num(t.dir_band.hit_rate, 1)}%`}
              </div>
              <div className="mt-1"><RateBar rate={t.dir_band.hit_rate ?? 0} /></div>
            </div>
            {/* ★基线是**必填项**，不是可选装饰：单个命中率没有信息量 */}
            <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-2">
              <div className="text-[10.5px] text-[var(--ink-3)]">同期「无脑喊涨」</div>
              <div className="tnum text-base text-[var(--ink)]" data-k="base">
                {t.baseline_always_up.hit_rate === null ? "—"
                  : `${num(t.baseline_always_up.hit_rate, 1)}%`}
              </div>
            </div>
            <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-2">
              <div className="text-[10.5px] text-[var(--ink-3)]">同期「跟随趋势」</div>
              <div className="tnum text-base text-[var(--ink)]">
                {t.baseline_momentum.hit_rate === null ? "—"
                  : `${num(t.baseline_momentum.hit_rate, 1)}%`}
              </div>
            </div>
            <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-2">
              <div className="text-[10.5px] text-[var(--ink-3)]">已到期条数</div>
              <div className="tnum text-base text-[var(--ink)]" data-k="n">
                {t.dir_band.n_called} / {b.n_total}
              </div>
            </div>
            <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-2">
              <div className="text-[10.5px] text-[var(--ink-3)]">收盘价误差（模型 / 假设价格不动）</div>
              <div className="tnum text-base text-[var(--ink)]">
                {num(t.mape_close_model, 2)}% / {num(t.mape_close_flat, 2)}%
              </div>
            </div>
          </div>

          {/* ★区间**算不出来**时这里一个节点都不许有（连兜底常量都不许）——
              一根编出来的棒比没有棒坏得多：它看起来就是"量过了"。
              ★写成**单行**：故障注入的锚点就是这一整行，跨行的话下次改格式
              就会静默漂掉（本仓库刚踩过一次，见 verify_agent.py 里那条元注入）。 */}
          {t.dir_band.ci95 ? <CIBar lo={t.dir_band.ci95[0]} hi={t.dir_band.ci95[1]} rate={t.dir_band.hit_rate} /> : null}

          <p className="mt-2 text-xs text-[var(--ink-2)]" data-k="ci">
            95% 区间 {t.dir_band.ci95 ? `${num(t.dir_band.ci95[0], 1)}–${num(t.dir_band.ci95[1], 1)}%` : "—"}；
            {v && v.beats_coin === false
              ? "这个区间跨着 50%，也就是和抛硬币分不出来 —— 样本还太少。"
              : v && v.beats_coin === true
                ? "区间下界在 50% 之上。"
                : "还判不了。"}
            {v && v.margin !== null
              ? `　比上面两个参照里更强的那个${v.beats_baseline ? "高" : "低"} ${num(Math.abs(v.margin), 1)} 个百分点。`
              : ""}
          </p>
        </>
      ) : (
        <p className="text-sm text-[var(--ink-2)]" id={`lgEmpty-${b.source}`}>
          <b>还没有到期的记录</b>，所以这一块不给命中率 —— 显示 0% 会被读成"算出来是 0"，
          而事实是"还没到期，答案还不知道"。{b.n_pending} 条在路上。
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs">
        {/* ★三个数分开列：在等 / 算不了 / 对不上。合成一个的话，
            "还有 78 条在路上"会读成"78 条坏了"。 */}
        {b.n_pending ? (
          <span className="text-[var(--ink-3)]" data-k="pending">
            还没到期：{b.n_pending} 条
          </span>
        ) : null}
        {b.n_skip ? (
          <span className="text-[var(--ink-3)]" data-k="skip" id={`lgSkip-${b.source}`}>
            永远算不了：{b.n_skip} 条{b.skip_reasons.length ? `（${b.skip_reasons.join("；")}）` : ""}
          </span>
        ) : null}
        {b.skipped.length ? (
          <span className="text-[var(--ink-3)]" data-k="bad">
            和真实行情对不上：{b.skipped.length} 条
          </span>
        ) : null}
        {/* ★覆盖率：算不出来就**不给数字**，而且**理由由服务端给**（第 4 条纪律）。
            ★理由为什么不写在这行字里：写在页面上的第二处真源会**静默变成假话** ——
              "还没校准"在离线校准（`backtest_kronos.py --coverage`）跑完之后就不再成立，
              而它和真的理由长得一模一样，从页面上看不出哪句已经过期。
              `ledger.coverage()` 的 `why` 分得出两种情况（台账里根本没有多采样区间 /
              有区间但一条都没到期），它是唯一知道当前是哪一种的地方。 */}
        <span className="text-[var(--ink-3)]" data-k="coverage" id={`lgCov-${b.source}`}>
          区间覆盖率：{cov.rate === null ? cov.why || "算不出来，不给数字" : `${num(cov.rate, 1)}%`}
        </span>
      </div>
    </section>
  )
}

export function Ledger({ probe = false, initial }: { probe?: boolean; initial?: LedgerResp }) {
  const [resp, setResp] = React.useState<LedgerResp | null>(() => initial ?? null)
  const [loading, setLoading] = React.useState(!initial)
  const accHtml = React.useMemo(readAccBoot, [])

  React.useEffect(() => {
    if (probe || initial) return
    setLoading(true)
    // ★用户这条路带 `refresh: 1`：顺手把到期的回填了，战绩就自己往前走。
    loadLedger(true)
      .then((r) => { setResp(r); setLoading(false) })
      .catch(() => setLoading(false))
  }, [probe, initial])

  const s = resp?.summary
  const blocks = resp?.blocks ?? []

  return (
    <Shell probe={probe}>
      <div className="mb-3 mt-3 flex flex-wrap items-center gap-2">
        <h1 className="serif text-xl font-semibold text-[var(--ink)]">预测台账</h1>
        <span className="text-xs text-[var(--ink-3)]">每一次预测都记下来，到期拿真实行情打分</span>
        {s ? <Badge variant="neutral">{s.n} 条记录</Badge> : null}
      </div>
      <div className="grid grid-cols-1 items-start gap-4">
        {loading ? (
          <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4">
            <span className="text-xs text-[var(--ink-3)]">正在读记录…（顺带把已到期的几条拿去和真实行情核对，要联网取数，几秒钟）</span>
          </section>
        ) : null}

        {/* ① 台账在哪 —— 这个站往磁盘上写了什么，得说清楚 */}
        <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" id="lgWhere">
          <div className="chd mb-1">
            <h2 className="text-base font-semibold text-[var(--ink)]">这些记录存在哪儿</h2>
          </div>
          <p className="text-sm text-[var(--ink-2)]">
            一份只追加的流水账，一行一条，存在运行这个站的机器上。
            <b>只记模型自己的输出</b>（标的、预测路径、到期后的真实涨跌）—— 你问了什么、画像填了什么，
            <b>一个字都不进来</b>。没有删除接口。
          </p>
          {s && s.bad_lines.length ? (
            <p className="mt-2 rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] p-2 text-xs text-[var(--warn-ink)]" id="lgBad">
              有 {s.bad_lines.length} 条记录读不动（多半是写到一半被打断），
              已跳过、不影响其余统计 —— 那几条的战绩查不到了。
            </p>
          ) : null}
        </section>

        {/* ② 分区。**每一块各说各的**，绝不合并 */}
        {blocks.length ? blocks.map((b) => <Block key={b.source + b.pred_len} b={b} />) : (
          !loading ? (
            <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" id="lgNoBlock">
              <p className="text-sm text-[var(--ink-2)]">
                台账还是空的 —— 去<b>对话页</b>让它跑一次 K 线预测，那条记录就会出现在这儿；
                预测几根 K 线，就等几个交易日，到期再回来就有真实结果可以打分了。
              </p>
            </section>
          ) : null
        )}

        <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" aria-label="风险提示">
          <Callout variant="warning" title="这份战绩说明的是模型，不是市场">
            台账里的命中率是<b>这个模型在这个站上跑出来的历史</b>，它<b>不预示下一条预测准不准</b>。
            离线回测已经给出过硬结论：这个模型只看了价格走势，<b>没能证明自己比「无脑喊涨」强</b>。
            下面那句实测数字是同一个结论的另一种说法。
          </Callout>
          <div className="disclaim mt-3" id="disclaim">
            <b>风险提示</b>：站内的预测来自只看了价格序列的小模型，不含基本面与新闻；
            所有内容仅供学习，<b>不构成投资建议</b>。
            <div className="acc-line" dangerouslySetInnerHTML={{ __html: accHtml }} />
          </div>
        </section>
      </div>
    </Shell>
  )
}
