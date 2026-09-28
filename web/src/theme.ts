/**
 * 主题开关。★这是补一个**死接口**：index.css 里 `[data-theme]` 那半边的 CSS 一直在，
 * 但旧页面里没有任何 JS 去设置这个属性 —— 也就是「写得出来、点不动」。
 *
 * 三态：`system` 是一个**独立**的状态，而不是"等于当前系统色"。
 *   - system：**把 data-theme 属性整个删掉**，让 @media (prefers-color-scheme) 说了算。
 *     存成 'light'/'dark' 的话，用户今天选了、明天系统换色，页面就不跟了 ——
 *     而"跟随系统"这个词的意思恰恰是**持续跟随**。
 *   - light / dark：显式写 data-theme，两条 variant 里那条负向条件会压住系统查询。
 *
 * ★★默认值是 `light`（2026-09-28 用户把上一版的 `dark` 翻过来的）。
 *   ★**这个决定散在五处载体上**，翻的时候必须一起动 —— 少一处不是闪一下、就是两个
 *     默认值对不上，而这两种**一条判据都不会红**（判据量的是最终渲染，不是源码文本）。
 *     逐条：① 这里的返回值；② `web/index.html` `<head>` 里那段**内联**脚本（防闪白 ——
 *     只靠这里的 `useLayoutEffect` **来不及**，React 挂载前那一帧会是旧主题）；
 *     ③ `dark-first-theme.css` 的 `:root { color-scheme }`（首帧还没有 data-theme 时
 *     原生控件与滚动条走的那一档；它与页面底色不一致时，页面上什么都看不出来）；
 *     ④ `graph_template.html` 的 `<html data-theme="…">`（那一页是**硬钉**的，而它钉成
 *     深色的理由原本就写着"与 SPA 是同一个产品决定"）；⑤ `verify_agent.py` 里三条判据
 *     （③/④/⑧）与两条注入的锚点 —— 判据 ③ 的注释自称是"用户点名要的那件事的唯一
 *     守卫"，它守的东西翻了，它就跟着翻。
 *   ★③ 那条判据还兼着一件与"是哪个色"**无关**的职责：防止默认值被静默改回 `system`。
 *     改回去之后页面照样是好的，只有系统偏好是亮的机器上才看得出差别 —— 而那恰好不是
 *     开发机。翻成 `light` 之后这条职责原样成立，只是较真的方向反过来（判据钉在
 *     系统**暗色**那一跑上）。
 *   ★探针必须能**显式钉住**主题（`probe.ts` 里用属性直接量亮/暗两套），否则"有默认值"
 *     这件事会把原来那三条主题判据一起变成假绿 —— 它们量的是"不动 data-theme 时的
 *     背景"，默认一变，它们量的东西就从「系统那条来源」悄悄变成了「页面自己启动的
 *     状态」，两边都绿、没人验 @media。
 *
 * ★`system` 并没有被删掉：用户**显式**选了才跟随系统，那时删属性、@media 接管。
 *   这条路仍然有判据守着（探针里真的 `removeAttribute` 量一次，两个方向都判）。
 */
import * as React from "react"

export type Theme = "system" | "light" | "dark"

const KEY = "licai-theme"

/** 没存过时用什么。**改这里必须同时改 `web/index.html` 里那段防闪脚本** ——
 *  两处写的是同一个决定（首帧那份和 React 挂载后那份），不一致就是一次闪烁；
 *  而闪烁只在"首帧"发生，`--selftest` 之外没人会盯着看。
 *  ⇒ 判据不去比这两处的源码文本（那是读输入），而是量**探针记录的启动值**：
 *    全新 profile（localStorage 一定是空的）里 `bootTheme` 必须是 'light'。 */
export const DEFAULT_THEME: Theme = "light"

export function readTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY)
    if (v === "light" || v === "dark" || v === "system") return v
  } catch {
    /* 隐私模式下 localStorage 会抛 —— 抛了就当没存过，页面照常 */
  }
  return DEFAULT_THEME
}

export function applyTheme(t: Theme) {
  const el = document.documentElement
  if (t === "system") delete el.dataset.theme
  else el.dataset.theme = t
  try {
    localStorage.setItem(KEY, t)
  } catch {
    /* 同上：存不下不影响这一屏 */
  }
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = React.useState<Theme>(readTheme)
  // 首帧就应用，别等 effect —— 否则会先闪一下**防闪脚本写的那一帧**再切成用户选的
  // （防闪脚本管的是"还没进 React"的那一帧，这里管的是挂载那一帧，两段接起来才没有缝）
  React.useLayoutEffect(() => applyTheme(theme), [theme])
  return [theme, setTheme]
}

/* ── 涨跌色：市场惯例是可配置的 ──────────────────────────────────────────
   ★为什么单独有一层：A 股是**红涨绿跌**，欧美是**绿涨红跌**。这不是审美，
     是"同一张图在两个市场里读出来的方向相反"。抄错一边，用户会把涨看成跌。
   ★实现方式是 `documentElement.dataset.market`：`index.css` 里
     `:root[data-market="us"] { --up: var(--c-green); --down: var(--c-red) }`
     一条规则把两档对调，`--c-red`/`--c-green` 是那两个**不随主题翻转**的原色。
   ★为什么不是"两套 `--up` 写死"：`--up`/`--down` 的取值是**逐字被注入锚着**的
     （`verify_agent.py` 有两条故障注入把这几个字面量当锚点），改它们等于让那两条
     注入失去否证能力。多一层 `--c-*` 是把"色号"和"方向"拆开，两头都不用动。

   ★UI 开关**已接**（2026-09-22 由协调者落）：`chrome.tsx` 的 `MarketSwitch` 就摆在
     `ThemeSwitch` 旁边 —— 是两个**并列**控件而不是一个多态开关，因为"底色亮/暗"
     与"什么颜色表示涨"是**正交**的两件事（四个组合都合法）。
   ★它需要**尽早的调用**，一共三处，缺一处就有一段窗口是错的：默认值 cn 由 CSS
     兜底 ⇒ 没存过时页面本来就是对的；但用户选过 us 之后，若不在首帧之前调用，
     会先按 A 股画一遍再翻过来 —— 闪的是**颜色语义**（同一个红一会儿表示涨、
     一会儿表示跌），比闪白更难发现。三处依次是：`web/index.html` 的防闪内联脚本
     （早于模块执行 ⇒ 救的是首帧）、`main.tsx` 顶层（模块执行时）、
     `MarketSwitch` 的 `useMarket`（挂载后）。三处读的是同一处 localStorage。
   ★★**市场这一档目前没有任何判据**（主题那一档有：探针把 `bootTheme` 带回去量
     "页面真的启动成了什么"）。这里**故意没有** `window.__bootMarket` —— 不假造
     一个没人读的启动值。所以"开关接线到了产物"只有 grep 级的证据，见交付说明。 */
export type Market = "cn" | "us"

const MARKET_KEY = "licai-market"

/** 默认 `cn`（A 股：红涨绿跌）。和 `DEFAULT_THEME` 一样，这里是**产品决定**。 */
export const DEFAULT_MARKET: Market = "cn"

export function readMarket(): Market {
  try {
    const v = localStorage.getItem(MARKET_KEY)
    if (v === "cn" || v === "us") return v
  } catch {
    /* 同 readTheme：隐私模式下就当没存过 */
  }
  return DEFAULT_MARKET
}

export function applyMarket(m: Market) {
  const el = document.documentElement
  // ★cn 时**把属性删掉**而不是写 `data-market="cn"`：默认值应该由 CSS 里
  //   `:root` 的那一份承担（那是"没得选时"的唯一出处），写死一个等价的属性
  //   会造出第二份"默认值"，两份将来会分叉。
  if (m === "cn") delete el.dataset.market
  else el.dataset.market = m
  try {
    localStorage.setItem(MARKET_KEY, m)
  } catch {
    /* 同上 */
  }
}

export function useMarket(): [Market, (m: Market) => void] {
  const [market, setMarket] = React.useState<Market>(readMarket)
  React.useLayoutEffect(() => applyMarket(market), [market])
  return [market, setMarket]
}
