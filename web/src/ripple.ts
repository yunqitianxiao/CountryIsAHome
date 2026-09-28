/**
 * 点击处的**圈状波纹** —— 用户的要求：「点击选项卡以及界面的时候要有圈状波纹，所有界面都是」。
 *
 * ★实现上最要紧的一个决定：波纹 `position: fixed` 挂在 `<body>` 上，
 *   **不进任何布局容器**。
 *
 *   为什么不挂在被点的元素里（那才是 Material 那套的常见做法）：
 *   挂进去就得给那个元素（或它的某个祖先）`position: relative` + `overflow: hidden`，
 *   否则波纹会溢出卡片；而"给任意被点元素动态加 position"这件事，
 *   会改变**层叠上下文**，也可能改变滚动容器 —— 表现是"某个下拉菜单突然被裁掉了"、
 *   "某处 sticky 不粘了"这类查起来极难的问题。
 *   判据那边还有一条更硬的：**点击前后被点元素的 getBoundingClientRect() 逐像素相同**。
 *   挂 body 上用 fixed，这条从构造上就成立，不是靠"但愿 overflow:hidden 兜住了"。
 *
 *   ⇒ 代价写清楚：波纹不会被卡片的圆角裁掉，它是浮在整页之上的一圈。
 *     这是**明知**的取舍，不是漏了。
 *
 * ★`prefers-reduced-motion` 下**不生成元素**（不是生成了再让动画瞬间结束）：
 *   前庭失调的用户对这类全屏扩散动画是真的会不舒服，而"动画时长为 0"仍然会
 *   留下一帧的实心圆。
 */
export const RIPPLE_CLASS = "ripple"

/** 只在**真的被点到**的元素上冒波纹：空白处点一下不该有东西。
 *  ★`closest` 从事件目标往上找，所以点卡片里的字、图标都算法。
 *  ★★往里加东西容易，**往外摘东西要当心**：这一串里 `button` / `[role='button']`
 *    那一组撑着的是表单里那些选项卡（`[data-opt]` 的 `<button>`）。
 *    判据「★波纹[/profile]：点选项卡也冒圈」量的是**这个增量**，
 *    对应的注入是把这一串窄成只认 `a`（见 verify_agent.py）。
 *    ★而 `.card` 那一项**不撑任何一条判据**：两页的选项卡本来就在一张 `.card`
 *      里，`closest` 往上走一层就命中它 —— 摘掉 `.card` 对选项卡没有影响。 */
const HOSTSEL = "a, button, [role='button'], .card, .tchip, .tab, label, input, select"

function reduced(): boolean {
  try {
    return !!window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches
  } catch {
    return false   // 拿不到就不当 reduced：宁可多一个动画，也不要静默地什么都不做
  }
}

/** 冒一圈波纹，返回那个元素（探针要量它）。**导出**是为了让测试能直接调，
 *  但真正的判据走的是下面那条 document 上的监听 —— 那才是用户走的路。 */
export function spawnRipple(x: number, y: number): HTMLElement | null {
  if (reduced()) return null
  const d = document.createElement("span")
  d.className = RIPPLE_CLASS
  d.setAttribute("data-x", String(Math.round(x)))
  d.setAttribute("data-y", String(Math.round(y)))
  // ★圆心就是点击点：左/上用点击坐标，再靠 CSS 的 translate(-50%,-50%) 把圆心挪过去。
  //   写 `x - SIZE/2` 也行，但那样尺寸一变就得改两处 —— 圆心这件事只该有一个来源。
  d.style.left = x + "px"
  d.style.top = y + "px"
  document.body.appendChild(d)
  const kill = () => d.remove()
  d.addEventListener("animationend", kill)
  // ★兜底：`animationend` 在某些情况下不来（元素被 display:none、标签页在后台
  //   被节流、动画被别处的 CSS 覆盖成 none）。没有这一手，页面上会攒下一堆
  //   永远不消失的圆圈 —— 而它们 `pointer-events:none`，所以**点不着、也不报错**。
  window.setTimeout(kill, 800)
  return d
}

export function installRipple() {
  document.addEventListener(
    "click",
    (e) => {
      const t = e.target as Element | null
      if (!t || !(t as HTMLElement).closest) return
      if (!t.closest(HOSTSEL)) return
      // 探针自己造的那些 click 事件（没有坐标）不冒波纹：它们的 clientX/Y 是 0，
      // 会在左上角留一圈，看起来像页面坏了。
      if (e.clientX === 0 && e.clientY === 0) return
      spawnRipple(e.clientX, e.clientY)
    },
    true,   // 捕获阶段：被点的元素 stopPropagation 了也照样冒
  )
}

/** 供 React 组件用：手写一个带坐标的 click 事件（`el.click()` 没有坐标）。 */
export function clickAt(el: Element, x: number, y: number) {
  el.dispatchEvent(new MouseEvent("click", {
    bubbles: true, cancelable: true, clientX: x, clientY: y, view: window,
  }))
}
