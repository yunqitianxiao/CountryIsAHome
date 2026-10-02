/**
 * 行情总览（`/dash`）。**只复用 `/api/kline`，不新增后端路由。**
 *
 * 三条纪律，每条都对应一条判据：
 *
 * 1. **页面上的数必须就是接口给的数。** 判据自己打一遍 `/api/kline`，再要求页面
 *    渲染出来的文本里逐字含有那几个数（收盘价、5/20/60 日涨跌）。所以这里
 *    **一个数字都不许四舍五入成别的样子**，格式化只走 format.ts 那几个函数。
 *
 * 2. **一行坏掉只坏一行。** 每个标的分开写错误，坏的行把原因显示出来 ——
 *    "悄悄少一行"是这类页面最常见的谎：看着像"今天只有四只标的"。
 *
 * 3. **回测数字只能从服务端那个出口来。** 这一页**不**自己去打 `/api/accuracy`，
 *    而是搬运壳子里 `#acc-boot` 已经被服务端填好的那段（和主页面同一份）。
 *    自己再打一次接口就多出了一个出口 —— 两个出口迟早会说两套话。
 */
import * as React from "react"
import { Badge } from "../Badge/Badge"
import { Callout } from "../Callout/Callout"
import { Divider } from "../Divider/Divider"
import { readAccBoot } from "../../chrome"
import { Shell } from "../Shell/Shell"
import { loadDash, rangePos, type DashRow } from "../../dash"
import { Spark } from "../Spark/Spark"
import { dirClass, dirOf, num, pct } from "../../format"

/** 一个涨跌格。★`k` 是**判据的落点**（`chg5/chg20/chg60`）：探针逐格读它的
 *  **计算色**，判据拿它跟 `--up`/`--down` 比。读计算色而不是读类名 ——
 *  "红涨绿跌"是法，而类名只是状态位：有人把 `.text-up` 的规则改掉，
 *  类名照样挂在元素上、颜色已经不是红的了。 */
function Chg({ v, k }: { v: number | null | undefined; k?: string }) {
  return <span className={`tnum ${dirClass(v)}`} data-k={k}>{pct(v)}</span>
}

/** 区间位置：收盘价在「区间最低～最高」里的位置。纯 div，不走 Recharts ——
 *  Recharts 首帧量不到尺寸（见 utils/ChartSizer.tsx），同步探针会整个量空。 */
function RangeBar({ pos }: { pos: number }) {
  return (
    <div className="relative h-1.5 w-full min-w-[70px] rounded-full bg-[var(--rule-2)]" title={`区间位置 ${pos.toFixed(0)}%`}>
      <div className="absolute inset-y-0 left-0 rounded-full bg-[var(--accent)]" style={{ width: `${pos}%` }} />
    </div>
  )
}

/** 60 日涨跌对比条。颜色只从 token 来（红涨绿跌），宽度按最大绝对值归一。 */
function CmpBar({ label, v, max }: { label: string; v: number | null; max: number }) {
  const d = dirOf(v)
  const w = v == null || max <= 0 ? 0 : (Math.abs(v) / max) * 100
  return (
    <div className="flex items-center gap-2 py-1">
      <span className="w-20 flex-none truncate text-xs text-[var(--ink-2)]" title={label}>
        {label}
      </span>
      <div className="relative h-4 flex-1 rounded-sm bg-[var(--rule-2)]">
        <div
          className={`absolute inset-y-0 left-0 rounded-sm ${d === "down" ? "bg-down" : d === "up" ? "bg-up" : "bg-[var(--ink-3)]"}`}
          style={{ width: `${w}%` }}
        />
      </div>
      <span className="w-20 flex-none text-right text-xs">
        <Chg v={v} />
      </span>
    </div>
  )
}

export function Dash({ probe = false, initial }: { probe?: boolean; initial?: DashRow[] }) {
  // 探针模式：数据在 main.tsx 里**同步**取好传进来（见 dash.ts 顶部那段）
  const [rows, setRows] = React.useState<DashRow[]>(() => initial ?? [])
  const [loading, setLoading] = React.useState(!initial)
  const accHtml = React.useMemo(readAccBoot, [])

  const load = React.useCallback(() => {
    setLoading(true)
    loadDash()
      .then((r) => { setRows(r); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  React.useEffect(() => {
    if (probe || initial) return
    load()
  }, [probe, initial, load])

  const ok = rows.filter((r) => r.s)
  const maxAbs = ok.reduce((m, r) => Math.max(m, Math.abs(r.s!.chg_pct_60d ?? 0)), 0)

  return (
    <Shell probe={probe}>
      <div className="mb-3 mt-3 flex flex-wrap items-center gap-2">
        <h1 className="serif text-xl font-semibold text-[var(--ink)]">行情总览</h1>
        <span className="text-xs text-[var(--ink-3)]">四个市场，同一套口径</span>
        <Badge variant="neutral">{rows.length} 个标的</Badge>
      </div>
      <div className="grid grid-cols-1 items-start gap-4">
        <section className="card rounded-lg border border-line bg-surface p-4" id="dashCard" aria-label="行情总览">
          <div className="chd mb-2 flex flex-wrap items-baseline gap-2">
            <h2 className="text-base font-semibold text-[var(--ink)]">最新行情</h2>
            <span className="text-xs text-[var(--ink-3)]">
              取数口径：日 K，最近 250 个交易日。数据源逐标的标注 —— 不同源的同一天可能差一点点。
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm" id="dashTable">
              <thead>
                <tr className="border-b border-line text-left text-xs text-[var(--ink-3)]">
                  <th className="py-1.5 pr-3 font-medium">标的</th>
                  <th className="py-1.5 pr-3 text-right font-medium">最新收盘</th>
                  <th className="py-1.5 pr-3 text-right font-medium">近 5 日</th>
                  <th className="py-1.5 pr-3 text-right font-medium">近 20 日</th>
                  <th className="py-1.5 pr-3 text-right font-medium">近 60 日</th>
                  <th className="py-1.5 pr-3 font-medium">近三个月走势</th>
                  <th className="py-1.5 pr-3 text-right font-medium">年化波动</th>
                  <th className="py-1.5 pr-3 font-medium">区间位置</th>
                  <th className="py-1.5 font-medium">数据源</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) =>
                  r.s ? (
                    <tr key={r.sym} className="dash-row border-b border-[var(--rule-2)]" data-sym={r.sym}>
                      <td className="py-1.5 pr-3">
                        {/* ★`name === code` 时**不要**把代码再显示一遍：`resolve()` 对没有
                            中文名的标的（600519 / 00700 / AAPL / BTC）返回的 name 就是代码本身，
                            照直渲染出来是「600519 600519」—— 看着像页面坏了。
                            不在这里替它编一个中文名：页面不该知道接口不知道的事。 */}
                        <span className="font-medium text-[var(--ink)]">{r.s.name}</span>
                        {r.s.name !== r.s.code ? (
                          <span className="ml-1.5 text-xs text-[var(--ink-3)]">{r.s.code}</span>
                        ) : null}
                      </td>
                      <td className="tnum py-1.5 pr-3 text-right text-[var(--ink)]">{num(r.s.last_close)}</td>
                      <td className="py-1.5 pr-3 text-right"><Chg v={r.s.chg_pct_5d} k="chg5" /></td>
                      <td className="py-1.5 pr-3 text-right"><Chg v={r.s.chg_pct_20d} k="chg20" /></td>
                      <td className="py-1.5 pr-3 text-right"><Chg v={r.s.chg_pct_60d} k="chg60" /></td>
                      {/* ★走势列：与主页行情条**共用** `Spark`（两处长得一样，
                          各写一份的话改了一处另一处不会跟着变）。
                          `label` 给的是这只标的的名字 —— 一列光秃秃的斜线对读屏
                          是纯噪声，而这一列的信息恰恰全在形状里。 */}
                      <td className="py-1.5 pr-3">
                        <Spark pts={r.spark} w={96} h={22}
                               label={`${r.s.name} 近三个月收盘价走势`} />
                      </td>
                      <td className="tnum py-1.5 pr-3 text-right text-[var(--ink-2)]">{pct(r.s.ann_vol_pct, 1)}</td>
                      <td className="py-1.5 pr-3">
                        <RangeBar pos={rangePos(r.s)} />
                        <span className="tnum mt-0.5 block text-[10.5px] text-[var(--ink-3)]">
                          {num(r.s.range_low)} – {num(r.s.range_high)}
                        </span>
                      </td>
                      <td className="py-1.5 text-xs text-[var(--ink-3)]">{r.s.source || "—"}</td>
                    </tr>
                  ) : (
                    // ★坏掉的行也要占一行并说明原因：少一行看起来像"今天只有四只标的"
                    <tr key={r.sym} className="dash-row border-b border-[var(--rule-2)]" data-sym={r.sym}>
                      <td className="py-1.5 pr-3 text-[var(--ink-2)]">{r.sym}</td>
                      <td className="py-1.5 text-xs text-[var(--f5)]" colSpan={8}>
                        这一行没取到：{r.err}
                      </td>
                    </tr>
                  ),
                )}
                {loading ? (
                  <tr>
                    <td className="py-3 text-xs text-[var(--ink-3)]" colSpan={9}>正在取数…</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>

        {ok.length ? (
          <section className="card rounded-lg border border-line bg-surface p-4" id="dashCmp" aria-label="多标的对比">
            <div className="chd mb-2">
              <h2 className="text-base font-semibold text-[var(--ink)]">近 60 日涨跌对比</h2>
              <span className="text-xs text-[var(--ink-3)]">
                横条长度按最大绝对值归一 —— 看的是谁强谁弱，不是绝对幅度。涨=红、跌=绿。
              </span>
            </div>
            {ok
              .slice()
              .sort((a, b) => (b.s!.chg_pct_60d ?? 0) - (a.s!.chg_pct_60d ?? 0))
              .map((r) => (
                <CmpBar key={r.sym} label={r.s!.name} v={r.s!.chg_pct_60d} max={maxAbs} />
              ))}
          </section>
        ) : null}

        <Divider />

        {/* ★回测那段话**原样搬运服务端填好的那一份**（和主页面同一份），不在这里写数字。
            自己再打一次 /api/accuracy 就是给同一件事开了第二个出口。 */}
        <section className="card rounded-lg border border-line bg-surface p-4" aria-label="风险提示">
          <Callout variant="warning" title="这一页只是行情，不是建议">
            表格里的数来自公开行情接口，可能延迟、可能因为数据源不同而有出入；
            <b>涨跌颜色是 A 股习惯（红涨绿跌）</b>，和欧美相反。
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
