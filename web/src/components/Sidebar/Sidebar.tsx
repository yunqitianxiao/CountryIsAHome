import * as React from "react"

/**
 * 全站左侧导航（仪表盘式布局，2026-10-02 加）。
 *
 * 把二十个路由按"用户想干什么"分五组，让"这个站有什么、现在在哪儿"
 * 一屏可见 —— 这是"增强引导性"的结构部分：
 *   宽屏（lg+）常驻左侧；窄屏由页头的 `FeatureNav` chips 承接
 *   （`chrome.tsx` 给它挂了 `lg:hidden`）。
 *
 * ★三条纪律（与探针有关）：
 *   1. 类名一律 `.side-*`，**不进 `.card` / `a.linkcard`** ——
 *      探针 `cardRects` 数的是 `.card`、`sgOrder` 比的是 `a.linkcard`
 *      同列卡，侧栏不能挤进它们的样本（`#siteLead` 同款做法）。
 *   2. 纯 `<a href>`：这些路由在 `app.py` 里是真路由，
 *      直接粘地址 / 刷新 / 新标签页打开都得落在同一页上。
 *   3. 当前页判定：与 `location.pathname` 比对，容忍尾斜杠与 `graph.html`。
 */

const GROUPS: { label: string; items: [href: string, label: string][] }[] = [
  {
    label: "认识这个站",
    items: [
      ["/siteguide", "引导智能体"],
      ["/onboarding", "新手引导"],
    ],
  },
  {
    label: "问与查",
    items: [
      ["/agent", "智能体问答"],
      ["graph.html", "知识图谱"],
    ],
  },
  {
    label: "检验与测评",
    items: [
      ["/inspect", "建议体检"],
      ["/assess", "风险测评"],
      ["/verify", "产品验真"],
    ],
  },
  {
    label: "行情与数据",
    items: [
      ["/dash", "行情速览"],
      ["/intraday", "盘中趋势"],
      ["/linkage", "关联检测"],
      ["/collect", "采集监视"],
      ["/ledger", "复盘记录"],
      ["/portfolio", "组合体检"],
    ],
  },
  {
    label: "练手与模拟",
    items: [
      ["/game", "模拟练手"],
      ["/invest-sim", "投资模拟可视化"],
      ["/luxury-dash", "奢华仪表板"],
      ["/psych", "投资心理"],
      ["/agents", "多智能体博弈"],
    ],
  },
]

/** 当前页判定。`graph.html` 这类静态页按 pathname 后缀比。 */
function isCurrent(href: string): boolean {
  const p = location.pathname
  if (href === "/") return p === "/" || p === "/index.html"
  if (href.endsWith(".html")) return p.endsWith(href)
  return p === href || p === href + "/"
}

export function Sidebar() {
  return (
    <aside id="sideNav" aria-label="全站导航" className="side-nav">
      <a
        className="side-link side-home"
        href="/"
        aria-current={isCurrent("/") ? "page" : undefined}
      >
        <span className="side-home-mark" aria-hidden="true">▤</span>
        首页总览
      </a>

      <nav aria-label="功能分组" className="side-groups">
        {GROUPS.map((g) => (
          <React.Fragment key={g.label}>
            <p className="side-group">{g.label}</p>
            {g.items.map(([href, label]) => (
              <a
                key={href}
                className="side-link"
                href={href}
                aria-current={isCurrent(href) ? "page" : undefined}
              >
                {label}
              </a>
            ))}
          </React.Fragment>
        ))}
      </nav>

      {/* ★与主页 `#siteLead` 同一句口径：不发建议、只检验。 */}
      <div className="side-mission">
        <b>这个站不发理财建议</b>
        <br />
        它只帮你检验别人给的建议靠不靠谱。
        <a href="/siteguide">一分钟看懂这个站 →</a>
      </div>
    </aside>
  )
}
