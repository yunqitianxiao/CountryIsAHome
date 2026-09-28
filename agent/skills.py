"""把 `skills/` 里两个技能的**流程骨架**按需注入提示词。

★这个模块存在的唯一理由，是把「框架」和「数字」分开：
    框架（考察哪几个维度、按什么顺序）→ 进提示词，让大模型知道该问什么、该看什么
    数字（集中度是多少、相关是多少）  → **只来自工具**，大模型一个都不许自己算

为什么不是把 SKILL.md 全文塞进去（两份合计 297 行）：
  ①挤掉工具结果 —— 上下文就那么大，塞 300 行框架等于把刚取回来的数挤出去；
  ②**最要命的是那些阈值表**。SKILL.md 里写着"单只 > 15% 为严重""前5大 > 50% 偏高"，
     一旦进了提示词，大模型就会**拿眼睛看着持仓去套这些档位** —— 它连权重都
     没算过，套出来的档位看着完全合理。这正是 `agent/game.py` 的 `_fail()`
     拼命在防的事（"抓不到行情就明确报错，绝不编数字"）。
  ⇒ 所以这里**一条阈值都不出现**：阈值住在 `agent/portfolio_rules.py`（与 skill 的
     md 逐项比对，有判据钉着），判断由 `analyze_portfolio` 工具做完再交上来。

判据：`verify_agent.py` 里那条"注入了框架 / 没触发时不许有"的对照，以及
      "这段文字里不许出现任何阈值数字"。
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(os.path.dirname(HERE), 'skills')

# 两个技能：名字 → 触发词。触发词是**用户会说的话**，不是分类标签。
SKILLS = {
    'portfolio-health-check': (
        '组合', '持仓', '仓位', '体检', '诊断', '分散', '集中', '重仓',
        '相关性', '回撤', '波动', '压力测试', '我的投资', '我的基金',
        '这几个', '这几只', '这样配', '配置'),
    'risk-adjusted-return-optimizer': (
        '风险调整', '夏普', '索提诺', '收益优化', '性价比', '划不划算',
        '风险收益', '风险收益比', '更优的配置', '怎么调仓'),
}

# 从 SKILL.md 里抽「第X步：标题」。**只抽标题**，理由见下面 COVERAGE 那段。
_STEP = re.compile(r'^###\s*第[一二三四五六七八九十]+步[：:]\s*(.+)$')

# ★这些是**步骤名**，不是判据。抽的时候把标题里的动词留下就够指路了。
_MAX_STEPS = 9


def _steps_md(name):
    """从一个 SKILL.md 里抽出步骤名。读不到就返回 []。"""
    path = os.path.join(SKILLS_DIR, name, 'SKILL.md')
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    steps = []
    for ln in lines:
        m = _STEP.match(ln)
        if m:
            steps.append(m.group(1).strip())
    return steps[:_MAX_STEPS]


def framework_block(question, skills=None):
    """问题 → 一段流程骨架提示词。**不触发就返回 ''**（整段不出现）。

    ★`skills` 参数是给判据用的（注入一个假名字，看这段还在不在），正常调用不传。
    """
    q = str(question or '')
    if not q:
        return ''
    want = []
    for name, keys in SKILLS.items():
        if any(k in q for k in keys):
            want.append(name)
    if skills is not None:
        want = list(skills)
    if not want:
        return ''

    body = []
    for name in want:
        steps = _steps_md(name)
        if not steps:
            # ★读不到就**什么都不加**，不编一段骨架出来 —— 编出来的框架会让
            #   大模型按一个不存在的流程去问，比没有框架更糟。
            continue
        body.append('【%s】' % name)
        for i, s in enumerate(steps, 1):
            body.append('  %d. %s' % (i, s))
    if not body:
        return ''

    return ('【分析框架】下面这个用户的问题落在组合诊断上，按这套**步骤**来组织你的回答'
            '（有几步没数据就跳过那步，不要硬凑）：\n' + '\n'.join(body) +
            COVERAGE +
            '\n★★框架只告诉你**该看哪几个维度**，**不含任何阈值、也不含任何数字**。'
            '集中度、相关性、波动、回撤、Beta 这些**一个都不许自己估** —— '
            '必须调 `analyze_portfolio` 拿回来，档位由它给。'
            '工具报"算不了"就照实说算不了，不要用框架里那套维度硬套出一个结论。')


# ★为什么**故意不注入每一步的"判据维度"**（方案里原本写的是"步骤名 + 判据维度"）：
#   skill 的第二步到第七步，表格第一列大量是**本站根本没有数据源**的东西 ——
#   流动性（要成交量）、价值/成长/规模/动量/质量因子（要组合层面的财务加权）、
#   VaR/CVaR/夏普/索提诺（要收益分布假设）、以及压力测试那**五个历史情景**
#   （2015股灾/2018贸易摩擦/2020疫情/2022调整/2008金融危机 —— 我们没有分情景的
#   历史持仓数据）。实测把它们抽出来之后，提示词里就出现了一行
#   「6. 压力测试（看：2015年股灾、2018年贸易摩擦、…）」。
#   ★那行字等于**邀请大模型编数**："2015 股灾时你这个组合大约会跌 45%" ——
#     读起来完全像那么回事，而我们一个字的数据都没有。
#   ⇒ 维度名不进提示词；改成本站**能算什么**的明确交代。能算的那几维本来就会
#     由 `analyze_portfolio` 连同档位一起交上来，不缺这一行。
COVERAGE = (
    '\n★本站对这套框架的**数据覆盖**（照着这个说，不要越界）：\n'
    '  · **能算**（由 `analyze_portfolio` 给出，直接引用它的数）：单只/前五大集中度、'
    'HHI、两两相关与相关性聚集、有效分散比率 EDR、年化波动、最大回撤、相对沪深300 的 Beta、'
    '按 Beta 线性外推的压力情景、行业分布（**仅 A 股个股有**）。\n'
    '  · **算不了**（不要给数、也不要给区间）：流动性/变现天数（本站没有成交量数据）、'
    '因子暴露里的价值·成长·规模·动量·质量（没有组合层面的财务加权）、'
    'VaR/CVaR/夏普/索提诺（没有收益分布假设）、'
    '以及那五个历史情景（2015/2018/2020/2022/2008）—— 本站**没有分情景的历史持仓数据**，'
    '所以**一个情景数字都编不出来**，用户问到就直说取不到。')

