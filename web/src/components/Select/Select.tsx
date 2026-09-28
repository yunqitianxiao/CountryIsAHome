// Tremor Select [v1.0.0]

import React from "react"
import * as SelectPrimitives from "@radix-ui/react-select"
import {
  RiArrowDownSLine,
  RiArrowUpSLine,
  RiCheckLine,
  RiExpandUpDownLine,
} from "@remixicon/react"

import { cx } from "../../utils/cx"

const Select = SelectPrimitives.Root
Select.displayName = "Select"

const SelectGroup = SelectPrimitives.Group
SelectGroup.displayName = "SelectGroup"

const SelectValue = SelectPrimitives.Value
SelectValue.displayName = "SelectValue"

const selectTriggerStyles = [
  cx(
    // base
    "group/trigger flex w-full select-none items-center justify-between gap-2 truncate rounded-md border px-3 py-2 shadow-xs outline-hidden transition sm:text-sm",
    // border color
    "border-line dark:border-line-subtle",
    // text color
    "text-heading",
    // placeholder
    /* ★原来是 `data-placeholder:text-heading0` —— `heading0` 这个 token
       **不存在**（`@theme` 里只有 `--color-heading`）。Tailwind 4 遇到不认识的
       工具类**不报错、也不生成 CSS**，所以这一条静默失效了：占位文字一直用
       的是继承来的 `text-heading`，与真值同色 —— 用户分不出"这是提示"
       还是"这里已经填了"。改成 `text-faint`（过 AA 的那一档说明文字色）。 */
    "data-placeholder:text-faint",
    // background color
    "bg-surface",
    // hover
    "hover:bg-surface-alt dark:hover:bg-canvas/50",
    // disabled
    "data-disabled:bg-surface-alt data-disabled:text-faint",
    "dark:data-disabled:border-line dark:data-disabled:bg-surface-alt",
    /* ★键盘焦点环：这一条**必须**有。触发器上有 `outline-hidden`
       （它是给 forced-colors 模式用的透明 outline），原来那个可见的焦点
       来自 `utils/focusInput.ts` 的蓝色 ring，而那个文件里是写死的
       `blue-500`/`blue-200`/`blue-700` —— 一个不随主题翻转、也不属于这个
       色板的颜色。就地写 token 环：`ring-accent` 两边都过对比，
       且 `focus-visible:` 只在键盘聚焦时亮（鼠标点不亮，符合 WCAG 2.4.7
       的意图：不要给鼠标用户加视觉噪音）。
       （`utils/focusInput.ts` 不在本次可改文件清单里 ⇒ 见交付说明。） */
    "focus-visible:ring-2 focus-visible:ring-accent/40 focus-visible:border-accent",
    // invalid (optional)
    // "aria-invalid:ring-2 aria-invalid:ring-status-danger/30 aria-invalid:border-status-danger"
  ),
]

const SelectTrigger = React.forwardRef<
  React.ElementRef<typeof SelectPrimitives.Trigger>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitives.Trigger> & {
    hasError?: boolean
  }
>(({ className, hasError, children, ...props }, forwardedRef) => {
  return (
    <SelectPrimitives.Trigger
      ref={forwardedRef}
      className={cx(
        selectTriggerStyles,
        /* ★就地写 token 版的"错误态"，不用 `utils/hasErrorInput.ts`：
           那个文件是 Tremor 原样搬来的 `red-500`/`red-700`/`red-200` ——
           站点色板里没有这三个红（错误色是 `--f5`，随主题翻转的砖红），
           且 `red-*` **没有被 `@theme` 收编**（收编的只有 `gray-*` 11 档）。
           于是同一个"输入有错"在 Select 上是 Tailwind 红、在提示文字上
           可能又是站点砖红 —— 两套红是来源分叉。
           ★`hasErrorInput` 仍是 `Input.tsx`/`SelectNative.tsx` 在用的，
             那两个文件不在本次可改清单里 ⇒ 见交付说明。 */
        hasError ? "border-status-danger ring-2 ring-status-danger/30" : "",
        className,
      )}
      tremor-id="tremor-raw"
      {...props}
    >
      <span className="truncate">{children}</span>
      <SelectPrimitives.Icon asChild>
        <RiExpandUpDownLine
          className={cx(
            // base
            "size-4 shrink-0",
            // text color
            "text-faint",
            // disabled
            "group-data-disabled/trigger:text-fg dark:group-data-disabled/trigger:text-muted",
          )}
        />
      </SelectPrimitives.Icon>
    </SelectPrimitives.Trigger>
  )
})

SelectTrigger.displayName = "SelectTrigger"

const SelectScrollUpButton = React.forwardRef<
  React.ElementRef<typeof SelectPrimitives.ScrollUpButton>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitives.ScrollUpButton>
>(({ className, ...props }, forwardedRef) => (
  <SelectPrimitives.ScrollUpButton
    ref={forwardedRef}
    className={cx(
      "flex cursor-default items-center justify-center py-1",
      className,
    )}
    {...props}
  >
    <RiArrowUpSLine className="size-3 shrink-0" aria-hidden="true" />
  </SelectPrimitives.ScrollUpButton>
))
SelectScrollUpButton.displayName = SelectPrimitives.ScrollUpButton.displayName

const SelectScrollDownButton = React.forwardRef<
  React.ElementRef<typeof SelectPrimitives.ScrollDownButton>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitives.ScrollDownButton>
>(({ className, ...props }, forwardedRef) => (
  <SelectPrimitives.ScrollDownButton
    ref={forwardedRef}
    className={cx(
      "flex cursor-default items-center justify-center py-1",
      className,
    )}
    {...props}
  >
    <RiArrowDownSLine className="size-3 shrink-0" aria-hidden="true" />
  </SelectPrimitives.ScrollDownButton>
))
SelectScrollDownButton.displayName =
  SelectPrimitives.ScrollDownButton.displayName

const SelectContent = React.forwardRef<
  React.ElementRef<typeof SelectPrimitives.Content>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitives.Content>
>(
  (
    {
      className,
      position = "popper",
      children,
      sideOffset = 8,
      collisionPadding = 10,
      ...props
    },
    forwardedRef,
  ) => (
    <SelectPrimitives.Portal>
      <SelectPrimitives.Content
        ref={forwardedRef}
        className={cx(
          // base
          "relative z-50 overflow-hidden rounded-md border shadow-xl shadow-black/[2.5%]",
          // widths
          "min-w-[calc(var(--radix-select-trigger-width)-2px)] max-w-[95vw]",
          // heights
          "max-h-(--radix-select-content-available-height)",
          // background color
          "bg-surface",
          // text color
          "text-heading",
          // border color
          "border-line-subtle",
          // transition
          "will-change-[transform,opacity]",
          // "data-[state=open]:animate-slide-down-and-fade",
          "data-[state=closed]:animate-hide",
          "data-[side=bottom]:animate-slide-down-and-fade data-[side=left]:animate-slide-left-and-fade data-[side=right]:animate-slide-right-and-fade data-[side=top]:animate-slide-up-and-fade",
          className,
        )}
        sideOffset={sideOffset}
        position={position}
        collisionPadding={collisionPadding}
        {...props}
      >
        <SelectScrollUpButton />
        <SelectPrimitives.Viewport
          className={cx(
            "p-1",
            position === "popper" &&
            "h-[var(--radix-select-trigger-height)] w-full min-w-[calc(var(--radix-select-trigger-width))]",
          )}
        >
          {children}
        </SelectPrimitives.Viewport>
        <SelectScrollDownButton />
      </SelectPrimitives.Content>
    </SelectPrimitives.Portal>
  ),
)

SelectContent.displayName = "SelectContent"

const SelectGroupLabel = React.forwardRef<
  React.ElementRef<typeof SelectPrimitives.Label>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitives.Label>
>(({ className, ...props }, forwardedRef) => (
  <SelectPrimitives.Label
    ref={forwardedRef}
    className={cx(
      // base
      "px-3 py-2 text-xs font-medium tracking-wide",
      // text color
      "text-muted",
      className,
    )}
    {...props}
  />
))

SelectGroupLabel.displayName = "SelectGroupLabel"

const SelectItem = React.forwardRef<
  React.ElementRef<typeof SelectPrimitives.Item>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitives.Item>
>(({ className, children, ...props }, forwardedRef) => {
  return (
    <SelectPrimitives.Item
      ref={forwardedRef}
      className={cx(
        // base
        "grid cursor-pointer grid-cols-[1fr_20px] gap-x-2 rounded-sm px-3 py-2 outline-hidden transition-colors data-[state=checked]:font-semibold sm:text-sm",
        // text color
        "text-heading",
        // disabled
        "data-disabled:pointer-events-none data-disabled:text-faint data-disabled:hover:bg-none dark:data-disabled:text-muted",
        // focus
        "focus-visible:bg-surface-alt dark:focus-visible:bg-surface",
        // hover
        "hover:bg-surface-alt dark:hover:bg-surface",
        className,
      )}
      {...props}
    >
      <SelectPrimitives.ItemText className="flex-1 truncate">
        {children}
      </SelectPrimitives.ItemText>
      <SelectPrimitives.ItemIndicator>
        <RiCheckLine
          className="size-5 shrink-0 text-fg"
          aria-hidden="true"
        />
      </SelectPrimitives.ItemIndicator>
    </SelectPrimitives.Item>
  )
})

SelectItem.displayName = "SelectItem"

const SelectSeparator = React.forwardRef<
  React.ElementRef<typeof SelectPrimitives.Separator>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitives.Separator>
>(({ className, ...props }, forwardedRef) => (
  <SelectPrimitives.Separator
    ref={forwardedRef}
    className={cx(
      // base
      "-mx-1 my-1 h-px",
      // background color
      "bg-line-strong",
      className,
    )}
    {...props}
  />
))

SelectSeparator.displayName = "SelectSeparator"

export {
  Select,
  SelectContent,
  SelectGroup,
  SelectGroupLabel,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
}
