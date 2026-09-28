/**
 * 杨柳青年画纹样（内联 SVG）。给「县乡村」那一组元素用的**插画语言**。
 *
 * ★★为什么是内联组件而不是 `.svg` 文件 —— 这个站有三重机制挡着，独立图片文件走不通：
 *   ① `web/public/` **不存在**，`vite.config.ts` 也没配 `publicDir`；
 *   ② `vite.config.ts` 的 `assetFileNames: 'app.[ext]'` 把构建期资源**统一改名**,
 *      ⇒ 放两个 svg 进去必然同名相撞；
 *   ③ `app.py` 的白名单（`SPA_ASSETS`/`WHITELIST`）只放行扁平名，别的名字一律 404。
 *   内联进 `app.js` 则不需要新增任何静态文件、不需要碰白名单、不需要改构建配置。
 *
 * ★★三条实现纪律：
 *   ① **上色一律走 `var(--nh-*)` 令牌，不吃 `currentColor`** —— 与 `Spark.tsx`、
 *      `agentui/charts.tsx`、`agentui/AgentBoard.tsx` 三处既有手写 SVG 一致。
 *      颜色只在 CSS 里给：写进 `fill` 属性的话，两套主题就得在 JS 里各写一遍，漏一套没人看得见。
 *   ② **宽度自适应 + `maxWidth:100%` + `height:auto`** —— 主页有一条判据在 **420px 宽**
 *      量 `documentElement.scrollWidth - clientWidth`，溢出超过 1px 就红。
 *      所以这里一律不用固定像素宽，也一律不写死不换行的横向结构。
 *   ③ **不给任何图形加 `id`** —— 同一个纹样在一页里可能出现多次（年画区 + 场景卡角标），
 *      带 `id` 的 `<pattern>`/`<mask>`/`<defs>` 会在第二个实例上抢到第一个的定义。
 *      所以回纹是**逐个单元画的**，不是 `<pattern>` 平铺的。
 *
 * ★技法：杨柳青是「半印半画」—— 墨线版印轮廓（`--nh-ink`），人工填色晕染（各颜料）。
 *   这里照着这个骨架来：**先墨线勾形，再平涂填色**，不追求写实，追求"一眼是年画"。
 *   年画颜料不随主题翻转（理由见 `index.css` 浅色块 `--nh-*` 那一段：
 *   它是"贴在深色墙上的一张宣纸"）。
 */

type Art = {
  /** 渲染高度（px）。宽度按 viewBox 比例自动推，永远不溢出。 */
  size?: number
  /** 无障碍名。★**给了就渲染成 `role="img"`**，不给则整个图对读屏软件隐藏 ——
   *  装饰性图形不给名字才是对的（一堆 `aria-label="云纹"` 是纯噪声）。 */
  label?: string
  className?: string
}

const V = { display: "block", height: "auto", maxWidth: "100%" } as const

function box(VW: number, VH: number, size: number) {
  return {
    viewBox: `0 0 ${VW} ${VH}`,
    style: { ...V, width: Math.round((size * VW) / VH) },
  }
}

/** `label` 有无 → 两种无障碍形态。照 `Spark.tsx:46` 那个二元分支写。 */
function acc(label?: string) {
  return { role: label ? ("img" as const) : undefined, "aria-label": label, "aria-hidden": label ? undefined : true }
}

/* ── 回纹（连续方折条带，年画的边框）────────────────────────────────────
   ★这个与别个不同：它是**通栏**的，宽度撑满容器、高度固定，所以走
   `preserveAspectRatio="none"`（单元被横向拉伸 —— 回纹本来就是均匀重复的，
   拉伸看不出来）。别拿它当图标使。 */
export function HuiWen({ n = 20, h = 14, className }: { n?: number; h?: number; className?: string }) {
  const d = (i: number) => {
    const x = i * h
    return `M${x + h * 0.14} ${h * 0.86} V${h * 0.14} H${x + h * 0.86} V${h * 0.6} H${x + h * 0.38} V${h * 0.38} H${x + h * 0.62}`
  }
  return (
    <svg viewBox={`0 0 ${n * h} ${h}`} preserveAspectRatio="none" aria-hidden="true" focusable="false"
         className={className} style={{ display: "block", width: "100%", height: h, maxWidth: "100%" }}>
      {Array.from({ length: n }, (_, i) => (
        <path key={i} d={d(i)} fill="none" stroke="var(--nh-ink)" strokeWidth={h * 0.1} strokeLinejoin="miter" />
      ))}
    </svg>
  )
}

/* ── 稻穗（用户点名的那一档金）──────────────────────────────────────── */
export function DaoSui({ size = 96, label, className }: Art) {
  // 谷粒沿穗轴左右交错：水滴形，左侧向左上、右侧向右上
  const grains: { x: number; y: number; a: number }[] = []
  for (let i = 0; i < 7; i++) {
    const y = 34 + i * 9
    const x = 30 - i * 0.35
    grains.push({ x: x - 5, y, a: -40 })
    grains.push({ x: x + 5, y: y + 4.5, a: 40 })
  }
  return (
    <svg {...box(60, 120, size)} {...acc(label)} className={className}>
      <path d="M30 118 C29 96 31 62 32 30" fill="none" stroke="var(--nh-ink)" strokeWidth="2.6" strokeLinecap="round" />
      {grains.map((g, i) => (
        <path key={i} d="M0 0 C5 -4.5 5 -12 0 -16.5 C-5 -12 -5 -4.5 0 0 Z"
              transform={`translate(${g.x} ${g.y}) rotate(${g.a})`}
              fill="var(--nh-gold)" stroke="var(--nh-ink)" strokeWidth="1.7" strokeLinejoin="round" />
      ))}
    </svg>
  )
}

/* ── 铜钱（★把年画和金融缝在一起的那个符号：外圆内方）──────────────────
   方孔用 `fillRule="evenodd"` 挖出来（**不是**拿一块底色盖上去 ——
   盖上去的话，这个纹样就只能在它出生的那个底色上用了）。 */
export function TongQian({ size = 44, label, className }: Art) {
  return (
    <svg {...box(40, 40, size)} {...acc(label)} className={className}>
      <path d="M20 2 a18 18 0 1 0 0.01 0 Z M13 13 h14 v14 h-14 Z"
            fillRule="evenodd" fill="var(--nh-gold)" stroke="var(--nh-ink)" strokeWidth="2.4" strokeLinejoin="round" />
      {/* 四出：方孔四角伸到外圆，年画铜钱的定式 */}
      <path d="M20 13 V2 M27 20 H38 M20 27 V38 M13 20 H2"
            stroke="var(--nh-ink)" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}

/* ── 坡屋顶民居 ────────────────────────────────────────────────────── */
export function WaFang({ size = 56, label, className }: Art) {
  return (
    <svg {...box(64, 56, size)} {...acc(label)} className={className}>
      <path d="M4 26 L32 6 L60 26 Z" fill="var(--nh-red)" stroke="var(--nh-ink)" strokeWidth="2.4" strokeLinejoin="round" />
      <rect x="12" y="26" width="40" height="26" fill="var(--nh-paper)" stroke="var(--nh-ink)" strokeWidth="2.4" />
      <rect x="27" y="38" width="10" height="14" fill="var(--nh-red)" stroke="var(--nh-ink)" strokeWidth="2" />
      <rect x="17" y="32" width="7" height="7" fill="var(--nh-blue)" stroke="var(--nh-ink)" strokeWidth="2" />
      <rect x="40" y="32" width="7" height="7" fill="var(--nh-blue)" stroke="var(--nh-ink)" strokeWidth="2" />
    </svg>
  )
}

/* ── 粮仓（圆顶仓 + 石基）──────────────────────────────────────────── */
export function LiangCang({ size = 56, label, className }: Art) {
  return (
    <svg {...box(64, 56, size)} {...acc(label)} className={className}>
      <path d="M10 46 V26 A22 16 0 0 1 54 26 V46 Z"
            fill="var(--nh-gold)" stroke="var(--nh-ink)" strokeWidth="2.4" strokeLinejoin="round" />
      <rect x="26" y="32" width="12" height="14" fill="var(--nh-paper)" stroke="var(--nh-ink)" strokeWidth="2" />
      <rect x="6" y="46" width="52" height="5" rx="2" fill="var(--nh-ink)" />
    </svg>
  )
}

/* ── 集市棚（承载"县"：县城里的那个摊位）────────────────────────────── */
export function JiShi({ size = 56, label, className }: Art) {
  return (
    <svg {...box(80, 56, size)} {...acc(label)} className={className}>
      <path d="M6 24 L20 8 H60 L74 24 Z" fill="var(--nh-red)" stroke="var(--nh-ink)" strokeWidth="2.4" strokeLinejoin="round" />
      {/* 棚布的条纹：用宣纸色压上去，读起来就是"一条条布" */}
      <path d="M24 9.5 L30 23 M38 9.5 L44 23 M52 9.5 L58 23" stroke="var(--nh-paper)" strokeWidth="4.5" />
      <rect x="14" y="24" width="52" height="7" fill="var(--nh-paper)" stroke="var(--nh-ink)" strokeWidth="2.2" />
      <path d="M19 31 V48 M61 31 V48" stroke="var(--nh-ink)" strokeWidth="3" strokeLinecap="round" />
      {/* 台面上的货：三个小箱子 */}
      <rect x="24" y="15" width="9" height="9" fill="var(--nh-green)" stroke="var(--nh-ink)" strokeWidth="1.8" />
      <rect x="36" y="17" width="8" height="7" fill="var(--nh-blue)" stroke="var(--nh-ink)" strokeWidth="1.8" />
      <rect x="47" y="15" width="9" height="9" fill="var(--nh-pink)" stroke="var(--nh-ink)" strokeWidth="1.8" />
    </svg>
  )
}

/* ── 祥云（如意云头）────────────────────────────────────────────────── */
export function XiangYun({ size = 40, label, className }: Art) {
  return (
    <svg {...box(60, 40, size)} {...acc(label)} className={className}>
      <path d="M8 31 C1.5 31 0.5 22 7 20 C5 12 13 6.5 19 11 C22 2.5 34 1.5 38 10 C46 5.5 55 11 53 19 C59.5 21 58.5 31 52 31 Z"
            fill="var(--nh-blue)" stroke="var(--nh-ink)" strokeWidth="2.4" strokeLinejoin="round" />
      {/* 云尾 */}
      <path d="M14 35 C22 32 38 32 46 35" fill="none" stroke="var(--nh-ink)" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

/* ── 缠枝莲（藤 + 叶 + 五瓣花）──────────────────────────────────────── */
export function ChanZhiLian({ size = 56, label, className }: Art) {
  return (
    <svg {...box(80, 56, size)} {...acc(label)} className={className}>
      <path d="M4 46 C18 46 18 30 32 30 C46 30 46 14 58 14"
            fill="none" stroke="var(--nh-green)" strokeWidth="3" strokeLinecap="round" />
      <ellipse cx="19" cy="38" rx="7" ry="4" transform="rotate(-34 19 38)"
               fill="var(--nh-green)" stroke="var(--nh-ink)" strokeWidth="1.8" />
      <ellipse cx="45" cy="22" rx="7" ry="4" transform="rotate(-34 45 22)"
               fill="var(--nh-green)" stroke="var(--nh-ink)" strokeWidth="1.8" />
      {[0, 72, 144, 216, 288].map((a) => (
        <ellipse key={a} cx="58" cy="7" rx="3.6" ry="7.5" transform={`rotate(${a} 58 14)`}
                 fill="var(--nh-pink)" stroke="var(--nh-ink)" strokeWidth="1.6" />
      ))}
      <circle cx="58" cy="14" r="3.2" fill="var(--nh-gold)" stroke="var(--nh-ink)" strokeWidth="1.6" />
    </svg>
  )
}
