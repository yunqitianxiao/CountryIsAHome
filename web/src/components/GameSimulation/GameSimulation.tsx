import React, { useState, useEffect } from "react";
import InvestmentChart from '../Charts/InvestmentChart';

interface Holding {
  symbol: string;
  name: string;
  shares: number;
  avgPrice: number;
  currentPrice: number;
}

interface SimulationResult {
  symbol: string;
  initialAmount: number;
  buyDate: string;
  currentValue: number;
  profitLoss: number;
  profitLossPercent: number;
  historicalData: Array<{
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
  }>;
}

interface SimulationData {
  result: SimulationResult;
  holdings: Holding[];
}

interface GameSimulationProps {
  initial?: SimulationData;
  probe?: boolean;
}

const GameSimulation: React.FC<GameSimulationProps> = ({ initial, probe }) => {
  const [simulationData, setSimulationData] = useState<SimulationData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    const fetchSimulationData = async () => {
      if (probe && initial) {
        // 使用探针提供的数据
        setSimulationData(initial);
        setLoading(false);
      } else {
        // 模拟获取数据
        setTimeout(() => {
          const mockData: SimulationData = {
            result: {
              symbol: "600519",
              initialAmount: 10000,
              buyDate: "2026-08-01",
              currentValue: 11250,
              profitLoss: 1250,
              profitLossPercent: 12.50,
              historicalData: [
                { date: "2026-08-01", open: 1750.50, high: 1780.20, low: 1745.80, close: 1775.30 },
                { date: "2026-08-02", open: 1775.30, high: 1795.60, low: 1760.40, close: 1788.70 },
                { date: "2026-08-03", open: 1788.70, high: 1805.20, low: 1780.10, close: 1795.40 },
                { date: "2026-08-04", open: 1795.40, high: 1810.80, low: 1785.20, close: 1805.60 },
                { date: "2026-08-05", open: 1805.60, high: 1820.30, low: 1800.10, close: 1815.70 },
                { date: "2026-08-06", open: 1815.70, high: 1830.40, low: 1810.20, close: 1825.80 },
                { date: "2026-08-07", open: 1825.80, high: 1840.50, low: 1820.30, close: 1835.90 },
                { date: "2026-08-08", open: 1835.90, high: 1850.60, low: 1830.40, close: 1845.10 },
                { date: "2026-08-09", open: 1845.10, high: 1860.70, low: 1840.50, close: 1855.20 },
                { date: "2026-08-10", open: 1855.20, high: 1870.80, low: 1850.60, close: 1865.30 },
                { date: "2026-08-11", open: 1865.30, high: 1880.90, low: 1860.70, close: 1875.40 },
                { date: "2026-08-12", open: 1875.40, high: 1890.10, low: 1870.80, close: 1885.50 },
                { date: "2026-08-13", open: 1885.50, high: 1900.20, low: 1880.90, close: 1895.60 },
                { date: "2026-08-14", open: 1895.60, high: 1910.30, low: 1890.10, close: 1905.70 },
                { date: "2026-08-15", open: 1905.70, high: 1920.40, low: 1900.20, close: 1915.80 },
              ]
            },
            holdings: [
              {
                symbol: "600519",
                name: "贵州茅台",
                shares: 5,
                avgPrice: 1800.50,
                currentPrice: 1850.50
              },
              {
                symbol: "000001",
                name: "平安银行",
                shares: 1000,
                avgPrice: 12.45,
                currentPrice: 12.65
              }
            ]
          };
          setSimulationData(mockData);
          setLoading(false);
        }, 500);
      }
    };

    fetchSimulationData();
  }, [initial, probe]);

  if (loading && !probe) {
    return (
      <div className="flex justify-center items-center min-h-[400px]">
        <div className="luxury-loader"></div>
      </div>
    );
  }

  if (!simulationData) {
    return (
      <div className="card">
        <div className="card chd">
          <h2>练手结果</h2>
        </div>
        <div className="p-4">
          <p className="text-gray-500">还没有练手数据</p>
        </div>
      </div>
    );
  }

  const { result, holdings } = simulationData;

  return (
    <div className="center-large-container">
      <div className="center-large-card">
        <div className="luxury-grid">
      {/* 投资摘要卡片 */}
      <div className="card glow-effect">
        <div className="card chd">
          <h2>概览</h2>
        </div>
        <div className="p-4">
          <div className="text-center mb-4">
            <div className="num-hero text-up">{result.profitLossPercent >= 0 ? '+' : ''}{result.profitLossPercent.toFixed(2)}%</div>
            <div className="text-sm text-gray-400 mt-1">收益情况</div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="text-center">
              <div className="num-lg">{result.initialAmount.toLocaleString()}</div>
              <div className="text-sm text-gray-400">初始本金</div>
            </div>
            <div className="text-center">
              <div className={`num-lg ${result.currentValue >= result.initialAmount ? 'text-up' : 'text-down'}`}>
                {result.currentValue.toLocaleString()}
              </div>
              <div className="text-sm text-gray-400">现在值多少</div>
            </div>
          </div>

          <div className="mt-4 pt-4 border-t border-gray-700">
            <div className="text-center">
              <div className={`num-lg ${result.profitLoss >= 0 ? 'text-up' : 'text-down'}`}>
                {result.profitLoss >= 0 ? '+' : ''}{result.profitLoss.toLocaleString()}
              </div>
              <div className="text-sm text-gray-400">盈亏</div>
            </div>
          </div>
        </div>
      </div>

      {/* 投资图表 */}
      <div className="col-span-full">
        <InvestmentChart
          data={result.historicalData}
          title={`${result.symbol} - ${holdings.find(h => h.symbol === result.symbol)?.name || '走势'} (练手)`}
          width={800}
          height={500}
        />
      </div>

      {/* 持仓详情 */}
      <div className="col-span-full">
        <div className="card luxury-border">
          <div className="card chd">
            <h2>当前持有</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-600">
                  <th className="text-left py-2 px-3">代码</th>
                  <th className="text-left py-2 px-3">名称</th>
                  <th className="text-right py-2 px-3">数量</th>
                  <th className="text-right py-2 px-3">买入均价</th>
                  <th className="text-right py-2 px-3">现价</th>
                  <th className="text-right py-2 px-3">现在值</th>
                  <th className="text-right py-2 px-3">盈亏</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((holding, index) => {
                  const currentValue = holding.shares * holding.currentPrice;
                  const costValue = holding.shares * holding.avgPrice;
                  const plPercent = ((holding.currentPrice - holding.avgPrice) / holding.avgPrice) * 100;

                  return (
                    <tr key={index} className="border-b border-gray-700">
                      <td className="py-2 px-3 font-mono">{holding.symbol}</td>
                      <td className="py-2 px-3 font-medium">{holding.name}</td>
                      <td className="py-2 px-3 text-right">{holding.shares}</td>
                      <td className="py-2 px-3 text-right">{holding.avgPrice.toFixed(2)}</td>
                      <td className="py-2 px-3 text-right">{holding.currentPrice.toFixed(2)}</td>
                      <td className="py-2 px-3 text-right">{currentValue.toFixed(2)}</td>
                      <td className={`py-2 px-3 text-right ${plPercent >= 0 ? 'text-up' : 'text-down'}`}>
                        {plPercent >= 0 ? '+' : ''}{plPercent.toFixed(2)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* 操作历史 */}
      <div className="col-span-full">
        <div className="card luxury-border">
          <div className="card chd">
            <h2>操作记录</h2>
          </div>
          <div className="p-4">
            <div className="space-y-3">
              <div className="flex items-center justify-between p-3 rounded-lg bg-gray-800">
                <div>
                  <div className="font-medium">买入 600519 贵州茅台</div>
                  <div className="text-sm text-gray-400">2026-08-01</div>
                </div>
                <div className="text-right">
                  <div className="font-medium">5 股</div>
                  <div className="text-sm">¥{1800.50}/股</div>
                </div>
              </div>

              <div className="flex items-center justify-between p-3 rounded-lg bg-gray-800">
                <div>
                  <div className="font-medium">买入 000001 平安银行</div>
                  <div className="text-sm text-gray-400">2026-08-05</div>
                </div>
                <div className="text-right">
                  <div className="font-medium">1000 股</div>
                  <div className="text-sm">¥{12.45}/股</div>
                </div>
              </div>

              <div className="flex items-center justify-between p-3 rounded-lg bg-gray-800">
                <div>
                  <div className="font-medium">买入 000858 五粮液</div>
                  <div className="text-sm text-gray-400">2026-08-10</div>
                </div>
                <div className="text-right">
                  <div className="font-medium">200 股</div>
                  <div className="text-sm">¥{175.20}/股</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
        </div>
      </div>
    </div>
  );
};

export default GameSimulation;