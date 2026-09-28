/**
 * 对话状态机。**一个 reducer，两个入口**。
 *
 * ★这是旧页面 index.html:1036-1097 那条纪律的落点：
 *   「demo() 必须走和真实回答**同一个** handle()，否则测的是另一条代码路径，等于没测」。
 *   所以 `applyEvent()` 既是 SSE 的处理函数，也是探针/演示回放的处理函数 ——
 *   回放走的是**同一个**函数，不是"照着写一遍"。
 *
 * 事件 type 全集来自 agent/llm.py 的 emit()：token / tool_start / tool_end /
 * chart / terms / suggest / heartbeat / error / done。★`done` 在旧页面里**没有分支**、
 * 被无声吞掉，结束动作由流循环退出后的 finish() 承担 —— 这里保持同样的语义，
 * 免得"两种结束方式"各写一套。
 */
import type { ChartData } from "./components/KChart/KChart"
import type { FundData } from "./components/FundCard/FundCard"
import { DEFAULT_QUICK, type Quick } from "./quick"

/** ★一条气泡**只放一张图**（`Msg.chart` 是单槽）。两种图的形状完全不同
 *  （K 线是 OHLC 序列、分位卡是标量+分位），所以这里是**判别联合**，
 *  渲染时按 `kind` 分发 —— 不能拿 `as ChartData` 硬塞，
 *  那等于把"分位卡被当成 K 线画"这种错留到运行期才炸。
 *  ★`fundamentals` 的 `kind` 由服务端给（tools.py 的 data 载荷），
 *  前端不自己造这个词。 */
export type AnyChart = ChartData | FundData

export type TraceRow = {
  nm: string
  ar: string
  st: string
  bad: boolean
  extra?: string
}

/** 一条资讯。字段形状**照抄服务端**（`agent/tools.py:get_news` 的 `data.items`），
 *  前端不自己造名字、也不自己编值。
 *  ★全部可选：`url` 为 `null` 是真会发生的（快讯类源没有原文链接），
 *    `excerpt` 也常常是空串。**渲染时缺什么就说缺什么**，绝不填一个看着像真的占位，
 *    这跟本仓库对"编一个金融数"的零容忍是同一条。 */
export type NewsItem = {
  title?: string
  excerpt?: string
  source?: string
  source_id?: string
  ts?: string
  url?: string
  /** 同一事件出现在**几个源**里（多源同题数）。服务端只在没给时兜底成 1。 */
  heat?: number
  seen_in?: string[]
}

/** `news` 事件带的是服务端 `data` 通道的**整个载荷**（`{kind:'news', items, ...}`）。 */
export type NewsPayload = {
  items?: NewsItem[]
  name?: string | null
  code?: string | null
  query?: string | null
  degraded?: { source?: string; reason?: string }[]
}

export type Msg = {
  id: number
  role: "me" | "ai" | "err"
  who: string
  text: string          // ai：累计的正文（原始 markdown）
  body: string          // me / err：纯文本
  code?: string         // err 的详情（落进 <code>）
  rows: TraceRow[]
  traceOpen: boolean
  chips: string[]
  chart?: AnyChart
  /** ★资讯**也是**一条气泡上的数据槽（跟 `chart` 一样是"这一轮采到什么"），
   *  所以挂 `Msg` 而不是 `State`。可选 —— 老气泡不因为多了这个字段就要回填。
   *  用数组而不是单条：`get_news` 一次就采回一批（服务端 `limit` 上限 12）。 */
  news?: NewsItem[]
  predMarked: boolean
  heartbeat?: number
}

export type Ev =
  | { type: "token"; text: string }
  | { type: "tool_start"; name: string; args?: unknown }
  | { type: "tool_end"; name: string; ok: boolean; ms?: number; msg?: string }
  | { type: "chart"; chart: AnyChart }
  | { type: "terms"; chips: string[] }
  /** ★`news` 是**资讯**事件（`get_news` 那条数据通道）。它和 `terms` 是同一形状的
   *  东西：都不是图，都走 data 通道、都由服务端在 `data_events()` 里发出来。
   *  ★在这条分支存在之前，`Ev` 里没有这一支 ⇒ 事件落穿到函数末尾那句
   *  `return {...state, msgs}`，**静默丢弃**：服务端发得出、页面上一个字节都不出现，
   *  而且不报错、不警告。静默正是这个仓库里所有坏法的共同点。 */
  | { type: "news"; news: NewsPayload }
  | { type: "suggest"; items: Quick[] }
  | { type: "heartbeat"; elapsed: number }
  | { type: "error"; msg?: string; code?: string; hint?: string }

export type Act =
  | { kind: "user"; text: string }
  /** ★开一个空气泡。旧页面是 `addAI()` —— 事件必须**先有气泡**才有落点，
   *  所以演示回放的第一步也是它。少了这个，applyEvent 找不到 ai 气泡会静默丢弃全部事件。 */
  | { kind: "ai" }
  | { kind: "ev"; ev: Ev }
  | { kind: "finish" }
  | { kind: "reset" }

/** `quick` = 底部「问问看」那一排。★它属于**这一轮**，不属于某条气泡，
 *  所以挂在 State 上而不是 Msg 上。初值就是兜底那四条（页面一打开就得有得点）。 */
export type State = { msgs: Msg[]; seq: number; quick: Quick[] }

export const initialState: State = { msgs: [], seq: 0, quick: DEFAULT_QUICK }

/** trace 的 summary 文字：三态，跟旧页面逐字一致。 */
export function traceSummary(m: Msg): string {
  const pend = m.rows.filter((r) => r.st === "…").length
  if (pend) return "工具调用（进行中）"
  if (!m.rows.length) return ""
  const bad = m.rows.filter((r) => r.bad).length
  return `用了 ${m.rows.length} 个工具${bad ? `（${bad} 个失败）` : ""}`
}

/** ★`tool_end` 找的是**最后一个同名且还没结束**的行 —— 同名工具会并发/重复调用，
 *  只按名字找会把前一次的 `✗` 改成 `✓`。旧页面用的就是这个倒序扫描。 */
function endRow(rows: TraceRow[], name: string, ok: boolean, ms?: number, msg?: string): TraceRow[] {
  const out = rows.slice()
  for (let i = out.length - 1; i >= 0; i--) {
    if (out[i].nm === name && out[i].st === "…") {
      out[i] = {
        ...out[i],
        st: ok ? `✓ ${ms || 0}ms` : "✗",
        bad: !ok,
        extra: !ok && msg ? msg : out[i].extra,
      }
      break
    }
  }
  return out
}

/** 把一个事件作用到最后一条 ai 消息上。**SSE 和演示回放共用这一个函数。** */
function applyEvent(state: State, ev: Ev): State {
  // ★suggest 必须在**找气泡之前**处理：它记的是"这一轮都碰过什么"，不挂在某条气泡上。
  //   放到下面去的话，"还没开气泡就收到 suggest"会被 `if (idx < 0) return state` 静默吞掉 ——
  //   而静默正是这一整类坏法的共同点（按钮没更新，控制台一个错都没有）。
  // ★空数组回落兜底：按钮行**任何时候都不许空着**（后端已保证，这里是第二层）。
  if (ev.type === "suggest") {
    const items = Array.isArray(ev.items) ? ev.items.filter((x) => x && x.ask).slice(0, 4) : []
    return { ...state, quick: items.length ? items : DEFAULT_QUICK }
  }
  const msgs = state.msgs.slice()
  // 从后往前找最后一条 ai 气泡：错误会另起一条 .msg.ai.err，但事件仍作用于当前那条
  let idx = -1
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === "ai") { idx = i; break }
  }
  if (idx < 0) return state
  const m = { ...msgs[idx] }

  if (ev.type === "token") {
    m.text += ev.text
  } else if (ev.type === "tool_start") {
    let ar = ""
    try { ar = JSON.stringify(ev.args || {}) } catch { ar = "" }
    m.rows = [...m.rows, { nm: ev.name, ar, st: "…", bad: false }]
    m.traceOpen = true
  } else if (ev.type === "tool_end") {
    m.rows = endRow(m.rows, ev.name, ev.ok, ev.ms, ev.msg)
    if (!m.rows.some((r) => r.st === "…")) m.traceOpen = false
  } else if (ev.type === "chart") {
    m.predMarked = true          // ★先打标再画图：图在下面，标在气泡头上
    m.chart = ev.chart
  } else if (ev.type === "terms") {
    m.chips = ev.chips.slice(0, 8)
  } else if (ev.type === "news") {
    // ★形状与 `terms` 那一支**逐字同构**：取一次、截一次、替换（不是追加）。
    //   截断上限与 `terms` 的 8 一致 —— 服务端 `limit` 上限 12，但一条气泡里
    //   列 8 条已经够看；多了会把气泡拉到几屏高。
    // ★`Array.isArray` 兜一层：`ev.news` 是**服务端来的**，`items` 缺了就当空，
    //   不许因为一个畸形载荷把整条气泡（连同已经写出来的正文）打崩。
    m.news = Array.isArray(ev.news?.items) ? ev.news.items.slice(0, 8) : []
  } else if (ev.type === "heartbeat") {
    // ★一旦有正文就彻底不管 —— 别让心跳把已经写出来的正文顶掉
    if (!m.text) m.heartbeat = ev.elapsed
  } else if (ev.type === "error") {
    msgs[idx] = m
    return {
      ...state,
      msgs: [...msgs, {
        id: state.seq, role: "err" as const, who: "出错了",
        body: ev.msg || "出错了", code: ev.hint || ev.code || "",
        text: "", rows: [], traceOpen: false, chips: [], predMarked: false,
      }],
      seq: state.seq + 1,
    }
  }
  msgs[idx] = m
  return { ...state, msgs }
}

export function reducer(state: State, act: Act): State {
  if (act.kind === "reset") return initialState
  // ★这两个分支必须 `...state` 展开，**不能**从零写一个字面量。
  //   原来就是 `{msgs, seq}` 两个字面量 —— 加 `quick` 那天当场炸：
  //   演示回放第一步是 `{kind:'ai'}`，于是 state 一出来就没有 `quick`，
  //   渲染 `st.quick.map` 直接抛 TypeError、整页白屏。
  //   展开写法对**以后再加的字段**也免疫，不用回来改这里。
  if (act.kind === "user") {
    return {
      ...state,
      msgs: [...state.msgs,
        { id: state.seq, role: "me" as const, who: "", body: act.text,
          text: "", rows: [], traceOpen: false, chips: [], predMarked: false }],
      seq: state.seq + 1,
    }
  }
  if (act.kind === "ai") {
    return {
      ...state,
      msgs: [...state.msgs,
        { id: state.seq, role: "ai" as const, who: "智能体", body: "",
          text: "", rows: [], traceOpen: false, chips: [], predMarked: false }],
      seq: state.seq + 1,
    }
  }
  if (act.kind === "ev") return applyEvent(state, act.ev)
  // finish：跟旧页面一致 —— 空气泡给一句"（没有内容返回）"
  const msgs = state.msgs.slice()
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === "ai") {
      const m = msgs[i]
      if (!m.text && !m.chart) msgs[i] = { ...m, body: "（没有内容返回）", heartbeat: undefined }
      break
    }
  }
  return { ...state, msgs }
}

/** 解析 SSE 字节流的一帧。返回 null 表示"这帧不管"（不是 data: 或 JSON 坏了）。
 *  ★多字节 CJK 跨 chunk 靠 TextDecoder 的 `{stream:true}` —— 去掉它会偶发乱码。 */
export function parseFrame(frame: string): Ev | null {
  const ln = frame.trim()
  if (ln.indexOf("data:") !== 0) return null
  try {
    return JSON.parse(ln.slice(5).trim()) as Ev
  } catch {
    return null
  }
}

/** 演示用的那条图（60 根历史 + 30 根预测）。
 *  ★对话卡和预测卡共用**同一份** —— 两边各造一份迟早会不一样。 */
export function demoChart(): ChartData {
  const bars: { t: string; o: number; h: number; l: number; c: number }[] = []
  const t0 = new Date(2026, 4, 1).getTime()
  let c = 4460
  for (let i = 0; i < 60; i++) {
    const o = c
    const cl = c * (1 + (Math.sin(i / 4.1) * 0.012 + ((i % 7) - 3) * 0.0016))
    bars.push({
      t: new Date(t0 + i * 864e5).toISOString().slice(0, 10),
      o: +o.toFixed(2), h: +(Math.max(o, cl) * 1.006).toFixed(2),
      l: +(Math.min(o, cl) * 0.994).toFixed(2), c: +cl.toFixed(2),
    })
    c = cl
  }
  const pb = []
  let pc = c
  for (let i = 0; i < 30; i++) {
    const po = pc
    const pcl = pc * (1 + Math.cos(i / 5) * 0.009)
    pb.push({
      t: new Date(t0 + (60 + i) * 864e5).toISOString().slice(0, 10),
      o: +po.toFixed(2), h: +(Math.max(po, pcl) * 1.005).toFixed(2),
      l: +(Math.min(po, pcl) * 0.995).toFixed(2), c: +pcl.toFixed(2),
    })
    pc = pcl
  }
  return {
    kind: "predict",
    name: "沪深300", code: "000300", base_close: +c.toFixed(2),
    params: { pred_len: 30, ms: 41200, ohlc_repaired_bars: 15, ohlc_max_clamp_pct: 2.31 },
    history: {
      t: bars.map((b) => b.t), o: bars.map((b) => b.o), h: bars.map((b) => b.h),
      l: bars.map((b) => b.l), c: bars.map((b) => b.c),
    },
    pred: {
      t: pb.map((b) => b.t), o: pb.map((b) => b.o), h: pb.map((b) => b.h),
      l: pb.map((b) => b.l), c: pb.map((b) => b.c),
    },
  }
}

/** 演示用的分位卡。★**这些数是真的**：2026-09-19 用 `get_fundamentals('600519')`
 *  实跑一次、逐字抄下来的（估值日期 2026-09-18），不是编的。
 *  本仓库对"编一个看着像真的金融数"零容忍，示例也不例外 —— K 线那条 demoChart
 *  是合成序列，所以它在正文里明写"（示例数据）"；这张卡不需要那句话，因为它就是真值。
 *  ★将来数据变了不必回来改这里：它是**快照**，卡上有 `valuation_date` 说明是哪天的。 */
export function demoFund(): FundData {
  return {
    kind: "fundamentals",
    name: "贵州茅台", code: "600519", industry: "酒、饮料和精制茶制造业",
    pe: 19.2979, pb: 6.2546,
    pe_pctile_5y: 3.4, pb_pctile_5y: 3.7,
    pe_lo5y: 17.66, pe_hi5y: 54.97,
    pb_lo5y: 5.39, pb_hi5y: 15.81,
    mkt_cap_yi: 15715.0, valuation_date: "2026-09-18",
    series: {
      span: "近 5 年（按月取月末）",
      t: ["2021-07-30", "2021-08-31", "2021-09-30", "2021-10-29", "2021-11-30", "2021-12-31",
        "2022-01-28", "2022-02-28", "2022-03-31", "2022-04-29", "2022-05-31", "2022-06-30",
        "2022-07-29", "2022-08-31", "2022-09-30", "2022-10-31", "2022-11-30", "2022-12-30",
        "2023-01-31", "2023-02-28", "2023-03-31", "2023-04-28", "2023-05-31", "2023-06-30",
        "2023-07-31", "2023-08-31", "2023-09-28", "2023-10-31", "2023-11-30", "2023-12-29",
        "2024-01-31", "2024-02-29", "2024-03-29", "2024-04-30", "2024-05-31", "2024-06-28",
        "2024-07-31", "2024-08-30", "2024-09-30", "2024-10-31", "2024-11-29", "2024-12-31",
        "2025-01-27", "2025-02-28", "2025-03-31", "2025-04-30", "2025-05-30", "2025-06-30",
        "2025-07-31", "2025-08-29", "2025-09-30", "2025-10-31", "2025-11-28", "2025-12-31",
        "2026-01-30", "2026-02-27", "2026-03-31", "2026-04-30", "2026-05-29", "2026-06-30",
        "2026-07-31", "2026-08-31", "2026-09-18"],
      pe: [44.3489, 40.1471, 47.1561, 45.7536, 48.3767, 51.364, 47.28, 44.8596, 41.1628,
        41.1978, 40.6492, 46.0788, 42.7765, 41.9604, 40.8373, 28.4571, 33.7059, 36.404,
        38.9074, 38.2324, 36.4542, 33.3738, 30.8787, 32.0559, 35.6498, 33.6734, 32.7901,
        29.7244, 31.611, 30.4553, 28.3186, 29.9083, 30.0477, 27.4576, 26.5469, 23.6311,
        22.8886, 22.5351, 27.2946, 23.2109, 23.1798, 23.1534, 21.8011, 22.8007, 23.7155,
        21.8327, 21.4799, 19.8925, 20.0639, 20.6723, 20.1063, 19.8913, 20.1763, 19.1564,
        19.4878, 20.2392, 20.1693, 20.9651, 20.04, 17.9164, 20.4118, 19.9487, 19.2979],
      pb: [13.0405, 12.1008, 13.1853, 13.1571, 13.9114, 13.5867, 12.5063, 11.8661, 10.4429,
        11.1073, 10.9594, 13.3726, 12.4143, 12.5814, 11.3794, 8.2041, 9.7173, 10.4952,
        11.7396, 11.5359, 10.4743, 10.132, 9.3745, 10.5727, 11.758, 11.5481, 11.2452,
        9.7157, 10.3324, 9.9546, 9.3481, 9.8728, 9.9188, 8.9342, 8.6379, 7.6891, 8.1684,
        8.2943, 9.2375, 8.0737, 8.0629, 8.2128, 7.7331, 8.0877, 7.59, 7.5219, 7.4003,
        7.4195, 7.4834, 7.7905, 7.0341, 6.966, 7.0659, 7.0496, 7.1715, 7.4481, 6.703,
        6.4015, 6.119, 5.8983, 6.7197, 6.4656, 6.2546],
    },
  }
}

/** 演示/探针用的假事件序列。★必须与真实链路喂同一个 reducer（见文件头）。 */
export function demoSeq(): Ev[] {
  const seq: Ev[] = [
    { type: "tool_start", name: "search_terms", args: { query: "可转债" } },
    { type: "tool_end", name: "search_terms", ok: true, ms: 3 },
    { type: "terms", chips: ["可转债", "债券", "国债", "股票"] },
    { type: "token", text: "**先给结论**：可转债就是一张能换成股票的债券。" },
    { type: "token", text: " 跌了可以当债券拿着吃利息，涨了能转成股票吃涨幅。\n\n" },
    // ★这里必须用单引号包：正文里那对引号是 **ASCII 双引号**，
    //   外面再套双引号就提前把字符串收了尾，构建期报的是"Expected , or }"
    //   —— 报错落在中文句子中间，一眼看不出是引号嵌套。
    { type: "token", text: '但**"下有保底"是错觉**，退市或违约照样腰斩。' },
    { type: "tool_start", name: "predict_kline", args: { symbol: "sh000300", pred_len: 30 } },
    { type: "tool_end", name: "predict_kline", ok: true, ms: 41200 },
    { type: "heartbeat", elapsed: 41 },
  ]
  seq.push({ type: "chart", chart: demoChart() })
  seq.push({ type: "token", text: "\n\n模型给的是一条**震荡偏弱**的路径（示例数据）。\n\n" })
  seq.push({ type: "token", text: "⚠️ 这只是拿价格序列采样出来的一条路径，没有基本面、新闻、政策信息，**不构成投资建议**。" })
  // ★「问问看」那一排。**必须在 demoSeq 里**，不能只靠真实链路：
  //   渲染层的判据是在同一个同步 tick 里量 `#qs` 的，量的是**回放**出来的那一份；
  //   少了这条，"按钮换了没有"就只能靠异步的真跑 —— 而异步不是让判据变红，是让它消失。
  // 内容 = 服务端 `suggest.items()` 对**本轮这套 used**（预测过 + 上面那排 chips）的真实输出，
  //   逐字抄来；所以这里能看见优先级的实际效果：预测那两条排前，词条追问跟在后面。
  seq.push({ type: "suggest", items: [
    { label: "这条预测是怎么算出来的", ask: "这条预测是怎么算出来的" },
    { label: "Kronos 历史上准不准", ask: "Kronos 历史上准不准" },
    { label: "可转债和债券有什么区别", ask: "可转债和债券的区别" },
    { label: "债券和股票有什么区别", ask: "债券和股票的区别" },
  ] })
  return seq
}

/** 演示的第三段：**又一条气泡**，装资讯（`get_news` 那条数据通道）。
 *
 *  ★**这些字全是真的**：2026-09-22 在本机实跑
 *  `get_news(query='沪深300', limit=6)` 一次、把 `data.items` 逐字抄下来的
 *  （下面那条 `get_news` 的 `ms` 也是那一趟的墙钟：18 个源并发采完 ≈8s）。
 *  本仓库对"编一个看着像真的金融数"零容忍，示例也不例外 ——
 *  所以这里连 `heat` 都照抄成 **1**（那一趟确实没有一条新闻同时出现在两个源里）。
 *  ★将来数据变了**不必**回来改这里：它是**快照**，每条自带 `ts` 说明是哪一刻的。
 *
 *  ★为什么要**单独一条气泡**：`Msg.news` 和 `Msg.chart` 是**两个独立的槽**，
 *    混在前两条里，"资讯没渲染"和"被图表挤掉了"在页面上长得一样。
 *    单独一条气泡之后，资讯那一块坏了就是坏了，没有旁的解释。
 */
export function demoNewsSeq(): Ev[] {
  const NEWS: NewsPayload = {
    items: [
      {
        title: "高盛看高微软至640美元 维持“买入”评级",
        excerpt: "",
        source: "金融界·首页头条",
        source_id: "c4a-jrj",
        ts: "2026-09-22 20:00:58",
        url: "https://stock.jrj.com.cn/2026/09/22200058516063.shtml",
        heat: 1,
        seen_in: ["c4a-jrj"],
      },
      {
        title: "拍断大腿！江淮汽车尾盘突袭涨停，最高封单量超67.4万手，玛莎拉蒂三方传闻+华为合作表态引爆",
        excerpt: "",
        source: "金融界·首页头条",
        source_id: "c4a-jrj",
        ts: "2026-09-22 19:47:58",
        url: "https://stock.jrj.com.cn/2026/09/22194758515835.shtml",
        heat: 1,
        seen_in: ["c4a-jrj"],
      },
      {
        title: "天顺风能控股股东上海天神质押1620万股偿还负债，同日解除质押1295万股",
        excerpt: "",
        source: "金融界·首页头条",
        source_id: "c4a-jrj",
        ts: "2026-09-22 19:45:58",
        url: "https://finance.jrj.com.cn/2026/09/22194558515848.shtml",
        heat: 1,
        seen_in: ["c4a-jrj"],
      },
      {
        title: "9月22日锂电行业信息汇总：碳酸锂现货挺价、期货收涨2.53%重返13万，杉杉股份、科达制造同日公告超96亿负极扩产",
        excerpt: "",
        source: "金融界·首页头条",
        source_id: "c4a-jrj",
        ts: "2026-09-22 19:22:58",
        url: "https://finance.jrj.com.cn/2026/09/22192258515551.shtml",
        heat: 1,
        seen_in: ["c4a-jrj"],
      },
      {
        title: "美股异动丨阿里巴巴盘前涨超3% 云栖大会“火力全开” 花旗料其AI业务发展潜力或进一步释放",
        excerpt: "",
        source: "金融界·首页头条",
        source_id: "c4a-jrj",
        ts: "2026-09-22 19:16:58",
        url: "https://stock.jrj.com.cn/2026/09/22191658515447.shtml",
        heat: 1,
        seen_in: ["c4a-jrj"],
      },
      {
        title: "川大智胜：因涉嫌信息披露违法违规 被证监会立案",
        excerpt: "",
        source: "金融界·首页头条",
        source_id: "c4a-jrj",
        ts: "2026-09-22 19:13:58",
        url: "https://stock.jrj.com.cn/2026/09/22191358515425.shtml",
        heat: 1,
        seen_in: ["c4a-jrj"],
      },
    ],
  }
  return [
    { type: "tool_start", name: "get_news", args: { query: "沪深300" } },
    { type: "tool_end", name: "get_news", ok: true, ms: 8100 },
    { type: "news", news: NEWS },
    { type: "token", text: "\n\n上面那批是**刚采到的资讯原文**，只采不析 —— " },
    { type: "token", text: "我没有替它们判断利好还是利空。\n\n" },
    { type: "token", text: "「同题 N 源」那一栏数的是**同一件事出现在几个源里**，不是我的判断。" },
    { type: "token", text: "\n\n资讯只是事实，**不构成投资建议**。" },
  ]
}

/** 演示的第二段：**另一条气泡**，装估值分位卡。
 *  ★为什么要另起一条气泡：`Msg.chart` 是**单槽**（一条气泡一张图），
 *    而分位卡和上面那张 K 线图要同时看得见 —— 塞进同一条只会互相顶掉。
 *  ★为什么单独一个函数：`demoSeq()` 那条序列是**一整轮问答**，
 *    这里是一轮新的（工具调用 + 图 + 正文），混在一起回头没法单独改。
 *    两段都喂**同一个 reducer**，纪律不变（见文件头）。 */
export function demoFundSeq(): Ev[] {
  return [
    { type: "tool_start", name: "get_fundamentals", args: { symbol: "600519" } },
    { type: "tool_end", name: "get_fundamentals", ok: true, ms: 860 },
    { type: "chart", chart: demoFund() },
    { type: "token", text: "\n\n上面那张卡里的每个数都能自己核：" },
    { type: "token", text: "现价 PE 19.30，近 5 年区间 17.66~54.97 —— 所以" },
    { type: "token", text: "分位 3.4% 说得通（几乎就是这 5 年最便宜的一档）。\n\n" },
    { type: "token", text: "但**分位低不等于便宜**：它只说明「" },
    { type: "token", text: "比它自己过去便宜」，不是说比同行便宜、更不是说该买。" },
    { type: "token", text: "这里也没有股息率 —— 这一项本仓库取不到，不做推测。" },
  ]
}
