import * as React from "react"
import { getStatus, type Status } from "./api"
import { useMarket, useTheme } from "./theme"

/**
 * 站点的**共用外壳件**：页头那排徽章、主题开关、以及「数字的唯一出口」的搬运工。
 *
 * ★这些件原来是和智能体那页写在一个文件里的（`App.tsx`）。主页变成导航层之后，
 *   **五页都要页头和主题开关**，再各写一份就等于五份会各自漂走的文案/结构 ——
 *   而判据（大屏对齐、定位条、K线预测徽章）只会在其中一页上量到，
 *   另外四页悄悄坏掉也没人知道。
 *
 * ★注意这里**没有** `App` 这个组件了：智能体那页在 `components/Agent/Agent.tsx`。
 *   文件叫 chrome 而不是 App，就是为了让"页面"和"外壳"在目录上一眼分得开。
 */

/** ★数字的唯一出口在服务端：它把 `<!--ACCURACY-->` 换成带 `<span class="acc">` 的句子，
 *  我们只负责**搬运**，绝不在这里写任何数字。
 *  ★这条有个新的假绿形状：壳里有数字 ≠ 用户看得见 —— 所以这里必须真的渲染进
 *  可见节点，判据量的是那个节点的**高度**，不是文本。 */
export function readAccBoot(): string {
  const el = document.getElementById("acc-boot")
  return el ? el.innerHTML : ""
}

function Dot({ tone }: { tone: "ok" | "warn" | "off" }) {
  const c = { ok: "bg-[var(--accent)]", warn: "bg-[var(--f2)]", off: "bg-[var(--ink-3)]" }[tone]
  return <i className={`h-[7px] w-[7px] flex-none rounded-full ${c}`} />
}

function Badge2({ tone, text, title, onClick }: { tone: "ok" | "warn" | "off"; text: string; title?: string; onClick?: () => void }) {
  return (
    <span
      className="badge inline-flex items-center gap-1.5 whitespace-nowrap rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1 text-xs text-[var(--ink-2)]"
      title={title} onClick={onClick} style={onClick ? { cursor: "pointer" } : undefined}
    >
      <Dot tone={tone} />
      {text}
    </span>
  )
}

/** 页头那排状态徽章。★它**每 30 秒被整排重建一次**（旧页面 status() 的行为），
 *  所以「K线预测」那枚能力标称**故意放在这个盒子外面** —— 放进去的话一断网就没了。 */
export function Badges({ probe }: { probe: boolean }) {
  const [s, setS] = React.useState<Status | null>(null)
  const load = React.useCallback(() => { getStatus().then(setS).catch(() => setS(null)) }, [])
  React.useEffect(() => {
    if (probe) return
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [probe, load])

  // ★对话卡页眉那句"引擎"说明跟着状态走。
  //   写在 effect 里而不是渲染期间：渲染期间改别人的 DOM 是不合法的副作用，
  //   而且 Badges 在 ChatCard **之前**挂载，渲染期读到的是还没建出来的节点。
  React.useEffect(() => {
    const el = document.getElementById("engineTag")
    if (!el) return
    el.textContent = s
      ? (s.engine?.loaded
          ? `模型就绪 · ${(s.engine as { device?: string }).device || ""}`
          : "模型未加载（首次预测时加载）")
      : "本地小模型 + 大模型"
  }, [s])

  if (!s) return probe ? null : <Badge2 tone="off" text="连不上本地服务" />

  const llmOk = !!s.llm?.configured
  return (
    <>
      <Badge2
        tone={s.engine?.loaded ? "ok" : "warn"}
        text={s.engine?.loaded ? "模型已加载" : "模型待加载"}
        title={String((s.engine as { device?: string }).device || "")}
      />
      <Badge2
        tone={llmOk ? "ok" : "off"}
        text={llmOk ? s.llm.model || "大模型已配置" : "大模型未配置"}
        title={llmOk ? "" : "点这里，在网页上把 key 填进去"}
        onClick={llmOk ? undefined : () => window.dispatchEvent(new Event("licai:focusKey"))}
      />
      {typeof s.kb?.terms === "number" ? <Badge2 tone="ok" text={`词表 ${s.kb.terms} 词`} /> : null}
    </>
  )
}

export function ThemeSwitch() {
  const [theme, setTheme] = useTheme()
  const opts: [typeof theme, string][] = [["light", "亮"], ["dark", "暗"], ["system", "跟随系统"]]
  return (
    <div className="ml-2 inline-flex overflow-hidden rounded border border-[var(--rule)]" role="group" aria-label="主题">
      {opts.map(([v, label]) => (
        <button
          key={v} type="button" onClick={() => setTheme(v)}
          aria-pressed={theme === v}
          className={
            "px-2 py-1 text-xs " +
            (theme === v
              ? "bg-[var(--accent)] text-white"
              : "bg-[var(--card)] text-[var(--ink-3)] hover:text-[var(--accent)]")
          }
        >
          {label}
        </button>
      ))}
    </div>
  )
}

/** 涨跌色开关。★它和主题开关是**两件独立的事**，所以是两个并列的控件而不是
 *  一个三态开关：主题换的是底色（亮/暗/跟随系统），这个换的是"什么颜色表示涨"
 *  （A 股红涨绿跌 / 欧美绿涨红跌）。合在一起会得到六个按钮，而且"深色 + 欧美"
 *  这种组合照样要在——它们正交。
 *
 *  ★标签写「红涨绿跌」而不是「A 股」「欧美」：用户要判断的是**这个红是涨还是跌**，
 *    地名得先过一次常识（"A 股是红涨对吧？"）。直接写方向，零推断。
 *  ★这条路有三个入口，都写到同一个 `documentElement.dataset.market`：
 *    `web/index.html` 的防闪内联脚本（首帧之前）、`main.tsx` 顶层（模块执行时）、
 *    这里的 `useMarket`（挂载后）。理由同主题那条链——见 theme.ts 顶上那段。 */
export function MarketSwitch() {
  const [market, setMarket] = useMarket()
  const opts: [typeof market, string, string][] = [
    ["cn", "红涨绿跌", "A 股惯例"],
    ["us", "绿涨红跌", "欧美惯例"],
  ]
  return (
    <div className="ml-2 inline-flex overflow-hidden rounded border border-[var(--rule)]" role="group" aria-label="涨跌颜色">
      {opts.map(([v, label, hint]) => (
        <button
          key={v} type="button" onClick={() => setMarket(v)} title={hint}
          aria-pressed={market === v}
          className={
            "px-2 py-1 text-xs " +
            (market === v
              ? "bg-[var(--accent)] text-white"
              : "bg-[var(--card)] text-[var(--ink-3)] hover:text-[var(--accent)]")
          }
        >
          {label}
        </button>
      ))}
    </div>
  )
}

// ── 新增五页的导航 ─────────────────────────────────────────────────────
//
// ★为什么放在 `chrome.tsx` 而不是塞进 `Shell.tsx` 的页头里：
//   页头那排是**全站共用**的，而 `verify_agent.py` 有一条判据量的是
//   "页头那排和主栏逐像素对齐"。往那一排里加五个中文链接，窄屏下它会换行、
//   把页头撑高 —— 那一条判据量的是左边缘，本来不受影响，但**页头是别人的地盘**
//   （`Shell.tsx` 归设计系统那一版所有）。所以这里只**导出**一个导航件，
//   由新增的五页自己渲染在正文顶部。
//
// ★代价说清楚（交付说明里也写了）：**存量五页的页头里没有这排链接** ——
//   把 `<FeatureNav/>` 放进 `Shell.tsx` 的页头、或者放进主页的导航区，
//   是协调者的一个动作，不是这一版前端能单方面决定的事（那两个文件都不归我改）。
//   在那之前，这五页之间可以互相跳，从主页进则需要知道地址。
//
// ★为什么是一个数组而不是几段 JSX：`main.tsx` 的 `ROUTES` 里那几条**同一个顺序**。
//   两处各写一遍的话，加下一页时必然只改一处（那一页于是"能打开但点不到"）。
export const NEW_PAGES: [href: string, label: string][] = [
  ["/agents", "多智能体博弈"],
  ["/intraday", "分钟级策略"],
  ["/linkage", "关联检测"],
  ["/collect", "采集监视"],
  ["/psych", "投资心理"],
  ["/assess", "风险测评"],
  // ★建议体检：**排在最后，与 `main.tsx: ROUTES` 里的位置同序**。
  //   它是这一批里第二条"数据住浏览器里"的页（`/assess` 是第一条），
  //   但它多一个入口 —— 主页那张 `#inspectCard`，所以从主页也能进。
  ["/inspect", "建议体检"],
]

/** 新增五页之间的导航。`current` 是当前路径，用来标 `aria-current="page"`。
 *  ★用真 `<a href>`（不是 `onClick` + `history.pushState`）：这几条路由
 *    在 `app.py` 里是**真路由**，直接粘地址、刷新、新标签页打开都得落在同一页上。
 *    SPA 内部那种"点一下不刷新"的写法会让新标签页打开变成主页。 */
export function FeatureNav({ current }: { current?: string }) {
  return (
    <nav id="featureNav" aria-label="新增功能页"
         className="flex flex-wrap items-center gap-1.5">
      {NEW_PAGES.map(([href, label]) => {
        const on = href === current
        return (
          <a
            key={href} href={href}
            aria-current={on ? "page" : undefined}
            className={
              "rounded border px-2 py-1 text-xs no-underline " +
              (on
                ? "border-[var(--accent)] bg-[var(--card)] font-semibold text-[var(--accent)]"
                : "border-[var(--rule)] bg-[var(--card)] text-[var(--ink-2)] hover:text-[var(--accent)]")
            }
          >
            {label}
          </a>
        )
      })}
    </nav>
  )
}
