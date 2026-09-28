/**
 * **采集健康度那张表**。`/linkage` 和 `/collect` 两页共用这一份。
 *
 * ★为什么必须共用：这一批的两页都要回答"哪个源、采到多少行、用了多少毫秒、
 *   报了什么错"，而它就是这五页里**最该一模一样**的一块 —— 它是拿来跟
 *   服务端日志对照着看的（`err` 那一栏要求照抄原句）。两处各写一遍的话，
 *   同一个源的同一个错误会显示成两句不同的话，而其中一句迟早会漂。
 *
 * ★**失败的源排最前**：一张健康表的价值全在"哪一行是红的"。
 * ★`null` → 「—」，**不是 0**："这个源没报数"和"这个源报了 0 行"是两件事
 *   （`api2.ts: SourceHealth` 顶上那段）。
 */
import * as React from "react"
import type { SourceHealth } from "../api2"
import { Th, Td, V } from "./State"

export function SourceTable({ sources, dense = false }: {
  sources: SourceHealth[]
  /** 窄版：挡掉"错误"那一列（采集页的时间线下面用），只留状态色。 */
  dense?: boolean
}) {
  if (!sources.length) {
    return <p className="text-xs text-[var(--ink-3)]">这一轮没有源的记录（<code>sources</code> 是空的）。</p>
  }
  const ranked = [...sources].sort((a, b) => Number(a.ok) - Number(b.ok))
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm" style={{ minWidth: dense ? 380 : 560 }}>
        <thead>
          <tr className="border-b border-[var(--rule)] text-xs text-[var(--ink-3)]">
            <Th>数据源</Th><Th right>行数</Th><Th right>耗时</Th><Th>状态</Th>
            {dense ? null : <Th>这一轮的错误</Th>}
          </tr>
        </thead>
        <tbody>
          {ranked.map((s) => (
            <tr key={s.id} data-k="source" data-sid={s.id} data-ok={s.ok ? "1" : "0"}
                className={s.ok ? undefined : "bg-[var(--warn-bg)]"}>
              <Td><code>{s.id}</code></Td>
              <Td right k="n"><V v={s.n} digits={0} /></Td>
              <Td right k="ms">{s.ms === null ? "—" : `${s.ms.toLocaleString("zh-CN")} ms`}</Td>
              <Td>
                <span className={s.ok ? "text-[var(--ink-2)]" : "font-semibold text-[var(--warn-ink)]"}>
                  {s.ok ? "成功" : "失败"}
                </span>
              </Td>
              {dense ? null : (
                <Td className="min-w-[240px]" k="err">
                  {s.err
                    ? <span className="text-[var(--warn-ink)]">{s.err}</span>
                    : <span className="text-[var(--ink-3)]">—</span>}
                </Td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 一句话总览：几个源成了、几个没成、失败的那几个叫什么。
 *  ★失败源**点名**，不说"部分源失败" —— 后者在页面上等于没说。 */
export function SourceSummary({ sources }: { sources: SourceHealth[] }) {
  const bad = sources.filter((s) => !s.ok)
  const okN = sources.length - bad.length
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs text-[var(--ink-3)]">
      <span>源：<b className="tnum text-[var(--ink-2)]">{okN}/{sources.length}</b></span>
      {bad.length ? (
        <span className="text-[var(--warn-ink)]">
          没成的：{bad.map((s) => s.id).join("、")}
        </span>
      ) : (
        <span>这一轮所有源都成了</span>
      )}
    </div>
  )
}
