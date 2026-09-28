"""独立风控官：**可复核的数值条件**，以及命中之后的否决/降级。

★这个模块的存在理由，是把"风险"从一句形容词变成一个**能被反驳的条件**。

    「感觉风险大」——这句话没法被检验，所以它对结论**一点约束力都没有**：
    谁都可以说，谁都可以不听，下一轮它还在。
    「年化波动 62.4% ≥ 阈值 60%」——这个可以被检验、被质疑（"你阈值凭什么 60%"）、
    被同意或不同意。**能被反驳的判断才算判断。**

规格 §3.4 对风控官有两条硬要求，这里是它们的实现：
  ① **独立于多空**：它不投方向票（`roles.DIRECTIONAL['risk'] is False`，
     权重恒为 0）。它影响结论靠的是下面这套否决/降级，不是往加权和里塞一票。
  ② **命中条件 ⇒ `veto` 非空且最终 verdict 必须体现它**：硬条件（level='veto'）
     把方向**降级成 flat**，软条件（level='warn'）保留方向但**强制加风险提示**
     并**扣掉一部分置信度**。两档都照实在 `level` 里写明。

★阈值全部是模块级常量、且**每条都写清取值理由与量纲**。它们大多是**先验档位**
  （没有本机回测支持），所以注释里明说"这是先验"——把一个先验阈值说成"实测标定过的"
  和编一个数字是同一种错。唯一例外是 `data_gap`（0.5）：它是"过半探针失败"，
  这个数不需要标定，它本身就是"这份数据不够用"的定义。
"""

import score

# ── 阈值（每条的取值理由见注释）───────────────────────────────────────
#
# 年化波动下限。★先验档位：A股个股长期年化多在 25%~45%，60% 是"极端档"
#   （参考 `weights.CRISIS_VOL_PCT`，两处必须同一个数 —— 场景判定和风控条件是
#   同一件事的两个视角，分叉了就会出现"场景说 crisis、风控说没事"这种自相矛盾）。
VOL_VETO_PCT = 60.0

# 从**区间最高点**回撤多少算"深"。★先验档位（-35%）：再往上一档 -20% 太常见
#   （普通回调），往下一档 -50% 只有崩盘才到，中间这档正好是"这段行情已经走过一轮熊"。
DD_VETO_PCT = -35.0

# 探针失败率上限。★这个**不是先验**：0.5 = "过半没取到"，它本身就是
#   "这份数据不够支撑一个方向判断"的定义，不需要标定。
MISS_VETO_RATE = 0.5

# 集中度。HHI 是权重平方和（0~1），★0.35 对应"前两大占了约六成"这种结构；
#   top5 占比 80% 是同一条线的另一种说法（`portfolio.py` 算的就是这两个）。
#   ★先验档位，理由同上。
HHI_VETO = 0.35
TOP5_VETO_PCT = 80.0

# 分歧度。多空立场值之差。★先验档位（1.4）：立场值域是 [-1,1]，
#   1.4 意味着"一个人强烈看多、另一个强烈看空"这种结构性对立 ——
#   那时候加权和的**中间值不代表共识，只代表双方各拉一半**。
SPREAD_WARN = 1.4

# 置信度折扣（软条件命中时）。★为什么是 0.8 而不是 0.5：
#   软条件说的是"这个判断的环境不好"，不是"这个判断是错的"。
#   砍掉 20% 足够让它在页面上显得没那么笃定，又不至于把一条真信息抹掉。
WARN_CONF_MULT = 0.8


def _num(v, nd=2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _cond(cid, level, metric, value, op, threshold, unit, why):
    """一条条件记录。★**没命中的也记**，因为要能说清"离阈值还有多远"。

    `value=None`（数据没取到）时 `hit=False` 且 `unknown=True` ——
    ★"没取到"**不是**"没风险"，也**不是**"有风险"。把它算成没过关的话，
      风控官会在数据缺失时**自动放行**（最该拦的时候拦不住）；
      算成过关的话，它会在缺一个字段时把整场会议打平。两条都不对，所以单独一档。

    ★`value` 是**字符串**时（目前只有「场景」那一档）**不能过 `_num()`**：
      `_num('trend_up')` 给 None ⇒ `unknown=True` ⇒ 风控官嘴里永远是
      「场景：没取到」，而同一场的 verdict 里明明白白写着 `trend_up`。
      实测症状就是这两处**互相打架**（一个说已知、一个说没取到），
      而它们都是"真读数"，看的人只会以为其中一个坏了。
      ⇒ 字符串走直接比较，`unknown` 只看它是不是 None。
    """
    if isinstance(value, str):
        return {'id': cid, 'level': level, 'metric': metric, 'value': value,
                'threshold': threshold, 'unit': unit,
                'hit': bool(op(value, threshold)) if callable(op) else False,
                'unknown': False, 'why': why}
    v = _num(value)
    hit = False
    if v is not None:
        hit = bool(op(v, threshold)) if callable(op) else False
    return {'id': cid, 'level': level, 'metric': metric, 'value': v,
            'threshold': threshold, 'unit': unit, 'hit': hit,
            'unknown': v is None, 'why': why}


def _ge(a, b):
    return a >= b


def _le(a, b):
    return a <= b


def evaluate(ev, views, w, score_total, scenario, side, standalone=False):
    """把条件跑一遍。**纯函数，零副作用。**

    返回 `{'conditions':[...], 'metrics':{...}, 'veto':None|{...}}`。
    `side` 是加权结果给出的方向（'long'/'short'/'flat'），用于填 `downgraded_from`。

    ★`standalone=True` =「**此刻还没有别人的观点可数**」。第 1 轮风控就是这种情形：
      `__init__.py` 故意给它传 `views={}`（理由见那边的注释）。这时候
      `no_directional_view`（"一个方向票都没投"）**不能判** —— 它的输入就是各角色的
      立场，而 `views` 空意味着"还没轮到别人说"，不是"大家都表了态、都说没方向"。
      不区分这两件事的话，第 1 轮风控的发言**恒**以「命中硬条件，任何方向判断都该
      降级为不做方向（no_directional_view）」开头，与证据无关 —— 实测症状：两轮
      完全不同的数据（探针失败率 0.3333 / 0.1667、有无标的），那句话**逐字相同**。
      所以这一支把它落成 `unknown`（沿用 `_cond` 的语义：**没法判 ≠ 没问题**），
      正文里照实说"这一项我没法判，不是'没问题'"。
    """
    ev = ev or {}
    views = views or {}
    probes = ev.get('probes') or {}
    tech = (probes.get('tech') or {}).get('summary') or {}
    kl = (probes.get('get_kline') or {}).get('summary') or {}
    pf = (probes.get('analyze_portfolio') or {}).get('summary') or {}

    miss = ev.get('miss_rate')
    vol = kl.get('ann_vol_pct')
    last, hi = kl.get('last_close'), kl.get('range_high')
    dd = None
    if _num(last) is not None and _num(hi):
        dd = round((_num(last) / _num(hi) - 1) * 100, 2)     # 距区间高点的回撤（%）
    hhi, top5 = pf.get('hhi'), pf.get('top5')

    # 各方向票的立场值（只用真的表了态的）
    st = [views[r]['stance'] for r in views
          if r in ('bull', 'bear', 'fundamental', 'technical', 'sentiment', 'pm')
          and (views[r] or {}).get('stance') is not None]
    spread = round(max(st) - min(st), 4) if len(st) >= 2 else None

    conds = [
        _cond('data_gap', 'veto', '探针失败率（比例）', miss, _ge, MISS_VETO_RATE, '',
              '过半探针没取到 ⇒ 这份数据支撑不了一个方向判断；'
              '这时候给方向就是在**用缺失当空白**（缺失的字段看起来像"没有异常"）'),
        _cond('vol_extreme', 'veto', '年化波动率', vol, _ge, VOL_VETO_PCT, '%',
              '波动到这个档位，任何方向的**持有体验**都不可控（日内 -8% 是常事），'
              '方向对不对已经不重要了'),
        _cond('deep_drawdown', 'veto', '距区间高点回撤', dd, _le, DD_VETO_PCT, '%',
              '已经走过一轮深度回撤，此时"超跌反弹"和"继续下行"的**先验概率接近**，'
              '拿均线/估值分位去区分它们是不成立的'),
        _cond('concentration', 'veto', '组合 HHI', hhi, _ge, HHI_VETO, '',
              'HHI = Σ权重²（0~1）。到这个数说明前几大持仓已经能决定整个组合的涨跌，'
              '**个股分析再准也回答不了"这个组合怎么样"**'),
        _cond('top5', 'veto', '前五大占比', top5, _ge, TOP5_VETO_PCT, '%',
              '同 concentration，换一个更容易读的口径'),
        # ★`standalone` 时 value 给 None ⇒ 落 unknown（没法判 ≠ 没问题），
        #   而不是拿"还没人表态"当"一个方向票都没投"去命中这条 veto。
        _cond('no_directional_view', 'veto', '表态的方向票数',
              None if standalone else len(st), _le, 0, '个',
              '**一个方向票都没投**（全都没表态）⇒ 加权和恒为 0，'
              '那个 0 是"没测到"，不是"测到是平的"（`score.sign_of` 是同一条规矩）'),
        _cond('low_consensus', 'warn', '多空立场极差', spread, _ge, SPREAD_WARN, '',
              '有人强烈看多、有人强烈看空 ⇒ 加权和落在中间**不代表共识**，'
              '只代表双方各拉了一半；这时候那个数不该被讲成一个方向'),
        # ★场景是**已知**的（weights 判定过），不是从数据重推 —— 重推一次就有
        #   两份口径，而两份口径迟早在边界上打架。
        _cond('crisis_regime', 'warn', '场景', scenario, lambda a, b: a == b, 'crisis', '',
              '多周期 regime 落在危机档（weights.scenario 判定）—— '
              '危机里最先失效的是"历史形态会重复"这个前提'),
    ]

    hits = [c for c in conds if c['hit']]
    hard = [c for c in hits if c['level'] == 'veto']
    soft = [c for c in hits if c['level'] == 'warn']
    veto = None
    if hits:
        level = 'veto' if hard else 'warn'
        veto = {
            'level': level,
            'by': 'risk',
            'conditions': [{'id': c['id'], 'metric': c['metric'], 'value': c['value'],
                            'threshold': c['threshold'], 'unit': c['unit'],
                            'level': c['level']} for c in hits],
            'hard': [c['id'] for c in hard], 'soft': [c['id'] for c in soft],
            'downgraded_from': side if level == 'veto' else None,
            'why': ('命中硬条件 ⇒ 方向**降级为 flat**（' + '、'.join(c['id'] for c in hard) + '）'
                    if hard else '命中软条件 ⇒ 保留方向，但**强制加风险提示**并扣置信度（'
                    + '、'.join(c['id'] for c in soft) + '）'),
        }
    return {'conditions': conds, 'hits': [c['id'] for c in hits],
            'metrics': {'miss_rate': miss, 'ann_vol_pct': vol, 'dd_from_high_pct': dd,
                        'hhi': hhi, 'top5': top5, 'stance_spread': spread,
                        'scenario': scenario, 'n_directional': len(st)},
            'veto': veto}


def apply(side, score_total, conf, risk):
    """把风控结论**落到最终 verdict 上**。返回 `(side, conf, note, changed)`。

    ★硬条件 ⇒ `side` 变 `flat`，**但 `score` 保留不擦掉**：
      把 score 归零等于篡改了加权结果（"大家其实说了 +0.4"这件事就没了），
      而风控官做的是"这个数**不该被当成方向**"，不是"这个数算错了"。
      ⇒ 页面上看到的会是"score=+0.41（加权），但风控降级：flat"，这才是实情。
    ★软条件 ⇒ 方向不动，置信度打折。
    """
    risk = risk or {}
    v = risk.get('veto')
    if not v:
        return side, conf, None, False
    if v['level'] == 'veto':
        return 'flat', conf, _note(v, 'veto'), True
    try:
        conf = round(float(conf) * WARN_CONF_MULT, 4)
    except (TypeError, ValueError):
        conf = None
    return side, conf, _note(v, 'warn'), True


def _note(v, level):
    """强制的风险提示。★**原样含「不构成投资建议」**（本站对用户的承诺）。

    ★提示必须**具体到数**：列出命中的条件、它的值、它的阈值。
      "注意风险"四个字是空话 —— 用户读完不知道自己该注意什么，
      而这句话的真正用途是"让用户看到这次判断是在什么条件下被拦下的"。
    """
    rows = '；'.join('%s %s%s（阈值 %s%s）'
                     % (c['metric'], c['value'], c['unit'], c['threshold'], c['unit'])
                     for c in v['conditions'])
    head = ('风控官命中%s条件，%s。' %
            ('硬' if level == 'veto' else '软',
             '这个方向**已被降级为不做方向判断**' if level == 'veto'
             else '方向保留，但下面这些条件让它的可信度打折'))
    tail = ('★这些是**可复核的数值条件**，不是"感觉风险大"：你可以拿上面的阈值'
            '自己核对，也可以不同意它（阈值是先验档位）。本站不知道你的持仓、成本与'
            '能承受多大亏损，所以这里只给条件、不给动作。**不构成投资建议**。')
    return head + '命中：' + (rows or '（无）') + '\n' + tail


def side_of(score_total, band=0.15):
    """加权分 → 方向。★死区 0.15 是"多小的分不该被讲成一个方向"的门槛。

    和 `score.sign_of` 同一个道理：`0.02` 说成"看多"是把噪声讲成信号，
    而页面上"看多/看空"这两个字的分量比它后面那个小数大得多。
    """
    if score_total is None:
        return None
    if abs(score_total) <= band:
        return 'flat'
    return 'long' if score_total > 0 else 'short'


if __name__ == '__main__':
    import json

    from . import evidence, roles
    ev = evidence.COLLECT('茅台最近走势怎么样')
    views = {r: roles.parse(r, json.dumps({'stance': 0.4 if r == 'bull' else -0.6,
                                           'conf': 0.9, 'text': 'x', 'points': []}))
             for r in roles.ROLES}
    r = evaluate(ev, views, {}, 0.1, 'range', 'long')
    print('命中：', r['hits'])
    print('指标：', json.dumps(r['metrics'], ensure_ascii=False))
    print('veto：', json.dumps(r['veto'], ensure_ascii=False)[:600])
    print('apply：', apply('long', 0.1, 0.6, r)[:3])
