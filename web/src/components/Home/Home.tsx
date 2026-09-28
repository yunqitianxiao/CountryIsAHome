import * as React from "react"
import { Shell } from "../Shell/Shell"
import { SiteBanner } from "../SiteBanner/SiteBanner"
import { readProfile, type Profile } from "../../profile"
import { readAccBoot } from "../../chrome"
import { Spark } from "../Spark/Spark"
import { loadDash, type DashRow } from "../../dash"
import { dirClass, pct, price } from "../../format"
import { loadRural, type RuralGroup } from "../../rural"

/**
 * 主页（`/`）—— **导航层**，不再是那个单页智能体。
 *
 * 六张导航卡各进一个子页，加一张「我的画像」卡。定位条在最上面。
 *
 * ★六张卡的 id 和 href 是**判据的落点**，不是随手起的：
 *   `#guideCard` → `/onboarding`、`#agentCard` → `/agent`、
 *   `#graphCard` → `graph.html`、`#gameCard` → `/game`、
 *   `#portfolioCard` → `/portfolio`、`#ledgerCard` → `/ledger`。
 *   探针把它们的 href **读成字符串**回传（不是"有没有"这个布尔），
 *   于是判据能同时钉住"卡在"和"指向哪儿"两件事 ——
 *   只判"有个 id 叫 guideCard 的元素"的话，把 href 改成 `#` 照样绿。
 *
 * ★每张卡都**必须挂 `.card`**：探针数的是 `.card` 的**几何**（宽高非零），
 *   漏了类名这张卡就不计入，"至少有 3 张卡"那条判据会红 ——
 *   这是故意的，一张没渲染出来的卡本来就不该算数。
 *
 * ★行情条（`#dstrip`）是**导航层上加的第一块真数据**，插在定位条之后。
 *   三条纪律，每条都配一条判据（`verify_agent.py` 的 `check_dash` 里，
 *   因为判据要的是**打桩的**行情 —— 拿真源当判据的输入是会飘的）：
 *     ① 数必须就是 `/api/kline` 给的数（逐字），不是写死的；
 *     ② 走势线必须是**真走势**（点数够、不是一条水平线）；
 *     ③ 取不到就**明说取不到**，一行坏掉只坏一行。
 *   ★它插在 `SiteBanner` **之后**：探针的波纹那一块点的是 `.card[0]`
 *     （`probe.ts` 里 `els(".card")[0]`），而 `SiteBanner` 自己就是第一张卡。
 *     插到它前面的话，波纹那条判据量的就换成这一块了 —— 那是**换被测对象**，
 *     不是"页面改了"。
 *   ★`/dash` 的入口**已补**（2026-09-22，协调者拍的决定）：导航区里加了一张
 *     `#dashCard` 卡指向 `/dash`，位置在「预测台账」卡之后。此前这段写的是
 *     "不许做成 `<a href="/dash">`，那个缺口明确留给协调者定" —— 那个决定现在拍了，
 *     所以这段改写成"入口就是下面那张卡"。★那个决定连同它换来的东西要一起看：
 *     `/dash` 从"只能手输地址"变成"站内点得到"，而 `verify_agent.py` 的 `HOME_LINKS`
 *     也终于有了一条可以钉住它的判据（探针字段 `dashHref`）。
 *   ★行情条（下面那个 `<DataStrip/>`）**仍然不是入口**：它一个链接都没有，
 *     只是读数的地方。入口是导航区里那张卡，两件事别混。
 */
export function Home({ probe = false, initial, initialRural }: {
  probe?: boolean
  initial?: DashRow[]
  /** 「县乡村」那两类词。★探针那条路**同步**取好了传进来（照 `initial` 的先例，
   *  见 `rural.ts` 顶部）：异步渲染出来的卡，同步探针量到的是一张空卡 ——
   *  那不是判据变红，是判据**消失**，而消失掉的恰好是"33 个词会不会撑破卡"这一条。
   *  ★`null` = 取不到，`undefined` = 还没开始取（用户那条路）。两者不是一回事。 */
  initialRural?: RuralGroup[] | null
}) {
  const [rows, setRows] = React.useState<DashRow[]>(() => initial ?? [])
  React.useEffect(() => {
    // ★探针那条路是**同步**取好了再渲染的（`initial`，见 main.tsx 那段）：
    //   异步取回来再渲染，同 tick 量的探针量到的是"正在取行情…" ——
    //   判据不是变红，是**消失**。
    if (probe || initial) return
    let alive = true
    loadDash().then((rs) => { if (alive) setRows(rs) }).catch(() => { /* 逐行自报错 */ })
    return () => { alive = false }
  }, [probe, initial])
  return (
    <Shell probe={probe}>
      {/* 添加居中和放大的容器包装 */}
      <div className="center-large-container">
        <div className="center-large-card">
          {/* ★外面这层 `flex flex-col gap-4` 是**锚点**（注入靠它定位），别改。 */}
          <div className="flex flex-col gap-4">
            <SiteBanner />

            <DataStrip rows={rows} />

            {/* ★★**全站主线的第一句**（2026-09-24 加）：这个站"检验建议"而不是"发建议"。
                ★它为什么必须在**卡片列表之前**：下面那些卡是**入口**，
                  用户看它们的时候已经在猜"这是个荐股的还是教学的"。
                  把立场放在入口之前，等于先回答那个问题再给门牌。
                ★**故意不带 `.card`**：探针的 `cardRects` 只量 DOM 顺序前 5 张 `.card`
                  （`SiteBanner` 是第 1 张），多一张 `.card` 就把它挤掉一位 ——
                  那是**换被测对象**，不是"页面改了"。所以这块用普通 `<section>`。
                ★措辞和「建议体检」那一页的 `sub` / `#notePanel` 是**同一套**：
                  不预测、不推荐、不判罪；说清了"它到底做了什么"才敢说不做什么。
                ★`id="siteLead"`：给判据一个能钉住这段话的锚（措辞哪一天被改软了，
                  得有一条判据看得见，而不是靠人记得）。 */}
            <section id="siteLead" aria-label="本站立场"
                     className="rounded-lg border border-[var(--rule)] bg-[var(--paper)] p-4">
              <p className="text-sm leading-relaxed text-[var(--ink-2)]">
                <b className="text-[var(--ink)]">这个站不发理财建议 —— 它检验你收到的那条。</b>
                群里转的、亲戚介绍的、理财经理发的，原文粘进「建议体检」，它告诉你：
                这段话<b>承诺了什么</b>、哪几句是<b>红旗</b>（每条都带你<b>能自己核</b>的原文）、
                哪几句<b>看着像红旗、其实不算</b>、你<b>该问它什么</b>，
                以及<b>哪几件事它判不了</b>。它<b>不预测涨跌</b>、<b>不推荐任何产品</b>、
                <b>不替你判"这是不是诈骗"</b> —— 判定走<b>规则</b>，每条都能追到词条原文。
              </p>
            </section>

            {/* ── 这里原来有一块 `<section id="nianhua">`（2026-09-28 撤掉）────
                撤它的原因（用户看过页面之后给的原话）：
                「主页是在**空白的地方**添加乡村元素，而不是在各个元素间**穿插**
                  乡村元素，而且现在并不是每个页面都有乡村元素」。
                那一块是**真内容块**，插在 `#siteLead` 与 `#agentCard` 之间，
                把下面七张卡整体推下一屏 —— 正是"穿插"那个词描述的东西。

                ★年画这套插画语言**没废**，是从"内容流中间的一大块"**搬到不占内容位
                  的地方**：`Shell` 的全站底纹（卡片后面的留白里）+ 每页页脚那条宣纸横带。
                  两处都在 `Shell` 里 ⇒ 十七个路由页一次全有（用户第二句要的就是这个）。
                ★★**不要再往这个文件里加回来。** 要加乡村元素，加到 `Shell.tsx` 那两处；
                  加在这个文件里就又是一块"插在元素之间"的东西。 */}

            {/* ★主入口：智能体。
                原来五张卡是**同一个模子**（`p-4` + `text-base` 标题 + 同样的底），
                "智能体"这个真正的主入口和"知识图谱"长得一模一样 —— 这就是
                "stock card grids"那个反模式：信息有主次，版面却没有。
                现在它独占一整行、内边距大一档、标题大一档、底上带一层极淡的品牌色。
                ★`bg-accent/5` 只有 5%，是**底色**不是**色号**：这个站的绿在 A 股里
                  是"跌"，但那个语义属于 K 线和涨跌色（`--up`/`--down`），
                  不属于导航卡的底。5% 的绿灰在肉眼上读不出"跌"，只是个"这张不一样"。
                ★`card linkcard block` 这个**前缀**是锚点，后面的类随便改。 */}
            <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-accent/5 p-6 no-underline"
               href="/agent" id="agentCard">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <span className="kicker">智能体</span>
                  <h2 className="mt-1 text-lg font-semibold text-[var(--ink)]">有问题直接问它</h2>
                  <p className="mt-1 text-sm text-[var(--ink-2)]">
                    查词、看行情、跑一段 K 线预测，它把结果翻成人话讲给你，还会告诉你
                    <b>它没把握的地方在哪儿</b>。
                  </p>
                </div>
                {/* 指向性：主入口要有"点这儿进去"的样子，不然和下面三张还是同级 */}
                <span className="kicker shrink-0 whitespace-nowrap" aria-hidden="true">开始 →</span>
              </div>
            </a>

            {/* ★两张主功能大卡（2026-09-22 加，用户点名「多智能体博弈 / 分钟级策略
                挪到主页，变成大卡片」）。此前这两页**只有页头那排小 chip** 进得去
                （`chrome.tsx` 的 `FeatureNav`），从主页看是两个不存在的东西。
                ★和上面那张智能体卡**同档尺寸**（`p-6` + `text-lg`）—— "大卡"这件事
                  只有尺寸能表达；底色仍用中性的 `--card`，绿底只留给智能体那一个
                  第一入口，否则三张一样绿就又没有主次了。
                ★`id="agentsCard"` / `id="intradayCard"` 照兄弟卡的 `<页面>Card` 规矩起，
                  名字对齐 `main.tsx` 的路由（`/agents`、`/intraday`）。
                  ★这两个 id **探针还没读**（`probe.ts` 的 `out.agentsHref` 之类都还没有），
                    所以 `verify_agent.py` 的 `HOME_LINKS` 里**没有**它们 —— 也就是
                    这两张卡目前**没有判据守着 href**（把 href 改成 `#` 不会红）。
                    补字段 + 补表项是协调者的动作（`probe.ts` 与 `verify_agent.py`
                    都不归改主页的人动），已写进交付说明。
                ★位置在 agentCard **之后**、次级卡排之前：主入口聚在一起。
                  代价说清楚 —— 探针的 `cardRects` 只量 **DOM 顺序前 5 张** `.card`，
                  插在这里把第 3、4 个名额从「新手引导 / 知识图谱」换成了这两张。
                  那一条判据量的是"几何非零"（`w>40 且 h>20`），不是"是哪张卡"，
                  所以它照样绿；但"前 5 张组成变了"这件事本身要记一笔，
                  万一将来有人把它收紧成"按 id 逐张比"，就是这里先动的手。
                ★`className="card linkcard block` 这个**前缀照抄**：它同时是那条
                  「把 `card` 类名削掉」注入（`WebFault(..., 99)` 全文替换）的落点 ——
                  新卡带上这个前缀，那条注入的覆盖面就跟着变大，注入本身不用改。 */}
            {/* ★三张卡（2026-09-22 加第三张风险测评）。用 `md:grid-cols-2 lg:grid-cols-3`
                而不是 `md:grid-cols-3`：768–1024px 那一档三列下来每张只剩 ~240px，
                里面还要塞 `p-6` 和一段说明 —— 中文能断行、不会横向溢出，
                但会挤到几乎读不了。让它在那一档落成"两列 + 一张换行"。
                ★窄屏（<768px）默认就是单列，这一档从来没问题。 */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-6 no-underline"
                 href="/agents" id="agentsCard">
                <div className="flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <span className="kicker">多智能体博弈</span>
                    <h2 className="mt-1 text-lg font-semibold text-[var(--ink)]">九个角色吵一架，再给结论</h2>
                    <p className="mt-1 text-sm text-[var(--ink-2)]">
                      多头、空头、基本面、技术面、舆情各有各的说法，风控官握着<b>一票否决</b>，
                      投资经理最后汇总裁决。谁被点名谁就得回应，权重按这轮表现动态调。
                    </p>
                  </div>
                  <span className="kicker shrink-0 whitespace-nowrap" aria-hidden="true">进去 →</span>
                </div>
              </a>

              <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-6 no-underline"
                 href="/intraday" id="intradayCard">
                <div className="flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <span className="kicker">分钟级策略</span>
                    <h2 className="mt-1 text-lg font-semibold text-[var(--ink)]">这一分钟，量价在说什么</h2>
                    <p className="mt-1 text-sm text-[var(--ink-2)]">
                      盘中量价怎么看：蜡烛、成交量、均价线和价值区共用一根横轴，
                      每条策略信号都带一条<b>你能自己动手复核的算术</b>。方向不是胜率。
                    </p>
                    {/* ★这一句**故意不提几档、也不提"日线"**（2026-09-22，
                        与 ylf-88 定的口径）：档位是服务端 `periods` 派生的，港美股/加密
                        在本机没有分钟源时会降级成日线 ⇒ 写死「5/15/30 三档」在那些市场
                        上就是**主页替 Intraday 页说了一句它会打自己脸的话**。
                        同一件事写在两处必然漂，而权威展示（周期表 + 降级横幅）在页内、
                        由实测数据驱动 ⇒ 入口卡只说到"看盘中量价"这一层为止。 */}
                  </div>
                  <span className="kicker shrink-0 whitespace-nowrap" aria-hidden="true">进去 →</span>
                </div>
              </a>

              {/* ★风险测评卡（2026-09-22 加，用户点名要一个投资者风险测评模块）。
                  与上面两张**同档尺寸**、同中性底色，理由同上面那段注释。
                  ★文案的分寸是这张卡唯一难的地方：这一页的结果是**本站教学口径**
                    的分档，不是监管原文（见 `assess.ts` 顶部那段）。所以标题说
                    "由哪几件事决定"而不是"你是哪一型"—— 后者读起来像一个结论，
                    而这套十道自答题撑不起一个结论。卡上那句"不是官方问卷"不能删。
                  ★id `assessCard` 照 `<页面>Card` 规矩起，对齐 `/assess`。
                  ★同上面两张：`probe.ts` 里**没有** `assessHref`，所以这张卡的 href
                    **没有判据守着**（改成 `#` 不会红）。补字段 + 补 `HOME_LINKS`
                    表项是协调者的动作，已写进交付说明。
                  ★`cardRects` 前 5 张 `.card` 的名单又变了一次（现在末位是
                    `guideCard`），照上面那段注释的记账方式记一笔 —— 那条判据量的
                    仍是"几何非零"，不会因为这次改动变红。 */}
              <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-6 no-underline"
                 href="/assess" id="assessCard">
                <div className="flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <span className="kicker">风险测评</span>
                    <h2 className="mt-1 text-lg font-semibold text-[var(--ink)]">你的承受能力，由哪几件事决定</h2>
                    <p className="mt-1 text-sm text-[var(--ink-2)]">
                      十道题问的是这笔钱占你资产多少、多久不用、你见过什么样的跌 ——
                      <b>能核对的事</b>，比「我觉得我能扛」实在。本站的教学口径，不是官方问卷。
                    </p>
                  </div>
                  <span className="kicker shrink-0 whitespace-nowrap" aria-hidden="true">测一下 →</span>
                </div>
              </a>
            </div>

            {/* ★次级三张：一排三个，窄屏塌成一列。用 `md:` 而不是写死宽度：
                判据量的 `overflowX`（scrollWidth - clientWidth）在 420px 那一档
                只要有一张卡不肯收缩就会红 —— 而"卡不肯收缩"最常见的成因
                正是卡里有个不肯换行的长串。 */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              {/* ★「县乡村」场景入口卡（2026-09-28 加）。放在这一组的第一张。
                  为什么是这里 —— 三个坑一次躲开：
                  · `cardRects` 只量 DOM 顺序**前 5 张** `.card` 的几何
                    （= 定位条 + 行情条 + 主入口 + agents + intraday），
                    这一张是本文件里第 7 张 ⇒ 不进那个名单。
                  · `check_ripple` 点的是 `els(".card")[0]`（今天 = `SiteBanner`），
                    只要不在 `SiteBanner` **之前**插 `.card` 就不影响它。
                  · `.card` 计数判据只有下界 `>= 3`。
                  ★它横跨整行（`md:col-span-2 lg:col-span-4`）：这是"场景入口"，
                    不是第 N 个功能入口，占一整行才立得住。 */}
              <RuralCard probe={probe} initial={initialRural} />

              <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4 no-underline"
                 href="/onboarding" id="guideCard">
                <span className="kicker">新手引导</span>
                <h2 className="mt-1 text-base font-semibold text-[var(--ink)]">第一次来？先看这页</h2>
                <p className="mt-1 text-sm text-[var(--ink-2)]">
                  这个站是干什么的、三步怎么用、Kronos 模型是什么、以及<b>它做不到什么</b>。
                  一步步走完，顺便把你的投资兴趣和情况填一下。
                </p>
              </a>

              <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4 no-underline"
                 href="graph.html" id="graphCard">
                <span className="kicker">知识图谱</span>
                <h2 className="mt-1 text-base font-semibold text-[var(--ink)]">家庭财富管理词典图谱</h2>
                <p className="mt-1 text-sm text-[var(--ink-2)]">
                  255 个理财术语，按「属于 / 对比 / 前提」连成一张图。想知道一个词和别的词什么关系，来这儿。
                </p>
              </a>

              <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4 no-underline"
                 href="/game" id="gameCard">
                <span className="kicker">模拟投资</span>
                <h2 className="mt-1 text-base font-semibold text-[var(--ink)]">拿虚拟本金练手</h2>
                <p className="mt-1 text-sm text-[var(--ink-2)]">
                  本金是虚拟的（跟你在别处填的资产无关），行情是<b>真抓的</b>。
                  回看三个月前买它会赚多少，或者今天建仓、一天天跟着走。
                </p>
              </a>

              {/* ★这张卡指向**你在游戏页买的那些**：体检的组合就是它们，不另建一套持仓。 */}
              <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4 no-underline"
                 href="/portfolio" id="portfolioCard">
                <span className="kicker">组合体检</span>
                <h2 className="mt-1 text-base font-semibold text-[var(--ink)]">看看这样配有没有问题</h2>
                <p className="mt-1 text-sm text-[var(--ink-2)]">
                  集中度、相关性聚集（哪几只是"名字不同、走势一条线"）、波动回撤、Beta 与压力情景。
                  <b>只算数、不给健康总分</b>，算不了的明说算不了。
                </p>
              </a>

              {/* ★这张是**唯一一处**展示"模型过去准不准"的地方，来源是服务端那份台账。
                  它和主页面上那句回测实测是同一件事的两种说法，所以两处都连着基线。 */}
              <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4 no-underline"
                 href="/ledger" id="ledgerCard">
                <span className="kicker">预测台账</span>
                <h2 className="mt-1 text-base font-semibold text-[var(--ink)]">它过去猜得怎么样</h2>
                <p className="mt-1 text-sm text-[var(--ink-2)]">
                  每一次预测都记下来，到期拿<b>真实行情</b>打分。命中率永远和「无脑喊涨」并排显示 ——
                  没有基线的命中率说明不了任何事。在线记录和离线回填<b>分开算，不混成一个数</b>。
                </p>
              </a>

              {/* ★「建议体检」那张卡（2026-09-24 加）。照抄上面几张的模子：
                  同一个 `.card linkcard block` 前缀 + `.kicker` + `<h2>` + `<p>`，
                  一个样式类都没自创。
                  ★`id="inspectCard"`：命名照兄弟卡的 `<页面>Card` 规矩
                    （对齐 `main.tsx` 里的 `/inspect`）。探针读 `a#inspectCard`
                    的 **href 字符串**（`out.inspectHref`）—— 只判"元素在不在"的话，
                    把 href 改成 `#` 照样绿，而用户点下去到不了那一页。
                  ★位置在**台账卡之后**（协调者定点）：探针的 `cardRects` 只量
                    DOM 顺序前 5 张 `.card`，插在这里 ⇒ 那前 5 张与改动前逐张相同，
                    这一张不参与"几何非零"那条判据，不引入任何多余变量。
                  ★它卖的东西**和别的卡不一样**：别的卡各提供一种"看"（行情、图谱、
                    组合、台账），这一张提供的是**检验你手上那条建议** ——
                    所以文案里没有一句"预测"、没有一句"推荐"。
                  ★`className` 开头这个 `card linkcard block` 前缀照抄还有一层原因：
                    它同时是那条「把 `card` 类名削掉」注入（`WebFault(..., 99)` 全文替换）
                    的落点 —— 新卡带上这个前缀，那条注入的覆盖面就跟着变大，注入不用改。 */}
              <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4 no-underline"
                 href="/inspect" id="inspectCard">
                <span className="kicker">建议体检</span>
                <h2 className="mt-1 text-base font-semibold text-[var(--ink)]">别人给你的建议，先过一遍</h2>
                <p className="mt-1 text-sm text-[var(--ink-2)]">
                  把那条建议的原文粘进来：它承诺了什么、哪几句是红旗（带原文）、
                  哪几句<b>看着像红旗其实不算</b>、你该问什么、哪几件事它判不了。
                  <b>不给预测、不推产品</b> —— 判定走规则，每条都能追到词条原文。
                </p>
              </a>

              {/* ★这张是 `/dash` 的入口（2026-09-22 补）。照抄上面那五张的模子：
                  同一个 `.card linkcard block` 前缀 + `.kicker` + `<h2>` + `<p>`，
                  一个样式类都没自创。
                  ★`id="dashCard"`：命名照兄弟卡（`agentCard`/`guideCard`/…）的
                    `<页面>Card` 规矩。★`/dash` 那一页自己有个 `id="dashCard"` 的
                    `<section>`（那只在 `/dash` 上存在）—— 两者**不同页、不会同时出现**，
                    而且探针读的是 `a#dashCard`（锚点），选不到那个 section。
                  ★放在**最后**（台账卡之后）是刻意的：探针的 `cardRects` 只量
                    **DOM 顺序前 5 张** `.card`，插在末尾 ⇒ 那前 5 张与改动前逐张相同，
                    这一张不参与"几何非零"那条判据，不引入任何多余变量。 */}
              <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4 no-underline"
                 href="/dash" id="dashCard">
                <span className="kicker">行情总览</span>
                <h2 className="mt-1 text-base font-semibold text-[var(--ink)]">一屏看完几只标的</h2>
                <p className="mt-1 text-sm text-[var(--ink-2)]">
                  A股、指数、港股、美股、加密各一只：最新收盘价、近 5/20/60 日涨跌、
                  年化波动、区间位置，底下还有一条对比条。每个数都是<b>当场取的</b>，
                  取不到就明说取不到。
                </p>
              </a>
            </div>

            <ProfileCard />
          </div>
        </div>
      </div>
    </Shell>
  )
}

/** 行情条那一行。★坏掉的行**一个数字都不给**，照实说取不到 ——
 *  这是 `game._fail()` 与 `/dash` 那个坏行先例的同一条规矩：
 *  "这一行没取到"和"这只标的今天没动"在页面上不许长得一样。 */
function StripRow({ r }: { r: DashRow }) {
  const s = r.s
  if (!s) {
    return (
      <div className="flex min-w-0 items-center gap-2 rounded border border-[var(--rule-2)] bg-[var(--paper)] px-3 py-2"
           data-sym={r.sym}>
        <span className="min-w-0 flex-1 truncate text-xs text-[var(--ink-3)]">{r.sym}</span>
        <span className="shrink-0 text-xs text-[var(--f5)]" data-k="err">这一只没取到</span>
      </div>
    )
  }
  return (
    <div className="flex min-w-0 items-center gap-2 rounded border border-[var(--rule-2)] bg-[var(--paper)] px-3 py-2"
         data-sym={r.sym}>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs text-[var(--ink-3)]">{s.name || s.code || r.sym}</div>
        {/* ★`data-k="px"` / `data-k="chg"` 是**判据的落点**：探针读的是这两个节点的
            **可见文本**，判据拿它跟判据自己打 `/api/kline` 拿到的数逐字比。
            读文本而不是读 `data-*` 属性：属性可以是另一个变量写进去的，
            而这两个字就是用户眼睛看到的那两个。 */}
        <div className="tnum text-base leading-tight text-[var(--ink)]" data-k="px">
          {price(s.last_close)}
        </div>
      </div>
      <span className={`tnum shrink-0 text-sm ${dirClass(r.chg1)}`} data-k="chg">
        {pct(r.chg1)}
      </span>
      <Spark pts={r.spark} />
    </div>
  )
}

/** 首页行情条。**读数的地方，不是入口**（`/dash` 的入口是导航区那张 `#dashCard` 卡，
 *  见 `Home` 顶上那段 —— 那一块讲的是"为什么入口补了、而这条仍然不是入口"）。 */
function DataStrip({ rows }: { rows: DashRow[] }) {
  const accHtml = React.useMemo(readAccBoot, [])
  return (
    <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4"
             id="dstrip" aria-label="行情条">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="kicker shrink-0">实时行情</span>
        <span className="text-xs text-[var(--ink-3)]">
          A股 / 指数 / 港股 / 美股 / 加密各一只，收盘价是<b>真抓的</b>，走势是最近三个月
        </span>
      </div>
      {rows.length ? (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
          {rows.map((r) => <StripRow key={r.sym} r={r} />)}
        </div>
      ) : (
        <p className="text-xs text-[var(--ink-3)]" id="dstripLoading">正在取行情…</p>
      )}
      {/* ★底下一行实测数字**不在这里手写**：它是服务端现读现填的那一份
          （`agent/accuracy.py` → 壳里的 `#acc-boot`）。页面上的第二份真源会
          静默变成假话 —— 换了数只有一处跟着动，另一处看着一模一样。 */}
      {accHtml ? (
        <div className="acc-line mt-3 border-t border-[var(--rule-2)] pt-2 text-xs text-[var(--ink-3)]"
             dangerouslySetInnerHTML={{ __html: accHtml }} />
      ) : null}
    </section>
  )
}

/** 「我的画像」卡：主页上回显用户填过的投资兴趣 / 风险偏好 / 资产区间。
 *
 *  ★`readProfile()` 放在 effect 里而不是渲染期间：localStorage 是**浏览器**的东西，
 *    渲染期间读它会让这个组件在 SSR / 测试环境下直接抛。
 *  ★读不到就显示"还没填"，**不编一个默认画像**（见 profile.ts 那段）。
 *    这条不是修辞，是一条判据的落点：探针读 `#profileEcho` 的**文本**，
 *    空态那句里必须出现「还没填」、且**不含 `｜` 这个分隔符** ——
 *    后者才是"有没有编出内容"的判据（拿一段写死的 demo 画像占位，
 *    文案长得像正常态，肉眼分不出）。
 */
function ProfileCard() {
  const [p, setP] = React.useState<Profile | null>(null)
  React.useEffect(() => { setP(readProfile()) }, [])
  const bits = p ? [p.interests.join("、"), p.risk, p.assets].filter(Boolean) : []
  return (
    /* ★收成一条横条（原来是和三张次级卡一样的竖排卡）：它是**回显**
       ——"你填过的，智能体记得" —— 不是第四个入口，不该占一张卡的位置。
       ★`py-3` 比卡片的 `p-4` 窄一档。会不会踩"卡片几何非零"那条判据？
         不会 —— 那条量的是 **DOM 顺序前 5 张** `.card` 的 rect（要 `w>40 且 h>20`），
         而**定位条自己就是一张 `.card`**（`SiteBanner` 的 `className` 以 `card` 开头），
         占掉第 1 个名额，于是前 5 张 =「定位条 + 四张导航卡」，这一张排第 6。
         ★顺带记一笔：实测 `cards` 报的是 **6** 张，不是"四张导航 + 画像"五张 ——
           `SiteBanner` 那张一直在数里，只是从没人注意过。
       ★`flex-wrap`：420px 那一档会折成两行，而不是把长句挤出视口。 */
    <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] px-4 py-3 no-underline"
       href="/profile" id="profileCard">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="kicker shrink-0">我的画像</span>
        <h2 className="text-base font-semibold text-[var(--ink)]">你填过的，智能体记得</h2>
        {p && bits.length ? (
          <p className="text-sm text-[var(--ink-2)]" id="profileEcho">
            {bits.join(" ｜ ")}　<b>点这里改</b>
          </p>
        ) : (
          <p className="text-sm text-[var(--ink-2)]" id="profileEcho">
            还没填，去填 →　填了之后智能体回答会带上你的情况，模拟投资也会按你的兴趣推荐标的。
          </p>
        )}
      </div>
    </a>
  )
}

/** 「县乡村」场景入口卡：把词表里 O 类「家庭财务场景」和 P 类「县域与乡村」两组词
 *  摆到首页上，让人一眼看出**这个站是按农村家庭的过法组织的**，而不是通用理财站。
 *
 *  ★★`className` 必须以 `card linkcard block` 开头 —— 这不是风格问题。
 *    `verify_agent.py` 有一条故障注入拿这个前缀做**全文替换**（把全站卡片削平成
 *    `card block`）来验"卡片样式没了会不会红"。新卡不以此前缀开头 ⇒ 注入**够不着它**
 *    ⇒ 那条判据在它身上一直是绿的，`--selftest` 读起来像"判据坏了"，实际是
 *    "注入被跑赢了"。三处先例（agentCard/agentsCard/…）都在注释里记过这一条。
 *
 *  ★数据**不硬编码**：词名从 `graph_data.json` 取（词表唯一真源），理由见 `rural.ts`。
 *    这里只管三态渲染，分组逻辑在那一层。
 *
 *  ★**空态是三句不同的话**，不是一个"暂无数据"：正在取 / 取不到 / 词表里真没有。
 *    本项目铁律 —— "空"与"坏"必须是两句不同的话（`agentui/State.tsx` 的 `Empty` 与
 *    `Degraded` 就是这么分的）。把"取不到"说成"没有"是**替用户下了一个假的结论**：
 *    他会以为这两类词不存在。
 *
 *  ★整张卡是一个 `<a>` ⇒ 里面的词条 chips **必须是 `<span>`**：`<a>` 里嵌 `<a>`
 *    是非法 HTML（浏览器会把它们拆开，DOM 结构跟写的不一样）。
 *    点 chip 不做定位，整卡跳 `graph.html`（词表页）。
 *
 *  ★金色（`--rural`）在这里**只做一条左色条**，不做文字 ——
 *    理由见 `index.css` 的 `--rural` 那段（没人守它的对比度，就不给自己挖坑）。
 *
 *  ★★这里原来还挂着**一枚稻穗插画**（放在一块宣纸底上），2026-09-28 撤掉了：
 *    它是"一棵插画，插在这张卡的内容里"，与用户说的"不要在各元素间穿插乡村元素"
 *    是同一件事（只不过尺度小一号）。**装饰性的年画现在只在 `Shell` 那两处**：
 *    全站底纹 + 页脚横带，都不占内容位。
 *    ⇒ 这张卡留下的"乡村"全在**内容**上（县乡村 kicker + O/P 两组的词条本身），
 *      不在装饰上。 */
function RuralCard({ probe, initial }: { probe: boolean; initial?: RuralGroup[] | null }) {
  const [groups, setGroups] = React.useState<RuralGroup[] | null | undefined>(initial)
  React.useEffect(() => {
    /* ★探针那条路的数据已经在**同一个 tick** 里取好传进来了（`main.tsx` 调
       `loadRuralSync()`）。这里若再发一次异步请求，同步探针量到的就是"正在取词表…"
       那张空卡 —— 那不是判据变红，是判据**消失**，而且消失掉的恰恰是最该量的那一版
       （33 个词会不会把卡撑破）。空卡当然撑不破。
       ★`undefined` 与 `null` 是两件事：前者"还没开始取"（用户那条路），
       后者"取不到"（已经问过了，没问着）。 */
    if (probe || initial !== undefined) return
    let alive = true
    loadRural()
      .then((g) => { if (alive) setGroups(g) })
      .catch(() => { if (alive) setGroups(null) })
    return () => { alive = false }
  }, [probe, initial])

  const total = groups?.reduce((a, g) => a + g.terms.length, 0)
  return (
    <a className="card linkcard block rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4 no-underline md:col-span-2 lg:col-span-4"
       href="graph.html" id="ruralCard">
      <div className="flex gap-3">
        {/* 左色条：稻穗金。`self-stretch` 是 flex 默认值 ⇒ 不用写，高度自然撑满。 */}
        <span aria-hidden="true" className="w-1 shrink-0 rounded-full" style={{ background: "var(--rural)" }} />
        <div className="min-w-0 flex-1">
          <span className="kicker">县乡村</span>
          <h2 className="mt-1 text-base font-semibold text-[var(--ink)]">家里的账，县里的事</h2>

          {groups === undefined ? (
            <p className="mt-1 text-sm text-[var(--ink-2)]">正在取词表…</p>
          ) : groups === null ? (
            <p className="mt-1 text-sm text-[var(--ink-2)]">
              词表没取到 —— 这不代表这两类没有词，是这份名单没读到，刷新一下再试。
            </p>
          ) : total === 0 ? (
            <p className="mt-1 text-sm text-[var(--ink-2)]">
              词表读到了，但这两类现在一个词都没有。
            </p>
          ) : (
            <div className="mt-2 flex flex-col gap-2">
              {groups.map((g) => (
                <div key={g.code} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="kicker shrink-0">{g.name}</span>
                  {g.terms.map((t) => (
                    <span key={t}
                          className="rounded border border-[var(--rule)] px-1.5 py-0.5 text-xs text-[var(--ink-2)]">
                      {t}
                    </span>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </a>
  )
}
