/**
 * 投资画像 —— **只住在浏览器里**。
 *
 * ★服务端**零用户状态**：没有账号、没有会话、没有 cookie，画像是 localStorage
 *   里一条 JSON。这不是"还没做登录"的临时状态，是**定下来的**：定位条那句
 *   「不用注册、不用登录」是本站对用户的承诺，所以画像必须存在用户自己的机器上。
 *   ⇒ 代价写清楚：换个浏览器、清一次站点数据，画像就没了。这是**明知**的取舍。
 *
 * ★这个文件**独占两把键**：`licai-profile`（画像本身）和 `licai-onboard-seen`
 *   （引导看过没有）。为什么不让 Onboarding 组件自己管第二把：那两把键之间有
 *   一条**真实的耦合** —— 「引导看完了」正是在"画像填完"那一刻写的
 *   （引导的最后一步就是画像表单）。分在两个文件里，这条耦合就没人管得住，
 *   将来把最后一步换掉、忘了写标记，症状是"引导每次打开都跳出来"，
 *   而两处代码各自看都没问题。
 *
 * ★读写一律包 try/catch，照 `theme.ts` 的规矩：隐私模式 / 禁了站点数据时
 *   localStorage 会**抛**，抛了就当没存过。绝不能让它把整页带崩 ——
 *   画像读不到只是"没填过"，不是"这页坏了"。
 */

export type Profile = {
  interests: string[]   // 投资兴趣（多选，至少一项）
  risk: string          // 风险偏好（单选）
  horizon: string       // 打算放多久（单选）
  assets: string        // 可投资资产（单选区间）
  income: string        // 每月可投入（单选区间）
  note: string          // 补充说明（自由填写）
}

export const PROFILE_KEY = "licai-profile"
export const SEEN_KEY = "licai-onboard-seen"

/** ★选项表就是**判据和页面的同一个来源**：探针把这个数组回传，判据拿它比对
 *  页面上真的渲染出了哪些选项 —— 两边各写一份的话，"页面少了一个选项"
 *  在判据那边是看不见的（它比的是自己那份）。 */
export const INTERESTS = [
  "基金", "股票", "债券", "可转债", "银行理财", "保险",
  "黄金", "存款", "ETF/指数", "养老/教育金", "外汇", "其他",
] as const

export const RISKS = ["保守", "稳健", "平衡", "进取", "激进"] as const
export const HORIZONS = ["1年以内", "1-3年", "3-5年", "5年以上"] as const
export const ASSETS = ["1万以下", "1-5万", "5-20万", "20-50万", "50-100万", "100万以上"] as const
export const INCOMES = ["1000以下", "1000-3000", "3000-1万", "1万以上"] as const

/** 表单上那五行**按渲染顺序**排好的一份 —— 探针拿它跟 DOM 里读到的逐项比。
 *  ★派生自上面那几个数组，不是另抄一份：抄一份的话"页面少一个选项"
 *    会变成"两边都少"，判据照样绿。 */
export const ALL_OPTS: string[] = [
  ...INTERESTS, ...RISKS, ...HORIZONS, ...ASSETS, ...INCOMES,
]

export const EMPTY: Profile = {
  interests: [], risk: "", horizon: "", assets: "", income: "", note: "",
}

/** ★必填校验**只有这一个出口**。表单用它拦提交、探针也调它 ——
 *  各写一份的话，"表单觉得必填的和判据觉得必填的"会慢慢分叉，
 *  而症状是"判据说必填校验在，用户却交得上去"（或反过来）。
 *  返回**缺哪些字段**（空数组 = 通过），不是布尔 —— 提示语要指出是哪一个。 */
export function missingFields(p: Profile): string[] {
  const miss: string[] = []
  if (!p.interests.length) miss.push("投资兴趣")
  if (!p.risk) miss.push("风险偏好")
  return miss
}

function isProfile(v: unknown): v is Profile {
  if (!v || typeof v !== "object") return false
  const o = v as Record<string, unknown>
  return Array.isArray(o.interests) && typeof o.risk === "string"
    && typeof o.horizon === "string" && typeof o.assets === "string"
    && typeof o.income === "string" && typeof o.note === "string"
}

/** 读画像。没填过 / 读坏了 / 存不进去 —— 一律返回 null，**绝不返回一个默认画像**。 */
export function readProfile(): Profile | null {
  try {
    const raw = localStorage.getItem(PROFILE_KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as unknown
    return isProfile(v) ? v : null
  } catch {
    return null
  }
}

export function saveProfile(p: Profile): void {
  try {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(p))
  } catch {
    /* 存不下不影响这一屏：填完照样能走，只是下次进来又要重填 */
  }
}

export function clearProfile(): void {
  try {
    localStorage.removeItem(PROFILE_KEY)
  } catch {
    /* 同上 */
  }
}

export function hasSeenOnboarding(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === "1"
  } catch {
    return false   // 读不到就当他没看过 —— 宁可多引导一次，也不要让新用户直接落在导航层
  }
}

export function markOnboardingSeen(): void {
  try {
    localStorage.setItem(SEEN_KEY, "1")
  } catch {
    /* 同上 */
  }
}

/** ★「该不该自动把用户送去引导页」**只由这一个函数说了算**。
 *
 *  抽成纯函数不是为了好看，是因为要让它**可被探针直接问到**：
 *  这条逻辑的分支在无头浏览器里是**量不全**的 ——
 *  无头 firefox 每次都是全新的 profile，localStorage 永远是空的，
 *  所以"看过了就不跳"那一半在浏览器里**永远走不到**。
 *  一个只在其中一半上判绿的判据，正是本仓库反复在打的那种假绿。
 *  ⇒ 让页面把几组输入的返回值都回传，判据一次把真值表钉全（含对照组）。
 *
 *  ★第一行是**保命线**，不是优化：`?probe=1` 是验证脚本量的那条路，
 *  它一旦被重定向走，`probeAndBeacon` 会在**另一个页面**上跑 ——
 *  于是几十条渲染判据量到的全是空白（"没收到探针数据"）。
 *  无头浏览器永远没看过引导，所以没有这一行，这一页每次都会跳。
 *
 *  ★它被**调用在最前面**（`main.tsx` 里探针分支之前）——这一条是判据的落点：
 *  探针载荷里的 `bootPath` 必须还是 `/`。两处各判一次不是重复：
 *  这个函数可以是对的、而 main.tsx 忘了调它。 */
export function shouldAutoOpenOnboarding(path: string, search: string, seen: boolean): boolean {
  if (/[?&]probe=1/.test(search)) return false
  return path === "/" && !seen
}
