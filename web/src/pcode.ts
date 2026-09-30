/**
 * `/verify` 理财产品登记编码 —— **只做格式校验**的那一层。
 *
 * ★★这一层最重要的不是"校验"，是**它不做什么**：
 *     · 不查库、不发请求、不认识任何一只产品 —— 它看的是**形状**；
 *     · 判断"这只产品是不是真的"这件事，**只有官方平台能回答**，本站不代答。
 *   这两句不是谦虚，是这一页能不能存在的理由：本站没有、也不该有理财产品数据。
 *
 * ★格式规则的出处（不是我猜的，可核）：
 *     · 登记编码由**全国银行业理财信息登记系统**赋予，一码一产品，相当于产品的身份证号；
 *     · 首字母 `C` = 发行主体是银行（含银行资管部），`Z` = 银行理财子公司；
 *       其余 13 或 14 位是数字 ⇒ 全长 14 或 15 位。
 *   （"14 位 / 15 位"那一句的公开口径见 `lenNote`：15 位多见于私行/机构/同业专属。
 *     这是**教学口径**，不是监管原文 —— 所以页面上要写明，判据也**不**去钉它。）
 *
 * ★★为什么**没有**参数化深链（`?code=xxx`）：
 *   官方查询入口是页面上一个输入框，我没找到任何公开文档承诺它接受 URL 参数。
 *   照着一个猜出来的参数拼链接，最坏的结果是**静默跳到官方首页** ——
 *   用户以为查了，其实什么都没查。那正是本站不做的"假装"。
 *   所以这一层只给**官方地址**，编码由用户粘贴（页面上有一键复制）。
 */

/** 登记编码的形状：C 或 Z 开头 + 13 或 14 位数字（全长 14 或 15）。 */
export const PCODE_RE = /^[CZ]\d{13,14}$/

/** 中国理财网（银行业理财登记托管中心运营，监管指定的行业统一信息披露渠道）。 */
export const OFFICIAL_HOME = "https://www.chinawealth.com.cn"

/**
 * 理财行业统一信息披露平台。★2026-09-01 起全面上线 —— 金融监管总局
 * 《银行保险机构资产管理产品信息披露管理办法》要求公募理财产品信息至少在
 * 该渠道披露。查**最新**的产品与净值，落点是这一个，不是上面那个门户首页。
 */
export const OFFICIAL_PILU = "https://xinxipilu.chinawealth.com.cn"

/** 形状对了之后**必须**跟着说的一句。少了它，"格式合法"会被读成"产品是真的"。 */
export const SHAPE_OK_NOTE =
  "形状对了不等于产品是真的 —— 是不是真的，只有官方平台能回答。"

export type Kind = "bank" | "bankwm"

export type CodeVerdict = {
  /** 形状是否合法。**与"这只产品真不真"无关。** */
  ok: boolean
  /** 归一化后的编码（去空格/连字符/全角、转大写），便于原样显示与复制。 */
  code: string
  /** 归一化后的长度（0 表示没输入）。 */
  len: number
  /** 首字母代表的发行主体类别；形状不合法时为 null（**不猜**）。 */
  kind: Kind | null
  /** 给人的一句话。★空串与"形状不对"是**两种**话，别合成一句。 */
  reason: string
}

/**
 * 归一化：全角 → 半角、去掉常见分隔符、转大写。
 *
 * ★为什么要做这一步：编码是从产品说明书上抄下来的，抄的时候带空格、带连字符、
 *   用中文输入法打出全角字母，三种都常见。**不归一化的话这三类的判词都是
 *   "不像登记编码"** —— 而用户明明抄对了。那不是校验严，是校验假。
 */
export function normalizeCode(raw: unknown): string {
  const s = String(raw ?? "")
    // 全角 ASCII（U+FF01–FF5E，含全角数字０-９ / 大写字母Ａ-Ｚ / 句点．）→ 半角
    // ★范围取 FF01–FF5E 这一整段，不是只取数字和字母：从 PDF 或网页上抄下来的
    //   编码常带着全角句点、全角括号，只转数字字母的话，`Ｃ1128．826` 这种会
    //   卡在句点上 —— 判据里有一条专门钉这个（"去空格/连字符/点/顿号"）。
    .replace(/[！-～]/g, (c) =>
      String.fromCharCode(c.charCodeAt(0) - 0xfee0))
    .replace(/[\s\-—_·.,，、]/g, "")
    .toUpperCase()
  return s
}

/** 首字母 → 发行主体类别。★不认识的字母返回 null，**不猜**。 */
export function kindOf(code: string): Kind | null {
  if (code.startsWith("C")) return "bank"
  if (code.startsWith("Z")) return "bankwm"
  return null
}

/** 给人的一句话：C / Z 各代表什么。 */
export function kindText(kind: Kind | null): string {
  if (kind === "bank") return "C 开头：发行主体是银行（含银行资管部）"
  if (kind === "bankwm") return "Z 开头：发行主体是银行理财子公司"
  return ""
}

/**
 * 长度的注解。★这一句是**教学口径**，不是监管原文 —— 公开资料的说法是
 * "15 位多见于私人银行 / 机构 / 银行同业专属产品"。判据**不**钉它的措辞，
 * 只钉 `lenNote` 对 14 / 15 都给得出话来（不给话才是漏洞）。
 */
export function lenNote(len: number): string {
  if (len === 14) return "14 位（首字母 + 13 位数字）：常见于面向个人客户销售的产品"
  if (len === 15) return "15 位（首字母 + 14 位数字）：公开资料称多见于私行 / 机构 / 同业专属产品"
  return ""
}

/** 唯一的出口：给一个原始输入，返回一份判词。 */
export function checkCode(raw: unknown): CodeVerdict {
  const code = normalizeCode(raw)
  if (!code) {
    return { ok: false, code: "", len: 0, kind: null, reason: "还没输入编码" }
  }
  if (!PCODE_RE.test(code)) {
    return {
      ok: false,
      code,
      len: code.length,
      kind: null,
      reason: "不像登记编码：应当是 C 或 Z 开头，后面跟 13 或 14 位数字（全长 14 或 15 位）",
    }
  }
  return {
    ok: true,
    code,
    len: code.length,
    kind: kindOf(code),
    reason: SHAPE_OK_NOTE,
  }
}
