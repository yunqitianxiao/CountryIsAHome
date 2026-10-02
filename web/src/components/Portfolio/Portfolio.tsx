/**
 * 组合体检（`/portfolio`）。**只复用 `/api/portfolio`，不新增后端路由**（壳的规矩同 /dash）。
 *
 * 四条纪律，每条都对应一条判据：
 *
 * 1. **算不出来就说算不出来。** 这一段最值钱。逐条降级（哪只取不到就点名哪只、
 *    并把它从计算里排除，**绝不静默丢掉再算权重**）、只有一只时不给分散度结论、
 *    ETF/指数**不许塞进任何行业**、取不到的行照实显示 —— 见 `agent/portfolio.py`。
 *
 * 2. **不给健康总分。** skill 的模板把 7 个维度加权成 0–100 分，但本站对
 *    「流动性」「成本效率」**没有任何数据源**。给一个含这两项的分数 = 用一个编出来的
 *    数字去稀释六个真的。所以这一页只列**算出来的**项，且每项都带 `why`（档位依据）。
 *
 * 3. **相关矩阵的底色用中性色**，不用涨跌色 —— 相关系数不是盈亏，
 *    涂红绿会让"高相关"看起来像"亏了"。
 *
 * 4. **图上不引入任何图表库**：净值曲线走内联 SVG、权重条走 div 宽度、相关矩阵走
 *    CSS grid。Recharts 首帧量不到尺寸（Tremor 那条老坑），同步探针会整个量空。
 */
import * as React from "react"
import { Badge } from "../Badge/Badge"
import { Callout } from "../Callout/Callout"
import { Divider } from "../Divider/Divider"
import { readAccBoot } from "../../chrome"
import { Shell } from "../Shell/Shell"
import { readWeights, loadPortfolio, corrTone, type PortfolioResp, type Weight } from "../../portfolio"
import { num, pct } from "../../format"

/** 档位 → 样式。★用中性/警示色，**不用涨跌色**：档位不是盈亏。 */
function lvlClass(level: string): string {
  if (/严重|高度集中/.test(level)) return "bg-[var(--warn-bg)] text-[var(--warn-ink)] border-[var(--warn-rule)]"
  if (/偏高|集中|聚集/.test(level)) return "bg-[var(--rule-2)] text-[var(--ink)] border-[var(--rule)]"
  return "bg-[var(--card)] text-[var(--ink-2)] border-[var(--rule)]"
}

/** 净值曲线：内联 SVG。**同一 tick 里几何就绪**，探针量得到。 */
function NavChart({ dates, nav }: { dates: string[]; nav: number[] }) {
  if (!dates.length || nav.length < 2) return null
  const lo = Math.min(...nav), hi = Math.max(...nav)
  const span = hi - lo || 1
  const W = 600, H = 120
  const pts = nav.map((v, i) => {
    const x = (i / (nav.length - 1)) * W
    const y = H - ((v - lo) / span) * H
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })
  return (
    <div id="pfNav">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-[120px] w-full"
           role="img" aria-label="组合净值曲线">
        <polyline points={pts.join(" ")} fill="none" stroke="var(--accent)" strokeWidth="1.5"
                  vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="tnum mt-1 flex justify-between text-[10.5px] text-[var(--ink-3)]">
        <span>{dates[0]}</span>
        <span>净值 {num(lo, 3)} – {num(hi, 3)}（起点 1.000）</span>
        <span>{dates[dates.length - 1]}</span>
      </div>
    </div>
  )
}

/** 回撤曲线：内联 SVG。**由同一条净值序列现算**（`nav / 运行最高 - 1`）。
 *
 * ★为什么不拿上面那个「最大回撤」当输入：那个数是服务端用**完整**序列算的
 *   （`agent/portfolio.py` 的 `_max_drawdown(nav)`），而这条曲线画在**抽样**序列上
 *   （同一个文件的 `step`，最多 240 点）—— 抽样会漏掉真正的峰和谷，两个值
 *   **本来就会差一点点**。把那个数当输入就会画出一条"最深正好等于 -7.3%"的假曲线：
 *   形状是编的，而它看起来比真的还整齐。
 *
 * ★y 轴按这一段自己的极差铺满，**0 在顶、最深在底** —— 回撤曲线只有一半空间有用。
 *   陡峭程度因此**不可比**（和迷你走势那条一样），所以旁边那个数才是"跌了多少"。
 *   方向有判据守着（第一点恒为 0 ⇒ 必须是最小的 y；最深那点必须是最大的 y）。
 *
 * ★染色用 `--down`（绿）：本站是 A 股习惯**红涨绿跌**，回撤是亏，
 *   用涨色会被读成"赚了"。这条也有判据守着（不许等于 `--up`）。
 */
function DDChart({ dates, nav }: { dates: string[]; nav: number[] }) {
  if (nav.length < 2) return null
  const dd: number[] = []
  let peak = nav[0]
  for (const v of nav) {
    if (v > peak) peak = v
    dd.push(peak > 0 ? (v / peak - 1) * 100 : 0)
  }
  const worst = Math.min(...dd)
  const wi = dd.indexOf(worst)
  const W = 600, H = 64
  const span = Math.abs(worst) || 1
  const x = (i: number) => (i / (dd.length - 1)) * W
  const y = (v: number) => (Math.abs(v) / span) * H
  const pts = dd.map((v, i) => `${x(i).toFixed(2)},${y(v).toFixed(2)}`)
  return (
    <div id="pfDD" className="mt-3">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-[64px] w-full"
           role="img" aria-label="组合回撤曲线（0 在顶、最深在底）">
        <polygon points={`0,0 ${pts.join(" ")} ${W},0`} fill="var(--down)" opacity="0.10" />
        <polyline data-k="ddLine" points={pts.join(" ")} fill="none" stroke="var(--down)"
                  strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="tnum mt-1 flex flex-wrap justify-between gap-x-3 text-[10.5px] text-[var(--ink-3)]">
        <span>{dates[0]}</span>
        <span data-k="ddWorst">最深 {pct(worst, 1)}（{dates[wi]}）</span>
        <span>{dates[dates.length - 1]}</span>
      </div>
    </div>
  )
}

export function Portfolio({ probe = false, initial }: { probe?: boolean; initial?: PortfolioResp }) {
  const [resp, setResp] = React.useState<PortfolioResp | null>(() => initial ?? null)
  const [loading, setLoading] = React.useState(!initial)
  const accHtml = React.useMemo(readAccBoot, [])
  const weights: Weight[] = React.useMemo(readWeights, [])

  React.useEffect(() => {
    if (probe || initial) return
    setLoading(true)
    loadPortfolio(weights)
      .then((r) => { setResp(r); setLoading(false) })
      .catch(() => setLoading(false))
  }, [probe, initial, weights])

  const s = resp?.summary
  const d = resp?.data ?? null
  const ok = !!s?.ok && !!d

  return (
    <Shell probe={probe}>
      <div className="mb-3 mt-3 flex flex-wrap items-center gap-2">
        <h1 className="serif text-xl font-semibold text-[var(--ink)]">组合体检</h1>
        <span className="text-xs text-[var(--ink-3)]">你在「模拟投资」里买的每一笔，就是这里体检的组合</span>
        <Badge variant="neutral">{weights.length} 笔持仓</Badge>
      </div>
      <div className="grid grid-cols-1 items-start gap-4">
        {loading ? (
          <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4">
            <span className="text-xs text-[var(--ink-3)]">正在算…（要取每只的日线，几秒钟）</span>
          </section>
        ) : null}

        {/* ★算不了的那一屏：**一个数字都不许有**。理由同游戏页：
            显示 0 会被读成"算出来是 0"。 */}
        {!loading && !ok ? (
          <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" id="pfFail">
            <div className="chd mb-2">
              <h2 className="text-base font-semibold text-[var(--ink)]">这份组合算不了</h2>
            </div>
            <p className="text-sm text-[var(--ink-2)]" id="pfFailMsg">
              {weights.length === 0
                ? "还没有持仓 —— 先去「模拟投资」里买几笔，回来就有得体检了。"
                : (s?.msg || s?.error || "取数失败")}
            </p>
            {s?.hint ? <p className="mt-2 text-xs text-[var(--ink-3)]">{s.hint}</p> : null}
          </section>
        ) : null}

        {ok && d ? (
          <>
            {/* ① 持仓与权重 */}
            <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" id="pfHold" aria-label="持仓明细">
              <div className="chd mb-2 flex flex-wrap items-baseline gap-2">
                <h2 className="text-base font-semibold text-[var(--ink)]">持仓与权重</h2>
                <span className="text-xs text-[var(--ink-3)]">
                  权重按你买入的金额归一化成百分比。进了计算的共 {d.metrics.n_holdings} 只。
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm" id="pfTable">
                  <thead>
                    <tr className="border-b border-[var(--rule)] text-left text-xs text-[var(--ink-3)]">
                      <th className="py-1.5 pr-3 font-medium">标的</th>
                      <th className="py-1.5 pr-3 text-right font-medium">权重</th>
                      <th className="py-1.5 font-medium">占比</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(d.weights || []).map((w) => (
                      <tr key={w.symbol} className="pf-row border-b border-[var(--rule-2)]" data-sym={w.symbol}>
                        <td className="py-1.5 pr-3">
                          <span className="font-medium text-[var(--ink)]">{w.name || w.symbol}</span>
                          {w.name && w.name !== w.symbol ? (
                            <span className="ml-1.5 text-xs text-[var(--ink-3)]">{w.symbol}</span>
                          ) : null}
                        </td>
                        <td className="tnum py-1.5 pr-3 text-right text-[var(--ink)]">{num(w.weight, 2)}%</td>
                        <td className="py-1.5">
                          {/* ★中性色：权重不是盈亏 */}
                          <div className="relative h-1.5 w-full min-w-[70px] rounded-full bg-[var(--rule-2)]">
                            <div className="absolute inset-y-0 left-0 rounded-full bg-[var(--accent)]"
                                 style={{ width: `${Math.max(0, Math.min(100, w.weight))}%` }} />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* ★没取到的那几只必须**点名**：静默丢掉一只 = 悄悄改变了集中度 */}
              {d.dropped && d.dropped.length ? (
                <div className="mt-3 rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] p-2 text-xs text-[var(--warn-ink)]" id="pfDropped">
                  <b>这几只没取到行情，已经从计算里排除</b>（没算进去的权重不会摊到别人头上）：
                  {d.dropped.map((x, i) => (
                    <span key={i} className="ml-1">{x.name || x.symbol}（{x.why}）</span>
                  ))}
                </div>
              ) : null}
            </section>

            {/* ② 集中度 + 相关性 + 风险：算出来的数 */}
            <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" id="pfMetrics" aria-label="风险指标">
              <div className="chd mb-2">
                <h2 className="text-base font-semibold text-[var(--ink)]">算出来的指标</h2>
                <span className="text-xs text-[var(--ink-3)]">
                  ★这里**不给健康总分** —— 本站没有流动性和成本效率的数据源，
                  给一个含这两项的分数等于用编出来的数稀释真的。
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                {([
                  ["前五大集中度", pct(d.metrics.top5, 1)],
                  ["HHI", num(d.metrics.hhi, 4)],
                  ["年化波动", pct(d.metrics.vol_annual, 1)],
                  ["最大回撤", pct(d.metrics.mdd, 1)],
                  ["有效分散比率 EDR", num(d.metrics.edr, 2)],
                  ["Beta（对沪深300）", num(d.metrics.beta, 2)],
                ] as [string, string][]).map(([k, v]) => (
                  <div key={k} className="rounded border border-[var(--rule)] bg-[var(--paper)] p-2">
                    <div className="text-[10.5px] text-[var(--ink-3)]">{k}</div>
                    <div className="tnum text-base text-[var(--ink)]">{v}</div>
                  </div>
                ))}
              </div>
              <div className="tnum mt-2 text-[10.5px] text-[var(--ink-3)]" id="pfWeeks">
                相关性用的是 {d.metrics.corr_weeks} 周收益率
                {d.metrics.beta_days ? `；Beta 用了 ${d.metrics.beta_days} 个共同交易日` : ""}
              </div>
              {d.series ? <div className="mt-3"><NavChart dates={d.series.dates} nav={d.series.nav} /></div> : null}
              {/* ★回撤曲线和净值曲线**同源**（都吃 `d.series`），所以共用一个判断。
                  它是**亏**：峰到谷画在 0 的下方，染色走 `--down`（见 DDChart 的注释）。 */}
              {d.series ? <DDChart dates={d.series.dates} nav={d.series.nav} /> : null}
            </section>

            {/* ③ 相关矩阵 —— CSS grid + 中性底色，不走任何图表库 */}
            {d.corr ? (
              <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" id="pfCorr" aria-label="相关性矩阵">
                <div className="chd mb-2 flex flex-wrap items-baseline gap-2">
                  <h2 className="text-base font-semibold text-[var(--ink)]">两两相关矩阵</h2>
                  <span className="text-xs text-[var(--ink-3)]">
                    越深 = 越同向。底色是<b>中性色</b>不是涨跌色 —— 相关系数不是盈亏。
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="border-collapse text-xs" id="pfCorrTable">
                    <thead>
                      <tr>
                        <th className="p-1" />
                        {d.corr.names.map((n, i) => (
                          <th key={i} className="max-w-[76px] truncate p-1 text-[10.5px] font-normal text-[var(--ink-3)]" title={n}>{n}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {d.corr.m.map((row, i) => (
                        <tr key={i}>
                          <th className="max-w-[76px] truncate p-1 text-left text-[10.5px] font-normal text-[var(--ink-3)]" title={d.corr!.names[i]}>
                            {d.corr!.names[i]}
                          </th>
                          {row.map((v, j) => (
                            <td key={j} className="tnum border border-[var(--rule-2)] p-1 text-center"
                                style={{ background: corrTone(v) }}
                                title={`${d.corr!.names[i]} × ${d.corr!.names[j]} = ${v}`}>
                              {v.toFixed(2)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}

            {/* ④ 档位（每项都带 why，指回 skill 的阈值） */}
            <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" id="pfGrades" aria-label="诊断档位">
              <div className="chd mb-2 flex flex-wrap items-baseline gap-2">
                <h2 className="text-base font-semibold text-[var(--ink)]">诊断</h2>
                <span className="text-xs text-[var(--ink-3)]">每一条都是上面算出来的数按 skill 的档位判的</span>
              </div>
              <ul className="m-0 list-none space-y-1.5 p-0">
                {d.grades.map((g, i) => (
                  <li key={i} className="flex flex-wrap items-baseline gap-2">
                    <span className={`inline-flex flex-none items-center rounded border px-1.5 py-0.5 text-[10.5px] ${lvlClass(g.level)}`}>
                      {g.level}
                    </span>
                    <span className="text-sm text-[var(--ink)]">{g.item}</span>
                    <span className="text-xs text-[var(--ink-3)]">{g.why}</span>
                  </li>
                ))}
              </ul>

              {/* 行业：★ETF/指数没有行业，明说"没有"，**绝不硬塞** */}
              {d.industry ? (
                <div className="mt-3 text-xs text-[var(--ink-2)]" id="pfIndustry">
                  <b>行业分布</b>（只有 A 股个股有公司概况里的行业）：
                  {Object.entries(d.industry).map(([k, v]) => (
                    <span key={k} className="ml-1.5">{k}：{v.join("、")}</span>
                  ))}
                </div>
              ) : null}

              {/* ★算不了的那几项**明写出来**（"没测"和"测了没问题"是两回事） */}
              {d.notes && d.notes.length ? (
                <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-[var(--ink-2)]" id="pfNotes">
                  {d.notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              ) : null}
            </section>

            {/* ⑤ 压力情景 */}
            {d.stress ? (
              <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" id="pfStress" aria-label="压力情景">
                <div className="chd mb-2 flex flex-wrap items-baseline gap-2">
                  <h2 className="text-base font-semibold text-[var(--ink)]">压力情景</h2>
                  <span className="text-xs text-[var(--ink-3)]">
                    按 Beta 线性外推 —— 不是预测，是一条"如果指数跌这么多、按现在的 Beta 大约跌多少"
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                  {d.stress.index_shock.map((ix, i) => (
                    <div key={i} className="rounded border border-[var(--rule)] bg-[var(--paper)] p-2">
                      <div className="text-[10.5px] text-[var(--ink-3)]">沪深300 {ix}%</div>
                      <div className="tnum text-base text-[var(--ink)]">
                        组合约 {d.stress!.port_shock[i]}%
                      </div>
                    </div>
                  ))}
                  <div className="rounded border border-[var(--rule)] bg-[var(--paper)] p-2">
                    <div className="text-[10.5px] text-[var(--ink-3)]">回看窗口里真实发生过的最差</div>
                    <div className="tnum text-base text-[var(--ink)]">{pct(d.stress.hist_worst, 1)}</div>
                  </div>
                </div>
                <p className="mt-2 text-xs text-[var(--ink-3)]" id="pfStressCaveat">{d.stress.caveat}</p>
              </section>
            ) : null}

            <Divider />
          </>
        ) : null}

        <section className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-4" aria-label="风险提示">
          <Callout variant="warning" title="这只是把风险摆出来，不是建议">
            体检用的是<b>回看</b>的行情：相关性、波动、Beta 都是过去发生的事，
            <b>不预示以后也这样</b>。指数下跌时相关性会上升、Beta 会变大，所以压力情景那一栏
            <b>偏乐观</b>。本站<b>不给健康总分</b>（没有流动性和成本效率的数据源）。
          </Callout>
          <div className="disclaim mt-3" id="disclaim">
            <b>风险提示</b>：站内的预测来自只看了价格序列的小模型，不含基本面与新闻；
            所有内容仅供学习，<b>不构成投资建议</b>。
            <div className="acc-line" dangerouslySetInnerHTML={{ __html: accHtml }} />
          </div>
        </section>
      </div>
    </Shell>
  )
}
