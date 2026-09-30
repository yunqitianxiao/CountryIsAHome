/**
 * 大模型 Key 卡。
 *
 * ★纪律：**页面上永远只出现掩码**。完整 key 前端拿不到、也不该拿 ——
 *   判据拿一把哨兵 key 把整张卡的文字扫一遍，找得到就算泄露。
 *
 * ★这里修掉了旧页面一个真实的 bug：旧 `keySend()` 发的是 `JSON.stringify({})`，
 *   **从来没读过输入框的值**，而服务端对空值回 400「key 不能为空」——
 *   也就是网页上点「保存」必然是坏的。<u>152 条判据全绿的时候它 100% 坏</u>，
 *   因为那条判据是直接对接口打的，不经过这个按钮。
 *   所以这里**必须**把 value 放进 `{key: value}`，并且**要有一条判据真的驱动这个按钮**。
 */
import * as React from "react"
import { getKey, saveKey, clearKey, type KeyState } from "../../api"

/** ★探针模式**必须用同步 XHR**：整条渲染层判据都押在"beacon 在 firefox 退出前
 *  发出去"上，而 beacon 是同步跑的。这里换成异步 fetch 就变成"key 状态"和"几何"
 *  两件事抢同一个退出窗口 —— 抢输了 beacon 根本不发，所有渲染判据一起**消失**
 *  （不是变红，是消失）。同源 GET，代价可以忽略。 */
export function readKeySync(): KeyState | null {
  try {
    const x = new XMLHttpRequest()
    x.open("GET", "/api/key", false)
    x.send(null)
    return JSON.parse(x.responseText) as KeyState
  } catch {
    return null      // 读不到就保持"检查中…"：状态未知比编一个状态好
  }
}

const EMPTY: KeyState = { configured: false, mask: null, source_text: "检查中…" }

export function KeyCard({ probe = false, onChanged }: { probe?: boolean; onChanged?: () => void }) {
  const [s, setS] = React.useState<KeyState>(() => (probe ? readKeySync() : null) || EMPTY)
  const [val, setVal] = React.useState("")
  const [out, setOut] = React.useState<{ bad: boolean; text: string } | null>(null)
  const [busy, setBusy] = React.useState(false)
  const inputRef = React.useRef<HTMLInputElement>(null)

  React.useEffect(() => {
    if (probe) return
    getKey().then(setS).catch(() => {})
  }, [probe])

  // 页头那枚「大模型未配置」徽章点一下 → 滚到这里并把光标放进输入框。
  // "告诉你缺什么"和"让你就地补上"应该是同一个地方。
  React.useEffect(() => {
    const fn = () => {
      document.getElementById("keyCard")?.scrollIntoView({ block: "center", behavior: "smooth" })
      inputRef.current?.focus({ preventScroll: true })
    }
    window.addEventListener("licai:focusKey", fn)
    return () => window.removeEventListener("licai:focusKey", fn)
  }, [])

  async function send(method: "POST" | "DELETE") {
    setBusy(true)
    try {
      const next = method === "POST" ? await saveKey(val.trim()) : await clearKey()
      const err = (next as unknown as { error?: string }).error
      if (err) {
        setOut({ bad: true, text: err })
      } else {
        setS(next)
        setVal("")
        setOut({ bad: false, text: method === "DELETE" ? "已清除网页保存的那把。" : "已保存，这次对话就会用它。" })
        onChanged?.()          // 页头那枚徽章跟着变 —— "真的存进去了"最直观的回执
      }
    } catch (e) {
      setOut({ bad: true, text: String((e as Error)?.message || e) })
    }
    setBusy(false)
  }

  const shellShadowed = !!(s as unknown as { shell_mask?: string }).shell_mask && (s as unknown as { source?: string }).source === "web"

  return (
    <section className="card keycard" id="keyCard" aria-label="大模型 Key 设置">
      <div className="chd">
        <h2>大模型 Key</h2>
        <span className="tag">存在本机配置文件</span>
      </div>
      <div className="kbody">
        {/* ★这里面**永远只放掩码** */}
        <div className={"kstate " + (s.configured ? "on" : "off")} id="kstate">
          <i className="kd" />
          {s.configured ? (
            <>已配置 <code>{s.mask}</code> · 来源：{s.source_text}</>
          ) : (
            <>还没配 —— 对话会降级成只答概念</>
          )}
        </div>
        <div className="inrow">
          <input
            id="kinput" type="password" autoComplete="off" spellCheck={false}
            // ★密码框没有标签尤其糟：读屏软件只会念"密码 编辑框"，
            //   用户不知道要填的是**哪一家**的 key。placeholder 不算标签（打字就没）。
            aria-label="DeepSeek API key"
            ref={inputRef} value={val} onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); send("POST") }
            }}
            placeholder="sk-… 填 DeepSeek 的 key"
          />
          <button id="ksave" type="button" disabled={busy} onClick={() => send("POST")}>保存</button>
          <button id="kclear" type="button" disabled={busy} onClick={() => send("DELETE")}>清除</button>
        </div>
        <div className="hint">
          只写进本机的一个配置文件里，页面上永远只显示掩码；保存后<strong>立刻生效</strong>，不用重启。
          <strong>没有 key 也能用</strong>，只是对话会降级成只答概念。
        </div>
        <div id="kwarn">
          {/* ★环境变量里那把：只在"它确实存在、而且现在生效的不是它"时提醒。
              不提醒的话，"我在 ~/.bashrc 里明明配过"和"怎么不生效"会同时成立。 */}
          {shellShadowed ? (
            <div className="kwarn">
              注意：你的系统环境变量里还有一把（<code>{(s as unknown as { shell_mask: string }).shell_mask}</code>），
              它已经被本机配置文件里那把盖住了 —— 本站不用它。
            </div>
          ) : null}
        </div>
        <div id="kout" aria-live="polite" aria-busy={busy}>
          {out ? <div className={"ksay" + (out.bad ? " bad" : "")}>{out.text}</div> : null}
        </div>
      </div>
    </section>
  )
}
