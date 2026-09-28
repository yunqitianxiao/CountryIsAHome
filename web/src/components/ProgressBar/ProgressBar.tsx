// Tremor ProgressBar [v0.0.3]

import React from "react"
import { tv, type VariantProps } from "tailwind-variants"

import { cx } from "../../utils/cx"

const progressBarVariants = tv({
  slots: {
    background: "",
    bar: "",
  },
  variants: {
    variant: {
      // ★这几档原来全是 Tremor 的裸调色板（default 是 **蓝的**）——
      //   而这个站的 `--accent` 是绿的，蓝色在这里没有任何语义。
      //   Onboarding 那边专门手写了一套 `.stepbar` 就是为了绕开这个蓝，
      //   代价是同一个"进度"在站里有两套长相。现在都指向 token。
      // ★`--accent`/`--up`/`--down`/`--warn-*` 自己会随主题翻转，
      //   所以 `dark:` 那一半整段可以删掉 —— 留着反而是两个出处。
      default: {
        background: "bg-accent/20",
        bar: "bg-accent",
      },
      neutral: {
        background: "bg-well",
        /* ★原来这里是 `bg-well` —— **与它自己的槽同色**，于是"中性"这一档
           渲染出来是一条看不见的空槽（进度 50% 和 0% 长得一样）。
           槽→填充的可见性靠 `--border-strong`：它由 `--rule` 往 `--ink-3` 混，
           两套主题下都比 `--well` 深一档 ⇒ 不用为深浅各写一份。 */
        bar: "bg-line-strong",
      },
      warning: {
        background: "bg-warn-bg",
        bar: "bg-warn-ink",
      },
      error: {
        background: "bg-up/20",
        bar: "bg-up",
      },
      success: {
        background: "bg-down/20",
        bar: "bg-down",
      },
    },
  },
  defaultVariants: {
    variant: "default",
  },
})

interface ProgressBarProps
  extends React.HTMLProps<HTMLDivElement>,
    VariantProps<typeof progressBarVariants> {
  value?: number
  max?: number
  showAnimation?: boolean
  label?: string
  /** 读屏用的名字。不传就用可见的 `label`；连 `label` 也没有才落到"进度"。
   *  ★加这个 prop 是因为原来写死的是 `aria-label="Progress bar"` —— 一句英文，
   *    而这是个中文站；且同一个进度条在站里被读成英文/没有名字都不对。 */
  ariaLabel?: string
}

const ProgressBar = React.forwardRef<HTMLDivElement, ProgressBarProps>(
  (
    {
      value = 0,
      max = 100,
      label,
      ariaLabel,
      showAnimation = false,
      variant,
      className,
      ...props
    }: ProgressBarProps,
    forwardedRef,
  ) => {
    const safeValue = Math.min(max, Math.max(value, 0))
    const { background, bar } = progressBarVariants({ variant })
    return (
      <div
        ref={forwardedRef}
        className={cx("flex w-full items-center", className)}
        role="progressbar"
        aria-label={ariaLabel ?? label ?? "进度"}
        // ★读的是**夹过边界**的 `safeValue`，不是原始 `value`：
        //   原来传 -5 时条画在 0%、读屏却念 -5，两个数不一样。
        aria-valuenow={safeValue}
        aria-valuemax={max}
        tremor-id="tremor-raw"
        {...props}
      >
        <div
          className={cx(
            "relative flex h-2 w-full items-center rounded-full",
            background(),
          )}
        >
          <div
            className={cx(
              "h-full flex-col rounded-full",
              bar(),
              showAnimation &&
                "transform-gpu transition-all duration-300 ease-in-out",
            )}
            style={{
              width: max ? `${(safeValue / max) * 100}%` : `${safeValue}%`,
            }}
          />
        </div>
        {label ? (
          <span
            className={cx(
              // base
              "ml-2 whitespace-nowrap text-sm font-medium leading-none",
              // text color
              "text-heading",
            )}
          >
            {label}
          </span>
        ) : null}
      </div>
    )
  },
)

ProgressBar.displayName = "ProgressBar"

export { ProgressBar, progressBarVariants, type ProgressBarProps }
