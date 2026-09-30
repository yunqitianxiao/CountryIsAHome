/**
 * 引导智能体的数据层：`/api/siteguide` 那份**本站结构**。
 *
 * ★这一层**不写任何介绍文字**。选项卡上的每一个字（有几页、几个角色、
 *   多少个词条、回测那几个数）都从这一个接口来；接口那一侧的来源是
 *   `site_map.json`，而那份 JSON 是 `build_sitemap.py` 从源码反向解析出来的。
 *   链条是：
 *
 *       源码 ──build_sitemap.py──▶ site_map.json ──/api/siteguide──▶ 这一层
 *
 *   这里手写一句话，就多出一份**会悄悄变假**的真源（改了页面只有一处跟着动，
 *   另一处看着一模一样）—— 那正是这个仓库对"第二份真源"零容忍的原因。
 *
 * ★失败分类走 `api2` 那一套（`offline` / `notfound` / `server` / `badjson` /
 *   `badreq` / `timeout`）：这一页必须是"这条通道取不到"和"这个站结构里真的
 *   没有那一块"两句不同的话。合成一句"加载失败"就把前者伪装成后者了。
 */
import { CHANNEL, call, type Fail, type Res } from "./api2"

/** 一个能一眼核对的数字。`v` **是字符串**：服务端给的原文（`'255'` / `'5/15/30'`），
 *  这一层不解析、不换算、不补 0 —— 拿不到就是 `null` 那一格。 */
export type Fact = { k: string; v: string }

/** 选项卡上的一张卡。字段形状**照服务端**（`agent/siteguide.py` 的 `_sec_*`），
 *  前端不自己造名字。 */
export type Section = {
  id: string
  /** 对应的主页卡 id（总览/页面地图/实测口径/资讯那几张是 `null`）。 */
  card_id: string | null
  /** "去这一页"那个按钮的目标。`/` 表示回主页。 */
  href: string
  /** 选项卡上那个短标签。★**它就是 `TabsTrigger` 的字**。 */
  kicker: string
  title: string
  intro: string
  facts: Fact[]
  bullets: string[]
  /** "点一下就能问"的问题。★没有数字，所以服务端那边可以手写措辞。 */
  asks: string[]
  /** 那份名单原文（九个角色的名字、十一把工具的名字…）。可能没有。 */
  quote?: string
}

export type Freshness = {
  ok: boolean
  stale: string[]
  n: number
  generated_at: string
  why?: string
}

export type Guide = {
  ok: boolean
  available: boolean
  /** 结构过时那一句人话（空串 = 与源码一致）。 */
  stale_hint: string
  freshness: Freshness
  sections: Section[]
  /** `ok:false` 时服务端给的原因。**优先用它**，别自己造一句。 */
  msg?: string
}

export const GUIDE_CHANNEL = CHANNEL.siteguide
/** 引导对话那条端点。与 `/agent` 上的 `/api/chat` **共用同一套服务端机器**
 *  （`llm.build_messages` + `llm.sse_stream`），只在系统提示词里多拼一块
 *  `siteguide.prompt_block()`（本站结构）。差别只有这一处 —— 多写一条链路的
 *  话，风险提示、工具循环那两件事就会在两条路上各长一个样。 */
export const GUIDE_CHAT = "/api/siteguide/chat"

export function loadGuide(): Promise<Res<Guide>> {
  return call<Guide>(GUIDE_CHANNEL)
}

/** **同步**取一份（只有探针那条路会调）。理由见文件头。 */
export function loadGuideSync(): Res<Guide> {
  return syncGet<Guide>(GUIDE_CHANNEL)
}

/* ── 这一页自己的同步取数（`?probe=1` 那条路）─────────────────────────
 *
 * ★为什么不直接用 `agentui/probe.ts` 的 `callSync`：**那一份的成功分支是坏的**。
 *   它的 `fromBody` 形参叫 `d`，最后一行却写 `return { ok: true, data }` ——
 *   `data` 在那个作用域里不存在 ⇒ 运行时 `ReferenceError: data is not defined`
 *   （`tsc` 也报：`agentui/probe.ts(89,22) TS18004`）。而 `vite build` 不做类型
 *   检查（esbuild 只剥类型），所以它一直是绿的。
 *
 *   ★★这一页**正好踩在那颗雷上**：`/api/siteguide` 是**活的真接口**、回 `ok:true`
 *     ⇒ 探针一进去就走成功分支 ⇒ `/siteguide?probe=1` 整页白屏、
 *     `probeAndBeacon()` 根本没机会跑。**那不是判据变红，是判据全部消失**，
 *     而报告里长得像"这一页没做" —— 正是本仓库最怕的那种失效形态
 *     （`Research.tsx:159` 顶上那段记着同一个病的另一个实例）。
 *
 *   ★**这条缺陷不归这一页修**（`research.ts:126`、`pages/Collect/sync.ts:4`
 *     都把它记成"公共底座的处置权在协调者"）。迄今已有三处绕开它：
 *     `research.ts` 的 `postSync`、`pages/Collect/sync.ts`、以及 `/research`
 *     那一页自带的 `getResearchSync`；`/collect?probe=1` 与 `/linkage?probe=1`
 *     实测**同样白屏、同一条栈**。这一份照那个站内约定写，只属于这一页。
 *     **公共底座哪天修好了（`return { ok: true, data: d }`），这一份可以直接删掉。**
 *
 * ★分类顺序**必须和 `api2.call()` 逐条一致** —— 两处不一致的话，"同一个后端错误"
 *   在用户那条路和探针那条路上会显示成两句不同的话。
 *
 * ★只在 `probe === true` 时被调用。用户那条路（`api2.call()`）一个字节都不受影响。
 */
function syncGet<T>(url: string): Res<T> {
  const x = new XMLHttpRequest()
  try {
    x.open("GET", url, false)
    x.send(null)
  } catch (e) {
    // 建连就炸 = 连不上（服务没起 / 端口不通 / 被代理拦了）
    return {
      ok: false, kind: "offline",
      reason: "连不上本地服务（后端这个通道还没起）",
      detail: String((e as Error)?.message || e),
    }
  }

  const status = x.status
  const text = x.responseText || ""
  if (status === 404) {
    return { ok: false, kind: "notfound", status, reason: `${url} 这条路由服务端还没有` }
  }
  if (status >= 500) {
    return { ok: false, kind: "server", status, reason: `服务端 ${status}`, detail: text.slice(0, 300) }
  }
  // ★`status === 0`（同步 XHR 的"根本没连上"）与空响应体都落这里。
  //   不落这里的话会走到 `JSON.parse("")` 抛异常 —— 而"抛出去"在探针里
  //   就是整块内容消失，不是显示一条原因。
  if (!text) {
    return { ok: false, kind: "offline", status, reason: "服务端没有给出响应（同步探针什么都没拿到）" }
  }
  let data: T
  try {
    data = JSON.parse(text) as T
  } catch {
    return {
      ok: false, kind: "badjson", status,
      reason: "服务端返回的不是 JSON（多半是错误页）", detail: text.slice(0, 200),
    }
  }
  // `ok:false` 的响应体 → `Fail`。**原因优先用服务端给的那句**（同 `api2.fromBody`）。
  // ★形参名与返回里用的名字**必须是同一个** —— 公共底座那一份就是错在这里。
  const d = data as { ok?: boolean; msg?: string; error?: string; hint?: string }
  if (d && d.ok === false) {
    return {
      ok: false, kind: "server", status,
      reason: d.msg || d.error || "服务端说这条取不到",
      detail: d.hint,
    }
  }
  return { ok: true, data }
}

/** 一个 `Res<Guide>` 的前半段能安全当 `Guide` 用吗。
 *  ★服务端的 `sections` 是**数组**才算数：给一个 `null` 的话，
 *    `sections.map` 会在渲染期抛，整页白掉 —— 而白掉在报告里长得像"这一页没做"，
 *    真正的原因（载荷畸形）一个字都看不到。 */
export function sectionsOf(g: Guide | null): Section[] {
  const s = g?.sections
  return Array.isArray(s) ? s.filter((x) => x && x.id && x.kicker) : []
}

/** `Res` 的失败那一半 → `Fail`（`Degraded` 那块要的形状）。
 *  ★原因**优先用服务端给的那句**（`msg`），前端自己造一句就会在服务端换了
 *    措辞之后继续说着旧的。 */
export function mkFail(r: Res<Guide>): Fail {
  if (r.ok) return { ok: false, kind: "server", reason: "（这是成功的那一半，不该走到这里）" }
  return r
}
