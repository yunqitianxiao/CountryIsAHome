// Tremor Button [v1.0.0]

import React from "react"
import { Slot } from "@radix-ui/react-slot"
import { RiLoader2Fill } from "@remixicon/react"
import { tv, type VariantProps } from "tailwind-variants"

import { cx } from "../../utils/cx"

/* ★焦点环在这里**就地**写、不从 `utils/focusRing.ts` 引：
   那个文件里是 `outline-blue-500 dark:outline-blue-500` —— 一个写死的蓝。
   就地写在 `outline-*` 上只有一条颜色声明 ⇒ 深浅两套主题各自由 token 决定，
   不会出现"我的 token 环与它的蓝环在 `:focus-visible` 下按生成顺序决胜"这种
   读源码看不出的结果。`outline-accent` 不带变体前缀是**故意**的：
   带上 `focus-visible:` 就成了另一个变体组，两条都会留在 CSS 里。
   （`utils/focusRing.ts` 不在本次可改文件清单里 ⇒ 已在交付说明里列为待协调项。） */
const buttonFocusRing = [
  // base
  "outline outline-offset-2 outline-0 focus-visible:outline-2",
  // outline color —— token，随主题翻转
  "outline-accent",
]

const buttonVariants = tv({
  base: [
    // base
    "relative inline-flex items-center justify-center whitespace-nowrap rounded-full border px-3 py-2 text-center text-sm font-medium shadow-xs transition-all duration-100 ease-in-out",
    // disabled
    "disabled:pointer-events-none disabled:shadow-none",
    // focus
    buttonFocusRing,
  ],
  variants: {
    variant: {
      primary: [
        // border
        "border-transparent",
        /* ★原来这里是 `text-white dark:text-white`。
           白字只在**亮色**那套上成立：亮色 `--accent` 是深青绿 `#0b6b5f`，
           白字 6.40:1 过线；暗色 `--accent` 是**亮**绿 `#5cc79a`，
           白字只有 2.08:1 —— 而 `verify_agent.py` 里那条
           「★主题[暗色]：accent 底上的字过 AA」量的正是这件事
           （它逐字节找 `background-color == --accent` 的元素再算对比度）。
           `--on-accent` 两套都对（6.40 / 8.65），且它是 token ⇒ 不用写 `dark:` 那一半。 */
        "text-on-accent",
        // background color
        "bg-accent",
        // hover color
        "hover:bg-accent/90",
        // disabled
        "disabled:bg-accent/40 disabled:text-on-accent",
      ],
      secondary: [
        // border
        "border-line",
        // text color
        "text-heading",
        // background color
        "bg-surface",
        //hover color
        "hover:bg-surface-alt",
        // disabled
        "disabled:text-faint",
      ],
      light: [
        // base
        "shadow-none",
        // border
        "border-transparent",
        // text color
        "text-heading",
        // background color —— `--well` 就是 `--paper`：在卡片上"凹下去一档"，
        // 正是次级按钮该有的观感，两套主题各自成立。
        "bg-well",
        // hover color
        "hover:bg-line-strong/70",
        // disabled
        "disabled:bg-surface-alt disabled:text-faint",
      ],
      ghost: [
        // base
        "shadow-none",
        // border
        "border-transparent",
        // text color
        "text-heading",
        // hover color
        "bg-transparent hover:bg-surface-alt",
        // disabled
        "disabled:text-faint",
      ],
      destructive: [
        /* ★同上：`text-white` 在暗色 `--status-danger`（`#e5765c`）上只有 2.97:1。
           `--on-danger` 两套都过（浅 4.76 / 暗 6.07）。 */
        "text-on-danger",
        // border
        "border-transparent",
        // background color
        "bg-status-danger",
        // hover color
        "hover:bg-status-danger/90",
        // disabled
        "disabled:bg-status-danger/40 disabled:text-on-danger",
      ],
    },
  },
  defaultVariants: {
    variant: "primary",
  },
})

interface ButtonProps
  extends React.ComponentPropsWithoutRef<"button">,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
  isLoading?: boolean
  loadingText?: string
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      asChild,
      isLoading = false,
      loadingText,
      className,
      disabled,
      variant,
      children,
      ...props
    }: ButtonProps,
    forwardedRef,
  ) => {
    const Component = asChild ? Slot : "button"
    return (
      <Component
        ref={forwardedRef}
        className={cx(buttonVariants({ variant }), className)}
        disabled={disabled || isLoading}
        // 加载态对读屏也要"说"出来：只有转圈图标是看不见的，
        // 下面的 `sr-only` 负责念出来，`aria-busy` 负责把按钮标成"忙"。
        aria-busy={isLoading || undefined}
        tremor-id="tremor-raw"
        {...props}
      >
        {isLoading ? (
          <span className="pointer-events-none flex shrink-0 items-center justify-center gap-1.5">
            <RiLoader2Fill
              className="size-4 shrink-0 animate-spin"
              aria-hidden="true"
            />
            <span className="sr-only">
              {loadingText ? loadingText : "Loading"}
            </span>
            {loadingText ? loadingText : children}
          </span>
        ) : (
          children
        )}
      </Component>
    )
  },
)

Button.displayName = "Button"

export { Button, buttonVariants, type ButtonProps }
