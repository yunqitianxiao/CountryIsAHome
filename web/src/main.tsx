import { createRoot } from "react-dom/client"
// ★flushSync 在 react-dom，**不在** react-dom/client —— 写错是 ESM 链接期错误，
//   整个模块不执行、页面静默停在初始 HTML（spike 第一版就是这么白屏的）
import { flushSync } from "react-dom"
import "./index.css"
import { Agent } from "./components/Agent/Agent"
import { Home } from "./components/Home/Home"
import { Onboarding } from "./components/Onboarding/Onboarding"
import { Profile } from "./components/Profile/Profile"
import { Game } from "./components/Game/Game"
import { Dash } from "./components/Dash/Dash"
import { Portfolio } from "./components/Portfolio/Portfolio"
import { Ledger } from "./components/Ledger/Ledger"
import { LuxuryDashboard } from "./components/LuxuryDashboard/LuxuryDashboard"
import GameSimulation from "./components/GameSimulation/GameSimulation"
// ★新增的五页在 `pages/**`，不在 `components/**` 下 —— 那一层归设计系统那一版所有。
//   分开放是为了让"这一版新加的页面"在目录上一眼分得开（同 `chrome.tsx` 顶上那句）。
import { Agents } from "./pages/Agents/Agents"
import { Intraday } from "./pages/Intraday/Intraday"
import { Linkage } from "./pages/Linkage/Linkage"
import { Collect } from "./pages/Collect/Collect"
import { Psych } from "./pages/Psych/Psych"
import { Assess } from "./pages/Assess/Assess"
import { Research } from "./pages/Research/Research"
import { Inspect } from "./pages/Inspect/Inspect"
import { loadDashSync } from "./dash"
import { loadPortfolioSync, readWeights } from "./portfolio"
import { loadLedgerSync } from "./ledger"
import {
  PROBE_AMT, PROBE_SYM, defaultBuyDate, getQuoteSync, getRecSync, loadPositionsSync, readPositions,
} from "./game"
import { hasSeenOnboarding, readProfile, shouldAutoOpenOnboarding } from "./profile"
import { probeSeedGameProfile, probeSeedInspect, probeSeedInspectNoHit, probeSeedPortfolio, probeAndBeacon } from "./probe"
import { installRipple } from "./ripple"
import { applyMarket, readMarket } from "./theme"

const host = document.getElementById("root")
if (!host) throw new Error("#root 不见了")

const root = createRoot(host)

// ★五个页面共用一个壳（同一份 `web/dist/index.html`）：壳里那个 `#acc-boot` 是
//   **数字的唯一出口**，每页都要用，所以路由只能发生在客户端，不能一人发一份 HTML。
//   ★匹配的是 `pathname`，不是 hash —— 这几条都是服务端真有的路由（app.py 里有），
//     刷新、直接粘链接、被搜索引擎抓，三种情况都要落在同一页上。
//
// ★`/index.html` 和 `/next` 故意都落回主页：前者是白名单那条通路发出来的同一份壳
//   （`check_static` 要求它 200），后者是换栈期间的旧链接。**没有匹配上一律落主页**，
//   不是"落 404 页"——这条默认值本身也是一条判据的落点：
//   一个拼错的地址应该给人主页，而不是一片空白。
type PageKey = "home" | "agent" | "onboarding" | "profile" | "game" | "dash" | "portfolio" | "ledger" | "luxuryDash" | "investSim" | "agents" | "intraday" | "linkage" | "collect" | "psych" | "assess" | "research" | "inspect"

const ROUTES: [RegExp, PageKey, string][] = [
  [/^\/agent\/?$/, "agent", "智能体 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/onboarding\/?$/, "onboarding", "新手引导 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/profile\/?$/, "profile", "我的画像 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/game\/?$/, "game", "模拟投资 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/dash\/?$/, "dash", "行情总览 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/portfolio\/?$/, "portfolio", "组合体检 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/ledger\/?$/, "ledger", "预测台账 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/luxury-dash\/?$/, "luxuryDash", "奢华仪表板 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/invest-sim\/?$/, "investSim", "投资模拟可视化 · 乡e家——县乡村家庭财富管理服务平台"],
  // ★顺序与 `chrome.tsx: NEW_PAGES` 里那几条**逐条相同**：两处各写一遍的话，
  //   加下一页时必然只改一处（那一页于是"能打开但点不到"）。
  // ★这五条在 `app.py` 里**还没有对应的路由**（SPA_PAGES 里没有它们）——
  //   前端这一半是通的（客户端路由 + 导航），服务端那一半是协调者的动作。
  [/^\/agents\/?$/, "agents", "多智能体博弈 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/intraday\/?$/, "intraday", "分钟级策略 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/linkage\/?$/, "linkage", "关联检测 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/collect\/?$/, "collect", "采集监视 · 乡e家——县乡村家庭财富管理服务平台"],
  [/^\/psych\/?$/, "psych", "投资心理 · 乡e家——县乡村家庭财富管理服务平台"],
  // ★风险测评：与上一条**不同构**。上面那五条各在等一个后端通道，
  //   这一页的数据住在浏览器里（`assess.ts` 的 `licai-assess`），
  //   所以它不需要 `initial` prop，也不会有"通道还没接上"那一屏。
  [/^\/assess\/?$/, "assess", "风险测评 · 乡e家——县乡村家庭财富管理服务平台"],
  // ★行情特征研判：**不在**上面那个导航条里（`chrome.tsx: NEW_PAGES` 里没有它），
  //   所以它不需要与那五条对齐顺序。它的入口是 `/game` 页上的一张卡
  //   （`Game.tsx` 的 `#researchCard`）—— 那张卡和玩法 A 是同一件事的两半
  //   （研判 → 决策），放在游戏里比放在导航条上更说得通。
  //   ★服务端那条路由是有的（`app.py` 的 `research_page()`），所以直接粘
  //     `/research` 或者刷新都落在这一页上，不是靠客户端跳过去才有。
  [/^\/research\/?$/, "research", "行情特征研判 · 乡e家——县乡村家庭财富管理服务平台"],
  // ★建议体检：数据住浏览器里（本机缓存），**没有** `initial` prop ——
  //   与 `/assess` 同构（`useState(() => readAssess())` 那种同步读）。
  //   ★但它比 `/assess` 多一层：这一页的输入是**用户现场粘的**，探针不会打字，
  //     所以"有结论那一屏"要靠**播种**（`probeSeedInspect`）才量得到。
  //   ★它在 `chrome.tsx: NEW_PAGES` 里**有**一条，位置与这里**同序**
  //     （两处各写一遍的话，加下一页时必然只改一处：那一页于是"能打开但点不到"）。
  //   ★服务端那条路由是有的（`app.py` 的 `inspect_page()`），所以直接粘
  //     `/inspect` 或者刷新都落在这一页上，不是靠客户端跳过去才有。
  [/^\/inspect\/?$/, "inspect", "建议体检 · 别人给你的理财建议，先拿它过一遍"],
]

function currentPage(): PageKey {
  for (const [re, key] of ROUTES) if (re.test(location.pathname)) return key
  return "home"
}

const PAGE = currentPage()

// ★标签页标题：五页共用一个壳，壳里的 <title> 只有一个。不改的话每页的
//   标签页标题都是「乡e家——县乡村家庭财富管理服务平台」—— 开着两个标签就分不出哪个是哪个。
//   ★★站名很长（2026-09-22 由「理财智能体」改成「赋能青年资产观念——接好青年投资第一棒」，
//     2026-09-28 又改成「乡e家——县乡村家庭财富管理服务平台」），所以**页名必须写在前面**：
//     浏览器对 <title> 会截断，截掉的是后半截 —— 页名在前，活下来的是它，
//     截没的是那句共用的站名。反过来写的话 17 个标签会一起被截成同一串，
//     这条注释要防的那个问题就又回来了。
//   写在 render 之前（不是 useEffect）：effect 是异步的，探针那条同步路上它还没跑。
for (const [, key, title] of ROUTES) {
  if (key === PAGE) { document.title = title; break }
}

// ★圈状波纹：装在 document 上（捕获阶段），**探针模式也装** ——
//   不装的话探针那条"点一下真的冒出圈来"的判据测的就只是 `spawnRipple` 这个函数，
//   而不是"用户点得到的那条路"。这正是本仓库踩过的那个坑的形状：
//   **判据测接口、用户走按钮**。装了之后探针才有真东西可点。
// ── ★涨跌色语义也要在**第一帧之前**落定（A 股红涨绿跌 / 欧美绿涨红跌）────
//   首发那处是 `web/index.html` 的防闪内联脚本（早于模块执行 ⇒ 救的是首帧）；
//   这一行是**第二个入口**，接住"内联脚本没跑成"那种情况（CSP 挡内联、
//   那段被重构挖空），并保证终态一定对。两条路读的是**同一处 localStorage**，
//   不会分叉 —— 它们读的是用户的选择，不是一个默认值写两遍。
//   第三个入口是 `chrome.tsx` 的 MarketSwitch（用户点的时候）。
applyMarket(readMarket())

installRipple()

function view(probe: boolean) {
  switch (PAGE) {
    case "agent": return <Agent probe={probe} />
    case "dash": return <Dash probe={probe} initial={probe ? loadDashSync() : undefined} />
    // ★同 dash：探针那条路上数据**同步取好了再渲染**，否则同步探针量到的是"正在算…"
    //   —— 那不是判据变红，是判据**消失**（见 portfolio.ts 顶部那段）。
    case "portfolio": return <Portfolio probe={probe} initial={probe ? loadPortfolioSync(readWeights()) : undefined} />
    // ★台账这一页的数据在**服务端**（data/predictions.jsonl），localStorage 里没有 ——
    //   所以探针那条路的同步 loader 带 `refresh: 0`：回填会往台账里追加记录，
    //   于是"这一屏有几个数"取决于探针到达那一刻回填跑到哪儿了。
    //   那不是判据变红，是判据**空转**（同一个页面两次跑出两个数，判据只能放宽）。
    case "ledger": return <Ledger probe={probe} initial={probe ? loadLedgerSync() : undefined} />
    case "onboarding": return <Onboarding probe={probe} />
    case "profile": return <Profile probe={probe} />
    case "game":
      // ★探针那条路上，报价和推荐都是**同步取好了再渲染**的（`getQuoteSync`）：
      //   异步回来的数，同步探针量不到（量到的是"正在取数…"），
      //   那不是判据变红，是判据**消失**。见 game.ts 里那段注释。
      return <Game probe={probe} initial={probe ? {
        quote: getQuoteSync(PROBE_SYM, PROBE_AMT, defaultBuyDate()),
        rec: getRecSync(readProfile()?.interests ?? []),
        rows: loadPositionsSync(readPositions()),
      } : undefined} />
    case "luxuryDash": return <LuxuryDashboard probe={probe} />
    case "investSim": return <GameSimulation probe={probe} />
    // ★新增的五页。数据在**客户端**（`api2.ts` 那四条通道，后端还没上线），
    //   所以这里**没有** `initial` 那个 prop：那五页自己知道探针那条路要用
    //   同步 XHR（`agentui/probe.ts`），而且它们**必须**自己走 —— 那些通道
    //   现在取不到数，探针要量的正是"取不到时这一页长什么样"。
    case "agents": return <Agents probe={probe} />
    case "intraday": return <Intraday probe={probe} />
    case "linkage": return <Linkage probe={probe} />
    case "collect": return <Collect probe={probe} />
    case "psych": return <Psych probe={probe} />
    // ★风险测评同理**没有** `initial`：它的数据本来就在本地
    //   （`useState(() => readAssess())` 是同步读的），探针那条路天然就绪。
    case "assess": return <Assess probe={probe} />
    // ★行情特征研判：**没有** `initial`，理由与上面那六页同构 ——
    //   它在组件内部自己走同步路（`callSync`，`Research.tsx` 里那段 `useMemo`），
    //   所以 `main.tsx` 不需要替它先把数取好。
    //   ★探针那条路**必须**是同步的：异步回来的数，同步探针量到的是"正在取数…"
    //     —— 那不是判据变红，是判据**消失**（见 `main.tsx:151` 那段）。
    case "research": return <Research probe={probe} />
    // ★建议体检：**没有** `initial`，理由与 `/assess` 同构 ——
    //   它的数据是**同步**读本机缓存（`useState(() => getInspectSync())`），
    //   探针那条路天然就绪。★这里**不传** `initial` 还有第二个理由：
    //   传了就多一条"只有探针才走"的渲染路径，而这一页的纪律是
    //   **探针路径和普通路径走同一个渲染**（判据量的那一屏必须就是用户看的那一屏）。
    case "inspect": return <Inspect probe={probe} />
    // ★主页那一块行情条也要**同步**取好了再渲染（照 `/dash` 那条），
    //   否则同 tick 量的探针量到的是"正在取行情…" —— 判据**消失**而不是变红。
    default: return <Home probe={probe} initial={probe ? loadDashSync() : undefined} />
  }
}

// ── ★「第一次来就自动送去引导页」─────────────────────────────────────
//   **只有这一处**决定跳不跳，判断本身在 `profile.ts` 的纯函数里
//   （探针能直接问到它的真值表，见那边的注释）。
//
//   ★它**必须排在探针分支之前**，而且探针那条保命线就写在那个函数的第一行：
//     无头 firefox 每次都是全新的 profile（localStorage 永远是空的），
//     没有那条线，这一页每次都会被跳走 —— 而 `location.replace("/onboarding")`
//     会**把 `?probe=1` 一起丢掉**，落在 `/onboarding` 上的探针根本不会 beacon，
//     于是几十条渲染判据变成「没收到探针数据」。
//     （"跳到 /onboarding"和"整页白掉"在报告里长得一模一样，都是没数据。）
//   ⇒ 所以判据有两条，各自独立：`shouldAutoOpenOnboarding` 的真值表，
//     以及探针载荷里的 `bootPath` 必须还是 `/`。函数对、调用点漏了，第一条照样绿。
if (shouldAutoOpenOnboarding(location.pathname, location.search, hasSeenOnboarding())) {
  location.replace("/onboarding")
} else if (/[?&]probe=1/.test(location.search)) {
  // ── 探针路径：必须**同步**走完 ─────────────────────────────────────
  // 渲染层判据全部押在"几何在同一个 tick 里就绪"上。
  // 实测（spike，真 firefox）：setTimeout 60ms / 300ms 的快照**一条都没赶上
  // firefox 退出** —— 异步不是让判据变红，是让判据**消失**。
  // 所以这里先算出终态、flushSync 一次渲染到位，再量、再 beacon。
  // ★总览页的数是**同步取好了再渲染**的（dash.ts 顶部那段）：异步取回来再渲染，
  //   同步探针量到的是"正在取数…"，判据不是变红而是消失。
  // ★模拟投资页的探针要一份**渲染之前就存在**的画像。
  //   这一页有一条判据是"本金框不许被画像里的资产预填"，而本金框的值是
  //   **挂载时**读一次 localStorage 定下的（`useState(() => readStake())`）——
  //   渲染完再塞画像，那一帧早就过去了，判据就变成空转：
  //   没有画像时本金框当然也是空的，坏成什么样都照样绿。
  //   ⇒ 播种必须在这里，`flushSync` 之前。它**只**在 game 这一页发生：
  //     别的页面（尤其主页那张画像回显卡）要的恰恰是"用户还没填"的空态。
  if (PAGE === "game") probeSeedGameProfile()
  // ★组合体检页也要播种，而且**是另一批**（理由写在 `probeSeedPortfolio` 顶上）：
  //   这一页的持仓来自游戏页那个 localStorage 键，不播种就是"还没有持仓"那一屏 ——
  //   上面四条判据会全部落空，而"落空"在报告里长得像"页面坏了"。
  //   ★和 game 那一页一样，播种**必须**在 `flushSync` 之前：持仓是在
  //     `view(true)` 里就同步读掉的（`loadPortfolioSync(readWeights())`），
  //     渲染完再塞就来不及了。
  if (PAGE === "portfolio") probeSeedPortfolio()
  // ★建议体检页也要播种（理由写在 `probeSeedInspect` 顶上）：这一页的输入是
  //   **用户现场粘进来的**，而探针**不会打字** ⇒ 不播种的话，"有结论那一屏"
  //   （承诺/红旗/豁免/该问的/判不了的）在结构上就量不到，上面每一条判据都会空转
  //   （空转在报告里长得像"页面坏了"）。
  //   ★和上面两处一样，**必须在 `flushSync` 之前**：页面是在 `view(true)` 里
  //     就**同步**读掉缓存的（`getInspectSync()` 落进 `useState` 初值），
  //     渲染完再塞就来不及了。
  //   ★★`empty=1` 时**不播种**：那正是要量的"真空态"（localStorage 是空的、
  //     页面渲染"还没体检过"那一屏）。判据用两个地址各量一屏：
  //       `?probe=1`              → 种子版（有结论、有红旗、有豁免）
  //       `?probe=1&empty=1`      → 真空版（一个键都没有）
  //     写法照 `shouldAutoOpenOnboarding` 那种看 `location.search` 的路子。
  //     ★`nohit=1` 是第三个地址（`probeSeedInspectNoHit`）：它播"有结论、但一条
  //       红旗都没检出"那一屏，因为 `empty_reason` 那句**整页的灵魂**在种子版里是
  //       `null`（契约如此）、在真空版里根本没有报告，两个约定地址都量不到它。
  //       ★★它现在是**判据的输入**（协调者已把它写进 `check_inspect`）——
  //          **别删**：删了那边不是"少一跳"，是红。
  if (PAGE === "inspect" && !/[?&]empty=1/.test(location.search)) {
    if (/[?&]nohit=1/.test(location.search)) probeSeedInspectNoHit()
    else probeSeedInspect()
  }

  flushSync(() => {
    root.render(view(true))
  })
  probeAndBeacon()
} else {
  root.render(view(false))
}
