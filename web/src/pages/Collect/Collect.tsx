/**
 * `/collect` 采集监视。
 *
 * ★这一页只回答一个问题：**采集器现在怎么样**。它不看行情、不算关联、
 *   不给方向 —— 那是别的页的事。
 *
 * ★三条通道，**各自独立降级**（`api2.ts: CHANNEL` 里的 collectStatus /
 *   collectRing / collectOnce）：
 *     · `status` 挂掉 → 上面那块状态卡变成一块写着原因的板子；
 *     · `ring` 挂掉   → 时间线那一块自己降级，状态卡照常显示；
 *     · `once` 挂掉   → 只影响"采一轮"这个按钮的回执，不动页面上的任何数。
 *   整页共用一个"加载失败"的话，"是哪条通道挂了"就看不出来了，而这一页
 *   存在的理由恰恰是把"哪一环挂了"指出来。
 *
 * ★"还在跑"与"服务死了"必须分得开（同 `/agents` 那条进度条）：
 *   `running===true` 且 `next_ts` 在未来 → 在跑；`running===false` → 停了（**说了**它停了）；
 *   `running===undefined` → 它没说（`CollectorStatus` 里这两个不是一个意思）。
 */
import * as React from "react"
import {
  CHANNEL, collectorOnce, getCollectorRing, getCollectorStatus,
  type CollectorStatus, type Fail, type RingItem,
} from "../../api2"
import { AreaChart } from "../../components/AreaChart/AreaChart"
import { DEMO, demoCollectorStatus, demoRing } from "../../agentui/demo"
import { Frame } from "../../agentui/Frame"
// ★同步探针**用这一页自己的一份**，不用 `agentui/probe.ts` 的那一份 ——
//   公共底座的成功分支是坏的（`ReferenceError: data is not defined`），
//   后端一上线就白屏（实测 `/collect?probe=1`）。理由与去留写在 `./sync.ts` 顶上。
import { callSync } from "./sync"
import { Degraded, DemoBanner, Empty, Loading, Panel, Stat, Th, Td, V, Well } from "../../agentui/State"
import { SourceSummary, SourceTable } from "../../agentui/Sources"

/** 探测图表的定尺寸宽度。★探针那一条路上必须给（见 `utils/ChartSizer.tsx`）：
 *  `ResponsiveContainer` 的尺寸来自 ResizeObserver 回调，那是**异步**的，
 *  首帧拿不到 ⇒ recharts 在 width<0 时直接 return null ⇒ 图**不存在**。
 *  那在报告里不是"图变红了"，是"判据消失"。 */
const CHART_W = 620
const CHART_H = 180

/** 时间线上的一次快照。`ok:false` 的那几次要能被指出来 —— 一条掉下去的曲线
 *  说明"有几次没采到"，不说明"是哪几次"。 */
function RingTimeline({ probe, items }: { probe: boolean; items: RingItem[] }) {
  const bad = items.filter((r) => r.ok === false)
  // ★`rows`/`ms` 是 `null` 的那几次**不进曲线**（recharts 遇到 null 会断开：
  //   这一版没有 `connectNulls`，因为"断开"正好是我们要的形状 ——
  //   一根连着画的线会把"这一次没采到"画成"这一次采到了"）。
  const data = items.map((r, i) => ({
    i: r.ts || String(i),
    rows: r.rows === null || r.rows === undefined ? null : r.rows,
    ms: r.ms === null || r.ms === undefined ? null : r.ms,
  }))
  if (!data.length) {
    return <Empty what="环形缓冲里还没有快照索引"
                  hint={<>环形缓冲 <code>ring</code> 是空的。采集器刚起来、或者这一轮没往缓冲里写 —— 两种都要在这句话里看得出来。</>} />
  }
  return (
    <>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div data-k="ringRows">
          <div className="mb-1 text-xs text-[var(--ink-3)]">每轮的采集行数</div>
          <AreaChart
            data={data} index="i" categories={["rows"]}
            fixedWidth={probe ? CHART_W : undefined}
            fixedHeight={probe ? CHART_H : undefined}
            showLegend={false} startEndOnly
            valueFormatter={(v: number) => v.toLocaleString("zh-CN")}
            yAxisWidth={56}
          />
        </div>
        <div data-k="ringMs">
          <div className="mb-1 text-xs text-[var(--ink-3)]">每轮的耗时（毫秒）</div>
          <AreaChart
            data={data} index="i" categories={["ms"]}
            fixedWidth={probe ? CHART_W : undefined}
            fixedHeight={probe ? CHART_H : undefined}
            showLegend={false} startEndOnly
            valueFormatter={(v: number) => `${v.toLocaleString("zh-CN")} ms`}
            yAxisWidth={56}
          />
        </div>
      </div>
      {/* ★两张图而不是一张双轴图：行数与毫秒是两个量纲。
          画成一张双轴图之后，"行数在涨"和"耗时在涨"会在同一个框里各走各的尺度，
          而看着像同一件事（`dataviz` 那条"永不双轴"）。 */}
      <p className="mt-1 text-[10.5px] text-[var(--ink-3)]">
        行数与耗时<b>分开画</b>（两个量纲）。曲线中间断开的那一段 = 那一次没报数，
        <b>不是 0</b> —— 把它连起来画，"没采到"就变成了"采到 0 行"。
      </p>

      {bad.length ? (
        <ul className="mt-2 space-y-1" data-k="badTicks">
          {bad.map((r, i) => (
            <li key={i} className="rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] p-2 text-xs text-[var(--warn-ink)]">
              <b className="tnum">{r.ts}</b> 这一轮没成：{r.err || "（服务端没给原因）"}
              {r.market ? <span className="ml-2 opacity-80">市场 {r.market}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-[var(--ink-3)]">缓冲里这 {items.length} 轮全都成了。</p>
      )}
    </>
  )
}

export function Collect({ probe = false }: { probe?: boolean }) {
  const initStatus = React.useMemo(() => {
    if (!probe) return null
    if (DEMO) return { ok: true as const, data: demoCollectorStatus() }
    return callSync<CollectorStatus>(CHANNEL.collectStatus)
  }, [probe])
  const initRing = React.useMemo(() => {
    if (!probe) return null
    if (DEMO) return { ok: true as const, data: { ok: true, ring: demoRing(40) } }
    return callSync<{ ok: boolean; items?: RingItem[]; ring?: RingItem[] }>(CHANNEL.collectRing, { limit: 120 })
  }, [probe])

  const [st, setSt] = React.useState<CollectorStatus | null>(() => (initStatus?.ok ? initStatus.data : null))
  const [stFail, setStFail] = React.useState<Fail | null>(() => (initStatus && !initStatus.ok ? initStatus : null))
  const [stLoading, setStLoading] = React.useState(!initStatus)

  const [ring, setRing] = React.useState<RingItem[]>(() => {
    const d = initRing?.ok ? initRing.data : null
    return d?.items ?? d?.ring ?? []
  })
  const [ringFail, setRingFail] = React.useState<Fail | null>(() => (initRing && !initRing.ok ? initRing : null))
  const [ringLoading, setRingLoading] = React.useState(!initRing)

  const [once, setOnce] = React.useState<{ busy: boolean; msg: string | null; fail: Fail | null }>({ busy: false, msg: null, fail: null })

  const loadStatus = React.useCallback(() => {
    setStLoading(true); setStFail(null)
    if (DEMO) { setSt(demoCollectorStatus()); setStLoading(false); return }
    void getCollectorStatus().then((r) => {
      if (r.ok) { setSt(r.data); setStFail(null) } else { setSt(null); setStFail(r) }
      setStLoading(false)
    })
  }, [])

  const loadRing = React.useCallback(() => {
    setRingLoading(true); setRingFail(null)
    if (DEMO) { setRing(demoRing(40)); setRingLoading(false); return }
    void getCollectorRing(120).then((r) => {
      if (r.ok) { setRing(r.data.items ?? r.data.ring ?? []); setRingFail(null) } else { setRing([]); setRingFail(r) }
      setRingLoading(false)
    })
  }, [])

  React.useEffect(() => {
    if (initStatus && initRing) return
    loadStatus(); loadRing()
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  /** 「采一轮」。★这是一个**会改服务端状态**的按钮 —— 所以回执必须显示出来
   *  （采了多少行、用了多久、哪个源没成），而不是"点完什么都不发生"。 */
  const runOnce = () => {
    setOnce({ busy: true, msg: null, fail: null })
    if (DEMO) {
      setOnce({ busy: false, msg: `演示：采了一轮，8,272 行、812 ms（本地编的，没有真的去采）`, fail: null })
      return
    }
    void collectorOnce("all").then((r) => {
      if (r.ok) {
        const rows = r.data.rows
        setOnce({
          busy: false,
          msg: `采完一轮：${rows === null || rows === undefined ? "服务端没报行数" : rows.toLocaleString("zh-CN") + " 行"}` +
            (r.data.ms === null || r.data.ms === undefined ? "" : `、${r.data.ms.toLocaleString("zh-CN")} ms`),
          fail: null,
        })
        // 采完立刻刷新这两块：不刷的话页面上显示的仍是采之前那一轮，
        // 而"点了没反应"与"采了但没变"在页面上长得一样。
        loadStatus()
        loadRing()
      } else {
        setOnce({ busy: false, msg: null, fail: r })
      }
    })
  }

  /** `running` 的三态。★`undefined` 与 `false` 是两句不同的话。 */
  const runningText = st?.running === true ? "正在跑"
    : st?.running === false ? "停了（服务端说它没在跑）"
      : st ? "服务端没说（running 这一栏没给）" : "—"

  return (
    <Frame
      probe={probe} title="采集监视"
      sub="常驻采集器现在怎么样 · 最近一轮 · 覆盖市场 · 健康时间线"
      footExtra={<>这一页显示的是<b>采集管道自己的状态</b>，不是行情。管道挂了的时候，别的页上那些数就该打折看。</>}
    >
      {DEMO ? <DemoBanner what="这一页显示的是本地编的一份采集器状态与快照索引，采集器并没有真的在跑。" /> : null}

      <Panel
        id="statusPanel" title="采集器"
        note={<>通道 <code className="tnum">{CHANNEL.collectStatus}</code></>}
        right={
          <div className="flex items-center gap-2">
            <button
              type="button" onClick={runOnce} disabled={once.busy}
              className="rounded border border-[var(--accent)] bg-[var(--card)] px-2 py-1 text-xs font-semibold text-[var(--accent)] disabled:opacity-40"
            >
              {once.busy ? "采集中…" : "采一轮"}
            </button>
            <button
              type="button" onClick={() => { loadStatus(); loadRing() }} disabled={stLoading}
              className="rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1 text-xs text-[var(--ink-2)] disabled:opacity-40"
            >
              刷新
            </button>
          </div>
        }
      >
        {stFail ? (
          <Degraded channel={CHANNEL.collectStatus} fail={stFail} what="采集器状态" />
        ) : stLoading && !st ? (
          <Loading what="正在取采集器状态" />
        ) : st ? (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="在跑吗" value={<span className="text-sm">{runningText}</span>} k="running"
                    tone={st.running === true ? "plain" : st.running === false ? "flat" : "flat"} />
              <Stat label="最近一次快照" value={<span className="text-sm">{st.last_ts || "—"}</span>} k="lastTs" />
              <Stat label="下一次" value={<span className="text-sm">{st.next_ts || "—"}</span>} k="nextTs"
                    sub="服务端说的时间，前端不推算" />
              <Stat label="累计轮数" value={st.ticks === null || st.ticks === undefined ? "—" : st.ticks.toLocaleString("zh-CN")}
                    k="ticks" />
              <Stat label="最近一轮行数" value={st.rows === null || st.rows === undefined ? "—" : st.rows.toLocaleString("zh-CN")}
                    k="rows" />
              <Stat label="覆盖市场" value={<span className="text-sm">{(st.markets ?? []).join(" / ") || "—"}</span>}
                    k="markets" />
            </div>

            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Well>
                <div className="text-[10.5px] text-[var(--ink-3)]">最近一轮（服务端给的 <code>last</code>）</div>
                {st.last ? (
                  <div className="mt-0.5 text-xs text-[var(--ink-2)]">
                    时刻 <span className="tnum">{st.last.ts || "—"}</span>
                    <span className="mx-2 opacity-40">·</span>市场 <span className="tnum">{st.last.market || "—"}</span>
                    <span className="mx-2 opacity-40">·</span>行数 <V v={st.last.rows} digits={0} />
                    <span className="mx-2 opacity-40">·</span>耗时 <V v={st.last.ms} digits={0} suffix=" ms" />
                  </div>
                ) : <div className="mt-0.5 text-xs text-[var(--ink-3)]">服务端没给 <code>last</code>。</div>}
              </Well>
              <Well>
                {/* ★落盘路径**要显示**：用户有资格知道这个站往磁盘上写了什么、写在哪儿
                    （同 `/ledger` 那条纪律）。 */}
                <div className="text-[10.5px] text-[var(--ink-3)]">落盘位置（只追加的事件日志在这个目录下）</div>
                <div className="mt-0.5 break-all text-xs text-[var(--ink-2)]">
                  {st.path || "服务端没说（path 这一栏没给）"}
                </div>
              </Well>
            </div>

            {st.err ? (
              <div className="mt-2 rounded border border-[var(--warn-rule)] bg-[var(--warn-bg)] p-2 text-xs text-[var(--warn-ink)]"
                   data-k="statusErr">
                <b>采集器这一轮报了个错</b>：{st.err}
              </div>
            ) : null}

            {once.fail ? (
              <div className="mt-2"><Degraded channel={CHANNEL.collectOnce} fail={once.fail} what="手动采一轮" /></div>
            ) : null}
            {once.msg ? (
              <div className="mt-2 rounded border border-[var(--rule)] bg-[var(--paper)] p-2 text-xs text-[var(--ink-2)]"
                   data-k="onceMsg" role="status">
                {once.msg}
              </div>
            ) : null}
          </>
        ) : (
          <Empty what="没有采集器状态" />
        )}
      </Panel>

      <Panel id="sourcesPanel" title="最近一轮的每个源" note="哪个源、采到多少行、用了多少毫秒、报了什么错">
        {st?.sources?.length ? (
          <>
            <SourceSummary sources={st.sources} />
            <div className="mt-2"><SourceTable sources={st.sources} /></div>
          </>
        ) : st?.health?.sources?.length ? (
          <>
            <SourceSummary sources={st.health.sources} />
            <div className="mt-2"><SourceTable sources={st.health.sources} /></div>
          </>
        ) : (
          <Empty what="这一轮没有源级的统计"
                 hint={<>服务端给的 <code>sources</code> 与 <code>health.sources</code> 都是空的。没有这一栏，「采集器在跑」这个结论就没有证据。</>} />
        )}
      </Panel>

      <Panel
        id="timelinePanel" title="健康时间线"
        note={<>{CHANNEL.collectRing} · 环形缓冲里的最近 {ring.length} 轮</>}
      >
        {ringFail ? (
          /* ★`ring` 挂掉**只坏这一块**：上面状态卡里的数是另一条通道给的，
             一起变成错误页的话，"是哪条通道挂了"就看不出来了。 */
          <Degraded channel={CHANNEL.collectRing} fail={ringFail} what="快照索引（健康时间线）" />
        ) : ringLoading && !ring.length ? (
          <Loading what="正在取快照索引" />
        ) : (
          <RingTimeline probe={probe} items={ring} />
        )}
      </Panel>

      {ring.length ? (
        <Panel id="ringPanel" title="快照索引" note="一次快照一行 —— 时间线的原始数据">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[620px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-[var(--rule)] text-xs text-[var(--ink-3)]">
                  <Th>时刻</Th><Th>市场</Th><Th right>行数</Th><Th right>耗时</Th><Th>结果</Th><Th>错误</Th>
                </tr>
              </thead>
              <tbody>
                {ring.map((r, i) => (
                  <tr key={`${r.ts}-${i}`} data-k="ring" data-ok={r.ok === false ? "0" : "1"}
                      className={r.ok === false ? "bg-[var(--warn-bg)]" : undefined}>
                    <Td><span className="tnum">{r.ts}</span></Td>
                    <Td>{r.market || "—"}</Td>
                    <Td right><V v={r.rows} digits={0} /></Td>
                    <Td right>{r.ms === null || r.ms === undefined ? "—" : `${r.ms.toLocaleString("zh-CN")} ms`}</Td>
                    <Td data-k="ok">{r.ok === undefined ? "没报" : r.ok ? "成功" : "失败"}</Td>
                    <Td className="min-w-[220px]">{r.err || "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}

      <Panel id="notDoPanel" title="这一页不做什么">
        <Well>
          <ul className="list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
            <li>不显示任何行情、不给方向、不算关联。它只回答"管道现在怎么样"。</li>
            <li>不推算"下一次采集在几点"之外的任何时间：<code>next_ts</code> 是服务端给的，
              前端不看表、不用定时器猜。</li>
            <li>「采一轮」是<b>会改服务端状态</b>的动作（往采集目录里追加一条记录），
              所以它的回执必须显示出来 —— 点了什么都不发生是最坏的一种反馈。</li>
          </ul>
        </Well>
      </Panel>
    </Frame>
  )
}

export default Collect
