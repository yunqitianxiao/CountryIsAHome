"""探针：把每个上游接口的**真实**签名、端点、列名、行数、耗时落成事实清单。

用法：
    .venv/bin/python -m agent.datasource._probe                 # 跑**活着的**那几组（几分钟）
    .venv/bin/python -m agent.datasource._probe tencent         # 只跑一组
    .venv/bin/python -m agent.datasource._probe sina minute     # 组 + 关键字过滤
    .venv/bin/python -m agent.datasource._probe dead            # ★跑"死亡台账复现"（慢，且都不通）

为什么要这个文件：`docs/AGENT-SPEC.md` §2 那张 API 表是**目录级**的
（从 `dir()` 抄下来的名字），签名/返回列/耗时一个都没实测过。本文件就是
"把记忆变成实测"的那一步，**输出即事实清单**。`agent/datasource/*.py` 的
每个 docstring 里写的数字都必须能在这里被复现。

★2026-09-22 06:00 重写：初版里有一组 `efinance` 和一组 adata 日K。
  两组都已从**默认**里拿掉，原因不是"懒得跑"，是**它们指向的上游整族都不通**：

  - `efinance` 0.5.9 **整包是纯东财 `push2*` 后端** ⇒ 在这台机器上没有任何一条路
    是通的。留着它的探针只会产出"XX ConnectionError"（已知），而且按第 0 节铁律
    **本包不许 import efinance** —— 探针也不该开这个口子。
  - adata 的 `get_market`（日K）同样走 `push2his`。它的"死"是有价值的台账
    （`handler_null` 把异常吞成空表 ⇒ **0 行而不是报错**），但那是**一次性的结论**，
    已经落在 `adata_src.py` 模块头那张表里，不需要每次跑探针都复现一遍。
    真要复现走 `dead` 组（显式点名才跑）。

★`efinance` 这个包**仍然装着，但本文件不 import 它**。要查它为什么不行，
  去看 adata 模块头那张主机表（同一族主机、同一个原因）。

★两条纪律，抄自 `agent/feed.py`：

  1. **国内站直连。** 本机 env 有 `http_proxy=127.0.0.1:7897`（Clash），国内数据站走它
     一律 502。所以 import 任何网络库**之前**先把 proxy 变量摘掉（`drop_proxy()`），
     而且要早于 `requests`/`adata` 的第一次 import —— 它们在 import 期就可能建 session。
     ★例外是加密（OKX/Binance）：那两家**必须**走代理，所以 `_probe` 里它们走
     `via_proxy=True` 的显式通道，不靠 env。
  2. **每个调用都要有超时。** adata 内部是裸 `requests.request(...)`，**没有 timeout**；
     腾讯/新浪/同花顺也都是裸 requests。所以 `probe()` 用 `concurrent.futures`
     包一层，超时按失败记 —— 服务里"挂住"比"报错"糟糕得多。

★**这个文件测的是"上游现在给什么"，不是"契约层在夹具下什么行为"。**
  它从头到尾**不认桩**（`LICAI_FEED_STUB` 在本文件里零命中），因为它的产物是
  实测清单。后果要知道：`datasource` 组里有两条探针的名字写着
  `★预期 XX：本机没有可用来源`（`minute(hk_stock,…)` / `capital_flow(…, daily)`）——
  那个"预期"是**活体世界的**事实（桩关掉时它们**真的**抛 `SourceError`，两条都实测过，
  而且两条都在建连接之前就抛，所以离线可复现）。可一旦 `LICAI_FEED_STUB` 开着，
  桩会**按设计**给出帧（`stub.py` 模块头：分钟线与资金流在本机没有真源，
  桩的职责就是在这两件事上顶上去），于是同一行打 `OK`。
  ⇒ 此时 `OK` 说的是"夹具在这条路上接上了"，**不是**"上游可用"。
  这不是期望表漏登记（本文件**没有**期望表），也不是 docstring 说过头
  —— 两边的口径各自都是对的，缺的是**这份清单没声明自己在哪个世界里测的**。
  要读"本机到底有没有源"，跑桩关掉的那一份；要读"契约层在夹具下的形状"，开桩。

实测环境：Linux 6.8.0-138-generic / Python 3.10.12 / pandas 2.3.3 /
adata 2.9.5 / akshare 1.18.88，2026-09-22。
"""

import os


def drop_proxy():
    """摘掉本机 Clash 代理。**必须在 import requests/adata 之前调**。

    实测（2026-09-22）：带着 `http_proxy=127.0.0.1:7897` 打腾讯/新浪会拿到 502
    或连不上（Clash 的规则不认这些域名），而同一台机器直连是通的。
    ★注意：加密（OKX/Binance）**反过来** —— 直连不通、必须走 7897。
    """
    for k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
              'ALL_PROXY', 'all_proxy'):
        os.environ.pop(k, None)


drop_proxy()                                     # ★早于下面所有第三方 import

import concurrent.futures as cf                  # noqa: E402
import contextlib                                # noqa: E402
import inspect                                   # noqa: E402
import time                                      # noqa: E402

import pandas as pd                              # noqa: E402

TIMEOUT = 30                                     # 单次调用上限（秒）

# ── 退出码（三态，照抄仓库里已有的那一条协议）──────────────────────────
# ★★这个文件原本**一个 `sys.exit` 都没有** ⇒ `main()` 的返回码恒 0，于是下面四种
#   结局的退出码**逐字相同**：全通过 / 有若干 XX / 某个组整批被删掉 /
#   **组名写瞎**。最后一种是这里面最坏的一种 —— 它一个探针都不跑、还报成功，
#   一条结构上永远绿的自检。谁拿它当门，那扇门就是时开时关的。
#
#   码值不是这里发明的：仓库里已经有一条三态协议（`verify_page.py: SKIP_RC = 3`，
#   `verify_agent.py: PAGE_SKIP_RC = 3`），注释里写明"为什么不是 2：2 被 argparse 的
#   用法错误（`SystemExit(2)`）占了，'这一层没跑' 和 '调用方参数写错了' 会撞码"。
#   ⇒ 本文件照用同一组语义：
#
#       0 = 全 OK
#       1 = 有 XX —— **跑了，且这条不合格**（`probe()` 吞掉的异常也算这一支：
#           它是"跑了并失败"，不是"没跑"）
#       3 = **一条都没跑**（组名不认识 / 组内零条目 / 关键字过滤后为空）
#
#   ★3 既不是绿也不是红，是"未验证"。消费端**不许**把它折叠成任何一边 ——
#     把"我没跑"读成"通过"就是本文件要修的那个病。
#   ★"全通过 ⇒ 0" 这一半必须保住：别处有判据断言的就是它。
PROBE_OK_RC = 0
PROBE_XX_RC = 1
PROBE_NOTHING_RC = 3

# 结果行的**逐字前缀**。这就是本文件对外的输出格式（逐条 `  OK  …` / `  XX  …`），
# 也是下面数结果的唯一判据 —— 调用方本来就在数这些行（`datasource` 组 21 条）。
_RESULT_PREFIX = (('  OK  ', 'ok'), ('  XX  ', 'xx'))


class _Tally:
    """把 stdout 里的**结果行**数出来，同时**原样转发**（不攒批，探针还是手电筒）。

    ★为什么拦 stdout、而不是在 `probe()` 里加个计数器：结果行有**两个**来源 ——
      `probe()` 是一条，`group_eastmoney_hosts` 里那两组 TCP/HTTP 循环是另一条
      （它们直接 `print`，根本不走 `probe`）。按 print 点枚举的话，**以后谁再加一组
      直接 print 的结果行，计数就静默漏掉它** —— 那正是这次要修的那个病
      （"漏数/没跑"被悄悄折叠成"通过"）。锚在**文档化的输出格式**上就没有这个缺口：
      谁打出来的都算。
    ★只数行首前缀，不数行数：`⇒ 结果行 …` 那几行汇总不带前缀，不会被自己数进去。
    """

    def __init__(self, sink):
        self._sink = sink
        self._buf = ''
        self.ok = 0
        self.xx = 0

    def write(self, s):
        self._sink.write(s)
        self._buf += s
        while True:
            nl = self._buf.find('\n')
            if nl < 0:
                return
            line, self._buf = self._buf[:nl], self._buf[nl + 1:]
            for pre, kind in _RESULT_PREFIX:
                if line.startswith(pre):
                    if kind == 'ok':
                        self.ok += 1
                    else:
                        self.xx += 1
                    break

    def flush(self):
        self._sink.flush()

    def __getattr__(self, name):
        """其余属性（`isatty` / `encoding` / `fileno`…）转发给真正的 stdout。

        ★下划线开头的一律不转发：`__getattr__` 只在属性**取不到**时才走，
          转发 `_sink` 自己会无限递归。
        """
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self._sink, name)


def sig(fn):
    """函数真实签名。★偷懒直接 str(signature) 会骗人：adata 大量函数是**实例方法**
    挂在单例上，`inspect.signature` 给的是绑定后的样子（没有 self），正合用。"""
    try:
        return str(inspect.signature(fn))
    except (TypeError, ValueError) as e:
        return '<签名取不到: %s>' % e


def _describe(obj):
    """把一个返回值压成一行事实：类型 / 行数 / 列名（逐字）。"""
    if isinstance(obj, pd.DataFrame):
        cols = [str(c) for c in obj.columns]
        head = obj.head(1).to_dict('records')
        attrs = {k: v for k, v in obj.attrs.items() if k != 'cached'}
        return 'DataFrame rows=%d cols=%s attrs=%s sample=%s' % (
            len(obj), cols, attrs, str(head[0] if head else {})[:200])
    if isinstance(obj, (list, tuple)):
        first = obj[0] if obj else None
        return '%s n=%d first=%s' % (type(obj).__name__, len(obj), str(first)[:220])
    if isinstance(obj, dict):
        return 'dict keys=%s' % list(obj.keys())[:20]
    return '%s %s' % (type(obj).__name__, str(obj)[:220])


def probe(label, fn, timeout=TIMEOUT):
    """跑一次调用，如实记录：OK/XX、耗时、返回形状。**不抛异常** —— 探针的产物
    是事实清单，一个探针炸掉不该让后面的探针看不见。

    ★打出 `XX` 的那两支（TIMEOUT / 异常）在退出码上是 **1（跑了且不合格）**，
      不是 3（没跑）—— 见 `PROBE_XX_RC` 上面那段。"""
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            r = fut.result(timeout=timeout)
            ms = int((time.time() - t0) * 1000)
            print('  OK  %-50s %6dms  %s' % (label, ms, _describe(r)))
            return r
        except cf.TimeoutError:
            print('  XX  %-50s %6dms  ★TIMEOUT（>%ds）—— 上游内部没有 timeout，'
                  '这层必须由调用方兜' % (label, int((time.time() - t0) * 1000), timeout))
            return None
        except Exception as e:                   # noqa: BLE001 —— 源边界，如实记
            print('  XX  %-50s %6dms  %s: %s'
                  % (label, int((time.time() - t0) * 1000), type(e).__name__, str(e)[:170]))
            return None


def _keep(only, *names):
    """`only` 是命令行第二个参数：给了就只跑名字里含它的那些探针。
    ★不做模糊匹配的高级东西 —— 探针是个手电筒，不是个框架。"""
    return (not only) or any(only in n for n in names)


# ── 组 1：★东财主机族分界线（本次最有价值的一条实测）────────────────────
# 上面那两个"东财全挂"的结论是**错的**，就错在没把主机名逐个打一遍。
# 这组是那条修正的**可复现证据**，也是本文件里唯一一组"打 TCP + 打 HTTP 两层"的。

def group_eastmoney_hosts(only=None):
    import socket
    import urllib.request
    print('\n=== 东财主机族分界线（2026-09-22 实测，直连无代理）===')
    hosts = ['push2.eastmoney.com', 'push2his.eastmoney.com', '82.push2.eastmoney.com',
             'push2delay.eastmoney.com', 'datacenter.eastmoney.com',
             'datacenter-web.eastmoney.com', 'quote.eastmoney.com', 'data.eastmoney.com',
             'f10.eastmoney.com', 'np-anotice-stock.eastmoney.com']
    print('-- 第 1 层：TCP 443（★这一层**全通**，所以墙上的是 HTTP 不是网络）--')
    for h in hosts:
        t0 = time.time()
        try:
            ip = socket.gethostbyname(h)
            s = socket.create_connection((h, 443), timeout=6)
            s.close()
            print('  OK  %-34s TCP 通  %-16s %5.0fms' % (h, ip, (time.time() - t0) * 1000))
        except Exception as e:                   # noqa: BLE001
            print('  XX  %-34s TCP 断  %s（%.0fms）'
                  % (h, type(e).__name__, (time.time() - t0) * 1000))

    print('-- 第 2 层：真实 API/网页路径（★分界线在这一层）--')
    ua = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'}
    urls = [
        ('push2 clist（行情列表）',
         'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1'
         '&fields=f12,f14,f2&fs=m:0+t:6,m:1+t:2'),
        ('push2his kline（历史K线）',
         'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600519'
         '&fields1=f1&fields2=f51,f53&klt=101&fqt=1&end=20500101&lmt=5'),
        ('82.push2 clist',
         'https://82.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1&fields=f12,f14,f2'),
        ('push2delay kline',
         'https://push2delay.eastmoney.com/api/qt/stock/kline/get?secid=1.600519'
         '&fields1=f1&fields2=f51,f53&klt=101&fqt=1&end=20500101&lmt=5'),
        ('datacenter F10 板块（adata get_plate_east 走这条）',
         'https://datacenter.eastmoney.com/securities/api/data/get'
         '?type=RPT_F10_CORETHEME_BOARDTYPE&sty=SECUCODE,SECURITY_CODE,BOARD_CODE,'
         'BOARD_NAME,BOARD_TYPE&filter=(SECUCODE%3D%22600519.SH%22)&p=1&ps=&sr=1'
         '&st=BOARD_RANK&source=HSF10&client=PC'),
        ('datacenter-web 报表',
         'https://datacenter-web.eastmoney.com/api/data/v1/get?pageSize=3&pageNumber=1'
         '&columns=ALL&reportName=RPT_DAILYBILLBOARD_DETAILSNEW'),
        ('quote 网页', 'https://quote.eastmoney.com/sh600519.html'),
        ('f10 网页', 'https://f10.eastmoney.com/Info/Index?code=SH600519'),
    ]
    for name, u in urls:
        t0 = time.time()
        try:
            req = urllib.request.Request(u, headers=ua)
            with urllib.request.urlopen(req, timeout=12) as r:   # noqa: S310 —— 探针
                body = r.read()
            print('  OK  %-46s %5.0fms  HTTP %s  %d bytes  %s'
                  % (name, (time.time() - t0) * 1000, r.status, len(body),
                     body[:100].decode('utf-8', 'replace').replace('\n', ' ')))
        except Exception as e:                   # noqa: BLE001
            print('  XX  %-46s %5.0fms  %s: %s'
                  % (name, (time.time() - t0) * 1000, type(e).__name__, str(e)[:120]))
    print('  ⇒ 结论：死的是一**族**主机（push2* / push2his / 82.push2 / push2delay），')
    print('     活的是另一族（datacenter* / datacenter-web）。不是"东财整站墙"。')


# ── 组 2：腾讯（主源）──────────────────────────────────────────────────

def group_tencent(only=None):
    from . import tencent as T
    print('\n=== 腾讯（2026-09-22 实测）===')
    if _keep(only, 'rt', 'realtime'):
        probe('realtime_batch([sh600519,hk00700,usAAPL])',
              lambda: T.realtime_batch(['sh600519', 'hk00700', 'usAAPL']))
        print('  -- ★不存在的代码静默缺条：必须按 code 对齐，不能按位置 --')
        probe('realtime_batch(60 码，含 8 个编造的)',
              lambda: T.realtime_batch(['sh%06d' % (600000 + i) for i in range(52)]
                                       + ['sh99999%d' % i for i in range(8)]))
    if _keep(only, 'kline'):
        print('  -- ★K 线：回包是两级嵌套 data[<code>][<key>]，key 随市场/复权变 --')
        for mk, c, adj in [('cn_stock', '600519', 'qfq'), ('cn_index', '000300', ''),
                           ('cn_etf', '510300', 'qfq'), ('hk_stock', '00700', ''),
                           ('us_stock', 'AAPL', '')]:
            probe('kline(%s, %s, fq=%s)' % (mk, c, adj or '空'),
                  lambda mk=mk, c=c, adj=adj: T.kline(mk, c, days=400, adjust=adj))
        print('  -- ★★美股裸码 vs 带后缀：裸码静默只回 2 根（不报错！）--')
        probe('美股的**真实代码**（从 qt 段挖）', lambda: T._us_real_code(
            T._kline_raw('usAAPL', 'day', 320, '')[2], 'usAAPL'))
        probe('kline(us_stock, AAPL)   —— 反正会自己修正并守住 30 根下限',
              lambda: T.kline('us_stock', 'AAPL', days=320))
        print('  -- ★深度：请求 400→401 / 800→801 / 2000→被服务端截到 641 --')
        for n in (400, 800, 2000):
            probe('kline(cn_stock, 600519, n=%d) 实到根数' % n,
                  lambda n=n: T.kline('cn_stock', '600519', days=n))
    if _keep(only, 'board', '板块'):
        print('  -- ★板块：key 是 lzg（领涨股）不是 zlg；turnover 单位是**万元** --')
        rows, total = T.board_list('concept', offset=0, count=3)
        print('  concept total=%s（翻页上限 count=100，>100 静默回 0 行）' % total)
        if rows:
            print('  原始行逐字: %s' % str(rows[0])[:400])
            b = T._row_board(rows[0], 'concept', 'X')
            print('  映射后    : %s' % b)
        rows, total = T.board_list('industry', offset=0, count=3)
        print('  industry total=%s' % total)
        # ★字段名是 `amount`、**单位是元**（2026-09-22 实测：31 个行业求和
        #   = 2,126,911,390,000 ≈ 2.13 万亿，正是 A 股全市场成交额那个量级）。
        #   这里原本写的是 `r['turnover']` —— `board_list_all` 的回包里**没有**这个键
        #   （原始行与映射后是两层，`turnover` 只在个股行情那条路上），于是这条探针
        #   必然 KeyError：**"板块清单能不能取到"这个真命题，被换成"字段名对不对"** ——
        #   而探针是判断上游健康的唯一尺子，尺子红在错的地方比不红更坏。
        print('  -- ★单位实测法：31 个行业 amount 求和应该 ≈ 全市场成交额（单位=元）--')
        probe('board_list_all(industry) 求和',
              lambda: sum(float(r['amount']) for r in T.board_list_all('industry')))
        probe('board_members_all(pt01801120)', lambda: T.board_members_all('pt01801120'))
    if _keep(only, 'minute', '分钟'):
        print('  -- ★A股分钟是坏的；港美股的 m5 只回 1 根聚合日线（与协调者说法冲突）--')
        for lbl, code, per in [('A股 m5', 'sh600519', 'm5'),
                               ('港股 m5', 'hk00700', 'm5'),
                               ('美股 m5', 'usAAPL.OQ', 'm5')]:
            probe('_kline_raw(%s, %s)' % (code, per),
                  lambda code=code, per=per: T._kline_raw(code, per, 320, ''))


# ── 组 3：新浪（分钟线 + 全市场名单）────────────────────────────────────

def group_sina(only=None):
    from . import sina as S
    print('\n=== 新浪（2026-09-22 实测）===')
    if _keep(only, 'minute', '分钟'):
        print('  -- ★A股分钟：scale= 直接给 5/15/30/60，**不重采样** --')
        for p in (5, 15, 30, 60):
            probe('minute(cn_stock, 600519, period=%d)' % p,
                  lambda p=p: S.minute('cn_stock', '600519', period=p, datalen=40))
        probe('daily_via_scale(cn_stock, 600519, days=1023)  深度',
              lambda: S.daily_via_scale('cn_stock', '600519', days=1023))
        print('  -- ★scale=1（1 分钟）实测回 null ⇒ A股 1 分钟没有稳定来源 --')
        probe('minute(cn_stock, 600519, period=1)', lambda: S.minute(
            'cn_stock', '600519', period=1, datalen=40))
        print('  -- ★港/美股在 getKLineData 上没有（只服务 A股）--')
        probe('minute(hk_stock, 00700, period=5)',
              lambda: S.minute('hk_stock', '00700', period=5))
    if _keep(only, 'rt', 'realtime', 'list', '名单'):
        print('  -- ★全市场名单：A股/HK 一站拿完；美股每页只 20 行（num 被忽略）--')
        for m in ('cn', 'hk', 'us'):
            probe('market_count(%s)' % m, lambda m=m: S.market_count(m))
        probe('market_list(cn)  全量', lambda: S.market_list('cn')[1])
        probe('market_list(hk)  全量', lambda: S.market_list('hk')[1])
        probe('market_list(us, pages=3)  证明"页预算"确实是瓶颈',
              lambda: S.market_list('us', pages=3)[1])
        probe("realtime_batch(['sh600519','rt_hk00700','gb_aapl'])",
              lambda: S.realtime_batch(['sh600519', 'rt_hk00700', 'gb_aapl']))


# ── 组 4：同花顺（板块 + 唯一带成交额的 A股日线）────────────────────────

def group_ths(only=None):
    from . import ths as H
    print('\n=== 同花顺（2026-09-22 实测）===')
    if _keep(only, 'kline'):
        probe('kline_daily(600519)  ★带成交额，但只有 140 根',
              lambda: H.kline_daily('600519'))
        probe('kline_daily(000300)  指数实测 404（只服务 hs_ 个股）',
              lambda: H.kline_daily('000300'))
    if _keep(only, 'board', '板块', 'member', '成分'):
        probe('board_list(concept)  实测 361 个，只有 code+name',
              lambda: H.board_list('concept'))
        probe('board_list(industry) 实测 90 个',
              lambda: H.board_list('industry'))
        print('  -- ★成分股每页只有 10 行；带 ajax/1 的翻页路径 401（反爬）--')
        probe('board_members(301558)  '
              '★注意 meta.complete=False —— 不是全量',
              lambda: H.board_members('301558'))


# ── 组 5：akshare（垫底，只留活着的那些）───────────────────────────────

def group_akshare(only=None):
    import akshare as ak
    print('\n=== akshare 1.18.88（2026-09-22 实测）===')
    print('  -- 签名 --')
    for nm in ('stock_zh_a_daily', 'stock_zh_a_hist', 'stock_zh_index_daily',
               'fund_etf_hist_sina', 'stock_hk_daily', 'stock_us_daily',
               'tool_trade_date_hist_sina', 'stock_board_concept_name_ths',
               'stock_info_a_code_name', 'stock_zh_a_spot_em', 'stock_board_concept_cons_ths'):
        print('  %-34s %s' % (nm, sig(getattr(ak, nm)) if hasattr(ak, nm) else '<没有这个函数>'))

    print('  -- ★活着的（全部是**新浪后端**）--')
    probe('stock_zh_a_daily(sh600519, qfq)   本机最深: 6010 根',
          lambda: ak.stock_zh_a_daily(symbol='sh600519', adjust='qfq'), timeout=60)
    probe('stock_zh_index_daily(sh000300)    没有成交额',
          lambda: ak.stock_zh_index_daily(symbol='sh000300'), timeout=60)
    probe('fund_etf_hist_sina(sh510300)',
          lambda: ak.fund_etf_hist_sina(symbol='sh510300'), timeout=60)
    probe('stock_hk_daily(00700)',
          lambda: ak.stock_hk_daily(symbol='00700', adjust='qfq'), timeout=60)
    probe('stock_us_daily(AAPL)              没有成交额',
          lambda: ak.stock_us_daily(symbol='AAPL', adjust='qfq'), timeout=60)
    probe('tool_trade_date_hist_sina()      8797 行、**含未来日期**',
          lambda: ak.tool_trade_date_hist_sina(), timeout=60)
    probe('stock_board_concept_name_ths()   7.1s / 375 行（会往 stderr 打 tqdm）',
          lambda: ak.stock_board_concept_name_ths(), timeout=90)
    probe('stock_info_a_code_name()         ★22.6 秒，别频繁调',
          lambda: ak.stock_info_a_code_name(), timeout=90)

    print('  -- ★死掉的（东财 push2* 族，全部 RemoteDisconnected）--')
    for nm, f in [('stock_zh_a_spot_em', lambda: ak.stock_zh_a_spot_em()),
                  ('stock_zh_a_hist', lambda: ak.stock_zh_a_hist(
                      symbol='600519', period='daily', start_date='20260101',
                      end_date='20260922', adjust='qfq'))]:
        probe('%s  ★预期 XX' % nm, f, timeout=45)
    print('  -- ★1.18.88 里**没有**这个函数（版本差异，别照抄文档）--')
    print('  %-34s %s' % ('stock_board_concept_cons_ths',
                           hasattr(ak, 'stock_board_concept_cons_ths')))


# ── 组 6：adata（只留活着的那几条）─────────────────────────────────────

def group_adata(only=None):
    import adata
    print('\n=== adata 2.9.5 —— 只跑**活着**的那几条（2026-09-22 实测）===')
    print('  -- 签名 --')
    inf, mk = adata.stock.info, adata.stock.market
    for nm in ('trade_calendar', 'index_constituent', 'get_plate_east',
               'all_concept_code_ths', 'concept_constituent_ths'):
        print('  info.%-28s %s' % (nm, sig(getattr(inf, nm))))
    for nm in ('get_capital_flow_min', 'get_market_min', 'get_market_concept_ths'):
        print('  market.%-26s %s' % (nm, sig(getattr(mk, nm))))

    probe('info.trade_calendar()        2.4s / 365 行',
          lambda: inf.trade_calendar(), timeout=45)
    probe('info.index_constituent(000300)   5.2s / 300 行',
          lambda: inf.index_constituent(index_code='000300'), timeout=45)
    print('  -- ★get_plate_east 打的是 datacenter.eastmoney.com（**不是百度**）--')
    probe('info.get_plate_east(600519)     198ms / 27 行',
          lambda: inf.get_plate_east(stock_code='600519'))
    probe('market.get_capital_flow_min(600519)   ★实测**时好时坏**，见交接说明',
          lambda: mk.get_capital_flow_min(stock_code='600519'), timeout=45)
    probe('market.get_market_concept_ths(886013)',
          lambda: mk.get_market_concept_ths(index_code='886013'), timeout=45)


# ── 组 7：死亡台账复现（★显式点名才跑，慢且全不通）──────────────────────

def group_dead(only=None):
    import adata
    print('\n=== 死亡台账复现（★都不是可用路径，跑它是为了重测"它确实死了"）===')
    print('  ★★这一组的意义在于**坏死的方式**：adata 的 `handler_null` 把异常吞成')
    print('     空表 ⇒ 看到的是"0 行"而不是"报错"。0 行会被当成"今天没数据"。')
    mk, inf = adata.stock.market, adata.stock.info
    probe('adata mk.get_market(600519, k_type=1)  ★预期 0 行、不报错',
          lambda: mk.get_market(stock_code='600519', k_type=1))
    probe('adata mk.get_capital_flow(600519)     ★预期 0 行',
          lambda: mk.get_capital_flow(stock_code='600519'), timeout=45)
    probe('adata inf.all_code()                  ★预期 60s 超时',
          lambda: inf.all_code(), timeout=60)
    probe('adata mk.list_market_current([100 码])  ★预期静默丢码',
          lambda: mk.list_market_current(code_list=['%06d' % (600000 + i) for i in range(100)]),
          timeout=45)
    probe('adata mk.get_market_bar(600519)        ★预期 30s 不返回（内部无 timeout）',
          lambda: mk.get_market_bar(stock_code='600519'), timeout=30)
    import akshare as ak
    probe('akshare stock_zh_a_spot_em()            ★预期 RemoteDisconnected',
          lambda: ak.stock_zh_a_spot_em(), timeout=45)


# ── 组 8：本包自己的契约层（★端到端，跟调用方看到的完全一样）────────────

def group_datasource(only=None):
    import sys
    ds = sys.modules[__package__]                 # 就是 `agent.datasource` 本身
    print('\n=== agent.datasource 契约层端到端（2026-09-22 实测）===')
    for m, c in [('cn_stock', '600519'), ('cn_index', '000300'), ('cn_etf', '510300'),
                 ('hk_stock', '00700'), ('us_stock', 'AAPL'), ('crypto', 'BTC')]:
        probe('daily(%s, %s, days=120)' % (m, c),
              lambda m=m, c=c: ds.daily(m, c, days=120))
    probe('minute(cn_stock, 600519, period=5, days=3)',
          lambda: ds.minute('cn_stock', '600519', period=5, days=3))
    probe('minute(hk_stock, 00700, period=5)   ★预期 XX：本机没有可用来源',
          lambda: ds.minute('hk_stock', '00700', period=5))
    probe('realtime(cn)   A股全量',
          lambda: ds.realtime('cn'))
    probe('realtime(us)   ★只有页预算内的那些（部分数据，health 里标红）',
          lambda: ds.realtime('us'))
    probe('realtime(crypto)  ★走 Clash 代理',
          lambda: ds.realtime('crypto'), timeout=45)
    probe('snapshot([600519, 000300, 00700, AAPL, BTC])  跨市场',
          lambda: ds.snapshot(['600519', '000300', '00700', 'AAPL', 'BTC']))
    probe('boards(concept)', lambda: ds.boards('concept'))
    probe('boards(industry)', lambda: ds.boards('industry'))
    probe('board_members(pt01801120, concept)',
          lambda: ds.board_members('pt01801120', 'concept'))
    probe('stock_boards(600519)', lambda: ds.stock_boards('600519'))
    probe('capital_flow(600519, daily)   ★预期 XX：本机没有可用来源',
          lambda: ds.capital_flow('600519', 'daily'))
    probe('trade_calendar(20)', lambda: ds.trade_calendar(20))
    print('  -- ★日历必须**不含未来日期**（上游排到年底，出口砍掉）--')
    probe('trade_calendar(400) 的 [前3, 后3]',
          lambda: (ds.trade_calendar(400)[:3], ds.trade_calendar(400)[-3:]))
    probe('today 与 日历最后一天（最后一天必须 <= 今天）', lambda: (
        __import__('datetime').date.today().strftime('%Y-%m-%d'),
        ds.trade_calendar(5)[-1]))
    probe('health()', lambda: ds.health())


# ── 组 9：现有 feed.py（对照组，确认没被这次改动弄坏）──────────────────

def group_feed(only=None):
    import sys
    print('\n=== agent/feed.py 现有路径（对照组）===')
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import feed
    for m, c in [('cn_stock', '600519'), ('cn_index', '000300'), ('cn_etf', '510300'),
                 ('hk_stock', '00700'), ('us_stock', 'AAPL'), ('crypto', 'BTC')]:
        probe('feed.fetch(%s, %s)' % (m, c),
              lambda m=m, c=c: feed.fetch(m, c, days=120), timeout=90)
    probe('feed.fetch_fundamentals(600519)',
          lambda: feed.fetch_fundamentals('600519'), timeout=60)


GROUPS = {
    'em': group_eastmoney_hosts,
    'tencent': group_tencent,
    'sina': group_sina,
    'ths': group_ths,
    'akshare': group_akshare,
    'adata': group_adata,
    'datasource': group_datasource,
    'feed': group_feed,
    'dead': group_dead,
}


def main(argv=None):
    """跑探针 → **返回退出码**（`0` 全过 / `1` 有 XX / `3` 一条都没跑）。

    ★返回码而不是在里面调 `sys.exit()`：这个函数可以被 import 着调（判据能拿它
      当被测对象），退出码由 `__main__` 那一支转成进程状态。见 `PROBE_*_RC`。
    """
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    which = argv[0] if argv else None
    only = argv[1] if len(argv) > 1 else None
    print('探针环境：pandas=%s' % pd.__version__)
    if which and which not in GROUPS:
        # ★这一支原本**静默**：打一行话、`return None` ⇒ rc=0 ⇒ 和"全通过"逐字相同。
        #   组名写瞎是最容易发生、也最坏的一种 —— 它一个探针都不跑。所以这里要
        #   ①说清"什么都没跑" ②给非 0 码。
        print('没有这一组：%s（有：%s）' % (which, ' / '.join(GROUPS)))
        print('⇒ 一个探针都没跑（组名不认识）⇒ 退出码 %d = **没跑**（既非通过也非失败）'
              % PROBE_NOTHING_RC)
        return PROBE_NOTHING_RC
    tally = _Tally(sys.stdout)
    with contextlib.redirect_stdout(tally):
        for k, fn in GROUPS.items():
            if which and k != which:
                continue
            try:
                fn(only)
            except Exception as e:               # noqa: BLE001 —— 一组炸了别拖垮其余
                # ★这行也带 `  XX  ` 前缀 ⇒ 会被 _Tally 数成"跑了且失败"（rc=1）。
                #   不该被读成"没跑"：这一组**确实跑了**，跑出一个明确的坏结果。
                # ★前缀必须是**逐字**的 `  XX  `（两个尾随空格）—— 与 `_RESULT_PREFIX`
                #   对齐。这里原本写的是 `  XX 组`，`_Tally` 数不着（它 startswith
                #   `  XX  `），于是**整个组崩掉被报成 rc=3「没跑」**，而上面那句注释
                #   坚称是 rc=1。2026-09-22 实测对照：人造一个必崩的组 ⇒ 改前 rc=3。
                print('  XX  组 %s 整体炸了：%s: %s' % (k, type(e).__name__, e))
    ran = tally.ok + tally.xx
    if ran == 0:
        why = ('关键字 %r 过滤后为空' % only) if only else '组里一条探针都没有'
        print('⇒ 组 %s 跑了，但**一条结果行都没打出来**（%s）⇒ 退出码 %d = **没跑**'
              % (which or '(全部)', why, PROBE_NOTHING_RC))
        return PROBE_NOTHING_RC
    rc = PROBE_XX_RC if tally.xx else PROBE_OK_RC
    print('⇒ 结果行 %d 条（OK %d / XX %d）⇒ 退出码 %d' % (ran, tally.ok, tally.xx, rc))
    return rc


if __name__ == '__main__':
    import sys
    sys.exit(main())
