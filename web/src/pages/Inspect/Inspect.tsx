/**
 * `/inspect` 建议体检。
 *
 * ★★这一页的立意与站里其它页**全部反过来**：它不给你建议，它**检验别人给你的建议**
 *   —— 你贴进来一段（微信群里转的、亲戚介绍的、理财经理发的），它告诉你
 *   ① 这段话**承诺**了什么（收益率逐条抽出来、折算成年化）；
 *   ② 哪几句是**红旗**（每条都带你**能自己核**的原文句子 + 触发词 + 词条出处）；
 *   ③ 哪几句**看着像红旗、其实不算**（银行存款说"保本"是合法的）；
 *   ④ 你**该问它什么**（不是罪证，是问题）；
 *   ⑤ 哪几件事**它判不了**（要不要查牌照、工商穿透 —— 本站没有那个数据源）；
 *   ⑥ 拿什么**对照**（本站自己那份回测：模型方向命中率和"无脑喊涨"统计上分不开）。
 *
 * ★四条纪律，每条都在下面有对应的 DOM：
 *   ① **不判罪、只给材料。** 这一页没有"这是一条诈骗"这种句子，只有
 *      "这一句里有『保本』，词条说资管新规之后理财产品不能这么说"。
 *      措辞的分寸不是修辞：一个能说"这是诈骗"的页面要有资格认定，本站没有。
 *   ② **豁免块必须显示**（`#exemptPanel`）。只会报红的检查器等于一个**恒真的告警**，
 *      用户第一天就学会无视它。能说"这句不是红旗，因为**银行存款除外**"，
 *      才是这一页存在的原因。★它**空着也要显示**：空着 = "这一轮没有需要豁免的句子"，
 *      和"这一块没做出来"是两件事。
 *   ③ **空态必须显示后端那句 `empty_reason`**（`#empty`），不许留一片空白 ——
 *      "没检出红旗"和"这条建议是干净的"在用户眼里长得一模一样，而它们是两句话。
 *   ④ **判不了的单独一块**（`#cannotPanel`），和红旗**措辞分开**：
 *      "我判不了"和"我没发现问题"之间差着一次立案调查。
 *
 * ★id 前缀一律 `i*`（`iText` / `iGo` / `iEg*`），**面板 id 一律 `*Panel`**，
 *   行级锚点走 `data-k`（`flag` / `exempt` / `question` / `cannot` / `rate` / `readnext`）。
 *   页内不许出现 `r*` / `g*` 前缀 —— 那是 `/research` 和 `/game` 的地盘。
 */
import * as React from "react"
import { Frame } from "../../agentui/Frame"
import { Degraded, Empty, Loading, Panel, Well } from "../../agentui/State"
import type { Fail } from "../../api2"
import { num } from "../../format"
import {
  forgetInspect, getInspect, getInspectSync, getInspectTextSync, INSPECT_CHANNEL,
  inspectErrText, rememberInspect,
  type InspectBaseline, type InspectCannot, type InspectFlag, type InspectNote,
  type InspectQuestion, type InspectReadNext, type InspectResp,
} from "../../inspect"

/** 后端的手写中文里带 `**强调**` 记号（它**不过** `uicopy.humanize`，是原文）。
 *
 *  ★**必须处理**：原样渲染的话用户看到的是字面的 `**规则**`（两个星号），
 *    而那是"文本没洗干净"，不是强调。
 *  ★**渲染成 `<b>` 而不是把记号删掉**（站里两种先例都有：`AgentBoard` 的
 *    `stripMd` 剥掉、`Psych` 也是剥掉、`ChatCard` 渲染成 `<strong>`）——
 *    这一页选渲染：这几句话里的强调**是有信息的**（"唯二能说保本的是银行存款和国债"、
 *    "没检出 ≠ 没问题"），剥掉之后那半句会和平铺的正文混在一起读不出来。
 *  ★**不渲染 HTML**（不碰 `dangerouslySetInnerHTML`）：按 `**` 切成奇数/偶数段，
 *    奇数段落进 `<b>`。用户贴进来的原文里若真带 `**`，它只会被加粗、不会被执行。
 *  ★**只用在"后端写的散文"上**（`why` / `because` / `empty_reason` 这一族），
 *    绝不用于 `sentence` / `raw` / 冻住的原文 —— 那是**证据**，要逐字照抄。 */
function md(s: string): React.ReactNode {
  const parts = String(s ?? "").split("**")
  return parts.map((p, i) => (i % 2 ? <b key={i}>{p}</b> : <React.Fragment key={i}>{p}</React.Fragment>))
}

/** 同上，但结果是**纯字符串**（`Empty` 的 `what` 只吃 `string`）。
 *  ★只用于那一个位置：它的记号剥掉之后仍然是一句完整的人话。 */
function plain(s: string): string {
  return String(s ?? "").replace(/\*\*/g, "").replace(/`/g, "")
}

/** 知识图谱页的地址。
 *
 *  ★**不带 `#词名` 那个片段**（虽然看起来更贴心）：`graph.html` 里**没有**
 *    `hashchange` / `location.hash` 那一套（实测 grep 过），加了等于一个**空跳转**
 *    —— 浏览器不会滚到那个词条，而链接上看不出来。这一页的纪律是不许出现
 *    "看着像做了、其实没做"的东西，所以宁可给一个诚实的整页链接。
 *    （`graph.html` 里有一个搜索框 `#q`，落地之后搜一下就到 —— 提示写在链接的
 *      `title` 里，用户 hover 得到。） */
const GRAPH = "/graph.html"

/** 两个示例。★第一个是**正例**（什么都命中：四条红旗 + 一条豁免 + 判不了的 +
 *  对照基准），第二个是**负例**（银行存款，豁免生效、一条红旗都不报）。
 *
 *  ★★正例的**句子顺序**是按后端的判定顺序排的，不是随手写的：
 *    `agent/checkup.py` 对每条规则**取第一句命中的**、之后就 `break`。
 *    所以那条"银行存款保本"必须排在"年化 12% 保本保息"**之前** ——
 *    排在后面的话它落到 `break` 之后，**豁免块会是空的**，而页面看着一切正常
 *    （红旗照样有一条）。这是"演示不出来"的典型形态：不是页面坏了，
 *    是这段示例文本**结构上**演示不到豁免那一块。 */
const EG_POS = "我剩下的钱是存银行定期存款的，那个保本。隔壁老王介绍的项目说年化 12%、保本保息，"
  + "拉人头还有返利，老师带单稳赚，钱打给个人账户就行。他还说这项目有备案、吸收了公众存款，"
  + "下个月肯定涨，建议买入。"

/** 负例。★演示的是**豁免**：一个只会报红的工具会把"银行定期存款保本"报成红旗。 */
const EG_NEG = "银行三年定期存款，年化 2.6%，保本。"

/** `ok:false` 的报告 → `Fail`。`Degraded` 要的是那个形状。
 *
 *  ★**失败有两种来源，页面上是两句不同的话**（`api2.ts` 的 `FailKind`）：
 *    · 契约里的那一支（HTTP 400）：`{ok:false, hint:'没给内容', source:'rule'}` ——
 *      服务端在、它说做不了 ⇒ `server`；
 *    · 路由还不存在（404）⇒ `notfound`（施工还没轮到，用户什么都不用做）。
 *      ★404 的响应体是 Flask 的 `{'error':'not found'}`，**没有 `ok` 这个键** ——
 *      所以"有没有 `ok`"就是这两支的分界线，比去猜 `error` 的措辞稳。
 *    · 同步那条路上的 `NETWORK`（连不上）⇒ `offline`。
 *    ★合成一句"加载失败"就等于把前两种伪装成第三种。 */
function mkFail(r: InspectResp): Fail {
  const noOk = typeof (r as { ok?: unknown }).ok !== "boolean"
  const kind: Fail["kind"] =
    r.error === "NETWORK" ? "offline" : (r.error === "NOTFOUND" || noOk) ? "notfound" : "server"
  return {
    ok: false, kind,
    reason: inspectErrText(r),
    detail: r.error && r.error !== r.hint ? `${r.error}${r.hint ? " · " + r.hint : ""}` : undefined,
  }
}

/** 一个词条 → 图谱页的链接。
 *
 *  ★**不是** `.tchip`：那个类名归 `ChatCard` 的对话卡所有，`verify_agent.py` 有一条
 *    判据数它的**个数**（`index.css:929` 记着这件事）。这一页借用那个类名的话，
 *    两条互不相干的判据会开始互相影响 —— 换一套自己的类，碰撞面是零。
 *
 *  ★★**这个件不许带 `data-term`**（虽然它天生就有一个词条名）。探针取红旗词条名
 *    走的是 `#insFlags [data-term]` 这个选择器，而 `Term` 就渲染在每一条红旗里面 ——
 *    它也带 `data-term` 的话，一条红旗会命中**两个**节点，
 *    `insFlags` 就成了 `["保本保收益承诺","保本保收益承诺","杀猪盘","杀猪盘"]`，
 *    而判据那边是跟规则引擎的 `flags[].term` **逐字全等**比的 ⇒ 必红，
 *    且红的原因（多了一个装饰性节点）从读数上完全看不出来。
 *    词条名已经在 `title` 里（hover 可见），链接不需要再带一份机器读的副本。 */
function Term({ t }: { t: string }) {
  return (
    <a href={GRAPH} title={`在知识图谱里搜「${t}」`}
       className="mr-1 inline-block rounded border border-[var(--rule)] bg-[var(--paper)] px-1.5 py-0.5 text-[11px] text-[var(--ink-2)] no-underline hover:text-[var(--accent)]">
      {t}
    </a>
  )
}

/** 一条红旗。★五样东西缺一不可：**词条名**（它是什么）、**原文那一句**
 *  （凭什么）、**触发词**（哪几个字）、**为什么**（词条的意思）、**出处**
 *  （去哪对账）。只写"有风险"的红旗等于没有信息。 */
function FlagRow({ f }: { f: InspectFlag }) {
  return (
    <Well className="mb-2" >
      <div data-k="flag" data-term={f.term}>
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-sm font-semibold text-[var(--warn-ink)]" data-k="flagTerm">{f.term}</span>
          <Term t={f.term} />
          <span className="text-[10.5px] text-[var(--ink-3)]" data-k="flagFrom">{f.from}</span>
        </div>
        {/* ★原文那一句**照抄**，不加引号之外的任何修饰 —— 用户要拿它回去
            对一遍"我收到的到底是不是这么说的"。 */}
        <blockquote className="mt-1 border-l-2 border-[var(--warn-rule)] pl-2 text-xs text-[var(--ink-2)]"
                      data-k="flagSentence">
          {f.sentence}
        </blockquote>
        <div className="mt-1 text-[11px] text-[var(--ink-3)]">
          命中的字：
          {(f.matched ?? []).map((m) => (
            <code key={m} className="mr-1.5 rounded border border-[var(--rule)] bg-[var(--card)] px-1"
                  data-k="flagMatched">{m}</code>
          ))}
        </div>
        {f.why ? <div className="mt-1 text-xs text-[var(--ink-2)]" data-k="flagWhy">{md(f.why)}</div> : null}
      </div>
    </Well>
  )
}

/** 一条"查过了、这句不算"。★**和红旗一样要有原文那一句** ——
 *  "我们查了"这句话不配原文就是空话，用户没法确认我们查的到底是不是他关心的那句。 */
function ExemptRow({ n }: { n: InspectNote }) {
  return (
    <Well className="mb-2">
      <div data-k="exempt" data-term={n.term}>
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-sm font-semibold text-[var(--accent)]">这句不算红旗</span>
          <span className="text-xs text-[var(--ink-2)]">词条「{n.term}」</span>
          <Term t={n.term} />
        </div>
        {n.sentence ? (
          <blockquote className="mt-1 border-l-2 border-[var(--accent)] pl-2 text-xs text-[var(--ink-2)]"
                        data-k="exemptSentence">
            {n.sentence}
          </blockquote>
        ) : null}
        <div className="mt-1 text-xs text-[var(--ink-2)]" data-k="exemptWhy">
          为什么不算：{md(n.why)}
          {n.exempt ? <>　<b>救下这一句的那个词是「{n.exempt}」。</b></> : null}
        </div>
      </div>
    </Well>
  )
}

/** 一条"规则和词表脱节了"（后端的 `rule_orphan`）。
 *  ★它和豁免**共用 `exempt_notes` 这个数组**，但意思是反的：豁免是"这句没问题"，
 *    这一条是"**这一页自己有问题**"（规则指的词条不在图谱里 ⇒ 这条规则形同虚设）。
 *    混进豁免那一段去渲染的话，后端的一致性告警会被读成"这句没问题"——
 *    那正好把它的意思说反了。所以单独一段、用警告色。 */
function OrphanRow({ n }: { n: InspectNote }) {
  return (
    <div className="mt-2 rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] p-2 text-xs text-[var(--warn-ink)]"
         data-k="orphan" data-term={n.term}>
      <b>规则和词表脱节了：</b>{md(n.why)}
      <div className="mt-1 opacity-80">
        这是<b>后端自己的一致性告警</b>，不是对你这段建议的判断 —— 它说的是
        「<code>{n.term}</code>」这条规则<b>没有生效</b>（词条不在图谱里）。
      </div>
    </div>
  )
}

/** 一条"该问的问题"。★措辞是**问句**，不是判决 —— 这一层不判罪。 */
function QRow({ q }: { q: InspectQuestion }) {
  return (
    <Well className="mb-2">
      <div data-k="question">
        <div className="text-sm font-semibold text-[var(--ink)]" data-k="qAsk">{q.ask}</div>
        <div className="mt-1 text-xs text-[var(--ink-2)]" data-k="qBecause">{md(q.because)}</div>
        {/* ★`term` 是 `null` 时**只是不链过去**，这一条照渲染 ——
            "图谱里没有这个词"不是"这个问题不该问"。 */}
        {q.term ? <div className="mt-1"><Term t={q.term} /></div> : null}
      </div>
    </Well>
  )
}

/** 一条"判不了的"。★这一层是**设计不是欠账**：`why` 说的是"我缺哪个事实"。 */
function CannotRow({ c }: { c: InspectCannot }) {
  return (
    <Well className="mb-2">
      <div data-k="cannot" data-term={c.term}>
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-sm font-semibold text-[var(--ink)]" data-k="cannotTerm">{c.term}</span>
          {/* ★`from` **可能没有**（种子里就没有这个键）—— 缺了它这一条照渲染：
              出处是加分项，"哪件事判不了 + 为什么"才是这一条的成立条件。 */}
          {c.from ? <span className="text-[10.5px] text-[var(--ink-3)]" data-k="cannotFrom">{c.from}</span> : null}
        </div>
        <div className="mt-1 text-xs text-[var(--ink-2)]" data-k="cannotWhy">{md(c.why)}</div>
      </div>
    </Well>
  )
}

/** 对照基准。★这一块是**这一页的骨**：它把"任何声称能预测涨跌的建议"和
 *  本站自己那份实测摆在一起 —— 我们自己的模型也做不到，所以谁说得斩钉截铁，
 *  谁就更可疑。数字**一个都不在这里写死**（后端读产物现给）。 */
function Baseline({ b }: { b: InspectBaseline }) {
  const ci = b.model_ci95
  const rows: [string, React.ReactNode, React.ReactNode][] = [
    ["本站模型方向命中率", b.model_hit_rate === null || b.model_hit_rate === undefined
      ? "—" : `${num(b.model_hit_rate, 1)}%`,
      ci && ci.length === 2 ? `95% 置信区间 ${num(ci[0], 1)}% – ${num(ci[1], 1)}%` : "后端没给区间"],
    ["无脑喊涨（基线）", b.always_up === null || b.always_up === undefined ? "—" : `${num(b.always_up, 1)}%`,
      "什么都不看、每天喊涨的命中率"],
    ["动量跟随（基线）", b.momentum === null || b.momentum === undefined ? "—" : `${num(b.momentum, 1)}%`,
      "顺着上一段走势喊的命中率"],
    ["起点数", b.n_origins === null || b.n_origins === undefined ? "—" : num(b.n_origins, 0),
      "这次回测用的样本量"],
    ["测于", b.measured_at || "—", "回测产物里记的时刻"],
  ]
  return (
    <div data-k="baseline">
      {/* ★两个数**并排**摆 —— 只摆模型那个数的话，49.6% 看着像个成绩；
          把它和 50.6% 摆在一起，才看得出"统计上分不开"。 */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map(([label, v, sub]) => (
          <Well key={String(label)}>
            <div className="text-[10.5px] leading-tight text-[var(--ink-3)]">{label}</div>
            <div className="tnum text-base text-[var(--ink)]">{v}</div>
            <div className="mt-0.5 text-[10.5px] text-[var(--ink-3)]">{sub}</div>
          </Well>
        ))}
      </div>
      {b.why ? <p className="mt-2 text-xs text-[var(--ink-2)]" data-k="baselineWhy">{md(b.why)}</p> : null}
    </div>
  )
}

/** "该看哪几个词"的一条。★`terms` 是**来源核对**用的：这一条是从哪些真词条推出来的。 */
function ReadRow({ it }: { it: InspectReadNext }) {
  return (
    <div className="mb-2" data-k="readnext">
      <a href={GRAPH} title={`在知识图谱里搜「${it.ask}」`}
         className="text-sm font-semibold text-[var(--accent)] no-underline hover:underline">
        {it.label}
      </a>
      {it.terms?.length ? (
        <span className="ml-2 text-[10.5px] text-[var(--ink-3)]">
          来源词条：{(it.terms ?? []).join("、")}
        </span>
      ) : null}
    </div>
  )
}

/** 四个**量点容器**（`#insPromise` / `#insFlags` / `#insExempt` / `#insEmpty`）
 *  在没有结论的那三档里共用的那一句话。
 *
 *  ★★**为什么这四个容器"始终渲染"、而不是"有结论才画"**（这是探针契约要的形状）：
 *    容器条件渲染的话，量到的高度是 0 时**有两种解释** ——
 *    "引擎说这一格是空的" 与 "整个报告根本没画出来"。
 *    判据红了也说不清是哪一种，而这两种的修法完全相反（改引擎 / 改渲染）。
 *    容器常在，0 就只有一种解释。**"红的时候只有一种解释"是这一条的全部理由。**
 *  ★三档措辞必须是**三句不同的话**（不许合成一句"没有数据"）：
 *    `busy`  = 正在体检（上一次的结论已经被清掉了，见 `load`）；
 *    `fail`  = 这一轮没拿到结论（原因在 `Degraded` 那一块，这一句只说"没有结论"）；
 *    否则    = 还没体检过（用户还没贴过东西 —— 这不是"坏了"，是"还没开始"）。
 *  ★它**不**替引擎说话：`empty_reason` 那句人话是后端给的，只有后端给了才显示（见 `#insEmpty`）。 */
function NotYet({ busy, fail }: { busy: boolean; fail: boolean }) {
  const s = busy ? "正在体检这一段……结论还没出来。"
    : fail ? "这一轮没拿到结论（原因在上面那一块）。"
      : "还没体检过 —— 在上面贴一段原文，点「体检一下」。"
  return <p className="text-sm text-[var(--ink-3)]" data-k="notYet">{s}</p>
}

export function Inspect({ probe = false }: { probe?: boolean }) {
  // ── ★★加载：**同步**读本机缓存，直接落进 `useState` 的**初值** ─────────────
  //
  //   ★**为什么是同步的**（这一条是本仓库栽过的坑）：探针在**同一个 tick** 里量几何，
  //     异步回来的东西它量到的是"正在体检…" —— 那不是判据变红，是判据**消失**
  //     （报告里两种形状长得一模一样）。所以这一段**绝不能**写进 `useEffect`：
  //     SSR、以及探针的 `flushSync` 那一帧，都**不跑 effect**。
  //   ★**为什么读缓存而不是现场发请求**：这一页的输入是**用户粘的**，探针不会打字
  //     ⇒ 不读缓存的话"有结论那一屏"在结构上就量不到（`inspect.ts` 里
  //     「本机缓存」那一段写了整件事，包括"服务端一个字节都不存"这条边界）。
  //   ★★**探针路径和普通路径走的是同一个渲染**：下面没有一处 `if (probe)` 决定画什么。
  //     `probe` 只影响 `main.tsx` 播不播种；画出来的是同一份东西。
  //     普通路径下读缓存的好处是白捡的：用户刷新一下，刚花时间换来的那份报告还在。
  const boot = React.useMemo(() => ({ resp: getInspectSync(), text: getInspectTextSync() }), [])

  const [text, setText] = React.useState("")
  const [resp, setResp] = React.useState<InspectResp | null>(() => boot.resp)
  /** ★这一屏是不是**从本机缓存画出来的**。它决定 `#insFromCache` 那条横幅在不在 ——
   *  用户必须分得清"这是刚贴的这段的结论"和"这是上一次留下来的结论"：
   *  两屏长得一模一样，而它们的**输入**可能完全不相干（`#iText` 甚至是空的）。
   *  ★探针读的是**横幅那个元素在不在**（读 DOM），不是下面这个状态位 ——
   *    "状态位对了、横幅没画"这种坏法，只有读 DOM 才看得见。 */
  const [fromCache, setFromCache] = React.useState(() => !!boot.resp)
  const [fail, setFail] = React.useState<Fail | null>(null)
  /** ★**送出去的那一段**（不是 textarea 里此刻那一段）。
   *  报告必须钉在**它自己那一份输入**上：textarea 允许改，改完之后不重新提交的话，
   *  屏上那份报告和框里那段文字就对不上了 —— 而两者都是"用户自己写的字"，
   *  页面上看不出来谁是谁。所以提交那一刻**冻结一份**，报告里印的是它。
   *  ★缓存那条路上，它读的是**另一个键**（`licai-inspect-text`）——
   *    种子/更早的响应可能只留了结论没留原文，那时它是 `null`，页面照样渲染
   *    （`#lookedAt` 会说"原文当时没记下来"）。 */
  const [submitted, setSubmitted] = React.useState<string | null>(() => (boot.resp ? boot.text : null))
  const [busy, setBusy] = React.useState(false)
  /** 空文本那道闸。★**不发请求**、也不走 `Degraded` ——
   *  "你还没贴东西"是**用户的一个动作没做完**，不是"通道有问题"，
   *  两者用同一块警告会把用户引到错的地方（去看后端）。 */
  const [needText, setNeedText] = React.useState(false)

  const load = React.useCallback((t: string) => {
    setBusy(true); setFail(null); setResp(null); setNeedText(false)
    setSubmitted(t); setFromCache(false)
    void getInspect(t)
      .then((r) => {
        if (r.ok) {
          setResp(r); setFail(null)
          // ★**只在这一处**写本机缓存：拿到了 `ok` 的结论那一刻。
          //  写进去的是**接口返回的原样 JSON**（`rememberInspect` 不加工）——
          //  判据往同一个键里写种子，形状必须同构，包一层壳它就认不出来了。
          //  ★写原文用的是**另一个键**（理由见 `inspect.ts`）。
          rememberInspect(r, t)
        } else { setResp(null); setFail(mkFail(r)) }
        setBusy(false)
      })
      .catch((e: unknown) => {
        setResp(null)
        setFail({ ok: false, kind: "offline", reason: "连不上本地服务", detail: String((e as Error)?.message || e) })
        setBusy(false)
      })
  }, [])

  /** 「忘掉上一次」：清本机那两个键 + 把这一屏退回"还没体检过"。
   *  ★清完必须**同步**把 state 也退回空态，否则屏幕上那份报告还在、而它已经没有出处了
   *    （刷新一下又没了 —— 那是最让人不信的一种不一致）。 */
  const forget = () => {
    forgetInspect()
    setResp(null); setSubmitted(null); setFromCache(false)
    setFail(null); setBusy(false); setNeedText(false)
  }

  const run = (t: string) => {
    if (!t.trim()) { setNeedText(true); return }
    load(t.trim())
  }

  const pr = resp?.promise
  const rates = pr?.rates ?? []
  const flags = resp?.flags ?? []
  const notes = resp?.exempt_notes ?? []
  const exempts = notes.filter((n) => n.kind === "exempt")
  const orphans = notes.filter((n) => n.kind !== "exempt")
  const questions = resp?.questions ?? []
  const cannots = resp?.cannot_verify ?? []
  const readNext = resp?.read_next ?? []

  return (
    <Frame
      probe={probe} title="建议体检"
      sub="别人给你的理财建议，先拿它过一遍 —— 这一页不给你建议，它检验你收到的那条"
      footExtra={<>这一屏的结论<b>是规则算出来的，不是大模型说的</b>；每一句红旗都带着你可以自己核的原文。</>}
    >
      <Panel id="inputPanel" title="把那条建议贴进来"
             note={<>通道 <code className="tnum">{INSPECT_CHANNEL}</code> · 结论来源 <b>规则</b></>}>
        <form onSubmit={(e) => { e.preventDefault(); run(text) }}>
          <label className="block text-xs text-[var(--ink-3)]" htmlFor="iText">
            微信群里转的、亲戚介绍的、理财经理发的 —— 原文粘进来，<b>不用改</b>（改了就等于替它修饰）
          </label>
          <textarea id="iText" rows={4} spellCheck={false}
                    value={text} onChange={(e) => setText(e.target.value)}
                    placeholder="例：这个项目年化 12%，保本保息，拉人头还有返利……"
                    className="mt-1 w-full rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1.5 text-sm text-[var(--ink)]" />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button id="iGo" type="submit" disabled={busy}
                    className="rounded border border-[var(--accent)] bg-[var(--card)] px-3 py-1.5 text-sm font-semibold text-[var(--accent)] disabled:opacity-40">
              {busy ? "体检中…" : "体检一下"}
            </button>
            <span className="text-xs text-[var(--ink-3)]">或者先看两个例子：</span>
            <button id="iEgPos" type="button" onClick={() => { setText(EG_POS); run(EG_POS) }}
                    className="rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1 text-xs text-[var(--ink-2)]">
              县域项目（正例）
            </button>
            <button id="iEgNeg" type="button" onClick={() => { setText(EG_NEG); run(EG_NEG) }}
                    className="rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1 text-xs text-[var(--ink-2)]">
              银行定期（负例：看豁免）
            </button>
          </div>
        </form>
        {needText ? (
          <p className="mt-2 text-xs text-[var(--warn-ink)]" id="needText" data-k="needText">
            还<b>没贴东西</b>。上面那个框里粘一段原文，再点「体检一下」——
            这一页不猜你想问什么。
          </p>
        ) : null}
        <p className="mt-2 text-xs text-[var(--ink-3)]">
          ★它<b>不判断"这是不是诈骗"</b> —— 那要有认定资格。它做的是：把这段话
          <b>承诺了什么</b>摆出来、把<b>文本特征明确的那几句</b>标出来、
          把<b>你该问的问题</b>列出来、以及<b>明说哪几件事它判不了</b>。
        </p>
      </Panel>

      {fail ? <Degraded channel={INSPECT_CHANNEL} fail={fail} what="建议体检" /> : null}
      {busy && !resp ? <Loading what="正在体检这一段" /> : null}

      {/* ★★**这一屏是从本机缓存画出来的**那条横幅（`#insFromCache`）。
          ★它为什么必须存在：这一页的输入是用户现场粘的，而缓存让"上一次的结论"
            和"刚贴的这段的结论"在屏幕上**长得一模一样** —— 上面那个框还可能是空的。
            不说清的话，用户会把一条旧结论当成对眼前这段文字的判决。
          ★探针读的就是**这个元素在不在**（`insFromCache`）：判据要的"这一屏是种子
            渲染出来的"是从 DOM 上量到的，不是问组件要的。 */}
      {fromCache ? (
        <div className="flex flex-wrap items-center gap-2 rounded border-2 border-[var(--warn-ink)] bg-[var(--paper)] p-3 text-sm text-[var(--ink-2)]"
             id="insFromCache" data-k="insFromCache" role="status">
          <span>
            <b>这是你上一次的体检结论</b>（从你自己这台机器的浏览器里读出来的，不是刚算的）
            —— 上面那个框现在是空的，要看新的一段就贴进去重新点一次。
          </span>
          <button id="iForget" type="button" onClick={forget}
                  className="ml-auto rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1 text-xs text-[var(--ink-2)]">
            忘掉上一次
          </button>
        </div>
      ) : null}

      {/* ★**看的是哪一段**。这块不是装饰：报告下面的每一个判断都挂在**这一段**上，
          而 textarea 里的字随时可以改。不印出来的话，"我明明改了它怎么还这么说"
          就变成一个说不清的问题（改完没重新提交）。 */}
      {resp ? (
        <div className="rounded border-2 border-[var(--rule)] bg-[var(--paper)] p-3 text-sm text-[var(--ink-2)]"
             id="lookedAt" data-k="lookedAt" role="status">
          <b>体检的是下面这一段</b>（贴进去的那一刻冻住的，改上面的框不会改它）：
          {submitted ? (
            <blockquote className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words border-l-2 border-[var(--rule)] pl-2 text-xs"
                        data-k="lookedAtText">
              {submitted}
            </blockquote>
          ) : (
            /* ★缓存里可能**只有结论、没有原文**（种子就是这么写的：判据只写 `licai-inspect-last`）。
              这时不许留一片空白、更不许编一段原文出来 —— 明说"原文当时没记下来"。
              这一格和"没有这一段"是两件事，措辞必须分开。 */
            <p className="mt-1 text-xs text-[var(--ink-3)]" data-k="lookedAtGone">
              原文当时没记下来（本机只留下了结论那一份）。
            </p>
          )}
          <div className="mt-1 text-xs text-[var(--ink-3)]">
            结论来源：<code>{resp.source || "—"}</code>
            {resp.source === "rule"
              ? <>（<b>规则</b>：每一条都能追到词条原文和触发字，模型不参与判定）</>
              : null}
          </div>
        </div>
      ) : null}

      {/* ── ★★第 0 块：**这一轮没检出什么**（整页的灵魂）──────────────────────
          ★★**后端给的那句 `empty_reason` 必须上屏**，而且**不能挂在任何别的分支里**：
            挂在"红旗为空"那个 else 里的话，后端一改判定条件它就被静默吞掉，
            而页面上看起来一切正常 —— 这正是"判据不是变红、是**消失**"的形状。
            所以它是**独立的一块**，容器 `#insEmpty` **始终渲染**。
          ★它有三档措辞（有红旗时是 `null`；「看见了但判成豁免」一档；
            「真的什么都没检出」一档），**三档都不在这里重写**，原样显示后端那句。
          ★★**这一格的各态，以及探针怎么分辨它们**（读形状，**不读高度**）：
              · 后端给了 `empty_reason` ⇒ 里面是 `Empty`（`#empty`）—— 探针读 `"empty"`；
              · 还没有结论 / 正在体检 / 没取到 ⇒ 里面是 `NotYet` —— 探针读 `"notYet"`；
              · 有结论、但这一轮后端没给（`null`）⇒ 里面是**看不见的** `noReason` 锚
                —— 探针读 `"noReason"`（见下面那段注释）。
            ★**高度分不出这几档**（`NotYet` 也有高度、而"故意空着"与"没画出来"
              高度都是 0），所以探针另有键 `insEmptyK` 读**形状**（`probe.ts` 里那段
              写了理由：只靠高度的话，一条"后端那句真的上屏了"的判据会在两档上
              读数逐字相同 ⇒ 恒绿）。高度仍然量（`insEmptyH`），它承担"容器在不在"。
          ★**不许**在任何一档里补一句"没检出红旗"之类的兜底话：那样这一格就恒有内容，
            高度和形状两个读数就都失去了分辨力。
          ★用 `Empty` 这个共享件：它有站内统一的 `#empty` 锚点和 `role="status"`，
            和"接口坏了"（`Degraded` / `#degraded`）是两个不同的盒子、两句不同的话。
            ★**全页只此一处 `Empty`**（`id="empty"` 全页唯一）：红旗那一块空的时候
              走它自己的一句 `<p>`，**不许再放一个 `Empty`** —— 重复 id 的 DOM 是坏的，
              而且探针正是拿 `#empty` 这个锚点在 `#insEmpty` 里认第一档的。 */}
      <div id="insEmpty" data-k="insEmpty">
        {resp?.empty_reason ? (
          <Empty what={plain(resp.empty_reason)}
                 hint={<>这一句是<b>后端给的原话</b>，说的是<b>「本站的规则没覆盖到」</b>，
                   不是<b>「这条建议是干净的」</b> —— 规则只认那几类文本特征，
                   换个说法的骗局它一样认不出来。</>} />
        ) : !resp ? (
          <NotYet busy={busy} fail={!!fail} />
        ) : (
          /* ★**有结论、但后端这一轮没给 `empty_reason`**（`checkup.py`：检出了东西
             就不给这句话）⇒ 这一格**真的什么都不画**，高度 ≈ 0。
             ★★可这个"空"是**故意的**，和"这一块没画出来"在高度上**逐字相同** ——
             所以留一个**看不见的锚**：`hidden` 的空 `<span data-k="noReason">`
             （零高度、不进布局、不进可读文本），探针靠它把这两件事分开
             （`insEmptyK` 的第四档）。**这是本页唯一一个只为探针存在的元素**，
             它值得存在的理由就是这一格：这里恰恰是"空得有理"与"空得可疑"
             最容易被读混的地方，而两者的修法正好相反。 */
          <span data-k="noReason" aria-hidden="true" className="hidden" />
        )}
      </div>

          {/* ── ① 它承诺了什么 ───────────────────────────────────────────
              ★容器 `#insPromise` **始终渲染**（探针契约）：没有结论时里面是 `NotYet`
                那一句，有结论时是下面这一堆。★"有结论但一个数都没抽到"和
                "根本没有结论"是**两句不同的话**，不许合成一句。 */}
          <Panel id="promisePanel" title="它承诺了什么"
                 note={resp ? "只陈述，不判罪 —— 判断在下面那两层做" : "还没有结论"}>
            <div id="insPromise" data-k="insPromise">
            {!resp ? (
              <NotYet busy={busy} fail={!!fail} />
            ) : rates.length || (pr?.words ?? []).length ? (
              <div data-k="promise">
                {rates.length ? (
                  <>
                    <div className="overflow-x-auto">
                      <table className="w-full border-collapse text-sm">
                        <thead>
                          <tr className="border-b border-[var(--rule)] text-xs text-[var(--ink-3)]">
                            <th scope="col" className="py-1.5 pr-3 text-left font-medium">原文怎么写的</th>
                            <th scope="col" className="py-1.5 pr-3 text-left font-medium">口径</th>
                            <th scope="col" className="py-1.5 pr-3 text-right font-medium">那个数</th>
                            <th scope="col" className="py-1.5 pr-3 text-right font-medium">折成年化</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rates.map((r) => (
                            <tr key={r.raw} data-k="rate">
                              <td className="py-1.5 pr-3 align-top text-[var(--ink)]" data-k="rateRaw">{r.raw}</td>
                              <td className="py-1.5 pr-3 align-top text-[var(--ink-2)]">{r.kind}</td>
                              <td className="tnum py-1.5 pr-3 align-top text-right text-[var(--ink-2)]">
                                {num(r.pct, 2)}%
                              </td>
                              <td className="tnum py-1.5 pr-3 align-top text-right font-semibold text-[var(--ink)]"
                                  data-k="rateAnnual">
                                {num(r.annual, 2)}%
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {/* ★**跨口径必须折算才比得了**：月息 1 分看着"才 1"，
                        折成年化是 12%。这一句是这一块存在的理由。 */}
                    <p className="mt-2 text-xs text-[var(--ink-2)]" data-k="rateWhy">
                      原文里每一种口径都折成了<b>年化</b>再摆在一起（月息、日息、几分利
                      都是这么折的）—— 不折的话「月息 1 分」看着比「年化 8%」小，
                      实际情况正好反过来。
                      {pr?.annual_max !== null && pr?.annual_max !== undefined ? (
                        <> 这段话里最高的一个：<b className="tnum">{num(pr.annual_max, 2)}%</b>（年化）。</>
                      ) : null}
                    </p>
                  </>
                ) : (
                  <p className="text-sm text-[var(--ink-3)]" data-k="noRate">
                    这一段里<b>一个收益率都没抽到</b>（不是"收益是 0"）——
                    它没给数，或者用的写法不在本站覆盖的那几种里。
                  </p>
                )}
                {(pr?.words ?? []).length ? (
                  <div className="mt-2 text-xs text-[var(--ink-2)]" data-k="words">
                    这段话里的「承诺词」：
                    {(pr?.words ?? []).map((w) => (
                      <code key={w} className="mr-1.5 rounded border border-[var(--rule)] bg-[var(--paper)] px-1"
                            data-k="word">{w}</code>
                    ))}
                    {/* ★这一句不能省：光看这几个词，用户会以为"保本"这个词本身就是罪证 ——
                        而它在银行存款上完全合法。那件事在下面「我们查了、这句不算」那一块。 */}
                    <div className="mt-1 text-[10.5px] text-[var(--ink-3)]">
                      这几个是<b>词</b>，不是判定 —— 同一个「保本」用的是不是合法，
                      要看它说的是什么东西（下面「我们查了、这句不算」那一块会分开说）。
                    </div>
                  </div>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-[var(--ink-3)]" data-k="promiseEmpty">
                这段话里既没有收益率，也没有那几个「承诺词」。
              </p>
            )}
            </div>
          </Panel>

          {/* ── ② 红旗 ───────────────────────────────────────────────────
              ★容器 `#insFlags` **始终渲染**，里面的 `[data-term]` 是探针逐字对账的锚：
                每个红旗**恰好一个** `data-term`，值就是引擎给的 `flags[].term` **原文**
                （不改写、不加序号、不用 `term-0` 这种 DOM id 顶替 —— 探针要拿它和
                `flags[].term` 做**逐字相等**的比较）。所以 `FlagRow` 里那个指向图谱的
                链接**不许**再带 `data-term`（带上的话一个红旗会匹配到两个节点）。
              ★没有结论时这里是 `NotYet`（而不是空），探针量到的 `insFlags` 是 `[]` ——
                `[]` 的含义是"量过了，确实没有红旗"，和"这个键没量到"（`null`）不同。 */}
          <Panel id="flagsPanel" title="红旗"
                 note={<>{
                   !resp ? "还没有结论"
                     : flags.length ? <>{flags.length} 条 · 每条都带你<b>能自己核</b>的原文</> : "一条都没有"
                 }</>}>
            <div id="insFlags" data-k="insFlags">
            {!resp ? (
              <NotYet busy={busy} fail={!!fail} />
            ) : flags.length ? (
              <div>{flags.map((f, i) => <FlagRow key={`${f.term}-${i}`} f={f} />)}</div>
            ) : (
              /* ★★"没检出红旗"**不等于**"这段建议是干净的"，这两句话在用户眼里长得
                 一模一样 —— 所以这一档**必须**把用户推向能解释它的那一格：
                 为什么没检出，是**后端那句 `empty_reason`** 说的，它在上面 `#insEmpty` 里。
                 ★这一档**不许再用 `Empty`**（`id="empty"` 全页只有一个，重复 id 的 DOM
                   是坏的），也就是不许在这里把后端那句话**再抄一遍**：
                   抄一遍就多了一个真源，后端改了措辞这里不会跟着改。 */
              <p className="text-sm text-[var(--ink-3)]" data-k="flagsNone">
                这一轮<b>没有检出一条红旗</b> —— 但这句话<b>不等于</b>「这段建议是干净的」。
                为什么没检出，看上面<b>「这一轮没检出什么」</b>那一格里的<b>后端原话</b>
                （规则只认那几类文本特征）。
              </p>
            )}
            </div>
          </Panel>

          {/* ── ③ 豁免（"我们查了、这句不算"）★这一页的灵魂 ────────────
              ★容器 `#insExempt` **始终渲染**（探针契约，`insExemptH` 量它）：
                种子/正例那一屏里有豁免 ⇒ 高度 > 0；没有豁免时里面是
                "这一轮没有需要除外的句子"那一句 ⇒ 依然 > 0（**空着也要显示**，
                见下）；没有结论时是 `NotYet`。三档都非空，是**故意**的：
                这一格的高度不该用来说"有没有豁免"，那件事看 `data-k="exempt"` 的行数。 */}
          <Panel id="exemptPanel" title="我们查了、这几句不算"
                 note={!resp ? "还没有结论"
                   : exempts.length ? "命中了字面，但词条本身给了除外" : "这一轮没有需要除外的句子"}>
            <div id="insExempt" data-k="insExempt">
            {!resp ? (
              <NotYet busy={busy} fail={!!fail} />
            ) : exempts.length ? (
              <div>{exempts.map((n, i) => <ExemptRow key={`${n.term}-${i}`} n={n} />)}</div>
            ) : (
              /* ★**空着也要显示**（不是 `null`）：用户得看见"这一块查过了、没有东西"，
                 而不是以为它没做出来。<b>只会报红的检查器等于一个恒真的告警。</b> */
              <p className="text-sm text-[var(--ink-3)]" data-k="exemptNone">
                这一轮里<b>没有"看着像红旗、其实不算"的句子</b>。
                有的话会显示在这里（例如银行存款说「保本」—— 那是词条原文允许的）。
              </p>
            )}
            {orphans.length ? <div className="mt-2">{orphans.map((n, i) => <OrphanRow key={`${n.term}-${i}`} n={n} />)}</div> : null}
            </div>
          </Panel>

          {/* ── 这里开始是**只有拿到结论才画**的四张卡（④⑤⑥⑦）───────────
              ★它们和上面那四块的区别不是重要程度，而是**探针契约里没有它们的量点**：
                上面那四块（`#insEmpty`/`#insPromise`/`#insFlags`/`#insExempt`）
                要"容器常在"是因为各有判据量它们的高度；这四张没有量点，
                再让它们在没有结论时也占着屏幕就只是噪声了。
              ★这一条分界**只由"有没有量点"划**，别拿它当"哪些块更重要"来读。 */}
          {resp ? (
            <>
          {/* ── ④ 该问的问题 ───────────────────────────────────────────── */}
          <Panel id="questionsPanel" title="该问它什么"
                 note={questions.length ? `${questions.length} 条 · 这不是罪证，是问题` : "这一轮没有问题要问"}>
            {questions.length ? (
              <div>{questions.map((q, i) => <QRow key={`${q.ask}-${i}`} q={q} />)}</div>
            ) : (
              <p className="text-sm text-[var(--ink-3)]" data-k="qNone">
                没有到该追问的量级（后端按词条自己举过的例子当锚点，不拍一个阈值）。
              </p>
            )}
          </Panel>

          {/* ── ⑤ 判不了的 ─────────────────────────────────────────────── */}
          <Panel id="cannotPanel" title="这几件事它判不了"
                 note={cannots.length ? `${cannots.length} 条 · 每一条都写清了缺哪个事实` : "这一段没提到要查资质的话题"}>
            {cannots.length ? (
              <div>{cannots.map((c) => <CannotRow key={c.term} c={c} />)}</div>
            ) : (
              <p className="text-sm text-[var(--ink-3)]" data-k="cannotNone">
                没提到要查牌照/资质/工商那一类话题（提到了才会列在这里 ——
                每次都说一遍"我什么都判不了"会变成一段废话墙，把真红旗淹掉）。
              </p>
            )}
            {/* ★这一句是这一块的立场，不能删：**判不了 ≠ 没问题**。
                两句话在用户眼里经常被读成同一句，而它们之间差着一次立案调查。 */}
            <p className="mt-2 text-xs text-[var(--ink-2)]" data-k="cannotNote">
              ★<b>这里的「判不了」和上面的「没检出红旗」是两件事。</b>
              判不了 = 要看牌照、工商穿透、合同底层，而<b>本站没有那些数据源</b> ——
              硬判就是编。所以它列出来，让你自己去查。
            </p>
          </Panel>

          {/* ── ⑥ 对照基准 ─────────────────────────────────────────────── */}
          {resp.baseline ? (
            <Panel id="baselinePanel" title="拿什么对照"
                   note="★这一段提到了预测/涨跌，所以把本站自己那份实测摆出来">
              <Baseline b={resp.baseline} />
              <p className="mt-2 text-xs text-[var(--ink-2)]">
                ★这条基准是<b>本站自己的模型</b>的实测数字（Kronos-small，回测产物里读出来的，
                不重算）。把它摆在这里的理由很直白：<b>连它自己都做不到</b> ——
                所以谁的措辞越斩钉截铁（"下个月肯定涨"），谁越可疑。
              </p>
            </Panel>
          ) : null}

          {/* ── ⑦ 该看哪几个词 ─────────────────────────────────────────── */}
          <Panel id="readNextPanel" title="该看哪几个词"
                 note="从上面命中的词条推的 · 点进去是知识图谱">
            {readNext.length ? (
              <div>{readNext.map((it, i) => <ReadRow key={`${it.ask}-${i}`} it={it} />)}</div>
            ) : (
              <p className="text-sm text-[var(--ink-3)]" data-k="readNone">
                这一轮没有推出来的词条（上面没有命中任何词条时会是这一态）。
              </p>
            )}
            <p className="mt-1 text-[10.5px] text-[var(--ink-3)]">
              ★链接落在知识图谱页（<code>{GRAPH}</code>）上，<b>没有带词名片段</b> ——
              那一页不支持按片段跳转，带上只是个空跳转，看着像做了、其实没做。
              落地之后在它的搜索框里搜那个词就行。
            </p>
          </Panel>
        </>
      ) : null}

      <Panel id="notePanel" title="这一页不做什么">
        <Well>
          <ul className="list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
            <li><b>不替你判"这是不是诈骗"。</b>那要有认定资格。它做的是把文本特征、
              该问的问题、以及判不了的部分分开摆出来。</li>
            <li><b>不给投资建议、不推荐任何产品。</b>这一页从头到尾没有一个"你该买什么"。</li>
            <li><b>判定不走大模型。</b>结论是<b>规则</b>算的（<code>source=rule</code>），
              每条都能追到词条原文和触发字 —— 换一次模型结论不会变，
              而且模型不能给自己发免责声明。</li>
            <li><b>不因为"没检出"就说你安全。</b>规则只认那几类文本特征；
              换个说法的骗局它一样认不出来 —— 空态那句话说的是"没覆盖到"，
              不是"干净"。</li>
            <li><b>不存你贴的东西。</b>这一段只在这一次请求里过一遍，服务端不落盘。</li>
          </ul>
        </Well>
      </Panel>
    </Frame>
  )
}
