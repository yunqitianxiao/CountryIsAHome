#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交互页的验收判据：重建 → 语法 → 面板/点开收起 → 静态内容 → 真浏览器渲染。

两条腿，缺一不可：
[1-3] 把页面里**真正要发布的那份 JS** 抠出来，套一个极简 DOM 桩跑一遍。
      凡是本该出现的文字没出现、或本该展开的东西没展开，就非零退出。
[4]   静态内容：读**产物文本**，**三张页面各验一遍**（graph.html / index.html / guide.html）。
      `<style>` 块数、外部引用计数、两套主题的文字×底色对比度、两张暗色表同值、
      底色覆盖（含"非三张底上压着字"的那些对）。不开浏览器、不跑 node，秒级。
      ★为什么是**三张**：这三页共用同一套 token，AA 那条轴是**站点级**的约定。
        只在 graph.html 上验，姊妹页会静默分叉 —— 实测 index.html / guide.html 的
        `--ink-3` 三对全低于 4.5（亮 3.28/3.61/3.41），而它们在[4]眼里从来没被量过。
      ★三页连跑时，★行自带页名：红必须说得出"是哪一页、哪条判据"。
[5]   真浏览器（firefox --headless）截图，让页面自己把结论画成一个色块，再读像素。
      桩只认 DOM 属性，看不见 CSS 优先级、布局、以及"字被 viewBox 缩小"这类事
      —— 本轮三个真故障（坐标没写、占位提示没藏住、面板不给 min-height:0）桩全都看不见，
      全是靠 [5] 才看见的。★所以 [5] 不是锦上添花，它看的是另一层。

─── 为什么不用肉眼 / 不靠"看起来对" ───
· 面板是拼字符串拼出来的，拼错了不会抛异常，只会静默少一段 ⇒ 必须断言"该有的文字在不在"
· 断言必须能输出"我错了"：--selftest 会故意注入十几种故障，
  每一种都必须让判据变红；有一种红不了，就说明那条断言是空的。
· ★`h.includes('')` 恒为真 —— 第一版判据用 `h.includes(例子文本)` 检查字段，
  数据里字段为空时等于什么都没查，对照组 2 因此假绿。现在先要求"字段真的有值"，再查渲染结果。
· ★`members` 为空数组时 for 循环体一次都不跑，判据同样假绿。
  现在改成数 `data-go` 按钮的个数和 members.length 比，不是看有没有标题。
· ★桩里的 clientWidth/clientHeight 是**编的**，所以"1 用户单位 = 1 像素"这种事它在结构上
  就判不了对错，只能判"自洽"。[5] 量的是真画布。
"""
import json
import os
import json
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(HERE, 'graph.html')
DATA = os.path.join(HERE, 'graph_data.json')

# ★图例该有几个 chip：**从数据里取**，不写死。
#   原来是写死的 `=== 6`。2026-09-28 给图谱加了第 7 个色族（O/P → 家庭与县域），
#   它当场红了 —— 红的**不是渲染**，是这个常量过期了。照读数把 6 改成 7 也能"修好"，
#   但这条判据的本意是「图例渲染出的 chip 数 == 数据里的族数」，不是「恰好 6 个」。
#   ⇒ 期望端回到数据源（graph_data.json），测量端仍是 DOM 里的 children.length。
#     两层不同源：以后再加族，这条判据不用改；而模板真把 chip 渲染漏了，它照样红。
FAM_N = len(json.load(open(DATA, encoding='utf-8'))['families'])

# ── DOM 桩：够跑通建图、render()、select()、panelHTML() 就行 ──
# ★桩垫片里那两个数的**唯一**出处（垫片本体在下面 `STUB` 末尾）。4g 会拿它和产物 CSS 逐格比。
STUB_FS = {'.lbl': '10.5', '.lbl.big': '12'}
STUB = r"""
const fs = require('fs');
/* D / REL_ORDER / NB / panelHTML / select 等都由页面自己的 JS 定义（它自带 __DATA__），
   这里只提供它需要的那几样浏览器 API，所以桩里不要重复声明 D。 */
let elCount = 0, attrCount = 0, listenerCount = 0;
const mkEl = (tag) => ({
  tagName: tag, children: [], style: {}, dataset: {}, _a: {}, _html: '', hidden: false,
  clientWidth: 900, clientHeight: 620, scrollTop: 0, parentNode: null, value: '',
  setAttribute(k, v) {
    if (v === undefined || v === null) throw new Error(tag + ' 的属性 ' + k + ' 被设成 ' + v);
    this._a[k] = String(v); attrCount++;
  },
  getAttribute(k) { return this._a[k]; },
  append(...cs) { for (const c of cs) { c.parentNode = this; this.children.push(c); elCount++; } },
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
  // ★1a-ii 起页面要这个：`FS` 那段会**临时**造一个 `<text>` 探针、量完就摘掉
  //   （量 CSS 生效字号唯一的办法就是挂上去 `getComputedStyle`）。
  //   ★**不做** `elCount++` 也不做 `elCount--`：`appendChild` 本来就不计数，
  //     而"创建元素 N 个"是给人看的**读数**，1a-ii 前后必须可比 —— 悄悄 +1 会让人
  //     以为页面结构变了。（真正新增的结构只有那一个**瞬时**探针，它不进最终 DOM。）
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
  addEventListener(t) { listenerCount++; },
  set onclick(f) { this._onclick = f; }, get onclick() { return this._onclick; },
  getBoundingClientRect() { return {left: 0, top: 0, width: 900, height: 620}; },
  querySelector() { return null; }, querySelectorAll() { return []; },
  closest() { return null; }, focus() {}, blur() {},
  set innerHTML(v) { this._html = String(v); }, get innerHTML() { return this._html; },
  set textContent(v) { this._text = String(v); }, get textContent() { return this._text; },
  set className(v) { this._a.class = v; }, get className() { return this._a.class; },
  classList: (() => { const s = new Set(); return {
    toggle(c, on) { on === undefined ? (s.has(c) ? s.delete(c) : s.add(c)) : (on ? s.add(c) : s.delete(c)); },
    add(c) { s.add(c); }, remove(c) { s.delete(c); }, contains(c) { return s.has(c); },
    get size() { return s.size; } }; })(),
});
const REG = new Map();
const byIdEl = id => { if (!REG.has(id)) REG.set(id, mkEl('div')); return REG.get(id); };
for (const id of ['net','hint','reset','panel','close','empty','detail','legend','q','hits','stage'])
  byIdEl(id);
global.document = {
  createElement: mkEl, createElementNS: (ns, t) => mkEl(t),
  getElementById: byIdEl, addEventListener() {},
};
global.window = { _l: {}, addEventListener(t, f) { this._l[t] = f; } };
""" + (
    # ── ★桩的 `getComputedStyle` 垫片 ──
    # 页面从 2b-1a-ii 起**从 CSS 现读**字号（`legible()` 里不再是字面量），而桩**没有
    # CSS 引擎**（见 4g 那段与 ㉕）⇒ 桩必须替浏览器把这个 API 补上，否则页面一加载就
    # `ReferenceError`、**整层死掉**（不是"少一条注入"）。
    # ★它是**硬写的**，所以它**不是产品的一部分**，是"脚手架对页面的承诺"。
    #   承诺要与产物 CSS 相等 —— 4g 那条判据守的正是「**桩不许对页面说谎**」，
    #   **不是**「页面画对了」（页面画没画对由真 firefox 那一层守）。
    # ★垫片自己锚不在现实上（它没读过真样式表）⇒ 它间接地靠 4g 的
    #   「真 firefox 实测字号 == 产物 CSS 声明值」那条断言钉住。三层链：
    #   **现实 ⟷ 产物 CSS ⟷ 垫片**，缺一头就变成"两边一起漂、判据恒绿"。
    # ★★读数会变的**一个**已知量（实测，2026-09-21）：`创建元素 1501 个 / 属性 N 次` 里的
    #   属性数 **+2**（改前 6894 → 改后 6896），元素数**不变**（1501）。原因：页面那段
    #   `FS` 会临时造一个 `<text>` 探针、`setAttribute('class', …)` 两次量字号再摘掉；
    #   `appendChild` 本来就不计元素数，而两次 `setAttribute` 各计一次。
    #   ⇒ 谁要是拿这行读数去比对 1a-ii 前后，**只有属性数该差 2**；元素数或监听数一变，
    #     那是别的改动，别算到这个头上。
    "\nglobal.getComputedStyle = (el) => {\n"
    "  const cls = String((el && el._a && el._a.class) || '');\n"
    "  return {fontSize: (/\\bbig\\b/.test(cls) ? '%s' : '%s') + 'px'};\n};\n"
    % (STUB_FS['.lbl.big'], STUB_FS['.lbl']))

# ── 故障注入：每一条都必须让下面的断言变红（--selftest 用） ──
FAULTS = {
  '1 抽掉一个词条的「一句话」':      "D.nodes.find(n => n.kind === 'term').f['一句话'] = '';",
  '2 清空一个分类节点的 members':    "D.nodes.find(n => n.kind === 'cat').members = [];",
  '3 关系名换成 CSS 名（曾真的错成这样）': "REL_ORDER.splice(0, 3, 'belong', 'prereq', 'contrast');",
  '4 某个节点的面板渲染成空串':      "const _p = panelHTML; panelHTML = n => n.id === '基金' ? '' : _p(n);",
  '5 面板里混进 undefined':         "const _p2 = panelHTML; panelHTML = n => n.id === '复利' ? _p2(n) + 'undefined' : _p2(n);",
  # 6/7/8 是补几何判据时一起加的：6 复刻本轮真实故障（坐标算了但没往元素上写），
  # 7/8 分别覆盖"单个节点漏坐标"和"标签漏坐标"——三种漏法症状完全不同，一条断言盖不住。
  '6 所有节点画在原点（真实故障）':   "for (const g of nodeEls) { g.children[0]._a.cx = '0'; g.children[0]._a.cy = '0'; }",
  '7 一个节点的坐标没写上去':        "delete nodeEls[3].children[0]._a.cx;",
  '8 标签没拿到坐标':                "for (const g of nodeEls) { delete g.children[1]._a.x; delete g.children[1]._a.y; }",
  # 9-12 是补视图判据时一起加的。9 复刻"缩略时照画标签"（全图灰糊的真身），
  # 10 拆开验 A5 不是靠 9 才红的，11 验 A4 的对照组不是空转，12 验比例那条。
  # ★9/11 改的是 legible / onclick，都必须是可重新赋值的绑定 —— 所以模板里
  #   legible 写成 function 声明而不是 const 箭头函数，注入才不至于把脚本打崩
  #   （打崩了也"变红"，但那是假红：根本没走到断言）。
  '9 缩略时照画标签（真实故障）':      "legible = () => true; fit();",
  '10 看全图按钮失灵（点了不缩）':     "document.getElementById('reset').onclick = () => select(null);",
  '11 打开时一个标签都不画':         "legible = () => false; render();",
  # 12 只坏比例不坏字号：这样红的必然是 A5 那条比例断言，而不是被前面那条挡掉。
  #    坏成比例不相等的 fit：字还是 1.1px（标签该灭），但 viewBox 被拉变形。
  '12 看全图后 viewBox 比例不对':     "fit = () => { view = {x: view.x, y: view.y, w: 8763, h: 8763}; applyView(); render(); };",
  '13 拖窗口把字号改了':            "window.addEventListener('resize', fit);",
  # 14/15 是补"在眼前"那条闸时加的：14 让视图外的标签照画（判据必须能指出来），
  # 15 让第一屏一个标签都没有（验证 A4 那两条**形状**下限不是空话 ——
  #   它曾经是"至少 5 个能读的词"，那个 5 已按"读数不是设计意图"退役，判定见 A4 的注释）。
  '14 视图外的标签照画':            "onScreen = () => true; render();",
  '15 打开时视图内标签全不画':       "onScreen = () => false; render();",
  # ★★这里**没有**"只改 CSS 字号"那条注入 —— 它挪去了渲染层（见 `cripples` 里那条）。
  #   理由是它在这层**根本说着说着就把自己打崩了**：它要 `document.head`/`createElement`，
  #   而本层的桩 DOM 没有这两个东西（桩只造它断言要用到的那点结构）。
  #   实测：退出码 1 且 stdout 一个 ★ 都没有 ⇒ 走的是 `crashed` 那条分支、
  #   打印"⊘ 注入把脚本打崩了，不是变红"。**没变红不等于判据不行，是这条注入放错了层。**
  #   ⇒ 一条纪律：**注入必须放在"它要量的那个能力真的存在"的那一层**。
  #     桩没有 CSS 引擎 ⇒ 它造不出"字号被改小"这件事，只能造"JS 常量变了个数"，
  #     而那恰好是**另一条**注入（`字号两边对不上`）在干的事。
}

CHECKS = r"""
const nBtn = (h, id) => (h.match(new RegExp('data-go="' + id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '"', 'g')) || []).length;
function F_esc(s) {
  return s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))
          .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}
const bad = [];
let asserts = 0;
const want = (cond, msg) => { asserts++; if (!cond) bad.push(msg); };

/* A. 建图阶段真的建出来了 */
const vb = (byIdEl('net')._a.viewBox || '').split(/[\s,]+/).filter(s => s);
want(vb.length === 4 && vb.every(v => Number.isFinite(Number(v)))
     && Number(vb[2]) > 0 && Number(vb[3]) > 0,
     'viewBox 不合法（图不会有内容）：' + JSON.stringify(byIdEl('net')._a.viewBox));
want(byIdEl('legend').children.length === __FAM_N__, '图例 chip 数 = ' + byIdEl('legend').children.length + '，应为 __FAM_N__');
want(byIdEl('empty').hidden === false, '空态初始应为可见');

/* A2. 几何：坐标真的写到 SVG 元素上了吗
   ★这一段是本轮补的。补之前 289 个节点、625 条边全画在用户坐标原点，
     页头/图例/解释面板全都正常，屏幕上只剩中心一个墨点 —— 而当时的断言
     （B/C 两段）只读面板拼出来的字符串，一次都没读元素上的坐标，所以全绿放行。
   ★判据必须读「产物」（元素上的属性），不能读「输入」（D.nodes 里的 x/y）：
     D.nodes 里的坐标一直是好的，坏的始终是"没往元素上写"这一步。 */
const num = v => { const n = Number(v); return Number.isFinite(n) ? n : null; };
const ptp = a => Math.max(...a) - Math.min(...a);
const circles = nodeEls.map(g => g.children[0]);
const labels = nodeEls.map(g => g.children[1]);
const cx = circles.map(c => num(c._a.cx)), cy = circles.map(c => num(c._a.cy));
want(cx.every(v => v !== null) && cy.every(v => v !== null),
     '有节点的 circle 没拿到坐标（cx 有效 ' + cx.filter(v => v !== null).length + '/' + D.nodes.length + '）');
if (cx.every(v => v !== null) && cy.every(v => v !== null)) {
  want(ptp(cx) > 100 && ptp(cy) > 100,
       '所有节点挤在一处（x 跨度 ' + ptp(cx).toFixed(1) + '，y 跨度 ' + ptp(cy).toFixed(1)
       + '）—— 坐标算出来了但没写到元素上');
  D.nodes.forEach((n, i) => want(cx[i] === n.x && cy[i] === n.y,
       n.id + ' 元素坐标 (' + cx[i] + ',' + cy[i] + ') 与布局坐标 (' + n.x + ',' + n.y + ') 不符'));
}
D.nodes.forEach((n, i) => want(num(labels[i]._a.x) === n.x && num(labels[i]._a.y) === n.y,
     n.id + ' 的标签没跟上节点坐标（289 个标签会全堆在原点）'));
D.links.forEach((l, i) => {
  const e = linkEls[i]._a, a = byId.get(l.s), b = byId.get(l.t);
  want(num(e.x1) === a.x && num(e.y1) === a.y && num(e.x2) === b.x && num(e.y2) === b.y,
       '边 ' + l.s + '→' + l.t + ' 的端点与节点坐标不符（线会连错）');
});

/* A3. 打开时必须是 1:1：1 用户单位 = 1 屏幕像素，CSS 里写的 10.5px 才是真 10.5px。
   ★同轮补的。补之前 view 是整个包围盒（6247 单位宽铺进 900px），10.5px 的字
     被 viewBox 压成 1.7px —— 图上"有节点"但一个词都读不出来。 */
const CW = 900, CH = 620;                    // 桩给画布的尺寸，和 STUB 里的 clientWidth/Height 对齐
want(Math.abs(view.w - CW) < 1e-6 && Math.abs(view.h - CH) < 1e-6,
     '打开时不是 1:1（view.w=' + view.w.toFixed(1) + '，画布 ' + CW + '）—— CSS 里的字号会被缩小');

/* A4. 不变量：画出来的每个标签，屏幕字号都得 ≥ 8px（糊字不如不画）**且**在眼前。
   ★"在眼前"是同轮补的：光有字号闸，1:1 停在「基金」上时第一屏只有 2 个标签 ——
     旧规则只放全局枢纽词（deg>=11），而基金旁边那些词恰恰度数都小，放大之后
     看的正是它们，它们反倒没名字，屏幕上只剩线和点。 */
/* ★1a-ii：两个字号**不在这里再写一遍** —— `FS` 就是页面自己从 CSS 现读的那一份。
   再写一遍就多出**第二份手写拷贝**（而且它在桩里，产物里看不见、`grep` 也 grep 不到），
   那正是 4g(α) 要根除的坏法：两份一旦错开，页面会"明明够大却被藏 / 明明糊了却画出来"，
   **两边各自自洽**。谓词仍与建类处、`legible()` 同形（4g-2 那条钉住三处一致）。 */
const labelPx = n => (n.deg >= 11 || n.kind === 'cat' ? FS.big : FS.base) * (svg.clientWidth / view.w);
const opacity = g => Number(g.__t.style.opacity);
const inView = n => n.x >= view.x && n.x <= view.x + view.w && n.y >= view.y && n.y <= view.y + view.h;
const shown = nodeEls.filter(g => opacity(g) > 0);
/* ★先要一个对照组：shown 为空时 every/filter 全都不跑，下面那条断言会假绿
   （和 `h.includes('')` 恒真是同一类坑）。而且要的不只是"非空"——第一屏的承诺是
   "一打开就是能读的词"。
   ★★下限**不再是条数**：这里曾经是 `want(shown.length >= 5, …)`。那个 **5 是读数、
     不是设计意图**，判定与依据留在这里（下次还会有人问"5 还是 4"）：
     ①`2b-版式收编方案.md` §1.2 把它**连同它的期望值**一起列进"通过 8px 闸的词变多或
       变少 ⇒ 读数会变、期望值要重新校准"那一类 —— 写文档的人当时就知道它是读数；
     ②本条注释对承诺的写法从头到尾是**定性**的（"不是空图"、"只剩线和点"），
       从没写过"至少五个"；
     ③本仓库真正**拍过**的决定不是这么写的（模板里"打开就 1:1 居中「基金」"是用户定的、
       `.lbl.on` 那条写着"2026-09-21 ylf-c0 定"），而 5 没有任何决策记录 ——
       它随 `3a23be8` 那次 squash 进来。
     而且它**零余量**（5 过、4 红）：布局微移一格就翻面 —— 那正是"读数被当成阈值"的
     指纹（本轮真踩：加 5 个词条让 FR 布局移了一点点，窄屏读到 4，它当场红）。
     ⇒ 纪律同 `--selftest` 那条教训：**读数只能用来证伪常量，不能用来定义常量**。
     所以换成两条**从"已经定下来的东西"推出来**的形状，都不含条数：
     ㈠ 居中的那个词自己得有标签 ——「打开就 1:1 居中「基金」」是**用户拍过**的决定
       （模板 `home()`），⇒ 它必然落在视口里；它又是全局枢纽词（deg 最大）⇒ 走大圈字号，
       屏幕字号 ≥ 8px 由下面那条 `labelPx(HOME) >= 9` 守着 ⇒ 它**必须**被画出来。
       少了它就是"退化成一个墨点"：本仓库真发生过（坐标没写到元素上时满屏只剩一个点）。
     ㈡ 第一屏里得有**普通档**（度数小的词）的标签 —— 那正是"在眼前"那道闸存在的理由，
       注释原话是"基金旁边那些词恰恰度数都小……它们反倒没名字，屏幕上只剩线和点"。
       ★用**字号档**表达（`labelPx` 与 `legible()` 同源；"哪些算大圈"那个谓词由 4g-2
         钉住三处一致，这里不重写它）。
     ★这两条**都不是"密度"判据**：密度各由下面"画出来的都必须 ≥8px 且在眼前"、
       渲染层的计数相等（`lit0 === modelLit`）、以及 `build_graph.py` 的布局自检
       （最近邻间距 / 类内类间距离）各管一轴。把密度塞进这一条，就又会回到"照读数写常量"。
     ★"整批消失"由这两条一起接住（shown 为空 ⇒ 两条都假 ⇒ 都红）。 */
want(shown.some(g => g.__n.id === HOME.id),
     '打开时居中的那个词「' + HOME.id + '」自己没有标签（第一屏退化成一个点）');
want(shown.some(g => labelPx(g.__n) < FS.big),
     '打开时第一屏里一个普通档的词都没有（只剩枢纽词/光点）—— 「' + HOME.id
     + '」旁边那一圈恰恰都是度数小的词，它们没名字，这一页就退化成点阵');
want(shown.filter(g => labelPx(g.__n) < 8).length === 0,
     '有标签在屏幕字号 < 8px 时就画了出来（灰糊）：最小 '
     + (shown.length ? Math.min(...shown.map(g => labelPx(g.__n))).toFixed(1) : '—') + 'px');
want(shown.filter(g => !inView(g.__n)).length === 0,
     '有 ' + shown.filter(g => !inView(g.__n)).length + ' 个标签画在了视图外面（放大后看不见，白画）');
want(labelPx(HOME) >= 9, '最粗的那个圈「' + HOME.id + '」的标签只有 ' + labelPx(HOME).toFixed(1) + 'px');
/* 视图动过之后标签必须重算：挪到远处再回来，能读的还得是那一带的词。 */
setView(D.nodes[0].x, D.nodes[0].y, 1);
const far = nodeEls.filter(g => opacity(g) > 0);
want(far.length > 0 && far.every(g => inView(g.__n)),
     '把视图挪到「' + D.nodes[0].id + '」那一带后，画着的标签没有跟着视图走（'
     + far.filter(g => !inView(g.__n)).length + ' 个在视图外）');
want(far.some(g => g.__n.id === D.nodes[0].id), '视图挪过去后「' + D.nodes[0].id + '」自己反而没标签');
home();

/* A5. 看全图：整张图缩进一屏；此时字只剩 ~1.7px，标签必须全灭 */
byIdEl('reset').onclick();
want(Math.abs(view.w / view.h - CW / CH) < 1e-6,
     '看全图后 viewBox 与画布不同比例（' + (view.w / view.h).toFixed(3) + ' vs ' + (CW / CH).toFixed(3)
     + '），图会被拉变形');
const left = nodeEls.filter(g => opacity(g) > 0);
want(left.length === 0, '看全图后还有 ' + left.length + ' 个标签在画（当前字号 '
     + labelPx(D.nodes[0]).toFixed(1) + 'px' + (labelPx(D.nodes[0]) < 8 ? '，是糊的' : '') + '）');

/* A6. 拖窗口：字号不该被改掉（缩放比例保持），viewBox 仍与画布同比例。
   窄屏那条分支最容易写错 —— 现场算 scale() 会拿新画布宽去配旧 view.w，窗口一窄字就变小。
   ★基准必须在改画布尺寸之前取：改完再算 scale() 拿到的是"旧 view 配新画布"的中间值，
     拿它当基准，正确的代码也会被判红（第一版就是这么写的，先红了自己）。 */
const font0 = labelPx(D.nodes[0]);
svg.clientWidth = 420; svg.clientHeight = 700;
window._l.resize();
want(Math.abs(labelPx(D.nodes[0]) - font0) < 1e-9,
     '拖窗口改了字号：' + font0.toFixed(2) + 'px → ' + labelPx(D.nodes[0]).toFixed(2) + 'px');
want(Math.abs(view.w / view.h - 420 / 700) < 1e-6,
     '窄屏下 viewBox 与画布不同比例（图被拉变形）：' + (view.w / view.h).toFixed(3));
svg.clientWidth = CW; svg.clientHeight = CH;      // 还原，后面的段落还是在大画布上跑

/* B. 每个节点的面板内容 */
for (const n of D.nodes) {
  let h;
  try { h = panelHTML(n); } catch (e) { want(false, n.id + ' 面板抛异常: ' + e.message); continue; }
  want(h && h.length >= 60, n.id + ' 面板内容过短');
  want(!/undefined|NaN|\[object Object\]/.test(h), n.id + ' 面板里有 undefined/NaN');
  if (n.kind === 'term') {
    for (const k of ['一句话', '例子', '误区']) {
      const v = n.f[k] || '';
      want(v, n.id + ' 数据里就没有「' + k + '」');          // 先要求真有值，否则 includes('') 恒真
      if (v) want(h.includes(F_esc(v)), n.id + ' 渲染结果里找不到「' + k + '」的正文');
    }
    const others = new Set(NB.get(n.id).map(x => x.other));
    const got = [...others].filter(o => nBtn(h, o) > 0).length;
    want(got === others.size && others.size > 0,
         n.id + ' 邻接按钮 ' + got + '/' + others.size + '（关系边没渲染出来 / 或本就没有边）');
  } else {
    want(h.includes('属于它的词条'), n.id + ' 分类节点缺成员区');
    const got = n.members.filter(m => nBtn(h, m) > 0).length;
    want(got === n.members.length, n.id + ' 成员按钮 ' + got + '/' + n.members.length);
    want(n.refs === n.members.length, n.id + ' refs(' + n.refs + ') 与 members(' + n.members.length + ') 不符');
  }
}

/* C. 点开 → 面板展开 → 关闭 → 空态回来 */
let opened = 0;
for (const n of D.nodes) {
  try {
    select(n.id);
    want(byIdEl('detail').innerHTML.length >= 60, n.id + ' 点开后 detail 为空');
    want(byIdEl('panel').classList.contains('up'), n.id + ' 面板没被标记为展开');
    want(byIdEl('empty').hidden === true, n.id + ' 空态没收起');
    opened++;
  } catch (e) { want(false, n.id + ' select() 抛异常: ' + e.message); }
}
select(null);
want(byIdEl('empty').hidden === false, '关闭后空态没回来');
want(!byIdEl('panel').classList.contains('up'), '关闭后面板没收起');

console.log('  创建元素 ' + elCount + ' 个 / 属性 ' + attrCount + ' 次 / 监听 ' + listenerCount + ' 个');
console.log('  点开节点 ' + opened + '/' + D.nodes.length + '，断言 ' + asserts + ' 条');
if (bad.length) {
  console.log('  ★ ' + bad.length + ' 处不通过：');
  for (const b of bad.slice(0, 12)) console.log('     ' + b);
  if (bad.length > 12) console.log('     …还有 ' + (bad.length - 12) + ' 处');
  process.exit(1);
}
console.log('  ✓ 全部通过');
"""


def script_body():
    return re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>',
                      open(HTML, encoding='utf-8').read(), re.S)[0]


# ── [4] 静态内容判据：读**产物文本**，不开浏览器、不跑 node ──
# 这一层量的是"源文本里到底写了什么"：引了哪些外部资源、颜色 token 到底是多少。
# ★与 [5] 的分工：[5] 看"画出来是什么样"（CSS 优先级 / 栅格行高 / viewBox 缩放），
#   这里看"文本里写的是什么"。两条腿各看一层，谁也别当谁的替代。
#
# ★★核心那条判据量的是 `--ink-*` × `--paper/--card/--card-2` 的**token 对**，它凭什么能代表
#    "用户所见"？—— 因为默认态下每个字色、每个底色都是从这几张 token 直出的。
#    前提会被三件事打破，三件都已经处理：
#    ①  hover / focus 态：底色规则写的是 `background:var(--card-2)` 这类**同三张底**的值，
#        所以 token 对**精确覆盖**它，不需要另开一套"hover 代理"。
#        ★但这不能靠注释保证 —— 由 4f【底色覆盖】判据来证：凡是 `background`/`background-color`
#        声明，值必须落在这三张底里，或进显式例外表（表里要写"上面压不压字"）。
#    ②  页头的底是 `color-mix(in srgb,var(--paper) 88%|90%,transparent)` + `backdrop-filter:blur`，
#        **不是** token 直出 —— 三页各有一处，都进了例外表并写明这是已知缺口。
#    ③  ★★**非三张底的那些底**（警示块 `--warn-bg`、按钮 `--accent`、`--ok-bg`…）：
#        只要上面压着字，那一对就**不在** 3×3 矩阵里 —— 这就是本轮真踩到的那个洞。
#        实测：`#FFFFFF on var(--accent)` 在暗色下 = **2.08**，落在发送/保存按钮、CTA、
#        消息气泡上，而 3×3 矩阵**结构上就看不见它**（矩阵的前景只认 INK_TOKENS）。
#        ⇒ 治法不是把它塞进矩阵，而是**让判据自己推出"这条底上压着字"**：
#          同一条规则里同时声明了 `background` 和 `color:` ⇒ 这条底就压着字
#          ⇒ 它的前景色**必须**在例外表里声明，并逐张 token 表算对比度、按 AA 闸。
#          ★这样"2.08"这类事不用人先发现 —— 判据会自己报。人只需要为**继承来的**字色
#            （规则里没有 `color:`，从祖先继承）补一条声明，那种情况 CSS 文本里推不出来。
#    ⇒ 这一层**看不见**"合成之后的实际颜色"（blur / opacity / stroke 的合成）。
#       graph.html 今天的合成点只有 4 处：页头那处（唯一压字的，已进例外表）、#stage 点阵、
#       两条边线 stroke 的 color-mix（34%/40%，刻意做淡的非文字图形）、#dim 的 opacity。
#       后三类上面都不压字。**明写为未覆盖**：边线 stroke 与 #dim 的 3:1（非文字阈值）本层不管。
AA = 4.5                           # WCAG 2.1 AA 正文阈值。这是标准值，不是本页自定的尺子。

# 只在自测里用的"这个键原本不在"哨兵。★不能用 `None` 表示"不在"：`None` 是**合法值**
# （白名单条目的理由本来就可以是 None），拿它当"缺席"会让还原那一步把真值抹掉。
_MISSING = object()
# "有层没跑"的**独立退出码**，语义是 `SKIP_RC`：三态 = 全过 / 有真红 / 有层没跑（未验证）。
# ★给消费者的一句话协议：`0` 绿、`SKIP_RC` ⇒ 登记为 skip（**不是**绿）、其他 ⇒ 红。
#   塞进 stdout 的 `⊘` 只能给人看，闸门读不到 —— 所以第三态必须走退出码。
# ★为什么不用 shell 那套惯例码：**惯例码全被占了** —— argparse 的 `parser.error()` 与
#   `SystemExit(2)` 同走"用法错误"那一个码，而"真红"那一个码也早被占了。用它们会让
#   "工具不在、这一层没跑"与"调用方把参数写错了"落到同一个码上 ⇒ 消费端分不清该
#   skip 还是该报错。⇒ 取一个谁都没占的值。
#   ★★这段注释里**不写任何码的字面值**（包括上面那句"为什么不用"）：本文件有一条判据
#   （`verify_agent.py` 的跨脚本协议）是**从我这份源码文本里读**出这个值的，写在这里的
#   数字会被当成它的值读走 —— 本轮真踩过一次同形的：注解里的旧值与新值矛盾。
#   （改这一个常量即可换码，消费端只认 `SKIP_RC` 这个语义，不认它的字面值。）
SKIP_RC = 3


def verdict_code(verdicts):
    """把各层的裁定（True/False/None，None=没跑）合成**退出码**：0=全过 / 1=有真红 / SKIP_RC=有层没跑。

    ★★**失败优先**，这是本函数存在的全部理由：
      原写法是 `if any(v is None for v in verdicts): return 0` 且**排在 `all()` 之前** ⇒
      只要有**一处没跑**，别处的**真红也会被一并洗成 0** —— 那比"没跑算通过"更毒：
      它把一条真缺陷报成成功。一句话纪律：**`没跑` 只能把 0 降级成 SKIP_RC，
      绝不能把 1 升成 0**。
    ★为什么抽成纯函数：这条判定是"整趟验证算不算数"的门，而它**只能**用真值表来钉 ——
      走子进程实测拿不到 `[False, None]` 那一档（那要求"同时有真红、又刚好没 firefox"，
      而那正是**基线被别处的红污染**的状态，测出来的东西分不清是谁造成的；见自测里那段真值表）。
    """
    ran = [v for v in verdicts if v is not None]
    if any(v is False for v in ran):
        return 1
    if len(ran) != len(verdicts):
        return SKIP_RC
    return 0
INK_TOKENS = ('--ink', '--ink-2', '--ink-3')         # 三档文字色
SURFACE_TOKENS = ('--paper', '--card', '--card-2')   # 三张底色
STYLE_BLOCKS = 1                # 本仓库三页都是"一套 token 一个 <style>"，见 4a
SITE_PAGES = ('graph.html', 'index.html', 'guide.html')   # ★[4] 逐页验，不只验图谱页
# ★哪些页面**必须有图上标签**（有 `.lbl` 的字号 + `legible()` 的尺寸模型）。
#   4g 只对名单里的页面要求"必须抽得出来" —— 名单外的页面本来就该没有，
#   对它们报"抽不出"是假红（本轮真踩）；而名单内抽不出必须红（否则删掉 `legible()`
#   就能让 4g 静默消失）。这就是"覆盖 vs 结构"那一对的又一用法。
LABEL_PAGES = ('graph.html',)

# "不形成底"的写法：背景透过去，字压在**祖先**的底上 ⇒ 它既不参与 4f-1 的"未知底"判定，
# 也不该被 4f-2 读成"这条底上压着字"。那两处字（`button{background:none}`、
# `.msg.err .body code{background:transparent}`）的实际底分别是 --paper 与 --warn-bg，
# 都已算过：前者在 3×3 矩阵里，后者在例外表里声明了前景色。
NO_SURFACE = ('none', 'transparent')

# 例外底色表：页名 → {写法: (理由, 上面压着的文字色)}
#   fg = None      这条上面**没有字**（纯装饰 / 不形成底）⇒ 3×3 矩阵不必管它
#   fg = '--ink-2' 上面压着这个 **token** 的字 ⇒ 逐张表取值算对比度（自动跟着主题走）
#   fg = '#FFFFFF' 上面压着**写死的字面量**的字 ⇒ 同上（这类正是矩阵看不见的）
# ★理由里**不写数值**：数值会随调色漂，写进来就成了会烂的引用（判据自己会打印实算值）。
BG_EXC = {
    'graph.html': {
        'color-mix(in srgb,var(--paper) 88%,transparent)':
            ('页头（★上面有字，这是唯一的已知缺口：88% 纸 + blur，未滚动时合成结果等于 --paper，'
             '所以 --paper 那一列在"整页不滚"时对它精确；页面一旦溢出、有内容滚到页头底下就看不见了）', None),
        'radial-gradient(circle at center,var(--dot) 1px,transparent 1px) 0 0/22px 22px':
            ('#stage 的点阵纸（上面只有 SVG，没有文字）', None),
        'none': ('button 的归零（不形成底）', None),
        'currentColor': ('.field .k::before 那根 3×11 的装饰竖条（上面没有字）', None),
    },
    'index.html': {
        'color-mix(in srgb,var(--paper) 90%,transparent)':
            ('页头（同上：未滚动时合成结果等于 --paper，以 --paper 那一列为准；滚动后是已知缺口）', None),
        'none': ('button 的归零（不形成底）', None),
        'transparent': ('.msg.err .body code 的归零（继承父底，不形成新底）', None),
        'var(--ink-3)': ('.badge .d / .kstate .kd 的 7×7 圆点（装饰，上面没有字）', None),
        'var(--f2)': ('.badge.warn .d / .kstate.off .kd 的 7×7 圆点（装饰，上面没有字）', None),
        'var(--f5)': ('.badge.off .d 的 7×7 圆点（装饰，上面没有字）', None),
        # ★下面两条是**压着字**的。它们的字色由 4f **从规则里推出**，这里写的 fg 是
        #   **交叉核对**用的冗余声明：与推出来的不一致 ⇒ 4f-3b 报"两边对不上"（先怀疑表）。
        #   ★值必须与 CSS 里的写法同形（CSS 是 `color:var(--card)`，这里就写 `var(--card)`）——
        #     写成展开后的 `#FFFFFF` 会在暗色主题下**两边对不上**（那一对的实际字色是暗色的 `--card`）。
        'var(--accent)': ('按钮 / banner / 消息气泡的底（.banner .bl、.msg.me、#send、#ksave…）',
                          'var(--card)'),
        'var(--warn-bg)': ('.predmark / .msg.err .body / .kwarn / .disclaim 的底', '--warn-ink'),
    },
    'guide.html': {
        'color-mix(in srgb,var(--paper) 90%,transparent)':
            ('页头（同上：未滚动时合成结果等于 --paper，以 --paper 那一列为准；滚动后是已知缺口）', None),
        'var(--accent)': ('.banner .bl / .cta / .steps li::before 的底', 'var(--card)'),
        'var(--warn-bg)': ('.no / .warn 的底', '--warn-ink'),
        # ★这条是**继承**来的字色：`.yes` 的规则里没有 `color:`，从 body{color:var(--ink)} 继承
        #   —— CSS 文本推不出"上面压不压字"，所以只能手验一次并在此声明（body 的 color 已核）
        'var(--ok-bg)': ('.yes 的底（字色是继承来的，规则里没有 color:）', '--ink'),
    },
}
# 外部引用的**形态**。★必须两件事一起做：先剥 `<script>`，再只认这几种引用形态。
#   少了剥 script：JS 里的字符串（本页真有 `const NS = 'http://www.w3.org/2000/svg'`）
#   会被当成引用；少了形态限制：裸 `http` 计数同理。实测本页剥完 script 后裸 http 计数=2、
#   引用形态=1 —— 两个数都打出来当对照，好看出"剥"这一步真的在干活。
EXT_REF_RE = (r'<link\b[^>]*\bhref\s*=\s*["\']?https?:',
              r'<script\b[^>]*\bsrc\s*=\s*["\']?https?:',
              r'<img\b[^>]*\bsrc\s*=\s*["\']?https?:',
              r'url\(\s*["\']?https?:')


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4) for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def style_blocks(html):
    """所有 <style> 区，按出现顺序，逐个去掉注释（注释里的色号/大括号不该参与判据）。"""
    return [re.sub(r'/\*.*?\*/', '', b, flags=re.S)
            for b in re.findall(r'<style\b[^>]*>(.*?)</style>', html, re.S)]


def style_text(html):
    """★拼**全部** <style> 块，不是只取第一个（原来是 `re.search`）。

    ★这里与 4a 那条"块数恰好 1"是**两件事**，缺一不可：
      · 这个函数管**覆盖面** —— 一块都不漏，判据不会静默只看第一块；
      · 4a 管**结构** —— 块数不是 1 就报红，因为三页都是"一套 token 一个块"，
        多出第二块意味着样式跑到了 token 纪律之外，需要人看一眼。
      只有 4a 而拼接仍只取第一块 ⇒ 报红之前[4]已经在看半张表了；
      只有拼接而没有 4a ⇒ 结构变了没人吭声。
    """
    return '\n'.join(style_blocks(html))


def token_tables(html):
    """认出"一浅两深"三张 token 表。

    ★按**语义**认，不写死选择器字面量 —— 版式正在重做，选择器会改，写死就成了"会烂的引用"
      （改完选择器判据会去找一个不存在的串，报出来的还是"缺 token"这种误导性的红）。
      判别"这是表不是引用"的依据：体内**声明**了 `--paper:`。`body`/`header`/`.lbl` 三条规则
      都用 `var(--paper)`，但它们不声明，所以不会被误认成表（实测：不加这条判别会多出 3 个假表）。
    返回 [(种类, 选择器, {token: 值})]，种类 ∈ {'亮','暗·手动','暗·跟随系统'}。
    """
    out = []
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', style_text(html)):
        sel, body = m.group(1).strip(), m.group(2)
        if not re.search(r'--paper\s*:', body):
            continue
        kind = ('暗·手动' if 'data-theme="dark"' in sel else
                '暗·跟随系统' if 'data-theme="light"' in sel else
                '亮' if sel == ':root' else '★不认识')
        out.append((kind, ' '.join(sel.split()),
                    dict(re.findall(r'(--[\w-]+)\s*:\s*([^;]+);', body))))
    return out


def _hex(spec, toks):
    """`--x` / `var(--x)` / `#RRGGBB` / `#RGB` → `#RRGGBB`；解析不出来返回 None（调用方当"声明烂了"报红）。

    ★三种形态都必须认：例外表里写裸 token 名（跟着主题走）、写 `var(--x)`、或写死字面量。
      少认一种的后果不是报错，是**把好声明报成"取不到值"**（本轮真踩：只认 `var(--x)` 时，
      表里那些裸 `--warn-ink` 全被判成"声明烂了"，而它们明明好着）。
    ★`#RGB` 三缩位也必须认（`#fff` 是 `#FFFFFF` 的合法写法）。少认它的后果同样是**假红** ——
      本轮真踩：页里 5 处 `color:#fff` 与例外表里的 `#FFFFFF` 本是**同一对**，却被报成
      "三张表里一张都解析不出色值"。判别法：同一页面里若既有 `#fff` 又有 `#FFFFFF`，
      而它们算出的对比度理应相同，那"解析不出"就是解析器的锅，不是页面的锅。
    ★解析不出来必须**报红**而不是跳过 —— 跳过就是一条永远绿的判据（对照组成立的唯一原因）。
    """
    m = re.match(r'^var\(\s*(--[\w-]+)\s*\)$', spec) or re.match(r'^(--[\w-]+)$', spec)
    if m:
        return toks.get(m.group(1))
    m = re.match(r'^#([0-9A-Fa-f]{6})$', spec)
    if m:
        return '#' + m.group(1).upper()
    m = re.match(r'^#([0-9A-Fa-f]{3})$', spec)
    if m:
        return '#' + ''.join(c * 2 for c in m.group(1)).upper()
    return None


def _canon_fg(spec):
    """前景色的**归一写法**：`#fff` → `#FFFFFF`，`var(--x)` → `--x`。

    目的是让"同一对"只有一个键。不归一的两条后果都真实发生过：
      · `#fff` 与例外表里的 `#FFFFFF` 本是同一对 ⇒ 报成"解析不出色值"的假红；
      · CSS 里的 `var(--warn-ink)` 与表里的 `--warn-ink` 本是同一对 ⇒ 打印两行一样的数。
    ★归一之后，"规则推出来的"与"人手声明的"若指向同一对，它们就**合并成一行** ——
      这行数才是跨来源的交叉核对：对不上就是页面改了而表没跟上。
    """
    h = _hex(spec, {})
    if h:
        return h
    m = re.match(r'^var\(\s*(--[\w-]+)\s*\)$', spec)
    return m.group(1) if m else spec


def _lbl_css(html):
    """抽 `.lbl*` 规则**声明**的 font-size，返回 {选择器: 数值串}。

    ★只认**声明了** font-size 的那几条（`.lbl` / `.lbl.big` / `.lbl.on`…），
      没声明的规则不进结果 —— "没声明"与"声明成空的"是两件事，前者正常、后者要报红。
    ★返回空 dict 与前缀缺失是同一件事，调用方必须**当红处理**：抽不出来还继续，
      就是一条永远绿的判据（`_hex` 那轮已经栽过一次，判别法相同）。
    """
    out = {}
    for m in re.finditer(r'(\.lbl(?:\.\w+)*)\s*\{([^}]*)\}', style_text(html)):
        fm = re.search(r'font-size\s*:\s*([0-9.]+)px', m.group(2))
        if fm:
            out[m.group(1)] = fm.group(1)
    return out


def _lbl_order(html):
    """每个 `.lbl*` 规则在样式块里的**先后位置** —— 用来算层叠平手时谁赢。

    ★为什么需要位置：`.lbl.big` 与 `.lbl.on` 的**优先级相等**（各两个类选择器），
      于是"选中态的字比大圈的字更大"这件事**只由源码先后决定**。
      把这两条规则对调一下，字号声明一个字没改、`_lbl_css` 读出来一模一样，
      **渲染出来的结果却反了** ⇒ 只比"声明的值"的判据一条都不会响。
    """
    return {m.group(1): m.start()
            for m in re.finditer(r'(\.lbl(?:\.\w+)*)\s*\{', style_text(html))}


def _lbl_eff(css, order):
    """算出三种标签**实际**渲染成多大 → {'普通':…, '大圈':…, '普通·选中':…, '大圈·选中':…}。

    规则（本页这三条都是纯类选择器、没有 `!important`）：
      优先级 = 选择器里的**类数**；打平 ⇒ **源码靠后的赢**。
    ★这一步是"读生效值"，不是"读声明值"。两者在本页**真的会分叉**（见 `_lbl_order`）。
    """
    def eff(classes):
        best = None
        for sel, val in css.items():
            if set(sel.lstrip('.').split('.')) <= set(classes):
                key = (sel.count('.'), order.get(sel, -1))
                if best is None or key > best[0]:
                    best = (key, val)
        return best[1] if best else None
    return {'普通': eff(['lbl']), '大圈': eff(['lbl', 'big']),
            '普通·选中': eff(['lbl', 'on']), '大圈·选中': eff(['lbl', 'big', 'on'])}


def _lbl_body(html):
    """抽 `legible()` 的**函数体** —— 数花括号配对，不是"到第一个 `}` 为止"的正则。

    ★为什么不能用 `\\{(.*?)\\}`：`.*?` 是**懒**的，它停在**第一个** `}` 上。今天函数体里
      恰好没有内层花括号 ⇒ "碰巧对"；1a-ii 之后函数体里出现了 `getComputedStyle(...)`，
      只要哪天有人写进一个对象字面量或一个内层块，抽取器就会**截断**，而截断的症状是
      判据只读到半句、**照样绿**。（同族另一个方向：`^NAME = (.+?)\\n\\n` 配 `re.S` 是**贪婪**的，
      一口吃掉几百行，下游 `literal_eval` 报错 ⇒ 那条判据从没比过任何东西。）
    ★本函数是**结构化**抽取，但它**不是** JS 解析器：字符串/注释里的花括号一样计入。
      这是**已知局限**。之所以不写真正的 AST：本机没有可用的 JS 解析器
      （实测 esprima / pyjsparser / tree_sitter / acorn 全无，node v22 只是运行时），
      要 AST 就得自带一个解析器 —— 那笔账不该记在这条判据头上。局限写在失败话术里，
      不装作没有。★另：`legible` 在本仓库是 `function` 声明（不是 const 箭头），这是**有意**的，
      渲染层的注入靠重新赋值它来造故障（见 FAULTS 里 9/11 两条）。
    """
    m = re.search(r'function\s+legible\s*\([^)]*\)\s*\{', html)
    if not m:
        return None
    depth, i, n = 0, m.end() - 1, len(html)
    while i < n:
        if html[i] == '{':
            depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                return html[m.end():i]
        i += 1
    return None


def _lbl_pred(html):
    """抽"哪些标签算大圈"这个**谓词**在两处的原始写法，返回 (建类处, legible 处) 或 None。

    ★为什么这个也要抽：字号"两边相等"只在**同一个节点被判进同一支**时才有意义。
      建类那处（`class: 'lbl' + (谓词 ? ' big' : '')`）决定这个字**画多大**，
      `legible()` 里那处决定这个字**按多大去算** 8px 闸 —— 两处谓词一分叉，
      就会有节点"按 12px 画出来、却按 10.5px 过闸"（或反过来），**两边各自自洽**。
    ★实测：两处今天字面相同（`n.deg >= 11 || n.kind === 'cat'`），**此前没有任何判据钉住它**。
    ★`legible` 那处**改成从函数体里找**（`_lbl_body`）：原来那个正则带 `.*?` + `re.S`，
      函数体中一旦出现 `}` 就会读到别处去，而"读到别处"的症状同样是**照样绿**。
    """
    a = re.search(r"'lbl'\s*\+\s*\(([^?]*)\?", html)
    body = _lbl_body(html)
    b = re.search(r'return\s*\(([^?]*)\?', body) if body else None
    return (a.group(1), b.group(1)) if (a and b) else None


def _lbl_tern(body):
    """抽 `legible()` 体内那处三元的**原文**与两个分支，返回 (原文, 甲, 乙) 或 None。

    ★两个分支**今天不再是数字**（1a-ii 之后是 `FS.big` / `FS.base`）⇒ 抽取器必须同时认
      "标识符"与"数字"两种写法，否则字体从 CSS 现读的那天它就抽不出来了
      —— 而"抽不出来"的表现就是**注入构造不出来**，那正是下面那个响亮分支要接住的事。
    ★返回值里的**原文**（`? FS.big : FS.base`）是注入 ③ 的锚：锚必须从产物现读，
      写死 `? 12 : 10.5` 的锚在改版式当天就烂了，而"锚烂了"与"判据坏了"长得一样。
    """
    m = re.search(r'\?\s*([A-Za-z_$][\w.$]*|[0-9.]+)\s*:\s*([A-Za-z_$][\w.$]*|[0-9.]+)',
                  body or '')
    return (m.group(0), m.group(1), m.group(2)) if m else None


def content_checks(html, name='graph.html', quiet=False):
    """[4] 静态判据（不写条数 —— 条数是会漂的数）。读的是传进来的**产物文本**（自测时传的是致残过的副本）。

    name 只干两件事：选这一页的例外表（BG_EXC[name]）、给 ★ 行定位。
    ★红行一律带页名 —— 三页连跑时"是哪一页红了"必须是读出来的，不是靠数第几段。
    """
    lines = []

    def say(s):
        if s.startswith('★'):
            # ★红行自带页名：三页连跑，红必须说得出落在哪一页。
            # ★★但页名必须插在 **★ 之后**，不能加在行首 —— `★` 在行首是**下游消费者的契约**：
            #   自测认红靠 `l.strip().startswith('★')`，而 c0 给 verify_agent.py 的退路方案
            #   （在 stdout 里认 `⊘`）也是同一种"认行首记号"的写法。
            #   本轮真踩：写成 `'%s %s' % (name, s)` 时，行首成了页名 ⇒ 自测把那 4 条静态注入
            #   全判成"✗ 没红！" —— **判据自己是红的，红在别人认不出它**。
            #   判别法：改了打印格式之后，凡是"按行首记号认红/认 ⊘"的消费者都得跟一遍。
            s = '★ [%s]%s' % (name, s[1:])
        lines.append(s)
        if not quiet:
            print('   ' + s)

    ok = True

    # ── 4a 结构：<style> 恰好 1 个（守住 style_text 的覆盖面）──
    nblk = len(style_blocks(html))
    say('<style> 块数 %d %s' % (nblk, '✓' if nblk == STYLE_BLOCKS else '★'))
    if nblk != STYLE_BLOCKS:
        say('★ <style> 块数 %d（本仓库三页都是 1 个）—— 多出第二块 = 样式跑到了 token 纪律之外；'
            'style_text 现在会拼全部块，所以不是因为漏看才报，是结构本身变了' % nblk)
        ok = False

    # ── 4b 外部引用计数：单文件页不许依赖任何外部资源 ──
    nojs = re.sub(r'<script\b.*?</script>', '', html, flags=re.S)
    refs = [x for p in EXT_REF_RE for x in re.findall(p, nojs, re.I)]
    raw = re.findall(r'https?://', html)
    say('外部引用 %d 处（剥 <script> 后按引用形态数；对照：整篇裸 http %d 处）%s'
        % (len(refs), len(raw), ' ✓' if not refs else ' ★'))
    if refs:
        say('★ 页面引用了外部资源 %d 处（单文件页必须自足；断网/内网打开会退化成另一副样子）' % len(refs))
        ok = False

    # ── 4c/4d 两套主题的文字 × 底色对比度 ──
    tabs = token_tables(html)
    kinds = [k for k, _s, _t in tabs]
    if len(tabs) != 3 or kinds.count('亮') != 1 or kinds.count('暗·手动') != 1 \
            or kinds.count('暗·跟随系统') != 1:
        say('★ 认出的 token 表不是"一浅两深"三张：%s（选择器：%s）'
            % (kinds, [s for _k, s, _t in tabs]))
        ok = False
    for kind, sel, toks in tabs:
        miss = [t for t in INK_TOKENS + SURFACE_TOKENS if t not in toks]
        if miss:                      # ★先断言存在：缺了会 KeyError 崩，那是"判据自己坏了"不是"页面坏了"
            say('★ %s 表（%s）里缺 token：%s' % (kind, sel, '、'.join(miss)))
            ok = False
            continue
        bad = []
        for i in INK_TOKENS:
            cells = []
            for s in SURFACE_TOKENS:
                c = contrast(toks[i], toks[s])
                cells.append('%s %.2f%s' % (s, c, '★' if c < AA else ''))
                if c < AA:
                    bad.append('%s on %s %.2f' % (i, s, c))
            say('%s  %-8s %s' % (kind, i, '  '.join(cells)))
        if bad:
            say('★ %s：%d 对文字/底色低于 AA %.1f —— %s'
                % (kind, len(bad), AA, '、'.join(bad)))
            ok = False

    # ── 4e 两张暗色表必须同值 ──
    # 它俩是两条不同的应用路径（跟随系统 vs 手动切）；不同值 = 同一页两种暗色，
    # 而上面的对比度判据是**分别**验的，所以那种漂移不会自己暴露，必须单独守。
    dark = [t for k, _s, t in tabs if k.startswith('暗')]
    if len(dark) == 2 and dark[0] != dark[1]:
        diff = sorted({k for k in set(dark[0]) | set(dark[1]) if dark[0].get(k) != dark[1].get(k)})
        say('★ 两张暗色表不同值：%s'
            % '、'.join('%s %s vs %s' % (k, dark[0].get(k), dark[1].get(k)) for k in diff))
        ok = False
    elif len(dark) == 2:
        say('两张暗色表同值（跟随系统 vs 手动切） ✓')

    # ── 4f 底色覆盖 + "矩阵之外的那些文字对" ──
    # 4f 的目的：让 4c/4d 那张 3×3 矩阵**有资格**代表"用户所见"。
    # 矩阵只认 INK_TOKENS 做前景、三张底做背景；矩阵之外的组合有两类：
    #   ① 底不是三张底（警示块 / 按钮 / 纯装饰）② 前景不是 ink（`--accent` 当字色）
    # 两类都由**规则自己**推：同一条规则里同时声明了 background 和 color ⇒
    # 那条底上就压着那个颜色的字 ⇒ 解析成十六进制、逐张表算、按 AA 闸。
    # ★不靠人先发现 —— 本轮的 `#FFFFFF on var(--accent)` 2.08 就是这一推推出来的
    #   （写这段时还不知道有它；3×3 矩阵**结构上**看不见它）。
    if name not in BG_EXC:
        say('★ BG_EXC 里没有 %s 的例外表 —— 判据自己缺一件，这一页的 4f 无从谈起' % name)
        return False, lines
    exc = BG_EXC[name]
    surfset = {'var(%s)' % t for t in SURFACE_TOKENS}
    UNRESOLVED = ('inherit', 'currentColor', 'unset', 'initial')
    bgs, derived, inherit = {}, {}, []
    for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', style_text(html)):
        m = re.search(r'background(?:-color)?\s*:\s*([^;}]+)', body)
        if not m:
            continue
        v = ' '.join(m.group(1).split())
        bgs[v] = bgs.get(v, 0) + 1
        # `(?<!-)` 是为了不把 `background-color:` / `border-color:` 当成前景色
        mc = re.search(r'(?<!-)\bcolor\s*:\s*([^;}]+)', body)
        if not mc:
            continue
        f = ' '.join(mc.group(1).split())
        # 前景色先归一（`#fff`→`#FFFFFF`、`var(--x)`→`--x`），否则同一对会被算成两条、还报假红
        f = _canon_fg(f)
        if f in UNRESOLVED:
            # 字色是**继承**来的：CSS 文本推不出它实际是什么 ⇒ 只有这一类需要人声明一次
            if v not in surfset and v not in NO_SURFACE:
                inherit.append((v, f or 'inherit', ' '.join(sel.split())))
        else:
            derived[(v, f)] = derived.get((v, f), 0) + 1
    allowed = surfset | set(exc)
    unknown = sorted(v for v in bgs if v not in allowed)
    if unknown:
        say('★ 有底色既不在三张底里、也不在例外表里：%s'
            % '、'.join('%s（%d 处）' % (v, bgs[v]) for v in unknown))
        ok = False
    else:
        say('底色覆盖：%d 种写法全落在三张底或例外表里 ✓（这是文字对比判据能代表用户所见的前提）'
            % len(bgs))
    # 4f-2 继承来的字色：推不出来 ⇒ 必须在例外表里声明；不声明就把"看不见"变成"报红"
    undeclared = sorted({v for v, _f, _s in inherit if v not in exc or exc[v][1] is None})
    if undeclared:
        for v in undeclared:
            who = [s for vv, _f, s in inherit if vv == v][:2]
            say('★ 底 %s 上压着**继承来的**字色（%s）—— CSS 文本推不出它是什么，'
                '例外表里必须声明这一条：%s' % (v, '、'.join(who), '不在表里' if v not in exc else '声明了"没有字"'))
        ok = False
    else:
        say('继承字色的底 %d 处全已声明 ✓（推不出来的那一类只有人补得了）' % len(inherit))
    # ── 4f-0 ★结构性前置：三张表必须声明**同一套颜色 token**，缺哪张点名报红 ──
    #   为什么必须是**前置**：`_hex(f, toks)` 是按**那一张表自己声明的** token 解析的 ⇒
    #   一个 token 只要少声明在一张表里，那张表就 ⊘，而下面的 AA 闸**照样是绿的** ——
    #   "那一套主题一个像素都没被验"这件事，只有这一条扫得出来。
    #   它同时也是下面 ⊘⇒红 的**资格**：集合齐全之后，任何 ⊘ 才必定是真异常。
    #   ★它顺带补上 4e 的盲区：4e 只比**两张暗表**，light↔dark 之间少了哪个 token 它看不见。
    # ★★范围必须收窄到**颜色** token（判据："这张表里它的值能不能解析成色值"）。
    #   不收窄就产假红，而且第一轮就真产了：`--serif` 是**字体栈**，暗色块只重声明颜色、
    #   不重声明字体 ⇒ 它天然只在部分表里出现。实测那次假红是**对照组**抓出来的
    #   （"对照组（未改动的产物）→ ✗ 红"）—— 对照组不只证明"判据好的时候是绿的"，
    #   它还是**唯一能抓"判据自己造红"**的装置；注入永远抓不到，因为注入只会加红。
    #   ⇒ 教训：写"一对集合取差"的判据时，**取宽的那一侧要能自己证否**（这里是 `_hex`），
    #     不能靠"我觉得这个 token 该在三张表里都有"。
    names = [k for k, _s, _t in tabs]
    sets = [{n for n, val in t.items() if _hex(val, t)} for _k, _s, t in tabs]
    every = set().union(*sets)
    hole = [(k, sorted(every - s)) for k, s in zip(names, sets) if every - s]
    if hole:
        for k, miss in hole:
            say('★ %s 那张表少声明了 %d 个**颜色** token：%s —— 在那一套主题下用到它们的元素'
                '会拿到无效的 var()（CSS 会退化成 transparent/inherit，不是"没颜色而已"）'
                % (k, len(miss), '、'.join(miss)))
        ok = False
    else:
        say('三张表的**颜色** token 集合一致（%d 个）✓ —— 这是下面 ⊘ 也能当红的资格'
            % len(every))

    # ── 4f-3 逐张表算这些对，同样按 AA 闸 ──
    #   ★两个来源并成一个集合：**规则推出来的**（主）∪ **例外表声明的**（只有"继承字色"那一类非它不可）。
    #     ★一个底若有规则推出来的对，就**不再采信例外表给它的 fg**（`v not in derived_vs`）——
    #     表里那份是冗余声明，两边一旦不一致，按下表算会得到一个**标签指错地方**的红（见 4f-3b）。
    #   归一之后同一对会同键合并，所以并集比各项之和小是**正常**的，下面把分解数一并打出来。
    derived_vs = {v for v, _f in derived}
    tabled = [v for v in exc if exc[v][1] and v not in NO_SURFACE]
    pairs = sorted(set(derived) | {(v, _canon_fg(exc[v][1])) for v in tabled
                                   if v not in derived_vs})
    say('矩阵之外要单独算的文字对 %d 条（规则推出 %d 条、例外表声明 %d 条，归一后合并成 %d 条）'
        % (len(pairs), len(derived), len(tabled), len(pairs)))
    # 4f-3b ★两个来源**必须一致**：不一致时**先怀疑表，不是页面**。
    #   这条的由来是本轮真踩：CSS 从 `color:#fff` 改成 `color:var(--card)` 之后，例外表里那句
    #   `'var(--accent)': (..., '#FFFFFF')` 成了旧声明 ⇒ 判据照旧量出 `#FFFFFF on #5CC79A 2.08`，
    #   报的话却是"**矩阵之外的文字对低于 AA**" —— **数是真的，标签指向了页面**。
    #   那会诱人去改一个没坏的页面（假红里最难认的一种：**真数 + 错标签**）。
    #   ⇒ 两边对不上就单报这一条，且**不再按表算那一对**（同一个原因不许响两次、把标签混掉）。
    for v, f in sorted(derived):
        if v in exc and exc[v][1] and _canon_fg(exc[v][1]) != f:
            say('★ %s：例外表声明的前景色是 %s，规则里推出来的是 %s —— **两边对不上**。'
                '先核这一对（多半是 CSS 改过而表没跟上），**别去改页面**；'
                '这一对已按规则算，表里那份不参与判定。'
                % (v, _canon_fg(exc[v][1]), f))
            ok = False
    for v, f in pairs:
        cells, bad = [], []
        for kind, _sel, toks in tabs:
            fh, bh = _hex(f, toks), _hex(v, toks)
            if not fh or not bh:
                cells.append('%s ⊘ %s 解析不出色值' % (kind, f if not fh else v))
                continue
            c = contrast(fh, bh)
            cells.append('%s %.2f%s' % (kind, c, '★' if c < AA else ''))
            if c < AA:
                bad.append('%s %s on %s %.2f' % (kind, fh, bh, c))
        say('%-30s ← %-12s %s' % (v, f, ' ｜ '.join(cells)))
        if bad:
            say('★ 矩阵之外的文字对低于 AA %.1f —— %s' % (AA, '；'.join(bad)))
            ok = False
        elif any('⊘' in x for x in cells):
            # ★这里问的是 `any` 而不是 `all`：**`all` 防的是"全瞎"，而真实的坏法几乎总是"瞎一只眼"**。
            #   "三张里两张量到、一张没量到"**有读数、报告很漂亮**，所以它比"全没量到"更容易蒙混过关。
            #   之所以现在敢用 any：上面 4f-0 已经把"三张表声明同一套 token"钉住了 ⇒
            #   漏掉的那张表不再是"本就不该有"，而是真异常。
            unmeasured = [k for k, x in zip(names, cells) if '⊘' in x]
            say('★ %s ← %s 在 %d/%d 张表里解析不出色值（%s）—— 不是"没问题"，'
                '是这条判据在那几套主题上没量到东西' % (v, f, len(unmeasured), len(cells), '、'.join(unmeasured)))
            ok = False

    # ── 4g ★标签字号：1a-ii 之后**真源只剩 CSS 一处**，判据分四段、各守一头 ──
    #   · (α) 静态·真源唯一：`legible()` 体内不许再有"两个字面量二选一"，且页面必须真的
    #     **读** CSS（`getComputedStyle(...).fontSize`）。★这条坏法是**静默**的：把
    #     `FS.big`/`FS.base` 换回 `? 12 : 10.5`，页面照常工作、CSS 一个字没改，
    #     于是"同一个数两份手写拷贝"这个**原始缺陷原地复活**，而两边错开时页面会
    #     "明明够大却被藏 / 明明糊了却画出来"，**两边各自自洽** ⇒ 只看单边的判据一条都不响。
    #   · (β) 静态·**桩不许对页面说谎**：桩没有 CSS 引擎（见 ㉕），只能拿硬写的垫片
    #     `STUB_FS` 替页面算字号 ⇒ 垫片必须与**产物 CSS 声明的**值逐格相等，
    #     否则桩那一层测的是一个**不存在的页面**。
    #     ★★它**判不了"页面画对了"** —— 它判的只是"重算用的那份数与 CSS 一致"。
    #   · (γ) 渲染·可读下限：**画出来的**标签，量到的最小渲染字号 ≥ 8px —— 在 [5] 真浏览器那层。
    #   · (δ) 渲染·现实锚：真浏览器量到的 min/max 渲染字号 == `_lbl_eff` 算出的**生效值** ——
    #     也在 [5]，它是把「现实 ⟷ 产物 CSS ⟷ 垫片」这条链两头接起来的那一条。
    #   ★为什么 (β) 不能单独存在：垫片**锚不在现实上**（硬写的，从没读过真样式表）⇒
    #     两者**一起漂**时它恒绿。所以 (δ) 把它钉在现实上，自测里另有一条**扰动垫片**的注入
    #     证明"垫片真的在被比"。四段缺任何一段，这条链就退化成"两边一起漂、判据恒绿"。
    #   ★对不上时先怀疑谁：CSS 是真浏览器渲染的依据，(α)(β) 两段都在**模型/脚手架**那一侧。
    #   ★实测的形状（2026-09-21 复核）：1a-ii 之前这个数在**四处**手写出现 ——
    #     CSS 三条规则、页面 JS 一处、桩探针一处（`labelPx`）、真浏览器探针一处。
    #   ★改指来历（c0 2026-09-21 裁定"消失 ⇒ 删；**换宿主 ⇒ 改指**"）：1a-ii 之前 (β) 比的是
    #     「页面 JS 的三元」；之后页面从 CSS 现读，那份"重算用的数"**没有消失，它换了宿主**
    #     —— 搬进了桩的垫片，判据因此**改指**到垫片，守卫的理由与范围一字未改。
    lbl_css, lbl_body = _lbl_css(html), _lbl_body(html)
    # ★4g 只管**有标签的页面**，所以先声明"哪些页必须有"。
    #   为什么需要这个声明：index/guide 里**根本没有** `.lbl`、也没有 `legible()` ——
    #   不声不响地对它们报"抽不出 ⇒ 红"，就是把"对象合法地不存在"判成故障（假红，
    #   本轮真踩：三页连跑时 index/guide 两条假红把 ⊘ 退出码协议从强档打成了弱档）。
    #   ★但反过来，也**不能**写成"抽不出就跳过" —— 那等于"把 `legible()` 从图谱页删掉"
    #     就能让这条判据静默消失（一条会永远绿的判据）。所以是**声明式**的：
    #     在名单里的页面抽不出 ⇒ 红；不在名单里的页面，本来就不该有。
    if name in LABEL_PAGES:
        if not lbl_css or lbl_body is None:
            say('★ 4g：抽不出标签字号（CSS 里认到 %s，`legible()` 的函数体 %s）—— '
                '**这一页在 LABEL_PAGES 名单里，就必须抽得出来**；抽不出还继续，'
                '这就是一条会永远绿的判据' % (lbl_css or '空', '认到了' if lbl_body else '认不到'))
            ok = False
        else:
            # (α2) 页面里到底有没有那处**读生效值**的调用（详见上面 (α) 那段）。
            # ★认的是 `<调用>(...).fontSize` 这个形状，不是 `getComputedStyle` 这个词 ——
            #   页面里另有一处**注释**提到它（讲"声明 ≠ 生效"那段），按词认会假绿。
            _reads = re.search(r'getComputedStyle\s*\([^)]*\)\s*\.\s*fontSize', html)
            say('标签字号：CSS .lbl %s / .lbl.big %s ｜ 桩垫片 .lbl %s / .lbl.big %s ｜ '
                '页面读生效字号：%s'
                % (lbl_css.get('.lbl', '（缺）'), lbl_css.get('.lbl.big', '（缺）'),
                   STUB_FS['.lbl'], STUB_FS['.lbl.big'],
                   '是' if _reads else '**否**'))
            #
            # ── (α1) ★★真源唯一：`legible()` 体内不许再出现"两个字面量二选一" ──
            #   ★为什么单独立一条：这条坏法坏了**不响**。有人把 `FS.big`/`FS.base` 换回
            #     `? 12 : 10.5`，页面照常工作、CSS 一个字没改 ⇒ "两处手写拷贝"原地复活，
            #     而两边错开时页面会"明明够大却被藏 / 明明糊了却画出来"，**各自自洽**。
            #   ★它中立吗：**中立**。它不认识任何具体数值，只认"这里有一对字面量"，
            #     改版式、换 token 都不会让它失效。
            _lit = re.search(r'\?\s*[0-9.]+\s*:\s*[0-9.]+', lbl_body)
            if _lit:
                say('★ 4g：`legible()` 体内又出现了**两个字面量二选一**（%s）—— '
                    '字号回到了"两处手写拷贝"：CSS 一改，这里的模型不跟着动，'
                    '页面就会"明明够大却被藏 / 明明糊了却画出来"，而**两边各自自洽**。'
                    '改成从 CSS 现读（`getComputedStyle(el).fontSize`）—— '
                    '1a-ii 要根除的就是这个坏法' % _lit.group(0).strip())
                ok = False
            #
            # ── (α2) 页面必须真的**读** CSS，而不是只在别处又硬写了一份 ──
            #   ★(α1) 只守函数体那一个位置：`const FS = {base: 10.5, big: 12}` 摆在函数体
            #     **外面**照样能过 (α1)，而它和"两处手写拷贝"是一模一样的坏法。
            #     ⇒ 两条合起来才是"真源唯一"。缺 (α2)，(α1) 可以被绕过。
            if not _reads:
                say('★ 4g：页面里**找不到**读生效字号的那处调用 '
                    '（`getComputedStyle(...).fontSize`）—— 1a-ii 之后 `legible()` 的尺寸模型'
                    '必须**从 CSS 现读**，页面里没有这处调用 ⇒ 它要么在别处又硬写了一份字号，'
                    '要么整条闸门被删了。★这一页在 LABEL_PAGES 名单里，两者都是红')
                ok = False
            #
            # ── (β) ★★本条守的是「**桩不许对页面说谎**」，**不是**「页面画对了」──
            #   ★为什么必须写在名字里：这层判据**没有能力**判断页面画得对不对（桩没有
            #     CSS 引擎，见 ㉕）。它唯一能判的是 —— 页面**重算用的那份字号**（桩垫片）
            #     与**产物 CSS 声明的**字号是否逐格相等。
            #   ★★垫片自己**锚不在现实上**（硬写的，从没读过真样式表）⇒ 只比"垫片 == CSS"
            #     在两者**一起错**时恒绿。另两头把它钉住：
            #       · (δ) 渲染层那条断言：真 firefox 量到的渲染字号 == `_lbl_eff` 的生效值
            #         （现实 ⟷ 产物 CSS）；两边来源不同，所以"一起错"是能被抓到的；
            #       · 自测里有一条**扰动垫片**的注入（垫片错、CSS 对 ⇒ 本条必须红）。
            #     三层链：**现实 ⟷ 产物 CSS ⟷ 垫片**；缺任何一头都退化成"两边一起漂"。
            for nm in ('.lbl', '.lbl.big'):
                c, s = lbl_css.get(nm), STUB_FS.get(nm)
                if c is None:
                    say('★ 4g：产物 CSS 里没有 %s 的 font-size —— 页面从此只能按垫片的 %s 去重算，'
                        '真浏览器却按别的值渲染 ⇒ **两边错开**。先查 CSS 是不是少了一条规则'
                        % (nm, s))
                    ok = False
                elif s is None:
                    say('★ 4g：桩垫片里没有 %s —— 页面一读它就是 NaN，整层桩会静默死掉。'
                        '垫片必须与产物 CSS 逐格对齐' % nm)
                    ok = False
                elif float(c) != float(s):
                    say('★ 4g：%s 的字号**垫片与产物 CSS 对不上**：CSS 声明 %s，垫片说 %s。'
                        '★桩不许对页面说谎 —— 垫片是脚手架，它对页面承诺的字号必须与产物 CSS '
                        '相等，否则桩那一层测的是一个**不存在的页面**。'
                        '★先查 CSS（真浏览器渲染的依据是它，垫片只是替身）' % (nm, c, s))
                    ok = False
            # ── 4g-2 ★谓词必须两处同一：字号"配对"只在**同一个节点进同一支**时才有意义 ──
            #   `:330` 决定这个字画多大、`legible()` 决定这个字按多大去过 8px 闸。
            #   两处谓词一分叉，就有节点"按 12px 画、却按 10.5px 过闸" —— 两边各自自洽。
            pred = _lbl_pred(html)
            if pred is None:
                say('★ 4g：抽不出"哪些标签算大圈"这个谓词（建类处或 `legible()` 里认不到）'
                    '—— 这一页在 LABEL_PAGES 名单里，就必须抽得出来')
                ok = False
            elif re.sub(r'\s+', '', pred[0]) != re.sub(r'\s+', '', pred[1]):
                say('★ 4g：大圈的**谓词**两处不一样：建类处「%s」vs `legible()`「%s」—— '
                    '后果是节点"按一支的字号画出来、按另一支的字号过闸"，'
                    '而两处各自都对。改阈值时**必须两处一起改**'
                    % (pred[0].strip(), pred[1].strip()))
                ok = False

            # ── 4g-3 ★层叠：读**生效值**，不是读声明值 ──
            #   `.lbl.big` 与 `.lbl.on` 优先级相等 ⇒ "选中态更大"只由**源码先后**决定。
            #   对调两条规则：声明一个字没改、`_lbl_css` 读出来一模一样，渲染结果却反了。
            #   ★`.lbl.on`(12.5) 在页面 JS 的显隐模型里**没有**对应项（被选中的必然在画），
            #     所以它唯一的守卫就是这里的**序**：普通 < 大圈 ≤ 各自选中态。
            #   ⇒ 这就是 c0 说的"12.5 不是只存在于 CSS 那一份的孤儿" —— 它被钉在关系上。
            eff = _lbl_eff(lbl_css, _lbl_order(html))
            try:
                np_, bg_ = float(eff['普通']), float(eff['大圈'])
                npon, bgon = float(eff['普通·选中']), float(eff['大圈·选中'])
            except (TypeError, ValueError):
                say('★ 4g：算不出标签的**生效**字号（%s）—— 三条 `.lbl*` 规则必须都在、'
                    '都声明 px 字号。生效值算不出来时，下面三条序判据全是空话' % (eff,))
                ok = False
            else:
                say('标签字号生效值：普通 %s ｜ 大圈 %s ｜ 普通·选中 %s ｜ 大圈·选中 %s'
                    % (eff['普通'], eff['大圈'], eff['普通·选中'], eff['大圈·选中']))
                # ★★下面**是三条判据，不是一条**（c0 2026-09-21 明确要求把"中立的"与"钉决定的"分开）。
                #   分界线是**它认不认识具体值**：
                #     (a) **设计中立**：不认识任何值 ⇒ 任何设计决定下都成立，永久有效。
                #     (b) **钉住一个决定**：它把"选中必须更大""大圈必须更大"这两层写进去了
                #         ⇒ 不中立 ⇒ **必须把决定与出处写进判据本体**（否则下一个人看见它红，
                #         第一反应是"删掉这条假判据"，而删掉的其实是刚刚拍的那个设计决定）。
                #   ★为什么不能合成一条：早期的合体写法是 `选中 >= 原样` ——
                #     既不够中立（容得下对调：对调后 12 >= 12 成立），又不够严格（容得下削平）
                #     ⇒ 两头都漏，而且漏得**静默**。
                #
                # ── (b1) 大圈 > 普通（钉决定：标签有大小两层）──
                if not bg_ > np_:
                    say('★ 4g：大圈的字号（%s）没有**严格大于**普通的（%s）—— 层次没了。'
                        '★这条**不中立**：它钉的是"标签分大小两层"这个既有设计。'
                        '★收编字号时最容易犯的一条就是把两层映到同一档 token'
                        '（本方案 §3 的初稿就是这么映的，c0 已改）' % (eff['大圈'], eff['普通']))
                    ok = False
                #
                # ── (a) ★设计中立的不变量：**选中之后的字号，不该跟"是不是大圈"有关系** ──
                #   这条是本轮**被注入逼出来**的：我起初只有上面那种 `选中 >= 原样`，
                #   拿"把 `.lbl.big` 与 `.lbl.on` 对调"去打它 —— **没红**（对调后 12 >= 12 成立）。
                #   ⇒ 那条判据的形状**容得下这次反转**。只有这条中立的不变量抓得住：
                #     对调**前** 12.5/12.5 相等；对调**后** 12.5 vs 12 ⇒ 红。
                #   ★它对**削平**（两个都变 12）不响 —— 那是 (b2) 的活。两条各管一头。
                if npon != bgon:
                    say('★ 4g：同一个标签**选中之后**的字号，跟它是不是大圈有关（普通 %s vs 大圈 %s）'
                        '—— `.lbl.on` 被 `.lbl.big` 盖住了。这两条规则**优先级相等**'
                        '（各两个类选择器），谁赢只看**源码先后** ⇒ 多半是被对调了，'
                        '先去查顺序，别去查字号' % (npon, bgon))
                    ok = False
                #
                # ── (b2) 选中 > 未选中（钉决定：选中要"变大"而不只是换颜色）──
                #   ★★本条**依赖一个决定**：`ylf-c0` 于 2026-09-21 拍定 ——
                #     `.lbl.on`(12.5) 收编到 **`--fs-base`(13)** 而**不是** `--fs-sm`(12)，
                #     理由是"**收编不许静默改掉一层关系**"；因此这里取**严格** `>`
                #     （原为 `>=`），让"把 on 与 big 映到同一档"当场红、不可能悄悄通过。
                #   ★★**若将来有人（含用户）合法地削平这层关系，先改那个决定、别删这条判据。**
                #   ★值一律不写死：10.5/12/12.5 与收编后的 11/12/13 都满足严格 `>`。
                for who, a_, b_ in (('大圈', bg_, bgon), ('普通', np_, npon)):
                    if not b_ > a_:
                        say('★ 4g：%s标签被选中之后字号**没有变大**（%s → %s）。'
                            '★这条**不中立**：它钉的是「选中要比未选中更大」这个**已拍的决定**'
                            '（2026-09-21 ylf-c0 定：`.lbl.on` 收编到 `--fs-base`(13) 而非 '
                            '`--fs-sm`(12)，理由是"收编不许静默改掉一层关系"）'
                            '⇒ **要合法地削平这层关系，先改那个决定、别删这条判据**；'
                            '若只是收编时把两个值映到了同一档 token，那就是改坏了。'
                            % (who, a_, b_))
                        ok = False
    else:
        # 这一页登记为"没有标签"。★这里**不静默**：说出"它没被 4g 量过"，
        #   免得日后有人把 `labelPx` 那类规则抄到这一页却以为有人在守。
        say('（这一页不在 LABEL_PAGES 里（没有图上标签），4g 不量它 —— '
            '它若哪天长出 `.lbl` 字号，要么进名单、要么说清为什么不进）')

    # ── 4h ★白名单必须**有条目级的理由**，否则它就是后门 ──
    #   本仓库栽过：第一版检查脚本手写 `EXTERNAL` 白名单，把 74% 的"属于"目标划走
    #   ⇒ **自己给自己开后门让检查通过**。所以规矩不是"写进名单就算数"，
    #   而是**每条都要带一条能被读到的理由**，且理由不能是占位符。
    #   ★只查**这一页**的表（`name`）：三页连跑时若三张表全查，别的页的问题会挂到
    #     这一页的头上 —— 红的落点必须是这一页（"是哪一页红了"要读得出来）。
    #   ★遍历条目、不写死条目名：2b 会加"裸像素白名单"这类新表，写死的那天就漏一张。
    _wt = BG_EXC.get(name) or {}
    _bad_reason = []
    for _wk, _we in sorted(_wt.items()):
        _wr = _we[0] if isinstance(_we, (tuple, list)) and _we else None
        if not (isinstance(_wr, str) and len(_wr.strip()) >= 4
                and _wr.strip() not in {'-', '--', 'x', 'X', '待补', 'TODO', '理由'}):
            _bad_reason.append((_wk, _wr))
    if _bad_reason:
        say('★ 白名单条目没有理由（或理由是占位符）：%s —— '
            '白名单条目没有理由就等于后门（本仓库真栽过：手写白名单把 74%% 的检查目标划走、'
            '检查于是通过）。要么写出理由，要么把这条从名单里删掉'
            % '、'.join('%s %r' % (k, v) for k, v in _bad_reason))
        ok = False
    else:
        say('白名单理由：%d 条逐条非空 ✓（写进名单不等于通过，得说得出为什么）' % len(_wt))
    return ok, lines


# ── [5] 真浏览器渲染判据 ──
# 页面自己在真浏览器里跑一遍，把结论画成一条色块；python 读那个像素就知道红绿。
# 桩看不见的三类东西全在这里：CSS 优先级（display 压过 hidden）、栅格布局（行高被内容撑开）、
# 以及"字被 viewBox 缩小"——它们都不是 JS 状态错，是画出来才错。
RENDER_PROBE = r"""
<script>
const fails = [];
const ck = (cond, msg) => { if (!cond) fails.push(msg); };
const H = sel => { const r = document.querySelector(sel); return r ? r.getBoundingClientRect().height : -1; };
try {
  const narrow = innerWidth <= 860;                      // 断点和 CSS 里那条 media query 一致
  const p = document.getElementById('panel'), e = document.getElementById('empty'),
        d = document.getElementById('detail');
  ck(Math.abs(view.w - svg.clientWidth) < 1e-6,
     '打开时不是 1:1（view.w=' + view.w.toFixed(0) + '，画布 ' + svg.clientWidth + '）字会被压小');
  /* ★第一屏的下限**不是条数**，也不在这里 —— 它是下面 `isBig` 之后那两条**形状**判据。
     这里曾经写死过 `ck(lit0 >= 5, …)`：判定为"读数、非设计意图"，判定与三条依据
     写在桩层同一条判据的注释里（两侧一套说法）。`lit0` 本身还留着 —— 计数相等
     （`lit0 === modelLit`）与末尾那条读数通道都在用它。 */
  const litEls = nodeEls.filter(g => Number(getComputedStyle(g.__t).opacity) > 0);
  const lit0 = litEls.length;
  // ★4g-b(δ) **现实锚**：把"真浏览器量到的渲染字号"逐档钉在"静态模型算出的生效值"上。
  //   ★时态（**理由留、主张过期**）：1a-ii **之前**这张表上写的是"全仓库此前没有任何一处
  //     读过 `fontSize`"—— 当时为真：`grep -nE 'getComputedStyle\([^)]*\)\.font'` 在
  //     `verify_page.py` 与产物页面里**都**零命中，四处字号全是手写拷贝。1a-ii 之后页面
  //     自己也会读一次（那正是被修好的那一处）⇒ **那句话过期了，理由没过期**：
  //     CSS 一改，不读真值的判据仍按旧数算、**页面真糊了它却绿**。
  //   ★桩层做不到这件事（没有 CSS 引擎，只能按垫片重算）⇒ 真值只有这一层读得到。
  //   ★★两边的**来源不同**，这正是这条断言的价值：期望值来自 python 的 `_lbl_eff`
  //     （读产物 CSS 的**声明** + 源码先后算层叠），实测值来自浏览器的**真样式表**。
  //     垫片那条（"垫片 == CSS"）测不出"两边一起错"，这条能 ——
  //     浏览器最小字号、`--fs-cap` 没解析成功、多出一条更优先的规则，全在这里现形。
  const fontOf = el => parseFloat(getComputedStyle(el).fontSize) || 0;
  const realSizes = nodeEls.map(g => fontOf(g.__t)).filter(v => v > 0);
  ck(realSizes.length > 0,
     '读不到任何标签的渲染字号（getComputedStyle 全返回 0）—— 判据自己失败了，当红处理');
  const realMin = realSizes.length ? Math.min.apply(null, realSizes) : 0;
  const realMax = realSizes.length ? Math.max.apply(null, realSizes) : 0;
  // ★★**逐档**比，不是比极值（c0 2026-09-21 点出）：`realMin/realMax` 是**区间端点**，
  //   两个端点相等而中间全错是可能的。要量的是**映射**对不对，所以每一档各自对上期望值。
  //   分档读的是元素上的 **class 属性**（产物），不是页面那个谓词的源码 ——
  //   谓词源码另有 4g-2 在守（两处必须同一）。
  const clsOf = t => String(t.getAttribute('class') || '');
  const isBig = t => /(^|\s)big(\s|$)/.test(clsOf(t));
  const isOn = t => /(^|\s)on(\s|$)/.test(clsOf(t));
  const tiers = {'普通': [], '大圈': [], '普通·选中': [], '大圈·选中': []};
  for (const g of nodeEls) {
    tiers[(isBig(g.__t) ? '大圈' : '普通') + (isOn(g.__t) ? '·选中' : '')].push(fontOf(g.__t));
  }
  /* ── 第一屏的**下限**：两条**形状**判据，不是条数 ──
     ★★这里曾经是 `ck(lit0 >= 5, '打开时只有 … 个标签')`。那个 5 是**读数**不是设计意图：
       它随 `3a23be8` 进来、没有任何决策记录，零余量（5 过 4 红），`2b-版式收编方案.md`
       §1.2 明确把它和它的期望值列进"读数会变、要重新校准"那一类。
       本轮加 5 个词条、FR 布局微移 ⇒ 窄屏读到 4 ⇒ 它当场翻面，而"页面变坏了"与
       "布局移了一格"在**读数**上分不开 —— 这正是"读数被当成阈值"的病。
       ⇒ 纪律同 `--selftest` 那条教训：**读数只能证伪常量，不能定义常量**。
       完整的判定与三条依据写在桩层同一条判据的注释里（两侧一套说法，别只改一边）。
     ★两条都从**已经定下来的东西**推出来，都不含条数（密度不在这里管）：
       ㈠ 居中的那个词自己得画出来 ——「打开就 1:1 居中「基金」」是**用户拍过**的决定
         ⇒ 它在视口中心（onScreen 真）、是枢纽词走大圈字号（屏幕字号 ≥ 8px）⇒ 必被画出来。
       ㈡ 第一屏里得有**普通档**（度数小的词）的标签 —— 那道"在眼前"的闸就是为它们补的
         （旧规则只放枢纽词，"基金"旁边那一圈度数小的词反倒没名字，屏幕上只剩线和点）。
       ★档位读元素上的 class（产物），不重写"哪些算大圈"那个谓词（谓词由 4g-2 钉三处一致）。
     ★"整批消失"由这两条一起接住：两边都归零时两条**都假** ⇒ 都红。
       下面 `lit0 === modelLit` 只比"声明 vs 实际"的**数**，在这条路上它是**绿的**
       （两边一起归零）⇒ 不能只留它（㊶：空样本那一支必须写明谁接住）。 */
  ck(litEls.filter(g => g.__n && g.__n.id === HOME.id).length === 1,
     '打开时居中的「' + HOME.id + '」自己没有标签（第一屏退化成一个点）');
  ck(litEls.filter(g => !isBig(g.__t)).length >= 1,
     '打开时第一屏里一个普通档的词都没有（只剩枢纽词/光点）—— 「' + HOME.id
     + '」旁边那一圈恰恰都是度数小的词，它们没名字，这一页就退化成点阵');
  // ★期望值由 python 从**同一份产物文本**算出后注入（`__EFF_*__`）；算不出来时填 `null`
  //   ⇒ 下面**必红**（不静默跳过）：静态模型算不出生效值，就等于没有任何东西能证明
  //   页面画的是它该画的大小。
  const EXP = {'普通': __EFF_MIN__, '大圈': __EFF_MAX__,
               '普通·选中': __EFF_ON_MIN__, '大圈·选中': __EFF_ON_MAX__};
  for (const k of Object.keys(tiers)) {
    const got = Array.from(new Set(tiers[k].map(v => v.toFixed(2))));
    const exp = EXP[k];
    if (!got.length) {
      // ★这一档**没有样本** ⇒ 这一格**没被验证**。说出来、别算绿
      //   （"没跑"不等于"通过"）。开场没有选中态是**合法**的；"普通"/"大圈"两档为空
      //   则是真异常 —— 那说明分档规则或建类那处坏了。
      ck(k.indexOf('·选中') > 0,
         '没有任何标签落在「' + k + '」这一档 ⇒ 这一格的字号**没被现实锚验过**'
         + '（初始态本该有「普通」「大圈」两档；只有选中态可以合法为空）');
      continue;
    }
    ck(exp !== null, '「' + k + '」的期望值算不出来（CSS 里不是 px 字面量？）'
       + '—— 静态模型给不出期望值，这条锚就是空话');
    ck(exp !== null && got.length === 1 && Math.abs(Number(got[0]) - exp) < 0.01,
       '「' + k + '」档的渲染字号是 [' + got.join(', ') + ']px，静态模型算出来是 ' + exp + 'px'
       + '（★逐档比，不比极值）—— 浏览器没按产物 CSS 渲染，或层叠算错了');
  }
  // ★4g-b(γ) **可读下限**：只量**画出来的**标签。1a-ii 之前这里断言的是"**所有**标签的
  //   渲染字号 ≥ 8px"（`realMin >= 8`），那条**必须退役**：字号从 CSS 现读之后，
  //   "把某一档调到 8px 以下"是**合法**的（页面会跟着不画它）⇒ 再断言 realMin 就是假红，
  //   会把合法改版式的人拦下。下限改由"**画出来的**那些"承担：画了就得能读。
  //   ★空样本这一支**是绿的**，不是漏判：`drawn` 与上面的 `lit0`/`litEls` 取的是同一个集合
  //     （都是 opacity > 0），而"整批消失"由上面那两条**形状**判据接住
  //     （`litEls` 为空 ⇒ 两条都假 ⇒ 都红；**不是**由某条"至少 N 个"接住 ——
  //      那条 `lit0 >= 5` 已按"读数不是设计意图"退役，判定见上）。
  const drawn = nodeEls.filter(g => Number(getComputedStyle(g.__t).opacity) > 0).map(g => fontOf(g.__t));
  const drawnMin = drawn.length ? Math.min.apply(null, drawn) : 0;
  ck(!drawn.length || drawnMin >= 8,
     '画出来的标签里有 ' + drawnMin.toFixed(2) + 'px 的（灰糊）—— 闸门该在 8px 处拦住它');
  // ★**计数**判据（c0 2026-09-21 点出的盲点，与㉝/㉞同族）：`drawnMin` 只量**画出来的**样本，
  //   于是"字号掉到 8px 以下 ⇒ 浏览器不画 ⇒ 它**离开了判据的样本**"这条路上，
   //   **整批消失**在"最小字号"这条轴上是看不见的（对象不是变坏，是离开了样本）。
  //   ⇒ 另比一次**数**：真画出来的个数 vs "按 CSS 声明字号推出来的个数"。
  //   ★两个数**来源不同**才有力：右边用的是 python 从产物文本算出的 `__EFF_*__`，
  //     左边是页面真画出来的；页面若自己另有一份与 CSS 不符的字号（(α) 那条坏法），
  //     两个数就会分开 —— 这是 (α) 在**渲染层**的第二个落点。
  const expPx = t => (isBig(t) ? __EFF_MAX__ : __EFF_MIN__);
  const modelLit = nodeEls.filter(g => expPx(g.__t) * (svg.clientWidth / view.w) >= 8
                                    && onScreen(g.__n)).length;
  ck(lit0 === modelLit, '画出来的标签有 ' + lit0 + ' 个，而按 CSS 声明的字号推出来该有 '
     + modelLit + ' 个 —— 页面用了一份与 CSS 不符的字号（闸门没真的在读 CSS）');
  select('基金');
  ck(getComputedStyle(e).display === 'none', '点开后占位提示还看得见（CSS 的 display 压过了 hidden）');
  ck(d.getBoundingClientRect().height > 100, '解释区高度 ≈ 0');
  const off = d.getBoundingClientRect().top - p.getBoundingClientRect().top + p.scrollTop;
  ck(off < 40, '解释被推到面板顶部以下 ' + Math.round(off) + 'px（占位提示还在占位）');
  // ★整页不许纵向溢出：面板自己滚，别把图和页头一起顶出去。
  //   实测过两种撑法：占位提示没藏住（+738px）、栅格行高按内容撑（body 1446 vs 950）。
  ck(document.documentElement.scrollHeight <= innerHeight + 2,
     '整页纵向溢出 ' + (document.documentElement.scrollHeight - innerHeight) + 'px（面板没在自己滚）');
  ck(H('#stage') <= innerHeight + 2, '图区高 ' + Math.round(H('#stage')) + 'px，超出屏幕 ' + innerHeight + 'px');
  if (narrow) {
    // 窄屏是另一套布局：面板从底部升起（position:fixed），所以它得自己滚、且不许高过 76dvh。
    // ★判据必须是"面板在屏幕上的位置"，不能是 classList.contains('up') —— 自测里
    //   `#panel.up{transform:none}` 被拿掉之后，类名照样在、面板却整块滑在屏幕下面，
    //   按类名判会全绿放行（本判据第一版就是这么写的，被故障注入当场抓出来）。
    //   同一条纪律：读产物，不读输入。
    const pr = p.getBoundingClientRect();
    ck(pr.top < innerHeight - 40 && pr.height > 60,
       '窄屏点开后解释面板没升上来（面板顶边在屏幕下方 ' + Math.round(pr.top - innerHeight) + 'px）');
    ck(getComputedStyle(document.getElementById('close')).display !== 'none', '窄屏没有关闭按钮');
    ck(pr.height <= innerHeight * 0.76 + 2,
       '窄屏面板高 ' + Math.round(pr.height) + 'px，超过 76dvh（' + Math.round(innerHeight * 0.76) + 'px）');
    ck(p.scrollHeight > p.clientHeight, '窄屏面板没有内部滚动条（长解释被截断且看不到）');
  } else {
    ck(H('#panel') <= innerHeight + 2, '面板高 ' + Math.round(H('#panel')) + 'px，超出屏幕');
    ck(p.scrollHeight > p.clientHeight, '解释很长但面板没有内部滚动条（滚轮滚的是整页）');
  }
  document.getElementById('reset').onclick();
  const lit = nodeEls.filter(g => Number(getComputedStyle(g.__t).opacity) > 0).length;
  ck(lit === 0, '看全图后还有 ' + lit + ' 个标签画着');
  const vpx = realMin * (svg.clientWidth / view.w);
  ck(vpx < 8, '看全图没真缩下去（字号仍有 ' + vpx.toFixed(1) + 'px）');
  // ★读数通道：这一层以前**只报裁定、不报读数**，于是"改版式前后差了多少"只能靠人眼看截图。
  //   靠 profile 里的 `devtools.console.stdout.content` 把 console.log 接到 firefox 的 stdout。
  //   ★它是**读数**不是判据：不打印不等于没跑，但**改字号之前必须先把它取下来**，
  //     否则"页面变清楚了"与"判据放松了"分不开（`legible()` 那个 8px 闸就卡在这上面）。
  //   格式故意用 `|` 分隔的裸串（不走 JSON）：console 输出里 JSON 的引号会被再转义一层。
  //   ★1a-ii 起把**断言用的那几个数**（drawnMin / modelLit / 逐档期望值）一起吐出来 ——
  //     c0 的要求是"断言版本的那份读数在两条路上都看得见"：只看 realMin 那种旧读数，
  //     根本看不出断言到底在比什么。期望值由 python 注入，所以这里打的是**实际用来比的**那份。
  console.log('LICAI-R|' + lit0 + '|' + lit + '|' + realMin.toFixed(2) + '|' + realMax.toFixed(2)
    + '|' + drawnMin.toFixed(2) + '|' + (drawn.length || 0) + '|' + modelLit + '|'
    + (__EFF_MIN__ === null ? '-' : __EFF_MIN__) + '|'
    + (__EFF_MAX__ === null ? '-' : __EFF_MAX__));
} catch (err) { fails.push('探测脚本抛异常：' + err.name + ': ' + err.message); }
const v = document.createElement('div');
v.textContent = fails.length ? '✗ ' + fails.join(' ／ ') : '✓ 渲染判据全部通过';
v.style.cssText = 'position:fixed;left:0;bottom:0;width:100%;height:46px;z-index:2147483647;'
  + 'font:600 12px monospace;color:#fff;padding:5px 8px;overflow:hidden;'
  + 'background:' + (fails.length ? '#b00020' : '#0b7a3b');
document.body.appendChild(v);
</script>
"""
GREEN, RED = (11, 122, 59), (176, 0, 32)
# ★两个尺寸都要跑：宽屏是"图 + 右侧面板"，窄屏是"图 + 底部升起的面板"两套完全不同的布局，
#   只验一套等于另一套没验（而窄屏那套的面板是 position:fixed，最容易悄悄坏）。
SIZES = [('宽屏', '1400,950'), ('窄屏', '420,700')]


def render_check(html_text=None, size=None, tag='', note=''):
    """真浏览器跑一遍页面，读它自己画的那条色块。返回 True/False，工具不在时返回 None。

    `note` 只用来**标读数的来源**（"正常路" / "注入·xxx"）：读数两条路都会打，
    而注入那几趟的读数是"坏掉之后"的，不标出来就会被当成基线。
    """
    if size is None:
        size = SIZES[0][1]
    ff = shutil.which('firefox')
    if not ff:
        print('    ⊘ 本机没有 firefox —— 这一层没跑（★"没跑"不等于"通过"）')
        return None
    try:
        from PIL import Image
    except ImportError:
        print('    ⊘ 没有 pillow —— 这一层没跑（★"没跑"不等于"通过"）')
        return None
    # ★snap 版 firefox 只看得到 $HOME 里的**非隐藏**路径：临时页面/profile/截图必须
    #   放在 $HOME 下（放 /tmp 报 "Could not find profile folder"），而且**目录名不能带点**
    #   —— `~/.licai-render-xxxx` 会让它报 "x is already running"（snap 的 home 接口
    #   不覆盖隐藏文件，profile 目录在它眼里不存在）。两处都真踩过。
    # ★--no-remote 也不能省：不带它的话，只要你自己的 firefox 正开着，
    #   这句会当场退化成"x is already running"，退出码 0 且一张图都不出 —— 静默失败。
    work = tempfile.mkdtemp(prefix='licai-render-', dir=os.path.expanduser('~'))
    shot, ok = os.path.join(work, 'shot.png'), False
    try:
        prof = os.path.join(work, 'profile')
        os.makedirs(prof)
        # ★把页面的 `console.log` 接到 firefox 的 **stdout** 上（探针里那条 `LICAI-R` 读数）。
        #   不加这个 pref 的话，headless firefox 的 console 是**黑洞** —— 探针里算出来的数
        #   只在"它变成红字"的时候才看得见，绿的时候一个字都不吐。
        open(os.path.join(prof, 'user.js'), 'w', encoding='utf-8').write(
            'user_pref("devtools.console.stdout.content", true);\n')
        src = html_text if html_text is not None else open(HTML, encoding='utf-8').read()
        # 关掉过渡动画：标签淡出 .12s、面板滑入 .26s，截图会截在动画中途，
        # 把"正在淡出"读成"还画着" —— 这是真踩过的假红。
        src = src.replace('</style>', '*{transition:none !important}</style>', 1)
        # ★★(δ) 现实锚的**期望值**：从**同一份产物文本**（可能已被致残）算出四档生效字号，
        #   注进探针。★用 `None`（JS 里的 `null`）而不是 0 兜底：算不出来时那条断言**必红**，
        #   不能让它退化成一个"看着像数"的假期望值 —— 那样它会静默地永远绿。
        _eff = _lbl_eff(_lbl_css(src), _lbl_order(src))

        def _num(k):
            v = _eff.get(k)
            try:
                return '%.2f' % float(v)
            except (TypeError, ValueError):
                return 'null'
        probe = RENDER_PROBE
        for ph, k in (('__EFF_MIN__', '普通'), ('__EFF_MAX__', '大圈'),
                      ('__EFF_ON_MIN__', '普通·选中'), ('__EFF_ON_MAX__', '大圈·选中')):
            probe = probe.replace(ph, _num(k))
        j = src.rindex('</script>') + len('</script>')
        page = os.path.join(work, 'probe.html')
        open(page, 'w', encoding='utf-8').write(src[:j] + probe + src[j:])
        r = subprocess.run([ff, '--headless', '--no-remote', '--new-instance',
                            '--profile', prof,
                            '--window-size', size, '--screenshot', shot, 'file://' + page],
                           capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, LIBGL_ALWAYS_SOFTWARE='1'))
        if not os.path.exists(shot):
            print('    ★ firefox 没产出截图（退出码 %d）：%s'
                  % (r.returncode, (r.stderr or r.stdout).strip()[-160:]))
            return False
        # ★读数**两条路都打**（c0 2026-09-21：断言版本的那份读数要在两条路都看得见），
        #   但**必须带来源标签**：注入那几趟是"坏掉之后"的数，混在一起会被当成基线。
        #   ★基线只取**正常路**那一趟 —— `--selftest` 的输出不许拿来凑 1b 的前后对比
        #     （"改前/改后"必须是同一把尺子；而在有别的红的时候取的数会被**污染**，
        #      所以那一趟要挑一个树上没有别的红的时刻取）。
        #     1a-ii 之前这里有个 `readings=True` 开关（只有正常路打读数），现在两条路都打、
        #     靠 `note` 标来源 —— 理由是 c0 那条要求：**断言版本的那份读数两条路都要看得见**。
        for _ln in (r.stdout or '').splitlines():
            _m = re.search(r'"LICAI-R\|([^"]*)"', _ln)
            if _m:
                _f = (_m.group(1).split('|') + ['-'] * 9)[:9]
                print('    读数[%s] 打开时画着 %s 个 ｜ 看全图后 %s 个 ｜ 1:1 逐档实测 min %s / '
                      'max %s ｜ 画出来的里最小 %s（共 %s 个）｜ 按 CSS 推该有 %s 个 ｜ '
                      '期望值 %s / %s'
                      % (note or '正常路', _f[0], _f[1], _f[2], _f[3], _f[4], _f[5], _f[6],
                         _f[7], _f[8]))
        im = Image.open(shot).convert('RGB')
        px = im.getpixel((im.width // 2, im.height - 23))       # 色块中心
        if all(abs(a - b) < 30 for a, b in zip(px, GREEN)):
            ok = True
        elif all(abs(a - b) < 30 for a, b in zip(px, RED)):
            ok = False                    # 失败原因在截图上那条色块里写着，留图给人看
        else:
            print('    ★ 色块颜色读不出来（读到 %s），判据本身失败了 —— 当成红处理' % (px,))
        return ok
    finally:
        # 绿了不留垃圾；红了留一张，好让人直接看到页面上那条红字。
        # ★文件名带 **PID**：两个会话同时跑 `--selftest` 时（本仓库真会同时跑），
        #   固定名会让后跑的覆盖先跑的 —— 而覆盖掉的正是"某一次失败"的**唯一证据**。
        #   带 PID 之后各自留各自的那张；路径就打印在下面，别去猜。
        if os.path.exists(shot):
            last = os.path.join(HERE, 'render_last%s_%d.png' % (tag, os.getpid()))
            if not ok:
                shutil.copy(shot, last)
                print('    失败截图：%s' % last)
            elif os.path.exists(last):
                os.remove(last)
        shutil.rmtree(work, ignore_errors=True)


def run(js, fault=None, quiet=False):
    # 顺序要紧：桩 → 页面 JS → 故障注入 → 断言。
    # 故障要是放在页面 JS 之前，`D` 还在 TDZ 里，注入语句自己就 ReferenceError 崩了，
    # 自测会看到"变红"却根本没走到断言 —— 那是假红，等于没验证判据。
    src = STUB + '\n' + js + '\n' + (fault or '') + '\n' + CHECKS.replace('__FAM_N__', str(FAM_N))
    # ★路径带 PID：写死成 `/tmp/_licai_page_test.js` 时，两个会话同时跑 `--selftest`
    #   会互相覆盖这一份**被测输入** —— 后写的那个决定先跑的那个在测什么，
    #   而两边都会打印"通过"。node 报错里带着这个路径，所以留名、不删，便于回看。
    path = os.path.join(tempfile.gettempdir(), '_licai_page_test_%d.js' % os.getpid())
    open(path, 'w', encoding='utf-8').write(src)
    return subprocess.run(['node', path], capture_output=True, text=True)


def main():
    if '--selftest' in sys.argv:
        print('故障注入自测：每条都必须让判据变红（红不了 = 那条断言是空话）')
        # ★自测**整趟**都建在 node 上（FAULTS/CHECKS 都是拼成 JS 交给 node 跑的）。
        #   node 缺席时原来会直接抛 FileNotFoundError ⇒ traceback + 退出码 1，
        #   而真相是"自测根本没跑"。⇒ 与正常路同形的守卫：报 ⊘、返回 SKIP_RC。
        if not shutil.which('node'):
            print('⊘ 本机没有 node —— 桩那一层（FAULTS/CHECKS）整层跑不了。'
                  '"自测没跑"既不是通过也不是失败，退出码 %d' % SKIP_RC)
            return SKIP_RC
        r = run(script_body())
        print(f'  无故障            → 退出码 {r.returncode}'
              f'{"  ✗ 本该是 0" if r.returncode else "  ✓"}')
        ok = r.returncode == 0
        for tag, inj in FAULTS.items():
            r = run(script_body(), fault=inj)
            lines = [l.strip() for l in r.stdout.splitlines()]
            k = next((i for i, l in enumerate(lines) if l.startswith('★')), None)
            first = lines[k + 1] if k is not None and k + 1 < len(lines) else ''
            # ★判红要**同时**认"退出码非零"与"断言真的跑到了"（输出里出现 ★ 行）。
            #   只看退出码的话，注入自己崩掉也算非零退出 —— 注入语句是拼在断言**之前**
            #   执行的（见 run() 的顺序注释），它一抛异常，CHECKS 根本没跑就退出了，
            #   却正好满足"非零退出" ⇒ 打印"✓ 变红"、ok 仍为真，整轮报"判据具备否证能力"。
            #   ★最容易踩的触发方式不是"锚串没了"，是**绑定种类变了**：把 function 声明
            #   "顺手现代化"成 const 箭头，注入的赋值语句抛 TypeError ⇒ 退出码 1 且 stdout 空。
            #   实测（本机 node）：退出码 1、stdout 0 字节。
            #   ⇒ 判别法一行：**假红 = 非零退出 且 stdout 为空**（真红一定带 ★ 行）。
            crashed = r.returncode != 0 and k is None
            hit = r.returncode != 0 and k is not None
            mark = ('⊘ 注入把脚本打崩了，不是变红' if crashed
                    else '✓ 变红' if hit else '✗ 没红！')
            print(f'  {tag:34s} → 退出码 {r.returncode}  {mark}  {first[:44]}')
            if crashed:
                err = (r.stderr or '').strip().splitlines()
                print('       ★ 这条注入什么都没验（断言没跑到）：%s'
                      % (err[-1][:110] if err else '(stderr 也是空的)'))
            ok &= hit
        # 渲染层的判据在浏览器里跑，注入故障的方式只能是改页面源文本。
        # ★这里必须有对照组：要是浏览器压根没起来，故障那份也会"红" ——
        #   那是假红，等于这条判据其实没被验证。所以先确认干净副本是绿的。
        # 每种故障都拿两种故障源各打一次：一种是 CSS 的（hidden 藏不住），
        # 一种是布局的（body 只写 min-height → flex 撑不出高度）。
        # ── 静态内容层自测：这一层读的就是文本，所以注入方式就是改文本（不碰模板、不开浏览器）──
        # ★entries 是 (名字, 锚, 替换, **够打几份**, **红在哪条判据上**) 五元组 —— 后两项必须显式写。
        #   第 3/4 条用的是**同一个锚**（两张暗色表里各有一份），区别只在打几份：
        #   打 2 份 = 两表都坏 ⇒ 只有"暗色对比度"红（两表同值仍成立）；
        #   打 1 份 = 只坏第一张（`@media` 那张）⇒ 只有"两张同值"红（坏的那张仍过 AA）。
        #   ⇒ 这样每条注入都是**单目标**的，红了就知道是哪条判据抓的。
        #   ★第四项查的是 `got < n`（够不够打），**不是** `got != n`（恰不恰好）：锚在产物里
        #     出现几次是页面自己的事（两张暗色表天然各有一份）。打偏了（该打 2 份只打了 1 份）
        #     由第五项"红在哪条判据上"来抓 —— 打偏就会红成别的判据，期望子串对不上。
        print('\n静态内容层自测（读产物文本，不碰模板、不开浏览器）')
        src = open(HTML, encoding='utf-8').read()
        base_ok, base_lines = content_checks(src, quiet=True)
        base_stars = {l.strip() for l in base_lines if l.strip().startswith('★')}
        if base_ok:
            print('  对照组（未改动的产物）   → ✓ 绿')
        else:
            # ★产物当前本来就红（这一层刚写好、缺陷还没修）时，对照组**必然**是红的。
            #   那不是"这一层不可信"，是"页面还没修" —— 两种情况的修法完全不同，所以分开说，
            #   别让人拿着"判据坏了"去找一个根本不在判据里的问题。
            print('  对照组（未改动的产物）   → ✗ 红：%s' % (' ｜ '.join(sorted(base_stars)))[:110])
            print('       ★ 这是**产物本身**还不合格，不是判据坏了 —— 但这也意味着'
                  '今天证明不了"控制组是绿的"，得等缺陷修完再跑一次 --selftest 才算数')
        ok &= base_ok
        # ★★锚**从产物现读**，不写成字面量。写死成"当前那个色值"的话，哪天有人调了色，
        #    注入就整条失效 —— 而它失效的样子是"锚找不到"，跟"判据坏了"混在一起，
        #    修的人会去改锚而不是去查判据。锚钉的是**值**，值又是最常变的东西
        #    ⇒ 典型的"会烂的引用"。
        #    ★本轮真踩：这两条锚原本写死成当时的色值（亮 `#7C8A83` / 暗 `#7A8881`），
        #      刚把 --ink-3 调过 AA 线，写死的锚当场就烂了、自测报"注入没生效"。
        #      （注：那两个值现在已经不存在于产物里了，这里是留作教训的**历史值**，别去 grep 它。）
        #    现读当前值再扰动，锚就自我更新；只有"不够 n 份"才报，而那说明 token 表结构真的变了。
        tabs = {k: t for k, _s, t in token_tables(src)}
        lt3 = tabs.get('亮', {}).get('--ink-3', '')
        dt3 = tabs.get('暗·手动', {}).get('--ink-3', '')
        if not lt3 or not dt3:
            print('  ★ 认不出"亮/暗·手动"两张表里的 --ink-3（token 表结构变了），'
                  '对比度那两条注入构造不出来 —— 当成自测失败处理')
            ok = False
        else:
            # ── ★★认红的尺子（放在 `static_inject` **外面**：下面那段对照要 `ok = False`，
            #   而 `static_inject` 内部对 `ok` 的赋值只会绑成它自己的局部变量 ——
            #   那正好是"静默失败"：对照挂了，自测照样报全绿）──
            #   **两个实现 + 一行派发**：派发那行就是这次修改的**唯一回滚点**
            #   （c0 2026-09-21 裁定：改的是匹配器 ⇒ 必须能单独回滚，
            #   重跑若红，要在"尺子"与"1a-ii 本体"之间二分）。
            #   `_hit_raw` = 改前的尺子（**原样保留，别删** —— 阳性对照要用那一头）。
            def _hit_raw(want, lines):
                wants = (want,) if isinstance(want, str) else tuple(want)
                return any(w in l for w in wants for l in lines)

            # ★★`_hit_strip`：比之前先把**强调记号**从红字里剥掉。`want` 认的是
            #   "红落在哪条判据上"，而红字里会夹 `**加粗**` —— 本轮真踩：红字是
            #   `4g：页面里**找不到**读生效字号的那处调用`，而 `want='找不到读生效字号'`
            #   ⇒ **判据明明红了，却被报成"✗ 没红！"**（与"尺子坏过三次"同族：
            #   要匹配的短语被强调记号从中间劈开）。★方向是**把尺子做结实**，
            #   不是去改红字的措辞 —— 措辞会随版式改，尺子只该认内容。
            #   ★★**这一改只会让匹配更容易**（剥掉字符只可能多命中）⇒ "注入全变红"
            #   它自己证明不了任何东西（把 `_hit` 换成 `lambda *a: True` 也能全红）。
            #   ⇒ 按老账「取宽那一侧要配对照」，必须**同时**钉住"该红的红"与
            #   "**该不红的不红**"；两条对照常驻在下面，不是一次性跑过。
            #   已核：现有 `want` **没有一个**含 `**` —— 两重证据：㈠ 按 `want` 的实参位置
            #   扫过一遍（正则 + 人工看 `_lbl_inj` 那张表）；㈡ **跑完的实测**：
            #   剥掉之后全部注入仍然 `✓ 变红`、**零 ✗** —— 若哪个 `want` 里含 `**`，
            #   它此后永远不命中，那一行必然变成 `✗ 没红！`。（㈡比㈠硬，因为㈠漏了一处
            #   就会让㈡红——而㈡没红。）
            def _hit_strip(want, lines):
                wants = (want,) if isinstance(want, str) else tuple(want)
                plain = [l.replace('**', '') for l in lines]
                return any(w in l for w in wants for l in plain)

            _hit = _hit_strip          # ← ★★回滚点：改回 `_hit_raw` 即恢复改前的尺子

            # ── 尺子自己的对照（c0 的三条条件之二、之三）──
            _bold = '★ [graph.html] 4g：页面里**找不到**读生效字号的那处调用 （`getComputedStyle`…'
            _plain = '★ [graph.html] 4g：页面里找不到读生效字号的那处调用'
            _other = '★ [graph.html] <style> 块数 2（本仓库三页都是 1 个）'
            _RULER_N = 4

            def _ruler_controls(hs, hr, quiet=False):
                """把两条"尺子实现"拿进来跑四条对照，返回 (跑了几条, 是否全合预期)。

                ★**参数化**是为了让对照**自己也有否证面**：正常调用传真的两个实现；
                注入那两趟各传一个"坏掉的尺子"（恒真 / 旧尺子恒真）⇒ 对照必须报红。
                否则这一段就是"新加的判据没有注入"——没人知道它会不会红（㉝）。
                """
                cases = [
                    # ①阳性：短语被 `**` 劈开 —— 新尺子**必须**认出来（本次修的就是这一处）
                    ('阳性·强调劈开', hs, '找不到读生效字号', [_bold], True),
                    # ②阳性对照（条件三）：**旧尺子**在同一行上**必须认不出**
                    #   ⇒ 证明新尺子改的正是这一处，不是"顺手把别的也放宽了"
                    ('对照·旧尺子认不出', hr, '找不到读生效字号', [_bold], False),
                    # ③否定对照（条件二）：短语**真的不在**那条红字里 ⇒ 还得报没红
                    #   （不配这条，就是"把尺子调宽"：什么注入都红 ⇒ 否证能力反而没了）
                    ('否定·短语真的不在', hs, '找不到读生效字号', [_other], False),
                    # ④否定对照之二：**只差一个字**也必须不认 —— 钉住"子串匹配没被放宽成模糊匹配"
                    ('否定·只差一个字', hs, '找不到生效字号', [_plain], False),
                ]
                ran, good = 0, True
                for d, fn, w, ls, exp in cases:
                    ran += 1
                    got = fn(w, ls)
                    if got != exp:
                        good = False
                        if not quiet:
                            print('  ★ 尺子对照「%s」不合预期：得 %s、该得 %s —— '
                                  '改的是匹配器 ⇒ 尺子坏了会让**每一条注入的结论**都不可信'
                                  % (d, got, exp))
                if ran != _RULER_N:
                    good = False
                    if not quiet:
                        print('  ★ 尺子对照少跑了（跑了 %d 条、常量写的 %d 条）'
                              '—— 对照自己被吃掉时，"该不红的不红"就没人守了' % (ran, _RULER_N))
                return ran, good

            _r_ran, _r_ok = _ruler_controls(_hit_strip, _hit_raw)
            print('  %-16s → %s  %s'
                  % ('尺子的对照', '✓ 全过' if _r_ok else '✗ 对照不合预期',
                     '阳性(被强调劈开要认出来)+阳性对照(旧尺子认不出)+两条否定(真的不在就不许认)'))
            ok &= _r_ok
            # ★对照自己的否证面：两把坏尺子打下去，**必须**让对照报红。
            #   恒真的尺子只该打死"否定"那两条；旧尺子恒真只该打死"旧尺子认不出"那条 ——
            #   所以两趟分开打，**不能合成一条**（合成一条的话，其中一半坏了看不出来）。
            for _d, _hs, _hr in [('尺子恒真(否定该红)', lambda *a: True, _hit_raw),
                                 ('旧尺子恒真(阳性对照该红)', _hit_strip, lambda *a: True)]:
                _b_ran, _b_ok = _ruler_controls(_hs, _hr, quiet=True)
                print('  %-16s → %s  %s'
                      % (_d, '✓ 变红' if not _b_ok else '✗ 没红！',
                         '把坏尺子换上，对照照样说"全过" ⇒ 这段对照是装饰'))
                ok &= (not _b_ok)

            def static_inject(name, a, b, n, want, text=None, stars=None, pname='graph.html'):
                """改**产物文本**打一枪，认的是"红落在哪条判据上"。返回 True 表示这一枪真打红了。

                text/stars/pname 是给**别的页**用的：默认打 graph.html、底噪用它自己的、页名也是它。
                ★三者**必须一起换** —— 本轮真踩：换了文本却漏了页名 ⇒ `content_checks` 拿
                  graph.html 的例外表去查 index.html 的 CSS，于是 index 那些 color-mix 全成了
                  "未声明的底"，红的落在**颜色覆盖**上、而期望的那条判据压根没红。
                  这正是"判据的落点不是你想要的落点"的又一例：**换对象要连判据的身份一起换**。
                """
                subject = src if text is None else text
                base = base_stars if stars is None else stars
                got = subject.count(a)
                # ★查"够不够打 n 份"，**不是**"恰好 n 份"：`--ink-3:<暗色值>;` 本来就有两份
                #   （两张暗色表各一份），而"两张同值"那条**就该**只打第 1 份（只坏 @media 那张）。
                #   打偏（该打 2 份只打了 1 份、或锚被复制成了 3 份）由下面的"红的落在哪条判据上"抓：
                #   打偏了红的就是别的判据，`want` 对不上。
                if got < n:
                    print('  %-16s ★ 锚在产物里只出现 %d 次，这条要打 %d 份 —— 注入没生效'
                          '（锚是从产物现读的，次数不够说明 token 表结构变了）' % (name, got, n))
                    return False
                _red, lines = content_checks(subject.replace(a, b, n), name=pname, quiet=True)
                new = [l.strip() for l in lines
                       if l.strip().startswith('★') and l.strip() not in base]
                # ★认红要认到**具体哪条判据**红：只认"多了一条 ★"的话，注入打偏、红的是别的判据
                #   也算通过。产物本来就红时尤其危险 —— 底噪里随手就能多出一条来（本轮实测：
                #   "亮色对比度"那条注入红的其实是外链判据，照样打印了"✓ 变红"）。
                # ★`want` 允许是**一组**落点（任一命中即算红）：一条注入**设计上**可以
                #   同时驱动多条判据，那就该**如实枚举**它期望红的那个集合。
                #   ★为什么不满足于"单个串"：⑥（对调）设计上同时驱动 (a) 与 (b)，
                #   而它的 `want='被对调'` **只写在 (a) 的措辞里** ⇒ 有人删掉 (a) 之后，
                #   ⑥ 其实**照样把 (b) 打红了**、屏上真有一行红，却被打成"✗ 没红"。
                #   读者看到"✗ 没红"会去查注入，而真相是**判据被删了** —— 与"红的其实是
                #   另一件事"同族，方向相反（这次是"确实红了，却被报成没红"）。
                #   ★收紧的方向是"**如实枚举它该驱动的落点**"，**不是**"容忍任何红"：
                #   把 (a)(b) 都删掉时谁都不红 ⇒ 照样老老实实报"✗ 没红"，否证能力不放宽。
                # ★尺子的定义与它自己的对照在 `static_inject` **外面**（见上）——
                #   放里面的话 `ok = False` 只会绑成局部变量，对照挂了也静默。
                hit = _hit(want, new)
                print('  %-16s → %s  %s'
                      % (name, '✓ 变红' if hit else '✗ 没红！',
                         (' ｜ '.join(new) if new else '(没有新的 ★ 行)')[:600]))
                return hit

            # ── 4g：标签字号这一组判据，(α)(β) 两段**各打一枪以上** ──
            #   ★为什么两边都要打：只打 CSS 那一枪，判据可能只是"碰巧"红在一个方向上；
            #     两边都能红，才说明它真的是在比"两个来源"而不是在比"某个来源是不是坏的"。
            #     锚从产物**现读**（值、原文、规则整条都现读），不写死 —— 版式一改
            #     这个值就变，写死的锚会烂，而烂的样子和"判据坏了"混在一起。
            #   ★★1b 收编字号之后，`.lbl` 会变成 `var(--fs-cap)` ⇒ `_lbl_css` 抽不出数
            #     ⇒ 这一组注入的锚当场全失效。那时候**不是删掉它们**，是换打法：
            #     扰动 token + 断言"页面 JS 里不再出现字号字面量"（这一层读得到文本，做得到）。
            #     ⇒ 下面**响亮地失败**（不静默跳过），逼下一次动这里的人面对它。
            #   ★★**条数本身也是一条判据**（c0 2026-09-21）：本轮真发生过"某条注入的锚
            #     随重构失效 ⇒ 它静默消失，没人知道否证能力少了一条"。所以下面把条数
            #     **写成一个具名常量**并断言相等 —— 重构吃掉一条，这里当场红。
            _LBL_INJ_N = 9
            _cv = _lbl_css(src)
            _lc = _cv.get('.lbl')
            _tern = _lbl_tern(_lbl_body(src))
            _pred = _lbl_pred(src)
            # ★(α2) 的锚：页面里那处**读生效字号**的调用，从产物现读（不写死形参名）。
            _rc = re.search(r'getComputedStyle\s*\([^)]*\)\s*\.\s*fontSize', src)
            _READ_CSS_ANCHOR = _rc.group(0) if _rc else None
            # ★`.lbl.big` / `.lbl.on` 的**整条规则**才是唯一锚：`font-size:12px` 全页出现 8 次、
            #   `font-size:12.5px` 3 次（grep 实测）⇒ 拿裸声明当锚会打到别人身上。
            #   锚一律**从产物现读**，不写死 —— 收编字号时值会变，写死的锚当场烂。
            _rule = lambda sel: (re.search(re.escape(sel) + r'\{[^}]*\}', src) or [None]) and \
                                re.search(re.escape(sel) + r'\{[^}]*\}', src).group(0)
            _swap = re.search(r'(\.lbl\.big\{[^}]*\})(\s*)(\.lbl\.on\{[^}]*\})', src)
            _rbig, _ron = _rule('.lbl.big'), _rule('.lbl.on')
            _lbl_inj = []
            if _lc and _tern and _pred and _rbig and _ron and _swap and _cv.get('.lbl.big') \
                    and _READ_CSS_ANCHOR:
                _bump = re.sub(r'font-size\s*:\s*[0-9.]+px',
                               'font-size:%.1fpx' % (float(_lc) + 0.5), _rbig)
                _lbl_inj = [
                    # ① 小圈那边错开：把 CSS 的值改掉 ⇒ 垫片（=页面重算用的数）与它对不上
                    ('垫片与 CSS 对不上(CSS)', 'font-size:%spx' % _lc, 'font-size:9px', 1,
                     '垫片与产物 CSS 对不上'),
                    # ② **大圈**那边错开。★这一枪此前是缺的：只打 `.lbl` 那一枪时，
                    #   "`.lbl.big` 这一对也在比"这件事**从没被验证过** —— 把大圈那一段判据
                    #   整段删掉，自测照样全绿。改成 `.lbl`+0.5 是为了**单目标**：
                    #   它落在 (小圈, 选中态) 之间，只红"对不上"，不惊动下面的序判据。
                    #   ★锚必须是**整条规则**：`font-size:12px` 全页 8 次、裸数字 `12` 全页 90 次
                    #     （本行初稿就退化成裸数字了，`got` 报 90 才被发现）。
                    ('垫片与 CSS 对不上(big)', _rbig, _bump, 1, '垫片与产物 CSS 对不上'),
                    # ③ ★★打的是 (α1)「`legible()` 体内不许再出现两个字面量二选一」——
                    #   1a-ii 之前这条注入的锚是"页面 JS 里的**数字**三元"（`? 12 : 10.5`）；
                    #   1a-ii 之后页面里是 `? FS.big : FS.base` ⇒ 那个锚**不存在了**，
                    #   注入会**静默消失**（不报错、不打 ⊘、不红）。⇒ 独立重指（c0 明确要求，
                    #   不能指望"改指 ①②"顺带救它）：锚改成从**产物现读**函数体里的三元原文。
                    #   ★★替换值**故意取 CSS 当前的两个值**：这样注入后的页面**行为完全不变**
                    #     （模型算出来的还是 12/10.5）—— 也就是说，**只有 (α1) 能抓住它**。
                    #     这不是"为了好构造"，是把真实事故演出来：有人手工把 `FS.big`/`FS.base`
                    #     换回字面量、值抄得一模一样，页面照常工作，而"两处手写拷贝"这个
                    #     原始缺陷**原地复活**，且没有任何行为上的症状。
                    ('JS 换回字面量三元(静默)', _tern[0],
                     '? %s : %s' % (_cv['.lbl.big'], _cv['.lbl']), 1, '字面量二选一'),
                    # ④ 打的是"抽不出来"那一支：把 CSS 那条声明整个拿掉 ⇒ 判据必须报
                    #   "CSS 里没有 .lbl 的 font-size"。★这一支特别重要：它会**假绿**——
                    #   抽不出还继续，就是一条永远绿的判据（`_hex` 那轮栽过同形的）。
                    ('字号声明整条缺失', 'font-size:%spx;' % _lc, '', 1, 'CSS 里没有'),
                    # ⑤ 「哪些标签算大圈」的**谓词**：只改建类那一处 ⇒ 画多大与按多大过闸分叉
                    ('大圈谓词两处分叉', _pred[0] + "? ' big'",
                     re.sub(r'\d+', lambda m: str(int(m.group(0)) - 2), _pred[0], count=1)
                     + "? ' big'", 1, '谓词'),
                    # ⑥ 层叠：把 `.lbl.big` 与 `.lbl.on` **对调**。字号声明一个字没动、
                    #   `_lbl_css` 读出来一模一样，**渲染结果却反了**（选中反而变小）。
                    #   这条注入存在的意义：证明"读生效值"那一段真的在算层叠，而不是在比声明。
                    #   ★**期望红的落点集合 = {(a), (b)}**（实测两行都出，见 2026-09-21 那次
                    #     放宽截断后的读数）：对调既违反 (a) 的中立不变量（12.5 vs 12.0），
                    #     又让 (b) 里大圈那一对 `12 > 12` 不成立。⇒ `want` 必须**两个都列**。
                    ('两条规则对调(层叠)', _swap.group(0),
                     _swap.group(3) + _swap.group(2) + _swap.group(1), 1, ('被对调', '没有变大')),
                    # ⑦ 选中态被改小 ⇒ **只有**序判据红：两个配对值一个没动，
                    #   而"两种选中态彼此相等"那条不变量也不响（两边都变 9、恰好相等）。
                    #   ★`want` 与判据的**措辞**是焊死的：序判据从 `>=` 收到 `>` 时，
                    #     那句话从"反而变小"改成了"没有变大" —— 这里不同步，这条注入当场变"✗ 没红"。
                    ('选中态字号被改小', _ron, re.sub(r'font-size\s*:\s*[0-9.]+px',
                                                      'font-size:9px', _ron), 1, '没有变大'),
                    # ⑧ ★「把 `.lbl.on` **削平**到与大圈同一档」= **选项 A 的忠实模拟**。
                    #   它就是 (b2) 那条"钉住决定"的判据的**对照物**：
                    #   若当初 `.lbl.on` 映到 `--fs-sm`(12)、而 (b2) 还停在 `>=`，
                    #   这条注入**当场被放行**、静默通过 —— 所以它的存在正说明
                    #   2026-09-21 那个拍板（映 `--fs-base`(13)）是**承重的**，不是审美。
                    #   ★它**只**该红 (b2)，而且只红两对里的大圈那一对：
                    #     · (a) 中立不变量**不响** —— 两个选中态都变 12、恰好相等；
                    #     · 普通那一对 10.5 → 12 仍然严格变大。
                    #   红多了一条（比如 (a) 也响），说明打偏了、不是这条注入在说话。
                    ('选中态削平到与大圈同档', _ron,
                     re.sub(r'font-size\s*:\s*[0-9.]+px',
                            'font-size:%spx' % _cv['.lbl.big'], _ron), 1, '没有变大'),
                    # ⑨ ★★打 (α2)「页面必须真的**读** CSS」。1a-ii 之前这条坏法不存在
                    #   （页面本来就是手写常量），1a-ii 之后它成了**唯一**能让字号悄悄
                    #   与 CSS 分家的位置 —— 把 `getComputedStyle` 那处调用换成一份常量。
                    #   ★替换值取 CSS 当前值：同样是**行为不变**的注入，只有 (α2) 抓得住。
                    #   ★它**不**惊动 (α1)：换掉的是**函数体外面**那处调用，(α1) 只看体内。
                    ('JS 改回硬写常量(静默)', _READ_CSS_ANCHOR,
                     'parseFloat(0) || %s' % _lc, 1, '找不到读生效字号'),
                ]
            else:
                print('  ★ 抽不出 .lbl 的 px 字号 / `legible()` 的函数体·三目 / '
                      '大圈谓词 / `.lbl.big`·`.lbl.on` 两条规则 / 页面那处读生效字号的调用 '
                      '—— 4g 的 %d 条注入构造不出来'
                      '（收编字号时就会这样）。**这不是"跳过几条注入"**：'
                      '这几条守的正是"字号只有一处真源、桩不许说谎、层叠顺序不能反"，'
                      '必须改成新的打法（扰动 token + 断言 JS 里没有字面量），不能删' % _LBL_INJ_N)
                ok = False
            # ── ★条数判据（c0 2026-09-21）：**否证能力本身**也要有守卫 ──
            #   本轮真发生过：一条注入的锚随重构失效 ⇒ 它**静默消失**，不报错、不打 ⊘、
            #   只是自测里少一行"✓ 变红"，没有人会注意到（㉝）。上面那个响亮分支只接得住
            #   "整组构造不出来"，接不住"少了一条"。⇒ 把条数写成常量并断言。
            if _lbl_inj and len(_lbl_inj) != _LBL_INJ_N:
                print('  ★ 4g 的注入只构造出 %d 条（应为 %d）—— 有一条**静默消失**了：'
                      '否证能力少一条，而自测仍然全绿' % (len(_lbl_inj), _LBL_INJ_N))
                ok = False
            # ── (β) 的**对照物**：扰动垫片本身（垫片错、CSS 对 ⇒ (β) 必须红）──
            #   ★为什么必须有这一枪：只扰动 CSS 的那两枪（①②）证明不了"判据在看垫片"——
            #     它们红的是"CSS 变了"，红因也可能来自别的比较。**扰动垫片能红**，
            #     才说明垫片真的在被比。
            #   ★★这是垫片**唯一**可能的否证面：垫片锚不在现实上（见 4g 那段的三层链），
            #     所以"垫片错了"只能由注入演出来，指望不上现实把它顶出来。
            #   ★手法：临时改模块级状态 + `finally` 还原 —— 垫片**不是产物文本的一部分**，
            #     改不了 `src`。（同 4h 那条"临时改状态"的先例。）
            _saved_fs = dict(STUB_FS)
            try:
                STUB_FS['.lbl'] = '%.1f' % (float(_saved_fs['.lbl']) + 0.5)
                _o_shim, _lines_shim = content_checks(src, quiet=True)
            finally:
                STUB_FS.clear()
                STUB_FS.update(_saved_fs)
            _hit_shim = any('垫片与产物 CSS 对不上' in l for l in _lines_shim)
            print('  %-16s → %s  %s'
                  % ('扰动桩垫片', '✓ 变红' if _hit_shim else '✗ 没红！',
                     '垫片错、CSS 对 ⇒ "桩说谎"必须被抓住' if _hit_shim
                     else '判据没在看垫片 ⇒ 它守的其实是别的东西'))
            ok &= _hit_shim
            for name, a, b, n, want in [
                ('外部引用计数', '<title>',
                 '<link rel="stylesheet" href="https://example.invalid/x.css"><title>', 1,
                 '页面引用了外部资源'),
                # 打成不过线的值：#BCC5C0 在亮色三张底上 1.6~1.8，#3A4440 在暗色三张底上 1.5~1.8
                ('亮色对比度', '--ink-3:%s;' % lt3, '--ink-3:#BCC5C0;', 1, '亮：'),
                ('暗色对比度', '--ink-3:%s;' % dt3, '--ink-3:#3A4440;', 2, '暗·'),
                # 这张表仍过 AA（实测三张暗底 4.78/5.22/5.68），坏的只是"两张表不同值"
                ('两张暗色表同值', '--ink-3:%s;' % dt3, '--ink-3:#84928A;', 1, '两张暗色表不同值'),
            ] + _lbl_inj:
                ok &= static_inject(name, a, b, n, want)

            # ── 本轮新增的判据，各配一条注入（★新判据没有注入 = 没人知道它会不会红）──
            v_r2 = tabs.get('暗·手动', {}).get('--rule-2', '')
            v_c2 = tabs.get('暗·手动', {}).get('--card-2', '')
            if not v_r2 or not v_c2:
                print('  ★ 认不出暗表的 --rule-2/--card-2（token 表结构变了）⇒ '
                      '新判据的注入构造不出来 —— 当成自测失败处理')
                ok = False
            else:
                for name, a, b, n, want in [
                    # 4a `<style>` 块数：多出第二块 = 样式跑到 token 纪律之外
                    ('style 块数', '</style>', '</style><style>body{color:red}</style>', 1,
                     '本仓库三页都是 1 个'),
                    # 4f 底色覆盖：凭空多出一种没人声明的底（不带 color:，所以不会顺带触发配对判据）
                    ('底色覆盖', '</style>', 'body{background:#123456}</style>', 1, '不在例外表里'),
                    # 4f-0 三表颜色 token 齐全：从一张表里**删掉**一个颜色 token。
                    #   挑 `--rule-2`：它没被任何"底×字"对用到 ⇒ **单目标**，只红 4f-0。
                    ('三表 token 齐全', '--rule-2:%s;' % v_r2, '', 1, '少声明了'),
                    # 4f-3 空转（⊘ 也要报红）：删掉一张表里的 `--card-2` —— 它是**配对用到的**底
                    #   ⇒ 那一对在暗表里解析不出色值。★这一枪会同时红三条（4c/4d 的"缺 token"、
                    #   4f-0、以及这里的 ⊘），`want` 认的就是 ⊘ 那条：**同一个故障被多条判据接住**
                    #   是纵深，不是重复 —— 要证明的是每一条**各自**都会响。
                    ('配对空转(⊘)', '--card-2:%s;' % v_c2, '', 1, '解析不出色值'),
                ]:
                    ok &= static_inject(name, a, b, n, want)

            # ── 4h 白名单理由：它的判据读的是**模块状态**（`BG_EXC`），不是产物文本 ──
            #   ⇒ `static_inject` 那条路**够不着它**（改文本改不动它）。改用"临时改状态 +
            #     `finally` 还原"，与 ⊘ 退出码协议那条同一个办法。
            #   ★这段**必须存在**：判据没有被任何注入够得着 = 静默状态，而 `--selftest`
            #     只遍历**已声明**的注入、不会报"哪条判据从没被注入过"。
            #     （这一课是本轮从 `verify_agent.py` 那侧学来的：那边补 `_c_pipeline` 时同一形状。）
            #   ★还原必须在 `finally`：注入撤不回来会污染后面每一条（本轮纪律里已有这一条）。
            _saved4h = BG_EXC['graph.html'].get('__4h_probe__', _MISSING)
            try:
                BG_EXC['graph.html']['__4h_probe__'] = ('', None)
                _ok4h, _l4h = content_checks(src, quiet=True)
                _hit4h = any('白名单条目没有理由' in l for l in _l4h)
                print('  %-16s → %s' % ('白名单理由(空)',
                                        '✓ 变红' if _hit4h else '✗ 没红！'))
                ok &= _hit4h
            finally:
                if _saved4h is _MISSING:
                    BG_EXC['graph.html'].pop('__4h_probe__', None)
                else:
                    BG_EXC['graph.html']['__4h_probe__'] = _saved4h
            # ★还原之后**自证一次**：4h 的判据是读模块状态的，撤不干净会让**后面每条**
            #   都多出一条红 —— 那正是"注入撤不回来污染后面每一条"。所以这里当场复查一次
            #   "污染已清"，而不是等后面某条判据莫名其妙地红。
            _after4h, _ = content_checks(src, quiet=True)
            if base_ok and not _after4h:
                print('  %-16s ★ 4h 的注入没撤干净（还原后这一层仍红）—— 后面每条判据都不可信了'
                      % '白名单理由(还原)')
                ok = False

            # ── 4f-3b（例外表与规则对不上）：**只能拿 index.html 打** ──
            #   它要在"某条底上有规则推出来的字色、而例外表也声明了 fg"时才可能红；
            #   graph.html 的例外表一条 fg 都没声明 ⇒ 在它身上这一枪永远打不响（不是判据坏，是没对象）。
            #   ★先立这一页**自己的对照组**：未改动的 index.html 现在必须是绿的，
            #     否则下面那一枪"红了"也说明不了是谁造成的（底噪和注入混在一起）。
            if os.path.exists(os.path.join(HERE, 'index.html')):
                idx = open(os.path.join(HERE, 'index.html'), encoding='utf-8').read()
                idx_ok, idx_lines = content_checks(idx, name='index.html', quiet=True)
                idx_stars = {l.strip() for l in idx_lines if l.strip().startswith('★')}
                if not idx_ok:
                    print('  %-16s ⊘ index.html 对照组现在是红的（%s）—— 这一枪推迟到它回绿再打'
                          % ('表与规则一致', (' ｜ '.join(sorted(idx_stars)))[:56]))
                    ok = False
                else:
                    # `color:var(--card)` → `color:var(--paper)`：两个 token **都过 AA**
                    #   （亮 6.45→~6.0、暗 8.16→8.87）⇒ AA 那条**不会响**，红的只该是"两边对不上"。
                    #   ★这就是为什么值得挑这一对：**单目标**。
                    ok &= static_inject(
                        '表与规则一致',
                        'background:var(--accent);color:var(--card);',
                        'background:var(--accent);color:var(--paper);', 1, '两边对不上',
                        text=idx, stars=idx_stars, pname='index.html')
            else:
                print('  %-16s ★ index.html 不在 ⇒ 这条判据（表与规则一致）在自测里无对象' % '表与规则一致')
                ok = False

            # ── 退出码真值表：`verdict_code` 是"整趟验证算不算数"的那道门，只能这样钉 ──
            #   ★`(False, None) → 1` 这一档是**要害**：原写法在这里返回 0（真红被"没跑"洗成绿）。
            #     它**走子进程测不出来** —— 那要求"同时有真红、又刚好没 firefox"，
            #     而那正是**基线被别处的红污染**的状态（我实测时就踩过：那个 1 来自别的红，
            #     于是"改前是绿的"这条证据什么也没证明）。
            #   ⇒ 判定要拆成纯函数用真值表钉；端到端那一枪只负责钉**接线**（下面那条）。
            print('\n退出码真值表（verdict_code：0=全过 / 1=有真红 / SKIP_RC=有层没跑）')
            NAMEV = {True: '过', False: '红', None: '没跑'}
            for case, want in [((True, True), 0), ((True, False), 1), ((False,), 1),
                               ((True, None), SKIP_RC), ((None, None), SKIP_RC),
                               ((False, None), 1)]:
                got = verdict_code(list(case))
                hit = got == want
                print('  %-22s → %s  得 %d（期望 %d）%s'
                      % ('+'.join(NAMEV[x] for x in case), '✓' if hit else '★ 错！', got, want,
                         '' if hit else '  ← 失败优先被破了：没跑把红洗掉了' if want == 1 else ''))
                ok &= hit

            # ── ⊘ 的退出码协议（**元**判据：验"没跑 ≠ 通过"这件事本身）──
            #   做法：把 firefox 从 PATH 上藏掉、再跑一次自己的 `--selftest`，看**退出码**。
            #   ★必须把 `node` 软链进那个临时目录：FAULTS 那一层靠 node 跑，PATH 全空会连 node
            #     一起藏掉 ⇒ 那一层以**假红**的方式失败、退出码成 1，这一枪就"因为别的原因红了"，
            #     什么也没证明（这正是它要防的那类假象）。
            #   ★只跑 `--selftest`、**不跑正常路**：正常路会先 `build_graph.py` 全量重建
            #     （把被测产物重写一遍），在共享仓库里跑它会污染别人的窗口。
            #   ★递归保护：嵌套那次自己也带这条注入 ⇒ 用环境变量让它跳过（只跳这一条）。
            _n = shutil.which('node')
            if os.environ.get('LICAI_SKIP_EXITCODE_TEST'):
                print('  %-16s ⊘ 嵌套调用，跳过这一条（递归保护）' % '⊘ 退出码协议')
            elif not _n:
                # ★原来是 `if _node: os.symlink(...)` —— **找不到就静默什么都不做**，
                #   然后嵌套那趟以 FileNotFoundError 崩掉、rc=1。前提不成立时必须**响**：
                #   而且**不能让它照样跑下去** —— 嵌套那趟一旦因"没有 node"而返回 SKIP_RC，
                #   这行的 `hit` 就会是 True、打印"✓ 变红"，**红的其实是另一件事**。
                #   ⇒ 整条不跑、也不打印"变红"，只报"没跑"。
                print('  %-16s ⊘ 本机没有 node —— 这一枪要往临时 PATH 里软链 node，链不了就跑不了'
                      '（★"没跑"不等于"通过"）' % '⊘ 退出码协议')
                ok = False
            else:
                with tempfile.TemporaryDirectory() as _d:
                    os.symlink(_n, os.path.join(_d, 'node'))
                    r = subprocess.run(
                        [sys.executable, __file__, '--selftest'], cwd=HERE, capture_output=True,
                        text=True, env=dict(os.environ, PATH=_d, LICAI_SKIP_EXITCODE_TEST='1'))
                hit = r.returncode == SKIP_RC
                # ★这判据**预设了"别的层是绿的"**：嵌套那趟若还有别处的真红，退出码就是 1
                #   （失败优先，正确行为），而不是 SKIP_RC —— 那不是它坏了。
                #   ⇒ 分三档说清楚，别把"树上有别的红"误报成"这条判据没红"：
                #     强档（SKIP_RC）= 接线对且全树绿；弱档（非零 + stdout 有 ★ 行）= 接线也对，
                #     只是嵌套那趟里另有一条真红；两者都算通过，但**打印里必须看得出是哪一档**。
                #   ★弱档的"停"必须带"stdout 非空"：**非零退出 + stdout 为空 = 脚本自己崩了**
                #     （断言根本没跑），那是假红、不算通过。
                weak = r.returncode != 0 and bool((r.stdout or '').strip())
                tail = (r.stdout or '').strip().splitlines()
                print('  %-16s → %s  退出码 %d%s'
                      % ('⊘ 退出码协议', '✓ 变红' if (hit or weak) else '✗ 没红！', r.returncode,
                         '（= SKIP_RC）' if hit else '（非零，但不是 SKIP_RC ⇒ 嵌套那趟另有真红）'
                         if weak else '（**0 = 会被读成"全绿"**，那正是这条要防的）'))
                if not (hit or weak):
                    print('       %s' % (tail[-1][:100] if tail else '(stdout 为空 = 它自己崩了)'))
                elif not hit:
                    print('       嵌套那趟的末行：%s' % tail[-1][:96])
                ok &= (hit or weak)

        print('\n渲染层自测（真浏览器，两种尺寸）')
        src = open(HTML, encoding='utf-8').read()
        # ★故障要挑"这一层的判据看得见"的：布局那条（body 只有 min-height）在窄屏
        #   根本不发作 —— 窄屏的面板是 position:fixed，图区又有 min-height:420px，
        #   内容装得下 700px 的窗口，于是注入之后窄屏照旧全绿。这不是判据漏了，
        #   是那条故障不属于窄屏；硬要它红就会得到一条永远红的假判据。
        # ★★1a-ii 起这层有三条**字号相关**的注入，各打一条判据（(β)(γ)(δ) 各一）。
        #   逐条理由写在下面。★条数**写成常量并断言**（㉝）：本轮真发生过"一条注入随重构
        #   静默消失、而自测照样全绿"。
        # ★两个列表**条数相同**（各 2 条布局/隐藏 + 3 条字号 = 5），差别只在**第二项是哪一条**：
        #   宽屏 `body 只有 min-height` / 窄屏 `面板升不起来` —— 那条布局故障在**对面尺寸不发作**
        #   （理由见上），所以不是"同一故障两边各一条"，是"各挑了一条在**这边**发作的"。
        #   常量照**数出来的**写（`cripples` 两个字面量里各有 5 个名字），不照观察到的读数写。
        #   注意：上面 `if` 那支（构造不出注入）两个列表各只有 2 条，**不受这两个常量管** ——
        #   它自己已经 `ok = False`，且条数判据在 `else` 支里。
        _wide_n, _narrow_n = 5, 5
        _lblpx = _lbl_css(src).get('.lbl')
        _lbl_px_on = _lbl_css(src).get('.lbl.big')
        _rm = re.search(r'\.lbl\{[^}]*\}', src)
        _lbl_rule = _rm.group(0) if _rm else None
        _tern_src = _lbl_tern(_lbl_body(src))
        if _lblpx is None or _lbl_px_on is None or _lbl_rule is None or _tern_src is None:
            # ★收编之后 `.lbl` 的 font-size 会变成 `var(--fs-cap)`，`_lbl_css` 就抽不出数了。
            #   那时**必须有人回来改这三条注入**（改成扰动 token，而不是扰动规则）——
            #   所以这里**响亮地失败**，不许静默跳过：跳过 = 这一层的否证能力悄悄少三条。
            print('  ★ 抽不出 .lbl / .lbl.big 的 px 字号、`.lbl{...}` 整条规则、'
                  '`legible()` 里的三元 —— 渲染层这三条注入构造不出来，静态层 4g 那一组也得跟着改，'
                  '不能删掉了事')
            ok = False
            cripples = {
                '宽屏': [('hidden 藏不住', [('[hidden]{display:none !important}', '/* 拿掉 */')]),
                         ('body 只有 min-height', [('  height:100dvh;\n', '  min-height:100dvh;\n')])],
                '窄屏': [('hidden 藏不住', [('[hidden]{display:none !important}', '/* 拿掉 */')]),
                         ('面板升不起来', [('#panel.up{transform:none}', '#panel.up{}')])],
            }
        else:
            # ── ① CSS 那一档字号改小 ──
            #   ★这条注入**改的是自己的期望值**，不是"证明字号重要"：1a-ii 之后页面从 CSS
            #     现读 ⇒ 字号一小，**页面自己就不画那些字了** ⇒ 它现在红的是渲染层那两条
            #     **形状**下限里的 (㈡)"第一屏里一个普通档的词都没有"（这一枪改的正是
            #     **普通档** `.lbl` 的字号 ⇒ 那一档从第一屏整批消失，而枢纽词还在）——
            #     **不再**是"探针读没读真值"，也**不再**是"条数跌破某个 5"。
            #     这个变化是 1a-ii 的**目的**，不是判据退化：以前它必须靠"所有标签 ≥8px"
            #     那条来红，而那条已经退役（见探针里）。
            #   ★注：这条注入与那条下限之间因此是**语义对齐**的 —— 它演的就是
            #     "度数小的词没名字、屏幕上只剩线和点"这个真症状。
            _shrink = ('font-size:%spx' % _lblpx, 'font-size:5px')
            # ── ② CSS 改小 **+ 闸门写死旧值**（= c0 说的 (β) 的对照物）──
            #   ★两处一起改才成故障：只把闸门换回字面量、而字面量恰好等于 CSS 当前值，
            #     是**行为完全不变**的（那正是 ③ 那条静态注入演的事）。要让"糊字照画"真发生，
            #     必须 CSS 掉到 8px 以下、而闸门不知道 ⇒ 这一枪红 `drawnMin >= 8`。
            #   ★这是 (β)（闸门端点）的否证面；(δ)（现实锚）在它下面**不响**是对的 ——
            #     现实与 CSS 是一致的（都 5px），错的是闸门。
            _hard = (_tern_src[0], '? %s : %s' % (_lbl_px_on, _lblpx))
            # ── ③ 让**现实**与**静态模型**错开（(δ) 的否证面）──
            #   插一条 `#stage .lbl{font-size:14px}`：它靠 **id 选择器**赢过 `.lbl`，
            #   而 `_lbl_css` 的正则只认 `.lbl` 那一段、且**同名键后来者覆盖** ⇒ 模型算出 10.5、
            #   现实渲染 14。★这一枪**只**该红 (δ)：`drawnMin` 是 14 ≥ 8（绿）、
            #   计数两边都算"全画"（绿）、(β) 在桩那层、这一层不跑。
            #   ★它守的正是那条断言的形状："逐档比、不比极值"—— 这一枪两个端点没人管，
            #     要靠**逐档**才抓得住（普通档 10.5→14）。
            _prio = (_lbl_rule, '#stage .lbl{font-size:14px}' + _lbl_rule)
            cripples = {
                '宽屏': [('hidden 藏不住', [('[hidden]{display:none !important}', '/* 拿掉 */')]),
                         ('body 只有 min-height', [('  height:100dvh;\n', '  min-height:100dvh;\n')]),
                         ('CSS 字号改小（页面该跟着少画）', [_shrink]),
                         ('CSS 改小+闸门写死旧值（糊字照画）', [_shrink, _hard]),
                         ('现实与静态模型错开（逐档锚）', [_prio])],
                '窄屏': [('hidden 藏不住', [('[hidden]{display:none !important}', '/* 拿掉 */')]),
                         ('面板升不起来', [('#panel.up{transform:none}', '#panel.up{}')]),
                         ('CSS 字号改小（页面该跟着少画）', [_shrink]),
                         ('CSS 改小+闸门写死旧值（糊字照画）', [_shrink, _hard]),
                         ('现实与静态模型错开（逐档锚）', [_prio])],
            }
            # ★条数判据（同静态层那条）：重构吃掉一条注入，这里当场红。
            #   ★打印要说**方向**：多与少是两件事 —— 少了是"否证能力丢了一条"（本条要抓的），
            #     多了是"常量自己过期了"（判据在说读者该去改常量）。原先把两种都写成"只有 N 条"，
            #     实测就撞上过（常量 4、实到 5）⇒ 打印自相矛盾，读者还得回去数。
            for _lb, _n in (('宽屏', _wide_n), ('窄屏', _narrow_n)):
                _got = len(cripples[_lb])
                if _got != _n:
                    print('  ★ %s的渲染层注入实测 %d 条、常量写的 %d 条 —— %s'
                          % (_lb, _got, _n,
                             '有一条**静默消失**了：这一层的否证能力少一条，而自测仍然全绿'
                             if _got < _n else
                             '比常量多：**先回去数 `cripples` 那个字面量**，多数是常量过期了；'
                             '确认真多了就该把常量改大，别直接照读数改'))
                    ok = False
        # ★锚的**唯一性**：下面用的是 `src.replace(a, b, 1)` —— 只替换**第一处**，
        #   而循环里的守卫查的是 `a not in src`（"在不在"），**不问"有几处"**。
        #   重做版式时最容易留下重复锚（新旧样式并存、或某条规则被复制进两个 media query），
        #   那时只有第一份被致残；真正生效的若是第二份，**注入等于没打，而守卫不响、自测报全绿**。
        #   ⇒ 查"出现次数恰好 1"，不是查"存在"。（查的是**产物**，不是模板。）
        for a in sorted({a for cs in cripples.values() for _n, edits in cs for a, _b in edits}):
            n = src.count(a)
            if n != 1:
                print('  ★ 锚 "%s" 在产物里出现 %d 次（应为恰好 1 次）—— '
                      '替换只打中第一处，指向它的注入可能没生效' % (a.strip(), n))
                ok = False
        skipped = 0
        for label, size in SIZES:
            tag = '_' + {'宽屏': 'wide', '窄屏': 'narrow'}[label]
            base = render_check(src, size=size, tag=tag, note='对照组')
            if base is None:
                # ★这里**不**动 `ok`：`ok` 只表示"跑过的那些层里有没有空断言"。
                #   "这一层没跑"是**第三种状态**，由退出码 SKIP_RC 承载（见 main 末尾那段）。
                #   原先把两者混在一起（或干脆 `continue` 掉不提）⇒ 自测会打印
                #   "全绿 ✓ 判据具备否证能力"，而渲染层一行没跑 —— **自测认证了一个它没检验的能力**。
                print('  %s ⊘ 没跑（工具不在）—— 这一层未被验证，不能算通过' % label)
                skipped += 1
                continue
            print('  %s 对照组（未改动的页面）    → %s' % (label, '✓ 绿' if base else '✗ 本该是绿，这一层不可信'))
            ok &= base
            # ★一条注入可以是**多处编辑**（`_shrink_hard` 就得同时改 CSS 和闸门：只改一处
            #   是"行为完全不变"的，演不出故障）。⇒ 每条注入是 `[(a, b), …]` 一串。
            for name, edits in cripples[label]:
                _miss = [a for a, _b in edits if a not in src]
                if _miss:
                    print('  ★ 故障注入没生效（源里找不到 "%s"）—— 判据没被验证'
                          % _miss[0].strip())
                    ok = False
                    continue
                _inj = src
                for _a, _b in edits:
                    _inj = _inj.replace(_a, _b, 1)
                hit = render_check(_inj, size=size, tag=tag, note='注入·' + name) is False
                print('  %s 注入"%s"  → %s' % (label, name, '✓ 变红' if hit else '✗ 没红！'))
                ok &= hit
        # ★★这句打印是**结论**，必须与退出码一致：有层没跑就**不许**说"全绿"。
        #   原写法 `'自测' + ('全绿 ✓ …' if ok else '★ …')` 只看 `ok`，而 ⊘ 那条路
        #   既没动 `ok` 也没登记 ⇒ 退出码 0 + "全绿 ✓ 判据具备否证能力"，**渲染层一行没跑**
        #   （实测：`PATH=<空目录>` 跑出 rc=0；实测前它是 rc=1，那个 1 来自别处的红，见 git log）。
        if not ok:
            print('\n自测★ 有断言是空的，必须修')
            return 1
        if skipped:
            print('\n自测 ⊘ 渲染层 %d/%d 种尺寸没跑 —— **这不是"全绿"**：'
                  '判据的否证能力只在其余层被证明；退出码 %d' % (skipped, len(SIZES), SKIP_RC))
            return SKIP_RC
        print('\n自测全绿 ✓ 判据具备否证能力')
        return 0

    print('[1] 重建（内含词表四项检查 + 两层一致性 + 布局自检）')
    r = subprocess.run([sys.executable, 'build_graph.py'], cwd=HERE,
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        print('   ', line)
    if r.returncode:
        print(r.stderr[-800:])
        return 1

    js = script_body()
    # ★★`node` 是这套脚本的**外部依赖**之一（另一个是 `[5]` 的 firefox）。
    #   在它缺席的机器上，`subprocess.run(['node', …])` 抛的是 **FileNotFoundError**、
    #   不是"非零退出" —— traceback 打出来、退出码 1，于是消费端把它读成**真红**，
    #   而真相是"这台机器没装 node"。⇒ 与 `[5]` **同形**：工具不在就记 `None`（没跑），
    #   由 `verdict_code` 把整趟降级成 SKIP_RC。**不要**用一个大 try/except 包住 ——
    #   那会把"node 语法错误"和"node 不在"混成同一个结果。
    #   ★实测（c0 量的）：藏掉 node 跑正常路 ⇒ 改前 `rc=1` + traceback；改后应为 `rc=3`。
    have_node = bool(shutil.which('node'))
    verdicts = []

    print('\n[2] JS 语法（node --check）')
    if not have_node:
        print('    ⊘ 本机没有 node —— 这一层没跑（★"没跑"不等于"通过"）')
        verdicts.append(None)
    else:
        p = '/tmp/_licai_syntax.js'
        open(p, 'w', encoding='utf-8').write(js)
        r = subprocess.run(['node', '--check', p], capture_output=True, text=True)
        print('    ' + ('✓ 通过' if not r.returncode else '★ ' + r.stderr.strip()[:200]))
        verdicts.append(not r.returncode)
        if r.returncode:
            return 1

    print('\n[3] 页面加载 + 每个节点的面板 + 点开/收起')
    if not have_node:
        # ★`[3]` 与 `[2]` 用的是**同一个**外部依赖，必须一起守：
        #   只守 `[2]` 的话，node 缺席时 `[3]` 照样抛 FileNotFoundError。
        print('    ⊘ 本机没有 node —— 这一层没跑（★"没跑"不等于"通过"）')
        verdicts.append(None)
    else:
        r = run(js)
        print(r.stdout.rstrip() or r.stderr.rstrip()[:400])
        verdicts.append(not r.returncode)
        if r.returncode:
            return r.returncode

    print('\n[4] 静态内容（三页各验：<style> 块数 / 外部引用 / 文字×底色对比 / 两块暗色同值 / '
          '底色覆盖 / token 集合 / 标签字号两边相等 / 白名单理由）')
    ok4 = True
    for name in SITE_PAGES:
        print('  ── %s ──' % name)
        try:
            h = open(os.path.join(HERE, name), encoding='utf-8').read()
        except OSError:
            # ★"判据守的对象不存在"必须当红：文件不在时如果只是跳过，这一页就是零验证而整趟仍绿
            print('   ★ %s：文件读不到 —— 判据守的对象不存在，不能算绿' % name)
            ok4 = False
            continue
        pok, _ = content_checks(h, name=name)
        ok4 &= pok                  # ★以好为真：全绿才放行；任何一个 False 都拦住
    if not ok4:
        return 1

    print('\n[5] 真浏览器渲染（桩看不见的那一层：CSS 优先级、栅格行高、viewBox 缩放）')
    for label, size in SIZES:
        # ★`note` 只影响读数那一行怎么标来源：正常路标"正常路"，注入那几趟标"注入·<名字>"。
        #   1a-ii 之前这里是 `readings=True`（只有正常路打读数），现在两条路都打、靠标签分。
        v = render_check(size=size, tag='_' + {'宽屏': 'wide', '窄屏': 'narrow'}[label])
        print('    %s（%s）→ %s' % (label, size, {True: '✓ 通过', False: '★ 不通过（红字见截图）',
                                                 None: '⊘ 没跑'}[v]))
        verdicts.append(v)
    # ── 退出码是**三态**，不是二态；合成规则在 `verdict_code()`（那里写了为什么失败优先）──
    #   0 = 全都跑了且都过；1 = 有真红；SKIP_RC（见文件顶部）= 有层没跑（"未验证"）。
    #   ★这段注释里**不许写码的字面值** —— 本轮真踩：改码时漏改了这里的旧注释（还写着 2），
    #     与顶部 `SKIP_RC = 3` 直接矛盾；下一个读这段的人会照抄出一个错的码。
    #     ⇒ 注释里只写**语义**（SKIP_RC），字面值只允许出现在它自己的定义那一行。
    # ★为什么必须是三态（**理由留、主张过期**）：**当时**那个消费者
    #   （`verify_agent.py` 里那条跨进程判据）判绿时**只把"全过"当通过**，
    #   二态下"没跑"会顺着那条边变成"通过"。
    #   把"未验证"塞进 stdout 是**没用的** —— 人能看见，闸门看不见（`⊘` 只活在 detail 字符串里）。
    #   三态的好处：**任何**消费者（CI、`&&` 链、别的 harness）都能分辨，不必解析 stdout。
    #   ★上面三行是**理由**，理由当时是对的 —— 所以是改时态，不是删。
    #     过期的是**主张**（那个消费端此后已把 `SKIP_RC` 登记成 skip）；
    #     而必要性**不依赖它**，理由照样留得住。
    #   ★★这段**点了名**，而点名的引用会烂：别信这里的名字与状态，去读那个消费端**实际**的登记分支
    #     （它若已改名或被拆开，就以"消费端拿到 `SKIP_RC` 之后干了什么"为准）。
    if any(v is None for v in verdicts):
        print('    ⊘ 有层没跑 ⇒ 退出码 %d（"未验证"，既不是通过也不是失败）' % SKIP_RC)
    return verdict_code(verdicts)


if __name__ == '__main__':
    sys.exit(main())
