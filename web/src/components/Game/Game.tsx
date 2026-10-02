/**
 * 模拟投资游戏（`/game`）。
 *
 * 这一页回答一个问题：**"如果我那时候买了，现在赚多少？"**
 * 两种玩法：A 回看某一天；B 按今天的真实价建仓、以后每次打开按最新价更新。
 *
 * 四条纪律，每条都对应一条判据：
 *
 * 1. **取不到行情就明确报错，绝不编数字。**（用户点名要的）
 *    失败时只渲染 `#gErr` 那段话，`#gNums` 那块**根本不出现** ——
 *    不是"显示 0"，是"没有这块"。一个编出来的 0% 和一个真实的 0%
 *    在页面上长得一模一样，而前者会让玩家以为"我保本了"。
 *
 * 2. **虚拟本金和画像里的资产无关。** `#gStake` 只读 `licai-game-stake`，
 *    **不读** `readProfile().assets` —— 那是"你有多少钱"，这是"你想拿多少练手"。
 *
 * 3. **页面上的数就是接口给的数。** 盈亏、市值、收益率一律是服务端
 *    （`agent/game.py`）算完送过来的，这里只格式化，绝不自己乘一遍。
 *
 * 4. **持仓一行坏掉只坏一行。** 一行取不到就显示那一行的原因，
 *    不许"悄悄少一行"（看着像"我只建了两仓"）。
 */
import * as React from "react"
import { Badge } from "../Badge/Badge"
import { Callout } from "../Callout/Callout"
import { Divider } from "../Divider/Divider"
import {
  Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow,
} from "../Table/Table"
import { readAccBoot, ThemeSwitch } from "../../chrome"
import { readProfile } from "../../profile"
import { dirClass, num, pct, price } from "../../format"
import {
  defaultBuyDate, getQuote, getRec, isOk, loadPositions, newPosition,
  readPositions, readStake, savePositions, saveStake, todayStr,
  type GameErr, type PosRow, type QuoteOk, type QuoteResp, type RecResp,
} from "../../game"

/** ── 持仓表的两个格样式 ──────────────────────────────────────────────
 *
 * ★**颜色一律写在格内的 `<span>` 上，不写在 `<td>` / `<th>` 上。**
 *   两个理由，都是这个仓库踩过的：
 *
 *   ① Tremor 的 `TableCell` 自带 `text-gray-600 dark:text-gray-400`，
 *      `TableHeaderCell` 自带 `text-gray-900 dark:text-gray-50`。
 *      想让盈亏那一格红/绿，若把 `text-up` 写在 `<td>` 上，
 *      **暗色下会被它的 `dark:text-gray-400` 按掉** —— `dark:` 变体在
 *      Tailwind 的输出里排在普通工具类**之后**，同特异性下后者赢。
 *      写进 `<span>` 就没这个问题：span 自己的 `color` 是直接声明，
 *      而 `<td>` 那份只是**继承**下来的，继承永远输给直接声明。
 *
 *   ② 这也让"这一格是什么颜色"不依赖 Tremor 内部那两行是什么。
 *      那几个副本哪天升级换了配色，这里一行都不用动。
 *
 * ★`py-2` 是压 Tremor 那枚 `p-4`：默认一格上下各 16px，一行 60px 高，
 *   三行持仓就把卡片撑得不像表格了。左右两侧不压（`p-4` 给的 16px 留着）——
 *   表格现在住在带内边距的卡身里，再压一次第一列会贴到卡边上。 */
const TH = "py-2 text-xs font-medium text-[var(--ink-3)] dark:text-[var(--ink-3)]"
const TD = "py-2 pr-3"

function Err({ e }: { e: GameErr }) {
  return (
    <div className="gerr" id="gErr">
      <b>没算出来</b>：{e.msg}
      {e.hint ? <div className="hint">{e.hint}</div> : null}
      <div className="hint">这里不会编一个数糊弄你 —— 没取到就是没取到。</div>
    </div>
  )
}

function Nums({ q }: { q: QuoteOk }) {
  return (
    <div id="gNums" className="gnums">
      <div className="grow">
        <span className="gk">买入</span>
        <span className="gv">
          {q.name} <span className="text-[var(--ink-3)]">{q.code}</span>
        </span>
      </div>
      <div className="grow">
        <span className="gk">买入日</span>
        {/* ★用户选的那天和服务端实际用的那天**可能差一两天**（周末 / 节假日）。
            不一致就明说，否则页面上写着"3 月 1 日买入"、算的却是 3 月 3 日的价，
            这一局的数字再也对不上了。 */}
        <span className="gv tnum" id="gBuyDate">
          {q.buy_date}
          {q.date_shifted ? <span className="text-[var(--ink-3)]">（那天不是交易日，按之后第一根 K 线计算）</span> : null}
        </span>
      </div>
      <div className="grow">
        <span className="gk">买入价</span>
        <span className="gv tnum" id="gBuy">{price(q.buy_price)}</span>
      </div>
      <div className="grow">
        <span className="gk">份额</span>
        {/* 虚拟盘不要求整手：要求整手的话，1 万块买 1700 元一股的票一股也买不了。 */}
        <span className="gv tnum" id="gShares">{num(q.shares, 4)}</span>
      </div>
      <div className="grow">
        <span className="gk">最新（{q.last_date}）</span>
        <span className="gv tnum" id="gLast">{price(q.last_close)}</span>
      </div>
      <div className="grow">
        <span className="gk">现在值</span>
        <span className="gv tnum" id="gValue">{num(q.value)}</span>
      </div>
      <div className="grow gbig">
        <span className="gk">盈亏</span>
        {/* ★红涨绿跌：A 股习惯，和欧美相反。颜色只从 token 来。 */}
        <span className={"gv tnum " + dirClass(q.pnl)} id="gPnl">
          {q.pnl >= 0 ? "+" : ""}{num(q.pnl)}
          {/* ★`pct()` 自己带正负号和百分号 —— 这里再补一个 `+` 会出来「(++6.43%)」。 */}
          <span className="gsub" id="gPnlPct">({pct(q.pnl_pct)})</span>
        </span>
      </div>
      <div className="grow">
        <span className="gk">这一局本金</span>
        {/* ★带 id 是为了让判据能拿"页面上写的本金"去回打一次接口 ——
            判据不该自己写死一个 10000（那会把"探针用了多少"变成第三处常量）。 */}
        <span className="gv tnum" id="gAmtShown">{num(q.amount)}</span>
      </div>
      <div className="hint">
        数据来自：{q.source} · 共 {q.bars} 根日 K。
        <b>过去涨不表示以后也会涨</b>，这一页就是让你看看"那会儿买了会怎样"。
      </div>
    </div>
  )
}

type Init = { quote: QuoteResp | null; rec: RecResp | null; rows?: PosRow[] }

export function Game({ probe = false, initial }: { probe?: boolean; initial?: Init }) {
  const [stake, setStake] = React.useState(() => readStake())
  const [sym, setSym] = React.useState("510300")
  const [amt, setAmt] = React.useState("")
  const [date, setDate] = React.useState(() => defaultBuyDate())
  const [res, setRes] = React.useState<QuoteResp | null>(initial?.quote ?? null)
  const [busy, setBusy] = React.useState(false)
  const [rec, setRec] = React.useState<RecResp | null>(initial?.rec ?? null)
  // ★持仓的初值也要能**同步**给进来（探针那条路）：`initial.rows` 有值时下面那个
  //   effect 不跑，这一帧上就摆着"两仓里坏了一仓"的画面 —— 那正是要量的东西。
  const [rows, setRows] = React.useState<PosRow[]>(initial?.rows ?? [])
  /** 页面上真正显示的那份持仓：探针给了就照探针给的，否则用取回来的。 */
  const shown = initial?.rows ?? rows
  const [loadingPos, setLoadingPos] = React.useState(false)
  const accHtml = React.useMemo(readAccBoot, [])

  const refresh = React.useCallback(() => {
    const list = readPositions()
    if (!list.length) { setRows([]); return }
    setLoadingPos(true)
    loadPositions(list)
      .then((r) => { setRows(r); setLoadingPos(false) })
      .catch(() => setLoadingPos(false))
  }, [])

  React.useEffect(() => {
    if (probe || initial?.rows) return
    refresh()
  }, [probe, initial, refresh])

  // 按兴趣推荐。★兴趣只是**推荐**，输入框照样能搜任意标的。
  React.useEffect(() => {
    if (probe || initial?.rec) return
    const p = readProfile()
    getRec(p?.interests ?? []).then(setRec).catch(() => {})
  }, [probe, initial])

  /** 这一局要用的金额：填了就用填的，没填就用本金。
   *  ★注意这里**只**回落到本金 —— 绝不回落到画像里的资产。 */
  const useAmt = () => (amt.trim() ? amt.trim() : stake.trim())

  async function look() {
    if (busy) return
    setBusy(true)
    try {
      setRes(await getQuote(sym, useAmt(), date))
    } catch (e) {
      setRes({ summary: { ok: false, error: "NETWORK", msg: String((e as Error)?.message || e),
                          hint: "连不上本地服务" }, data: null })
    }
    setBusy(false)
  }

  async function hold() {
    if (busy) return
    setBusy(true)
    try {
      // ★建仓这一枪**必须是真报价**：报价失败就**不加这一仓**。
      //   先把一笔算不出价的仓记进持仓表，等于把"编数字"从这一页挪到了下一页。
      const r = await getQuote(sym, useAmt(), todayStr())
      setRes(r)
      if (isOk(r.summary)) {
        const q = r.summary
        const next = [...readPositions(),
                      newPosition(q.code, q.name, q.amount, q.buy_date)]
        savePositions(next)
        refresh()
      }
    } catch (e) {
      setRes({ summary: { ok: false, error: "NETWORK", msg: String((e as Error)?.message || e),
                          hint: "连不上本地服务" }, data: null })
    }
    setBusy(false)
  }

  function del(id: string) {
    savePositions(readPositions().filter((p) => p.id !== id))
    refresh()
  }

  // 探针模式那份持仓由 `main.tsx` 同步取好传进来（见 game.ts 的 `loadPositionsSync`）。
  const okRows = shown.filter((r) => r.q)
  const totPnl = okRows.reduce((s, r) => s + (r.q!.pnl || 0), 0)
  const totAmt = okRows.reduce((s, r) => s + (r.q!.amount || 0), 0)
  const items = rec?.data?.items ?? []

  return (
    <div className="center-large-container min-h-screen bg-[var(--paper)]">
      <div className="center-large-card">
        <div className="min-h-screen bg-[var(--paper)]">
          <header className="sticky top-0 z-30 border-b border-[var(--rule)] bg-[var(--card)]">
            <div className="bar mx-auto flex max-w-[clamp(1180px,78vw,2200px)] flex-wrap items-center gap-2.5 px-5 py-2.5">
              <h1 className="text-lg font-semibold text-[var(--ink)]">
                <a href="/" className="grad-title no-underline">乡e家</a>
                模拟练手
                <span className="ml-2 text-xs font-normal text-[var(--ink-3)]">假钱练手，行情是真的</span>
              </h1>
              <Badge variant="neutral">钱是假的，行情是真的</Badge>
              <div className="ml-auto">
                <ThemeSwitch />
              </div>
            </div>
          </header>

          <main className="mx-auto grid max-w-[clamp(1180px,78vw,2200px)] grid-cols-1 items-start gap-4 p-5">
        {/* ★准备区两列（≥1024px）：左「拿多少练手」、右「不知道买什么」。
            原来五张卡一路竖着排，其中"本金"那张整张卡里只有一个输入框，
            在 1400px 的窗口里横着铺 1140px —— 卡里九成是空白。
            这是"卡片网格"最典型的偷懒长相：每张卡都一样、大小和内容无关。
            ★DOM 顺序 == 视觉顺序（左→右、上→下），键盘 Tab 走下来是对的。
            ★只在外层加一个 `div`，`<main>` 仍是 `grid-cols-1`，
              这个 div 只是它的一个格子。 */}
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="card" id="stakeCard" aria-label="虚拟本金">
            <div className="chd">
              <h2>这一局拿多少练手</h2>
            </div>
            <div className="px-4 pt-3 pb-4">
              <div className="inrow">
                <input
                  id="gStake" type="text" inputMode="decimal" autoComplete="off"
                  // ★外层 `<section>` 上那个 `aria-label="虚拟本金"` 命名的是**区块**，
                  //   命名不到这个输入框。同一页 `#gSym`/`#gAmt`/`#gDate` 是用
                  //   `<label>` 包起来的（对的），这里漏了。
                  aria-label="这一局的虚拟本金"
                  value={stake}
                  onChange={(e) => { setStake(e.target.value); saveStake(e.target.value) }}
                  placeholder="比如 10000"
                />
              </div>
              <div className="hint">
                这是<b>虚拟</b>本金，赚赔都不影响你的真钱。
                {/* ★这句话必须写出来：用户会以为这里该填画像里那个"可投资资产"。
                    两者没有任何关系 —— 那个是"你有多少钱"，这个是"你想拿多少练手"。 */}
                它和你画像里填的情况<b>没有任何关系</b>，也不会自动帮你填上 ——
                想练多少就填多少，填错了改掉就是。
              </div>
            </div>
          </section>

          <section className="card" id="recCard" aria-label="按兴趣挑几只">
            <div className="chd flex-wrap">
              <h2>不知道买什么？</h2>
              <span className="text-xs text-[var(--ink-3)]">
                按你画像里填的偏好挑的
              </span>
            </div>
            <div className="px-4 pt-3 pb-4">
              <div className="chips" id="recBox" aria-busy={!items.length}>
                {items.map((it) => (
                  <button key={it.symbol} type="button" className="tchip" data-rec={it.symbol}
                          title={it.why} onClick={() => setSym(it.symbol)}>
                    {it.name}
                    <span className="text-[var(--ink-3)]">{it.symbol}</span>
                  </button>
                ))}
                {!items.length ? <span className="hint">{rec?.summary?.note || "正在加载…"}</span> : null}
              </div>
              {items.length ? <div className="hint">{rec?.summary?.note}</div> : null}
              {rec?.data?.unmapped?.length ? (
                <div className="hint">
                  {/* ★取不到的类别要**说出来**，不许拿别的类别硬凑一只：
                      给一个选了"存款"的人推一只股票，等于告诉他"存款就是这个"。 */}
                  {rec.data.unmapped.map((u) => (
                    <div key={u.interest}>⊘ {u.interest}：{u.why}</div>
                  ))}
                </div>
              ) : null}
            </div>
          </section>
        </div>

        <section className="card" id="lookCard" aria-label="回看某一天">
          <div className="chd flex-wrap">
            <h2>玩法 A · 如果那天买了</h2>
            <span className="text-xs text-[var(--ink-3)]">选一天，用那天的真实收盘价买入，算到今天</span>
          </div>
          <div className="px-4 pt-3 pb-4">
            <div className="gform">
              <label className="gf">
                <span>品种</span>
                <input id="gSym" type="text" autoComplete="off" spellCheck={false}
                       value={sym} onChange={(e) => setSym(e.target.value)}
                       onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); look() } }}
                       placeholder="600519 / 510300 / sh000300 / 00700 / AAPL / BTC" />
              </label>
              <label className="gf">
                <span>金额</span>
                <input id="gAmt" type="text" inputMode="decimal" autoComplete="off"
                       value={amt} onChange={(e) => setAmt(e.target.value)}
                       placeholder={stake.trim() || "这一局的本金"} />
              </label>
              <label className="gf">
                <span>买入日</span>
                <input id="gDate" type="date" value={date}
                       onChange={(e) => setDate(e.target.value)} />
              </label>
              <button id="gGo" type="button" disabled={busy} onClick={look}>算算看</button>
              <button id="gHold" type="button" disabled={busy} onClick={hold}>按今天真实价买入</button>
            </div>
            <div id="gOut" aria-live="polite" aria-busy={busy}>
              {res ? (isOk(res.summary) ? <Nums q={res.summary} /> : <Err e={res.summary} />) : null}
            </div>
          </div>
        </section>

        {/* ★★入口卡：**行情特征研判**（2026-09-22 加）。
            它和上面那张「玩法 A」是同一件事的两半 —— A 是"事后算算你赚了多少"，
            这一张是"事前看材料、自己押一边，再回来揭晓"。所以两张挨着放。
            ★**整张卡是一个 `<a>`**（`card linkcard`，与主页那几张入口卡同款）：
              它不是这一页的一个玩法面板，而是"去另一页"的入口 —— 用面板样式
              （`.chd` + 内部按钮）会让人以为点了会在原地展开。
            ★href 是**服务端真有的路由**（`app.py` 的 `research_page()`）：刷新、
              直接粘链接、被搜索引擎抓，三种情况都落在同一页上，不是靠客户端跳过去。
            ★文案的**分寸**是这张卡唯一难的地方：它一个字都不能说"系统会告诉你涨跌"。
              用户原来对那张旧「K线预测」卡的要求就是**不要输出未来涨跌结果** ——
              这张卡上写的必须就是那一页真做的事（只给截至买入日的材料）。
            ★★那句「一个字的未来都不给」**被实测推翻过一次，已改**：那一页上
              「ATR 止损目标位」那行的 `why` 里带着 入场/止损/目标1/目标2 ——
              是引擎自己的风险算术，long/short 两侧都印、不替谁选边，但它**是价位**。
              ⇒ 卡片上承诺"没有未来"就是假话。现在改说**方向**（那才是用户的原话：
              "不要输出未来涨跌结果"），价位那件事交给 `/research` 页上那条点名说明。
              教训：卡片的承诺要能被那一页**真的验**，不能凭"我设计成没有"就写上。
            ★id 用 `research*` 前缀，**不用 `g` 前缀**：`probe.ts` 里模拟投资页那批
              选择器一律读 `g*`（`#gStake`/`#gNums`/`#gOut`…），混进来不是会红，
              是纯命名噪音 —— 下一个人会以为这张卡也在那批判据的覆盖里。 */}
        <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-6 no-underline"
           href="/research" id="researchCard">
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <span className="kicker">量价分析</span>
              <h2 className="mt-1 text-lg font-semibold text-[var(--ink)]">先看材料，自己押一边</h2>
              <p className="mt-1 text-sm text-[var(--ink-2)]">
                系统只给<b>截至买入日</b>的量价特征（趋势、成交量、背离、挤压、波动），
                <b>一个字都不告诉你明天会涨会跌</b> —— 你自己判断，判完才揭晓真实结果。
                每条指标都附了<b>你能自己动手验证的算法</b>。
              </p>
            </div>
            <span className="kicker shrink-0 whitespace-nowrap" aria-hidden="true">进去 →</span>
          </div>
        </a>

        <section className="card" id="holdCard" aria-label="我的持仓">
          <div className="chd flex-wrap">
            <h2>玩法 B · 我的持仓</h2>
            <span className="text-xs text-[var(--ink-3)]">
              每次打开都按最新真实价更新；一行取不到只坏一行
            </span>
            {/* ★`ml-auto`：合计是这个卡片的**结论**，跟标题挤在一起就成了又一句说明。
                推到最右边，眼睛扫完表格落到它身上。 */}
            {okRows.length ? (
              <Badge variant="neutral" className="ml-auto">
                合计 <span className={"tnum " + dirClass(totPnl)} id="gTot">{totPnl >= 0 ? "+" : ""}{num(totPnl)}</span>
                {" / "}{num(totAmt)}
              </Badge>
            ) : null}
          </div>
          {/* ★表格换成了 Tremor 的 `Table`（原来是一张手写的 `<table>`）。
              **判据的落点一个没动**：`#gPos` 还是那个 id、`tbody tr[data-sym]`
              还是每笔持仓一行（坏掉的那行也**照旧占一行**并写明原因）、
              坏行的文案里仍然有"没取到"。
              ★没用 `TableRoot` 那层滚动壳：它把 `whitespace-nowrap` 写死在里面，
                坏掉的那一行 `colSpan=7` 的文字是"标的 + 原因"，不许换行就会
                把整张卡顶出横向滚动条 —— 为了用而用，坏处是实的。
              ★横向滚动壳仍旧手写：这张表 7 列，窄屏需要能拖。
              ★边框颜色不在这里的类名上改，统一放 `index.css` 的 `#gPos` 那一段 ——
                Tremor 自带的那两档灰是**固定色阶**，暗色那一端离背景太近。 */}
          <div className="overflow-x-auto px-4 pt-3 pb-4">
            <Table id="gPos">
              <TableHead>
                <TableRow>
                  <TableHeaderCell className={TH}>品种</TableHeaderCell>
                  <TableHeaderCell className={TH + " text-right"}>本金</TableHeaderCell>
                  <TableHeaderCell className={TH + " text-right"}>买入价</TableHeaderCell>
                  <TableHeaderCell className={TH + " text-right"}>现价</TableHeaderCell>
                  <TableHeaderCell className={TH + " text-right"}>现在值</TableHeaderCell>
                  <TableHeaderCell className={TH + " text-right"}>盈亏</TableHeaderCell>
                  <TableHeaderCell className={TH} />
                </TableRow>
              </TableHead>
              <TableBody>
                {shown.map((r) => (
                  <TableRow key={r.p.id} className="g-row" data-sym={r.p.symbol}>
                    {r.q ? (
                      <>
                        <TableCell className={TD}>
                          <span className="font-medium text-[var(--ink)]">{r.q.name}</span>
                          <span className="ml-1.5 text-xs text-[var(--ink-3)]">{r.q.code} · {r.q.buy_date}</span>
                        </TableCell>
                        <TableCell className={TD + " text-right"}>
                          <span className="tnum text-[var(--ink)]">{num(r.q.amount)}</span>
                        </TableCell>
                        <TableCell className={TD + " text-right"}>
                          <span className="tnum text-[var(--ink-2)]">{price(r.q.buy_price)}</span>
                        </TableCell>
                        <TableCell className={TD + " text-right"}>
                          <span className="tnum text-[var(--ink-2)]">{price(r.q.last_close)}</span>
                        </TableCell>
                        <TableCell className={TD + " text-right"}>
                          <span className="tnum text-[var(--ink)]">{num(r.q.value)}</span>
                        </TableCell>
                        <TableCell className={TD + " text-right"}>
                          <span className={"tnum font-semibold " + dirClass(r.q.pnl)}>
                            {r.q.pnl >= 0 ? "+" : ""}{num(r.q.pnl)}
                            <span className="gsub">({pct(r.q.pnl_pct)})</span>
                          </span>
                        </TableCell>
                        <TableCell className={TD + " text-right"}>
                          <button type="button" className="gdel" onClick={() => del(r.p.id)}>删除</button>
                        </TableCell>
                      </>
                    ) : (
                      // ★坏掉的行也占一行并写明原因：少一行看起来像"我只建了两仓"
                      <TableCell className={TD + " text-xs text-[var(--f5)]"} colSpan={7}>
                        {r.p.symbol} 这一行没读到：{r.err}
                        <button type="button" className="gdel ml-2" onClick={() => del(r.p.id)}>删掉</button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
                {loadingPos ? (
                  <TableRow>
                    <TableCell className={TD + " text-xs text-[var(--ink-3)]"} colSpan={7}>正在获取最新价…</TableCell>
                  </TableRow>
                ) : null}
                {!shown.length && !loadingPos ? (
                  <TableRow>
                    <TableCell className={TD + " text-xs text-[var(--ink-3)]"} colSpan={7}>
                      还没买过。上面选一个、点「按今天真实价买入」，它就会存在你浏览器里。
                    </TableCell>
                  </TableRow>
                ) : null}
              </TableBody>
            </Table>
          </div>
        </section>

        <Divider />

        <section className="card p-4" aria-label="风险提示">
          <Callout variant="warning" title="钱是假的，教训是真的">
            行情来自公开接口，可能会有延迟，不同数据源之间也可能差一点点；
            <b>涨跌颜色按 A 股习惯（红涨绿跌）</b>。持仓只存在你自己的浏览器里，
            换个浏览器就没了。
          </Callout>
          <div className="disclaim mt-3" id="disclaim">
            <b>风险提示</b>：这里的盈亏都按历史行情计算，<b>过去涨不表示以后也会涨</b>；
            所有内容<b>仅供学习参考</b>。
            <div className="acc-line" dangerouslySetInnerHTML={{ __html: accHtml }} />
          </div>
        </section>
      </main>
    </div>
  </div>
</div>
  )
}
