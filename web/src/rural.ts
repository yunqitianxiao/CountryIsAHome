/**
 * 「县乡村」场景入口卡（主页 `#ruralCard`）的数据层。
 *
 * ★词表**只有一个真源**：仓库根的 `graph_data.json`。这张卡**不硬编码词名** ——
 *   硬编码等于在真源之外再造一份，词表一改（255 词这一版本身改过好几轮）卡里那份
 *   必然静默漂移，而页面上看不出任何异常。`graph_data.json` 在服务端白名单里
 *   （`app.py` 的 `WHITELIST`），可以直接 GET。前端**不需要**新接口。
 *
 * ★两个入口：`loadRural()` 是用户那条路（异步 fetch），`loadRuralSync()` 是探针那条路
 *   （**同步 XHR**）。照 `dash.ts:1-12` 的先例，那里把理由写透了，这里只重复结论：
 *   渲染层判据全部押在"几何在同一个 tick 里就绪"上，异步取回来再渲染的卡，
 *   同步探针量到的是一张**空卡**（写着"正在取…"）。那不是判据变红，是判据**消失**
 *   —— 而且消失得最难看的正是这一张：空卡不会横向溢出，于是"33 个词会不会把卡
 *   撑破"这条最容易翻车的判据，结构上永远量不到真实的那一版。
 *
 * ★分组按 `cat`（O/P）而不是按 `fam`：这两类**同属色族 6**（"家庭与县域"），
 *   按色族分会被并成一组，卡上就分不出"家里的账"和"县里的事"了。
 */

/** `graph_data.json` 里 term 节点我们用得到的字段（只声明读的那几个）。 */
type GNode = { id: string; kind: string; cat?: string; catName?: string }

export type RuralGroup = {
  /** 二级类代码，`O` / `P` */
  code: string
  /** 二级类中文名，取自词表真源（`O` = 家庭财务场景、`P` = 县域与乡村） */
  name: string
  terms: string[]
}

/** 词表里属于「县乡村」的那两类。★与 `graph_data.json` 的 `cats` 一一对应。 */
export const RURAL_CATS = ["O", "P"] as const

/** 节点表 → 按二级类分好组的词名。类的**顺序和名字**都跟着词表走，不在这里写死。 */
export function splitRural(nodes?: GNode[]): RuralGroup[] {
  const out: RuralGroup[] = []
  for (const code of RURAL_CATS) {
    const hit = (nodes ?? []).filter((n) => n.kind === "term" && n.cat === code)
    // ★一个词都没有时**不塞一个空组**：空组会在卡上渲染出一个只有标题的空框，
    //   读起来像"这一类坏了"。真空是"这一类确实没词"，由调用方决定怎么讲。
    if (hit.length) out.push({ code, name: hit[0].catName ?? code, terms: hit.map((n) => n.id) })
  }
  return out
}

/** 用户那条路：异步。取不到就抛，由调用方接住并**说清是取不到**（不是"没有这类词"）。 */
export async function loadRural(): Promise<RuralGroup[]> {
  const r = await fetch("graph_data.json")
  if (!r.ok) throw new Error(`词表 HTTP ${r.status}`)
  return splitRural((await r.json())?.nodes)
}

/** 探针那条路：同步。★`null` = **取不到**，与 `[]`（取到了但这一类没词）是两件事。 */
export function loadRuralSync(): RuralGroup[] | null {
  try {
    const x = new XMLHttpRequest()
    x.open("GET", "graph_data.json", false)
    x.send()
    if (x.status !== 200) return null
    return splitRural(JSON.parse(x.responseText)?.nodes)
  } catch {
    return null
  }
}
