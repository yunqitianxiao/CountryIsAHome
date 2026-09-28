/**
 * 新增五页共用的**页壳**：站名栏 + 新页导航 + 页标题 + 风险提示脚注。
 *
 * ★站名栏复用 `components/Shell/Shell.tsx`，**不另写一个 header**。
 *   那里面有两条判据的落点（"大屏内容跟着屏幕长""页头与主栏逐像素对齐"），
 *   而两条都属于"改宽度的地方是两处，改一处等于没改"那一族。另写一份的话，
 *   这两条判据只会量到我这一份，另外四页（以及存量页）悄悄歪掉没人知道。
 *
 * ★脚注里的 `#disclaim` / `.acc-line` 是**照抄 `Ledger.tsx` 的**（同 class、同 id 语义）。
 *   那个 `.acc-line` 里装的是服务端替换出来的 `<!--ACCURACY-->` 那句话 ——
 *   **它的唯一出口在服务端**，前端只负责搬运（`chrome.tsx: readAccBoot` 顶上那段）。
 *   这一批五页也要显示它：这五页全都带方向性内容（博弈裁决、策略信号、异动榜），
 *   不搬这一句，用户看到的"这站对准确率什么态度"就只有存量页面在说。
 */
import * as React from "react"
import { Shell } from "../components/Shell/Shell"
import { readAccBoot } from "../chrome"
import { RISK_NOTE } from "./roles"

export function RiskFoot({ extra }: { extra?: React.ReactNode }) {
  const accHtml = React.useMemo(readAccBoot, [])
  return (
    <div className="disclaim mt-3" id="disclaim">
      <b>风险提示</b>：{RISK_NOTE}
      {extra ? <> {extra}</> : null}
      <div className="acc-line" dangerouslySetInnerHTML={{ __html: accHtml }} />
    </div>
  )
}

/** 一页的外框。★**没有** `current` 这个 prop，也别再加回来：导航条在 `Shell` 的页头里
 *  已经渲染了一份，那份的 `current` 取 `location.pathname`（权威源）。以前这里也放了一份
 *  ⇒ 这五页上出现**两条一样的导航条**、`id="featureNav"` 重复（`Shell.tsx:84` 记着这件事，
 *  真机截图见交付说明）。 */
export function Frame({
  probe, title, sub, children, footExtra,
}: {
  probe: boolean
  title: string
  sub?: React.ReactNode
  children: React.ReactNode
  footExtra?: React.ReactNode
}) {
  return (
    <Shell probe={probe} back>
      <div className="mb-3 mt-3">
        <h1 className="serif text-xl font-semibold text-[var(--ink)]">{title}</h1>
        {sub ? <p className="mt-0.5 text-sm text-[var(--ink-3)]">{sub}</p> : null}
      </div>

      <div className="space-y-3">{children}</div>

      <RiskFoot extra={footExtra} />
    </Shell>
  )
}
