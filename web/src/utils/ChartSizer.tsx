import React from "react"
import { ResponsiveContainer } from "recharts"

/**
 * ★这个文件是 Tremor Raw 组件被改动的地方，理由是**验证体系**，不是审美。
 *
 * Tremor Raw 的图表全部包在 `<ResponsiveContainer>` 里（AreaChart.tsx 里是零 props 的那个），
 * 尺寸完全由 CSS 决定。而 recharts 2.x 的 ResponsiveContainer 在 `containerWidth < 0` 时
 * **直接 return null**（recharts/es6/component/ResponsiveContainer.js:106），它的 sizes 初值
 * 就是 -1、要等 ResizeObserver 回调才变成真数 —— 所以**首帧必然渲染不出图**，
 * 连"传数值 width/height"都救不了（那两个数只在 `isPercent(width)` 为假时参与计算，
 * 前面的 `< 0` 早退闸门照样拦）。
 *
 * 实测（真 firefox，同一 tick 内量）：
 *   t0 同步 tick         裸 ResponsiveContainer → svg:false 0×0 marks:0
 *                        走下面这条定尺寸分支 → svg:true 600×240 marks:3
 *   t2 setTimeout 0      ResponsiveContainer 才出图
 *   t3/t4（60/300ms）    **一条都没赶上 firefox 退出** —— 异步就是"判据消失"而不是"判据变红"
 *
 * 而 licai-agent 的渲染层判据（29 条 × 3 视口）全部押在"探针在同一个同步 tick 里量完几何"
 * 之上（见 index.html 里 keyLoadSync 那段注释）。所以：**探针模式下必须走定尺寸分支**，
 * 把几何变成确定的、同步的，而不是等观察者回调。
 *
 * 这不是 hack —— Tremor Raw 本来就是"复制粘贴进项目"的发行版，改副本是它的正当用法。
 */
export function ChartSizer({
  width,
  height,
  children,
}: {
  width?: number
  height?: number
  children: React.ReactElement
}) {
  // 给了确定的宽高 ⇒ 绕开 ResponsiveContainer，直接给 recharts 一个定尺寸的画布
  if (width && height) {
    return React.cloneElement(children, { width, height } as any)
  }
  return <ResponsiveContainer>{children}</ResponsiveContainer>
}
