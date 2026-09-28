import React, { useState, useEffect } from "react";

interface ChartDataPoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

interface ChartProps {
  data: ChartDataPoint[];
  title?: string;
  width?: number;
  height?: number;
}

const InvestmentChart: React.FC<ChartProps> = ({
  data,
  title = "价格走势图",
  width = 600,
  height = 400
}) => {
  if (!data || data.length === 0) {
    return (
      <div className="chart-container card p-4 flex items-center justify-center" style={{ width: width, height: height }}>
        <p className="text-gray-500">暂无数据</p>
      </div>
    );
  }

  // 简单的EMA计算函数
  const calculateEMA = (prices: number[], period: number): number[] => {
    const ema: number[] = [];
    const k = 2 / (period + 1);

    // 初始化SMA作为第一个EMA值
    let smaSum = 0;
    for (let i = 0; i < period; i++) {
      smaSum += prices[i];
    }
    const initialEMA = smaSum / period;

    for (let i = 0; i < prices.length; i++) {
      if (i < period - 1) {
        ema.push(0); // EMA初始值设为0
      } else if (i === period - 1) {
        ema.push(initialEMA);
      } else {
        const emaValue = prices[i] * k + ema[i - 1] * (1 - k);
        ema.push(emaValue);
      }
    }

    return ema;
  };

  // 获取价格数据
  const prices = data.map(d => d.close);
  const ema12 = calculateEMA(prices, 12);
  const ema26 = calculateEMA(prices, 26);

  // 找到价格范围
  const allPrices = [
    ...data.flatMap(d => [d.high, d.low]),
    ...ema12.filter(val => val !== 0),
    ...ema26.filter(val => val !== 0)
  ].filter(val => val !== undefined) as number[];

  const minPrice = Math.min(...allPrices);
  const maxPrice = Math.max(...allPrices);
  const priceRange = maxPrice - minPrice;

  // 图表边距
  const margin = { top: 30, right: 50, bottom: 50, left: 70 };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;

  // 计算坐标转换函数
  const getX = (index: number) => margin.left + (index / (data.length - 1)) * chartWidth;
  const getY = (value: number) => margin.top + chartHeight - ((value - minPrice) / priceRange) * chartHeight;

  // 创建路径数据
  const createPath = (values: number[], color: string) => {
    const validPoints = values
      .map((value, i) => ({ value, i }))
      .filter(point => point.value !== 0); // 只包括有效的EMA值

    if (validPoints.length < 2) return null;

    let pathData = `M ${getX(validPoints[0].i)} ${getY(validPoints[0].value)}`;
    for (let i = 1; i < validPoints.length; i++) {
      pathData += ` L ${getX(validPoints[i].i)} ${getY(validPoints[i].value)}`;
    }
    return (
      <path
        key={color}
        d={pathData}
        stroke={color}
        strokeWidth="1.5"
        fill="none"
        opacity="0.8"
      />
    );
  };

  return (
    <div className="advanced-chart-container">
      <h3 className="advanced-chart-title">{title}</h3>
      <div className="chart-content" style={{ width: width, height: height }}>
        <svg width={width} height={height}>
          {/* X轴 */}
          <line
            x1={margin.left}
            y1={height - margin.bottom}
            x2={width - margin.right}
            y2={height - margin.bottom}
            stroke="currentColor"
            strokeWidth="1"
            opacity="0.3"
          />

          {/* Y轴 */}
          <line
            x1={margin.left}
            y1={margin.top}
            x2={margin.left}
            y2={height - margin.bottom}
            stroke="currentColor"
            strokeWidth="1"
            opacity="0.3"
          />

          {/* 网格线 */}
          {[0, 0.25, 0.5, 0.75, 1].map((ratio, i) => (
            <g key={i}>
              <line
                x1={margin.left}
                y1={margin.top + chartHeight * ratio}
                x2={width - margin.right}
                y2={margin.top + chartHeight * ratio}
                stroke="currentColor"
                strokeWidth="0.5"
                opacity="0.1"
              />
              <text
                x={margin.left - 10}
                y={margin.top + chartHeight * ratio + 4}
                textAnchor="end"
                fontSize="10"
                fill="currentColor"
                opacity="0.6"
              >
                {(minPrice + priceRange * (1 - ratio)).toFixed(2)}
              </text>
            </g>
          ))}

          {/* K线图 */}
          {data.map((d, i) => {
            const x = getX(i);
            const yHigh = getY(d.high);
            const yLow = getY(d.low);
            const yOpen = getY(d.open);
            const yClose = getY(d.close);

            const isUp = d.close >= d.open;
            const bodyTop = isUp ? yClose : yOpen;
            const bodyBottom = isUp ? yOpen : yClose;
            const color = isUp ? "var(--up)" : "var(--down)";

            return (
              <g key={i}>
                {/* 上影线 */}
                <line
                  x1={x}
                  y1={yHigh}
                  x2={x}
                  y2={bodyTop}
                  stroke={color}
                  strokeWidth="1"
                />
                {/* 下影线 */}
                <line
                  x1={x}
                  y1={bodyBottom}
                  x2={x}
                  y2={yLow}
                  stroke={color}
                  strokeWidth="1"
                />
                {/* 实体 */}
                <rect
                  x={x - 3}
                  y={bodyTop}
                  width={6}
                  height={Math.max(1, Math.abs(bodyBottom - bodyTop))}
                  fill={color}
                  stroke={color}
                  strokeWidth="0.5"
                />
              </g>
            );
          })}

          {/* EMA 12 */}
          {createPath(ema12, "#3b82f6")} {/* 蓝色 */}

          {/* EMA 26 */}
          {createPath(ema26, "#8b5cf6")} {/* 紫色 */}

          {/* 图例 */}
          <g transform={`translate(${width - margin.right + 10}, ${margin.top})`}>
            <g transform="translate(0, 0)">
              <rect x={0} y={0} width={12} height={2} fill="#3b82f6" />
              <text x={18} y={2} fontSize="10" fill="currentColor" opacity="0.8">EMA12</text>
            </g>
            <g transform="translate(0, 15)">
              <rect x={0} y={0} width={12} height={2} fill="#8b5cf6" />
              <text x={18} y={2} fontSize="10" fill="currentColor" opacity="0.8">EMA26</text>
            </g>
          </g>
        </svg>
      </div>
    </div>
  );
};

export default InvestmentChart;