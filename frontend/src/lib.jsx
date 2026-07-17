import React, { useEffect, useRef } from "react";
import { ResponsiveContainer } from "recharts";
import { strategyLabels } from "./content.js";

/* ----------------------------------------------------------- constants --- */
export const PALETTE = [
  "#34d399", "#38bdf8", "#a78bfa", "#f472b6", "#fbbf24",
  "#fb7185", "#22d3ee", "#4ade80", "#facc15",
];
export const BENCH_COLOR = "#cbd5e1";
export const ACCENT = "#34d399";
export const ACCENT2 = "#38bdf8";
export const WARN = "#fbbf24";
export const DANGER = "#fb7185";

export const axis = { stroke: "#5f6e82", fontSize: 11, fontFamily: "JetBrains Mono" };
export const gridStroke = "rgba(148,163,184,0.10)";

/* ---------------------------------------------------------- formatters --- */
export const label = (k) => strategyLabels[k] || k;
export const pct = (x, d = 1) =>
  x === null || x === undefined || Number.isNaN(x) ? "—" : `${(x * 100).toFixed(d)}%`;
export const signedPct = (x, d = 1) =>
  x === null || x === undefined || Number.isNaN(x)
    ? "—"
    : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%`;
export const num = (x, d = 2) =>
  x === null || x === undefined || Number.isNaN(x) ? "—" : Number(x).toFixed(d);
const _compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
export const compact = (x) => (x == null || Number.isNaN(x) ? "—" : _compact.format(x));
export const usd = (x) => (x == null ? "—" : `$${compact(x)}`);

export function mergeBy(series, xKey, valueKey) {
  const map = new Map();
  series.forEach((s) => {
    s.points.forEach((p) => {
      const row = map.get(p[xKey]) || { [xKey]: p[xKey] };
      row[s.name] = p[valueKey];
      map.set(p[xKey], row);
    });
  });
  return Array.from(map.values()).sort((a, b) =>
    a[xKey] < b[xKey] ? -1 : a[xKey] > b[xKey] ? 1 : 0
  );
}

/* -------------------------------------------------------------- reveal --- */
export function Reveal({ children, className = "" }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reveal = () => el.classList.add("in");
    const ob = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) {
            reveal();
            ob.unobserve(el);
          }
        }),
      { threshold: 0.06, rootMargin: "0px 0px -6% 0px" }
    );
    ob.observe(el);
    const t = setTimeout(reveal, 500);
    return () => {
      ob.disconnect();
      clearTimeout(t);
    };
  }, []);
  return (
    <div ref={ref} className={`reveal ${className}`}>
      {children}
    </div>
  );
}

/* ----------------------------------------------------------- tooltips --- */
export function ChartTip({ active, payload, label: lbl, fmt = (v) => v, suffix = "", nameMap = label }) {
  if (!active || !payload || !payload.length) return null;
  const rows = payload.filter((p) => p.value != null);
  return (
    <div className="rc-tip">
      <div className="rc-tip-h">{lbl}{suffix}</div>
      {rows.map((p, i) => (
        <div className="rc-row" key={i}>
          <span className="swatch" style={{ background: p.color || p.fill || p.stroke }} />
          <span className="nm">{nameMap(p.name ?? p.dataKey)}</span>
          <span className="vl">{fmt(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------- atoms --- */
export function SectionHead({ eyebrow, title, sub }) {
  return (
    <div className="section-head">
      {eyebrow && <span className="eyebrow">{eyebrow}</span>}
      <h2>{title}</h2>
      {sub && <p className="section-sub">{sub}</p>}
    </div>
  );
}

export function Kpi({ value, label: l, sub, tone }) {
  return (
    <div className="kpi">
      <div className={`kpi-v ${tone || ""}`}>{value}</div>
      <div className="kpi-l">{l}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

export function KpiRow({ items, cols }) {
  return (
    <div className="kpis" style={cols ? { gridTemplateColumns: `repeat(${cols}, 1fr)` } : undefined}>
      {items.map((k) => (
        <Kpi key={k.label} {...k} />
      ))}
    </div>
  );
}

export function Card({ title, sub, right, children, className = "" }) {
  return (
    <div className={`card ${className}`}>
      {(title || right) && (
        <div className="card-head">
          <div>
            {title && <div className="card-title">{title}</div>}
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Tag({ kind = "muted", children }) {
  return <span className={`tag ${kind}`}>{children}</span>;
}

/* Wraps a recharts chart with a sized responsive container + optional header. */
export function ChartFrame({ title, sub, right, height = 320, children }) {
  return (
    <div className="chart-card">
      {(title || right) && (
        <div className="card-head">
          <div>
            {title && <div className="card-title">{title}</div>}
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          {right}
        </div>
      )}
      <div className="chart-wrap" style={{ height }}>
        <ResponsiveContainer>{children}</ResponsiveContainer>
      </div>
    </div>
  );
}

export function Legend({ items }) {
  return (
    <div className="legend">
      {items.map((it) => (
        <span className="legend-item" key={it.label}>
          <span className={`swatch ${it.dash ? "dash" : ""}`} style={it.dash ? undefined : { background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  );
}
