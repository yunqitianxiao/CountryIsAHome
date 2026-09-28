/**
 * 迷你走势线（内联 SVG）。**`/dash` 的走势列与主页行情条共用这一个实现。**
 *
 * ★为什么必须共用：这两处在页面上长得一模一样、说的是同一件事（"这三个月什么形状"）。
 *   各写一份的话，改了一处另一处不会跟着变 —— 而它们在截图上看不出是两份代码，
 *   于是"其中一处坏了"这件事没有任何人会发现（判据只会各红各的）。
 *
 * ★三条实现纪律（每一条都对应一条判据）：
 *   ① **点必须是从收盘价抽样来的**（`dash.ts` 的 `sparkOf`），不是画的示意图。
 *      用的是 `polyline` 而不是 `path`：`points` 是一串**能被读回来的数**（数得出几个点、
 *      算得出 y 有几种值），而 `d` 是一串绘图指令，判据要解一遍迷你 SVG 语法才能问
 *      同一句话 —— 判据越像"把实现再写一遍"，它自己坏掉时越哑。
 *   ② **y 轴按这一条线自己的极差铺满**：五只标的的价格量级差几个数量级，
 *      共用一根纵轴的话除了一只以外全是直线。代价是"陡峭程度不可比" ——
 *      所以它只回答"这三个月什么形状"，不回答"谁涨得多"，后者是旁边那个百分数的事。
 *   ③ **染色跟着这一段窗口的净涨跌走**（红涨绿跌），不跟"今日涨跌"走：
 *      一条三个月的线染成"今天涨"的颜色，会被读成"今天这根"。
 */
export type SparkPt = { i: number; c: number }

export function Spark({ pts, w = 76, h = 26, label }: {
  pts?: SparkPt[]
  w?: number
  h?: number
  /** 无障碍名。★空着的斜线图对读屏软件是纯噪声，所以给了就渲染。 */
  label?: string
}) {
  if (!pts || pts.length < 2) {
    // ★取不到就**不画**，也不画一根平的糊弄过去 —— 一根平线会被读成"它一直没动"，
    //   而事实是"这一格的走势没有数据"。这两种在截图上一模一样。
    return <span className="text-[10px] text-[var(--ink-3)]" data-k="sparkNone">没走势</span>
  }
  const cs = pts.map((p) => p.c)
  const lo = Math.min(...cs)
  const hi = Math.max(...cs)
  const pad = 2
  const span = hi - lo
  const x = (i: number) => (i / (pts.length - 1)) * w
  // 极差为 0（整段一个价）时压在中线：除法会给出 NaN，而 NaN 拼出来的 `points`
  // 是空串，线会**整条消失** —— 看起来像"没有走势"，实际是"它一直没动"。
  const y = (c: number) => (span > 0 ? pad + (1 - (c - lo) / span) * (h - 2 * pad) : h / 2)
  const points = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.c).toFixed(1)}`).join(" ")
  const up = cs[cs.length - 1] >= cs[0]
  return (
    <svg className="shrink-0" width={w} height={h} viewBox={`0 0 ${w} ${h}`} data-k="spark"
         role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <polyline points={points} fill="none" strokeWidth="1.5"
                strokeLinejoin="round" strokeLinecap="round"
                style={{ stroke: up ? "var(--up)" : "var(--down)" }} />
    </svg>
  )
}
