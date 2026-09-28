/**
 * 模拟投资游戏的前端数据层。
 *
 * ★虚拟本金**和画像没有任何关系**。画像里那个"可投资资产"回答的是"你有多少钱"，
 *   这里回答的是"这一局你想拿多少练手" —— 两者相等**没有任何理由**，
 *   所以这个文件**不读** `readProfile().assets`、也绝不拿它当默认值。
 *   判据「虚拟本金不是画像里那个资产」查的就是这条：塞一个"100万以上"的画像
 *   进去，本金框必须还是空的。
 *
 * ★盈亏是**服务端算的**（`agent/game.py`），前端一个数都不许自己乘。
 *   前端只会格式化（`num` / `pct` / `price`）—— "页面上的数必须就是接口给的数"
 *   是这一站的老规矩（见 Dash.tsx）。
 */
import { post } from "./api"

export const STAKE_KEY = "licai-game-stake"
export const POS_KEY = "licai-game-positions"

/** 探针那一屏用的演示报价：**只在 `?probe=1` 下**由 `main.tsx` 同步取一次。
 *
 *  ★为什么探针要自己定一个数、而不是"让用户填"：探针跑的时候没有用户，
 *    本金框是空的 —— 而"空本金"这条路上服务端只会回一个 `BAD_AMOUNT`，
 *    于是整个页面只剩错误块，渲染层判据就**没有数字可量**了。
 *  ★本金框**仍然是空的**（那是另一条判据要的：它不许被画像里的资产预填）。
 *    两者不矛盾：`#gNums` 里那行「这一局本金」写的就是这个数，页面上看得见。 */
export const PROBE_SYM = "510300"
export const PROBE_AMT = 10000

/** 一次报价成功时的全部字段（就是服务端 `summary` 那份）。 */
export type QuoteOk = {
  ok: true
  symbol: string
  name: string
  code: string
  market: string
  amount: number
  buy_date: string
  date_shifted: boolean
  buy_price: number
  last_date: string
  last_close: number
  shares: number
  value: number
  pnl: number
  pnl_pct: number
  bars: number
  source: string
}

/** 失败时**只有这四个键** —— 一个收益数字都没有。 */
export type GameErr = { ok: false; error: string; msg: string; hint?: string }

export type QuoteResp = { summary: QuoteOk | GameErr; data: null }
export type RecItem = { symbol: string; name: string; why: string; interest: string }
export type RecResp = {
  summary: { ok: boolean; count: number; unmapped_count: number; note: string }
  data: { items: RecItem[]; unmapped: { interest: string; why: string }[] } | null
}

export function isOk(s: QuoteOk | GameErr): s is QuoteOk {
  return (s as { ok?: boolean }).ok === true
}

export function getQuote(symbol: string, amount: number | string, buyDate: string): Promise<QuoteResp> {
  return post<QuoteResp>("/api/game/quote", { symbol, amount, buy_date: buyDate })
}

export function getRec(interests: string[]): Promise<RecResp> {
  return post<RecResp>("/api/game/recommend", { interests })
}

/** 同步版：**探针专用**（照 `dash.ts` 的 `postSync` 写）。
 *
 *  ★为什么非要两份：渲染层判据全部押在"几何在同一个 tick 里就绪"上
 *    （见 `main.tsx` 那段注释）。异步取数回来之后再渲染出来的数，同步探针
 *    **量不到** —— 量到的是"正在取数…"。那不是让判据变红，是让判据**消失**，
 *    而且消失得像"页面挺正常"。
 *  ★同步 XHR 只在 `?probe=1` 那条路上走，用户那条路一个字节都不受影响。
 */
function postSync<T>(url: string, body: unknown): T {
  const x = new XMLHttpRequest()
  x.open("POST", url, false)
  x.setRequestHeader("Content-Type", "application/json")
  x.send(JSON.stringify(body))
  return JSON.parse(x.responseText) as T
}

/** 探针的报价：网络断了也**必须**回一个形状完整的失败，不能抛。
 *  抛出去的话调用方拿到的是 undefined，页面上连"为什么没有数"都写不出来。 */
export function getQuoteSync(symbol: string, amount: number | string, buyDate: string): QuoteResp {
  try {
    return postSync<QuoteResp>("/api/game/quote", { symbol, amount, buy_date: buyDate })
  } catch (e) {
    return { summary: { ok: false, error: "NETWORK", msg: String((e as Error)?.message || e) }, data: null }
  }
}

export function getRecSync(interests: string[]): RecResp | null {
  try {
    return postSync<RecResp>("/api/game/recommend", { interests })
  } catch {
    return null
  }
}

/* ── 本金与持仓：都住在浏览器里（服务端零用户状态） ── */

function ls<T>(fn: () => T, fallback: T): T {
  try {
    return fn()
  } catch {
    return fallback
  }
}

/** 读虚拟本金。★返回**字符串**（输入框里那个原样），不返回数字 ——
 *  读成数字的话"用户还没填"和"用户填了 0"就分不开了。 */
export function readStake(): string {
  return ls(() => localStorage.getItem(STAKE_KEY) || "", "")
}

export function saveStake(v: string): void {
  ls(() => localStorage.setItem(STAKE_KEY, v), undefined)
}

/** 一笔记账式的持仓。`buyDate` 是**用户选的那天**，服务端会回它实际用的那天；
 *  两者可能差一两天（周末/节假日），页面上两个都要显示。 */
export type Position = {
  id: string
  symbol: string
  name: string
  amount: number
  buyDate: string
  at: string
}

export function readPositions(): Position[] {
  return ls(() => {
    const raw = localStorage.getItem(POS_KEY)
    if (!raw) return []
    const v = JSON.parse(raw) as unknown
    if (!Array.isArray(v)) return []
    return v.filter(
      (p): p is Position =>
        !!p && typeof p === "object" && typeof (p as Position).symbol === "string"
        && typeof (p as Position).amount === "number",
    )
  }, [])
}

export function savePositions(list: Position[]): void {
  ls(() => localStorage.setItem(POS_KEY, JSON.stringify(list)), undefined)
}

export function newPosition(symbol: string, name: string, amount: number, buyDate: string): Position {
  // ★id 只要**在本地唯一**就够了，不需要密码学强度：它唯一的作用是 React 的 key
  //   和"删哪一行"。用 randomUUID 会在老浏览器上直接抛，反而把加仓这条路弄坏。
  return { id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
           symbol, name, amount, buyDate, at: new Date().toISOString() }
}

/* ── 持仓逐行取最新价 ── */

export type PosRow = { p: Position; q?: QuoteOk; err?: string }

function rowFrom(p: Position, r: QuoteResp): PosRow {
  const s = r?.summary
  if (!s || !isOk(s)) {
    const e = s as GameErr | undefined
    return { p, err: e?.msg || e?.error || "取数失败" }
  }
  return { p, q: s }
}

/** ★逐行降级：一行取不到只坏那一行（照 `dash.ts` 的 `loadDash` 写）。
 *  "悄悄少一行"在这类页面上是最常见的谎 —— 看着像"我只建了两仓"。 */
export async function loadPositions(list: Position[]): Promise<PosRow[]> {
  return Promise.all(
    list.map((p) =>
      getQuote(p.symbol, p.amount, p.buyDate)
        .then((r) => rowFrom(p, r))
        .catch((e: unknown) => ({ p, err: String((e as Error)?.message || e) }) as PosRow),
    ),
  )
}

/** 同步版：探针专用（理由同 `getQuoteSync`）。
 *  ★它存在的意义是让"一行坏掉只坏一行"这条判据**在同一个 tick 里**量得到：
 *    异步那版要等 `Promise.all` 回来，探针量到的会是"正在取最新价…" ——
 *    那不是判据变红，是判据**消失**。 */
export function loadPositionsSync(list: Position[]): PosRow[] {
  return list.map((p) => rowFrom(p, getQuoteSync(p.symbol, p.amount, p.buyDate)))
}

/** 默认回看日期：三个月前（本地时区的 YYYY-MM-DD）。
 *  ★不能用 `toISOString().slice(0,10)`：那是 UTC，东八区下午 8 点之后会**少一天**，
 *    用户看到的默认日期会莫名其妙地跳。 */
export function defaultBuyDate(now = new Date()): string {
  const d = new Date(now.getFullYear(), now.getMonth() - 3, now.getDate())
  return ymd(d)
}

export function todayStr(now = new Date()): string {
  return ymd(now)
}

function ymd(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
