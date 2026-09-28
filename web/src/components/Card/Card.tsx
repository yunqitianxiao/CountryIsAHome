// Tremor Card [v1.0.0]

import React from "react"
import { Slot } from "@radix-ui/react-slot"

import { cx } from "../../utils/cx"

interface CardProps extends React.ComponentPropsWithoutRef<"div"> {
  asChild?: boolean
}

const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, asChild, ...props }, forwardedRef) => {
    const Component = asChild ? Slot : "div"
    return (
      <Component
        ref={forwardedRef}
        className={cx(
          // base
          "relative w-full rounded-lg border p-6 text-left shadow-xs",
          // ★这里原来是 `bg-white dark:bg-[#090E1A]` —— 那个十六进制是
          //   Tremor 自己的深蓝黑，**不在站点的任何 token 里**（`--card` 是
          //   `#121815`，偏绿）。于是 PredCard 那四张读数小卡在暗色下
          //   和它们所在的那张卡是两个底色。指到 token 就没有第二个出处了。
          // background color
          "bg-surface",
          // border color
          "border-line",
          className,
        )}
        tremor-id="tremor-raw"
        {...props}
      />
    )
  },
)

Card.displayName = "Card"

export { Card, type CardProps }
