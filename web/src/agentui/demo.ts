/**
 * **演示数据**。只在开发构建 + `?demo=1` 下会被调用（见 `State.tsx` 的 `DEMO`）。
 *
 * ★★这个文件为什么必须存在、又为什么必须长成这样：
 *   这五页的后端**现在还不存在**（规格 §3.2–§3.5 是并行施工的另外几路）。
 *   没有演示数据，页面只能看到降级形态 —— 而"两轮发言流""多空交锋""采集健康度"
 *   这些版式**恰恰是这一批页面的主要内容**，做不出来也验收不了。
 *   所以演示数据是**开发工具**，不是产品功能。
 *
 * ★三条纪律，每条都是为了"它不可能被当成行情看"：
 *   ① 出口只有 `State.tsx` 的 `DEMO` 一个，而那个常量在生产构建里
 *      **构建期就是 `false`**（`import.meta.env.DEV` 被 Vite 替换成字面量）——
 *      线上无论带什么查询串都进不来。这不是约定，是常量折叠。
 *   ② 每一页顶上有一条**警告色的横幅**（`<DemoBanner/>`，`role="alert"`），
 *      只要演示数据出现它就必然出现。数据自己不许"安静地"出现在页面上。
 *   ③ 序列一律**确定性合成**（同参数同结果，照 `chat.ts: demoChart` 那套写法），
 *      不引随机数 —— 随机会让"两趟跑出来不一样"变成一件需要解释的事，
 *      而演示数据不该消耗任何人的诊断时间。
 *
 * ★它**不假装**是行情：没有一处读网络，每个数的来源就是下面这几行算术。
 */
import type {
  AgentEv, Anomaly, CollectorStatus, IntradayResp, LinkageResp, RingItem, SourceHealth,
} from "../api2"
import type { Candle } from "./charts"
import { ROLE_ORDER } from "./roles"

/** ★这个开关**只有一份定义**，在 `State.tsx`（它和 `<DemoBanner/>` 是一对：
 *  横幅只在开关为真时出现 —— 两处各定义一次的话，迟早会出现"数据是演示的、
 *  横幅没出来"那一屏，而那一屏看起来和真数据一模一样）。
 *  这里**转出去**只是让五个页面能从同一个地方把"演示那一套"整个取走：
 *  第二次导出，不是第二个真值源。 */
export { DEMO } from "./State"

/** 确定性伪随机（同一个种子 → 同一串数）。**不是**为了"像真的"，
 *  是为了两次打开同一页形状一致 —— 版式调试时"变了一个数"必须只可能是代码改了。 */
function seeded(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 4294967296
  }
}

const pad = (n: number) => String(n).padStart(2, "0")

/** 演示时刻：写死在**一个交易日盘中**，不取 `Date.now()`。
 *  取当前时间的话，同一页在不同时刻打开会落进不同时段分支（`session.phase`），
 *  而那是另一条要调的路径。 */
const DEMO_DAY = "2026-09-22"

function minutesFrom(hhmm: string, k: number): string {
  const [h, m] = hhmm.split(":").map(Number)
  const t = h * 60 + m + k
  return `${pad(Math.floor(t / 60))}:${pad(t % 60)}`
}

// ── §3.2 分钟级策略 ───────────────────────────────────────────────────

/** 演示返回 = 规格 §3.2 的形状 **加上** 分钟 K 本身。
 *  ★`_bars`/`_vwap` 带下划线是故意的：它们在**真实返回里不存在**
 *    （§3.2 只给分析结果，不给 K 线），带个下划线是为了让"这一段不是契约"
 *    在代码里一眼看得出来 —— 同 `api.ts` 里 `_bars()` 用下划线标"内部"是一个约定。 */
export type DemoIntraday = IntradayResp & { _bars: Candle[]; _vwap: number[] }

export function demoIntraday(code = "sh000300", periods: readonly number[] = [5, 15, 30]): DemoIntraday {
  const rnd = seeded(20260922)
  const n = 96 // 15 分钟线一个上午的量
  const bars: Candle[] = []
  let c = 3980
  for (let i = 0; i < n; i++) {
    const o = c
    const cl = c * (1 + (Math.sin(i / 9.3) * 0.0016 + (rnd() - 0.5) * 0.0022))
    bars.push({
      t: minutesFrom("09:30", i * 15),
      o: +o.toFixed(2),
      h: +(Math.max(o, cl) * 1.0008).toFixed(2),
      l: +(Math.min(o, cl) * 0.9992).toFixed(2),
      c: +cl.toFixed(2),
      v: Math.round(18000 * (1 + Math.abs(Math.sin(i / 5)) * 2.4)),
    })
    c = cl
  }
  // VWAP：逐根的累计成交量加权均价（**真的按那个式子算**，不是另画一条线）
  let pv = 0
  let vv = 0
  const vwap = bars.map((b) => {
    pv += ((b.h + b.l + b.c) / 3) * b.v
    vv += b.v
    return +(pv / vv).toFixed(2)
  })

  const px: Record<string, number> = {}
  for (const p of periods) px[String(p)] = p === 5 ? 240 : p === 15 ? 96 : 48
  const base: DemoIntraday = {
    ok: true, market: "cn", code, name: "沪深300（演示）", ts: `${DEMO_DAY} 10:45`,
    bars: px,
    regime: Object.fromEntries(periods.map((p) => [String(p), p === 30 ? "range" : "trend_up"])),
    signals: [
      {
        id: "orb_break", name: "开盘区间突破", period: 5, side: "long", strength: 0.72,
        why: "前 30 分钟区间 3972.10–3994.60，最新价 3996.20 站到上沿之上 1.60 点；同期量比 2.1 倍",
        refs: { level: 3994.6, rvol: 2.1 },
      },
      {
        id: "vwap_revert", name: "VWAP 偏离与回归", period: 15, side: "flat", strength: 0.34,
        why: "现价 3996.20 对 VWAP 3988.40 偏离 +0.196%，未达 0.35% 的阈值；成交量未确认",
        refs: { vwap: 3988.4, dev_pct: 0.196 },
      },
      {
        id: "rvol_grade", name: "相对成交量分级", period: 5, side: "long", strength: 0.61,
        why: "本根量 41,200 手，过去 20 根同刻中位数 19,600 手 ⇒ RVOL 2.10，落进「明显放量」档（>1.5）",
        refs: { rvol: 2.1, med20: 19600 },
      },
      {
        id: "volume_profile", name: "成交量分布（POC / 价值区）", period: 15, side: "long", strength: 0.48,
        why: "POC 3985.50 在现价之下 10.70 点；价值区 3978.00–3993.20，现价刚越上沿",
        refs: { poc: 3985.5, va_hi: 3993.2, va_lo: 3978.0 },
      },
      {
        id: "divergence", name: "量价背离", period: 15, side: "short", strength: 0.42,
        why: "最近 12 根：价格新高而 OBV 未新高，价-量滚动相关 −0.31（窗口 12，样本 12）",
        refs: { obv_slope: -0.08, corr: -0.31 },
      },
      {
        id: "momentum", name: "短周期动量", period: 5, side: "long", strength: 0.66,
        why: "EMA9 3991.20 > EMA21 3986.90（差 4.30 点）；RSI14 61.2；ROC5 +0.42%",
        refs: { ema9: 3991.2, ema21: 3986.9, rsi14: 61.2, roc5: 0.42 },
      },
      {
        id: "bollinger", name: "布林带挤压/扩张", period: 30, side: "long", strength: 0.55,
        why: "带宽（上轨−下轨）/中轨 = 0.62%，处在近 60 根的 18% 分位 ⇒ 挤压；已向上轨扩张",
        refs: { bandwidth_pct: 0.62, pctile60: 18 },
      },
      {
        // ★**`period` 是 `null`**（不是 0）：这一条是**跨周期**的，服务端给跨周期信号
        //   用的就是这个写法。上一版演示写的是 0（规格里"综合"那一档），而服务端
        //   现在不用 0 了 —— 演示要是还写 0，页面上就永远看不到"跨周期"那一支，
        //   而线上真实发生的是那一支。
        id: "resonance", name: "多周期共振", period: null, side: "long", strength: 0.63,
        why: "5/15/30 三档方向：多 / 多 / 不表态 ⇒ 一致度 2/3，加权 0.63",
        refs: { agree_long: 2, total: 3 },
      },
      {
        id: "atr_stop", name: "ATR 止损与目标", period: 15, side: "long", strength: 0.5,
        why: "ATR14 = 4.10 点；止损 1.5×ATR = 6.15 点，目标1 2.0×ATR = 8.20 点 ⇒ 盈亏比 1.33",
        refs: { atr: 4.1, stop_pts: 6.15, target_pts: 8.2 },
      },
      {
        id: "session_effect", name: "时段效应", period: 5, side: "flat", strength: 0.28,
        why: "当前 10:45 落在「开盘后 30–75 分钟」窗；该窗历史样本（2022-01 至 2026-09，2,340 根 5 分钟线）均值漂移 +0.008%，标准差 0.19% —— 统计上不显著",
        refs: { win: "10:00-10:45", n: 2340 },
      },
    ],
    composite: { score: 0.35, side: "long", confidence: 0.61, agree: { long: 4, short: 1, flat: 2 }, n_signals: 7 },
    volume: {
      rvol: 2.1,
      rvol_by_period: { "5": 2.1, "15": 1.6, "30": 1.2 },
      rvol_base: { slot_mean: 19600, naive_mean: 27300, note: "同刻中位数 19,600 手（20 根窗口）。" },
      profile: { poc: 3985.5, va_hi: 3993.2, va_lo: 3978.0 },
      divergence: "bearish",
    },
    levels: {
      entry: 3996.2, stop: 3990.05, target1: 4004.4, target2: 4012.6,
      atr: 4.1, rr: 1.33, method: "ATR14 的 1.5 / 2.0 / 3.0 倍（自开盘价起算）",
      side: "long", composite_side: "long", long: 4, short: 1,
    },
    session: {
      phase: "morning",
      window: "10:00-10:45",
      note: "开盘 30 分钟已过；这段时间的历史波动比全天均值高约 18%（样本见 signals.session_effect）",
    },
    errors: [
      "资金流（min）这个源这轮没返回数据，所以「主力净流入」那条策略整条没出现",
    ],
    _bars: bars,
    _vwap: vwap,
  }
  return synthCase() ? synthFrame(base) : base
}

/** 演示里的**第二个场景**：服务端退回了一份合成帧（`synthetic: true`）。
 *
 *  ★为什么演示要专门做这一个：`/intraday` 上最不能坏的一条就是**合成数据横幅**
 *    —— 它是"你看到的这些价位是编的"这件事**唯一的出口**。如果只在真后端
 *    退回合成帧时才出现，那我交付前永远没机会亲眼看过它长什么样。
 *  ★这个开关（`?demo=1&case=synth`）**和 `?demo=1` 一样进不了生产**：整个
 *    模块在构建期就被 `import.meta.env.DEV` 删掉了（`State.tsx` 那段）。
 *  ★这一个合成帧沿用**真契约的字段名**（`data_source` / `synthetic`），
 *    不是另发明一套 —— 演示要是自己发明字段，测的就是另一条代码路径。
 *    它同时还把几个"合法空值"走一遍：`volume.profile: null`、
 *    `divergence: null`、`levels: null`、某一档 `rvol: null`、`phase: 'all'`。 */
function synthCase(): boolean {
  if (typeof location === "undefined") return false
  return /[?&]case=synth(&|$)/.test(location.search)
}

function synthFrame(b: DemoIntraday): DemoIntraday {
  return {
    ...b,
    market: "crypto", code: "BTCUSDT", name: "BTC/USDT（合成的分钟帧）",
    data_source: "SYNTH(scenario=random)",
    synthetic: true,
    volume: {
      rvol: null,
      rvol_by_period: { "5": 1.4, "15": null, "30": null },
      rvol_base: { slot_mean: null, naive_mean: null, note: "合成帧没有历史量，两个基准都是 null（不是 0）。" },
      profile: null,
      divergence: null,
    },
    levels: null,
    session: { phase: "all", window: "00:00-24:00", note: "加密 24 小时全时段，服务端给的取值就是 all。" },
    // 合成帧里**没有真 K 线**：连演示用的那份蜡烛也不给它（`_bars: []`）。
    //   ★不这么写就白做了 —— 页面上 `withBars()` 是 `r.klines ?? d._bars`，
    //     而 `null ?? x` 是 `x`，只把 `klines` 置 null 的话它会把演示蜡烛捡回来，
    //     于是"合成帧画着真蜡烛"，正好是这一支要防的那件事。
    _bars: [], _vwap: [], klines: null, vwap_series: null,
    errors: [
      "分钟线源这一轮没拿到真数据，退回了一份合成帧把流程跑完 —— 下面的价位和成交量都是造出来的",
    ],
  }
}

// ── §3.3 关联检测 ─────────────────────────────────────────────────────

const DEMO_SOURCES: SourceHealth[] = [
  { id: "adata.realtime", ok: true, n: 5412, ms: 812, err: null },
  { id: "adata.boards.concept", ok: true, n: 386, ms: 1240, err: null },
  { id: "efinance.hk", ok: true, n: 2612, ms: 690, err: null },
  { id: "efinance.us", ok: false, n: 0, ms: 3000, err: "timeout after 3.0s（这个源这一轮没答完）" },
  { id: "crypto.okx", ok: true, n: 248, ms: 141, err: null },
  { id: "akshare（兜底）", ok: true, n: 0, ms: 0, err: null },
]

function demoAnomaly(code: string, name: string, pct: number, vr: number, amt: number, z1: number, z2: number): Anomaly {
  return {
    code, name, market: "cn",
    kinds: [pct > 0 ? "pct_spike" : "pct_drop", vr > 1.5 ? "volume_spike" : "volume_quiet"].slice(0, vr > 1.5 ? 2 : 1),
    metrics: {
      pct, vol_ratio: vr, amount_yi: amt,
      turnover: +(amt / 120).toFixed(2), z_pct: z1, z_vol: z2,
    },
    ts: `${DEMO_DAY} 10:45`,
  }
}

export function demoLinkage(top = 20): LinkageResp {
  const anomalies = [
    demoAnomaly("600519", "贵州茅台", 6.2, 4.1, 12.3, 2.9, 3.4),
    demoAnomaly("300750", "宁德时代", 4.8, 3.2, 28.6, 2.4, 2.8),
    demoAnomaly("002415", "海康威视", -5.1, 2.7, 9.4, -2.6, 2.2),
    demoAnomaly("601318", "中国平安", 2.2, 1.9, 21.1, 1.4, 1.7),
    demoAnomaly("000858", "五粮液", 5.4, 3.6, 15.2, 2.7, 3.0),
  ].slice(0, Math.max(1, Math.min(top, 5)))

  const links: LinkageResp["links"] = {
    "600519": {
      boards: [
        { code: "BK0477", name: "白酒", kind: "concept", pct: 3.1, lead_code: "600519", lead_name: "贵州茅台" },
        { code: "BK0438", name: "消费50", kind: "concept", pct: 1.8, lead_code: "000858", lead_name: "五粮液" },
      ],
      peers: [
        { code: "000858", name: "五粮液", rel: "同板块", rel_value: 0.87, lag_min: 0, pct: 5.4, vol_ratio: 3.6, ts: `${DEMO_DAY} 10:45` },
        { code: "000568", name: "泸州老窖", rel: "高相关", rel_value: 0.81, lag_min: 0, pct: 4.2, vol_ratio: 2.9 },
        { code: "600809", name: "山西汾酒", rel: "领先", rel_value: null, lag_min: 15, pct: 3.8, vol_ratio: 2.4 },
      ],
      evidence: [
        "同属 白酒 / 消费50 两个概念板块",
        "与 000858 的 20 日收益相关性 0.87（窗口 20，样本 20）",
        "与 600809 出现 15 分钟领先关系（同板块内，样本 18 天）",
      ],
    },
  }

  return {
    ok: true,
    ts: `${DEMO_DAY} 10:45`,
    snapshot: { ts: `${DEMO_DAY} 10:45`, market: "cn", rows: 5412 },
    collected: {
      sources: DEMO_SOURCES,
      ts: `${DEMO_DAY} 10:45:07`,
      rows: 8272,
      markets: ["cn", "hk", "us", "crypto"],
    },
    anomalies,
    links,
    note: "上面这些只是「采到了什么」的流水：涨跌幅、量比、z-score 都是从快照里直接算的。这里不做判断、不给买卖建议、也不预测。相关性那几条都带窗口和样本数 —— 样本不足 60 的根本不给这个数。",
  }
}

// ── §3.5 常驻采集器 ───────────────────────────────────────────────────

export function demoCollectorStatus(): CollectorStatus {
  return {
    ok: true,
    ts: `${DEMO_DAY} 10:45:07`,
    running: true,
    last_ts: `${DEMO_DAY} 10:45:07`,
    next_ts: `${DEMO_DAY} 10:46:07`,
    path: "data/collect/",
    markets: ["cn", "hk", "us", "crypto"],
    rows: 8272,
    ticks: 41,
    health: { sources: DEMO_SOURCES },
    sources: DEMO_SOURCES,
    last: { ts: `${DEMO_DAY} 10:45:07`, market: "cn", rows: 5412, ms: 812 },
    err: null,
  }
}

export function demoRing(limit = 120): RingItem[] {
  const rnd = seeded(77712)
  const out: RingItem[] = []
  for (let i = 0; i < Math.min(limit, 40); i++) {
    const bad = i === 5 || i === 17
    out.push({
      ts: `${DEMO_DAY} ${minutesFrom("09:30", i)}:0${Math.floor(rnd() * 9)}`,
      market: "cn",
      rows: 5200 + Math.floor(rnd() * 400),
      ms: 700 + Math.floor(rnd() * 900),
      ok: !bad,
      err: bad ? "efinance.us 超时（这一个源没答完，别的源照常）" : null,
      sources: DEMO_SOURCES.map((s) => ({ ...s, ok: bad && s.id === "efinance.us" ? false : s.ok })),
    })
  }
  return out
}

// ── §3.4 多智能体博弈 ─────────────────────────────────────────────────

/** 一轮完整的演示博弈：**九人发言 + 第二轮多空交锋 + 动态权重 + 风控否决 + 裁决**。
 *
 *  ★这一串事件是按**真实事件表的形状**写的（`agent_round` → `agent_start` →
 *    `agent_say` → `agent_done` …），喂给页面的也是**同一个 reducer** ——
 *    演示回放和真实 SSE 走同一条处理路径（这是 `chat.ts` 顶上那条纪律：
 *    "demo() 必须走和真实回答同一个 handle()"）。各写一条路的话，
 *    演示能过的东西真实链路可能过不了，而那正是演示要防的事。 */
export function demoAgentEvents(question: string): AgentEv[] {
  const ev: AgentEv[] = []
  const say = (
    role: string, round: number, text: string, stance: number | null, conf: number | null,
    evidence: string[] = [], ms = 1200,
  ) => {
    ev.push({ type: "agent_start", role })
    ev.push({ type: "agent_say", role, round, text, stance, conf, evidence, ms })
    ev.push({ type: "agent_done", role, ok: true, ms })
  }

  ev.push({ type: "agent_round", round: 1, roles: [...ROLE_ORDER] })
  say("bull", 1, "现在 3980 一带的换手已经比上周同期高一倍，而价格没跌回去 —— 抛压被人接走了。我的看法是偏多。",
    0.62, 0.60, ["近 5 日均量 / 前 5 日均量 = 1.94", "价格站稳 20 日均线 3 个交易日"], 1420)
  say("bear", 1, "量是放大了，但你看的是「没跌回去」，不是「涨上去了」。上沿 3994 试了三次都没过，这更像派发。",
    -0.35, 0.55, ["3994 一线三次触及未破", "MACD 柱状连续 4 根收缩"], 1310)
  say("fundamental", 1, "这只标的的估值分位处在近 5 年的 12%，盈利预期这两个月没被下调。基本面这一侧没有反向证据。",
    0.20, 0.50, ["PE 分位 12%（近 5 年）", "近 60 日盈利预测上调 0 次、下调 0 次"], 1640)
  say("technical", 1, "5 分钟和 15 分钟都在多头一侧，30 分钟还没表态。关键位很清楚：上方 3994.6，下方 3972.1。",
    0.45, 0.62, ["EMA9 > EMA21（5 分钟），差 4.30 点", "ATR14 = 4.10 点"], 1180)
  say("sentiment", 1, "这两天的相关资讯量比平时多三成，用词偏正面，但没有一条是政策级的。情绪这一票我给得很轻。",
    0.15, 0.35, ["相关资讯 42 条 / 平常 32 条", "无政策级条目"], 1960)
  say("kg_tutor", 1, "先解释两个词：**量比**是当前成交量与过去同刻均量之比，>1.5 才算明显放量；**POC** 是成交量最密集的那个价位。这两个词在图谱里都有词条，点开就能看。",
    null, 0.80, ["词条：量比", "词条：POC（成交量控制点）"], 900)
  say("psych", 1, "提醒一句：**最近三天连涨**的标的容易让人产生「再不买就来不及」的感觉，这是 FOMO 的常见触发条件。这只是常见偏差的提示，不是对你的判断。",
    null, 0.70, ["近 3 日累计涨幅 +4.2%"], 860)
  say("risk", 1, "我先给一条可复核的数值条件：**现价对 20 日波动率归一后的当日涨幅 2.9 个标准差**。这个数超过 2.5，触发降级条款。",
    null, 0.75, ["z_pct = 2.9（阈值 2.5）", "ATR14 = 4.10 点"], 1120)
  say("pm", 1, "第一轮先不下结论。多头和空头在「3994 过不过」上是真分歧，第二轮请你们拿到对方的说法之后再各自表态。",
    0.10, 0.40, [], 640)

  ev.push({ type: "agent_round", round: 2, roles: ["bull", "bear", "risk", "pm", "technical", "fundamental"] })
  // ★第二轮：多空**必须**要么改 stance、要么写出一条反驳（规格 §3.4）。
  //   这里两个都做了，因为"页面上看得见立场变了"这件事需要一条能演示的路径。
  say("bull", 2, "空头说 3994 三次没破 —— 这点我接受，所以我把立场从 0.62 下调到 0.48。但反驳一条：前两次触及时的量是 1.1 倍和 1.3 倍，这次是 2.1 倍，**同样是失败，成本不一样**。",
    0.48, 0.58, ["三次触及的量比：1.1 / 1.3 / 2.1", "stance 由 0.62 修正为 0.48"], 1540)
  say("bear", 2, "多头的量能论点我认，所以我不再坚持「派发」，但方向不变：我把立场从 −0.35 收到 −0.22。理由换成风险收益比 —— 这里向上到 3994 只有 0.5 个 ATR，向下到 3972 有 1.9 个 ATR，这个赔率不值得做多。",
    -0.22, 0.60, ["向上空间 0.5×ATR / 向下空间 1.9×ATR", "stance 由 −0.35 修正为 −0.22"], 1610)
  say("technical", 2, "补一句：如果 3994.6 带量过去，我会把 30 分钟那一档也翻成多；现在它还是「不表态」。",
    0.45, 0.62, ["30 分钟 regime = range"], 880)
  say("fundamental", 2, "我的观点不变，也没有新证据。",
    0.20, 0.50, [], 520)
  say("risk", 2, "维持否决：z_pct 2.9 仍在 2.5 之上，无论方向，最终裁决必须降级并带风险提示。",
    null, 0.78, ["z_pct = 2.9（阈值 2.5）"], 700)
  say("pm", 2, "收到。风控的否决我不推翻。",
    0.15, 0.60, [], 500)

  ev.push({
    type: "agent_weights",
    scenario: "trend_up",
    w: {
      bull: 0.17, bear: 0.15, fundamental: 0.11, technical: 0.16,
      sentiment: 0.07, risk: 0.14, pm: 0.10, kg_tutor: 0.05, psych: 0.05,
    },
    basis: {
      scenario: "由 5/15/30 的 regime（trend_up / trend_up / range）+ 年化波动率 21.4% 判定为「上行趋势」",
      history: {
        bull: { hit: 0.54, n: 41, prior: 0.5 },
        bear: { hit: 0.51, n: 39, prior: 0.5 },
        technical: { hit: 0.55, n: 37, prior: 0.5 },
        note: "命中率来自预测台账（agent/ledger.py）里这一角色的历史记录；样本 < 30 的角色退回先验 0.5",
      },
      self_conf: "本轮自报置信度做了**校准收缩**：w ∝ 0.5×场景 + 0.3×历史校准 + 0.2×收缩后的自报置信度（收缩系数 0.5）",
      risk_boost: "风控官命中数值条件，权重额外 +0.04（它的票在这一轮更值钱）",
    },
  })
  ev.push({
    type: "agent_verdict",
    side: "flat",
    score: 0.24,
    confidence: 0.44,
    reasons: [
      "多空在「上沿过不过」上是真分歧，第二轮双方都修正了立场但方向未变（+0.48 / −0.22）",
      "基本面一侧无反向证据，技术面 5/15 同向、30 分钟未表态",
      "风控官命中数值条件（z_pct 2.9 > 2.5），触发降级条款",
    ],
    dissent: [
      "多头：三次触及上沿的成交量逐次放大（1.1 / 1.3 / 2.1 倍），这次失败的成本高于前两次",
      "空头：向上 0.5 个 ATR、向下 1.9 个 ATR，赔率不值得做多",
      "风控官：无论方向，本轮必须降级并带风险提示",
    ],
    veto: {
      by: "risk",
      rule: "z_pct > 2.5（当日涨幅对 20 日波动率归一）",
      reason: "实测 z_pct = 2.9，超过阈值 ⇒ 裁决由 long 降级为 flat，并强制附加风险提示",
      downgrade_to: "flat",
    },
    risk_note: RISK_NOTE_TEXT,
  })
  ev.push({ type: "done", ms: 21400, steps: 24 })
  return ev
}

const RISK_NOTE_TEXT =
  "以上内容由程序自动生成，仅供学习参考，不构成投资建议。这一轮里风控官命中了一条数值否决条件，所以最终裁决被降级为「不表态」。"

/** 演示时页面上那句提问。**由页面传进来**，不在这里写死 —— 用户真的打了字，
 *  回放里那行问题必须是他打的那句，否则演示看起来像"忽略了我的输入"。 */
export const DEMO_QUESTION = "沪深300 现在这个位置能不能进？"
