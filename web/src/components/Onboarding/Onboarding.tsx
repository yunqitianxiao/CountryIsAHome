import * as React from "react"
import { Shell } from "../Shell/Shell"
import { ProfileForm } from "../Profile/ProfileForm"
import { markOnboardingSeen, readProfile, saveProfile } from "../../profile"

/**
 * 新手引导（`/onboarding`）—— 交互式分步，最后一步**就是**画像表单。
 *
 * ★两条行为上的硬规矩，各自都配了判据：
 *
 *   1. **只在"没看过"时自动出现一次。** 主页那半边的判断在 `profile.ts` 的
 *      `shouldAutoOpenOnboarding()` 里（纯函数，探针能直接问到真值表）。
 *      这一页负责"两件把标记写上"的事：走完最后一步、以及点「跳过」。
 *
 *      ★「跳过」**也算看过了**，这不是顺手加的：一个写着「跳过，直接用」的按钮，
 *        点完下次进来又弹一遍 —— 那是按钮在骗人。用户明确表示不想看，
 *        就该真的不再主动弹（主页那张卡还在，随时进得来）。
 *
 *   2. ★**`?probe=1` 下绝不自动跳转**，而且这一页自己也不做任何会跳走的动作。
 *      「跳过」那条链接是 `<a href="/">`，只有在用户真的点它时才会离开。
 *
 * ★步数写成常量 `STEPS`，标题和正文都在里面 —— 进度条、上一步/下一步、
 *   以及探针回传的"第几步/一共几步"读的是**同一个数组**。
 *   分开写的话，"进度条说 3/5、页面显示第 4 步"这种错没人拦得住。
 */

type Step = { title: string; body: React.ReactNode }

function P({ children }: { children: React.ReactNode }) {
  return <p className="mt-2 text-sm leading-relaxed text-[var(--ink-2)]">{children}</p>
}

export function Onboarding({ probe = false }: { probe?: boolean }) {
  const [i, setI] = React.useState(0)
  const [done, setDone] = React.useState(false)
  const already = React.useMemo(() => readProfile(), [])

  const STEPS: Step[] = [
    {
      title: "你是不是也这样？",
      body: (
        <>
          <P>想买点理财，打开 APP 全是专业词，看不懂不敢下手？</P>
          <P>网上都说"AI 预测很准"，但你不敢信，怕被忽悠？</P>
          <P>想试试又怕亏，没人可以问？</P>
          <P>别怕，这个平台就是帮你解决这些的——<b>不收费、不用注册，更不会荐股让你接盘。</b></P>
        </>
      ),
    },
    {
      title: "它能帮你做什么",
      body: (
        <>
          <P><b>查词</b>：不懂的术语，大白话给你讲明白——就像讲橘子顺便把橙子、柚子也串起来，一次搞懂。</P>
          <P><b>问 AI</b>：拿不准的行情，帮你分析风险，但不替你做决定，也不荐股——你多一个参考，少一点盲目。</P>
          <P><b>练手</b>：用虚拟本金买真实行情，亏了不心疼，赚了能看到自己判断对不对——下手之前先练胆子。</P>
          <P>除此之外，还有行情总览、风险测评、组合体检等功能，你可以慢慢探索。</P>
        </>
      ),
    },
    {
      title: "我们和别的平台不一样",
      body: (
        <>
          <P>
            别的平台都在吹"AI 预测很准"，我们不这么说。
          </P>
          <P>
            这个模型的作用<b>不是告诉你明天涨还是跌</b>，而是让你在真金白银下手之前，先用假钱感受一下：预测错起来是什么样。
          </P>
          <P>
            <b>亏的是假钱，长的是真记性</b>——这才是它存在的意义。
          </P>
        </>
      ),
    },
    {
      title: "填个简单画像，开始用",
      body: (
        <>
          <P>
            填完之后，智能体回答时会带上你的情况，模拟投资也会按你的兴趣推荐标的。
            <b>只存在你这台机器的浏览器里</b>，不上传、不需要注册。
          </P>
          <ProfileForm
            formId="obProfileForm"
            initial={already}
            compact
            submitLabel="去试试 →"
            onSubmit={(p) => {
              saveProfile(p)
              markOnboardingSeen()
              setDone(true)
            }}
          />
        </>
      ),
    },
  ]

  const last = i === STEPS.length - 1
  const pct = Math.round(((i + 1) / STEPS.length) * 100)

  return (
    <Shell probe={probe} back>
      {/* 添加居中和放大的容器包装 */}
      <div className="center-large-container">
        <div className="center-large-card guidance-card">
          <div className="flex flex-col gap-4">
            {i === 0 && (
              <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" aria-label="零门槛说明">
                <h3 className="text-lg font-semibold text-[var(--accent)] mb-2">零门槛，直接用</h3>
                <div className="flex flex-wrap gap-2 mb-2">
                  <span className="inline-block px-3 py-1 rounded-full bg-[var(--accent)]/10 text-[var(--accent)] text-sm font-medium">不收费</span>
                  <span className="inline-block px-3 py-1 rounded-full bg-[var(--accent)]/10 text-[var(--accent)] text-sm font-medium">不用注册</span>
                  <span className="inline-block px-3 py-1 rounded-full bg-[var(--accent)]/10 text-[var(--accent)] text-sm font-medium">不荐股</span>
                </div>
                <p className="text-sm text-[var(--ink-2)]">查词、看图谱、跑预测直接用；想让 AI 陪你聊天，再填一把你自己的 DeepSeek Key——只存在你这台电脑上。</p>
              </section>
            )}

            <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4"
                     id="obCard" aria-label="新手引导">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-xs text-[var(--ink-3)]" id="obStepNo">
                  第 {i + 1} / {STEPS.length} 步
                </span>
                <h2 className="text-base font-semibold text-[var(--ink)]" id="obTitle">
                  {done ? "好了，开始用吧" : STEPS[i].title}
                </h2>
              </div>

              {/* ★自制的进度条，不用 Tremor 那个：它的配色是写死的 Tailwind 色阶，
                  不跟本站的 `--accent` 走 —— 深色主题下会是一根刺眼的蓝条。
                  ★`aria-valuenow` 是**探针读的那个数**：判据拿它跟 `#obStepNo`
                  那句「第 N / M 步」比 —— 两边各算一遍的话，
                  "进度条说 3/5、页面显示第 4 步"没人拦得住。 */}
              <div className="stepbar mt-3" role="progressbar" aria-valuenow={pct}
                   aria-valuemin={0} aria-valuemax={100} id="obBar">
                <i style={{ width: pct + "%" }} />
              </div>

              {done ? (
                <div className="mt-2">
                  <P>画像已经存在你这台机器的浏览器里了。下面几个入口，从哪儿开始都行。</P>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <a className="pfbtn enlarged-btn" href="/">回主页看看那四张卡 →</a>
                    <a className="pfbtn ghost enlarged-btn" href="/agent">直接去问智能体</a>
                  </div>
                </div>
              ) : (
                <>
                  <div className="mt-3">{STEPS[i].body}</div>

                  <div className="mt-4 flex flex-wrap items-center gap-2">
                    <button type="button" className="pfbtn ghost enlarged-btn" id="obPrev"
                            disabled={i === 0}
                            onClick={() => setI((n) => Math.max(0, n - 1))}>
                      ← 上一步
                    </button>
                    {/* ★最后一步**不渲染**「下一步」：那一步的出口是表单自己的提交按钮。
                        留一个能跳过表单的按钮，等于必填校验形同虚设。
                        ★判据量的是 `#obNext` **在不在**（不是 disabled）——
                        一个 disabled 的按钮"点不动"和"根本没有"在键盘用户那边
                        是两回事，而截图上看不出区别。 */}
                    {last ? null : (
                      <button type="button" className="pfbtn enlarged-btn" id="obNext"
                              onClick={() => setI((n) => Math.min(STEPS.length - 1, n + 1))}>
                        下一步 →
                      </button>
                    )}
                    {last ? null : (
                      <a className="obskip enlarged-btn" href="/" id="obSkip"
                         onClick={markOnboardingSeen}>
                        跳过，直接用 →
                      </a>
                    )}
                  </div>
                </>
              )}
            </section>
          </div>
        </div>
      </div>
    </Shell>
  )
}
