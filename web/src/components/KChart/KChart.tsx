/**
 * 手写 SVG 蜡烛图。**不用 Recharts** —— 它不是蜡烛图库，硬套会丢掉高开低收，
 * 而高/低点正是这张图存在的理由（影线）。旧页面 index.html:623-709 的 renderChart
 * 就是这个东西，这里是它的等价迁移。
 *
 * ★迁移时**必须原样带走**的三件事（每一件都有判据钉着）：
 *
 * 1. **class 名**：`.k-body` / `.k-wick` / `.k-grid` / `.k-axis` / `.k-base` /
 *    `.k-split` / `g.pred` / `.chart` / `.cap`，以及外层的 `g.up` / `g.dn`。
 *    判据数的是 `rect.k-body` 和 `line.k-wick` 的**个数**，改类名等于把判据变成空转。
 *
 * 2. **红涨绿跌不在 JS 里**：这里只输出 `up`/`dn` 类名，颜色由 CSS 的
 *    `.up .k-body{fill:var(--up)}` 给。★把颜色写进 JSX 的 fill 属性也能"看起来对"，
 *    但那样两套主题就得在 JS 里各写一遍，暗色必然漏 —— 颜色统一留在 CSS token。
 *    判定式是 `c >= o`（**平盘算涨**，跟旧页面一致，别改成 `>`）。
 *
 * 3. **`<g class="pred">` 的边界靠下标**（开于 `i === splitAt`、闭于 `i === n-1`），
 *    不用"上一根是不是预测"这种状态位 —— 状态位在首/尾会漏。
 *
 * ★说明条 `.cap` 是 `div.chart` 的**最后一个子节点**，不是它的兄弟。
 *   探针的选择器是 `#msgs .chart .cap`，层级错了就取不到，`repairNote` 判据变空转。
 */
import * as React from "react"

export type Bar = { t: string; o: number; h: number; l: number; c: number }
export type ChartParams = {
  pred_len?: number
  ms?: number
  ohlc_repaired_bars?: number
  [k: string]: unknown
}
export type ChartData = {
  /** ★服务端真的发这个字段（`tools.py` 里 kline=纯历史 / predict=带预测段）。
   *  它是和 `FundData.kind` 一起构成**判别联合**的那个判别式 ——
   *  没有它，"这张图该按哪种画"就只能靠猜字段（或 `as` 硬塞）。 */
  kind?: "kline" | "predict"
  name?: string
  code?: string
  base_close?: number
  params?: ChartParams
  history?: { t: string[]; o: number[]; h: number[]; l: number[]; c: number[] }
  pred?: { t: string[]; o: number[]; h: number[]; l: number[]; c: number[] } | null
  bars?: { t: string[]; o: number[]; h: number[]; l: number[]; c: number[] }
  /** ★多采样才有的逐根极差（`tools.py` 的 `data.band`），与 `pred` **逐下标对齐**。
   *  N=1 时服务端给 `null` —— 一条路径的 min/max 不是区间，不画。
   *  `level_pct` 是**名义**水平（(N-1)/(N+1)），画在 `.cap` 里，不在图上标注。 */
  band?: { lo: number[]; hi: number[]; level_pct: number; withheld: boolean } | null
}

// 旧页面的坐标常量，一个都没动 —— 它们决定图的长宽比。
const W = 660
const H = 300
const PL = 52
const PR = 14
const PT = 14
const PB = 30

const P = (v: number) => Math.round(v * 100) / 100

/** ★「模型给的高低价不自洽、已重建」这句话必须出现在**用户看得见的地方**，
 *  不能只写在给大模型的摘要里 —— 否则图上那根影线会被当成模型的原话。
 *  返回的是 JSX（旧版返回的是已 esc 的 HTML 串），React 会自动转义。 */
function RepNote({ params }: { params?: ChartParams }) {
  const n = params && params.ohlc_repaired_bars
  if (!n) return null
  return (
    <>
      ；其中 <b>{n}</b> 根的最高/最低价模型给得不自洽（影线穿进实体），已按开/收价重建
    </>
  )
}

export function KChart({ data }: { data: ChartData }) {
  const hist = data.history || data.bars
  if (!hist || !hist.t || !hist.t.length) return null
  const pred = data.pred || null
  /* ★区间只在**长度对得上 `pred`** 时才画。服务端是按下标逐根构造的
     （`tools.py` 里 `lo`/`hi` 与 `central` 同源），长度不等说明契约破了 ——
     那时候**宁可不画**：错位半根画出来的带子看不出错，但它指着的是别的日子。 */
  const band =
    pred && data.band && data.band.lo && data.band.hi &&
    data.band.lo.length === pred.t.length && data.band.hi.length === pred.t.length
      ? data.band
      : null

  const bars: (Bar & { p: boolean })[] = []
  for (let i = 0; i < hist.t.length; i++)
    bars.push({ t: hist.t[i], o: hist.o[i], h: hist.h[i], l: hist.l[i], c: hist.c[i], p: false })
  const splitAt = bars.length
  if (pred)
    for (let i = 0; i < pred.t.length; i++)
      bars.push({ t: pred.t[i], o: pred.o[i], h: pred.h[i], l: pred.l[i], c: pred.c[i], p: true })
  const n = bars.length

  let lo = Infinity
  let hi = -Infinity
  for (let i = 0; i < n; i++) {
    if (bars[i].l < lo) lo = bars[i].l
    if (bars[i].h > hi) hi = bars[i].h
  }
  if (data.base_close) {
    if (data.base_close < lo) lo = data.base_close
    if (data.base_close > hi) hi = data.base_close
  }
  /* ★区间必须**并进纵轴刻度**，不能只画不量 —— 不然带子的两条边会画到绘图框外面
     （被 svg 的视口裁掉），症状是"区间看起来只有上面那半截"。 */
  if (band)
    for (let i = 0; i < band.lo.length; i++) {
      if (band.lo[i] < lo) lo = band.lo[i]
      if (band.hi[i] > hi) hi = band.hi[i]
    }
  const pad = (hi - lo) * 0.06 || hi * 0.02 || 1
  lo -= pad
  hi += pad

  const iw = W - PL - PR
  const ih = H - PT - PB
  const step = iw / n
  const bw = Math.max(1, Math.min(9, step * 0.62))
  const X = (k: number) => PL + step * (k + 0.5)
  const Y = (v: number) => PT + ((hi - v) / (hi - lo)) * ih

  const grid = []
  for (let i = 0; i <= 4; i++) {
    const v = lo + ((hi - lo) * i) / 4
    const y = P(Y(v))
    grid.push(
      <React.Fragment key={`g${i}`}>
        <line className="k-grid" x1={PL} y1={y} x2={W - PR} y2={y} />
        <text className="k-axis" x={PL - 7} y={y + 3.5} textAnchor="end">
          {v >= 1000 ? v.toFixed(0) : v.toFixed(2)}
        </text>
      </React.Fragment>,
    )
  }

  const candles = []
  for (let i = 0; i < n; i++) {
    const b = bars[i]
    // ★平盘算涨（>= 而不是 >），跟旧页面一致
    const cls = b.c >= b.o ? "up" : "dn"
    const x = P(X(i))
    const yh = P(Y(b.h))
    const yl = P(Y(b.l))
    const yo = P(Y(b.o))
    const yc = P(Y(b.c))
    const top = Math.min(yo, yc)
    const hgt = Math.max(1, Math.abs(yc - yo))
    candles.push(
      <g className={cls} key={i}>
        <line className="k-wick" x1={x} y1={yh} x2={x} y2={yl} />
        <rect className="k-body" x={P(x - bw / 2)} y={top} width={P(bw)} height={P(hgt)} />
      </g>,
    )
  }

  /* ★区间带：上沿走一遍、下沿倒着走回来、闭合。横坐标就用**预测段那些蜡烛的中心**
     （`X(splitAt + i)`），不额外外扩半格 —— 带子的宽度就等于"这些根"，多画出去一点
     就得解释那一点是从哪来的。 */
  let bandPath = ""
  if (band) {
    const up: string[] = []
    const dn: string[] = []
    for (let i = 0; i < band.lo.length; i++) {
      const x = P(X(splitAt + i))
      up.push(`${x},${P(Y(band.hi[i]))}`)
      dn.push(`${x},${P(Y(band.lo[i]))}`)
    }
    bandPath = `M${up.join("L")}L${dn.reverse().join("L")}Z`
  }

  const ticks = [0, Math.floor((splitAt - 1) / 2), splitAt - 1]
  if (pred) ticks.push(n - 1)
  const seen: Record<number, number> = {}

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="K线图">
        {grid}
        {data.base_close ? (
          <>
            <line className="k-base" x1={PL} y1={P(Y(data.base_close))} x2={W - PR} y2={P(Y(data.base_close))} />
            <text className="k-axis" x={W - PR} y={P(Y(data.base_close)) - 4} textAnchor="end">
              基准 {data.base_close}
            </text>
          </>
        ) : null}
        {pred && splitAt < n ? (
          <>
            <line className="k-split" x1={P(PL + step * splitAt)} y1={PT} x2={P(PL + step * splitAt)} y2={H - PB} />
            <text className="k-split-l" x={P(PL + step * splitAt) + 4} y={PT + 11}>
              预测起点
            </text>
          </>
        ) : null}
        {/* ★预测段整段包在一个 <g class="pred"> 里 —— 但 JSX 里不能像字符串那样
            先 push 开标签再 push 闭标签，所以这里用**两层嵌套结构**表达同一件事：
            下标落在预测段内的那些 <g> 直接套在 <g className="pred"> 里面。
            边界仍然由下标判断（splitAt / n），没有引入任何状态位。 */}
        {pred && splitAt < n ? (
          <>
            <g>{candles.slice(0, splitAt)}</g>
            <g className="pred">
              {/* ★带子画在**蜡烛之前**（同一个 <g> 里的第一个子节点）：叠在蜡烛上面
                  会盖掉影线，而影线正是这张图存在的理由。 */}
              {bandPath ? <path className="k-band" d={bandPath} /> : null}
              {candles.slice(splitAt)}
            </g>
          </>
        ) : (
          <g>{candles}</g>
        )}
        {ticks.map((k) => {
          if (k < 0 || k >= n || seen[k]) return null
          seen[k] = 1
          return (
            <text className="k-axis" key={`t${k}`} x={P(X(k))} y={H - 10} textAnchor="middle">
              {bars[k].t}
            </text>
          )
        })}
      </svg>

      {/* ★说明条：`.chart` 的最后一个子节点（探针选择器是 `#msgs .chart .cap`） */}
      <div className="cap">
        <span>
          <b>红涨</b> 绿跌
        </span>
        {pred ? (
          <span>
            虚线部分为模型预测，<b>仅供参考</b>，非实际行情
            <RepNote params={data.params} />
          </span>
        ) : (
          <span>历史日K</span>
        )}
        {band ? (
          <span>
            阴影为 <b>{band.lo.length}</b> 条采样路径的<b>名义 {band.level_pct}%</b> 区间
            {/* ★"名义"两字不许删：这带子说的是"N 条路径的极差"，
                不是"价格有这么大可能落在里面"。详见 PredCard 里那段纪律注释。 */}
            {band.withheld ? "（方向弃答）" : ""}
          </span>
        ) : null}
        <span>
          {data.name || data.code || ""}
          {pred && data.params ? ` · 预测 ${data.params.pred_len} 根 · ${data.params.ms || 0}ms` : ""}
        </span>
      </div>
    </div>
  )
}
