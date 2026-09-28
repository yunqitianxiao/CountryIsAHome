/**
 * 新五页共用的**图表件**。共两个：横向条形图（Recharts）与蜡烛面板（手写 SVG）。
 *
 * ★**为什么这里只有条形和蜡烛，没有折线**：折线/面积那种"随时间走"的图
 *   直接用 Tremor 的 `AreaChart`（它已经带 `fixedWidth`/`fixedHeight` 那两枚
 *   定尺寸分支的 props，见 `utils/ChartSizer.tsx`）。在这里再包一层
 *   只会多出一个需要跟着上游改的副本。
 *
 * ★★**定尺寸分支是这个文件存在的理由**（规格 §3.6 点名的那条坑）：
 *   Recharts 的 `ResponsiveContainer` 尺寸来自 `ResizeObserver` 回调，而那是
 *   **异步**的；它的 sizes 初值是 −1，recharts 在 `< 0` 时**直接 return null**
 *   ⇒ 首帧必然出不了图。同步探针量到的于是不是"图坏了"，而是"图还没来"
 *   （判据**消失**，不是变红）。所以每一张图都能被告知一个确定宽高：
 *   `probe` 为真时**必须**给，交互路径不给（保持响应式）。
 *   先例与实测数字在 `utils/ChartSizer.tsx` 顶部，别重写一套。
 */
import * as React from "react"
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, XAxis, YAxis, Tooltip,
} from "recharts"
import { ChartSizer } from "../utils/ChartSizer"

/** 图表里做标记用的**语义色**（走 token，不写死色值）。
 *  ★`up`/`down` **不许对调** —— 红涨绿跌是 A 股习惯（见 `format.ts` 顶部）。 */
export const TONE_VAR = {
  accent: "var(--accent)",
  f2: "var(--f2)",
  f5: "var(--f5)",
  ink3: "var(--ink-3)",
  up: "var(--up)",
  down: "var(--down)",
} as const
export type ToneKey = keyof typeof TONE_VAR

/** 浮层。**必须自定义**：Recharts 默认那个是白底黑字写死的，
 *  在本站两套主题的暗色那一套里是一块刺眼的白斑。 */
function TipBox({ rows, label }: { rows: { k: string; v: string }[]; label?: React.ReactNode }) {
  return (
    <div className="rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1 text-xs shadow-sm">
      {label !== undefined && label !== "" ? (
        <div className="font-medium text-[var(--ink)]">{label}</div>
      ) : null}
      {rows.map((r, i) => (
        <div key={i} className="text-[var(--ink-2)]">
          {r.k} <span className="tnum text-[var(--ink)]">{r.v}</span>
        </div>
      ))}
    </div>
  )
}

/** 横向条形图。**一行 = 一个类目**（板块涨跌、z-score、各周期共振分…）。
 *
 *  ★为什么横着：类目名是中文（"光学光电子""同板块"），竖着画就得斜排或截断，
 *    而截断之后的类目名读不出是哪一个 —— 那这张图就白画了。
 *  ★`layout="vertical"` 在 Recharts 里是**反的**（vertical = 条是横的），
 *    这是那个库的历史命名，不是我们写错了。 */
export function HBars({
  rows, index, value, tone = "accent", probe, height, width = 520,
  digits = 2, suffix = "", domain, labelFormatter, k,
}: {
  rows: Record<string, unknown>[]
  index: string
  value: string
  tone?: ToneKey | ((row: Record<string, unknown>) => ToneKey)
  probe?: boolean
  height?: number
  width?: number
  digits?: number
  suffix?: string
  /** `[lo, hi]`。**给了就是钉死定义域** —— 几条并排的图共用一把尺子时必给，
   *  否则每条按自己的极差铺满，"谁更长"就变成了尺子的事。 */
  domain?: [number, number]
  labelFormatter?: (v: number) => string
  k?: string
}) {
  const h = height ?? Math.max(80, rows.length * 26 + 24)
  const fmt = labelFormatter ?? ((v: number) => `${v.toFixed(digits)}${suffix}`)
  return (
    <div data-k={k} style={{ width: "100%" }}>
      <ChartSizer width={probe ? width : undefined} height={probe ? h : undefined}>
        <BarChart
          layout="vertical"
          data={rows}
          width={probe ? width : undefined}
          height={probe ? h : undefined}
          margin={{ top: 4, right: 46, bottom: 4, left: 4 }}
          barCategoryGap={2}
        >
          <CartesianGrid horizontal={false} className="stroke-gray-200 stroke-1 dark:stroke-gray-800" />
          <XAxis
            type="number" domain={domain ?? ["auto", "auto"]} tickLine={false} axisLine={false}
            className="text-xs fill-gray-500" tickFormatter={fmt} allowDecimals
          />
          <YAxis
            type="category" dataKey={index} width={96} tickLine={false} axisLine={false}
            className="text-xs fill-gray-500"
          />
          <Tooltip
            cursor={{ fill: "var(--rule-2)" }}
            content={({ active, payload, label }) => {
              if (!active || !payload || !payload.length) return null
              const v = payload[0].value
              return (
                <TipBox label={label as React.ReactNode}
                        rows={[{ k: "", v: typeof v === "number" ? fmt(v) : String(v) }]} />
              )
            }}
          />
          <Bar dataKey={value} isAnimationActive={false} radius={[0, 4, 4, 0]} maxBarSize={18}>
            {rows.map((r, i) => (
              <Cell key={i}
                    fill={TONE_VAR[typeof tone === "function" ? tone(r) : tone]} />
            ))}
            {/* ★直接标注**只给值**，不给类目名：类目名在左边的轴上，
                两边都写就是同一件事说两遍，而右边那一栏会被长数字挤爆。 */}
            <LabelList dataKey={value} position="right"
                       className="fill-gray-500 text-[10px]"
                       formatter={(v: number) => (typeof v === "number" ? fmt(v) : "")} />
          </Bar>
        </BarChart>
      </ChartSizer>
    </div>
  )
}

/** 竖向条形图（成交量那种"按时间排"的）。x 轴是类目、按给定顺序，
 *  **不排序** —— 时间序列排过序就不是时间序列了。 */
export function VBars({
  rows, index, value, tone = "accent", probe, height = 120, width = 620,
  k, ariaLabel,
}: {
  rows: Record<string, unknown>[]
  index: string
  value: string
  tone?: ToneKey | ((row: Record<string, unknown>) => ToneKey)
  probe?: boolean
  height?: number
  width?: number
  k?: string
  ariaLabel?: string
}) {
  return (
    <div data-k={k} role={ariaLabel ? "img" : undefined} aria-label={ariaLabel}
         style={{ width: "100%" }}>
      <ChartSizer width={probe ? width : undefined} height={probe ? height : undefined}>
        <BarChart
          data={rows}
          width={probe ? width : undefined}
          height={probe ? height : undefined}
          margin={{ top: 4, right: 8, bottom: 4, left: 8 }}
          barCategoryGap={1}
        >
          <XAxis dataKey={index} hide />
          <YAxis hide domain={[0, "auto"]} />
          <Tooltip
            cursor={{ fill: "var(--rule-2)" }}
            content={({ active, payload, label }) => {
              if (!active || !payload || !payload.length) return null
              const v = payload[0].value
              return (
                <TipBox label={label as React.ReactNode}
                        rows={[{ k: "", v: typeof v === "number" ? v.toLocaleString("zh-CN") : String(v) }]} />
              )
            }}
          />
          <Bar dataKey={value} isAnimationActive={false} radius={[4, 4, 0, 0]}>
            {rows.map((r, i) => (
              <Cell key={i} fill={TONE_VAR[typeof tone === "function" ? tone(r) : tone]} />
            ))}
          </Bar>
        </BarChart>
      </ChartSizer>
    </div>
  )
}

// ── 蜡烛 + 成交量 + 叠加线（手写 SVG）──────────────────────────────────

/** ★`v`（成交量）**可有可无**：分钟线的契约是 `CANON`，里面有 `volume`；
 *  但后端可以只给价不给量。缺了就不画量带，**不补一根 0 高度的柱子** ——
 *  0 高度和"这一分钟没有成交"在图上一样，而后者是个真结论。 */
export type Candle = { t: string; o: number; h: number; l: number; c: number; v?: number }

/** 一根蜡烛的**语义色类名**：平盘算涨（`>=`，跟 `KChart` 和旧页面一致）。
 *  ★颜色**不写进 JSX 的 fill 属性** —— 写了的话两套主题得在 JS 里各写一遍，
 *    暗色必然漏。这里只输出 `up`/`dn` 两个类名，颜色由 `index.css` 里
 *    `.up .k-body{fill:var(--up)}` 给（那条规则是现成的，本文件直接复用）。 */
const candleCls = (b: Candle) => (b.c >= b.o ? "up" : "dn")

/** 同一根蜡烛的**颜色变量**（给那些 `index.css` 里没有对应规则的标记用）。
 *
 *  ★为什么蜡烛走类名、这几个标记却写 `style`：`index.css` 里现成的只有
 *    `.up .k-body` / `.up .k-wick` 两条（`KChart` 用得上），成交量柱和价值区
 *    在那边**没有规则**。而那个文件归设计系统那一版所有，本页不许去加。
 *  ★★这**不是**"把颜色写进 JS"：写进去的是 `var(--up)` 这个**token 引用**，
 *    不是色值 —— 两套主题各定义一次 `--up`，这里跟着翻，仍然只有一个出处。
 *    真正被那条纪律禁掉的是 `fill="#e5765c"` 这种字面量。 */
const candleVar = (b: Candle) => (b.c >= b.o ? "var(--up)" : "var(--down)")

/** 盘中面板：**蜡烛 + 成交量 + VWAP + 成交量分布的价值区**，四层共用一根横轴。
 *
 *  ★★**为什么四层画在一个 SVG 里，而不是四个图叠起来**：它们回答的是同一个
 *    问题（"这一根的量价关系"）——分开画的话四张图的横轴各自缩放，
 *    于是"放量的那根"和"价格突破的那根"会**指在不同的 x 上**，而四张图
 *    在截图上看不出这件事。共用 `X(i)` 是这张图存在的全部理由。
 *
 *  ★**为什么蜡烛不用 Recharts**：它不是蜡烛图库，硬套会丢掉高开低收，
 *    而高/低点（影线）正是这张图存在的理由。先例 `components/KChart/KChart.tsx`
 *    顶上那段；本文件只带走它的三个约定（类名 / 颜色在 CSS / 平盘算涨）。
 *
 *  ★`vwap` / `poc` / `va` 里任何一项是 `null` 就**那一层不画**，
 *    不画一根平线顶上去 —— 一根平线会被读成"均价一直没动"。
 */
export function CandlePanel({
  bars, vwap, poc, va, probe, width = 660, height = 320,
  k, label,
}: {
  bars: Candle[]
  /** 逐根 VWAP，与 `bars` **按下标对齐**。长度不等就整层不画。 */
  vwap?: (number | null)[] | null
  poc?: number | null
  va?: { lo: number | null; hi: number | null } | null
  probe?: boolean
  width?: number
  height?: number
  k?: string
  label?: string
}) {
  if (!bars.length) return null

  const W = width
  const H = height
  const PL = 52
  const PR = 14
  const PT = 12
  // ★底部的成交量槽是**同一个坐标系里的第二条带**：它占多少高度写死，
  //   不按数据自适应 —— 自适应会让"价"和"量"两块的高度随数据变，
  //   同一只票的两次打开形状不同，读起来像两只票。
  const VH = Math.round(H * 0.22)
  const PB = 22
  const PH = H - PT - PB - VH // 价格带的高度（含它与量带之间的 6px 间隙）
  const GAP = 6

  const n = bars.length
  let lo = Infinity
  let hi = -Infinity
  let vmax = 0
  for (const b of bars) {
    if (b.l < lo) lo = b.l
    if (b.h > hi) hi = b.h
  }
  // ★叠加线（VWAP）与价值区**必须并进纵轴定义域**：不并的话它们会画到
  //   绘图框外面被视口裁掉，症状是"这条线只有上面那半截"（KChart 里同一个坑）。
  const vs: number[] = []
  if (vwap && vwap.length === n) for (const v of vwap) if (v !== null && Number.isFinite(v)) vs.push(v)
  if (poc !== null && poc !== undefined && Number.isFinite(poc)) vs.push(poc)
  if (va) {
    if (va.lo !== null && Number.isFinite(va.lo)) vs.push(va.lo)
    if (va.hi !== null && Number.isFinite(va.hi)) vs.push(va.hi)
  }
  for (const v of vs) {
    if (v < lo) lo = v
    if (v > hi) hi = v
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null
  const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.002 || 1
  lo -= pad
  hi += pad

  const iw = W - PL - PR
  const step = iw / n
  const bw = Math.max(1, Math.min(9, step * 0.62))
  const X = (i: number) => PL + step * (i + 0.5)
  const Y = (v: number) => PT + ((hi - v) / (hi - lo)) * PH
  const VY = (v: number, mx: number) => H - PB - (mx > 0 ? (v / mx) * VH : 0)

  // 量：**逐根都要有**才算"有量"。只有零星几根有 `v` 时整条量带不画 ——
  // 半条量带比没有量带坏：它会让人以为那些空白是真的"零成交"。
  const vols = bars.map((b) => (Number.isFinite(b.v) ? (b.v as number) : 0))
  for (const v of vols) if (v > vmax) vmax = v
  const hasVol = vmax > 0

  const grid = []
  for (let i = 0; i <= 4; i++) {
    const v = lo + ((hi - lo) * i) / 4
    const y = Y(v)
    grid.push(
      <React.Fragment key={`g${i}`}>
        <line className="k-grid" x1={PL} y1={y} x2={W - PR} y2={y} />
        <text className="k-axis" x={PL - 7} y={y + 3.5} textAnchor="end">{v.toFixed(2)}</text>
      </React.Fragment>,
    )
  }

  const candles = bars.map((b, i) => {
    const x = X(i)
    const yo = Y(b.o)
    const yc = Y(b.c)
    return (
      <g className={candleCls(b)} key={i}>
        <line className="k-wick" x1={x} y1={Y(b.h)} x2={x} y2={Y(b.l)} />
        <rect className="k-body" x={x - bw / 2} y={Math.min(yo, yc)} width={bw}
              height={Math.max(1, Math.abs(yc - yo))} />
      </g>
    )
  })

  let vwapPath = ""
  if (vwap && vwap.length === n) {
    const seg: string[] = []
    let open = false
    for (let i = 0; i < n; i++) {
      const v = vwap[i]
      if (v === null || !Number.isFinite(v)) { open = false; continue }
      seg.push(`${open ? "L" : "M"}${X(i).toFixed(1)},${Y(v as number).toFixed(1)}`)
      open = true
    }
    vwapPath = seg.join("")
  }

  const ticks = n > 3 ? [0, Math.floor((n - 1) / 2), n - 1] : [0, n - 1]

  return (
    <div data-k={k} role={label ? "img" : undefined} aria-label={label}
         className="rounded border border-[var(--rule)] bg-[var(--card)] p-2">
      <svg viewBox={`0 0 ${W} ${H}`} style={{ display: "block", width: "100%", height: "auto", maxHeight: 420 }}>
        {grid}

        {/* 价值区（VA）：一条**带**，画在蜡烛下面（叠上去会盖掉影线） */}
        {va && Number.isFinite(va.lo) && Number.isFinite(va.hi) ? (
          <rect data-k="va" x={PL} y={Y(va.hi as number)} width={iw}
                height={Math.max(1, Y(va.lo as number) - Y(va.hi as number))}
                style={{ fill: "var(--accent)", fillOpacity: 0.12 }} />
        ) : null}

        {candles}

        {/* VWAP：一条 2px 的线。★它和 POC 都不用量纲之外的装饰 —— 这两条线
            本身已经是"均价"和"最密集价"的直观表示，加图例就够了 */}
        {vwapPath ? <path data-k="vwap" d={vwapPath} fill="none" stroke="var(--f2)" strokeWidth={2}
                          strokeLinejoin="round" vectorEffect="non-scaling-stroke" /> : null}

        {/* POC：成交量最密集的那个价位 */}
        {poc !== null && poc !== undefined && Number.isFinite(poc) ? (
          <g data-k="poc">
            <line x1={PL} y1={Y(poc)} x2={W - PR} y2={Y(poc)}
                  stroke="var(--f5)" strokeWidth={1} strokeDasharray="4 4"
                  vectorEffect="non-scaling-stroke" />
            <text className="k-axis" x={W - PR} y={Y(poc) - 4} textAnchor="end">POC {poc.toFixed(2)}</text>
          </g>
        ) : null}

        {/* 成交量。红涨绿跌跟着**同一根**蜡烛走，不跟"量比昨天大还是小"走 ——
            跟着量走的话，放量下跌会画成红的，而那正是最容易看反的一格 */}
        {hasVol ? (
          <g data-k="volume">
            {bars.map((b, i) => {
              const v = vols[i]
              const y = VY(v, vmax)
              return (
                <rect key={i} data-k="vol" x={X(i) - bw / 2} y={y} width={bw}
                      height={Math.max(0, H - PB - y)}
                      style={{ fill: candleVar(b), fillOpacity: 0.7 }} />
              )
            })}
            <text className="k-axis" x={PL - 7} y={H - PB - VH + 9} textAnchor="end">
              {(vmax / 10000).toFixed(0)}万
            </text>
          </g>
        ) : null}

        <line className="k-grid" x1={PL} y1={H - PB - VH - GAP} x2={W - PR} y2={H - PB - VH - GAP} />

        {ticks.map((i) => (i >= 0 && i < n ? (
          <text className="k-axis" key={`t${i}`} x={X(i)} y={H - 6} textAnchor="middle">{bars[i].t}</text>
        ) : null))}
      </svg>
    </div>
  )
}
