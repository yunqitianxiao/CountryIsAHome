import * as React from "react"
import { Shell } from "../Shell/Shell"
import { SiteBanner } from "../SiteBanner/SiteBanner"
import { ProfileForm } from "./ProfileForm"
import { clearProfile, readProfile, saveProfile } from "../../profile"

/**
 * 我的画像（`/profile`）—— 和引导最后一步**同一个表单**，只是可以反复进来改。
 *
 * ★这一页必须存在，不能只靠引导里那一次：引导按设计只自动出现一次，
 *   没有这一页，用户填错一个选项就再也改不了了 —— 而"改不了"在页面上
 *   表现为"这张卡点不开"，看起来像没做完，不像设计。
 *
 * ★`#pfSaved` 这行**只在真的存过之后才有字**（见下面那段注释）。
 */
export function Profile({ probe = false }: { probe?: boolean }) {
  const [saved, setSaved] = React.useState(false)
  const [has, setHas] = React.useState(false)

  // ★`readProfile()` 放在 effect 里而不是渲染期间：localStorage 是**浏览器**的东西，
  //   渲染期间读它会让这个组件在 SSR / 测试环境下直接抛。
  React.useEffect(() => { setHas(!!readProfile()) }, [saved])

  return (
    <Shell probe={probe}>
      <div className="center-large-container">
        <div className="center-large-card">
          <div className="flex flex-col gap-4">
            <SiteBanner />

            <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4"
                     id="profileFormCard" aria-label="投资画像">
              <h2 className="text-base font-semibold text-[var(--ink)]">我的投资画像</h2>
              <p className="mt-1 text-sm text-[var(--ink-2)]">
                填过的会自动带出来，改完再存一次就行。智能体回答时会参考这些，
                模拟投资也会按你的兴趣推荐标的 —— 但<b>不会</b>限制你只能选那几只。
              </p>

              <div className="mt-4">
                <ProfileForm
                  formId="profilePageForm"
                  initial={readProfile()}
                  submitLabel="保存"
                  onSubmit={(p) => { saveProfile(p); setSaved(true) }}
                />
              </div>

              {/* ★「存好了」这行**只在真的存过之后出现**：一开始就挂一句"已保存"
                  的话，判据量到它非空也没法说明任何事 —— 那是"本来就有"。
                  探针量的是它**从空变非空**。 */}
              <p className="mt-2 text-sm text-[var(--accent)]" id="pfSaved" role="status">
                {saved ? "已保存到这台机器的浏览器里。" : ""}
              </p>

              {has ? (
                <p className="mt-3">
                  <button type="button" className="obskip" id="pfClear"
                          onClick={() => { clearProfile(); setSaved(false); setHas(false) }}>
                    清空画像
                  </button>
                </p>
              ) : null}
            </section>

            <p className="text-sm"><a href="/">← 回主页</a></p>
          </div>
        </div>
      </div>
    </Shell>
  )
}
