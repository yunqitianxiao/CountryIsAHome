/**
 * 投资者风险承受能力测评 —— **题库、打分、存取三件事的唯一来源**。
 *
 * ★它和 `profile.ts` 里那道「风险偏好」单选是**两件事**，别合并：
 *   那个是**自述**（"我觉得我能承受多少"），这个是**按几个维度问出来的**。
 *   站里为了守住这个区别写了三条硬规矩（`agent/llm.py:192`、
 *   `agent/tools.py:1573`、`agent/portfolio.py:217`：自述"只用于措辞，不改变任何计算"）。
 *   ⇒ 测评结果**绝不回填**画像里那个字段，两者只在结果页**并置对照**。
 *
 * ★★★ 这套题、每题的分值、以及 C1–C5 的分档线，是**本站自己定的教学口径**，
 *   不是监管原文。页面上必须写明这一句（`Assess.tsx` 顶部那块）。
 *   不写的话，这个模块会成为站里唯一一处"没有出处的数字"——而它比别处更危险，
 *   因为它长得像官方问卷。真实券商的题目、权重、分档线各不相同。
 *
 * ★存哪儿：**只住在浏览器里**（`licai-assess`），照 `profile.ts` 那套。
 *   服务端零用户状态那三条硬边界（不存身份 / 不存用户写的内容 / 只追加）
 *   被测评结果**同时**撞上，所以它不是"还没做服务端存储"，是**不能**。
 *
 * ★读写一律包 try/catch（照 `profile.ts:16-18` 的规矩）：隐私模式 / 禁了站点
 *   数据时 localStorage 会抛，抛了就当没存过 —— 读不到只是"没测过"，
 *   不是"这页坏了"。
 */
import { RISKS } from "./profile"

export type Opt = { label: string; score: number }

export type Question = {
  id: string
  /** 维度名。结果页按它分组讲"你这个等级是从哪几件事推出来的"。 */
  dim: string
  text: string
  hint?: string
  opts: Opt[]
}

export type Level = {
  code: string
  name: string
  /** 这一档**意味着什么**（描述，不是判定）。 */
  means: string
  /** ★这一档**不能**说明什么。这一段是这个模块的教学价值所在：
   *  少了它，"你属于 C3"就成了一句比它实际支撑得起的更强的结论。 */
  limit: string
}

export type Result = {
  /** 按 `QUESTIONS` 顺序存的选项 label；没答是 `null`。 */
  answers: (string | null)[]
  total: number
  level: string     // Level.code
  at: number        // 测评时刻（Date.now()）
}

// ── 题库 ──────────────────────────────────────────────────────────────
//
// 十道题覆盖监管常见的四个维度：财务状况 / 投资知识与经验 / 投资目标 / 风险态度。
// ★"风险认知"那一维**故意不占题量** —— 它放进结果页的解释里讲，
//   因为"高收益伴随高风险"这种事，问一遍不如在给出等级时讲一遍。
//
// ★选项的 `label` 在**同一题内**必须唯一（`grade` 靠它反查分值）。
//   跨题重复（例如"1 年以内"在投资年限和投资期限里都出现）是可以的，
//   因为反查是**按题**在那一题的 `opts` 里做的。
//
// ★选项分值一律从 1 起（每问最低的那一项 = 1 分），但**同一分值在不同题里的含义不同**。
//   ★第 6 题（买过哪些品种）**只有 4 个选项、最高 4 分** —— 因为"没买过"和
//     "只买过存款"其实分不开，硬拆成两档就是在编分辨力。
//     代价说清楚：**满分是 `MAX_TOTAL`（= 49，不是 50）**。页面上那个分母读的就是它，
//     **不许在任何地方写死 50** —— 写了就是站里又一处没有出处的数字。

export const QUESTIONS: Question[] = [
  {
    id: "age", dim: "财务状况", text: "你的年龄",
    hint: "年龄不直接决定承受力，但它和收入年限、用钱时间绑在一起，所以问卷普遍会问",
    opts: [
      { label: "60 岁以上", score: 1 },
      { label: "50–60 岁", score: 2 },
      { label: "40–50 岁", score: 3 },
      { label: "30–40 岁", score: 4 },
      { label: "30 岁以下", score: 5 },
    ],
  },
  {
    id: "income", dim: "财务状况", text: "你的家庭税后年收入",
    opts: [
      { label: "10 万以下", score: 1 },
      { label: "10–30 万", score: 2 },
      { label: "30–50 万", score: 3 },
      { label: "50–100 万", score: 4 },
      { label: "100 万以上", score: 5 },
    ],
  },
  {
    id: "share", dim: "财务状况", text: "这笔投资的钱，占你可投资金融资产的比例",
    hint: "这套题里最有分辨力的一问 —— 比「你觉得自己能扛多少」实得多",
    opts: [
      { label: "几乎全部", score: 1 },
      { label: "一半以上", score: 2 },
      { label: "三成左右", score: 3 },
      { label: "一成左右", score: 4 },
      { label: "只是很小一部分", score: 5 },
    ],
  },
  {
    id: "debt", dim: "财务状况", text: "你目前的家庭负债（房贷、车贷、消费贷等）",
    opts: [
      { label: "月供已占收入一半以上", score: 1 },
      { label: "月供占收入三成左右", score: 2 },
      { label: "月供占收入一成左右", score: 3 },
      { label: "基本没有负债", score: 4 },
      { label: "没有负债，且有较多存款", score: 5 },
    ],
  },
  {
    id: "years", dim: "投资知识与经验", text: "你做过投资多少年",
    opts: [
      { label: "没做过", score: 1 },
      { label: "1 年以内", score: 2 },
      { label: "1–3 年", score: 3 },
      { label: "3–10 年", score: 4 },
      { label: "10 年以上", score: 5 },
    ],
  },
  {
    id: "instruments", dim: "投资知识与经验",
    text: "你实际买过的最复杂的一类是哪种",
    hint: "买过不等于赚过 —— 这一问量的是「你见过什么样的波动」",
    opts: [
      { label: "只有存款和货币基金", score: 1 },
      { label: "债券、债券基金或银行理财", score: 2 },
      { label: "股票、股票型基金或 ETF", score: 3 },
      { label: "期货、期权或融资融券", score: 4 },
    ],
  },
  {
    id: "horizon", dim: "投资目标", text: "这笔钱你打算放多久",
    opts: [
      { label: "1 年以内", score: 1 },
      { label: "1–3 年", score: 2 },
      { label: "3–5 年", score: 3 },
      { label: "5–10 年", score: 4 },
      { label: "10 年以上", score: 5 },
    ],
  },
  {
    id: "expect", dim: "投资目标", text: "你期望的年化收益",
    hint: "期望值本身不构成任何判断 —— 但它和你能接受的亏损是否对得上，很说明问题",
    opts: [
      { label: "能不亏就行", score: 1 },
      { label: "3–4%", score: 2 },
      { label: "5–8%", score: 3 },
      { label: "10–20%", score: 4 },
      { label: "20% 以上", score: 5 },
    ],
  },
  {
    id: "drawdown", dim: "风险态度", text: "如果买入后一个月跌了 20%，你会",
    opts: [
      { label: "全部卖出，不再碰它", score: 1 },
      { label: "卖出一部分", score: 2 },
      { label: "继续持有不动", score: 3 },
      { label: "再加一点", score: 4 },
      { label: "明显加仓", score: 5 },
    ],
  },
  {
    id: "maxloss", dim: "风险态度", text: "你能接受的一年内最大亏损",
    opts: [
      { label: "一点都不能亏", score: 1 },
      { label: "5% 以内", score: 2 },
      { label: "10% 以内", score: 3 },
      { label: "20% 以内", score: 4 },
      { label: "30% 以上", score: 5 },
    ],
  },
]

/** 每个维度有哪几题 —— 结果页按它分组解释"你这个等级是从哪几件事推出来的"。
 *  ★从 `QUESTIONS` 派生，不是另抄一份：抄一份的话，题库改了这里不跟着改，
 *    而症状是"结果页讲的维度少了一个"，页面上看着完全正常。 */
export const DIMS: string[] = QUESTIONS.reduce<string[]>(
  (acc, q) => (acc.includes(q.dim) ? acc : [...acc, q.dim]), [])

export const MIN_TOTAL = QUESTIONS.reduce((s, q) => s + Math.min(...q.opts.map((o) => o.score)), 0)
export const MAX_TOTAL = QUESTIONS.reduce((s, q) => s + Math.max(...q.opts.map((o) => o.score)), 0)

// ── 分档 ──────────────────────────────────────────────────────────────

/** C1–C5。★`means` 是描述、`limit` 是**这一档不能说明什么** ——
 *  两者必须成对：只给 `means` 的话，"你属于 C3"会被读成一句比它实际
 *  支撑得起的更强的结论，而这个模块手上只有十道自答题。 */
export const LEVELS: Level[] = [
  {
    code: "C1", name: "保守型",
    means: "你选的答案整体指向「本金安全优先」：不太愿意为了更高收益去承担明显的波动，跌下来会想先离场。",
    limit: "这测的是你的**意愿**，不是你的**处境**。如果这笔钱其实三五年用不上，而你也从没经历过一轮完整下跌，你真能扛的可能比这更高——也可能更低。",
  },
  {
    code: "C2", name: "稳健型",
    means: "能接受小幅波动，但大部分钱的安稳仍然排在收益前面。",
    limit: "「小幅波动」在纸面上和在账户里是两个感受。这一档的人最常见的意外，是第一次看到自己的持仓亏掉几个月工资时，才发现承受力是另一个数。",
  },
  {
    code: "C3", name: "平衡型",
    means: "愿意用一部分波动换长期收益，前提是别把鸡蛋放在一个篮子里。",
    limit: "分散只降波动，不保证不亏。这一档测的是你能忍多大波动，不是你选的标的一定安全。",
  },
  {
    code: "C4", name: "成长型",
    means: "为了长期收益，你能接受账户阶段性跌掉两成左右。",
    limit: "「能接受跌两成」这句话在**想象里**和**真跌的时候**是两件事。真正区分这两者的不是这次测评，是你第一次实际亏到这个数时的反应。",
  },
  {
    code: "C5", name: "进取型",
    means: "你追求较高收益，并且选了明确的「愿意承担大幅波动」那一档。",
    limit: "★承受力高**不等于**该集中下注。这一档量的是你能忍多大痛，不是你该冒多大险——这两件事经常被混成一件。",
  },
]

/** 总分 → 档位。★分档线是**本站教学口径**（页面上明写了这句），
 *  不是监管原文；改这里等于改口径，`Assess.tsx` 顶部那段话要跟着改。 */
export function levelOf(total: number): Level {
  if (total <= 18) return LEVELS[0]
  if (total <= 26) return LEVELS[1]
  if (total <= 34) return LEVELS[2]
  if (total <= 42) return LEVELS[3]
  return LEVELS[4]
}

// ── 打分（唯一出口）───────────────────────────────────────────────────

/** ★**打分的唯一出口**，地位同 `profile.ts` 的 `missingFields`：
 *  表单调它、结果页调它、将来的探针也调它 —— 各写一份的话，
 *  "页面上显示的等级"和"算出来的等级"会慢慢分叉，而症状是两边各自看都对。
 *
 *  返回 `null` 的两种情况都**不猜**：
 *    · 还有题没答完；
 *    · 答案里有一个 label 在**当前题库**里找不到 —— 那说明题库改过了，
 *      旧记录与新题库对不上。猜一个分数就等于编数字，本仓库对这件事零容忍。 */
export function grade(answers: (string | null)[]): { total: number; level: Level } | null {
  let total = 0
  for (let i = 0; i < QUESTIONS.length; i++) {
    const a = answers[i]
    if (!a) return null
    const opt = QUESTIONS[i].opts.find((o) => o.label === a)
    if (!opt) return null
    total += opt.score
  }
  return { total, level: levelOf(total) }
}

/** 还没答的题号（0 基），空数组 = 可以提交。
 *  ★和 `grade` 分开：提交按钮要的是"还差几题"，不是"分数是多少"。 */
export function missing(answers: (string | null)[]): number[] {
  const out: number[] = []
  for (let i = 0; i < QUESTIONS.length; i++) if (!answers[i]) out.push(i)
  return out
}

// ── 与自述偏好对照 ────────────────────────────────────────────────────

/** 把测评等级和画像里那个**自述**的 `risk` 并置，返回一句描述性的话。
 *
 *  ★它**只描述差距，不判定谁对**：自述高不等于"你高估了自己"
 *    （可能是这笔钱真的亏得起），自述低也不等于"你太保守"。
 *    照 `Psych.tsx` 的口径 —— 说"这两处不一样"，不说"你是…"。
 *  ★`risk` 不在 `RISKS` 里（没填过、或填的是别的字）就返回 `null`，
 *    调用方**整段不渲染** —— 不是渲染一段空的（`agent/llm.py` 那条第③条规矩）。 */
export function compareSelfReport(risk: string, level: Level): { gap: number; text: string } | null {
  const i = (RISKS as readonly string[]).indexOf(risk)
  if (i < 0) return null
  const j = LEVELS.findIndex((l) => l.code === level.code)
  if (j < 0) return null
  const gap = i - j
  if (gap === 0) {
    return { gap, text: `你自述的是「${risk}」，这十道题把你放在 ${level.code}（${level.name}）——两处对得上。` }
  }
  if (gap > 0) {
    return {
      gap,
      text: `你自述的是「${risk}」，比这十道题问出来的 ${level.code}（${level.name}）高 ${gap} 档。`
        + `这个差距本身值得看一眼：是真的扛得住那种波动，还是只是不想错过？`
        + `两处都没有错——但它们不是同一件事。`,
    }
  }
  return {
    gap,
    text: `你自述的是「${risk}」，比这十道题问出来的 ${level.code}（${level.name}）低 ${-gap} 档。`
      + `你把自己说得比答案保守——可能是那几道关于钱和期限的题比「你觉得自己是什么样的人」更接近你的实际情况。`,
  }
}

// ── 存取（只住在浏览器里）─────────────────────────────────────────────

export const ASSESS_KEY = "licai-assess"

function isResult(v: unknown): v is Result {
  if (!v || typeof v !== "object") return false
  const o = v as Record<string, unknown>
  return Array.isArray(o.answers)
    && o.answers.every((x) => x === null || typeof x === "string")
    && typeof o.total === "number" && typeof o.level === "string"
    && typeof o.at === "number"
}

/** 读上一次的测评结果。没测过 / 读坏了 / 存不进去 —— 一律返回 null，
 *  **绝不返回一个默认结果**（那会把"没测过"说成"测出来是某一档"）。 */
export function readAssess(): Result | null {
  try {
    const raw = localStorage.getItem(ASSESS_KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as unknown
    return isResult(v) ? v : null
  } catch {
    return null
  }
}

export function saveAssess(r: Result): void {
  try {
    localStorage.setItem(ASSESS_KEY, JSON.stringify(r))
  } catch {
    /* 存不下不影响这一屏：结果照样显示，只是下次进来要重测 */
  }
}

export function clearAssess(): void {
  try {
    localStorage.removeItem(ASSESS_KEY)
  } catch {
    /* 同上 */
  }
}

/** 「上次测评于 N 天前」那句话。★**不设硬性有效期**：
 *  监管口径下测评结果是有有效期、要重测的，但那个年限各地各机构不同，
 *  本站在这里编一个"两年"就成了又一处没有出处的数字。
 *  所以只如实显示距今多久，把"要不要重测"留给用户自己判断。 */
export function sinceText(at: number, now: number = Date.now()): string {
  const days = Math.max(0, Math.floor((now - at) / 86400000))
  if (days === 0) return "今天"
  if (days === 1) return "昨天"
  if (days < 30) return `${days} 天前`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months} 个月前`
  return `${Math.floor(days / 365)} 年前`
}
