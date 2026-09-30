#!/usr/bin/env python3
"""规则校对位：成稿里的每个数字，都要能在站点目录里找到出处。

★ 这是那个**非 LLM** 的位置（对应议会里的风控官那一票否决 —— 也是算出来的、
  不经过模型）。它要兑现的是导览团最重要的一条承诺：**目录里没有的不许编**。

  为什么不能靠提示词嘱咐：嘱咐是**指令**，模型可以不照做，而且不照做的时候
  看不出来 —— 一段编了数字的话和一段照念的话，长得一模一样，读起来都很专业。
  把它变成**一条能跑的判据**，才有唯一答案：把这个数拿去目录里查，查不到就是查不到。

★ 它**只做一件能证伪的事**：说"这个数字字面在 site_map.json 里出现过/没出现过"。
  它**不说**：
    - "这个数用对了"（243 个起点写成 234 个，字面在目录里，这条查不出来）；
    - "这句话是错的"（口径错、因果反了，都不在它的射程内）。
  把射程写在这里，是因为**一条声称管得比它实际管得宽的判据，比没有判据更坏**：
  它会让读的人以为这一块已经有人守着了。超出射程的那部分，靠的是导览员各自
  只看自己那块目录（`slices.py`）+ 主持手上一个数都没有（它只能转述）。

★ 允许的例外是**声明出来的**，不是悄悄放宽的（`LANG`、`extra`）：
  下面这两条各有理由，且都写在它旁边。
"""

from . import slices

# 句法数：0..12 一律放行。理由：这类数在中文里是**句法**不是**事实** ——
# "第 1 轮""两种看法""三个角色"。不放行的话，成稿里每出现一个"两个"
# 都会亮一次红，校对位就变成噪音源，而噪音的结局是被人关掉。
LANG = frozenset(str(i) for i in range(0, 13))


def _key(n):
    """排序用：数字按大小排，排不了按字面。"""
    try:
        return (0, float(n))
    except (TypeError, ValueError):
        return (1, 0.0)


def check(text, catalog, extra=()):
    """成稿 → {'numbers', 'unsourced', 'n', 'ok', 'why'}

    ★`extra` 是**用户自己问句里的数**。理由：用户问"255 个词条是真的吗"，
      主持回答里出现的 255 是从**问题**里来的 —— 那是转述用户，不是编。
      不把它算进来，每一条"用户带着数字问"的问题都会误报。

    ★主持**一个字都没说出来**时返回 `ok=None`（不是 `True`）。
      空文本量不出任何东西，"没量到"和"量到了没问题"是两件事；
      返回 True 就是**空样本恒真**那一族错（判据量的是一个空集合，
      于是"整批消失"和"全都合格"在它眼里一模一样）。
    """
    text = text or ''
    if not text.strip():
        return {'numbers': [], 'unsourced': [], 'n': 0, 'ok': None,
                'why': '主持这一趟没有输出，校对位无从下手'}
    found = slices.numbers_in(text)
    pool = slices.catalog_numbers(catalog) | set(LANG) | {str(x) for x in (extra or [])}
    unsourced = sorted({n for n in found if n not in pool}, key=_key)
    return {
        'numbers': sorted(set(found), key=_key),
        'unsourced': unsourced,
        'n': len(found),
        'ok': not unsourced,
        'why': ('每个数都能在站点目录里找到出处' if not unsourced
                else '有 %d 个数字在站点目录里找不到出处' % len(unsourced)),
    }


def line(a):
    """给界面/日志读的一行。★查得到和查不到要说成**两句话**，
    不能合一句含糊的"校对完成"（那样红绿在读数上没差别）。"""
    if a.get('ok') is None:
        return a.get('why') or '校对位这一次没跑'
    if a.get('ok'):
        return ('校对位：成稿里 %d 个数字，都能在站点目录里找到出处'
                % (a.get('n') or 0))
    return ('校对位：成稿里 %d 个数字，其中 %d 个在站点目录里**找不到出处**：%s'
            % (a.get('n') or 0, len(a.get('unsourced') or []),
               '、'.join(a.get('unsourced') or [])))
