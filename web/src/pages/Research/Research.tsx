/**
 * `/research` 行情特征研判。
 *
 * ★这一页是 `/agent` 上那张「K线预测」卡的**归位**。那张卡原来给一堆"未来 30 根"，
 *   用户看完什么也做不了；这一页把它翻了过来 —— **系统只给材料，判断由玩家自己下**，
 *   然后由游戏揭晓真实结果。闭环是「研判 → 判断 → 揭晓」，不是「系统告诉你答案」。
 *
 * ★★页面的全部纪律压在一句话上：**系统侧一个字的未来涨跌都不许出现。**
 *   落地成四条，每一条都在下面有对应的 DOM：
 *   ① 研判材料只用**截至买入日**的 K 线 —— 服务端截的（`daily_short.analyze` 的
 *      `as_of`），这里不许拿别的接口的数去"补全"。`#cutoff` 那块把
 *      "材料截止到哪一天"印出来，让人自己核。
 *   ② **不显示 `levels`**（入场/止损/目标 1/2）。接口给了，这一页故意不用：
 *      那是"交易决策的输出"，摆在这里玩家会直接抄那个目标价 —— 而抄目标价
 *      就是在读未来，"自己下判断"当场落空。★这是**取舍不是遗漏**。
 *   ③ **先押后揭**：没选之前揭晓按钮是**关着**的。先看结果再补一个判断，
 *      那就不叫判断 —— 而这一页唯一值钱的东西就是那个判断。
 *   ④ 揭晓走的是**另一条**端点（`game.getQuote`），它回答"那天买、到今天赚了多少"。
 *      那是**回看已经发生的事**，不是预测。两者的区别写在那块面板的标题上。
 *
 * ★为什么这一页不复用 `/intraday` 的 `SignalRow` 等内部件：它们没有 `export`
 *   （那五页共享的只有 `agentui/**` 那一层）。这一页共用 `agentui/State` 的状态件，
 *   信号表按自己的需要写一份 —— 抄一份比改别人的文件安全（多会话共用仓库）。
 *
 * ★id 前缀一律 `r*`（`rSym`/`rDate`/`rGo`/`rCall*`/`rReveal`）。`/game` 那一页的
 *   探针选择器只读 `g*`，两边不撞。这一页目前**没有探针判据**（站里的探针只走
 *   `/agent` 和 `/game`），所以这些锚点先是给"人读 DOM"用的。
 */
import * as React from "react"
import { Frame } from "../../agentui/Frame"
import { Degraded, Empty, ErrorList, Loading, Meter, Panel, Stat, Td, Th, Well } from "../../agentui/State"
import { REGIME_LABEL, sideLabel, sideTone } from "../../agentui/roles"
import type { Fail } from "../../api2"
import { dirClass, num, pct, price } from "../../format"
import {
  defaultBuyDate, getQuote, isOk, PROBE_SYM, todayStr,
  type GameErr, type QuoteResp,
} from "../../game"
import {
  getResearch, getResearchSync, RESEARCH_CHANNEL, researchErrText,
  type ResearchResp, type ResearchSignal, type Side,
} from "../../research"

/** 玩家的三选一。
 *  ★`unsure` **不是**服务端的 `flat`（那三个字在服务端的意思是"策略看了材料
 *    之后拒绝给方向"，是**材料的一部分**）。这里是"我不下判断"，是**人的动作**。
 *    两者会同时出现在这一屏上，所以措辞必须分开 —— 混成一个字之后，
 *    "系统说没方向"和"我说不好"就只剩一个说法了。 */
type Call = Side | "unsure"

const CHOICES: [Call, string, string][] = [
  ["long", "看涨", "我觉得买入日之后会往上走"],
  ["short", "看跌", "我觉得买入日之后会往下走"],
  ["unsure", "看不准", "材料不足以让我押一边 —— 这也是一个判断，而且经常是对的"],
]

/** 揭晓用的名义本金。★**只有比例有意义**：服务端回的 `pnl_pct` 与本金无关
 *  （比例），金额只是为了让 `/api/game/quote` 那套（它要一个合法的正数）跑起来。
 *  所以这一页**不显示金额**，只显示比例 —— 显示金额等于暗示"这一页在算仓位"。 */
const NOTIONAL = 10000

/** 这个字段值能安全地当数字显示吗。**服务端的字段一个都不许直接
 *  `.toFixed()` / `num()`**：`num({})` 会走进 `v.toLocaleString is not a function`
 *  把整页打崩，而 `null` 要显示成「—」。照 `Intraday.tsx` 那一层写。 */
function numOf(v: unknown, digits = 2): string {
  return typeof v === "number" ? num(v, digits) : "—"
}

/** 每条信号的强度条。★**中性色**（`--accent`）—— "强度"不是盈亏。 */
function Strength({ v }: { v: number }) {
  return (
    <span className="flex items-center gap-2">
      <span className="w-16 flex-none"><Meter v={v * 100} k="strength" title={`强度 ${v}`} /></span>
      <span className="tnum text-xs text-[var(--ink)]">{numOf(v)}</span>
    </span>
  )
}

/** `refs` → 一行行「键=值」。
 *
 *  ★**必须摊一层**：`refs` 里会出现**嵌套对象**（`atr` 的 `long`/`short`、
 *    `momentum` 的 `weights`）。一句 `String(v)` 会在页面上印出**字面的
 *    `[object Object]`** —— 实测抓到过：`<code>long</code>=[object Object]`。
 *    那是这一页上唯一一处"我自己写坏的"东西，而且它长得像"引擎给了个怪值"，
 *    真正的原因在前端这一行 —— 归因会被引到错的地方。
 *  ⇒ 对象摊成 `long.target2` 这样的点号键。**不隐藏**：这一页的纪律是
 *    "取不到就明说，不拿编的顶替"，同理，拿到了就得原样摆出来。
 */
function refPairs(refs: Record<string, unknown>): [string, string][] {
  const out: [string, string][] = []
  const flat = (k: string, v: unknown) =>
    out.push([k, v === null || v === undefined ? "—" : String(v)])
  for (const [k, v] of Object.entries(refs)) {
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      for (const [k2, v2] of Object.entries(v as Record<string, unknown>)) flat(`${k}.${k2}`, v2)
    } else flat(k, v)
  }
  return out
}

function SignalRow({ s }: { s: ResearchSignal }) {
  const tone = sideTone(s.side)
  return (
    <tr data-k="signal" data-sid={s.id}>
      <Td>
        <span className="text-[var(--ink)]">{s.name}</span>
        <div className="text-[10.5px] text-[var(--ink-3)]"><code>{s.id}</code></div>
      </Td>
      <Td>
        <span className={"font-semibold " + (tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-[var(--ink-3)]")}
              data-k="side">
          {sideLabel(s.side)}
        </span>
      </Td>
      <Td><Strength v={s.strength} /></Td>
      <Td className="min-w-[320px]" k="why">
        {/* ★空的 why **不是空白行**：规格要求每条信号都给可复核的算术，
            没给的时候页面上必须看得出来"这一条后端没做到"。 */}
        {s.why
          ? <span className="text-[var(--ink-2)]">{s.why}</span>
          : <span className="text-[var(--warn-ink)]">这一条<b>没有给可复核的说明</b>（<code>why</code> 是空的）</span>}
        {s.refs && Object.keys(s.refs).length ? (
          <div className="mt-0.5 text-[10.5px] text-[var(--ink-3)]">
            {refPairs(s.refs).map(([k, v]) => (
              <span key={k} className="mr-2">
                <code>{k}</code>=<span className="tnum">{v}</span>
              </span>
            ))}
          </div>
        ) : null}
      </Td>
    </tr>
  )
}

/** `ok:false` 的材料 → `Fail`。三处（探针初值、取数失败、都是同一个形状）共用一份，
 *  免得两处措辞漂开。
 *  ★`detail` 把整份 `errors[]` 也带上：这一支的失败常常是"五条特征里有几条没跑"，
 *    那些原话只在 `errors` 里，`hint` 是空的。 */
function mkFail(r: ResearchResp): Fail {
  return {
    ok: false, kind: "server",
    reason: researchErrText(r),
    detail: r.hint || (r.errors?.length ? r.errors.join("；") : undefined),
  }
}

export function Research({ probe = false }: { probe?: boolean }) {
  // ★探针那条路**同步**取（`research.ts` 里那份私有 `getResearchSync`）：异步回来的
  //   数，同步探针量不到，量到的是"正在取数…" —— 那不是判据变红，是判据**消失**。
  //   ★自己带一份、不用 `agentui/probe.ts` 的 `callSync`：那一份的**成功分支**是坏的
  //     （`ReferenceError: data is not defined`），而它到今天没暴露，恰恰因为
  //     "同步 + 通的真接口"这个组合此前**没有过消费者**。理由写在 `research.ts` 里。
  const initial = React.useMemo(() => {
    if (!probe) return null
    return getResearchSync(PROBE_SYM, defaultBuyDate())
  }, [probe])  // eslint-disable-line react-hooks/exhaustive-deps

  // ★这三行的形状是**探针那条路能不能被量到**的全部 —— 与 `Intraday.tsx:149-151` 同构。
  //   ★★`initial` 必须在这里**直接落进 `useState` 的初值**，不能写进 `useEffect`：
  //      **SSR（以及探针的 `flushSync` 那一帧）根本不跑 `useEffect`**，写在那里等于
  //      整趟渲染都停在"正在取数…"。实测症状是**三档对照渲染出逐字节相同的一份 HTML**
  //      （`ok` / `minute` / `fail` 都是 5743 字节）—— 那正是本条最坏的失效形态：
  //      判据不是变红，是**整批消失**，而报告里长得像"这一页是好的"。
  const [sym, setSym] = React.useState(PROBE_SYM)
  const [date, setDate] = React.useState(defaultBuyDate)
  const [resp, setResp] = React.useState<ResearchResp | null>(() => (initial?.ok ? initial : null))
  const [fail, setFail] = React.useState<Fail | null>(() => (initial && !initial.ok ? mkFail(initial) : null))
  const [busy, setBusy] = React.useState(!initial)
  /** 玩家下的判断。★它**不入库**（第一版只在页面内对比）——
   *  要不要记进台账是下一个决定，不在这一版里。 */
  const [call, setCall] = React.useState<Call | null>(null)
  const [quote, setQuote] = React.useState<QuoteResp | null>(null)
  const [qbusy, setQbusy] = React.useState(false)

  React.useEffect(() => {
    if (!initial) load(sym, date)
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const load = React.useCallback((m: string, d: string) => {
    setBusy(true); setFail(null); setResp(null)
    // ★换一次材料就把上一次的判断和揭晓**一起清掉**。留着的话，玩家会把
    //   A 的判断盖到 B 上，而对比会算出一个**看似成立**的对错 ——
    //   那种错在页面上看不出来（两边都是数），只有"清掉"能从结构上避免。
    setCall(null); setQuote(null)
    void getResearch(m, d)
      .then((r) => {
        if (r.ok) { setResp(r); setFail(null) }
        else { setResp(null); setFail(mkFail(r)) }
        setBusy(false)
      })
      .catch((e: unknown) => {
        setResp(null)
        setFail({ ok: false, kind: "offline", reason: "连不上本地服务", detail: String((e as Error)?.message || e) })
        setBusy(false)
      })
  }, [])

  /** 取一次材料。★日期为空时**不发请求**：`buy_date` 空着传到服务端就是
   *  "到今天为止"，而这一页的揭晓是"那天买、到今天" —— 两个"今天"撞在一起时
   *  收益恒等于 0，玩家会以为"这游戏坏了"。所以这一支要挡住并说清原因。 */
  const run = () => {
    if (!date) return
    load(sym.trim(), date)
  }

  const reveal = () => {
    if (call === null || qbusy) return
    setQbusy(true)
    void getQuote(sym.trim(), NOTIONAL, date)
      .then((r) => setQuote(r))
      .catch((e: unknown) => setQuote({
        summary: { ok: false, error: "NETWORK", msg: String((e as Error)?.message || e) }, data: null,
      }))
      .finally(() => setQbusy(false))
  }

  const P: number[] = React.useMemo(() => {
    const ps = resp?.periods
    return Array.isArray(ps) && ps.length ? ps : [1440]
  }, [resp])
  const daily = resp?.bar_scale === "daily"
  const regime = resp?.regime?.[String(P[0])]
  const signals = resp?.signals ?? []
  const comp = resp?.composite
  const lb = resp?.last_bar
  /** 材料的最后一根 K 落在哪一天。★这一页"没有未来"就靠这一个日期可核验。 */
  const matDate = lb?.date ?? null

  const q = quote && isOk(quote.summary) ? quote.summary : null
  const qerr = quote && !isOk(quote.summary) ? (quote.summary as GameErr) : null
  /** ★**两个日期必须相等**：研判材料截到的那根 K，与揭晓用的实际买入日，
   *  如果不等，那"用截至买入日的材料去比买入日之后的收益"根本不成立 ——
   *  对比会算出一个看着对的结论。这一支必须**显式暴露**，不许悄悄放过。 */
  const dateMismatch = !!(matDate && q?.buy_date && matDate !== q.buy_date)
  /** 对错。★持平（`pnl_pct === 0`）**不算中**：看涨看跌都要求一个方向，
   *  0 是"没动"，两边都不该记一分。 */
  const verdict: "hit" | "miss" | "flat" | "skip" | null =
    call === null || !q ? null
      : call === "unsure" ? "skip"
        : q.pnl_pct === 0 ? "flat"
          : (call === "long") === (q.pnl_pct > 0) ? "hit" : "miss"

  return (
    <Frame
      probe={probe} title="行情特征研判"
      sub="系统只给「截至买入日」的量价材料 · 判断由你自己下 · 然后揭晓真实结果"
      footExtra={<>这一页的<b>系统侧没有任何一天在买入日之后的数据</b>；揭晓那一步是回看已经发生的事。</>}
    >
      <Panel id="pickPanel" title="先选标的和买入日"
             note={<>通道 <code className="tnum">{RESEARCH_CHANNEL}</code></>}>
        <form className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => { e.preventDefault(); run() }}>
          <label className="text-xs text-[var(--ink-3)]">
            <span className="block">标的</span>
            <input id="rSym" type="text" autoComplete="off" spellCheck={false}
                   value={sym} onChange={(e) => setSym(e.target.value)}
                   placeholder="600519 / 510300 / sh000300 / 00700 / AAPL / BTC"
                   className="mt-0.5 w-56 rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1.5 text-sm text-[var(--ink)]" />
          </label>
          <label className="text-xs text-[var(--ink-3)]">
            <span className="block">买入日</span>
            <input id="rDate" type="date" value={date}
                   onChange={(e) => setDate(e.target.value)}
                   className="mt-0.5 rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1.5 text-sm text-[var(--ink)]" />
          </label>
          <button id="rGo" type="submit" disabled={busy || !date}
                  className="rounded border border-[var(--accent)] bg-[var(--card)] px-3 py-1.5 text-sm font-semibold text-[var(--accent)] disabled:opacity-40">
            {busy ? "取数中…" : "取研判材料"}
          </button>
          <button id="rToday" type="button"
                  onClick={() => setDate(todayStr())}
                  className="rounded border border-[var(--rule)] bg-[var(--card)] px-3 py-1.5 text-sm text-[var(--ink-2)]">
            改成今天
          </button>
        </form>
        {!date ? (
          <p className="mt-2 text-xs text-[var(--warn-ink)]" data-k="noDate">
            <b>还没填买入日。</b>这一页要一个具体的日子 —— 研判材料截到那一天为止，
            揭晓算的也是那一天之后的真实走势。留空的话服务端会按"到今天为止"给材料，
            而揭晓也是"到今天"，收益恒等于 0，看着像这一页坏了。
          </p>
        ) : null}
        <p className="mt-2 text-xs text-[var(--ink-3)]">
          填"今天"也可以：那时最后一根 K 可能<b>还没收盘</b>，服务端会如实标出来（下面那块会显示）。
        </p>
      </Panel>

      {fail ? <Degraded channel={RESEARCH_CHANNEL} fail={fail} what="行情特征研判" /> : null}
      {busy && !resp ? <Loading what="正在取研判材料" /> : null}

      {resp ? (
        <>
          {/* ★★**材料截止**这块是这一页的根。没有它，"系统没有未来信息"就只是一句话；
              有了它，用户可以自己核：材料最后一根 K 是哪天、服务端凭什么说它定稿了。 */}
          <Panel id="cutoffPanel" title="这份材料截止到哪一天"
                 note={<>服务端给的 <code>ts</code> = <span className="tnum">{resp.ts ?? "—"}</span></>}>
            <div className="rounded border-2 border-[var(--rule)] bg-[var(--paper)] p-3 text-sm text-[var(--ink-2)]"
                 id="cutoff" data-k="cutoff" role="status">
              <b>最后一根 K 线是 <span className="tnum">{matDate ?? "（服务端没给 last_bar）"}</span>。</b>
              {/* ★服务端自己给的那句理由（"最后一根是 X，早于今天 ⇒ 已收盘定稿"）。
                  照抄不翻译 —— 它是这一屏可核验性的来源，换了措辞就没法对账了。 */}
              {lb?.why ? <div className="mt-1 text-xs text-[var(--ink-3)]">{lb.why}</div> : null}
              <div className="mt-1">
                <b>这一屏里没有任何一天在买入日之后。</b>
                服务端是按"截到买入日为止"取的数（<code>as_of</code>），不是取到今天再往前挑 ——
                两者的区别在<b>周末和节假日</b>上看得最清楚：填一个休市日，材料会落在它之前最近的那个交易日。
              </div>
              {lb?.closed === false ? (
                <div className="mt-1 text-[var(--warn-ink)]" data-k="notClosed">
                  ⚠ <b>这一根还没收盘。</b>
                  量、收盘价、最高最低都还会变 ⇒ 下面基于它算出来的量比、形态都是"<b>半根 K</b>"的读数。
                  <b>这一条不影响"没有未来"</b>（它仍是过去），但它不是定稿。
                </div>
              ) : null}
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="标的" value={<span className="text-sm">{resp.name || resp.code}</span>} sub={<code>{resp.code}</code>} />
              {/* ★标签走 `periodLabel` 那一套：日线口径下唯一那一档是 `1440`，
                  印「1440 分钟体制」会让用户认不出它就是"日线"。 */}
              <Stat label={daily ? "日线体制" : "体制"}
                    value={regime ? (REGIME_LABEL[regime] ?? regime) : "—"}
                    sub="服务端给的 regime，原样翻译" />
              <Stat label="材料根数" value={numOf(resp.bars?.[String(P[0])], 0)} k="nbars"
                    sub="服务端说这一档拿到多少根 K" />
              <Stat label="量比（RVOL）" value={numOf(resp.volume?.rvol)}
                    sub={resp.volume?.rvol === null || resp.volume?.rvol === undefined
                      ? "服务端说 null —— 不是 0" : "服务端给的这一轮量比"} />
              <Stat label="综合方向" value={sideLabel(comp?.side ?? null)} tone={sideTone(comp?.side)} k="compSide"
                    sub="服务端按截至买入日的量价算的读数" />
              <Stat label="综合分" value={numOf(comp?.score, 3)} k="compScore"
                    sub="−1…+1，服务端给的" />
            </div>

            {/* ★★这块是"综合方向**不是**预测"最要紧的一处措辞。
                那个字段名叫 `side`、取值是 long/short，用涨跌色画出来之后
                极其容易被读成"系统说接下来会涨"。所以紧挨着它写清它是什么。 */}
            <div className="mt-3 rounded border border-[var(--rule)] bg-[var(--paper)] p-2 text-xs text-[var(--ink-2)]"
                 data-k="sideMeans">
              <b>上面那个「综合方向」说的是<b>截至 {matDate ?? "买入日"} 那天之前</b>量价偏向哪一侧</b>
              —— 它是**状态的描述**，不是"接下来会涨/跌"。
              <div className="mt-1 text-[var(--ink-3)]">
                服务端算它的方法原样给出来：<code>{comp?.method || "（服务端没给 method）"}</code>
              </div>
              <div className="mt-1">
                票数（服务端给的）：多 <span className="tnum text-up">{comp?.agree?.long ?? "—"}</span>
                {" · "}空 <span className="tnum text-down">{comp?.agree?.short ?? "—"}</span>
                {" · "}不表态 <span className="tnum">{comp?.agree?.flat ?? "—"}</span>
                {" · "}参与的信号 <span className="tnum" data-k="nSignals">{numOf(comp?.n_signals, 0)}</span> 条。
                <b>「不表态」的条数要一起读</b>：五条里四条不表态时，那个分数只是最后一条在说话。
              </div>
            </div>
          </Panel>

          {resp.synthetic === true || /^synth/i.test(String(resp.data_source ?? "")) ? (
            <div className="rounded border-2 border-[var(--warn-rule)] bg-[var(--warn-bg)] p-3 text-sm text-[var(--warn-ink)]"
                 id="synthBanner" data-k="synthBanner" role="alert">
              <b>这一屏的行情是合成的。</b>
              服务端这次给的 <code>data_source</code> 是
              <code className="tnum"> {resp.data_source || "（没给）"}</code> 并且 <code>synthetic=true</code>
              —— 它<b>没拿到真行情</b>，为了把流程跑完退回了一份造出来的帧。
              <span className="mt-1 block">
                下面的每一个价位、每一根成交量都是编的，只说明"这条通道通不通"，<b>不能当行情读</b>。
              </span>
            </div>
          ) : null}

          <Panel id="signalsPanel"
                 title={daily ? "量价特征（日线口径：五条）" : "量价特征"}
                 note={`服务端这一轮给了 ${signals.length} 条 · 每条都带一条能自己动手复核的算术`}>
            {signals.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-[var(--rule)] text-xs text-[var(--ink-3)]">
                      <Th>特征</Th><Th>方向</Th><Th>强度</Th><Th>为什么（可复核的算术）</Th>
                    </tr>
                  </thead>
                  <tbody>{signals.map((s) => <SignalRow key={s.id} s={s} />)}</tbody>
                </table>
              </div>
            ) : (
              <Empty what="这一轮没有特征信号" hint="接口是通的，只是这一轮信号表是空的 —— 这和「接口坏了」是两件事。" />
            )}
            {/* ★★**唯一一处会印出未来价位的行，必须点名。**
                这一段是实测逼出来的，不是补的修辞：`levels` 字段这一页**确实没渲染**
                （`target2` 这个键名在整份 DOM 里 0 命中），可 `levels.target2` 是
                `1423.6733`，而 **ATR 那行的 `why` 里就印着 `1423.67`** —— 同一个数，
                从引擎自己那段"可复核的算术"里上了屏。⇒ 方案里"不渲染 levels
                ⇒ 玩家抄不到目标价"那条**没有成立**。
                处理方式是**点名而不是删**：那一行是引擎的材料，这一页的承诺就是
                "每条都给一条你能自己动手复核的算术"，抹掉它等于既骗人又少给材料。
                而它 long/short **两侧都印**（原话是"镜像"）⇒ 它没有替谁选边，
                与"系统不给方向"并不冲突 —— 冲突的只是**措辞**，所以改的是措辞。 */}
            {signals.some((s) => s.id === "atr") ? (
              <p className="mt-2 text-xs text-[var(--ink-2)]" data-k="atrLevels">
                <b>这一屏只有「ATR 止损目标位」那一行会印出价位</b>（入场/止损/目标1/目标2）。
                那是<b>风险管理的算术</b>，不是看法：它 long、short <b>两侧都印、不替谁选边</b>，
                目标位回答的是"按这个止损做，赔率是多少"，<b>不是"价格会到那"</b>。
                除此之外，这一屏<b>不给未来价位、也不给涨跌方向</b>。
              </p>
            ) : null}
            {/* ★**日线口径**必须说清。港美股/加密在本机没有分钟线源，服务端把口径降到了
                日线，五条"要用当天第几分钟"的策略**没跑** —— 原因逐条在下面的错误清单里。
                不写的话，用户拿这一屏去讲行情时很容易说成"分钟级"。 */}
            {daily ? (
              <p className="mt-2 text-xs text-[var(--ink-2)]" data-k="dailyNote">
                <b>口径是日线，不是分钟级。</b>这个标的在本机没有分钟线源，所以服务端
                自己把口径降到了日线：周期只有一个、
                只跑那五条"只用 K 线序列与量能"的特征。
                <b>这不等于"分钟级那套也能用"</b>，更不许说成分钟线。
              </p>
            ) : null}
            {/* ★**量比在日线上会退化**：服务端按 `tod`（当天第几分钟）分组算"同刻基线"，
                日线全归 00:00 ⇒ 只有一个时段，于是"同刻对比"退化成"前几日的均量"。
                这是 sanctioned 的路径（`daily_short` 一直在跑），但**必须如实标注**，
                不能让用户以为是"同时刻对比"。 */}
            {daily && resp.volume?.rvol_by_period ? (
              <p className="mt-1 text-[10.5px] text-[var(--ink-3)]" data-k="rvolDegrade">
                ⚠ 日线口径下量比的<b>分母是前几个交易日的均量</b>，不是"同一时刻的历史均量"
                （日内量随开盘/尾盘起伏，而日线只有 00:00 一个时段）——
                读它的时候别按分钟级那个口径理解。
              </p>
            ) : null}
          </Panel>

          {resp.errors?.length ? (
            <Panel id="errorsPanel" title="服务端说这几件事没做出来">
              <ErrorList items={resp.errors} title="原样照抄，不翻译" />
              <p className="mt-1 text-[10.5px] text-[var(--ink-3)]">
                这一栏是<b>这一页最值钱的地方之一</b>：少一条特征、缺一档量比，原因都写在这里。
                它空着的时候，"五条特征只有三条"就变成了一个谜。
              </p>
            </Panel>
          ) : null}

          {/* ★★**判断 → 揭晓**。这两块必须挨在一起、而且顺序固定：
              先选中，揭晓按钮才可点。 */}
          <Panel id="callPanel" title="轮到你判断"
                 note="系统到这里就停住了 —— 它只给材料，这一下是你自己的">
            <fieldset disabled={!resp}>
              <legend className="sr-only">你对买入日之后走势的判断</legend>
              <div className="flex flex-wrap gap-2">
                {CHOICES.map(([v, label, why]) => (
                  <label key={v}
                         className={
                           "cursor-pointer rounded border px-3 py-2 text-sm " +
                           (call === v
                             ? "border-[var(--accent)] bg-[var(--card)] font-semibold text-[var(--accent)]"
                             : "border-[var(--rule)] bg-[var(--card)] text-[var(--ink-2)]")
                         }>
                    <input type="radio" name="rcall" value={v} id={`rCall${v}`}
                           checked={call === v}
                           onChange={() => setCall(v)}
                           className="mr-2 align-middle" />
                    <span className="align-middle">{label}</span>
                    <span className="mt-0.5 block pl-6 text-[10.5px] text-[var(--ink-3)]">{why}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            {call === null ? (
              <p className="mt-2 text-xs text-[var(--ink-3)]">
                选一个之后，下面的「揭晓」才会亮 —— <b>先看结果再补一个判断，那就不叫判断了</b>。
              </p>
            ) : null}
          </Panel>

          <Panel id="revealPanel" title="揭晓：那天买、到今天"
                 note="这一步是回看已经发生的事，不是预测">
            {call === null ? (
              <Empty what="先在上面选一个判断"
                     hint="揭晓按钮在你下判断之前是关着的。这不是防呆，是这一页的规矩：判断必须在结果之前。" />
            ) : !quote ? (
              <div>
                <button id="rReveal" type="button" onClick={reveal} disabled={qbusy}
                        className="rounded border border-[var(--accent)] bg-[var(--card)] px-3 py-1.5 text-sm font-semibold text-[var(--accent)] disabled:opacity-40">
                  {qbusy ? "取数中…" : "揭晓真实结果"}
                </button>
                <p className="mt-2 text-xs text-[var(--ink-3)]">
                  你选的是「<b>{CHOICES.find(([v]) => v === call)?.[1]}</b>」。
                  揭晓算的是<b>{date}</b> 那天按真实收盘价买入、持有到今天的结果 ——
                  它只用<b>已经发生</b>的价格。
                </p>
              </div>
            ) : qerr ? (
              <div className="rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] p-3 text-sm text-[var(--warn-ink)]"
                   role="status" data-k="revealFail">
                <b>揭晓这一步取不到数。</b>
                <div className="mt-1">
                  服务端说：{qerr.msg || qerr.error}
                  {qerr.hint ? <span className="block text-xs opacity-80">{qerr.hint}</span> : null}
                </div>
                {/* ★这一句不能省：取不到数与"那天之后没涨也没跌"在画面上都是"没有数字"，
                    而它们是两件完全不同的事。 */}
                <div className="mt-1 text-xs opacity-80">
                  这<b>不是</b>"那天之后没涨没跌" —— 是这一趟没取到，页面上不会拿编的数补上。
                </div>
              </div>
            ) : q ? (
              <>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                  <Stat label="实际买入日" value={q.buy_date} k="qBuyDate"
                        sub={q.date_shifted ? "你填的那天休市，服务端就近取的前一个交易日" : "就是你填的那天"} />
                  <Stat label="买入价" value={price(q.buy_price)} k="qBuy" />
                  <Stat label="最新交易日" value={q.last_date} k="qLastDate" />
                  <Stat label="最新收盘" value={price(q.last_close)} k="qLast" />
                  <Stat label="涨跌" value={pct(q.pnl_pct)} k="qPnlPct"
                        tone={q.pnl_pct > 0 ? "up" : q.pnl_pct < 0 ? "down" : "flat"} />
                  <Stat label="K 线根数" value={num(q.bars, 0)} k="qBars"
                        sub="服务端这一趟拿到多少根" />
                </div>

                {/* ★**判断 vs 结果**。措辞必须站对位置：这是**玩家的**判断被检验，
                    不是"系统预测得准不准"。 */}
                <div className={"mt-3 rounded border-2 p-3 text-sm " + (
                  verdict === "hit" ? "border-[var(--accent)] bg-[var(--paper)] text-[var(--ink)]"
                    : verdict === "miss" ? "border-[var(--rule)] bg-[var(--paper)] text-[var(--ink-2)]"
                      : "border-[var(--rule)] bg-[var(--paper)] text-[var(--ink-2)]")}
                     id="verdict" data-k="verdict" role="status">
                  {verdict === "skip" ? (
                    <><b>你选了「看不准」</b>，这一局不记对错。
                      <div className="mt-1 text-xs text-[var(--ink-3)]">
                        "看不准"本身是一个判断，而且经常是对的 —— 材料不够时它比硬押一边强。
                        这一局的结果是：{pct(q.pnl_pct)}。
                      </div></>
                  ) : verdict === "flat" ? (
                    <><b>你选了「{CHOICES.find(([v]) => v === call)?.[1]}」，而这期间它没涨也没跌</b>（0.00%）。
                      <div className="mt-1 text-xs text-[var(--ink-3)]">持平不算中：看涨和看跌都要求一个方向。</div></>
                  ) : (
                    <><b>你选的是「{CHOICES.find(([v]) => v === call)?.[1]}」，从 {q.buy_date} 到今天
                      是 <span className={"tnum " + dirClass(q.pnl_pct)}>{pct(q.pnl_pct)}</span>
                      —— {verdict === "hit" ? "这一局你猜对了。" : "这一局你猜反了。"}</b>
                      <div className="mt-1 text-xs text-[var(--ink-3)]">
                        区间是 {q.buy_date} 到 {q.last_date}（{num(q.bars, 0)} 根 K），
                        按真实收盘价算，没有复权之外的处理。
                      </div></>
                  )}
                </div>

                {/* ★★两个日期必须相等才谈得上"用买入日之前的材料比买入日之后的结果"。
                    不等时对比**根本不成立** —— 而它算出来的对错看着完全正常。 */}
                {dateMismatch ? (
                  <div className="mt-2 rounded border-2 border-[var(--warn-rule)] bg-[var(--warn-bg)] p-2 text-xs text-[var(--warn-ink)]"
                       id="dateMismatch" role="alert">
                    ⚠ <b>材料截止的那一天，和揭晓用的买入日不是同一天。</b>
                    材料最后一根 K 是 <code className="tnum">{matDate}</code>，
                    而揭晓用的是 <code className="tnum">{q.buy_date}</code>。
                    <b>上面那个对错因此不成立</b>（材料多看或少看了一两天，
                    而这两天里可能就有一次跳空）。这一条是这一页自己的对账，不是服务端的报错。
                  </div>
                ) : null}

                <p className="mt-2 text-[10.5px] leading-snug text-[var(--ink-3)]">
                  ★<b>一局的对错不说明任何事。</b>样本量是 1 —— 押对一次不能说明材料有用，
                  押错一次也不能说明它没用。这个站做过的回测<b>没有证明过任何策略有超额能力</b>
                  （见 README）。要判断这套材料值不值钱，你得记很多局，而且得记下
                  <b>没下判断的那些局</b> —— 只记自己押过的，样本天生是偏的。
                </p>
              </>
            ) : null}
          </Panel>
        </>
      ) : null}

      <Panel id="notePanel" title="这一页不做什么">
        <Well>
          <ul className="list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
            <li><b>系统侧不预测涨跌。</b>上面所有的数都只用<b>截至买入日</b>的 K 线算出来；
              揭晓那一步是你按下按钮之后才发生的，它读的是<b>已经发生</b>的价格。</li>
            <li><b>不显示「入场 / 止损 / 目标价」。</b>接口其实给了这一组数
              （服务端按 ATR 推算的），这一页<b>故意不显示</b>：那是交易决策的输出，
              摆在这里会变成"照着抄"，而这一页要的是你自己看材料。
              {/* ★这句是这一页唯一一处"接口给了但页面不用"的地方 —— 必须说出来，
                  否则下一个人会以为是漏了渲染。 */}
              要看那一组数去 <code>/intraday</code>（那边有，而且写着"是波动率的推算，不是预测"）。</li>
            <li><b>不替你算仓位、不记你的判断。</b>你选的"看涨/看跌/看不准"只活在这一屏里，
              刷新就没了。要不要把它记进台账是<b>下一个决定</b>。</li>
            <li><b>不拿别的数顶替。</b>这一趟取不到就是取不到 —— 上面会写清是哪一种"没有"，
              页面上不会出现一个编出来的价位。</li>
            <li><b>空值不补 0。</b>成交量分布在日线口径下是 <code>null</code>（一天只有一个样本，
              分布退化成单点）—— 那是"没算"，不是"算出来是 0"。</li>
            <li><b>不是投资建议。</b>它是把量价特征摆出来给人看的一份材料，仅此而已。</li>
          </ul>
        </Well>
      </Panel>
    </Frame>
  )
}
