/**
 * **这一页自己的**同步取数（`?probe=1` 那条路）。
 *
 * ★为什么不直接用 `agentui/probe.ts` 的 `callSync`：**那一份的成功分支是坏的**。
 *   它的 `fromBody` 形参叫 `d`，最后一行却写 `return { ok: true, data }` ——
 *   `data` 这个名字在作用域里不存在 ⇒ 运行时 `ReferenceError: data is not defined`
 *   （`tsc` 也报 `TS18004`）。而 `vite build` **不做类型检查**（esbuild/rolldown
 *   只剥类型），所以它一直是绿的。
 *
 *   ★它到今天才发作，是因为**成功分支此前从来没有跑过**：这五页的后端还没上线，
 *     `callSync` 一进去就落在失败分支上（那条是好的，返回 `{ok:false, ...}`）。
 *     现在 `/api/collect/status` 与 `/api/collect/ring` 都通了、回 `ok:true`
 *     ⇒ 第一次走成功分支 ⇒ **整页白屏**（实测 `/collect?probe=1`：`#root` 里
 *     一个子节点都没有，控制台一条 `ReferenceError: data is not defined`，
 *     栈落在 `useMemo` 里 —— 见交付说明）。
 *
 *   ★这份**不是"顺手修"那个公共底座** —— 那一条的处置权在协调者
 *     （`research.ts:126` 已经把它记进交付说明，`Research.tsx` 也自己带了一份绕开）。
 *     这一份照 `research.ts` / `dash.ts` / `portfolio.ts` / `ledger.ts` / `game.ts`
 *     那五处**私有同步取数**的站内约定写，只属于这一页。
 *     **公共底座哪天修好了，这一份可以直接删掉、改回 `agentui/probe.ts`。**
 *     （同一条缺陷还挂在 `/linkage?probe=1` 上 —— 实测同样白屏、同一条栈。
 *       `/intraday` 的通道回 `ok:false` 时落在失败分支上，暂时不发作。）
 *
 * ★分类顺序**必须和 `api2.call()` 逐条一致** —— 两处不一致的话，"同一个后端错误"
 *   在用户那条路和探针那条路上会显示成两句不同的话（`agentui/probe.ts` 顶上那句）。
 *
 * ★只在 `probe === true` 时被调用。用户那条路（`api2.call()`）一个字节都不受影响。
 */
import type { Res } from "../../api2"

/** 一次**同步**往返。和 `api2.call()` 一样：**永不抛**，失败也是一种返回值。 */
export function callSync<T>(url: string, body?: unknown): Res<T> {
  const x = new XMLHttpRequest()
  try {
    x.open(body === undefined ? "GET" : "POST", url, false)
    if (body !== undefined) x.setRequestHeader("Content-Type", "application/json")
    x.send(body === undefined ? null : JSON.stringify(body))
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
  if (status === 400 || status === 422) {
    // 本项目的接口在 `ok:false` 时发 400，响应体**照样是一份完整 JSON**
    // （`api2.ts: call()` 顶上那段）。所以 400 要先读体，读不出 JSON 才算 badreq。
    // ★这里按 `api2.call()` 的写法**带着 `T` 去解析**，而不是原地解成
    //   `{ok?, msg?…}` 再交给 `fromBody` —— 后者会让 `fromBody` 把 `T` 推成
    //   那个字面量对象类型，返回 `Res<{ok?…}>`、赋值不回 `Res<T>`
    //   （公共底座 `agentui/probe.ts:49` 至今挂着这一条 `TS2322`）。
    const j = tryJson<T>(text)
    if (j && (j as { ok?: boolean }).ok === false) return fromBody<T>(j, status)
    return { ok: false, kind: "badreq", status, reason: "请求被服务端拒了", detail: text.slice(0, 200) }
  }
  // ★`status === 0`（同步 XHR 的"根本没连上"）与空响应体都落这里。
  //   不落这里的话会走到 `JSON.parse("")` 抛异常 —— 而"抛出去"在探针里
  //   就是整块内容消失，不是显示一条原因。
  if (!text) {
    return {
      ok: false, kind: "offline", status,
      reason: "服务端没有给出响应（同步探针什么都没拿到）",
    }
  }
  const data = tryJson<T>(text)
  if (data === undefined) {
    return {
      ok: false, kind: "badjson", status,
      reason: "服务端返回的不是 JSON（多半是错误页）", detail: text.slice(0, 200),
    }
  }
  return fromBody<T>(data, status)
}

function tryJson<T>(s: string): T | undefined {
  try {
    return JSON.parse(s) as T
  } catch {
    return undefined
  }
}

/** `ok:false` 的响应体 → `Fail`。**原因优先用服务端给的那句**（同 `api2.fromBody`）。
 *  ★形参名与返回里用的名字**必须是同一个** —— 公共底座那一份就是错在这里
 *    （形参 `d`、返回写 `data`），而 `vite build` 不做类型检查 ⇒ 构建期不报、
 *    运行期才炸。所以这里刻意写成 `data: d` 而不是简写。 */
function fromBody<T>(d: T, status: number): Res<T> {
  const o = d as { ok?: boolean; msg?: string; error?: string; hint?: string }
  if (o && o.ok === false) {
    return {
      ok: false, kind: "server", status,
      reason: o.msg || o.error || "服务端说这条取不到",
      detail: o.hint,
    }
  }
  return { ok: true, data: d }
}
