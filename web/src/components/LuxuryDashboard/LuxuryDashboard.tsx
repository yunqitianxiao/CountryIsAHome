import React, { useState, useEffect } from "react"

import { Callout } from "../Callout/Callout"

interface FinancialData {
  symbol: string
  name: string
  price: number
  change: number
  changePercent: number
  volume: number
  marketCap: number
  peRatio: number
  dividendYield: number
}

interface DashboardProps {
  /** 真数据。由页面（`main.tsx` 的 `case "luxuryDash"`）传进来。
   *  ★这个 prop 原来就有、但**从来没被读过** —— 组件自己造了一份数据。
   *    现在它才是唯一的数据入口。 */
  initial?: FinancialData[]
  probe?: boolean
}

/* ── 探针夹具（**只在 probe 那条路上用**）─────────────────────────────────
   ★这一段必须讲清楚，因为它是这一版改动最实质的一处：
     原来**无论 probe 与否**，这个组件都会把下面这四个标的的价格、
     涨跌额、成交量、市值当成"今日表现"渲染出来（非探针那条路只是多等 500ms）。
     用户在原页面上看到的"贵州茅台 ¥1850.50 +1.39%"不是行情，是**写死的常量**，
     而页面上没有任何一个字说明这一点。这个仓库的第一条铁律就是
     **不许把编的数当数据摆出来**（README 里"回测没有超额能力"那一段整段
     就是在纠这件事），所以这一版把那份常量收进 `probe` 分支：
     探针需要一份**每趟都一样**的输入才能量几何，那是夹具的正当用途；
     真实访问则走"没有数据源"那一屏（见下面 `if (!rows.length)`）。
   ★另外注意这四个数里 `volume` / `marketCap` 差距极大（茅台 125000 手 vs
     平安银行 8900000），那是原来随手写的，不是任何时刻的真实值 ——
     所以它也不能被"降级成示例数据"留在真实路径上。 */
const PROBE_FIXTURE: FinancialData[] = [
  {
    symbol: "600519",
    name: "贵州茅台",
    price: 1850.5,
    change: 25.3,
    changePercent: 1.39,
    volume: 125000,
    marketCap: 2330000000000,
    peRatio: 25.6,
    dividendYield: 1.2,
  },
  {
    symbol: "000001",
    name: "平安银行",
    price: 12.45,
    change: -0.15,
    changePercent: -1.19,
    volume: 8900000,
    marketCap: 242000000000,
    peRatio: 8.9,
    dividendYield: 7.8,
  },
  {
    symbol: "SH000300",
    name: "沪深300",
    price: 4025.67,
    change: 42.35,
    changePercent: 1.06,
    volume: 0,
    marketCap: 0,
    peRatio: 15.2,
    dividendYield: 2.1,
  },
  {
    symbol: "000858",
    name: "五粮液",
    price: 172.3,
    change: 3.2,
    changePercent: 1.89,
    volume: 2100000,
    marketCap: 670000000000,
    peRatio: 23.4,
    dividendYield: 0.9,
  },
]

export const LuxuryDashboard: React.FC<DashboardProps> = ({ initial, probe }) => {
  const [financialData, setFinancialData] = useState<FinancialData[]>([])
  const [loading, setLoading] = useState<boolean>(true)

  /* ★原来这里是一个假的 `fetchData`（注释写着"模拟API调用"，
     实质是 `setTimeout` 之后再 set 同一份常量）。
     它没有"以后会换成真接口"的痕迹 —— 是一份永远返回常量的函数，
     所以这一版直接删掉，改成"有 `initial` 就用、没有就是空"。
     ★探针那条路上**同步**取好再渲染（`initial`/夹具都在首帧就位），
       否则探针量到的是加载态 —— 那不是判据变红，是判据**消失**。 */
  useEffect(() => {
    if (probe) {
      setFinancialData(PROBE_FIXTURE)
      setLoading(false)
      return
    }
    setFinancialData(initial ?? [])
    setLoading(false)
  }, [probe, initial])

  const rows = financialData

  if (loading && !probe) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="luxury-loader" role="status" aria-label="正在载入"></div>
      </div>
    )
  }

  /* ── 没有数据源时的那一屏 ────────────────────────────────────────────
     ★**不能只留白**（"空屏"和"坏了"在用户眼里是同一件事，也不告诉人下一步做什么）。
     这一段说的是实话：这一页要的行情/市值/市盈率来自 `api2.ts` 那条通道，
     而那条通道现在取不到数（后端还没上线）。 */
  if (!rows.length) {
    return (
      <div className="center-large-container">
        <div className="center-large-card">
          <h2>这一页还没有接上行情数据</h2>
          <p className="text-muted">
            卡片的骨架都在，但行情、成交量、市值、市盈率要等行情通道接上才会出现。
          </p>
          <Callout variant="warning" title="这里现在什么都不显示，是有意的">
            以前这一页摆的是四个写死的标的（含价格和涨跌幅），页面上没说明它们不是行情。
            与其摆一份看不出真假的数，不如让这里空着。
          </Callout>
        </div>
      </div>
    )
  }

  const positiveCount = rows.filter((item) => item.changePercent > 0).length
  const negativeCount = rows.filter((item) => item.changePercent < 0).length
  const avgChange =
    rows.length > 0
      ? rows.reduce((sum, item) => sum + item.changePercent, 0) / rows.length
      : 0
  const totalVolume = rows.reduce((sum, item) => sum + item.volume, 0)

  return (
    <div className="center-large-container">
      <div className="center-large-card">
        <div className="luxury-grid">
          {/* 总览卡片 */}
          <div className="card glow-effect">
            <div className="card chd">
              <h2>今日表现</h2>
            </div>
            <div className="p-4">
              <div className="num-hero text-center mb-4">
                {positiveCount}/{rows.length} 上涨
              </div>
              <div className="mb-4">
                {/* ★这是一条**真进度**（上涨家数占比），所以给它 progressbar 语义。
                    ★它不是分位条：分位条不能用涨跌色，这一条恰恰是涨跌占比。 */}
                <div
                  className="luxury-progress-bar"
                  role="progressbar"
                  aria-label="上涨家数占比"
                  aria-valuenow={positiveCount}
                  aria-valuemin={0}
                  aria-valuemax={rows.length}
                >
                  <div
                    className="luxury-progress-fill"
                    style={{ width: `${(positiveCount / rows.length) * 100}%` }}
                  ></div>
                </div>
              </div>
              {/* ★原来是 `text-gray-400`。gray-* 在 `index.css` 的 `@theme` 里被
                  收编成**固定**色阶（不随主题翻转）⇒ 固定灰在暗色底上压不住。
                  `text-muted` 是过 AA 的那一档，两套主题各一份。 */}
              <div className="flex justify-between text-sm text-muted">
                <span>上涨: {positiveCount}</span>
                <span>下跌: {negativeCount}</span>
              </div>
            </div>
          </div>

          <div className="card glow-effect">
            <div className="card chd">
              <h2>平均涨跌幅</h2>
            </div>
            <div className="p-4">
              {/* ★`text-up`/`text-down` 是站点 token（红涨绿跌，随主题翻转）。
                  这个判断原来就是对的 —— 坏的是**同一个文件里的徽章**用了
                  绿涨红跌，于是"平均 +1.5%"是红的、旁边那枚 "+1.39%" 是绿的。 */}
              <div
                className={`num-hero text-center mt-2 ${avgChange >= 0 ? "text-up" : "text-down"}`}
              >
                {avgChange >= 0 ? "+" : ""}
                {avgChange.toFixed(2)}%
              </div>
              <div className="luxury-progress-bar mt-4">
                <div
                  className="luxury-progress-fill"
                  style={{ width: `${Math.min(Math.abs(avgChange) * 10, 100)}%` }}
                ></div>
              </div>
            </div>
          </div>

          <div className="card glow-effect">
            <div className="card chd">
              <h2>市场热度</h2>
            </div>
            <div className="p-4">
              <div className="num-hero text-center mt-2">
                {totalVolume.toLocaleString()}
              </div>
              <div className="text-center text-sm text-muted mt-2">成交量</div>
            </div>
          </div>

          <div className="card glow-effect">
            <div className="card chd">
              <h2>市值排名</h2>
            </div>
            <div className="p-4">
              <div className="space-y-3">
                {[...rows]
                  .sort((a, b) => b.marketCap - a.marketCap)
                  .slice(0, 2)
                  .map((item) => (
                    <div
                      key={item.symbol}
                      className="flex justify-between items-center"
                    >
                      <div>
                        <div className="font-medium">{item.name}</div>
                        <div className="text-sm text-muted">{item.symbol}</div>
                      </div>
                      <div className="text-right">
                        <div className="num-lg">
                          {item.marketCap.toLocaleString(undefined, {
                            maximumFractionDigits: 0,
                          })}
                        </div>
                        {/* ★这两枚徽章原来是 `bg-emerald-900/30 text-emerald-400`
                            （涨）与 `bg-rose-900/30 text-rose-400`（跌）——
                            **绿涨红跌**，正好与站点的红涨绿跌相反，
                            也与同一张卡里 `text-up`/`text-down` 的数字相反。
                            改成 `--status-up`/`--status-down` 的直通。
                            ★用 `status-*` 而不是 `up`/`down` 是措辞问题：
                            这不是"错误/成功"，是"涨/跌"，`Badge` 那两档
                            （`error`=涨、`success`=跌）在这里会读成褒贬。 */}
                        <span
                          className={`inline-block px-2 py-1 rounded-full text-xs ${
                            item.changePercent >= 0
                              ? "bg-status-up/10 text-status-up"
                              : "bg-status-down/10 text-status-down"
                          }`}
                        >
                          {item.changePercent >= 0 ? "+" : ""}
                          {item.changePercent.toFixed(2)}%
                        </span>
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          </div>

          {/* 详细的个股数据 */}
          <div className="col-span-full">
            <div className="card luxury-border">
              <div className="card chd">
                <h2>个股详情</h2>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-line">
                      <th className="text-left py-2 px-3">股票代码</th>
                      <th className="text-left py-2 px-3">名称</th>
                      <th className="text-right py-2 px-3">价格</th>
                      <th className="text-right py-2 px-3">涨跌额</th>
                      <th className="text-right py-2 px-3">涨跌幅</th>
                      <th className="text-right py-2 px-3">市盈率</th>
                      <th className="text-right py-2 px-3">股息率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((item) => (
                      <tr
                        key={item.symbol}
                        className="border-b border-line-subtle hover:bg-surface-alt"
                      >
                        <td className="py-2 px-3 font-mono">{item.symbol}</td>
                        <td className="py-2 px-3 font-medium">{item.name}</td>
                        <td className="py-2 px-3 text-right num-lg">
                          {item.price.toFixed(2)}
                        </td>
                        {/* ★金融数字一律 `tabular-nums`（`index.css` 的 `body`
                            已经全局开了 `font-variant-numeric`，这里不重复写）——
                            否则逐行的位数不一样，一列数字看着就是歪的。 */}
                        <td
                          className={`py-2 px-3 text-right ${item.change >= 0 ? "text-up" : "text-down"}`}
                        >
                          {item.change >= 0 ? "+" : ""}
                          {item.change.toFixed(2)}
                        </td>
                        <td
                          className={`py-2 px-3 text-right ${item.changePercent >= 0 ? "text-up" : "text-down"}`}
                        >
                          {item.changePercent >= 0 ? "+" : ""}
                          {item.changePercent.toFixed(2)}%
                        </td>
                        <td className="py-2 px-3 text-right">
                          {item.peRatio.toFixed(2)}
                        </td>
                        <td className="py-2 px-3 text-right">
                          {item.dividendYield.toFixed(2)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* 金融指标卡 */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-4">
            {rows.map((item) => (
              <div key={item.symbol} className="card glow-effect">
                <div className="p-4">
                  <div className="flex justify-between items-start mb-3">
                    <div>
                      <div className="text-lg font-bold">{item.name}</div>
                      <div className="text-muted">{item.symbol}</div>
                    </div>
                    <span
                      className={`inline-block px-2 py-1 rounded-full text-xs ${
                        item.changePercent >= 0
                          ? "bg-status-up/10 text-status-up"
                          : "bg-status-down/10 text-status-down"
                      }`}
                    >
                      {item.changePercent >= 0 ? "+" : ""}
                      {item.changePercent.toFixed(2)}%
                    </span>
                  </div>

                  <div
                    className={`num-lg mb-3 ${item.change >= 0 ? "text-up" : "text-down"}`}
                  >
                    ¥{item.price.toFixed(2)}
                  </div>

                  <div className="grid grid-cols-2 gap-4 mb-4">
                    <div>
                      <div className="text-sm text-muted">市盈率</div>
                      <div className="font-medium">{item.peRatio.toFixed(2)}</div>
                    </div>
                    <div>
                      <div className="text-sm text-muted">股息率</div>
                      <div className="font-medium">
                        {item.dividendYield.toFixed(2)}%
                      </div>
                    </div>
                  </div>

                  <div>
                    <div className="text-sm text-muted mb-2">成交量</div>
                    <div className="w-full bg-well rounded-full h-2 mb-1">
                      {/* ★原来这里是两条**渐变**：`from-emerald-600 to-teal-700`
                          （涨）/ `from-red-600 to-orange-700`（跌）。
                          两个毛病：①同一个意思用了两种色相（涨是"绿→青"），
                          与站点红涨绿跌相反；②渐变是"AI 审美"的招牌外观，
                          而这里表达的是**一个二值状态**，一个色块就够。
                          现在直接用 `--status-up` / `--status-down`。 */}
                      <div
                        className={`h-2 rounded-full ${
                          item.changePercent >= 0
                            ? "bg-status-up"
                            : "bg-status-down"
                        }`}
                        style={{
                          width: `${Math.min((item.volume / 10000000) * 100, 100)}%`,
                        }}
                      ></div>
                    </div>
                    <div className="text-xs text-muted">
                      {(item.volume / 1000000).toFixed(2)}M
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
