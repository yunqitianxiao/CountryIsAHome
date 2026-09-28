/**
 * 组合体检（`/portfolio`）的数据层。
 *
 * ★持仓**不新开一套模型**：直接读游戏页那个 `licai-game-positions`
 *   （`game.ts` 的 `readPositions()`）。叙事也连得上：你在模拟投资里买的每一笔
 *   就是你的组合。新开一个 `POS_KEY` 的话，用户在游戏页买了三笔、体检页说"没有持仓"
 *   —— 而且两边都"没坏"。
 *
 * ★两个入口：`loadPortfolio()` 是用户那条路（异步），`loadPortfolioSync()` 是探针
 *   那条路（**同步 XHR**）。理由同 `dash.ts` 顶部那段：渲染层判据全押在"几何在同一个
 *   tick 里就绪"上，异步取回来的数同步探针量不到 —— 那不是判据变红，是判据**消失**。
 *
 * ★权重由**前端**把 `amount`（金额）归一化成百分比，因为服务端不知道用户总共有多少钱
 *   投进去。归一化在这里只做一次 —— 散在三处做，"加起来不是 100"这种事会在每一步里
 *   各错一遍。
 */
import { post } from "./api"
import { readPositions, type Position } from "./game"

export type Weight = { symbol: string; name: string; weight: number }

export type Grade = { item: string; level: string; value: number; why: string }

export type Corr = { codes: string[]; names: string[]; m: number[][] }

export type PortfolioData = {
  kind: "portfolio"
  hypothetical: boolean
  weights: Weight[]
  series: { dates: string[]; nav: number[] } | null
  corr: Corr | null
  grades: Grade[]
  industry: Record<string, string[]> | null
  stress: {
    basis: string
    beta: number
    index_shock: number[]
    port_shock: number[]
    hist_worst: number | null
    caveat: string
  } | null
  dropped: { name?: string; symbol?: string; why: string }[] | null
  notes: string[]
  metrics: Record<string, number | null>
}

export type PortfolioResp = {
  summary: {
    ok: boolean
    error?: string
    msg?: string
    hint?: string
    n_holdings?: number
    caveat?: string
    [k: string]: unknown
  }
  data: PortfolioData | null
}

/** 一笔持仓 → `{symbol, name, weight}`；`total` 是本金合计。 */
export function toWeights(list: Position[], total: number): Weight[] {
  return list.map((p) => ({
    symbol: p.symbol,
    name: p.name,
    weight: total > 0 ? (p.amount / total) * 100 : 0,
  }))
}

/** 读游戏页的持仓并归一化成权重。空持仓返回 `[]`（**不是** `null`）。 */
export function readWeights(): Weight[] {
  const list = readPositions()
  const total = list.reduce((s, p) => s + (Number(p.amount) || 0), 0)
  return toWeights(list, total)
}

/** 同步版 XHR：探针专用（理由见文件头）。 */
function postSync<T>(url: string, body: unknown): T {
  const x = new XMLHttpRequest()
  x.open("POST", url, false)
  x.setRequestHeader("Content-Type", "application/json")
  x.send(JSON.stringify(body))
  return JSON.parse(x.responseText) as T
}

export async function loadPortfolio(holdings: Weight[], risk?: string): Promise<PortfolioResp> {
  return post<PortfolioResp>("/api/portfolio", { holdings, risk })
}

export function loadPortfolioSync(holdings: Weight[], risk?: string): PortfolioResp {
  return postSync<PortfolioResp>("/api/portfolio", { holdings, risk })
}

/** 相关矩阵的底色分档。★**中性色**，不许用涨跌色 ——
 *  相关系数不是盈亏，用红绿会让"高相关"看起来像"亏了"（README ④ 记着这条上过线）。 */
export function corrTone(v: number): string {
  if (!(v === v)) return "transparent"                 // NaN：不涂色，别让格子凭空消失
  const a = Math.max(0, Math.min(1, Math.abs(v)))
  const pct = Math.round(a * 62)                        // 上限 62%：再深就读不出字了
  return v >= 0 ? `color-mix(in srgb, var(--accent) ${pct}%, transparent)`
                : `color-mix(in srgb, var(--ink-3) ${pct}%, transparent)`
}
