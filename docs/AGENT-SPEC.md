# licai-agent 多智能体改造 —— 接口冻结与分工规格

> 本文件是**并行施工的唯一契约**。所有子代理必须先读完本文件再动手。
> 目录就是本项目根目录，venv 是 `.venv/bin/python`（用 `uv pip install --python .venv/bin/python -i https://mirrors.aliyun.com/pypi/simple/ <pkg>` 装包）。

---

## 0. 铁律（违反即返工）

1. **不许编数字。** 取不到数就说取不到，返回 `ok=false` + 人话原因。任何看起来像真数据的假值都比报错坏。
2. **不许 `git commit` / `git reset` / `git checkout` / `git stash` / `--amend`。** 仓库是多人共用的，暂存区和 HEAD 是共享状态。只写工作区文件。
3. **不许改 `verify_agent.py`**（12525 行判据网，专人负责）、**不许改 `agent/runner.py`**（Kronos 引擎）、**不许改 `agent/kronos/**`**（vendored 上游）。
4. **只创建/修改你被分配到的那批文件。** 需要别处的改动，在交付说明里写出来，由协调者改。
5. **国产数据源要直连。** 本机 env 里 `http_proxy=127.0.0.1:7897`（Clash），打国内站点会挂。任何对国内站点的请求前先 `for k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','all_proxy'): os.environ.pop(k, None)`。已有先例：`agent/feed.py: proxy_env_fix()`。
6. **每个外部调用都要有超时。** 服务里"挂住"比"报错"糟糕得多。已有先例：`agent/feed.py: _with_timeout()`。
7. **JSON 安全。** 返回给上层的数一律过 `agent/feed.py: _num()`（NaN/inf/numpy 标量 → None）。`json.dumps(..., allow_nan=False)` 必须能过。
8. **列名按名字映射，不按位置取。** 先例 `agent/feed.py: to_canon()`。
9. 注释写**为什么**，不写**是什么**。这个项目的注释密度很高，且都是"踩过的坑"，请保持这个风格：实测数字要带日期和机器。

---

## 1. 现状（不要重复造）

```
app.py                  Flask 站点，506 行，路由 + SSE
agent/runner.py         Kronos 引擎（★禁改）
agent/tools.py          8 把工具的注册表 REGISTRY + SCHEMAS + call()
agent/llm.py            DeepSeek tool-calling 循环 + SSE 事件流 + 系统提示词
agent/feed.py           行情/基本面数据层（现在主源是 akshare）★本次要换源
agent/news/             资讯采集三层（akshare / crawl4ai / MediaCrawler）
agent/indicators.py     日线技术指标
agent/graph_kb.py       知识图谱（255 词条，terms.json + 解释.md 是唯一真源）
agent/score.py/ledger.py/accuracy.py   预测台账与回测
agent/portfolio*.py     组合体检
web/src/**              React 18 + Tailwind 4 + Tremor 风格组件 + Recharts
web/dist/**             入库的构建产物（跑站不需要 npm）
verify_agent.py         12525 行判据（★禁改，专人负责）
```

**板块/分钟线/实时全市场/关联检测，现在一行都没有。** 本次全新增。

---

## 2. 已定选型：数据源（**2026-09-22 全量实测后修订，推翻初版**）

用户要求：**必须比 akshare 强，且要国内外所有股票的实时数据。**

### 2.1 上游可达性矩阵（本机，直连无代理，逐条打真实端点）

| 上游 | 结果 | 备注 |
|---|---|---|
| 腾讯 `qt.gtimg.cn`（批量实时） | ✅ 0.35s | A股/港股/美股都能批量 |
| 腾讯 `web.ifzq.gtimg.cn/appstock/app/fqkline/get` | ✅ 0.30s | 日K前复权；港/美股分钟 |
| 新浪 `hq.sinajs.cn`（实时） | ✅ 0.18s | |
| 新浪 `vip.stock.finance.sina.com.cn/.../CN_MarketData.getKLineData` | ✅ 0.25s | **`scale=5/15/30/60/240` 直接给分钟线与日线** |
| 同花顺 `d.10jqka.com.cn` | ✅ 0.34s | |
| 东财 **网页** `quote.eastmoney.com` | ✅ 200 | 但 API 不通，见下 |
| 东财 **API** `push2his/push2.eastmoney.com` | ❌ | `ConnectionError: RemoteDisconnected`，0.2s 即被断连 |
| 百度股市通 `finance.pae.baidu.com` | ❌ 403 | `isCaptchaEnabled: true`（风控） |
| 雪球 `stock.xueqiu.com` | ❌ 400 | 需 cookie |
| Yahoo `query1.finance.yahoo.com` | ❌ 403 | |

**由此推翻初版选型（这是本次最重要的修正）：**
- **`efinance` 整包是纯东财后端 ⇒ 在这台机器上完全不可用**，已从方案中删除。
- **adata 的 `get_market`（日K）也是纯东财 ⇒ 不可用。** 实测 0.1s 返 0 行，异常被它自己的
  `handler_null` 装饰器（`adata/common/exception/handler.py`，`except Exception: return pd.DataFrame()`）吞掉，
  所以**看起来像"没数据"，实际是"连接被拒"**。
- adata 的 `get_market_min` 能出 266 行，是因为它**内部降级到了百度**（返回列是
  `trade_time/price/avg_price` 形态，不是东财的 OHLC）。可用，但要清楚它靠谁活着。
- adata 的**指数**通路可用：`get_market_index('000300')` 1630 行、`get_market_index_min` 241 行。

### 2.2 采用的多源链（逐层降级，与既有"源链"纪律一致）

| 数据 | 主源 | 副源 / 兜底 |
|---|---|---|
| A股 日K（前复权） | 腾讯 `fqkline`（取 `qfqday` 字段） | 新浪 `scale=240`、akshare |
| A股 分钟K 5/15/30/60 | **新浪 `scale=5/15/30/60`**（直接给，**不用重采样**） | — |
| A股 实时（全市场） | 腾讯 `qt.gtimg.cn/q=` 批量 | 新浪 `hq.sinajs.cn` |
| 港股 日K / 分钟 | 腾讯 `fqkline`（`hk00700`: `day` / `m5`） | — |
| 美股 日K / 分钟 | 腾讯 `fqkline`（`usAAPL.OQ`: `day` / `m5`） | — |
| 港股 / 美股 实时 | 腾讯 `qt.gtimg.cn/q=hk00700`、**`q=usAAPL`（纯代码，不带 `.OQ`）** | — |
| 指数 | 腾讯 `fqkline`（`sh000001` / `hkHSI` / `usIXIC`） | adata `get_market_index` |
| 板块 / 成分股 | **待探**（A1 继续） | adata（走百度兜底） |
| 资金流 | **待探** | adata |
| 加密 | OKX / Binance 直连（`agent/feed.py: _fetch_crypto` 已有） | 已有 |

**实测数字**（2026-09-22，本机）：
- 腾讯 A股日K 深度：请求 400 → 实到 401 根；请求 800 → 801 根；请求 2000 → **被服务端截到 641 根**。
- 新浪 A股日K 深度：请求 1023 → 实到 1023 根（**比腾讯深**）。
- 腾讯实时批量：一次 60 个代码 → 命中 52 条（**不存在的代码静默缺条** ⇒ 必须**按 code 对齐，不许按位置取**）。
- 腾讯实时字段以 `~` 分隔、**GBK 编码**，`v_usAAPL=...` 里第 3 段才是真代码（`AAPL.OQ`）。

**★腾讯的 A股分钟是坏的，别在这上面浪费时间**：`fqkline` 对 A股 `m5/m15/m30` 返回
`{"code":1,"msg":"bad params"}`；改用 `appstock/app/kline/mkline` 会被 **301 跳到 `web3.ifzq.gtimg.cn`，
而该主机 DNS 不解析**（`NameResolutionError`）。港股/美股走 `fqkline` 的 `m5` 是正常的。
**结论：A股分钟一律走新浪 `scale`；港美股分钟走腾讯 `fqkline`。**

### 2.3 纪律

- `agent/datasource/health()` 必须**如实反映"东财不可达"**这件事，不许静默吞；前端要显示。
- 每个源的真实端点、参数、返回列名、耗时写进模块 docstring（**带日期 + 机器**）。
- 列名按名字映射，不按位置（先例 `agent/feed.py: to_canon()`）。
- **不许删 akshare**。它的日K现在实际走的就是新浪，是最后的兜底。
- 上面每条都是实测量出来的；**如果新测结果与此表冲突，以新测为准，并回来改这张表**。

---

## 3. 接口冻结（**逐字遵守，别改名**）

### 3.1 `agent/datasource/`（A1 负责，其他人只调用不修改）

```python
# agent/datasource/__init__.py —— 唯一出口，别人只 import 这个包
CANON = ['dt','open','high','low','close','volume','amount']   # 与 feed.py 一致

def daily(market, code, days=400, adjust='qfq') -> pd.DataFrame
    """日K。返回 CANON 列、dt 升序、attrs['source']=源名。取不到抛 SourceError。"""

def minute(market, code, period=5, days=5) -> pd.DataFrame
    """分钟K。period ∈ {1,5,15,30,60}。同上契约。attrs['period']=period。"""

def realtime(market='cn') -> list[dict]
    """全市场实时快照。market ∈ {'cn','hk','us','crypto'}。
    每条：{code,name,market,price,open,high,low,prev_close,pct,volume,amount,
           turnover,pe,ts}。取不到的字段是 None，**不是 0**。"""

def snapshot(codes) -> list[dict]
    """指定代码的实时快照，字段同 realtime()。codes 可跨市场。"""

def boards(kind='concept') -> list[dict]
    """板块清单。kind ∈ {'concept','industry'}。
    每条：{code,name,kind,pct,amount,vol_ratio,lead_code,lead_name,ts}。"""

def board_members(board_code, kind='concept') -> list[str]
    """板块成分股代码。"""

def stock_boards(code) -> list[dict]
    """一只股票所属的全部板块：[{code,name,kind}]。"""

def capital_flow(code, period='daily') -> pd.DataFrame
    """资金流。period ∈ {'daily','min'}。列名按上游如实返回，另加 attrs['source']。"""

def trade_calendar() -> list[str]
    """交易日 YYYY-MM-DD 列表。"""

def health() -> dict
    """{"sources":[{"id","ok","ms","err","n"}]} —— 采集健康度，前端要显示。"""
```

**约束**
- 这一层是**纯取数**：不缓存业务语义、不下结论、不分析。缓存可以放这里（键=源+市场+代码+参数）。
- 每一层源失败要**如实记进 `health()`**，不许静默吞。
- 桩：`LICAI_FEED_STUB` 已有的语义必须继续工作（`agent/feed.py: _stub_frame` 那套可复现性要求：显式播种、同参同帧）。分钟线与板块也要有桩，否则判据没法离线跑。

### 3.2 `agent/intraday/`（A2 负责）

```python
# agent/intraday/__init__.py
def analyze(market, code, periods=(5,15,30), name=None) -> dict
```
返回结构（逐字）：
```python
{
 'ok': True, 'market':..., 'code':..., 'name':..., 'ts': '2026-09-22 14:30',
 'bars': {'5': 240, '15': 96, '30': 48},          # 每周期实际拿到多少根
 'regime': {'5':'trend_up','15':'range',...},      # trend_up|trend_down|range|squeeze
 'signals': [                                       # 每个策略一条
    {'id':'orb_break','name':'开盘区间突破','period':5,'side':'long',
     'strength':0.72,'why':'...','refs':{'level':12.34,'rvol':2.1}},
 ],
 'composite': {'score': 0.35, 'side':'long'|'short'|'flat',
               'confidence':0.61, 'agree':{'long':4,'short':1,'flat':2}},
 'volume': {'rvol': 2.1, 'rvol_by_period': {...},
            'profile': {'poc':12.30,'va_hi':12.45,'va_lo':12.10},
            'divergence': 'bearish'|'bullish'|'none'},
 'levels': {'entry':..., 'stop':..., 'target1':..., 'target2':...,
            'atr':..., 'rr':2.1, 'method':'...'},
 'session': {'phase':'morning'|'midday'|'afternoon'|'close',
             'window':'09:45-10:30', 'note':'...'},
 'errors': [],
}
```
**必须实现的策略（每个都是真实算法，不许糊弄）**
1. 开盘区间突破 ORB（前 N 分钟高低点 + 突破确认 + 失败回撤）
2. VWAP 偏离与回归（含成交量确认）
3. 相对成交量 RVOL 分级（>1.5/>2.5/>4.0 三档语义）
4. Volume Profile（POC / 价值区 VA / 高低量节点）
5. 量价背离（OBV 或 价-量 滚动相关）
6. 短周期动量（EMA 斜率 + RSI + ROC）
7. 布林带挤压/扩张（squeeze → breakout）
8. 多周期共振（5/15/30 方向一致性打分）
9. ATR 止损/目标位
10. 时段效应（开盘 30 分/午盘/尾盘的统计性差异，**要在注释里写明样本与来源**）

**纪律**
- 每个策略的 `why` 必须是**可复核的算术**（带上具体价位/倍数），不许写"技术面偏强"这种话。
- 算不出来的策略（数据不够）**整条不出现**，并在 `errors` 里写明少了什么。
- 分数是加权求和，权重必须是模块级常量且注释里写清取值理由。

### 3.3 `agent/linkage/`（A3 负责）—— **重采集、弱分析**

```python
# agent/linkage/__init__.py
def scan(scope='all', codes=None, top=20) -> dict
def anomaly_of(code) -> dict
```
返回结构：
```python
{
 'ok': True, 'ts':..., 'snapshot':{'ts':..., 'market':'cn', 'rows': 5400},
 'collected': {                    # ★这一块是重点：把"采到了什么"完整交代
    'sources':[{'id':'adata.realtime','ok':True,'n':5400,'ms':812,'err':None}],
    'ts':..., 'rows':..., 'markets':['cn','hk','us'],
 },
 'anomalies':[                     # 异动榜
    {'code':..., 'name':..., 'market':'cn',
     'kinds':['volume_spike','pct_spike'],      # 可多标签
     'metrics':{'pct':6.2,'vol_ratio':4.1,'amount_yi':12.3,'turnover':8.8,
                'z_pct':2.9,'z_vol':3.4},
     'ts':...},
 ],
 'links':{                         # code → 关联物
    '600519': {
      'boards':[{'code':...,'name':'白酒','kind':'concept','pct':1.2,
                 'lead_code':...,'lead_name':...}],
      'peers':[{'code':...,'name':...,'rel':'同板块'|'高相关'|'领先',
                'rel_value':0.87, 'lag_min':0, 'pct':..., 'vol_ratio':...,'ts':...}],
      'evidence':['同属 白酒/消费50 两个概念板块','20 日收益相关性 0.87'],
    },
 },
 'note': '...',                    # 一段**短**说明，弱化分析：只说事实，不下买卖判断
}
```
**纪律（这是本模块最容易被做歪的地方）**
- **采集是主角。** `collected` 必须详实：哪个源、几行、几毫秒、什么错。用户明确说"注重采集、弱化分析"。
- **分析只做最朴素的一层**：涨跌幅、量比、z-score、滚动相关、lead-lag。**不写长论述、不给买卖建议、不预测**。
- 相关性必须给**窗口和样本数**，样本不足（<60）就不给这个数，不要假精确。
- 关联必须带**证据字符串**（"同属 X 板块"、"20 日相关 0.87"），不许只说"相关性强"。

### 3.4 `agent/agents/`（A4 负责）

```python
# agent/agents/__init__.py
ROLES = ('bull','bear','fundamental','technical','sentiment','risk','pm','kg_tutor','psych')

def run(question, history=None, profile=None, emit=None, transport=None,
        deep=False) -> dict
```
- `emit(dict)` 是 SSE 事件回调，**必须在 `done` 之前发完所有事件**（既有纪律：一轮以 done 收尾）。
- 事件类型（**新增，别和既有的混**）：
```python
{'type':'agent_round','round':1,'roles':[...]}
{'type':'agent_start','role':'bull','name':'多头分析师','icon':'...'}
{'type':'agent_say','role':'bull','round':1,'text':'...','stance':0.7,
 'conf':0.6,'evidence':[...],'ms':1234}
{'type':'agent_done','role':'bull','ok':True,'ms':...}
{'type':'agent_weights','w':{'bull':0.18,...},'scenario':'trend_up','basis':{...}}
{'type':'agent_verdict','side':'long'|'short'|'flat','score':0.31,
 'confidence':0.58,'reasons':[...],'dissent':[...],'veto':None|{...},
 'risk_note':'...不构成投资建议'}
{'type':'done','ms':...,'steps':...}
```
- **博弈**：至少两轮。第 1 轮各角色独立出观点；第 2 轮多头与空头**拿到对方的论点**后必须**要么修正自己的 stance 数值、要么写出一条反驳**（反驳要写在 `text` 里，并且 `stance` 变化要能看出来）。
- **动态权重**（`agent/agents/weights.py`）：权重至少受三个因素影响，每个都要在 `basis` 里给出数值依据：
  1. **场景**（趋势/震荡/危机）—— 由 `agent/intraday` 的 regime + 波动率决定
  2. **历史校准** —— 每个角色的历史命中率，来自台账（`agent/ledger.py`），无数据时退回先验
  3. **本轮自报置信度** —— 且要做**校准收缩**，不许让自报置信度直接当权重
- **独立风控官有一票否决/降级权**：命中风控条件时 `veto` 非空，且**最终 verdict 必须体现**（降级为 flat 或强制加风险提示）。风控条件必须是**可复核的数值条件**。
- **`kg_tutor`**：知识图谱解释智能体。消费 `agent/graph_kb.py`（255 词条 + 属/对比/前提三种关系）。它回答概念问题，并要能把用户带到图谱页的具体词条。
- **`psych`**：投资心理智能体。行为金融视角（损失厌恶、处置效应、锚定、过度自信、FOMO、羊群、心理账户）。**它说的话不是诊断**，要明确写成"常见偏差的提示"，且不许据此给建议。
- **硬规矩必须继承**：出现任何价格走向内容 ⇒ 结尾必须有风险提示且**原样含「不构成投资建议」**；没数据就说没数据。
- 传输层：**必须支持注入假 transport**（先例 `agent/llm.py: TRANSPORT` + `_stub_transport`），判据靠它离线跑。

### 3.5 `agent/collector/`（A5 负责，第二批）—— 常驻采集器

```python
# agent/collector/__init__.py
def once(scope='all') -> dict      # 采一轮，落盘，返回本轮统计
def status() -> dict               # 采集器健康状况
def ring(limit=120) -> list[dict]  # 最近的快照索引
```
- 落盘到 `data/collect/`，**事件日志、只追加**（先例 `agent/ledger.py` 的落盘纪律）。
- 采集：全市场实时快照（cn/hk/us/crypto）、板块实时、异动候选、资讯。
- **这是个"通信采集"装置**：它只负责**采、存、记健康度**，不做判断。
- 不许无条件重建/整份重写文件（先例：整份备份写回会抹掉并发写）。

### 3.6 前端（A6/A7 负责）

- 新增页面路由：`/agents`（多智能体博弈）、`/intraday`（分钟策略）、`/linkage`（关联检测）、`/collect`（采集监视）、`/psych`（心理）
- **双主题**：默认深色，可切浅色。所有颜色走 CSS 变量/design token，**不许在组件里写死色值**。
- 必须用到的 skill：`frontend-ui-engineering`、`tremor-design-system`、`tailwind-4-docs`、`dataviz`。
- ★已知坑（务必读）：Tremor 的 `AreaChart` 包在 `ResponsiveContainer` 里时**同步 tick 出不了图**（ResizeObserver 异步）—— 需要"定尺寸分支"。已有先例在 `web/src/utils/ChartSizer.tsx`。
- ★`graph.html` 是**硬钉深色**的（已知取舍），不要动它的主题。
- ★改 `web/src/**` 之后必须 `cd web && npm run build`（产物入库，`run.sh` 会检查 dist 是否比 src 新）。

---

## 4. 集成点（**协调者本人负责，子代理不要改**）

- `agent/tools.py` 的 `REGISTRY` / `SCHEMAS` 新增工具入口
- `agent/llm.py` 的系统提示词与 `data_events` 的 `CHART_KINDS`
- `app.py` 的路由
- `verify_agent.py` 的判据
- `README.md`

子代理需要这些改动时：**在交付说明里逐条写清楚"文件:行号 要加什么"**，不要自己动手。

---

## 5. 交付说明格式（每个子代理结尾必须给）

```
## 我建/改了哪些文件
- <路径>  <一句话>
## 实测过什么（命令 + 真实输出摘要）
## 我没做的 / 没验的（★这一节最值钱，不许写"无"）
## 需要协调者改的集成点（文件:行号 + 具体内容）
## 期间踩到的坑
```
