"""层1：akshare 新闻源（随主 venv 就有、永远可用的地板）。

归一化目标（所有源统一的 item 形状）：
    {'title', 'excerpt', 'source', 'source_label', 'ts', 'url'}
  ts 一律 ISO 字符串（'%Y-%m-%d %H:%M:%S' 或 '%Y-%m-%d'）——排序靠它，
  上游给的是 datetime.date / datetime.time / str 大杂烩，必须在这里收敛。

★列按**名字**找、不按位置取（feed.py 的同款纪律）：上游调列序是常事，
  按位置取一旦错位就是静默的（标题列换成摘要列，看起来还挺像新闻）。
★时间戳解析失败 → ts=None，条目**保留**（标题本身仍有信息量），排序时沉底。
"""

import datetime as dt

import feed          # 复用 _ak()（含代理修复）与 _with_timeout（45s 同款）

TIMEOUT_SEC = 45

# fn 名 → (标题列候选, 摘要列候选, 时间列候选, 链接列候选, 来源列候选)
# ★这些列名必须**实测**，不能照文档抄：`stock_info_global_ths`/`futu` 两条我一开始
#   按印象写成 `摘要`/`时间`，而实测真实列是 `内容`/`发布时间`/`链接` —— 按名字取
#   取不到就返回 None，于是**不报错、不空行**，只有 ts 静默变 None（条目全部沉底、
#   时间轴里归到 undated）。2026-09-22 本机 `df.columns` 实测值如下。
_COLS = {
    'stock_news_em':          (['新闻标题'], ['新闻内容'], ['发布时间'], ['新闻链接'], ['文章来源']),
    'stock_info_global_em':   (['标题'], ['摘要'], ['发布时间'], ['链接'], None),
    'stock_info_global_cls':  (['标题'], ['内容'], None, None, None),   # 时间=发布日期+发布时间
    'stock_info_global_sina': (None, ['内容'], ['时间'], None, None),   # 标题从内容里剥【】
    'stock_info_global_ths':  (['标题'], ['内容'], ['发布时间'], ['链接'], None),  # 实测 2026-09-22
    'stock_info_global_futu': (['标题'], ['内容'], ['发布时间'], ['链接'], None),  # 实测 2026-09-22
}


def installed():
    try:
        feed._ak()
        return True
    except Exception:                            # noqa: BLE001
        return False


def _pick(row, cands):
    """按名字候选取第一个存在且非空的列值。"""
    for c in (cands or []):
        if c in row and row[c] not in (None, ''):
            return row[c]
    return None


def _ts_str(*parts):
    """把 日期/时间/时间字符串 拼成可排序的 ISO 字符串；解析不了返回 None。

    ★8 位纯数字串要认成 `YYYYMMDD`（新闻联播的 `date` 列就是 `20260921` 这种 int）。
      不认的后果**不是 None 而是更坏的**：它会掉进下面 `time_s` 那一支，
      于是 ts 变成 `'20260921'` —— 一个**看起来像时间戳的畸形串**，而在字符串
      排序里 `'20260921' > '2026-09-23 00:00:00'`（'0' > '-'），于是这些老条目
      会**排到最前面**、把真正的当日新闻挤出 limit。这是静默的：没有异常、
      没有空值，只有"今天的头条怎么是前天的"。
    """
    date_s, time_s = None, None
    for p in parts:
        if p is None:
            continue
        if isinstance(p, dt.datetime):
            return p.strftime('%Y-%m-%d %H:%M:%S')
        if isinstance(p, dt.date):
            date_s = p.strftime('%Y-%m-%d')
        elif isinstance(p, dt.time):
            time_s = p.strftime('%H:%M:%S')
        else:
            s = str(p).strip()
            if not s:
                continue
            if len(s) == 8 and s.isdigit():       # '20260921' / 20260921 → 日期
                s = '%s-%s-%s' % (s[:4], s[4:6], s[6:])
            if ' ' in s and date_s is None:      # '2026-09-22 14:11:03'
                date_s, _, t = s.partition(' ')
                time_s = t or time_s
            elif date_s is None and len(s) >= 10:
                date_s = s[:10]
            elif time_s is None:
                time_s = s
    if date_s and time_s:
        return '%s %s' % (date_s, time_s)
    return date_s or time_s or None


def _title_from_content(content):
    """新浪 7x24 没有标题列：剥开头的【…】当标题，否则取前 40 字。"""
    c = str(content or '').strip()
    if c.startswith('【') and '】' in c:
        return c[1:c.index('】')][:80]
    return c[:40]


def _norm(title, content, ts, url, sid, label, source_col=None, tags=None, related=None,
          ts_kind='upstream', sentiment_raw=None):
    """统一的 item 形状。★所有键**永远都在**（缺了就是 None/空表）——
    下游（sentiment/前端）不必写 `it.get(x, default)` 那种防御式取法，
    而"这个源不给这个字段"与"这个字段是空"因此**长得不一样**。"""
    return {
        'title': str(title or '').strip()[:120],
        'excerpt': str(content or '').strip()[:300],
        'source': sid,
        'source_label': str(source_col or label),
        'ts': ts,
        'ts_kind': ts_kind,
        'url': str(url or '') or None,
        'tags': [str(t) for t in (tags or []) if t],
        'related': related or [],
        'sentiment_raw': sentiment_raw,
    }


def _num_or_none(v):
    return feed._num(v, 4)


def _sym_prefixed(code):
    """裸代码 → 带市场前缀（`SH600519`）。**只有 `stock_hot_keyword_em` 要这个**。

    ★这是**命名规则**不是查表：6 开头＝沪、0/3 开头＝深、4/8 开头＝北。
      规则本身可复核（交易所代码段是公开规则），所以这里不算"编数字"；
      但它不覆盖指数/基金的全部段位 —— 覆盖不到的会带着错前缀去请求，
      然后**取不到就空**（不会静默变成别人的数据），这就是可接受的原因。
    """
    c = str(code or '').strip()
    if not c or not c.isdigit() or len(c) != 6:
        return c
    if c[0] == '6' or c[0] == '5' or c[0] == '9':
        return 'SH' + c
    if c[0] in ('0', '3', '1', '2'):
        return 'SZ' + c
    if c[0] in ('4', '8'):
        return 'BJ' + c
    return c


# ── 专用适配器 ────────────────────────────────────────────────────────
# 为什么单列：下面这些函数的列形状和"标题/摘要/时间"三件套差得远（`stock_js_weibo_report`
# 干脆只有 name+rate 两列），硬塞进 `_COLS` 只会得到一堆空标题的条目。
# ★适配器**只搬运上游字段**，不合成任何上游没有的数。

def _a_news_cctv(rows, src, symbol):
    """新闻联播文字稿：date / title / content。日期是上游给的（`date` 列）。"""
    out = []
    for r in rows:
        out.append(_norm(r.get('title'), r.get('content'),
                         _ts_str(r.get('date')), None, src.get('id'),
                         src.get('label')))
    return out


def _a_news_cx(rows, src, symbol):
    """财新网·财新数据通：tag / summary / url。**没有标题列** —— summary 当前 40 字。"""
    out = []
    for r in rows:
        s = str(r.get('summary') or '').strip()
        out.append(_norm(s[:40], s, None, r.get('url'), src.get('id'),
                         src.get('label'), tags=[r.get('tag')] if r.get('tag') else []))
    return out


def _a_weibo_nlp(rows, src, symbol):
    """金十·微博舆情报告：name / rate。

    ★rate 是**上游给的**（jin10 数据中心），它的原定义照抄上游文档：
      「个股在微博讨论中的人气排行指数。红色越深＝讨论热度越高、当前涨幅更大；
        绿色越深＝热度越低、跌幅更大。」
      ⇒ **它是一个"人气+涨幅"的混合指数，不是纯粹的情绪分**。所以这里
      `sentiment_raw` 原样带上上游的 `value` 和自己的定义，绝不改口径、
      也不算任何东西。取不到就是 None（这条纪律见 sentiment 模块的 docstring）。
    """
    out = []
    for r in rows:
        nm, rate = r.get('name'), _num_or_none(r.get('rate'))
        raw = {'src': 'jin10.weibo_nlp', 'value': rate, 'label': None,
               'upstream_def': '个股微博讨论人气排行指数（热度+涨幅着色）'}
        out.append(_norm('%s 微博讨论人气指数 %s' % (nm, '—' if rate is None else rate),
                         None, None, None, src.get('id'), src.get('label'),
                         related=[] if not nm else [{'code': None, 'name': str(nm)}],
                         ts_kind='collected', sentiment_raw=raw))
    return out


def _a_notice(rows, src, symbol):
    """个股公告：公告标题 / 公告类型 / 公告日期 / 网址。`公告类型` 是上游分类 → tags。"""
    out = []
    for r in rows:
        out.append(_norm(r.get('公告标题'), r.get('公告类型'), _ts_str(r.get('公告日期')),
                         r.get('网址'), src.get('id'), src.get('label'),
                         tags=[r.get('公告类型')] if r.get('公告类型') else [],
                         related=[{'code': r.get('代码'), 'name': r.get('名称')}]))
    return out


# 千股千评系列：每只票一份**历史序列**，这里只取**最新一行**。
# ★只取最新行不是"分析"：上游的每一行都是它自己的读数，取最后一行是选时间点；
#   想要整条序列的人应该去 `adata`/东财原站，舆情层要的是"此刻的读数"。
_COMMENT_FNS = {
    'stock_comment_detail_zhpj_lspf_em':      ('交易日', '评分'),
    'stock_comment_detail_scrd_focus_em':     ('交易日', '用户关注指数'),
    'stock_comment_detail_zlkp_jgcyd_em':     ('交易日', '机构参与度'),
    'stock_comment_detail_scrd_desire_em':    ('交易日期', '参与意愿'),
}


def _latest(rows, date_col):
    """按日期列取最新一行。日期解析不了的行**不参与排序**（不猜谁更新）。"""
    best, best_d = None, None
    for r in rows:
        d = _ts_str(r.get(date_col))
        if d is None:
            continue
        if best_d is None or d > best_d:
            best, best_d = r, d
    return best, best_d


def _a_comment(fn_name):
    """生成千股千评系列适配器。读的是 `_COMMENT_FNS` 里登记的两列名字。"""
    date_col, val_col = _COMMENT_FNS[fn_name]

    def _adapter(rows, src, symbol):
        row, d = _latest(rows, date_col)
        if row is None:
            return []
        v = _num_or_none(row.get(val_col))
        raw = {'src': 'eastmoney.qgkpsc.' + val_col, 'value': v, 'label': None,
               'upstream_def': '东方财富千股千评·%s' % val_col}
        # ★标题只用源清单的 `label`（干净的名字）；实测耗时之类的**注解**在 `note` 里，
        #   不进标题 —— 标题会流到 items/digest/时间轴里给人和大模型看，
        #   把"实测335ms"写进去等于让一条事实串带着运维注释到处走。
        return [_norm('%s(%s) %s 上游值=%s' % (src.get('label'), symbol or '—', val_col,
                                             '—' if v is None else v),
                      '最新交易日 %s；上游定义：%s' % (d or '—', raw['upstream_def']),
                      d, None, src.get('id'), src.get('label'), sentiment_raw=raw)]
    return _adapter


def _a_hot_keyword(rows, src, symbol):
    """个股人气榜·热门关键词：时间 / 股票代码 / 概念名称 / 热度。

    ★`概念名称` 是**上游给的概念标签**（白酒/茅指数…）—— 这是本项目
      `top_terms` 唯一的合法来源：数上游标签是计数，从标题里抽词是分析，
      而抽出来的词与上游给的词长得一模一样、事后分不清（见 sentiment 模块 docstring）。
    """
    out = []
    for r in rows:
        cname = r.get('概念名称')
        heat_v = _num_or_none(r.get('热度'))
        out.append(_norm('%s(%s) 概念标签「%s」 上游热度=%s'
                         % (src.get('label'), symbol or '—', cname or '—',
                            '—' if heat_v is None else heat_v),
                         '上游概念代码 %s' % (r.get('概念代码') or '—'),
                         _ts_str(r.get('时间')), None, src.get('id'), src.get('label'),
                         tags=[cname] if cname else [],
                         related=[{'code': r.get('概念代码'), 'name': cname}]))
    return out


_ADAPTERS = {
    'news_cctv': _a_news_cctv,
    'stock_news_main_cx': _a_news_cx,
    'stock_js_weibo_report': _a_weibo_nlp,
    'stock_individual_notice_report': _a_notice,
    'stock_hot_keyword_em': _a_hot_keyword,
}
for _fn in _COMMENT_FNS:
    _ADAPTERS[_fn] = _a_comment(_fn)


def _kwargs(src, symbol, date_ph=None):
    """调用参数：symbol 类的按 `arg` 指定的形参名传，日期类按占位符现算。

    ★`stock_hot_keyword_em` 要 `SH600519` 这种带前缀的代码，`security` 那类要裸代码
      —— 差异写在源清单的 `arg`/`symbol_fmt` 里（配置是数据，不是代码里的 if）。
    ★日期用**本机本地时区**（本机是 CST，和上游同一时区）；跨时区部署要改这里。
    `date_ph` 只给 `fetch()` 的日期回退用（见那里的注释），调用方一般不传。
    """
    kw = dict(src.get('kwargs') or {})
    if src.get('scope') == 'symbol':
        if not symbol:
            return None                       # 没有 symbol 就不该被调用（上层已拦）
        arg = src.get('arg') or 'symbol'
        kw[arg] = (_sym_prefixed(symbol) if src.get('symbol_fmt') == 'prefixed'
                   else str(symbol))
    ph = date_ph or src.get('date')
    if ph:
        today = dt.date.today()
        kw[src.get('date_arg') or 'date'] = (
            (today - dt.timedelta(days=1) if ph == 'yesterday' else today).strftime('%Y%m%d'))
    return kw


def fetch(src, symbol=None, terms=None, limit=10):
    """打一个 akshare 源，返回归一化 item 列表（已按 terms 过滤、已截断）。"""
    fn_name = src.get('fn')
    adapter = _ADAPTERS.get(fn_name)
    if adapter is None and fn_name not in _COLS:
        raise ValueError('未登记的 akshare 新闻函数：%s（先在 _COLS/_ADAPTERS 里写清列名）'
                         % fn_name)
    kwargs = _kwargs(src, symbol)
    if kwargs is None:
        raise ValueError('源 %s 是 symbol 域，但没有拿到 symbol' % src.get('id'))
    ak = feed._ak()
    fn = getattr(ak, fn_name)
    df = feed._with_timeout(lambda: fn(**kwargs), TIMEOUT_SEC, fn_name)
    # ★日期回退（只在源清单写了 `date_fallback` 时走）：`news_cctv` 是**当天档**，
    #   当天 19:00 前上游根本没发布 ⇒ 返回 0 行。这个 0 行**不是故障也不是"没新闻"**，
    #   是"今天的还没到"。固定用 yesterday 又会在 19:00 后拿不到当天的。
    #   ⇒ 主日期空 + 声明了回退 ⇒ 换回退日期**再问一次上游**（不是我们编数据，
    #   只是换个参数重问）。两次都空就是真空。
    if (df is None or len(df) == 0) and src.get('date_fallback') and src.get('date'):
        kw2 = _kwargs(src, symbol, date_ph=src['date_fallback'])
        df = feed._with_timeout(lambda: fn(**kw2), TIMEOUT_SEC, fn_name)
    if df is None or len(df) == 0:
        return []
    label = src.get('label') or src.get('id')
    sid = src.get('id')
    rows = df.to_dict('records')

    if adapter is not None:
        out = [it for it in adapter(rows, src, symbol) if it['title']]
    else:
        t_c, e_c, ts_c, u_c, s_c = _COLS[fn_name]
        out = []
        for row in rows:
            content = _pick(row, e_c)
            title = _pick(row, t_c) or _title_from_content(content)
            if fn_name == 'stock_info_global_cls':
                ts = _ts_str(row.get('发布日期'), row.get('发布时间'))
            else:
                ts = _ts_str(_pick(row, ts_c))
            it = _norm(title, content, ts, _pick(row, u_c), sid, label, _pick(row, s_c),
                       tags=[], related=[])
            if not it['title']:
                continue
            out.append(it)
    # symbol 域的天生相关；global 域的按 terms 过滤（无 terms 则取最新几条当"市场在发生什么"）
    if src.get('scope') != 'symbol' and terms:
        joined = [t for t in terms if t]
        out = [it for it in out
               if any(t and (t in it['title'] or t in (it['excerpt'] or '')
                             or any(t in tg for tg in it['tags']))
                      for t in joined)]
    return out[:max(1, int(limit))]
