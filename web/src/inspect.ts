/**
 * 「建议体检」页（`/inspect`）的数据层。
 *
 * ★这一页的对象和 `/ledger` **正好相反**，两页的方法却**是同一套**：
 *   `/ledger` 检验的是**本站自己的**预测（命中率永远和无脑喊涨并排摆着），
 *   这一页检验的是**别人给你的**建议（红旗永远和"哪一条不算红旗、为什么"并排摆着）。
 *   两处都配对照基准、都明说"我判不了哪一部分"。
 *
 * ★★这一页的硬纪律：**后端说判不了，页面一个字都不许替它判。**
 *   响应里有四层，确定性完全不同（`agent/checkup.py` 抬头写得很清楚）：
 *     `flags`          —— 文本特征明确可判，**是罪证**；
 *     `exempt_notes`   —— 命中了字面但**被判无罪**（银行存款/国债），**必须显示**：
 *                        用户得看见"我们查了、这句不算"，而不是以为工具漏了；
 *                        只会报红的检查器等于一个恒真的告警，豁免才是"检验层"；
 *     `questions`      —— 收益率异常、底层不明。**不是罪证，是问题** —— 该问什么；
 *     `cannot_verify`  —— 要不要查牌照、工商穿透。**我手上没有那个事实**，明说判不了。
 *   四层的**措辞**在页面上一处都不许串（把"判不了"写成"没问题"是最坏的那种错）。
 *
 * ★`empty_reason` 是**后端给的一句人话**，不是空数组的信号灯。
 *   "没检出红旗"和"这段建议是干净的"在用户眼里长得一模一样，所以它必须上屏
 *   （页面里那块 `Empty` 的 `what` 直接吃这句话，不自己另写一句）。
 *   ★它**有三档措辞**（有红旗时是 `null`；「看见了但判成豁免」一档；
 *     「真的什么都没检出」一档），三档都不许在页面里重写。
 *
 * ★**最近一次结论存在本机**（`licai-inspect-last`），页面**同步**读它渲染 ——
 *   理由是探针不会打字，见下面「本机缓存」那一大段的注释。
 *
 * ★为什么数据层单独一个文件、不改 `api.ts` / `api2.ts`：理由同 `research.ts` ——
 *   那两个文件的消费方多，而这一页只有自己用这一条通道，新文件的碰撞面是零。
 */
import { post } from "./api"

/** 后端通道。★**不放进 `api2.ts` 的 `CHANNEL`**：那张表是"新五页共用"的，
 *  往里加一条会牵动那个文件的所有读者，而这里只有这一页在用。 */
export const INSPECT_CHANNEL = "/api/inspect"

/** 抽出来的一个收益率。★`raw` 是**原文那一段**（"月息 1 分"这样），
 *  `annual` 是折算后的年化 —— 两者都要显示：只显示年化的话，
 *  用户没法对账"它是从哪一句折出来的"；只显示原文的话，跨口径没法比大小。 */
export type InspectRate = {
  raw: string
  /** 原文那个数（"月息 1%" 就是 1）。 */
  pct: number
  /** 折算成年化（月息 1% ⇒ 12）。**跨口径比大小只能用它。** */
  annual: number
  /** `年化` / `月息` / `半年` / `日息` / `月息（几分利）` 这一族，后端给的原话。 */
  kind: string
}

/** 它承诺了什么。**只陈述、不判罪** —— 判断在 `flags` / `questions` 那两层做。 */
export type InspectPromise = {
  rates: InspectRate[]
  /** 所有收益率里最高的那个年化。**没有抽到就是 `null`**（不是 0）。 */
  annual_max: number | null
  /** 命中的"承诺词"（保本/稳赚/无风险…）。★它只是**词**，不是判定 —— 同一个词
   *  在"银行存款保本"里被判无罪，那件事在 `exempt_notes` 里说。 */
  words: string[]
}

/** 一条红旗（**罪证**）。`term` 必须是图谱里真有的词条 —— 后端拿图谱查过，
 *  查不到就不报（"不编"这件事是结构上成立的，不靠自觉）。 */
export type InspectFlag = {
  term: string
  /** 命中的**那一句原文**。判据和用户都靠它对账。 */
  sentence: string
  /** 这一句里命中的触发词，逐个摆出来 —— 只说"有风险"等于没说。 */
  matched: string[]
  why: string
  /** 出处（"N 类词条「保本保收益承诺」·辨析"）。**照抄不翻译**，判据可查。 */
  from: string
}

/** 一条"查过了、这句不算"。
 *
 *  ★两种 `kind` 都会出现在同一个数组里（后端就是这么发的）：
 *    · `exempt`      —— 命中了触发词，但同一句里有豁免词（银行存款/国债）⇒ **不报红旗**；
 *    · `rule_orphan` —— 规则指的**词条不在图谱里**，这条规则和词表脱节了。
 *      这是**后端自己的一致性告警**，页面照样得显示（不显示的话它只在日志里）。
 *   ⇒ 页面按 `kind` 分两段渲染，不许把两者混成一句"豁免"。 */
export type InspectNote = {
  kind: "exempt" | "rule_orphan"
  term: string
  /** `exempt` 才有：命中的那一句原文。 */
  sentence?: string
  /** `exempt` 才有：救下这一句的那个豁免词（"定期存款"）。 */
  exempt?: string
  why: string
}

/** 该问的问题。**不是罪证** —— 这一层的措辞是"你该问它什么"。 */
export type InspectQuestion = {
  ask: string
  because: string
  /** 关联词条。★可能是 `null`（后端在图谱里查不到就置空）—— 那时只是不链过去，
   *  **问题本身照问**。页面别因为 `null` 就不渲染这一条。 */
  term?: string | null
}

/** 判不了的一个。★`why` 说的是"**我缺哪个事实**"，不是"暂不支持" —— 照抄。
 *  这一层是**设计**不是欠账：一个声称能判断"这是不是非法集资"的工具必须能查牌照，
 *  而本站没有那个数据源，硬判就是编。 */
export type InspectCannot = {
  term: string
  why: string
  /** 出处。★**可选**：种子/更早的响应里可能没有这个键（实测过），
   *  页面缺了它照样渲染那一条（出处是加分项，不是这一条的成立条件）。 */
  from?: string
}

/** 对照基准：**本站自己**那份回测的实测数（Kronos 方向命中率 vs 无脑喊涨）。
 *  ★数字**不重算**，后端直接引用回测产物（唯一实现，一个数只能有一个出处）。
 *  页面只负责摆出来 —— 任何声称能预测涨跌的建议都该拿这条对照来看。 */
export type InspectBaseline = {
  model_hit_rate: number | null
  model_ci95: [number, number] | null
  always_up: number | null
  momentum: number | null
  n_origins: number | null
  measured_at: string | null
  why: string
}

/** "该看哪几个词"。走 `agent/suggest.py` 的现成通路（后端那只列表**永不空**），
 *  `terms` 是来源核对用的锚点：判据拿它查"这词在图谱里真有吗"。 */
export type InspectReadNext = {
  label: string
  ask: string
  terms?: string[]
  symbol?: string
}

/** 一份体检报告。**字段几乎全是可选的**：失败那一支只给 `ok:false` + `hint`，
 *  而成功的每一层都可能是空的 —— 页面必须按"缺了就是缺了"渲染，不许补默认值。 */
export type InspectResp = {
  ok: boolean
  /** `rule` —— 这一屏的结论是**规则算的**，不是模型说的。照 `/agents` 的风控官：
   *  模型不能给自己发免责声明。页面把这个来源标出来。 */
  source?: string
  promise?: InspectPromise | null
  flags?: InspectFlag[]
  exempt_notes?: InspectNote[]
  questions?: InspectQuestion[]
  cannot_verify?: InspectCannot[]
  /** **可能是 `null`** —— 只有文本里提到了预测/涨跌那类话题时后端才去读回测产物。 */
  baseline?: InspectBaseline | null
  read_next?: InspectReadNext[]
  /** 什么都没检出时后端给的一句话。**必须上屏**（见文件抬头那段）。 */
  empty_reason?: string | null
  /* ── 失败那一支 ── */
  hint?: string
  error?: string
  msg?: string
}

/** 一次体检。**永不抛** —— 失败也是返回值，这样页面才写得出"为什么没有数"。
 *  ★`post` 不看状态码（`api.ts` 顶上那段）：本站的接口在 `ok:false` 时发 400，
 *    而响应体照样是一份完整的 JSON，`hint` 就在里面。 */
export function getInspect(text: string): Promise<InspectResp> {
  return post<InspectResp>(INSPECT_CHANNEL, { text })
}

// ── ★★本机缓存：`/inspect` 的输入是**用户现场粘的**，所以"正例那一屏"不能靠探针打字 ──
//
//   问题：探针不会打字 ⇒ 它只能量到"还没贴东西"那一屏，而这一页要判的每一块
//   （承诺 / 红旗 / 豁免 / 空态那句话）都在**有结论那一屏**上。
//   做法（与 `licai-assess` 的答案、`licai-profile` 的画像同一个待遇）：
//   **最近一次结论存进本机**，页面加载时**同步**读它渲染。于是
//   "往这个键里写一份 JSON → 打开页面"就能量到完整的一屏，不需要打字、不需要后端。
//
//   ★★**必须是同步读**：异步回来的数，同步探针量到的是"正在体检…" ——
//     那不是判据变红，是判据**消失**（这两句话在报告里长得一模一样）。
//     所以读这一段的函数被写成**纯函数**（`getInspectSync`），由页面在
//     `useState` 的**初值**里直接调用 —— 不能放进 `useEffect`，SSR 和探针的
//     `flushSync` 那一帧都不跑 effect。
//
//   ★**存的是接口返回的原样 JSON**（不加工、不包壳）：判据往这个键里写的
//     就是一份响应，页面读的必须也是那个形状 —— 包一层 `{resp, text}` 的话，
//     判据写进去的种子会被读成"没有报告"。
//
//   ★**只住在用户自己的浏览器里，服务端一个字节都不存** ——
//     与 `licai-assess`（`ASSESS_KEY`）同一条边界：它是有身份的用户内容，
//     存到服务端就要回答"谁能看、谁能删、留多久"，而本站没有账户体系。
//     读写一律包 `try/catch`（照 `assess.ts` 顶上那段）：隐私模式/禁站点数据时
//     `localStorage` 会抛，抛了就当**没存过** —— 读不到只是"还没体检过"，
//     不是"这一页坏了"。

/** 最近一次结论的键。**判据往这里写种子**。 */
export const INSPECT_KEY = "licai-inspect-last"

/** 被体检的那段原文的键（**另存一份**，与上面那个分开）。
 *
 *  ★为什么要第二个键、而不是把它塞进上面那份 JSON 里：上面那个键的形状是
 *    **接口返回的原样 JSON**（判据要能照着写种子），塞进去就破了那个契约。
 *  ★为什么值得存：报告里每一块都挂在"**体检的是哪一段**"上（`#lookedAt`），
 *    恢复一屏结论却不记得它是针对哪段文字的，那一屏就没法核了。
 *  ★**它是可选键**：种子路径（判据只写 `INSPECT_KEY`）读不到它，
 *    页面必须照样渲染 —— 那一块会说"原文当时没记下来"。 */
export const INSPECT_TEXT_KEY = "licai-inspect-text"

/** 同步读最近一次结论。**探针路径与普通路径共用同一个渲染**，所以这个函数
 *  两条路都会走到：普通路径下它让老用户刷新后仍然看得见上一次的结论
 *  （否则一刷新就白屏，而那段报告是用户刚花时间换来的）。
 *
 *  ★形状不对就返回 `null`（当没存过）：`JSON.parse` 出来的东西可能是
 *    几周前某个旧版本写的、也可能被人手改过，直接当 `InspectResp` 用
 *    会在渲染期抛（白屏）。判据看的是"这一屏画出来了没有"，
 *    为一份坏缓存白屏是最坏的形状 —— 宁可当它没存过。
 *  ★**只认 `ok === true` 的**：一份 `{ok:false,hint:'没给内容'}` 存下来没有任何意义。 */
export function getInspectSync(): InspectResp | null {
  try {
    const raw = localStorage.getItem(INSPECT_KEY)
    if (!raw) return null
    const j = JSON.parse(raw) as InspectResp
    if (!j || typeof j !== "object" || j.ok !== true) return null
    return j
  } catch {
    return null
  }
}

/** 同步读那段被体检的原文。读不到返回 `null`（**不是空串** —— "没记下来"
 *  和"记下来是空的"是两件事，页面那两句措辞不一样）。 */
export function getInspectTextSync(): string | null {
  try {
    const t = localStorage.getItem(INSPECT_TEXT_KEY)
    return t && t.trim() ? t : null
  } catch {
    return null
  }
}

/** 记住这一轮的结论和原文。**永不抛** —— 存不下（配额满/隐私模式）只是
 *  "下次打开看不到上一次"，不是这一页坏了；页面已经拿到结果了，照常渲染。 */
export function rememberInspect(r: InspectResp, text: string): void {
  try {
    if (r.ok) localStorage.setItem(INSPECT_KEY, JSON.stringify(r))
  } catch { /* 存不下就算了，见上 */ }
  try {
    localStorage.setItem(INSPECT_TEXT_KEY, text)
  } catch { /* 同上 */ }
}

/** 忘掉本机的结论与原文（页面上那个「忘掉上一次」的按钮）。
 *  ★为什么由数据层出面而不是页面里直接 `localStorage.removeItem`：
 *    读那一侧（`getInspectSync`）把 `try/catch` 和"抛了就当没存过"的纪律收在这里，
 *    写这一侧也必须同一条边界 —— 页面里再抄一遍的话，两处迟早分叉
 *    （一处 `try` 一处没有，隐私模式下就是白屏）。
 *  ★**永不抛**，理由同 `rememberInspect`：清不掉只是"下次打开还看得见上一次"。 */
export function forgetInspect(): void {
  try {
    localStorage.removeItem(INSPECT_KEY)
  } catch { /* 见上 */ }
  try {
    localStorage.removeItem(INSPECT_TEXT_KEY)
  } catch { /* 同上 */ }
}

/** 一份 `ok:false` 的报告 → 给用户看的那一句话。
 *  ★**原因优先用后端给的那句**：前端自己造一句，就会在后端换了措辞之后继续说着旧的。
 *  ★顺序是 `hint` → `msg` → `error` —— `/api/inspect` 失败时的契约就是 `hint`
 *    （"没给内容"），后面两个是网络/路由那两条路用的。 */
export function inspectErrText(r: InspectResp | null): string {
  if (!r) return "还没有取到体检结果"
  return r.hint || r.msg || r.error || "服务端说这一次体检做不了"
}
