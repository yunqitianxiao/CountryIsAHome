"""层1.6：**adata.sentiment** 的情绪/热度类源（本机已装 adata 2.9.5）。

为什么单开一层而不是塞进 akshare：这几个接口在 `adata` 里，而 akshare 的同类
（`stock_hot_rank_em`、`stock_hot_follow_xq`）实测**已经挂了**（见交付说明的可用性
清单）—— 同花顺/东财的人气榜要从 adata 走。

★这一层给的是**热度榜/龙虎榜**，不是"新闻"。它们进同一个 item 形状是为了能参与
  跨源共振计数：一只票同时出现在同花顺热度榜、东财人气榜和微博舆情里，
  那是 3 个源在说同一件事 —— 这正是 `heat` 要量的东西。
  ★所以它们的标题是**事实串**（`哈药股份(600664) 人气榜第1 涨跌+10.06%`），
    每个数都来自上游字段，一个都不编。

★两个不同的"热度"，别混（这是本模块最容易做歪的地方）：
    item['heat']      = **我们数的**：同一事件被几个源报道（多源共振）
    item['hot_value'] = **上游给的**：该榜自己的热度值（同花顺 482886.0 那种）
    item['rank']      = **上游给的**：榜内名次
  三个字段各自带来源，谁也不许顶替谁 —— 把上游热度值填进 `heat` 会让
  "多源共振"这个信号凭空变大，且事后没人分得清。

★时间戳：榜单是**快照**，上游不给条目时间。这里 `ts` = 采集时刻 + `ts_kind='collected'`
  （见 `http_src` 的同款约定）。绝不假装那是"事件发生时间"。
"""
import time

import feed


def _ts_now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _tags(*vals):
    """上游标签 → 标签列表。`concept_tag` 是 '民营医院;维生素' 这种分号串。"""
    out = []
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if not s or s.lower() in ('nan', 'none'):
            continue
        for part in s.replace('；', ';').replace(',', ';').split(';'):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def _item(src, title, excerpt, tags=None, related=None, rank=None, hot_value=None):
    return {
        'title': str(title or '')[:120],
        'excerpt': str(excerpt or '')[:300],
        'source': src.get('id'),
        'source_label': src.get('label') or src.get('id'),
        'ts': _ts_now(),                     # 采集时刻，不是事件时刻
        'ts_kind': 'collected',
        'url': None,                         # 榜单没有逐条详情页 —— 不拼假链接
        'tags': tags or [],
        'related': related or [],
        # ★这一层有 `rank`/`hot_value`（上游给的热度），但**没有情绪分值** ——
        #   榜单不表达情绪。所以 sentiment_raw 恒 None：热度 ≠ 情绪，
        #   拿 hot_value 填 sentiment_raw 就是把"多热闹"冒充成"多看好"。
        'sentiment_raw': None,
        'rank': feed._num(rank, 0),
        'hot_value': feed._num(hot_value, 2),
    }


def _pct(v):
    v = feed._num(v, 2)
    return '—' if v is None else ('%+.2f%%' % v)


# ── 解析器（按**名字**取列，不按位置）──────────────────────────────────

def _a_ths_hot100(rows, src):
    """同花顺热股 100。实测 2026-09-22 本机：263ms / 100 行。
    列：rank, stock_code, short_name, change_pct, hot_value, pop_tag, concept_tag。"""
    out = []
    for r in rows:
        nm, code = r.get('short_name'), r.get('stock_code')
        tags = _tags(r.get('concept_tag'), r.get('pop_tag'))
        out.append(_item(
            src,
            '%s(%s) 同花顺热股第%s 涨跌%s' % (nm, code, r.get('rank'), _pct(r.get('change_pct'))),
            '人气标签：%s；概念：%s' % (r.get('pop_tag') or '—', r.get('concept_tag') or '—'),
            tags=tags, related=[{'code': code, 'name': nm}],
            rank=r.get('rank'), hot_value=r.get('hot_value')))
    return out


def _a_em_pop100(rows, src):
    """东方财富人气榜 100。实测 2026-09-22 本机：395ms / 100 行。
    列：rank, stock_code, short_name, price, change, change_pct。"""
    out = []
    for r in rows:
        nm, code = r.get('short_name'), r.get('stock_code')
        out.append(_item(
            src,
            '%s(%s) 东财人气榜第%s 涨跌%s' % (nm, code, r.get('rank'), _pct(r.get('change_pct'))),
            '最新价 %s' % (feed._num(r.get('price'), 2),),
            related=[{'code': code, 'name': nm}], rank=r.get('rank')))
    return out


def _a_ths_concept(rows, src):
    """同花顺热门概念 20。实测 2026-09-22 本机：284ms / 20 行。
    列：rank, concept_code, concept_name, change_pct, hot_value, hot_tag。"""
    out = []
    for r in rows:
        nm, code = r.get('concept_name'), r.get('concept_code')
        out.append(_item(
            src,
            '概念 %s(%s) 热度第%s 涨跌%s' % (nm, code, r.get('rank'), _pct(r.get('change_pct'))),
            str(r.get('hot_tag') or ''),
            tags=_tags(nm, r.get('hot_tag')),
            related=[{'code': code, 'name': nm}],
            rank=r.get('rank'), hot_value=r.get('hot_value')))
    return out


def _a_lhb(rows, src):
    """龙虎榜（每日）。实测 2026-09-22 本机：299ms / 78 行。
    ★`reason` 是**交易所给的上榜原因原话**（'连续三个交易日内，涨幅偏离值累计达到20%的证券'）
      —— 原样进 excerpt，一个字不改：这是最有信息量的字段，改写就是加观点。"""
    out = []
    for r in rows:
        nm, code = r.get('short_name'), r.get('stock_code')
        out.append(_item(
            src,
            '%s(%s) 龙虎榜 涨跌%s' % (nm, code, _pct(r.get('change_cpt'))),
            '上榜原因：%s' % (r.get('reason') or '—'),
            tags=_tags(r.get('reason')),
            related=[{'code': code, 'name': nm}]))
    return out


# 调用名 → (adata 里的点路径, 解析器)。点路径在 fetch 里现解析（adata 是可选依赖，
# 装了才 import —— 主 venv 装了，但判据/别的机器不一定）。
_CALLS = {
    'ths_hot100':      ('sentiment.hot.hot_rank_100_ths', _a_ths_hot100),
    'em_pop100':       ('sentiment.hot.pop_rank_100_east', _a_em_pop100),
    'ths_concept_hot': ('sentiment.hot.hot_concept_20_ths', _a_ths_concept),
    'lhb_daily':       ('sentiment.hot.list_a_list_daily', _a_lhb),
}


def installed():
    try:
        import adata                                   # noqa: F401
        return True
    except Exception:                                  # noqa: BLE001
        return False


def _resolve(path):
    import adata
    obj = adata
    for part in path.split('.'):
        obj = getattr(obj, part)
    return obj


def fetch(src, symbol=None, terms=None, limit=10):
    """打一个 adata 情绪源，返回归一化 item 列表（已按 terms 过滤、已截断）。"""
    call = src.get('call')
    if call not in _CALLS:
        raise ValueError('未登记的 adata 调用：%s（先在 _CALLS 里写清列名）' % call)
    path, parser = _CALLS[call]
    timeout = int(src.get('timeout_sec') or 40)
    df = feed._with_timeout(lambda: _resolve(path)(), timeout, 'adata.' + call)
    if df is None or len(df) == 0:
        return []
    out = [_it for _it in parser(df.to_dict('records'), src) if _it['title']]
    if terms:
        joined = [t for t in terms if t]
        out = [it for it in out
               if any(t and (t in it['title'] or t in (it['excerpt'] or '')
                             or any(t in tg for tg in it['tags']))
                      for t in joined)]
    return out[:max(1, int(limit))]
