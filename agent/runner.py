"""Kronos 推理引擎：进程内单例 + 懒加载 + 全局锁 + 结果缓存。

三件事值得先说明白：

1. **顶层不 import torch / model**。Flask 是多线程的，但 torch 模型不是线程安全的；
   更要紧的是「装不上 torch 时页面还能不能打开」—— 所以重活全推迟到 `load()` 里，
   失败了也只是 `status()['ok']=False`，页面照常出，聊天降级成「只答概念」。

2. **逐根进度是真的**，不是估算：上游 `auto_regressive_inference` 里写的是
   `ran = trange if verbose else range`，`trange` 是模块级全局名。我们把它临时换成
   自己的生成器，就能每生成一根回调一次。★这是**打在上游内部**的补丁 —— 上游这份代码
   是 vendored 的（`agent/kronos/UPSTREAM.json` 记了哈希），换版本时这段要重新核对。

3. **缓存按「数据末根时间 + 全部采样参数」做键**。同一只票同一天问两遍不该跑两遍 ——
   一次推理是分钟级的，缓存命中与否直接决定这个页面能不能用。
"""

import hashlib
import json
import os
import sys
import threading
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KRONOS_SRC = os.path.join(HERE, 'kronos')          # vendored：里面是 model/ 包
MODEL_DIR = os.path.join(ROOT, 'models', 'Kronos-small')
TOKENIZER_DIR = os.path.join(ROOT, 'models', 'Kronos-Tokenizer-base')
MAX_CONTEXT = 512                                   # Kronos-small/base 的硬上限

# 采样参数默认值。pred_len 由 bench 结果决定（见 kronos_perf.json）。
DEFAULTS = dict(lookback=400, pred_len=30, T=1.0, top_k=0, top_p=0.9, sample_count=1)


class EngineError(RuntimeError):
    def __init__(self, code, msg, hint=''):
        super().__init__(msg)
        self.code, self.hint = code, hint


class KronosEngine:
    def __init__(self):
        self._predictor = None
        self._load_lock = threading.Lock()
        self._run_lock = threading.Lock()            # 推理串行化：模型非线程安全 + 显存
        self._cache = {}
        self._cache_fix = {}               # 与 _cache 同键：那次预测对包络的修正量
        self._cache_order = []
        self.device = None
        self.load_error = None
        self.last_ms = None
        self.last_fix = None               # 最近一次对模型输出的包络修正（见 _repair_ohlc）
        self.runs = 0
        self.cache_hits = 0
        # 并发峰值：验证脚本用它来证明锁真的在起作用（去掉锁的话这里会 >1）
        self.concurrent = 0
        self.concurrent_peak = 0
        self._probe()

    def _probe(self):
        """只读地看一眼环境，不加载模型。"""
        try:
            import torch
            self.torch_version = torch.__version__
            self.cuda = bool(torch.cuda.is_available())
            try:
                self.gpu = torch.cuda.get_device_name(0) if self.cuda else None
            except Exception:                        # noqa: BLE001
                self.gpu = None
        except Exception as e:                       # noqa: BLE001
            self.torch_version, self.cuda, self.gpu = None, False, None
            self.load_error = 'torch 不可用：%s' % e

    # ── 加载 ──────────────────────────────────────────────────────────

    def load(self):
        """幂等。返回 predictor；失败抛 EngineError（调用方负责翻译成人话）。"""
        if self._predictor is not None:
            return self._predictor
        with self._load_lock:
            if self._predictor is not None:
                return self._predictor
            if self.load_error and self.torch_version is None:
                raise EngineError('NO_TORCH', self.load_error, '在 .venv 里跑：./run.sh')
            if KRONOS_SRC not in sys.path:
                sys.path.insert(0, KRONOS_SRC)
            try:
                from model import Kronos, KronosTokenizer, KronosPredictor
            except Exception as e:                   # noqa: BLE001
                raise EngineError('IMPORT_FAILED', 'Kronos 上游代码 import 失败：%s' % e,
                                  '检查 agent/kronos/model/ 三个文件是否齐全、einops 是否装上') from e
            t0 = time.time()
            try:
                tok = KronosTokenizer.from_pretrained(TOKENIZER_DIR)
                mdl = Kronos.from_pretrained(MODEL_DIR)
            except Exception as e:                   # noqa: BLE001
                # ★兜底：绕开 huggingface_hub 的 mixin，直接从 config.json + safetensors 构造。
                #   上游 pin 的 hf_hub 是 0.33.1，本机别处是 1.27 —— 这条路径保证「版本不匹配」
                #   不会变成「整个功能不可用」。
                try:
                    tok, mdl = _load_without_hub(Kronos, KronosTokenizer)
                except Exception as e2:              # noqa: BLE001
                    raise EngineError('LOAD_FAILED',
                                      '权重加载失败：%s / 兜底路径也失败：%s' % (e, e2)) from e2
            try:
                self._predictor = KronosPredictor(mdl, tok, device=None, max_context=MAX_CONTEXT)
            except Exception as e:                   # noqa: BLE001
                raise EngineError('LOAD_FAILED', 'KronosPredictor 构造失败：%s' % e) from e
            self.device = self._predictor.device
            self.load_ms = int((time.time() - t0) * 1000)
            self.load_error = None
            return self._predictor

    def warmup(self, progress=None):
        """预热：拿一段合成 K 线跑最小的一次，把首次的编译/分配成本挪到启动时。"""
        try:
            df, xts, yts = synthetic_ohlcv(lookback=120, pred_len=4)
            self.predict(df, xts, yts, pred_len=4, sample_count=1, progress=progress, use_cache=False)
            return True
        except Exception as e:                       # noqa: BLE001
            self.load_error = '预热失败：%s' % e
            return False

    # ── 推理 ──────────────────────────────────────────────────────────

    def predict(self, df, x_ts, y_ts, pred_len, T=1.0, top_k=0, top_p=0.9,
                sample_count=1, progress=None, use_cache=True, cache_key=None):
        """返回 (pred_df, info)。info 里带 cached / ms。"""
        key = cache_key or _key(df, pred_len, T, top_k, top_p, sample_count)
        if use_cache and key in self._cache:
            self.cache_hits += 1
            return self._cache[key], {'cached': True, 'ms': 0,
                                      'ohlc_fix': self._cache_fix.get(key)}
        pred = self.load()
        with self._run_lock:
            if use_cache and key in self._cache:      # 等锁期间别人已经算出来了
                self.cache_hits += 1
                return self._cache[key], {'cached': True, 'ms': 0,
                                          'ohlc_fix': self._cache_fix.get(key)}
            self.concurrent += 1
            self.concurrent_peak = max(self.concurrent_peak, self.concurrent)
            t0 = time.time()
            try:
                _patch_trange(progress)
                out = pred.predict(df=df, x_timestamp=x_ts, y_timestamp=y_ts, pred_len=pred_len,
                                   T=T, top_k=top_k, top_p=top_p, sample_count=sample_count,
                                   verbose=False)
            except Exception as e:                   # noqa: BLE001
                raise EngineError('PREDICT_FAILED', '推理失败：%s' % e) from e
            finally:
                _patch_trange(None)
                self.concurrent -= 1
            ms = int((time.time() - t0) * 1000)
            self.last_ms, self.runs = ms, self.runs + 1
        # ★先修包络，再断言 —— 顺序不能反。
        #   实测（2026-09-18，RTX 4060 / torch 2.14.0+cu130）：Kronos 吐出来的
        #   high/low **本来就不保证自洽**，8 根里有 2 根 high 低于 max(open,close)，
        #   最深的一根低了 0.46（约 0.63%）。原因是它把 6 个通道**各自独立**自回归采样，
        #   没有任何机制约束 high ≥ max(open,close)。上游 `kronos.py:558` 也只做
        #   z-score 反归一化就把 DataFrame 返回了 —— **上游根本没有这一步**。
        #   所以这不是"模型坏了"，是"我们之前把模型的输出当成了合法 K 线"。
        out, fix = _repair_ohlc(out)
        self.last_fix = fix
        _sanity(out, pred_len)                 # 断言现在验的是**要发出去的那份**
        if use_cache:
            self._cache[key] = out
            self._cache_fix[key] = fix
            self._cache_order.append(key)
            while len(self._cache_order) > 24:        # 一个预测就是几百 KB，别无限涨
                old = self._cache_order.pop(0)
                self._cache.pop(old, None)
                self._cache_fix.pop(old, None)
        return out, {'cached': False, 'ms': ms, 'ohlc_fix': fix}

    def sample_paths(self, df, x_ts, y_ts, pred_len, n=1, progress=None,
                     T=1.0, top_k=0, top_p=0.9):
        """**N 条各自独立的采样路径**，返回 `(paths, info)`。

        这是阶段 3 的地基：有了个体路径才有区间和分歧度可谈。

        ★ 为什么是「N 次 sample_count=1」而不是「一次 sample_count=N」：
          上游 `auto_regressive_inference` 的收尾是
              z = z.reshape(-1, sample_count, ...); preds = np.mean(preds, axis=1)
          （`kronos.py:465-467`）—— **N 条路径当场被平均掉，个体路径拿不回来**。
          而 `agent/kronos/UPSTREAM.json` 明说这个目录装的是"上游原样代码"、记哈希
          就是为了"换版本时知道拿哪份做 diff"。为了一个功能去改它，那份声明就假了，
          哈希也就不再能当 diff 的锚点。所以这里宁可多花一点时间，也不动 vendored 文件。

        ★ 代价是实测过的，不是估的（kronos_perf.json，RTX 4060 / pred_len=30）：
          批量 3 条 533ms，单条 210ms ⇒ 批量化只省下约 15%（线性本该是 630ms）。
          也就是说**编码上下文的开销远小于自回归那 30 步**，所以 N 次独立调用
          几乎不比批量贵。这条结论换机器要重新量（bench_kronos.py）。

        ★★ `use_cache=False` 是**硬要求**，不是省事：缓存键（`_key`）里**没有**
           采样序号，只有"数据末根 + 全部采样参数"。开着缓存跑 N 次，第 2…N 次
           全部命中同一条 → N 条路径**完全相同** → 区间宽度 0、分歧度 0，
           而页面上会显示成"模型非常确定"。这是**假绿里最坏的一种**：
           它不报错、不报空，报的是一个看起来最好看的结论。
        """
        n = max(1, int(n or 1))
        paths, ms_total = [], 0
        fix_total = {'bars': 0, 'high_clamped': 0, 'low_clamped': 0,
                     'flipped': 0, 'max_clamp': 0.0, 'max_clamp_pct': 0.0}
        hi_set, lo_set, flip_set = set(), set(), set()
        for k in range(n):
            cb = None
            if progress is not None:
                # 进度要**跨路径累加**：每条路径内部都从 1 数到 pred_len，
                # 原样透传的话进度条会走满 n 次，用户看到的是"快好了…又从头开始"。
                def cb(i, _nn, _k=k, _n=n):              # noqa: E306
                    progress(_k * pred_len + i, _n * pred_len)
            out, info = self.predict(df, x_ts, y_ts, pred_len=pred_len, T=T,
                                     top_k=top_k, top_p=top_p, sample_count=1,
                                     progress=cb, use_cache=False)
            paths.append(out)
            ms_total += int(info.get('ms') or 0)
            f = info.get('ohlc_fix') or {}
            # ★「修了几根」在 N 条路径上**按根取并集**，不是把计数加起来。
            #   相加会得到 45 根这种数（9×几条路径各修十几根），而一段 30 根的
            #   预测里不可能有 45 根 —— 那个数自相矛盾，却和"算出来的"长得一样。
            #   并集同时更贴原意：一根是"被修过"还是"没被修过"，与几条路径碰过它无关。
            hi_set.update(f.get('hi_idx') or [])
            lo_set.update(f.get('lo_idx') or [])
            flip_set.update(f.get('flip_idx') or [])
            fix_total['bars'] = max(fix_total['bars'], int(f.get('bars') or 0))
            fix_total['max_clamp'] = max(fix_total['max_clamp'],
                                         float(f.get('max_clamp') or 0.0))
            fix_total['max_clamp_pct'] = max(fix_total['max_clamp_pct'],
                                             float(f.get('max_clamp_pct') or 0.0))
        fix_total['high_clamped'] = len(hi_set)
        fix_total['low_clamped'] = len(lo_set)
        fix_total['flipped'] = len(flip_set)
        # `touched` = **被动过的根数**（上包络与下包络取并集）。工具摘要报的是它，
        # 而不是 `high_clamped + low_clamped` —— 后者在一根同时修了上下包络时会
        # 数两次，于是"根数"可能超过预测段本身的根数。
        fix_total['touched'] = len(hi_set | lo_set)
        fix_total['hi_idx'] = sorted(hi_set)
        fix_total['lo_idx'] = sorted(lo_set)
        fix_total['flip_idx'] = sorted(flip_set)
        return paths, {'ms': ms_total, 'cached': False, 'ohlc_fix': fix_total,
                       'n_paths': len(paths)}

    def status(self):
        return {
            'ok': self._predictor is not None and self.load_error is None,
            'loaded': self._predictor is not None,
            'device': str(self.device) if self.device else None,
            'torch': self.torch_version, 'cuda': self.cuda, 'gpu': self.gpu,
            'model': 'Kronos-small (24.7M)', 'max_context': MAX_CONTEXT,
            'runs': self.runs, 'cache_hits': self.cache_hits,
            'last_ms': self.last_ms,
            'last_fix': self.last_fix,
            'concurrent_peak': self.concurrent_peak,
            'error': self.load_error,
        }


def _repair_ohlc(pred):
    """把模型吐出的 OHLC 包络修成合法的 K 线，**并把修了多少如实报出来**。

    只动 `high`/`low` 两个通道，**不碰 open/close** —— 因为信息在开收价那条路径上，
    high/low 才是那个不可靠的（见 `predict()` 里的实测记录）。
        high ← max(high, open, close)
        low  ← min(low,  open, close)
    再兜一道 high ≥ low（模型偶尔会给出 high < low，那是彻底翻转的包络）。

    ★ 为什么不干脆放宽 `_sanity` 了事：一根 high 低于实体的 K 线**画出来就是错的**
    （影线穿进实体里），前端会渲染出一个视觉上不存在的形状。要么修，要么不画。
    ★ 为什么不悄悄修：这是**对模型输出的改动**，改了就必须留下痕迹 ——
    返回的 stats 会一路带到工具摘要和验证脚本里，页面上也会写明。
    """
    if pred is None or len(pred) == 0:
        return pred, {'bars': 0, 'high_clamped': 0, 'low_clamped': 0, 'flipped': 0,
                      'max_clamp': 0.0, 'max_clamp_pct': 0.0,
                      'hi_idx': [], 'lo_idx': [], 'flip_idx': []}
    import numpy as np                     # 本文件一贯按需 import（见 synthetic_ohlcv）
    out = pred.copy()
    o = out['open'].astype(float)
    c = out['close'].astype(float)
    h = out['high'].astype(float)
    l = out['low'].astype(float)

    hi_need = np.maximum(o, c)
    lo_need = np.minimum(o, c)
    d_hi = h - hi_need
    d_lo = lo_need - l
    touched_hi = d_hi < 0
    touched_lo = d_lo < 0
    flipped = (h - l) < 0

    new_h = np.maximum(np.maximum(h, hi_need), np.minimum(h, l))
    new_l = np.minimum(np.minimum(l, lo_need), np.maximum(h, l))
    out['high'] = np.maximum(new_h, new_l)
    out['low'] = np.minimum(new_h, new_l)

    clamp = np.concatenate([np.abs(d_hi[touched_hi]), np.abs(d_lo[touched_lo])])
    denom = float(np.mean(np.abs(c))) or 1.0
    stats = {
        'bars': int(len(out)),
        'high_clamped': int(touched_hi.sum()),
        'low_clamped': int(touched_lo.sum()),
        'flipped': int(flipped.sum()),
        'max_clamp': round(float(clamp.max()), 6) if clamp.size else 0.0,
        'max_clamp_pct': round(float(clamp.max()) / denom * 100, 4) if clamp.size else 0.0,
        # ★逐根下标（多采样时要用，见 `sample_paths`）：N 条路径各自修了十几根，
        #   直接把计数**相加**会得到 45 这种数 —— 而一段 30 根的预测里不可能有
        #   45 根。两个数都是"算出来的"，只有下标能告诉你它们指的是同一批根。
        #   它们是普通 list[int]，照样能进 `status()`（那里是 JSON）。
        'hi_idx': [int(i) for i in np.nonzero(touched_hi)[0]],
        'lo_idx': [int(i) for i in np.nonzero(touched_lo)[0]],
        'flip_idx': [int(i) for i in np.nonzero(flipped)[0]],
    }
    return out, stats


def _sanity(pred, pred_len):
    """★预测结果必须自洽，否则宁可报错也别把一张假图送到用户眼前。"""
    if pred is None or len(pred) != pred_len:
        raise EngineError('BAD_OUTPUT', '预测行数 %s ≠ 请求的 %s'
                          % (len(pred) if pred is not None else None, pred_len))
    if pred[['open', 'high', 'low', 'close']].isnull().values.any():
        raise EngineError('BAD_OUTPUT', '预测结果里出现 NaN（模型输出异常）')
    if (pred['high'] < pred[['open', 'close']].max(axis=1) - 1e-6).any():
        raise EngineError('BAD_OUTPUT', '预测的 high 低于 open/close，K 线不自洽')
    if (pred['low'] > pred[['open', 'close']].min(axis=1) + 1e-6).any():
        raise EngineError('BAD_OUTPUT', '预测的 low 高于 open/close，K 线不自洽')


def _key(df, pred_len, T, top_k, top_p, sample_count):
    tail = df.tail(1)
    stamp = '%s|%.6f' % (tail['dt'].iloc[0], float(tail['close'].iloc[0]))
    raw = '%s|%d|%s|%s|%.4f|%.4f|%d|%d' % (stamp, len(df), T, top_k, top_p, sample_count,
                                            pred_len, int(df['close'].sum() * 100))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ── 逐根进度：替换上游模块里的 trange ────────────────────────────────────

def _patch_trange(cb):
    """cb=None 表示还原。上游 `for i in ran(pred_len)`，所以给个生成器就够。"""
    try:
        import model.kronos as k
    except Exception:                                # noqa: BLE001
        return
    if cb is None:
        if hasattr(k, '_orig_trange'):
            k.trange = k._orig_trange
        return
    if not hasattr(k, '_orig_trange'):
        k._orig_trange = k.trange
    total = {'n': 0}

    def _gen(n):
        for i in range(n):
            yield i
            total['n'] = i + 1
            try:
                cb(i + 1, n)
            except Exception:                        # noqa: BLE001
                pass                                 # 进度回调坏了不能弄死推理
    k.trange = _gen


def _load_without_hub(Kronos, KronosTokenizer):
    """绕开 huggingface_hub：直接读 config.json + safetensors 构造。

    上游两个类都继承 PyTorchModelHubMixin，`from_pretrained` 只是「读 config → cls(**cfg)
    → load_state_dict」。这里把那三步手写一遍，就与 hf_hub 的版本彻底无关了。
    """
    import json as _json
    from safetensors.torch import load_file

    def build(cls, d):
        with open(os.path.join(d, 'config.json'), encoding='utf-8') as f:
            cfg = _json.load(f)
        m = cls(**cfg)
        m.load_state_dict(load_file(os.path.join(d, 'model.safetensors')))
        m.eval()
        return m
    return build(KronosTokenizer, TOKENIZER_DIR), build(Kronos, MODEL_DIR)


def synthetic_ohlcv(lookback=400, pred_len=30, seed=7, start='2023-01-02'):
    """合成一段几何随机游走 K 线，给预热和验证脚本用（不需要联网取真数据）。"""
    import numpy as np
    rng = np.random.default_rng(seed)
    n = lookback + pred_len
    ret = rng.normal(0, 0.012, n)
    close = 100 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[close[0]], close[:-1]])
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    vol = np.abs(rng.normal(1e6, 2e5, n))
    dts = pd.bdate_range(start, periods=n)
    df = pd.DataFrame({'dt': dts, 'open': open_, 'high': hi, 'low': lo,
                       'close': close, 'volume': vol, 'amount': vol * close})
    return (df.iloc[:lookback].reset_index(drop=True),
            pd.Series(dts[:lookback]), pd.Series(dts[lookback:lookback + pred_len]))


ENGINE = KronosEngine()


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--pred-len', type=int, default=DEFAULTS['pred_len'])
    ap.add_argument('--lookback', type=int, default=DEFAULTS['lookback'])
    ap.add_argument('--samples', type=int, default=1)
    a = ap.parse_args()
    print('环境：', json.dumps(ENGINE.status(), ensure_ascii=False))
    t0 = time.time()
    try:
        ENGINE.load()
        print('加载耗时 %dms，device=%s' % (int((time.time() - t0) * 1000), ENGINE.device))
    except EngineError as e:
        print('加载失败 [%s] %s\n  → %s' % (e.code, e, e.hint))
        sys.exit(1)
    df, xts, yts = synthetic_ohlcv(lookback=a.lookback, pred_len=a.pred_len)
    bar = []
    out, info = ENGINE.predict(df, xts, yts, pred_len=a.pred_len, sample_count=a.samples,
                               progress=lambda i, n: bar.append(i),
                               use_cache=False)
    print('pred_len=%d lookback=%d samples=%d → %dms（%d 根进度回调）'
          % (a.pred_len, a.lookback, a.samples, info['ms'], len(bar)))
    print(out.tail(3).to_string())
