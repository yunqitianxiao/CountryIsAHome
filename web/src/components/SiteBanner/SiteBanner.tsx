/**
 * 「人人可用」定位条 —— **一份文案，五页共用**。
 *
 * ★抽成组件的理由不是"复用"，是**判据会漂**：这段话被判据双向钉住
 *   （「人人可用」必须在、「教学」必须不在，且必须说清门槛为零），
 *   而它现在出现在主页、智能体页、引导页、画像页、游戏页上。
 *   各页抄一份的话，改的时候必然漏一处 —— 漏掉的那一页上，用户读到的
 *   就是另一套说法，而判据只在其中一页上量，全绿。
 *
 * ★措辞里**不写"右栏"**：主页是卡式导航层，没有右栏。写死方位的那半句
 *   在四个页面上都是错的。判据只要求"说清门槛为零"，方位词不在其中 ——
 *   所以删掉它不改判据，只是让它在这五页上都成立。
 */
export function SiteBanner() {
  return (
    <section
      id="siteBanner"
      className="card rounded-lg border border-[var(--rule)] bg-[var(--card)] p-3"
      aria-label="本站定位"
    >
      <p className="text-sm text-[var(--ink-2)]">
        <span className="mr-2 font-semibold text-[var(--accent)]">人人可用</span>
        <strong>打开就能用</strong>
        ：不用注册、不用登录、不用装东西，也不用先懂金融术语。不填 Key 也能查词、看图谱、跑 K
        线预测；想要会对话的助手，就填一把<strong>你自己的</strong> DeepSeek Key
        —— 它只存在本机，页面上只显示掩码。
      </p>
    </section>
  )
}
