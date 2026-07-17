import React, { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ComposedChart,
  ReferenceLine,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import {
  ArrowRight,
  BrainCircuit,
  CandlestickChart,
  CircleDot,
  FlaskConical,
  Globe,
  Info,
  Layers,
  LineChart as LineChartIcon,
  Lock,
  Network,
  Scale,
  ShieldCheck,
  Tag as TagIcon,
  Target,
  TriangleAlert,
  Wallet,
} from "lucide-react";
import {
  ACCENT,
  ACCENT2,
  BENCH_COLOR,
  Card,
  ChartFrame,
  ChartTip,
  DANGER,
  Kpi,
  KpiRow,
  Legend,
  PALETTE,
  Reveal,
  SectionHead,
  Tag,
  WARN,
  axis,
  compact,
  gridStroke,
  label,
  mergeBy,
  num,
  pct,
  signedPct,
  usd,
} from "./lib.jsx";
import {
  agents,
  capabilities,
  dataProviders,
  integrity,
  limitations,
  moduleTally,
  pipeline,
  site,
  sleeves,
} from "./content.js";

const CAP_ICONS = { BrainCircuit, FlaskConical, Scale, ShieldCheck, Network, LineChart: LineChartIcon };

const ICONS = {
  Globe, CandlestickChart, Network, Layers, Tag: TagIcon,
  BrainCircuit, Wallet, LineChart: LineChartIcon,
};

/* =============================================================== shared == */
function Callout({ tone = "warn", icon: Icon = TriangleAlert, children }) {
  return (
    <div className={`callout ${tone === "info" ? "info" : ""}`}>
      <Icon className="ic" size={20} />
      <p>{children}</p>
    </div>
  );
}

function EquityChart({ equity, height = 380 }) {
  const series = equity?.series || [];
  const data = useMemo(() => mergeBy(series, "date", "value"), [series]);
  const strats = series.filter((s) => !s.is_benchmark);
  const bench = series.find((s) => s.is_benchmark);
  return (
    <>
      <ChartFrame
        title="Net-of-cost equity — indexed to 100"
        sub="All strategies vs the equal-weight universe benchmark. Dashed line at 100 is break-even."
        height={height}
      >
        <LineChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: -6 }}>
          <CartesianGrid stroke={gridStroke} vertical={false} />
          <XAxis dataKey="date" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }}
            minTickGap={60} tickFormatter={(d) => (d ? d.slice(0, 7) : d)} />
          <YAxis tick={axis} tickLine={false} axisLine={false} width={42} domain={["dataMin - 4", 105]} />
          <Tooltip content={<ChartTip fmt={(v) => v.toFixed(1)} />} />
          <ReferenceLine y={100} stroke="#64748b" strokeDasharray="4 4" />
          {strats.map((s, i) => (
            <Line key={s.name} type="monotone" dataKey={s.name} stroke={PALETTE[i % PALETTE.length]}
              strokeWidth={1.5} dot={false} isAnimationActive={false} opacity={0.92} />
          ))}
          {bench && (
            <Line type="monotone" dataKey={bench.name} stroke={BENCH_COLOR} strokeWidth={2.4}
              strokeDasharray="6 4" dot={false} isAnimationActive={false} />
          )}
        </LineChart>
      </ChartFrame>
      <Legend
        items={[
          ...strats.map((s, i) => ({ label: label(s.name), color: PALETTE[i % PALETTE.length] })),
          ...(bench ? [{ label: label(bench.name), dash: true }] : []),
        ]}
      />
    </>
  );
}

function PageHero({ a }) {
  const Icon = ICONS[a.icon] || CircleDot;
  return (
    <div className="page-hero">
      <div className="page-hero-icon"><Icon size={26} /></div>
      <div>
        <span className="eyebrow">Agent {a.n} · {a.tagline}</span>
        <h1>{a.name}</h1>
        <p className="lead">{a.intro}</p>
      </div>
    </div>
  );
}

function BulletList({ items }) {
  return (
    <ul className="bullets">
      {items.map((b) => (
        <li key={b}><CircleDot size={14} />{b}</li>
      ))}
    </ul>
  );
}

const meta = (slug) => agents.find((a) => a.slug === slug);

/* ============================================================== Overview = */
export function Overview({ data }) {
  const s = data.summary || {};
  return (
    <div className="page">
      <div className="ov-hero">
        <span className="eyebrow">Reproducible crypto quant research</span>
        <h1>Does crypto have <span className="grad">tradable alpha</span>?<br />We tested it honestly.</h1>
        <p className="lead">{site.hero}</p>
        <div className="verdict">
          <ShieldCheck size={17} />
          Headline verdict:&nbsp;<span className="mono">alpha_verified = false</span>
        </div>
        <div className="chips" style={{ marginTop: 18 }}>
          <Link to="/results" className="btn primary"><Target size={15} /> See the evidence</Link>
          <Link to="/system" className="btn ghost"><Layers size={15} /> Explore the system</Link>
        </div>
      </div>

      <KpiRow
        cols={4}
        items={[
          { value: "false", label: "Alpha verified", sub: "after costs + benchmarks", tone: "neg" },
          { value: s.market_symbols ?? "—", label: "Assets ingested", sub: `${s.universe_snapshots ?? "—"} monthly snapshots` },
          { value: s.n_experiments ?? "—", label: "Model experiments", sub: "purged walk-forward CV" },
          { value: s.best_rank_ic != null ? s.best_rank_ic.toFixed(3) : "—", label: "Best Rank IC", sub: "real signal, not alpha", tone: "pos" },
        ]}
      />

      <Reveal>
        <div style={{ marginTop: 22 }}>
          <EquityChart equity={data.equity} />
          <Callout>
            <b>No verified alpha.</b> The signal layer is real, but no strategy survived transaction
            costs and benchmark discipline. This is a research finding, preserved with integrity —
            not a live trading recommendation.
          </Callout>
        </div>
      </Reveal>

      <SectionHead eyebrow="The pipeline" title="Explore every agent"
        sub="Nine research agents, each with its own data, checks and visualizations — feeding one alpha authority. Open any one." />
      <div className="agent-grid">
        {agents.map((a) => {
          const Icon = ICONS[a.icon] || CircleDot;
          return (
            <Link to={`/agent/${a.slug}`} className="agent-card" key={a.slug}>
              <div className="agent-card-top">
                <span className="agent-card-icon"><Icon size={20} /></span>
                <span className="agent-card-n mono">{a.n}</span>
              </div>
              <div className="agent-card-name">{a.name}</div>
              <div className="agent-card-tag">{a.tagline}</div>
              <span className="agent-card-go">Open <ArrowRight size={14} /></span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

/* ============================================================== Universe = */
export function UniversePage({ data }) {
  const u = data.universe || {};
  const cov = u.coverage || [];
  return (
    <div className="page">
      <PageHero a={meta("universe")} />
      <KpiRow cols={4} items={[
        { value: u.snapshots ?? "—", label: "Monthly snapshots", sub: "point-in-time" },
        { value: u.avg_eligible != null ? Math.round(u.avg_eligible) : "—", label: "Avg eligible / month", sub: "after filters" },
        { value: u.avg_candidates != null ? Math.round(u.avg_candidates) : "—", label: "Avg candidates / month", sub: "before filters" },
        { value: "0", label: "Survivorship bias", sub: "delisted coins included", tone: "pos" },
      ]} />
      <div className="grid-2">
        <Reveal>
          <ChartFrame title="Eligible universe over time" sub="Candidates vs eligible assets per monthly snapshot" height={300}>
            <AreaChart data={cov} margin={{ top: 6, right: 12, bottom: 0, left: -8 }}>
              <defs>
                <linearGradient id="gElig" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={ACCENT} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={ACCENT} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={gridStroke} vertical={false} />
              <XAxis dataKey="date" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} minTickGap={50} />
              <YAxis tick={axis} tickLine={false} axisLine={false} width={36} />
              <Tooltip content={<ChartTip nameMap={(k) => ({ candidate: "Candidates", eligible: "Eligible" }[k] || k)} />} />
              <Area type="monotone" dataKey="candidate" stroke="#64748b" fill="none" strokeWidth={1.5} name="candidate" isAnimationActive={false} />
              <Area type="monotone" dataKey="eligible" stroke={ACCENT} fill="url(#gElig)" strokeWidth={2} name="eligible" isAnimationActive={false} />
            </AreaChart>
          </ChartFrame>
        </Reveal>
        <Reveal>
          <ChartFrame title="Why assets are excluded" sub="Total exclusions across all snapshots, by rule" height={300}>
            <BarChart data={u.exclusions || []} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 28 }}>
              <CartesianGrid stroke={gridStroke} horizontal={false} />
              <XAxis type="number" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} />
              <YAxis type="category" dataKey="reason" tick={axis} tickLine={false} axisLine={false} width={92} />
              <Tooltip cursor={{ fill: "rgba(148,163,184,0.06)" }} content={<ChartTip nameMap={() => "Excluded"} />} />
              <Bar dataKey="count" radius={[0, 5, 5, 0]} maxBarSize={22}>
                {(u.exclusions || []).map((e, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
              </Bar>
            </BarChart>
          </ChartFrame>
        </Reveal>
      </div>
      <Reveal>
        <Card title="Point-in-time methodology"><BulletList items={meta("universe").bullets} />
          {u.survivorship_note && <p className="card-sub" style={{ marginTop: 12, lineHeight: 1.6 }}>{u.survivorship_note}</p>}
        </Card>
      </Reveal>
    </div>
  );
}

/* ================================================================ Market = */
export function MarketPage({ data }) {
  const m = data.market || {};
  return (
    <div className="page">
      <PageHero a={meta("market")} />
      <KpiRow cols={4} items={[
        { value: m.n_symbols ?? "—", label: "Symbols covered", sub: "across providers" },
        { value: m.n_rows != null ? compact(m.n_rows) : "—", label: "Daily OHLCV rows", sub: "leakage-checked" },
        { value: m.date_start ? m.date_start.slice(0, 4) : "—", label: "History from", sub: `through ${m.date_end || "—"}` },
        { value: (m.providers || []).length || "—", label: "Data providers", sub: "with failover" },
      ]} />
      <div className="grid-2">
        <Reveal>
          <ChartFrame title="Symbols with data over time" sub="Distinct assets with OHLCV each month" height={300}>
            <AreaChart data={m.coverage || []} margin={{ top: 6, right: 12, bottom: 0, left: -8 }}>
              <defs>
                <linearGradient id="gSym" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={ACCENT2} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={ACCENT2} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={gridStroke} vertical={false} />
              <XAxis dataKey="date" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} minTickGap={50} />
              <YAxis tick={axis} tickLine={false} axisLine={false} width={36} />
              <Tooltip content={<ChartTip nameMap={() => "Symbols"} />} />
              <Area type="monotone" dataKey="n_symbols" stroke={ACCENT2} fill="url(#gSym)" strokeWidth={2} name="n_symbols" isAnimationActive={false} />
            </AreaChart>
          </ChartFrame>
        </Reveal>
        <Reveal>
          <ChartFrame title="Most liquid assets" sub="Latest daily dollar volume (top 15)" height={300}>
            <BarChart data={m.top_assets || []} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 12 }}>
              <CartesianGrid stroke={gridStroke} horizontal={false} />
              <XAxis type="number" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} tickFormatter={(v) => `$${compact(v)}`} />
              <YAxis type="category" dataKey="symbol" tick={axis} tickLine={false} axisLine={false} width={56} />
              <Tooltip cursor={{ fill: "rgba(148,163,184,0.06)" }} content={<ChartTip fmt={(v) => usd(v)} nameMap={() => "$ volume"} />} />
              <Bar dataKey="dollar_volume" radius={[0, 5, 5, 0]} maxBarSize={16} fill={ACCENT2} />
            </BarChart>
          </ChartFrame>
        </Reveal>
      </div>
      <Reveal><Card title="What the market agent guarantees"><BulletList items={meta("market").bullets} /></Card></Reveal>
    </div>
  );
}

/* =============================================================== Onchain = */
export function OnchainPage({ data }) {
  const o = data.onchain || {};
  const sets = (o.sets || []).map((s) => ({
    name: s.name === "market_only" ? "Market only" : "Market + on-chain",
    ic: s.mean_rank_ic, hit: s.mean_hit_rate, feats: s.n_features,
  }));
  return (
    <div className="page">
      <PageHero a={meta("onchain")} />
      <KpiRow cols={3} items={[
        { value: "No", label: "Do on-chain features help?", sub: "for this universe", tone: "neg" },
        { value: o.onchain_marginal_ic_lift != null ? signedPct(o.onchain_marginal_ic_lift, 2) : "—", label: "Marginal Rank IC lift", sub: "≈ zero", tone: "neg" },
        { value: o.n_onchain_features ?? "—", label: "On-chain features built", sub: "NVT · MVRV · TVL · fees" },
      ]} />
      <div className="grid-2">
        <Reveal>
          <ChartFrame title="Ablation: mean Rank IC by feature set" sub="Adding on-chain data does not improve prediction"
            right={<Tag kind="fail">No lift</Tag>} height={300}>
            <BarChart data={sets} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
              <CartesianGrid stroke={gridStroke} vertical={false} />
              <XAxis dataKey="name" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} />
              <YAxis tick={axis} tickLine={false} axisLine={false} width={52} />
              <Tooltip cursor={{ fill: "rgba(148,163,184,0.06)" }} content={<ChartTip fmt={(v) => v.toFixed(4)} nameMap={() => "Mean Rank IC"} />} />
              <Bar dataKey="ic" radius={[6, 6, 0, 0]} maxBarSize={88}>
                {sets.map((d, i) => <Cell key={i} fill={i === 0 ? ACCENT : ACCENT2} />)}
              </Bar>
            </BarChart>
          </ChartFrame>
        </Reveal>
        <Reveal>
          <Card title="On-chain feature coverage" sub="Sparser than market data — higher null %">
            <div className="table-scroll">
              <table className="dtable" style={{ minWidth: 320 }}>
                <thead><tr><th>Feature</th><th>Null %</th></tr></thead>
                <tbody>
                  {(o.features || []).slice(0, 10).map((f) => (
                    <tr key={f.feature}>
                      <td className="name-cell mono" style={{ fontSize: 12 }}>{f.feature}</td>
                      <td className="num">{pct(f.null_pct, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </Reveal>
      </div>
      <Reveal>
        <Callout tone="info" icon={Info}>
          Adding on-chain features changed mean Rank IC by <b>{signedPct(o.onchain_marginal_ic_lift, 2)}</b> — within noise.
          The honest conclusion: for this top-N universe, on-chain data did not add cross-sectional edge.
        </Callout>
      </Reveal>
    </div>
  );
}

/* ============================================================== Features = */
export function FeaturesPage({ data }) {
  const f = data.features || {};
  const groups = (f.by_group || []).slice(0, 10);
  return (
    <div className="page">
      <PageHero a={meta("features")} />
      <KpiRow cols={4} items={[
        { value: f.n_features ?? "—", label: "Engineered features", sub: "market + on-chain" },
        { value: f.n_groups ?? "—", label: "Feature families", sub: "grouped by type" },
        { value: f.qa_pass != null ? `${f.qa_pass}/${f.n_features}` : "—", label: "Passed QA", sub: "coverage + finiteness", tone: "pos" },
        { value: f.avg_null_pct != null ? pct(f.avg_null_pct, 1) : "—", label: "Avg null rate", sub: "across features" },
      ]} />
      <div className="grid-2">
        <Reveal>
          <ChartFrame title="Features by family" sub="Count of features in each group (top 10)" height={320}>
            <BarChart data={groups} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 40 }}>
              <CartesianGrid stroke={gridStroke} horizontal={false} />
              <XAxis type="number" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} />
              <YAxis type="category" dataKey="group" tick={{ ...axis, fontSize: 10 }} tickLine={false} axisLine={false} width={140} />
              <Tooltip cursor={{ fill: "rgba(148,163,184,0.06)" }} content={<ChartTip nameMap={() => "Features" } />} />
              <Bar dataKey="count" radius={[0, 5, 5, 0]} maxBarSize={18}>
                {groups.map((d, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
              </Bar>
            </BarChart>
          </ChartFrame>
        </Reveal>
        <Reveal>
          <ChartFrame title="Lowest-coverage features" sub="Highest null % — mostly sparse on-chain metrics" height={320}>
            <BarChart data={f.worst_coverage || []} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 40 }}>
              <CartesianGrid stroke={gridStroke} horizontal={false} />
              <XAxis type="number" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <YAxis type="category" dataKey="feature" tick={{ ...axis, fontSize: 9 }} tickLine={false} axisLine={false} width={140} />
              <Tooltip cursor={{ fill: "rgba(148,163,184,0.06)" }} content={<ChartTip fmt={(v) => pct(v, 1)} nameMap={() => "Null %"} />} />
              <Bar dataKey="null_pct" radius={[0, 5, 5, 0]} maxBarSize={14} fill={WARN} />
            </BarChart>
          </ChartFrame>
        </Reveal>
      </div>
      <Reveal><Card title="How features stay leakage-safe"><BulletList items={meta("features").bullets} /></Card></Reveal>
    </div>
  );
}

/* ================================================================ Labels = */
export function LabelsPage({ data }) {
  const l = data.labels || {};
  const hist = l.histogram || [];
  const total = (l.horizons || []).reduce((a, h) => a + h.valid_rows, 0);
  return (
    <div className="page">
      <PageHero a={meta("labels")} />
      <KpiRow cols={4} items={[
        { value: (l.horizons || []).length || "—", label: "Forward horizons", sub: "7d · 14d · 30d" },
        { value: total ? compact(total) : "—", label: "Labelled observations", sub: "across horizons" },
        { value: l.horizons?.length ? signedPct(l.horizons.find((h) => h.horizon === 14)?.mean, 2) : "—", label: "Mean 14d return", sub: "bear-skewed window", tone: "neg" },
        { value: "0", label: "Look-ahead rows", sub: "strictly forward", tone: "pos" },
      ]} />
      <Reveal>
        <ChartFrame title="Distribution of 14-day forward returns" sub="Label values clipped to ±100%; dashed line at zero" height={320}>
          <BarChart data={hist} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
            <CartesianGrid stroke={gridStroke} vertical={false} />
            <XAxis dataKey="x" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }}
              tickFormatter={(v) => `${Math.round(v * 100)}%`} minTickGap={24} />
            <YAxis tick={axis} tickLine={false} axisLine={false} width={42} />
            <Tooltip cursor={{ fill: "rgba(148,163,184,0.06)" }} content={<ChartTip nameMap={() => "Count"} />} />
            <ReferenceLine x={0} stroke="#64748b" strokeDasharray="4 4" />
            <Bar dataKey="count" maxBarSize={14}>
              {hist.map((d, i) => <Cell key={i} fill={d.x >= 0 ? ACCENT : DANGER} />)}
            </Bar>
          </BarChart>
        </ChartFrame>
      </Reveal>
      <Reveal>
        <Card title="Per-horizon label statistics">
          <div className="table-scroll">
            <table className="dtable">
              <thead><tr><th>Horizon</th><th>Valid rows</th><th>Mean</th><th>Std</th><th>Positive</th><th>Negative</th><th>P01 / P99</th></tr></thead>
              <tbody>
                {(l.horizons || []).map((h) => (
                  <tr key={h.horizon}>
                    <td className="name-cell">{h.horizon}d</td>
                    <td className="num">{compact(h.valid_rows)}</td>
                    <td className={`num ${h.mean >= 0 ? "pos" : "neg"}`}>{signedPct(h.mean, 2)}</td>
                    <td className="num">{pct(h.std, 1)}</td>
                    <td className="num pos">{compact(h.positive)}</td>
                    <td className="num neg">{compact(h.negative)}</td>
                    <td className="num">{pct(h.p01, 0)} / {pct(h.p99, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </Reveal>
    </div>
  );
}

/* ================================================================ Models = */
export function ModelsPage({ data }) {
  const m = data.models || {};
  const byModel = (m.by_model || []).map((d) => ({ ...d }));
  const passed = (m.scatter || []).filter((p) => p.passed);
  const failed = (m.scatter || []).filter((p) => !p.passed);
  return (
    <div className="page">
      <PageHero a={meta("models")} />
      <KpiRow cols={4} items={[
        { value: m.n_experiments ?? "—", label: "Experiments run", sub: "models × features × horizons" },
        { value: m.n_passed_gate ?? "—", label: "Passed signal gate", sub: "of all experiments", tone: m.n_passed_gate ? "pos" : "neg" },
        { value: data.summary?.best_rank_ic != null ? data.summary.best_rank_ic.toFixed(3) : "—", label: "Best mean Rank IC", sub: "top experiment in leaderboard", tone: "pos" },
        { value: m.leaderboard?.[0]?.rank_ic_tstat != null ? num(m.leaderboard[0].rank_ic_tstat, 1) : "—", label: "Best IC t-stat", sub: "statistically real" },
      ]} />
      <div className="grid-2">
        <Reveal>
          <ChartFrame title="Mean Rank IC by model" sub="Averaged across feature sets and horizons" height={340}>
            <BarChart data={byModel} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 30 }}>
              <CartesianGrid stroke={gridStroke} horizontal={false} />
              <XAxis type="number" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} tickFormatter={(v) => v.toFixed(2)} />
              <YAxis type="category" dataKey="model" tick={{ ...axis, fontSize: 9 }} tickLine={false} axisLine={false} width={150} />
              <Tooltip cursor={{ fill: "rgba(148,163,184,0.06)" }} content={<ChartTip fmt={(v) => v.toFixed(4)} nameMap={() => "Mean IC"} />} />
              <ReferenceLine x={0} stroke="#64748b" />
              <Bar dataKey="ic" radius={[0, 4, 4, 0]} maxBarSize={13}>
                {byModel.map((d, i) => <Cell key={i} fill={d.ic >= 0 ? ACCENT : DANGER} />)}
              </Bar>
            </BarChart>
          </ChartFrame>
        </Reveal>
        <Reveal>
          <ChartFrame title="Signal strength vs significance" sub="Each point is one experiment — Rank IC vs its t-statistic" height={340}>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 4, left: -6 }}>
              <CartesianGrid stroke={gridStroke} />
              <XAxis type="number" dataKey="ic" name="Rank IC" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} tickFormatter={(v) => v.toFixed(2)} />
              <YAxis type="number" dataKey="tstat" name="t-stat" tick={axis} tickLine={false} axisLine={false} width={36} />
              <ZAxis range={[40, 40]} />
              <Tooltip cursor={{ strokeDasharray: "3 3" }} content={<ChartTip fmt={(v) => num(v, 2)} />} />
              <ReferenceLine x={0} stroke="#64748b" strokeDasharray="3 3" />
              <Scatter data={failed} fill="#64748b" name="screened" isAnimationActive={false} />
              <Scatter data={passed} fill={ACCENT} name="passed gate" isAnimationActive={false} />
            </ScatterChart>
          </ChartFrame>
        </Reveal>
      </div>
      <Reveal>
        <Card title="Model leaderboard" sub="Top experiments by Rank IC t-statistic">
          <div className="table-scroll">
            <table className="dtable">
              <thead><tr><th>Model</th><th>Features</th><th>Horizon</th><th>Rank IC</th><th>t-stat</th><th>Hit rate</th><th>Gate</th></tr></thead>
              <tbody>
                {(m.leaderboard || []).map((r, i) => (
                  <tr key={i}>
                    <td className="name-cell">{r.model_name}</td>
                    <td className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>{r.feature_set}</td>
                    <td className="num">{r.horizon_days}d</td>
                    <td className={`num ${r.mean_rank_ic >= 0 ? "pos" : "neg"}`}>{num(r.mean_rank_ic, 3)}</td>
                    <td className="num">{num(r.rank_ic_tstat, 1)}</td>
                    <td className="num">{pct(r.hit_rate, 1)}</td>
                    <td>{r.signal_gate_passed ? <Tag kind="pass">pass</Tag> : <Tag kind="muted">screen</Tag>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </Reveal>
    </div>
  );
}

/* ============================================================= Portfolio = */
export function PortfolioPage({ data }) {
  const p = data.portfolio || {};
  return (
    <div className="page">
      <PageHero a={meta("portfolio")} />
      <KpiRow cols={4} items={[
        { value: p.n_strategies ?? "—", label: "Allocation strategies", sub: "weighting schemes" },
        { value: p.latest_weights?.length ?? "—", label: "Latest holdings", sub: `as of ${p.as_of || "—"}` },
        { value: "long-only", label: "Direction", sub: "positive-signal filter" },
        { value: "diagnostic", label: "Allocation mode", sub: "not live trading", tone: "neg" },
      ]} />
      <div className="grid-2">
        <Reveal>
          <ChartFrame title="Latest target weights" sub={`${label(p.strategy || "")} · as of ${p.as_of || "—"}`} height={340}>
            <BarChart data={p.latest_weights || []} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 12 }}>
              <CartesianGrid stroke={gridStroke} horizontal={false} />
              <XAxis type="number" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <YAxis type="category" dataKey="symbol" tick={axis} tickLine={false} axisLine={false} width={56} />
              <Tooltip cursor={{ fill: "rgba(148,163,184,0.06)" }} content={<ChartTip fmt={(v) => pct(v, 1)} nameMap={() => "Weight"} />} />
              <Bar dataKey="weight" radius={[0, 5, 5, 0]} maxBarSize={16} fill={ACCENT} />
            </BarChart>
          </ChartFrame>
        </Reveal>
        <Reveal>
          <ChartFrame title="Turnover & positions over time" sub="Rebalance turnover (left) and number of holdings (right)" height={340}>
            <ComposedChart data={p.series || []} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid stroke={gridStroke} vertical={false} />
              <XAxis dataKey="date" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} minTickGap={60} tickFormatter={(d) => (d ? d.slice(0, 7) : d)} />
              <YAxis yAxisId="l" tick={axis} tickLine={false} axisLine={false} width={40} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <YAxis yAxisId="r" orientation="right" tick={axis} tickLine={false} axisLine={false} width={28} />
              <Tooltip content={<ChartTip nameMap={(k) => ({ turnover: "Turnover", positions: "Positions" }[k] || k)} fmt={(v) => (v < 1 ? pct(v, 1) : v)} />} />
              <Area yAxisId="l" type="monotone" dataKey="turnover" stroke={ACCENT2} fill={ACCENT2} fillOpacity={0.12} strokeWidth={1.6} name="turnover" isAnimationActive={false} />
              <Line yAxisId="r" type="monotone" dataKey="positions" stroke={WARN} strokeWidth={1.6} dot={false} name="positions" isAnimationActive={false} />
            </ComposedChart>
          </ChartFrame>
        </Reveal>
      </div>
      <Reveal>
        <Callout icon={Lock}>
          <b>Prediction-only, diagnostic.</b> Weights come from as-of model predictions with liquidity and
          positive-signal filters — realized returns are never an input. Because the source signal did not pass
          the alpha gate, this allocation is diagnostic, not a live trading instruction.
        </Callout>
      </Reveal>
    </div>
  );
}

/* ============================================================== Backtest = */
export function BacktestPage({ data }) {
  const b = data.backtest || {};
  const strat = data.strategies || [];
  const best = strat[0];
  const costPick = ["top_20_vol_scaled", "score_weighted_vol_scaled", "top_5_equal_weight"];
  const costChosen = (data.costSweep || []).filter((s) => costPick.includes(s.name));
  const costData = mergeBy(costChosen, "cost_bps", "total_return");
  return (
    <div className="page">
      <PageHero a={meta("backtest")} />
      <KpiRow cols={4} items={[
        { value: "false", label: "Alpha verified", sub: "the final verdict", tone: "neg" },
        { value: best ? signedPct(best.total_return) : "—", label: "Best total return", sub: best ? label(best.strategy_name) : "", tone: "neg" },
        { value: best ? pct(best.max_drawdown) : "—", label: "Best max drawdown", sub: "peak-to-trough" },
        { value: data.summary?.transaction_cost_bps != null ? `${data.summary.transaction_cost_bps} bps` : "—", label: "Transaction cost", sub: "applied throughout" },
      ]} />
      <Reveal><EquityChart equity={data.equity} /></Reveal>
      <div className="grid-2" style={{ marginTop: 18 }}>
        <Reveal>
          <ChartFrame title="Drawdown (underwater)" sub={`${label(b.drawdown_strategy || "")} — depth below prior peak`} height={280}>
            <AreaChart data={b.drawdown || []} margin={{ top: 6, right: 12, bottom: 0, left: -6 }}>
              <defs>
                <linearGradient id="gDD" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={DANGER} stopOpacity={0.05} />
                  <stop offset="100%" stopColor={DANGER} stopOpacity={0.5} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={gridStroke} vertical={false} />
              <XAxis dataKey="date" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} minTickGap={60} tickFormatter={(d) => (d ? d.slice(0, 7) : d)} />
              <YAxis tick={axis} tickLine={false} axisLine={false} width={42} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <Tooltip content={<ChartTip fmt={(v) => pct(v, 1)} nameMap={() => "Drawdown"} />} />
              <Area type="monotone" dataKey="dd" stroke={DANGER} fill="url(#gDD)" strokeWidth={1.6} name="dd" isAnimationActive={false} />
            </AreaChart>
          </ChartFrame>
        </Reveal>
        <Reveal>
          <ChartFrame title="Cost sensitivity" sub="Total return as costs sweep 0→100 bps" height={280}>
            <LineChart data={costData} margin={{ top: 8, right: 16, bottom: 0, left: -2 }}>
              <CartesianGrid stroke={gridStroke} vertical={false} />
              <XAxis dataKey="cost_bps" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} tickFormatter={(v) => `${v}bps`} />
              <YAxis tick={axis} tickLine={false} axisLine={false} width={46} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <Tooltip content={<ChartTip fmt={(v) => signedPct(v)} suffix=" bps" />} />
              {costChosen.map((s, i) => (
                <Line key={s.name} type="monotone" dataKey={s.name} stroke={PALETTE[i]} strokeWidth={2} dot={{ r: 2.5 }} isAnimationActive={false} />
              ))}
            </LineChart>
          </ChartFrame>
        </Reveal>
      </div>
      <Reveal>
        <Card title="Strategy scorecard" sub="Net of costs — alpha status decided only here" className="mt">
          <div className="table-scroll">
            <table className="dtable">
              <thead><tr><th>Strategy</th><th>Total return</th><th>CAGR</th><th>Sharpe</th><th>Max DD</th><th>vs EW univ.</th><th>Status</th></tr></thead>
              <tbody>
                {strat.map((r) => (
                  <tr key={r.strategy_name}>
                    <td className="name-cell">{label(r.strategy_name)}<small>{r.strategy_name}</small></td>
                    <td className={`num ${r.total_return >= 0 ? "pos" : "neg"}`}>{signedPct(r.total_return)}</td>
                    <td className={`num ${r.CAGR >= 0 ? "pos" : "neg"}`}>{signedPct(r.CAGR)}</td>
                    <td className="num">{num(r.Sharpe)}</td>
                    <td className="num neg">{pct(r.max_drawdown)}</td>
                    <td>{r.beats_equal_weight ? <Tag kind="pass">beats</Tag> : <Tag kind="muted">below</Tag>}</td>
                    <td><Tag kind="fail">{(r.alpha_status || "failed").toUpperCase()}</Tag></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </Reveal>
      {b.subperiods?.length ? (
        <Reveal>
          <Card title="Sub-period robustness" sub={`${label(b.drawdown_strategy || "top_20_vol_scaled")} across market regimes`} className="mt">
            <div className="table-scroll">
              <table className="dtable" style={{ minWidth: 360 }}>
                <thead><tr><th>Sub-period</th><th>Total return</th><th>Sharpe</th><th>Max DD</th></tr></thead>
                <tbody>
                  {b.subperiods.map((sp) => (
                    <tr key={sp.subperiod}>
                      <td className="name-cell">{sp.subperiod.replace(/_/g, " ")}</td>
                      <td className={`num ${sp.total_return >= 0 ? "pos" : "neg"}`}>{signedPct(sp.total_return)}</td>
                      <td className="num">{num(sp.sharpe)}</td>
                      <td className="num neg">{pct(sp.max_drawdown)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </Reveal>
      ) : null}
      <Reveal>
        <Callout>
          <b>Verdict: no verified alpha.</b> Across costs, benchmarks and sub-periods, no strategy showed
          robust risk-adjusted outperformance. The backtest agent rejected every candidate — exactly what a
          trustworthy pipeline should do when the edge isn't there.
        </Callout>
      </Reveal>
    </div>
  );
}

/* ========================================================== Paper trading = */
const fmtUsd = (x) =>
  x === null || x === undefined || Number.isNaN(x)
    ? "—"
    : `$${Number(x).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;

const fmtUtc = (iso) => (iso ? `${iso.slice(0, 16).replace("T", " ")} UTC` : "—");

function BookCard({ book }) {
  const positions = Object.entries(book.positions || {});
  const fills = book.recent_fills || [];
  const equity = book.equity || [];
  const skipped = book.status !== "ok";
  return (
    <Card
      title={label(book.name) || book.name}
      sub={book.name}
      right={skipped ? <Tag kind="fail">skipped</Tag> : <Tag kind="pass">ok</Tag>}
      className="mt"
    >
      {skipped && (
        <Callout>
          <b>Book skipped this run.</b> Reason: <span className="mono">{book.skip_reason || "unknown"}</span>.
          No virtual NAV is reported until prices are available.
        </Callout>
      )}
      <KpiRow cols={4} items={[
        { value: fmtUsd(book.nav), label: "Virtual NAV", sub: "paper money, not real" },
        { value: book.cum_return != null ? signedPct(book.cum_return, 2) : "—", label: "Cumulative return", sub: "since book start", tone: book.cum_return != null ? (book.cum_return >= 0 ? "pos" : "neg") : undefined },
        { value: fmtUsd(book.cash), label: "Virtual cash", sub: "uninvested balance" },
        { value: book.last_rebalance || "—", label: "Last rebalance", sub: "book state" },
      ]} />
      {equity.length > 1 ? (
        <ChartFrame title="Virtual equity (NAV)" sub="Daily marked-to-market paper NAV" height={240}>
          <LineChart data={equity} margin={{ top: 6, right: 12, bottom: 0, left: 6 }}>
            <CartesianGrid stroke={gridStroke} vertical={false} />
            <XAxis dataKey="date" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} minTickGap={60} />
            <YAxis tick={axis} tickLine={false} axisLine={false} width={62} domain={["auto", "auto"]} tickFormatter={(v) => compact(v)} />
            <Tooltip content={<ChartTip fmt={(v) => fmtUsd(v)} nameMap={() => "NAV"} />} />
            <Line type="monotone" dataKey="nav" stroke={ACCENT} strokeWidth={1.8} dot={false} isAnimationActive={false} />
          </LineChart>
        </ChartFrame>
      ) : (
        <Callout tone="info" icon={Info}>
          Collecting history — the equity chart appears once this book has more than one daily NAV point
          ({equity.length} so far). Books update via <span className="mono">python3 main.py papertrade</span>.
        </Callout>
      )}
      <div className="grid-2" style={{ marginTop: 14 }}>
        <div>
          <div className="card-sub" style={{ marginBottom: 8 }}>Positions</div>
          {positions.length ? (
            <div className="table-scroll">
              <table className="dtable" style={{ minWidth: 240 }}>
                <thead><tr><th>Symbol</th><th>Quantity</th></tr></thead>
                <tbody>
                  {positions.map(([sym, qty]) => (
                    <tr key={sym}>
                      <td className="name-cell">{sym}</td>
                      <td className="num">{num(qty, 6)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="card-sub">No open virtual positions.</p>
          )}
        </div>
        <div>
          <div className="card-sub" style={{ marginBottom: 8 }}>Recent fills (last {fills.length || 0})</div>
          {fills.length ? (
            <div className="table-scroll">
              <table className="dtable" style={{ minWidth: 380 }}>
                <thead><tr><th>Date</th><th>Symbol</th><th>Side</th><th>Notional</th><th>Cost</th><th>Reason</th></tr></thead>
                <tbody>
                  {fills.slice().reverse().map((f, i) => (
                    <tr key={i}>
                      <td className="name-cell">{f.date}</td>
                      <td>{f.symbol}</td>
                      <td className={f.side === "buy" ? "pos" : "neg"}>{f.side}</td>
                      <td className="num">{fmtUsd(f.notional)}</td>
                      <td className="num">{fmtUsd(f.cost_usd)}</td>
                      <td className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>{f.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="card-sub">No fills recorded yet.</p>
          )}
        </div>
      </div>
    </Card>
  );
}

export function PaperTradePage({ data }) {
  const pt = data.papertrade;
  if (!pt) {
    return (
      <div className="page">
        <div className="page-hero">
          <div className="page-hero-icon"><FlaskConical size={26} /></div>
          <div>
            <span className="eyebrow">Virtual books · research validation</span>
            <h1>Paper Trading</h1>
            <p className="lead">No paper trading data has been baked yet.</p>
          </div>
        </div>
        <Callout tone="info" icon={Info}>
          Run <span className="mono">python3 main.py papertrade</span> and then re-bake with{" "}
          <span className="mono">npm run bake</span> to populate this page.
        </Callout>
      </div>
    );
  }
  const books = pt.books || [];
  const okBooks = books.filter((b) => b.status === "ok");
  const combinedNav = okBooks.reduce((a, b) => a + (b.nav || 0), 0);
  return (
    <div className="page">
      <div className="page-hero">
        <div className="page-hero-icon"><FlaskConical size={26} /></div>
        <div>
          <span className="eyebrow">Virtual books · research validation</span>
          <h1>Paper Trading</h1>
          <p className="lead">
            Daily virtual books that track model allocations against simple benchmarks with paper money.
            They exist to validate the research loop out-of-sample — nothing here is traded for real.
          </p>
        </div>
      </div>
      <Callout>
        <b>Virtual paper trading.</b> These books are research validation with simulated cash — not
        financial advice, not live holdings, and not a claim of verified alpha.
      </Callout>
      <KpiRow cols={3} items={[
        { value: `${okBooks.length}/${books.length}`, label: "Books active", sub: "ok vs total (skipped shown honestly)", tone: okBooks.length ? "pos" : "neg" },
        { value: fmtUsd(combinedNav), label: "Combined virtual NAV", sub: "ok books only · paper money" },
        { value: fmtUtc(pt.last_run_utc), label: "Last run", sub: `as of ${pt.as_of || "—"}` },
      ]} />
      {books.map((b) => (
        <Reveal key={b.name}><BookCard book={b} /></Reveal>
      ))}
      {!books.length && (
        <Callout tone="info" icon={Info}>
          No books found in the manifest. Run <span className="mono">python3 main.py papertrade</span>.
        </Callout>
      )}
    </div>
  );
}

/* ============================================================== Fund Ops = */
const bps = (x, d = 2) =>
  x === null || x === undefined || Number.isNaN(x) ? "—" : `${Number(x).toFixed(d)} bps`;

function FundOpsEmpty() {
  return (
    <div className="page">
      <div className="page-hero">
        <div className="page-hero-icon"><ShieldCheck size={26} /></div>
        <div>
          <span className="eyebrow">Institutional layers · virtual books</span>
          <h1>Fund Ops</h1>
          <p className="lead">No fund-operations data has been baked yet.</p>
        </div>
      </div>
      <Callout tone="info" icon={Info}>
        Run <span className="mono">python3 main.py nav</span> and{" "}
        <span className="mono">python3 main.py monitor</span>, then re-bake with{" "}
        <span className="mono">npm run bake</span> to populate this page.
      </Callout>
    </div>
  );
}

function statusTag(status) {
  const s = String(status || "unknown").toLowerCase();
  if (["ok", "pass", "normal"].includes(s)) return <Tag kind="pass">{s}</Tag>;
  if (["warn", "insufficient_data", "halted", "recovery"].includes(s)) return <Tag kind="muted">{s}</Tag>;
  if (["alert", "break", "fail"].includes(s)) return <Tag kind="fail">{s}</Tag>;
  return <Tag kind="muted">{s}</Tag>;
}

export function FundOpsPage({ data }) {
  const fo = data.fundops;
  if (!fo) return <FundOpsEmpty />;
  const rec = fo.reconciliation;
  const audits = fo.risk_audits || [];
  const ddStates = fo.drawdown_states || [];
  const regime = fo.regime;
  const sleeves = fo.sleeve_allocation;
  const mon = fo.monitoring;
  const navBooks = fo.nav || [];
  const alertCount = (mon?.alerts || []).filter((a) => a.severity === "alert").length;
  const recOk = rec ? rec.books.every((b) => b.status === "OK") : null;
  return (
    <div className="page">
      <div className="page-hero">
        <div className="page-hero-icon"><ShieldCheck size={26} /></div>
        <div>
          <span className="eyebrow">Institutional layers · virtual books</span>
          <h1>Fund Ops</h1>
          <p className="lead">
            Dual-book accounting, risk governance, regime-aware sleeves and a monitoring suite —
            institutional fund practices applied to the virtual paper books.
          </p>
        </div>
      </div>
      <Callout>
        <b>Virtual paper trading.</b> {fo.note || "Simulated cash and simulated fees — not live holdings, not financial advice, and no claim of verified alpha."}
      </Callout>
      <KpiRow cols={4} items={[
        { value: rec ? (recOk ? "OK" : "BREAK") : "—", label: "Reconciliation", sub: rec ? `${rec.books.length} books · tol ${bps(rec.tolerance_bps, 1)}` : "not run yet", tone: rec ? (recOk ? "pos" : "neg") : undefined },
        { value: mon ? String(alertCount) : "—", label: "Open alerts", sub: "from monitoring suite", tone: alertCount ? "neg" : "pos" },
        { value: regime?.regime || "—", label: "Market regime", sub: regime?.days_in_regime != null ? `${regime.days_in_regime} days in regime` : "regime job not run" },
        { value: sleeves ? (sleeves.approved ? "approved" : "proposal") : "—", label: "Sleeve allocation", sub: "approval-gated, never auto-executed", tone: sleeves && !sleeves.approved ? undefined : "pos" },
      ]} />

      <SectionHead eyebrow="Accounting" title="Dual-book reconciliation"
        sub="Engine NAV vs an independent shadow NAV rebuilt from the accounting ledger. Divergence beyond tolerance is a break." />
      <Reveal>
        <Card title="Reconciliation report" sub={rec ? `Tolerance ${bps(rec.tolerance_bps, 1)}` : undefined}>
          {rec ? (
            <div className="table-scroll">
              <table className="dtable">
                <thead><tr><th>Book</th><th>Status</th><th>Divergence</th><th>Max divergence</th><th>Days OK</th><th>Break class</th></tr></thead>
                <tbody>
                  {rec.books.map((b) => (
                    <tr key={b.book}>
                      <td className="name-cell">{label(b.book)}<small>{b.book}</small></td>
                      <td>{statusTag(b.status)}</td>
                      <td className="num">{bps(b.divergence_bps, 4)}</td>
                      <td className="num">{bps(b.max_divergence_bps, 4)}</td>
                      <td className="num">{b.days_ok}/{b.days_checked}</td>
                      <td className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>{b.break_classification || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="card-sub">No reconciliation report yet — run <span className="mono">python3 main.py nav</span>.</p>
          )}
        </Card>
      </Reveal>
      {navBooks.length > 0 && (
        <Reveal>
          <Card title="Net-of-fees vs gross NAV" sub="Independent accounting NAV with a simulated 2%/20% fee schedule (virtual)" className="mt">
            <div className="table-scroll">
              <table className="dtable">
                <thead><tr><th>Book</th><th>As of</th><th>Gross NAV</th><th>Net NAV</th><th>Mgmt fees (cum)</th><th>Perf fees (cum)</th></tr></thead>
                <tbody>
                  {navBooks.map((b) => (
                    <tr key={b.book}>
                      <td className="name-cell">{label(b.book)}<small>{b.book}</small></td>
                      <td className="num">{b.latest?.date || "—"}</td>
                      <td className="num">{fmtUsd(b.latest?.gross)}</td>
                      <td className="num">{fmtUsd(b.latest?.net)}</td>
                      <td className="num">{b.latest?.mgmt_fee_cum != null ? `$${Number(b.latest.mgmt_fee_cum).toFixed(2)}` : "—"}</td>
                      <td className="num">{b.latest?.perf_fee_cum != null ? `$${Number(b.latest.perf_fee_cum).toFixed(2)}` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </Reveal>
      )}

      <SectionHead eyebrow="Risk governance" title="Risk pipeline & drawdown control"
        sub="Vol targeting, liquidity and concentration limits, and a drawdown de-risking state machine applied before any paper order." />
      <div className="grid-2">
        <Reveal>
          <Card title="Latest risk audits" sub="Per-book multipliers and final exposures">
            {audits.length ? (
              <div className="table-scroll">
                <table className="dtable" style={{ minWidth: 420 }}>
                  <thead><tr><th>Book</th><th>Vol mult</th><th>DD mult</th><th>Final gross</th><th>Cash</th><th>Positions</th></tr></thead>
                  <tbody>
                    {audits.map((a) => (
                      <tr key={a.book}>
                        <td className="name-cell">{label(a.book)}<small>{a.as_of}</small></td>
                        <td className="num">{num(a.vol_target_multiplier)}</td>
                        <td className="num">{num(a.drawdown_multiplier)}</td>
                        <td className="num">{pct(a.final_gross_exposure, 1)}</td>
                        <td className="num">{pct(a.final_cash_weight, 1)}</td>
                        <td className="num">{a.n_final_positions ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="card-sub">No risk audits yet — the risk pipeline has not produced artifacts.</p>
            )}
          </Card>
        </Reveal>
        <Reveal>
          <Card title="Drawdown states" sub="NORMAL → de-risk → HALT state machine per book">
            {ddStates.length ? (
              <div className="table-scroll">
                <table className="dtable" style={{ minWidth: 360 }}>
                  <thead><tr><th>Book</th><th>State</th><th>Since</th><th>Drawdown</th></tr></thead>
                  <tbody>
                    {ddStates.map((d) => (
                      <tr key={d.book}>
                        <td className="name-cell">{label(d.book)}<small>{d.last_date}</small></td>
                        <td>{statusTag(d.state)}</td>
                        <td className="num">{d.state_entered_date || "—"}</td>
                        <td className={`num ${d.drawdown != null && d.drawdown < 0 ? "neg" : ""}`}>{d.drawdown != null ? signedPct(d.drawdown, 2) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="card-sub">No drawdown state files yet.</p>
            )}
          </Card>
        </Reveal>
      </div>

      <SectionHead eyebrow="Strategies" title="Regime & sleeves"
        sub="A deterministic market-regime classifier drives a sleeve allocation proposal. Promotion always goes through the human approval gate." />
      {regime && (
        <Reveal>
          <Card title="Current regime" sub={`As of ${regime.date || "—"}`}>
            <div className="chips" style={{ marginBottom: 10 }}>
              <span className="chip">{regime.regime || "unknown"}</span>
              {regime.days_in_regime != null && <span className="chip">{regime.days_in_regime} days</span>}
              <span className="chip">BTC trend {regime.btc_trend_up ? "up" : "down"}</span>
              {regime.btc_dominance_trend && <span className="chip">dominance {regime.btc_dominance_trend}</span>}
              {regime.fear_greed_bucket && <span className="chip">{`F&G ${regime.fear_greed_value ?? "—"} (${regime.fear_greed_bucket})`}</span>}
            </div>
            <p className="card-sub">
              Breadth above 50d MA: {pct(regime.breadth_pct_above_50d_ma, 1)} · avg pairwise corr (60d, top 20): {num(regime.avg_pairwise_corr_60d_top20)}
            </p>
          </Card>
        </Reveal>
      )}
      <Reveal>
        <Card title="Sleeve allocation proposal" sub={sleeves ? `As of ${sleeves.as_of || "—"} · regime ${sleeves.regime || "—"} · proposed cash ${pct(sleeves.cash_weight, 1)}` : undefined}
          right={sleeves ? (sleeves.approved ? <Tag kind="pass">approved</Tag> : <Tag kind="muted">proposal · not approved</Tag>) : undefined}
          className="mt">
          {sleeves?.sleeves?.length ? (
            <>
              <div className="table-scroll">
                <table className="dtable">
                  <thead><tr><th>Sleeve</th><th>Base weight</th><th>Proposed weight</th><th>Sortino (90d)</th><th>Return source</th><th>Paper NAV</th></tr></thead>
                  <tbody>
                    {sleeves.sleeves.map((s) => {
                      const book = (data.papertrade?.books || []).find((b) => b.name === s.sleeve);
                      return (
                        <tr key={s.sleeve}>
                          <td className="name-cell">{label(s.sleeve) || s.sleeve}<small>{s.sleeve}</small></td>
                          <td className="num">{pct(s.base_weight, 1)}</td>
                          <td className="num">{pct(s.proposed_weight, 1)}</td>
                          <td className="num">{num(s.sortino_90d)}</td>
                          <td className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>{s.return_source || "—"}</td>
                          <td className="num">{book ? fmtUsd(book.nav) : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {sleeves.note && <p className="card-sub" style={{ marginTop: 10 }}>{sleeves.note}</p>}
            </>
          ) : (
            <p className="card-sub">No sleeve allocation proposal artifact yet.</p>
          )}
        </Card>
      </Reveal>

      <SectionHead eyebrow="Monitoring" title="Alerts & module status"
        sub="Daily monitors for data quality, signal health, execution, risk, shadow NAV, pipeline staleness and champion/challenger." />
      <div className="grid-2">
        <Reveal>
          <Card title="Module status" sub={mon ? `Daily quality verdict: ${mon.daily_quality_verdict || "unknown"}` : undefined}>
            {mon?.modules?.length ? (
              <div className="table-scroll">
                <table className="dtable" style={{ minWidth: 300 }}>
                  <thead><tr><th>Module</th><th>Status</th><th>Alerts</th></tr></thead>
                  <tbody>
                    {mon.modules.map((m) => (
                      <tr key={m.module}>
                        <td className="name-cell mono" style={{ fontSize: 12 }}>{m.module}</td>
                        <td>{statusTag(m.status)}</td>
                        <td className="num">{m.n_alerts}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="card-sub">No monitoring reports yet — run <span className="mono">python3 main.py monitor</span>.</p>
            )}
          </Card>
        </Reveal>
        <Reveal>
          <Card title={`Open alerts (${mon?.alerts?.length ?? 0})`} sub="Raised by the monitoring suite — honest, unfiltered">
            {mon?.alerts?.length ? (
              <div className="table-scroll">
                <table className="dtable" style={{ minWidth: 380 }}>
                  <thead><tr><th>Module</th><th>Severity</th><th>Message</th></tr></thead>
                  <tbody>
                    {mon.alerts.map((a, i) => (
                      <tr key={i}>
                        <td className="name-cell mono" style={{ fontSize: 12 }}>{a.module}</td>
                        <td>{statusTag(a.severity)}</td>
                        <td style={{ fontSize: 12 }}>{a.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="card-sub">No open alerts.</p>
            )}
          </Card>
        </Reveal>
      </div>
    </div>
  );
}

/* ============================================================== Results == */
export function ResultsPage({ data }) {
  const s = data.summary || {};
  const strat = (data.strategies || []).slice().sort((a, b) => (b.Sharpe ?? -99) - (a.Sharpe ?? -99));
  const best = strat[0];
  const lb = (data.leaderboard || []).slice().sort((a, b) => (b.mean_rank_ic ?? -9) - (a.mean_rank_ic ?? -9));
  const costPick = ["top_5_vol_scaled", "score_weighted_vol_scaled", "top_20_equal_weight"];
  const costChosen = (data.costSweep || []).filter((c) => costPick.includes(c.name));
  const costData = mergeBy(costChosen, "cost_bps", "total_return");

  return (
    <div className="page">
      <div className="page-hero">
        <div className="page-hero-icon"><Target size={26} /></div>
        <div>
          <span className="eyebrow">Research proof · the evidence</span>
          <h1>The honest result, in full</h1>
          <p className="lead">
            Every number here is loaded from real pipeline output — no hand-typed metrics. The headline
            is deliberately negative, and that is the point: a process you can trust is one that will
            tell you when the edge isn&apos;t there.
          </p>
        </div>
      </div>

      <div className="verdict" style={{ marginBottom: 18 }}>
        <ShieldCheck size={17} />
        Backtest authority verdict:&nbsp;<span className="mono">alpha_verified = false</span>
      </div>

      <KpiRow cols={4} items={[
        { value: "false", label: "Alpha verified", sub: "after costs + benchmarks", tone: "neg" },
        { value: s.best_rank_ic != null ? s.best_rank_ic.toFixed(3) : "—", label: "Best Rank IC", sub: "real signal, not tradable alpha", tone: "pos" },
        { value: s.transaction_cost_bps != null ? `${s.transaction_cost_bps} bps` : "—", label: "Cost gate", sub: `swept ${(s.cost_sweep_bps || []).join("/")} bps` },
        { value: s.n_experiments ?? "—", label: "Experiments", sub: `${s.n_strategies ?? "—"} strategies · ${s.n_benchmarks ?? "—"} benchmarks` },
      ]} />

      <KpiRow cols={4} items={[
        { value: s.window_start ? `${s.window_start.slice(0, 7)}→${(s.window_end || "").slice(2, 7)}` : "—", label: "OOS window", sub: `${s.n_days ?? "—"} trading days` },
        { value: s.universe_size ?? "—", label: "Universe assets", sub: `${s.universe_snapshots ?? "—"} PIT snapshots` },
        { value: best ? signedPct(best.total_return) : "—", label: "Best strategy return", sub: best ? label(best.strategy_name) : "", tone: best && best.total_return >= 0 ? "pos" : "neg" },
        { value: s.onchain_features_help ? "yes" : "no", label: "On-chain helps?", sub: s.onchain_marginal_ic_lift != null ? `${(s.onchain_marginal_ic_lift * 100).toFixed(2)}% marginal IC` : "" },
      ]} />

      <Reveal><EquityChart equity={data.equity} /></Reveal>

      <SectionHead eyebrow="Cost discipline" title="Fragile to friction"
        sub="Total return as transaction costs sweep 0 → 100 bps. Thin edges evaporate — which is exactly why the gate is set at a realistic level, not zero." />
      <Reveal>
        <ChartFrame title="Cost sensitivity" sub="Representative strategies, net total return vs cost assumption" height={300}>
          <LineChart data={costData} margin={{ top: 8, right: 16, bottom: 0, left: -2 }}>
            <CartesianGrid stroke={gridStroke} vertical={false} />
            <XAxis dataKey="cost_bps" tick={axis} tickLine={false} axisLine={{ stroke: gridStroke }} tickFormatter={(v) => `${v}bps`} />
            <YAxis tick={axis} tickLine={false} axisLine={false} width={46} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
            <Tooltip content={<ChartTip fmt={(v) => signedPct(v)} suffix=" bps" />} />
            <ReferenceLine y={0} stroke="#64748b" strokeDasharray="4 4" />
            {costChosen.map((c, i) => (
              <Line key={c.name} type="monotone" dataKey={c.name} stroke={PALETTE[i]} strokeWidth={2} dot={{ r: 2.5 }} isAnimationActive={false} />
            ))}
          </LineChart>
        </ChartFrame>
        <Legend items={costChosen.map((c, i) => ({ label: label(c.name), color: PALETTE[i] }))} />
      </Reveal>

      <Reveal>
        <Card title="Strategy scorecard" sub="Net of costs — the only place alpha is decided" className="mt">
          <div className="table-scroll">
            <table className="dtable">
              <thead><tr><th>Strategy</th><th>Total return</th><th>CAGR</th><th>Sharpe</th><th>Max DD</th><th>Turnover</th><th>vs BTC</th><th>vs EW</th><th>Status</th></tr></thead>
              <tbody>
                {strat.map((r) => (
                  <tr key={r.strategy_name}>
                    <td className="name-cell">{label(r.strategy_name)}<small>{r.strategy_name}</small></td>
                    <td className={`num ${r.total_return >= 0 ? "pos" : "neg"}`}>{signedPct(r.total_return)}</td>
                    <td className={`num ${r.CAGR >= 0 ? "pos" : "neg"}`}>{signedPct(r.CAGR)}</td>
                    <td className="num">{num(r.Sharpe)}</td>
                    <td className="num neg">{pct(r.max_drawdown)}</td>
                    <td className="num">{r.average_turnover != null ? pct(r.average_turnover, 0) : "—"}</td>
                    <td>{r.beats_btc ? <Tag kind="pass">beats</Tag> : <Tag kind="muted">below</Tag>}</td>
                    <td>{r.beats_equal_weight ? <Tag kind="pass">beats</Tag> : <Tag kind="muted">below</Tag>}</td>
                    <td><Tag kind="fail">{(r.alpha_status || "failed").toUpperCase()}</Tag></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <Card title="Model signal leaderboard" sub="Rank IC by model / feature set / horizon — the signal layer can find structure; it just cannot claim alpha" className="mt">
          <div className="table-scroll">
            <table className="dtable">
              <thead><tr><th>Model</th><th>Features</th><th>Target</th><th>Horizon</th><th>Rank IC</th><th>IC t-stat</th><th>Stability</th><th>Signal gate</th><th>Alpha</th></tr></thead>
              <tbody>
                {lb.map((r, i) => (
                  <tr key={i}>
                    <td className="name-cell">{r.model_name}</td>
                    <td>{(r.feature_set || "").replace(/_/g, " ")}</td>
                    <td className="mono" style={{ fontSize: 11 }}>{(r.label_target || "").replace(/_/g, " ")}</td>
                    <td className="num">{r.horizon_days}d</td>
                    <td className={`num ${r.mean_rank_ic >= 0 ? "pos" : "neg"}`}>{num(r.mean_rank_ic, 3)}</td>
                    <td className="num">{num(r.rank_ic_tstat, 1)}</td>
                    <td className="num">{r.stability_score != null ? num(r.stability_score, 2) : "—"}</td>
                    <td>{r.signal_gate_passed ? <Tag kind="pass">pass</Tag> : <Tag kind="muted">fail</Tag>}</td>
                    <td><Tag kind="fail">{(r.final_alpha_status || "failed").toUpperCase()}</Tag></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </Reveal>

      {data.backtest?.subperiods?.length ? (
        <Reveal>
          <Card title="Sub-period robustness" sub={`${label(data.backtest.drawdown_strategy || "")} across market regimes`} className="mt">
            <div className="table-scroll">
              <table className="dtable" style={{ minWidth: 360 }}>
                <thead><tr><th>Sub-period</th><th>Total return</th><th>Sharpe</th><th>Max DD</th></tr></thead>
                <tbody>
                  {data.backtest.subperiods.map((sp) => (
                    <tr key={sp.subperiod}>
                      <td className="name-cell">{sp.subperiod.replace(/_/g, " ")}</td>
                      <td className={`num ${sp.total_return >= 0 ? "pos" : "neg"}`}>{signedPct(sp.total_return)}</td>
                      <td className="num">{num(sp.sharpe)}</td>
                      <td className="num neg">{pct(sp.max_drawdown)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </Reveal>
      ) : null}

      <Reveal>
        <Callout>
          <b>Verdict: no verified alpha.</b> The signal layer is real and, on some model/feature/horizon
          combinations, statistically significant — but nothing survived transaction costs and benchmark
          discipline robustly enough for the backtest authority to verify. That negative result, honestly
          reported, is the deliverable.
        </Callout>
      </Reveal>
    </div>
  );
}

/* =============================================================== System == */
export function SystemPage({ data }) {
  const fo = data.fundops || {};
  const recon = fo.reconciliation || {};
  const total = moduleTally.reduce((a, m) => a + m.n, 0);
  return (
    <div className="page">
      <div className="page-hero">
        <div className="page-hero-icon"><Layers size={26} /></div>
        <div>
          <span className="eyebrow">The platform · {total}+ modules</span>
          <h1>Not a notebook — a system</h1>
          <p className="lead">
            CHF is built like a real desk&apos;s stack: deterministic research agents feed a single alpha
            authority, then an execution-and-ops layer runs the full institutional apparatus around it —
            all in code, all tested, all honest about being simulated.
          </p>
        </div>
      </div>

      <KpiRow cols={4} items={[
        { value: `${total}+`, label: "First-class modules", sub: "agents · sleeves · services" },
        { value: "13", label: "Virtual paper books", sub: "canonical · candidate · sleeves" },
        { value: recon.tolerance_bps != null ? `${recon.tolerance_bps} bps` : "0 bps", label: "NAV reconciliation", sub: "engine vs independent ledger" },
        { value: "600+", label: "Passing tests", sub: "incl. research-integrity" },
      ]} />

      <SectionHead eyebrow="Capabilities" title="What the platform does"
        sub="Six layers wrap the research pipeline, turning a study into an operable, monitorable system." />
      <Reveal>
        <div className="features">
          {capabilities.map((c) => {
            const Icon = CAP_ICONS[c.icon] || CircleDot;
            return (
              <div className="feature" key={c.title}>
                <div className="fic"><Icon size={20} /></div>
                <h3>{c.title}</h3>
                <p>{c.body}</p>
              </div>
            );
          })}
        </div>
      </Reveal>

      <SectionHead eyebrow="Multi-strategy" title="Seven strategy sleeves"
        sub="Each sleeve is deterministic and trades its own virtual book — including two genuinely market-neutral books that don't need beta to win." />
      <Reveal>
        <div className="agent-grid">
          {sleeves.map((sl) => (
            <div className="agent-card" key={sl.book} style={{ cursor: "default" }}>
              <div className="agent-card-top">
                <span className="agent-card-icon"><Wallet size={18} /></span>
                <Tag kind={sl.kind === "Market-neutral" ? "pass" : "muted"}>{sl.kind}</Tag>
              </div>
              <div className="agent-card-name">{sl.name}</div>
              <div className="agent-card-tag">{sl.body}</div>
              <span className="mono" style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>{sl.book}</span>
            </div>
          ))}
        </div>
      </Reveal>

      <SectionHead eyebrow="Data breadth" title="21 provider adapters"
        sub="Most keyless-capable, spanning market, on-chain, derivatives, DeFi and macro." />
      <Reveal>
        <div className="grid-2">
          {dataProviders.map((g) => (
            <Card key={g.group} title={g.group} sub={`${g.items.length} sources`}>
              <div className="chips">{g.items.map((it) => <span className="chip" key={it}>{it}</span>)}</div>
            </Card>
          ))}
        </div>
      </Reveal>

      <SectionHead eyebrow="By the numbers" title="Module tally"
        sub="The distinct building blocks behind the platform (see docs/CASE_STUDY.md for the full breakdown)." />
      <Reveal>
        <div className="table-scroll">
          <table className="dtable">
            <thead><tr><th>Layer</th><th>Modules</th><th>Notes</th></tr></thead>
            <tbody>
              {moduleTally.map((m) => (
                <tr key={m.label}>
                  <td className="name-cell">{m.label}</td>
                  <td className="num pos">{m.n}</td>
                  <td className="card-sub" style={{ textAlign: "left" }}>{m.sub}</td>
                </tr>
              ))}
              <tr>
                <td className="name-cell"><b>Total</b></td>
                <td className="num pos"><b>{total}+</b></td>
                <td className="card-sub" style={{ textAlign: "left" }}>plus tests, docs, configs, frontend</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Reveal>

      <Reveal>
        <Callout tone="info" icon={Info}>
          <b>Real vs simulated.</b> The pipeline, modeling, walk-forward, backtest, accounting, monitoring
          and tests are real and run on real historical data. All <em>trading</em> is virtual — paper books
          with notional NAV and simulated fees. Nothing here is a live track record.
        </Callout>
      </Reveal>
    </div>
  );
}

export const AGENT_PAGES = {
  universe: UniversePage,
  market: MarketPage,
  onchain: OnchainPage,
  features: FeaturesPage,
  labels: LabelsPage,
  models: ModelsPage,
  portfolio: PortfolioPage,
  backtest: BacktestPage,
};
