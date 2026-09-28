/**
 * 行情总览（/dash）的数据层。
 *
 * ★两个入口：`loadDash()` 是用户那条路（异步 fetch），`loadDashSync()` 是探针那条路
 *   （**同步 XHR**）。为什么非要两份：渲染层判据全部押在"几何在同一个 tick 里就绪"上
 *   （见 main.tsx 那段注释），而异步取数回来之后再渲染出来的数，同步探针**量不到** ——
 *   量到的是"加载中"。那不是判据变红，是判据**消失**（还消失得像"页面挺正常"）。
 *   ★同步 XHR 只在 `?probe=1` 下走，用户那条路一个字节都不受影响。
 *
 * ★逐标的降级：一个标的取数失败**只坏那一行**，别的行照常。行情源本来就会挂一两个，
 *   整页一起挂掉的话"是哪个源挂了"这件事就没法从页面上看出来了。
 */
import { getKline, type Bars, type KlineResp, type KlineSummary } from "./api"

/** 四个市场各挑一个：A股 / 指数 / 港股 / 美股 / 加密。 */
export const DASH_SYMBOLS = ["600519", "sh000300", "00700", "AAPL", "BTC"]

export type DashRow = {
  sym: string
  s?: KlineSummary
  /** ★表里那一列迷你走势。**是接口给的真收盘价的等距抽样**（见 `sparkOf`），
   *  不是插值、不是平滑、更不是随手编的示意图 —— 这条线的形状必须就是这只标的
   *  这 120 个交易日的形状。取不到就整列不画，不画一根假的。 */
  spark?: { i: number; c: number }[]
  /** **今日涨跌**（%，最后两根收盘价算的）。★不足两根就是 `null`，不给 `0` ——
   *  "算不出来"和"今天没涨没跌"不是一回事（同 `format.ts` 的 `pct` 那条）。
   *  ★为什么不复用 `chg_pct_5d` 那几个：那是接口按**自己的窗口**算的，
   *    而"今日涨跌"要的就是最后两根，跟窗口无关。窗口换了它不该跟着变。 */
  chg1: number | null
  /** 这一行坏掉时的原因。★必须留着并显示出来，不能悄悄少一行。 */
  err?: string
}

/** 收盘价序列 → 迷你走势的点。
 *
 *  ★抽样，不插值：`c` 是**从旧到新**的收盘价（`_tail_bars` 取的是 `df.tail(n)`，
 *    而 df 是时间升序，所以 `c[c.length-1]` 就是 `last_close`）。等距取 ≤48 个点，
 *    点与点之间让 recharts 自己连直线。
 *  ★为什么是 48：一格只有 ~96px 宽，48 个点已经比像素密；再多不会更好看，
 *    只会让 5 行各自多画几十个 path 段，而探针那一帧是要**同步**量完的。
 *  ★为什么不用 `bars.o/h/l`：迷你走势回答的是"这三个月长什么样"，
 *    画 K 线要的是"每天发生了什么"，在一格 96px 里前者才读得出来。 */
function sparkOf(bars?: Bars): { i: number; c: number }[] | undefined {
  const c = bars?.c
  if (!c || c.length < 2) return undefined
  const n = 48
  const step = Math.max(1, Math.ceil(c.length / n))
  const out: { i: number; c: number }[] = []
  for (let k = 0; k < c.length; k += step) out.push({ i: out.length, c: c[k] })
  // ★收尾那一点单独补：`step` 不整除时最后一天会被甩掉，而**最后一天就是"最新收盘"**，
  //   少掉它等于走势线停在昨天 —— 表里那一列却写着今天的价，两处对不上。
  const last = c[c.length - 1]
  if (out.length && out[out.length - 1].c !== last) out.push({ i: out.length, c: last })
  return out
}

/** 最后两根收盘价 → 今日涨跌（%）。★一根都没有、或前一根是 0 ⇒ `null`。 */
export function dayChg(c?: number[]): number | null {
  if (!c || c.length < 2) return null
  const prev = c[c.length - 2]
  if (!(prev > 0)) return null
  return (c[c.length - 1] / prev - 1) * 100
}

function rowFrom(sym: string, r: KlineResp): DashRow {
  if (!r?.summary?.ok) {
    return { sym, chg1: null, err: r?.summary?.msg || r?.summary?.error || "取数失败" }
  }
  return { sym, s: r.summary, spark: sparkOf(r.data?.bars), chg1: dayChg(r.data?.bars?.c) }
}

export async function loadDash(): Promise<DashRow[]> {
  const rs = await Promise.all(
    DASH_SYMBOLS.map((sym) =>
      getKline(sym, 250)
        .then((r) => rowFrom(sym, r))
        .catch((e: unknown) =>
          ({ sym, chg1: null, err: String((e as Error)?.message || e) }) as DashRow),
    ),
  )
  return rs
}

/** 同步版：探针专用。`false` = 同步，弃用告警由浏览器控制台出，但这里没有别的选择。 */
function postSync<T>(url: string, body: unknown): T {
  const x = new XMLHttpRequest()
  x.open("POST", url, false)
  x.setRequestHeader("Content-Type", "application/json")
  x.send(JSON.stringify(body))
  return JSON.parse(x.responseText) as T
}

export function loadDashSync(): DashRow[] {
  return DASH_SYMBOLS.map((sym) => {
    try {
      return rowFrom(sym, postSync<KlineResp>("/api/kline", { symbol: sym, days: 250 }))
    } catch (e) {
      return { sym, chg1: null, err: String((e as Error)?.message || e) }
    }
  })
}

/** 收盘价落在区间里的位置（0–100）。区间退化成一个点时给 50，不给 NaN —— NaN 会让整条 bar 消失。 */
export function rangePos(s: KlineSummary): number {
  const span = s.range_high - s.range_low
  if (!(span > 0)) return 50
  return Math.max(0, Math.min(100, ((s.last_close - s.range_low) / span) * 100))
}
