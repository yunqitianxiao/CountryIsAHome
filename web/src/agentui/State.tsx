/**
 * 新增五页共用的**版式件与状态件**。
 *
 * ★为什么这五页要共用一层，而不是各写各的：这五页里**每一页**都要表达同一件事
 *   ——「这条通道现在取不到数，原因是这个」。各写一份的话，"取不到"会长出五种样子，
 *   而其中几种看起来像"这一页做坏了"。本项目对这件事的立场写在 README 里
 *   （「抓不到行情就明确报错，绝不编数字」），所以五种样子本身就是缺陷。
 *
 * ★颜色一律走站点 token（`var(--card)` / `var(--ink-3)` 这一族），**不写死色值**。
 *   这些 `var(--…)` 名字就是本文件当前读到的 `index.css` 里的真名 ——
 *   设计系统那边收编成语义 token 时，改这一层就够（各页面用的是这里的组件，
 *   不是自己拼 class）。
 */
import * as React from "react"
import type { Fail } from "../api2"
import { failText } from "../api2"

// ── 卡片 / 分区 ───────────────────────────────────────────────────────

/** 一张卡。`id` 是探针/判据的锚点，**每一张卡都给一个**（没有 id 的卡
 *  在报告里和"这张卡没渲染"长得一样）。 */
export function Panel({
  id, title, note, right, children, aria,
}: {
  id?: string
  title?: React.ReactNode
  note?: React.ReactNode
  right?: React.ReactNode
  children: React.ReactNode
  aria?: string
}) {
  return (
    <section
      className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4"
      id={id}
      aria-label={aria}
    >
      {title || note || right ? (
        <div className="chd mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-1">
          {title ? <h2 className="text-base font-semibold text-[var(--ink)]">{title}</h2> : null}
          {note ? <span className="text-xs text-[var(--ink-3)]">{note}</span> : null}
          {right ? <div className="ml-auto">{right}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  )
}

/** 卡内的一级凹槽（"卡里再套一块"）。底色用 `--paper`，两套主题里都在卡片**下面**。 */
export function Well({ children, className = "", id }: {
  children: React.ReactNode; className?: string; id?: string
}) {
  return (
    <div id={id} className={`rounded border border-[var(--rule)] bg-[var(--paper)] p-2 ${className}`}>
      {children}
    </div>
  )
}

/** 一档读数（标签 + 数字）。数字走 `.tnum`：行情表里小数点不对齐是最刺眼的
 *  "不像金融产品"。`k` 是探针锚点（没有锚点的数字，判据只能按文本找，
 *  而文本会随文案漂）。 */
export function Stat({ label, value, sub, k, tone, title }: {
  label: React.ReactNode
  value: React.ReactNode
  sub?: React.ReactNode
  k?: string
  tone?: "up" | "down" | "flat" | "plain"
  title?: string
}) {
  const cls = tone === "up" ? "text-up" : tone === "down" ? "text-down"
    : tone === "flat" ? "text-[var(--ink-3)]" : "text-[var(--ink)]"
  return (
    <Well>
      <div className="text-[10.5px] leading-tight text-[var(--ink-3)]">{label}</div>
      <div className={`tnum text-base ${cls}`} data-k={k} title={title}>{value}</div>
      {sub ? <div className="mt-0.5 text-[10.5px] text-[var(--ink-3)]">{sub}</div> : null}
    </Well>
  )
}

/** 一条中性的进度条（宽度 = 一个 0–100 的数）。
 *  ★**中性色**（`--accent`），不是涨跌色：它量的东西（强度、权重、覆盖率）
 *    不是盈亏。`dash.ts` 里那条"分位条不能用涨跌色"是同一个道理。 */
export function Meter({ v, max = 100, k, title }: {
  v: number | null; max?: number; k?: string; title?: string
}) {
  const w = v === null || !Number.isFinite(v) ? 0 : Math.max(0, Math.min(100, (v / max) * 100))
  return (
    <div className="relative h-1.5 w-full min-w-[56px] rounded-full bg-[var(--rule-2)]"
         data-k={k} title={title}>
      <div className="absolute inset-y-0 left-0 rounded-full bg-[var(--accent)]"
           style={{ width: `${w}%` }} />
    </div>
  )
}

// ── 三态：加载 / 取不到 / 空 ──────────────────────────────────────────

export function Loading({ what = "正在取数" }: { what?: string }) {
  return (
    <p className="text-sm text-[var(--ink-3)]" role="status" aria-live="polite" data-k="loading">
      {what}…
    </p>
  )
}

/** **取不到数时唯一允许显示的东西**。
 *
 *  ★三句话必须分开，因为它们指向**三个不同的下一步**：
 *    · 后端还没上线的路由 → "这条通道还没接上"，用户什么都不用做；
 *    · 服务端报错 → "它在，但它说做不了"，用户该去看服务端的日志；
 *    · 请求发错 → "我们发错了"，这是**前端的锅**，不能赖后端。
 *    合成一句"加载失败"就把前两种伪装成第三种了。
 *
 *  ★`reason` 优先用服务端给的那句（`Fail.reason` 在 `api2.ts` 里已经这么选了）。
 *    前端自己造一句话，就会在服务端换了措辞之后**继续说着旧的**。 */
export function Degraded({ channel, fail, what }: {
  channel: string
  fail: Fail
  what?: string
}) {
  return (
    <div
      className="rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] p-3 text-sm text-[var(--warn-ink)]"
      id="degraded" data-k="degraded" data-kind={fail.kind} role="status"
    >
      <div className="font-semibold" data-k="degradedTitle">
        这条通道还没接上{what ? `（${what}）` : ""}
      </div>
      <div className="mt-1">
        <span className="opacity-80">通道</span> <code className="tnum">{channel}</code>
        <span className="mx-2 opacity-40">·</span>
        <span className="opacity-80">原因</span> {fail.reason || failText(fail)}
      </div>
      <div className="mt-1 text-xs opacity-80">
        这一页<b>不会</b>用编出来的数字顶替它 —— 取不到就说取不到。后端上线后再回来刷一次。
      </div>
      {fail.detail ? (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs opacity-80">原始错误（给开发者看）</summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all text-[11px]">
            {fail.status ? `HTTP ${fail.status}\n` : ""}{fail.detail}
          </pre>
        </details>
      ) : null}
    </div>
  )
}

/** `ok:true` 但这一屏没东西可显示。**和 `Degraded` 是两件事**：
 *  "今天没有异动"与"接口坏了"在页面上必须两句不同的话，
 *  否则用户会把平静的一天当成故障，或者把故障当成平静的一天。 */
export function Empty({ what, hint }: { what: string; hint?: React.ReactNode }) {
  return (
    <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-3 text-sm text-[var(--ink-2)]"
         id="empty" data-k="empty" role="status">
      <b>{what}</b>
      {hint ? <div className="mt-1 text-xs text-[var(--ink-3)]">{hint}</div> : null}
    </div>
  )
}

/** 后端报的错（`resp.errors` / `collected.sources[].err`）。**照抄原句**，
 *  一个字都不翻译 —— 它是给人拿去 grep 日志用的。 */
export function ErrorList({ items, title = "后端说这些没做出来" }: {
  items: string[]; title?: string
}) {
  if (!items.length) return null
  return (
    <div className="mt-2 rounded border border-[var(--rule)] bg-[var(--paper)] p-2" data-k="errors">
      <div className="text-xs font-semibold text-[var(--ink-2)]">{title}</div>
      <ul className="mt-1 list-disc pl-5 text-xs text-[var(--ink-2)]">
        {items.map((e, i) => <li key={i}>{e}</li>)}
      </ul>
    </div>
  )
}

// ── 演示开关（★只在 dev 生效）────────────────────────────────────────

/** **演示模式**：`import.meta.env.DEV && ?demo=1`。
 *
 *  ★两道锁，第二道是结构性的：
 *    ① `import.meta.env.DEV` —— Vite 在**构建期**把它替换成字面量 `false`
 *       （dev 是 `true`）。所以**生产产物里这个 `&&` 左边就是 `false`**，
 *       右边连求值都不会求值，`DEMO` 恒为 `false`。
 *       ⇒ 生产环境**无论带什么查询串都进不了演示模式**。这不是"约定"，
 *         是构建期的常量折叠。
 *    ② `?demo=1` —— 开发时也要显式要，避免"跑起来就看见假数据"。
 *
 *  ★为什么需要它：这批后端**现在还不存在**，没有演示模式的话这五页在开发期
 *    只能看到降级形态，"两轮发言流""多空交锋"这些版式根本没法做也没法看。
 *  ★代价说清楚：演示数据的那几个函数**仍然会被打进产物**（它就在模块里）。
 *    能保证的是它们**渲染不出来**（上面第①条）。交付说明里写了怎么核这件事。
 */
export const DEMO: boolean =
  import.meta.env.DEV && typeof location !== "undefined" && /[?&]demo=1(&|$)/.test(location.search)

/** 演示模式下的头部横幅。**只要 DEMO 为真就必然出现**，而且写在页面最顶上、
 *  用警告色 —— 演示数据最坏的用法就是被当成行情看，所以它不能是一行小字。 */
export function DemoBanner({ what }: { what: string }) {
  if (!DEMO) return null
  return (
    <div
      className="rounded border-2 border-[var(--f5)] bg-[var(--warn-bg)] p-3 text-sm text-[var(--warn-ink)]"
      id="demoBanner" data-k="demoBanner" role="alert"
    >
      <b>这是演示数据。</b>
      地址里带了 <code>?demo=1</code>，而当前是开发构建，所以这一页显示的是
      <b>本地编出来的示例</b>，不是任何真实行情、也不是后端返回的东西。
      <span className="mt-1 block text-xs">
        {what}　生产构建里这个开关<b>结构上打不开</b>（<code>import.meta.env.DEV</code> 在构建期就成了
        <code>false</code>），所以线上看不到这一屏。
      </span>
    </div>
  )
}

// ── 表格 ──────────────────────────────────────────────────────────────
//
// ★这五页的表格一律用**裸 `<table>`**（照 `Dash.tsx` 的写法），不用 Tremor 的
//   `Table`：Tremor 那套的单元格是 `whitespace-nowrap` + 固定 padding，
//   而这里的表要装"证据串""为什么"这类长文本，塞进去会横向撑爆卡片。
//   裸表配 `overflow-x-auto` 是这一页真正需要的形状。

export function Th({ children, right, className = "" }: {
  children?: React.ReactNode; right?: boolean; className?: string
}) {
  return (
    <th scope="col"
        className={`py-1.5 pr-3 font-medium ${right ? "text-right" : "text-left"} ${className}`}>
      {children}
    </th>
  )
}

export function Td({ children, right, className = "", colSpan, k }: {
  children?: React.ReactNode; right?: boolean; className?: string
  colSpan?: number; k?: string
}) {
  return (
    <td colSpan={colSpan} data-k={k}
        className={`py-1.5 pr-3 align-top ${right ? "text-right tnum" : ""} ${className}`}>
      {children}
    </td>
  )
}

/** 一个数的显示：**`null` 一律显示「—」**，显示 `0` 是把"没拿到"说成"是零"。
 *  本仓库对这件事零容忍（`format.ts` 顶部那段）。 */
export function V({ v, digits = 2, suffix = "", signed = false, nullText = "—" }: {
  v: number | null | undefined; digits?: number; suffix?: string
  signed?: boolean; nullText?: string
}) {
  if (v === null || v === undefined || !Number.isFinite(v)) return <span>{nullText}</span>
  const s = v.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return <span>{signed && v > 0 ? `+${s}` : s}{suffix}</span>
}
