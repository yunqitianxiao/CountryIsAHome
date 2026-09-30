/**
 * `src/pcode.ts`（登记编码校验）+ `src/pages/Verify/Verify.tsx`（产品验真页）的自测。
 *
 * 跑法（在 `web/` 下）：
 *     node pcode_selftest.mjs              # 只跑判据
 *     node pcode_selftest.mjs --selftest   # 逐条注入，验每条判据**真的会红**
 *
 * ★★为什么这个文件必须存在，而且必须自带注入：
 *   这一页要对外说的三句话 —— **不代查、不落库、不参数化深链** ——
 *   全是"**没有做某件事**"这种形状的承诺。这种承诺最难守：它们绿，可能只是因为
 *   没人去查；哪天有人顺手加一句 `localStorage.setItem`，页面照常工作、照常好看，
 *   而"不落库"那句承诺从此是假的，**没有任何一条别的判据会红**。
 *   ⇒ 所以这里除了断言纯函数的行为，还有一组**读源码文本**的判据（不许出现
 *     `localStorage.` / `fetch(` / `XMLHttpRequest` …），并且每一条都配了注入。
 *
 * ★和 `assess_selftest.mjs` 一样，它**不是常驻判据**：`verify_agent.py` 不调它。
 *   那一格（把 `/verify` 接进整站那 69 条）是协调者的动作。但"判据配注入"这条
 *   纪律不因为是临时脚本就免掉 —— 免掉的那一刻，这些断言就只是装饰。
 *
 * ── 两条环境上的实话（都不是判据的事，是本机的事）──────────────────────
 * ★**为什么用 `tsc` 而不是 `assess_selftest.mjs` 那个 `rolldown`**：本机
 *   （Windows / node 22）`rolldown` 的原生二进制没装上
 *   （`Cannot find module './rolldown-binding.win32-x64-msvc.node'`），
 *   而 `.bin/rolldown` 又只是个一行路径的纯文本 shim ⇒ **ENOENT**。两条路都不通，
 *   那个脚本在本机跑不起来。那是**环境缺口**，不是判据红，所以我没有改它
 *   （它属于 `/assess` 那一页，不归这一轮动），而是这里换一条本机真能跑的路：
 *   `node_modules/typescript/bin/tsc`，两边平台都能跑。
 * ★**为什么注入不起子进程**：本机（Windows）`spawnSync(process.execPath, …)`
 *   直接报 **EBUSY**。所以 `--selftest` 是**同一个进程里**重新转译一份带注入的
 *   副本、再动态 import 它 —— 每次文件名都不同，不会撞上 ESM 的模块缓存。
 *
 * ★`--noResolve` 在这里是**正当的**，不是图省事：`pcode.ts` 有零个 import，
 *   而且下面恰恰有一条判据钉着这件事（"纯函数、零依赖"）。哪天有人给它加了 import，
 *   那条判据先红 —— 转译这条路会不会跟着坏，反而不用操心。
 */
import { execFileSync } from "node:child_process"
import { readFileSync, writeFileSync } from "node:fs"
import { basename, dirname, join } from "node:path"
import { tmpdir } from "node:os"
import { fileURLToPath, pathToFileURL } from "node:url"

const NODE = process.execPath
const here = dirname(fileURLToPath(import.meta.url))
const TSC = join(here, "node_modules/typescript/bin/tsc")
const SRC = join(here, "src/pcode.ts")
const PAGE = join(here, "src/pages/Verify/Verify.tsx")

const SELFTEST = process.argv.includes("--selftest")

// ── 故障注入表 ──────────────────────────────────────────────────────────
// ★每条 `want` 里的名字必须**逐字**等于下面 `t(...)` 的第一个参数：
//   对不上时这一组会被报成"注入没打到预期的判据"，而不是悄悄算通过。
const INJECTIONS = [
  {
    id: "re-any-letter",
    what: "首字母放宽成任意大写字母（B 开头也放过）",
    want: ["B 开头（不属于 C/Z）→ 不接受"],
    apply: (s) => s.replace("^[CZ]\\d{13,14}$", "^[A-Z]\\d{13,14}$"),
  },
  {
    id: "re-len-12",
    what: "长度下界放宽到 12 位",
    want: ["首字母 + 12 位数字（共 13 位）→ 不接受"],
    apply: (s) => s.replace("\\d{13,14}", "\\d{12,14}"),
  },
  {
    id: "no-upper",
    what: "归一化不再转大写（小写抄下来就判错）",
    want: ["小写 c 开头 → 归一化后照样接受"],
    apply: (s) => s.replace(".toUpperCase()", ""),
  },
  {
    id: "no-fullwidth",
    what: "全角转半角的偏移量改成 0（中文输入法抄下来的编码全判错）",
    want: ["全角字母数字 → 归一化后照样接受"],
    apply: (s) => s.replace("0xfee0", "0x0"),
  },
  {
    id: "kind-swap",
    what: "C / Z 的含义对调（页面上两类发行主体说反）",
    // ★只 `want` 前两条：`kindText` 那条由下面 `kindtext-swap` 单独守 ——
    //   实测过，这一组注入打不到它（`kindText` 用的是自己的字面量）。
    want: [
      "C 开头 → kind 是 bank（银行，含银行资管部）",
      "Z 开头 → kind 是 bankwm（银行理财子公司）",
    ],
    apply: (s) => s
      .replace('if (code.startsWith("C")) return "bank"', 'if (code.startsWith("C")) return "bankwm"')
      .replace('if (code.startsWith("Z")) return "bankwm"', 'if (code.startsWith("Z")) return "bank"'),
  },
  {
    id: "official-http",
    what: "官方地址降级成 http",
    want: ["两个官方地址都是 https"],
    apply: (s) => s.replace('OFFICIAL_PILU = "https://', 'OFFICIAL_PILU = "http://'),
  },
  {
    id: "official-host",
    what: "官方地址换成一个长得像的域名（钓鱼那一种坏法）",
    want: ["两个官方地址的 host 都是 chinawealth.com.cn"],
    apply: (s) => s.replace("xinxipilu.chinawealth.com.cn", "xinxipilu.chinawealth-cn.com"),
  },
  {
    // ★和 `kind-swap` 是**两种**坏法，别合并：`kindOf` 说反只影响"这个编码属于
    //   哪一类"，`kindText` 说错影响的是"页面上那句话"。第一条覆盖不到第二条 ——
    //   实测过：只注入 kind-swap 时，`kindText` 那条判据照样绿。
    id: "kindtext-swap",
    what: "kindText 里把 C 那一类的说明写错（写成理财子公司）",
    want: ["kindText：C 那句说「银行」，Z 那句说「理财子公司」"],
    apply: (s) => s.replace(
      'if (kind === "bank") return "C 开头：发行主体是银行（含银行资管部）"',
      'if (kind === "bank") return "C 开头：发行主体是理财子公司"'),
  },
  {
    id: "leak-storage",
    what: "在 pcode.ts 里偷偷读一次 localStorage（「不落库」那句承诺当场作废）",
    want: ["pcode.ts 不许碰存储 / 网络（不落库、不代查）"],
    apply: (s) => s + '\nexport const _leak = typeof localStorage !== "undefined" ? localStorage.getItem("pcode") : null\n',
  },
  {
    id: "empty-reason-merge",
    what: "把「还没输入」和「形状不对」合成同一句判词",
    want: ["没输入 → reason 是「还没输入编码」，不是「不像登记编码」"],
    apply: (s) => s.replace('reason: "还没输入编码"', 'reason: "不像登记编码"'),
  },
]

// ── 转译 + 装载 ────────────────────────────────────────────────────────
/** 把一份（可能是**改过的副本**）TS 转译成 ESM 并 import 回来。 */
async function load(entry, tag) {
  const out = join(tmpdir(), "licai_pcode_out_" + tag)
  execFileSync(NODE, [TSC, entry, "--outDir", out, "--module", "esnext",
    "--target", "es2022", "--noResolve", "--skipLibCheck"], {
    cwd: here, stdio: ["ignore", "ignore", "inherit"],
  })
  // ★必须是**动态** import()：静态 import 在本文件开始执行之前就要解析模块。
  return import(pathToFileURL(join(out, basename(entry).replace(/\.ts$/, ".js"))).href)
}

/** 注入用的副本：改的是 **tmp 里的副本**，原文件一个字节都不动。 */
function injectedCopy(inj) {
  const before = readFileSync(SRC, "utf8")
  const after = inj.apply(before)
  // ★改不动必须**报出来**，不能继续跑：改不动的注入会给出一整屏"全绿"，
  //   那不是"这条注入安全"，那是"这条注入根本没发生"。
  if (after === before) throw new Error("注入没匹配上（判据会假绿）：" + inj.id)
  const p = join(tmpdir(), "licai_pcode_" + inj.id + ".ts")
  writeFileSync(p, after)
  return p
}

// ── 判据 ───────────────────────────────────────────────────────────────
let n = 0
let reds = []
let quiet = false
/** 分节标题。★走 `console.log` 而不是自己存一份：注入那一轮是把 `console.log`
 *  整体换成 sink 来静音的，这里另开一个出口就会漏。 */
const say = (...a) => { if (!quiet) console.log(...a) }
const t = (name, cond, extra = "") => {
  n++
  if (cond) { if (!quiet) console.log("  ✓ " + name); return }
  reds.push(name)
  if (!quiet) console.log("  ✗ " + name + (extra ? "   ← 实际：" + extra : ""))
}

// ★注释里提到这些词**不算漏** —— 注释不执行，而这两个文件的注释恰恰要
//   大讲"为什么不落库"（那份解释本身就是承诺的一部分）。
//   ⇒ 所以先剥注释再测。★剥的时候不能把 URL 里的 `//` 当注释（"https://"）：
//     只剥前面不是冒号的 `//`。**不剥**的后果是判据假绿 —— 半句代码跟着 URL
//     后半截一起被抹掉，恰好把真正那次 `fetch(` 也抹了。
const stripComments = (s) => s
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/(^|[^:])\/\/[^\n]*/g, "$1")

// ★抓的是**调用**，不是"这个词出现过"：页面上明写着"编码不写 localStorage"
//   那句话里也含这个词 —— 那正是承诺被写出来的地方，判它红等于逼人删掉承诺。
//   ⇒ 所以每个词都要求后面跟着 `.` 或 `[`（属性访问）或 `(`（调用）。
const BANNED = /\b(localStorage|sessionStorage|indexedDB)\s*[.[]|XMLHttpRequest|sendBeacon|fetch\s*\(|import\s*\(/

/**
 * 全部判据。`M` 是被测模块的命名空间（注入时是改过的副本）。
 *
 * ★`entry` 是**被测的那一份源码的路径**，不是恒定的 `SRC`：下面那组"读源码文本"
 *   的判据必须读**同一份** —— 读 `SRC` 的话，`leak-storage` 那条注入（在副本末尾
 *   加一行 `localStorage.getItem`）永远打不到它，而它会一直绿着假装自己有效。
 */
function checks(M, entry) {
  const {
    PCODE_RE, OFFICIAL_HOME, OFFICIAL_PILU, SHAPE_OK_NOTE,
    checkCode, normalizeCode, kindOf, kindText, lenNote,
  } = M
  n = 0; reds = []

  console.log("── 形状：什么算一个登记编码 ──")
  t("PCODE_RE 是正则", PCODE_RE instanceof RegExp, String(PCODE_RE))
  for (const good of ["C1128826000131", "Z7003323000081", "C11288260001311", "Z70033230000812"]) {
    t(`${good} → 接受`, checkCode(good).ok, JSON.stringify(checkCode(good)))
  }
  t("小写 c 开头 → 归一化后照样接受", checkCode("c1128826000131").ok)
  t("带空格与连字符 → 归一化后照样接受", checkCode("C1128 8260-00131").ok)
  t("全角字母数字 → 归一化后照样接受", checkCode("Ｃ１１２８８２６０００１３１").ok)
  t("B 开头（不属于 C/Z）→ 不接受", !checkCode("B1128826000131").ok)
  t("首字母 + 12 位数字（共 13 位）→ 不接受", !checkCode("C112882600013").ok)
  t("首字母 + 15 位数字（共 16 位）→ 不接受", !checkCode("C112882600013111").ok)
  t("纯数字（没有首字母）→ 不接受", !checkCode("1128826000131").ok)
  t("夹杂汉字 → 不接受", !checkCode("C1128826000131号").ok)

  say("\n── 空与未输入：与「形状不对」是两种判词 ──")
  t("没输入 → reason 是「还没输入编码」，不是「不像登记编码」",
    checkCode("").reason === "还没输入编码", checkCode("").reason)
  t("没输入 → len 是 0、kind 是 null", checkCode("").len === 0 && checkCode("").kind === null)
  t("null / undefined 与空串同判（不抛异常）",
    checkCode(null).reason === "还没输入编码" && checkCode(undefined).reason === "还没输入编码")
  t("只有分隔符 → 归一化后是空 ⇒ 按没输入判", checkCode("  --  ").reason === "还没输入编码")
  t("形状不对时 reason 里点名了 C/Z 与位数",
    /C 或 Z/.test(checkCode("B1").reason) && /13 或 14/.test(checkCode("B1").reason),
    checkCode("B1").reason)
  t("形状对时 reason 是 SHAPE_OK_NOTE（跟着说「不等于真的」）",
    checkCode("C1128826000131").reason === SHAPE_OK_NOTE)
  t("SHAPE_OK_NOTE 里明写了「不等于」——少了它，形状合法会被读成产品是真的",
    /不等于/.test(SHAPE_OK_NOTE), SHAPE_OK_NOTE)

  say("\n── 归一化本身 ──")
  t("去空格/连字符/点/顿号", normalizeCode("C 11-28·826．000，131") === "C1128826000131",
    normalizeCode("C 11-28·826．000，131"))
  t("转大写", normalizeCode("z7003323000081") === "Z7003323000081")
  t("全角转半角", normalizeCode("Ｚ７００３３２３００００８１") === "Z7003323000081")
  t("非字符串输入不抛异常", normalizeCode(null) === "" && normalizeCode(123) === "123")

  say("\n── 首字母代表什么（说反了页面照样通顺，所以必须钉住）──")
  t("C 开头 → kind 是 bank（银行，含银行资管部）", kindOf("C1128826000131") === "bank")
  t("Z 开头 → kind 是 bankwm（银行理财子公司）", kindOf("Z7003323000081") === "bankwm")
  t("其它字母 → null（不猜）", kindOf("B1128826000131") === null)
  t("kindText：C 那句说「银行」，Z 那句说「理财子公司」",
    kindText("bank").includes("银行") && kindText("bankwm").includes("理财子公司"),
    kindText("bank") + " / " + kindText("bankwm"))
  t("kindText(null) 是空串（调用方据此整段不渲染）", kindText(null) === "")
  t("两种说法不是同一句（对调必红）", kindText("bank") !== kindText("bankwm"))
  t("lenNote 对 14 / 15 位都给得出话", lenNote(14) !== "" && lenNote(15) !== "")
  t("lenNote 对无关长度给空串", lenNote(13) === "" && lenNote(0) === "")

  say("\n── 官方地址（这一页唯一站外的落点，被换掉就是钓鱼）──")
  const hosts = [OFFICIAL_HOME, OFFICIAL_PILU].map((u) => {
    try { return new URL(u).host } catch { return "" }
  })
  t("两个官方地址都是 https",
    OFFICIAL_HOME.startsWith("https://") && OFFICIAL_PILU.startsWith("https://"),
    OFFICIAL_HOME + " / " + OFFICIAL_PILU)
  t("两个官方地址的 host 都是 chinawealth.com.cn",
    hosts.every((h) => h === "www.chinawealth.com.cn" || h === "xinxipilu.chinawealth.com.cn"),
    hosts.join(" / "))
  t("两个地址不是同一个（门户首页 ≠ 信息披露平台）", OFFICIAL_HOME !== OFFICIAL_PILU)

  say("\n── 「不落库 / 不代查」：读源码文本 ──")
  // ★★这一组是本文件存在的理由：上面那些断言量的都是**行为**，而"没有做某件事"
  //   这种形状的性质，行为测不出来 —— 加了 `localStorage` 之后所有行为判据照样全绿。
  const srcText = stripComments(readFileSync(entry, "utf8"))
  // ★页面这一份**不参与注入**（现在没有一条注入改它）：`want` 里那些"页面上明写着…"
  //   的判据因此是**没有注入**的 —— 按本仓库的规矩，它们要在下面写明豁免理由：
  //   它们量的是"页面有没有把承诺写出来"，而删掉那句话的注入会让页面文案缺一段，
  //   这种坏法由人工走查比由注入守更直白（注入能证明的只是"判据读到了那段字"）。
  const pageText = stripComments(readFileSync(PAGE, "utf8"))
  t("pcode.ts 不许碰存储 / 网络（不落库、不代查）", !BANNED.test(srcText),
    (srcText.match(BANNED) || [""])[0])
  t("Verify.tsx 不许碰存储 / 网络（编码不进 localStorage、不发请求）", !BANNED.test(pageText),
    (pageText.match(BANNED) || [""])[0])
  t("pcode.ts 不 import 任何东西（纯函数，零依赖 ⇒ 判定不受外部状态影响）",
    !/^\s*import\s/m.test(srcText))
  // ★这一组守的是"页面必须把不做什么说出来"：话删了，行为没变，但承诺没了。
  for (const must of ["不代查", "不落库", "不参数化深链", "不推荐、不比价、不排序"]) {
    t(`页面上明写着「${must}」`, pageText.includes(must))
  }
  t("页面上给了官方平台的真地址（不是一句「请自行查询」）",
    pageText.includes("OFFICIAL_PILU") && pageText.includes("OFFICIAL_HOME"))

  return { n, reds: [...reds] }
}

// ── 主流程 ─────────────────────────────────────────────────────────────
const base = checks(await load(SRC, "base"), SRC)
console.log(`\n跑 ${base.n} 条，红 ${base.reds.length} 条`)
if (base.reds.length) console.log("红的：" + base.reds.join(" / "))

if (SELFTEST) {
  console.log("\n══ 故障注入：每条都必须把它该打红的那几条打红 ══")
  let badInject = 0

  // ★对照组：不注入时必须**全绿**。没有它，一条本来就红的判据会让下面每一组
  //   注入都"通过"（它红它的，跟注入没关系）—— 那是注入永远抓不到的坏法。
  console.log(`\n对照组（不注入）：红 ${base.reds.length} 条`
    + (base.reds.length ? " ← 判据本来就红，下面的注入结果无意义" : " ✓"))
  if (base.reds.length) badInject++

  // ★静音跑：注入那一轮里 `checks()` 自己会打印几十行（每条判据一行），
  //   不静音的话人根本看不见下面那一组总结。做法是把 `console.log` 换成 sink，
  //   而不是给每个打印点加 `if (!quiet)` —— 后者早晚漏一处。
  const realLog = console.log
  const rows = []
  quiet = true
  console.log = () => {}
  for (const inj of INJECTIONS) {
    try {
      const p = injectedCopy(inj)
      rows.push({ inj, got: checks(await load(p, inj.id), p).reds })
    } catch (e) {
      rows.push({ inj, err: e.message })
    }
  }
  console.log = realLog
  quiet = false

  for (const r of rows) {
    const { inj } = r
    if (r.err) {
      badInject++
      console.log(`\n✗ ${inj.id}（${inj.what}）→ ${r.err}`)
      continue
    }
    const missing = inj.want.filter((w) => !r.got.includes(w))
    const ok = missing.length === 0 && r.got.length > 0
    if (!ok) badInject++
    console.log(`\n${ok ? "✓" : "✗"} ${inj.id}（${inj.what}）→ 红 ${r.got.length} 条`)
    if (!r.got.length) console.log("    ← 一条都没红：这条注入打不到任何判据")
    else if (missing.length) console.log("    ← 预期要红但没红：" + missing.join(" / "))
    for (const w of inj.want) console.log(`    ${r.got.includes(w) ? "✓ 如预期变红" : "✗ 没红"}：${w}`)
  }
  console.log(`\n${INJECTIONS.length} 组注入，${badInject} 组没按预期打红`)
  process.exit(badInject ? 1 : 0)
}

process.exit(base.reds.length ? 1 : 0)
