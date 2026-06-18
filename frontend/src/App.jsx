import React, { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import {
  ArrowRight,
  BarChart3,
  BookOpen,
  BriefcaseBusiness,
  CheckCircle2,
  ChevronRight,
  Command,
  Eye,
  FileText,
  FolderSearch,
  Home,
  Image,
  Lock,
  PanelTop,
  Rocket,
  Scale,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Table2,
  WalletCards,
  Workflow,
  Zap
} from "lucide-react";
import {
  accessModules,
  agents,
  benchmarks,
  consoleCards,
  demoAudiences,
  heroBullets,
  painPoints,
  pipelineTimeline,
  portfolioModules,
  productFacts,
  release,
  researchArtifacts,
  roadmapColumns,
  trustBadges,
  validationProof
} from "./data/chfProductData";
import {
  artifactCounts,
  galleryArtifacts,
  portfolioArtifacts,
  portfolioPreviewRows
} from "./data/artifactIndex";

const navItems = [
  { id: "landing", label: "Landing", icon: Home },
  { id: "why", label: "Why CHF", icon: Zap },
  { id: "console", label: "Product Console", icon: Command },
  { id: "agents", label: "Agent Pipeline", icon: Workflow },
  { id: "outputs", label: "Verified Outputs", icon: CheckCircle2 },
  { id: "gallery", label: "Visual Gallery", icon: Image },
  { id: "portfolioViewer", label: "Portfolio Viewer", icon: WalletCards },
  { id: "portfolio", label: "Portfolio IQ", icon: SlidersHorizontal },
  { id: "benchmarks", label: "Benchmark Intel", icon: Scale },
  { id: "trust", label: "Trust", icon: ShieldCheck },
  { id: "roadmap", label: "Roadmap", icon: Rocket },
  { id: "demo", label: "Demo View", icon: BriefcaseBusiness }
];

function Card({ children, className = "", onClick }) {
  return (
    <div className={`card ${className}`} onClick={onClick}>
      {children}
    </div>
  );
}

function Section({ eyebrow, title, body, children }) {
  return (
    <section className="section">
      <div className="section-copy">
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
        {body ? <p>{body}</p> : null}
      </div>
      {children}
    </section>
  );
}

function Landing({ setActive }) {
  return (
    <div className="page">
      <section className="hero-section">
        <div className="hero-content">
          <span className="eyebrow">Agentic crypto quant infrastructure</span>
          <h1>CHF Alpha Research OS</h1>
          <p>{release.subtitle}</p>
          <div className="hero-bullets">
            {heroBullets.map((bullet) => (
              <span key={bullet}><CheckCircle2 size={17} />{bullet}</span>
            ))}
          </div>
          <div className="cta-row">
            <button onClick={() => setActive("agents")}>Explore Research Pipeline <ArrowRight size={16} /></button>
            <button className="secondary" onClick={() => setActive("benchmarks")}>View Verified Benchmarks</button>
            <button className="secondary" onClick={() => setActive("console")}>Open Product Console</button>
            <button className="secondary" onClick={() => setActive("trust")}>View Reproducibility Pack</button>
          </div>
        </div>
        <div className="product-mock">
          <div className="mock-header">
            <span>CHF Control Plane</span>
            <span className="status-pill">Release locked</span>
          </div>
          <div className="mock-grid">
            <div><span>Pipeline</span><strong>8 agents online</strong></div>
            <div><span>Validation</span><strong>54 tests passed</strong></div>
            <div><span>Benchmark window</span><strong>{release.window}</strong></div>
            <div><span>Release tag</span><strong>{release.tag}</strong></div>
            <div><span>Mode</span><strong>{release.researchMode}</strong></div>
            <div><span>Benchmark set</span><strong>4 baselines</strong></div>
          </div>
          <div className="mock-pipeline">
            {["Universe", "Data", "Features", "Models", "Portfolio", "Backtest"].map((step) => (
              <span key={step}>{step}</span>
            ))}
          </div>
        </div>
      </section>

      <div className="metric-grid">
        {productFacts.map((fact) => {
          const Icon = fact.icon;
          return (
            <Card key={fact.label} className="metric-card">
              <Icon size={22} />
              <span>{fact.label}</span>
              <strong>{fact.value}</strong>
              <small>{fact.detail}</small>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function WhyCHF() {
  return (
    <Section
      eyebrow="Why CHF exists"
      title="Crypto research breaks when data, validation, and portfolio logic live in separate notebooks."
      body="CHF converts a fragile research workflow into an auditable agent pipeline with explicit contracts."
    >
      <div className="pain-solution-grid">
        <div className="column-label">Common failure mode</div>
        <div className="column-label solution">CHF product answer</div>
        {painPoints.map((item) => {
          const Icon = item.icon;
          return (
            <React.Fragment key={item.pain}>
              <Card className="pain-card">
                <Icon size={20} />
                <p>{item.pain}</p>
              </Card>
              <Card className="solution-card">
                <CheckCircle2 size={20} />
                <p>{item.solution}</p>
              </Card>
            </React.Fragment>
          );
        })}
      </div>
    </Section>
  );
}

function ProductConsole() {
  const outputCards = [
    { label: "Graph artifacts found", value: artifactCounts.visualArtifactsDiscovered, detail: "Safe visual files indexed", icon: Image },
    { label: "Portfolio artifacts found", value: artifactCounts.portfolioArtifactsDiscovered, detail: "Allocation/portfolio files discovered", icon: WalletCards },
    { label: "Backtest reports found", value: artifactCounts.backtestAndBenchmarkArtifactsDiscovered, detail: "Backtest, benchmark, equity, and metric files", icon: BarChart3 },
    { label: "Reviewer docs found", value: artifactCounts.reviewerDocsDiscovered, detail: "Final research documentation files", icon: FileText }
  ];

  return (
    <div className="page">
      <Section
        eyebrow="Product Console"
        title="A command center for agentic research operations."
        body="The MVP surfaces pipeline modules, schedules, benchmark infrastructure, and reproducibility assets as product primitives."
      >
        <div className="console-grid">
          {consoleCards.map((card) => {
            const Icon = card.icon;
            return (
              <Card key={card.title} className="console-card">
                <div className="card-topline">
                  <Icon size={22} />
                  <span>{card.status}</span>
                </div>
                <strong>{card.title}</strong>
                <p>{card.detail}</p>
              </Card>
            );
          })}
        </div>
      </Section>
      <Card>
        <div className="card-heading">
          <h3>Generated outputs</h3>
          <span className="status-pill">Local artifact index</span>
        </div>
        <div className="output-stat-grid">
          {outputCards.map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.label} className="output-stat">
                <Icon size={19} />
                <span>{item.label}</span>
                <strong>{item.value}</strong>
                <small>{item.detail}</small>
              </div>
            );
          })}
        </div>
        <p className="integrity-note">
          Counts come from the local artifact inventory. The React dashboard indexes existing files; it does not generate charts, rerun backtests, or create portfolio holdings.
        </p>
      </Card>
      <Card className="timeline-card">
        <div className="card-heading">
          <h3>Pipeline status timeline</h3>
          <span className="status-pill">MVP modules</span>
        </div>
        <div className="timeline">
          {pipelineTimeline.map((stage, index) => (
            <div key={stage.label} className="timeline-item">
              <div className="timeline-dot">{index + 1}</div>
              <strong>{stage.label}</strong>
              <span>{stage.status}</span>
              <p>{stage.purpose}</p>
              <small>{stage.output} • {stage.value}</small>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function ArtifactCard({ artifact }) {
  const isImage = artifact.kind === "image" && artifact.publicPath;
  const Icon = artifact.kind === "image" ? Image : artifact.kind === "table" ? Table2 : FileText;
  return (
    <Card className="artifact-card">
      {isImage ? (
        <div className="artifact-preview image-preview">
          <img src={artifact.publicPath} alt={artifact.title} />
        </div>
      ) : (
        <div className="artifact-preview">
          <Icon size={28} />
          <span>{artifact.type?.toUpperCase()}</span>
        </div>
      )}
      <div className="artifact-meta">
        <div className="card-topline">
          <span>{artifact.category}</span>
          <em>{artifact.source}</em>
        </div>
        <strong>{artifact.title}</strong>
        <p>{artifact.description}</p>
        <code>{artifact.path}</code>
      </div>
    </Card>
  );
}

function VisualizationGallery() {
  const [category, setCategory] = useState("All");
  const [query, setQuery] = useState("");
  const portfolioAsArtifacts = portfolioArtifacts.map((artifact) => ({
    title: artifact.title,
    category: "Portfolio",
    path: artifact.path,
    type: artifact.type,
    description: `${artifact.candidate}. ${artifact.rows} allocation rows, ${artifact.symbols} symbols, ${artifact.rebalanceFrequency} rebalance.`,
    source: artifact.source,
    kind: "table",
    available: artifact.available
  }));
  const artifacts = [...galleryArtifacts, ...portfolioAsArtifacts];
  const categories = ["All", "Benchmark", "Backtest", "Portfolio", "Model", "Feature", "QA", "Report"];
  const filtered = artifacts.filter((artifact) => {
    const categoryMatch = category === "All" || artifact.category === category;
    const text = `${artifact.title} ${artifact.path} ${artifact.description} ${artifact.source}`.toLowerCase();
    return categoryMatch && text.includes(query.toLowerCase());
  });

  return (
    <div className="page">
      <Section
        eyebrow="Visualization Gallery"
        title="Inspect generated visuals, reports, and table artifacts without fabricating charts."
        body="The gallery is backed by a static-safe artifact index. Images are previewed when available; Parquet and report files are shown as local research artifacts."
      >
        <div className="gallery-controls">
          <label className="search-box">
            <Search size={17} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search artifacts, sources, paths..." />
          </label>
          <div className="filter-row">
            {categories.map((item) => (
              <button key={item} className={category === item ? "chip active" : "chip"} onClick={() => setCategory(item)}>
                {item}
              </button>
            ))}
          </div>
        </div>
      </Section>
      {filtered.length ? (
        <div className="gallery-grid">
          {filtered.map((artifact) => <ArtifactCard key={`${artifact.path}-${artifact.title}`} artifact={artifact} />)}
        </div>
      ) : (
        <Card className="empty-state">
          <FolderSearch size={28} />
          <h3>No matching artifacts found</h3>
          <p>Only existing generated artifacts are shown. No charts are fabricated.</p>
        </Card>
      )}
      <Card>
        <h3>Artifact policy</h3>
        <p>
          Only existing generated artifacts are shown. The dashboard does not browse unrestricted local paths, does not expose secrets, and does not synthesize charts from unavailable files.
        </p>
      </Card>
    </div>
  );
}

function AgentPipeline() {
  const [category, setCategory] = useState("All");
  const [selected, setSelected] = useState(agents[0]);
  const categories = ["All", ...Array.from(new Set(agents.map((agent) => agent.category)))];
  const filtered = category === "All" ? agents : agents.filter((agent) => agent.category === category);

  return (
    <div className="page">
      <Section
        eyebrow="Agent Pipeline"
        title="Eight specialized agents with explicit research contracts."
        body="Click an agent to see what it does, why it matters, and what artifact it produces."
      >
        <div className="filter-row">
          {categories.map((item) => (
            <button key={item} className={category === item ? "chip active" : "chip"} onClick={() => setCategory(item)}>
              {item}
            </button>
          ))}
        </div>
        <div className="flow-strip">
          {agents.map((agent) => <span key={agent.name}>{agent.name.replace("Agent", "")}</span>)}
        </div>
      </Section>
      <div className="agent-workbench">
        <div className="agent-card-grid">
          {filtered.map((agent) => {
            const Icon = agent.icon;
            return (
              <Card
                key={agent.name}
                className={selected.name === agent.name ? "agent-card selected" : "agent-card"}
                onClick={() => setSelected(agent)}
              >
                <Icon size={25} />
                <strong>{agent.name}</strong>
                <span>{agent.category}</span>
                <p>{agent.what}</p>
              </Card>
            );
          })}
        </div>
        <Card className="agent-detail-panel">
          <span className="eyebrow">Selected agent</span>
          <h3>{selected.name}</h3>
          <div className="detail-stack">
            <div><span>What it does</span><p>{selected.what}</p></div>
            <div><span>Why it matters</span><p>{selected.why}</p></div>
            <div><span>Input</span><p>{selected.input}</p></div>
            <div><span>Output</span><p>{selected.output}</p></div>
            <div><span>Product value</span><p>{selected.value}</p></div>
            <div><span>Example artifact</span><code>{selected.artifact}</code></div>
          </div>
        </Card>
      </div>
    </div>
  );
}

function VerifiedOutputs({ setActive }) {
  return (
    <div className="page">
      <Section
        eyebrow="Verified Outputs"
        title="A release package that proves what was checked."
        body="Benchmarks and validation artifacts are shown as verified context, not promotional strategy performance."
      >
        <div className="benchmark-monitor">
          {benchmarks.map((benchmark) => (
            <Card key={benchmark.name} className="benchmark-tile">
              <span>{benchmark.name}</span>
              <strong>{benchmark.returnPct.toFixed(2)}%</strong>
              <small>{benchmark.description}</small>
            </Card>
          ))}
        </div>
      </Section>
      <div className="split-grid">
        <Card className="chart-card">
          <h3>Benchmark verification</h3>
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={benchmarks}>
              <CartesianGrid strokeDasharray="3 3" stroke="#20304f" />
              <XAxis dataKey="name" stroke="#91a2c4" />
              <YAxis stroke="#91a2c4" tickFormatter={(value) => `${value}%`} />
              <Tooltip formatter={(value) => `${value}%`} contentStyle={{ background: "#0b1220", border: "1px solid #263858" }} />
              <Bar dataKey="returnPct" radius={[12, 12, 0, 0]}>
                {benchmarks.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card>
          <h3>Validation proof</h3>
          <div className="proof-list">
            {validationProof.map((proof) => {
              const Icon = proof.icon;
              return (
                <div key={proof.label}>
                  <Icon size={18} />
                  <span>{proof.label}</span>
                  <strong>{proof.status}</strong>
                </div>
              );
            })}
          </div>
        </Card>
      </div>
      <Card>
        <div className="card-heading">
          <h3>Research artifact status</h3>
          <span className="status-pill">Reviewer-ready</span>
        </div>
        <div className="artifact-grid">
          {researchArtifacts.map((artifact) => (
            <div key={artifact.name} className="artifact-row">
              <FileIcon />
              <div>
                <strong>{artifact.name}</strong>
                <span>{artifact.path}</span>
              </div>
              <em>{artifact.status}</em>
            </div>
          ))}
        </div>
        <p className="integrity-note">
          CHF’s current release prioritizes verified research infrastructure and benchmark discipline over unsupported alpha claims. Candidate strategies are tracked through the verification layer before being promoted.
        </p>
      </Card>
      <div className="action-grid">
        <Card className="action-card" onClick={() => setActive("gallery")}>
          <Image size={22} />
          <strong>Visualization Gallery</strong>
          <p>Inspect indexed visual, report, table, and benchmark artifacts.</p>
        </Card>
        <Card className="action-card" onClick={() => setActive("portfolioViewer")}>
          <WalletCards size={22} />
          <strong>Portfolio Viewer</strong>
          <p>Review generated allocation files and candidate portfolio metadata.</p>
        </Card>
        <Card className="action-card" onClick={() => setActive("benchmarks")}>
          <Scale size={22} />
          <strong>Benchmark Intelligence</strong>
          <p>Understand why exact windows and passive baselines matter.</p>
        </Card>
        <Card className="action-card" onClick={() => setActive("trust")}>
          <ShieldCheck size={22} />
          <strong>Reproducibility Pack</strong>
          <p>Open release audit, artifact manifest, and validation references.</p>
        </Card>
      </div>
    </div>
  );
}

function FileIcon() {
  return <BookOpen size={18} />;
}

function PortfolioIntelligence() {
  return (
    <div className="page">
      <Section
        eyebrow="Portfolio Intelligence"
        title="A product module for turning forecasts into research portfolios."
        body="Capabilities below are platform modules and roadmap concepts, not claims of live trading performance."
      >
        <div className="module-grid">
          {portfolioModules.map((module) => {
            const Icon = module.icon;
            return (
              <Card key={module.title} className="module-card">
                <Icon size={23} />
                <strong>{module.title}</strong>
                <p>{module.detail}</p>
              </Card>
            );
          })}
        </div>
      </Section>
      <Card className="mock-product">
        <div className="mock-nav">
          <span>Product interface mockup — not live trading</span>
          <span className="status-pill">Research mode</span>
        </div>
        <div className="mock-console-grid">
          <div><small>Candidate signal queue</small><strong>3 candidate signals</strong></div>
          <div><small>Portfolio rule selected</small><strong>Top-K / risk adjusted</strong></div>
          <div><small>Benchmark selected</small><strong>BTC + ETH + blend + universe</strong></div>
          <div><small>Validation status</small><strong>Requires BacktestAgent</strong></div>
          <div><small>Export targets</small><strong>weights, reports, audit docs</strong></div>
          <div><small>Roadmap monitor</small><strong>future live allocation view</strong></div>
        </div>
      </Card>
    </div>
  );
}

function PortfolioViewer() {
  return (
    <div className="page">
      <Section
        eyebrow="Portfolio Viewer"
        title="Historical allocation artifacts from PortfolioAgent."
        body="This viewer surfaces real generated portfolio artifacts when they exist locally. It is not a live trading screen and does not fabricate holdings."
      >
        <div className="benchmark-monitor">
          <Card className="benchmark-tile">
            <span>Portfolio artifacts</span>
            <strong>{artifactCounts.portfolioArtifactsDiscovered}</strong>
            <small>Allocation, manifest, coverage, and related files discovered locally</small>
          </Card>
          <Card className="benchmark-tile">
            <span>Benchmark set</span>
            <strong>4</strong>
            <small>BTC, ETH, BTC/ETH 50-50, equal-weight universe</small>
          </Card>
          <Card className="benchmark-tile">
            <span>Cost convention</span>
            <strong>20 bps</strong>
            <small>BacktestAgent benchmark cost convention</small>
          </Card>
        </div>
      </Section>
      <Card>
        <div className="card-heading">
          <h3>Candidate allocation availability</h3>
          <span className="status-pill">Research output, not advice</span>
        </div>
        <div className="portfolio-table">
          <div className="portfolio-row header">
            <span>Candidate</span>
            <span>Artifact</span>
            <span>Rows</span>
            <span>Symbols</span>
            <span>Rebalance</span>
            <span>Alpha verified</span>
          </div>
          {portfolioPreviewRows.map((row) => (
            <div key={row.candidate} className="portfolio-row">
              <span>{row.candidate}</span>
              <span>{row.artifact}</span>
              <span>{row.rows}</span>
              <span>{row.symbols}</span>
              <span>{row.rebalance}</span>
              <span>{row.alphaVerified}</span>
            </div>
          ))}
        </div>
      </Card>
      <div className="gallery-grid portfolio-artifact-grid">
        {portfolioArtifacts.map((artifact) => (
          <Card key={artifact.path} className="portfolio-artifact-card">
            <div className="card-topline">
              <span>{artifact.source}</span>
              <em>{artifact.type.toUpperCase()}</em>
            </div>
            <strong>{artifact.title}</strong>
            <p>{artifact.candidate}</p>
            <div className="artifact-facts">
              <span>Rows <b>{artifact.rows}</b></span>
              <span>Symbols <b>{artifact.symbols}</b></span>
              <span>Rebalance <b>{artifact.rebalanceFrequency}</b></span>
              <span>Alpha verified <b>{String(artifact.alphaVerified)}</b></span>
            </div>
            <code>{artifact.path}</code>
            <code>{artifact.manifestPath}</code>
          </Card>
        ))}
      </div>
      <Card>
        <h3>Research-use warning</h3>
        <p>
          These files are historical allocation artifacts created by PortfolioAgent for BacktestAgent evaluation. They are not current holdings, orders, financial advice, or a live trading feed.
        </p>
      </Card>
    </div>
  );
}

function BenchmarkIntelligence() {
  const [focus, setFocus] = useState("BTC");
  const selected = benchmarks.find((item) => item.name === focus) || benchmarks[0];
  return (
    <div className="page">
      <Section
        eyebrow="Benchmark Intelligence"
        title="A strong benchmark stack makes weak alpha claims harder."
        body="CHF evaluates candidates against passive crypto baselines over the exact same window."
      >
        <div className="filter-row">
          {benchmarks.map((item) => (
            <button key={item.name} className={focus === item.name ? "chip active" : "chip"} onClick={() => setFocus(item.name)}>
              {item.name}
            </button>
          ))}
        </div>
      </Section>
      <div className="split-grid">
        <Card className="chart-card">
          <h3>Benchmark returns</h3>
          <ResponsiveContainer width="100%" height={330}>
            <BarChart data={benchmarks}>
              <CartesianGrid strokeDasharray="3 3" stroke="#20304f" />
              <XAxis dataKey="name" stroke="#91a2c4" />
              <YAxis stroke="#91a2c4" tickFormatter={(value) => `${value}%`} />
              <Tooltip formatter={(value) => `${value}%`} contentStyle={{ background: "#0b1220", border: "1px solid #263858" }} />
              <Bar dataKey="returnPct" radius={[12, 12, 0, 0]}>
                {benchmarks.map((entry) => <Cell key={entry.name} fill={entry.name === focus ? "#22d3ee" : entry.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card className="focus-card">
          <span className="eyebrow">Selected benchmark</span>
          <h3>{selected.name}</h3>
          <strong>{selected.returnPct.toFixed(2)}%</strong>
          <p>{selected.description}</p>
          <div className="window-line">
            <span>Start</span><b>2022-12-15</b>
            <span>End</span><b>2026-03-24</b>
            <span>Cost convention</span><b>BacktestAgent benchmark cost</b>
          </div>
        </Card>
      </div>
      <Card>
        <h3>What this tells us</h3>
        <p>
          BTC was the strongest passive benchmark in this window. Any strategy promoted by CHF must clear a demanding crypto beta baseline after costs, which is why the platform avoids weak alpha claims.
        </p>
      </Card>
    </div>
  );
}

function Trust() {
  return (
    <div className="page">
      <Section
        eyebrow="Reproducibility / Trust"
        title="Built for auditability before automation."
        body="The release package makes validation, benchmark windows, and artifact status inspectable."
      >
        <div className="trust-grid">
          {trustBadges.map((badge) => {
            const Icon = badge.icon;
            return (
              <Card key={badge.label} className="trust-card">
                <Icon size={22} />
                <strong>{badge.label}</strong>
                <p>{badge.detail}</p>
              </Card>
            );
          })}
        </div>
      </Section>
      <Card className="commands-card">
        <h3>Validation commands</h3>
        <code>python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py app/*.py jobs/*.py</code>
        <code>python3 -m pytest tests/test_alpha_research_agent.py tests/test_model_agent_research_mode.py tests/test_backtest_agent_research_mode.py -q</code>
      </Card>
    </div>
  );
}

function Roadmap() {
  return (
    <div className="page">
      <Section
        eyebrow="Product Roadmap"
        title="From local MVP to institutional research portal."
        body="Roadmap items are product capabilities, not proven returns."
      >
        <div className="roadmap-columns">
          {roadmapColumns.map((column) => (
            <Card key={column.title} className="roadmap-column">
              <h3>{column.title}</h3>
              {column.items.map((item) => <span key={item}><ChevronRight size={15} />{item}</span>)}
            </Card>
          ))}
        </div>
      </Section>
    </div>
  );
}

function DemoView() {
  return (
    <div className="page">
      <Section
        eyebrow="Demo / Investor View"
        title="Tell the right product story to the right audience."
        body="Different reviewers care about different proof points. CHF can be presented as research infrastructure, product MVP, or quant engineering portfolio."
      >
        <div className="audience-grid">
          {demoAudiences.map((audience) => (
            <Card key={audience.audience} className="audience-card">
              <Eye size={22} />
              <strong>{audience.audience}</strong>
              <p><b>They care about:</b> {audience.cares}</p>
              <p><b>Show them:</b> {audience.show}</p>
            </Card>
          ))}
        </div>
      </Section>
      <Card>
        <h3>Project access modules</h3>
        <div className="access-grid">
          {accessModules.map((module) => (
            <div key={module.name} className="access-row">
              <PanelTop size={18} />
              <div>
                <strong>{module.name}</strong>
                <span>{module.path}</span>
                <p>{module.detail}</p>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function App() {
  const [active, setActive] = useState("landing");
  const activeMeta = useMemo(() => navItems.find((item) => item.id === active), [active]);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">CHF</div>
          <div>
            <strong>Alpha Research OS</strong>
            <span>Crypto quant platform</span>
          </div>
        </div>
        <nav>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button key={item.id} className={active === item.id ? "nav-item active" : "nav-item"} onClick={() => setActive(item.id)}>
                <Icon size={18} />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-card">
          <span>Release</span>
          <strong>{release.tag}</strong>
          <small>{release.window}</small>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            <span className="pulse" />
            <span>{activeMeta?.label}</span>
          </div>
          <div className="topbar-right">
            <span><ShieldCheck size={15} /> Benchmark-aware</span>
            <span><Lock size={15} /> Research mode</span>
          </div>
        </header>

        {active === "landing" && <Landing setActive={setActive} />}
        {active === "why" && <WhyCHF />}
        {active === "console" && <ProductConsole />}
        {active === "agents" && <AgentPipeline />}
        {active === "outputs" && <VerifiedOutputs setActive={setActive} />}
        {active === "gallery" && <VisualizationGallery />}
        {active === "portfolioViewer" && <PortfolioViewer />}
        {active === "portfolio" && <PortfolioIntelligence />}
        {active === "benchmarks" && <BenchmarkIntelligence />}
        {active === "trust" && <Trust />}
        {active === "roadmap" && <Roadmap />}
        {active === "demo" && <DemoView />}
      </main>
    </div>
  );
}

export default App;
