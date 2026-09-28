/**
 * 多智能体博弈的**展示件**。九个角色卡、多空交锋图、动态权重表、
 * 风控否决横幅、最终裁决卡、两轮发言流。
 *
 * ★这一层是**纯展示**：所有派生量都从 `debate.ts` 来，组件里一个数都不算。
 *   组件里再算一遍的话，"改了立场"这件事就会有两个答案（卡片上一套、
 *   交锋图上一套），而它们看着都对。
 *
 * ★颜色一律走 token。**红涨绿跌**在这一页同样成立：看多 = `text-up`（红）、
 *   看空 = `text-down`（绿）。方向映射只写在 `roles.ts` 的 `stanceTone`/`sideTone`
 *   里，这里不另开一套 —— 一眼就能看反的东西不许有两个出处。
 */
import * as React from "react"
import type { Weights } from "./debate"
import { stanceTrack, tension, weightSum, type Debate, type Say, type Verdict } from "./debate"
import { metaOf, sideLabel, sideTone, stanceTone, RISK_NOTE } from "./roles"
import { Meter } from "./State"

const TONE_CLASS = {
  up: "text-up", down: "text-down", flat: "text-[var(--ink-3)]",
} as const

function fmtStance(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "—"
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}`
}

/** 角色标记。**一个汉字**，不是 emoji（见 `roles.ts` 顶上那段）。 */
export function Glyph({ role }: { role: string }) {
  const m = metaOf(role)
  return (
    <span
      className="inline-flex h-6 w-6 flex-none items-center justify-center rounded border border-[var(--rule)] bg-[var(--paper)] text-xs font-semibold text-[var(--ink-2)]"
      aria-hidden="true" title={m.note}
    >
      {m.glyph}
    </span>
  )
}

// ── 九个角色卡 ────────────────────────────────────────────────────────

function RoleCard({ role, says, speaking, finished }: {
  role: string
  says: Say[]
  speaking: boolean
  finished: boolean | undefined
}) {
  const m = metaOf(role)
  const last = says[says.length - 1]
  const track = stanceTrack(says)
  const tone = stanceTone(last?.stance ?? null)
  // 立场条：把 −1…+1 摊成一条 0…100 的轴，中线是 50（"不表态"）
  const pos = last?.stance === null || last?.stance === undefined ? null : (last.stance + 1) * 50
  return (
    <article
      className={
        "rounded border bg-[var(--card)] p-2.5 " +
        (speaking ? "border-[var(--accent)] shadow-[var(--shadow-1)]" : "border-[var(--rule)]")
      }
      id={`role-${role}`} data-role={role} data-speaking={speaking ? "1" : "0"}
      aria-label={`${m.name}${speaking ? "（正在发言）" : ""}`}
      aria-busy={speaking || undefined}
    >
      <div className="flex items-center gap-2">
        <Glyph role={role} />
        <span className="text-sm font-medium text-[var(--ink)]">{m.name}</span>
        {says.length === 0 && !speaking ? (
          <span className="ml-auto text-[10.5px] text-[var(--ink-3)]">还没轮到他</span>
        ) : null}
        {speaking ? (
          <span className="ml-auto text-[10.5px] font-semibold text-[var(--accent)]" data-k="speaking">发言中…</span>
        ) : null}
        {!speaking && finished === false ? (
          <span className="ml-auto text-[10.5px] text-[var(--f5)]" data-k="failed">这一轮它失败了</span>
        ) : null}
      </div>

      <div className="mt-2 flex items-baseline gap-2">
        <span className={"tnum text-sm " + TONE_CLASS[tone]} data-k="stance">
          {fmtStance(last?.stance ?? null)}
        </span>
        <span className="text-[10.5px] text-[var(--ink-3)]">立场（−1 全空 … +1 全多）</span>
        {track.changed ? (
          <span className="ml-auto rounded border border-[var(--rule)] bg-[var(--paper)] px-1 text-[10px] text-[var(--ink-2)]"
                data-k="changed" title={`第 ${track.firstRound} 轮 ${fmtStance(track.first)} → 第 ${track.lastRound} 轮 ${fmtStance(track.last)}`}>
            改了立场
          </span>
        ) : null}
      </div>

      {/* 立场条：中点是 0，红半段是多、绿半段是空 */}
      <div className="relative mt-1 h-1.5 w-full rounded-full bg-[var(--rule-2)]" aria-hidden="true">
        <div className="absolute inset-y-0 left-1/2 w-px bg-[var(--ink-3)]" />
        {pos !== null ? (
          <div
            className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{ left: `${pos}%`, background: tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : "var(--ink-3)" }}
          />
        ) : null}
      </div>

      <p className="mt-2 line-clamp-4 text-xs leading-relaxed text-[var(--ink-2)]">
        {last ? stripMd(last.text) : "—"}
      </p>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-[var(--ink-3)]">
        <span>置信 <span className="tnum text-[var(--ink-2)]">{fmtStance(last?.conf ?? null)}</span></span>
        {last?.ms !== null && last?.ms !== undefined ? <span className="tnum">{last.ms}ms</span> : null}
        {says.length > 1 ? <span>发言 {says.length} 次</span> : null}
      </div>
      {last?.evidence?.length ? (
        <ul className="mt-1.5 list-disc pl-4 text-[10.5px] text-[var(--ink-3)]">
          {last.evidence.slice(0, 3).map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      ) : null}
    </article>
  )
}

/** 正文里服务端给的是 markdown 片段（`**强调**`）。卡片这一格只给一行摘要，
 *  所以把记号剥掉 —— **不渲染 HTML**（`dangerouslySetInnerHTML` 在这页没有理由）。 */
function stripMd(s: string): string {
  return s.replace(/\*\*/g, "").replace(/`/g, "").trim()
}

export function RoleGrid({ d }: { d: Debate }) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3" id="roleGrid" data-k="roleGrid">
      {d.order.map((role) => (
        <RoleCard
          key={role}
          role={role}
          says={d.byRole[role] ?? []}
          speaking={d.speaking === role}
          finished={d.finished[role]}
        />
      ))}
    </div>
  )
}

// ── 多空交锋图（谁在第 2 轮改了立场）──────────────────────────────────

const AXW = 620
const AXH = 30

/** **这一页的主角**：每个角色一条哑铃 —— 空心点 = 第一次表态，
 *  实心点 = 最后一次表态，中间的箭头指向变化方向。
 *
 *  ★为什么不画折线：角色的数量是固定的九个、每个只有两个点，
 *    画成折线会让人以为"中间还有几个点"。哑铃把"只有两次表态"这件事
 *    画对了。
 *  ★箭头方向**只表示立场往哪边挪**，不表示"变好了/变坏了"。颜色跟着
 *    **挪动后的那一边**（往多看就是红），而不是跟着"涨/跌"这个更大的词。
 *  ★"谁写了反驳"**图上不标**：反驳写在 `text` 里，而这一个字面量之外没有
 *    任何机器可读的信号。前端从正文里猜"这句是不是反驳"就是在编 ——
 *    所以这里只标**数值真的动了**的角色，正文那条流里能读到原话。 */
export function StanceDumbbell({ d }: { d: Debate }) {
  const rows = d.order
    .map((role) => ({ role, t: stanceTrack(d.byRole[role] ?? []) }))
    .filter((r) => r.t.first !== null || r.t.last !== null)
  if (!rows.length) {
    return (
      <p className="text-sm text-[var(--ink-3)]" data-k="noStance">
        还没有任何角色给出<b>数值立场</b>（<code>stance</code>）—— 交锋图要有两个数才画得出来。
      </p>
    )
  }
  const X = (v: number) => ((Math.max(-1, Math.min(1, v)) + 1) / 2) * AXW
  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${AXW} ${AXH * rows.length}`} width="100%"
           style={{ minWidth: 420, maxWidth: AXW }}
           role="img" id="stanceDumbbell" data-k="dumbbell"
           aria-label={`${rows.length} 个角色的立场：空心点为第一次表态，实心点为最后一次表态`}>
        {rows.map((r, i) => {
          const y = i * AXH + AXH / 2
          const a = r.t.first
          const b = r.t.last
          const moved = r.t.changed
          const tone = stanceTone(b)
          const col = tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : "var(--ink-3)"
          return (
            <g key={r.role} data-role={r.role} data-changed={moved ? "1" : "0"}>
              {/* 轴 */}
              <line x1={0} y1={y} x2={AXW} y2={y} stroke="var(--rule-2)" strokeWidth={1} />
              {/* 0 位参考线 */}
              <line x1={X(0)} y1={y - 9} x2={X(0)} y2={y + 9} stroke="var(--ink-3)" strokeWidth={1}
                    strokeDasharray="3 3" />
              {a !== null && b !== null && a !== b ? (
                <>
                  <line x1={X(a)} y1={y} x2={X(b)} y2={y} stroke={col} strokeWidth={4}
                        strokeLinecap="round" />
                  {/* 箭头：画成一个小三角，指到实心点那一侧 */}
                  <polygon
                    points={
                      b > a
                        ? `${X(b)},${y} ${X(b) - 8},${y - 5} ${X(b) - 8},${y + 5}`
                        : `${X(b)},${y} ${X(b) + 8},${y - 5} ${X(b) + 8},${y + 5}`
                    }
                    fill={col}
                  />
                </>
              ) : null}
              {a !== null ? (
                <circle cx={X(a)} cy={y} r={4} fill="var(--card)" stroke="var(--ink-3)" strokeWidth={1.5} />
              ) : null}
              {b !== null ? <circle cx={X(b)} cy={y} r={4.5} fill={col} /> : null}
              <text x={4} y={y - 8} className="k-axis">{metaOf(r.role).name}</text>
            </g>
          )
        })}
      </svg>
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10.5px] text-[var(--ink-3)]">
        <span>左 = 看空（−1）　右 = 看多（+1）　虚线 = 不表态（0）</span>
        <span>空心点 = 第一次表态，实心点 = 最后一次表态，箭头 = 立场挪动的方向</span>
        <span>标「改了立场」= <code>stance</code> 数值真的动了 ≥0.02；只说了第二轮、数值没动的不算</span>
      </div>
    </div>
  )
}

/** 多空净张力。**必须把它怎么算的说出来**，否则一个 −1…+1 的数会被当成信号。 */
export function TensionTile({ d }: { d: Debate }) {
  const t = tension(d)
  const tone = stanceTone(t.n ? t.value : null)
  return (
    <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-2" data-k="tension">
      <div className="text-[10.5px] text-[var(--ink-3)]">本轮多空净张力</div>
      <div className={"tnum text-lg " + TONE_CLASS[tone]}>{t.n ? fmtStance(t.value) : "—"}</div>
      <div className="text-[10.5px] leading-snug text-[var(--ink-3)]">
        {t.n ? `${t.n} 个角色给了数值立场；${t.note}` : t.note}
      </div>
    </div>
  )
}

// ── 动态权重表 ────────────────────────────────────────────────────────

/** 权重的**依据**。`basis` 的形状规格里只说了"要给出数值依据"，没逐字段冻结，
 *  所以这里是**通用渲染**（字符串当句子、数字当读数、对象递归一层、数组逗号连），
 *  而不是按我猜的键名取 —— 猜键名的话，服务端换个写法这一栏就静默变空。
 *  ★一个字段都没给时**要说出来**（规格要求"每个都要在 basis 里给出数值依据"）。*/
function BasisView({ basis }: { basis: Record<string, unknown> }) {
  const keys = Object.keys(basis)
  if (!keys.length) {
    return (
      <p className="text-xs text-[var(--warn-ink)]" data-k="basisEmpty">
        服务端这一轮<b>没有给权重依据</b>。规格要求每个权重都在 <code>basis</code> 里给出数值依据 ——
        没有依据的权重表，页面上就只剩一排数字。
      </p>
    )
  }
  return (
    <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-2">
      {keys.map((k) => (
        <div key={k} data-basis={k}>
          <dt className="font-medium text-[var(--ink-2)]">{k}</dt>
          <dd className="text-[var(--ink-3)]">{renderBasis(basis[k], 0)}</dd>
        </div>
      ))}
    </dl>
  )
}

function renderBasis(v: unknown, depth: number): React.ReactNode {
  if (v === null || v === undefined) return "—"
  // ★字符串也要过一遍 `stripMd`：`basis` 是**给机器看的依据**，服务端在里写
  //   `**强调**` 是很自然的事，而这一段是**原样显示**的（这是它的全部意义）。
  //   不剥的话页面上会出现两个星号 —— 演示那一场里 `self_conf` 就是这么露的。
  if (typeof v === "string") return stripMd(v)
  if (typeof v === "number" || typeof v === "boolean") return <span className="tnum">{String(v)}</span>
  if (Array.isArray(v)) {
    return v.map((x) => (typeof x === "object" ? JSON.stringify(x) : stripMd(String(x)))).join("；")
  }
  if (typeof v === "object") {
    if (depth >= 2) return <code className="break-all">{JSON.stringify(v)}</code>
    return (
      <ul className="mt-0.5 space-y-0.5">
        {Object.entries(v as Record<string, unknown>).map(([k, x]) => (
          <li key={k}>
            <span className="text-[var(--ink-2)]">{k}</span>{" "}
            {renderBasis(x, depth + 1)}
          </li>
        ))}
      </ul>
    )
  }
  return String(v)
}

function WeightBars({ w }: { w: Record<string, number> }) {
  const rows = Object.entries(w).sort((a, b) => b[1] - a[1])
  if (!rows.length) return <p className="text-xs text-[var(--ink-3)]">服务端给的权重表是空的。</p>
  const max = Math.max(...rows.map(([, v]) => v), 0.0001)
  return (
    <ul className="space-y-1" id="weightBars" data-k="weightBars">
      {rows.map(([role, v]) => (
        <li key={role} className="flex items-center gap-2" data-role={role}>
          <span className="w-24 flex-none truncate text-xs text-[var(--ink-2)]" title={metaOf(role).name}>
            {metaOf(role).name}
          </span>
          <span className="w-14 flex-none">
            <Meter v={v} max={max} title={`权重 ${v}`} />
          </span>
          <span className="tnum w-14 flex-none text-right text-xs text-[var(--ink)]" data-k="w">
            {v.toFixed(3)}
          </span>
        </li>
      ))}
    </ul>
  )
}

export function WeightsPanel({ weights }: { weights: Weights | null }) {
  if (!weights) {
    return <p className="text-sm text-[var(--ink-3)]" data-k="noWeights">还没收到权重表（<code>agent_weights</code>）。</p>
  }
  const sum = weightSum(weights.w)
  // ★和不为 1 就**照实说**，不归一化。归一化会把"服务端这块算歪了"这个事实抹掉。
  const off = Math.abs(sum - 1) > 0.02
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(220px,1fr)_2fr]">
      <div>
        <div className="mb-1 text-xs text-[var(--ink-3)]">
          场景判定：<b className="text-[var(--ink-2)]">{weights.scenario || "—"}</b>
          <span className="ml-2">合计 <span className="tnum">{sum.toFixed(3)}</span></span>
        </div>
        {off ? (
          <p className="mb-2 rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] p-1.5 text-[11px] text-[var(--warn-ink)]"
             data-k="weightSumOff">
            权重合计是 <span className="tnum">{sum.toFixed(3)}</span>，不是 1。这里<b>不替它归一化</b> ——
            归一化会把"这一块算歪了"这件事抹掉，而权重表正是要看的东西。
          </p>
        ) : null}
        <WeightBars w={weights.w} />
      </div>
      <div>
        <div className="mb-1 text-xs text-[var(--ink-3)]">每个权重的原因（服务端给的 <code>basis</code>）</div>
        <BasisView basis={weights.basis} />
      </div>
    </div>
  )
}

// ── 风控否决横幅 ──────────────────────────────────────────────────────

export function VetoBanner({ veto }: { veto: Verdict["veto"] }) {
  if (!veto) return null
  return (
    <div
      className="rounded border-2 border-[var(--warn-rule)] bg-[var(--warn-bg)] p-3 text-sm text-[var(--warn-ink)]"
      id="vetoBanner" data-k="veto" role="alert"
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <b>风控官否决</b>
        {veto.by ? <span className="text-xs">（{metaOf(veto.by).name}）</span> : null}
        {veto.downgrade_to ? (
          <span className="rounded border border-[var(--warn-rule)] px-1 text-xs">
            裁决降级为「{sideLabel(veto.downgrade_to)}」
          </span>
        ) : null}
      </div>
      {/* ★条件与理由**分开显示**：前者是可复核的数值条件（规格要求），
          后者是它为什么算命中。合成一句话之后，"条件是什么"就查不到了。 */}
      {veto.rule ? (
        <div className="mt-1 text-xs">触发条件：<code className="break-all">{veto.rule}</code></div>
      ) : null}
      {veto.reason ? <div className="mt-1 text-xs">{veto.reason}</div> : null}
    </div>
  )
}

// ── 最终裁决卡 ────────────────────────────────────────────────────────

export function VerdictCard({ v }: { v: Verdict | null }) {
  if (!v) {
    return <p className="text-sm text-[var(--ink-3)]" data-k="noVerdict">还没有裁决（<code>agent_verdict</code>）。</p>
  }
  const tone = sideTone(v.side)
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4" id="verdictCard" data-k="verdict">
      <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-3">
        <div className="text-[10.5px] text-[var(--ink-3)]">方向</div>
        <div className={"text-lg font-semibold " + TONE_CLASS[tone]} data-k="side">{sideLabel(v.side)}</div>
        {v.veto ? <div className="text-[10.5px] text-[var(--warn-ink)]">已被风控降级</div> : null}
      </div>
      <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-3">
        <div className="text-[10.5px] text-[var(--ink-3)]">分数（−1…+1）</div>
        <div className={"tnum text-lg " + TONE_CLASS[stanceTone(v.score)]} data-k="score">
          {fmtStance(v.score)}
        </div>
      </div>
      <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-3">
        <div className="text-[10.5px] text-[var(--ink-3)]">置信度</div>
        <div className="tnum text-lg text-[var(--ink)]" data-k="confidence">
          {(v.confidence * 100).toFixed(0)}%
        </div>
      </div>
      <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-3">
        <div className="text-[10.5px] text-[var(--ink-3)]">分歧条款</div>
        <div className="tnum text-lg text-[var(--ink)]" data-k="ndissent">{v.dissent.length}</div>
        <div className="text-[10.5px] text-[var(--ink-3)]">一致同意和吵成一团，在方向上可能长得一样</div>
      </div>

      {v.reasons.length ? (
        <div className="sm:col-span-2 lg:col-span-2">
          <div className="text-xs font-semibold text-[var(--ink-2)]">裁决理由</div>
          <ul className="mt-1 list-disc pl-5 text-xs text-[var(--ink-2)]" data-k="reasons">
            {v.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      ) : null}
      {v.dissent.length ? (
        <div className="sm:col-span-2 lg:col-span-2">
          <div className="text-xs font-semibold text-[var(--ink-2)]">谁不同意、不同意在哪</div>
          <ul className="mt-1 list-disc pl-5 text-xs text-[var(--ink-2)]" data-k="dissent">
            {v.dissent.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      ) : null}

      {/* ★风险提示**原样显示服务端给的那句**（规格要求它必须含「不构成投资建议」）。
          服务端没给就用本地那句常量，并在页面上说清是兜底。 */}
      <div className="sm:col-span-2 lg:col-span-4">
        <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-2 text-xs text-[var(--ink-2)]" data-k="riskNote">
          <b>风险提示</b>：{v.risk_note || RISK_NOTE}
          {!v.risk_note ? <span className="text-[var(--ink-3)]">（服务端这一轮没给 <code>risk_note</code>，上面这句是前端兜底）</span> : null}
        </div>
      </div>
    </div>
  )
}

// ── 两轮发言流 ────────────────────────────────────────────────────────

/** 发言流。**按到达顺序**，遇到 `round` 变了就打一条分隔 —— 这就是"两轮"在
 *  页面上唯一的样子。按轮次重排过的话，"实时流"这件事就没了（用户看到的
 *  会是"一下子全出来"）。 */
export function SayStream({ d }: { d: Debate }) {
  if (!d.says.length) {
    return <p className="text-sm text-[var(--ink-3)]" data-k="noSays">还没有发言。</p>
  }
  let lastRound: number | null = null
  return (
    <ol className="space-y-2" id="sayStream" aria-live="polite" aria-busy={d.speaking ? true : undefined}>
      {d.says.map((s, i) => {
        const sep = s.round !== lastRound
        lastRound = s.round
        const m = metaOf(s.role)
        return (
          <React.Fragment key={i}>
            {sep ? (
              <li className="pt-1 text-xs font-semibold text-[var(--ink-3)]" data-k="roundSep" data-round={s.round}>
                ── 第 {s.round} 轮 ──
              </li>
            ) : null}
            <li className="flex gap-2" data-role={s.role} data-round={s.round}>
              <Glyph role={s.role} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-sm font-medium text-[var(--ink)]">{m.name}</span>
                  <span className={"tnum text-xs " + TONE_CLASS[stanceTone(s.stance)]}>
                    {s.stance === null ? "没给数值立场" : `立场 ${fmtStance(s.stance)}`}
                  </span>
                  {s.conf !== null ? (
                    <span className="tnum text-[10.5px] text-[var(--ink-3)]">置信 {fmtStance(s.conf)}</span>
                  ) : null}
                  {s.ms !== null ? <span className="tnum text-[10.5px] text-[var(--ink-3)]">{s.ms}ms</span> : null}
                </div>
                <p className="mt-0.5 whitespace-pre-wrap break-words text-sm leading-relaxed text-[var(--ink-2)]">
                  {stripMd(s.text) || "（空的）"}
                </p>
                {s.evidence.length ? (
                  <ul className="mt-1 list-disc pl-5 text-[10.5px] text-[var(--ink-3)]">
                    {s.evidence.map((e, j) => <li key={j}>{e}</li>)}
                  </ul>
                ) : null}
              </div>
            </li>
          </React.Fragment>
        )
      })}
    </ol>
  )
}

/** 顶部那条进度。**"还在跑"和"服务死了"必须分得开** ——
 *  一条几十秒的流上，两者在页面上都是"没动静"，所以心跳要显示出来。 */
export function Progress({ d, busy, elapsed }: { d: Debate; busy: boolean; elapsed: number }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--ink-3)]" data-k="progress">
      <span>轮次：<b className="text-[var(--ink-2)]">{d.rounds.length ? d.rounds.join(" / ") : "还没开始"}</b></span>
      <span>发言：<b className="tnum text-[var(--ink-2)]">{d.says.length}</b> 条</span>
      <span>
        正在说：<b className="text-[var(--ink-2)]">{d.speaking ? metaOf(d.speaking).name : "—"}</b>
      </span>
      <span>{busy ? `已经跑了 ${(elapsed / 1000).toFixed(1)}s` : d.done ? `用时 ${(d.done.ms / 1000).toFixed(1)}s${d.done.steps !== null ? ` · ${d.done.steps} 步` : ""}` : "未开始"}</span>
      {/* ★不认识的事件类型要露出来：这是"服务端加了新事件、页面还没跟上"的
          唯一可见信号（同 `debate.ts` 顶上那段）。 */}
      {d.unknown ? (
        <span className="rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] px-1 text-[var(--warn-ink)]"
              data-k="unknownEv">
          有 {d.unknown} 个事件这一版前端不认识（已忽略，没丢弃计数）
        </span>
      ) : null}
    </div>
  )
}
