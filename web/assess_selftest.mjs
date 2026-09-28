/**
 * `src/assess.ts` 的自测 —— 打分是这个模块的核心，而它的错法**症状隐蔽**：
 * 分档线偏一位、自述高低的 gap 方向反过来、未知 label 不返回 null 而是猜一个分，
 * 这三种在页面上都长得完全正常（两句文案都通顺、等级也显示得出来）。
 *
 * 跑法（在 `web/` 下，一步）：
 *     node assess_selftest.mjs
 *
 * ★它**不是常驻判据**：没有任何 CI、也没有 `verify_agent.py` 会调它，跑不跑全靠人。
 *   `verify_agent.py` 与 `src/probe.ts` 都不归做 `/assess` 的人动 ⇒
 *   **`/assess` 那一页目前没有任何 HTTP 判据守着**（服务端路由被摘掉、
 *   主页卡的 href 被改坏，都不会有红灯）。这个脚本是在那个缺口补上之前，
 *   唯一能重跑的实测证据。把它接进正式判据是协调者的动作。
 *
 * ★它只测**纯函数**，不碰 DOM、不碰 localStorage ⇒ 页面渲染得对不对它答不了。
 *   那一格由主页那组判据（`_c_home` 的 `cardRects` / `overflowX`）和人工走查覆盖。
 *
 * ★这些断言**被注入验证过能红**（8 组：分档线下移 / 分档线倒挂 / 未知 label 不再
 *   返回 null / 未答的闸拿掉 / gap 方向反 / 总分多加 1 / 给第 6 题补一个 5 分档 /
 *   C1 档返回最高档），每一组都打红了预期的那几条。注入做法与逐组结果写在交付说明里。
 */
import { execFileSync } from "node:child_process"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const bundle = join(tmpdir(), "licai_assess_bundle.mjs")

// 先用项目自己的打包器（rolldown，vite 用的就是它）把 TS 打成 ESM。
execFileSync(
  join(here, "node_modules/.bin/rolldown"),
  [join(here, "src/assess.ts"), "-o", bundle, "-f", "esm", "-p", "node"],
  { cwd: here, stdio: ["ignore", "ignore", "inherit"] },
)

// ★必须是**动态** import()：静态 import 在本文件开始执行之前就要解析模块，
//   而那时上面那一步还没跑、文件还不存在。这个坑本仓库踩过（SSR 垫片那次）。
const {
  ASSESS_KEY, DIMS, LEVELS, MAX_TOTAL, MIN_TOTAL, QUESTIONS,
  compareSelfReport, grade, levelOf, missing, sinceText,
} = await import(bundle)

let bad = 0, n = 0
const t = (name, cond, extra = "") => {
  n++
  if (cond) console.log("  ✓ " + name)
  else { bad++; console.log("  ✗ " + name + (extra ? "   ← 实际：" + extra : "")) }
}

console.log("── 题库本身的形状 ──")
t("题数是 10（不是空数组 —— 空数组会让下面每条 every 都恒真）",
  QUESTIONS.length === 10, String(QUESTIONS.length))
t("题 id 唯一",
  QUESTIONS.length > 0 && new Set(QUESTIONS.map((q) => q.id)).size === QUESTIONS.length)
t("每题 4–5 个选项",
  QUESTIONS.length > 0 && QUESTIONS.every((q) => q.opts.length >= 4 && q.opts.length <= 5),
  QUESTIONS.map((q) => q.opts.length).join(","))
t("题内选项 label 唯一（grade 靠它反查分值）",
  QUESTIONS.length > 0
  && QUESTIONS.every((q) => new Set(q.opts.map((o) => o.label)).size === q.opts.length))
t("每问的选项分值从 1 起（最小值恰好是 1）",
  QUESTIONS.length > 0 && QUESTIONS.every((q) => Math.min(...q.opts.map((o) => o.score)) === 1))
t("分值都在 1–5",
  QUESTIONS.length > 0 && QUESTIONS.every((q) => q.opts.every((o) => o.score >= 1 && o.score <= 5)))
t("每一问都有 dim，且 dim 都在 DIMS 里",
  QUESTIONS.length > 0 && QUESTIONS.every((q) => q.dim && DIMS.includes(q.dim)))
t("DIMS 恰好 4 个、无重复、且每维至少一问",
  DIMS.length === 4 && new Set(DIMS).size === 4
  && DIMS.every((d) => QUESTIONS.filter((q) => q.dim === d).length > 0),
  DIMS.join(" / "))
t("MIN_TOTAL 是 10", MIN_TOTAL === 10, String(MIN_TOTAL))
// ★49 不是 50：第 6 题（买过哪些品种）只有 4 档、最高 4 分 —— 因为"没买过"和
//   "只买过存款"其实分不开，硬拆一档是在编分辨力。这是**设计决定**，不是笔误，
//   所以把它钉在这里：谁哪天把第 6 题补齐成 5 档，这条会红，那时该重想的
//   不是这条断言，是 C1–C5 的分档线（已实测：补一档这条就红，出 50）。
t("MAX_TOTAL 是 49（第 6 题只有 4 档）", MAX_TOTAL === 49, String(MAX_TOTAL))

console.log("\n── grade：打分的唯一出口 ──")
const pick = (q, better) => q.opts.reduce((a, b) => (better(a, b) ? a : b))
const lows = QUESTIONS.map((q) => pick(q, (a, b) => a.score <= b.score).label)
const highs = QUESTIONS.map((q) => pick(q, (a, b) => a.score >= b.score).label)

const gl = grade(lows), gh = grade(highs)
t("全选最低分 → total 10", !!gl && gl.total === 10, JSON.stringify(gl))
t("全选最低分 → C1 保守型", !!gl && gl.level.code === "C1", gl && gl.level.code)
t("全选最高分 → total == MAX_TOTAL(49)", !!gh && gh.total === MAX_TOTAL, gh && String(gh.total))
t("全选最高分 → C5 进取型", !!gh && gh.level.code === "C5", gh && gh.level.code)
t("有一题没答 → null（不猜）",
  grade(QUESTIONS.map((_, i) => (i === 3 ? null : lows[i]))) === null)
t("全没答 → null", grade(QUESTIONS.map(() => null)) === null)
t("答案里有个 label 不在当前题库里 → null（题库改过，不猜）",
  grade(QUESTIONS.map((_, i) => (i === 0 ? "一个不存在的选项" : lows[i]))) === null)
t("答案数组比题库短 → null（不是把 undefined 当 0 分）",
  grade(lows.slice(0, 5)) === null)
t("答案数组比题库长 → 只看前 N 个，仍能算",
  !!grade([...lows, "多余的"]), JSON.stringify(grade([...lows, "多余的"])))

// 逐题改一档，总分只该动那一档的差 —— 证明没有哪一题被算了两次
const mod = lows.slice()
const q0 = QUESTIONS[0]
const base0 = q0.opts.find((x) => x.label === mod[0]).score
const second = q0.opts.find((o) => o.label !== mod[0] && o.score === base0 + 1)
if (second) {
  mod[0] = second.label
  const g1 = grade(mod)
  t("只把第 1 题提高一档 → 总分恰好 +1",
    !!gl && !!g1 && g1.total === gl.total + 1, gl && g1 && `${gl.total} → ${g1.total}`)
} else {
  bad++; n++
  console.log("  ✗ 第 1 题没有「比最低分高一档」的选项 —— 拿不到对照组，这条没跑")
}

console.log("\n── missing ──")
t("全空 → 10 道都没答", missing(QUESTIONS.map(() => null)).length === 10)
const partial = [...lows.slice(0, 3), ...Array(QUESTIONS.length - 3).fill(null)]
t("只答前 3 题 → 缺的是第 4..10 题（1 基）",
  JSON.stringify(missing(partial)) === JSON.stringify([3, 4, 5, 6, 7, 8, 9]),
  JSON.stringify(missing(partial)))
t("全答完 → missing 为空数组", missing(lows).length === 0)
t("missing 与 grade 一致：missing 空 ⟺ grade 非 null",
  (missing(lows).length === 0) === (grade(lows) !== null)
  && (missing(partial).length === 0) === (grade(partial) !== null))

console.log("\n── 分档边界（一条一个具体数，不是「都在范围内」）──")
for (const [v, code] of [[10, "C1"], [18, "C1"], [19, "C2"], [26, "C2"], [27, "C3"],
                         [34, "C3"], [35, "C4"], [42, "C4"], [43, "C5"], [49, "C5"]]) {
  t(`levelOf(${v}) === ${code}`, levelOf(v).code === code, levelOf(v).code)
}
t("越界值不崩：levelOf(50) 仍落最后一档", levelOf(50).code === "C5", levelOf(50).code)
t("越界值不崩：levelOf(0) 仍落第一档", levelOf(0).code === "C1", levelOf(0).code)

const outside = [], seenIdx = []
for (let v = MIN_TOTAL; v <= MAX_TOTAL; v++) {
  const i = LEVELS.findIndex((l) => l.code === levelOf(v).code)
  if (i < 0) outside.push(v)
  seenIdx.push(i)
}
t(`总分 ${MIN_TOTAL}..${MAX_TOTAL} 每个整数都落在某一档（没有掉在档外）`,
  outside.length === 0, outside.join(","))
t("总分升高时档位只升不降（分档线没有倒挂）",
  seenIdx.every((v, i) => i === 0 || v >= seenIdx[i - 1]))
t("五档**都**被总分区间真的走到过（没有一档是死的）",
  new Set(seenIdx).size === 5, [...new Set(seenIdx)].join(","))

console.log("\n── compareSelfReport（与自述偏好并置）──")
const c3 = LEVELS[2]
t("LEVELS[2] 是 C3", c3.code === "C3", c3.code)
t("没填过（空串）→ null ⇒ 调用方整段不渲染", compareSelfReport("", c3) === null)
t("填了个不在 RISKS 里的字 → null", compareSelfReport("随便写的", c3) === null)
t("自述与等级相同 → gap 0", compareSelfReport("平衡", c3)?.gap === 0,
  JSON.stringify(compareSelfReport("平衡", c3)))
// ★方向是这一组里最容易错、也最难看出错的一条：反了的话页面上
//   「你自述得比测出来高」会说反，而两种说法都通顺。
t("自述比等级高（激进 vs C3）→ gap > 0", (compareSelfReport("激进", c3)?.gap ?? 0) > 0)
t("自述比等级低（保守 vs C3）→ gap < 0", (compareSelfReport("保守", c3)?.gap ?? 0) < 0)
t("自述最高(激进=RISKS[4]) vs 最低档 C1 → gap 恰好 4",
  compareSelfReport("激进", LEVELS[0])?.gap === 4)
t("自述最低(保守=RISKS[0]) vs 最高档 C5 → gap 恰好 -4",
  compareSelfReport("保守", LEVELS[4])?.gap === -4)
const rt = compareSelfReport("激进", c3)
t("对照文案里同时出现了自述值和测出来的档号",
  !!rt && rt.text.includes("激进") && rt.text.includes("C3"))

console.log("\n── sinceText ──")
const D = 86400000, A = 1000 * D
t("同一时刻 → 今天", sinceText(A, A + 3600 * 1000) === "今天")
t("隔一天 → 昨天", sinceText(A, A + D) === "昨天")
t("隔 5 天 → 5 天前", sinceText(A, A + 5 * D) === "5 天前", sinceText(A, A + 5 * D))
t("隔 95 天 → 3 个月前", sinceText(A, A + 95 * D) === "3 个月前", sinceText(A, A + 95 * D))
t("隔 800 天 → 2 年前", sinceText(A, A + 800 * D) === "2 年前", sinceText(A, A + 800 * D))
t("时刻在未来（时钟歪了）→ 不显示负数，退回「今天」",
  sinceText(A + D, A) === "今天", sinceText(A + D, A))

console.log("\n── 存储键 ──")
t("键名是 licai-assess", ASSESS_KEY === "licai-assess", ASSESS_KEY)

console.log(`\n跑 ${n} 条，红 ${bad} 条`)
process.exit(bad ? 1 : 0)
