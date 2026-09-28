"""动态权重：谁的话这次更该听。

权重不是"给每个角色配个固定分值"——那等于把多智能体退化成一份加权平均的固定公式。
这里每一轮的权重由**三个因素**乘出来，而且每个因素都要在 `basis` 里给出**数值依据**
（规格 §3.4 点名了这三条）：

  ① **场景**（趋势/震荡/危机）—— 由 `agent/intraday` 的 regime + 波动率决定。
  ② **历史校准** —— 每个角色过去说对过多少次，读台账（`agent/ledger.py` 的落盘纪律）。
  ③ **本轮自报置信度** —— 且**必须做校准收缩**（见 `SHRINK` 那一大段）。

    w_i  ∝  base_i × scenario_i × vol_i × calib_i × conf_i      （只对 6 个"投方向票"的角色）
    Σ w_i = 1
    score = Σ w_i × (stance_i or 0)

★`score` 这一个式子是**可复算**的：`compute()` 会把每条 `w` 和每个 `stance` 一起发出去，
  任何人拿那个权重表乘一遍就能得到同一个数。这是"权重不是装饰"的唯一检验方式 ——
  如果算不出来，那份权重表就只是长得像权重的说明文字。

★三个角色拿 0 权重：`risk` / `kg_tutor` / `psych`。**这不是把它们踢出会议**，
  而是它们的票**本来就不该进加权和**：
    - `risk` 判的是风险条件，不是方向（规格 §3.4「独立于多空」）；
      它影响结论靠的是**否决/降级**（`risk.py`），不是往方向票里塞一票。
    - `kg_tutor` 答的是"这个词是什么意思"，`psych` 讲的是常见偏差；
      它们没有立场，硬给权重只会制造一个**看起来在投票**的假象。
  它们的 0 会**照实出现在权重表里**（不是被删掉），并且 `basis.zero_weight_roles`
  说明原因 —— "这一票是 0"和"这个角色不在表里"是两件事。
"""

import os
import threading
import time

import ledger
import score

from . import evidence     # 只为了问一句"现在是不是桩模式"（见 scenario_of 里那段）
from . import roles        # 角色表只有一份（roles.ROLES / roles.DIRECTIONAL）

# ── 先验权重 ──────────────────────────────────────────────────────────
#
# ★这是**先验**，不是实测。写下来是因为总得有个起点，而它的依据是**职责分工**：
#   多头/空头各 0.17（它们是"专职找证据"的两个，且第 2 轮要交锋）；
#   基本面与技术各 0.15（两个独立的分析口径）；舆情 0.11（只采不析，证据最弱）；
#   投资经理 0.25（它既投票又收口，且握着最终口径）。
#   ★这和"回测没有超额能力"那件事不矛盾：这里量的是**同一份数据下不同口径的相对
#     可信度**，不是"谁能预测未来"。
#   ★一旦本地台账攒够样本（见 MIN_SAMPLES），`calib` 这一项就会**盖过**它 ——
#     那时先验只作为收缩的中心。所以它不是"永久设定"，是"起点"。
BASE = {
    'bull': 0.17, 'bear': 0.17, 'fundamental': 0.15, 'technical': 0.15,
    'sentiment': 0.11, 'pm': 0.25,
}

# ── 因素①：场景（regime + 波动率）─────────────────────────────────────
#
# ★两条路并存，走哪条**照实写在 `basis.scenario.source` 里**，调用方和判据不用猜：
#     - 真 `intraday`（`source: 'intraday'`）：`import intraday` 成功且
#       `analyze()` 返回 `ok=True`。A2 的 `agent/intraday/` 是 2026-09-22 17:42–17:54
#       落地的 —— **本文件最初写下的那句"那个包是空的"已经过期**，改成随包出现自动接上。
#     - 占位（`source: 'placeholder:ma_alignment+vol'`）：只用 `evidence` 里真取到的
#       数字（均线排列 + 布林带宽 + 年化波动）现算一个场景，阈值是先验档位。
#       **绝不写成 `'intraday'`** —— 假报一个没接上的源，比明写"占位"坏得多。
#
# ★★离线/判据模式**不许出网**（这是踩过的坑，不是预防性设计）：A2 的包一落地，
#   `intraday.analyze()` 就会去 `datasource.minute()` 取**真**分钟线。我这边
#   `run()` 是同步的，那一层卡住 = 整场会议永远到不了 `done`（自测实测：跑过 120s
#   还没回来，只能 kill）。所以：
#     ① 证据层是桩（`ev['src'] == 'stub'`，即 `LICAI_AGENTS_STUB` 那套）时
#        **默认根本不调 intraday** —— 桩模式的全部意义就是离线、可复现；
#        要离线把 intraday 那条链也走通，由调用方显式设 `LICAI_INTRADAY_SYNTH=1`
#        （intraday 自己的合成帧开关，见它的模块头），那时才调，且仍然是离线。
#     ② 真调的时候一律**带超时**（`SCENARIO_TIMEOUT`，见 `_with_timeout`）。
#     ③ 同一份 `ev` 只判一次（`_scenario_cache`）：`run()` 里 `compute()` 要调两趟
#        （pm 之前一趟、最终一趟），不缓存就是两次网络往返，白等。
INTRA_SYNTH_ENV = 'LICAI_INTRADAY_SYNTH'
SCENARIO_TIMEOUT = 8.0          # 秒。取值理由：一次正常的 5/15/30 三周期取数在
                                # 本机实测是秒级；8s 足够覆盖慢网，又不至于让用户
                                # 觉得"页面卡死了"。超时不是"没数据"，是"没等到"。
#
# 占位口径（只用 `evidence` 里真的取到的数）：
#   年化波动 ≥ 60%            → crisis
#   MA5 > MA20 > MA60 且 差值 > 0.5% → trend_up（镜像 → trend_down）
#   BOLL 带宽 < 4% 且 没有斜率 → squeeze
#   其余                       → range
#   ★阈值 0.5% / 4% / 60% 是**先验档位**（不是测出来的）：0.5% 是"均线有可见间隔"，
#     4% 是布林带常见的"收口"区，60% 是 A 股个股年化的极端档。
#     它们都是**可复核的数值条件**（风控官那一套也是这个要求）。
TREND_GAP_PCT = 0.5
SQUEEZE_BAND = 0.04
CRISIS_VOL_PCT = 60.0

# 场景 → 各角色乘数。★方向是"这个场景下**哪种镜头更可靠**"，不是"哪个方向更可能"。
#   趋势里技术面更灵（趋势跟随），震荡里基本面/舆情更灵（均值回归、事件驱动），
#   危机里**所有方向票都不可靠**（一起调低，让风控的否决说话）。
SCENARIO_MULT = {
    'trend_up':   {'bull': 1.05, 'bear': 1.05, 'fundamental': 0.85,
                   'technical': 1.30, 'sentiment': 0.90, 'pm': 1.00},
    'trend_down': {'bull': 1.05, 'bear': 1.05, 'fundamental': 0.85,
                   'technical': 1.30, 'sentiment': 0.90, 'pm': 1.00},
    'range':      {'bull': 1.00, 'bear': 1.00, 'fundamental': 1.20,
                   'technical': 0.80, 'sentiment': 1.15, 'pm': 1.00},
    'squeeze':    {'bull': 0.95, 'bear': 0.95, 'fundamental': 0.95,
                   'technical': 1.15, 'sentiment': 0.90, 'pm': 1.00},
    'crisis':     {'bull': 0.70, 'bear': 0.70, 'fundamental': 0.85,
                   'technical': 0.70, 'sentiment': 0.85, 'pm': 1.00},
}

# 波动率档 → 乘数。★高波动下技术面最先失效（假突破、假信号密度上升），
#   基本面反而**更**有意义（价格乱跳、生意没那么乱跳）。
VOL_BUCKETS = ((15.0, 'calm', {'technical': 1.10, 'sentiment': 1.00, 'fundamental': 0.95}),
               (30.0, 'normal', {}),
               (45.0, 'choppy', {'technical': 0.85, 'sentiment': 0.95, 'fundamental': 1.05}),
               (999.0, 'wild', {'technical': 0.65, 'sentiment': 0.85, 'fundamental': 1.10}))


def vol_bucket(ann_vol_pct):
    """年化波动率 → 档名 + 该档的乘数。查不到波动率 ⇒ 不动任何权重（全 1.0）。"""
    if ann_vol_pct is None:
        return 'unknown', {}
    for hi, name, mult in VOL_BUCKETS:
        if ann_vol_pct < hi:
            return name, mult
    return 'wild', VOL_BUCKETS[-1][2]


def _with_timeout(fn, timeout=SCENARIO_TIMEOUT):
    """→ `(ok, 值 或 异常)`。**超时就放弃，泄漏一个卡住的线程。**

    ★为什么宁肯泄漏线程：`intraday.analyze()` 里面是 `datasource.minute()`，那一层的
      超时归 A2 管；我这边是**同步**调用，一旦卡住卡的是整场会议 —— SSE 心跳还在跳，
      但会议永远到不了 `done`（实测：自测跑过 120s 没回来，只能 kill）。
      泄漏一个 daemon 线程比让用户对着一场永不结束的会议好 —— 与
      `evidence._with_timeout` 同一条纪律。
    ★异常一律**抓下来当返回值**，不往外抛：权重只是后处理的数，它坏了不该让
      9 个角色已经发出去的发言全部作废。
    """
    box = {}

    def _run():
        try:
            box['v'] = fn()
        except BaseException as e:                  # noqa: BLE001 —— 见上，不往外抛
            box['e'] = e

    t = threading.Thread(target=_run, name='agents-scenario', daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, TimeoutError('超过 %.1fs 没返回（已放弃这个线程；它可能还占着一条连接）'
                                   % timeout)
    if 'e' in box:
        return False, box['e']
    return True, box.get('v')


def _scenario_via_intraday(s0):
    """真调 `intraday.analyze()`（在另一个线程里、带超时）→ `(pick 或 None, detail)`。"""
    def _call():
        import intraday                                     # noqa: PLC0415
        # ★港美股/加密没有分钟线源 ⇒ 走日线口径（分流逻辑的唯一真源在
        #   `daily_short.analyze_auto`）。拿不到那个模块就退回原路 ——
        #   行为与本改动之前逐字一致，不让降级本身变成新失败点。
        try:
            from daily_short import analyze_auto            # noqa: PLC0415
        except Exception:                                   # noqa: BLE001
            analyze_auto = intraday.analyze
        return analyze_auto(s0['market'], s0['code'],
                            periods=(5, 15, 30), name=s0.get('name'))

    ok, r = _with_timeout(_call)
    if not ok:
        return None, {'error': '%s: %s' % (type(r).__name__, str(r)[:120])}
    if not isinstance(r, dict):
        return None, {'error': 'analyze() 返回了 %s，不是 dict' % type(r).__name__}
    reg = r.get('regime') or {}
    if not (r.get('ok') and reg):
        return None, {'error': 'ok=%r，errors=%r' % (r.get('ok'), (r.get('errors') or [])[:3])}
    # 多周期取"短周期优先"的那一个：短周期先反应（30 分钟还在均线里的时候，5 分钟已经破位了）
    # ★`'1440'` 必须在表里：日线降级的市场 `regime` 只有这一个键，漏掉它
    #   就永远挑不出东西 ⇒ `pick=None` ⇒ 下面 `or 'range'` 会**编一个 'range' 场景**，
    #   而真实 regime（实测 HK/US 常见 'squeeze'）没人读。这不是"没有读数"，
    #   是"有读数但没人看，然后编了一个"。缺周期时仍走 `or 'range'` 是对的
    #   （那才是真的没读数），所以修的是表，不是那个兜底。
    pick = None
    for p in ('5', '15', '30', '1440'):
        if reg.get(p):
            pick = reg[p]
            break
    _dbar = r.get('bar_scale') == 'daily'
    return pick or 'range', {
        'source': 'intraday', 'regime': reg, 'bars': r.get('bars'),
        'chosen': pick, 'ts': r.get('ts'), 'data_source': r.get('data_source'),
        'bar_scale': r.get('bar_scale'),
        'note': ('regime 来自 agent/daily_short.analyze_auto() ⇒ **日线口径**'
                 '（该市场在本机没有分钟线源，period=%s）' % ','.join(sorted(reg))
                 if _dbar else
                 'regime 来自 agent/intraday.analyze()（真接上了）'),
    }


def scenario_of(ev, symbol=None):
    """场景判定。返回 `(scenario, detail)`；detail 里带**判定用到的每一个数**。

    ★先试真 `intraday`（带超时；桩模式默认不调，见上面那次踩坑的记录），
      接不上走占位。两条路的 detail 都写明自己是哪条，调用方（和判据）**不用猜**。
    ★同一份 `ev` 只判一次：结果挂在 `ev['_scenario_cache']` 上。**不是模块级缓存** ——
      挂 `ev` 上就没有"过期"这回事，`ev` 是这一轮的证据对象，这一轮结束它就该走。
      （`run()` 里 `compute()` 要调两趟：pm 之前一趟、最终一趟；不缓存就是两次网络往返。）
    """
    syms = ev.get('symbols') or []
    s0 = (symbol or (syms[0] if syms else None)) or {}
    tech = ((ev.get('probes') or {}).get('tech') or {}).get('summary') or {}
    kl = ((ev.get('probes') or {}).get('get_kline') or {}).get('summary') or {}

    # ★"是不是桩模式"**不能只看 `ev['src']`**：`evidence.collect()` 里 `src` 写死成
    #   `'live'` —— 它标的是"**哪个 collect 函数跑的**"，不是"有没有出网"
    #   （桩帧是 `evidence._stub_collect` 的产物，那个才写 `src='stub'`）。
    #   一开始我只认 `src == 'stub'`，结果在 `LICAI_AGENTS_STUB=1` 下判成 live、
    #   照样去调 intraday —— 实测当场现形（尺子量的不是我以为的那个量）。
    #   ⇒ 真开关是 `evidence.stub_active()`（它读 `LICAI_AGENTS_STUB`），
    #     两个都认：`src=='stub'` 覆盖"调用方直接塞了一份桩帧"的情况。
    stub = (ev.get('src') == 'stub') or evidence.stub_active()
    synth = bool(os.environ.get(INTRA_SYNTH_ENV, '').strip())
    ckey = (s0.get('code'), s0.get('market'), stub, synth)
    cache = ev.setdefault('_scenario_cache', {})
    if cache.get('key') == ckey:
        return cache['scenario'], cache['detail']

    # ① 真 intraday
    if not (s0.get('code') and s0.get('market')):
        why = '本轮没认出标的（symbols 里没有 market+code），没法调 intraday'
    elif stub and not synth:
        why = ('证据层在桩模式（ev.src=stub）⇒ **故意不调** intraday：它会走真网络取分钟线，'
               '而桩模式存在的意义就是离线、可复现。要离线把这条链也走通，'
               '请显式设 %s=1（intraday 自己的合成帧开关）' % INTRA_SYNTH_ENV)
    else:
        pick, det = _scenario_via_intraday(s0)
        if pick:
            if stub:
                det = dict(det)
                det['note'] += '★离线：intraday 取的是 %s=1 的**合成帧**，看 data_source' \
                               % INTRA_SYNTH_ENV
            cache['key'], cache['scenario'], cache['detail'] = ckey, pick, det
            return pick, det
        why = 'intraday 调不通（%s）' % det.get('error')

    # ② 占位口径
    vol = kl.get('ann_vol_pct')
    ma5, ma20, ma60 = tech.get('ma5'), tech.get('ma20'), tech.get('ma60')
    band = tech.get('boll_band')
    gap = None
    if None not in (ma5, ma20, ma60) and ma20:
        gap = round((ma5 - ma60) / ma60 * 100, 4)          # MA5 相对 MA60 的偏离（%）
    if vol is not None and vol >= CRISIS_VOL_PCT:
        sc = 'crisis'
    elif gap is not None and gap >= TREND_GAP_PCT and ma5 > ma20 > ma60:
        sc = 'trend_up'
    elif gap is not None and gap <= -TREND_GAP_PCT and ma5 < ma20 < ma60:
        sc = 'trend_down'
    elif band is not None and band < SQUEEZE_BAND and gap is not None \
            and abs(gap) < TREND_GAP_PCT:
        sc = 'squeeze'
    else:
        sc = 'range'
    detail = {
        'source': 'placeholder:ma_alignment+vol',
        'why': why, 'ann_vol_pct': vol, 'boll_band': band,
        'ma5': ma5, 'ma20': ma20, 'ma60': ma60, 'ma_gap_pct': gap,
        'thresholds': {'trend_gap_pct': TREND_GAP_PCT, 'squeeze_band': SQUEEZE_BAND,
                       'crisis_vol_pct': CRISIS_VOL_PCT},
        'note': '这一轮的 regime **不是** intraday 给的（原因见 why），而是用均线排列 + '
                '布林带宽 + 年化波动**现算**的一个场景，阈值是先验档位。'
                '★这不是"接上了 intraday"，是占位；`source` 就是给判据认这个用的。',
    }
    cache['key'], cache['scenario'], cache['detail'] = ckey, sc, detail
    return sc, detail


# ── 因素②：历史校准（读台账，无数据退回先验）─────────────────────────
#
# ★复用 `agent/ledger.py` 的**落盘纪律**（追加、fsync、坏行如实上报），
#   但**用我自己的文件**（`data/agent_roles.jsonl`），不碰 `data/predictions.jsonl`：
#   那是 Kronos 预测的台账，往里塞"角色的观点"会让那份战绩的起点数、分组、回填
#   逻辑全部失去意义（`ledger.state()` 会把我的记录当成一条预测去找回填）。
#   ⇒ 同一个**模块**（纪律一致），不同的**文件**（语义不混）。
CALIB_ENV = 'LICAI_AGENT_LEDGER'
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'data', 'agent_roles.jsonl')

# ★"有没有数据"的门槛。**没有它，第一个样本就能把权重带飞**：一个角色蒙对一次
#   就是 100% 命中率，下一轮它独裁。Wilson 区间是同一件事的另一种说法
#   （`score.wilson` 的 docstring：98 个起点和 500 个起点说的不是同一件事）。
MIN_SAMPLES = 20

# Beta(2,2) 先验 = 把"命中率 50%"当成已经看过 4 次（2 中、2 不中）。
# ★用先验而不是直接用 k/n：k/n 在小样本下方差极大，而且 0/0 时没有定义。
# ★`PRIOR_N` 是**总的**伪观测数（a+b），不是"命中侧"那个数 —— 写成 2 的话
#   空台账会算出 (0+2)/(0+2)=1.0，即"默认这个角色 100% 准"，
#   而正确的中性先验必须先落到 0.5：`_shrink_rate(0,0) == 0.5`（`_probe.py` 有断言）。
PRIOR_K, PRIOR_N = 2.0, 4.0

# 校准项的**摆动幅度**。命中率 0.5 → 乘数 1.0；命中率 1.0 → 1 + GAIN；0 → 1 - GAIN。
# ★为什么给 0.6 而不是 1.0：命中率这一项是**估计**，估计错了不该把权重整个翻掉。
#   0.6 意味着"历史全对"最多让权重涨 60%、"全错"最多跌 60% —— 足够改变排序，
#   不足以让一个角色独裁。
CALIB_GAIN = 0.6

# stance 和"实际涨跌幅"不是同一个量纲（一个是 [-1,1] 的主观强度，一个是 %），
# 所以**两个死区**必须分开：判断"它表没表态"用 STANCE_BAND，
# 判断"行情到底涨没涨"用 `score.DEADBAND`（全站唯一的那个 0.3%）。
# ★拿 0.3 去量 stance=0.2 会把"他明确看多"读成"他没表态"，命中率全错。
STANCE_BAND = 0.15


def path():
    """校准台账的文件路径。`LICAI_AGENT_LEDGER` 换掉它（判据靠它不碰真数据）。"""
    return os.environ.get(CALIB_ENV) or DEFAULT_PATH


def record_view(rid, role, stance, conf, ev=None, round_=1):
    """记一条"这个角色这一轮说了什么"。**只追加**，返回 `(ok, 原因)`。

    ★为什么**不在 `run()` 里自动调**：`run()` 必须**零写入副作用**。
      判据会离线跑几十上百次 `run()`，每次都往台账里灌一条，会让"历史校准"
      变成"被测试数据污染的战绩"（`ledger.LEDGER_ENV` 那段讲的正是这个病）。
      ⇒ 由调用方（`app.py` 的路由）在合适的时候显式调，见交付说明的集成点。
    ★只记**原始输入**（stance/conf），分数在读的时候现算 ——
      和 `ledger.py` 的「打分的活只归 `score.py`」是同一条纪律。
    """
    return ledger.append({'kind': 'view', 'id': rid, 'role': role,
                          'stance': stance, 'conf': conf,
                          'round': round_, 'scenario': (ev or {}).get('scenario'),
                          'file': 'agents'}, p=path())


def record_outcome(rid, pct, note=None):
    """回填结果：这条观点对应的标的，之后实际涨跌了多少（%）。只追加。"""
    return ledger.append({'kind': 'outcome', 'id': rid, 'pct': pct,
                          'note': note, 'file': 'agents'}, p=path())


def read_log():
    """台账 → (按 id 折叠后的观测, 坏行数)。坏行**如实上报**（ledger 的纪律）。"""
    recs, bad = ledger.read_all(p=path())
    views, out = {}, {}
    for r in recs:
        if r.get('kind') == 'view' and r.get('id'):
            views[r['id']] = r
        elif r.get('kind') == 'outcome' and r.get('id'):
            out[r['id']] = r
    return views, out, len(bad)


def _shrink_rate(hits, n):
    """命中率往 0.5 收缩。`n=0` ⇒ 0.5（**中性**，不是 1.0、也不是 0）。"""
    return (hits + PRIOR_K) / (n + PRIOR_N)


def calibration():
    """每个角色的命中统计。返回 `(stats, meta)`；stats[role] 一定存在（可能 n=0）。

    `stats[role] = {'n','hits','rate','shrunk','factor','source'}`：
      - `rate`：k/n（**n=0 时是 None，不是 0**）；
      - `shrunk`：(k+先验)/(n+先验) —— 小样本被拉回 0.5；
      - `factor`：乘数。★`source='prior'` 时**恒为 1.0**（不动权重）——
        n 不够就**不调**，而不是"按一个不可信的估计调一点点"。
      - `source`：`'ledger'`（样本够，真的在用）/ `'prior'`（退回先验）。
    """
    views, out, bad = read_log()
    st = {r: {'n': 0, 'hits': 0, 'rate': None, 'shrunk': None,
              'factor': 1.0, 'source': 'prior'} for r in BASE}
    for rid, v in views.items():
        o = out.get(rid)
        role = v.get('role')
        if role not in st or not o or o.get('pct') is None or v.get('stance') is None:
            continue
        # ★两把尺子：它表没表态（STANCE_BAND）／行情到底动没动（score.DEADBAND）。
        said = score.sign_of(v['stance'], STANCE_BAND)
        happened = score.sign_of(o['pct'], score.DEADBAND)
        if said is None or happened is None:
            continue
        # ★两条都要：**它说平、行情也平**算对（"我判断没方向"是对的）；
        #   **一边平一边动**算错。这正是把两把尺子分开的意义 ——
        #   合成一把尺子时，"说平"会被记成 50% 命中，那是捏造出来的精度。
        ok = (said == happened)
        st[role]['n'] += 1
        st[role]['hits'] += int(ok)
    for role, s in st.items():
        if s['n']:
            s['rate'] = round(s['hits'] / s['n'], 4)
        s['shrunk'] = round(_shrink_rate(s['hits'], s['n']), 4)
        if s['n'] >= MIN_SAMPLES:
            s['factor'] = round(1.0 + CALIB_GAIN * (s['shrunk'] - 0.5), 4)
            s['source'] = 'ledger'
        else:
            # ★样本不够 ⇒**不动权重**，并且**照实说这是先验**。
            #   把先验当实测报出去（比如"命中率 67%"）就是把"我看过 4 次"说成
            #   "我量过 N 次" —— 这个站最忌讳的两件事之一。
            s['factor'] = 1.0
            s['source'] = 'prior'
    meta = {'path': path(), 'min_samples': MIN_SAMPLES, 'prior': [PRIOR_K, PRIOR_N],
            'total_views': len(views), 'scored': sum(s['n'] for s in st.values()),
            'bad_lines': bad,
            'note': ('样本不足 %d 的角色**不做校准**、乘数恒为 1.0，'
                     '并标明 source=prior（先验，不是实测）' % MIN_SAMPLES)
                    + ('；坏行 %d 条（台账有半行/坏行，读不动的那部分没有被静默跳过）'
                       % bad if bad else '')}
    return st, meta


# ── 因素③：自报置信度 + 校准收缩 ──────────────────────────────────────
#
# ★★为什么必须收缩，而不是直接拿 `conf` 当权重：
#    **自报置信度系统性地偏高**，这是行为金融里最稳的发现之一（"过度自信"族，
#    Alpert & Raiffa 1969 的校准实验、Lichtenstein/Fischhoff/Phillips 1977 的
#    综述、以及之后几十年的复现里，人对自己判断的置信度普遍高于真实命中率）。
#    模型同理：它说 0.9 的时候，实际命中率远不到 90%。
#    如果直接把 0.9 当权重，那**最自信的角色就是最有分量的角色**，
#    而"最自信"和"最准"之间没有这条关系 —— 这等于把会议交给嗓门最大的人。
#    ⇒ 往 0.5 收缩：`conf_eff = 0.5 + (conf - 0.5) * SHRINK`。
#      conf=1.0 → 0.75；conf=0.5 → 0.5（不动）；conf=0 → 0.25。
#    ★0.5 是"完全没有信息"的位置，所以收缩就是"把它拉回不知道"，
#      而不是拉向某一个方向 —— 后者会偷偷引入一个"默认看空/看多"的偏置。
#    ★0.5 这个系数是**先验选择**（不是测出来的），和 `CALIB_GAIN` 一样只是幅度，
#      不是结论。台账攒够之后，`calib` 那一项会去校正它。
SHRINK = 0.5

# 收缩后的置信度 → 乘数。0.5 → 1.0；0.75 → 1.4（上限）；0.25 → 0.6（下限）。
# ★上下限都要有：不设上限，一个 conf=1.0 的乱说者能靠自报把权重顶到 1.4 倍；
#   不设下限，一个谦虚的准角色会被压到听不见。
CONF_LO, CONF_SPAN = 0.6, 0.8


def shrink(conf):
    """自报置信度 → 收缩后的置信度。`None` ⇒ 0.5（"没报" = "不知道"）。"""
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return 0.5
    if c != c:
        return 0.5
    c = max(0.0, min(1.0, c))
    return round(0.5 + (c - 0.5) * SHRINK, 4)


def conf_factor(conf):
    return round(CONF_LO + CONF_SPAN * shrink(conf), 4)


# ── 合成 ──────────────────────────────────────────────────────────────

def compute(ev, views, scenario=None):
    """这一轮的权重表 + 复算得出的一致性分数。**纯读，零副作用。**

    `views`：{role: roles.parse() 的结果}（只需要 `stance` / `conf`）。
    返回：
      `w`           9 个角色**全都在**（没进加权和的三个是 0.0，不是缺键）
      `stance`      各角色这一轮投的票（None 原样保留 —— "没表态"不算 0 分票）
      `score`       Σ w_i × (stance_i or 0)，**就是最终拿出去的那个数**
      `equation`    一句话的复算式（页面上直接显示，人可以照着算）
      `basis`       三个因素各自的数值依据（含"哪一项是占位/先验"）
    """
    ev = ev or {}
    views = views or {}
    if scenario is None:
        scenario, sc_detail = scenario_of(ev)
    else:
        sc_detail = {'source': 'given', 'value': scenario}

    kl = ((ev.get('probes') or {}).get('get_kline') or {}).get('summary') or {}
    vol = kl.get('ann_vol_pct')
    vb, vmult = vol_bucket(vol)
    smult = SCENARIO_MULT.get(scenario, SCENARIO_MULT['range'])

    cal, cal_meta = calibration()

    w, steps, stance = {}, [], {}
    for role in roles.ROLES:
        v = views.get(role) or {}
        stance[role] = v.get('stance')
        if not roles.DIRECTIONAL[role]:
            # ★不投方向票的角色：权重 0，且**不参与归一化**（见下面 denominator）。
            w[role] = 0.0
            steps.append({'role': role, 'w': 0.0, 'why': '不投方向票（职责：否决/解释/提示）'})
            continue
        b = BASE.get(role, 0.0)
        s = smult.get(role, 1.0)
        vv = vmult.get(role, 1.0)
        cc = cal.get(role, {}).get('factor', 1.0)
        cf = conf_factor(v.get('conf'))
        raw = b * s * vv * cc * cf
        w[role] = raw
        steps.append({'role': role, 'w': raw, 'base': b, 'scenario_mult': s,
                      'vol_mult': vv, 'calib_mult': cc, 'conf_mult': cf,
                      'stance': stance[role], 'conf': v.get('conf'),
                      'conf_shrunk': shrink(v.get('conf'))})

    tot = sum(w.values())
    if tot <= 0:
        # ★全部乘数塌到 0 时**不许**交出一张全 0 的权重表（那样 score 恒为 0，
        #   看起来像"大家一致看平"）。退回先验，并**照实说明回退了**。
        for role in roles.ROLES:
            w[role] = BASE.get(role, 0.0)
        tot = sum(w.values())
        sc_detail = dict(sc_detail, weight_fallback='全部乘数塌到 0，退回先验权重')
    for role in roles.ROLES:
        if w[role]:
            w[role] = round(w[role] / tot, 6)

    # 归一化会引入浮点残差，最后一格补差 —— 权重表必须**正好**加到 1
    # （判据和我们自己都会去加一遍；1.0000000000000002 这种数会让人怀疑整张表）。
    keys = [r for r in roles.ROLES if w[r]]
    if keys:
        s_now = sum(w[r] for r in keys)
        w[keys[-1]] = round(w[keys[-1]] + (1.0 - s_now), 6)

    parts = ['%s(%s)×%s' % (r, _num(w[r]), _num(stance[r] if stance[r] is not None else 0.0))
             for r in roles.ROLES if w[r]]
    score = round(sum(w[r] * (stance[r] or 0.0) for r in roles.ROLES), 6)

    basis = {
        'scenario': {'value': scenario, 'mult': smult, **sc_detail},
        'volatility': {'ann_vol_pct': vol, 'bucket': vb, 'mult': vmult,
                       'buckets': [{'lt': hi, 'name': n} for hi, n, _ in VOL_BUCKETS]},
        'calibration': {'roles': cal, **cal_meta},
        'confidence': {'shrink': SHRINK, 'lo': CONF_LO, 'span': CONF_SPAN,
                       'why': '自报置信度**系统性偏高**（行为金融里最稳的发现之一，'
                              'Alpert & Raiffa 1969 / Lichtenstein 等 1977 起几十年的复现）。'
                              '直接拿它当权重 = 把会议交给嗓门最大的人。'
                              '所以先往 0.5 收缩（0.5 = "完全不知道"，不引入方向偏置）再当乘数。',
                       'roles': {r: {'reported': (views.get(r) or {}).get('conf'),
                                     'shrunk': shrink((views.get(r) or {}).get('conf')),
                                     'mult': conf_factor((views.get(r) or {}).get('conf'))}
                                 for r in roles.ROLES if roles.DIRECTIONAL[r]}},
        'base': dict(BASE),
        'zero_weight_roles': {r: ('不判方向（规格 §3.4：独立于多空）；它的影响力走**否决**，'
                                  '不是加权和' if r == 'risk' else
                                  '它的活是解释/提示，没有方向立场 —— 给它权重只会制造一个'
                                  '"看起来在投票"的假象')
                              for r in roles.ROLES if not roles.DIRECTIONAL[r]},
        'steps': steps,
        'sources': {'intraday': sc_detail.get('source'),
                    'ledger': cal_meta['path'],
                    'note': '★`scenario.source` 不是 "intraday" 时，那一项就是**占位**，'
                            '不是接上了 agent/intraday；`calibration.roles[*].source` '
                            '是 "prior" 时，那个角色的校准是**先验**、没在动权重。'},
    }
    return {'w': w, 'stance': stance, 'score': score, 'scenario': scenario,
            'parts': parts, 'steps': steps, 'basis': basis,
            'equation': 'score = Σ w_i × stance_i = ' + ' + '.join(parts) + ' = ' + _num(score)}


def _num(v):
    try:
        return ('%.6f' % float(v)).rstrip('0').rstrip('.') or '0'
    except (TypeError, ValueError):
        return str(v)


if __name__ == '__main__':
    import json

    from . import evidence
    ev = evidence.COLLECT('茅台最近走势怎么样')
    views = {r: {'stance': (0.4 if r == 'bull' else -0.5 if r == 'bear' else 0.1),
                 'conf': 0.8} for r in roles.ROLES}
    out = compute(ev, views)
    print(json.dumps({'scenario': out['scenario'], 'w': out['w'],
                      'score': out['score'], 'equation': out['equation']},
                     ensure_ascii=False, indent=2))
    print('\nscenario basis:', json.dumps(out['basis']['scenario'], ensure_ascii=False)[:400])
    print('\ncalibration:', json.dumps(out['basis']['calibration']['roles'], ensure_ascii=False)[:400])
