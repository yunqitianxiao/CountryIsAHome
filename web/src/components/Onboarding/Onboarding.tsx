import * as React from "react"
import { Shell } from "../Shell/Shell"
import { SiteBanner } from "../SiteBanner/SiteBanner"
import { ProfileForm } from "../Profile/ProfileForm"
import { readAccBoot } from "../../chrome"
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
  const accHtml = React.useMemo(readAccBoot, [])
  const already = React.useMemo(() => readProfile(), [])

  const STEPS: Step[] = [
    {
      title: "这是个什么站",
      body: (
        <>
          <P>
            一句话：<b>把理财术语和行情数据翻成人话</b>，让你在掏钱之前先看明白。
          </P>
          <P>
            但更要紧的是这一句：<b>这个站不发理财建议 —— 它检验你收到的那条。</b>
            亲戚介绍的"保本、年化 12%"、群里"老师"带着做的单、私聊发来的产品，
            原文粘进「建议体检」，它告诉你那段话里哪几句有问题、每句凭什么、
            哪几句看着可疑其实不算、以及<b>哪几件事它查不到、判不了</b>。
          </P>
          <P>
            它不荐股、不给你"该不该买"的答案，也不替你判"这是不是诈骗" ——
            那是这一站从第一天就定下的规矩。
          </P>
          <P>
            不填任何 Key 也能查词、看图谱、跑 K 线预测；想要会对话的助手，
            才需要填一把你自己的 DeepSeek Key（只存在本机）。
          </P>
        </>
      ),
    },
    {
      title: "最要紧的一件事，和另外三个入口",
      body: (
        <>
          {/* ★这四段**故意不带 ① ② ③ ④ 圈号**：页头已经在用「第 N / M 步」编号，
              同一屏里两套编号（入口序号 vs 步骤号）会让人把"第 3 个入口"读成"第 3 步"——
              而这两个数在这一页里都会数到 4，混起来看不出错。
              主次靠"先看这个"这个说法表达，不靠数字。 */}
          <P><b>建议体检（先看这个）</b>：亲戚介绍的、群里转的、陌生人私聊发给你的那段理财话，
            原文粘进来。它告诉你：这段话<b>承诺了什么</b>、哪几句是<b>红旗</b>（每句都带你<b>自己核</b>的原文）、
            哪几句<b>看着像红旗、其实不算</b>、你<b>该问它什么</b>，以及<b>哪几件事它判不了</b>。
            <b>它不发建议，只检验你收到的那一条。</b></P>
          <P><b>查词</b>：知识图谱里有 255 个术语，按「属于 / 对比 / 前提」连成一张图。不懂的词先来这儿。</P>
          <P><b>问智能体</b>：把你的问题直接丢给它。它会去调工具取真数据，再把结果讲成人话，
            并且告诉你<b>它没把握的地方在哪儿</b>。</P>
          <P><b>模拟投资</b>：拿虚拟本金练手。行情是真抓的，本金是假的 —— 亏了不心疼，
            但会真的看到"我当时要是买了会怎样"。</P>
          {/* ★这里"255"是**手写的**，真源是 `terms.json`（由 `解释.md` 投影出来）。
              它没有判据守着 —— 图谱侧有判据守"词表内部一致"（parse_terms.py 的分类小计、
              build_graph.py 的两层一致），但**守不到这句文案**：词数对不上时它不会红。
              词表再变时要记得回来改这里。**同类手写数一共四处**（2026-09-28 核实）：
              `Home.tsx` 图谱卡、`guide.html` 图谱那条、本文件这一句，
              以及 `graph_template.html` 里 `<h1>` 的副标题（那个在产物 `graph.html` 里）。 */}
        </>
      ),
    },
    {
      title: "那个预测模型，能做到什么、做不到什么",
      body: (
        <>
          <P>
            站里跑着一个本地小模型（Kronos），它看一段历史 K 线，采样出一段"接下来可能长这样"的路径。
            <b>它不预测新闻、不知道公司做什么生意、也不知道你是谁。</b>
          </P>
          {/* ★这段数字**不是手写的**：服务端每次请求现读回测结果填进壳里的
              `#acc-boot`，这里只是把那段搬过来（和智能体页用的是同一个出口）。
              手抄一份的话，回测重跑之后这里就开始撒谎，而页面上看不出来。
              ★外面那层 `disclaim` 类名**不能省**：`.acc-line` 的样式选择器是
              `.disclaim .acc-line`（后代选择器），少了它这行就退回默认字号。 */}
          <div className="disclaim mt-3">
            <div className="acc-line" dangerouslySetInnerHTML={{ __html: accHtml }} />
          </div>
          <P>
            所以请把它当成<b>一条参考路径</b>，不是结论。真想动手之前，
            先去<b>模拟投资</b>那一页感受一下它错起来是什么样。
          </P>
          {/* ★这里原本写的是"先看**第 3 步**的模拟投资"—— 而**没有哪一步是模拟投资**
              （第 3 步是这一步自己）。那是个指不到任何地方的步号：读者按"第 3 步"去找，
              找到的是"预测模型能做到什么"。★改成不带步号的说法，和上面那段"故意不写数字"
              是同一条纪律 —— 会腐烂的坐标不如不给坐标。 */}
        </>
      ),
    },
    {
      title: "还有几个地方值得逛逛",
      body: (
        <>
          <P>
            <b>预测台账</b>：站里那个小模型的战绩公开摆着 —— 命中率
            <b>永远和"无脑喊涨"并排显示</b>，不许单独出现。我们自己跑出来的结论是
            <b>它没有超额能力</b>（方向命中率和"抛硬币"统计上分不开，具体数字印在那一页上），
            这句话就印在那一页上。为什么要把短处摆出来？因为一个只挑好数字给你看的模型，
            你没法判断它哪次在骗你。
          </P>
          {/* ★这里**故意不写命中率的具体数字**（原来是"方向命中率 52.3%"）。两个理由：
              ① 台账页显示的那个数来自**另一个口径**（`kronos_accuracy.json` 的
                 `dir_band`），和首页那句实测（`dir_strict`）不是同一个数 ——
                 引导页写 52.3%、用户点进台账看到 49.6%，就是"文案和页面打架"。
              ② 手写的数**没有判据守着**（`check_onboarding` 只钉步号、标题互异、
                 末步出口），回测重跑一次它就过期。指向那一页比抄一个数结实。 */}
          <P>
            <b>行情总览</b>：一屏看完几只标的的收盘价和近 5/20/60 日涨跌，
            每个数都是当场取的，取不到就明说取不到，不会编。
          </P>
          <P>
            想要更细的图文讲解，可以走 <a href="guide.html" id="obGuideLink">详细图文版 →</a>。
          </P>
        </>
      ),
    },
    {
      title: "最后一步：你的投资画像",
      body: (
        <>
          <P>
            填完之后，智能体回答时会带上你的情况，模拟投资也会按你的兴趣推荐标的。
            <b>只存在你这台机器的浏览器里</b>，不上传、不需要注册。
          </P>
          <ProfileForm
            formId="obProfileForm"
            initial={already}
            submitLabel="填好了，开始用 →"
            onSubmit={(p) => {
              saveProfile(p)
              markOnboardingSeen()      // ★「看过了」是在**这一刻**写下的，
              setDone(true)             //   不是在进入这一页时 —— 中途关掉的不算看完
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
            <SiteBanner />

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
