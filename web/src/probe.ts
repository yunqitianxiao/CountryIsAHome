/**
 * 渲染层探针：把**实测几何**送回 /api/probe。
 *
 * 纪律（继承自旧版 index.html，不是新发明）：
 *   1. 只读 getBoundingClientRect / scrollWidth / textContent，**不读状态位**
 *      —— `classList.contains('up')` 那种查法，把 CSS 一删类名照样在，会全绿放行。
 *   2. 量**高度**而不是文本：`display:none` 时 textContent 照样返回全文。
 *   3. 必须在同一个同步 tick 里量完（见 main.tsx 的注释）。
 *
 * ★**字段名是扁平的一份契约**，由 verify_agent.py 的 render_probe() 逐字读取：
 *   `cards` / `cardRects` / `chatH` / `overflowX` / `svg` / `msgs` / `trace` /
 *   `chips` / `ansLen` / `hasBold` / `chartCaps` / `repairNote` /
 *   `newsH` / `newsItems`（★资讯块：`#newsBox` 的高度 + 逐条的
 *   `{title, src, heat, ts, href, linked, h}`。缺元素时是 `0` 与 `[]` ——
 *   两者都要，理由见 `probeAndBeacon` 里那一段：只有 `newsItems` 的话，
 *   "整块被 CSS 藏起来"与"事件被丢弃"长得一样。）/
 *   `guideHref` / `graphHref` / `agentHref` / `gameHref` / `portfolioHref` /
 *   `ledgerHref` / `inspectHref` / `dashHref` / `profileHref` / `backHref`
 *   （★这几个是 **href 字符串**，不是布尔。主页那四张导航卡要同时钉住"卡在"和
 *   "指向哪儿"两件事 —— 只判"有个 id 叫 guideCard 的元素"的话，把 href 改成 `#`
 *   或者改到别的页，判据照样绿。判据那边比的是**全等**。
 *   `backHref` 是子页页头那个回主页的链接 —— 主页上**不渲染**它，落回 ""。）/
 *   `siteLeadH` / `siteLeadMiss`（★首页那段"不发建议，只检验"的立场块：
 *   **高度** + **缺了哪几条否定承诺**。`siteLeadMiss` 是**数组**，而量不到那一段时
 *   回传的是 **`null`、不是 `[]`** —— `[]` 会被下游读成"三条都在"，
 *   那是把"没量到"折成"通过"。探针读的那三个短语在 `Home.tsx` 里各只出现一次，
 *   注入的锚正是照这个选的。）/
 *   `ripple`（点击反馈那一簇，见文件末尾）/
 *   `homeStrip`（主页行情条那一簇，见文件末尾：逐行的可见文本 + `polyline` 的
 *   点数/不同 y 的个数/描边色，外加条子自身的高度与两个涨跌 token）/
 *   `accText` / `accH` / `featText` / `featH` / `predMarks` /
 *   `predMarkText` / `predMarkH` / `predCardH` / `predChart` / `predNote` /
 *   `predNoteH` / `predSpreadH` / `predSpreadText` / `predBandPts` / `predBandH` /
 *   `predDir`（★后五个是阶段 3 的多采样：区间块的高度与文案、图上那条带子的
 *   点数与高度、方向徽章的文案。`predBandPts` 为 `-1` 表示**压根没有那个元素**，
 *   与"有元素但 0 个点"分开 —— 这两种坏法的修法不一样。）/
 *   `mainW` / `docW` / `mainL` / `mainCW` / `barL` / `barCW` /
 *   `bannerText` / `bannerH` / `bannerTop` / `foldH` / `keyCardH` /
 *   `keyCardText` / `keyStateText` / `keyStateH` / `keyInputH` / `keyWarnText` /
 *   `keyClickVal` / `keySaveMethod` / `keySaveBody` /
 *   `quick` / `quickText` / `quickBoot` / `quickLabel` / `quickAsk` /
 *   `chatKeys0` / `chatProf0` / `chatKeys1` / `chatProf1`（★「那一枪带没带画像」。
 *   下标 `0` = 浏览器里**没存**画像时点的那一下，`1` = 塞了一份之后点的同一下。
 *   两个都要有：只有 `1` 的话，"把画像写死成一段常量"照样绿。）
 *   `sysDark` / `bootTheme` / `bootThemeAttr` / `bgBoot` / `bgNoAttr` / `bgDark` /
 *   `bgLight` / `kpiColorBoot` / `kpiColorNoAttr` / `kpiColorDark` / `kpiColorLight` /
 *   `upNoAttr` / `downNoAttr` / `paperNoAttr` / `cardNoAttr` / `upDark` / `downDark` /
 *   `paperDark` / `cardDark` / `upLight` / `downLight` /
 *   `gridDark` / `gridLight`（★`{n, stroke}`：这一跑画了几条 `line.k-grid`、
 *   它们算出来的 stroke 是什么。配 `gridCardDark`/`gridCardLight` 用来算"网格
 *   和它脚下的卡片底色差多少" —— 守的是"网格被某个 token 改动悄悄抹掉"。）
 *   `accentColDark` / `accentColLight` / `accentTextDark` / `accentTextLight`
 *   （★`--accent` 与"落在 accent 上的那些元素实际用的字色/底色"，**按主题各一份**。
 *   为什么必须是两份：见 `themeSnapshot` 上面那段 —— 单份的那一版量到的是
 *   "页面此刻恰好是哪个主题"，而默认深色之后亮色那半条**永远绿**。）/
 *   `swLabels` / `swInit` / `swLight` / `swDark`（★主题那几格：`boot` = 页面**自己
 *   启动成**的样子、`NoAttr` = 真的 `removeAttribute` 之后（系统那条来源）、
 *   `Dark`/`Light` = 手动钉住。★它们原来叫 `Sys` —— 站点改成默认深色之后那个名字
 *   就**变成假的了**（那时它量的是应用自己钉的 dark，跟系统无关），所以连同判据一起
 *   改名，不留旧名：不改名的后果不是判据变红，是判据悄悄换了被测对象而报告照旧。
 *   `sw*` 是主题开关**按钮**点下去之后的同步读数，见下面那一段注释。）/
 *   `dashText` / `dashTextH` / `dashRows` / `dashCmpH`（总览页 /dash 那四条，
 *   在主页面上落回空 —— 同一份探针服务两页，别给 /dash 单开一个文件）。
 *   `portText` / `portTextH` / `portRows` / `portSyms` / `portMetricsText` / `portMetricsH` /
 *   `portDroppedH` / `portDroppedText` / `portFailText` / `portFailH` /
 *   `portCorrH` / `portGradesH` / `portNotesText` / `portCorrTone` / `portStressCaveat` /
 *   `portDD`（回撤曲线：`{n, ys[], stroke, w, h, worst}` 或 `null`）
 *   （组合体检页 /portfolio。★前缀是 `port` 不是 `pf` —— `pf` 已经被**画像页**占了
 *   （`pfForm`/`pfSavedBefore`/`pfSavedAfter`），两页共用一个前缀迟早会读串。）
 *   `lgWhereText` / `lgWhereH` / `lgSrcs` / `lgTexts` / `lgEmptyTexts` /
 *   `lgHits` / `lgBases` / `lgNumTexts` / `lgCovText` / `lgBadText` /
 *   `lgNoBlockText` / `lgBlocksH` /
 *   `lgCI`（命中率**误差棒**：逐块、和 `lgSrcs` 同序，
 *   `{w, h, lo, hi, mid, rate, stroke}` 或 `null`。
 *   ★`null` = 这一块**没有棒**（"该有而没有"也是这个值，判据自己去分辨是哪种）；
 *     `lo: null` = 有元素但量不出坐标 —— 两种坏法的修法不一样，所以分开。）
 *   （预测台账页 /ledger。★`lgTexts` / `lgHits` / `lgBases` / `lgEmptyTexts`
 *   都是**逐块**的、和 `lgSrcs` 同序 ——
 *   判据要拿它跟同一份台账在进程内算出来的数逐字比。比"页面上有个百分比"
 *   强得多：写死一个 66.7% 也能过那种。）
 *   `bootPath` / `bootSearch` / `obSeen` / `routeGuard` / `profileEcho`
 *   （路由与"第一次来送去引导页"那几条。★`routeGuard` 是**四组输入的真值表**，
 *   不是布尔 —— 无头浏览器里 localStorage 永远是空的，"看过了就不跳"那一半
 *   在页面里**走不到**，只回传一个布尔等于只判了半个函数。）
 *   `obSteps`（引导页每一步的快照数组，`null` = 这页上没有引导）
 *   `obForm` / `obSeenAfter` / `obDoneTitle` / `obFormGone`
 *   `pfForm` / `pfSavedBefore` / `pfSavedAfter`（画像页那几条，
 *   `pfForm` 的字段见底部 `formProbe()`）。
 *   `g`（模拟投资页那一簇：`stakeVal` / `profAssets` / `recCount` / `recSyms` /
 *   `hasNums` / `err` / `pctCount` / `buy` / `shares` / `last` / `value` /
 *   `pnl` / `pnlPct` / `amtShown` / `buyDate` / `posRows` / `posErrRows` /
 *   `profInterests`。别的页面上是 `null`。
 *   ★`profAssets` 是 `stakeVal` 那条的**对照**：没有它，
 *   "本金框是空的"在"画像根本没塞进去"时也成立，那条判据就是空转）。
 *   `fundCardH` / `fundName` / `fundRows` / `fundNoneBars` / `fundFills` /
 *   `fundSpark` / `fundCap` / `fundCapH` / `fundLegend` / `upCol` / `downCol`
 *   （估值分位卡那一簇。★`fundFills` 是**成对**读回来的：每行
 *   `{label, pct, val, rng, ratio, color}` —— `ratio` 是**实测**的填充/轨道宽度比。
 *   判据拿它比 `分位/100`，于是"数字→宽度"这条链断了一定会红。）
 *   改成嵌套（`{bar: {l, cw}}` 而不是 `barL`/`barCW`）**不会打红任何东西** ——
 *   判据读到的是 `None`，而 `None > 20` 是 False……**但** `data.get('barCW', 0) > 0`
 *   这类拿默认值兜底的判据会安静地判假，红是红了、理由却指向别处。
 *   所以字段名照抄，别重排。
 *
 * ★这里**故意不读 `#badges` 里有什么**：见下面 featBadge 那段。
 *
 * ★**建议体检页（`/inspect`）那一簇**：`insPromiseH` / `insFlags` /
 *   `insExemptH` / `insEmptyH` / `insEmptyK` / `insFromCache`。
 *   · `insPromiseH` / `insExemptH` / `insEmptyH` 是三个容器的 `offsetHeight`
 *     （`#insPromise` / `#insExempt` / `#insEmpty`）。★这三个容器在页面上
 *     **始终渲染**（没有结论时里面是一句"还没体检过"）：容器条件渲染的话，
 *     量到 0 时有两种解释 ——"引擎说这一格是空的"和"整块没画出来"，
 *     而这两种的修法正好相反。容器常在，0 就只有一种解释。
 *     ★★但**高度分不出"画的是哪一档"** —— 见 `insEmptyK`。
 *   · `insEmptyK` 是**字符串**，读的是 `#insEmpty` 里画的是哪一档（**形状，不是高度**）：
 *     `"empty"` = 里面是 `Empty`（后端那句 `empty_reason` 上屏了）、
 *     `"notYet"` = 里面是 `<p data-k="notYet">`（还没有结论/正在体检/没取到）、
 *     `"noReason"` = 有结论但这一轮后端**没有** `empty_reason`（**种子那一屏就是这档**，
 *                   里面是一个看不见的 `data-k="noReason"` 锚，高度 ≈ 0）、
 *     `null` = **连 `#insEmpty` 都不在**、
 *     `""` = 容器在但四样锚一个都没有（**坏法**，判据应为它红）。
 *     ★它是"哪一档"的**唯一**证据：`insEmptyH > 0` 在好几档上**逐字相同**
 *     （`NotYet` 也有高度）⇒ 拿高度当"后端那句话真的上屏了"的判据是**恒绿**的
 *     （本仓库 ⑪/㉜ 那种形状：量一个所有情况下都相同的值）。
 *     `insEmptyH` **保留**（"容器在不在、有没有高度"仍值得量），只是不再由它
 *     承担"画的是哪一档"。
 *     `insEmptyH` **保留**（"容器在不在、有没有高度"仍值得量），只是不再由它
 *     承担"画的是哪一档"。
 *   · `insFlags` 是 `#insFlags [data-term]` 逐个读回来的 **`data-term` 原值**数组，
 *     判据拿它跟规则引擎的 `flags[].term` **逐字全等**比（顺序也照 DOM 顺序 ——
 *     那就是引擎给的顺序）。★和 `portSyms` 同一条纪律：比**代码**不比条数。
 *     ★**没有容器时给 `null`**（不是 `[]`）：`null` = 这一跑根本没量到这一页
 *     （探针压根没跑在 `/inspect` 上 / 这一版还没有那块），
 *     `[]` = 量到了、确实一条红旗都没有。这两种的修法不一样，所以必须分开。
 *   · `insFromCache` 是**布尔**：`#insFromCache` 那条横幅在不在。它说的是
 *     "这一屏是从 `localStorage`（`licai-inspect-last`）渲染出来的" ——
 *     判据先往那个键里写一份正例 JSON、再打开页面，量到的就是**有结论那一屏**。
 *     （这一页的输入是用户现场粘的，探针不会打字 ⇒ 不这么做的话
 *     "有结论那一屏"在结构上量不到。播种见 `probeSeedInspect`。）
 *   ★三个地址（判据的输入，别删）：
 *       `?probe=1`              播**正例**种（有红旗、有豁免、`empty_reason` 是 null）
 *       `?probe=1&empty=1`      **不播种** ⇒ 真空态（localStorage 是空的）
 *       `?probe=1&nohit=1`      播**第二份**种 ⇒ 有结论但一条红旗都没检出，
 *                               那一屏才量得到后端第二档那句"灵魂话"
 *                               （见 `probeSeedInspectNoHit` 顶上那段）
 */
import { flushSync } from "react-dom"
import { DEFAULT_QUICK } from "./quick"
import { clickAt } from "./ripple"
import { ALL_OPTS, PROFILE_KEY, hasSeenOnboarding, shouldAutoOpenOnboarding } from "./profile"
// ★种子里写的是 `INSPECT_KEY` 这个**常量**（不从页面那边引一个字符串字面量）：
//   键名只有一处真源。判据那边是 Python 里的字面量 `licai-inspect-last` ——
//   两边一旦分叉，页面读不到种子 ⇒ `insFromCache` 是 false、判据**红**，
//   而不是安静地量一片空白。分叉是响的，可以接受。
import { INSPECT_KEY } from "./inspect"

type Rec = Record<string, unknown>

/** ★**点一下，并且等 React 真的把这一下画完**。
 *
 *  这个函数存在的唯一理由，是这台机器上**实测**出来的一件事（不是推测）：
 *
 *  ```
 *  nb.click();                            → 读 #obStepNo 还是「第 1 / 5 步」
 *  dispatchEvent(new MouseEvent("click")) → 同上
 *  flushSync(() => clickAt(nb, x, y))     → 读到「第 5 / 5 步」 ✓
 *  ```
 *
 *  React 18（`createRoot`）把点击引起的状态更新**记在同步车道上、
 *  在微任务里提交**。探针整段跑在**同一个 tick** 里、跑完立刻 beacon ——
 *  微任务要等这个 tick 结束才轮到，所以 **DOM 永远不会在探针读它之前更新**。
 *  更阴的是：handler **确实跑了**（实测计数 14 次），只是没画。
 *  于是"点了没反应"和"React 还没来得及画"在探针眼里长得一模一样。
 *
 *  ★为什么以前没撞上：Key 卡和「问问看」那两块也是点完立刻读 ——
 *    但它们读的是**发出去的请求体**（`fetch` 在 handler 体内同步发生，
 *    跟 React 提交不提交毫无关系）。"点完读 DOM"这条路，一直到引导页
 *    才第一次有人走。⇒ 凡是**点完之后要读 DOM** 的地方，一律走这个函数。
 *
 *  ★`flushSync` 在这里是**正当用法**，不是绕路：它的语义就是
 *    "把待处理的更新立刻刷掉，我要马上看结果"。包住 click 而不是点完再调，
 *    是为了让顺序无条件正确。
 */
function tap(e: Element | null): void {
  if (!e) return
  const r = e.getBoundingClientRect()
  flushSync(() => {
    // 用元素自己中心点派发**带坐标的**真 MouseEvent：波纹那条 document 监听
    // 只认有坐标的点击（坐标 0 会被当成探针自己的合成事件而跳过）。
    clickAt(e, Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2))
  })
}

const el = (s: string) => document.querySelector(s)
const els = (s: string) => Array.from(document.querySelectorAll(s))
const box = (e: Element | null) => (e ? e.getBoundingClientRect() : null)
/** 高度：判据一律拿它比阈值 —— **读产物不读状态位** */
const h = (e: Element | null) => {
  const r = box(e)
  return r ? Math.round(r.height) : 0
}
/** 去掉**所有**空白（不是把连续空白折成一个空格）—— 判据按去空白后的样子比。 */
const txt = (e: Element | null) => (e ? (e.textContent || "").replace(/\s+/g, "") : "")

function beacon(o: Rec) {
  const w = window as unknown as { __beacon?: (o: Rec) => void }
  if (typeof w.__beacon === "function") w.__beacon(o)
}

/** 把一个 CSS 变量的**最终颜色**解析成 `rgb(...)`。
 *  ★为什么要真建一个元素：`getComputedStyle(root).getPropertyValue('--up')` 返回的是
 *    作者写的原文（`#c0392b`），而元素上量到的是 `rgb(192, 57, 43)` —— 两种写法
 *    在字符串上永远不相等，判据会**恒绿**（或恒红）。让浏览器解析一次才可比。
 *  ★为什么用 `color` 而不是 `background`：只设颜色、不塞内容，这个 span 的盒子是
 *    0×0，拔掉之后不可能影响任何布局测量。 */
function tokenColor(name: string): string {
  const d = document.createElement("span")
  d.style.color = `var(${name})`
  document.body.appendChild(d)
  const c = getComputedStyle(d).color
  d.remove()
  return c
}

export function probeAndBeacon() {
  const out: Rec = {}

  // ── 卡片：数量 + 前 5 张的实际几何 ──
  const cards = els(".card")
  out.cards = cards.length
  out.cardRects = cards.slice(0, 5).map((c) => {
    const r = c.getBoundingClientRect()
    return {
      cls: (c as HTMLElement).className,
      w: Math.round(r.width),
      h: Math.round(r.height),
      t: Math.round(r.top),
      l: Math.round(r.left),
    }
  })

  const mn = el("main")
  const chat = el(".chat")
  out.chatH = h(chat)

  const de = document.documentElement
  out.overflowX = de.scrollWidth - de.clientWidth

  // ── ★首页立场块（`#siteLead`："这个站不发理财建议 —— 它检验你收到的那条"）──
  //   `Home.tsx` 里那段话的注释自己写着"措辞哪一天被改软了，得有一条判据看得见，
  //   而不是靠人记得"。这一对键就是那条判据的取数口。
  //   ★**高度只证明"这一段画出来了"**，证明不了"它还在说那三件不做的事" ——
  //     所以还要读它**说了什么**，那是 `siteLeadMiss`。两条判据分工不同。
  //   ★`siteLeadMiss` 回传的是**缺了哪几条**，不是命中几条：数组空 = 三条都在。
  //     命中数是个读数（文案加一句就变），"缺哪条"才是能指认、能报给人看的结论。
  //   ★量不到这一段时回传 **`null`，不是 `[]`** —— `[]` 会被下游读成"三条都在"，
  //     那是把"没量到"折成"通过"（本仓库反复踩过的坑：回退值必须与被测的真值可分辨）。
  //   ★三个短语在 `Home.tsx` 里**各只出现一次**：`check_fault_anchors` 靠这个把注入
  //     打进那段话里 —— 别处再写一遍同一句（比如引导页），注入的锚就烂了。
  const siteLead = el("#siteLead")
  out.siteLeadH = h(siteLead)
  {
    const txt = siteLead
      ? ((siteLead as HTMLElement).innerText || siteLead.textContent || "")
      : null
    out.siteLeadMiss = txt === null ? null
      : ["不预测涨跌", "不推荐任何产品", "不替你判"].filter((s) => txt.indexOf(s) < 0)
  }

  // ── ★对话里的那张图 ──
  //   必须限定在 `#msgs` 里找。预测卡排在对话卡**前面**，用
  //   `document.querySelector('.chart svg')` 会取到预测卡那张 ——
  //   于是"对话里的图"这条判据改去量预测卡，对话图整个坏掉它也照样绿。
  const svg = el("#msgs .chart svg")
  out.svg = svg
    ? {
        w: Math.round(svg.getBoundingClientRect().width),
        h: Math.round(svg.getBoundingClientRect().height),
        candles: svg.querySelectorAll("rect.k-body").length,
        wicks: svg.querySelectorAll("line.k-wick").length,
        pred: svg.querySelectorAll("g.pred rect.k-body").length,
      }
    : null

  out.msgs = els(".msg").length
  out.trace = els(".trace").length
  out.chips = els(".tchip").length
  // 正文真的落进气泡里了吗（流式 token 有没有被拼起来）——
  // 只看"有 .msg 元素"是不够的：空气泡也满足那个条件。
  const body = el(".msg.ai .body")
  out.ansLen = body ? (body.textContent || "").replace(/\s+/g, "").length : 0
  out.hasBold = !!(body && body.querySelector("strong"))
  out.chartCaps = els("#msgs .chart .cap").length

  // ── ★资讯块（`get_news` 那条数据通道画出来的）──────────────────────────
  // ★量的是**渲染出来的**文本与几何，不是源码里有没有 `#newsBox`。
  //   这正是这条缺陷的形状：`news` 事件在服务端发得出、前端没有消费它的分支，
  //   落穿到 `applyEvent` 末尾被**静默丢弃** —— 源码里那个组件一直都在，
  //   只是没人渲染它。所以判据只能落在产物上。
  // ★`newsH` 缺元素时是 **0**（`h()` 的约定）；`newsItems` 缺元素时是 **[]**。
  //   两者都要有：只有 `newsItems` 的话，「整块被 CSS 藏起来」照样是空的、
  //   与「事件被丢掉」长得一样；而只有 `newsH` 的话，"有个空壳子"也满足 > 0。
  // ★每条的 `heat` / `ts` / `src` **分开读**（不拼成一句话再读）：
  //   判据要拿 `heat` 跟服务端那份数据对，拼成一串之后"同题 1 源"里的 1
  //   是算出来的还是写死的就分不出了。
  const nb = el("#newsBox")
  out.newsH = h(nb)
  out.newsItems = els("#newsBox .newsitem").map((n) => {
    const a = n.querySelector("a.newstitle")
    return {
      title: txt(n.querySelector(".newstitle")),
      src: txt(n.querySelector(".nsrc")),
      heat: txt(n.querySelector(".nheat")),
      ts: txt(n.querySelector(".nts")),
      /** 有原文链接时是那条 URL；**没有就是 ""**（这条渲染成不可点的 span）。 */
      href: a ? a.getAttribute("href") || "" : "",
      /** 有没有把标题渲染成**可点的 `<a>`**。 */
      linked: !!a,
      h: Math.round(n.getBoundingClientRect().height),
    }
  })

  // ── ★工具轨迹画成时间线了吗（`.trace`）──────────────────────────────
  // ★竖轨与站点是**伪元素**画的（`ChatCard.tsx` 那条硬契约：判据数 `.msg` /
  //   `.trace` / `.tchip` 的**个数**，加节点等于让那些判据空转）。
  //   所以这一块**不能**复用 `box()` —— `getBoundingClientRect()` 在伪元素上
  //   根本不存在，唯一的读法是 `getComputedStyle(el, '::before')`。
  // ★`content` 必须与 `'none'`/`'normal'` 比：没写这条规则、或写了
  //   `content: none` 时**不生成盒子**，那时 `width`/`height` 报的是 `auto` ——
  //   拿 `parseFloat('auto')` 得到 `NaN`，说"轨宽 1px"就是在编。
  // ★★**坐标系**：伪元素的 `left` 是相对它**自己的包含块**的 —— 轨相对
  //   `.rows` 的 padding box，点相对 `.row` 的 padding box。两个数**不能直接比**：
  //   `.rows` 有 `padding-left: 28px`，直接比会恒定差出 28px ⇒ 判据**恒红**。
  //   这里把两者都折进 `.rows` 边框盒的坐标系，并且直接把**圆心 / 轨心**算好
  //   交给判据 —— 让判据自己去加坐标，正是这个地方最容易加错的。
  const trows = el(".trace .rows")
  const trow = els(".trace .row")
  const _cs = (e: Element | null, p?: string) => (e ? getComputedStyle(e, p) : null)
  const _px = (s: string | undefined) => {
    const v = parseFloat(s || "")
    return Number.isFinite(v) ? Math.round(v * 100) / 100 : null
  }
  const _live = (c: CSSStyleDeclaration | null) =>
    !!c && c.content !== "none" && c.content !== "normal"
  const _rail = _cs(trows, "::before")
  const _dot = _cs(trow[0] || null, "::before")
  const _rb = box(trows)
  const _wb = box(trow[0] || null)
  // 点自己的 `left` 要加上"它的包含块在 `.rows` 里偏了多少"（= 行左 − 容器左）
  const _shift = _rb && _wb ? _wb.left - _rb.left : null
  const _railL = _rail ? _px(_rail.left) : null
  const _railW = _rail ? _px(_rail.width) : null
  const _dotL = _dot ? _px(_dot.left) : null
  const _dotW = _dot ? _px(_dot.width) : null
  out.trRows = trow.length
  out.trRowsH = _rb ? Math.round(_rb.height) : 0
  out.trRail = _live(_rail) ? {
    on: true, w: _railW, h: _rail ? _px(_rail.height) : null,
    cx: (_railL !== null && _railW !== null) ? _railL + _railW / 2 : null,
  } : { on: false, w: null, h: null, cx: null }
  out.trDot = _live(_dot) ? {
    on: true, w: _dotW, r: _dot!.borderTopLeftRadius, bg: _dot!.backgroundColor,
    cx: (_dotL !== null && _dotW !== null && _shift !== null)
      ? Math.round((_shift + _dotL + _dotW / 2) * 100) / 100 : null,
  } : { on: false, w: null, r: "", bg: "", cx: null }
  // 横线还在不在（时间线不该长得像表格）。读**用值**，不是源码里写了什么。
  out.trRule = (() => {
    const r = _cs(trow[0] || null)
    return r ? (_px(r.borderBottomWidth) ?? -1) : -1
  })()
  // 层次：名字最亮、参数最暗。两个都读算出来的 rgb。
  out.trNmCol = (() => { const n = el(".trace .nm"); return n ? getComputedStyle(n).color : "" })()
  out.trArCol = (() => { const a = el(".trace .ar"); return a ? getComputedStyle(a).color : "" })()
  out.trNmMono = (() => {
    const nm = el(".trace .nm")
    return nm ? getComputedStyle(nm).fontFamily : ""
  })()
  // 状态那一格（`.st`）——「成功/失败扫一眼分得出来」。
  out.trSt = (() => {
    const s = el(".trace .st")
    if (!s) return null
    const g = getComputedStyle(s)
    return { col: g.color, bw: _px(g.borderTopWidth), br: g.borderTopLeftRadius }
  })()

  // ★说明条里有没有**写明预测段的高/低点是重建的**。
  //   量的是渲染出来的文字，不是源码里有没有那个函数 —— 读产物不读输入：
  //   函数在、没被调到，等于没写。
  const cap = el("#msgs .chart .cap")
  const capT = cap ? cap.textContent || "" : ""
  out.repairNote = !!(cap && /重建/.test(capT) && /最高\/最低价/.test(capT))

  // ── ★估值分位卡（`get_fundamentals` 那条数据通道画出来的）──
  //   ★这张卡的判据核心不是"有没有卡"，而是「**条子宽度是算出来的**」：
  //     只判"有条子"是不够的 —— 写死一个宽度、或者把数字和宽度接错线，
  //     条子照样在、卡照样高。所以每行的 **分位文本** 和 **实测填充比**
  //     成对读回来，判据比 `ratio ≈ 分位/100`；这条链断了必红。
  //   ★`color` 读的是**算出来的**颜色（getComputedStyle → rgb(...)），
  //     不是源码里写了什么类名 —— 判据要拿它跟 `--up`/`--down` 的**分辨率**
  //     比（见 `upCol`/`downCol`），证明分位卡一个涨跌色都没用。
  const fund = el("#msgs .fundcard")
  out.fundCardH = h(fund)
  out.fundName = txt(el("#msgs .fundcard .fname"))
  out.fundRows = els("#msgs .fundcard .frow").length
  out.fundNoneBars = els("#msgs .fundcard .fbar.none").length
  out.fundFills = els("#msgs .fundcard .frow").map((r) => {
    const track = r.querySelector(".fbar")
    const fill = r.querySelector(".fbar-fill")
    const tw = track ? track.getBoundingClientRect().width : 0
    const fw = fill ? fill.getBoundingClientRect().width : 0
    return {
      label: txt(r.querySelector(".flabel")),
      pct: txt(r.querySelector(".fpct")),
      val: txt(r.querySelector(".fval")),
      rng: txt(r.querySelector(".frange")),
      tw: Math.round(tw),                      // 轨道宽：判据按它换算容差（见 verify_agent）
      fw: Math.round(fw),
      ratio: tw > 0 ? +(fw / tw).toFixed(4) : 0,
      color: fill ? getComputedStyle(fill as HTMLElement).backgroundColor : "",
    }
  })
  const sp = el("#msgs .fundcard .fspark-line")
  const spd = sp ? sp.getAttribute("d") || "" : ""
  out.fundSpark = sp
    ? {
        h: h(el("#msgs .fundcard .fspark")),
        pts: (spd.match(/[ML]/g) || []).length,
        len: spd.length,
      }
    : null
  out.fundLegend = txt(el("#msgs .fundcard .fspark-legend"))
  out.fundCap = txt(el("#msgs .fundcard .cap"))
  out.fundCapH = h(el("#msgs .fundcard .cap"))

  // ★把两个涨跌 token **算成真实颜色**。判据要拿它跟分位条实测的颜色比。
  //   直接读 `getPropertyValue('--up')` 得到的是作者写的原文（"#c0392b"），
  //   跟实测的 "rgb(192, 57, 43)" 比不出东西 —— 必须让浏览器自己解析一次。
  //   ★放在**所有几何量完之后**：临时插的这个 span 不能影响上面任何一次测量。
  out.upCol = tokenColor("--up")
  out.downCol = tokenColor("--down")
  // ★第三档也解析出来（`--ink-3`，`text-flat` 用的那一档）。
  //   不解析它的话，`/game` 那条"盈亏真的按红涨绿跌画出来了"在
  //   **盈亏正好为 0** 的那一局判不了 —— 那时正确的颜色是个中性色，
  //   而判据手边只有 up/down 两个值可比，只能跳过，跳过就等于没判。
  out.flatCol = tokenColor("--ink-3")
  // ★第四档：`--ink`（正文最亮那一档）。工具轨迹那一行要求"名字比参数亮"，
  //   判据拿它跟 `.nm` 实测的颜色比 —— 仍然是"算出来的 rgb"，不是源码里写了什么。
  out.inkCol = tokenColor("--ink")

  // ★这几个链接是**水合之后**还在 DOM 里 —— 比旧版判"服务端 HTML 里有"更强
  //   （旧版那条是读输入，这条读的是跑完 JS 的产物）。
  //
  // ★**必须带上卡片自己的 id**，光写 `a[href="graph.html"]` 是个会空转的判据：
  //   对话里的术语 chip 也是指向 graph.html 的 `<a>`（见 ChatCard 的 `.tchip`），
  //   demo 模式下一定渲染得出来。于是把入口卡的链接改坏之后，chip 仍然匹配 ——
  //   判据照样绿，而用户点那张卡是坏的。
  //   （旧版没踩到这个坑，是因为它读的是**服务端 HTML**，而 chip 是 JS 运行时建的。
  //    换成读水合后的 DOM，判据变强了，同时这个空转口子也一起露出来了。）
  //
  // ★这里读的是 **href 的值**，不是"元素在不在"。判据那边比全等 ——
  //   把 `guideCard` 的 href 从 `/onboarding` 改成 `#` 或者 `/agent`，
  //   元素照样在、`!!el(...)` 照样是 true，而用户点下去到不了该去的地方。
  const hrefOf = (sel: string) => {
    const a = el(sel)
    return a ? a.getAttribute("href") || "" : ""
  }
  out.guideHref = hrefOf("a#guideCard")
  out.graphHref = hrefOf("a#graphCard")
  out.agentHref = hrefOf("a#agentCard")
  out.gameHref = hrefOf("a#gameCard")
  out.portfolioHref = hrefOf("a#portfolioCard")
  out.ledgerHref = hrefOf("a#ledgerCard")
  // ★建议体检那张卡（`/inspect`）。同形：回传 **href 字符串**，判据比全等 ——
  //   只看"元素在不在"的话，把 href 改成 `#` 照样绿，而用户点下去哪儿都到不了。
  out.inspectHref = hrefOf("a#inspectCard")
  // ★`/dash` 那张卡（2026-09-22 补进导航区）。它和上面那几条同形：探针回传
  //   **href 字符串**，判据拿它跟 `/dash` 全等比 —— 只判"元素在不在"的话，
  //   把 href 改成 `#` 照样绿。★读的是 `a#dashCard`（锚点）：`/dash` 那一页
  //   自己也有一个 `id="dashCard"` 的 `<section>`，锚点把它挡在外面。
  out.dashHref = hrefOf("a#dashCard")
  out.profileHref = hrefOf("a#profileCard")
  // ★子页页头那个回主页的链接。主页自己**不渲染**它（一个指向自己的链接是死链的
  //   一种），所以这一条在主页上恒为 ""，判据只在子页那一侧量。
  out.backHref = hrefOf("a#homeLink")

  // ── ★风险提示里那句实测数字：量**几何**和渲染出来的**文字**两样 ──
  //   只量文字是不够的：把这块 CSS 藏起来（display:none）时 textContent
  //   照样返回全文，于是一条"用户根本看不见"的页面会判绿。
  const acc = el("#disclaim .acc-line")
  out.accText = txt(acc)
  out.accH = h(acc)
  // ★诊断（大屏上 accH 只有 16px，比它自己的 padding+border 还高不了多少）。
  //   判据本身在宽屏那一档是绿的，但"高 16px 装下两行字"物理上不可能 ——
  //   与其猜，不如把这一块每一层的盒子都读回来。
  if (acc) {
    const cs = getComputedStyle(acc as HTMLElement)
    const inner = acc.querySelector(".acc")
    out.accDiag = {
      fs: cs.fontSize,
      lh: cs.lineHeight,
      disp: cs.display,
      ovf: cs.overflow,
      padT: cs.paddingTop,
      borT: cs.borderTopWidth,
      marT: cs.marginTop,
      innerH: inner ? Math.round(inner.getBoundingClientRect().height) : -1,
      innerW: inner ? Math.round(inner.getBoundingClientRect().width) : -1,
      scrollH: (acc as HTMLElement).scrollHeight,
      clientH: (acc as HTMLElement).clientHeight,
      sib: acc.parentElement ? Math.round(acc.parentElement.getBoundingClientRect().height) : -1,
      nLines: inner ? inner.getClientRects().length : -1,
    }
  }

  // ── ★「K线预测」三处标称，每处都量几何，不量源码里有没有那个标签 ──
  //   ★页头那枚：先**真的演一遍**状态徽章那排的破坏动作再量。
  //     探针模式不联网、状态那排压根没渲染，不演这一下的话"它扛得住重建"
  //     就是句空话 —— 把它挪进 #badges 里也照样绿。演完再看它还在不在。
  //     （React 那边不是用 innerHTML 重建的，是靠 reconciler 换 #badges 的孩子；
  //      但**效果是同一个**：featBadge 不是 #badges 的孩子，所以活得下来。
  //      这里用 innerHTML 清空来模拟，是对"挪进去就会死"这件事的直接检验。）
  const bbox = el("#badges")
  if (bbox) bbox.innerHTML = ""
  const fb = el("#featBadge")
  out.featText = txt(fb)
  out.featH = h(fb)

  // ★预测回答上的打标：大模型的话和模型的输出在同一个气泡里，
  //   不打标就分不出哪句是模型采出来的。
  const pm = el(".predmark")
  out.predMarks = els(".predmark").length
  out.predMarkText = txt(pm)
  out.predMarkH = h(pm)

  // ── ★独立预测卡：卡片本身、它画出来的图、它自带的那条风险提示 ──
  const pc = el("#predCard")
  out.predCardH = h(pc)
  const psvg = el("#pout .chart svg")
  out.predChart = psvg
    ? {
        w: Math.round(psvg.getBoundingClientRect().width),
        h: Math.round(psvg.getBoundingClientRect().height),
        candles: psvg.querySelectorAll("rect.k-body").length,
        pred: psvg.querySelectorAll("g.pred rect.k-body").length,
      }
    : null
  // ★这条通路**不经过大模型**，那句风险提示没有别人替它说。
  //   卡的是 #pout 里那块 .disclaim，不是卡片正文的小字 ——
  //   正文那句 hint 是**没跑之前**就在的，拿它当判据等于空转。
  const pnote = el("#pout .disclaim")
  out.predNote = !!(pnote && (pnote.textContent || "").indexOf("不构成投资建议") >= 0)
  out.predNoteH = h(pnote)

  // ── ★多采样：区间块（`.spread`）、图上那条带子（`path.k-band`）、弃答徽章 ──
  //   ★三样都读**几何/产物**，不读源码里有没有那段 JSX：
  //     · `.spread` 量高度 —— 它挂在外层 `<div className="spread">` 上；
  //     · 带子量 `path` 的 d 属性里有多少个点（`-1` = 压根没有这个元素）；
  //     · 徽章量**文案**（弃答那一枚必须写"方向弃答"，不许还写着"偏多"）。
  //   ★这一块在探针模式下是真的画出来的：`fakePredict()` 返回的就是
  //     N=9 + 弃答那一态。不这么造的话，判据会在一个从没渲染过的东西上判绿。
  const psp = el("#predCard .spread")
  out.predSpreadH = h(psp)
  out.predSpreadText = txt(psp)
  const pband = psvg ? (psvg.querySelector("path.k-band") as SVGPathElement | null) : null
  out.predBandPts = pband ? (pband.getAttribute("d") || "").split("L").length : -1
  out.predBandH = pband ? Math.round(pband.getBoundingClientRect().height) : -1
  // ★带子的**用户坐标**包围盒。它守的是"区间并进了纵轴刻度"这件事：
  //   没并进去的话带子的两条边会落到绘图区外面（svg 的视口把它裁掉，
  //   页面上看到的是"带子只剩半截"），而 `getBoundingClientRect` **量不出裁剪**
  //   —— 裁剪只影响渲染、不影响几何。`getBBox()` 给的是未裁剪的用户坐标，
  //   跟 viewBox 的 0..H 一比就露馅了。
  const bb = pband ? pband.getBBox() : null
  out.predBandBox = bb ? { y: Math.round(bb.y), h: Math.round(bb.height) } : null
  out.predDir = txt(el("#pdir"))

  // ── ★布局有没有跟着屏幕长（用户原话"太小了"）：主栏实际宽度 vs 视口宽度 ──
  out.mainW = mn ? Math.round(mn.getBoundingClientRect().width) : 0
  out.docW = de.clientWidth

  // ── ★「人人可用」定位条 ──
  //   ★"说明"这件事只有一种可测的形式：**它在折叠线以上**。源码里写了、
  //   或者塞在页脚，都算不得说明。所以量 top，判据拿视口高比 ——
  //   写死像素值的话，下次改页面就得连判据一起改，等于没判。
  const bn = el("#siteBanner")
  if (bn) {
    const r = bn.getBoundingClientRect()
    out.bannerText = txt(bn)
    out.bannerH = Math.round(r.height)
    out.bannerTop = Math.round(r.top)
  } else {
    out.bannerText = ""
    out.bannerH = 0
    out.bannerTop = -1
  }
  out.foldH = window.innerHeight // "折叠线"就是视口高，不写死

  // ── ★大模型 Key 卡 ──
  //   ★keyCardText 是**整张卡的文字**，判据拿它去找"完整 key 有没有漏出来"。
  //   只断言"有个 mask 字段"是不够的：那样拦不住"顺手把原文也带上"。
  const kc = el("#keyCard")
  if (kc) {
    out.keyCardH = h(kc)
    out.keyCardText = kc.textContent || ""
    const ks = el("#kstate")
    out.keyStateText = txt(ks)
    out.keyStateH = h(ks)
    out.keyInputH = h(el("#kinput"))
    out.keyWarnText = txt(el("#kwarn .kwarn"))
  } else {
    out.keyCardH = 0
    out.keyStateH = 0
    out.keyInputH = 0
    out.keyCardText = ""
    out.keyStateText = ""
    out.keyWarnText = ""
  }

  // ── ★页头和主栏对不对齐 ──
  //   量的是**内容框**（border box 减去左右内边距）：页头那排的内边距在 .bar 上、
  //   主栏的在 main 上，两边的 border box 一样宽并不代表内容对得齐。
  out.barL = -1
  out.barCW = 0
  out.mainL = 0
  out.mainCW = 0
  const bar = el("header .bar")
  if (bar && mn) {
    const cb = (e: Element) => {
      const r = e.getBoundingClientRect()
      const cs = getComputedStyle(e as HTMLElement)
      const l = r.left + parseFloat(cs.paddingLeft || "0")
      return { l: Math.round(l), w: Math.round(r.right - parseFloat(cs.paddingRight || "0") - l) }
    }
    const b1 = cb(bar)
    const b2 = cb(mn)
    out.barL = b1.l
    out.barCW = b1.w
    out.mainL = b2.l
    out.mainCW = b2.w
  }

  // ── ★行情总览（/dash）────────────────────────────────────────────────
  //   这一页的判据是"页面上的数就是接口给的数"：判据自己打一遍 /api/kline，
  //   再要求下面这段**渲染出来的文本**里逐字含那几个数。
  //   ⇒ 所以量的是 textContent（去掉空白），不是"有没有那张表"。
  //   ⇒ 同时量高度：把 `display:none` 一加，文本照样在，用户却什么都看不见。
  //   ★主页面上这几个元素不存在，全部落回 0/"" —— 于是同一条判据在主页面上
  //     判的是"这页没有总览卡"（对的），在 /dash 上判的是"数对不对"。
  const dt = el("#dashTable")
  out.dashText = txt(dt)
  out.dashTextH = h(dt)
  out.dashRows = els("#dashTable tr.dash-row").length
  out.dashCmpH = h(el("#dashCmp"))
  // ★走势列与涨跌色：**逐行**读。`/dash` 上最容易的坏法是"表里数都对，
  //   走势那一列画成一条平线"或"涨跌一律灰的" —— 两个都不影响文本，
  //   只读 `dashText` 的判据一条都抓不住。
  //   涨跌色读的是 `[data-k="chg5"]` 的**计算色**（不是类名）：类名是状态位，
  //   有人把 `.text-up` 的规则改掉，类名照样在、颜色已经不是红的了。
  out.dashSparks = els("#dashTable tr.dash-row").map((n) => {
    const pl = n.querySelector('[data-k="spark"] polyline') as SVGPolylineElement | null
    const sv = n.querySelector('[data-k="spark"]') as SVGSVGElement | null
    const pts = pl ? (pl.getAttribute("points") || "").trim().split(/\s+/).filter(Boolean) : []
    const sr = sv ? sv.getBoundingClientRect() : null
    // ★三个涨跌格**分开**报，不合成一个"颜色对不对"的布尔：判据要能说清
    //   "是 5 日那格错了还是 60 日那格错了" —— 合成一个的话，红了的理由是
    //   "有一格颜色不对"，读日志的人还得自己去翻是哪一格。
    return {
      sym: (n as HTMLElement).dataset.sym || "",
      n: pts.length,
      yKinds: new Set(pts.map((p) => (p.split(",")[1] || "").trim())).size,
      stroke: pl ? getComputedStyle(pl).stroke : "",
      // ★同 `homeStrip`：走势线自己的宽高。`h={0}` 那种坏法只有几何会露。
      sparkW: sr ? Math.round(sr.width) : 0,
      sparkH: sr ? Math.round(sr.height) : 0,
      chgs: ["chg5", "chg20", "chg60"].map((k) => {
        const c = n.querySelector('[data-k="' + k + '"]') as HTMLElement | null
        return { k, text: c ? (c.textContent || "").trim() : "",
                 color: c ? getComputedStyle(c).color : "" }
      }),
    }
  })

  // ── ★组合体检（/portfolio）──────────────────────────────────────────
  //   和 /dash 同一个形状：判据自己打一遍 `/api/portfolio`，再要求这段
  //   **渲染出来的文本**里逐字含那几个数（集中度/HHI/EDR/Beta/周数）。
  //   ⇒ 量 textContent，不是"有没有那张表"。
  //   ★另外三样是这一页**独有**的纪律，各自要有个落点：
  //     · `portDroppedH`：取不到的那几只必须**点名**。量高度不量文本 ——
  //       "明写了原因"和"悄悄少一行"在纯文本上可能都是"没有那几只"。
  //     · `portFailText`：**算不了的那一屏**。判据要的是"那屏上一个数字都没有"，
  //       所以这里把它整段文本回传出去给判据数百分号。
  //     · `portCorrTone`：相关矩阵用的是**中性色**。读数格子算出来的
  //       background-color，判据要求它**不等于** `--up`/`--down` 那两色。
  //   ★主页面上这些元素都不存在，全部落回 0/""。
  const pt = el("#pfTable")
  out.portText = txt(pt)
  out.portTextH = h(pt)
  out.portRows = els("#pfTable tr.pf-row").length
  // ★逐字比**代码**，不比行数。行了 `tr.pf-row` 那个 class，行数对得上，
  //   里面写的是谁却没人看 —— 把取不到的那只也列进来、或者列成别的标的，
  //   行数一个不差。判据拿它跟 `/api/portfolio` 回的 `weights[].symbol` 全等比。
  out.portSyms = els("#pfTable tr.pf-row").map((r) => r.getAttribute("data-sym") || "")
  out.portMetricsText = txt(el("#pfMetrics"))
  out.portMetricsH = h(el("#pfMetrics"))
  out.portDroppedH = h(el("#pfDropped"))
  out.portDroppedText = txt(el("#pfDropped"))
  out.portFailText = txt(el("#pfFail"))
  out.portFailH = h(el("#pfFail"))
  out.portCorrH = h(el("#pfCorr"))
  out.portGradesH = h(el("#pfGrades"))
  out.portNotesText = txt(el("#pfNotes"))
  const cell = el("#pfCorrTable td")
  out.portCorrTone = cell ? getComputedStyle(cell).backgroundColor : ""
  const st = el("#portStressCaveat")
  out.portStressCaveat = txt(st)

  // ── ★建议体检页（`/inspect`）那五个读数 ─────────────────────────────
  //   ★`insFlags` 的回退值**故意是 `null` 不是 `[]`**（理由见抬头那一段）：
  //     这一版量不到这一页 ⇒ `null`；量到了、确实没有红旗 ⇒ `[]`。
  //   ★`data-term` 读的是**原值**：判据拿它和引擎的 `flags[].term` 逐字比，
  //     所以这里不许加序号、不许截断、不许去空白。
  //   ★隔壁页面上这些元素都不存在，全部落回 0 / false / null。
  out.insPromiseH = h(el("#insPromise"))
  out.insExemptH = h(el("#insExempt"))
  out.insEmptyH = h(el("#insEmpty"))
  // ── ★`insEmptyK`：**这一格画的是哪一档**（读形状，不读高度）──────────────
  //   ★为什么必须有它：`#insEmpty` 是"容器常在、内容按状态填"，而
  //     "后端那句 `empty_reason` 上屏了"和"还没有结论（`NotYet`）"**都有高度** ⇒
  //     `insEmptyH > 0` 在两档上**逐字相同**，拿它当判据是「恒绿验空气」
  //     （量到一个所有情况下都相同的值，结构上测不出它声称测的那条轴）。
  //     判据要的是"哪一档"，那就得回传**是哪一档**。
  //   ★四值（＋一个"坏法"值，见下）——**四档各有一个正向的锚**，缺一样就读不出来：
  //       "empty"     —— 里面是共享件 `Empty`（`#empty`）⇒ 后端那句人话上屏了；
  //       "notYet"    —— 里面是 `<p data-k="notYet">`（还没有结论 / 正在体检 / 没取到）；
  //       "noReason"  —— 里面是**看不见的** `<span data-k="noReason">`：有结论、
  //                      但这一轮后端没给 `empty_reason`（检出了东西就不给）⇒
  //                      这一格**故意空着**、高度 ≈ 0。
  //                      ★这一档必须单独有名字：把它并进 `"notYet"` 的话，
  //                        **种子那一屏和真空那一屏读数就一样了** —— 那正是
  //                        这个键被加进来要修的毛病（⑪ 恒绿）；并进 `null` 又是在
  //                        撒谎（容器在）。
  //       null        —— **连 `#insEmpty` 都不在**（这一版压根没画那一格）。
  //       ""          —— **容器在、四样锚一个都没有** ⇒ 这是**坏法**，不是状态：
  //                      判据该为它红。降级成上面任何一个值都会把它读成一件正常的事。
  //   ★找的锚是页面注释里已经写明的那几个：`Empty` 全站统一的 `#empty` 锚点
  //     （这一页只有一处 `Empty`）、`NotYet` 与"故意空着"各自的 `data-k`。
  //     不求最省，求的是"这个锚和渲染那一档的组件是同一份契约"。
  const insE = el("#insEmpty")
  out.insEmptyK = !insE ? null
    : insE.querySelector("#empty") ? "empty"
      : insE.querySelector("[data-k='notYet']") ? "notYet"
        : insE.querySelector("[data-k='noReason']") ? "noReason"
          : ""
  const insF = el("#insFlags")
  out.insFlags = insF ? els("#insFlags [data-term]").map((e) => e.getAttribute("data-term") || "") : null
  out.insFromCache = !!el("#insFromCache")

  // ── ★回撤曲线（`#pfDD`）──────────────────────────────────────────────
  //   ★判据要的是**几何 + 每个点的 y + 描边色 + 那一格的字**，四样缺一不可：
  //     · 点数与 `y` 数组：判据自己从 `/api/portfolio` 的 `series.nav` 再算一遍
  //       回撤，逐点比 —— 比"有没有画出来"强得多（平的、画反的都能过那种）。
  //       所以这里回的是**每个点的 y**，不是"极差有多大"：极差要给个阈值
  //       （多小算平？），而"哪个点的 y 最大"这件事没有阈值可挑。
  //     · `w`/`h`：`h={0}` 那类坏法**只有几何会露**（点数、y、颜色全对）——
  //       迷你走势那条注入实测就漏过一次，教训写在那儿。
  //     · `stroke`：回撤是亏，该用跌色 —— 读**计算色**，不读 `var(--down)` 这串字。
  //     · `worst`：那一格的**可见文本**，判据按它比那个数和日期。
  //   ★元素不在就落 `null`（不是 `{}`）：`{}` 会让"没量到"和"量到了但是空的"
  //     在报告里长得一样，而这两种坏法的修法不一样。
  out.portDD = null
  const ddel = el("#pfDD")
  if (ddel) {
    const dpl = ddel.querySelector('[data-k="ddLine"]') as SVGPolylineElement | null
    const dsv = ddel.querySelector("svg")
    const dpts = dpl ? (dpl.getAttribute("points") || "").trim().split(/\s+/).filter(Boolean) : []
    const dr = dsv ? dsv.getBoundingClientRect() : null
    out.portDD = {
      n: dpts.length,
      ys: dpts.map((t) => (t.split(",")[1] || "").trim()),
      stroke: dpl ? getComputedStyle(dpl).stroke : "",
      w: dr ? Math.round(dr.width) : 0,
      h: dr ? Math.round(dr.height) : 0,
      worst: txt(ddel.querySelector('[data-k="ddWorst"]')),
    }
  }

  // ── 预测台账（/ledger）────────────────────────────────────────────────
  //   ★这一页的数据在**服务端**（data/predictions.jsonl），localStorage 里没有 ——
  //     所以探针不能靠"播种 localStorage"造内容，只能靠 `verify_agent.py` 起测试
  //     服务时往那份**隔离台账**里种（见 `ledger_seed_lines`）。不种的话页面是空的，
  //     而"空台账"和"页面坏了"在报告里长得一样：所有渲染判据一起落 0。
  //
  //   三样东西各自要个落点：
  //     · `lgSrcs`：**分区**。live 和 backfill 必须各是一块 —— 混成一个数的话，
  //       "台账战绩"几乎全部由离线回测的旧窗口决定，而它看起来像在线成绩。
  //       所以这里回传的是每块的 `data-source`，判据拿它做**集合**比对，
  //       不是判"页面上有没有 live 这两个字"。
  //     · `lgTexts`：逐块整段文本。判据要拿它和**同一份台账**在进程内算出来的数
  //       逐字比 —— 比"页面上有个百分比"强得多：写死一个 66.7% 也能过那种。
  //     · `lgCovText`：覆盖率那一栏。★判据要的是"**没有**数字"，
  //       所以这里把整段回传出去给判据数百分号（同 `portFailText` 那条的形状）。
  out.lgWhereText = txt(el("#lgWhere"))
  out.lgWhereH = h(el("#lgWhere"))
  out.lgSrcs = els(".lg-block").map((n) => n.getAttribute("data-source") || "")
  out.lgTexts = els(".lg-block").map((n) => txt(n))
  out.lgEmptyTexts = els(".lg-block .text-sm, .lg-block p[id^='lgEmpty-']").map((n) => txt(n))
  // ★命中率与基线**逐块成对**量出来（和 `lgSrcs` 同序），分成两个数组：
  //   判据要判的是"每一块的命中率旁边都跟着一个基线，而且两个数**不相等**"。
  //   合成一个整段文本再去正则捞，就分不出"这块的基线"和"那块漏了基线"了。
  out.lgHits = els(".lg-block [data-k='hit']").map((n) => txt(n))
  out.lgBases = els(".lg-block [data-k='base']").map((n) => txt(n))
  // ★**数字格子**（命中率/基线/样本数/区间四条），不是整段。
  //   "还没到期那一屏一个数字都不许有"这条判据**不能**拿整段文本去数百分号：
  //   那一屏自己的说明文字里就写着「显示 0% 会被读成"算出来是 0"」——
  //   整段一扫，判据**永远红**，而红的原因是它读到了散文，不是页面上有数字。
  //   ⇒ 量"格子"，不量"段落"。（本仓库那条纪律：判据读到的必须是
  //     被测的那件东西，不是旁边那句话。）
  out.lgNumTexts = els(".lg-block [data-k='hit'], .lg-block [data-k='base'], "
    + ".lg-block [data-k='n'], .lg-block [data-k='ci']").map((n) => txt(n))
  out.lgCovText = els(".lg-block [data-k='coverage']").map((n) => txt(n)).join(" ｜ ")
  out.lgBadText = txt(el("#lgBad"))
  out.lgNoBlockText = txt(el("#lgNoBlock"))
  out.lgBlocksH = els(".lg-block").map((n) => h(n))

  // ★误差棒（`CIBar`）：区间是**画出来**的，所以回传的必须是**几何坐标** ——
  //   判据拿它跟同一份台账里 `wilson()` 算出来的 `ci95` 比。
  //   ★位置一律从 `x1`/`x2` **属性**读、再除以 viewBox 宽度，
  //     **不量渲染出来的包围盒**：那三条线都带 `vectorEffect="non-scaling-stroke"`，
  //     包围盒会被描边撑开，端点偏半个线宽，而判据的容差是 0.02（= 12px 里的 0.24px）。
  //   ★`null`（整块没有棒）和 `lo: null`（有元素但量不出坐标）**分开**：
  //     前者是"该画没画"，后者是"画了但读不出来"，两种坏法的修法不一样。
  out.lgCI = els(".lg-block").map((b) => {
    const svg = b.querySelector("[data-k='ciBar']")
    if (!svg) return null
    const W = parseFloat(((svg.getAttribute("viewBox") || "").split(/\s+/))[2] || "")
    if (!W) return { w: 0, h: 0, lo: null, hi: null, mid: null, rate: null, stroke: "" }
    const at = (k: string) => {
      const n = svg.querySelector(`[data-k='${k}']`)
      if (!n) return null
      const a = parseFloat(n.getAttribute("x1") || "")
      const c = parseFloat(n.getAttribute("x2") || "")
      return Number.isFinite(a) && Number.isFinite(c) ? { a: a / W, b: c / W } : null
    }
    const rng = at("ciRange"), mid = at("ciMid"), pin = at("ciRate")
    const rb = box(svg)
    const rn = svg.querySelector("[data-k='ciRange']")
    return {
      w: rb ? rb.width : 0, h: rb ? rb.height : 0,
      lo: rng ? rng.a : null, hi: rng ? rng.b : null,
      mid: mid ? mid.a : null, rate: pin ? pin.a : null,
      stroke: rn ? getComputedStyle(rn).stroke : "",
    }
  })

  // ── ★主题：两套配色都是"精修"过的承诺，所以两套都要有判据 ──────────────
  //   量的是 **getComputedStyle**，不是源码里写没写那些规则（读产物不读输入）。
  //
  //   ★三个状态分开量，缺一个就有一条判据会空转：
  //     · **启动态**（`boot`）—— 什么都没改，页面自己启动成了什么样。这是用户
  //       打开站点看到的第一眼。★它原来叫 `sys`（"跟随系统"），2026-09-20 站点改成
  //       **默认深色**之后这个名字就**变成假的了**：那时它量的是"应用自己钉的 dark"，
  //       跟系统没有半点关系。名字不改的后果不是判据变红，是判据**悄悄换了被测对象**
  //       而报告上一切照旧 —— 所以这里连同判据一起改名，不留旧名。
  //     · **不设 data-theme**（`noAttr`）—— 真的 `removeAttribute` 之后量。
  //       这才是 @media (prefers-color-scheme) 那条来源，而且**两个方向都能量**：
  //       系统亮色那跑应当等于手动亮色、系统暗色那跑应当等于手动暗色。
  //       （原来是靠"不动 data-theme"当这个状态用的，默认深色之后那就不再是它了。）
  //     · 手动 dark / 手动 light —— 验 [data-theme] 这条来源真的接通了。
  //
  //   ★放在最后量、量完立刻还原：改 data-theme 会让整页重新算样式，
  //     上面那些几何要是量在改过之后，量的就不是用户看到的那个页面了。
  const root = document.documentElement
  const tok = (name: string) => getComputedStyle(root).getPropertyValue(name).trim()
  // ★kpi：预测卡里那个大数字（`text-gray-900 dark:text-gray-50`）。
  //   为什么非要量**一个 dark: 工具类驱动的**颜色：token 那几条（--up/--paper…）
  //   走的是 index.css 里 `@media (prefers-color-scheme: dark)` 那个块，
  //   跟 Tailwind 的 `dark:` variant 是**两条独立的路**。
  //   把 `@custom-variant dark` 写成只认 [data-theme] 的形态时，token 那条路照样通
  //   ——背景色仍然是暗的——死的只有 `dark:` 工具类。只量背景色的话，
  //   这条注入会**静默地打不红**（判据看起来像装饰，实际是量错了东西）。
  const kpi = el("#predCard .grid .tnum")
  // ★「页面底色」到底是哪个元素的 background？
  //   本页的规则是 `html { background: var(--paper) }`（index.css:108），**body 上没有背景**
  //   —— 所以量 `document.body` 拿到的是 `rgba(0,0,0,0)`，两种主题下都一样，
  //   于是"切主题背景变了没"这条判据会**在两个主题都透明的情况下恒假**：
  //   它红得毫无信息量（页面其实好好的），而真正要防的那种坏法它一条也抓不住。
  //
  //   按 CSS 的画布规则取**用户实际看到的那一层**，不是取某个写死的元素：
  //     · body 有不透明背景 ⇒ 它盖住视口，用户看到的是它（这时 html 改了也白改）；
  //     · body 透明 ⇒ 画布取 html 的背景（html 的背景本来就会铺满画布）；
  //   两头都量、取有效的那层，才能同时抓住"切了没反应"和
  //   "给 body 写死一个浅色底、暗色下整页发白"这两种坏法。
  const opaque = (c: string) => !!c && c !== "transparent" && !/rgba\(0,\s*0,\s*0,\s*0\)/.test(c)
  const pageBg = () => {
    const b = getComputedStyle(document.body).backgroundColor
    if (opaque(b)) return b
    return getComputedStyle(document.documentElement).backgroundColor
  }
  // ★`--accent` 是**两套主题里差得最狠的一个 token**：亮色深绿 `#1b6b4c`
  //   （白字 6.45:1），暗色**亮**绿 `#5cc79a`（白字只有 2.08:1）⇒ 一份写死的
  //   字色只在其中**一套**上成立。所以它必须跟 `bgLight`/`bgDark` 一样**按主题各量一份**。
  //
  // ★★这里踩过一次真的：这两个字段最早是**单份**的（在 `themeSnapshot` 外面
  //   各读一次），量到的是"页面此刻恰好是哪个主题"。而当时（阶段 1 之后）**默认是深色**，
  //   ⇒ 亮色那一跑和暗色那一跑量到的**都是暗色**。后果不是判据变红，是它
  //   **永远绿**：亮色那半条根本没人看，亮色主题里把 accent 上的字写坏也抓不到。
  //   注入自测把这条抓出来了（两条注入里有一条"没打红"），形状见 verify_agent.py
  //   里那两条 fault 上面的注释。教训：**凡是"每套主题各一条"的判据，它的输入
  //   就必须是"每套主题各一份"，不能在快照外面顺手读一次。**
  const accentText = () =>
    [".msg.me", "#send", "#ksave", "#prun"].flatMap((s) => {
      const e = el(s)
      if (!e) return []
      const g = getComputedStyle(e)
      return [{ sel: s, col: g.color, bg: g.backgroundColor }]
    })
  // ★网格线也要**按主题各量一份**（理由同上一条）：`.k-grid` 的 stroke 走
  //   `var(--rule)`，而 `--rule` 在亮色是 `var(--n-200)`、暗色是 `#26302b` ——
  //   一次 token 改动就能把网格抹成和卡片底色一样，而**页面上没有别的东西会变**。
  //   这里量的是 `line.k-grid` 元素**算出来的 stroke**（不是源码里写了什么），
  //   并连同"这一跑到底有没有网格线"一起报上去。
  const gridOf = () => {
    const g = psvg ? psvg.querySelector("line.k-grid") : null
    return {
      n: psvg ? psvg.querySelectorAll("line.k-grid").length : -1,
      stroke: g ? getComputedStyle(g).stroke : "",
    }
  }
  const themeSnapshot = () => ({
    bg: pageBg(),
    kpi: kpi ? getComputedStyle(kpi).color : "",
    up: tok("--up"), down: tok("--down"), paper: tok("--paper"), card: tok("--card"),
    accent: tokenColor("--accent"), accentText: accentText(),
    grid: gridOf(),
  })
  const prevTheme = root.getAttribute("data-theme")
  const boot = themeSnapshot()                    // 什么都不改：页面自己启动成的样子
  root.setAttribute("data-theme", "dark")
  const dk = themeSnapshot()
  root.setAttribute("data-theme", "light")
  const lt = themeSnapshot()
  root.removeAttribute("data-theme")
  const na = themeSnapshot()                      // 真的删掉属性 ⇒ @media 那条来源
  if (prevTheme) root.setAttribute("data-theme", prevTheme)
  else root.removeAttribute("data-theme")
  out.sysDark = window.matchMedia("(prefers-color-scheme: dark)").matches
  // ★启动值由 index.html <head> 里那段**内联**脚本记下（它比任何模块都早）。
  //   拿不到 ⇒ 那段脚本没了或没跑到 ⇒ 判据要能看见"没有"，而不是把 undefined
  //   当成"和 'dark' 不一样"就完事（那样报出来的原因是错的）。
  const bt = (window as unknown as { __bootTheme?: { v?: string; attr?: string } }).__bootTheme
  out.bootTheme = bt && typeof bt === "object" ? (bt.v || "") : ""
  out.bootThemeAttr = bt && typeof bt === "object" ? (bt.attr || "") : ""
  out.bgBoot = boot.bg
  out.kpiColorBoot = boot.kpi
  out.bgNoAttr = na.bg
  out.kpiColorNoAttr = na.kpi
  out.upNoAttr = na.up
  out.downNoAttr = na.down
  out.paperNoAttr = na.paper
  out.cardNoAttr = na.card
  out.bgDark = dk.bg
  out.bgLight = lt.bg
  out.kpiColorDark = dk.kpi
  out.kpiColorLight = lt.kpi
  out.upDark = dk.up
  out.downDark = dk.down
  out.paperDark = dk.paper
  out.cardDark = dk.card
  out.upLight = lt.up
  out.downLight = lt.down
  out.accentColDark = dk.accent
  out.accentColLight = lt.accent
  out.accentTextDark = dk.accentText
  out.accentTextLight = lt.accentText
  out.gridDark = dk.grid
  out.gridLight = lt.grid
  // ★网格判据要拿它跟自己那套主题的**卡片底色**比（图就画在卡片上）。
  //   `cardDark`/`cardLight` 上面已经有了，这里不用重复发。
  out.gridCardDark = dk.card
  out.gridCardLight = lt.card

  // ── ★主题开关这个**按钮**到底通不通 ─────────────────────────────────
  //   用户要的是"有个默认主题 + 能切另一个"（2026-09-28 起默认是亮色），
  //   而上面那些判据量的全是 **CSS 层**：
  //   探针直接改 `data-theme` 属性，绕过了按钮、也绕过了 `useTheme()`。
  //   这正是本仓库踩过的那个形状 ——「判据测接口、用户走按钮」：旧页面 `keySend()`
  //   发的是空对象，"点保存 100% 坏"活过了 152 条全绿判据（它们打的是 /api/key）。
  //   所以这里要点**真的那一下**，读点完发生了什么。
  //
  //   ★只量**同步**能读到的部分：无头 firefox 是带 `--screenshot` 起的，页面一 load
  //     就截图退出，实测**连 setTimeout 0 都赶不上**（见上面对 `tap()` 的注释）。
  //     ⇒ 点击一律走 `tap()`：它用 `flushSync` 把这次更新**当场刷完**，
  //       所以下面读到的就是"用户点下去之后真的变成了什么"。
  //       （★实测：直接 `b.click()` 的话，本页这三个读数**一格都不会变** ——
  //        handler 确实跑了，React 把提交排进了微任务，而探针在同一 tick 里读。）
  //   ★★四个读数**分四格记**，不合成一个字符串：属性、localStorage、aria-pressed、
  //     背景色分别是四条不同的路（CSS 生效 / 记住选择 / 界面上标出当前项 /
  //     真的画出来了），坏法各不相同，合成一格就只能一起判、坏了也说不清坏在哪。
  //   ★点完**必须还原**：下面还有一大段几何要量，不许让它们看见这里动过的主题。
  //     还原的是**属性和 localStorage 的原文**（不是"再点回暗色"——
  //     那要多依赖一次 React 的提交，而这里要测的正是提交本身）。
  const swGroup = el('[role="group"][aria-label="主题"]')
  const swBtns = swGroup
    ? (Array.from(swGroup.querySelectorAll("button")) as HTMLElement[])
    : []
  const swCur = () => swBtns
    .filter((b) => b.getAttribute("aria-pressed") === "true")
    .map((b) => (b.textContent || "").trim())
    .join(",")
  const lsRead = () => { try { return localStorage.getItem("licai-theme") || "" } catch { return "(读不到)" } }
  const lsPrev = lsRead()
  const swClick = (label: string) => {
    const b = swBtns.find((x) => (x.textContent || "").trim() === label)
    if (!b) return { attr: "(没有这个按钮)", ls: "", pressed: "", bg: "" }
    tap(b)
    // ★★`tap()` 走的是**真点击**（`clickAt` 派发带坐标的 MouseEvent）⇒ 波纹监听
    //   照样会冒圈。这里点两下就是**两个 `.ripple` 留在 body 上**。
    //   正常态它们 `position: fixed`（不参与布局）所以看不出来 —— 可它们**会**
    //   改变后面那些几何判据的"起始状态"：波纹块量 `r0` 在它自己那次清理**之前**，
    //   于是"点击前后位移"量到的是"清残圈造成的位移"，不是点击造成的。
    //   实测就是这么发现的：注入把波纹改成参与布局后，`dt` 报的是 **-190**
    //   （清残圈往下缩）而不是预期的 +190。**探针不许留痕** —— 量完就撤，
    //   和波纹块自己收尾那一手同一个纪律。
    els(".ripple").forEach((n) => n.remove())
    return {
      attr: root.getAttribute("data-theme") || "",
      ls: lsRead(),
      pressed: swCur(),
      bg: pageBg(),
    }
  }
  out.swLabels = swBtns.map((b) => (b.textContent || "").trim()).join(",")
  out.swInit = swCur()
  out.swLight = swClick("亮")
  out.swDark = swClick("暗")
  try {
    if (lsPrev) localStorage.setItem("licai-theme", lsPrev)
    else localStorage.removeItem("licai-theme")
  } catch { /* 存不下不影响这一屏 */ }
  if (prevTheme) root.setAttribute("data-theme", prevTheme)
  else root.removeAttribute("data-theme")

  // ── ★Key 卡的「保存」按钮到底通不通 ──────────────────────────────────
  //   为什么非得有这一段：`/api/key` 那条接口有一整组判据（check_apikey），
  //   但**用户走的是按钮**。旧页面那个 bug 就是这么活下来的：`keySend()` 发的是
  //   `JSON.stringify({})`，从来没读过输入框 —— 服务端回 400「key 不能为空」，
  //   网页上点保存 100% 是坏的，而**当时 152 条判据全绿**（它们打的是接口）。
  //   ⇒ 判据测接口、用户走按钮，中间那一段谁都没管。
  //
  //   ★能**同步**做完的只有前半段：`saveKey()` 里那次 `fetch` 是同步发生的
  //     （async 函数跑到第一个 await 之前全是同步的）⇒ 点完立刻就能读到
  //     "这一枪发出了什么"。而**回执**在 await 之后，无头 firefox 等不到
  //     （spike 实测：连 setTimeout 0 都赶不上它退出）⇒ 所以这一段**不假装**
  //     验了"点完 .env 真的变了"。
  //
  //   ★这里**不读按钮的忙碌态**，虽然它看着更"像"几何。原因是**实测**：
  //     程序化 `click()` 之后同一个 tick 里 `disabled` 仍是 false，50ms 后也还是 false。
  //     （为什么没在这个 tick 里 commit，我没有查到底 —— 所以只写量到的事实，
  //     不编一个机制出来。后一个 false 至少能解释：桩立刻 resolve，busy 已翻回去了。）
  //     **判据的前提量不到，就不该照着它写判据** ——
  //     发出去的请求体比 busy 态信息量大得多：它同时证明了按钮接上了、
  //     接的是哪条路、带的是哪个值。
  //
  //   ★这一枪**不出浏览器**：探针把 fetch 临时换成"只记录、不发送"，点完立刻还原。
  //     不这么干，要证明"发的是输入框里那个值"，就得往输入框里填一个哨兵，
  //     而那一枪是真会打到服务端的。哨兵同时**故意填成不合法的那种**
  //     （带空格和中文，服务端必然拒绝）—— 两道闸各自独立，少一道也写不坏 .env。
  //   ★桩回的是 **400 + 一句真话**，不是 200：回 200 的话 `send()` 会走成功分支，
  //     把 `#kstate` 改成"还没配"、还在 `#kout` 写上一句"已保存" ——
  //     截图里就留下一张"刚保存成功"的假证据，而这一枪根本没发出去。
  const ksave = el("#ksave") as HTMLButtonElement | null
  const kin = el("#kinput") as HTMLInputElement | null
  out.keyClickVal = ""
  out.keySaveMethod = ""
  out.keySaveBody = ""
  if (ksave && kin) {
    const SENTINEL = "probe sentinel 不许保存"
    const realFetch = window.fetch
    const seen: { method: string; url: string; body: string }[] = []
    window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : String((input as Request).url || input)
      seen.push({
        method: String((init && init.method) || "GET").toUpperCase(),
        url,
        body: init && init.body != null ? String(init.body) : "",
      })
      return Promise.resolve(new Response(
        JSON.stringify({ error: "探针：这一枪没有真的发出去" }),
        { status: 400, headers: { "Content-Type": "application/json" } }))
    }) as typeof fetch
    try {
      // ★受控输入：直接写 `kin.value` 改不动 React 的 state（只有 onChange 改）。
      //   得像用户打字那样，用原型上的**原生 setter** 再补一个 input 事件。
      //   （实测有效：不补这一下，下面读到的请求体里 key 会是空串。）
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set
      if (setter) setter.call(kin, SENTINEL)
      kin.dispatchEvent(new Event("input", { bubbles: true }))
      out.keyClickVal = kin.value
      ksave.click()
      const hit = seen.filter((s) => s.url.indexOf("/api/key") >= 0)[0]
      if (hit) {
        out.keySaveMethod = hit.method + " " + hit.url
        out.keySaveBody = hit.body
      }
    } finally {
      window.fetch = realFetch            // 立刻还原：不把页面留在被插桩的状态里
    }
  }

  // ── 「问问看」那一排（每轮更新）──────────────────────────────────────
  //   量的是**渲染出来的按钮**，不是源码里那个数组。
  //   `quickBoot` 是**参照物**（兜底那四条，即"没更新时该长什么样"）；
  //   判据拿"渲染出来的"跟它比 —— 相等就说明这一排还是常量，跟这一轮无关。
  {{
    const btns = els("#qs button") as HTMLButtonElement[]
    const labels = btns.map((b) => (b.textContent || "").replace(/\s+/g, ""))
    out.quick = btns.length
    out.quickText = labels.join("|")
    out.quickBoot = DEFAULT_QUICK.map((q) => q.label.replace(/\s+/g, "")).join("|")
    out.quickLabel = labels.length ? labels[0] : ""
    //   点第一颗，读它**真发出去的那一枪** —— 与 Key 卡同形状。
    //   ★量的是请求体里的 `question`，不是按钮上的字：按钮写着 A 却发 B（或者干脆
    //     发一个写死的问题）在 DOM 上完全看不出来。旧页面 `keySend()` 发空对象
    //     那件事就是这么活过 152 条全绿判据的。
    //   ★这一块**必须放在最后**：`ask()` 会同步 dispatch（React 对离散事件是同步刷的），
    //     点完 DOM 就多了气泡和错误条。前面所有字段这时已经写进 `out` 了，不受影响；
    //     放到前面去就会把 `msgs` / `chips` / 各种高度全量成点完之后的数。
    out.quickAsk = ""
    out.chatKeys0 = ""
    out.chatProf0 = ""
    out.chatKeys1 = ""
    out.chatProf1 = ""
    if (btns.length) {
      const realFetch = window.fetch
      type Shot = { q: string; keys: string; prof: string }
      const seen: Shot[] = []
      window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : String((input as Request).url || input)
        if (url.indexOf("/api/chat") >= 0 && init && init.body != null) {
          try {
            const b = JSON.parse(String(init.body)) as { question?: string; profile?: unknown }
            seen.push({
              q: String(b.question || ""),
              keys: Object.keys(b).sort().join(","),
              prof: b.profile === undefined ? "" : JSON.stringify(b.profile),
            })
          } catch { seen.push({ q: "", keys: "", prof: "" }) }
        }
        return Promise.resolve(new Response("探针：这一枪没有真的发出去", { status: 400 }))
      }) as typeof fetch
      try {
        //  ① 浏览器里**没存**画像时点一下。这一枪**必须不带** `profile` 这个键 ——
        //     它是上面那一条的对照组，防的是"前端写死一段画像塞进每一枪"。
        btns[0].click()
        out.quickAsk = seen.length ? seen[0].q : ""
        if (seen.length) { out.chatKeys0 = seen[0].keys; out.chatProf0 = seen[0].prof }

        //  ② 塞一份画像，**不刷新页面**，再点同一下。
        //     ★"不刷新"是这条判据的全部要点：`ask()` 如果是在挂载时读一次画像存进
        //       state，那么这里的第二枪读到的还是挂载时那份（空的）—— 而用户的真实
        //       路径正是"刚在 /profile 填完、回到这一页接着问"。刷新一下就好，
        //       于是谁也不会往"读的时机"上想。
        //     ★值故意和 `/profile` 那套判据用的「基金 / 保守」**不一样**：
        //       一样的话，"把这几个字写死"能同时满足两边。
        //     ★直接写原始 JSON，不走 `saveProfile()`：走它的话，"读回来的是不是
        //       写下去的那份"就变成自己验自己了。
        try {
          localStorage.setItem(PROFILE_KEY, JSON.stringify({
            interests: ["债券", "黄金"], risk: "进取", horizon: "1-3年",
            assets: "20-50万", income: "1-3万", note: "探针塞的",
          }))
        } catch { /* 存不下就量不到，下面的字段留空、判据自会红 */ }
        const b2 = els("#qs button")[0] as HTMLElement | undefined
        if (b2) {
          b2.click()
          if (seen.length > 1) { out.chatKeys1 = seen[1].keys; out.chatProf1 = seen[1].prof }
        }
      } finally {
        window.fetch = realFetch          // 立刻还原：不把页面留在被插桩的状态里
        try { localStorage.removeItem(PROFILE_KEY) } catch { /* 清不掉不影响本页 */ }
      }
    }
  }}

  // ── ★主页行情条（`#dstrip`）──────────────────────────────────────────
  //   读的是**渲染出来的那两串字**（`[data-k="px"]` / `[data-k="chg"]` 的可见文本），
  //   不是 `data-*` 属性：属性可以是另一个变量写进去的，而这两串字就是用户眼睛
  //   看到的那两串。判据拿它们跟**判据自己打 `/api/kline` 拿到的数**逐字比。
  //   ★走势线读的是 `polyline` 的 `points` —— 那是一串**能被数出来、能被算极差的数**。
  //     读 `path` 的 `d` 要解一遍迷你 SVG 路径语法：判据越像"把实现再写一遍"，
  //     它坏起来越哑（它自己错了不会响，只会红成"页面不对"）。
  //   ★`ystep` 报了"这一串 y 有几个不同的值"：等于 1 就是**一条水平线** ——
  //     那正是"画了个假的示意图"的样子，而它照样有 48 个点、照样有颜色。
  //   ★这一块**只读不写**，所以可以放在波纹之前（那一块要往 body 插元素，必须最后）。
  out.homeStrip = null
  const strip = el("#dstrip") as HTMLElement | null
  if (strip) {
    const rows = els("#dstrip [data-sym]").map((n) => {
      const px = n.querySelector('[data-k="px"]')
      const chg = n.querySelector('[data-k="chg"]')
      const pl = n.querySelector('[data-k="spark"] polyline') as SVGPolylineElement | null
      const sv = n.querySelector('[data-k="spark"]') as SVGSVGElement | null
      const pts = pl ? (pl.getAttribute("points") || "").trim().split(/\s+/).filter(Boolean) : []
      const ys = pts.map((p) => (p.split(",")[1] || "").trim())
      const r = n.getBoundingClientRect()
      const sr = sv ? sv.getBoundingClientRect() : null
      return {
        sym: (n as HTMLElement).dataset.sym || "",
        px: px ? (px.textContent || "").trim() : "",
        chg: chg ? (chg.textContent || "").trim() : "",
        err: !!n.querySelector('[data-k="err"]'),
        n: pts.length,
        // 数的是**不同的 y 有几个**，不是"极差有多大"：极差要挑阈值（多小算平？），
        // 而"只有一个值"这件事没有阈值可挑。
        yKinds: new Set(ys).size,
        stroke: pl ? getComputedStyle(pl).stroke : "",
        // ★走势线**自己的**宽高（不是这一行的）。点数、y 种类、颜色全对，
        //   但线画成 0 高的时候（`<Spark h={0} />`）**只有几何会露** ——
        //   前三个量一个都不变（`points` 照样 48 个点、y 照样有几种值、
        //   颜色照样对），而屏幕上什么都没有。这就是"量几何不读文本"。
        sparkW: sr ? Math.round(sr.width) : 0,
        sparkH: sr ? Math.round(sr.height) : 0,
        w: Math.round(r.width),
        h: Math.round(r.height),
      }
    })
    const root = getComputedStyle(document.documentElement)
    out.homeStrip = {
      rows,
      h: Math.round(strip.getBoundingClientRect().height),
      up: root.getPropertyValue("--up").trim(),
      down: root.getPropertyValue("--down").trim(),
    }
  }

  // ── ★圈状波纹（点击反馈）─────────────────────────────────────────────
  //   用户的要求是「点击选项卡以及界面的时候要有圈状波纹，所有界面都是」。
  //   可测的形式有两条，缺一条这一层就是装饰：
  //
  //   ① **点一下真的冒出来**，而且圆心就是点击点。测法必须是**走 document 上
  //      那条监听**（`clickAt` 派发一个带坐标的真 MouseEvent），不能直接调
  //      `spawnRipple()` —— 直接调等于"判据测接口、用户走按钮"，
  //      正是本仓库踩过的那个坑：函数写对了、接线断了，判据照样绿。
  //
  //   ② **它一点布局都不许动**。量的是被点元素点击前后的 getBoundingClientRect
  //      差值（宽/高/左），必须逐像素相同。波纹挂 `<body>` 上用 `position:fixed`
  //      时这条从构造上就成立；哪天有人把它改成塞进被点元素里（Material 那套常见
  //      写法），为了让圈不溢出卡片就得加 padding 或者改 overflow —— 差值立刻不为 0。
  //
  //   ★这一块**必须放在所有几何量完之后**：它会往 body 上插一个元素，
  //     虽然 `fixed` 不参与布局，但"量在前、动在后"这条纪律不该靠这个性质兜着。
  //   ★它放在「问问看」那块**之后**也没问题：那块量的是发出去的请求体，
  //     而这一块量完立刻把元素摘掉，不会留在页面里污染 beacon 之后的任何东西。
  out.ripple = null
  const rcard = els(".card")[0] as HTMLElement | undefined
  if (rcard) {
    const r0 = rcard.getBoundingClientRect()
    const cx = Math.round(r0.left + r0.width / 2)
    const cy = Math.round(r0.top + r0.height / 2)
    // ★**必须挡住这次点击的默认行为**：主页那几张卡是 `<a href="/agent">`，
    //   一次没挡住的点击 = 真的跳转 = 页面卸载 = 探针载荷再也送不出去。
    //   那批渲染层判据不是变红，是**消失**（本仓库反复写的那件事）。
    //   捕获阶段注册，且注册在 installRipple 之后 ⇒ 波纹先冒、再挡默认行为。
    const block = (ev: Event) => ev.preventDefault()
    document.addEventListener("click", block, true)
    els(".ripple").forEach((n) => n.remove())       // 先清干净：下面量到的必须是这一次的
    let d: HTMLElement | null = null
    try {
      clickAt(rcard, cx, cy)
      d = el(".ripple") as HTMLElement | null
    } finally {
      document.removeEventListener("click", block, true)
    }
    const r1 = rcard.getBoundingClientRect()
    const dr = d ? d.getBoundingClientRect() : null
    out.ripple = {
      seen: !!d,
      // 位置与 pointer-events 用**算出来的**值读：源码里写了 `position:fixed`
      // 不算数 —— 被后面某条规则盖掉时，源码一个字都没变。
      pos: d ? getComputedStyle(d).position : "",
      pe: d ? getComputedStyle(d).pointerEvents : "",
      cx: dr ? Math.round(dr.left + dr.width / 2) : -1,
      cy: dr ? Math.round(dr.top + dr.height / 2) : -1,
      atX: cx, atY: cy,
      dw: Math.round(r1.width - r0.width),
      dh: Math.round(r1.height - r0.height),
      dl: Math.round(r1.left - r0.left),
      dt: Math.round(r1.top - r0.top),
    }
    els(".ripple").forEach((n) => n.remove())       // 量完就摘，别留给后面的字段
  }

  // ── ★路由：探针跑在**哪一页**上 ──────────────────────────────────────
  //   量的是 `location.pathname` 本身，不是 PAGE 那个常量 —— 常量是路由表
  //   算出来的（"我以为我在哪页"），pathname 是浏览器真的在哪页。
  //   「第一次来就自动送去引导页」一旦在探针路径上也生效，
  //   `location.replace('/onboarding')` 会把 `?probe=1` 一起丢掉 ⇒
  //   落到 `/onboarding` 上**不会 beacon**，这一整份载荷变成「没收到探针数据」。
  //   所以这条判据钉的是"探针量的还是它以为的那一页"。
  out.bootPath = location.pathname
  out.bootSearch = location.search

  // ── ★「该不该自动弹引导」的真值表 ────────────────────────────────────
  //   为什么要在浏览器里把**四组输入**都问一遍：无头 firefox 每次都是全新的
  //   profile，localStorage 永远是空的 —— "看过了就不跳"那一半在浏览器里
  //   **永远走不到**。只判"新用户会跳"的话，判据只是半个。
  //   ★第 1 组是**保命线**（探针路径下必须不跳），第 4 组是**对照组**
  //   （子页面上不该跳）—— 少了它，把函数写成"只要没看过就跳"照样绿。
  out.obSeen = hasSeenOnboarding()
  out.routeGuard = [
    shouldAutoOpenOnboarding("/", "?probe=1", false),
    shouldAutoOpenOnboarding("/", "", false),
    shouldAutoOpenOnboarding("/", "", true),
    shouldAutoOpenOnboarding("/agent", "", false),
  ]

  // ── ★主页「我的画像」卡：空态不编内容 ────────────────────────────────
  //   量的是**文本**：空态那句里必须出现「还没填」、且不含 `｜` 这个分隔符。
  //   只判"有这个元素"是不够的 —— 塞一段写死的 demo 画像占位，
  //   文案长得像正常态，肉眼分不出，而元素照样在。
  out.profileEcho = txt(el("#profileEcho"))

  // ── ★引导页：分步、进度、末步不放过必填 ──────────────────────────────
  //   ★整块**必须走 document 上那条真监听**（`.click()` 派发的真点击），
  //     不能直接调 setState 或读组件内部 —— 那是"判据测接口、用户走按钮"，
  //     本仓库踩过的那个坑（旧页面 `keySend()` 发空对象，152 条判据全绿）。
  out.obSteps = null
  if (el("#obCard")) {
    const snap = () => ({
      // ★步号从 `#obStepNo` 那句「第 N / M 步」里**解析**出来，
      //   而不是从探针自己数的第几次点击推 —— 后者是"我以为"，前者是页面上写的。
      no: stepNo(el("#obStepNo")),
      title: txt(el("#obTitle")),
      pct: barPct(el("#obBar")),
      prevOff: !!(el("#obPrev") as HTMLButtonElement | null)?.disabled,
      hasNext: !!el("#obNext"),
      hasSkip: !!el("#obSkip"),
      hasForm: !!el("#obProfileForm"),
      guideHref: hrefOf("a#obGuideLink"),
    })
    const steps: Rec[] = []
    for (let n = 0; n < 12; n++) {
      steps.push(snap())
      const nb = el("#obNext")
      if (!nb) break               // 末步没有「下一步」—— 那就是停下的信号
      tap(nb)                      // ★必须 flushSync —— 见 tap() 的注释
    }
    out.obSteps = steps
    // 末步的画像表单：空提交必须被拦住（红字且**没写存储**），
    // 填上必填项再提交，才该写下"看过了"和画像。
    out.obForm = formProbe("obProfileForm")
    out.obSeenAfter = hasSeenOnboarding()
    out.obDoneTitle = txt(el("#obTitle"))
    out.obFormGone = !el("#obProfileForm")
  }

  // ── ★画像页：同一份表单，可反复改 ────────────────────────────────────
  out.pfForm = null
  out.pfSavedAfter = ""
  if (el("#profilePageForm")) {
    out.pfSavedBefore = txt(el("#pfSaved"))
    out.pfForm = formProbe("profilePageForm")
    out.pfSavedAfter = txt(el("#pfSaved"))
  }

  // ── ★模拟投资页：一屏有数、一屏没数 ──────────────────────────────────
  //   这一页只有一条用户点名的硬要求：**抓不到行情就明确报错，绝不编数字**。
  //   所以这里量的不是"数算得对不对"（那条在 http 层用手算判过 —— 页面上再算
  //   一遍只是把同一个函数抄两遍），而是**"算不出来的那一屏上有没有数字"**。
  //   一个编出来的 0% 和一个真实的 0% 在页面上长得一模一样。
  //
  //   ★成败两屏没法用一份载荷量：一份载荷上的页面只可能是其中一个样子。
  //     探针这一份是**成**的那一屏（数由 main.tsx 同步取好传进来，见 game.ts），
  //     **败**的那一屏由判据那边换一份会失败的桩再 gather 一次。
  out.g = null
  if (el("#gStake") || el("#gNums") || el("#gErr")) {
    const numTxt = (s: string) => txt(el(s))
    out.g = {
      // ★本金框里现在是什么。判据要的是**空**，而这条必须配一条"画像真塞进去了"
      //   的对照（`profAssets`）—— 没有对照的话，localStorage 是空的、
      //   本金框当然也空，这条判据就是在空转（本仓库踩过的那种假绿）。
      stakeVal: (el("#gStake") as HTMLInputElement | null)?.value ?? null,
      profAssets: (() => {
        try { return (JSON.parse(rawKey(PROFILE_KEY) || "{}") as Rec).assets || "" } catch { return "" }
      })(),
      // ★推荐那一排拿到的兴趣，从**浏览器里那份画像**读（判据拿它去回打一次接口，
      //   而不是自己再写死一份「基金/黄金」—— 那样"探针塞了什么"就成了第三处常量）。
      profInterests: (() => {
        try {
          const v = (JSON.parse(rawKey(PROFILE_KEY) || "{}") as Rec).interests
          return Array.isArray(v) ? v.map(String) : []
        } catch { return [] }
      })(),
      sym: (el("#gSym") as HTMLInputElement | null)?.value ?? "",
      // 推荐那一排：条数与标的。★"只推荐不限制"这条的另一半是
      // "不在推荐里的标的一样能报价"，那半在 http 层判。
      recCount: els("#recBox [data-rec]").length,
      recSyms: els("#recBox [data-rec]").map((n) => n.getAttribute("data-rec") || ""),
      hasNums: !!el("#gNums"),
      err: numTxt("#gErr"),
      // ★**失败那一屏上的百分号个数**。这是这一页最要紧的一个数：
      //   只要 > 0，就说明"算不出来"的时候页面上出现了一个用户会当真的数字。
      //   量的是 `#gOut` 整块的文本，不是 `#gPnl` —— 只量盈亏那个 span 的话，
      //   收益率换个地方冒出来（比如"收益率 —"旁边的另一个数）照样绿。
      pctCount: (numTxt("#gOut").match(/%/g) || []).length,
      buy: numTxt("#gBuy"), shares: numTxt("#gShares"), last: numTxt("#gLast"),
      value: numTxt("#gValue"), pnl: numTxt("#gPnl"), pnlPct: numTxt("#gPnlPct"),
      amtShown: numTxt("#gAmtShown"),
      buyDate: (numTxt("#gBuyDate").match(/\d{4}-\d{2}-\d{2}/) || [""])[0],
      posRows: els("#gPos tbody tr[data-sym]").length,
      // ★坏掉的那几行**必须还占着一行**并写明原因。少一行看起来像
      //   "我只建了一笔仓"，而不像出错 —— 页面上没有任何异常。
      posErrRows: els("#gPos tbody tr[data-sym]")
        .filter((r) => /没取到/.test(r.textContent || "")).length,
      // ★★红涨绿跌在这一页上**到底画出来了没有**。
      //   这两个数必须成对看，单看 `pnlColor` 判不了：
      //     · `pnlColor` = 盈亏那个数字（`#gPnl`，带 `text-up`/`text-down`）的计算色
      //     · `inkColor` = 同一块里一个**不带方向类**的读数（`#gValue`，纯净的 `.gv`）
      //   判据要的是 `pnlColor != inkColor` **且** 它等于 `--up` / `--down` 的解析值。
      //   没有 `inkColor` 这个对照的话，`pnlColor == 正文色` 这一种结果
      //   既是"方向色被按掉了"、也是"这一局正好不赚不亏"，两种原因分不开。
      //
      //   ★为什么要专门量这一条：`text-up` / `text-down` 是 `@theme` 生成、
      //     落在 Tailwind 的 `@layer utilities` 里，而这一页的 `.gv`
      //     （`index.css` 里裸写、不在任何 layer）也声明了 `color` ——
      //     未分层的声明胜过一切分层声明，**和特异性无关**。
      //     实测过一次：`#gPnl` 和 `#gValue` 量出来是同一个 rgb，
      //     也就是说这一页最要紧的一条信息（赚了还是亏了）当时根本没传出去。
      pnlColor: (() => { const n = el("#gPnl"); return n ? getComputedStyle(n).color : "" })(),
      inkColor: (() => { const n = el("#gValue"); return n ? getComputedStyle(n).color : "" })(),
    }
  }

  out.title = document.title
  beacon(out)
}

/** ★**渲染之前**把一份画像放进浏览器里 —— 只有 `/game` 那一页用（见 main.tsx）。
 *
 *  为什么非要在渲染之前：这一页有一条判据是「本金框不许被画像里的资产预填」，
 *  而本金框的值是**挂载时**读一次 localStorage 定下的。渲染完再塞画像，
 *  那一帧早就过去了 —— 判据就变成空转：没有画像时本金框当然也是空的，
 *  坏成什么样都照样绿。**这条判据的全部意义就在"有画像的时候"**。
 *
 *  ★塞的资产取 `100万以上`（选项表里最大的那一档）：注入一旦真的拿它当默认值，
 *    本金框里会出现一个一眼认得出的数。
 *  ★兴趣取「基金 / 黄金」，是为了让推荐那一排**真的有东西**——
 *    顺带给"推荐是按画像来的"提供一份对照（没画像时它是空的）。
 */
export function probeSeedGameProfile(): void {
  try {
    localStorage.setItem(PROFILE_KEY, JSON.stringify({
      interests: ["基金", "黄金"], risk: "稳健", horizon: "1-3年",
      assets: "100万以上", income: "1-3万", note: "探针塞的",
    }))
  } catch { /* 存不下就量不到，`profAssets` 会是空的、判据自会红 */ }
  // ★再塞**两笔**持仓，其中一笔的标的在判据那份桩里是**取数失败**的。
  //   这一页有一条和总览页同形的判据：**一行坏掉只坏一行**。
  //   只塞一笔好仓的话，那条判据没有对照组 —— "整张表都空着"和
  //   "我只有一笔仓"在页面上长得一模一样（本仓库反复写的那件事）。
  //   ★`510300` 是探针自己报价那一只（`PROBE_SYM`），`600519` 是判据的桩里
  //     唯一被点名失败的标的；两个都写死在这里，判据那边有同样的两个锚点。
  try {
    const d = new Date()
    const p = (n: number) => String(n).padStart(2, "0")
    const back = new Date(d.getFullYear(), d.getMonth() - 3, d.getDate())
    const buy = `${back.getFullYear()}-${p(back.getMonth() + 1)}-${p(back.getDate())}`
    localStorage.setItem("licai-game-positions", JSON.stringify([
      { id: "probe-ok", symbol: "510300", name: "场内沪深300ETF", amount: 10000, buyDate: buy, at: "" },
      { id: "probe-bad", symbol: "600519", name: "贵州茅台", amount: 10000, buyDate: buy, at: "" },
    ]))
  } catch { /* 同上：塞不进去，持仓那两条判据自会红 */ }
}

/** 组合体检页（`/portfolio`）的持仓种子。
 *
 *  ★它和上面那份**不是同一批**，理由不是"想不一样"，是这一页要判的三件事
 *    各自需要一个**上面那份给不了**的形状：
 *
 *    ① **取不到的那只必须点名。** 丢一只之后还要能出报告，活下来的权重就得
 *       ≥ 60%（`MIN_SURVIVING`）。上面那份是两只各 50%，丢一只剩 50% ——
 *       直接落进"不出报告"那一屏，点名那条判据**根本没有页面可判**。
 *       ⇒ 这里用四只、把被丢的那只压到 20%，丢掉之后还剩 75%。
 *
 *    ② **ETF 不许被塞进行业，而且这一跑不许碰网络。** 上面那份里有 `600519`
 *       （A 股个股）—— 它活着的时候 `_industry` 会去拉公司概况，判据就变成了
 *       "今天巨潮通不通"。这里活下来的三只**全是 ETF**，`_industry` 一只都不问
 *       （见 `portfolio._industry` 的 market 分流），于是这条判据是确定的。
 *
 *    ③ **算不了那一屏要有得判。** 第二跑把三只 ETF 全点名失败，只剩 600519 的
 *       20% < 60% ⇒ 走"不出报告"那条早退（在 Beta 和行业**之前**，仍然不碰网络）。
 *
 *  ★`600519` 是判据那份桩里被点名失败的标的，三只 ETF 是活着的 —— 四个锚点
 *    在 `verify_agent.py` 的 `PF_SEED` 里各写了一遍，改一处要改两处。
 */
export function probeSeedPortfolio(): void {
  try {
    localStorage.setItem("licai-game-positions", JSON.stringify([
      { id: "pf-a", symbol: "510300", name: "场内沪深300ETF", amount: 30000, buyDate: "2024-01-02", at: "" },
      { id: "pf-b", symbol: "510500", name: "中证500ETF", amount: 30000, buyDate: "2024-01-02", at: "" },
      { id: "pf-c", symbol: "518880", name: "黄金ETF", amount: 20000, buyDate: "2024-01-02", at: "" },
      { id: "pf-d", symbol: "600519", name: "贵州茅台", amount: 20000, buyDate: "2024-01-02", at: "" },
    ]))
  } catch { /* 塞不进去，页面会显示"还没有持仓"，判据自会红 */ }
}

/** ★**建议体检页（`/inspect`）的种子** —— 写一份体检结论进 `localStorage`。
 *
 *  为什么非要有它：这一页的输入是**用户现场粘进来的**，而探针**不会打字**。
 *  没有种子的话，"有结论那一屏"（承诺 / 红旗 / 豁免 / 该问的 / 判不了的 /
 *  该看哪几个词）在结构上就量不到 —— 探针只能量到"还没体检过"那一屏，
 *  而那上面的每一条判据都会**空转**（量什么都是空的）。页面正在读的就是
 *  `licai-inspect-last` 这个键（`inspect.ts` 里「本机缓存」那一段），
 *  所以"先写一份结论、再打开页面"就等于用户刚贴完那一段。
 *
 *  ★★**这是"给判据用的确定输入"，不是产品数据**（地位等同行情桩
 *  `LICAI_FEED_STUB` 和台账种子）：下面这份 JSON 的形状是**照着接口契约写死的**，
 *  为的是让判据能拿它做**逐字相等**的比较（`insFlags` 必须逐字等于
 *  `["保本保收益承诺","杀猪盘"]`）。**不要为了"和后端保持一致"去调它** ——
 *  后端换一个措辞就跟着改的话，判据的期望值也会跟着漂，那条判据就白写了。
 *
 *  ★它**只写 `INSPECT_KEY` 一个键**（不写 `licai-inspect-text`）：判据那边的种子
 *  也只写这一个键，两边形状必须同构。页面缺了原文那一份会自己说
 *  "原文当时没记下来"（`#lookedAt` 里的 `data-k="lookedAtGone"`），不是坏。
 *
 *  ★**服务端一个字节都不存**：这份东西住在跑探针的那台浏览器的 localStorage 里，
 *  和 `licai-assess` 的答案、`licai-profile` 的画像同一个待遇。
 *
 *  ★`flags[].why` / `exempt_notes[].why` 里保留了后端的 `**强调**` 记号
 *  （后端 JSON 不过 `uicopy.humanize`，那记号是**有意**带的）——
 *  页面把它们渲染成 `<b>`，判据可以顺带量"记号有没有被当成字面星号画出来"。
 */
export function probeSeedInspect(): void {
  try {
    localStorage.setItem(INSPECT_KEY, JSON.stringify({
      ok: true,
      source: "rule",
      promise: {
        // ★一个收益率 + 一个"承诺词"就够判据量这一块了（种子不是回测语料）。
        rates: [{ annual: 12.0, kind: "年化", pct: 12.0, raw: "年化 12%" }],
        annual_max: 12.0,
        words: ["保本"],
      },
      flags: [
        // ★两个 `term` 的顺序是**判据的期望值**，别调换、别加第三条。
        { term: "保本保收益承诺",
          sentence: "隔壁老王介绍的项目说年化 12%、保本保息",
          matched: ["保本保息"],
          why: "**资管新规**之后理财产品不许承诺保本保收益 —— 说的和做的对不上。",
          from: "N 类词条「保本保收益承诺」·辨析" },
        { term: "杀猪盘",
          sentence: "拉人头还有返利，老师带单稳赚",
          matched: ["拉人头", "老师带单"],
          why: "**拉人头**和**老师带单**是这一类话术的两件套：钱不来自投资、来自下一个人。",
          from: "N 类词条「杀猪盘」·辨析" },
      ],
      exempt_notes: [
        // ★这一条是整页的灵魂：命中了字面、但被判成**不算红旗**（要单独渲染）。
        { kind: "exempt", term: "保本保收益承诺",
          sentence: "我剩下的钱是存银行定期存款的，那个保本",
          exempt: "定期存款",
          why: "**银行存款**不在资管新规那条的适用范围里 —— 它说「保本」是合法的。" },
      ],
      questions: [
        { ask: "这个收益率凭什么这么高？钱从哪赚来的？",
          because: "你写的最高年化约 12.0%。图谱词条「非法集资」举的例子正是“年化 12% 保本保息”。",
          term: "非法集资" },
      ],
      cannot_verify: [
        { term: "非法集资",
          why: "要看它**有没有吸收公众存款的资质**。本站查不到牌照，判不了 —— "
            + "词条说得很清楚：营业执照≠金融牌照。" },
      ],
      baseline: null,
      read_next: [{ ask: "什么是可转债", label: "什么是可转债" }],
      empty_reason: null,
    }))
  } catch { /* 塞不进去 ⇒ `insFromCache` 是 false、页面落在"还没体检过"那一屏，判据自会红 */ }
}

/** ★建议体检页的**第二份种子**：有结论、但**一条红旗都没检出**那一屏。
 *
 *  为什么非要有第二份：`empty_reason` 那句人话是**这一页的灵魂**（"没检出红旗
 *  不等于这条建议没问题"），而它在上面那份种子里是 `null`（契约如此：有红旗时
 *  后端不给这句话）⇒ 用第一份种子**量不到它**。这一份把 `flags` 清空、留一条
 *  豁免、把后端**第二档**那句原话填进去（`agent/checkup.py` 里那一档的原文，
 *  逐字抄的），于是"灵魂那句话"变成一个可量的读数。
 *
 *  ★它由 `main.tsx` 里的 `?probe=1&nohit=1` 触发。★★这个地址是**判据的输入**
 *   （协调者已把它写进 `check_inspect`）——**别删**，删了那边不是"少一跳"，是红。
 */
export function probeSeedInspectNoHit(): void {
  try {
    localStorage.setItem(INSPECT_KEY, JSON.stringify({
      ok: true,
      source: "rule",
      promise: {
        rates: [{ annual: 2.6, kind: "年化", pct: 2.6, raw: "年化 2.6%" }],
        annual_max: 2.6,
        words: ["保本"],
      },
      flags: [],
      exempt_notes: [
        { kind: "exempt", term: "保本保收益承诺",
          sentence: "银行三年定期存款，年化 2.6%，保本。",
          exempt: "定期存款",
          why: "**银行存款**不在资管新规那条的适用范围里。" },
      ],
      questions: [],
      cannot_verify: [],
      baseline: null,
      read_next: [{ ask: "什么是定期存款", label: "什么是定期存款" }],
      // ★后端三档里的**第二档**（"看见了但判成豁免"），逐字照抄 `checkup.py`。
      empty_reason: "这段里有可疑措辞**被判成不算红旗** —— 命中的词落进了豁免"
        + "条件，下面列了是哪个词、因为什么。"
        + "★豁免只按**同一句**生效、也只看措辞：它证明不了对方真是"
        + "银行存款，这一点要你自己去核。",
    }))
  } catch { /* 同上：塞不进去，`insEmptyH` 会接近 0，判据自会红 */ }
}

/** 「第 N / M 步」→ `[N, M]`；解析不出来给 `[0, 0]`。 */
function stepNo(e: Element | null): number[] {
  const m = /第\s*(\d+)\s*\/\s*(\d+)\s*步/.exec(txt(e))
  return m ? [Number(m[1]), Number(m[2])] : [0, 0]
}

/** 进度条的 `aria-valuenow`；读不到给 -1（**不是 0** —— 0 是个合法进度）。 */
function barPct(e: Element | null): number {
  const v = e ? e.getAttribute("aria-valuenow") : null
  return v == null ? -1 : Number(v)
}

/** localStorage 里那两把键的**原始字符串**。读不到/读坏了给 ""。 */
function rawKey(k: string): string {
  try {
    return localStorage.getItem(k) || ""
  } catch {
    return ""
  }
}

/** ★**画像表单的交互判据**——引导末步和 `/profile` 共用这一段。
 *
 *  它替两条**只有真点过才知道**的规矩取证：
 *    ① 空表单提交：红字要出现，而且**存储里一个字都不许写**。
 *       只量红字的话，"先写存储、再报个错"照样绿 —— 而那是真的坏了。
 *    ② 填上必填项再提交：存储里要**真有内容**，写下去的还得是选项表里的值
 *       （不是"存了个空壳"）。
 *
 *  ★两个锚点名字是**写死的字符串**，故意不从 `profile.ts` 引：
 *    引的话，"选项表被改空"会让探针也一起找不到东西，
 *    于是"填不进去"和"选项表没了"变成同一个症状 —— 而它们是两回事。
 */
function formProbe(formId: string): Rec | null {
  const form = el("#" + formId) as HTMLFormElement | null
  if (!form) return null
  const submit = form.querySelector('button[type="submit"]') as HTMLButtonElement | null
  const errEl = el("#pfErr")
  const r: Rec = {
    rendered: els("#" + formId + " [data-opt]").map((n) => n.getAttribute("data-opt") || ""),
    // ★`declared` 是 `profile.ts` 那份选项表**自己说的话**，`rendered` 是从 DOM 里读的。
    //   判据比的是这两者 —— "页面少渲染了一个选项"就是这么被抓到的。
    //   ★故意**不**回传一份探针自己写死的清单：那样两份清单会一起烂，
    //     而"页面漏了"和"清单改了"就变成同一个症状。
    //   ★两边**一起空**是这条比法的天生盲区（`[] == []`），
    //     所以判据那边另有一条拿字面锚点钉住"表不是空的"。
    declared: [...ALL_OPTS],
    rows: els("#" + formId + " .pfrow").length,
    hasNote: !!el("#pfNote"),
    err0: txt(errEl),
    store0: rawKey(PROFILE_KEY),
    err1: "",
    store1: "",
    err2: "",
    store2: "",
    picked: 0,
    clicks: 0,
    // ★点**选项卡**（`[data-opt]`）冒不冒圈。用户的原话是「点击选项卡以及
    //   界面的时候要有圈状波纹」—— 只量"点卡片冒不冒"的话，把那条 document
    //   监听的 HOSTSEL 窄成只认 `a`（卡片是 `<a class="card">`，仍然命中）
    //   就照样绿，而页面上最常点的恰恰是这些选项卡。
    //   ★别拿 `.card` 当反例：选项卡**本来就在** `.card` 里（两页都是），
    //     `closest` 往上找得到那张卡 ⇒ 窄成 `.card` 是抓不住这条的（实测过，
    //     见 verify_agent.py 里那条注入的注释）。
    chipRipple: 0,
  }
  if (!submit) return r
  tap(submit)
  r.clicks = 1
  r.err1 = txt(errEl)
  r.store1 = rawKey(PROFILE_KEY)
  const pick = (name: string) => {
    const b = form.querySelector('[data-opt="' + name + '"]')
    if (b) { tap(b); r.picked = (r.picked as number) + 1 }
  }
  // ★量的是**增量**（这次点击前后各数一遍），不是"页面上有没有圈"——
  //   上面 `tap(submit)` 已经冒过圈了，只数总数的话，选项卡这一下冒不冒
  //   都看不出来（本仓库说的"对照组假绿"就是这个形状）。
  const rip0 = els(".ripple").length
  pick("基金")            // 投资兴趣的第一个（锚点，见上）
  r.chipRipple = els(".ripple").length - rip0
  pick("保守")            // 风险偏好的第一个（锚点，见上）
  tap(submit)
  r.clicks = 2
  r.err2 = txt(errEl)
  r.store2 = rawKey(PROFILE_KEY)
  return r
}
