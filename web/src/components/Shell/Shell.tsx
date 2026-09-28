import * as React from "react"
import { Badges, FeatureNav, MarketSwitch, ThemeSwitch } from "../../chrome"
import { ChanZhiLian, DaoSui, HuiWen, JiShi, LiangCang, TongQian, WaFang } from "../Nianhua/Nianhua"

/**
 * 站点外壳：年画底纹 + 页头 + 主栏容器 + 年画页脚。**每一页都走它**。
 *
 * ★★**动这个文件前先看这一段**（2026-09-28 补）：外壳里有**三处是故障注入的
 *   逐字锚点**，改一个字符就有两条判据变成装饰（`check_fault_anchors` 会报红）：
 *     ① 页头那排的宽度类 —— `max-w` 后面那个 clamp 宽度
 *     ② 主栏 `<main>` 上的**同一个**宽度类
 *        （这两条是「只把宽度写死回 1180px」那对注入的落点，用来验
 *         「内容跟着屏幕长」和「页头与主栏逐像素对齐」两条判据）
 *     ③ 回主页那个链接的 `href` 与 `id`（主页自己不渲染它，见下面 `back` 那段）
 *   **往后接新类名是安全的**（锚点比的是前缀），**改中间那一段不是**。
 *   底纹与页脚都是**新增的兄弟节点**，一条锚点都没碰到。
 *
 *   ★★**上面 ①②③ 是描述、不是原文，这是有意的**：这一段注释的**第一版**
 *      把三条锚点逐字抄了进来，于是锚点在文件里各出现 **2 次**（注释一次、
 *      真代码一次）⇒ `check_fault_anchors` 当场报红「锚点在 Shell.tsx 里
 *      出现 2 次（注入只替换第一处，而"第一处"取决于文件里的先后顺序）」。
 *      —— 那段提醒**自己破坏了它要提醒的东西**。
 *      ⇒ 纪律（与 `graph_template.html` 顶部那段同一条）：**harness 是按文本
 *        正则读的，注释对它不透明**。要指认锚点就**指到 `verify_agent.py`
 *        里那几条 `fault(...)` 的 `old` 值**，**不要在本文件里复述它**。
 *      ★想改宽度的话，先 `grep -n 'clamp(1180px' verify_agent.py` 看那两条
 *        注入的原文，再决定怎么改；改完必须重跑 `verify_agent.py --no-render`。
 *
 * ★为什么非要抽出来，而不是每页各写一遍：
 *   `verify_agent.py` 里有两条判据量的是**页头和主栏的距离关系** ——
 *   「大屏：内容真的跟着屏幕长（主栏 ≥ 视口 70%）」和
 *   「大屏：页头那排和主栏逐像素对齐（内容框左边一致）」。它们在**这一层**上量。
 *   各页各写一遍的话，判据只会量到其中一页，另外四页的 max-w 写歪了没人知道。
 *   ⇒ 这一条 README 里那句"改宽度的地方是两处，改一处等于没改"在这里有了
 *     第三个落点：**两处都得在这一个文件里**。
 *
 * ★`back`：非主页的那几页，站名做成回主页的链接（`#homeLink`）。
 *   主页自己**不渲染**它 —— 一个指向自己的链接是死链的一种，而且探针那条
 *   「智能体页有回主页的链接」的判据就没法用"主页上也一样有"来蒙混。
 */
export function Shell({ probe, back = false, children }: {
  probe: boolean
  back?: boolean
  children: React.ReactNode
}) {
  return (
    // ★这一版把 `bg-[var(--paper)]` 这类**任意值写法**换成语义 token 类
    //   （`bg-canvas` / `border-line` / `bg-surface` / `text-fg` / `text-faint`）。
    //   两者编译出来的 CSS 是等价的（都落在 `@layer utilities`、
    //   都是 `var(--…)`），换的理由是**命名层**：`--paper` 是个色板位置，
    //   而"这一页的底"是个角色；角色名才能在别处复用（同一个 `--paper`
    //   在别的地方可能是"凹槽"`--well`）。见 `index.css` 的语义层那段。
    <div className="relative isolate min-h-screen bg-canvas">
      {/* ── 年画底纹（2026-09-28 加）──────────────────────────────────────
          用户的原话：「主页是在**空白的地方**添加乡村元素，而不是在各个元素间穿插
          乡村元素，而且现在并不是每个页面都有乡村元素」。

          ⇒ 乡村视觉**从内容流里搬出来**，落到两处**不占内容位**的地方：
             (1) 这一层 —— 坐在 `bg-canvas` 之上、所有卡片之下，只从留白里透出来；
             (2) 每页的 `<footer>`（见文件末尾那段）。
          两处都在 `Shell` 里 ⇒ 十七个路由页一次全有（这就是第二句要的那件事）。
          ★原来主页上那一整块 `<section id="nianhua">` 已经删掉：它是一个真内容块，
            插在两张卡**之间**，把下面的卡全推下一屏 —— 正是"穿插"。

          ★`relative isolate` 是**这一层能看见的前提**，两个类缺一不可：
            `isolate` 让这层 div 自己成为一个层叠上下文，`-z-10` 于是只在它内部
            与兄弟比大小 —— 上色顺序变成「父底色 → 负 z 的装饰 → 正文」。
            少了 `isolate`，`-z-10` 会跑到**根**层叠上下文里跟 `.bg-canvas` 比，
            结果是**装饰被底色盖住、整层声明了等于没声明**（页面照常、什么都不报）。
          ★`absolute` 不是 `fixed`：它跟着页面走，是"纸的底"而不是"贴在屏幕上的 HUD"。
          ★`overflow-hidden` + `inset-0`：纹样一律用负偏移探到画面外，
            没有它 420px 那两档的 `scrollWidth ≤ clientWidth` 判据会被这层顶红。
          ★整层 `aria-hidden` + `pointer-events-none`：纯装饰，不进无障碍树、
            也不吃点击（卡片上的按钮照常点得到）。 */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
        <DaoSui size={560} className="absolute -left-28 top-[7%] opacity-[0.10]" />
        <ChanZhiLian size={340} className="absolute -right-20 top-[34%] opacity-[0.09]" />
        <TongQian size={360} className="absolute -left-24 top-[62%] opacity-[0.09]" />
        <DaoSui size={420} className="absolute -right-24 top-[82%] opacity-[0.10]" />
      </div>

      <header className="sticky top-0 z-30 border-b border-line bg-surface">
        <div className="bar mx-auto flex max-w-[clamp(1180px,78vw,2200px)] flex-wrap items-center gap-2.5 px-5 py-2.5">
          <h1 className="text-lg font-semibold text-fg">
            {back
              ? <a href="/" id="homeLink" className="no-underline text-fg">乡e家</a>
              : "乡e家"}
            <span className="ml-2 text-xs font-normal text-faint">多智能体与金融知识图谱驱动的县乡村家庭财富管理平台</span>
          </h1>

          {/* ★能力标称，**故意放在 #badges 外面**：那个盒子每 30 秒被整排重建一次，
              断网时还会整个换成"连不上本地服务"，放进去这枚就没了。
              title 里那句"没证明它更强"不是修辞，是回测的结论，别删。 */}
          {/* ★下面这一段的**类名写法是冻住的**，不许改成语义 token 类。
              理由：`verify_agent.py` 里那条故障注入（"把「K线预测」徽章挪进
              #badges"）把这一整段当**逐字锚点**，锚点一漂，那条注入就变成装饰
              （它不报错、也不打 ⊘，只是不再能打红 —— 静默失效）。
              ★行号**会漂**：`verify_agent.py` 被别的会话频繁编辑，本轮那处锚点
                已经从 `:9953` 漂到 `:10037`（同一段的三处锚点在 `:10037` /
                `:10045` / `:10062`）⇒ 要核这一条，**拿上面那句判据描述去
                `grep`**，别按行号找（写死行号的注释下一轮就是错的）。
              实测：2026-09-22 我把 `border-[var(--accent)] bg-[var(--card)]`
              改成 `border-accent bg-surface` 之后，全量跑立刻多出一条红：
              「★每条注入的锚点都在目标文件里」→ Shell.tsx 里找不到锚点。
              ★注意这里**不是**写死的色值：`var(--accent)`/`var(--card)` 都是
                 token 引用，两套主题各自成立 —— 所以留着它并不违反
                "组件里不许出现写死的色值"那一条。
              ★要动的顺序是"先改判据的锚点、再改这里"，不是反过来。
                改锚点是协调者的事（`verify_agent.py` 禁改）⇒ 已写进交付说明。 */}
          <span
            className="badge feat inline-flex items-center gap-1.5 whitespace-nowrap rounded border border-[var(--accent)] bg-[var(--card)] px-2 py-1 text-xs font-semibold text-[var(--accent)]"
            id="featBadge"
            title="本地小模型 Kronos 现场采样一段未来K线（几十秒）。回测没能证明它比「无脑喊涨」强，只当形态参考，不是行情。"
          >
            <i className="h-[7px] w-[7px] flex-none rounded-full bg-[var(--accent)]" />
            K线预测
          </span>

          <div className="badges ml-auto flex flex-wrap items-center gap-2" id="badges">
            <Badges probe={probe} />
          </div>

          {/* ★新增五页的入口。**装在页头**（每一页都过 `Shell`），而不是各页
              正文里各写一遍 —— 这样主页也点得到它们（`chrome.tsx` 导出了
              `FeatureNav` / `NEW_PAGES`，那五条与 `main.tsx` 的 ROUTES
              必须逐条同序）。
              ★`current` 传 `location.pathname`：这个 SPA 认的是 **pathname**
                （不是 hash），见 `main.tsx` 里 ROUTES 上面那段。
              ★**为什么放在 `#badges` 之后、而不是徽章左边**：上面那枚
                `#featBadge` 的 <span> 与 `#badges` 那个 div **之间**是一整条
                故障注入的逐字锚点（见更上面那段注释）—— 中间插任何东西，
                那条注入都站不住。实测 2026-09-22 插了一次，全量跑立刻多出
                一条红「锚点找不到」。⇒ 新东西一律放在 `#badges` **之后**。
              ★已知重复：`agentui/Frame.tsx:44` 也渲染了一份（那五页正文第一行）
                ⇒ 那五页上会看到**两条一样的导航条**、`id="featureNav"` 重复。
                那一份该删掉（A7 的文件，已写进交付说明）。 */}
          <FeatureNav current={location.pathname} />
          <ThemeSwitch />
          {/* ★涨跌色开关（`MarketSwitch`）也放在这一排。它和 `ThemeSwitch` 是**并列**
              的两个控件、不是一个多态开关：`--up`/`--down` 是"底色"那一半的局部变量，
              而"红表示涨还是表示跌"是**逐条市场惯例** —— 四个组合都合法（暗底+A股、
              亮底+A股、暗底+欧美、亮底+欧美），所以是**正交**的两轴。
              ★为什么落在这里：`Shell` 是**每一页都过**的那一层 ⇒ 这是唯一"全站可达"
                的位置。摆在单独某一页的正文里，另外四页就点不到。
              ★页头那排是 `flex-wrap`（第 33 行）⇒ 窄屏上它只是比原来更早换行，
                不会把页头撑破；它量不到的两条判据量的是**左边缘与宽度**
                （"主栏 ≥ 视口 70%" / "页头和主栏逐像素对齐"），右对齐的控件动不了它们。
              ★为什么必须放在 `#badges` **之后**（而不是紧挨 `#featBadge` 那枚徽章）：
                上面 79-83 行那段注释说的锚点区在 `#featBadge` 的 <span> 与
                `#badges` 的 <div> **之间**，中间插东西那条注入就站不住。
              ★它的三处值入口（首帧内联脚本 / `main.tsx` 顶层 / `useMarket`）见
                `theme.ts` —— 这里只是**用户点得着**的第三处，不是唯一一处。 */}
          <MarketSwitch />
        </div>
      </header>

      {/* ★主栏的 max-w 必须和上面页头那排**用同一个 clamp**。
          原来这里是写死的 max-w-[1180px]，页头却已经改成 clamp(1180px,78vw,2200px)
          —— 于是 2490px 的窗口里：页头 1872 宽、主栏还是 1180 宽，
          既"内容不跟着屏幕长"（占 49%），又"页头和主栏左边对不齐"
          （两个都 mx-auto，宽度不同 ⇒ 左边缘必然错开）。两条判据同时红就是这个。
          ★改宽度的地方是**两处**，改一处等于没改 —— 这正是当初漏掉的那处。 */}
      <main className="mx-auto max-w-[clamp(1180px,78vw,2200px)] p-5">
        {children}
      </main>

      {/* ── 年画页脚（2026-09-28 加）──────────────────────────────────────
          「每页都有一处乡村元素」靠它兜底：底纹（上面那层）在窄屏与普通笔记本上
          会被卡片压得几乎看不见 —— 两侧留白要到 **≥1600px** 才够宽
          （主栏 `clamp(1180px,78vw,2200px)` 在 1400px 视口下已经是 1180，
          两边各只剩 110px），而 1180px 以下根本没有留白。
          这一条横带**任何宽度下都在**（窄屏只是折行），所以"每页都有"这句话
          由它承载力，底纹只负责在大屏上加一层氛围。

          ★它是在**空白的地方**：`<footer>` 排在 `main` 之后，不占任何一个内容位、
            不把任何一张卡往下推 —— 与上一版那块插在卡与卡之间的年画区正相反。

          ★`--nh-paper`（宣纸暖白）**两套主题下都不翻转**，理由写在 `index.css`
            的 `--nh-*` 那段：年画离开纸白底就不是年画了，视觉上就是
            「贴在墙上的一张宣纸」。所以这带的字色写死 `--nh-ink`（墨色），
            那一对 ≈13.5:1，两套主题同值（`verify_page.py` 的 4f 会逐张表算它）。
          ★回纹条**通栏**（`preserveAspectRatio="none"`，见 `Nianhua.tsx`）：
            单元数给到 64 是因为这条带最宽能到 2200px，单元太少会被横向拉扁。
          ★整带 `overflow-hidden`：回纹是撑满容器的，圆角与出界靠它裁。
          ★这里的入口用**绝对路径** `/graph.html`：路由表允许尾斜杠
            （`/^\/dash\/?$/`），相对写法 `graph.html` 在 `/dash/` 上会解析成
            `/dash/graph.html` 而 404。主页那张 `#graphCard` 用的是相对写法，
            那是 `check_links` 的锚（`HOME_LINKS` 里期望值就是 `graph.html`），
            两者**不是同一个链接，别去改那一张**。 */}
      <footer className="relative mt-8" aria-label="县乡村">
        <div className="overflow-hidden border-t border-[var(--nh-gold-2)]"
             style={{ background: "var(--nh-paper)", color: "var(--nh-ink)" }}>
          <HuiWen n={64} h={10} />
          <div className="mx-auto flex max-w-[clamp(1180px,78vw,2200px)] flex-wrap items-center gap-x-6 gap-y-3 px-5 py-4">
            <span aria-hidden="true" className="flex shrink-0 items-end gap-2">
              <DaoSui size={44} />
              <WaFang size={38} />
              <JiShi size={42} />
              <LiangCang size={36} />
              <TongQian size={28} />
            </span>
            <p className="min-w-0 grow basis-72 text-xs leading-relaxed">
              <b>乡e家</b>是按<b>县乡村家庭</b>的过法编的 —— 地里的收成、打工的汇款、
              孩子的学费、老人的药钱、村里的人情往来，都在那 255 个词条和那张图里。
            </p>
            <a href="/graph.html"
               className="shrink-0 rounded border px-3 py-1 text-xs no-underline"
               style={{ borderColor: "var(--nh-gold-2)", color: "var(--nh-ink)" }}>
              看图谱 →
            </a>
          </div>
        </div>
      </footer>
    </div>
  )
}
