/**
 * `/agents` 多智能体博弈。
 *
 * ★这一页只有**一条**通道：`POST /api/agents` 的 SSE（规格 §3.4）。它不是一个
 *   取数的接口，是一场**正在发生**的辩论 —— 所以这一页的状态机在
 *   `agentui/debate.ts` 里，而不是在这里：`applyEvent()` 既是真实 SSE 每帧的
 *   处理函数，也是演示回放每一步的处理函数。各写一条的话，演示能过的东西
 *   真实链路可能过不了，而"演示"要防的恰恰是这件事（`chat.ts` 顶上那条纪律）。
 *
 * ★降级形态：这条流取不到时**不是**空一屏，是一块写着原因的板子
 *   （`Degraded`）。理由写在 `api2.ts` 顶上：`offline`/`notfound`/`server`
 *   指向三个不同的下一步，混成一句"加载失败"就等于把前两种伪装成第三种。
 *
 * ★探针（`?probe=1`）那条路上**发起不了 SSE**（同步 XHR 做不了流）。
 *   所以探针路径下这一页渲染的是**空棋盘**，或者（dev 下带 `?demo=1` 时）
 *   用 `applyAll()` 一次性折叠出的**终局**。这不是回避判据，是把这件事写在
 *   脸上：`data-k="sseProbeNote"` 那块就是给判据读的。
 */
import * as React from "react"
import { CHANNEL, streamAgent, type Fail } from "../../api2"
import { applyAll, applyEvent, emptyDebate, type Debate } from "../../agentui/debate"
import { DEMO, DEMO_QUESTION, demoAgentEvents } from "../../agentui/demo"
import { Frame } from "../../agentui/Frame"
import { Degraded, DemoBanner, ErrorList, Panel, Well } from "../../agentui/State"
import {
  Progress, RoleGrid, SayStream, StanceDumbbell, TensionTile, VerdictCard, VetoBanner, WeightsPanel,
} from "../../agentui/AgentBoard"

type Phase = "idle" | "running" | "done" | "fail"

export function Agents({ probe = false }: { probe?: boolean }) {
  // ★探针那条路：同步就把终态算出来（`flushSync` 之前渲染的那个 tick 里）。
  //   `applyAll` 走的是**和真实链路同一个 reducer**，所以这一屏的几何
  //   就是真实数据会得到的几何 —— 不是另画一套示意图。
  const probeInit = probe && DEMO ? applyAll(demoAgentEvents(DEMO_QUESTION)) : emptyDebate

  // ★问题在演示模式下**预先填好**（不限于探针那条路）：演示时那个输入框是空的，
  //   而按钮 `disabled={busy || !q.trim()}` ⇒ 打开页面点「开一场」没有任何反应 ——
  //   看的人会以为页面坏了。填一段进去，`?demo=1` 就能直接点开看。
  //   生产里 `DEMO` 恒为 `false`（`import.meta.env.DEV` 构建期被替换），这一行不会生效。
  const [q, setQ] = React.useState(DEMO ? DEMO_QUESTION : "")
  const [d, setD] = React.useState<Debate>(probeInit)
  const [phase, setPhase] = React.useState<Phase>(probe && DEMO ? "done" : "idle")
  const [fail, setFail] = React.useState<Fail | null>(null)
  const [elapsed, setElapsed] = React.useState(0)
  const ctl = React.useRef<AbortController | null>(null)
  const demoTimer = React.useRef<number | null>(null)

  const stop = React.useCallback(() => {
    if (ctl.current) { ctl.current.abort(); ctl.current = null }
    if (demoTimer.current !== null) { window.clearInterval(demoTimer.current); demoTimer.current = null }
  }, [])

  // 卸载时必须停：一条还在跑的 SSE / 一个还在跳的 interval 会在页面切走之后
  // 继续 setState（React 18 不再警告，但那个流会一直占着连接）。
  React.useEffect(() => stop, [stop])

  /** 演示回放：**一个事件一跳**，所以"两轮发言流实时呈现"这件事在演示里也看得见。
   *  ★不一次性 `applyAll`：那样这一页在 dev 下就永远是终局快照，
   *    而"实时呈现"这一条版式需求根本没法自测。 */
  const runDemo = (question: string) => {
    stop()
    setD(emptyDebate); setFail(null); setElapsed(0); setPhase("running")
    const evts = demoAgentEvents(question)
    const t0 = Date.now()
    let i = 0
    demoTimer.current = window.setInterval(() => {
      if (i >= evts.length) {
        if (demoTimer.current !== null) window.clearInterval(demoTimer.current)
        demoTimer.current = null
        setElapsed(Date.now() - t0)
        setPhase("done")
        return
      }
      const ev = evts[i++]
      setD((prev) => applyEvent(prev, ev))
      setElapsed(Date.now() - t0)
    }, 110)
  }

  const runReal = async (question: string) => {
    stop()
    setD(emptyDebate); setFail(null); setElapsed(0); setPhase("running")
    const t0 = Date.now()
    const c = new AbortController()
    ctl.current = c
    const timer = window.setInterval(() => setElapsed(Date.now() - t0), 200)
    const r = await streamAgent(
      { question },
      (ev) => setD((prev) => applyEvent(prev, ev)),
      c.signal,
    )
    window.clearInterval(timer)
    ctl.current = null
    setElapsed(Date.now() - t0)
    if (!r.ok) { setFail(r); setPhase("fail"); return }
    setPhase("done")
  }

  const run = (question: string) => {
    // ★`DEMO` 在**生产构建里是构建期常量 `false`**（`State.tsx` 那段），
    //   所以这条分支在线上永远进不来。
    if (DEMO) runDemo(question)
    else void runReal(question)
  }

  const busy = phase === "running"

  return (
    <Frame
      probe={probe} title="多智能体博弈"
      sub="九个角色各说各的 → 第二轮互相交锋 → 动态权重加权 → 风控官可否决 → 投资经理出裁决"
      footExtra={<>这一页的方向、分数、置信度都是<b>模型与程序算出来的</b>，不是行情，也不是建议。</>}
    >
      {DEMO ? (
        <DemoBanner what="这一页显示的是本地编的一场完整博弈（九人发言、第二轮多空交锋、权重表、风控否决、最终裁决），不是任何模型跑出来的东西。" />
      ) : null}

      <Panel
        id="askPanel" title="问一句"
        note="这一页只接受一个问题；历史与画像由服务端决定要不要用"
      >
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => { e.preventDefault(); if (!busy) run(q.trim()) }}
        >
          <label htmlFor="agentQ" className="sr-only">要问投资经理的问题</label>
          <input
            id="agentQ" name="q" type="text" value={q} disabled={busy}
            onChange={(e) => setQ(e.target.value)}
            placeholder="例如：沪深300 现在这个位置能不能进？"
            className="min-w-[240px] flex-1 rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1.5 text-sm text-[var(--ink)] placeholder:text-[var(--ink-3)] disabled:opacity-60"
          />
          <button
            type="submit" disabled={busy || !q.trim()}
            className="rounded border border-[var(--accent)] bg-[var(--card)] px-3 py-1.5 text-sm font-semibold text-[var(--accent)] disabled:opacity-40"
          >
            {busy ? "进行中…" : "开一场"}
          </button>
          <button
            type="button" onClick={stop} disabled={!busy}
            className="rounded border border-[var(--rule)] bg-[var(--card)] px-3 py-1.5 text-sm text-[var(--ink-2)] disabled:opacity-40"
          >
            停下
          </button>
        </form>
        <p className="mt-2 text-xs text-[var(--ink-3)]">
          通道 <code className="tnum">{CHANNEL.agents}</code>（POST · SSE）。九个角色的发言是
          <b>逐条推过来</b>的，所以这一页会一边说一边长。
        </p>
      </Panel>

      {fail ? (
        <Degraded channel={CHANNEL.agents} fail={fail} what="多智能体博弈" />
      ) : null}
      {d.err ? <ErrorList items={[d.err]} title="这条流报了个错" /> : null}

      {probe && !DEMO ? (
        <div
          className="rounded border border-[var(--rule)] bg-[var(--paper)] p-3 text-sm text-[var(--ink-2)]"
          id="sseProbeNote" data-k="sseProbeNote" role="status"
        >
          <b>探针路径下这一页是空棋盘。</b>
          <div className="mt-1 text-xs text-[var(--ink-3)]">
            这条通道是 SSE（<code>text/event-stream</code>），同步探针那条路上<b>发起不了它</b>
            —— 不是「页面坏了」。要看完整的九个角色卡与两轮交锋，开发时用
            <code>?demo=1</code>（只在 dev 构建生效，见页面顶部的横幅）。
          </div>
        </div>
      ) : null}

      <Panel id="progressPanel" title="进度" note="心跳是「还在跑」与「服务死了」之间唯一的分界线">
        <Progress d={d} busy={busy} elapsed={elapsed} />
      </Panel>

      {d.verdict?.veto ? <VetoBanner veto={d.verdict.veto} /> : null}

      <Panel
        id="verdictPanel" title="最终裁决"
        note="方向 / 分数 / 置信度 / 分歧 —— 分歧那一栏是「一致同意」与「吵成一团」唯一分得开的地方"
      >
        <VerdictCard v={d.verdict} />
      </Panel>

      <Panel
        id="tensionPanel" title="多空交锋"
        note="空心点 = 第一次表态，实心点 = 最后一次；箭头 = 立场挪动的方向"
        right={<div className="w-56"><TensionTile d={d} /></div>}
      >
        <StanceDumbbell d={d} />
      </Panel>

      <Panel id="rolesPanel" title="九个角色" note="「改了立场」= stance 数值真的动了 ≥0.02，不是「说了第二轮」">
        <RoleGrid d={d} />
      </Panel>

      <Panel
        id="weightsPanel" title="动态权重表"
        note="每一行的权重都要有原因 —— 没给原因的那一栏会被显式标出来"
      >
        <WeightsPanel weights={d.weights} />
      </Panel>

      <Panel
        id="streamPanel" title="发言流"
        note="按到达顺序；轮次变了就打一条分隔"
      >
        <SayStream d={d} />
        {!d.says.length && phase === "idle" ? (
          <p className="mt-2 text-xs text-[var(--ink-3)]">
            还没开始。点上面那个「开一场」。
          </p>
        ) : null}
      </Panel>

      <Panel id="notePanel" title="这一页不做什么">
        <Well>
          <ul className="list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
            <li>不预测价格、不给买卖点、不算收益。它只把九个角色的<b>说法与分歧</b>摆出来。</li>
            <li>「立场」这个数是<b>服务端给的</b>（<code>agent_say.stance</code>）。前端不从正文里猜 ——
              猜出来的立场会被当成一个数读，而它其实是一句散文的印象。</li>
            <li>权重表的依据原样显示（<code>agent_weights.basis</code>）。合计不是 1 时<b>照实说</b>，不替它归一化。</li>
            <li>风控官的否决<b>只降级、不推翻</b>：条件与理由分开显示，因为条件是可复核的数值。</li>
          </ul>
        </Well>
      </Panel>
    </Frame>
  )
}

export default Agents
