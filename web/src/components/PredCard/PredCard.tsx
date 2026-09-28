/**
 * 独立预测卡。**直接打 /api/predict，不经过大模型**。
 *
 * ★#pout 的子节点顺序是判据的契约：`.reading` → `.chart`（内含 svg + .cap）→ `.disclaim`。
 *   探针分别读 `#pout .chart svg`（predChart）和 `#pout .disclaim`（predNote/predNoteH）。
 * ★和对话里那张图是**同一个** KChart —— 另画一套迟早两边不一样。
 * ★措辞别改松：说的是"这条采样路径长什么样"，**不是**"会涨到哪"。
 */
import * as React from "react"
import { KChart, type ChartData } from "../KChart/KChart"
import { Card } from "../Card/Card"
import { Badge } from "../Badge/Badge"
import { Callout } from "../Callout/Callout"
import { ProgressBar } from "../ProgressBar/ProgressBar"
import { getPredict, type PredictResp } from "../../api"
import { demoChart } from "../../chat"
import { pct, num, dirOf } from "../../format"

/** 探针模式的假返回：只需要够渲染出图和后缀那句话。
 *
 *  ★它必须带上 `spread` **且是弃答那一态**，否则探针模式下新区间块根本不渲染，
 *    而"新区间块存在"的判据会在一个从没画出来的东西上判绿（判据空转）。
 *    弃答那一态同时覆盖了两件事：区间数字画得出来 + 弃答文案画得出来。 */
function fakePredict(): PredictResp {
  const ch = demoChart()
  const n = ch.pred!.c.length
  return {
    summary: {
      ok: true, name: "沪深300", code: "000300", market: "cn", pred_len: 30,
      sample_count: 9, base_close: ch.base_close!, pred_last_close: 4500,
      pred_chg_pct: 0.9, pred_high: 4600, pred_low: 4400, pred_max_up_pct: 3.1,
      pred_max_dn_pct: -1.4, direction: "flat", direction_center: "up", days_above_base: 18,
      hist_ann_vol_pct: 22, pred_ann_vol_pct: 19, model: "Kronos-small 24.7M / 512 上下文",
      ms: 41200, cached: false, ohlc_repaired_bars: 15, ohlc_max_clamp_pct: 2.31,
      caveat: "",
      spread: {
        n_paths: 9, level_pct: 80, lo: 4420, hi: 4602, lo_pct: -1.8, hi_pct: 2.3,
        width_pct: 4.1, hist_move_pct: 5.6, up: 5, down: 4, flat: 0, called: 9,
        consensus: 0.556, vote_ci95: [26.7, 81.1], withheld: true,
        why: "9 条采样路径的投票比例 0.556 的 95% 区间（26.7–81.1%）跨过了 50% —— 这些路径没有系统偏向哪一边。",
      },
    },
    data: {
      kind: "predict", name: ch.name!, code: ch.code!, history: ch.history!,
      pred: ch.pred!, base_close: ch.base_close!, ohlc_repaired_bars: 15,
      ohlc_max_clamp_pct: 2.31,
      band: {
        lo: ch.pred!.c.map((v) => +(v * 1.02).toFixed(2)),
        hi: ch.pred!.c.map((v) => +(v * 1.05).toFixed(2)),
        level_pct: 80, withheld: true,
      },
      params: { pred_len: 30, sample_count: 9, lookback: 400, T: 1, top_p: 0.9, ms: 41200 },
    },
  }
}

export function PredCard({ probe = false }: { probe?: boolean }) {
  const [r, setR] = React.useState<PredictResp | null>(() => (probe ? fakePredict() : null))
  const [sym, setSym] = React.useState("")
  const [busy, setBusy] = React.useState(false)

  async function run() {
    const s = sym.trim()
    if (!s || busy) return
    setBusy(true)
    try {
      // 不传 sample_count：让**服务端**用它的默认值（前端写死一份就是把它抄成两份）
      setR(await getPredict(s, 30))
    } catch (e) {
      setR({
        summary: { ok: false, error: String((e as Error)?.message || e) } as PredictResp["summary"],
        data: null,
      })
    }
    setBusy(false)
  }

  const s = r?.summary
  const d = r?.data

  return (
    <section className="card pred" id="predCard" aria-label="K线预测">
      <div className="chd">
        <h2>K线预测</h2>
        <span className="tag">本地小模型 Kronos · 不经过大模型</span>
      </div>
      <div className="pbody">
        <div className="inrow">
          <input
            id="psym" type="text" autoComplete="off" spellCheck={false} value={sym}
            // ★这个框旁边只有 placeholder，没有 `<label>`。placeholder **不算标签**：
            //   一打字就没了，而且不少读屏软件压根不念它。
            aria-label="标的代码"
            onChange={(e) => setSym(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); run() } }}
            placeholder="标的代码：600519 / sh000300 / 00700 / AAPL / BTC"
          />
          <button id="prun" type="button" disabled={busy} onClick={run}>
            {busy ? "跑着…" : "跑预测"}
          </button>
        </div>
        <div className="hint">
          直接调本地小模型采样一段未来 K 线，几十秒起步（首次要先加载模型）。
          它只看了价格序列，不知道公司做什么生意、有没有新闻。
        </div>

        {/* ── 模型读数（Tremor）────────────────────────────────────────
            ★红涨绿跌：方向的颜色从这里出去 —— 涨用 up（红）、跌用 down（绿）。
              这是**规矩不是偏好**，见 format.ts 顶部那段。 */}
        {s?.ok && d ? (
          <div className="mt-3">
            {/* ★抬头：把「预测的是谁」和「最重要的那个数」先摆出来。两件都是原来缺的 ——
                · 这张卡从头到尾**没写过标的的名称和代码**。跑完一次预测，页面上只有
                  用户刚敲进输入框的那串字符，别处一个字都没有，没法确认自己在看哪只。
                  `d.name` / `d.code` 是接口返回的，不在这里编。
                · 涨跌幅在 A 股界面里是**主角**，原来被塞在一枚 11px 的 Badge 里
                  （"偏多 · 终点 +0.90%"）—— 下面四个 24px 的数一字排开，
                  最重要的那个反而最小。现在它是 `.num-hero`（30px）。
                ★涨跌色写在这里、不写进 `.num-hero`：那个类是**字号刻度**那一类，
                  不带色。带上的话"涨跌色"就和"数字刻度"耦合成一条规则，
                  下次想把某个大数字改成中性色，就得动刻度本身。 */}
            <div className="phead">
              <div className="min-w-0">
                <span className="kicker">预测标的</span>
                <h3 className="phead-name">
                  {d.name || sym}
                  {d.code ? <span className="phead-code">{d.code}</span> : null}
                </h3>
              </div>
              <div className="phead-chg">
                <span className={"num-hero " + (dirOf(s.pred_chg_pct) === "up"
                  ? "text-up"
                  : dirOf(s.pred_chg_pct) === "down"
                    ? "text-down"
                    : "text-gray-500 dark:text-gray-400")}>
                  {pct(s.pred_chg_pct)}
                </span>
                <span className="kicker">较基准 · {s.pred_len} 根后</span>
              </div>
            </div>

            <div className="mb-2 flex flex-wrap items-center gap-2">
              {/* ★弃答时那个徽章**必须换成"方向弃答"**，不许继续显示中心线的方向。
                  不然页面上一处写着"偏多"（中心线 +14%）、另一处写着"没有一致看法"，
                  用户只会记住"偏多"那个 —— 而且两处各自看都自洽，没人发现是矛盾的。 */}
              {s.spread?.withheld ? (
                <Badge id="pdir" variant="neutral">
                  <span className="tnum">方向弃答</span>
                </Badge>
              ) : (
                <Badge id="pdir" variant={dirOf(s.pred_chg_pct) === "up" ? "error" : dirOf(s.pred_chg_pct) === "down" ? "success" : "neutral"}>
                  <span className="tnum">
                    {dirOf(s.pred_chg_pct) === "up" ? "偏多" : dirOf(s.pred_chg_pct) === "down" ? "偏空" : "走平"}
                  </span>
                </Badge>
              )}
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {s.model} · {s.ms}ms{s.cached ? " · 命中缓存" : ""} · 采样 {s.sample_count} 条
              </span>
            </div>

            {/* ★四格读数。字号从 `text-2xl` 换成 `.num-lg`：**都是 24px**，
                换的是"它落在刻度上"这件事 —— `.num-lg` 同时给了
                `tabular-nums` / `line-height: 1.1` / `letter-spacing: -0.01em`，
                四个数并排时数位对齐、行高一致（原来靠 `text-2xl` 自带的 1.33
                行高，卡片会被撑高一截）。标签那一档没动字号，它本来就是 12px。
                ★第一格的 `text-gray-900 dark:text-gray-50` **必须留着**：
                  它是探针 `#predCard .grid .tnum` 量的那个元素，而那条判据守的是
                  "Tailwind 的 `dark:` 工具类还跟着系统走"（见 probe.ts:425 那段）。
                  把这四格全换成 token 色，那条判据会照样绿 —— 但它量的东西没了，
                  变成一条空转的装饰。 */}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Card className="card">
                <p className="text-xs font-medium text-gray-500 dark:text-gray-400">基准收盘</p>
                <p className="tnum num-lg mt-1 text-gray-900 dark:text-gray-50">
                  {num(s.base_close)}
                </p>
              </Card>
              <Card className="card">
                <p className="text-xs font-medium text-gray-500 dark:text-gray-400">预测终点</p>
                <p className="tnum num-lg mt-1 text-gray-900 dark:text-gray-50">
                  {num(s.pred_last_close)}
                </p>
              </Card>
              <Card className="card">
                <p className="text-xs font-medium text-gray-500 dark:text-gray-400">路径内最高</p>
                <p className="tnum num-lg mt-1 text-up">{pct(s.pred_max_up_pct)}</p>
              </Card>
              <Card className="card">
                <p className="text-xs font-medium text-gray-500 dark:text-gray-400">路径内最低</p>
                <p className="tnum num-lg mt-1 text-down">{pct(s.pred_max_dn_pct)}</p>
              </Card>
            </div>

            {/* ── 多采样区间 + 分歧度 ──────────────────────────────────────
                ★这一块的三个纪律（都不是风格问题）：

                ① **区间用中性色，绝不用涨跌色**。区间宽窄不是"涨/跌"，它是
                   "模型有多不确定"。套 `--up`/`--down` 等于替用户把区间读成方向
                   ——和 FundCard 那条"分位不是涨跌"是同一个错。

                ② **"名义"两个字必须留着**。这区间不是"价格有 80% 的概率落在里面"，
                   而是"N 条路径的极差 —— 若模型的分布就是真相，一个新样本落在
                   极差内的概率恰好是 (N-1)/(N+1)"。少了"名义"两字就成了对未来的
                   概率承诺，而本仓库的回测已经实测它没有超额能力。

                ③ **弃答要说在前面，不能藏在数字下面**。`withheld` 为真时上面那个
                   大数字（较基准的涨跌）仍然画着 —— 它是**中心线**读出来的，
                   与"模型有没有一致看法"是两件事。不在这儿明说，用户就只会看那个数字。 */}
            {s.spread ? (
              <div className="spread">
                <div className="spread-band">
                  <span className="kicker">
                    {s.spread.n_paths} 条独立采样路径的<b>名义 {s.spread.level_pct}%</b> 区间
                  </span>
                  <span className="spread-range tnum">
                    {num(s.spread.lo)} ~ {num(s.spread.hi)}
                  </span>
                  <span className="spread-pct tnum">
                    （{pct(s.spread.lo_pct)} ~ {pct(s.spread.hi_pct)}）
                  </span>
                </div>
                <div className="spread-votes">
                  <span className="tnum">
                    终点方向：看涨 {s.spread.up} / 看跌 {s.spread.down}
                    {s.spread.flat ? ` / 走平 ${s.spread.flat}` : ""}
                  </span>
                  {s.spread.consensus !== null ? (
                    <span className="tnum">（表态的里面 {Math.round(s.spread.consensus * 100)}% 同向）</span>
                  ) : null}
                  {s.spread.hist_move_pct !== null ? (
                    <span className="spread-hist">
                      同期历史自然波动约 ±{s.spread.hist_move_pct}%
                    </span>
                  ) : null}
                </div>
                <div className={s.spread.withheld ? "spread-verdict off" : "spread-verdict"}>
                  {s.spread.withheld ? (
                    <>
                      <b>方向弃答</b>：{s.spread.why}
                    </>
                  ) : (
                    <>
                      <b>模型偏向一边</b>：{s.spread.n_paths} 条路径里{" "}
                      {Math.max(s.spread.up, s.spread.down)} 条同向，
                      投票比例的 95% 区间
                      {s.spread.vote_ci95
                        ? `（${s.spread.vote_ci95[0]}–${s.spread.vote_ci95[1]}%）`
                        : ""}
                      不跨 50%，所以这一次它确实偏向了一边。
                      <b>偏向不等于会实现</b> —— 本站回测已实测它没有超额能力。
                    </>
                  )}
                </div>
              </div>
            ) : s.sample_count === 1 ? (
              /* N=1 时**明说没有区间**，而不是留一块空白让人以为"区间是 0"。 */
              <div className="spread">
                <div className="spread-verdict off">
                  <b>没有区间</b>：这次只采了 1 条路径。一条路径的 min/max 说明的是
                  "这一次采样飘了多远"，<b>不是</b>"价格可能落在哪" ——
                  所以这里不给区间，也不给覆盖率。
                </div>
              </div>
            ) : null}

            {/* ★「模型觉得会安静还是剧烈」—— 用**同一条路径内的天数占比**表达，
                不是另造一个分数。s.days_above_base / pred_len 是有据可查的。 */}
            <div className="mt-3">
              <div className="mb-1 flex items-baseline justify-between text-xs text-gray-500 dark:text-gray-400">
                <span>路径中收在基准之上的天数</span>
                <span className="tnum">{s.days_above_base} / {s.pred_len}</span>
              </div>
              <ProgressBar value={(s.days_above_base / Math.max(1, s.pred_len)) * 100} />
            </div>

            {/* ★修正量如实上报。模型给的 high/low 本来就不自洽，我们按开/收价重建了 ——
                **修了多少必须说出来**，不说就是"悄悄改了模型的输出"，那是另一回事。
                这一层在三条路上都要有：给大模型的摘要、页面的说明条、以及判据。 */}
            {s.ohlc_repaired_bars > 0 ? (
              <div className="mt-3">
                {/* ★Tremor 的 prop 名是 `variant`（default/success/error/warning/neutral），
                    不是 `color`。写 `color="amber"` 不报错、不告警 —— React 会把它当成
                    一个未知的 DOM 属性透传到 div 上，于是样式**静默地**退回 default（蓝）。
                    Tremor 系列组件统一用 variant，别按 MUI 的习惯写。 */}
                <Callout
                  className="card"
                  variant="warning"
                  title={`预测段有 ${s.ohlc_repaired_bars} 根的最高/最低价被重建`}
                >
                  模型给的高低价不自洽（影线穿进实体），已按开/收价重建，最大修正{" "}
                  {pct(s.ohlc_max_clamp_pct)}。所以<b>高/低点的可信度低于收盘价那条路径</b>。
                </Callout>
              </div>
            ) : null}
          </div>
        ) : null}

        <div id="pout" aria-live="polite" aria-busy={busy}>
          {r && !s?.ok ? (
            <div className="disclaim">
              <b>没跑成</b>：{s?.error || s?.msg || "模型没返回结果"}
              {s?.hint ? <><br />{s.hint}</> : null}
            </div>
          ) : null}

          {s?.ok && d ? (
            <>
              <div className="reading">
                {s.sample_count > 1
                  ? <>模型采样出 <b>{s.sample_count} 条</b>独立路径（{d.params?.pred_len ?? "?"} 根），
                      图上实线是它们的<b>逐根均值</b>（一条谁也没走过的中心线），
                      阴影带是它们的极差。中心线终点较基准{" "}
                      <b>{pct(s.pred_chg_pct)}</b>，路径内最高 <b>{pct(s.pred_max_up_pct)}</b> / 最低{" "}
                      <b>{pct(s.pred_max_dn_pct)}</b>。</>
                  : <>模型采样出的<b>一条</b>路径（{d.params?.pred_len ?? "?"} 根）：终点较基准{" "}
                      <b>{pct(s.pred_chg_pct)}</b>，路径内最高 <b>{pct(s.pred_max_up_pct)}</b> / 最低{" "}
                      <b>{pct(s.pred_max_dn_pct)}</b>。</>}
              </div>
              <KChart data={d as unknown as ChartData} />
              {/* ★这段措辞是**判据的锚**（`#pout .disclaim`）：它必须一直说"不构成投资建议"，
                  而且随着"一条路径"变成"N 条路径"要跟着改 —— 不改的话页面就成了
                  "画着区间、说着一条路径"。 */}
              <div className="disclaim">
                <b>风险提示</b>：这是模型按价格序列条件采样出的
                {s.sample_count > 1 ? <><b>{s.sample_count} 条</b>路径及其区间</> : <><b>一条</b>路径</>}，
                不是概率意义上的预期值{s.spread ? <>（区间标着"名义"二字：它说的是"N 条路径的极差"，
                不是"价格有这么大可能落在里面"）</> : null}；
                它没有基本面、财报、新闻、政策这些输入。
                所有内容仅供学习，<b>不构成投资建议</b>。
              </div>
            </>
          ) : null}
        </div>
      </div>
    </section>
  )
}
