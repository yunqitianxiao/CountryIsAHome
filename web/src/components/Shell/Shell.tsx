import * as React from "react"
import { Badges, FeatureNav, MarketSwitch, ThemeSwitch } from "../../chrome"

/**
 * 站点外壳：页头 + 主栏容器。**每一页都走它**。
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
    <div className="min-h-screen bg-canvas">
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
    </div>
  )
}
