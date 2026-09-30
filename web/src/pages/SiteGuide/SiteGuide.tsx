/**
 * `/siteguide` 引导智能体 —— **一个大聊天界面 + 选项卡**。
 *
 * ★这一页是**主页第一张卡**指向的地方。它的活儿只有一件：回答"这个站有什么、
 *   每一块是怎么算的"。所以页面上的每一个字都得从 `site_map.json` 来 ——
 *   手写一句"本站有九位智能体"就从落笔那天开始烂，而且烂得看不出来
 *   （页面上数字是新的，这句话还是旧的，两者长得一模一样）。
 *
 * ★三层链条，这一页是第三层：
 *
 *     源码 ──build_sitemap.py──▶ site_map.json ──/api/siteguide──▶ 这一页
 *                                              └──/api/siteguide/chat──▶ 大模型
 *
 *   所以这一页**一个业务数字都不许手写**。文案（"点上面的选项卡看"、
 *   "这份目录和源码对不上了"）是界面的话，不是关于这个站的话，那些可以写。
 *
 * ★**聊天那一半刻意复用 `/agent` 的机器**：
 *   · 服务端：`/api/siteguide/chat` 的**出口协议**和 `/api/chat` 一样 ——
 *     正文仍是 `token` 事件。多出来的只是 `guide_*` 那一族（导览团的议事过程）。
 *   · 前端：`MsgView` / `Fmt` / `reducer` / `parseFrame` **全部从 `ChatCard`、
 *     `chat.ts` 直接引**，不抄一份。自己写一份渲染的意思是：
 *     "图表事件被静默丢弃"这类坏法会在两条路上各长一个样，而其中一条没人看。
 *
 * ★**导览团**（`agent/guide/`）那一栏加在 `#sgTeam`：一位位导览员各自把话说完，
 *   主持收口，规则校对位查成稿里的数字有没有出处。这一栏是这个团**唯一**
 *   在屏幕上留下的痕迹 —— 不铺它的话，多智能体和一个智能体在界面上长得一模一样
 *   （用户只看得到最后那段回答），而那正好把设计里最要紧的一件事藏起来了：
 *   **五位不管页面的导览员是当场拒答的**（"这块不归我，我说不了"）。
 *
 * ★`guide_*` 是**未知事件**（对 `chat.ts` 而言）—— `reducer` 的 `applyEvent`
 *   落到最后一个分支原样返回，所以它们是**加法**：`/agent` 那条路一个字节都不变。
 *   这里在 dispatch **之前**把它们截下来自己收（`applyGuide`），不进 reducer。
 *
 * ★选项卡用 `forceMount`（九块面板**全部**在 DOM 里）。两件事都靠它：
 *   ① 探针/判据能数到每一张卡；② 切走再切回来时对话区是**同一个 DOM 节点**
 *   （`st` 在父组件里、卸载不丢，但滚动位置和正在流的那些行会重来一遍）。
 *   ★★**`forceMount` 顺手把 `hidden` 也关掉了**（radix 写的是
 *     `hidden: !(forceMount || isSelected)`）⇒ 藏那件事得自己做，
 *     见下面 `TabsContent` 上那条 `data-[state=inactive]:hidden`。
 *     这是**实测**出来的：`check_siteguide` 那条「恰好一张有几何」量到 `visible=8`，
 *     也就是说九块面板本来是**同时铺在屏幕上**的，"选项卡"这个交互等于不存在。
 *   ★所以"现在哪一块是显示的"这个问题，判据只能靠**数高度 > 0 的块数**
 *     （恰好 1 块）+ **亮着的那盏灯**跟它对上，不能只靠 `classList`/`data-state`
 *     那一类状态位 —— 状态位分不出"显示着"和"被 CSS 压成 0 高"。
 *
 * ★id 前缀一律 `sg*`（`sgTabs`/`sgChat`/`sgMsgs`/`sgQs`/`sgQ`/`sgSend`/
 *   `sgSec-<id>`）。`/agent` 那张卡的探针只读 `#q`/`#send`/`#msgs`，
 *   两边不撞 —— 撞了的话探针量到的会是另一页的元素，而那看起来像"判据通过"。
 */
import * as React from "react"
import { Frame } from "../../agentui/Frame"
import { Degraded, Empty, Loading, Panel, Stat, Well } from "../../agentui/State"
import { Fmt, MsgView } from "../../components/ChatCard/ChatCard"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/Tabs/Tabs"
import { initialState, parseFrame, reducer, type Msg } from "../../chat"
import { DEFAULT_QUICK, type Quick } from "../../quick"
import type { Fail, Res } from "../../api2"
import {
  GUIDE_CHANNEL, GUIDE_CHAT, loadGuide, loadGuideSync, mkFail, sectionsOf,
  type Guide, type Section,
} from "../../siteguide"

// ── 导览团那一栏的状态 ────────────────────────────────────────────────
//
// ★这一栏**只读事件、不猜语义**：谁说了什么、多久、成不成，全部照服务端发来的
//   `guide_*` 字段填。前端一个判断都不做（"他答得对不对""这个数有没有出处"
//   都是服务端算的）—— 前端只要开始自己判，屏幕上就会出现第二份真源。

type Docent = {
  role: string; name: string; icon: string; text: string
  ms: number; ok: boolean; done: boolean; sources: string[]
}
type Team = {
  on: boolean
  rows: Docent[]
  host: { name: string; icon: string; text: string; ms: number; ok: boolean } | null
  audit: { text: string; ok: boolean | null; n: number; unsourced: string[] } | null
}
const EMPTY_TEAM: Team = { on: false, rows: [], host: null, audit: null }

/** 这一条事件归不归导览团。★判前缀，不枚举 —— 枚举的话，服务端新加一种
 *  `guide_x` 就会**静默地**掉进 reducer（也就是被丢掉），而症状是
 *  "有一格永远不亮"，看不出是前端漏了一行。 */
function isGuide(ev: { type?: string }): boolean {
  return String(ev?.type || "").indexOf("guide_") === 0
}

/** 把一个 `guide_*` 事件并进导览团那一栏。★纯函数（`setTeam` 的 updater），
 *  因为事件是在 `await` 的循环里到的，闭包里的 `team` 会是旧的。
 *
 *  ★★**字段一个一个抄，不要 `{...r, ...ev}`。** 第一版就是展开写的，当场踩了两个坑，
 *    两个都只在真浏览器里看得出来：
 *    ① "6 位说完"那个计数一直读 `0/6`，而同一行右边写着"主持已收口" ——
 *       因为 `done` 是我**自己那一栏的**字段，服务端事件里**没有**它
 *       （`guide_docent_done` 只发 `{type, role, ok, ms}`）。展开写法把 ev 里没有的键
 *       当成"没提到"，于是 `done` 永远停在 `false`：屏幕上每一位都顶着"…"，
 *       而流程其实全跑完了。**一个界面在说谎，而页面上没有任何东西是红的。**
 *    ② 展开会把 `type` / `error` 这些事件字段塞进数据行，越攒越脏。
 *    ⇒ 事件是**外部来的**（服务端），它的形状不由这里决定；这里必须自己认字段，
 *      服务端多发一个键不该改变这一栏的含义。 */
function applyGuide(t: Team, ev: Record<string, unknown>): Team {
  const type = String(ev.type)
  const role = String(ev.role || "")
  const say = (f: (r: Docent) => Docent): Team =>
    ({ ...t, rows: t.rows.map((r) => (r.role === role ? f(r) : r)) })
  const name = String(ev.name || role)
  const icon = String(ev.icon || "")
  switch (type) {
    case "guide_start":
      // ★一次新提问就把上一轮清掉：不清的话两轮的导览员会叠在同一栏里，
      //   而"这一轮谁说了话"就没法从屏幕上读出来了。
      return { on: true, rows: [], host: null, audit: null }
    case "guide_docent_start":
      return { ...t, rows: [...t.rows, {
        role, name, icon, text: "", ms: 0, ok: true, done: false, sources: [],
      }] }
    case "guide_say":
      return say((r) => ({ ...r, name, icon,
                           text: String(ev.text || ""),
                           ms: Number(ev.ms || 0),
                           ok: ev.ok !== false,
                           sources: Array.isArray(ev.sources) ? (ev.sources as string[]) : r.sources }))
    case "guide_docent_done":
      // ★`done` 是**这里**说的，不是服务端说的：服务端发这个事件**这件事本身**
      //   就是"这一位说完了"。挂在 ev 的某个字段上的话，字段名一改这一栏就永远"…"。
      return say((r) => ({ ...r, done: true, ok: ev.ok !== false, ms: Number(ev.ms ?? r.ms) }))
    case "guide_host_start":
      return { ...t, host: { name: name === role ? "主持人" : name, icon,
                             text: "", ms: 0, ok: true } }
    case "guide_host":
      return { ...t, host: { name: name === role ? "主持人" : name, icon,
                             text: String(ev.text || ""), ms: Number(ev.ms || 0),
                             ok: ev.ok !== false } }
    case "guide_audit":
      return { ...t, audit: { text: String(ev.text || ""),
                              ok: ev.ok === null || ev.ok === undefined ? null : ev.ok === true,
                              n: Number(ev.n || 0),
                              unsourced: Array.isArray(ev.unsourced) ? (ev.unsourced as string[]) : [] } }
    default:
      return t
  }
}

/** 从 `st.msgs` 拼出这一轮要带的上下文。
 *  ★只送 `me` / `ai` 两种气泡：`err` 那条是**本地造的**（网络错误、400），
 *    它不是对话的一部分，送给服务端等于让大模型去读我们自己的故障信息。
 *  ★空正文的气泡（还没吐字就断了的那条 ai）必须滤掉：带一条 `content:''`
 *    过去，某些服务端会直接判 400。 */
function historyOf(msgs: Msg[]): { role: string; content: string }[] {
  const out: { role: string; content: string }[] = []
  for (const m of msgs) {
    if (m.role === "me") out.push({ role: "user", content: m.body })
    else if (m.role === "ai") out.push({ role: "assistant", content: m.text })
  }
  return out.filter((h) => h.content.trim())
}

/** 「问问看」那一排：选项卡上每一张卡都带几条"点一下就能问"的问题
 *  （`Section.asks`，服务端给的），这里**轮着取**，让四条来自四个不同的话题 ——
 *  顺序取的话四条会全落在第一张卡上，看起来像"只有这一块值得问"。
 *
 *  ★它只是**兜底**：服务端每轮会推一组（`suggest` 事件落在 `st.quick` 上），
 *    推过之后以服务端的为准。判别靠**引用相等**（`st.quick === DEFAULT_QUICK`）：
 *    `applyEvent` 的 `suggest` 分支只在真拿到东西时才换成**新数组**，
 *    空数组会回落成同一个 `DEFAULT_QUICK` 引用 —— 那正是我们要的
 *    （服务端没话说时，用这一页自己的问题）。 */
function guideQuick(secs: Section[]): Quick[] {
  const out: Quick[] = []
  const depth = Math.max(0, ...secs.map((s) => s.asks.length))
  for (let i = 0; i < depth && out.length < 4; i++) {
    for (const s of secs) {
      if (out.length >= 4) break
      const a = s.asks[i]
      if (a) out.push({ label: a, ask: a })
    }
  }
  return out.length ? out : DEFAULT_QUICK
}

/** 选项卡上的一张卡。 */
function SecCard({ s, onAsk }: { s: Section; onAsk: (q: string) => void }) {
  return (
    <div data-k="sgSec" data-sec={s.id}>
      <Panel
        id={`sgSec-${s.id}`}
        title={s.title}
        note={<>{s.kicker}{s.card_id ? <> · 主页卡 <code>{s.card_id}</code></> : null}</>}
        right={s.href ? (
          // ★`data-k="sgGo"` 是探针的抓手：`secHrefs` 量的是**这个 `href` 的值**，
          //   拿它跟 `/api/siteguide` 的 `href` 逐条全等比。少了它，"卡在、按钮在、
          //   而 `href` 被改成 `#`"（用户点不动）在探针眼里完全看不出来。
          <a href={s.href} data-k="sgGo"
             className="rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1 text-xs text-[var(--ink-2)] no-underline">
            去这一页 →
          </a>
        ) : null}
      >
        {/* `intro` 是主页那张卡自己的 `<p>`（反向解析来的）—— 原样显示，
            不润色。润色过的话它就和主页上那句不一样了，而对不上账时
            没人知道该信哪一句。 */}
        {s.intro ? <p className="text-sm leading-relaxed text-[var(--ink-2)]"><Fmt text={s.intro} /></p> : null}

        {s.facts.length ? (
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5" data-k="sgFacts">
            {s.facts.map((f) => (
              <Stat key={f.k} label={f.k} value={f.v} k={`sgFact-${s.id}-${f.k}`} />
            ))}
          </div>
        ) : null}

        {s.bullets.length ? (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm leading-relaxed text-[var(--ink-2)]">
            {s.bullets.map((b, i) => <li key={i}><Fmt text={b} /></li>)}
          </ul>
        ) : null}

        {/* `quote` = 那份**名单原文**（九个角色的名字、十一把工具的名字…）。
            摆出来是为了让人能自己数：上面写着"9 个角色"，这里就得列得出九个。 */}
        {s.quote ? (
          <div className="mt-3">
            <div className="text-[10.5px] text-[var(--ink-3)]">这一栏的名字，原样照抄自源码：</div>
            <Well className="mt-1">
              <code className="text-xs leading-relaxed break-words whitespace-pre-wrap text-[var(--ink-2)]">{s.quote}</code>
            </Well>
          </div>
        ) : null}

        {s.asks.length ? (
          <div className="mt-3 flex flex-wrap gap-2" data-k="sgAsks">
            {s.asks.map((q) => (
              <button key={q} type="button" onClick={() => onAsk(q)}
                      className="rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1 text-xs text-[var(--ink-2)]">
                {q}
              </button>
            ))}
          </div>
        ) : null}
      </Panel>
    </div>
  )
}

export function SiteGuide({ probe = false }: { probe?: boolean }) {
  // ★探针那条路**同步**取（`loadGuideSync`，内部走 `agentui/probe.ts` 的 `callSync`）：
  //   异步回来的数，同步探针量到的是"正在读站点目录…" —— 那不是判据变红，
  //   是判据**消失**（`Research.tsx:159` 顶上那段记着这个病）。
  //   ★它必须**直接落进 `useState` 的初值**，不能写进 `useEffect`：
  //     SSR 与探针的 `flushSync` 那一帧**根本不跑 `useEffect`**。
  const sync = React.useMemo(() => (probe ? loadGuideSync() : null), [probe])
  const [res, setRes] = React.useState<Res<Guide> | null>(sync)
  const [busyData, setBusyData] = React.useState(!sync)

  React.useEffect(() => {
    if (sync) return
    let alive = true
    loadGuide()
      .then((r) => { if (alive) { setRes(r); setBusyData(false) } })
      .catch((e: unknown) => {
        if (!alive) return
        // `call()` 永不抛，走到这里说明是它自己之外的问题（比如渲染期的意外）。
        // 仍然给一个**分类正确**的失败，而不是留一片空白。
        setRes({ ok: false, kind: "offline", reason: "连不上本地服务", detail: String((e as Error)?.message || e) })
        setBusyData(false)
      })
    return () => { alive = false }
  }, [sync])

  const fail: Fail | null = res && !res.ok ? mkFail(res) : null
  const g: Guide | null = res && res.ok ? res.data : null
  const secs = React.useMemo(() => sectionsOf(g), [g])

  const [tab, setTab] = React.useState("chat")
  const [st, dispatch] = React.useReducer(reducer, initialState)
  const [team, setTeam] = React.useState<Team>(EMPTY_TEAM)
  const [q, setQ] = React.useState("")
  const [busy, setBusy] = React.useState(false)
  const boxRef = React.useRef<HTMLDivElement>(null)

  const scroll = React.useCallback((force?: boolean) => {
    const el = boxRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 90
    if (force || atBottom) el.scrollTop = el.scrollHeight
  }, [])

  React.useEffect(() => {
    if (!probe) scroll()
  }, [st, probe, scroll])

  /** 发一问。★`/api/siteguide/chat` **不带 `profile`**：这一页回答的是
   *  "这个站有什么"，个性化画像在这里只是噪声（`app.py` 那边记着）。 */
  async function ask(text: string) {
    if (busy || !text.trim()) return
    setBusy(true)
    // ★历史必须在 `dispatch` **之前**取：dispatch 之后 `st` 会被重新渲染成
    //   新的 state，这一轮的问句就在历史里出现两次了。
    const history = historyOf(st.msgs)
    dispatch({ kind: "user", text })
    dispatch({ kind: "ai" })
    let failed = false
    try {
      const r = await fetch(GUIDE_CHAT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, history }),
      })
      if (!r.ok) {
        dispatch({ kind: "ev", ev: { type: "error", msg: `服务返回 ${r.status}`, code: (await r.text()).slice(0, 200) } })
        failed = true
      } else {
        const rd = r.body!.getReader()
        const dec = new TextDecoder()
        let buf = ""
        for (;;) {
          const x = await rd.read()
          if (x.done) break
          // ★`{stream:true}`：多字节 CJK 跨 chunk 全靠它，去掉会偶发乱码。
          buf += dec.decode(x.value, { stream: true })
          const parts = buf.split("\n\n")
          buf = parts.pop() || ""
          for (const p of parts) {
            const ev = parseFrame(p)
            if (!ev) continue
            if (isGuide(ev)) setTeam((t) => applyGuide(t, ev as unknown as Record<string, unknown>))
            else dispatch({ kind: "ev", ev })
          }
        }
        const tail = parseFrame(buf)
        if (tail) {
          if (isGuide(tail)) setTeam((t) => applyGuide(t, tail as unknown as Record<string, unknown>))
          else dispatch({ kind: "ev", ev: tail })
        }
      }
    } catch (e) {
      dispatch({ kind: "ev", ev: { type: "error", msg: "连不上本地服务", code: String((e as Error)?.message || e) } })
      failed = true
    }
    if (!failed) dispatch({ kind: "finish" })
    setBusy(false)
  }

  /** 从选项卡上点"问这个"：**先切回对话那一格**再发问 ——
   *  不切的话问题发出去了、回答也回来了，而用户还停在介绍卡上，
   *  他看到的只有"按钮亮了一下"。 */
  function askFromTab(text: string) {
    setTab("chat")
    setQ("")
    void ask(text)
  }

  const quick = st.quick === DEFAULT_QUICK && secs.length ? guideQuick(secs) : st.quick

  return (
    <Frame
      probe={probe}
      title="引导智能体"
      sub="这个站有什么、每一块是怎么算的 —— 目录是从源码反向解析出来的，不是我在这儿手写的"
      footExtra={<>这一页上的每一个数字都来自站点自己生成的一份目录，<b>页面上没有第二份</b>。</>}
    >
      {fail ? <Degraded channel={GUIDE_CHANNEL} fail={fail} what="站点目录" /> : null}
      {busyData && !g ? <Loading what="正在读站点目录" /> : null}

      {g ? (
        <Panel id="sgSrc" title="这些话是从哪儿来的"
               note={<>通道 <code className="tnum">{GUIDE_CHANNEL}</code></>}>
          <p className="text-sm leading-relaxed text-[var(--ink-2)]">
            这一页（以及这个引导智能体）<b>没有一句关于本站的介绍是手写的</b>。
            目录是自动读出来的 —— 读的就是站上的页面本身：有哪些页、页头有哪些导航、
            主页上摆了哪几张卡、智能体手上有哪些工具、议会里坐着哪几个角色、
            分钟级行情用了哪几条策略、知识图谱收了哪些词、以及回测跑出来的实测结果。
            全都现读现算，再排成下面这些选项卡。改了页面却没重新生成目录，这一页<b>会自己说出来</b>。
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Stat label="选项卡" value={secs.length} sub="每一张都对应站上的一页或一块" />
            <Stat label="校对的源文件" value={g.freshness?.n ?? "—"} sub="生成时逐个记了 sha256" />
            <Stat label="目录生成于" value={g.freshness?.generated_at || "（没给时间）"} k="sgGen" />
          </div>
          {/* ★**过时是要说出来的**：源码改了而目录没重跑，这一页会继续用旧数字
              回答"站上有几个角色"。判据是**逐文件重算 sha256 现比**，
              不是读那个 `generated_at` —— 后者只管"什么时候生成的"。 */}
          {g.freshness && !g.freshness.ok ? (
            <div className="mt-3 rounded border-2 border-[var(--warn-rule)] bg-[var(--warn-bg)] p-2 text-xs text-[var(--warn-ink)]"
                 id="sgStale" data-k="sgStale" role="alert">
              ⚠ <b>{g.stale_hint || "这份目录与源码已经不一致。"}</b>
              <div className="mt-1 opacity-80">
                {g.freshness.stale.length
                  ? <>有 <b className="tnum">{g.freshness.stale.length}</b> 处页面改动还没收进目录 —— 把目录重新生成一次就会对齐。</>
                  : <>它没说具体是哪些地方改了。</>}
              </div>
            </div>
          ) : null}
        </Panel>
      ) : null}

      {g && !secs.length && !fail ? (
        <Empty what="目录读到了，但一张卡都拼不出来"
               hint={<>通道是通的（<code>{GUIDE_CHANNEL}</code> 回了 200），只是 <code>sections</code> 是空的
                 —— 这和「接口坏了」是两件事。多半是那份目录的字段形状变了，
                 回去把目录重新生成一次看看。</>} />
      ) : null}

      {secs.length ? (
        <Tabs value={tab} onValueChange={setTab} id="sgTabs">
          <TabsList variant="line" id="sgTabsList" aria-label="引导智能体的选项卡">
            <TabsTrigger value="chat" data-k="sgTab" data-sec="chat">对话</TabsTrigger>
            {secs.map((s) => (
              <TabsTrigger key={s.id} value={s.id} data-k="sgTab" data-sec={s.id}>{s.kicker}</TabsTrigger>
            ))}
          </TabsList>

          {/* ★`forceMount`：九块面板全在 DOM 里（理由见文件头）。
              ★★**`forceMount` 会连带把 `hidden` 关掉** —— radix 的
                `TabsContent` 写的是 `hidden: !(forceMount || isSelected)`，
                所以光加 `forceMount` 的话，九块面板**同时有几何**、
                全部铺在屏幕上，而"选项卡"这个交互等于不存在。
                （不是推测：`check_siteguide` 那条「恰好一张有几何」当场量到
                `visible=8`。）
              ⇒ 藏那件事得自己做：用 radix 自己的 `data-state`（它会照常打上
                `active`/`inactive`）在**同一个真源**上判，不另抄一份"现在是哪一格"。
              ★为什么必须**恰好一格**有几何：那是"点一盏灯、换一块内容"这件事
                唯一的几何证据。量 `data-state` 不行 —— 状态位分不出
                "显示着"和"被 CSS 压成 0 高"（本仓库的老规矩）。 */}
          <TabsContent value="chat" forceMount
                       className="mt-3 data-[state=inactive]:hidden">
            <Panel id="sgChat" title="问它"
                   note={<>答话的是<b>导览团</b>：几位各看一块目录、一位收口、一位按目录核对数字出处</>}
                   right={<span className="tag" id="sgEngineTag">多智能体 · 目录切片</span>}>
              {/* 开场那句：数字全部来自目录（几张卡、都叫什么），
                  一个字都不是我写的。没有它，一进来是个空框，看着像这一页没做完。 */}
              {!st.msgs.length ? (
                <div className="msg ai" id="sgWelcome" data-k="sgWelcome">
                  <div className="who">导览团</div>
                  <div className="body">
                    <p>
                      这个站我们从源码里读了一遍，攒了 <b>{secs.length}</b> 张卡：
                      {secs.map((s) => s.kicker).join("、")}。
                    </p>
                    <p>
                      你问一句，几位导览员分头去看各自那块目录，主持把它们的话收成一段回答，
                      再有一位按目录核对成稿里的数字有没有出处。
                      <b>谁不归自己管的，他会当场说"这块不归我"</b>，而不是猜一个。
                    </p>
                    <p>
                      点上面的选项卡看每一块是干什么的（每个数字都标着它是从哪儿来的），
                      或者直接在这儿问 —— 比如下面这几个。
                    </p>
                  </div>
                </div>
              ) : null}

              {/* ★导览团那一栏。铺在这里而不是铺在回答里：它是**过程**，
                  不是答案的一部分。折叠起来默认只占几行；想看他到底说了什么
                  就点开 —— 那几句原话正是主持的唯一素材，也是校对位唯一的锚。 */}
              {team.on ? (
                <div id="sgTeam" data-k="sgTeam"
                     className="mb-3 rounded border border-[var(--rule)] bg-[var(--card)] p-2">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <b>导览团</b>
                    <span className="tag" data-k="sgTeamCount">
                      {team.rows.filter((r) => r.done).length}/{team.rows.length} 位说完
                      {team.host ? "，主持已收口" : ""}
                      {team.audit ? "，校对位已核" : ""}
                    </span>
                    <span className="text-[var(--ink-3)]">
                      各看一块目录，互不越界；主持手上没有数，只能转述
                    </span>
                  </div>
                  <div className="mt-2 grid gap-1">
                    {team.rows.map((r) => (
                      <details key={r.role} data-k="sgDocent" data-role={r.role}
                               data-ok={r.ok ? "1" : "0"} data-done={r.done ? "1" : "0"}>
                        <summary className="cursor-pointer text-xs">
                          <span aria-hidden>{r.icon}</span> {r.name}
                          <span className="text-[var(--ink-3)]">
                            {" · "}{r.done ? (r.ok ? `${r.ms}ms` : "没说出话") : "…"}
                          </span>
                        </summary>
                        <div className="mt-1 border-l-2 border-[var(--rule)] pl-2 text-xs leading-relaxed text-[var(--ink-2)]">
                          {r.text || "（这一位没有说话）"}
                          {r.sources.length ? (
                            <div className="mt-1 text-[var(--ink-3)]">分到的目录：{r.sources.join("、")}</div>
                          ) : null}
                        </div>
                      </details>
                    ))}
                    {team.host ? (
                      <div className="text-xs" data-k="sgHost">
                        <span aria-hidden>{team.host.icon}</span> {team.host.name}
                        <span className="text-[var(--ink-3)]">
                          {" · "}{team.host.ms}ms{team.host.ok ? "" : "（没说出话）"}
                        </span>
                      </div>
                    ) : null}
                    {/* ★校对位是**规则**算出来的（非 LLM）。绿和红必须说成两句
                        不同的话 —— 合成一句含糊的"校对完成"，红绿在读数上就没差别了。 */}
                    {team.audit ? (
                      <div data-k="sgAudit"
                           data-ok={team.audit.ok === null ? "?" : team.audit.ok ? "1" : "0"}
                           className={`rounded border px-2 py-1 text-xs ${team.audit.ok === false
                             ? "border-[var(--warn-rule)] bg-[var(--warn-bg)] text-[var(--warn-ink)]"
                             : "border-[var(--rule)] text-[var(--ink-2)]"}`}>
                        <span aria-hidden>{team.audit.ok === false ? "⚠" : "🔍"}</span>{" "}
                        {team.audit.text}
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}

              <div id="sgMsgs" ref={boxRef} aria-live="polite" aria-busy={busy}
                   className="min-h-[46vh] max-h-[70vh] overflow-y-auto">
                {st.msgs.map((m) => <MsgView m={m} key={m.id} />)}
              </div>

              <div className="cfoot">
                <div className="qs" id="sgQs">
                  {quick.map((it) => (
                    <button key={it.ask} type="button" disabled={busy}
                            onClick={() => askFromTab(it.ask)}>{it.label}</button>
                  ))}
                </div>
                <div className="inrow">
                  <textarea
                    id="sgQ" rows={1} value={q} disabled={busy}
                    aria-label="向引导智能体提问"
                    onChange={(e) => setQ(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                        e.preventDefault()
                        const t = q
                        setQ("")
                        void ask(t)
                      }
                    }}
                    placeholder="问这个站有什么、某一块是怎么算的、某个数是从哪儿来的"
                  />
                  <button id="sgSend" type="button" disabled={busy}
                          onClick={() => { const t = q; setQ(""); void ask(t) }}>
                    发送
                  </button>
                </div>
                <div className="hint">
                  回答里凡是提到"站上有几个角色/几个词条/命中率多少"的，都来自上面那份目录；
                  <b>目录里没有的不许编</b> —— 说完还会有一位<b>不经过大模型</b>，
                  把成稿里每个数字拿去目录里核一遍出处（就在那一栏里写着）。
                </div>
              </div>
            </Panel>
          </TabsContent>

          {secs.map((s) => (
            <TabsContent key={s.id} value={s.id} forceMount
                         className="mt-3 data-[state=inactive]:hidden">
              <SecCard s={s} onAsk={askFromTab} />
            </TabsContent>
          ))}
        </Tabs>
      ) : null}
    </Frame>
  )
}
