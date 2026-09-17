#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交互页的验收判据：重建 → 语法 → 每个节点的面板 → 每个节点的点开/收起 → 真浏览器渲染。

两条腿，缺一不可：
[1-3] 把页面里**真正要发布的那份 JS** 抠出来，套一个极简 DOM 桩跑一遍。
      凡是本该出现的文字没出现、或本该展开的东西没展开，就非零退出。
[4]   真浏览器（firefox --headless）截图，让页面自己把结论画成一个色块，再读像素。
      桩只认 DOM 属性，看不见 CSS 优先级、布局、以及"字被 viewBox 缩小"这类事
      —— 本轮三个真故障（坐标没写、占位提示没藏住、面板不给 min-height:0）桩全都看不见，
      全是靠 [4] 才看见的。★所以 [4] 不是锦上添花，它看的是另一层。

─── 为什么不用肉眼 / 不靠"看起来对" ───
· 面板是拼字符串拼出来的，拼错了不会抛异常，只会静默少一段 ⇒ 必须断言"该有的文字在不在"
· 断言必须能输出"我错了"：--selftest 会故意注入十几种故障，
  每一种都必须让判据变红；有一种红不了，就说明那条断言是空的。
· ★`h.includes('')` 恒为真 —— 第一版判据用 `h.includes(例子文本)` 检查字段，
  数据里字段为空时等于什么都没查，对照组 2 因此假绿。现在先要求"字段真的有值"，再查渲染结果。
· ★`members` 为空数组时 for 循环体一次都不跑，判据同样假绿。
  现在改成数 `data-go` 按钮的个数和 members.length 比，不是看有没有标题。
· ★桩里的 clientWidth/clientHeight 是**编的**，所以"1 用户单位 = 1 像素"这种事它在结构上
  就判不了对错，只能判"自洽"。[4] 量的是真画布。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(HERE, 'graph.html')
DATA = os.path.join(HERE, 'graph_data.json')

# ── DOM 桩：够跑通建图、render()、select()、panelHTML() 就行 ──
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
"""

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
  # 15 让第一屏一个标签都没有（验证"至少 5 个能读的词"这条下限不是空话）。
  '14 视图外的标签照画':            "onScreen = () => true; render();",
  '15 打开时视图内标签全不画':       "onScreen = () => false; render();",
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
want(byIdEl('legend').children.length === 6, '图例 chip 数 = ' + byIdEl('legend').children.length + '，应为 6');
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
const labelPx = n => (n.deg >= 11 || n.kind === 'cat' ? 12 : 10.5) * (svg.clientWidth / view.w);
const opacity = g => Number(g.__t.style.opacity);
const inView = n => n.x >= view.x && n.x <= view.x + view.w && n.y >= view.y && n.y <= view.y + view.h;
const shown = nodeEls.filter(g => opacity(g) > 0);
/* ★先要一个对照组：shown 为空时 every/filter 全都不跑，下面那条断言会假绿
   （和 `h.includes('')` 恒真是同一类坑）。而且要的不只是"非空"——第一屏的承诺是
   "一打开就是能读的词"，所以这里按能读的词给下限。 */
want(shown.length >= 5, '打开时只有 ' + shown.length + ' 个标签（第一屏该是能读的词，不是空图）');
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


# ── [4] 真浏览器渲染判据 ──
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
  const lit0 = nodeEls.filter(g => Number(getComputedStyle(g.__t).opacity) > 0).length;
  ck(lit0 >= 5, '打开时只有 ' + lit0 + ' 个标签（第一屏该是能读的词）');
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
  const vpx = 10.5 * (svg.clientWidth / view.w);
  ck(vpx < 8, '看全图没真缩下去（字号仍有 ' + vpx.toFixed(1) + 'px）');
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


def render_check(html_text=None, size=None, tag=''):
    """真浏览器跑一遍页面，读它自己画的那条色块。返回 True/False，工具不在时返回 None。"""
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
        src = html_text if html_text is not None else open(HTML, encoding='utf-8').read()
        # 关掉过渡动画：标签淡出 .12s、面板滑入 .26s，截图会截在动画中途，
        # 把"正在淡出"读成"还画着" —— 这是真踩过的假红。
        src = src.replace('</style>', '*{transition:none !important}</style>', 1)
        j = src.rindex('</script>') + len('</script>')
        page = os.path.join(work, 'probe.html')
        open(page, 'w', encoding='utf-8').write(src[:j] + RENDER_PROBE + src[j:])
        r = subprocess.run([ff, '--headless', '--no-remote', '--new-instance',
                            '--profile', prof,
                            '--window-size', size, '--screenshot', shot, 'file://' + page],
                           capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, LIBGL_ALWAYS_SOFTWARE='1'))
        if not os.path.exists(shot):
            print('    ★ firefox 没产出截图（退出码 %d）：%s'
                  % (r.returncode, (r.stderr or r.stdout).strip()[-160:]))
            return False
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
        if os.path.exists(shot):
            last = os.path.join(HERE, 'render_last%s.png' % tag)
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
    src = STUB + '\n' + js + '\n' + (fault or '') + '\n' + CHECKS
    path = os.path.join('/tmp', '_licai_page_test.js')
    open(path, 'w', encoding='utf-8').write(src)
    return subprocess.run(['node', path], capture_output=True, text=True)


def main():
    if '--selftest' in sys.argv:
        print('故障注入自测：每条都必须让判据变红（红不了 = 那条断言是空话）')
        r = run(script_body())
        print(f'  无故障            → 退出码 {r.returncode}'
              f'{"  ✗ 本该是 0" if r.returncode else "  ✓"}')
        ok = r.returncode == 0
        for tag, inj in FAULTS.items():
            r = run(script_body(), fault=inj)
            hit = r.returncode != 0
            lines = [l.strip() for l in r.stdout.splitlines()]
            k = next((i for i, l in enumerate(lines) if l.startswith('★')), None)
            first = lines[k + 1] if k is not None and k + 1 < len(lines) else ''
            print(f'  {tag:34s} → 退出码 {r.returncode}  {"✓ 变红" if hit else "✗ 没红！"}  {first[:44]}')
            ok &= hit
        # 渲染层的判据在浏览器里跑，注入故障的方式只能是改页面源文本。
        # ★这里必须有对照组：要是浏览器压根没起来，故障那份也会"红" ——
        #   那是假红，等于这条判据其实没被验证。所以先确认干净副本是绿的。
        # 每种故障都拿两种故障源各打一次：一种是 CSS 的（hidden 藏不住），
        # 一种是布局的（body 只写 min-height → flex 撑不出高度）。
        print('\n渲染层自测（真浏览器，两种尺寸）')
        src = open(HTML, encoding='utf-8').read()
        # ★故障要挑"这一层的判据看得见"的：布局那条（body 只有 min-height）在窄屏
        #   根本不发作 —— 窄屏的面板是 position:fixed，图区又有 min-height:420px，
        #   内容装得下 700px 的窗口，于是注入之后窄屏照旧全绿。这不是判据漏了，
        #   是那条故障不属于窄屏；硬要它红就会得到一条永远红的假判据。
        cripples = {
            '宽屏': [('hidden 藏不住', ('[hidden]{display:none !important}', '/* 拿掉 */')),
                     ('body 只有 min-height', ('  height:100dvh;\n', '  min-height:100dvh;\n'))],
            '窄屏': [('hidden 藏不住', ('[hidden]{display:none !important}', '/* 拿掉 */')),
                     ('面板升不起来', ('#panel.up{transform:none}', '#panel.up{}'))],
        }
        for label, size in SIZES:
            tag = '_' + {'宽屏': 'wide', '窄屏': 'narrow'}[label]
            base = render_check(src, size=size, tag=tag)
            if base is None:
                print('  %s ⊘ 没跑（工具不在）—— 这一层未被验证，不能算通过' % label)
                continue
            print('  %s 对照组（未改动的页面）    → %s' % (label, '✓ 绿' if base else '✗ 本该是绿，这一层不可信'))
            ok &= base
            for name, (a, b) in cripples[label]:
                if a not in src:
                    print('  ★ 故障注入没生效（源里找不到 "%s"）—— 判据没被验证' % a.strip())
                    ok = False
                    continue
                hit = render_check(src.replace(a, b, 1), size=size, tag=tag) is False
                print('  %s 注入"%s"  → %s' % (label, name, '✓ 变红' if hit else '✗ 没红！'))
                ok &= hit
        print('\n自测' + ('全绿 ✓ 判据具备否证能力' if ok else '★ 有断言是空的，必须修'))
        return 0 if ok else 1

    print('[1] 重建（内含词表四项检查 + 两层一致性 + 布局自检）')
    r = subprocess.run([sys.executable, 'build_graph.py'], cwd=HERE,
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        print('   ', line)
    if r.returncode:
        print(r.stderr[-800:])
        return 1

    js = script_body()
    print('\n[2] JS 语法（node --check）')
    p = '/tmp/_licai_syntax.js'
    open(p, 'w', encoding='utf-8').write(js)
    r = subprocess.run(['node', '--check', p], capture_output=True, text=True)
    print('    ' + ('✓ 通过' if not r.returncode else '★ ' + r.stderr.strip()[:200]))
    if r.returncode:
        return 1

    print('\n[3] 页面加载 + 每个节点的面板 + 点开/收起')
    r = run(js)
    print(r.stdout.rstrip() or r.stderr.rstrip()[:400])
    if r.returncode:
        return r.returncode

    print('\n[4] 真浏览器渲染（桩看不见的那一层：CSS 优先级、栅格行高、viewBox 缩放）')
    verdicts = []
    for label, size in SIZES:
        v = render_check(size=size, tag='_' + {'宽屏': 'wide', '窄屏': 'narrow'}[label])
        print('    %s（%s）→ %s' % (label, size, {True: '✓ 通过', False: '★ 不通过（红字见截图）',
                                                 None: '⊘ 没跑'}[v]))
        verdicts.append(v)
    if any(v is None for v in verdicts):
        return 0                     # 工具不在 = 这一层没跑，不算红也不算绿
    return 0 if all(verdicts) else 1


if __name__ == '__main__':
    sys.exit(main())
