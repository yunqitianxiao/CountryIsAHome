/**
 * 九个角色的**展示元数据**（名字、单字标记、语气色、一句话职责）。
 *
 * ★顺序逐字来自规格 §3.4 的 `ROLES`，**别改**：服务端发 `agent_round` 时
 *   带的 `roles` 数组就是按这个顺序，而它是页面上那排卡片的**初始占位**。
 *   顺序变了，卡片会在第一轮发言时重新洗牌 —— 看着像"卡片跳了一下"。
 *
 * ★★这一层是**兜底**，不是真源：`agent_start` 事件里带着 `name`/`icon`，
 *   服务端给了就用服务端的（见 `AgentBoard` 里的 `nameOf`）。前端写死一份
 *   再"以我为准"，就变成了"服务端改了角色的中文名，页面上永远显示旧的"。
 *
 * ★**没有 emoji**。这个站的排版是中文金融终端那一套（衬线标题 + 等宽数字），
 *   塞一排彩色的表情符号进来会把整页的调子带跑。单字标记（多/空/基/技/舆/风/经/图/心）
 *   在中文里有信息量，而且是在卡片上能一眼认出来的那种。
 */
import type { Role } from "../api2"

export type RoleMeta = {
  role: Role
  /** 兜底中文名。服务端 `agent_start.name` 优先。 */
  name: string
  /** 卡上的那一枚单字标记。 */
  glyph: string
  /** 一句话职责（鼠标悬停 / `title`）。 */
  note: string
}

export const ROLE_META: Record<Role, RoleMeta> = {
  bull: { role: "bull", name: "多头分析师", glyph: "多", note: "只从「它会涨」那一侧找证据" },
  bear: { role: "bear", name: "空头分析师", glyph: "空", note: "只从「它会跌」那一侧找证据" },
  fundamental: { role: "fundamental", name: "基本面分析师", glyph: "基", note: "估值、盈利、行业位置" },
  technical: { role: "technical", name: "技术面分析师", glyph: "技", note: "价量形态、指标、关键位" },
  sentiment: { role: "sentiment", name: "舆情分析师", glyph: "舆", note: "资讯与情绪面" },
  risk: { role: "risk", name: "风控官", glyph: "风", note: "独立一票否决 / 降级权（条件是数值）" },
  pm: { role: "pm", name: "投资经理", glyph: "经", note: "汇总各角色，出最终裁决" },
  kg_tutor: { role: "kg_tutor", name: "图谱解释", glyph: "图", note: "把概念讲清楚，并指到图谱里的词条" },
  psych: { role: "psych", name: "心理顾问", glyph: "心", note: "常见行为偏差的提示（不是诊断）" },
}

export const ROLE_ORDER: Role[] = [
  "bull", "bear", "fundamental", "technical", "sentiment", "risk", "pm", "kg_tutor", "psych",
]

export function metaOf(role: string): RoleMeta {
  return ROLE_META[role as Role] ?? {
    role: "pm",
    name: role,
    glyph: "?",
    note: "服务端发来的角色，前端这张表里没有它",
  }
}

/** 立场（stance −1…+1）→ 语气。★**红涨绿跌**：看多 = 涨 = 红，
 *  和 `format.ts` 里那条全站规矩是同一个映射，别在这里另立一套。 */
export function stanceTone(stance: number | null): "up" | "down" | "flat" {
  if (stance === null || !Number.isFinite(stance)) return "flat"
  if (stance > 0.05) return "up"
  if (stance < -0.05) return "down"
  return "flat"
}

/** 方向（long/short/flat）→ 中文 + 语气。裁决卡、信号、共振都用这一份。 */
export function sideLabel(side: string | null | undefined): string {
  return { long: "看多", short: "看空", flat: "不表态" }[side ?? ""] ?? String(side ?? "—")
}

export function sideTone(side: string | null | undefined): "up" | "down" | "flat" {
  return side === "long" ? "up" : side === "short" ? "down" : "flat"
}

/** 体制（`agent/intraday` 的 regime）。**原样翻译，不加工** ——
 *  "squeeze" 译成"挤压"，而"挤压"对理财小白是个没信息量的词，
 *  所以后面跟一句括号说明。 */
export const REGIME_LABEL: Record<string, string> = {
  trend_up: "上行趋势",
  trend_down: "下行趋势",
  range: "区间震荡",
  squeeze: "波动收窄（挤压）",
}

/** 风险提示那句话。**必须原样含「不构成投资建议」**（规格 §3.4 的硬规矩）。
 *  ★写成一个常量而不是各页各抄一句：抄的话迟早有一页少掉"不构成投资建议"六个字，
 *    而它少掉的页面看起来和别的页面一模一样。 */
export const RISK_NOTE = "以上内容由程序自动生成，仅供学习参考，不构成投资建议。"
