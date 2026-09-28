/**
 * `/psych` 投资心理。
 *
 * ★★这一页**全部的措辞都是「你可能正在…」**，没有一句是「你是…」。
 *   差别不是修辞：这个站手上**没有**任何能支撑诊断的数据（它看得见的只有
 *   你在这个站的点击、以及你自己填的画像），而"你是过度自信的"这种句子
 *   在被读到的时候就已经变成结论了。所以：
 *     · 卡片给的是**常见的偏差名字 + 触发条件 + 一个可以自己问自己的问题**；
 *     · 不给你打分、不说你属于哪一种、不说"你应该"；
 *     · 页面上有一句明写的"这不是心理诊断"。
 *
 * ★八个偏差的名单来自行为金融那一支的常见分类（损失厌恶 / 处置效应 /
 *   锚定 / 过度自信 / FOMO / 羊群 / 心理账户 / 近因）。这里的正文是**纯定性**的 ——
 *   **一个数都没有**。写一个"研究表明 80% 的散户…"很容易，而那个数我拿不出来源，
 *   拿不出来源的数在这一页和在行情页一样是编的。
 *
 * ★「心理顾问」那个角色**走的是同一条 SSE 通道**（§3.4 的 `/api/agents`），
 *   只是这一页只要 `role === 'psych'` 的那几条发言。另开一条通道的话，
 *   同一个角色在两个页面上会说出两套口径的话。
 */
import * as React from "react"
import { CHANNEL, streamAgent, type AgentEv, type Fail } from "../../api2"
import { DEMO, DEMO_QUESTION, demoAgentEvents } from "../../agentui/demo"
import { Frame } from "../../agentui/Frame"
import { Degraded, DemoBanner, Empty, Panel, Well } from "../../agentui/State"

type Bias = {
  id: string
  /** 中文名 + 括号里的英文术语（**原样给术语**：想自己去查的人需要这个词）。 */
  name: string
  /** 一句话的"你可能正在…"。**主语是"你"，但句子是疑问/提示，不是结论。** */
  prompt: string
  /** 常见触发条件（什么时候最容易撞上）。 */
  when: string[]
  /** 一个可以自己回答的问题。**这一条是这一页真正的功能**：
   *  诊断给不了，但"问自己一个问题"给得了。 */
  ask: string
}

const BIASES: Bias[] = [
  {
    id: "loss_aversion",
    name: "损失厌恶（loss aversion）",
    prompt: "你可能正在因为「亏了难受、赚了没感觉」而把亏损那一笔一直拖着不看。",
    when: [
      "账户里有一笔跌了很多、你已经很久没打开看它",
      "比起赚 100 块，亏 100 块让你难受得多（同一笔钱，感受不一样大）",
      "你宁愿「先不管它」，也不愿意承认这一笔做错了",
    ],
    ask: "如果我现在是空仓，我会用今天的价格买进它吗？如果答案是不会，那我留着它的理由是什么？",
  },
  {
    id: "disposition",
    name: "处置效应（disposition effect）",
    prompt: "你可能正在「把赚钱的卖了、把亏钱的留着」—— 卖出的是让你舒服的那一笔。",
    when: [
      "赚了一点点就忍不住卖掉落袋为安",
      "亏着的一直不卖，说「等回本就走」",
      "卖出决策比买入决策做得多、也做得快",
    ],
    ask: "把每一笔的「盈利 / 亏损」标签撕掉，只问：按今天的价格，这笔钱放在这儿还是挪到那儿更合理？",
  },
  {
    id: "anchoring",
    name: "锚定（anchoring）",
    prompt: "你可能正在拿「我买的时候是多少钱」当成它值多少钱。",
    when: [
      "你的成本价变成了你判断「贵还是便宜」的基准",
      "盯着「回本就卖」这个具体数字（那个数字跟这家公司现在怎样没有任何关系）",
      "把「最高到过多少」当成它「应该」回到的位置",
    ],
    ask: "如果我不知道自己的成本价，只看到今天这个价格和这家公司现在的情况，我会觉得它贵还是便宜？",
  },
  {
    id: "overconfidence",
    name: "过度自信（overconfidence）",
    prompt: "你可能正在因为「最近猜对了几次」而把仓位加得比计划更大。",
    when: [
      "连赢几笔之后开始觉得「这波我看得懂」",
      "开始做短线、加杠杆，或者把原本分散的钱集中到一只上",
      "把赚钱归因于自己的判断，把亏钱归因于「运气不好 / 被砸了」",
    ],
    ask: "我最近这几笔的决策过程，和我赚钱之前那几笔有区别吗？还是只是结果变好了？",
  },
  {
    id: "fomo",
    name: "错失恐惧（FOMO）",
    prompt: "你可能正在因为「再不买就来不及了」而准备买入 —— 而这个感觉本身就是触发条件。",
    when: [
      "看到它连续涨了几天，越涨越想买",
      "怕的不是亏钱，是「别人赚了我没赚」",
      "买完之后才去找理由（「它有什么利好？」）",
    ],
    ask: "如果它今天跌回三天前那个价格，我还会这么急着买吗？如果不会，我急着买的是它，还是「不想错过」？",
  },
  {
    id: "herding",
    name: "羊群效应（herding）",
    prompt: "你可能正在因为「大家都在买」而觉得这件事更安全 —— 但人多不等于对。",
    when: [
      "你的理由是「群里 / 论坛上都在说」",
      "某只票上热搜、上讨论榜之后你才开始关注",
      "你其实说不太清它赚钱的方式，但知道买它的人很多",
    ],
    ask: "把「有多少人在买」这一条完全去掉，我剩下的理由还够不够支撑这笔买入？",
  },
  {
    id: "mental_accounting",
    name: "心理账户（mental accounting）",
    prompt: "你可能正在把「赚来的钱」和「本金」当成两种不同的钱。",
    when: [
      "觉得自己「用赚的那部分去赌一把没关系」",
      "把某一笔钱单独标记成「给孩子 / 养老」的，就不按同一套标准算它",
      "同一个账户里，不同来源的钱被你用不同的风险标准对待",
    ],
    ask: "如果把这些钱的来源标签全撕掉，合成一笔，我还会这样分配吗？",
  },
  {
    id: "recency",
    name: "近因偏差（recency bias）",
    prompt: "你可能正在拿「最近这一个月」的样子，当成它「一直是这样」。",
    when: [
      "最近涨得好就觉得它「本来就稳」（最近跌得惨就觉得它「一直很烂」）",
      "判断「风险大不大」用的是最近几周的波动，而不是更长的时间",
      "觉得「这次不一样」，而理由只有最近发生的事",
    ],
    ask: "把这个窗口拉长到几年：最近这一段，在它更长的历史里算特殊还是一般？",
  },
]

export function Psych({ probe = false }: { probe?: boolean }) {
  // ★同 `/agents`：演示模式下把那段话预先填好 —— 空着的话按钮是灰的，
  //   点「问一句」没反应，看着像页面坏了。生产里 `DEMO` 恒为 `false`。
  const [text, setText] = React.useState(DEMO ? DEMO_QUESTION : "")
  const [says, setSays] = React.useState<{ text: string; evidence: string[] }[]>(() => {
    if (!(probe && DEMO)) return []
    // ★演示回放走的是**同一个 reducer 的输入形状**（`AgentEv`），只是这一页
    //   把非 psych 的那些滤掉 —— 真实那条路（下面）做的是同一件事。
    return demoAgentEvents(DEMO_QUESTION)
      .filter((e): e is Extract<AgentEv, { type: "agent_say" }> =>
        e.type === "agent_say" && (e as { role?: string }).role === "psych")
      .map((e) => ({ text: String(e.text ?? ""), evidence: (e.evidence ?? []) as string[] }))
  })
  const [busy, setBusy] = React.useState(false)
  const [fail, setFail] = React.useState<Fail | null>(null)
  const ctl = React.useRef<AbortController | null>(null)

  React.useEffect(() => () => { if (ctl.current) ctl.current.abort() }, [])

  const run = async () => {
    if (ctl.current) ctl.current.abort()
    setSays([]); setFail(null); setBusy(true)
    if (DEMO) {
      // ★演示那条路**也走同一个事件表**：不一样的话，"演示好看、真实链路少一块"
      //   这种事就永远看不见（`chat.ts` 顶上那条纪律）。
      const ev = demoAgentEvents(text).filter(
        (e): e is Extract<AgentEv, { type: "agent_say" }> =>
          e.type === "agent_say" && (e as { role?: string }).role === "psych",
      )
      setSays(ev.map((e) => ({ text: String(e.text ?? ""), evidence: (e.evidence ?? []) as string[] })))
      setBusy(false)
      return
    }
    const c = new AbortController()
    ctl.current = c
    const r = await streamAgent({ question: text }, (ev: AgentEv) => {
      // ★只收心理顾问那一条。别的角色的发言在这一页上是噪音 ——
      //   而"这一页只显示 psych"这件事必须写在明面上（下面的注里写了）。
      if (ev.type !== "agent_say") return
      const e = ev as unknown as { role?: unknown; text?: unknown; evidence?: unknown }
      if (String(e.role ?? "") !== "psych") return
      const t = typeof e.text === "string" ? e.text : ""
      const ev2 = Array.isArray(e.evidence) ? (e.evidence as string[]) : []
      setSays((prev) => [...prev, { text: t, evidence: ev2 }])
    }, c.signal)
    ctl.current = null
    setBusy(false)
    if (!r.ok) setFail(r)
  }

  return (
    <Frame
      probe={probe} title="投资心理"
      sub="八个常见的行为偏差 · 说法一律是「你可能正在…」，不是诊断"
      footExtra={<>这一页谈的是<b>你自己的决策习惯</b>，不是市场，也不是心理诊断。它不构成任何建议。</>}
    >
      {DEMO ? <DemoBanner what="这一页显示的是本地编的一段心理顾问发言；卡片上的文字是固定的，不来自任何后端。" /> : null}

      <Panel
        id="whyPanel" title="为什么这一页的措辞是「你可能正在…」"
      >
        <p className="text-sm leading-relaxed text-[var(--ink-2)]">
          这个站手上<b>没有</b>任何能支撑诊断的数据。它看得见的只有你在这个站点了什么、
          以及你自己填的画像 —— 拿这些去说「你是过度自信的」，是给一个结论，
          而结论一旦被读出来，就变不回去了。
        </p>
        <p className="mt-1 text-sm leading-relaxed text-[var(--ink-2)]">
          所以下面每一张卡都是三件事：<b>这个偏差叫什么</b>、<b>它通常在什么时候出现</b>、
          以及<b>一个你可以自己回答的问题</b>。第三件事是这一页真正的功能 ——
          诊断给不了，但问题给得了。
        </p>
        <div className="mt-2 rounded border border-[var(--rule)] bg-[var(--paper)] p-2 text-xs text-[var(--ink-3)]">
          这不是心理诊断，也不是投资建议。任何时候觉得这些内容让你更焦虑，
          就关掉它 —— 一页网页没有资格对你的心理状态下判断。
        </div>
      </Panel>

      <Panel
        id="biasPanel" title="八个常见的偏差"
        note="点开每一张卡看「什么时候最容易撞上」与「可以问自己的一个问题」"
      >
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-4" id="biasGrid" data-k="biasGrid">
          {BIASES.map((b) => (
            <details
              key={b.id} data-k="bias" data-bid={b.id}
              className="rounded border border-[var(--rule)] bg-[var(--card)] p-3 open:border-[var(--accent)]"
            >
              <summary className="cursor-pointer text-sm font-semibold text-[var(--ink)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]">
                {b.name}
              </summary>
              {/* ★这一句是**提示**，不是判断。措辞里每一个字都是刻意的：
                  没有"你属于"，没有"你需要改"，没有程度副词。 */}
              <p className="mt-2 text-sm leading-relaxed text-[var(--ink-2)]" data-k="prompt">{b.prompt}</p>

              <div className="mt-2">
                <div className="text-[10.5px] text-[var(--ink-3)]">什么时候最容易撞上</div>
                <ul className="mt-0.5 list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
                  {b.when.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              </div>

              <div className="mt-2 rounded border border-[var(--accent)] bg-[var(--paper)] p-2">
                <div className="text-[10.5px] text-[var(--ink-3)]">可以问自己的一个问题</div>
                <div className="mt-0.5 text-xs leading-relaxed text-[var(--ink)]" data-k="ask">{b.ask}</div>
              </div>
            </details>
          ))}
        </div>
      </Panel>

      <Panel
        id="askPanel" title="说说你现在的处境"
        note={<>走的是和其它页同一条通道（<code className="tnum">{CHANNEL.agents}</code>），只取「心理顾问」那一角色的发言</>}
      >
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => { e.preventDefault(); if (!busy && text.trim()) void run() }}
        >
          <label htmlFor="psyQ" className="sr-only">说说你现在的处境</label>
          <input
            id="psyQ" value={text} disabled={busy} onChange={(e) => setText(e.target.value)}
            placeholder="例如：我手里有一只亏了 20% 的，一直没卖，最近老想着补仓"
            className="min-w-[240px] flex-1 rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1.5 text-sm text-[var(--ink)] placeholder:text-[var(--ink-3)] disabled:opacity-60"
          />
          <button
            type="submit" disabled={busy || !text.trim()}
            className="rounded border border-[var(--accent)] bg-[var(--card)] px-3 py-1.5 text-sm font-semibold text-[var(--accent)] disabled:opacity-40"
          >
            {busy ? "正在问…" : "问一句"}
          </button>
          <button
            type="button" onClick={() => { if (ctl.current) ctl.current.abort() }} disabled={!busy}
            className="rounded border border-[var(--rule)] bg-[var(--card)] px-3 py-1.5 text-sm text-[var(--ink-2)] disabled:opacity-40"
          >
            停下
          </button>
        </form>

        {fail ? (
          /* ★降级形态：这一页**没有**本地兜底答案。心理这一块最不能做的事就是
             "模型没答上，我编一句像样的安慰话顶上" —— 那种话读起来和真的没区别。 */
          <div className="mt-2">
            <Degraded channel={CHANNEL.agents} fail={fail} what="心理顾问这一段（同一通道，只取 psych 角色）" />
            <p className="mt-1 text-xs text-[var(--ink-3)]">
              心理这一块<b>没有本地兜底答案</b>：模型没答上时，不会有一段写好的安慰话顶上来 ——
              那种话读起来和真的没区别。上面那八张卡是静态内容，它们不依赖这条通道，所以还在这儿。
            </p>
          </div>
        ) : null}

        {says.length ? (
          <ol className="mt-3 space-y-2" id="psychSays" aria-live="polite" data-k="psychSays">
            {says.map((s, i) => (
              <li key={i} className="rounded border border-[var(--rule)] bg-[var(--paper)] p-3">
                <div className="text-[10.5px] text-[var(--ink-3)]">心理顾问</div>
                <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed text-[var(--ink-2)]">
                  {s.text.replace(/\*\*/g, "")}
                </p>
                {s.evidence.length ? (
                  <ul className="mt-1 list-disc pl-5 text-[10.5px] text-[var(--ink-3)]">
                    {s.evidence.map((e, j) => <li key={j}>{e}</li>)}
                  </ul>
                ) : null}
              </li>
            ))}
          </ol>
        ) : !busy && !fail && text ? (
          <div className="mt-3">
            <Empty what="还没问" hint="点上面那个「问一句」。" />
          </div>
        ) : null}
      </Panel>

      <Panel id="notDoPanel" title="这一页不做什么">
        <Well>
          <ul className="list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
            <li><b>不做诊断</b>：不说你属于哪一种偏差，不给你打分，不排序。</li>
            <li><b>不给建议</b>：不说"你应该分散""你应该止损"。这些话每一句都跟你的具体情况有关，
              而这一页不知道你的具体情况。</li>
            <li><b>不引用数字</b>：卡片正文里一个统计数字都没有。写「研究表明 80% 的散户…」很容易，
              而那个数在这里拿不出来源 —— 拿不出来源的数，在这一页和在行情页一样是编的。</li>
            <li>上方那段对话里，页面上只显示 <code>psych</code> 一个角色的发言。
              别的角色也说了话，但那些话属于别的页面。</li>
          </ul>
        </Well>
      </Panel>
    </Frame>
  )
}

export default Psych
