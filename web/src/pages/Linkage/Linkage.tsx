/**
 * `/linkage` 关联检测。
 *
 * ★★**这一页的重心是"采集"，不是"分析"**（用户明确说了"重采集弱分析"）。
 *   所以第一块卡是**采集健康度**：哪个源、采到多少行、用了多少毫秒、报了什么错。
 *   把它放在异动榜下面的话，页面就在说另一件事了 —— 一个"异动榜"看着像结论，
 *   而一堆源里有一个已经挂了三轮，才是这一页真正要喊的事。
 *
 * ★异动榜**只报"发生了什么"**，不下判断：涨跌幅、量比、z-score 都是从快照里
 *   直接算出来的数。所以这一页**没有**"建议关注""机会"这类词 —— 加上去之后，
 *   一张流水表就变成了推荐（服务端的 `note` 里也是这个立场，那一句原样显示）。
 *
 * ★`z_pct` / `z_vol` 是**标准化之后**的偏离（对波动率归一），
 *   这正是排序要用的那把尺子：涨 5% 在一个高波动标的上不叫异动。
 *   但它仍然只是"偏离"，不是"会涨"。
 */
import * as React from "react"
import {
  CHANNEL, getLinkage, getLinkageOne, type Anomaly, type Fail, type LinkGroup, type LinkageResp,
} from "../../api2"
import { HBars } from "../../agentui/charts"
import { DEMO, demoLinkage } from "../../agentui/demo"
import { Frame } from "../../agentui/Frame"
import { callSync, type SyncState } from "../../agentui/probe"
import { Degraded, DemoBanner, Empty, Loading, Meter, Panel, Stat, Th, Td, V, Well } from "../../agentui/State"
import { SourceTable } from "../../agentui/Sources"
import { dirClass } from "../../format"

/** 采集健康度：**这一页的第一块**。
 *
 *  ★`n`/`ms` 是 `null` 时显示「—」而不是 0 —— "这个源没报数"和"这个源报了 0 行"
 *    在页面上必须是两句不同的话（`api2.ts: SourceHealth` 那段）。
 *  ★失败的源**排在最前面**：一张健康表的价值全在"哪一行是红的"。 */
function CollectHealth({ sources, ts, rows, markets }: {
  sources: LinkageResp["collected"]["sources"]
  ts?: string
  rows?: number | null
  markets?: string[]
}) {
  const bad = sources.filter((s) => !s.ok)
  const okN = sources.length - bad.length
  const ratio = sources.length ? (okN / sources.length) * 100 : 0
  // 最慢的源：`ms` 是 null 的**不参与比较**（"没报耗时"不是"耗时 0"）。
  // ★这一份算出来只为了显示，所以放在渲染前而不是嵌在 JSX 里 ——
  //   嵌进去的话那个 `??` 与 `<` 的优先级会让它悄悄算成别的东西。
  let slow: { id: string; ms: number } | null = null
  for (const s of sources) {
    if (s.ms === null) continue
    if (slow === null || s.ms > slow.ms) slow = { id: s.id, ms: s.ms }
  }
  return (
    <>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="源" value={`${okN}/${sources.length}`} k="srcOk"
              sub={`${bad.length} 个这一轮没成`} />
        <Stat label="采集时刻" value={<span className="text-sm">{ts || "—"}</span>} k="collectTs" />
        <Stat label="这一轮行数" value={rows === null || rows === undefined ? "—" : rows.toLocaleString("zh-CN")}
              k="collectRows" sub="所有源加起来" />
        <Stat label="覆盖市场" value={<span className="text-sm">{(markets ?? []).join(" / ") || "—"}</span>}
              k="collectMarkets" />
        <Stat label="最慢的源" k="slowest"
              value={<span className="text-sm">{slow ? slow.id : "—"}</span>}
              sub={slow ? `${slow.ms.toLocaleString("zh-CN")} ms` : "没有源报了耗时"} />
        <Stat label="健康度" value={`${ratio.toFixed(0)}%`} k="healthPct"
              sub="成功的源 / 全部源" />
      </div>

      <div className="mt-2"><Meter v={ratio} k="healthMeter" title={`${okN}/${sources.length} 个源这一轮成功了`} /></div>

      {bad.length ? (
        <ul className="mt-2 space-y-1" data-k="badSources">
          {bad.map((s) => (
            <li key={s.id}
                className="rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] p-2 text-xs text-[var(--warn-ink)]">
              <b>{s.id}</b> 这一轮没成：{s.err || "（服务端没给原因）"}
              <span className="ml-2 opacity-80">
                采到 <V v={s.n} digits={0} /> 行 · 用了 <V v={s.ms} digits={0} suffix=" ms" />
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-3">
        {/* 表的形状与 `/collect` 那一页**共用一份**（`agentui/Sources.tsx`）：
            同一个源的同一个错误在两页上必须是同一句话。 */}
        <SourceTable sources={sources} />
      </div>
    </>
  )
}

function AnomalyRow({ a, sel, onPick }: { a: Anomaly; sel: boolean; onPick: () => void }) {
  const m = a.metrics
  return (
    <tr data-k="anomaly" data-code={a.code} data-sel={sel ? "1" : "0"}
        className={sel ? "bg-[var(--paper)]" : undefined}>
      <Td>
        <button
          type="button" onClick={onPick}
          aria-pressed={sel}
          className="rounded border border-[var(--rule)] bg-[var(--card)] px-1.5 py-0.5 text-xs text-[var(--ink-2)] hover:border-[var(--accent)] hover:text-[var(--accent)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        >
          看关联
        </button>
      </Td>
      <Td><code className="tnum">{a.code}</code> <span className="text-[var(--ink)]">{a.name}</span></Td>
      <Td right><span className={dirClass(m.pct)} k="pct">{m.pct === null ? "—" : `${m.pct > 0 ? "+" : ""}${m.pct.toFixed(2)}%`}</span></Td>
      <Td right k="vr"><V v={m.vol_ratio} digits={2} suffix="×" /></Td>
      <Td right k="zpct"><span className={dirClass(m.z_pct)}>{m.z_pct === null ? "—" : (m.z_pct > 0 ? "+" : "") + m.z_pct.toFixed(2)}</span></Td>
      <Td right k="zvol"><V v={m.z_vol} digits={2} /></Td>
      <Td right k="amt"><V v={m.amount_yi} digits={2} suffix=" 亿" /></Td>
      <Td right k="turn"><V v={m.turnover} digits={2} suffix="%" /></Td>
      <Td>
        {/* ★标签**原样显示**（服务端可以加新标签）。前端翻译一遍的话，
            后端加一个新标签时页面上会静默少一个。 */}
        {(a.kinds ?? []).map((k) => (
          <span key={k} className="mr-1 rounded border border-[var(--rule)] bg-[var(--paper)] px-1 text-[10.5px] text-[var(--ink-2)]">
            {k}
          </span>
        ))}
      </Td>
    </tr>
  )
}

/** 选中的那只票的关联组。**证据为空时要说出来** —— 规格 §3.3 的纪律是
 *  "空的证据 = 这条关联不该显示"，所以这里不给一个空框，给一句话。 */
function LinkDetail({ code, group, note }: { code: string; group: LinkGroup | null; note?: string | null }) {
  if (!group) return null
  const ev = group.evidence ?? []
  return (
    <div data-k="linkDetail" data-code={code}>
      {!ev.length ? (
        <div className="rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] p-2 text-xs text-[var(--warn-ink)]"
             data-k="noEvidence">
          这一组关联<b>没有给证据</b>（<code>evidence</code> 是空的）。按规格，没有证据的关联不该显示 ——
          所以下面那两张表只是「服务端说它们相关」这一句话，<b>读的时候要打折</b>。
        </div>
      ) : (
        <ul className="mt-2 list-disc pl-5 text-xs text-[var(--ink-2)]" data-k="evidence">
          {ev.map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      )}

      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-semibold text-[var(--ink-2)]">相关板块</div>
          {group.boards?.length ? (
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-[var(--rule)] text-xs text-[var(--ink-3)]">
                  <Th>板块</Th><Th>类型</Th><Th right>涨跌</Th><Th>领涨</Th>
                </tr>
              </thead>
              <tbody>
                {group.boards.map((b) => (
                  <tr key={b.code} data-k="board">
                    <Td><code className="tnum">{b.code}</code> <span className="text-[var(--ink)]">{b.name}</span></Td>
                    <Td>{b.kind || "—"}</Td>
                    <Td right><span className={dirClass(b.pct)}><V v={b.pct} digits={2} suffix="%" signed /></span></Td>
                    <Td>{b.lead_name ? <>{b.lead_name} <code className="text-[10.5px]">{b.lead_code}</code></> : "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty what="没有相关板块" hint={<>服务端这一轮给的 <code>boards</code> 是空的 —— 这也是一条结论：它在板块这一层没有邻居。</>} />}
        </div>

        <div>
          <div className="mb-1 text-xs font-semibold text-[var(--ink-2)]">相关个股</div>
          {group.peers?.length ? (
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-[var(--rule)] text-xs text-[var(--ink-3)]">
                  <Th>标的</Th><Th>关系</Th><Th right>相关度</Th><Th right>领先</Th><Th right>涨跌</Th><Th right>量比</Th>
                </tr>
              </thead>
              <tbody>
                {group.peers.map((p) => (
                  <tr key={p.code} data-k="peer" data-code={p.code}>
                    <Td><code className="tnum">{p.code}</code> <span className="text-[var(--ink)]">{p.name}</span></Td>
                    <Td data-k="rel">{p.rel || "—"}</Td>
                    {/* ★`rel_value` 是 `null` 时**不许显示成 0**：规格 §3.3 说样本不足
                        就不给这个数。0 相关和"没量到相关"是两件事。 */}
                    <Td right k="relv">{p.rel_value === null ? "—" : p.rel_value.toFixed(2)}</Td>
                    <Td right k="lag">{p.lag_min === null ? "—" : `${p.lag_min} 分`}</Td>
                    <Td right><span className={dirClass(p.pct)}><V v={p.pct} digits={2} suffix="%" signed /></span></Td>
                    <Td right><V v={p.vol_ratio} digits={2} suffix="×" /></Td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty what="没有相关个股" />}
        </div>
      </div>

      {note ? <p className="mt-2 text-[10.5px] text-[var(--ink-3)]">{note}</p> : null}
    </div>
  )
}

export function Linkage({ probe = false }: { probe?: boolean }) {
  const initial = React.useMemo<SyncState<LinkageResp> | null>(() => {
    if (!probe) return null
    // ★探针那条路**同步**取（`agentui/probe.ts`）：异步回来的数，同步探针量不到。
    if (DEMO) return { phase: "ok", data: demoLinkage(20) }
    return callSync<LinkageResp>(CHANNEL.linkage, { scope: "all", top: 20 })
  }, [probe])

  const [resp, setResp] = React.useState<LinkageResp | null>(() => (initial?.phase === "ok" ? initial.data : null))
  const [fail, setFail] = React.useState<Fail | null>(() => (initial?.phase === "fail" ? initial.fail : null))
  const [loading, setLoading] = React.useState(!initial)

  const [sel, setSel] = React.useState<string | null>(null)
  const [one, setOne] = React.useState<{ code: string; group: LinkGroup | null; note?: string | null; fail: Fail | null; loading: boolean } | null>(null)

  const load = React.useCallback(() => {
    setLoading(true); setFail(null)
    if (DEMO) { setResp(demoLinkage(20)); setLoading(false); return }
    void getLinkage("all", undefined, 20).then((r) => {
      if (r.ok) { setResp(r.data); setFail(null) } else { setResp(null); setFail(r) }
      setLoading(false)
    })
  }, [])

  React.useEffect(() => { if (!initial) load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  /** 挑一只票看关联。**先看这一轮的主响应里有没有**（`links[code]`）——
   *  有就不再发一次请求；没有再单独问一次（规格 §3.3 有那条单查的通道）。
   *  ★单查失败**只坏这一块**，异动榜和采集面板照常显示（逐块降级）。 */
  const pick = (code: string) => {
    setSel(code)
    const local = resp?.links?.[code]
    if (local) { setOne({ code, group: local, fail: null, loading: false }); return }
    if (DEMO) {
      const all = demoLinkage(20)
      setOne({ code, group: all.links[code] ?? null, note: all.note, fail: null, loading: false })
      return
    }
    setOne({ code, group: null, fail: null, loading: true })
    void getLinkageOne(code).then((r) => {
      if (r.ok) setOne({ code, group: r.data.link ?? null, note: r.data.note ?? null, fail: null, loading: false })
      else setOne({ code, group: null, fail: r, loading: false })
    })
  }

  const anomalies = resp?.anomalies ?? []
  // ★排序按 `z_pct` 降序，**在页面上写明**。不写的话这一列的顺序看起来像
  //   服务端给的顺序，而它只是"涨得最离谱的排前面"的一种读法。
  const ranked = [...anomalies].sort((a, b) => (b.metrics?.z_pct ?? -Infinity) - (a.metrics?.z_pct ?? -Infinity))

  return (
    <Frame
      probe={probe} title="关联检测"
      sub="先看采到了什么 · 再看谁在异动 · 最后才看它们跟谁有关联"
      footExtra={<>这一页只报"发生了什么"，不下判断、不给买卖建议、也不预测。</>}
    >
      {DEMO ? <DemoBanner what="这一页显示的是本地编的一份快照、一份异动榜与一组关联，不是任何行情源返回的东西。" /> : null}

      {/* ★★第一块：**采集健康度**。放在异动榜前面是刻意的 —— 见文件顶上那段。 */}
      <Panel
        id="collectPanel" title="采集健康度"
        note={<>通道 <code className="tnum">{CHANNEL.linkage}</code> 的 <code>collected</code> 那一段</>}
        right={<button
          type="button" onClick={load} disabled={loading}
          className="rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1 text-xs text-[var(--ink-2)] disabled:opacity-40"
        >重新采一次看</button>}
      >
        {resp?.collected?.sources?.length ? (
          <CollectHealth sources={resp.collected.sources} ts={resp.collected.ts}
                         rows={resp.collected.rows} markets={resp.collected.markets} />
        ) : fail ? (
          <Degraded channel={CHANNEL.linkage} fail={fail} what="关联检测（采集健康度就在这份返回里）" />
        ) : loading ? (
          <Loading what="正在取采集健康度" />
        ) : (
          <Empty what="这一轮没有采集健康度的记录"
                 hint={<>服务端这一轮给的 <code>collected.sources</code> 是空的。没有这一栏，这一页就没有「采到了什么」可看 —— 那正是它最该有的东西。</>} />
        )}
      </Panel>

      {fail ? null : loading && !resp ? <Loading what="正在取关联检测" /> : null}

      {resp ? (
        <>
          <Panel id="snapshotPanel" title="这一轮的快照" note="下面所有的数都是从这一份快照里算的">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              <Stat label="快照时刻" value={<span className="text-sm">{resp.snapshot?.ts || "—"}</span>} k="snapTs" />
              <Stat label="快照市场" value={<span className="text-sm">{resp.snapshot?.market || "—"}</span>} k="snapMarket" />
              <Stat label="快照行数" value={resp.snapshot?.rows === null || resp.snapshot?.rows === undefined
                ? "—" : resp.snapshot.rows.toLocaleString("zh-CN")} k="snapRows" />
            </div>
            {resp.ts ? <div className="mt-1 text-[10.5px] text-[var(--ink-3)]">整份返回的时刻 <span className="tnum">{resp.ts}</span></div> : null}
          </Panel>

          <Panel id="radarPanel" title="异动雷达"
                 note={`按 z_pct 降序（前端排的，不是服务端给的顺序）· 共 ${anomalies.length} 条`}>
            {anomalies.length ? (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[900px] border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-[var(--rule)] text-xs text-[var(--ink-3)]">
                        <Th>关联</Th><Th>标的</Th><Th right>涨跌</Th><Th right>量比</Th>
                        <Th right>z(涨幅)</Th><Th right>z(量)</Th><Th right>成交额</Th><Th right>换手</Th><Th>标签</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {ranked.map((a) => (
                        <AnomalyRow key={a.code} a={a} sel={sel === a.code} onPick={() => pick(a.code)} />
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="mt-3">
                  <div className="mb-1 text-xs text-[var(--ink-3)]">
                    z(涨幅) 排名 —— <b>条越长 = 相对它自己的波动越离谱</b>，不是涨得越多
                  </div>
                  <HBars
                    rows={ranked.map((a) => ({ name: `${a.name}`, z: a.metrics?.z_pct ?? 0 }))}
                    index="name" value="z" probe={probe} k="zBars" digits={2}
                    tone={(r) => ((r.z as number) >= 0 ? "up" : "down")}
                  />
                </div>

                <p className="mt-2 text-[10.5px] leading-snug text-[var(--ink-3)]">
                  ★<b>异动 ≠ 机会</b>。z-score 说的是「这个偏离在统计上少见」，它一个字都没说
                  "接下来会往哪儿走"。涨得多的用红、跌得多的用绿（红涨绿跌），
                  但颜色只表示方向，不表示好坏。
                </p>
              </>
            ) : (
              <Empty what="这一轮没有异动"
                     hint="平静的一天与坏掉的接口是两件事 —— 采集健康度那张表在上面，用它来分辨。" />
            )}
          </Panel>

          <Panel id="pickPanel" title="它跟谁有关联" note="点异动榜左边的「看关联」，或者在下面直接选">
            <div className="flex flex-wrap items-center gap-2">
              <label htmlFor="linkPick" className="text-xs text-[var(--ink-3)]">标的</label>
              <select
                id="linkPick" value={sel ?? ""} onChange={(e) => { if (e.target.value) pick(e.target.value) }}
                className="rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1.5 text-sm text-[var(--ink)]"
              >
                <option value="">— 选一只 —</option>
                {anomalies.map((a) => <option key={a.code} value={a.code}>{a.code} {a.name}</option>)}
              </select>
              {sel ? <span className="text-xs text-[var(--ink-3)]">当前：<code>{sel}</code></span> : null}
              {sel && !resp.links?.[sel] ? (
                <span className="text-[10.5px] text-[var(--ink-3)]">
                  这一轮的主返回里没有它的关联组 —— 已经走单查通道 <code className="tnum">{CHANNEL.linkageAnomaly}</code> 单独问了一次。
                </span>
              ) : null}
            </div>

            <div className="mt-3">
              {!sel ? (
                <Empty what="还没选标的" hint={<>关联是「这只票跟谁一起动」，所以得先有「这只票」。</>} />
              ) : one?.loading ? (
                <Loading what={`正在单独问 ${sel} 的关联`} />
              ) : one?.fail ? (
                /* ★单查失败**只坏这一块** —— 上面那张异动榜和采集面板都还在。
                   整页一起变成错误页的话，"是哪个通道挂了"就看不出来了。 */
                <Degraded channel={CHANNEL.linkageAnomaly} fail={one.fail} what={`${sel} 的关联`} />
              ) : one?.group ? (
                <LinkDetail code={one.code} group={one.group} note={one.note} />
              ) : (
                <Empty what={`服务端说 ${sel} 这一轮没有关联组`}
                       hint="它可能确实没有邻居，也可能这一轮没算它 —— 两种都在这里，没有第三种。" />
              )}
            </div>
          </Panel>

          {resp.note ? (
            <Panel id="notePanel" title="服务端对这份数据的说明">
              <p className="text-xs leading-relaxed text-[var(--ink-2)]">{resp.note}</p>
            </Panel>
          ) : null}
        </>
      ) : null}

      <Panel id="notDoPanel" title="这一页不做什么">
        <Well>
          <ul className="list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
            <li>不预测、不推荐、不给买卖点。相关性说的是"过去一起动"，不是"接下来会一起动"。</li>
            <li>相关性都带窗口与样本数；<b>样本不足 60 的，服务端不给这个数</b>，页面上显示「—」。
              这里不拿 0 顶上 —— 0 相关和"没量到"是两件事。</li>
            <li>采集健康度放在最前面是刻意的：这一页先回答"采到了什么"，
              再回答"这些数据说了什么"。一个源挂了的时候，下面那张榜的读法要跟着打折。</li>
          </ul>
        </Well>
      </Panel>
    </Frame>
  )
}

export default Linkage
