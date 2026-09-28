// Tremor ProgressCircle [v0.0.3]

import React from "react"
import { tv, VariantProps } from "tailwind-variants"

import { cx } from "../../utils/cx"

// ★整条调色板收到站点语义 token 上（原来那五个是 Tremor 的 raw
//   blue/gray/yellow/red/emerald —— 这就是"两套配色"里的另一套）。
//   轨道一律用 `--rule-2`：那是"卡内凹槽"那一档，两套主题里都成立；
//   原来每档各配一个 `-200 / -500/30` 的浅色轨道，等于给五个色各调一遍浅色。
const progressCircleVariants = tv({
  slots: {
    background: "",
    circle: "",
  },
  variants: {
    variant: {
      default: {
        background: "stroke-[var(--rule-2)]",
        circle: "stroke-[var(--accent)]",
      },
      neutral: {
        background: "stroke-[var(--rule-2)]",
        circle: "stroke-[var(--ink-3)]",
      },
      warning: {
        background: "stroke-[var(--warn-rule)]",
        circle: "stroke-[var(--warn-ink)]",
      },
      // ★★★A 股口径：**涨是红、跌是绿**，和欧美正好相反。
      //   所以这里**故意不提供 `success`/`error` 这两个名字** ——
      //   那个命名体系会把「赚了」映射到 success，而 success 在欧美配色里是绿色，
      //   于是"赚了"被画成绿的，用户一眼读成跌。命名一错，用的人怎么写都是错的。
      //   ⇒ 直接用市场口径命名，`variant={pnl >= 0 ? "up" : "down"}` 读出来就是对的。
      up: {
        background: "stroke-[var(--rule-2)]",
        circle: "stroke-[var(--up)]",
      },
      down: {
        background: "stroke-[var(--rule-2)]",
        circle: "stroke-[var(--down)]",
      },
    },
  },
  defaultVariants: {
    variant: "default",
  },
})

interface ProgressCircleProps
  extends Omit<React.SVGProps<SVGSVGElement>, "value">,
    VariantProps<typeof progressCircleVariants> {
  value?: number
  max?: number
  showAnimation?: boolean
  radius?: number
  strokeWidth?: number
  children?: React.ReactNode
  /** 无障碍名。
   *  ★原来这里是写死的 `aria-label="Progress circle"` —— 一个中文站里，
   *    读屏软件对着这个环念一句英文，而且**没说是"什么的"进度**。
   *    调用方必须给一个说清"这是哪个进度"的名字；不给就退回原来那句。 */
  label?: string
}

const ProgressCircle = React.forwardRef<SVGSVGElement, ProgressCircleProps>(
  (
    {
      value = 0,
      max = 100,
      radius = 32,
      strokeWidth = 6,
      showAnimation = true,
      variant,
      className,
      children,
      label,
      ...props
    }: ProgressCircleProps,
    forwardedRef,
  ) => {
    const safeValue = Math.min(max, Math.max(value, 0))
    const normalizedRadius = radius - strokeWidth / 2
    const circumference = normalizedRadius * 2 * Math.PI
    const offset = circumference - (safeValue / max) * circumference

    const { background, circle } = progressCircleVariants({ variant })
    return (
      <div
        className={cx("relative")}
        role="progressbar"
        aria-label={label ?? "Progress circle"}
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={max}
        data-max={max}
        data-value={safeValue ?? null}
        tremor-id="tremor-raw"
      >
        <svg
          ref={forwardedRef}
          width={radius * 2}
          height={radius * 2}
          viewBox={`0 0 ${radius * 2} ${radius * 2}`}
          className={cx("-rotate-90 transform", className)}
          {...props}
        >
          <circle
            r={normalizedRadius}
            cx={radius}
            cy={radius}
            strokeWidth={strokeWidth}
            fill="transparent"
            stroke=""
            strokeLinecap="round"
            className={cx("transition-colors ease-linear", background())}
          />
          {safeValue >= 0 ? (
            <circle
              r={normalizedRadius}
              cx={radius}
              cy={radius}
              strokeWidth={strokeWidth}
              strokeDasharray={`${circumference} ${circumference}`}
              strokeDashoffset={offset}
              fill="transparent"
              stroke=""
              strokeLinecap="round"
              className={cx(
                "transition-colors ease-linear",
                circle(),
                showAnimation &&
                  "transform-gpu transition-all duration-300 ease-in-out",
              )}
            />
          ) : null}
        </svg>
        <div
          className={cx("absolute inset-0 flex items-center justify-center")}
        >
          {children}
        </div>
      </div>
    )
  },
)

ProgressCircle.displayName = "ProgressCircle"

export { ProgressCircle, type ProgressCircleProps }
