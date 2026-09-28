/**
 * **第二批接口**的客户端：多智能体博弈 / 分钟级策略 / 关联检测 / 常驻采集器。
 *
 * ★为什么另起一个文件而不是写进 `api.ts`：那边四条（K线、预测、状态、密钥）
 *   是**已经在跑的**契约，这边四条是**后端还不存在**的契约（施工中）。
 *   两批东西的寿命不一样 —— 混进一个文件之后，"这个函数失败时页面该显示什么"
 *   就有了两种答案，而读者得把整个文件读完才知道自己看的是哪一批。
 *
 * ★这一层的中心不是"取数"，是**把失败分成能说人话的几类**。
 *   本项目的铁律是「取不到数就说取不到，绝不编数字」，所以前端这一侧必须能
 *   分辨"后端还没上线"和"后端上线了但报错"和"取到了但是空的" ——
 *   三者在页面上是完全不同的三句话，混成一句"加载失败"就等于把前两种
 *   伪装成了第三种，而用户会以为"这个功能做坏了"。
 *
 * ★**不许在这里写任何默认值**。取不到就是 `ok:false`，一个字段都不许补 0、
 *   补 `[]`、补 `null` 再假装成功 —— 那正是 `dash.ts` 顶部那段说的
 *   "看起来像真数据的假值比报错坏"。
 */

// ── 失败分类 ───────────────────────────────────────────────────────────

/** 五种失败，各对应页面上**一句不同的人话**。
 *
 *  ★`offline` 和 `notfound` 必须分开：本项目的后端是**分批上线**的，
 *    "服务没起"（用户自己该去跑站）和"服务起了但没有这条路由"（施工还没轮到）
 *    对用户是两种不同的下一步。
 *  ★`empty` 不是失败 —— 它由各页面自己判（`ok:true` 但数组是空的），
 *    写在类型里会被当成失败处理，于是"今天没有异动"会被显示成"接口坏了"。 */
export type FailKind =
  /** 连不上：服务没起、端口不通、DNS 挂了、被代理拦了 */
  | 'offline'
  /** 404：服务起着，但没有这条路由 —— **施工还没轮到**，不是用户的问题 */
  | 'notfound'
  /** 5xx 或者 `ok:false`：后端在，但它说自己做不了这件事 */
  | 'server'
  /** 200 但响应体不是 JSON（多半是 Flask 的错误页 / 代理的 HTML 页） */
  | 'badjson'
  /** 请求本身不合法（我们发错了字段名之类） */
  | 'badreq'
  /** 超时。★和 `offline` 分开：超时是"它在，但没在预算内答完" */
  | 'timeout'

export type Fail = {
  ok: false
  kind: FailKind
  /** 给用户看的一句话。**服务端给了原因就用服务端的那个**，别自己造。 */
  reason: string
  /** 给开发者看的补充（端点的响应片段、异常名）。页面折叠着显示。 */
  detail?: string
  status?: number
}

export type Res<T> = { ok: true; data: T } | Fail

/** 超时预算（**默认值**，不是上限）。★每个外部调用都要有超时（铁律 6）——
 *  "挂住"比"报错"糟糕得多。
 *  SSE 那条不走这个数：它是长连接，用 idle 心跳判断（见 `streamAgent`）。
 *
 *  ★一个端点**设计上就该更慢**时，由它自己带预算（见 `LINKAGE_BUDGET_MS`）——
 *    共用这一个数会让"这条本来就慢"被误报成"服务端答不完"。
 *    实测的现场：`/linkage` 冷启动 26s–53s，而缓存只保温 60s（`datasource.TTL` 里
 *    `realtime: 60`）⇒ 正常浏览几乎每次都是冷的，20s 必然掐断它。 */
const TIMEOUT_MS = 20000

function isAbort(e: unknown): boolean {
  return !!e && typeof e === 'object' && (e as { name?: string }).name === 'AbortError'
}

export function failText(f: Fail): string {
  return {
    offline: '连不上服务',
    notfound: '服务端没有这条路由（这个后端还没接上）',
    server: '服务端说它做不了这件事',
    badjson: '服务端返回的不是 JSON',
    badreq: '请求发错了（字段名或取值不对）',
    timeout: '服务端没在预算内答完',
  }[f.kind]
}

/** 端点路径 → 人话名字。**一张表说了算** —— 页面里各写一遍的话，
 *  改路由时必然漏掉某一页，而漏掉的那页显示的是一条旧路径（看着像真地址）。 */
export const CHANNEL = {
  agents: '/api/agents',
  intraday: '/api/intraday',
  linkage: '/api/linkage',
  linkageAnomaly: '/api/linkage/anomaly',
  collectStatus: '/api/collect/status',
  collectRing: '/api/collect/ring',
  collectOnce: '/api/collect/once',
} as const

/** 一次 JSON 往返。**永不抛**，失败也是一种返回值（`Res`）。
 *
 *  ★为什么不抛：抛出去的异常到了 `catch` 里只剩一个字符串，而
 *    "404 还是超时"这件事**决定了页面该说哪句话**。本项目已经有过一次
 *    这类事故的等价物（`api.ts: post()` 上面那段注释：抛出去之后
 *    `msg`/`hint` 就没了，页面上只剩"出错了"三个字）。 */
export async function call<T>(url: string, body?: unknown, budgetMs = TIMEOUT_MS): Promise<Res<T>> {
  const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null
  const timer = ctl ? setTimeout(() => ctl.abort(), budgetMs) : null
  let r: Response
  try {
    r = await fetch(url, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl ? ctl.signal : undefined,
    })
  } catch (e) {
    if (timer) clearTimeout(timer)
    // ★`AbortError` 是我们自己掐的 ⇒ 超时；别的都算连不上。
    return isAbort(e) ? { ok: false, kind: 'timeout', reason: failText({ ok: false, kind: 'timeout', reason: '' }) }
      : {
        ok: false, kind: 'offline',
        reason: '连不上本地服务（后端这个通道还没起）',
        detail: String((e as Error)?.message || e),
      }
  }
  if (timer) clearTimeout(timer)

  if (r.status === 404) {
    return { ok: false, kind: 'notfound', status: 404, reason: `${url} 这条路由服务端还没有` }
  }
  if (r.status === 400 || r.status === 422) {
    // 本项目的接口在 `ok:false` 时发 400，响应体**照样是一份完整 JSON**
    // （`api.ts: post()` 顶上那段）。所以 400 要先读体，读不出 JSON 才算 badreq。
    const j = await readJson<T>(r)
    if (j.ok && j.data && (j.data as { ok?: boolean }).ok === false) return fromBody(j.data, r.status)
    return { ok: false, kind: 'badreq', status: r.status, reason: '请求被服务端拒了' }
  }
  if (r.status >= 500) {
    const text = await safeText(r)
    return {
      ok: false, kind: 'server', status: r.status,
      reason: `服务端 ${r.status}`, detail: text.slice(0, 300),
    }
  }

  const j = await readJson<T>(r)
  if (!j.ok) return j
  return fromBody(j.data, r.status)
}

/** `ok:false` 的响应体 → `Fail`。**原因优先用服务端给的那句**。 */
function fromBody<T>(data: T, status: number): Res<T> {
  const d = data as { ok?: boolean; msg?: string; error?: string; hint?: string }
  if (d && d.ok === false) {
    return {
      ok: false, kind: 'server', status,
      reason: d.msg || d.error || '服务端说这条取不到',
      detail: d.hint,
    }
  }
  return { ok: true, data }
}

async function readJson<T>(r: Response): Promise<Res<T>> {
  const text = await safeText(r)
  try {
    return { ok: true, data: JSON.parse(text) as T }
  } catch {
    return {
      ok: false, kind: 'badjson', status: r.status,
      reason: '服务端返回的不是 JSON（多半是错误页）',
      detail: text.slice(0, 200),
    }
  }
}

async function safeText(r: Response): Promise<string> {
  try {
    return await r.text()
  } catch {
    return ''
  }
}

// ── §3.4 多智能体博弈 ─────────────────────────────────────────────────

/** 九个角色。**顺序就是规格里的 `ROLES`**，别改。
 *  ★这里是**兜底**：`agent_start` 事件里带着 `name`/`icon`，服务端给了就用服务端的。 */
export const ROLES = [
  'bull', 'bear', 'fundamental', 'technical', 'sentiment',
  'risk', 'pm', 'kg_tutor', 'psych',
] as const
export type Role = (typeof ROLES)[number]

export type Side = 'long' | 'short' | 'flat'

export type AgentEv =
  | { type: 'agent_round'; round: number; roles: string[] }
  | { type: 'agent_start'; role: string; name?: string; icon?: string }
  | {
    type: 'agent_say'; role: string; round: number; text: string
    /** −1 全空 ~ +1 全多。**是服务端给的数，前端不许自己从 text 里猜**。 */
    stance: number | null
    conf: number | null
    evidence?: string[]
    ms?: number
  }
  | { type: 'agent_done'; role: string; ok: boolean; ms?: number }
  | { type: 'agent_weights'; w: Record<string, number>; scenario: string; basis: Record<string, unknown> }
  | {
    type: 'agent_verdict'; side: Side; score: number; confidence: number
    reasons?: string[]
    /** 反对方写了什么。**这是这一页最该看见的一栏** —— 一致同意和吵成一团
     *  在"方向/分数"上可能长得一样，只有分歧栏分得出来。 */
    dissent?: string[]
    veto?: { by?: string; rule?: string; reason?: string; downgrade_to?: string } | null
    risk_note?: string
  }
  | { type: 'done'; ms?: number; steps?: number }
  | { type: 'error'; msg?: string; code?: string; hint?: string }
  /** 心跳。规格里没有，但一条几十秒的长连接**必须有**心跳，
   *  否则"还在跑"和"服务死了"在页面上一样（都是没动静）。
   *  收到未知 type 时**不当作错误**，只记一笔（见 `streamAgent`）。 */
  | { type: 'heartbeat'; elapsed?: number }
  | { type: string;[k: string]: unknown }

/** 跑一轮博弈（SSE）。
 *
 *  ★形态照抄 `components/ChatCard/ChatCard.tsx` 里那段（那是全站唯一一处
 *    已经跑通的 SSE 读法），三件事不许改：
 *    ① `TextDecoder` 必须带 `{stream:true}` —— 多字节 CJK 跨 chunk 全靠它；
 *    ② 按 `\n\n` 切帧、最后一块留在缓冲里（不切干净会把半帧当整帧解）；
 *    ③ 流结束后**还要解一次残留缓冲**，否则最后一帧（往往正是 `done`）会丢。
 *
 *  ★和对话那条路的区别只有一处：那边一帧解不出就静默丢弃（`parseFrame` 返回
 *    null），这边**必须把解不出的帧记下来** —— 这一页的事件类型是新契约，
 *    "服务端发了个我不认识的 type"正是最该被看见的事（静默丢弃的话，
 *    新加的事件类型永远不会有任何反馈）。 */
export async function streamAgent(
  body: { question: string; history?: string[]; profile?: unknown },
  onEvent: (ev: AgentEv) => void,
  signal?: AbortSignal,
): Promise<Res<{ ms: number; steps: number | null; unknown: number }>> {
  let r: Response
  try {
    r = await fetch(CHANNEL.agents, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
  } catch (e) {
    return {
      ok: false, kind: 'offline',
      reason: '连不上本地服务（多智能体这条通道还没接上）',
      detail: String((e as Error)?.message || e),
    }
  }
  if (r.status === 404) return { ok: false, kind: 'notfound', status: 404, reason: `${CHANNEL.agents} 这条路由服务端还没有` }
  if (r.status >= 400 || !r.body) {
    const t = await safeText(r)
    return { ok: false, kind: r.status >= 500 ? 'server' : 'badreq', status: r.status, reason: `服务端 ${r.status}`, detail: t.slice(0, 300) }
  }

  const rd = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  let unknown = 0
  let ms = 0
  let steps: number | null = null
  const feed = (frame: string) => {
    const ln = frame.trim()
    if (ln.indexOf('data:') !== 0) return
    let ev: AgentEv
    try {
      ev = JSON.parse(ln.slice(5).trim()) as AgentEv
    } catch {
      unknown++
      return
    }
    if (ev.type === 'done') {
      ms = typeof ev.ms === 'number' ? ev.ms : 0
      steps = typeof ev.steps === 'number' ? ev.steps : null
    }
    if (!KNOWN_EV.has(ev.type)) unknown++
    onEvent(ev)
  }
  try {
    for (;;) {
      const c = await rd.read()
      if (c.done) break
      buf += dec.decode(c.value, { stream: true })
      const parts = buf.split('\n\n')
      buf = parts.pop() || ''
      for (const p of parts) feed(p)
    }
    if (buf.trim()) feed(buf)
  } catch (e) {
    // 用户主动掐断是正常路径（`AbortError`），不是失败
    if (isAbort(e)) return { ok: true, data: { ms, steps, unknown } }
    return { ok: false, kind: 'timeout', reason: '这条流中途断了', detail: String((e as Error)?.message || e) }
  }
  return { ok: true, data: { ms, steps, unknown } }
}

const KNOWN_EV = new Set([
  'agent_round', 'agent_start', 'agent_say', 'agent_done',
  'agent_weights', 'agent_verdict', 'done', 'error', 'heartbeat',
])

// ── §3.2 分钟级策略 ───────────────────────────────────────────────────

export type Regime = 'trend_up' | 'trend_down' | 'range' | 'squeeze'

export type IntradaySignal = {
  id: string
  name: string
  /** ★**两种"不属于任何一档"都有**，都要放行：
   *  · `0` —— 冻结的 §3.2 里"综合"那一档（演示数据用的就是这个）；
   *  · `null` —— 服务端现在给跨周期信号（`resonance`）的写法，**一轮只有一条**。
   *  ★按周期分组/排序时**两个都不能落进 5/15/30 任何一格里**，也不能把 `null`
   *    写成 `0` —— 那会把一条"跨周期"的信号伪装成一条"综合"信号。
   *    页面上两者的标签也不一样（「跨周期」/「综合」）。 */
  period: number | null
  side: Side
  /** 0–1。**服务端给多少就显示多少**，不做二次归一 —— 二次归一之后
   *  "强度"就不再有可比性，而它看着还像个强度。 */
  strength: number
  /** 可复核的算术（带具体价位/倍数）。**空的 why 是要显示出来的**
   *  —— 规格 §3.2 明写"每个 why 必须是可复核的算术"，空的说明后端没做到，
   *  页面有责任把这件事露出来，而不是给个空行。 */
  why: string
  refs?: Record<string, number | string | null>
}

export type IntradayResp = {
  ok: boolean
  market: string
  code: string
  name?: string | null
  ts: string
  /** `{'5':240,'15':96,'30':48}` —— **每周期实际拿到多少根**。
   *  ★键是字符串：JSON 的键只能是字符串，写 `bars[5]` 取不到（那是 `bars['5']`）。 */
  bars: Record<string, number>
  /** 服务端多给的几项。**放行**（不是白名单校验，多出来的键不该把整条 payload 判成非法）；
   *  这里写进类型只是因为页面要读它们。 */
  periods?: number[]
  bars_per_day?: Record<string, number> | null
  /** ★★**口径**（服务端给，前端只读）。
   *  `'daily'` = 服务端把这一页**降级成了日线**：该市场在本机没有分钟线源
   *  （港美股/加密），于是 `periods` 是 `[1440]`、只有五条"只用 K 线序列与量能"
   *  的策略成立（`agent/daily_short.py`）。缺键 = 真分钟线口径。
   *  ★**页面必须把这件事说出来**（`Intraday.tsx` 那条横幅）：不说的话，用户
   *    看到的是三个 5/15/30 的空格子，而他会以为"接口坏了"，不会想到是口径。 */
  bar_scale?: "daily" | null
  /** 日线口径下"最后拿到的那根 K"的状态（服务端给，前端只读）。
   *  ★`closed === false` = **这一根还在变**（盘中）：它的量、收盘价、最高最低
   *    都还没定稿，基于它算的 RVOL/形态/止损位都是"半根 K"的读数。 */
  last_bar?: { dt?: string; date?: string; closed?: boolean | null; why?: string; bars?: number } | null
  /** 拿到的那份价**复权了没有**（`'qfq'` / `'none'` / …）。港美股源不给复权
   *  ⇒ `'none'` —— 除权跳空会原样进 ATR，而它看起来和真波动没有区别。 */
  price_adjust?: { mode?: string | null; note?: string } | null
  /** 上游数据源的**真名**（`tencent.kline.day` 这种）。
   *  ★和 `data_source` **不是一回事**：那个是"真行情/合成"的开关（决定要不要挂
   *    合成横幅），这个是"这份数是从哪儿来的"。混用会把真行情显示成合成。 */
  upstream_source?: string | null
  /** ★★**这一条是"这屏数是不是真的"的开关**，必须显示给用户看。
   *
   *  · `synthetic === true`（或 `data_source` 以 `SYNTH` 开头）= 分钟引擎**没拿到
   *    真分钟线**，退回了一份**合成帧**把流程跑完 —— 那时候每个价位、每根成交量
   *    都是造出来的。页面上必须挂一条显眼的横幅说明这件事（`Intraday.tsx` 里
   *    那个 `synthBanner`）。不显示的话，用户看到的就是"编出来的行情"配上一堆
   *    具体数字，而它和真的长得一模一样 —— 这比页面报错坏得多。
   *  · `'datasource'` = 真行情。
   *  ★**两个键都读**（`synthetic` 为真 **或** `data_source` 看着像合成）：服务端
   *    若只给其中一个，另一个就是没说的那一半，不能因为"另一个键不在"而放行。 */
  data_source?: string
  synthetic?: boolean
  regime: Record<string, Regime>
  signals: IntradaySignal[]
  composite: {
    score: number
    side: Side
    confidence: number
    agree: { long: number; short: number; flat: number }
    /** 服务端自己数出来的、参与共振的信号条数。**和下面那个前端派生的
     *  「几条信号参与了」不是一回事**（那个是按档数的），所以两个都显示、各自标名。 */
    n_signals?: number
  }
  volume: {
    rvol: number | null
    /** ★`null` 是**合法值**（当天 bar 不够），不是 0、也不是缺键。 */
    rvol_by_period?: Record<string, number | null>
    /** ★量比的基准。**两个基准都在，别用错那个**：
     *  · `slot_mean` = **同一时刻**的历史均量（对的那个）；
     *  · `naive_mean` = 不分时段的全天均量（**错**做法，留着当对照）。
     *  ★标错了就等于把"日内量是 U 型"这个坑重新踩一遍：早盘和尾盘的量本来就大，
     *    拿全天均量当分母，每天开盘都"放量"。 */
    rvol_base?: { slot_mean: number | null; naive_mean: number | null; note?: string } | null
    profile: { poc: number | null; va_hi: number | null; va_lo: number | null } | null
    divergence: 'bearish' | 'bullish' | 'none' | null
    /** ★**日线口径独有**：末根 K **还没收盘**时，服务端额外给一个"剔掉那根再算"
     *  的量比。它唯一的用处就是让人看出上面那个 `rvol` 里有多少是"**这天还没过完**"
     *  造成的 —— 实测美股盘中：`rvol` 0.31、剔掉后 0.61，一半的"缩量"是假的。
     *  缺键 = 口径不是日线，或那根已经收盘（正常路径的分钟 K 一收盘就定稿）。 */
    rvol_ex_last_bar?: number | null
  }
  levels: {
    entry: number | null; stop: number | null
    target1: number | null; target2: number | null
    atr: number | null; rr: number | null
    method: string
    /** ★**兜底方向，不是判断**：`composite` 落在死区（说不出方向）时它照样会给一个，
     *  而且平票取 `long`。页面上的"方向"一律用 `composite.side`；
     *  这几个键只作为"兜底说了什么"显示在位置那一段里，并写明它是兜底。 */
    side?: Side | null
    composite_side?: Side | null
    long?: number | null
    short?: number | null
  } | null
  /** ★`phase` **原样照抄**：我们只翻译确定知道的那一个取值（`'all'` = 全时段，
   *  加密 24 小时恒为它，**这是正确值不是"未知"**）。别的取值猜成中文标签
   *  就等于替服务端改口径 —— 猜错的那个标签看起来和真的一样。 */
  session: { phase: string; window: string; note: string } | null
  errors: string[]
  /** ★★**规格 §3.2 没有冻结下面这两个字段** —— §3.2 只冻结了**分析结果**
   *  （`signals` / `levels` / `composite` / `volume` / `session`），
   *  而这一页的图上要画的是**分钟 K 线本身**。
   *  ⇒ 前端在这里**预留两个位置**（这是"期望后端返回的字段"清单里的一条，
   *    交付说明里逐条列了）：
   *      · 服务端给了 → 画蜡烛 + 成交量 + VWAP + 价值区（四层共用一根横轴）；
   *      · 服务端没给 → **只画分析层**，并在图上写明「这一轮的返回里没有分钟K线」。
   *    ★**绝不自己重采样出 K 线**：用日线插值、或者把 5 分钟线拼成 15 分钟，
   *      都是在编一根不存在的蜡烛，而它画出来和真的长得一样。 */
  klines?: { t: string; o: number; h: number; l: number; c: number; v?: number }[] | null
  /** 逐根 VWAP，与 `klines` **按下标对齐**。长度不等就整层不画（同 `klines` 的说明）。 */
  vwap_series?: (number | null)[] | null
  msg?: string
  hint?: string
}

export const PERIODS = [5, 15, 30] as const
export type Period = (typeof PERIODS)[number]

export const getIntraday = (market: string, code: string, periods: readonly number[] = PERIODS) =>
  call<IntradayResp>(CHANNEL.intraday, { market, code, periods: periods.slice() })

// ── §3.3 关联检测 ─────────────────────────────────────────────────────

/** 采集健康度的一行。**这一页的主角**（用户明确说"重采集弱分析"）。
 *  `n`/`ms` 是 `null` 时**显示"—"而不是 0** —— 0 行和"没采到"不是一回事。 */
export type SourceHealth = {
  id: string
  ok: boolean
  n: number | null
  ms: number | null
  err: string | null
}

export type Anomaly = {
  code: string
  name: string
  market: string
  /** 可多标签，如 `['volume_spike','pct_spike']`。**原样显示** ——
   *  前端自己翻译一遍的话，后端加一个新标签时页面上会静默少一个。 */
  kinds: string[]
  metrics: {
    pct: number | null
    vol_ratio: number | null
    amount_yi: number | null
    turnover: number | null
    z_pct: number | null
    z_vol: number | null
  }
  ts: string
}

export type LinkBoard = {
  code: string
  name: string
  kind: string
  pct: number | null
  lead_code?: string | null
  lead_name?: string | null
}

export type LinkPeer = {
  code: string
  name: string
  /** `'同板块' | '高相关' | '领先'` —— **字符串原样显示**，别自己分类。 */
  rel: string
  /** 相关性的值时有时无：**样本不足（<60）服务端就不给这个数**（规格 §3.3 纪律）。
   *  所以它是 `null` 时**不许显示成 0**，也不许拿别的数顶上。 */
  rel_value: number | null
  lag_min: number | null
  pct: number | null
  vol_ratio: number | null
  ts?: string
}

export type LinkGroup = {
  boards: LinkBoard[]
  peers: LinkPeer[]
  /** 证据串（"同属 白酒/消费50 两个概念板块"）。**空的证据 = 这条关联不该显示**。 */
  evidence: string[]
}

export type LinkageResp = {
  ok: boolean
  ts: string
  snapshot: { ts: string; market: string; rows: number }
  collected: { sources: SourceHealth[]; ts: string; rows: number; markets: string[] }
  anomalies: Anomaly[]
  links: Record<string, LinkGroup>
  note: string
  msg?: string
  hint?: string
}

/** `/linkage` 自己的预算。★它**设计上就该慢** —— 这一趟要把
 *  `cn/hk/us` 三个市场的实时+快照都采一遍（`linkage.scan`，模块头一句就是
 *  「采集是主角」）。实测（同机、同网，`/api/linkage` scope=all top=20）：
 *    · 冷启动 **53.49s**（新进程、缓存全冷）／ 另一次 **25.93s** —— ★两次差 2×，
 *      是机器负载（同时有别的会话在跑），不是"口径不同的两次读数"；两次都远超 20s
 *    · 缓存命中 **0.32s / 0.51s**
 *  而缓存只保温 60s（`datasource.TTL['realtime']=60`）⇒ 用户正常浏览的间隔
 *  远超 60s，**几乎每次都是冷的**。20s 的共用预算下这一页必然每次都报
 *  "服务端没在预算内答完"——用户看到的就是"这页用不了"。
 *
 *  ★★**服务端在这个端点上没有整段墙钟** —— 这一条我先前判错过，改对过来：
 *  我原以为"预算是 60s"，实际 `linkage.DEFAULT_BUDGET = 60` 的原文注释是
 *  「**深挖调用次数上限**」（`agent/linkage/__init__.py:42`），而
 *  `DEFAULT_TIMEOUT = 25.0` 是「**单次**调用挂钟上限」（`:44`）。
 *  否证的读数：一次冷启动 `/api/linkage` scope=all top=20 跑了 **77.96s**
 *  且 `ok=True` —— 若真有 60s 整段墙钟，它该在 60s 处放弃、给我 `ok:false`
 *  而不是跑满 78s。所以最坏情形是 60 次调用 × 25s，**没有任何服务端闸门**。
 *  三个独立冷进程的实测：**25.93s / 53.49s / 77.96s** ⇒ 跨度 ≥3×，
 *  完全是机器负载的函数（不是"口径不同的读数"）。
 *
 *  ⇒ 预算只能由前端自己定，且必须**明显盖过观测到的最坏值**。取 180s ≈ 观测最坏值的
 *    2.3×。这不是"算出来的下界"，是"够用的猜测"——**真正该修的在服务端**：
 *    给它加一道整段墙钟，让它在预算耗尽时返回**部分结果** + 那句现成的 note
 *    （`lg.skip()` 那套「有 N 次深挖没做」的脚手架已经在了，`__init__.py:168`），
 *    这样前端才有一个**由服务端给出的、有上下文的**收束点。
 *  ★代价说清楚：用户最长可能盯着加载态等到上面那个上限。这是"宁可慢、不要假失败"
 *    的取舍，不是免费的；真正省时间要在服务端把冷启动做短。 */
const LINKAGE_BUDGET_MS = 180000

export const getLinkage = (scope = 'all', codes?: string[], top = 20) =>
  call<LinkageResp>(CHANNEL.linkage, { scope, ...(codes?.length ? { codes } : {}), top }, LINKAGE_BUDGET_MS)

/** 单只票的关联。★与 `getLinkage` 共用同一个预算：`linkage.anomaly_of`
 *  自己建的那个 `Ledger`（`agent/linkage/__init__.py:232`）用的是**同一个**
 *  `DEFAULT_BUDGET`/`DEFAULT_TIMEOUT` 组合 —— 也就是说这条路上**一样没有整段
 *  墙钟**（同上面那段更正：60 是调用次数上限、25s 是单次上限）。共用的 20s
 *  一样会把它掐成一句没有上下文的 timeout，所以共用同一个预算。
 *  ★它比 `/linkage` 轻（一只票、不是三个市场），但**不因此就快**：横截面那一步
 *  取的是**该市场全市场实时快照**（`lg.call('realtime:%s' % market, …)`），
 *  冷启动一样要等源。实测冷 **11.37s** / 温 6.85s —— 而本机冷启动读数在
 *  `/linkage` 上量到过 ≥3× 的负载漂移，11.37×2 就已经越过 20s。
 *  所以"轻"撑不起"20s 够用"这个结论。
 *  这条路是真用户路径：异动榜右边的「看关联」和下面的 `#linkPick` 下拉都走它
 *  （`pages/Linkage/Linkage.tsx:311/344`），页面上那句"已经走单查通道单独问了一次"
 *  说明作者本来就预期它会触发。 */
export const getLinkageOne = (code: string) =>
  call<{ ok: boolean; ts: string; code: string; link: LinkGroup; note?: string; msg?: string }>(
    CHANNEL.linkageAnomaly, { code }, LINKAGE_BUDGET_MS,
  )

/** §3.1 的 `health()`。采集健康度在**两个**地方出现（关联页的 `collected`、
 *  采集页的 `status`），形状逐字相同 —— 所以这个类型只写一次。 */
export type Health = { sources: SourceHealth[] }

// ── §3.5 常驻采集器 ───────────────────────────────────────────────────

/** 采集器健康度。★规格 §3.5 只说了 `status() -> dict`，**没有逐字段冻结**，
 *  所以这里写的是**前端期望的形状**，并逐项声明了"缺了会怎样"。
 *  每一项都按"可以没有"处理（`?`）：后端少给一个字段时页面显示"—"，
 *  不会崩，也不会补一个 0。交付说明里把这张期望表逐条列了出来。 */
export type CollectorStatus = {
  ok: boolean
  ts?: string
  /** 采集器是不是在跑。**`undefined` 和 `false` 不一样**：前者是"没说"，
   *  后者是"说了没跑"。页面上两句不同的话。 */
  running?: boolean
  /** 这一轮的开始/结束时刻 */
  last_ts?: string | null
  next_ts?: string | null
  /** 落盘根目录（只追加的事件日志在它下面）。同 `/ledger` 那条纪律：
   *  用户有资格知道这个站往磁盘上写了什么、写在哪儿。 */
  path?: string | null
  markets?: string[]
  rows?: number | null
  /** 累计采了多少轮 */
  ticks?: number | null
  health?: Health
  /** 最近一轮每个源的统计（有就是比 health 更细的一份） */
  sources?: SourceHealth[]
  last?: { ts?: string; market?: string; rows?: number | null; ms?: number | null } | null
  err?: string | null
  msg?: string
  hint?: string
}

/** `ring(limit)` 的一项 = **一次快照的索引**（不是快照本身）。 */
export type RingItem = {
  ts: string
  market?: string
  rows?: number | null
  ms?: number | null
  ok?: boolean
  err?: string | null
  sources?: SourceHealth[]
}

export const getCollectorStatus = () => call<CollectorStatus>(CHANNEL.collectStatus)
export const getCollectorRing = (limit = 120) =>
  call<{ ok: boolean; items?: RingItem[]; ring?: RingItem[]; msg?: string }>(
    CHANNEL.collectRing, { limit },
  )
/** 「采一轮」的预算。★同 `LINKAGE_BUDGET_MS` 那条理由，但这里是**证明出来的**，
 *  不是靠实测撞见的：采集器自己给一整轮定的墙钟预算是
 *  `collector/sources.py: DEFAULT_BUDGET_MS = 25000`（**25s**，单次外部调用
 *  另受 `DEFAULT_TIMEOUT = 8.0` 约束）。而前端原来共用 20s ⇒
 *  服务端**有权**跑到 25s，前端 20s 就掐断，用户看到"服务端没在预算内答完" ——
 *  ★而那一轮在服务端**是真采完了的**（`once()` 会落索引 + 各市场载荷），
 *  于是页面上"采一轮失败了"和环里多出来的那一轮互相矛盾，
 *  且用户会以为要重试（重试就又落一轮）。
 *  取 40s：宽于服务端那 25s，让**服务端**先给出它自己的答案
 *  （成功，或它自己那句"这一轮的墙钟预算用完了"）。 */
const COLLECT_ONCE_BUDGET_MS = 40000

export const collectorOnce = (scope = 'all') =>
  call<{ ok: boolean; ts?: string; rows?: number | null; ms?: number | null; sources?: SourceHealth[]; err?: string | null; msg?: string }>(
    CHANNEL.collectOnce, { scope }, COLLECT_ONCE_BUDGET_MS,
  )

// ── 小工具：数值读法（**不补零**）─────────────────────────────────────

/** 读一个"可能是数字、可能是 null、可能是 NaN"的字段。
 *  ★`Number(null)` 是 **0** —— 直接 `Number(x)` 会把"没采到"静默变成"采到 0"。
 *  这个函数存在的唯一理由就是堵住那一手。 */
export function numOrNull(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

/** 读一个"应该是数组、但可能整个缺了"的字段。缺了就是空数组 ——
 *  这是**唯一**允许补的默认值：遍历一个 `undefined` 会抛，而"没有这一栏"
 *  本来就等于"这一栏是空的"。 */
export function arr<T>(v: T[] | null | undefined): T[] {
  return Array.isArray(v) ? v : []
}
