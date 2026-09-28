// Tremor Tabs [v1.0.0]

import React from "react"
import * as TabsPrimitives from "@radix-ui/react-tabs"

import { cx } from "../../utils/cx"

const Tabs = (
  props: Omit<
    React.ComponentPropsWithoutRef<typeof TabsPrimitives.Root>,
    "orientation"
  >,
) => {
  return <TabsPrimitives.Root tremor-id="tremor-raw" {...props} />
}

Tabs.displayName = "Tabs"

type TabsListVariant = "line" | "solid"

const TabsListVariantContext = React.createContext<TabsListVariant>("line")

interface TabsListProps
  extends React.ComponentPropsWithoutRef<typeof TabsPrimitives.List> {
  variant?: TabsListVariant
}

const variantStyles: Record<TabsListVariant, string> = {
  line: cx(
    // base
    "flex items-center justify-start border-b",
    // border color
    "border-line-subtle",
  ),
  solid: cx(
    // base
    "inline-flex items-center justify-center rounded-md p-1",
    // background color
    "bg-surface-alt dark:bg-surface",
  ),
}

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitives.List>,
  TabsListProps
>(({ className, variant = "line", children, ...props }, forwardedRef) => (
  <TabsPrimitives.List
    ref={forwardedRef}
    className={cx(variantStyles[variant], className)}
    {...props}
  >
    <TabsListVariantContext.Provider value={variant}>
      {children}
    </TabsListVariantContext.Provider>
  </TabsPrimitives.List>
))

TabsList.displayName = "TabsList"

function getVariantStyles(tabVariant: TabsListVariant) {
  switch (tabVariant) {
    case "line":
      return cx(
        // base
        "-mb-px items-center justify-center whitespace-nowrap border-b-2 border-transparent px-3 pb-2 text-sm font-medium transition-all",
        // text color
        "text-muted",
        // hover
        "hover:text-fg dark:hover:text-faint",
        // border hover
        "hover:border-line dark:hover:border-line-strong",
        // selected
        "data-[state=active]:border-accent data-[state=active]:text-accent",
        "dark:data-[state=active]:border-accent dark:data-[state=active]:text-accent",
        // disabled
        "data-disabled:pointer-events-none",
        "data-disabled:text-fg dark:data-disabled:text-fg",
      )
    case "solid":
      return cx(
        // base
        "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1 text-sm font-medium ring-1 ring-inset transition-all",
        // text color
        "text-muted",
        // hover
        "hover:text-fg dark:hover:text-fg",
        // ring
        "ring-transparent",
        // selected
        /* ★`bg-white` 换成语义 token：亮色的 `--card` 就是 `#ffffff`
           （见 `index.css` 语义层 `--surface: var(--card)`），所以**像素上等价**，
           但从此不再是一个写死的色 —— 写死的那一份在亮色下"恰好是对的"，
           一旦有人调 `--card`（比如把它往 --n-50 挪一点），
           这个"选中态"会**继续是纯白**，而它脚下那张卡的底色已经变了。
           ★`dark:` 那一半原来就是 token（`bg-canvas`），不动。 */
        "data-[state=active]:bg-surface data-[state=active]:text-heading data-[state=active]:shadow-sm",
        "dark:data-[state=active]:bg-canvas dark:data-[state=active]:text-heading",
        // disabled
        "data-disabled:pointer-events-none data-disabled:text-faint data-disabled:opacity-50 dark:data-disabled:text-muted",
      )
  }
}

/* ★焦点环在这里**就地**写、不从 `utils/focusRing.ts` 引：那个文件里是
   `outline-blue-500 dark:outline-blue-500` —— 一个写死的蓝，在这个绿灰调的站里
   是第三种颜色，且不随主题翻转。就地写只有一个颜色声明（`outline-accent`），
   深浅两套各自由 token 决定，不会出现"两条环按生成顺序决胜"。
   （`utils/focusRing.ts` 不在本次可改文件清单里 ⇒ 交付说明里已列为待协调项。） */
const tabFocusRing =
  "outline outline-offset-2 outline-0 focus-visible:outline-2 outline-accent"

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitives.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitives.Trigger>
>(({ className, children, ...props }, forwardedRef) => {
  const variant = React.useContext(TabsListVariantContext)
  return (
    <TabsPrimitives.Trigger
      ref={forwardedRef}
      className={cx(getVariantStyles(variant), tabFocusRing, className)}
      {...props}
    >
      {children}
    </TabsPrimitives.Trigger>
  )
})

TabsTrigger.displayName = "TabsTrigger"

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitives.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitives.Content>
>(({ className, ...props }, forwardedRef) => (
  <TabsPrimitives.Content
    ref={forwardedRef}
    className={cx("outline-hidden", tabFocusRing, className)}
    {...props}
  />
))

TabsContent.displayName = "TabsContent"

export { Tabs, TabsContent, TabsList, TabsTrigger }
