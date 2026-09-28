// Tremor Callout [v0.0.1]

import React from "react"
import { tv, type VariantProps } from "tailwind-variants"

import { cx } from "../../utils/cx"

const calloutVariants = tv({
  base: "flex flex-col overflow-hidden rounded-md p-4 text-sm",
  variants: {
    variant: {
      // ★同样是从 Tremor 的裸调色板收到站点 token 上。
      //   `warning` 这一档最要紧：站里两处用它装**风险提示**
      //   （Dash「这一页只是行情，不是建议」、Game「钱是假的，教训是真的」），
      //   而站点早就有一组专门给提示用的 `--warn-bg/--warn-rule/--warn-ink`。
      //   原来那套 yellow-50/yellow-900 跟这组不是一个黄 —— 于是"提示"
      //   在 Callout 里和 `.disclaim` 里长得不一样。
      default: [
        "text-accent bg-accent/10",
      ],
      success: [
        "text-down bg-down/10",
      ],
      error: [
        "text-up bg-up/10",
      ],
      warning: [
        "text-warn-ink bg-warn-bg",
      ],
      neutral: [
        // ★与 Badge 的 `neutral` 同一个道理（见 `Badge.tsx` 那段注释）：
        //   token 自己翻转 ⇒ 不需要 `dark:` 那一半。
        "text-fg bg-surface-alt",
      ],
      info: [
        // 「数据来自 X、更新时间 Y」这类**说明**用它。不是提醒、不是警告，
        // 所以既不用 `--warn-*`（那会让每一条都像要出事），
        // 也不用 `--accent`（那是品牌绿，等于说"这条是重点"）。
        "text-info bg-info-bg",
      ],
    },
  },
  defaultVariants: {
    variant: "default",
  },
})

interface CalloutProps
  extends React.ComponentPropsWithoutRef<"div">,
    VariantProps<typeof calloutVariants> {
  title: string
  icon?: React.ElementType | React.ReactElement
}

const Callout = React.forwardRef<HTMLDivElement, CalloutProps>(
  (
    { title, icon: Icon, className, variant, children, ...props }: CalloutProps,
    forwardedRef,
  ) => {
    return (
      <div
        ref={forwardedRef}
        className={cx(calloutVariants({ variant }), className)}
        tremor-id="tremor-raw"
        {...props}
      >
        <div className={cx("flex items-start")}>
          {Icon && typeof Icon === "function" ? (
            <Icon
              className={cx("mr-1.5 h-5 w-5 shrink-0")}
              aria-hidden="true"
            />
          ) : (
            Icon
          )}
          <span className={cx("font-semibold")}>{title}</span>
        </div>
        <div className={cx("overflow-y-auto", children ? "mt-2" : "")}>
          {children}
        </div>
      </div>
    )
  },
)

Callout.displayName = "Callout"

export { Callout, calloutVariants, type CalloutProps }
