/**
 * 对话卡。SSE 流式 + 工具轨迹 + 图表 + 术语 chip。
 *
 * ★class 名全部沿用旧页面（`.msg` / `.msg.ai` / `.body` / `.trace` / `.tchip` /
 *   `.predmark` / `.who`）：判据数的是 `document.querySelectorAll('.msg')`、
 *   `.trace`、`.tchip` 的**个数**，改名字等于让判据空转。
 *
 * ★`**加粗**` 的渲染在 React 里**不用 innerHTML**：旧版是 esc() 之后拼 `<strong>`，
 *   这里直接把文本切成 React 节点，产出的是**真的 `<strong>` 元素**
 *   （判据 `hasBold` 读的就是 `.msg.ai .body strong`）。不用 dangerouslySetInnerHTML
 *   反而更安全 —— 根本没有"忘了转义"这条路。
 */
import * as React from "react"
import { readProfile } from "../../profile"
import { KChart } from "../KChart/KChart"
import { FundCard } from "../FundCard/FundCard"
import {
  reducer, initialState, parseFrame, traceSummary, demoSeq, demoFundSeq, demoNewsSeq,
  type Act, type Ev, type Msg, type AnyChart, type NewsItem,
} from "../../chat"

/** 按 `kind` 分发。★**判别联合的用处就在这几行**：两种图形状完全不同，
 *  以前是 `as ChartData` 硬塞 —— 分位卡被当 K 线画时不会报错，
 *  只会画出一张空图，那种坏法在页面上看起来"就是没数据"。
 *  ★未知 kind 落到 default：画一句人话，而不是静默什么都不画。
 *    静默正是这个项目里所有坏法的共同点。 */
function Chart({ data }: { data: AnyChart }) {
  if (data.kind === "fundamentals") return <FundCard data={data} />
  if (data.kind === "kline" || data.kind === "predict") return <KChart data={data} />
  return <div className="chart">（这条数据前端不认识，没有画）</div>
}

/* ── 文本渲染：只做 **加粗** + 换行。不引 markdown 库。 ── */
function inline(s: string, keyBase: string): React.ReactNode[] {
  const out: React.ReactNode[] = []
  const re = /\*\*([^*]+)\*\*/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index))
    out.push(<strong key={`${keyBase}-b${i++}`}>{m[1]}</strong>)
    last = m.index + m[0].length
  }
  if (last < s.length) out.push(s.slice(last))
  return out
}

export function Fmt({ text }: { text: string }) {
  // 段落切分是**两个及以上**换行；段内单个 \n 换 <br>（跟旧页面一致）
  const paras = text.split(/\n{2,}/)
  return (
    <>
      {paras.map((p, i) => (
        <p key={i}>
          {p.split("\n").map((line, j) => (
            <React.Fragment key={j}>
              {j > 0 ? <br /> : null}
              {inline(line, `${i}-${j}`)}
            </React.Fragment>
          ))}
        </p>
      ))}
    </>
  )
}

/** ★资讯块：`get_news` 那条数据通道画出来的东西。
 *
 *  三条纪律，每条都对应一次踩过的坑：
 *
 *  1. **刻意不用 `.tchip`**。探针 `out.chips = els(".tchip").length` 数的是
 *     **全文档**的 `.tchip`，而术语 chip 那条判据拿它当计数。资讯标题挂上
 *     `.tchip` 会让那个数**跟着资讯条数变** —— 那是拿判据空转换来的好看，
 *     而且术语那一条会跟着一起失去分辨力。所以这里另起 `.newsitem`。
 *
 *  2. **样式走内联 + 现有 design token**（跟本文件"模型推理中…"那句同一个做法）。
 *     不新开样式表、不写死颜色 ⇒ 亮/暗两套主题自动跟着走。
 *
 *  3. **缺什么说什么**。`url` 为 `null` 是真会发生的（快讯类源没有原文链接）；
 *     那时标题渲染成**不可点的 `<span>`**，而不是套一个 `<a>`/`.tchip` 假装能点
 *     ——（`.tchip` 自带一个 `↗`，套上去就等于对用户说谎）。热度取不到就写"热度没有取到"。
 *     一个都不编。 */
function News({ items }: { items: NewsItem[] }) {
  if (!items.length) return null
  const metaStyle: React.CSSProperties = {
    color: "var(--ink-3)", fontSize: "var(--fs-cap)", marginTop: "2px",
    display: "flex", flexWrap: "wrap", gap: "var(--sp-1)", alignItems: "baseline",
  }
  return (
    <div
      id="newsBox"
      style={{
        marginTop: "var(--sp-2)", display: "flex", flexDirection: "column",
        gap: "var(--sp-2)",
      }}
    >
      {items.map((it, i) => (
        <div
          className="newsitem"
          key={i}
          style={{
            border: "1px solid var(--rule)", borderRadius: "var(--r-sm)",
            padding: "var(--sp-2)", background: "var(--card-2)", minWidth: 0,
          }}
        >
          {it.url ? (
            <a
              className="newstitle"
              href={it.url}
              target="_blank"
              rel="noopener noreferrer"
              title="打开原文（外部站点，不是本站内容）"
              style={{ color: "var(--ink)", fontWeight: 600, textDecoration: "none" }}
            >
              {it.title || "（标题没有取到）"}
            </a>
          ) : (
            <span className="newstitle" style={{ color: "var(--ink)", fontWeight: 600 }}>
              {it.title || "（标题没有取到）"}
            </span>
          )}
          <div className="newsmeta" style={metaStyle}>
            <span className="nsrc">{it.source || "来源未标注"}</span>
            <span className="nsep" aria-hidden="true">·</span>
            {/* ★热度 = **同一事件出现在几个源里**（服务端 `heat`）。它是个计数，
                不是"看涨/看跌"那类判断 —— 这里只把它原样摆出来，不给任何倾向。 */}
            <span className="nheat">
              {typeof it.heat === "number" ? `同题 ${it.heat} 源` : "热度没有取到"}
            </span>
            <span className="nsep" aria-hidden="true">·</span>
            <span className="nts">{it.ts || "时间未标注"}</span>
            {it.url ? null : (
              <>
                <span className="nsep" aria-hidden="true">·</span>
                <span className="nourl">（这条没有原文链接）</span>
              </>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

/** 一条气泡。★**导出**是给 `/siteguide` 那张大对话界面用的（引导智能体的聊天
 *  与这一张走的是同一套事件、同一个 reducer、同一份服务端机器）——
 *  那边自己再写一份渲染的话，"图表事件被静默丢弃"这类坏法就会在两条路上
 *  各长一个样，而其中一条没人看。★导出它**不改这一页的任何行为**：
 *  函数体一字未动，只是多了一个 `export`。 */
export function MsgView({ m }: { m: Msg }) {
  if (m.role === "me") return <div className="msg me">{m.body}</div>
  if (m.role === "err") {
    return (
      <div className="msg ai err">
        <div className="who">出错了</div>
        <div className="body">
          {m.body}
          {m.code ? <><br /><code>{m.code}</code></> : null}
        </div>
      </div>
    )
  }
  return (
    <div className="msg ai">
      <div className="who">
        智能体
        {/* ★为什么非打不可：大模型的话和模型的输出在同一个气泡里、同一套字号、
            同一个颜色，不打个标就分不出哪句是模型采出来的。 */}
        {m.predMarked ? (
          <span className="predmark" title="这条回答里有本地小模型采样出来的K线，不是行情。">
            ⚡ 含小模型预测 · Kronos
          </span>
        ) : null}
      </div>
      <div className="body">
        {m.text ? <Fmt text={m.text} />
          : m.heartbeat != null ? <p style={{ color: "var(--ink-3)" }}>模型推理中… {m.heartbeat} 秒</p>
          : m.body ? <p style={{ color: "var(--ink-3)" }}>{m.body}</p>
          : null}
      </div>
      {m.rows.length ? (
        <details className="trace" open={m.traceOpen}>
          <summary>{traceSummary(m)}</summary>
          <div className="rows">
            {m.rows.map((r, i) => (
              <div className="row" key={i}>
                <span className="nm">{r.nm}</span>
                <span className="ar">{r.ar}</span>
                <span className={r.bad ? "st bad" : "st"}>{r.st}</span>
                {r.extra ? <span className="ar">{r.extra}</span> : null}
              </div>
            ))}
          </div>
        </details>
      ) : null}
      {m.chips.length ? (
        <div className="chips">
          {/* ★graph.html 不认 URL 参数（改它就违反"图谱管线一行不动"），
              所以 chip 只把人送到图谱页，落哪由他自己搜 —— 不做假深链。 */}
          {m.chips.map((t) => (
            <a className="tchip" href="graph.html" key={t} title={`去知识图谱看看「${t}」`}>
              {t}
            </a>
          ))}
        </div>
      ) : null}
      {/* ★资讯在**图之前**：资讯是"采到了什么"，图是"算出了什么" ——
          同一轮里两者都有时，先读事实再读推断。 */}
      {m.news && m.news.length ? <News items={m.news} /> : null}
      {m.chart ? <Chart data={m.chart} /> : null}
    </div>
  )
}

// ★原来这里有一个写死的 `QUICK` 常量 —— 从打开到关掉一个样，跟用户刚问的东西
//   毫无关系（问完可转债，下面还是"茅台最近走势怎么样"）。现在它搬去了 `web/src/quick.ts`
//   当**兜底**，真正的内容由服务端每轮现推、经 `suggest` 事件落在 `st.quick` 上。

/** 演示回放：**同步**跑完，产出初始 state。
 *  ★用 useReducer 的惰性初始化器（第三个参数），不是"渲染期间 dispatch" ——
 *    后者在 React 18 的 StrictMode 下会跑两次，而且是不合法的副作用。 */
function withDemo(s: typeof initialState) {
  let cur = reducer(s, { kind: "ai" })
  for (const ev of demoSeq()) cur = reducer(cur, { kind: "ev", ev })
  // ★第二段**必须另起一条气泡**（`{kind:'ai'}`）：`Msg.chart` 是单槽，
  //   分位卡和 K 线图要同时看得见，塞进同一条会互相顶掉。
  cur = reducer(cur, { kind: "ai" })
  for (const ev of demoFundSeq()) cur = reducer(cur, { kind: "ev", ev })
  // ★第三段：又一条气泡，装**资讯**（`get_news` 那条数据通道）。
  //   单独一条的理由同上面那条——`Msg.chart` 是单槽，而资讯又是一个
  //   **独立的槽**（`Msg.news`）；混进前两条谁顶掉谁说不清，判据也分不出
  //   "资讯没渲染"和"被图表挤掉了"。
  cur = reducer(cur, { kind: "ai" })
  for (const ev of demoNewsSeq()) cur = reducer(cur, { kind: "ev", ev })
  return reducer(cur, { kind: "finish" })
}

export function ChatCard({ probe = false }: { probe?: boolean }) {
  const [st, dispatch] = React.useReducer(
    reducer,
    initialState,
    probe ? withDemo : (s) => s,
  )
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

  async function ask(text: string) {
    if (busy || !text.trim()) return
    setBusy(true)
    dispatch({ kind: "user", text })
    dispatch({ kind: "ai" })
    let failed = false
    // ★画像是**每次提问现读**的，不是在组件挂载时读一次存进 state：
    //   用户刚在 `/profile` 里填完、回到这一页接着问，挂载时读的那份
    //   还是空的 —— 症状是"填了画像但智能体像没看见"，而刷新一下就好了，
    //   于是谁也不会往"读的时机"上想。
    // ★没填过就**不带这个键**（不是带一个 `{}`）：`{}` 和"没填过"在服务端
    //   是两种输入，前者会让 `profile_block` 白白走一遍。
    const prof = readProfile()
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: text,
          history: [],
          ...(prof ? { profile: prof } : {}),
        }),
      })
      if (!res.ok) {
        dispatch({ kind: "ev", ev: { type: "error", msg: `服务返回 ${res.status}`, code: (await res.text()).slice(0, 200) } })
        failed = true
      } else {
        const rd = res.body!.getReader()
        const dec = new TextDecoder()
        let buf = ""
        for (;;) {
          const r = await rd.read()
          if (r.done) break
          // ★{stream:true}：多字节 CJK 跨 chunk 全靠它
          buf += dec.decode(r.value, { stream: true })
          const parts = buf.split("\n\n")
          buf = parts.pop() || ""
          for (const p of parts) {
            const ev = parseFrame(p)
            if (ev) dispatch({ kind: "ev", ev })
          }
        }
        const tail = parseFrame(buf)
        if (tail) dispatch({ kind: "ev", ev: tail })
      }
    } catch (e) {
      dispatch({ kind: "ev", ev: { type: "error", msg: "连不上本地服务", code: String((e as Error)?.message || e) } })
      failed = true
    }
    if (!failed) dispatch({ kind: "finish" })
    setBusy(false)
  }

  return (
    <section className="card chat" id="chatCard" aria-label="智能体对话">
      <div className="chd">
        <h2>问问看</h2>
        <span className="tag" id="engineTag">本地小模型 + 大模型</span>
      </div>
      <div id="msgs" ref={boxRef} aria-live="polite" aria-busy={busy}>
        {st.msgs.map((m) => <MsgView m={m} key={m.id} />)}
      </div>
      <div className="cfoot">
        {/* ★读 `st.quick`（每轮更新），不是常量。两条纪律都在 state 那边保证：
            永不空（后端 `items()` 兜底 + reducer 里空数组再兜一次）、
            只从这一轮真发生过的事里取（来源是服务端 `agent/suggest.py`）。 */}
        <div className="qs" id="qs">
          {st.quick.map((it) => (
            <button key={it.ask} type="button" onClick={() => ask(it.ask)}>{it.label}</button>
          ))}
        </div>
        <div className="inrow">
          <textarea
            id="q" rows={1} value={q} disabled={busy}
            // ★对话的主输入框原来也只有一个 placeholder。它是全站最该有名字的那个框。
            aria-label="向智能体提问"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                const t = q
                setQ("")
                ask(t)
              }
            }}
            placeholder="问概念，或问某只标的的走势（600519 / sh000300 / 00700 / AAPL / BTC）"
          />
          <button id="send" type="button" disabled={busy} onClick={() => { const t = q; setQ(""); ask(t) }}>
            发送
          </button>
        </div>
        <div className="hint">预测要跑本地小模型，几十秒起步；这期间页面会显示推理秒数，别当它卡死了。</div>
      </div>
    </section>
  )
}

export type { Act, Ev }
