/**
 * **探针专用**的同步取数（`?probe=1` 那条路）。
 *
 * ★为什么非要这份同步版：渲染层判据全部押在"几何在同一个 tick 里就绪"上
 *   （`main.tsx` 顶上那段），而异步取数回来之后再渲染，同步探针量到的是"正在取数…"。
 *   那不是判据变红，是判据**消失**（而且消失得像"页面挺正常"）。
 *   先例是 `dash.ts` / `portfolio.ts` / `ledger.ts` / `game.ts` 里那四份 `postSync`。
 *
 * ★为什么不复用那四份：它们是各文件里的**私有函数**（没有 export），
 *   而且**成功时不处理失败分支**（`JSON.parse` 出去就抛）。这一批五个通道
 *   **后端还不存在**，探针跑到这里必定是"取不到" —— 所以这一份必须把
 *   `api2.call()` 那套失败分类**原样搬过来**（`offline`/`notfound`/`server`/
 *   `badjson`/`badreq`），否则探针量到的是一个抛出来的异常，
 *   而页面上会缺掉"这条通道还没接上"那一块（正是这一批页面最该看的东西）。
 *
 * ★这份代码**只在 `probe===true` 时被调用**，用户那条路一个字节都不受影响
 *   （同 `dash.ts` 那句）。同步 XHR 的弃用告警由浏览器控制台出，这里没有别的选择。
 */
import type { Fail, Res } from "../api2"

/** 一次**同步**往返。和 `api2.call()` 一样：**永不抛**，失败也是一种返回值。
 *  ★分类顺序必须和 `api2.call()` 一致 —— 两处不一致的话，"同一个后端错误"
 *    在用户那条路和探针那条路上会显示成两句不同的话。 */
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
    // ★`T &` 不能省：`fromBody` 收的是 `T`，而这里手上是 `tryJson` 出来的**那个物件** ——
    //   不带 `T` 就报 TS2322（`Res<{ok?:…}>` 不是 `Res<T>`）。下面第 68 行那处同理，
    //   它写的是 `data as T & {…}`。两份写法要一起看，别只改一处。
    const d = tryJson<T & { ok?: boolean; msg?: string; error?: string; hint?: string }>(text)
    if (d && d.ok === false) return fromBody(d, status)
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
  return fromBody(data as T & { ok?: boolean; msg?: string; error?: string; hint?: string }, status)
}

function tryJson<T>(s: string): T | undefined {
  try {
    return JSON.parse(s) as T
  } catch {
    return undefined
  }
}

/** `ok:false` 的响应体 → `Fail`。**原因优先用服务端给的那句**（同 `api2.fromBody`）。 */
function fromBody<T>(d: T, status: number): Res<T> {
  const o = d as { ok?: boolean; msg?: string; error?: string; hint?: string }
  if (o && o.ok === false) {
    return {
      ok: false, kind: "server", status,
      reason: o.msg || o.error || "服务端说这条取不到",
      detail: o.hint,
    }
  }
  // ★★`data: d` —— **参数叫 `d`，不叫 `data`**。这一行是从 `api2.ts` 那份 `fromBody`
  //   抄过来的，而那份的参数恰好就叫 `data`，所以那行 `{ ok: true, data }` 是对的；
  //   搬过来时参数改名成了 `d`，这一行却原样留着 ⇒ `data` 不在作用域（TS18004），
  //   而 `vite build` **不做类型检查** ⇒ 它照样上线，成功分支返回 `data: undefined`
  //   ⇒ 探针那条路白屏（`/linkage?probe=1` 就撞在这上面）。
  //   ⚠ 再抄这一份时，改参数名要连着看下一行的属性简写。
  return { ok: true, data: d }
}

/** 探针路径上"这一屏的数据"。`phase: "loading"` 只在**用户那条路**上出现 ——
 *  同步探针拿到的一定是终态（成功或者一条 `Fail`），不存在"还在取"。
 *  ★这样写是为了让页面里只有**一处**判断"该显示什么"，而不是在 JSX 里
 *    分散地 `probe ? ... : ...`。 */
export type SyncState<T> = { phase: "loading" } | { phase: "fail"; fail: Fail } | { phase: "ok"; data: T }

export function okState<T>(data: T): SyncState<T> {
  return { phase: "ok", data }
}

export function failState<T>(f: Fail): SyncState<T> {
  return { phase: "fail", fail: f }
}
