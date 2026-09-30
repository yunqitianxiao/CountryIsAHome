/**
 * `/verify` 产品验真。
 *
 * ★★这一页的位置很窄，窄到必须先说清楚"它不做什么"：
 *   它**只校验编码的形状**，不查任何一只产品、不给任何一只产品背书、
 *   也不说"这只可以买"。真伪的判断权在**中国理财网**（监管指定的行业统一
 *   信息披露渠道，2026-09-01 起全面上线），本站只是把你送到那个入口，
 *   并顺手把"抄编码时最容易抄错的几种样子"替你归整好。
 *
 * ★为什么值得单独开一页：**反飞单**这件事，全站只有这一页在正面做。
 *   "飞单"指的是银行员工在营业场所私自销售非本行发行、非本行授权的第三方产品；
 *   而识别它最硬的一招，就是拿产品说明书上的**登记编码**去官方平台对一对 ——
 *   查不到、或者查到的要素（名称、发行机构、风险等级）与说明书对不上，就是假的。
 *   这一招不依赖任何模型、不依赖本站有任何数据，**只依赖一个官方入口**。
 *
 * ★纯前端、零后端：校验是 `pcode.ts` 里的纯函数，编码**不落 localStorage、
 *   不发任何请求**（`pcode_selftest.mjs` 有一条判据逐字守着这一点）。
 *   理由与 `/assess` 同族：本站服务端零用户状态，而"你查过哪只产品"本身就是
 *   一份用户内容 —— 不存，比存下来再解释为什么存要省事得多，也安全得多。
 */
import * as React from "react"
import { Frame } from "../../agentui/Frame"
import { Panel, Well } from "../../agentui/State"
import {
  OFFICIAL_HOME, OFFICIAL_PILU, checkCode, kindText, lenNote,
} from "../../pcode"

export function Verify({ probe = false }: { probe?: boolean }) {
  const [raw, setRaw] = React.useState("")
  const [copied, setCopied] = React.useState(false)
  const v = React.useMemo(() => checkCode(raw), [raw])

  const copy = () => {
    if (!v.ok) return
    // ★剪贴板在有的环境里**根本没有**（无头 firefox、非 https 的旧内核）：
    //   那种情况下不报错、不假装复制成功 —— 只是不显示"已复制"。
    const c = navigator.clipboard
    if (c && typeof c.writeText === "function") {
      void c.writeText(v.code).then(() => setCopied(true), () => setCopied(false))
    }
  }

  return (
    <Frame
      probe={probe} title="产品验真"
      sub="理财产品登记编码 · 本站只校验形状，真伪由官方平台判定"
      footExtra={<>这一页<b>不认识任何一只产品</b>，也不给任何一只产品背书。它不构成投资建议。</>}
    >
      <Panel
        id="checkPanel" title="把说明书上的登记编码抄进来"
        note={<><code className="tnum">C</code> 或 <code className="tnum">Z</code> 开头，全长 14 或 15 位</>}
      >
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => e.preventDefault()}
        >
          <label htmlFor="pcodeInput" className="sr-only">理财产品登记编码</label>
          <input
            id="pcodeInput" value={raw} onChange={(e) => { setRaw(e.target.value); setCopied(false) }}
            inputMode="text" autoComplete="off" spellCheck={false}
            placeholder="例如 C1128826000131 或 Z7003323000081"
            className="tnum min-w-[240px] flex-1 rounded border border-[var(--rule)] bg-[var(--card)] px-2 py-1.5 text-sm text-[var(--ink)] placeholder:text-[var(--ink-3)]"
          />
          <button
            type="button" onClick={copy} disabled={!v.ok}
            className="rounded border border-[var(--rule)] bg-[var(--card)] px-3 py-1.5 text-sm text-[var(--ink-2)] disabled:opacity-40"
          >
            {copied ? "已复制" : "复制编码"}
          </button>
        </form>

        {/* ★判词区：三种状态各一句，**没有**第四种。空着的时候不显示"不像"，
            显示"不像"的时候不显示"形状对了" —— 混在一起就是一句永远看不出错的话。 */}
        <div
          id="pcodeVerdict" data-ok={v.ok ? "1" : "0"}
          className={
            "mt-2 rounded border p-2 text-sm " +
            (v.code === ""
              ? "border-[var(--rule)] bg-[var(--paper)] text-[var(--ink-3)]"
              : v.ok
                ? "border-[var(--accent)] bg-[var(--paper)] text-[var(--ink)]"
                : "border-[var(--status-danger)] bg-[var(--paper)] text-[var(--ink)]")
          }
        >
          <div data-k="reason">{v.reason}</div>
          {v.ok ? (
            <ul className="mt-1 list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
              <li data-k="kind">{kindText(v.kind)}</li>
              <li data-k="len">{lenNote(v.len)}</li>
              <li data-k="note">
                形状对了<b>不等于</b>产品是真的。下一步必须去官方平台把这一串粘进查询框：
                查不到、或查到的名称 / 发行机构 / 风险等级与说明书对不上，就别买。
              </li>
            </ul>
          ) : null}
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <a
            id="pcodeOfficial" href={OFFICIAL_PILU} target="_blank" rel="noopener noreferrer"
            className="rounded border border-[var(--accent)] bg-[var(--card)] px-3 py-1.5 text-sm font-semibold text-[var(--accent)] no-underline"
          >
            打开官方信息披露平台 ↗
          </a>
          <a
            href={OFFICIAL_HOME} target="_blank" rel="noopener noreferrer"
            className="rounded border border-[var(--rule)] bg-[var(--card)] px-3 py-1.5 text-sm text-[var(--ink-2)] no-underline"
          >
            中国理财网首页 ↗
          </a>
          <span className="text-xs text-[var(--ink-3)]">
            两个地址都是 <code className="tnum">chinawealth.com.cn</code>；
            别的域名一律不是官方平台。
          </span>
        </div>
      </Panel>

      <Panel id="rulePanel" title="这串编码是怎么构成的">
        <Well>
          <ul className="list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
            <li>
              登记编码由<b>全国银行业理财信息登记系统</b>赋予，一码一产品，相当于产品的身份证号。
              监管要求：未在该系统登记并取得编码的理财产品，<b>不得发行和销售</b>。
            </li>
            <li>
              首字母：<b>C</b> = 发行主体是银行（含银行资管部）；<b>Z</b> = 银行理财子公司。
              后面跟 13 或 14 位数字，所以全长是 14 或 15 位。
            </li>
            <li>
              它和"产品代码"不是一回事：产品代码由产品管理人自己按名称、年份、期次编，
              便于内部管理；登记编码是登记系统给的，可以用来对外验真。
            </li>
            <li>
              在哪儿找：产品说明书的标题下方或"产品基本信息"里，一串 C 或 Z 开头的代码。
            </li>
          </ul>
        </Well>
      </Panel>

      <Panel id="notDoPanel" title="这一页不做什么">
        <Well>
          <ul className="list-disc pl-5 text-xs leading-relaxed text-[var(--ink-2)]">
            <li><b>不代查</b>：本站不请求官方平台、不缓存任何产品数据 —— 页面上没有一个数是本站查来的。</li>
            <li><b>不落库</b>：编码不写 localStorage、不进任何日志文件。关掉标签页，它就没了。</li>
            <li><b>不参数化深链</b>：官方查询是一个输入框，没有公开文档承诺它接受 URL 参数。
              照猜出来的参数拼链接，最坏的结果是静默跳到官方首页 —— 你以为查了，其实没查。</li>
            <li><b>不推荐、不比价、不排序</b>：这一页一个产品名都没有，也不给任何购买入口。
              做这三项要持牌，本站不持有任何牌照。</li>
            <li><b>不接银行内部系统</b>：账户级与交易级的接口（查余额、下单）需要企业主体、
              ICP/EDI 与公安备案、并在银行建档走审批 —— 个人与本站形态都够不到，
              也不该想办法绕过（模拟登录抓取是违法，不是灰色）。</li>
          </ul>
        </Well>
      </Panel>
    </Frame>
  )
}

export default Verify
