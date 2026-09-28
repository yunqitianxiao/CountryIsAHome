/**
 * 估值分位卡 —— `get_fundamentals` 那条**数据通道**画出来的东西。
 *
 * ★这张卡最容易犯的错：把"分位低"画成"好"。所以这里有三条硬约束：
 *
 * 1. **不许用涨跌色**。`--up`/`--down` 是给 K 线的高低开收用的（红涨绿跌是法）。
 *    估值分位不是"涨/跌"，PE 处在 3% 分位既不红也不绿 —— 它只说明"跟它自己的
 *    过去比，现在偏便宜"，而不是"便宜"。用涨跌色等于替用户下了结论。
 *    所以条子统一用 `--accent`，颜色留在 CSS token 里，JS 里一个色值都不写。
 *
 * 2. **时序分位 ≠ 截面分位**。后端给的是这只股**跟自己的过去**比。
 *    这句话必须出现在**用户看得见的地方**（说明条），不能只写在给大模型的摘要里
 *    —— 否则用户会读成"比同行便宜"。这跟 KChart 那条 `.cap` 是同一个道理。
 *
 * 3. **取不到就是取不到**。分位算不出来（样本不够 / PE 为负 / 该标的没有估值）时，
 *    条子画成空槽并写"取不到"，**绝不画成 0%** —— 0% 是个有意义的数
 *    （历史上最便宜），画成 0% 就是编数。
 *
 * ★分位和底下那条走势线**必须同一个窗口**（服务端 `feed.PCTILE_WINDOW`）。
 *   这条线原来画的是近 1 年、分位算的是近 5 年 —— 一张卡两个窗口，
 *   用户会看着 1 年的线读"近 5 年分位"。现在线画 5 年（服务端按月抽稀到 ~60 点），
 *   并且纵轴用服务端给的**整窗口** lo/hi 定标（不是抽样点上的极值），
 *   于是"这条线现在贴着底 ⇒ 分位 3.4%"这句话用户**拿眼睛就能验**。
 *
 * 类名是判据的契约（探针按这些选择器量宽高，不读文本）：
 *   `.fundcard`（根）、`.fbar-fill`（分位条的填充，宽度 = 分位值）、
 *   `.fspark`（近 5 年 PE 走势的 svg）、`.cap`（说明条，**必须是最后一个子节点**）。
 */
export type FundSeries = {
  t: string[]
  pe: (number | null)[]
  pb?: (number | null)[]
  /** 服务端给的人话窗口名（"近 5 年（按月取月末）"）。**不许在前端自己编**。 */
  span?: string
}

export type FundData = {
  kind: "fundamentals"
  name?: string
  code?: string
  industry?: string | null
  pe?: number | null
  pb?: number | null
  pe_pctile_5y?: number | null
  pb_pctile_5y?: number | null
  /** 近 5 年区间的两个端点（与分位同窗口）。有它们，分位才是可验的。 */
  pe_lo5y?: number | null
  pe_hi5y?: number | null
  pb_lo5y?: number | null
  pb_hi5y?: number | null
  mkt_cap_yi?: number | null
  valuation_date?: string | null
  series?: FundSeries | null
}

const num = (v: unknown): number | null =>
  typeof v === "number" && isFinite(v) ? v : null

const rng = (lo: number | null, hi: number | null): string =>
  lo !== null && hi !== null ? `${lo}~${hi}` : ""

/** 分位条。`pctile` 为 null 时画空槽 + "取不到"（**不是** 0%）。 */
function PBar({ label, value, pctile, lo, hi }: {
  label: string; value: number | null; pctile: number | null
  lo: number | null; hi: number | null
}) {
  const has = pctile !== null
  // 夹到 [1.5, 100]：0.4% 这种极端值也要看得见一点点，否则条子整个消失、
  // 看起来像"没渲染"。1.5% 是"看得见"的下限，不是数据。
  const w = has ? Math.min(100, Math.max(1.5, pctile)) : 0
  const range = rng(lo, hi)
  return (
    <div className="frow">
      <span className="flabel">{label}</span>
      <span className="fval">{value === null ? "—" : value.toFixed(2)}</span>
      <span className={has ? "fbar" : "fbar none"}
            role="img" aria-label={`${label} 分位 ${has ? pctile + "%" : "取不到"}`}>
        <i className="fbar-fill" style={{ width: w + "%" }} />
      </span>
      <span className={has ? "fpct" : "fpct none"}>{has ? pctile.toFixed(1) + "%" : "取不到"}</span>
      {range ? <span className="frange">近5年 {range}</span> : null}
    </div>
  )
}

/** 近 5 年 PE 走势。**手写 SVG**，不用 Recharts/Tremor ——
 *  它们靠 ResizeObserver 量尺寸，异步；而这张卡要在服务端事件到达的**同一个 tick**
 *  里就画出可量的宽度（探针量的是 getBoundingClientRect），异步那半赶不上。
 *  这也是本仓库换成 SPA 时踩过的坑（Tremor Raw 的 AreaChart 同步出不了图）。
 *
 *  ★svg 里**一个字形都没有**（只有 path）。原因：这条线为了跟卡片等宽用了
 *    `preserveAspectRatio="none"`（横向拉满、纵向定高 96px），而 svg 里的
 *    `<text>` 和 `<circle>` 会**跟着一起被横向拉扁**（字变胖、圆点变椭圆）。
 *    所以区间/窗口名这些**字**全放在下面的 HTML 里，svg 只留几何。
 *    描边用 `vector-effect: non-scaling-stroke`（CSS 里给），否则线宽也被拉。 */
function Spark({ series, lo, hi }: { series: FundSeries; lo: number | null; hi: number | null }) {
  const pts = (series.pe || []).map(num)
  const ok = pts.map((v, i) => ({ v, i })).filter((p) => p.v !== null) as { v: number; i: number }[]
  if (ok.length < 2) return null

  const W = 660, H = 96, PT = 8, PB = 8
  // ★纵轴用**服务端给的整窗口** lo/hi 定标，不是抽样点上的极值：
  //   抽样（月末）会漏掉月中的高低点，用它定标会让轴上的数字与线对不上。
  //   拿不到整窗口极值时退回抽样极值（退化，但至少画得出来）。
  const vals = ok.map((p) => p.v)
  const ylo = lo !== null ? lo : Math.min(...vals)
  const yhi = hi !== null ? hi : Math.max(...vals)
  const span = yhi - ylo || 1
  const n = pts.length
  const X = (i: number) => (i / Math.max(1, n - 1)) * W
  // 值超出整窗口（理论上不会，抽样点是窗口子集）时夹回框内，别画到图外
  const Y = (v: number) => {
    const r = (v - ylo) / span
    return PT + (1 - Math.min(1, Math.max(0, r))) * (H - PT - PB)
  }

  const d = ok.map((p, k) => (k ? "L" : "M") + X(p.i).toFixed(1) + " " + Y(p.v).toFixed(1)).join(" ")
  const last = ok[ok.length - 1]

  return (
    <>
      <svg className="fspark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
           role="img" aria-label={`PE(TTM) 走势，${series.span || "近 5 年"}`}>
        <path className="fspark-line" d={d} />
        {/* 「现在」标一条竖虚线（不是圆点：圆点在拉扁的坐标系里会变椭圆）。
            只标位置，不标"高/低"。 */}
        <path className="fspark-now" d={`M${X(last.i).toFixed(1)} ${PT} L${X(last.i).toFixed(1)} ${H - PB}`} />
      </svg>
      {/* ★窗口名由服务端给，不在这里写死 —— 服务端换窗口时这里必须跟着变，
          写死就变成"图上标着 5 年、数据其实 1 年"这种没人会发现的错。 */}
      <div className="fspark-legend">
        <span className="fspark-span">{series.span || ""}</span>
        {lo !== null && hi !== null
          ? <span className="fspark-rng">纵轴 {lo}~{hi}（与分位同窗口）</span>
          : null}
        <span>竖虚线 = 最新一个点</span>
      </div>
    </>
  )
}

export function FundCard({ data }: { data: FundData }) {
  const d = data || ({} as FundData)
  const pe = num(d.pe), pb = num(d.pb)
  const peP = num(d.pe_pctile_5y), pbP = num(d.pb_pctile_5y)
  const cap = num(d.mkt_cap_yi)
  const hasPct = peP !== null || pbP !== null

  return (
    <div className="chart fundcard">
      <div className="fhead">
        <b className="fname">{d.name || d.code || "该标的"}</b>
        {d.code ? <span className="fcode">{d.code}</span> : null}
        {d.industry ? <span className="find">{d.industry}</span> : null}
        {cap !== null ? <span className="fcap2">总市值 {cap} 亿</span> : null}
      </div>

      <PBar label="PE(TTM)" value={pe} pctile={peP}
            lo={num(d.pe_lo5y)} hi={num(d.pe_hi5y)} />
      <PBar label="PB" value={pb} pctile={pbP}
            lo={num(d.pb_lo5y)} hi={num(d.pb_hi5y)} />

      {/* ★一个分位都算不出来时，明说为什么可能，而不是留个空槽让人猜 */}
      {hasPct ? null : (
        <p className="fnone">
          这只标的的近 5 年分位算不出来（上市不足 5 年、历史 PE 为负、或该标的没有估值数据）。
        </p>
      )}

      {d.series ? <Spark series={d.series} lo={num(d.pe_lo5y)} hi={num(d.pe_hi5y)} /> : null}

      {/* ★说明条必须是**最后一个子节点**（探针选择器是 `#msgs .chart .cap`） */}
      <div className="cap">
        <b>分位是纵向的，不是横向的</b>：拿这只股票<b>跟它自己的近 5 年</b>比
        {d.valuation_date ? `（截至 ${d.valuation_date}）` : ""}，<b>不是</b>跟同行比
        —— 两个口径会给出相反的结论。<b>低分位不等于便宜</b>：银行地产的 PE/PB
        天生低于成长股，跨行业比没有意义。这里没有股息率（本仓库取不到）。
      </div>
    </div>
  )
}
