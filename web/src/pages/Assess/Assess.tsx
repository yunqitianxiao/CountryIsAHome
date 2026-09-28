/**
 * `/assess` 投资者风险承受能力测评。
 *
 * ★★这一页有一条**别的页都没有的**风险：它长得像官方问卷。
 *   券商开户时那份"风险测评"就是这个形状 —— 十道题、一个分数、一个 C 几的等级。
 *   所以本站必须把它**是**什么写在最显眼的地方：这是一套**教学口径**的题，
 *   不是你能拿去用的适当性结论。那段话在 `#caliberPanel` 里，别删。
 *
 * ★★它和 `/profile` 里那道「风险偏好」单选**不是一件事**（见 `assess.ts` 顶部）。
 *   结果是**并置对照**，不是回填 —— 这是 `agent/llm.py:192` 那条规矩要守的边界。
 *
 * ★措辞照 `Psych.tsx` 的口径：**说"这一档意味着什么 / 不能说明什么"，
 *   不说"你是…"、不说"你应该…"**。这个站手上只有十道自答题。
 *
 * ★硬约定（照 `ProfileForm.tsx` 与 `Onboarding.tsx`）：
 *   · 选项是 `<button type="button" aria-pressed data-opt>`（`.cchip`），**不是 `div`**
 *     —— div Tab 不到、回车选不了，而截图上看不出区别；
 *   · 一行"题面 + 选项组"用 `<div className="pflabel">`，**不套 `<label>`**
 *     —— 一组选项卡有多个控件，`<label>` 只能指一个，套上去是假的关联；
 *   · 没答完时**不渲染**提交按钮（不是 `disabled`）—— "点不动"和"根本不在"
 *     对键盘用户是两回事；
 *   · `aria-valuenow` 与「已答 N / M 题」那句**同源**（同一个 `answered` 数），
 *     两边各算一遍的话"进度条说 3/10、页面显示 4/10"没人拦得住。
 */
import * as React from "react"
import {
  DIMS, LEVELS, MAX_TOTAL, QUESTIONS, clearAssess, compareSelfReport, grade, missing,
  readAssess, saveAssess, sinceText, type Result,
} from "../../assess"
import { readProfile } from "../../profile"
import { Frame } from "../../agentui/Frame"
import { Empty, Meter, Panel, Well } from "../../agentui/State"

/** 一个选项。**模块私有**，照 `ProfileForm.tsx` 的 `Chip` —— 不 export 是因为
 *  它和那个 `Chip` 是同一个形状但**不共用**：那个绑在画像字段上，这个绑在题上，
 *  抽成一个共用件会让两边都得传一堆回调。重复三行比错耦合强。 */
function Opt({ on, onClick, label, opt }: {
  on: boolean; onClick: () => void; label: string; opt: string
}) {
  return (
    <button
      type="button" onClick={onClick} aria-pressed={on} data-opt={opt}
      className={"cchip" + (on ? " on" : "")}
    >
      {label}
    </button>
  )
}

export function Assess({ probe = false }: { probe?: boolean }) {
  const [answers, setAnswers] = React.useState<(string | null)[]>(() => QUESTIONS.map(() => null))
  const [result, setResult] = React.useState<Result | null>(() => readAssess())
  // ★画像只读一次：这一页**不写**它。自述偏好是另一件事，这里只是拿来并置。
  const profile = React.useMemo(readProfile, [])

  // ★旧记录与当前题库对不上（题库改过）时**不显示它**。
  //   `grade` 在这时返回 null —— 拿旧答案套新分值算出来的等级是编的，
  //   而它看起来和真算出来的完全一样，所以宁可让人重测一遍。
  const stale = result !== null && grade(result.answers) === null
  const shown = stale ? null : result

  const set = (qi: number, label: string) => {
    setAnswers((old) => old.map((v, i) => (i === qi ? label : v)))
  }

  const left = missing(answers)
  const answered = QUESTIONS.length - left.length
  const pct = Math.round((answered / QUESTIONS.length) * 100)

  const submit = () => {
    const g = grade(answers)
    if (!g) return                      // 按钮本来就只在答完时渲染；这是第二道闸
    const r: Result = { answers, total: g.total, level: g.level.code, at: Date.now() }
    saveAssess(r)
    setResult(r)
  }

  const again = () => {
    clearAssess()
    setResult(null)
    setAnswers(QUESTIONS.map(() => null))
  }

  const level = shown ? LEVELS.find((l) => l.code === shown.level) ?? null : null
  const self = level && profile ? compareSelfReport(profile.risk, level) : null

  return (
    <Frame
      probe={probe} title="投资者风险测评"
      sub="十道题 · 结果只存在你这台机器的浏览器里，不上传、不需要注册"
      footExtra={<>这一页是<b>教学工具</b>：它不驱动站里任何计算，也不构成任何适当性结论。</>}
    >
      {/* ★★这一段**不能删**：这一套题、每题的分值、C1–C5 的分档线都是本站自己定的，
          不是监管原文。不写明的话，这个模块就成了站里唯一一处"没有出处的数字" ——
          而它比别处更危险，因为它长得像官方问卷。（`Psych.tsx` 里
          "不引用数字"那条说的是同一个道理。） */}
      <Panel id="caliberPanel" title="这套题的来历">
        <p className="text-sm leading-relaxed text-[var(--ink-2)]">
          这套题、每题的分值、以及 <b>C1–C5 的分档线</b>，是本站为了讲清楚
          「风险承受能力由哪几件事决定」而设计的<b>教学口径</b>。
          真实券商用的题目、权重、分档线各不相同，<b>以你开户的那家为准</b>。
          这里的结果不构成任何适当性结论，也不能拿去替代任何一份真实问卷。
        </p>
        <p className="mt-1 text-sm leading-relaxed text-[var(--ink-2)]">
          它想让你看见的是另一件事：你的承受能力不是"我觉得我能扛多少"，
          而是由<b>这笔钱占你资产的多少、多久不用、你实际见过什么样的波动</b>
          这几件可以核对的事共同决定的。
        </p>
      </Panel>

      {stale ? (
        <Panel id="stalePanel" title="上一份记录已经作废">
          <Empty
            what="上次测的记录和当前题库对不上"
            hint="题库更新过。为了不给你一个按旧题算出来的等级，请重新测一遍 —— 十道题，几分钟。"
          />
        </Panel>
      ) : null}

      {shown ? (
        // ── 结果 ──────────────────────────────────────────────────────
        <>
          <Panel id="assessResult" title="你的结果" aria="测评结果">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-2xl font-semibold text-[var(--accent)] tnum" id="assessLevel"
                    data-k="assessLevel">
                {shown.level}
              </span>
              <span className="text-lg font-semibold text-[var(--ink)]">{level?.name}</span>
              <span className="ml-auto text-xs text-[var(--ink-3)]">
                测于{sinceText(shown.at)} · 总分 <b className="tnum" data-k="assessTotal">{shown.total}</b> / {MAX_TOTAL}
              </span>
            </div>

            <div className="mt-2">
              <Meter v={shown.total} max={MAX_TOTAL} k="assessMeter" title="总分落在满分的什么位置" />
            </div>

            {/* ★五档刻度：让"你在哪一档、上面还有几档"一眼可见。
                只给一个"C3"是看不出来的（多数人不知道 C3 是高还是低）。 */}
            <div className="mt-2 flex flex-wrap gap-1" id="assessScale" data-k="assessScale">
              {LEVELS.map((l) => (
                <span
                  key={l.code} data-k="assessScaleItem" data-code={l.code}
                  className={
                    "rounded border px-2 py-0.5 text-[10.5px] " +
                    (l.code === shown.level
                      ? "border-[var(--accent)] font-semibold text-[var(--accent)]"
                      : "border-[var(--rule)] text-[var(--ink-3)]")
                  }
                >
                  {l.code} {l.name}
                </span>
              ))}
            </div>

            {level ? (
              <>
                <div className="mt-3">
                  <div className="text-[10.5px] text-[var(--ink-3)]">这一档意味着什么</div>
                  <p className="mt-0.5 text-sm leading-relaxed text-[var(--ink-2)]" data-k="assessMeans">
                    {level.means}
                  </p>
                </div>
                {/* ★这一块是整页教学价值最高的地方。少了它，"你是 C3"就成了一句
                    比十道自答题实际支撑得起的更强的结论 —— 而它读起来完全像真的。 */}
                <div className="mt-2 rounded border border-[var(--rule)] bg-[var(--paper)] p-2">
                  <div className="text-[10.5px] text-[var(--ink-3)]">但这一档不能说明什么</div>
                  <p className="mt-0.5 text-xs leading-relaxed text-[var(--ink-2)]" data-k="assessLimit">
                    {level.limit}
                  </p>
                </div>
              </>
            ) : null}
          </Panel>

          {/* ★与自述偏好**并置，绝不回填**。没填过画像就整段不出现
              （不是渲染一段空的）—— `agent/llm.py` 那条第③条规矩。 */}
          {self ? (
            <Panel id="assessSelf" title="和你在画像里自述的那一项对照"
                   note="画像里的「风险偏好」是自述；这一页是按题问出来的。两件事，不互相覆盖">
              <p className="text-sm leading-relaxed text-[var(--ink-2)]" data-k="assessSelfText">
                {self.text}
              </p>
              <p className="mt-1 text-xs text-[var(--ink-3)]">
                你的自述<b>仍然原样留在画像里</b>，这一页没有改动它 ——
                它不会被这个结果覆盖，也不会被拿去改站里任何一项计算。
              </p>
            </Panel>
          ) : (
            <Panel id="assessSelfMissing" title="和自述偏好对照">
              <Empty
                what="你还没填过画像里的「风险偏好」"
                hint={<><a href="/profile" className="underline">去填一下</a>，回来就能看到这两处的对照 ——
                  它们不一致的时候，往往是最值得看一眼的时候。</>}
              />
            </Panel>
          )}

          <Panel id="assessBreakdown" title="这个等级是从哪几件事推出来的"
                 note={`${QUESTIONS.length} 道题分在 ${DIMS.length} 个维度上`}>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2" id="assessDims" data-k="assessDims">
              {DIMS.map((dim) => (
                <Well key={dim}>
                  <div className="text-xs font-semibold text-[var(--ink-2)]">{dim}</div>
                  <ul className="mt-1 space-y-0.5 text-xs leading-relaxed">
                    {QUESTIONS.map((q, qi) => q.dim !== dim ? null : (
                      <li key={q.id} data-k="assessDimRow">
                        <span className="text-[var(--ink-3)]">{q.text}</span>
                        <span className="mx-1 opacity-40">·</span>
                        <b className="text-[var(--ink)]">{shown.answers[qi] ?? "—"}</b>
                      </li>
                    ))}
                  </ul>
                </Well>
              ))}
            </div>
            <p className="mt-2 text-xs text-[var(--ink-3)]">
              第 3 题（这笔钱占你可投资金融资产的比例）是这套题里<b>最有分辨力</b>的一问：
              它问的是一件可以核对的事，而"你觉得自己能扛多少"不是。
            </p>
          </Panel>

          <Panel id="assessAgainPanel" title="想重测">
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className="pfbtn ghost" id="assessAgain" onClick={again}>
                重新测一遍
              </button>
              <span className="text-xs text-[var(--ink-3)]">
                重测会清掉这一份记录、从第一题重新开始。收入、家庭情况或这笔钱的用途变了，就值得重测 ——
                本站<b>不设</b>一个固定的有效期（那个年限各地各机构不同，编一个出来就成了又一处没有出处的数字）。
              </span>
            </div>
          </Panel>
        </>
      ) : (
        // ── 问卷 ──────────────────────────────────────────────────────
        <Panel id="assessPanel" title="开始测评"
               note={`${QUESTIONS.length} 道题，都是单选；答完才会出现提交按钮`}>
          {/* ★`aria-valuenow` 和右边那句「已答 N / M 题」读的是**同一个 `answered`**。 */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="stepbar flex-1" role="progressbar" aria-valuenow={pct}
                 aria-valuemin={0} aria-valuemax={100} id="assessBar">
              <i style={{ width: pct + "%" }} />
            </div>
            <span className="text-xs text-[var(--ink-3)]" id="assessCount" aria-live="polite">
              已答 <b className="tnum">{answered}</b> / {QUESTIONS.length} 题
            </span>
          </div>

          <ol className="mt-4 flex flex-col gap-4" id="assessQs">
            {QUESTIONS.map((q, qi) => (
              <li key={q.id} className="pfrow" data-row={q.id} data-k="assessQ">
                {/* ★`div.pflabel` 而不是 `label`：这一行的"标签"对应的是一组选项卡
                    （多个 button），`<label>` 只能指一个控件，套上去是假的关联。 */}
                <div className="pflabel">
                  <span className="tnum text-[var(--ink-3)]">{qi + 1}.</span>
                  {q.text}
                  {q.hint ? <span className="pfhint">{q.hint}</span> : null}
                </div>
                <div className="pfopts">
                  {q.opts.map((o) => (
                    <Opt
                      key={o.label} opt={`${q.id}:${o.label}`} label={o.label}
                      on={answers[qi] === o.label} onClick={() => set(qi, o.label)}
                    />
                  ))}
                </div>
              </li>
            ))}
          </ol>

          {/* ★没答完时**不渲染**提交按钮，不是 `disabled`。
              还在答的人眼前不该有一个点不动的按钮 —— 那是句废话。
              答完它才出现，而且紧挨着最后一题（正在答第十题的人抬眼就是它）。 */}
          {left.length ? (
            <p className="mt-4 text-xs text-[var(--ink-3)]" id="assessLeft" role="status">
              还差 <b className="tnum">{left.length}</b> 题（第 {left.map((i) => i + 1).join("、")} 题）。
            </p>
          ) : (
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <button type="button" className="pfbtn" id="assessSubmit" onClick={submit}>
                看结果 →
              </button>
              <span className="text-xs text-[var(--ink-3)]">
                十道题都答完了。结果只存在这台机器的浏览器里。
              </span>
            </div>
          )}
        </Panel>
      )}

      <Panel id="notDoPanel" title="这一页不做什么">
        <Well>
          <ul className="list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
            <li><b>不做适当性结论</b>：它不判断你"适合买什么"，站里也没有可交易的真实产品可以对上。
              真实开户流程里的那份测评有法律效力，这份没有。</li>
            <li><b>不改你的画像</b>：结果不会回填画像里的「风险偏好」，也不会进入智能体的任何计算。
              两处不一致的时候，这一页只指出来，不替你决定哪个才算数。</li>
            <li><b>不上传</b>：十道题的答案存在 <code>localStorage</code>（键 <code>licai-assess</code>），
              不发给服务端。本站服务端不存任何用户写的内容 —— 这不是"还没做"，是不能。</li>
            <li><b>不设有效期</b>：不写"结果两年内有效"。那个年限各地各机构不同，
              这里编一个就成了没有出处的数字。只如实告诉你测于什么时候，要不要重测你自己判断。</li>
          </ul>
        </Well>
      </Panel>
    </Frame>
  )
}

export default Assess
