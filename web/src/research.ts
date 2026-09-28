/**
 * 「行情特征研判」页（`/research`）的数据层。
 *
 * ★它读的 `/api/game/research` 与**同前缀那两条形状不同**，这不是漏了包装：
 *   `/api/game/quote`、`/api/game/recommend` 返回的是**游戏的** dict
 *   （`{summary, data}`，失败键只有 `{ok, error, msg, hint}`），而这一条返回的是
 *   **量价引擎的** dict（顶层 `ok`/`regime`/`signals`/`composite`/`errors`，
 *   与 `/api/intraday` 同形）。理由写在 `app.py` 的 `game_research()` 里。
 *   ⇒ 所以这里的类型**照引擎写**，别去套 `game.ts` 那两个壳：套错了不会有编译错，
 *     只会在运行时把 `summary` 读成 `undefined`，页面上表现为"整块空白"。
 *
 * ★★这一页的硬纪律：**系统侧一个字的未来涨跌都不许出现。**
 *   端点给的 `ts` / `last_bar` 是"**材料截止到哪一天**"，不是"现在"；
 *   `signals` / `composite` 说的是"截至那一天，量价处在什么状态"，不是"接下来会怎样"。
 *   揭晓那一步走的是**另一条**端点（`game.getQuote`），它回答
 *   "那天买、到今天赚了多少" —— 那是**回看已经发生的事**，不是预测。
 *
 * ★为什么数据层单独一个文件、不改 `api.ts` / `api2.ts`：那两个文件的消费方多，
 *   而这一页只有自己用这一条通道；新文件的碰撞面是零。
 */
import { post } from "./api"

/** 后端通道。★**不放进 `api2.ts` 的 `CHANNEL`**：那张表是"新五页共用"的，
 *  往里加一条会牵动那个文件的所有读者，而这里只有这一页在用。 */
export const RESEARCH_CHANNEL = "/api/game/research"

/** 信号的三个方向。★`flat` 是**不表态**，不是"中性"：它意味着这条策略
 *  看了材料之后**拒绝给方向**（为什么写在它的 `why` 里）。 */
export type Side = "long" | "short" | "flat"

export type ResearchSignal = {
  id: string
  name: string
  /** 日线口径下只有一个 `1440`；`null` 是服务端给跨周期信号的写法。 */
  period: number | null
  side: Side
  strength: number
  /** 可复核的算术。**空字符串是要显示出来的缺陷**，不是一行空白。 */
  why: string
  refs?: Record<string, unknown> | null
}

export type ResearchComposite = {
  side?: Side | null
  score?: number | null
  confidence?: number | null
  n_signals?: number | null
  n_expected?: number | null
  agree?: { long?: number; short?: number; flat?: number } | null
  method?: string
  vote_weight?: number | null
}

export type ResearchVolume = {
  rvol?: number | null
  rvol_by_period?: Record<string, number | null> | null
  rvol_ex_last_bar?: number | null
  divergence?: string | null
  /** ★日线口径下**恒为 `null`**（分布按分钟量摊桶，日线一天一个样本）。
   *  `null` 不是 0 —— 见页面里那条"没量到 ≠ 量到 0"。 */
  profile?: { poc?: number | null; va_lo?: number | null; va_hi?: number | null } | null
}

/** ★**材料截止的那一根**。这一页"没有未来"这件事，靠的就是这三个字段可核验：
 *  `date` 必须 **≤ 买入日**，`closed` 在历史日期上必须是 `true`。 */
export type ResearchLastBar = {
  date?: string
  dt?: string
  closed?: boolean | null
  why?: string
  bars?: number
}

export type ResearchResp = {
  ok: boolean
  market?: string
  code?: string
  name?: string
  /** **材料截止的时刻**（`as_of` 那天），**不是**"现在"。 */
  ts?: string
  periods?: number[]
  period_label?: string
  bar_scale?: string
  bars?: Record<string, number>
  bars_per_day?: Record<string, number>
  regime?: Record<string, string>
  signals?: ResearchSignal[]
  composite?: ResearchComposite | null
  volume?: ResearchVolume | null
  /** ★接口**给了**这个字段，而这一页**故意不渲染**它 ——
   *  入场/止损/目标2 是"交易决策的输出"，不是"量价特征"。把它摆在这一页上，
   *  玩家会直接抄那个目标价（那是在读未来），"自己下判断"就落空了。
   *  类型留在这里是为了让下一个人知道"不是接口没给，是这一页选择不显示"。 */
  levels?: Record<string, unknown> | null
  session?: { phase?: string | null; window?: string | null; note?: string } | null
  last_bar?: ResearchLastBar | null
  price_adjust?: { mode?: string | null; note?: string } | null
  upstream_source?: string | null
  data_source?: string | null
  /** **合成帧**（服务端没拿到真行情时退回一份造出来的）。两个键都要读，
   *  只看一个的话，服务端少给另一个键时横幅就不出来了。 */
  synthetic?: boolean
  errors?: string[]
  /* ── 失败那一支：`_bad()` 只给这三个键 ── */
  error?: string
  msg?: string
  hint?: string
}

/** 一条研判材料。**永不抛** —— 失败也是返回值，这样页面才写得出"为什么没有数"。
 *  ★`post` 不看状态码（`api.ts` 顶上那段）：本站的接口在 `ok:false` 时发 400，
 *    而响应体照样是一份完整的 JSON，`msg`/`hint` 就在里面。 */
export function getResearch(symbol: string, buyDate: string): Promise<ResearchResp> {
  return post<ResearchResp>(RESEARCH_CHANNEL, { symbol, buy_date: buyDate })
}

/** 同步版：**探针专用**（照 `game.ts` 里那份私有 `postSync` 写）。
 *
 *  ★为什么这一页自己带一份、不用 `agentui/probe.ts` 的 `callSync`：
 *    ① 站内约定本来就是"数据层各带一份私有 `postSync`" —— `dash.ts` /
 *       `portfolio.ts` / `ledger.ts` / `game.ts` 那四份全是这么来的
 *       （`agentui/probe.ts` 顶上那段自己写着这件事）。
 *    ② 更要紧的是：**那一份在成功分支上是坏的**。它的 `fromBody` 最后一行写的是
 *       `return { ok: true, data }`，而那个函数的形参叫 `d` ⇒ 编译出来是
 *       `ReferenceError: data is not defined`（`tsc` 也报，`TS18004`）。
 *       它到现在没暴露，是因为那五页的后端**还不存在**，`callSync` 的**成功分支
 *       从来没跑过**；而这一页的通道是通的真接口，一进去就踩（实测：`/research?probe=1`
 *       整页白屏，渲染期抛）。
 *       ★**这条缺陷不归这一页修** —— 那是别人正在施工的那一批的公共底座，
 *         动它会给那一趟换输入。已写进交付说明交协调者排期；这一页用自己这份绕开。
 */
function postSync<T>(url: string, body: unknown): T {
  const x = new XMLHttpRequest()
  x.open("POST", url, false)
  x.setRequestHeader("Content-Type", "application/json")
  x.send(JSON.stringify(body))
  return JSON.parse(x.responseText) as T
}

/** 探针的研判材料：网络断了也**必须**回一个形状完整的对象，不能抛 ——
 *  抛出去的话调用方拿到的是 `undefined`，页面上连"为什么没有数"都写不出来。
 *  ★它**只在 `?probe=1` 那条路上被调用**，用户那条路一个字节都不受影响。 */
export function getResearchSync(symbol: string, buyDate: string): ResearchResp {
  try {
    return postSync<ResearchResp>(RESEARCH_CHANNEL, { symbol, buy_date: buyDate })
  } catch (e) {
    return { ok: false, error: "NETWORK", msg: String((e as Error)?.message || e) }
  }
}

/** 一份 `ok:false` 的材料 → `Fail`（`Degraded` 那块要的是那个形状）。
 *  ★**原因优先用服务端给的那句**：前端自己造一句，就会在服务端换了措辞之后
 *    继续说着旧的。
 *  ★★`errors[]` **也是"服务端给的那句"之一**，而且要排在兜底之前 —— 有一条失败
 *    形状里 `msg`/`hint`/`error` **三个键全是空的**，原因**只在 `errors[0]`**：
 *    日线窗太短时服务端回的是 `ok:false` + 「as_of=2015-01-05 太早了：这只标的的
 *    日线最早只回到 2023-11-06」（实测原话）。只看那三个键的话，页面上就只剩一句
 *    笼统的"服务端说这一次取不到"，把**唯一有信息量的那句**吞掉了 ——
 *    而"取不到"和"取不到是因为你问的日期太早、最早只到某天"是两件完全不同的事。 */
export function researchErrText(r: ResearchResp | null): string {
  if (!r) return "还没有取到材料"
  return r.msg || r.hint || r.error || r.errors?.[0] || "服务端说这一次取不到"
}
