// Tremor Badge [v1.0.0]

import React from "react"
import { tv, type VariantProps } from "tailwind-variants"

import { cx } from "../../utils/cx"

const badgeVariants = tv({
  base: cx(
    "inline-flex items-center gap-x-1 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset",
  ),
  variants: {
    variant: {
      // ★原来这五档是 Tremor 的 raw 调色板（blue/emerald/red/yellow）。
      //   最要命的是 `error`/`success` 这一对：`PredCard` 拿它**表示涨跌**
      //   （`dirOf(...)==="up" → "error"`），而 Tailwind 的 red-600 `#dc2626`
      //   和站点的 `--up` `#c0392b` 是**两个红**，emerald 和 `--down` 也一样。
      //   ⇒ 同一个"涨"在徽章上是 Tailwind 红、在旁边那行字上是站点红。
      //   现在两处都走 token，红涨绿跌只剩一个出处。
      // ★`/10`、`/30` 这些透明度修饰在 var() 颜色上会编译成
      //   `color-mix(in oklab, var(--color-up) 10%, transparent)`，
      //   所以暗色不用再写一套，token 自己会翻。
      default: [
        "bg-accent/10 text-accent ring-accent/30",
      ],
      neutral: [
        // ★一条类名就够了 —— `bg-surface-alt` / `text-fg` / `ring-line`
        //   每一个都是**会翻转的 token**，所以这里没有 `dark:` 那一半。
        //   原来写的是 `bg-gray-100 text-gray-700 ring-gray-500/30` +
        //   `dark:bg-gray-400/10 dark:text-gray-300 dark:ring-gray-400/20`：
        //   `@theme` 把 gray-* 收编到**固定**色阶（不翻转）之后，
        //   亮暗两套必须成对写，而"成对写"就是两份出处 —— 删掉一半不会报错，
        //   只会让暗色下变成浅字压浅底。语义 token 从结构上消掉了这个可能。
        "bg-surface-alt text-fg ring-line",
      ],
      info: [
        // 信息：不吵、也不中性到看不见。色相见 `index.css` 的 `--info-hue`。
        "bg-info-bg text-info ring-info-rule",
      ],
      success: [
        "bg-down/10 text-down ring-down/30",
      ],
      error: [
        "bg-up/10 text-up ring-up/30",
      ],
      warning: [
        "bg-warn-bg text-warn-ink ring-warn-rule",
      ],
    },
  },
  defaultVariants: {
    variant: "default",
  },
})

interface BadgeProps
  extends React.ComponentPropsWithoutRef<"span">,
  VariantProps<typeof badgeVariants> { }

const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant, ...props }: BadgeProps, forwardedRef) => {
    return (
      <span
        ref={forwardedRef}
        className={cx(badgeVariants({ variant }), className)}
        tremor-id="tremor-raw"
        {...props}
      />
    )
  },
)

Badge.displayName = "Badge"

export { Badge, badgeVariants, type BadgeProps }
