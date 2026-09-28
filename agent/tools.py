"""工具层：智能体手上就只有这几把工具，每个都返回**两条通道**。

★这个模块唯一重要的事，是「给大模型看的」和「给前端画的」彻底分开：

    摘要通道 (summary) → 进大模型上下文。只能放标量和小列表。
    数据通道 (data)    → 直接发给浏览器画图。可以是几百根 K 线。
                          **永远不进上下文。**

为什么不合成一条：一次 `predict_kline` 的原始输出是 30×6 个数，加上历史尾段就是
150×6 —— 序列化成 JSON 大概 15KB，约 6000 token，而它承载的信息量（"涨了 3%"）
用 40 个 token 就说完了。更要命的是大模型看一列裸数字**读不出形状**，多花的 token
纯属噪音。反过来前端没有原始数组就画不出图。所以这两件事必须是两条路。

`assert_compact()` 是这个纪律的守卫：摘要超过预算就抛 —— 那不是"有点大"，
那说明有人把数组漏进摘要通道了。
"""

import importlib
import importlib.util
import json
import time
import uuid
import contextvars

import pandas as pd

import feed
import goal
import graph_kb
import ledger
import news
import portfolio
import score
from runner import DEFAULTS, ENGINE, EngineError

# 摘要通道的预算。一条摘要 ≈ 一个工具结果，几千 token 的上下文里塞不下更多。
# 这不是"建议值"：超了就报错，因为超预算只有一种成因 —— 数据漏进了摘要。
SUMMARY_BUDGET = 4000

# ── 采样几条 ──────────────────────────────────────────────────────────
# ★9 这个数不是拍的：N 条独立采样的**极差**是一个精确的预测区间
#   （`score.level_of`：P(新样本落在极差内) = (N-1)/(N+1)），
#   而 **N=9 恰好对应 80%**。选 80% 而不是 95%，是因为 95% 要 N=39 ——
#   那是 39 次采样、约 8 秒（按 kronos_perf.json 的 210ms/条外推），
#   而且区间宽到没有信息量。80% 是"说得出结论"和"等得起"的交点。
#
# ★它与 `sample_count` 的**schema 描述**必须一致（tools.SCHEMAS 里），
#   验证脚本有一条判据钉住这个数在代码、schema、文档三处相同 ——
#   改一处而别处不动的话，大模型会按旧的默认值调用。
DEFAULT_SAMPLES = 9
# 上限：再多就该换 pred_len 或换机器了，不是加采样能解决的。
MAX_SAMPLES = 20


class ToolError(RuntimeError):
    def __init__(self, code, msg, hint=''):
        super().__init__(msg)
        self.code, self.hint = code, hint


# ── 守卫 ──────────────────────────────────────────────────────────────

def assert_compact(summary, label, budget=None):
    """摘要通道的体量守卫。★故意做成抛异常而不是打印警告。

    ★`budget=None` 而不是 `budget=SUMMARY_BUDGET`：默认值在**定义时**求值，
      写成后者就是把这个阈值**冻成一份副本**，而下面几处瘦身循环读的是**活的**
      `SUMMARY_BUDGET`（`_shrink_linkage`/`get_news`/`analyze_intraday`/`debate`）。
      两份一旦分叉，瘦身会按 A 压、守卫按 B 判 —— 压到 A 达标、B 当场抛，
      或者反过来压过头。本仓库对"阈值住两处"有专门的判据，这里就让它只有一处。
    """
    if budget is None:
        budget = SUMMARY_BUDGET
    n = len(json.dumps(summary, ensure_ascii=False).encode('utf-8'))
    if n > budget:
        raise ToolError('SUMMARY_TOO_BIG',
                        '%s 的摘要 %d 字节，超过预算 %d —— 多半是把 K 线数组漏进摘要通道了'
                        % (label, n, budget))
    return summary


def _bars(df, cols=('open', 'high', 'low', 'close', 'volume')):
    """DataFrame → 列式数组。列式比 [{t,o,h,l,c}×N] 省掉 N 次键名，小一半。

    ★时间可能出现在**两个地方**，两种都得认：
      - 历史段：`feed.fetch()` 给的是普通 DataFrame，时间在 `dt` **列**里；
      - 预测段：上游 `KronosPredictor.predict` 写的是
        `pd.DataFrame(preds, columns=..., index=y_timestamp)` —— 时间在 **index** 上，
        压根没有 `dt` 列。
    这里原来只认 `dt` 列，于是 `predict_kline` 一跑到预测段就 `KeyError: 'dt'`。
    ★ 为什么没被早点发现：`check_dual_channel` 走的是 `get_kline`（历史段，有 `dt` 列），
      预测段那条路**当时没有任何判据**覆盖 —— 是"取数先失败"那条 skip 把它顶出来的。
    """
    ts = df['dt'] if 'dt' in df.columns else df.index
    out = {'t': [pd.Timestamp(d).strftime('%Y-%m-%d') for d in ts]}
    for c in cols:
        if c in df.columns:
            out[c[0]] = [round(float(x), 4) for x in df[c]]
    return out


def _pct(a, b):
    return None if not b else round((a / b - 1) * 100, 2)


def _vol(close):
    """年化波动率（按日收益算）。只用来把预测幅度放到"这只票平时晃多少"的尺子上量。"""
    import math
    r = close.pct_change().dropna()
    if len(r) < 5:
        return None
    return round(float(r.std() * math.sqrt(252) * 100), 2)


# ── 行情缓存 ──────────────────────────────────────────────────────────
# 一次取数实测 1~3 秒（走新浪），而用户连着问两句同一只票是常态。
_KLINE = {}
_KLINE_TTL = 600


def _get_kline(market, code, days):
    k = (market, code, days)
    hit = _KLINE.get(k)
    now = time.time()
    # ★桩开着的时候**绕开缓存**：桩是测试夹具，而夹具的意义就在于"改了就立刻生效"。
    #   走缓存的话，判据换了桩文件之后还得等 _KLINE_TTL 才看得到新帧 ——
    #   那条"换掉返回、页面跟着变"的对照会静默地拿旧帧去比，绿得毫无意义。
    if feed.stub_active():
        hit = None
    if hit and now - hit[0] < _KLINE_TTL:
        df = hit[1].copy()
        df.attrs['source'] = hit[2]
        df.attrs['cached'] = True
        return df
    df = feed.fetch(market, code, days=days)
    _KLINE[k] = (now, df.copy(), df.attrs.get('source'))
    df.attrs['cached'] = False
    return df


# ── 标的解析（工具共用的第一步）────────────────────────────────────────

def _need_symbol(symbol):
    r = feed.resolve(symbol)
    if not r.get('ok'):
        raise ToolError('BAD_SYMBOL', r.get('hint', '认不出这个标的'),
                        '问用户要一个更明确的代码')
    if r.get('ambiguous'):
        # ★不能自己挑一个：000001 是上证指数还是平安银行，差得远，猜错了整段分析都是错的。
        names = '、'.join('%s(%s)' % (c['name'], c['code']) for c in r.get('candidates', []))
        raise ToolError('AMBIGUOUS', '「%s」有歧义：%s' % (symbol, names),
                        '必须反问用户指的是哪一个，不要替他选')
    return r


# ── 工具 1：get_kline ─────────────────────────────────────────────────

def get_kline(symbol, days=400):
    """拉历史日 K。给大模型的是「这段走势长什么样」的标量描述。"""
    days = max(60, min(int(days or 400), 1000))
    r = _need_symbol(symbol)
    df = _get_kline(r['market'], r['code'], days)
    c = df['close']
    last = float(c.iloc[-1])
    hist = _tail_bars(df, 120)

    summary = {
        'ok': True,
        'name': r['name'], 'code': r['code'], 'market': r['market'],
        'rows': len(df),
        'from': df['dt'].iloc[0].strftime('%Y-%m-%d'),
        'to': df['dt'].iloc[-1].strftime('%Y-%m-%d'),
        'last_close': round(last, 4),
        'chg_pct_5d': _pct(last, float(c.iloc[-6])) if len(c) > 6 else None,
        'chg_pct_20d': _pct(last, float(c.iloc[-21])) if len(c) > 21 else None,
        'chg_pct_60d': _pct(last, float(c.iloc[-61])) if len(c) > 61 else None,
        'range_high': round(float(df['high'].max()), 4),
        'range_low': round(float(df['low'].min()), 4),
        'ann_vol_pct': _vol(c),
        'source': df.attrs.get('source'),
    }
    assert_compact(summary, 'get_kline')
    return {'summary': summary,
            'data': {'kind': 'kline', 'bars': hist,
                     'name': r['name'], 'code': r['code'],
                     'more': max(0, len(df) - len(hist['t']))}}


def _tail_bars(df, n):
    return _bars(df.tail(n))


def _past_chg(df, n):
    """回看窗口的涨跌（%）—— "跟随趋势"这个朴素对手要看的就是它。

    ★算法必须和 `backtest_kronos.py` 的**一模一样**（那边是
      `hist.close[-1] / hist.close[-min(pred_len, len(hist))] - 1`）。
      两边不一致的话，台账里 live 那半的"跟随趋势"基线和回测那半
      就不是同一个数，而页面把它们并排放着 —— 那种错没人看得出来。
    """
    k = min(n, len(df))
    return score.pct_change(float(df['close'].iloc[-1]), float(df['close'].iloc[-k]))


# ── 工具 2：predict_kline（Kronos）─────────────────────────────────────

def predict_kline(symbol, pred_len=None, sample_count=None, lookback=None):
    """跑 Kronos 出预测。★这是唯一一个要等几十秒的工具。

    ★`sample_count` 的默认值是**算出来的**（`DEFAULT_SAMPLES`，见下），不是 1：
    采 1 条只能给出一条路径，而"价格可能落在哪"这件事**一条路径答不了**
    —— 它的 min/max 说的是"这一次采样飘了多远"。所以默认就要多采。
    """
    r = _need_symbol(symbol)
    pred_len = int(pred_len or DEFAULTS['pred_len'])
    pred_len = max(5, min(pred_len, 120))
    sample_count = max(1, min(int(sample_count or DEFAULT_SAMPLES), MAX_SAMPLES))
    lookback = int(lookback or DEFAULTS['lookback'])
    lookback = max(120, min(lookback, 510))          # 上限贴着 max_context=512

    df = _get_kline(r['market'], r['code'], lookback)
    if len(df) < 120:
        raise ToolError('TOO_SHORT', '%s 只有 %d 根 K 线，不够喂模型' % (r['code'], len(df)))

    x_ts = pd.Series(pd.to_datetime(df['dt']))
    last = pd.Timestamp(df['dt'].iloc[-1])
    # 未来时间戳：股票走交易日，加密走自然日（7×24 没有"周末"这回事）
    if r['market'] == 'crypto':
        y_dts = pd.date_range(last + pd.Timedelta(days=1), periods=pred_len, freq='D')
    else:
        y_dts = pd.bdate_range(last + pd.Timedelta(days=1), periods=pred_len)
    y_ts = pd.Series(y_dts)

    try:
        paths, info = ENGINE.sample_paths(df, x_ts, y_ts, pred_len=pred_len,
                                          n=sample_count)
    except EngineError as e:
        raise ToolError(e.code, str(e), e.hint or '模型不可用，改走知识/行情回答，别编预测数字') from e

    # 引擎对 high/low 包络的修正量（见 runner._repair_ohlc）。缓存命中时也带着，
    # 所以它一定在 info 里，缺了就是 None —— 那就按"没修"报，不编数字。
    fix = info.get('ohlc_fix') or {}

    base = float(df['close'].iloc[-1])
    # ★中心线 = N 条路径的**逐根、逐字段均值**（开/高/低/收四个字段各自取均值）。
    #   ★为什么不是"第 0 条的影线 + 均值的收盘"：那样中心线的收盘价会跑到第 0 条
    #     那根影线外头去 —— 一条 high < max(open, close) 的 K 线画出来就是错的
    #     （影线穿进实体里），而这正是 `runner._repair_ohlc` 存在的理由。
    #     实测（N=9，pred_len=30）这么干有 7 根违反，等于把刚修好的毛病从另一个门放回来。
    #   ★四个字段各自取均值则**天然自洽**：每条路径都有 h_i ≥ max(o_i, c_i)，
    #     取均值后 mean(h) ≥ mean(max(o,c)) ≥ max(mean(o), mean(c)) 恒成立（low 同理）
    #     ⇒ 送出去的这条中心线不需要再修一次。
    #   ★它**不是一条可能发生的路径**（谁也没走过这条线），页面上那条实线只能读作
    #     "采样中心"；"模型认为的范围"在下面的带子里。
    fields = ['open', 'high', 'low', 'close']
    if sample_count == 1:
        # 单条路径没有"中心"可言，直接用那一条 —— 免得均值/单条两种口径
        # 在页面上长得一样而其实是两个东西。
        central = {k: [float(x) for x in paths[0][k].astype(float)] for k in fields}
    else:
        central = {k: score.central([list(p[k].astype(float)) for p in paths])
                   for k in fields}
    pred = paths[0].copy()
    for k in fields:
        pred[k] = central[k]
    pc = pd.Series(central['close'])
    p_last = float(pc.iloc[-1])
    chg = _pct(p_last, base)
    hv = _vol(df['close'])
    # 预测路径自身的波动 vs 历史波动 —— 给"模型觉得会安静还是剧烈"一个参照
    pv = _vol(pred['close'])
    up = sum(1 for x in pc if x > base)
    # ★"路径内最高/最低"= **所有采样路径的包络**（不是中心线的极值）。
    #   N=1 时它退回原来那个数（paths[0] 就是 pred），所以这不是新口径 ——
    #   是同一句话在 N>1 时的正确读法：中心线的最高点低于任何一条真实采样路径。
    pred_high = max(float(p['high'].max()) for p in paths)
    pred_low = min(float(p['low'].min()) for p in paths)

    # ── 区间与分歧度（阶段 3）──────────────────────────────────────────
    # 只有 **N≥2** 才谈得上区间。N=1 那条路径的 min/max 不是区间，
    # 所以这里给 None，台账也跟着写 `path_range`（那边有判据守着两者不许混）。
    plo, phi = score.envelope([list(p['close'].astype(float)) for p in paths])
    spread = None
    if sample_count >= 2 and plo:
        band_lo, band_hi = plo[-1], phi[-1]          # 终点那一根 = 视野终点的区间
        votes = score.agree([list(p['close'].astype(float)) for p in paths], base)
        # ★"弃答"的判据在 `score.withhold()`（投票比例的 95% 区间跨不跨 50%），
        #   不在这里、也不拍阈值 —— 那边 docstring 记了我第一版错在哪。
        withheld = score.withhold(votes)
        # ★模型给的区间 vs **它自己的历史**在同长度视野上的自然波动。
        #   这是诊断，**不当弃答判据**：判据需要一条阈值线，而这条线是拍出来的。
        #   但两个数并排放着，用户自己就能看出"模型声称的确定性是不是过头了"
        #   （实测 6 个起点里有 2 个：模型的 80% 区间比历史自然波动还窄）。
        hist_move = None
        try:
            sd = float(df['close'].pct_change().std() or 0)
            if sd:
                hist_move = round(sd * (pred_len ** 0.5) * 100, 2)
        except Exception:                            # noqa: BLE001
            hist_move = None                         # 算不出来就不给，不编一个
        spread = {
            'n_paths': sample_count,
            # 名义水平由 N 算出（score.level_of）—— 写死就会在 N 变了之后撒谎。
            'level_pct': score.level_of(sample_count),
            'lo': round(band_lo, 4), 'hi': round(band_hi, 4),
            'lo_pct': _pct(band_lo, base), 'hi_pct': _pct(band_hi, base),
            'width_pct': _pct(band_hi, band_lo),
            'hist_move_pct': hist_move,
            'up': votes['up'], 'down': votes['down'], 'flat': votes['flat'],
            'called': votes['called'],
            'consensus': votes['consensus'], 'vote_ci95': votes['ci95'],
            'withheld': withheld,
            'why': (('N=%d 条采样路径里表了态的只有 %d 条，投票比例 %s 的 95%% '
                     '区间（%.1f–%.1f%%）跨过了 50%% —— 这些路径没有系统偏向哪一边，'
                     '所以**模型在这只上没有一致看法**，方向弃答。'
                     '这不是"看平"：看平是"它认为不会动"，而这里是"N 条路径各走各的"。'
                     '★想少弃答只能多采样（区间会变窄），不能把这个判据放宽。'
                     % (sample_count, votes['called'], votes['consensus'],
                        votes['ci95'][0], votes['ci95'][1]))
                    if (withheld and votes.get('ci95')) else
                    ('N=%d 条采样路径里一条都没表态（全部落在 ±%.1f%% 死区里），'
                     '所以方向弃答 —— "全都没表态"不等于"看平"。'
                     % (sample_count, score.DEADBAND))) if withheld else None,
        }

    # ★方向：弃答时写 `flat`，与"死区里没表态"**共用同一个语义** ——
    #   `score.hit()` 把 flat 从分母里摘掉（"没说话"和"说错了"不是一回事），
    #   所以弃答的那条不会在事后被算成一次错判。
    #   写成 None 会正好掉进反面的坑：`hit()` 判的是 `!= FLAT`，None 会被当成
    #   "表了态"，然后永远不等于真实方向 ⇒ 每一次弃答都记一次错。
    d_mean = score.sign_of(chg, score.DEADBAND) or 'flat'
    d_final = 'flat' if (spread and spread['withheld']) else d_mean

    summary = {
        'ok': True,
        'name': r['name'], 'code': r['code'], 'market': r['market'],
        'pred_len': pred_len, 'sample_count': sample_count,
        'base_close': round(base, 4),
        'pred_last_close': round(p_last, 4),
        'pred_chg_pct': chg,
        'pred_high': round(pred_high, 4),
        'pred_low': round(pred_low, 4),
        'pred_max_up_pct': _pct(pred_high, base),
        'pred_max_dn_pct': _pct(pred_low, base),
        # ★方向口径来自 `score.py`，不在这里写死数字。
        #   回测（backtest_kronos.py）打分用的是同一个 `score.DEADBAND`：
        #   写死的话，"回测 52.3%"和页面上的 `direction` 会各用各的死区，
        #   而两处都看不出对方改过 —— 那正是"两把尺子"的坏法。
        'direction': d_final,
        # 中心线自己说了什么 —— 弃答时 `direction` 会被压成 flat，那个原始判断
        # 不能就这么丢掉（诊断要看"是它本来就没表态，还是被区间否掉的"）。
        'direction_center': d_mean,
        'spread': spread,
        'days_above_base': up,
        'hist_ann_vol_pct': hv,
        'pred_ann_vol_pct': pv,
        'model': 'Kronos-small 24.7M / 512 上下文',
        'ms': info['ms'], 'cached': info['cached'],
        # ★模型输出的 high/low 包络**本来就不自洽**（实测 20 根里 12 根要修）。
        #   我们按 max/min 把它修成合法 K 线，但**修了多少必须说出来** ——
        #   不说就是"悄悄改了模型的输出"，那是另一回事。这两个数会进大模型的上下文，
        #   它回答时才有资格说"高点是重建的、可信度低于收盘价那条线"。
        #   ★报的是 `touched` = **被动过的根数**（上下包络取并集，见 sample_paths）。
        #     不用 `high_clamped + low_clamped`：那是"修正次数"，一根同时修上下包络时
        #     会数两次，于是这个数可能超过预测段本身的根数（N 条路径相加更离谱）。
        #     页面上那句话说的是"N 根"，就得是根数。
        'ohlc_repaired_bars': fix.get('touched', 0),
        'ohlc_max_clamp_pct': fix.get('max_clamp_pct', 0.0),
        'caveat': '★模型的输出**只做参考**，不是结论、更不是依据：它只输入了这段价格序列，'
                  '没有基本面、财报、新闻、政策、资金面。**不构成投资建议**。'
                  '★其中 open/close 是模型直接给的；high/low 的包络由我们按 max/min 重建'
                  '（模型给的高低价不自洽），所以**高/低点的可信度低于收盘价那条路径**。'
                  + ('★`spread` 是 %d 条**各自独立**采样路径的极差：名义水平 %s%%'
                     '由 N 算出（极差覆盖一个新样本的概率是 (N-1)/(N+1)，不假设正态）。'
                     '它说的是"模型自己认为的范围"，**不是**"价格有 %s%% 的概率落在里面" ——'
                     '本站回测已实测该模型没有超额能力，区间宽窄只反映模型的不确定，'
                     '不构成对未来的概率承诺。'
                     % (sample_count, score.level_of(sample_count),
                        score.level_of(sample_count)) if spread else
                     '★这一次只采了 1 条路径，**没有区间**：一条路径的 min/max 不是'
                     '置信区间（它只说明"这一次采样飘了多远"）。要区间就调大 `sample_count`。')
                  + ('★`spread.withheld=true` ⇒ **方向弃答**：%s '
                     '回答时**不许**给出涨跌方向，要明说模型在这只上没有一致看法。'
                     % (spread or {}).get('why') if spread and spread['withheld'] else ''),
    }
    # ── 落台账 ────────────────────────────────────────────────────────
    # ★写在 `assert_compact` **之后**：那一句会抛（摘要里漏进数组时），
    #   而"抛掉的那次预测"压根没送到用户手上，不该在台账里留一条战绩。
    #
    # ★台账里存的是**打分的原始输入**，不是算好的分：模型给的收盘价路径、
    #   基准价、回看窗口均值和涨跌（`revert` 和"跟随趋势"基线要用）。
    #   这样 `score.py` 以后修好了，整个历史台账跟着一起修，不用重跑模型。
    pred_path = [round(float(x), 6) for x in pc]
    eid = uuid.uuid4().hex[:12]
    ok, err = ledger.append({
        'kind': 'pred', 'id': eid, 'source': 'live',
        'symbol': r['code'], 'code': r['code'], 'market': r['market'],
        'name': r['name'],
        # `asof` = 预测**站在这根**上（回看窗口的最后一根，也就是真实数据的最后一天）。
        # 回填就是从它之后取真实 K 线。
        'asof': str(df['dt'].iloc[-1])[:10],
        'pred_len': pred_len, 'lookback': len(df), 'sample_count': sample_count,
        'direction': summary['direction'],
        'close_last': round(base, 6), 'pred_close_last': round(p_last, 6),
        'pred_close': pred_path,
        # ★`interval_kind` 把"这是哪种区间"写死在记录里，**两种绝不许混**：
        #     `sample_quantile` —— N 条独立采样终点的极差，名义水平由 N 算出
        #                          （score.level_of），**这才算得出覆盖率**；
        #     `path_range`     —— 单次采样这一条路径的 min/max，**不是区间**，
        #                          它量的是"这一次采样飘了多远"。
        #   `ledger.coverage()` 只认前者（那边有一条判据钉着）。不写这一笔，
        #   以后算覆盖率的人分不出手上这份是哪种，会拿路径极差当区间用 ——
        #   而那个数看着一样像结论。
        'interval_kind': 'sample_quantile' if spread else 'path_range',
        'interval_level': (spread or {}).get('level_pct'),
        'lo': (spread or {}).get('lo', round(min(pred_path), 4)),
        'hi': (spread or {}).get('hi', round(max(pred_path), 4)),
        # ★弃答也记一笔。不记的话，事后看台账只能看到 `direction: flat`，
        #   分不出那是"死区里没表态"还是"区间跨过现价所以弃答"——
        #   而"多采样之后弃答率有多高"正是这个功能值不值得留的依据。
        'direction_withheld': bool((spread or {}).get('withheld')),
        'window_mean': round(float(df['close'].mean()), 6),
        'past_chg': round(_past_chg(df, pred_len), 6),
        'model_sha': ledger.model_sha()['sha'],
        # ★这里必须 `.get()` 而不是 `[...]`：台账是**旁路**。摘要里少一个字段
        #   （比如以后有人重构掉 `ohlc_repaired_bars`）不该让整条预测死掉 ——
        #   用户等了十几秒，为了一条"修了几根"的备注把预测本身弄丢，
        #   是把旁路当成了主路。真拿不到就记 None（= 不知道），不编一个 0。
        #   （这条不是假想：`--selftest` 里那条"把修正量藏起来"的注入
        #    正是删掉摘要里那一行，写成 `[...]` 时它会让 predict_kline
        #    直接 KeyError，判据红在"预测挂了"而不是"没报修正量"。）
        'repaired_bars': summary.get('ohlc_repaired_bars'),
    })
    # ★落不上必须**明说**，不许静默不记 —— 但也不许把这条预测弄丢
    #   （用户等了十几秒）。所以它进摘要，大模型和页面都有资格看到。
    summary['ledger'] = ({'ok': True, 'id': eid, 'path': ledger.path()}
                         if ok else
                         {'ok': False, 'why': err,
                          'note': '★这次预测**没记进台账**（%s）—— 以后查不到它的战绩。'
                                  '不是模型的问题，是磁盘/权限的问题。' % err})
    assert_compact(summary, 'predict_kline')

    # 给前端：历史尾段 + 预测段拼成一条连续 K 线
    hist = _tail_bars(df, 90)
    return {'summary': summary,
            'data': {'kind': 'predict', 'name': r['name'], 'code': r['code'],
                     'history': hist, 'pred': _bars(pred),
                     'base_close': round(base, 4),
                     # ★采样扇形：逐根的 [lo, hi]（收盘价那一路），页面画成带子。
                     #   它是**逐根**的，不是一条水平带 —— 逐根的才看得出
                     #   "越推越不确定"这件事，水平带会把这个信息压掉。
                     #   N=1 时没有扇形（一条路径没有范围），给 None。
                     'band': ({'lo': [round(x, 4) for x in plo],
                               'hi': [round(x, 4) for x in phi],
                               'level_pct': score.level_of(sample_count),
                               'withheld': bool(spread and spread['withheld'])}
                              if spread else None),
                     'params': {'pred_len': pred_len, 'sample_count': sample_count,
                                'lookback': len(df), 'T': DEFAULTS['T'],
                                'top_p': DEFAULTS['top_p'], 'ms': info['ms'],
                                # ★给页面用的：预测段有多少根的高/低点是**重建**的
                                #   （见 runner._repair_ohlc）。这一层必须同时出现在
                                #   三条路上 —— 给大模型的摘要、给页面的说明条、
                                #   以及验证脚本的判据 —— 少一条就等于"悄悄修"。
                                'ohlc_repaired_bars': fix.get('touched', 0),
                                'ohlc_max_clamp_pct': fix.get('max_clamp_pct', 0.0)}}}


# ── 工具 3：get_fundamentals（估值 / 财务比率 / 行业）────────────────────
# 为什么它单独是一把工具，而不是塞进 get_kline：这两个问题的**数据源、时效、
# 适用面**都不一样。K 线哪只标的都有；基本面**只有 A 股个股有** —— ETF 是一篮子、
# 指数是加权数、港美股不在巨潮/A 股财报口径里。混在一起，模型会理所当然地
# 对一只 ETF 问市盈率，然后拿到一个编出来的数。
_FUND = {}
_FUND_TTL = 3600          # 估值/财报按天变，缓存一小时不亏


def _get_fund(code):
    hit = _FUND.get(code)
    now = time.time()
    if feed.stub_active():                       # 同 _get_kline：桩开着就绕开缓存
        hit = None
    if hit and now - hit[0] < _FUND_TTL:
        d = dict(hit[1])
        d['cached'] = True
        return d
    d = feed.fetch_fundamentals(code)
    _FUND[code] = (now, d)
    d['cached'] = False
    return d


def get_fundamentals(symbol):
    """A 股个股的基本面：估值（PE/PB/市值/分位）+ 财务比率（ROE/负债率/增速）+ 行业。

    ★这个工具**只对 A 股个股成立**，别的一律明说没有 —— 不许拿 K 线去凑一个 PE。
    """
    r = _need_symbol(symbol)
    if r['market'] != 'cn_stock':
        raise ToolError(
            'NOT_APPLICABLE',
            '%s（%s）不是 A 股个股，没有 PE/PB/行业这类基本面数据' % (r['name'], r['market']),
            'ETF 跟踪的是一篮子、指数是个加权数、港美股不在巨潮/A 股财报口径里。'
            '★**绝不要给它编一个市盈率或 ROE**；要么直接说这只标的没有这块数据，'
            '要么改用 get_kline 讲走势')
    f = _get_fund(r['code'])
    if not f.get('ok'):
        raise ToolError('NO_FUNDAMENTALS', '没取到 %s(%s) 的基本面' % (r['name'], r['code']),
                        '；'.join(f.get('errors') or []) or '三个来源都没回数据，稍后再试')

    v, fin = f.get('valuation') or {}, f.get('financial') or {}
    summary = {
        'ok': True,
        'name': f.get('name') or r['name'], 'code': r['code'], 'market': r['market'],
        'full_name': f.get('full_name'),
        'industry': f.get('industry'),
        'board': f.get('board'),
        'listed': f.get('listed'),
        'business': f.get('business'),
        # 估值：分位是**这只票自己历史**里的位置，不是跟同行比 —— 名字里和说明里都得写清
        'valuation_date': v.get('date'),
        'pe_ttm': v.get('pe_ttm'), 'pb': v.get('pb'),
        'pe_static': v.get('pe_static'), 'peg': v.get('peg'),
        'ps': v.get('ps'), 'pcf': v.get('pcf'),
        'mkt_cap_yi': v.get('mkt_cap_yi'), 'float_cap_yi': v.get('float_cap_yi'),
        'pe_pctile_5y': v.get('pe_pctile_5y'), 'pb_pctile_5y': v.get('pb_pctile_5y'),
        'valuation_history_rows': v.get('history_rows'),
        # 财务：**报告期**数据，不是"当前"的数。附上报告期日期，别让模型说成"现在"。
        'report_date': fin.get('report_date'),
        'roe': fin.get('roe'), 'roe_weighted': fin.get('roe_weighted'),
        'debt_ratio': fin.get('debt_ratio'),
        'rev_growth': fin.get('rev_growth'), 'profit_growth': fin.get('profit_growth'),
        'gross_margin': fin.get('gross_margin'),
        'current_ratio': fin.get('current_ratio'), 'quick_ratio': fin.get('quick_ratio'),
        'sources': [x for x in (f.get('industry_src'), f.get('valuation_src'),
                                f.get('financial_src')) if x],
        'missing': f.get('errors') or [],
        'cached': f.get('cached'),
        'caveat': '★这几条是**事实**，不是**结论**，说的时候必须带上限定：'
                  '①PE/PB 低不等于便宜 —— 可能是市场在预期业绩下滑，银行地产的 PE/PB '
                  '天生就低于成长股，**跨行业比 PE 没有意义**；'
                  '②pe_pctile_5y / pb_pctile_5y 是**这只票跟自己的过去比**（时序分位），'
                  '**不是**跟同行比（截面分位），两者会给出相反的结论；'
                  '③财务比率是**报告期**（见 report_date）的数据，不是实时，'
                  '一季报和年报的口径也不一样；'
                  '④字段为 null 表示「这个口径下没有/取不到」（如银行没有"主营业务收入'
                  '增长率"），**不是 0**，不要当 0 用、也不要自己补一个数；'
                  '⑤本工具**不含任何股息/分红数据** —— 既没有股息率（每股股利÷股价），'
                  '也没有股息发放率（每股股利÷每股收益）。新浪那列"股息发放率"实测不可用'
                  '（银行股整列为空、其余标的跨期数值互相矛盾），所以**没有采纳**。'
                  '★用户问股息率/分红率，就直说这个口径我们取不到、让他换个来源；'
                  '**不要**用别的字段去凑一个（比如拿 ROE 或 PE 硬套），凑出来是个'
                  '看着很像真的错数。',
    }
    assert_compact(summary, 'get_fundamentals')

    # ★数据通道：分位卡要用的东西。**字段名是前后端的契约**，
    #   前端 `FundCard.tsx` 的 `FundData` 逐个读它们，改名等于把卡画空。
    #   ★这里给的是**数**（pe/pb/分位），不是结论 —— 卡片不许替用户判断"便宜"。
    #   ★`kind` 必须出现在 llm.py 的 emit 白名单里，否则这份数据**发不出去**：
    #     那是"算了但没人收"的静默坏法，页面上什么都看不见、也不报错。
    data = {'kind': 'fundamentals', 'code': r['code'],
            'name': summary['name'], 'industry': summary['industry'],
            'pe': v.get('pe_ttm'), 'pb': v.get('pb'),
            'pe_pctile_5y': v.get('pe_pctile_5y'), 'pb_pctile_5y': v.get('pb_pctile_5y'),
            # ★近 5 年区间的两个端点。有它们，用户**拿眼睛就能验**那个分位
            #   （"现价 19.3，区间 19.3~79.1 ⇒ 3.4% 说得通"），
            #   没有它们，分位就只是一个要信的数。区间与分位同窗口（见 feed.PCTILE_WINDOW）。
            'pe_lo5y': f.get('pe_lo5y'), 'pe_hi5y': f.get('pe_hi5y'),
            'pb_lo5y': f.get('pb_lo5y'), 'pb_hi5y': f.get('pb_hi5y'),
            'mkt_cap_yi': v.get('mkt_cap_yi'), 'valuation_date': v.get('date')}
    if f.get('series'):
        data['series'] = f['series']               # 给前端画近 5 年 PE 走势
    return {'summary': summary, 'data': data}


# ── 工具：get_news（资讯采集，只采不析）───────────────────────────────

def get_news(symbol=None, query=None, limit=6):
    """多源新闻/资讯采集。摘要只给标题+来源+时间+热度；正文走数据通道。

    ★两条纪律：
      1. **只采不析**（用户对关联检测的口径："注重采集，弱化分析"）——
         这个工具不判断利好利空，`heat`（同题多源数）是唯一的"分析"，
         而它只是数数。观点是舆情 Agent / 大模型的事，且必须标明是它的观点。
      2. **全源失败 = 结构化失败**，绝不拿旧缓存或记忆充数。
    """
    limit = max(1, min(int(limit or 6), 12))
    if not symbol and not query:
        raise ToolError('BAD_ARGS', 'symbol 和 query 至少要给一个',
                        '问用户想查哪只票或什么主题的新闻')
    keywords, name, code = [], None, None
    if symbol:
        r = _need_symbol(symbol)
        name, code = r['name'], r['code']
        keywords += [r['name'], r['code']]
    if query:
        keywords.append(str(query).strip())
    res = news.collect(symbol=code, query=query, limit=limit, keywords=keywords)
    if not res.get('ok'):
        raise ToolError('NO_NEWS',
                        '新闻没取到（配置的源全部失败：%s）'
                        % '；'.join(d.get('reason', '')[:60] for d in res.get('degraded', []))[:300],
                        '直说"新闻我没取到"，绝不许凭记忆编新闻')
    items = res.get('items') or []
    sm_items = [{'title': (it.get('title') or '')[:60],
                 'source': it.get('source_label') or it.get('source'),
                 'ts': it.get('ts'), 'heat': it.get('heat', 1)}
                for it in items]
    summary = {'ok': True, 'count': len(items), 'as_of': res.get('as_of'),
               'name': name, 'code': code, 'query': query,
               'items': sm_items,
               'degraded': (res.get('degraded') or [])[:5],
               'note': '只采集不分析：heat=同一事件出现在几个源里（多源同题=热度信号）。'
                       '新闻不代表买卖依据；转述时保留"据报道"口径，不要写成事实断言。'}
    # 摘要超预算就砍条目（标题长度不可控）——砍到进预算为止，绝不动 note/degraded
    while len(json.dumps(summary, ensure_ascii=False).encode()) >= SUMMARY_BUDGET - 200 \
            and len(summary['items']) > 1:
        summary['items'].pop()
        summary['count'] = len(items)          # count 说的是采到几条，不是摘要放了几条
        summary['shown'] = len(summary['items'])
    assert_compact(summary, 'get_news')
    return {'summary': summary,
            'data': {'kind': 'news',
                     'items': [{'title': it.get('title'), 'excerpt': it.get('excerpt'),
                                'source': it.get('source_label') or it.get('source'),
                                'source_id': it.get('source'), 'ts': it.get('ts'),
                                'url': it.get('url'), 'heat': it.get('heat', 1),
                                'seen_in': it.get('seen_in') or []}
                               for it in items],
                     'degraded': res.get('degraded') or [],
                     'name': name, 'code': code, 'query': query}}


# ── 工具 4/5：查词与走图（零成本，不调模型、不联网）──────────────────────

def search_terms(query, limit=3):
    """查词典。走的是现有产物 graph_data.json，不碰 md 真源。"""
    kb = graph_kb.kb()
    hits = kb.search(query, limit=max(1, min(int(limit or 3), 6)))
    if not hits:
        return {'summary': {'ok': True, 'found': False, 'query': query,
                            'hint': '词表里没有这个词。可以换个说法再试，或直接说"我不知道这个词"——'
                                    '不要编一个解释出来。'},
                'data': {'kind': 'terms', 'chips': []}}
    named = [h['name'] for h in hits]
    summary = {'ok': True, 'found': True, 'query': query, 'count': len(hits),
               'terms': [{'name': h['name'], 'category': h['cat'], 'family': h['family'],
                          'is_starting_point': h['is_start'],
                          'fields': h['fields'], 'contrast_with': h['contrast_with']}
                         for h in hits]}
    assert_compact(summary, 'search_terms')
    return {'summary': summary, 'data': {'kind': 'terms', 'chips': named}}


def walk_graph(term, relation=None, limit=10):
    """顺着「属于 / 对比 / 前提」走一步，用来回答"这跟什么有关"。"""
    kb = graph_kb.kb()
    rel = None
    if relation:
        m = {'属于': 'belong', '对比': 'contrast', '前提': 'prereq',
             'belong': 'belong', 'contrast': 'contrast', 'prereq': 'prereq'}
        rel = m.get(str(relation).strip())
        if rel is None:
            raise ToolError('BAD_RELATION', '关系只能是 属于/对比/前提，收到「%s」' % relation)
    w = kb.walk(term, rel, limit=max(1, min(int(limit or 10), 20)))
    if not w:
        return {'summary': {'ok': True, 'found': False, 'term': term,
                            'hint': '词表里没有这个词，别硬走'},
                'data': {'kind': 'terms', 'chips': []}}
    summary = {'ok': True, 'found': True, 'term': w['term'], 'relation': w['relation'],
               'neighbors': w['neighbors']}
    assert_compact(summary, 'walk_graph')
    return {'summary': summary,
            'data': {'kind': 'terms', 'chips': [w['term']] + [n['name'] for n in w['neighbors']]}}


# ── 工具 6：analyze_portfolio（组合体检）──────────────────────────────

# ★真实持仓走**请求上下文**，不走工具参数 —— 这是本工具唯一一处刻意的设计。
#   为什么不让大模型把持仓抄进 `holdings` 参数：它会**抄漏一只**（3~8 只里漏一只，
#   在对话里肉眼完全看不出来），而漏一只的后果是权重被重新归一化 ——
#   集中度、相关性、分散度**每一个数**都跟着变，产出一份"看着完全合理、
#   但整个是错的"报告，而且没有任何判据看得出来（数确实都是真算的）。
#   ⇒ 真实持仓由 `app.py` 从请求里取出来放这儿，模型**够不着**它。
#   它的 `holdings` 参数只用于**假设**（"如果我再加一只茅台呢"），
#   用了假设会在摘要里明写 `hypothetical: true`，且在页面上也标出来。
_REQ_HOLDINGS = contextvars.ContextVar('licai_req_holdings', default=None)


def set_request_holdings(holdings):
    """把本次请求的持仓放进上下文。返回 token —— 调用方**必须**在 finally 里复位。

    ★不复位的后果不是报错，是**串台**：下一个请求（可能来自另一个浏览器）
      会拿到上一个人的持仓去做体检，一份完全张冠李戴的报告。
    """
    return _REQ_HOLDINGS.set(holdings or None)


def reset_request_holdings(token):
    _REQ_HOLDINGS.reset(token)


# ★画像里的三个档位走**同一个形状**的请求上下文，理由和持仓一模一样（见上）：
#   让大模型把「每月可投入」从提示词里抄进工具参数，它会抄成中点、抄成"3000元"
#   这种本站根本没有的档位 —— 而算出来的是一份**看着完全合理**的概率。
#   所以真实档位由 `app.py` 从请求里取出来放这儿，模型够不着它。
#   它的三个参数只用于**假设**（"如果我每月再多投 2000 呢"），
#   用了假设会在摘要里明写 `hypothetical: true`。
_REQ_PROFILE = contextvars.ContextVar('licai_req_profile', default=None)


def set_request_profile(profile):
    """把本次请求的画像档位放进上下文。返回 token —— 调用方**必须**在 finally 里复位。

    ★不复位的后果不是报错，是**串台**：下一个请求（可能来自另一个浏览器）
      会拿上一个人的月投入去算他的目标概率。**这一段没有任何数字会报错** ——
      算出来的每一格都是"真算的"，只是算的不是这个人的。
    """
    return _REQ_PROFILE.set(profile or None)


def reset_request_profile(token):
    _REQ_PROFILE.reset(token)


def clean_profile(raw):
    """客户端送上来的画像一律当**不可信输入**：这里只留目标推演用得上的三档。

    ★只认**字符串**，且**一个字的宽松解析都不做**：`goal.band()` 那边是按档位名
      精确查表的，"三到五年"这种写法认不出来就是认不出来（它会被当成"画像没填"
      而明确报错）。做模糊匹配的话，把 "3-5年" 认成 "5年以上" 也能"成功" ——
      算出来的是另一个区间，而结果看起来一样正常。**认错比认不出坏得多。**
    """
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in ('assets', 'income', 'horizon'):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:24]
    return out or None


def clean_holdings(raw):
    """客户端送上来的持仓一律当**不可信输入**：清洗在这里，只做一次。

    ★这三行形状归一**不是防御性编程，是补一个真的会炸的洞**：原来写的是
      `for h in (raw or [])`，于是 `raw=123`（或任何非可迭代对象）会在**迭代那一刻**
      抛 `TypeError: 'int' object is not iterable`。而调用它的是 `/api/chat`、
      `/api/portfolio`、`/api/agents` 三处 —— 一个手写客户端的笔误
      就变成三个接口的 500，报的还是"服务器坏了"。（`/api/linkage` 的 `codes`
      犯过一模一样的错，已在那边按 `BAD_ARGS` 拦下。）
    ★这里选择**宽进**、不抛：持仓是**可选**参数，没有它请求照样成立
      （组合体检那一路会降级成"还不知道你的持仓"），所以一个畸形载荷不值得
      把整个请求判死。这与 `codes` 的处理相反是有意的 —— 那边的值畸形时
      **整个请求没有意义**，必须报 `BAD_ARGS`；判据是"没有它请求还成不成立"。
    ★裸 dict 收成一个持仓：`{"symbol":…,"weight":…}` 直接当 `holdings` 送
      （忘了套数组）是很容易犯的错，而它的意思**没有歧义**，收下就是。
    """
    if isinstance(raw, dict):
        raw = [raw]
    elif not isinstance(raw, (list, tuple)):
        raw = []
    out = []
    for h in raw:
        if not isinstance(h, dict):
            continue
        sym = str(h.get('symbol') or '').strip()
        if not sym:
            continue
        try:
            w = float(h.get('weight') or 0)
        except (TypeError, ValueError):
            w = 0.0
        out.append({'symbol': sym, 'name': str(h.get('name') or '')[:40], 'weight': w})
    return out


def analyze_portfolio(holdings=None, risk=None):
    """给当前持仓做一次体检：集中度 / 相关性聚集 / 波动回撤 / Beta / 压力。

    ★这里**只算数、不给健康总分**：skill 的模板把 7 个维度加权成 0–100 分，
      但其中「流动性」「成本效率」本站**没有任何数据源**。给一个含这两项的分数，
      等于用一个编出来的数字去稀释六个真的。所以只报能算的维度，
      算不了的明写"本站没有数据，不给分"。
    """
    hypo = bool(holdings)
    raw = clean_holdings(holdings) if hypo else (_REQ_HOLDINGS.get() or [])
    if not raw:
        raise ToolError(
            'NO_HOLDINGS', '还不知道你的持仓，没法体检',
            '持仓存在用户自己的浏览器里（「模拟投资」页买的那些），本站服务端不保存。'
            '如果他还没买过任何一笔，就直说"先去模拟投资里买几笔"；'
            '**不要**自己编一个组合去分析。')
    try:
        r = portfolio.analyze(raw, risk=risk)
    except feed.SourceError as e:
        raise ToolError('SOURCE_FAILED', '取行情失败，没法体检（%s）' % e,
                        '行情源临时挂了，稍后再试；**不要**拿记忆里的行情凑一份报告')
    if not r.get('ok'):
        raise ToolError('CANNOT_ANALYZE', r.get('reason') or '这份组合算不了',
                        r.get('hint') or '把原因照实说给用户，不要硬给结论')
    m = r['metrics']
    summary = {
        'ok': True,
        'hypothetical': hypo,
        'n_holdings': m.get('n_holdings'),
        'survived_weight': r.get('survived_weight'),
        'weights': m.get('weights'),
        'top5': m.get('top5'), 'hhi': m.get('hhi'),
        'vol_annual': m.get('vol_annual'), 'mdd': m.get('mdd'),
        'edr': m.get('edr'),
        'beta': m.get('beta'), 'beta_days': m.get('beta_days'),
        'corr_weeks': m.get('corr_weeks'),
        'clusters': m.get('clusters'),
        'grades': r['grades'],
        'dropped': r.get('dropped'),
        'industry': m.get('industry'),
        'stress': m.get('stress'),
        'caveat': '★这几条是**算出来的数**，不是买卖建议，说的时候必须带上限定：'
                  '①**没有健康总分** —— skill 的模板里有「流动性」「成本效率」两项，'
                  '本站取不到这两类的数据，所以**不给你一个 0–100 的分数**，'
                  '免得用编出来的数稀释真的；'
                  '②相关性是**回看**的（见 corr_weeks 周），历史相关不等于以后一起跌；'
                  '③ETF/指数**没有行业**，行业分布里缺它们不是漏算，别替它们编一个行业；'
                  '④**不要**因为 EDR 高就说"分散得好" —— EDR 只数波动率，'
                  '相关性聚集那一项才是"名字不同、走势是一条线"，两项要一起说；'
                  '⑤不得据此说"你该买/该卖什么"，只能把风险点摆出来。',
    }
    if r.get('notes'):
        summary['notes'] = r['notes']
    if hypo:
        summary['hypothetical_note'] = ('★这是一组**假设**的持仓，不是他真实的组合 —— '
                                        '说的时候必须点明是"如果这样配"，别让他以为是自己的仓位。')
    assert_compact(summary, 'analyze_portfolio')

    # ★数据通道：给前端画图。**字段名是前后端的契约**（`Portfolio.tsx` 逐个读），
    #   改名等于把页面画空。`kind` 必须出现在 `llm.CHART_KINDS` 里，
    #   否则这份数据**发不出去** —— "算了但没人收"的静默坏法。
    return {'summary': summary,
            'data': {'kind': 'portfolio', 'hypothetical': hypo,
                     'weights': m.get('weights'), 'series': r.get('series'),
                     'corr': r.get('corr'), 'grades': r['grades'],
                     'industry': m.get('industry'), 'stress': m.get('stress'),
                     'dropped': r.get('dropped'), 'notes': r.get('notes') or [],
                     'metrics': {k: m.get(k) for k in
                                 ('top5', 'hhi', 'vol_annual', 'mdd', 'edr', 'beta',
                                  'beta_days', 'corr_weeks', 'n_holdings')}}}


# ── 工具 7：plan_goal（目标达成概率）──────────────────────────────────

def plan_goal(target, symbol=None, monthly=None, horizon=None, assets=None):
    """"按历史情况，每月投这么多、放这么久，攒到目标金额的概率"。

    ★分布来自**历史月度收益的重排**（`goal.py` 的 block bootstrap），
      **不是** Kronos 采样路径。理由写在 `goal.py` 开头：那个模型在 30 天视野上
      实测没有超额能力，拿它外推 60 个月得到的是一个**看着最像结论、却毫无依据**的数。

    ★三个档位参数只在**假设**时填。真实档位从请求上下文取（见 `_REQ_PROFILE`）——
      和持仓那条是同一个理由，不再重复。
    """
    hypo = bool(monthly or horizon or assets)
    over = clean_profile({'income': monthly, 'horizon': horizon, 'assets': assets}) or {}
    prof = dict(_REQ_PROFILE.get() or {})
    prof.update(over)
    if not prof:
        raise ToolError(
            'NO_PROFILE',
            '还不知道他的每月可投入和打算放多久，算不了目标概率',
            '这两项在「我的画像」里（单选档位，不是自由输入）。'
            '如果他还没填过，就直说"先去填一下画像"；'
            '**不要**替他假设一个金额或期限，也不要拿别处的数字凑一个概率。')
    try:
        r = goal.plan(target, prof, symbol=symbol)
    except feed.SourceError as e:
        raise ToolError('SOURCE_FAILED', '取行情失败，算不了目标概率（%s）' % e,
                        '行情源临时挂了，稍后再试；**不要**拿记忆里的行情凑一个概率')
    if not r.get('ok'):
        raise ToolError('CANNOT_PLAN', r.get('reason') or '这个目标算不了',
                        r.get('hint') or '把原因照实说给用户，不要硬给一个概率')
    summary = {
        'ok': True,
        'hypothetical': hypo,
        'target': r['target'],
        # ★代理标的必须原样带上：这个概率说的**不是他的组合**，
        #   而是"拿沪深300的历史重排一遍"。不说这一句，用户会读成"我的组合有 62% 概率"。
        'proxy': r['proxy'], 'proxy_name': r['proxy_name'],
        'months': r['months'],
        # ★两端一对，**不是**一个数：画像里的档位是区间，概率也是区间。
        'monthly': r['monthly'], 'assets': r['assets'],
        'p_attain_pct': r['p_attain_pct'],
        'corners': r['corners'],
        'hist_months': r['hist_months'], 'block_months': r['block_months'],
        'paths': r['paths'],
        'sensitivity': r['sensitivity'],
        'bound_note': r['bound_note'],
        'caveat': '★这几条**必须**跟着数字一起说，缺一条这个数就成了算命：'
                  '①分布来自**历史上真实发生过的月份重排**（%d 个月，%d 个月一段），'
                  '**不是预测** —— 历史分布不等于未来分布，这句要原样说出来；'
                  '②它推的是 **%s** 的历史，**不是他手里那个组合**，两者的差别要讲清；'
                  '③`p_attain_pct` 和 `corners` 都是**两端一对**（画像档位的两端各算一次），'
                  '**没有中点**：档位是用户选的区间，替他取中点就是编一个他没说过的数；'
                  '④`sensitivity` 那几格是**假设年化下的确定性推演**，不是概率 —— '
                  '标签上写着"假设"，说的时候也要说是假设；'
                  '⑤**不许**说"你一定能达成/肯定达不到"，也**不许**说该买什么、'
                  '该投多少 —— 只能把概率和它的前提摆出来，选择权在用户手里；'
                  '⑥这是**历史重排**（历史模拟），不是"模拟未来"——'
                  '说成后者，用户听到的就是一句预测。'
                  % (r['hist_months'], r['block_months'], r['proxy_name'] or r['proxy']),
    }
    # ★没有单边档位时把整个键**去掉**，不是留一个 null：留着的症状是大模型看到
    #   `bound_note: null` 仍然去提"这是下界"，而这次三档都是两端俱全的区间。
    if not r['bound_note']:
        summary.pop('bound_note', None)
    if hypo:
        summary['hypothetical_note'] = ('★这次用的是**假设**的档位（%s），'
                                        '不是他画像里填的 —— 说的时候必须点明'
                                        '"如果按这个假设"，别让他以为是自己那份。'
                                        % json.dumps(over, ensure_ascii=False))
    assert_compact(summary, 'plan_goal')
    return {'summary': summary, 'data': None}


# ── 新增能力：分钟策略 / 关联检测 / 多智能体博弈 ────────────────────────
#
# ★这三块各自由一个独立包承担（intraday / linkage / agents），而它们是**并行施工**的。
#   写这段的时候那三个包还没落地，所以这里一律**惰性 import**，
#   并把"包还没到位"翻译成一句人话的结构化失败 —— 而不是让 `import tools` 直接崩，
#   那会把整个站点连同已有的 8 把工具一起带走。
#   等包落地，这三把工具自动点亮，这里一个字都不用改。

def _lazy(modname, label, need=None):
    """取一个并行施工中的包。区分「还没落地」和「落地了但坏了」。

    ★这两种情况必须分开报：
      - 包不存在、或包在但**入口函数还没写出来** ⇒ NOT_READY。这是施工期间的
        **预期状态**，报出来让人知道不是自己用错了。
      - 包在、入口也在、但 import 过程本身抛异常 ⇒ **真有 bug**，让它原样冒上去。
        绝不能报成"还没就绪" —— 那会让一个坏掉的包伪装成"还没做"，永远没人去修。

    ★"入口函数在不在"这一条不是多余的：Python 3 的**命名空间包**让一个只有目录、
      没有 `__init__.py` 的包也能被 `find_spec` 找到并 import 成功，
      于是 `mod.analyze` 会抛 AttributeError —— 那看起来像"包坏了"，
      实际只是"还没写到这里"。靠这一条把两者分开。
    """
    if importlib.util.find_spec(modname) is None:
        raise ToolError('NOT_READY',
                        '%s 这个能力还没就绪（%s 包未落地）' % (label, modname),
                        '直说这个能力现在不可用，不要拿别的东西顶替，更不许编数字')
    mod = importlib.import_module(modname)
    if need and not callable(getattr(mod, need, None)):
        raise ToolError('NOT_READY',
                        '%s 这个能力还没就绪（%s.%s 入口未落地）' % (label, modname, need),
                        '直说这个能力现在不可用，不要拿别的东西顶替，更不许编数字')
    return mod


def is_synthetic(data_source):
    """这个 `data_source` 标签说的是不是"数不是真的"。

    ★**只有这一处定义**。两个消费者都要这个判断：
      - 聊天那条路（本文件的 `analyze_intraday` → 摘要 + data 通道）；
      - 页面那条路（`app.py` 的 `/api/intraday` → `mod.analyze()` 的**原样**返回）。
      两条路各写一遍的话，迟早一条说真、一条说假 —— 而这条判断管的是
      「用户看到的数是不是编的」，判错了比不判坏得多。
    ★标签的约定是 `analyze()` 给的：真源叫 `'datasource'`，合成叫 `'SYNTH(...)'`。
      判据取"**不是 datasource 就算合成**"（宽进），因为两种错法的代价不对称：
      把真数据误标成合成 = 用户白紧张一下；把合成误标成真的 = 拿编的价位当行情讲。
    """
    return not str(data_source or '').startswith('datasource')


def analyze_intraday(symbol, periods=None):
    """分钟级量价策略分析（默认 5/15/30 分钟）。摘要给结论与关键位；信号明细走数据通道。

    ★两条纪律：
      1. 每个信号的 `why` 必须是**可复核的算术**（带具体价位/倍数）。摘要里只截断显示，
         但截断是展示问题，不是把"未算"说成"算过"。
      2. 数据不够的策略**整条不出现**，并在 errors 里写明少了什么 ——
         不许用"技术面偏强"之类的话把空缺填满。
    """
    r = _need_symbol(symbol)
    mod = _lazy('intraday', '分钟级策略分析', need='analyze')
    pl = tuple(int(p) for p in (periods or (5, 15, 30)))
    bad = [p for p in pl if p not in (1, 5, 15, 30, 60)]
    if not pl or bad:
        raise ToolError('BAD_ARGS', 'periods 只能取 1/5/15/30/60，收到 %s' % (bad or '空'),
                        '按 5/15/30 三档来问')
    # ★港美股/加密在本机没有分钟线源 ⇒ 走日线口径的短期策略（分流逻辑唯一真源在
    #   `daily_short.analyze_auto`，见那个模块头）。A 股那条路**逐字节没动**。
    #   lazy import + 兜底：拿不到就按原路走，不让降级本身变成新失败点。
    try:
        from daily_short import analyze_auto as _auto, needs_daily as _needs
        _daily = _needs(r['market'])
    except Exception:                                             # noqa: BLE001
        _auto, _daily = None, False
    res = (_auto(r['market'], r['code'], periods=pl, name=r['name']) if _daily
           else mod.analyze(r['market'], r['code'], periods=pl, name=r['name']))
    if not res.get('ok'):
        _errs = '；'.join(str(e)[:70] for e in (res.get('errors') or []))[:280]
        if _daily:
            # ★这条 guidance 与上面那条**不能共用**：原话"不要退化去说日线"是在
            #   "日线还没算过"的前提下写的；走日线降级的市场里，日线**已经算过了**，
            #   再照那句话指示模型，它会把"日线也没取到"说成"分钟线取不到"，
            #   用户就会去等一个不存在的东西。
            raise ToolError('NO_DAILY', '%s 的日线也没取到：%s' % (r['code'], _errs),
                            '如实说这个标的的日线也没取到（本机没有它的分钟线源，'
                            '日线是降级口径）；不许拿分钟价或编的数字顶上')
        raise ToolError('NO_INTRADAY', '分钟数据没取到：%s' % _errs,
                        '直说分钟数据没取到，不要退化去说日线，更不许编分钟价')
    comp = res.get('composite') or {}
    sig = [{'id': s.get('id'), 'name': s.get('name'), 'period': s.get('period'),
            'side': s.get('side'), 'strength': s.get('strength'),
            'why': str(s.get('why') or '')[:110]}
           for s in (res.get('signals') or [])]
    lv = res.get('levels') or {}
    # ★`data_source` **必须原样带出去**，它不是装饰：分钟引擎拿不到真的分钟线时，
    #   确实会退回一份**合成**帧继续把流程跑完（`LICAI_INTRADAY_SYNTH`），
    #   那时每个价位、每根成交量都是编的。摘要里不留这一项，模型就只能看见
    #   "ok=True + 一堆具体数字" —— 它会照着这些编出来的价位给用户讲短线。
    #   这是本仓库「不得编造」纪律最坏的失效方式：不是没有数据时说不知道，
    #   而是**把造出来的数据当成采集到的**说出去。
    src = res.get('data_source')
    synthetic = is_synthetic(src)
    # ★口径必须跟着读数一起走：`bar_scale='daily'` 表示这份**不是 5/15/30 分钟**，
    #   而是该市场没有分钟线源时的日线降级口径。不说的话，模型会把日线的
    #   `period=1440` 读数当分钟级讲给用户（"5 分钟级别突破"），
    #   而 `price_adjust.mode='none'`（港美股价不复权）与
    #   `last_bar.closed=False`（最后一根 K 还没收盘）都是会改变结论的东西。
    _dbar = res.get('bar_scale') == 'daily'
    sm = {'ok': True, 'name': r['name'], 'code': r['code'], 'market': r['market'],
          'ts': res.get('ts'), 'bars': res.get('bars'), 'regime': res.get('regime'),
          'data_source': src,
          # ★`data_source` 按约定只区分"真/合成"（`is_synthetic` 认它），
          #   具体哪个上游在这里 —— 日线降级时是 `tencent.kline.day`，
          #   而 A 股分钟级那条路的 `data_source` 恒为 `'datasource'`（丢上游）。
          'upstream_source': res.get('upstream_source'),
          'bar_scale': res.get('bar_scale'),
          'price_adjust': res.get('price_adjust'),
          'last_bar': res.get('last_bar'),
          'side': comp.get('side'), 'score': comp.get('score'),
          'confidence': comp.get('confidence'), 'agree': comp.get('agree'),
          'rvol': (res.get('volume') or {}).get('rvol'),
          'divergence': (res.get('volume') or {}).get('divergence'),
          'signal_count': len(sig), 'signals': sig,
          'levels': {k: lv.get(k) for k in ('entry', 'stop', 'target1', 'target2', 'atr', 'rr')},
          'session': res.get('session'),
          'errors': (res.get('errors') or [])[:5],
          'note': '分钟级信号是短周期的，噪音大、可复核性低于日线。'
                  '`agree` 分歧大时不要挑一边说；`confidence` 低就等于没有结论。'
                  '不构成投资建议。'}
    if _dbar:
        # ★这段话不能与上面那条共用：那条说的是"信号是短周期的、噪音大"，
        #   而日线口径的信号**恰好相反**（周期长、噪音小，但只按日更新）。
        #   照抄那条会给模型一个方向相反的暗示。
        sm['note'] = ('★这一份是**日线口径**（该市场在本机没有分钟线源 ⇒ 日线降级），'
                      '不是 5/15/30 分钟：给用户讲的时候**不许说成分钟级**，'
                      '`signals[].period` 是 1440。`periods` 里写了哪几条日内策略'
                      '（开盘区间/VWAP/成交量分布/时段效应/多周期共振）在这个口径下'
                      '不成立。`price_adjust.mode="none"` 表示价不复权（除权跳空会进 '
                      'ATR，止损偏宽）；`last_bar.closed=false` 表示最后一根 K 还没'
                      '收盘、读数还会变。不构成投资建议。')
    elif res.get('last_bar') and res['last_bar'].get('closed') is False:
        sm['note'] = ('⚠ 最后一根 K 未收盘，量价都还会变。\n%s' % sm['note'])
    if synthetic:
        sm['note'] = ('★★这一份**不是真行情**（data_source=%s）：里面的分钟线是'
                      '**合成/占位**的，价位与成交量都是造出来的，只用来验证'
                      '"这条流程通不通"。**一个数字都不许当市场事实说给用户**；'
                      '要么明说"真分钟线这会儿取不到"，要么什么都别说。\n%s'
                      % (src, sm['note']))
    # 信号条数不可控（每个策略一条，且 why 长度不定）—— 超预算就从**尾部**砍条，
    # 砍掉的在 signal_count 里仍然计入（那个数说的是"算出来几条"，不是"摘要放了几条"）。
    while len(json.dumps(sm, ensure_ascii=False).encode()) >= SUMMARY_BUDGET - 200 \
            and len(sm['signals']) > 1:
        sm['signals'].pop()
        sm['shown'] = len(sm['signals'])
    assert_compact(sm, 'analyze_intraday')
    return {'summary': sm,
            'data': {'kind': 'intraday', 'name': r['name'], 'code': r['code'],
                     'market': r['market'], 'ts': res.get('ts'),
                     'bars_n': res.get('bars'), 'regime': res.get('regime'),
                     'data_source': src, 'synthetic': synthetic,
                     'signals': res.get('signals') or [],
                     'composite': comp, 'volume': res.get('volume'),
                     'levels': lv, 'session': res.get('session'),
                     'errors': res.get('errors') or []}}


def scan_linkage(symbol=None, top=10):
    """关联检测：扫全市场找异动，并列出异动标的的关联板块/个股。**重采集、弱分析**。

    ★用户对这个能力的口径原话是「注重采集，弱化分析」：
      `collected`（哪个源、几行、几毫秒、什么错）是**主角**，要如实转述；
      `links` 只给最朴素的一层（涨跌幅、量比、滚动相关、lead-lag），
      **不写论述、不给买卖建议、不预测**。相关性的窗口与样本数必须一起说，
      样本不够时那个数**根本不该出现**（不是"约等于"）。
    """
    mod = _lazy('linkage', '关联检测', need='scan')
    top = max(1, min(int(top or 10), 30))
    code = None
    if symbol:
        code = _need_symbol(symbol)['code']
    try:
        res = mod.scan(scope='all', codes=[code] if code else None, top=top)
    except TypeError:
        # 包可能先落了 scan(scope, top) 这个更窄的签名 —— 那也是合规的冻结接口子集
        res = mod.scan(scope='all', top=top)
    if not res.get('ok'):
        raise ToolError('NO_LINKAGE', '关联检测没跑成：%s' % str(res.get('note') or res.get('err'))[:250],
                        '直说没采到，不要用记忆里的板块关系顶替')
    snap = res.get('snapshot') or {}
    col = res.get('collected') or {}
    anom = [{'code': a.get('code'), 'name': a.get('name'), 'market': a.get('market'),
             'kinds': a.get('kinds'), 'metrics': a.get('metrics')}
            for a in (res.get('anomalies') or [])]
    links = {}
    for c, v in (res.get('links') or {}).items():
        links[c] = {'boards': [{'code': b.get('code'), 'name': b.get('name'),
                                'kind': b.get('kind'), 'pct': b.get('pct')}
                               for b in (v.get('boards') or [])][:8],
                    'peers': [{'code': p.get('code'), 'name': p.get('name'), 'rel': p.get('rel'),
                               'rel_value': p.get('rel_value'), 'pct': p.get('pct')}
                              for p in (v.get('peers') or [])][:8],
                    'evidence': (v.get('evidence') or [])[:6]}
    sm = {'ok': True, 'ts': res.get('ts'),
          'snapshot': {'ts': snap.get('ts'), 'market': snap.get('market'), 'rows': snap.get('rows')},
          # ★`collected` 进摘要的必须是**折叠账**（`sources_brief`），不是逐次清单。
          #   实测撞出来的：逐次清单 62 条 / 4614 字节，整份摘要 11764 字节，
          #   而 `SUMMARY_BUDGET` 是 4000 ⇒ `assert_compact` 抛 SUMMARY_TOO_BIG，
          #   工具返回 `data: None`，**模型一个数都拿不到**。
          #   滑稽的地方在于：模块把该采的全采到了（113 次调用、400 行），
          #   纯死在"汇报格式"上。
          #   ★折叠账折的是**成功**那些（按函数名归并），**失败逐条保留** ——
          #     把失败也折进去，"哪个源、什么错"就丢了，而那正是用户说的
          #     "注重采集"里最要紧的一半。
          #   ★`data` 通道仍然给**逐次全清单**（页面/审计要的就是它）。
          'collected': {'sources': col.get('sources_brief') or {
                            'n_calls': len(col.get('sources') or []),
                            'note': '折叠账没落地，逐次清单在 data 通道'},
                        'ts': col.get('ts'), 'rows': col.get('rows'),
                        'markets': col.get('markets'),
                        # 这三个是"采集这次做成了什么样子"的主干：
                        #   失败了几次、预算用掉多少、省下多少次重复调用。
                        'failed': col.get('failed'), 'budget': col.get('budget'),
                        'memo_hits': col.get('memo_hits'),
                        'skipped_n': len(col.get('skipped') or [])},
          'anomaly_count': len(anom), 'anomalies': anom, 'links': links,
          'note': res.get('note') or ('只列事实：异动标签、涨跌幅、量比、相关性（含窗口与样本数）、'
                                      '以及"谁和它同板块"。不构成投资建议。')}
    _shrink_linkage(sm)
    assert_compact(sm, 'scan_linkage')
    return {'summary': sm,
            'data': {'kind': 'linkage', 'ts': res.get('ts'), 'snapshot': snap,
                     'collected': col, 'anomalies': res.get('anomalies') or [],
                     'links': res.get('links') or {}, 'note': res.get('note')}}


def _shrink_linkage(sm):
    """把 `scan_linkage` 的摘要压进预算，并**如实记下砍了什么**。

    ★为什么不能只 pop `anomalies`（第一版就是这么写的，实测压不动）：
      `links` 才是大头 —— 每个 code 约 1.5KB（boards + peers + evidence 三段），
      而且它**可能比 anomalies 还多**（实测那一轮 anomalies 20 条、links 9 条）。
      实测账：只用折叠账时摘要 7869 字节；anomalies 砍到 1 条仍 6722；
      **把 links 清空才 1711**。⇒ 只砍 anomalies 会砍到一条不剩也进不了预算，
      最后仍然 SummaryTooBig —— 而"砍了却没用"看着像"已经很精简了"。

    ★砍的顺序是有讲究的，不是随便挑，而且**第一版挑错了**（实测抓出来的）：
      links 每条 ≈1.5KB，anomalies 每条 ≈0.25KB —— 差 6 倍。第一版先砍 anomalies，
      结果 `top=6` 时砍到只剩 1 条异动才勉强进预算（`dropped_for_budget=10`）。
      可那时候 **6 条异动 + 1 条链接**（≈3.0KB）明明也放得下，而且信息量大得多：
      榜单是"谁动了"（用户最关心的那一眼），链接是"它跟谁有关"（第二层）。
      ⇒ 先砍**链接**、后砍榜单：
      ① 孤儿链接 —— 榜单上已经没这只票了，留着是让模型读一个它在 `anomalies`
         里问不到的代码，**比没有更坏**。
      ② 有主的链接，**从尾部砍**（留榜首那只的），链接砍到只剩 1 条也够用。
      ③ 最后才砍榜单尾部的异动（它一掉，它的链接下一轮会被 ① 收走）。
      ④ 连折叠账都放不下时，把采集明细摘掉 —— 但**不删 `collected` 这个键**，
         它是这个工具的主角（用户原话："注重采集，弱化分析"）。
    ★砍掉的条数进 `shown`，不静默。用户要的是"采集"，一份**不说自己少了什么**
      的榜单会被当成完整的。

    ★这个次序不是拍的，是按**实测字节**排的：一条完整链接 ≈ 1506 字节
      （`boards` 217 + `peers` 557 + `evidence` 683），一条异动 ≈ 400，
      `collected` 折叠账 ≈ 899。4000 字节的硬预算下"20 条异动 × 完整关联"
      （≈ 3.8 万字节）根本放不下，砍是必然的 —— 所以问题只剩"先砍谁"。
      按 ①②③④ 排出来的是：**采集明细（899）比异动的条数（400）更靠后**，
      即宁可少报几只异动也要留住"这批数是怎么采到的"。这正是用户那句
      「注重采集，弱化分析」落在字节预算上的样子。

    ★为什么要写成"压 → 补话 → 再压"的**收敛循环**，而不是压完再补一句话：
      补进去的那句话本身占字节（约 150）。压到 3799 字节刚好过关、再补一句
      就顶回 3950 —— 离 4000 只剩 50。**在预算边缘上，附言会把结论推翻。**
      所以每轮先把上一轮的附言撤掉（回到 `base_note`）、重新压、再按**当前**
      数字重写附言，直到"字节数达标"和"附言里的数字是真的"同时成立。
      写成"补完就走"的话，附言里的数字会在最后一轮瘦身之后变成假话 ——
      而一个说自己列了 2 条、实际只有 1 条的附言，比不写附言坏得多。
    ★每轮裁剪数是**累加**的（`total += d`），见下面循环里那段注释：
      覆盖会让"第一趟砍到底、第二趟砍不动"这种常态退化成"什么都没砍"。
    """
    def size():
        return len(json.dumps(sm, ensure_ascii=False).encode())

    base_note = sm['note']

    def ladder():
        dropped = 0
        while size() >= SUMMARY_BUDGET - 200:
            shown_codes = {a.get('code') for a in sm['anomalies']}
            orphan = [c for c in sm['links'] if c not in shown_codes]
            keys = list(sm['links'])
            if orphan:
                sm['links'].pop(orphan[-1], None)
            elif len(keys) > 1:
                sm['links'].pop(keys[-1], None)  # 从尾部砍：榜首那只的链接留住
            elif len(sm['anomalies']) > 1:
                sm['anomalies'].pop()
            elif sm['collected'].get('sources') is not None:
                # 最后一级：把明细摘掉、只留一句话 + 那个调用数。
                # ★条件必须是"sources 还不是 None"（**单调**），否则这一级会自己
                #   一遍遍命中自己 —— 每轮 json.dumps 整个 sm，跑满防呆计数才停，
                #   而屏幕上看起来像"在瘦身"。
                n = sm['collected']['sources']
                sm['collected']['sources'] = None
                sm['collected']['sources_note'] = (
                    '预算太小，采集明细放不下（调用数 %s；全清单在 data 通道）'
                    % ((n or {}).get('n_calls') if isinstance(n, dict) else None))
            else:
                break
            dropped += 1
            if dropped > 400:        # 防呆：真压不下去就让 assert_compact 去报错
                break
        return dropped

    total = 0
    for _ in range(6):
        d = ladder()
        # ★`total += d` 而不是 `total = d`：第一趟砍到底是常态，第二趟阶梯就
        #   再也砍不动了、返回 **0**。若用覆盖，那个 0 会把第一趟的裁剪记录
        #   **抹掉** —— `shown.dropped_for_budget` 变成 0（假话），附言也一起消失，
        #   于是 note 里"榜单只留前 20 只"和实际列出的 1 只又打起来了。
        #   实测过：3000 字节预算下正是这个形状（2789 字节、却报 dropped=0）。
        total += d
        # ★上游的 `note` 里有一句"榜单只留前 N 只"（N 是用户要的 top）。砍完之后
        #   `anomalies` 里只剩 1 条，而 note 还写着"前 6 只" —— 两句话在**同一份
        #   摘要里打架**，读的人（模型或人）没法知道该信哪个。所以砍过就补一句，
        #   且只补事实：真正列出来的是几只、完整的东西在哪。
        #   （`anomaly_count` 一直是**砍之前**的总数，那一项没被污染。）
        #   ★每趟都从 `base_note` 重建（不是往上叠），所以附言永远只有一句，
        #     且数字是**这一版**的 —— 叠的话会积成两三条互相打架的附言。
        sm['note'] = base_note
        if total:
            sm['note'] = ('%s\n★摘要通道预算所限：上面只列了 %d 只异动（共 %s 只）、'
                          '%d 只的关联；完整榜单与关联在页面和数据通道里。'
                          % (base_note, len(sm['anomalies']), sm.get('anomaly_count'),
                             len(sm['links'])))
        sm['shown'] = {'anomalies': len(sm['anomalies']), 'links': len(sm['links']),
                       'dropped_for_budget': total}
        if not d or size() < SUMMARY_BUDGET - 200:
            break


def debate(question, deep=False):
    """多智能体博弈：9 个角色分两轮辩论 + 动态权重 + 独立风控官可否决。

    ★运行一次要几十秒（9 个角色各一次大模型调用，深挖模式更久），
    所以**只在用户明确要求"几个分析师讨论一下/辩论/多智能体怎么看"时才调用**，
    不要和 get_kline 之类一起无脑调 —— 那是白烧时间。

    ★风控官有一票否决：`veto` 非空时，最终结论**必须**体现降级或强制风险提示，
    转述时不许把被否决的方向当成结论说出去。
    """
    mod = _lazy('agents', '多智能体博弈', need='run')
    # ★事件**自己收**：`run()` 不把事件放进返回值，它只把事件推给 `emit`
    #   （返回的那个 `emit_report` 是"发了几条 / 修了几条"的**计数**，不是事件本身）,
    #   而原来这里没传 `emit` ⇒ 出口是空操作、`emit_report` 恒为 `None`。
    #   塞一个 list 当出口，data 通道才拿得到真的 `agent_*` 事件。
    events = []
    res = mod.run(question, deep=bool(deep), emit=events.append)
    if not res.get('ok'):
        raise ToolError('NO_DEBATE', '多智能体没跑成：%s' % str(res.get('err') or res.get('msg'))[:250],
                        '直说没跑成，不要自己扮演几个角色编一段辩论')

    # ── 逐键读 `run()` **真正返回**的东西 ─────────────────────────────
    # ★这里原来读的是 `rounds` / `weights` / `weights_basis` / `stances` /
    #   `says` / `events` —— 而 `agents.run()` 收尾那个 return **一个都不返回**
    #   （它给的是 `w` / `views` / `basis` / `score_check` / `n_says` /
    #   `emit_report`，见 `agent/agents/__init__.py` 末尾）。后果**不是报错**：
    #   每个键都取到 `None` / `[]`，于是摘要里的**权重表与 9 条立场永远是空的**，
    #   而 `assert_compact` 只要"没超预算"就放行 ⇒ 大模型读到的是"一场没有任何
    #   观点的会议"；页面上还看不出来（`/agents` 页走的是 SSE 的 `agent_*`
    #   事件流，那条路一直是好的）。2026-09-22 对着 `run()` 的 return 重写。
    verdict = res.get('verdict') or {}
    views = res.get('views') or {}       # {role: {round/stance/conf/text/…}}
    W = res.get('w') or {}               # 权重表：9 个角色全在（不投票的是 0.0）
    basis = res.get('basis') or {}       # 权重依据（完整的那一份）
    # 角色顺序与名字从 `run()` 那个包**现取**；取不到就退回角色 id（**不编名字**）。
    names = getattr(getattr(mod, 'roles', None), 'NAMES', None) or {}
    order = list(getattr(mod, 'ROLES', None) or views.keys())

    rows = []
    for r in order:
        v = views.get(r) or {}
        rows.append({
            'role': r, 'name': names.get(r, r),
            # ★`stance is None` **原样保留**：那是"这个角色不投方向票"（风控官 /
            #   图谱解释官 / 心理顾问），**不是 0 分票** —— 填 0 就是把"没表态"
            #   说成"看平"（`weights.py` 的 docstring 里专门写了这一条）。`conf` 同理。
            'stance': v.get('stance'), 'conf': v.get('conf'),
            'round': v.get('round'), 'weight': W.get(r),
            'text': str(v.get('text') or ''), 'points': v.get('points')})

    sc = basis.get('scenario') or {}
    vol = basis.get('volatility') or {}
    cal = basis.get('calibration') or {}
    cfd = basis.get('confidence') or {}
    rr = basis.get('roles_round') or {}
    # 原话的档位阶梯：★取得细一点，是为了让"刚好装得下"的那一档**离上限更近**
    # （粗档位会让摘要白扔掉几十字节）。阶梯只用来找第一个装得下的档。
    caps = (160, 120, 90, 70, 50, 40, 30, 24, 18, 12)
    sm = {'ok': True, 'question': question,
          # `run()` 里**没有** `rounds` 这个键：真正的轮次在 `basis.roles_round`
          # （每个角色"这条立场来自第几轮"）。取它的最大值 —— 这是**读出来的**，
          # 不是拍的；一个角色都没有时给 `None`，**不填 2**。
          'rounds': max(rr.values()) if rr else None,
          'scenario': res.get('scenario'),
          'weights': W,
          # ★依据只放**要点**：完整的 `basis`（含每人每一步乘数 `steps`、台账路径、
          #   那段"为什么必须收缩置信度"的长注释）有好几 KB，塞进摘要一定爆预算，
          #   而预算要从"每条立场留几个字"里抢。这里只留**作者署名级**的三件事：
          #   场景是什么（以及它是真 intraday 还是占位）、波动落在哪一档、
          #   校准是真在动权重（ledger）还是退回先验（prior）。
          #   每个角色的乘数表与逐步算式在 data 通道 —— note 里写明这件事。
          'weights_basis': {
              'scenario': {'value': sc.get('value'), 'source': sc.get('source')},
              'volatility': {'ann_vol_pct': vol.get('ann_vol_pct'),
                             'bucket': vol.get('bucket')},
              'calibration': {'min_samples': cal.get('min_samples'),
                              'using_ledger': sorted(
                                  [r for r, s in (cal.get('roles') or {}).items()
                                   if (s or {}).get('source') == 'ledger']),
                              'using_prior': sorted(
                                  [r for r, s in (cal.get('roles') or {}).items()
                                   if (s or {}).get('source') != 'ledger'])},
              'confidence_shrink': cfd.get('shrink'),
              'zero_weight_roles': sorted(basis.get('zero_weight_roles') or {}),
              'note': '这是依据的**要点**；每个角色的乘数表与逐步算式在 '
                      'data 通道的 weights_basis 里'},
          'side': verdict.get('side'), 'score': verdict.get('score'),
          'confidence': verdict.get('confidence'),
          'reasons': (verdict.get('reasons') or [])[:6],
          'dissent': (verdict.get('dissent') or [])[:6],
          'veto': verdict.get('veto'),
          'stances': [{'role': r['role'], 'name': r['name'], 'stance': r['stance'],
                       'conf': r['conf'], 'round': r['round'],
                       'text': r['text'][:caps[0]]} for r in rows],
          'risk_note': verdict.get('risk_note') or '不构成投资建议'}

    # ── 瘦身阶梯（★每一步都**照实记账**，记账写进 `note`/`shown`）───────
    # ★台阶的顺序是有讲究的：
    #   ①缩短每个人的原话（9 个角色**都发过言**是这把工具的卖点，"少了两三个人"
    #     比"每个人话说短一点"失真得多）→ ②去掉整条立场（最后手段）
    #   → ③异议的**明细**换成计数 → ④理由删到只剩一条。
    #   ★权重表 / 结论 / `veto` / 风险提示**永远不砍** —— 那几样降级就是另一回事了。
    #   ★③**绝不换成空表**：`_dissent` 的 docstring 专门写过，异议空着会被读成
    #     "没有异议"，而那恰恰是分歧最大的时候 —— 空表是假话，计数不是。
    #   ★这一阶梯是原来没有的：原代码只会在立场那一条上 pop，而 `reasons`/`dissent`
    #     是**真的**从 `verdict` 读出来的（那两把键本来就读对了），所以一份又长又
    #     分裂的会议能让摘要直接超预算、`assert_compact` 当场抛 SUMMARY_TOO_BIG ——
    #     调用方拿到的是一个**报错**而不是一场会议。
    def _size():
        return len(json.dumps(sm, ensure_ascii=False).encode('utf-8'))

    # 这一版的裁剪情况（`_settle` 照着它重述附言）
    st = {'cap': caps[0], 'dropped': [], 'n_dis': None,
          'n_reasons0': len(sm['reasons'])}

    def _settle():
        """把"这一版砍掉了什么"写进 `note`/`shown`，**再**返回此刻的字节数。

        ★必须在**每一次判体量之前**调用：`note` 自己也有好几百字节，先量后写的
          话，写进去的那几百字节就不在判据里 —— 压到 3790 的摘要加上 note 又回到
          4000 以上，`assert_compact` 照样抛。这正是本仓库最反复复发的那个病：
          **量的不是要量的那个量**（本文件第一版就是这么写的，实测把 4301 字节
          的摘要放行了）。
        ★它是**幂等**的：每一趟都从当前状态重述、不看历史，所以反复调用不会
          堆出两三条互相打架的附言。
        """
        cuts = []
        if st['cap'] != caps[0] and sm['stances']:
            cuts.append('每条立场只留了原话前 %d 字' % st['cap'])
        if st['dropped']:
            # ★删了谁必须说出来：只写"共 N 条"的话，读的人不知道**少了谁**，
            #   会以为"风控官也没意见"。（按角色表顺序报，读起来稳定。）
            cuts.append('去掉了 %d 条立场（%s）'
                        % (len(st['dropped']),
                           '、'.join([r for r in order if r in st['dropped']])))
        if st['n_dis'] is not None:
            cuts.append('%d 条异议的明细移到了 data 通道' % st['n_dis'])
        if len(sm['reasons']) < st['n_reasons0']:
            cuts.append('理由从 %d 条删到 %d 条'
                        % (st['n_reasons0'], len(sm['reasons'])))
        if cuts:
            sm['note'] = ('★摘要通道预算所限：%s；权重表、结论、否决与风险提示都是完整的，'
                          '各角色的完整原话与全部明细在 data 通道。' % '；'.join(cuts))
            sm['shown'] = {'stances': len(sm['stances']),
                           'dropped_roles': st['dropped'], 'text_cap': st['cap']}
        return _size()

    # ① 缩短每个人的原话（9 个角色**都发过言**是这把工具的卖点：先缩话、后删人）
    for cap in caps:
        for s, r in zip(sm['stances'], rows):
            s['text'] = r['text'][:cap]
        st['cap'] = cap
        if _settle() < SUMMARY_BUDGET - 200:
            break
    # ② 还放不下 ⇒ 整条删立场（★留至少一条：一条都没有 = 读成"没人发言"）
    while _settle() >= SUMMARY_BUDGET - 200 and len(sm['stances']) > 1:
        st['dropped'].append(sm['stances'].pop()['role'])
    # ③ 还放不下 ⇒ 异议**明细**换成计数。★绝不换成空表：`_dissent` 的 docstring
    #    专门写过，异议空着会被读成"没有异议"，而那恰恰是分歧最大的时候。
    if _settle() >= SUMMARY_BUDGET - 200 and sm['dissent']:
        st['n_dis'] = len(sm['dissent'])
        sm['dissent'] = {'n': st['n_dis'], 'moved_to': 'data 通道',
                         'note': '★预算所限只留计数（**不是没有异议**）'}
    # ④ 最后才动理由（同样留至少一条）
    while _settle() >= SUMMARY_BUDGET - 200 and len(sm['reasons']) > 1:
        sm['reasons'].pop()
    _settle()
    assert_compact(sm, 'debate')
    return {'summary': sm,
            'data': {'kind': 'agents', 'question': question,
                     'rounds': sm['rounds'], 'scenario': res.get('scenario'),
                     'weights': W, 'weights_basis': basis,
                     # data 通道**不截断**：这里放得下原话与每人立场。
                     'stances': rows,
                     'verdict': verdict, 'events': events}}


# ── 注册表 ────────────────────────────────────────────────────────────

REGISTRY = {
    'get_kline': get_kline,
    'get_fundamentals': get_fundamentals,
    'predict_kline': predict_kline,
    'get_news': get_news,
    'search_terms': search_terms,
    'walk_graph': walk_graph,
    'analyze_portfolio': analyze_portfolio,
    'plan_goal': plan_goal,
    'analyze_intraday': analyze_intraday,
    'scan_linkage': scan_linkage,
    'debate': debate,
}

# ★每把工具声明自己 `data` 通道产出的 kind；没有 data 通道的填 None。
#
# 这张表存在的唯一理由，是让"算了但没人收"这件事**可被反推**：
# verify_agent 的 `check_data_events` 遍历它，逐个把 kind 喂进 `llm.data_events`，
# 断言每种声明过的 kind 都真发得出事件 —— 于是"加了第 12 把工具、
# 却忘了在出口登记它的 kind"当场红。
#
# ★它不是 `llm.CHART_KINDS` 的副本，别把它当副本维护：
#   CHART_KINDS 回答"哪些 kind 走 chart 事件"，这张表回答"每把工具实际产什么 kind"。
#   两者**不相等**正是正常的 —— `news`（走 news 事件）和 `terms`（走 terms 事件）
#   都不在 CHART_KINDS 里，可它们各有自己的出口。判据要的也不是"两边相等"，
#   而是"这张表里每一个非 None 的 kind，都能在 data_events 里找到出路"。
DATA_KINDS = {
    'search_terms': 'terms',
    'walk_graph': None,              # 只回一段文本，没有 data 通道
    'get_kline': 'kline',
    'get_fundamentals': 'fundamentals',
    'predict_kline': 'predict',
    'get_news': 'news',
    'analyze_portfolio': 'portfolio',
    'plan_goal': None,               # 只有标量结论，没有 data 通道
    'analyze_intraday': 'intraday',
    'scan_linkage': 'linkage',
    'debate': 'agents',
}

# DeepSeek 走 OpenAI 的 function-calling 协议
SCHEMAS = [    {'type': 'function', 'function': {
        'name': 'search_terms',
        'description': '在理财知识图谱里查术语（255 个词条）。用户问"什么是XX""XX和YY什么区别"'
                       '这类概念问题时用它。只读本地词表，不联网、不耗算力，优先用它。',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': '要查的词，直接给词本身，比如"可转债"'},
            'limit': {'type': 'integer', 'description': '最多返回几条，默认 3'}},
            'required': ['query']}}},
    {'type': 'function', 'function': {
        'name': 'walk_graph',
        'description': '顺着图谱关系走一步：属于（上位概念）/对比（易混概念）/前提（学它之前该先懂什么）。'
                       '用来回答"这跟什么有关""该先学什么"。',
        'parameters': {'type': 'object', 'properties': {
            'term': {'type': 'string', 'description': '词条名'},
            'relation': {'type': 'string', 'enum': ['属于', '对比', '前提'],
                         'description': '不填则返回全部关系'}},
            'required': ['term']}}},
    {'type': 'function', 'function': {
        'name': 'get_kline',
        'description': '取某个标的的历史日 K 数据摘要（最新价、区间涨跌幅、波动率、区间高低点）。'
                       '支持 A 股 600519 / 指数 sh000300 / ETF 510300 / 港股 00700 / 美股 AAPL / 加密 BTC。'
                       '用户问到具体价格的走势时用它。这一步只取数、不跑模型，很快。',
        'parameters': {'type': 'object', 'properties': {
            'symbol': {'type': 'string', 'description': '标的代码或常见名，如 600519、sh000300、00700、AAPL、BTC、茅台'},
            'days': {'type': 'integer', 'description': '取多少根日 K，默认 400'}},
            'required': ['symbol']}}},
    {'type': 'function', 'function': {
        'name': 'get_fundamentals',
        'description': '取 **A 股个股** 的基本面：估值（PE/PB/市值/PEG/历史分位）、'
                       '财务比率（ROE/资产负债率/营收与净利增速/毛利率/流动比率）、'
                       '行业与主营。用户问"这股票贵不贵""基本面怎么样""什么行业""ROE 多少"'
                       '这类问题时用它。只取数、不跑模型，很快，且一小时内有缓存。'
                       '★ETF/指数/港股/美股**没有**这些数据，调用会明确失败 —— 那时不要编数字。',
        'parameters': {'type': 'object', 'properties': {
            'symbol': {'type': 'string',
                       'description': 'A 股个股代码或常见名，如 600519、000001、茅台、比亚迪'}},
            'required': ['symbol']}}},
    {'type': 'function', 'function': {
        'name': 'predict_kline',
        'description': '用 Kronos 小模型预测未来若干根 K 线。★运行一次要几十秒（GPU），'
                       '所以只在用户明确想看"接下来会怎么走"时才调用，不要和 get_kline 一起无脑调。'
                       '返回 N 条条件采样路径的中心线 + 它们的极差区间，不含基本面信息。'
                       '★摘要里的 `spread.withheld=true` 表示**方向弃答**（N 条路径没有系统偏向），'
                       '那时**不许**说涨或跌，要明说模型在这只上没有一致看法。',
        'parameters': {'type': 'object', 'properties': {
            'symbol': {'type': 'string', 'description': '标的代码，同 get_kline'},
            'pred_len': {'type': 'integer', 'description': '预测多少根（5~120），默认 %d' % DEFAULTS['pred_len']},
            # ★这三个数必须与 `DEFAULT_SAMPLES` 一致（验证脚本有判据钉三处相同）：
            #   描述里写"默认 1"而代码默认 9，大模型会按错的默认值判断成本。
            'sample_count': {'type': 'integer',
                             'description': '采几条**独立**路径，默认 %d（1~%d）。'
                                            '条数决定区间：N 条的极差是一个精确的'
                                            '(N-1)/(N+1) 区间 ⇒ %d 条≈80%%。'
                                            '调大更慢，但弃答更少（区间会变窄）'
                                            % (DEFAULT_SAMPLES, MAX_SAMPLES,
                                               DEFAULT_SAMPLES)}},
            'required': ['symbol']}}},
    {'type': 'function', 'function': {
        'name': 'get_news',
        'description': '取资讯/新闻：**只采不析**。用户问"最近有什么消息""XX 的新闻""市场在发生什么"'
                       '时用它。symbol（某只标的的新闻）和 query（某个话题，如"降息"）**至少给一个**，'
                       '都不给会明确失败。返回条目带 ts（可能为空=上游没给时间）、url、'
                       'heat（同题被几个源报道，>1 说明多源在说同一件事，可当热度信号）。'
                       '★degraded 非空=有源挂了但别的源还有货，照实用 items、可顺带提一句哪些源没取到。'
                       '★失败（NO_NEWS）时**直说"新闻我没取到"**，绝不许凭记忆编新闻——'
                       '你的训练数据截止于过去，"你记得的新闻"对今天的用户就是假消息。',
        'parameters': {'type': 'object', 'properties': {
            'symbol': {'type': 'string', 'description': '标的代码或常见名，如 600519、茅台；取它的相关新闻'},
            'query': {'type': 'string', 'description': '话题关键词，如"降息""白酒"；与 symbol 至少给一个'},
            'limit': {'type': 'integer', 'description': '最多返回几条，默认 6'}},
            'required': []}}},
    {'type': 'function', 'function': {
        'name': 'analyze_portfolio',
        'description': '给**用户当前的持仓**做一次体检：集中度（前五大/HHI）、相关性聚集'
                       '（哪几只"名字不同、走势是一条线"）、年化波动与最大回撤、'
                       '有效分散比率 EDR、相对沪深300 的 Beta、以及按 Beta 外推的压力情景。'
                       '用户问"我的组合怎么样""这样配有没有问题""分散了吗"时用它。'
                       '★**不传 holdings 就是体检他真实的组合**（持仓在浏览器里，本站服务端不保存，'
                       '由请求带上来）—— 绝大多数情况就该这样调，不要自己去猜他的持仓。'
                       '只有用户问**假设**（"如果我再加一只茅台呢"）时才用 holdings 传一组假设的，'
                       '传了会在结果里标成"假设组合"。'
                       '★这里只算数、**不给健康总分**（流动性/成本效率本站没有数据源）。',
        'parameters': {'type': 'object', 'properties': {
            'holdings': {'type': 'array', 'description': '**只有做假设时才填**。假设的持仓列表；不填=体检真实持仓',
                         'items': {'type': 'object', 'properties': {
                             'symbol': {'type': 'string', 'description': '标的代码，同 get_kline'},
                             'weight': {'type': 'number', 'description': '占组合的百分比，如 30 表示 30%'}},
                             'required': ['symbol']}},
            'risk': {'type': 'string', 'description': '用户自述的风险偏好，如"稳健"；只用于措辞，不改变任何计算'}},
        }}},
    {'type': 'function', 'function': {
        'name': 'plan_goal',
        'description': '算"按他每月能投的钱、打算放的年限，攒到目标金额的概率"。'
                       '用户问"我这样攒够 X 万要多久""能达到吗""每月投 Y 够不够"时用它。'
                       '★它把**历史上真实发生过的月份重排**很多遍来数概率，'
                       '**不是预测**，也不是拿模型外推——历史分布不等于未来分布，'
                       '回答时必须原样说出这句。'
                       '★它推的是**沪深300**的历史（`proxy` 字段），**不是他手里那个组合**，'
                       '说的时候必须点明这个差别。'
                       '★概率是**两端一对**（画像档位的两端各算一次），**没有中点**：'
                       '报告时要把这个区间一起给出来，不要自己取中间一个数。'
                       '★没填过画像就会失败（`NO_PROFILE`）——那时让他先去填，**不要**替他假设金额。',
        'parameters': {'type': 'object', 'properties': {
            'target': {'type': 'number', 'description': '想攒到的金额（元），如 500000'},
            'symbol': {'type': 'string',
                       'description': '用哪个标的的历史来推。默认沪深300（大盘，最保守的选择）；'
                                      '只有当用户明确说"按某只基金/某只股票算"时才填'},
            'monthly': {'type': 'string', 'description': '**只有做假设时才填**。假设的每月可投入档位，'
                                                         '取值："1000以下"/"1000-3000"/"3000-1万"/"1万以上"。'
                                                         '不填=用他画像里自己选的那一档'},
            'horizon': {'type': 'string', 'description': '**只有做假设时才填**。假设的期限档位，'
                                                         '取值："1年以内"/"1-3年"/"3-5年"/"5年以上"'},
            'assets': {'type': 'string', 'description': '**只有做假设时才填**。假设的现有可投资资产档位，'
                                                        '取值："1万以下"/"1-5万"/"5-20万"/"20-50万"/'
                                                        '"50-100万"/"100万以上"'}},
            'required': ['target']}}},
    {'type': 'function', 'function': {
        'name': 'analyze_intraday',
        'description': '分钟级量价分析（5/15/30 分钟）：开盘区间突破、VWAP 偏离、相对成交量 RVOL、'
                       'Volume Profile（POC/价值区）、量价背离、短周期动量、布林挤压、多周期共振、'
                       'ATR 止损目标位、时段效应。用户问"今天盘中怎么样""短线有没有机会"'
                       '"5 分钟/15 分钟/30 分钟上是什么形态""量能如何"时用它。'
                       '只取数+算指标，不跑模型，较快。'
                       '★`agree` 分歧大时不要挑一边说；`confidence` 低就等于没有结论。'
                       '★数据不够的策略会整条不出现并写进 `errors` —— 不许用"技术面偏强"填空。'
                       '★先看 `data_source`：**不是 `datasource` 开头就说明这一帧的分钟线是'
                       '合成/占位的（价位与成交量都是造出来的）**，那种情况下一个数字都不许'
                       '当市场事实说，要么明说取不到真分钟线，要么什么都别说。',
        'parameters': {'type': 'object', 'properties': {
            'symbol': {'type': 'string', 'description': '标的代码或常见名，同 get_kline'},
            'periods': {'type': 'array', 'items': {'type': 'integer'},
                        'description': '要哪几个周期，取值 1/5/15/30/60，默认 [5,15,30]'}},
            'required': ['symbol']}}},
    {'type': 'function', 'function': {
        'name': 'scan_linkage',
        'description': '关联检测：扫全市场找**异动**（量比/涨跌幅的 z-score 离群），'
                       '并列出异动标的所属板块与同板块/高相关的其它个股。'
                       '用户问"今天什么在异动""XX 异动了，跟它相关的是什么情况"'
                       '"哪些板块一起动了"时用它。'
                       '★这个能力的口径是「注重采集、弱化分析」：`collected`（哪个源、几行、'
                       '几毫秒、什么错）是主角，要如实转述；`links` 只有最朴素的一层'
                       '（涨跌幅、量比、滚动相关含窗口与样本数、同板块），'
                       '**不写论述、不给买卖建议、不做预测**。'
                       '★**慢**：默认 `top=10` 在真源上要几十秒（它要挨个调板块成员、'
                       '再对每只异动做高相关深挖）。用户催的话把 `top` 说小一点，'
                       '别在别的工具旁边无脑并调。'
                       '★摘要放不下时会从尾部砍（`shown.dropped_for_budget` 是砍掉的条数），'
                       '**完整榜单在数据通道和页面上** —— 转述时按摘要里真有的条数说，'
                       '不许把榜单说成完整的。',
        'parameters': {'type': 'object', 'properties': {
            'symbol': {'type': 'string',
                       'description': '只看这一只的关联。不填=扫全市场出异动榜'},
            'top': {'type': 'integer', 'description': '异动榜取前几条，默认 10（1~30）'}},
            'required': []}}},
    {'type': 'function', 'function': {
        'name': 'debate',
        'description': '召集 9 个智能体开会博弈（多头/空头/基本面/技术/舆情/独立风控/投资经理/'
                       '知识图谱导师/投资心理），两轮辩论 + 动态权重 + 风控官可否决。'
                       '**只在用户明确要求多人讨论时用它** —— 比如"让几个分析师辩论一下"'
                       '"多智能体怎么看""开个会讨论下"。'
                       '★很慢（几十秒，9 个角色各一次大模型调用），不要和别的工具一起无脑调。'
                       '★`veto` 非空 = 风控官否决了那个方向，**转述时必须体现降级或风险提示**，'
                       '不许把被否决的方向当结论说。',
        'parameters': {'type': 'object', 'properties': {
            'question': {'type': 'string', 'description': '要他们讨论的问题，用用户的原话'},
            'deep': {'type': 'boolean', 'description': '深挖模式，辩论轮次更多也更慢，默认 false'}},
            'required': ['question']}}},
]


def call(name, args):
    """按名字派发。未知工具/参数错误都变成结构化的失败摘要，不抛给上层。"""
    fn = REGISTRY.get(name)
    if fn is None:
        return {'summary': {'ok': False, 'error': 'NO_SUCH_TOOL',
                            'msg': '没有叫 %s 的工具' % name}, 'data': None}
    try:
        return fn(**(args or {}))
    except ToolError as e:
        return {'summary': {'ok': False, 'error': e.code, 'msg': str(e), 'hint': e.hint},
                'data': None}
    except TypeError as e:                       # 参数名/个数不对
        return {'summary': {'ok': False, 'error': 'BAD_ARGS', 'msg': str(e),
                            'hint': '参数名要和 schema 一致'}, 'data': None}
    except Exception as e:                       # noqa: BLE001
        return {'summary': {'ok': False, 'error': type(e).__name__, 'msg': str(e)[:300]},
                'data': None}


if __name__ == '__main__':
    import sys
    print(json.dumps(ENGINE.status(), ensure_ascii=False))
    for nm, a in [('search_terms', {'query': '什么是可转债'}),
                  ('walk_graph', {'term': '可转债', 'relation': '对比'}),
                  ('get_kline', {'symbol': '600519', 'days': 120}),
                  ('get_fundamentals', {'symbol': '600519'}),
                  ('get_fundamentals', {'symbol': '510300'})]:
        r = call(nm, a)
        print('\n── %s%s → %s' % (nm, a, json.dumps(r['summary'], ensure_ascii=False)[:400]))
    if '--predict' in sys.argv:
        r = call('predict_kline', {'symbol': '600519', 'pred_len': 20})
        print('\n── predict → %s' % json.dumps(r['summary'], ensure_ascii=False))
        print('   data.pred 行数 =', len(r['data']['pred']['t']) if r['data'] else 0)
