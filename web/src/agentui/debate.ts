/**
 * 多智能体博弈的**状态机**。一个 reducer，两个入口（真实 SSE / 演示回放）。
 *
 * ★这条纪律是从 `chat.ts` 顶上那段直接搬过来的：
 *   「demo() 必须走和真实回答**同一个** handle()，否则测的是另一条代码路径，等于没测」。
 *   所以 `applyEvent()` 既是 `streamAgent` 每帧的处理函数，也是演示回放每一步的
 *   处理函数 —— 回放不是"照着写一遍"，它走的就是这一条。
 *
 * ★**未知事件类型不丢弃**：这一页的事件表是新契约（规格 §3.4），
 *   服务端加一个新 type 时，页面上唯一能看见这件事的地方就是这里记的那个数。
 *   静默丢弃的话，"新事件发了但页面没反应"在报告里长得像"页面忘了做"。
 */
import { ROLES, arr, numOrNull, type AgentEv, type Side } from "../api2"
import { ROLE_ORDER } from "./roles"

export type Say = {
  role: string
  round: number
  text: string
  stance: number | null
  conf: number | null
  evidence: string[]
  ms: number | null
}

export type Weights = {
  w: Record<string, number>
  scenario: string
  basis: Record<string, unknown>
}

export type Veto = {
  by?: string
  rule?: string
  reason?: string
  downgrade_to?: string
}

export type Verdict = {
  side: Side
  score: number
  confidence: number
  reasons: string[]
  dissent: string[]
  veto: Veto | null
  risk_note: string | null
}

export type Debate = {
  /** 服务端声明过的轮次（`agent_round`），升序去重。 */
  rounds: number[]
  /** 本轮参与的角色顺序。**第一次 `agent_round` 到达前**用规格里的 `ROLES` 兜底
   *  —— 卡片在开跑前就得有位置，否则页面会先空一块再"长"出来。 */
  order: string[]
  says: Say[]
  /** 角色 → 它自己历轮的发言（**升序按到达顺序**，不是按 round 排）。
   *  ★轮次数组而不是"最新一条"：第 2 轮要判"改了没改"，就非得留住第 1 轮那条。 */
  byRole: Record<string, Say[]>
  /** 正在说话的那个角色（`agent_start` 到 `agent_done` 之间）。 */
  speaking: string | null
  /** 角色的收尾状态：`true` 成功 / `false` 失败 / `undefined` 还没结束。 */
  finished: Record<string, boolean>
  weights: Weights | null
  verdict: Verdict | null
  done: { ms: number; steps: number | null } | null
  /** 流级别的错（`type:'error'`，或 transport 报的错）。 */
  err: string | null
  /** 服务端发来的、这一版前端**不认识**的事件类型个数。 */
  unknown: number
  /** 跑了几毫秒（本地钟，来自 `heartbeat`）。**和 `done.ms` 是两回事**：
   *  后者是服务端自己量的，前者是"从发起到现在"。 */
  heartbeat: number | null
}

export const emptyDebate: Debate = {
  rounds: [],
  order: [...ROLE_ORDER],
  says: [],
  byRole: {},
  speaking: null,
  finished: {},
  weights: null,
  verdict: null,
  done: null,
  err: null,
  unknown: 0,
  heartbeat: null,
}

export function applyEvent(d: Debate, ev: AgentEv): Debate {
  switch (ev.type) {
    case "agent_round": {
      const r = numOrNull((ev as { round?: unknown }).round)
      const roles = arr((ev as { roles?: string[] }).roles).filter((x) => typeof x === "string")
      const rounds = r === null || d.rounds.includes(r) ? d.rounds : [...d.rounds, r].sort((a, b) => a - b)
      // ★`roles` 是**并集**，不是替换。
      //   第二轮的 `roles` 通常只列"这一轮还要说话的那几个"（演示那一场就是 6 个），
      //   直接赋值的话，第二轮一到，没被列进去的三个角色的**卡片就整块消失** ——
      //   而它们上一轮说过话、卡片上还挂着发言。九宫格变成六宫格是这种赋值造成的，
      //   不是服务端少给了角色。顺序按"第一次被提到的先后"稳定下来。
      const order = [...d.order]
      for (const x of roles) if (!order.includes(x)) order.push(x)
      return { ...d, rounds, order }
    }
    case "agent_start": {
      const role = String((ev as { role?: unknown }).role ?? "")
      if (!role) return { ...d, unknown: d.unknown + 1 }
      // ★`name`/`icon` 是服务端给的，页面上优先用它 —— 前端那张 `ROLE_META`
      //   只是兜底（见 `roles.ts` 顶上那段）。
      return { ...d, speaking: role, finished: { ...d.finished, [role]: undefined as unknown as boolean } }
    }
    case "agent_say": {
      const e = ev as unknown as {
        role?: unknown; round?: unknown; text?: unknown
        stance?: unknown; conf?: unknown; evidence?: unknown; ms?: unknown
      }
      const role = String(e.role ?? "")
      if (!role) return { ...d, unknown: d.unknown + 1 }
      const say: Say = {
        role,
        round: numOrNull(e.round) ?? d.rounds[d.rounds.length - 1] ?? 1,
        text: typeof e.text === "string" ? e.text : "",
        // ★`null` 要留住，不许 `?? 0`：**"没表态"和"完全中立"是两件事**，
        //   补成 0 之后"这个角色弃权了"会被画成"它立场正好在中间"。
        stance: numOrNull(e.stance),
        conf: numOrNull(e.conf),
        evidence: arr(e.evidence as string[]).filter((x) => typeof x === "string"),
        ms: numOrNull(e.ms),
      }
      // ★说了话的角色**必须有卡片**：`agent_round` 里没列它（服务端少列、
      //   或者这一轮临时加了个角色）时在这里补进顺序里。不补的话，
      //   它的发言会出现在下面的发言流里，而上面找不到它那张卡 ——
      //   读者看到的是"一句话没有出处"。
      const order = d.order.includes(role) ? d.order : [...d.order, role]
      return {
        ...d,
        order,
        says: [...d.says, say],
        byRole: { ...d.byRole, [role]: [...(d.byRole[role] ?? []), say] },
      }
    }
    case "agent_done": {
      const e = ev as unknown as { role?: unknown; ok?: unknown }
      const role = String(e.role ?? "")
      if (!role) return { ...d, unknown: d.unknown + 1 }
      return {
        ...d,
        speaking: d.speaking === role ? null : d.speaking,
        finished: { ...d.finished, [role]: e.ok !== false },
      }
    }
    case "agent_weights": {
      const e = ev as unknown as { w?: unknown; scenario?: unknown; basis?: unknown }
      const raw = (e.w && typeof e.w === "object" ? e.w : {}) as Record<string, unknown>
      const w: Record<string, number> = {}
      for (const [k, v] of Object.entries(raw)) {
        const n = numOrNull(v)
        if (n !== null) w[k] = n
      }
      return {
        ...d,
        weights: {
          w,
          scenario: typeof e.scenario === "string" ? e.scenario : "",
          basis: (e.basis && typeof e.basis === "object" ? e.basis : {}) as Record<string, unknown>,
        },
      }
    }
    case "agent_verdict": {
      const e = ev as unknown as {
        side?: unknown; score?: unknown; confidence?: unknown
        reasons?: unknown; dissent?: unknown; veto?: unknown; risk_note?: unknown
      }
      const vetoRaw = e.veto && typeof e.veto === "object" ? (e.veto as Record<string, unknown>) : null
      return {
        ...d,
        verdict: {
          // ★方向缺了就**照实说不知道**（`flat` 是本站的"不表态"，
          //   不是"看多"也不是"看空"）—— 补一个 `long` 是最坏的一种补法。
          side: (["long", "short", "flat"].includes(String(e.side)) ? String(e.side) : "flat") as Side,
          score: numOrNull(e.score) ?? 0,
          confidence: numOrNull(e.confidence) ?? 0,
          reasons: arr(e.reasons as string[]).filter((x) => typeof x === "string"),
          dissent: arr(e.dissent as string[]).filter((x) => typeof x === "string"),
          veto: vetoRaw ? {
            by: typeof vetoRaw.by === "string" ? vetoRaw.by : undefined,
            rule: typeof vetoRaw.rule === "string" ? vetoRaw.rule : undefined,
            reason: typeof vetoRaw.reason === "string" ? vetoRaw.reason : undefined,
            downgrade_to: typeof vetoRaw.downgrade_to === "string" ? vetoRaw.downgrade_to : undefined,
          } : null,
          risk_note: typeof e.risk_note === "string" ? e.risk_note : null,
        },
      }
    }
    case "done": {
      const e = ev as unknown as { ms?: unknown; steps?: unknown }
      return { ...d, done: { ms: numOrNull(e.ms) ?? 0, steps: numOrNull(e.steps) }, speaking: null }
    }
    case "error": {
      const e = ev as unknown as { msg?: unknown; hint?: unknown; code?: unknown }
      return {
        ...d,
        err: [e.msg, e.hint, e.code].filter((x) => typeof x === "string" && x).join(" · ") || "服务端报了个错",
        speaking: null,
      }
    }
    case "heartbeat": {
      const e = ev as unknown as { elapsed?: unknown }
      return { ...d, heartbeat: numOrNull(e.elapsed) }
    }
    default:
      return { ...d, unknown: d.unknown + 1 }
  }
}

/** 把一串事件折叠成终态。**演示回放走的就是这个函数**。 */
export function applyAll(evts: AgentEv[]): Debate {
  return evts.reduce(applyEvent, emptyDebate)
}

// ── 派生量（页面上要显示的东西都从这里来，不许在组件里另算一遍）────────

/** 一个角色的立场轨迹：**第一次表态**与**最后一次表态**。
 *  ★"第一次"取的是**第 1 轮**那条（不是"数组第一条"）—— 演示和真实链路里
 *    到达顺序都可能乱，按 round 取才是"轮次变了没有"这件事本身。 */
export function stanceTrack(says: Say[]): {
  first: number | null; firstRound: number | null
  last: number | null; lastRound: number | null
  changed: boolean
} {
  if (!says.length) return { first: null, firstRound: null, last: null, lastRound: null, changed: false }
  const withStance = says.filter((s) => s.stance !== null)
  if (!withStance.length) {
    return {
      first: null, firstRound: says[0].round, last: null, lastRound: says[says.length - 1].round,
      changed: false,
    }
  }
  const sorted = [...withStance].sort((a, b) => a.round - b.round)
  const first = sorted[0]
  const last = sorted[sorted.length - 1]
  // ★"改了立场"用的是 **stance 数值真的动了**，不是"说了第二轮"。
  //   阈值取一个比 0.01 大的数（0.02）：服务端给的 stance 是浮点，
  //   0.6200000001 和 0.62 之间的差不是"改了立场"。
  const changed = first.round !== last.round && Math.abs((last.stance as number) - (first.stance as number)) >= 0.02
  return {
    first: first.stance, firstRound: first.round,
    last: last.stance, lastRound: last.round,
    changed,
  }
}

/** 本轮**多空张力的净值**（只算有 stance 的角色，按权重加权）。
 *  ★它是一个**诊断量**，不是"看多看空指数"——页面上必须写明它怎么算的，
 *    否则一个从 −1 到 +1 的数会被当成信号。没有权重时退化成等权。 */
export function tension(d: Debate): { value: number; n: number; note: string } {
  const w = d.weights?.w ?? {}
  let num = 0
  let den = 0
  // ★`n` 数的是**角色**，不是发言条数 —— 把它写成 `says.filter(...).length`
  //   的话，一个角色说两遍就被数成两个角色（演示那一场里 9 个角色会报成 11 个，
  //   而四个字是"个角色"）。这里和上面那个加权平均**数同一批人**：
  //   进了加权平均的角色才计数，两个数永远对得上。
  let n = 0
  for (const s of d.says) {
    if (s.stance === null) continue
    // 每个角色只算它**最后**那一条：一个人说两遍不等于两个人说一遍
    const last = (d.byRole[s.role] ?? []).filter((x) => x.stance !== null).slice(-1)[0]
    if (!last || last !== s) continue
    const weight = weightOf(w, s.role)
    num += last.stance * weight
    den += weight
    n += 1
  }
  if (!den) return { value: 0, n: 0, note: "还没有任何角色给出数值立场" }
  return {
    value: num / den,
    n,
    note: d.weights
      ? "按本轮动态权重加权（每个角色只算它最后一次表态）"
      : "还没有权重表，暂时等权（每个角色只算它最后一次表态）",
  }
}

/** 角色的权重。**权重表里没有它就当等权中最小的一档** ——
 *  不许悄悄补成 0（0 权重 = 这一票不算数，而"表里没有"只是"没说"）。 */
function weightOf(w: Record<string, number>, role: string): number {
  const v = w[role]
  if (typeof v === "number" && Number.isFinite(v) && v >= 0) return v
  return 1 / Math.max(1, ROLES.length)
}

/** 权重表里各项加起来不等于 1 时**照实说**（不归一化）。
 *  归一化会把"服务端算错了"这个事实抹掉 —— 而权重表就是这一页要看的东西。 */
export function weightSum(w: Record<string, number>): number {
  return Object.values(w).reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0)
}
