import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { compact, num, pct, signedPct, dirClass } from "../format";
import { Card, ChartTooltip, ErrorState, Loading, Metric } from "../ui";
import type { BacktestMetrics, BacktestResult, SymbolInfo } from "../types";

function MetricsTable({
  strat,
  bench,
}: {
  strat: BacktestMetrics;
  bench: BacktestMetrics;
}) {
  const rows: [string, (m: BacktestMetrics) => string, boolean][] = [
    ["Total Return", (m) => signedPct(m.total_return), true],
    ["CAGR", (m) => signedPct(m.cagr), true],
    ["Volatility", (m) => pct(m.annualized_volatility), false],
    ["Sharpe", (m) => num(m.sharpe_ratio), true],
    ["Sortino", (m) => num(m.sortino_ratio), true],
    ["Max Drawdown", (m) => pct(m.max_drawdown), false],
    ["Win Rate", (m) => pct(m.win_rate), false],
    ["Profit Factor", (m) => num(m.profit_factor), false],
    ["Trades", (m) => String(m.num_trades), false],
  ];
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Metric</th>
          <th>Strategy</th>
          <th>Buy &amp; Hold</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, fn, signed]) => {
          const sv = fn(strat);
          return (
            <tr key={label}>
              <td>{label}</td>
              <td className={signed ? dirClass(parseFloat(sv)) : ""}>{sv}</td>
              <td className="dim">{fn(bench)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function BacktestPanel({ symbols }: { symbols: SymbolInfo[] }) {
  const [symbol, setSymbol] = useState(symbols[0]?.symbol ?? "");
  const [strategies, setStrategies] = useState<string[]>([]);
  const [strategy, setStrategy] = useState("MovingAverageCrossover");
  const [costBps, setCostBps] = useState("5");
  const [slipBps, setSlipBps] = useState("2");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .strategies()
      .then((r) => setStrategies(r.strategies))
      .catch(() => {});
  }, []);

  const run = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.backtest({
        symbol,
        strategy,
        transaction_cost_bps: Number(costBps),
        slippage_bps: Number(slipBps),
      });
      setResult(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const chartData = useMemo(() => {
    if (!result) return [];
    return result.equity_curve.map((p) => ({
      date: p.date,
      equity: p.equity,
      weight: p.target_weight,
    }));
  }, [result]);

  return (
    <div>
      <div className="panel-head">
        <div>
          <div className="eyebrow">Backtesting</div>
          <h1>Strategy Lab</h1>
          <p>
            Event-driven backtest with transaction costs and slippage. Signals
            at bar i are held into bar i+1, so there is no look-ahead. Every run
            is measured against a buy-and-hold benchmark over the same window.
          </p>
        </div>
      </div>

      <Card title="Configuration" style={{ marginBottom: 16 }}>
        <div className="grid cols-4">
          <div>
            <label className="field">Instrument</label>
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {symbols.map((s) => (
                <option key={s.symbol} value={s.symbol}>
                  {s.symbol}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="field">Strategy</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
            >
              {strategies.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="field">Cost (bps)</label>
            <input
              className="inp"
              value={costBps}
              onChange={(e) => setCostBps(e.target.value)}
            />
          </div>
          <div>
            <label className="field">Slippage (bps)</label>
            <input
              className="inp"
              value={slipBps}
              onChange={(e) => setSlipBps(e.target.value)}
            />
          </div>
        </div>
        <div className="row mt-16">
          <div className="spacer" />
          <button className="btn" onClick={run} disabled={loading}>
            {loading ? "Running" : "Run Backtest"}
          </button>
        </div>
      </Card>

      {error && <ErrorState message={error} />}
      {loading && <Loading label="Simulating" />}

      {result && (
        <>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <Metric
              label="Total Return"
              value={signedPct(result.metrics.total_return)}
              tone={dirClass(result.metrics.total_return)}
            />
            <Metric label="Sharpe" value={num(result.metrics.sharpe_ratio)} />
            <Metric
              label="Max Drawdown"
              value={pct(result.metrics.max_drawdown)}
            />
            <Metric label="Trades" value={String(result.metrics.num_trades)} />
          </div>

          <Card title={`Equity Curve - ${result.strategy} on ${result.symbol}`}>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart
                data={chartData}
                margin={{ top: 6, right: 12, bottom: 0, left: 10 }}
              >
                <CartesianGrid stroke="#1c2233" vertical={false} />
                <XAxis
                  dataKey="date"
                  minTickGap={56}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  width={64}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) => compact(v)}
                  domain={["auto", "auto"]}
                />
                <Tooltip
                  content={(p) => (
                    <ChartTooltip {...p} formatter={(v) => compact(v)} />
                  )}
                />
                <Line
                  type="monotone"
                  dataKey="equity"
                  stroke="#f0a23c"
                  strokeWidth={1.7}
                  dot={false}
                  name="Equity"
                />
              </LineChart>
            </ResponsiveContainer>
          </Card>

          <Card title="Strategy vs Benchmark" style={{ marginTop: 16 }}>
            <MetricsTable strat={result.metrics} bench={result.benchmark} />
          </Card>
        </>
      )}
    </div>
  );
}
