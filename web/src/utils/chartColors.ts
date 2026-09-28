// Tremor chartColors —— ★已收编到站点 token 层（原版是 Tremor 的 raw 调色板）

/**
 * 一、原来这里错在哪
 *
 * 原版是 Tremor 默认那九档：blue / emerald / violet / amber / gray /
 * cyan / pink / lime / fuchsia，全是 `-500` 的裸色值。两件事不对：
 *  1. **violet / fuchsia / pink 是"AI 审美"的招牌配色**（模型默认就爱用紫），
 *     跟这个站的绿灰调子完全不搭。
 *  2. 更实质的是：这套调色板**绕过了站点语义**。这个站里红色是"涨"、
 *     绿色是"跌"（A 股习惯，跟欧美相反），而 `emerald-500` 和站点 `--down`
 *     是两个绿、`blue-500` 在这个站里根本没有对应语义。
 *
 * 二、这一版（2026-09-22）
 *
 * ★**每一档都指向 `index.css` 的 `--chart-1..6`**，不再直接指中间色号。
 *   为什么多这一层？因为"图表第 3 条线该是什么颜色"是个**设计决定**，
 *   而 `--f5` 是个**色板位置**。原来那份把两者焊死在一起：想给图表换
 *   取色顺序，就得来改这个文件里的类名，而类名又同时被 `CategoryBar`
 *   的分位条用着（那条判据要求分位条的填充**不等于**涨跌色）。
 *   现在决定住在 `index.css` 那一段注释里，这里只做"第 N 档 → 第 N 个 token"。
 *   ★这条映射是 **1:1 且同序**：`chart-1..6` 就是按本文件的语义顺序定义的。
 *
 * ★`up`/`down` 保留原名和红涨绿跌的取向，**不许对调** ——
 *   它们是 `--up`/`--down` 的直通，随主题翻转，且**排在最后**：
 *   排前面的话，随便画个多序列图第一条就是红的，而红在这站里是有语义的。
 *   ★它们**不在 `--chart-1..6` 里**（见 `index.css` 那一段的解释）：
 *   分类色一旦被当成方向色读，整张图的意思就变了。
 *
 * ★**键名换了但没有调用方会因此坏掉**：所有图表组件的默认值都是
 *   `colors = AvailableChartColors`（整条数组），没有一处把键名写死
 *   （`grep -rn 'n300\|n700' web/src` 只剩本文件；`ink` 这个名字留着，
 *   它指的是"中性色"这个角色，不是某一个灰）。
 *   ★数组**长度仍是 8、`up`/`down` 仍在第 6/7 位** —— 因为取色是
 *   `colors[index % colors.length]`，长度一变，每张多序列图的配色就会
 *   整体错位，而那种错位在源码里看不出来。
 */

export type ColorUtility = "bg" | "stroke" | "fill" | "text"

export const chartColors = {
  // ── 前六档 = `--chart-1..6`，顺序与 token 定义一致 ────────────────────
  accent: {
    bg: "bg-chart-1",
    stroke: "stroke-chart-1",
    fill: "fill-chart-1",
    text: "text-chart-1",
  },
  f2: {
    bg: "bg-chart-2",
    stroke: "stroke-chart-2",
    fill: "fill-chart-2",
    text: "text-chart-2",
  },
  f5: {
    bg: "bg-chart-3",
    stroke: "stroke-chart-3",
    fill: "fill-chart-3",
    text: "text-chart-3",
  },
  info: {
    // ★第 4 档是这站里**唯一一个冷色相**（`--info-hue`）。
    //   换掉的是原来的 `n700`（`gray-700`）：多序列图里三条线全是灰绿系，
    //   第二条和第三条在线型之外根本分不开。冷色是所有色觉无障碍方案里
    //   与红/绿冲突最小的那一支。
    bg: "bg-chart-4",
    stroke: "stroke-chart-4",
    fill: "fill-chart-4",
    text: "text-chart-4",
  },
  ink: {
    // 中性主力（`--ink-2`）。换掉的是 `gray-500`：那是**固定**的灰，
    // 而站点有两套主题 —— 固定灰在暗色下压不住底，也不随主题翻转。
    bg: "bg-chart-5",
    stroke: "stroke-chart-5",
    fill: "fill-chart-5",
    text: "text-chart-5",
  },
  faint: {
    // 中性次档（`--ink-3`）。换掉的是 `n300`（`gray-300`），同上。
    bg: "bg-chart-6",
    stroke: "stroke-chart-6",
    fill: "fill-chart-6",
    text: "text-chart-6",
  },
  // ── ★红涨绿跌 —— 放在最后，且**不许对调** ────────────────────────────
  up: {
    bg: "bg-up",
    stroke: "stroke-up",
    fill: "fill-up",
    text: "text-up",
  },
  down: {
    bg: "bg-down",
    stroke: "stroke-down",
    fill: "fill-down",
    text: "text-down",
  },
} as const satisfies {
  [color: string]: {
    [key in ColorUtility]: string
  }
}

export type AvailableChartColorsKeys = keyof typeof chartColors

export const AvailableChartColors: AvailableChartColorsKeys[] = Object.keys(
  chartColors,
) as Array<AvailableChartColorsKeys>

export const constructCategoryColors = (
  categories: string[],
  colors: AvailableChartColorsKeys[],
): Map<string, AvailableChartColorsKeys> => {
  const categoryColors = new Map<string, AvailableChartColorsKeys>()
  categories.forEach((category, index) => {
    categoryColors.set(category, colors[index % colors.length])
  })
  return categoryColors
}

export const getColorClassName = (
  color: AvailableChartColorsKeys,
  type: ColorUtility,
): string => {
  // ★兜底色是"未知键"的落点：写了个不存在的键名不会报错，
  //   只会静默画成这一档 —— 所以它必须是站点中性色（`--chart-5`），
  //   而不是第三种灰。原来是 `bg-gray-500` 的 Tremor 灰。
  const fallbackColor = {
    bg: "bg-chart-5",
    stroke: "stroke-chart-5",
    fill: "fill-chart-5",
    text: "text-chart-5",
  }
  return chartColors[color]?.[type] ?? fallbackColor[type]
}
