/**
 * 后端契约层。**只做类型和取数，不做任何业务判断**。
 *
 * 契约来源是 agent/tools.py 的真实返回（不是猜的）：
 *   get_kline     → summary{name,code,market,last_close,chg_pct_5d,chg_pct_20d,
 *                           chg_pct_60d,range_high,range_low,ann_vol_pct,source,from,to}
 *                   data{kind:'kline', bars, more}
 *   predict_kline → summary{...pred_chg_pct,direction,pred_high,pred_low,
 *                           pred_ann_vol_pct,hist_ann_vol_pct,ohlc_repaired_bars,
 *                           ohlc_max_clamp_pct,caveat...}
 *                   data{kind:'predict', history, pred, base_close, params}
 *
 * ★Bar 是**列式**的（`_bars()`：`{t:[],o:[],h:[],l:[],c:[],v:[]}`），不是
 *   `[{t,o,h,l,c}×N]`。按对象数组写会在 `.t[i]` 上取到 undefined 而不报错 ——
 *   那种错会一路走到画图才现形，所以类型在这里就写死成列式。
 */
export type Bars = {
  t: string[]
  o?: number[]
  h?: number[]
  l?: number[]
  c: number[]
  v?: number[]
}

export type KlineSummary = {
  ok: boolean
  name: string
  code: string
  market: string
  rows: number
  from: string
  to: string
  last_close: number
  chg_pct_5d: number | null
  chg_pct_20d: number | null
  chg_pct_60d: number | null
  range_high: number
  range_low: number
  ann_vol_pct: number | null
  source: string | null
  error?: string
  msg?: string
  hint?: string
}

/** ★`spread` 是**多采样**才有的东西（N≥2）。N=1 时后端给 `null`，页面必须能画
 *  "没有区间"这一态 —— 一条路径的 min/max 不是区间，把它画成区间就是在编。
 *  `withheld` = 方向弃答（N 条路径的投票没有系统偏向），此时 `direction` 是
 *  'flat'，而 `direction_center` 保留中心线自己那个判断（诊断用）。 */
export type PredictSpread = {
  n_paths: number
  level_pct: number
  lo: number
  hi: number
  lo_pct: number | null
  hi_pct: number | null
  width_pct: number | null
  hist_move_pct: number | null
  up: number
  down: number
  flat: number
  called: number
  consensus: number | null
  vote_ci95: [number, number] | null
  withheld: boolean
  why: string | null
}

export type PredictSummary = {
  ok: boolean
  name: string
  code: string
  market: string
  pred_len: number
  sample_count: number
  base_close: number
  pred_last_close: number
  pred_chg_pct: number | null
  pred_high: number
  pred_low: number
  pred_max_up_pct: number | null
  pred_max_dn_pct: number | null
  direction: 'up' | 'down' | 'flat'
  direction_center: 'up' | 'down' | 'flat'
  spread: PredictSpread | null
  days_above_base: number
  hist_ann_vol_pct: number | null
  pred_ann_vol_pct: number | null
  model: string
  ms: number
  cached: boolean
  ohlc_repaired_bars: number
  ohlc_max_clamp_pct: number
  caveat: string
  error?: string
  msg?: string
  hint?: string
}

export type KlineResp = {
  summary: KlineSummary
  data: { kind: 'kline'; bars: Bars; name: string; code: string; more: number } | null
}

export type PredictResp = {
  summary: PredictSummary
  data: {
    kind: 'predict'
    name: string
    code: string
    history: Bars
    pred: Bars
    base_close: number
    /** 逐根采样扇形（收盘价那一路的极差）。N=1 时为 null —— 没有范围可画。 */
    band: { lo: number[]; hi: number[]; level_pct: number; withheld: boolean } | null
    params: { pred_len: number; sample_count: number; lookback: number; T: number; top_p: number; ms: number }
    ohlc_repaired_bars: number
    ohlc_max_clamp_pct: number
  } | null
}

export type KeyState = {
  configured: boolean
  mask: string | null
  source_text: string
}

export type Status = {
  engine: { loaded: boolean; [k: string]: unknown }
  llm: { configured: boolean; model: string }
  key: KeyState
  kb: { terms?: number; edges?: number; [k: string]: unknown }
  families: unknown[]
}

/** ★`export` 出去是为了让别的数据层（`game.ts`）不用把这段抄一遍 ——
 *  抄一遍之后"出错时 `r.json()` 抛不抛"这种事就会有两个答案。
 *  ★注意它**不看状态码**：本站的接口在 `ok:false` 时发 400，而响应体
 *  照样是一份完整的 JSON（`summary` 里写着为什么）。页面要的是那份 JSON，
 *  不是"抛个异常表示失败了" —— 抛出去之后 `msg`/`hint` 就没了，
 *  页面上只剩下"出错了"三个字。 */
export async function post<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const j = (await r.json()) as T
  return j
}

export const getKline = (symbol: string, days = 400) => post<KlineResp>('/api/kline', { symbol, days })
/** ★`sample_count` 不填就**不传这个键**，让服务端用它自己的默认值（现在是 9）。
 *  在前端写死一个默认值等于把那个数抄成两份：服务端改默认值时这里不会跟着变，
 *  而两份都能"正常工作"，只是采样的条数不一样。 */
export const getPredict = (symbol: string, pred_len = 30, sample_count?: number) =>
  post<PredictResp>('/api/predict', { symbol, pred_len, sample_count })

export async function getStatus(): Promise<Status> {
  return (await fetch('/api/status')).json()
}

export async function getKey(): Promise<KeyState> {
  return (await fetch('/api/key')).json()
}

/** ★这三条是唯一能改 key 的路。响应里**永远只有掩码**（服务端保证）。 */
export async function saveKey(key: string): Promise<KeyState> {
  return post<KeyState>('/api/key', { key })
}

export async function clearKey(): Promise<KeyState> {
  const r = await fetch('/api/key', { method: 'DELETE' })
  return r.json()
}
