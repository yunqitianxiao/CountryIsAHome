import * as React from "react"
import {
  ASSETS, EMPTY, HORIZONS, INCOMES, INTERESTS, RISKS,
  missingFields, type Profile,
} from "../../profile"

/**
 * 投资画像表单 —— **引导的最后一步和 `/profile` 用的是同一个组件**。
 *
 * ★为什么非要共用：这两处填的是**同一份数据**（`localStorage` 的 `licai-profile`）。
 *   各写一份的话，选项表、必填规则、"存哪儿"三件事会各自漂，
 *   而症状是"引导里填了、画像页打开是空的"这种**看不见的**不一致 ——
 *   页面上两处都长得对。
 *
 * ★**选项一律用 `button` 而不是 `div`**：键盘能 Tab 到、回车能选中、
 *   `aria-pressed` 能让读屏软件念出"已选中"。用 div + onClick 的话，
 *   鼠标用户能用、键盘用户整个表单是死的，而截图上看不出区别。
 *
 * ★每个选项还带 `data-opt`、每行带 `data-row`：探针**逐项读 DOM**，
 *   拿回来跟 `profile.ts` 那份选项表比。判"有个 .cchip 元素"是不够的 ——
 *   那样删掉一半选项照样绿。
 */

function Chip({ on, onClick, children, opt }: {
  on: boolean
  onClick: () => void
  children: React.ReactNode
  opt: string
}) {
  return (
    <button
      type="button" onClick={onClick} aria-pressed={on} data-opt={opt}
      // ★用 `button` 的第三个理由是**键盘**，不是波纹：`<div onClick>` Tab 不到、
      //   回车选不了。别顺手把波纹写进这条理由里 —— 选项卡外面还有一张 `.card`
      //   （两页都是），`ripple.ts` 的 `closest(HOSTSEL)` 往上找得到它，
      //   换成 div 圈照冒（实测过）。波纹那半句不成立。
      className={"cchip" + (on ? " on" : "")}
    >
      {children}
    </button>
  )
}

function Row({ row, label, hint, htmlFor, children }: {
  row: string
  label: string
  hint?: string
  /** 有它就把左栏渲染成**真的 `<label>`**（而不是 div），指向这一行的控件。
   *  ★这一栏看着一直像标签，但它原来是个 `div` —— **视觉上有、程序上没有**：
   *    读屏软件念到 `#pfNote` 只会说"编辑框"，不说它是什么；
   *    点那行字也不会把光标送进输入框。这类问题截图上看不出来。
   *  ★为什么只在给了 `htmlFor` 时才用 `<label>`：其余几行的"标签"对应的是
   *    一组选项卡（多个 button），`<label>` 只能指一个控件，套上去是**假的**
   *    关联 —— 那比不套更坏。
   *  ★元素从 div 换成 label **不影响排版**：`.pflabel` 的 CSS 里写死了
   *    `display: flex`，哪种元素都吃这条。 */
  htmlFor?: string
  children: React.ReactNode
}) {
  const inner = (
    <>
      {label}
      {hint ? <span className="pfhint">{hint}</span> : null}
    </>
  )
  return (
    <div className="pfrow" data-row={row}>
      {/* ★这里**故意不写成 `const Tag = htmlFor ? "label" : "div"`**：
          `Tag` 的类型会是 `"label" | "div"`，而 `label` 收 `htmlFor`、`div` 不收 ——
          TS 对联合类型的 JSX 会去检查属性交集，直接报错。两个分支写开最省事。 */}
      {htmlFor
        ? <label className="pflabel" htmlFor={htmlFor}>{inner}</label>
        : <div className="pflabel">{inner}</div>}
      <div className="pfopts">{children}</div>
    </div>
  )
}

export function ProfileForm({ initial, submitLabel, onSubmit, formId = "profileForm", compact = false }: {
  initial?: Profile | null
  submitLabel: string
  onSubmit: (p: Profile) => void
  formId?: string
  compact?: boolean
}) {
  const [p, setP] = React.useState<Profile>(initial || EMPTY)
  const [err, setErr] = React.useState("")

  const set = (k: keyof Profile, v: string | string[]) => {
    setP((old) => ({ ...old, [k]: v }) as Profile)
    setErr("")            // ★改了就把上一次的报错清掉：留着一句已经不对的红字
  }                       //   比不报错更让人困惑（他明明刚把那一项填上了）

  const toggle = (name: string) =>
    setP((old) => ({
      ...old,
      interests: old.interests.includes(name)
        ? old.interests.filter((x) => x !== name)
        : [...old.interests, name],
    }))

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    // ★必填校验走 `missingFields`，**不在这里另写一遍**（见 profile.ts 那段）。
    const miss = missingFields(p)
    if (miss.length) {
      setErr("还差：" + miss.join("、"))
      return                // ★拦住：不写存储、不进下一步
    }
    setErr("")
    onSubmit(p)
  }

  return (
    <form id={formId} className="flex flex-col gap-4" onSubmit={submit} noValidate>
      <Row row="interests" label="投资兴趣" hint="可多选 —— 模拟投资会按这个推荐标的，但不限制你选别的">
        {INTERESTS.map((n) => (
          <Chip key={n} opt={n} on={p.interests.includes(n)} onClick={() => toggle(n)}>{n}</Chip>
        ))}
      </Row>

      <Row row="risk" label="风险偏好" hint="必填">
        {RISKS.map((n) => (
          <Chip key={n} opt={n} on={p.risk === n} onClick={() => set("risk", n)}>{n}</Chip>
        ))}
      </Row>

      <Row row="horizon" label="打算放多久">
        {HORIZONS.map((n) => (
          <Chip key={n} opt={n} on={p.horizon === n} onClick={() => set("horizon", n)}>{n}</Chip>
        ))}
      </Row>

      {!compact && (
        <Row row="assets" label="可投资资产" hint="只存在你自己的浏览器里">
          {ASSETS.map((n) => (
            <Chip key={n} opt={n} on={p.assets === n} onClick={() => set("assets", n)}>{n}</Chip>
          ))}
        </Row>
      )}

      {!compact && (
        <Row row="income" label="每月可投入" hint="不跟模拟投资的虚拟本金挂钩">
          {INCOMES.map((n) => (
            <Chip key={n} opt={n} on={p.income === n} onClick={() => set("income", n)}>{n}</Chip>
          ))}
        </Row>
      )}

      <Row row="note" label="还想说点什么" htmlFor="pfNote">
        <input
          id="pfNote" className="pfinput" value={p.note} maxLength={200}
          placeholder="比如：已经有一笔三年期定存、不想碰杠杆…"
          onChange={(e) => set("note", e.target.value)}
        />
      </Row>

      {/* ★这行红字**一开始就是空的**：判据会先量它为空、再点提交、再量它非空 ——
          两量一对比才知道"拦住"是真的发生了，而不是本来就有句话挂在那儿。 */}
      <p id="pfErr" className="pferr" role="alert">{err}</p>

      <div>
        <button type="submit" id="pfSubmit" className="pfbtn">{submitLabel}</button>
      </div>

      <p className="text-xs text-[var(--ink-3)]">
        这些只存在你这台机器的浏览器里（<code>localStorage</code>），
        不会上传到任何服务器，也不跟模拟投资的虚拟本金挂钩。
      </p>
    </form>
  )
}
