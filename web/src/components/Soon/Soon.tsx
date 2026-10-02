import { Shell } from "../Shell/Shell"
import { SiteBanner } from "../SiteBanner/SiteBanner"

/** 施工中的子页 —— **临时的**，第 2/4 阶段各自换成真页面。
 *
 *  ★为什么第 1 阶段就要把这几条路由和页面先立起来：主页那四张导航卡
 *    一挂上去，`/onboarding`、`/profile`、`/game` 就是**用户点得到**的地址。
 *    先做页面搬迁、之后再补这三页的话，中间那段时间里导航卡是死的 ——
 *    而"死链"恰恰是这一轮最要紧的那条判据要防的东西。
 *    宁可先给一个诚实的"这页还没做完"，也不要一个 404。
 */
export function Soon({ probe, title, note }: { probe: boolean; title: string; note: string }) {
  return (
    <Shell probe={probe}>
      <div className="flex flex-col gap-4">
        <SiteBanner />
        <div className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" id="soonCard">
          <span className="text-xs text-[var(--ink-3)]">还没做完</span>
          <h2 className="mt-1 text-base font-semibold text-[var(--ink)]">{title}</h2>
          <p className="mt-1 text-sm text-[var(--ink-2)]">{note}</p>
          <p className="mt-2 text-sm"><a href="/">← 回主页</a></p>
        </div>
      </div>
    </Shell>
  )
}
