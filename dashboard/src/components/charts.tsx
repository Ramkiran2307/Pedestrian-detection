// Charts follow the data-viz rules used across the project: thin columns (<=24px) with
// 4px rounded data-ends, a 2px surface gap between stacked segments, recessive hairline
// grid, a legend for multi-series charts, hover tooltips and a table fallback.
import { useState } from "react";
import {
  Bar, BarChart, Rectangle, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps,
} from "recharts";
import { Table2 } from "lucide-react";

const SERIES = [
  { key: "person", label: "Pedestrian", color: "var(--series-person)" },
  { key: "vehicle", label: "Vehicle", color: "var(--series-vehicle)" },
  { key: "unknown", label: "Unattributed", color: "var(--series-unknown)" },
] as const;

const axisTick = { fill: "var(--muted)", fontSize: 11 };

function shortDay(iso: string) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function TrendTooltip({ active, payload, label }: Partial<TooltipContentProps<number, string>>) {
  if (!active || !payload?.length) return null;
  const total = payload.reduce((s: number, p: { value?: number }) => s + (Number(p.value) || 0), 0);
  return (
    <div className="viz-tooltip">
      <div className="t">{shortDay(String(label))}</div>
      {[...payload].reverse().map((p) => (
        <div className="r" key={p.dataKey as string}>
          <span><span className="sw" style={{ background: p.color }} />{SERIES.find((s) => s.key === p.dataKey)?.label}</span>
          <b>{p.value}</b>
        </div>
      ))}
      <div className="r" style={{ borderTop: "1px solid var(--border)", marginTop: 4, paddingTop: 4 }}>
        <span>Total</span><b>{total}</b>
      </div>
    </div>
  );
}

// Round only the top-most non-empty segment of each stacked column.
function stackShape(index: number) {
  return (props: any) => {
    const above = SERIES.slice(index + 1).some((s) => Number(props.payload?.[s.key]) > 0);
    return <Rectangle {...props} radius={above ? 0 : [4, 4, 0, 0]} />;
  };
}

export function TrendChart({ data }: { data: { date: string; person: number; vehicle: number; unknown: number }[] }) {
  const [table, setTable] = useState(false);
  return (
    <>
      <div className="legend" aria-hidden={table}>
        {SERIES.map((s) => (
          <span className="key" key={s.key}><span className="sw" style={{ background: s.color }} />{s.label}</span>
        ))}
        <button className="btn ghost" style={{ marginLeft: "auto", padding: "2px 6px" }} onClick={() => setTable(!table)}>
          <Table2 aria-hidden /> {table ? "Chart" : "Table"}
        </button>
      </div>
      {table ? (
        <div className="table-wrap" style={{ maxHeight: 260, padding: "8px 12px" }}>
          <table className="data">
            <thead><tr><th>Date</th>{SERIES.map((s) => <th className="num" key={s.key}>{s.label}</th>)}</tr></thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.date}><td>{shortDay(d.date)}</td>
                  {SERIES.map((s) => <td className="num" key={s.key}>{d[s.key]}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart-box" style={{ height: 260 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: -18 }} barCategoryGap="28%">
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis dataKey="date" tickFormatter={shortDay} tick={axisTick} tickLine={false}
                     axisLine={{ stroke: "var(--axis)" }} interval="preserveStartEnd" minTickGap={18} />
              <YAxis allowDecimals={false} tick={axisTick} tickLine={false} axisLine={false} width={44} />
              <Tooltip content={<TrendTooltip />} cursor={{ fill: "var(--surface-3)", opacity: 0.6 }} />
              {SERIES.map((s, i) => (
                <Bar key={s.key} dataKey={s.key} stackId="a" fill={s.color} maxBarSize={24}
                     stroke="var(--chart-surface)" strokeWidth={2} shape={stackShape(i)} isAnimationActive={false} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </>
  );
}

function HourTooltip({ active, payload, label }: Partial<TooltipContentProps<number, string>>) {
  if (!active || !payload?.length) return null;
  const h = Number(label);
  return (
    <div className="viz-tooltip">
      <div className="t">{`${String(h).padStart(2, "0")}:00 – ${String(h).padStart(2, "0")}:59`}</div>
      <div className="r"><span>Incidents</span><b>{payload[0].value}</b></div>
    </div>
  );
}

export function HourlyChart({ data }: { data: { hour: number; count: number }[] }) {
  const peak = data.reduce((a, b) => (b.count > a.count ? b : a), data[0] ?? { hour: 0, count: 0 });
  return (
    <>
      <div className="legend">
        <span className="muted">
          Peak hour: <b style={{ color: "var(--text)" }}>{`${String(peak.hour).padStart(2, "0")}:00`}</b> ({peak.count} incidents)
        </span>
      </div>
      <div className="chart-box" style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: -18 }} barCategoryGap="22%">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="hour" tick={axisTick} tickLine={false} axisLine={{ stroke: "var(--axis)" }}
                   tickFormatter={(h) => String(h).padStart(2, "0")} interval={2} />
            <YAxis allowDecimals={false} tick={axisTick} tickLine={false} axisLine={false} width={44} />
            <Tooltip content={<HourTooltip />} cursor={{ fill: "var(--surface-3)", opacity: 0.6 }} />
            <Bar dataKey="count" fill="var(--series-single)" maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

export function BarList({ rows }: { rows: { label: string; value: number }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="bar-list">
      {rows.map((r) => (
        <div className="bar-row" key={r.label} title={`${r.label}: ${r.value}`}>
          <span className="sub">{r.label}</span>
          <div className="track"><div className="fill" style={{ width: `${(r.value / max) * 100}%` }} /></div>
          <span className="v">{r.value}</span>
        </div>
      ))}
    </div>
  );
}
