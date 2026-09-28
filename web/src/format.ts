/**
 * 数字格式化 + **红涨绿跌**。
 *
 * ★红涨绿跌是**规矩不是偏好**（旧页面 index.html:21-22 原话：「A 股习惯，与欧美相反。
 *   抄欧美配色会让用户把涨看成跌」）。所以这里不叫 `good`/`bad`，也不叫 `positive`/`negative`
 *   —— 叫 `up`/`down`，颜色的映射只写一次，全站从这里取。
 *   ★把涨跌映射成 green/red 是**改错了方向**：它会静默地把每个用户的涨跌看反，
 *   而页面上没有任何异常。改动这里之前先看 verify_agent.py 里那两条判据（亮/暗各一条）。
 */
export type Dir = 'up' | 'down' | 'flat'

export function dirOf(v: number | null | undefined): Dir {
  if (v == null || Number.isNaN(v)) return 'flat'
  if (v > 0) return 'up'
  if (v < 0) return 'down'
  return 'flat'
}

/** 涨=红、跌=绿、平=灰。返回的是**类名**，不是色值 —— 色值在 index.css 的 token 里。 */
export function dirClass(v: number | null | undefined): string {
  return { up: 'text-up', down: 'text-down', flat: 'text-flat' }[dirOf(v)]
}

export function dirBg(v: number | null | undefined): string {
  return { up: 'bg-up-soft', down: 'bg-down-soft', flat: 'bg-gray-100' }[dirOf(v)]
}

/** 带正负号的百分比。null → 「—」而不是 0 —— 「没有数据」和「零增长」不是一回事。 */
export function pct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** 千分位、不带小数 —— 价格卡上的大数字用这个。 */
export function price(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** 价格在 [low, high] 区间里的位置，0–100。用于 CategoryBar 的 marker。 */
export function posInRange(v: number | null | undefined, low: number, high: number): number {
  if (v == null || high <= low) return 50
  return Math.max(0, Math.min(100, ((v - low) / (high - low)) * 100))
}
