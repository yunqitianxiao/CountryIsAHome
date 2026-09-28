/**
 * 预测台账（`/ledger`）的数据层。
 *
 * ★这一页和别页有一个根本区别：**它的数据在服务端**（`data/predictions.jsonl`），
 *   不在 localStorage 里。所以探针那条路上没法靠"播种 localStorage"造出内容 ——
 *   台账得由 `verify_agent.py` 在起测试服务时**种进去**（见 `ledger_seed_lines`）。
 *   不种的话这一页渲染出来是空的，而"空台账"和"页面坏了"在报告里长得一样：
 *   所有渲染判据一起落 0。
 *
 * ★两个入口：`loadLedger()` 是用户那条路（`refresh: 1`，顺手把到期的回填了），
 *   `loadLedgerSync()` 是探针那条路（`refresh: 0`）。理由有两条，都在一处：
 *     ① 同步 XHR 那条路本来就慢，回填要联网取行情（每只 1–3 秒）；
 *     ② **判据要的是确定**。回填会往台账里追加记录，于是"这一屏有几个数"
 *        取决于探针到达的那一刻回填跑到哪儿了 —— 那不是判据变红，是判据**空转**。
 *
 * ★服务端默认**不**回填（`refresh` 缺省是 0）：一个裸 GET 不该干重活
 *   （`check_links` 那种"挨个路由打一下"的判据会因此变慢、还顺带改状态）。
 *   用户那条路显式带 `refresh: 1`。
 */
import { post } from "./api"

/** 一条打分行的汇总 —— 形状来自 `agent/score.py` 的 `summarise()`，不是猜的。 */
export type LedgerTotal = {
  n_origins: number
  horizon_bars: number
  dir_strict: { n_called: number; n_hit: number; hit_rate: number | null; ci95: number[] | null }
  dir_band: { n_called: number; n_hit: number; hit_rate: number | null; ci95: number[] | null }
  baseline_always_up: { hit_rate: number | null }
  baseline_momentum: { n_called: number; hit_rate: number | null; ci95: number[] | null }
  mape_close_model: number
  mape_close_flat: number
  actual_up_pct: number
}

export type LedgerVerdict = {
  n_called: number | null
  hit_rate: number | null
  ci95: number[] | null
  best_baseline: number | null
  margin: number | null
  beats_coin: boolean | null
  beats_baseline: boolean | null
  mape: { model: number | null; flat: number | null; wins: boolean | null }
}

export type LedgerBlock = {
  source: "live" | "backfill" | string
  pred_len: number
  n_total: number
  n_scored: number
  n_pending: number
  n_skip: number
  skip_reasons: string[]
  skipped: string[]
  coverage: { n: number; level: number | null; covered: number | null; rate: number | null; why: string | null }
  /** ★`null` = **还没有到期的条目**，不是 0。见 `Ledger.tsx` 那条判据。 */
  total: LedgerTotal | null
  verdict: LedgerVerdict | null
  n_raw: number
}

export type LedgerResp = {
  ok: boolean
  /** ★`path` 是**服务器上的绝对路径**，2026-09-21 起不再发给浏览器
   *  （`app.py` 在出口处 `summ.pop('path', None)`）—— 用户看不懂它、也用不上它，
   *  而它白送了这台机器的目录结构。所以这里必须是**可选**字段：
   *  写成必填的话，类型会替一个已经不存在的字段作保。
   *  隔离判据读的是**进程内**的 `ledger.path()`，不读这个接口 ⇒ 不受影响。 */
  summary: { path?: string; n: number; n_scored: number; bad_lines: number[]; exists: boolean }
  backfill: Record<string, number | string[]> | null
  blocks: LedgerBlock[]
}

/** 同步版 XHR：探针专用（理由见文件头）。 */
function postSync<T>(url: string, body: unknown): T {
  const x = new XMLHttpRequest()
  x.open("POST", url, false)
  x.setRequestHeader("Content-Type", "application/json")
  x.send(JSON.stringify(body))
  return JSON.parse(x.responseText) as T
}

export async function loadLedger(refresh = true): Promise<LedgerResp> {
  return post<LedgerResp>("/api/ledger", { refresh: refresh ? 1 : 0 })
}

export function loadLedgerSync(): LedgerResp {
  return postSync<LedgerResp>("/api/ledger", { refresh: 0 })
}

/** 分区的标题。★`backfill` 那半**必须**和 live 分开讲清楚它是哪来的 ——
 *  混在一起的话，"台账战绩"这个数几乎全部由离线回测的旧窗口决定，
 *  而它看起来像"这个站在线跑出来的成绩"。
 *  ★这两段是**直接渲染成纯文本**的（`<p>{lab.note}</p>`），不是 markdown：
 *    写 `**加粗**` 的话用户看到的就是两排星号（组合页真出过这个事）。
 *    也不要写脚本文件名（`backtest_kronos.py`）—— 用户不知道那是什么。 */
export function sourceLabel(src: string): { title: string; note: string } {
  if (src === "live") {
    return {
      title: "在线预测",
      note: "在这个站上点一次、算一次的记录。样本少，但每一条都是真的：预测发出来的那一刻，谁也不知道答案。",
    }
  }
  if (src === "backfill") {
    return {
      title: "冷启动回填",
      note: "离线回测跑过的一段段历史窗口，灌进来给个大概。" +
        "它和上面那块不是一个数：这些窗口是事后挑出来的历史片段，" +
        "答案在跑之前就已经知道了。",
    }
  }
  return { title: src, note: "" }
}
