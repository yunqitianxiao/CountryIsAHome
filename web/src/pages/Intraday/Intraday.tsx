/**
 * `/intraday` 分钟级策略。
 *
 * ★这一页回答的是"**这一分钟，量价在说什么**"，所以它的主体是两件事：
 *   ① 一张**四层共用一根横轴**的盘中图（蜡烛 / 成交量 / VWAP / 价值区）。
 *      四层分开画的话，"放量的那一根"和"突破的那一根"会指在不同的 x 上，
 *      而四张图在截图上看不出这件事（`agentui/charts.tsx` 那段）。
 *   ② 一张**十条策略信号**的表，每一行都必须有一条**可复核的算术**
 *      （`why`）。空的 `why` 是**要显示出来的缺陷**，不是一行空白 ——
 *      规格 §3.2 明写每个 why 必须可复核，页面有责任把"它没做到"露出来。
 *
 * ★降级形态有**两层**，别混：
 *   ① 通道坏了：`/api/intraday` 拿不到 ⇒ 一块写着原因的板子（`Degraded`）。
 *   ② **日线口径**（`bar_scale === 'daily'`）：港美股/加密在本机**没有分钟线源**，
 *      服务端把这一页降级成**日线**，周期只剩一个（`periods` = `[1440]`，
 *      见 `agent/daily_short.py`）。那一刻周期表 / tab / 各档量比**必须跟着
 *      服务端的 `periods` 走** —— 硬编码 5/15/30 的话，页面会画出一排
 *      **永远空着的格子**，而用户看不出那是因为走了日线（他会以为接口坏了）。
 *      所以这里有一条横幅明说口径。
 *   ★两层都**不许拿别的接口的数顶替**：顶替出来的图看着更"完整"，
 *     而它回答的不是同一个问题。
 *
 * ★5 / 15 / 30 的切换**换的是"看哪一档"**（体制、根数、量比、把哪一档的信号
 *   提到前面），**不是**前端重采样出另一档 K 线。重采样出来的 15 分钟线
 *   和真的长得一样，而它是编的（`api2.ts` 里 `klines` 那段）。
 *   ★日线口径下这一档只有一个，所以下面凡是"三档"的字都得跟着 `periods` 走。
 */
import * as React from "react"
import { CHANNEL, PERIODS, getIntraday, type Fail, type IntradayResp, type IntradaySignal } from "../../api2"
import { CandlePanel, HBars } from "../../agentui/charts"
import { DEMO, demoIntraday } from "../../agentui/demo"
import { Frame } from "../../agentui/Frame"
import { callSync } from "../../agentui/probe"
import { Degraded, DemoBanner, ErrorList, Empty, Loading, Meter, Panel, Stat, Th, Td, Well } from "../../agentui/State"
import { REGIME_LABEL, sideLabel, sideTone } from "../../agentui/roles"
import { num } from "../../format"

/** 市场选择器。
 *  ★`cn` 在服务端 `ALIASES` 里**无条件**落成 `cn_stock`（股票），而数据源那一层是
 *    分得开的：`cn_stock` / `cn_index` / `cn_etf`。原来这里只有 `cn`，于是
 *    **指数在这页上无法表达** —— 而这一页的默认标的（`sh000300`）恰恰就是指数。
 *    后果是静默的：手输 `000905`（中证500）时服务端拿 `sz000905` 去取，
 *    回的是**厦门港务**的行情、不报错（`sz000905` 是真实存在的深市个股，
 *    所以"取不到就换读法"那条兜底也不会触发）。
 *    ⇒ 把数据源真的分得开的三档摆出来，让用户自己说清楚要哪个 ——
 *      不在前端复制"哪个前缀配哪个市场"那份知识（它在 `sina.scodes` 里）。 */
const MARKETS: [string, string][] = [
  ["cn", "A股"], ["cn_index", "指数"], ["cn_etf", "ETF"],
  ["hk", "港股"], ["us", "美股"], ["crypto", "加密"],
]

/** 演示数据里那两个带下划线的内部字段 → 契约里预留的那两个位置。
 *  ★只做**改名**，不做换算：演示的 `_bars` 就是那些蜡烛，`_vwap` 就是那条均价线。 */
function withBars(r: IntradayResp): IntradayResp {
  const d = r as IntradayResp & { _bars?: IntradayResp["klines"]; _vwap?: (number | null)[] }
  return { ...r, klines: r.klines ?? d._bars ?? null, vwap_series: r.vwap_series ?? d._vwap ?? null }
}

/** 后端 `agent/daily_short.py` 里 `DAILY_PERIOD` 的**同一个值**（分钟数）。
 *  ★前端**不靠这个常量决定走哪条路** —— 那是服务端的 `periods` / `bar_scale`
 *    说了算。它只用来把 `1440` 这一个数**翻译成人看得懂的字**。 */
const DAILY_PERIOD = 1440

/** 一条信号属于哪一档的**标签**。
 *  ★三种"不属于 5/15/30 任何一档"要分开说（见 `api2.ts` 里 `period` 那段）：
 *    0 = 冻结规格里的"综合"档；null = 服务端给跨周期信号的写法（`resonance`）；
 *    1440 = **日线口径**（服务端降级了，见 `bar_scale`）。
 *    `String(null)` 那种写法会印出「null 分钟」—— 那是把服务端的空值
 *    当成一个周期名给用户看。印「1440 分钟」是同一个毛病的另一种样子：
 *    那个数是对的，但**用户认不出它说的是"日线"**。 */
function periodLabel(p: number | null): string {
  if (p === null) return "跨周期"
  if (p === DAILY_PERIOD) return "日线"
  return p === 0 ? "综合" : `${p} 分钟`
}

/** 这个字段值能安全地当数字显示吗。**新契约的字段一个都不许直接 `.toFixed()`
 *  / `num()`**：`num({})` 会走进 `v.toLocaleString is not a function` 直接崩页面，
 *  而 `null` 又要显示成「—」。所以新字段一律先过这一层类型判断。 */
function numOf(v: unknown, digits = 2): string {
  return typeof v === "number" ? num(v, digits) : "—"
}

/** 每条信号的强度条。★**中性色**（`--accent`）—— "强度"不是盈亏。 */
function Strength({ v }: { v: number }) {
  return (
    <span className="flex items-center gap-2">
      <span className="w-16 flex-none"><Meter v={v * 100} k="strength" title={`强度 ${v}`} /></span>
      <span className="tnum text-xs text-[var(--ink)]">{v.toFixed(2)}</span>
    </span>
  )
}

function SignalRow({ s, active }: { s: IntradaySignal; active: boolean }) {
  const tone = sideTone(s.side)
  return (
    <tr data-k="signal" data-sid={s.id} data-period={s.period === null ? "resonance" : String(s.period)}
        className={active ? "bg-[var(--paper)]" : undefined}>
      <Td>
        <span className="text-[var(--ink)]">{s.name}</span>
        <div className="text-[10.5px] text-[var(--ink-3)]"><code>{s.id}</code></div>
      </Td>
      <Td k="period">{periodLabel(s.period)}</Td>
      <Td>
        <span className={"font-semibold " + (tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-[var(--ink-3)]")}
              data-k="side">
          {sideLabel(s.side)}
        </span>
      </Td>
      <Td><Strength v={s.strength} /></Td>
      <Td className="min-w-[280px]" k="why">
        {/* ★空的 why **不是空白行**：规格要求每条信号都给可复核的算术，
            没给的时候页面上必须看得出来"这一条后端没做到"。 */}
        {s.why
          ? <span className="text-[var(--ink-2)]">{s.why}</span>
          : <span className="text-[var(--warn-ink)]">这一条<b>没有给可复核的说明</b>（<code>why</code> 是空的）</span>}
        {s.refs && Object.keys(s.refs).length ? (
          <div className="mt-0.5 text-[10.5px] text-[var(--ink-3)]">
            {Object.entries(s.refs).map(([k, v]) => (
              <span key={k} className="mr-2">
                <code>{k}</code>=<span className="tnum">{v === null ? "—" : String(v)}</span>
              </span>
            ))}
          </div>
        ) : null}
      </Td>
    </tr>
  )
}

/** 各周期的一档"这一档偏多还是偏空"。
 *
 *  ★★这是**前端派生**的：服务端只给每条信号自己的 `side` 和 `period`，
 *    没有给"5 分钟这一档的整体方向"。这个数按信号表算出来（同向取 +1/−1，
 *    乘以强度求均值），页面**必须写明它是派生的** —— 不写的话它会看起来
 *    像服务端给的一个字段，而它只是这十条信号的一种读法。 */
function periodLean(signals: IntradaySignal[], period: number): { v: number | null; n: number } {
  const rows = signals.filter((s) => s.period === period && s.side !== "flat")
  if (!rows.length) return { v: null, n: 0 }
  const num2 = rows.reduce((a, s) => a + (s.side === "long" ? 1 : -1) * s.strength, 0)
  return { v: num2 / rows.length, n: rows.length }
}

export function Intraday({ probe = false }: { probe?: boolean }) {
  // ★默认市场是 `cn_index`，**不是** `cn`：默认标的 `sh000300` 本来就是指数
  //   （沪深300）。写成 `cn` 时服务端会按股票解析成 `sz000300`，取不到才靠
  //   "上游空了就换读法"那条兜底退回 `sh000300` —— 结果对，但**选择器上写着
  //   "A股"、取回来的是指数**，两处说法不一致，而这个不一致靠兜底掩着。
  //   改成 `cn_index` 之后解析是直接命中的，不依赖兜底。
  const [market, setMarket] = React.useState("cn_index")
  const [code, setCode] = React.useState("sh000300")
  const [period, setPeriod] = React.useState<number>(15)
  /** 三个周期按钮的 DOM 引用。**只为了在方向键换档时把焦点一起挪过去**
   *  （`role="tab"` 的自动激活约定，见下面 `go()` 上面那段）。 */
  const tabRefs = React.useRef<Record<number, HTMLButtonElement | null>>({})

  const initial = React.useMemo(() => {
    if (!probe) return null
    // ★探针那条路**同步**取（`agentui/probe.ts`）：异步回来的数，同步探针量不到。
    if (DEMO) return withBars(demoIntraday(code, PERIODS))
    return callSync<IntradayResp>(CHANNEL.intraday, { market, code, periods: PERIODS.slice() })
  }, [probe])  // eslint-disable-line react-hooks/exhaustive-deps

  const [resp, setResp] = React.useState<IntradayResp | null>(() => (initial?.ok ? initial.data : null))
  const [fail, setFail] = React.useState<Fail | null>(() => (initial && !initial.ok ? initial : null))
  const [loading, setLoading] = React.useState(!initial)

  const load = React.useCallback((m: string, c: string) => {
    setLoading(true); setFail(null)
    if (DEMO) {
      // ★演示那条路**也走同一个 setState 形状**：不一样的话，"演示好看、
      //   真实链路少一块"这种事就永远看不见。
      setResp(withBars(demoIntraday(c, PERIODS)))
      setLoading(false)
      return
    }
    void getIntraday(m, c).then((r) => {
      if (r.ok) { setResp(r.data); setFail(null) } else { setResp(null); setFail(r) }
      setLoading(false)
    })
  }, [])

  React.useEffect(() => { if (!initial) load(market, code) }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const signals = resp?.signals ?? []
  /** ★★**周期表以服务端为准**：日线降级的市场服务端给的是 `[1440]`，
   *  那一刻还画 5/15/30 就是三个**永远空着的格子** —— 而空格子和"这一档没信号"
   *  在画面上长得一模一样。缺键才回退到硬编码那三档（老服务端 / 演示数据）。 */
  const P: number[] = React.useMemo(() => {
    const ps = resp?.periods
    return Array.isArray(ps) && ps.length ? ps : (PERIODS as readonly number[]).slice()
  }, [resp])
  /** 当前选中的那一档。
   *  ★**必须兜底**：`period` 的初值是 15，而日线口径下 `P` 里只有 `1440`
   *    ⇒ 不兜的话 tab 一个都不高亮、方向键的 `indexOf` 得 −1（能算出界外的档）、
   *    体制和根数全取到空 —— 三处都是"看着像服务端没给"，实际是这里没接上。 */
  const cur: number = P.indexOf(period) >= 0 ? period : P[0]
  const daily = resp?.bar_scale === "daily"
  const regime = resp?.regime?.[String(cur)]
  const levelKeys = resp?.volume?.profile

  return (
    <Frame
      probe={probe} title={daily ? "策略分析（日线口径）" : "分钟级策略"}
      sub={daily
        ? "日线口径（该市场在本机没有分钟线源）· 五条只用 K 线序列与量能的策略"
        : "5 / 15 / 30 分钟三档 · 十条信号 · 每条都要有一条能自己动手复核的算术"}
      footExtra={<>这一页的信号是<b>规则算出来的</b>，没有回测证明它比"无脑喊涨"强；它只描述"量价刚刚发生了什么"。</>}
    >
      {DEMO ? <DemoBanner what="这一页显示的是本地编的一段分钟行情与十条信号，不是任何行情源返回的东西。" /> : null}

      {/* ★★**合成数据横幅**（`data.synthetic` / `data.data_source`）。
          分钟引擎拿不到真分钟线时会退回一份**合成帧**把流程跑完 —— 那一刻
          每个价位、每根成交量都是造出来的，而它们画出来、印出来和真的没有区别。
          所以这里不是"提示一下"，是**必须在人看得见的地方说清这一屏是编的**。
          ★判据是 `synthetic === true` **或** `data_source` 以 `SYNTH` 开头：
            两个键都读。只看一个的话，服务端少给另一个键时横幅就不出来了，
            而那一屏看起来和真行情一模一样。
          ★`role="alert"`：读屏用户也要听到这件事，不能只有眼睛能看见。 */}
      {resp && (resp.synthetic === true || /^synth/i.test(String(resp.data_source ?? ""))) ? (
        <div
          className="rounded border-2 border-[var(--warn-rule)] bg-[var(--warn-bg)] p-3 text-sm text-[var(--warn-ink)]"
          id="synthBanner" data-k="synthBanner" role="alert"
        >
          <b>这一屏的分钟线是合成的。</b>
          服务端这次返回的 <code>data_source</code> 是
          <code className="tnum"> {resp.data_source || "（没给）"}</code>，
          并且 <code>synthetic=true</code> —— 也就是说它<b>没拿到真分钟线</b>，
          为了把流程跑完退回了一份造出来的帧。
          <span className="mt-1 block">
            下面的<b>每一个价位、每一根成交量都是编的</b>，只能用来确认"这条通道通不通"，
            <b>不能当行情读</b>，更不能拿它去下单。
          </span>
        </div>
      ) : null}

      {/* ★★**日线口径横幅**（`bar_scale === 'daily'`）。
          港美股/加密在本机没有分钟线源 ⇒ 服务端把这一页降级成**日线**，周期只剩一档。
          不挂这条横幅的话，用户看到的是一排**永远空着的 5/15/30 格子**，而他会以为
          "接口坏了" —— 实际是口径换了一个，数本身是真的。
          ★它和上面那条合成横幅**说的是两件独立的事**（合成 = 数不是真的；
            日线 = 数是真的但粒度粗），所以两条都要能同时出现，谁也不能吃掉谁。 */}
      {resp && daily ? (
        <div
          className="rounded border-2 border-[var(--rule)] bg-[var(--paper)] p-3 text-sm text-[var(--ink-2)]"
          id="dailyBanner" data-k="dailyBanner" role="status"
        >
          <b>这一屏是「日线口径」，不是分钟级。</b>
          <span className="mt-1 block">
            服务端给的 <code>bar_scale</code> 是 <code>daily</code>、周期表是
            <code className="tnum"> {JSON.stringify(resp.periods ?? [])}</code> ——
            也就是说这个市场在本机<b>没有分钟线源</b>，服务端把口径降到了日线：
            下面只有<b>一档</b>，策略也只跑那五条"只用 K 线序列与量能"的。
          </span>
          <span className="mt-1 block">
            靠"当天第几分钟"才成立的那几条（开盘区间突破、VWAP 回归、时段量能、
            多周期共振）在日线上<b>不成立</b> —— 服务端<b>没跑</b>它们，
            原因逐条写在下面的错误清单里。
            <b>拿这一屏去讲行情的时候，不许说成"分钟级"。</b>
          </span>
          {/* ★★**日线口径独有的陷阱**：分钟 K 一收盘就定稿，而**日线的那一根
              是"当天"的、盘中一直在变**。实测过：北京 09:57 取美股，末行就是
              今天那根，量只有全天均量的 0.31 倍 —— 读出来是"缩量"，
              而那个"缩量"有一半只是因为**这天还没过完**。
              服务端把这件事算好了给过来（`last_bar`），页面有责任说出来：
              不说的话，那个 RVOL 看着就是一个正常的读数。 */}
          {resp.last_bar && resp.last_bar.closed === false ? (
            <span className="mt-1 block">
              ⚠ <b>最后那一根 K 还没收盘。</b>
              {resp.last_bar.why ? <>（{resp.last_bar.why}）</> : null}
              它的<b>成交量、收盘价、最高最低都还会变</b> ⇒ 基于它算出来的量比、
              形态、止损位都是"<b>半根 K</b>"的读数，别当定稿用。
              {resp.volume?.rvol_ex_last_bar !== undefined && resp.volume?.rvol_ex_last_bar !== null ? (
                <> 剔掉那根重算的量比是
                  <code className="tnum"> {num(resp.volume.rvol_ex_last_bar)}</code>
                  （服务端两个都给了，就是为了让人看出上面那个数有多少是"没收盘"造成的）。</>
              ) : null}
            </span>
          ) : null}
        </div>
      ) : null}

      <Panel
        id="pickPanel" title="标的与周期"
        note={<>通道 <code className="tnum">{CHANNEL.intraday}</code></>}
      >
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => { e.preventDefault(); load(market, code) }}
        >
          <label className="text-xs text-[var(--ink-3)]">
            <span className="block">市场</span>
            <select
              value={market} onChange={(e) => setMarket(e.target.value)}
              className="mt-0.5 rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1.5 text-sm text-[var(--ink)]"
            >
              {MARKETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="text-xs text-[var(--ink-3)]">
            <span className="block">代码</span>
            <input
              value={code} onChange={(e) => setCode(e.target.value)}
              className="mt-0.5 w-40 rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1.5 text-sm text-[var(--ink)]"
            />
          </label>
          <button
            type="submit" disabled={loading}
            className="rounded border border-[var(--accent)] bg-[var(--card)] px-3 py-1.5 text-sm font-semibold text-[var(--accent)] disabled:opacity-40"
          >
            {loading ? "取数中…" : "取一次"}
          </button>

          {/* ★周期切换用 `role="tablist"` + `aria-selected`：它是同一份数据的三种视图，
              不是三个"筛选按钮"。
              ★既然用了 `role="tab"`，方向键就**必须**能用（这是这个角色的约定：
              读屏用户按 `tab` 只落在选中的那一个上，换档靠左右箭头）。所以下面
              配了 roving tabindex + Arrow/Home/End。只写 role 不写键盘，
              比不写 role 更坏 —— 它给了读屏用户一个不成立的承诺。
              ★**焦点要跟着选中一起走**（自动激活那一种）：只改 `aria-selected`
              而不挪焦点的话，焦点环留在旧档上、高亮跑到新档上，读屏报的是
              「第 2 个，已选中」而眼睛看到的是第 3 个被选中 —— 两个通道说两句话，
              而且下一次按右键会从"焦点所在的那个"继续算，档位会跳。 */}
          <div className="ml-auto" role="tablist" aria-label="周期"
               onKeyDown={(e) => {
                 // ★用 `P` / `cur`（服务端给的档）而不是硬编码那三档：
                 //   日线降级时 `P` 只有一项，拿 `PERIODS.indexOf(period)` 会得 −1
                 const i = P.indexOf(cur)
                 const n = P.length
                 // 三个键走同一条路：先算出目标档，再"选中 + 挪焦点"一起做
                 const go = (j: number) => {
                   e.preventDefault()
                   const p = P[(j + n) % n]
                   setPeriod(p)
                   // 节点是复用的（`key` 是常量），所以这里同步 focus 落到的
                   // 就是重渲染之后的那一个，不需要等一帧
                   tabRefs.current[p]?.focus()
                 }
                 if (e.key === "ArrowRight") go(i + 1)
                 else if (e.key === "ArrowLeft") go(i - 1)
                 else if (e.key === "Home") go(0)
                 else if (e.key === "End") go(n - 1)
               }}>
            <div className="inline-flex overflow-hidden rounded border border-[var(--rule)]">
              {P.map((p) => (
                <button
                  key={p} type="button" role="tab" aria-selected={cur === p}
                  tabIndex={cur === p ? 0 : -1}
                  ref={(el) => { tabRefs.current[p] = el }}
                  onClick={() => setPeriod(p)}
                  className={
                    "px-3 py-1.5 text-sm focus-visible:outline focus-visible:outline-2 " +
                    "focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--accent)] " +
                    (cur === p
                      ? "bg-[var(--accent)] text-white"
                      : "bg-[var(--card)] text-[var(--ink-3)] hover:text-[var(--accent)]")
                  }
                >
                  {periodLabel(p)}
                </button>
              ))}
            </div>
          </div>
        </form>
        <p className="mt-2 text-xs text-[var(--ink-3)]">
          切档换的是「看哪一档」——体制、根数、量比、以及把哪一档的信号提到前面。
          <b>前端不重采样</b>：把 5 分钟拼成 15 分钟拼出来的线和真的长得一样，而它是编的。
        </p>
      </Panel>

      {fail ? <Degraded channel={CHANNEL.intraday} fail={fail} what="分钟级策略" /> : null}
      {loading && !resp && !fail ? <Loading what="正在取分钟级分析" /> : null}

      {resp ? (
        <>
          <Panel id="headPanel" title="这一轮拿到的东西"
                 note={<>分析时刻 <span className="tnum">{resp.ts}</span></>}>
            {/* ★「数据来源」**永远显示**，不管它是不是合成的 —— 它空着、或者写着
                一串我们不认识的 `SYNTH(...)`，那本身就是这一屏最该被看到的信息。
                只在"看着不对"时才显示来源的话，用户学会的是"没写来源就是没问题"。 */}
            <div className="mb-2 text-xs text-[var(--ink-2)]" data-k="dataSource">
              数据来源：<code className="tnum">{resp.data_source || "（服务端没给 data_source）"}</code>
              {resp.synthetic === true || /^synth/i.test(String(resp.data_source ?? ""))
                ? <b className="text-[var(--warn-ink)]">　—— 这一屏是合成的（见上面那条横幅）</b>
                : null}
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="标的" value={<span className="text-sm">{resp.name || resp.code}</span>} sub={<code>{resp.code}</code>} />
              {/* ★标签一律走 `periodLabel(cur)`：日线口径下 `cur` 是 `1440`，
                  写死「${period} 分钟」会印出「15 分钟体制」而矩阵里那一档根本不存在
                  （`cur` 会兜底到 `P[0]`，但标签若还用 `period` 就与服务端不一致）。 */}
              <Stat label={`${periodLabel(cur)}体制`} value={regime ? (REGIME_LABEL[regime] ?? regime) : "—"}
                    sub={regime ? "服务端给的 regime，原样翻译" : "服务端没给这一档的体制"} />
              <Stat label={`${periodLabel(cur)}根数`} value={num(resp.bars?.[String(cur)] ?? null, 0)} k="nbars"
                    sub="服务端说这一档拿到多少根" />
              <Stat label="量比（RVOL）" value={num(resp.volume?.rvol ?? null)}
                    sub={resp.volume?.rvol === null
                      ? "服务端说 null（当天 bar 不够）—— 不是 0"
                      : "服务端给的这一轮量比"} />
              <Stat label="方向（综合）" value={sideLabel(resp.composite?.side ?? null)}
                    tone={sideTone(resp.composite?.side)} k="compositeSide"
                    sub="方向看这一个：服务端 composite" />
              <Stat label="共振分" value={num(resp.composite?.score ?? null, 3)}
                    sub="−1…+1，服务端给的" k="compositeScore" />
            </div>
            {!signals.length ? (
              <div className="mt-2">
                <Empty what="这一轮没有策略信号" hint="接口是通的，只是这一轮信号表是空的 —— 这和「接口坏了」是两件事。" />
              </div>
            ) : null}
          </Panel>

          <Panel
            id="klinePanel"
            /* ★日线口径下面板标题不能还写「分钟」——标题是这一屏口径最显眼的一处 */
            title={daily ? `价格与量能（${periodLabel(cur)}档）` : `分钟价量（${periodLabel(cur)}档）`}
            note="蜡烛 + 成交量 + VWAP + 价值区，四层共用一根横轴"
          >
            {resp.klines && resp.klines.length ? (
              <CandlePanel
                bars={resp.klines.map((b) => ({ t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v }))}
                vwap={resp.vwap_series ?? null}
                poc={levelKeys?.poc ?? null}
                va={{ lo: levelKeys?.va_lo ?? null, hi: levelKeys?.va_hi ?? null }}
                probe={probe}
                k="candlePanel"
                label={`分钟 K 线，共 ${resp.klines.length} 根，含 VWAP 与价值区`}
              />
            ) : (
              /* ★这一支不是"没做好"，是**契约里没这个字段**（`api2.ts` 那句）。
                 所以它说的是"接口没给"，并指出那张预留的字段表 —— 不画一根假的。 */
              <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-3 text-sm text-[var(--ink-2)]"
                   id="noKlines" data-k="noKlines" role="status">
                <b>这一轮的返回里没有 K 线。</b>
                <div className="mt-1 text-xs text-[var(--ink-3)]">
                  规格 §3.2 只冻结了分析结果（信号 / 位置 / 共振 / 量能），<b>没有冻结 K 线本身</b>。
                  前端已经在「K 线」和「分时均价线（VWAP）」这两个位置上预留了口子
                  —— 服务端一给，这张图就有了。
                  {/* ★「别拿日线顶上来」这句在**日线口径下**会读成自相矛盾
                      （这一屏的口径就是日线）⇒ 分口径说清"不要哪种顶替"。 */}
                  {daily
                    ? <><b>不拿"另一个接口"的日线凑一张</b>：这一屏的口径本来就是日线，
                        但那张图要的是<b>这个标的、这一次分析</b>的蜡烛序列，这一轮没给 ——
                        换个数源凑出来的横轴，和上面那些数就不是同一份了。</>
                    : <><b>不使用别的接口的日线顶上来</b>：那张图回答的不是"这一分钟发生了什么"。</>}
                </div>
              </div>
            )}
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label="POC（成交最密集价）" value={num(levelKeys?.poc ?? null)} k="poc"
                    sub="服务端给的成交量分布" />
              <Stat label="价值区下沿" value={num(levelKeys?.va_lo ?? null)} k="vaLo" />
              <Stat label="价值区上沿" value={num(levelKeys?.va_hi ?? null)} k="vaHi" />
              <Stat label="量价背离" value={
                resp.volume?.divergence === "bearish" ? "看跌背离"
                  : resp.volume?.divergence === "bullish" ? "看涨背离"
                    : resp.volume?.divergence === "none" ? "没有" : "—"
              } sub={resp.volume?.divergence === null ? "服务端说 null（不是「没有」）" : "原样显示服务端给的取值"} />
            </div>
            {/* ★空值纪律：`volume.profile` 在当天 bar 不够时是 **null**，不是 0、也不是缺键。
                这三个「—」必须说清是哪一种 —— 不说的话，用户会把"没算"读成"算出来是 0 元"。 */}
            {resp.volume?.profile === null ? (
              <p className="mt-1 text-xs text-[var(--warn-ink)]" data-k="profileNull">
                成交量分布这一轮是 <code>null</code>：服务端说当天 bar 不够，没算。
                <b>这不是 0</b> —— 上面三个「—」是「没量到」，不是「量到 0」。
              </p>
            ) : null}
          </Panel>

          <Panel
            id="resonancePanel" title="多周期共振"
            note="服务端给的 composite；下面那三条是前端按信号表派生的"
          >
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(260px,1fr)_2fr]">
              <div>
                <div className="text-xs text-[var(--ink-3)]">服务端 composite</div>
                <div className="mt-1 flex items-baseline gap-3">
                  <span className={"text-lg font-semibold " + (sideTone(resp.composite?.side) === "up" ? "text-up" : sideTone(resp.composite?.side) === "down" ? "text-down" : "text-[var(--ink-3)]")}>
                    {sideLabel(resp.composite?.side ?? null)}
                  </span>
                  <span className="tnum text-lg text-[var(--ink)]" data-k="resScore">
                    {num(resp.composite?.score ?? null, 3)}
                  </span>
                  <span className="text-xs text-[var(--ink-3)]">
                    置信 <span className="tnum">{num(resp.composite?.confidence ?? null, 2)}</span>
                  </span>
                </div>
                <div className="mt-1 text-xs text-[var(--ink-3)]">
                  票数（服务端给的）：多 <span className="tnum text-up">{resp.composite?.agree?.long ?? "—"}</span>
                  {" · "}空 <span className="tnum text-down">{resp.composite?.agree?.short ?? "—"}</span>
                  {" · "}不表态 <span className="tnum">{resp.composite?.agree?.flat ?? "—"}</span>
                  {" · "}参与共振的信号 <span className="tnum" data-k="nSignals">{numOf(resp.composite?.n_signals, 0)}</span>
                  <span className="ml-1">条（<code>n_signals</code>，服务端自己数的）</span>
                </div>

                {/* ★派生量。**写在旁边**它是派生的：不写的话它看起来像一个服务端字段。 */}
                <div className="mt-3">
                  <div className="mb-1 text-xs text-[var(--ink-3)]">
                    各周期偏向（<b>前端派生</b>：把这一档里所有非「不表态」的信号按方向±强度求均值；
                    <b>跨周期</b>和<b>综合</b>那两类不属于任何一档，<b>不参与</b>下面这些行）
                  </div>
                  {P.map((p) => {
                    const s = periodLean(signals, p)
                    return (
                      <div key={p} className="flex items-center gap-2" data-k="lean" data-period={p}>
                        <span className="w-14 flex-none text-xs text-[var(--ink-3)]">{periodLabel(p)}</span>
                        <span className={"tnum w-14 flex-none text-xs " + (s.v === null ? "text-[var(--ink-3)]" : s.v > 0 ? "text-up" : s.v < 0 ? "text-down" : "text-[var(--ink-3)]")}>
                          {s.v === null ? "—" : `${s.v > 0 ? "+" : ""}${s.v.toFixed(2)}`}
                        </span>
                        <span className="text-[10.5px] text-[var(--ink-3)]">
                          {s.n ? `${s.n} 条信号参与了` : "这一档没有方向性信号（不是 0，是没量到）"}
                        </span>
                      </div>
                    )
                  })}
                </div>

                <p className="mt-2 text-[10.5px] leading-snug text-[var(--ink-3)]">
                  ★共振分回答的是「几档指向同一侧」。三档全同向和只有一档有信号，
                  在"分数"上可能都是 0.6 —— 所以上面那一行「几条信号参与了」必须一起读。
                </p>
              </div>

              <div>
                <div className="mb-1 text-xs text-[var(--ink-3)]">每条信号的方向 × 强度（横条是强度，颜色是方向）</div>
                <HBars
                  rows={signals.filter((s) => s.side !== "flat")
                    .map((s) => ({ name: `${s.name}（${periodLabel(s.period)}）`, strength: s.strength, side: s.side }))}
                  index="name" value="strength" probe={probe} k="signalBars"
                  digits={2} domain={[0, 1]}
                  tone={(r) => (r.side === "long" ? "up" : "down")}
                />
                <div className="mt-1 text-[10.5px] text-[var(--ink-3)]">
                  「不表态」的那些<b>不画</b>在图上（它们的强度不代表方向）；它们仍然在下面的表里。
                </div>
              </div>
            </div>
          </Panel>

          <Panel id="levelsPanel" title="入场 / 止损 / 目标" note="这几个数是服务端按 ATR 算的，方法原样显示">
            {resp.levels ? (
              <>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                  <Stat label="入场" value={num(resp.levels.entry)} k="entry" />
                  <Stat label="止损" value={num(resp.levels.stop)} k="stop" tone="down" />
                  <Stat label="目标 1" value={num(resp.levels.target1)} k="t1" tone="up" />
                  <Stat label="目标 2" value={num(resp.levels.target2)} k="t2" tone="up" />
                  <Stat label="ATR" value={num(resp.levels.atr)} k="atr" />
                  <Stat label="盈亏比" value={num(resp.levels.rr, 2)} k="rr"
                        sub="服务端算的，不是前端拿目标/止损再除一遍" />
                </div>
                <div className="mt-2 text-xs text-[var(--ink-2)]">
                  <b>算法</b>：{resp.levels.method || "（服务端没给 method）"}
                </div>
                {/* ★**价格口径**：这几个数建在 ATR 上，而 ATR 吃的是拿到的那个价。
                    腾讯对港美股**不给复权**（`price_adjust.mode === 'none'`）⇒
                    除权那天的跳空会原样进 ATR，把止损位撑宽 —— 而它看起来
                    和"真波动变大"没有区别。A 股对照组是 `qfq`，所以这不是
                    "函数写死了"，是**这条源没有**。 */}
                {resp.price_adjust?.mode === "none" ? (
                  <div className="mt-1 text-xs text-[var(--warn-ink)]" data-k="noAdjust">
                    ⚠ 这一份价<b>没复权</b>（服务端给的 <code>price_adjust.mode</code> = <code>none</code>）。
                    除权除息那一天的跳空会原样进 ATR ⇒ 上面那个止损位会偏宽。
                    {resp.price_adjust.note ? <> {resp.price_adjust.note}</> : null}
                  </div>
                ) : null}
                {/* ★**两个方向读数不能混**：`composite.side` 是判断，`levels.side` 是兜底
                    （composite 落在死区、说不出方向时它照样给一个，而且平票取 long）。
                    这里把它显示出来但**标明它是兜底** —— 藏起来的话，它哪天和上面那个
                    方向不一致，用户只会觉得"页面上有两个方向，随便看哪个都行"。 */}
                {resp.levels.side ? (
                  <div className="mt-1 text-xs text-[var(--ink-3)]" data-k="levelsSide">
                    · 这一段自己带的兜底方向 <code>levels.side</code> = <b>{sideLabel(resp.levels.side)}</b>。
                    <span className="ml-1">
                      它是<b>兜底不是判断</b>：综合方向落在死区时它照样会给一个，平票直接取「看多」。
                      这一页的「方向」一律看上面的 <code>composite.side</code>
                      {resp.composite?.side && resp.levels.side !== resp.composite.side
                        ? <>
                          　—— <b className="text-[var(--warn-ink)]">这次两者不一致</b>
                          （composite 说 {sideLabel(resp.composite.side)}、兜底说 {sideLabel(resp.levels.side)}），
                          以 composite 为准。
                        </>
                        : "。"}
                    </span>
                  </div>
                ) : null}
                <p className="mt-1 text-[10.5px] text-[var(--ink-3)]">
                  ★这几个价位是<b>波动率的推算</b>，不是预测。它回答"按这个波动，多少点算异常"，
                  不回答"它会不会到那儿"。
                </p>
              </>
            ) : (
              <Empty what="这一轮没有给位置（levels 是 null）"
                     hint="没有 ATR 就没有止损位，也就算不出盈亏比 —— 这里不替它算一个。" />
            )}
          </Panel>

          <Panel id="signalsPanel"
                 title={daily ? "策略信号（日线口径：五条）" : "十条策略信号"}
                 note={`服务端这一轮给了 ${signals.length} 条`}>
            {signals.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-[var(--rule)] text-xs text-[var(--ink-3)]">
                      <Th>策略</Th><Th>周期</Th><Th>方向</Th><Th>强度</Th><Th>为什么（可复核的算术）</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {/* ★当前档的信号排前面（不隐藏别的）：切档时"这一档有什么"要一眼看到，
                        但"还有哪些别的档"也不能消失 —— 隐藏掉的话，用户会以为
                        这一档的判断没有反证。 */}
                    {[...signals]
                      .sort((a, b) => (a.period === cur ? 0 : 1) - (b.period === cur ? 0 : 1))
                      .map((s) => <SignalRow key={s.id} s={s} active={s.period === cur} />)}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty what="这一轮的信号表是空的" hint="接口通了、返回里没有信号 —— 这本身就是一条结论。" />
            )}
          </Panel>

          <Panel id="volumePanel" title="量能与分布" note="每一档的量比、量比的基准、以及成交量分布">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {P.map((p) => (
                <Stat key={p} label={`${periodLabel(p)}量比`} k={`rvol${p}`}
                      value={num(resp.volume?.rvol_by_period?.[String(p)] ?? null)}
                      sub="服务端按各档算的，前端不自己算" />
              ))}
            </div>
            {resp.volume?.rvol_by_period === undefined ? (
              <p className="mt-1 text-xs text-[var(--warn-ink)]" data-k="rvolByPeriodMissing">
                这一轮的返回里<b>没有</b> <code>rvol_by_period</code>（不是若干个 0）——
                上面那些「—」是「服务端没给」，不是「量比等于 0」。
              </p>
            ) : null}

            {/* ★★量比的**分母**是哪一种，决定了这个数对不对。两个基准服务端都给了：
                `slot_mean` 是同一时刻的历史均量（对的那个），`naive_mean` 是不分时段的
                全天均量（**错**做法，留着当对照）。标错了就等于把"日内量是 U 型"
                这个坑重新踩一遍 —— 早盘和尾盘的量本来就大，拿全天均量当分母，
                每天开盘都"放量"，而那个放量是日历造成的、不是行情造成的。 */}
            <div className="mt-3 rounded border border-[var(--rule)] bg-[var(--paper)] p-2" data-k="rvolBase">
              <div className="text-xs text-[var(--ink-2)]">量比的基准（服务端两个都给了，<b>这里必须分清</b>）</div>
              <div className="mt-1 grid grid-cols-1 gap-2 sm:grid-cols-2">
                <div className="rounded border border-[var(--rule)] bg-[var(--card)] p-2">
                  <div className="text-[10.5px] text-[var(--ink-3)]">
                    同一时刻的历史均量 <code>slot_mean</code> —— <b>对的那个</b>
                  </div>
                  <div className="tnum text-sm text-[var(--ink)]">{numOf(resp.volume?.rvol_base?.slot_mean)}</div>
                </div>
                <div className="rounded border border-[var(--rule)] bg-[var(--card)] p-2">
                  <div className="text-[10.5px] text-[var(--ink-3)]">
                    全天均量 <code>naive_mean</code> —— <b>错的那个</b>（留着当对照）
                  </div>
                  <div className="tnum text-sm text-[var(--ink-3)]">{numOf(resp.volume?.rvol_base?.naive_mean)}</div>
                </div>
              </div>
              <p className="mt-1 text-[10.5px] leading-snug text-[var(--ink-3)]">
                {resp.volume?.rvol_base?.note ? <>{resp.volume.rvol_base.note}　</> : null}
                上面的量比用的是<b>同时刻</b>的均量；拿全天均量做分母会在每天开盘和尾盘
                造出一批假的"放量"（日内量本来就是 U 型）。
              </p>
            </div>
            <div className="mt-2">
              <HBars
                rows={P.map((p) => ({ name: periodLabel(p), rvol: resp.volume?.rvol_by_period?.[String(p)] ?? 0 }))}
                index="name" value="rvol" probe={probe} k="rvolBars" digits={2}
                tone="accent"
              />
              <div className="mt-1 text-[10.5px] text-[var(--ink-3)]">
                ★量比是<b>中性色</b>：放量本身不代表涨，缩量本身不代表跌。
                红绿留给方向，不给成交量。
              </div>
            </div>
          </Panel>

          <Panel id="sessionPanel" title="时段">
            {resp.session ? (
              <>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {/* ★**只翻译我们确定知道的那一个取值**：`'all'` 是加密的全时段，
                      24 小时恒为它 —— 这是**正确值**，不是"未知"。别的取值原样照抄：
                      猜一个中文标签就等于替服务端改口径，而猜错的那个标签
                      看起来和真的一样。 */}
                  {/* ★日线口径下服务端**明说** `phase=null`、`window=null`
                      （不是"没算出来"，是"这个口径里没有'当天第几分钟'这个维度"）。
                      直接渲染 `null` 会得到一个**空值格子** —— 而空格子与
                      "服务端没给"在画面上长得一模一样 ⇒ 这里必须把话写出来。 */}
                  <Stat label="时段" k="phase"
                        value={resp.session.phase === null ? "不适用（日线口径）"
                          : resp.session.phase === "all"
                            ? "全时段（all，加密 24 小时）"
                            : resp.session.phase}
                        sub={resp.session.phase === null
                          ? "服务端给的 phase 就是 null —— 它同时说明「日线口径没有时段效应」"
                          : resp.session.phase === "all"
                            ? "服务端给的取值就是 all —— 加密不分时段，这是对的"
                            : "服务端给的取值，原样照抄"} />
                  <Stat label="当前窗口" k="window"
                        value={resp.session.window === null ? "—" : resp.session.window}
                        sub={resp.session.window === null ? "这一档没有窗口（phase 是 null）" : undefined} />
                </div>
                <p className="mt-2 text-xs text-[var(--ink-2)]">{resp.session.note}</p>
              </>
            ) : (
              <Empty what="这一次没有时段信息（session 是 null）" />
            )}
          </Panel>

          {resp.errors?.length ? (
            <Panel id="errorsPanel" title="服务端说这几件事没做出来">
              <ErrorList items={resp.errors} title="原样照抄，不翻译" />
              <p className="mt-1 text-[10.5px] text-[var(--ink-3)]">
                这一栏是<b>这一页最值钱的地方之一</b>：少一条策略、缺一档量比，
                原因都写在这里。它空着的时候，"十条信号只有六条"就变成了一个谜。
              </p>
            </Panel>
          ) : null}
        </>
      ) : null}

      <Panel id="notePanel" title="这一页不做什么">
        <Well>
          <ul className="list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
            <li>不预测下一根 K 线。信号说的是"刚刚发生了什么"，不是"接下来会怎样"。</li>
            {/* ★口径这件事必须写进"这一页不做什么"：不然用户拿这一屏去讲行情时，
                很容易说成"分钟级分析" —— 而那正是这一屏**不是**的东西。 */}
            {daily ? (
              <li><b>口径是日线，不是分钟级</b>：这个市场在本机没有分钟线源，
                所以服务端自己把口径降到了日线 ——
                周期只有一个（<code className="tnum">{P.map(periodLabel).join(" / ")}</code>）、
                策略只跑那五条"只用 K 线序列与量能"的。
                <b>这不等于"分钟级那套也能用"</b>，更不许说成分钟线。</li>
            ) : null}
            <li>不重采样：{daily
              ? <>日线口径下服务端只给了<b>一个</b>周期，前端也不会把它拆成"更细"的档。</>
              : <>5 / 15 / 30 三档都是服务端各自算的，前端不把一档拼成另一档。</>}</li>
            <li>强度是 0–1 的<b>相对</b>数，服务端给多少显示多少，不做二次归一 ——
              二次归一之后"强度"就不再有可比性，而它看着还像个强度。</li>
            <li>「方向」和「强度」都<b>不是胜率</b>。这个站的回测没有证明过任何一条策略有超额能力。</li>
            <li><b>不把合成帧当行情</b>：服务端拿不到真分钟线时会退回一份造出来的帧
              （<code>synthetic=true</code>）。那时候页面上会挂一条横幅说明这件事 ——
              这一页不会因为"不显示它比较好看"就不显示。</li>
            <li><b>空值不补 0</b>：成交量分布在当天 bar 不够时是 <code>null</code>、
              没有 ATR 时 <code>levels</code> 整个是 <code>null</code>。
              页面对这些都显示「—」并写明是哪一种"没有"，<b>不替服务端算一个</b>。</li>
          </ul>
        </Well>
      </Panel>
    </Frame>
  )
}

/** 兜住"TONE_VAR 没用到"的告警：HBars 的 tone 用的是语义键，这个文件里没有
 *  直接引用那个表 —— 留着这行是为了让"配色只有一处出处"这件事在 import 上看得见。 */

export default Intraday
