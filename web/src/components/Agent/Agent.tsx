import * as React from "react"
import { Shell } from "../Shell/Shell"
import { SiteBanner } from "../SiteBanner/SiteBanner"
import { PredCard } from "../PredCard/PredCard"
import { ChatCard } from "../ChatCard/ChatCard"
import { KeyCard } from "../KeyCard/KeyCard"
import { readAccBoot } from "../../chrome"

/**
 * 智能体页（`/agent`）。
 *
 * ★这一页的内容**原来就是整个站点**（`App.tsx` 里的 `App`）。主页变成导航层之后
 *   它整体搬到这里，只动了外壳那一层：
 *     · `<header>` + `<main>` 换成 `<Shell>`（页头和主栏的 clamp 只此一份）
 *     · `#siteBanner` 换成 `<SiteBanner>`
 *     · 侧栏那两张入口卡（guide.html / graph.html）**搬去主页**了 ——
 *       导航层的活不该由子页干；这一页只留 Key 卡。
 *   除此之外**一字未改**：`#predCard`、`#disclaim`、`.acc-line`、`#keyCard`
 *   这些判据的落点全部原样。
 */
export function Agent({ probe = false }: { probe?: boolean }) {
  // 同步读（不是 useEffect）：探针要在同一个 tick 里看到它已经渲染出来
  const accHtml = React.useMemo(readAccBoot, [])

  return (
    <Shell probe={probe} back>
      <div className="grid grid-cols-[minmax(0,1fr)_clamp(300px,15vw,400px)] items-start gap-4 max-[880px]:grid-cols-1">
        {/* ★「人人可用」定位条：措辞被判据双向钉住（'人人可用' 在、'教学' 不在），改字前先看判据。
            判据还要求它在**折叠线以上** —— 所以它必须是这一栏的第一个孩子。 */}
        <div className="col-span-full"><SiteBanner /></div>

        {/* ★大屏（≥1900px）才把「预测」和「对话」摆成左右工作台。
            **两个数决定了这个断点不能更低**（都是实测，不是估的）：
              · 1400px：主栏 1180、扣掉侧栏 300 和间距，预测栏只有 864 ——
                劈成两半每边 430，K 线图一根蜡烛不到 3px。
              · 1700px：主栏 1326、侧栏 300，预测栏 1010 —— 劈开之后
                预测那半只有 615，图比 1400px 时**还小**。窗口更大、主角更小，
                这是"为响应式而响应式"的典型坏味道。
            1900px 往上，劈开之后的预测栏（724）才开始追平 1400px 那一档（864）。
            ⇒ 劈开的收益是**对话卡进了首屏**（不劈的话预测卡自己 1011px 高，
              对话永远在折叠线下面）；代价是 K 线图不再占满整行。
              这个交换只在真正的大屏上划算。
            ★1.7fr 而不是 1fr：对话那边 426px 够用（气泡本身限宽 88%），
              而 K 线图是**按宽度读形态**的，给它多一点。
            ★`minmax(0,…)` 不能省：`fr` 轨的 `min-width` 默认是 `auto`，
              里面那张 K 线 svg 撑住不肯收缩时就是横向溢出 ——
              外面那层 `minmax(0,1fr)` 是同一个理由。
            ★默认的 `align-items: stretch` 是**要的**：对话卡跟着预测卡一样高，
              `#msgs` 自己滚（它有 `flex:1` + `max-height:62vh`）。
              加 `items-start` 的话右栏会是 440px 的卡配下面一大片空白。 */}
        <div className="col-start-1 flex flex-col gap-4 min-[1900px]:grid min-[1900px]:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
          <PredCard probe={probe} />
          <ChatCard probe={probe} />

          {/* ★这里**必须**带 `disclaim` 这个类名：`.acc-line` 的样式选择器是
              `.disclaim .acc-line`（后代选择器），而 .acc-line 本身是 `#disclaim`
              的孩子。只写 id 不写 class 的话那条规则**一条都匹配不上** ——
              结果是不报错、不打红，只是实测数字那一行**掉回了裸文本**：
              没有上方的虚线分隔、没有更小一号的字。
              ★它差点蒙混过关：那条判据量的是 accH > 20，而宽屏下 32 / 窄屏下 64，
              照样过 —— 过的是"行数够多"，不是"样式在"。是**大屏那一档**
              （2400px 下文字只占一行 → 16px）把这件事露出来的。 */}
          <div className="disclaim min-[1900px]:col-span-full" id="disclaim">
            <b>风险提示</b>：站内的预测来自只看了价格序列的小模型，不含基本面与新闻；
            所有内容仅供学习，<b>不构成投资建议</b>。
            {/* ★搬运服务端注入的实测数字，不在这里写数字 */}
            <div className="acc-line" dangerouslySetInnerHTML={{ __html: accHtml }} />
          </div>
        </div>

        <aside className="col-start-2 flex flex-col gap-4 max-[880px]:col-start-1">
          <KeyCard probe={probe} />
        </aside>
      </div>
    </Shell>
  )
}
