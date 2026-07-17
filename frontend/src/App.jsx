import React, { useEffect } from "react";
import {
  HashRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router-dom";
import {
  BrainCircuit,
  CandlestickChart,
  CircleDot,
  FlaskConical,
  Github,
  Globe,
  Home,
  Layers,
  LineChart as LineChartIcon,
  Network,
  Scale,
  ShieldCheck,
  Tag as TagIcon,
  Target,
  TriangleAlert,
  Wallet,
} from "lucide-react";
import { useChfData } from "./useData.js";
import { agents, integrity, limitations, pipeline, site, stack } from "./content.js";
import { Reveal, SectionHead } from "./lib.jsx";
import { Overview, PaperTradePage, FundOpsPage, ResultsPage, SystemPage, AGENT_PAGES } from "./pages.jsx";

const ICONS = {
  Globe, CandlestickChart, Network, Layers, Tag: TagIcon,
  BrainCircuit, Wallet, LineChart: LineChartIcon,
};

/* ---------------------------------------------------------------- chrome -- */
function Topbar({ meta }) {
  return (
    <header className="topbar">
      <Link to="/" className="brand">
        <span className="brand-mark">CHF</span>
        <span className="brand-text">{site.name}<small>{site.tagline}</small></span>
      </Link>
      <div className="topbar-right">
        {meta?.baked_at_utc && (
          <span className="nav-link mono" style={{ fontSize: 11, color: "var(--muted)" }}>
            Data baked: {meta.baked_at_utc.slice(0, 16).replace("T", " ")} UTC
          </span>
        )}
        <NavLink to="/results" className="nav-link">Results</NavLink>
        <NavLink to="/system" className="nav-link">System</NavLink>
        <NavLink to="/methodology" className="nav-link">Methodology</NavLink>
        <a className="nav-cta" href="https://github.com/" target="_blank" rel="noreferrer">
          <Github size={15} /> Repo
        </a>
      </div>
    </header>
  );
}

function Sidebar() {
  const linkClass = ({ isActive }) => `side-link ${isActive ? "active" : ""}`;
  return (
    <aside className="sidebar">
      <nav className="side-nav">
        <NavLink to="/" end className={linkClass}>
          <Home size={17} /> <span>Overview</span>
        </NavLink>
        <NavLink to="/results" className={linkClass}>
          <Target size={17} /> <span>Results &amp; evidence</span>
        </NavLink>
        <NavLink to="/system" className={linkClass}>
          <Layers size={17} /> <span>System</span>
        </NavLink>
        <div className="side-label">Agents</div>
        {agents.map((a) => {
          const Icon = ICONS[a.icon] || CircleDot;
          return (
            <NavLink key={a.slug} to={`/agent/${a.slug}`} className={linkClass}>
              <Icon size={17} />
              <span>{a.name}</span>
              <em className="side-n mono">{a.n}</em>
            </NavLink>
          );
        })}
        <div className="side-label">Validation</div>
        <NavLink to="/papertrade" className={linkClass}>
          <FlaskConical size={17} /> <span>Paper Trading</span>
        </NavLink>
        <NavLink to="/fundops" className={linkClass}>
          <Scale size={17} /> <span>Fund Ops</span>
        </NavLink>
        <div className="side-label">About</div>
        <NavLink to="/methodology" className={linkClass}>
          <ShieldCheck size={17} /> <span>Methodology</span>
        </NavLink>
      </nav>
      <div className="side-foot">
        <span className="dot g" /> alpha_verified = <b>false</b>
      </div>
    </aside>
  );
}

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    const main = document.querySelector(".content");
    if (main) main.scrollTo({ top: 0 });
    window.scrollTo({ top: 0 });
  }, [pathname]);
  return null;
}

/* ----------------------------------------------------------------- pages -- */
function AgentRoute({ data }) {
  const { slug } = useParams();
  const Page = AGENT_PAGES[slug];
  if (!Page) return <NotFound />;
  return <Page data={data} />;
}

const integrityIcons = [Target, ShieldCheck, Scale, CircleDot];

function Methodology() {
  return (
    <div className="page">
      <div className="page-hero">
        <div className="page-hero-icon"><ShieldCheck size={26} /></div>
        <div>
          <span className="eyebrow">Research integrity</span>
          <h1>Built to make a negative result trustworthy</h1>
          <p className="lead">
            The hard part of quant research isn't finding patterns — it's not fooling yourself.
            These contracts are enforced in code and guarded by a dedicated test suite.
          </p>
        </div>
      </div>

      <Reveal>
        <div className="features">
          {integrity.map((f, i) => {
            const Icon = integrityIcons[i % integrityIcons.length];
            return (
              <div className="feature" key={f.title}>
                <div className="fic"><Icon size={20} /></div>
                <h3>{f.title}</h3>
                <p>{f.body}</p>
              </div>
            );
          })}
        </div>
      </Reveal>

      <SectionHead eyebrow="The engine" title="Eight agents, one file-based contract"
        sub="Each stage validates its inputs, does its job, and writes versioned artifacts. The order is fixed and provenance is tracked per run." />
      <Reveal>
        <div className="pipeline">
          {pipeline.map((p) => (
            <div className="stage" key={p.id}>
              <div className="stage-n">{String(p.id).padStart(2, "0")}</div>
              <div className="stage-name">{p.name}</div>
              <p className="stage-sum">{p.summary}</p>
              <p className="stage-detail">{p.detail}</p>
            </div>
          ))}
        </div>
      </Reveal>

      <SectionHead eyebrow="Honest limitations" title="What this study does not claim" />
      <Reveal>
        <div className="notes">
          {limitations.map((l, i) => (
            <div className="note" key={i}><CircleDot className="ic" size={16} /><span>{l}</span></div>
          ))}
        </div>
      </Reveal>
      <Reveal>
        <div style={{ marginTop: 22 }}>
          <div className="card-sub" style={{ marginBottom: 10 }}>Built with</div>
          <div className="chips">{stack.map((t) => <span className="chip" key={t}>{t}</span>)}</div>
        </div>
      </Reveal>
    </div>
  );
}

function NotFound() {
  return (
    <div className="page">
      <div className="state" style={{ minHeight: "50vh" }}>
        <div>
          <TriangleAlert size={28} color="#fbbf24" />
          <p>Page not found.</p>
          <Link className="btn ghost" to="/">Back to overview</Link>
        </div>
      </div>
    </div>
  );
}

function Footer() {
  return (
    <footer className="footer">
      <div className="footer-inner">
        <div className="brand">
          <span className="brand-mark">CHF</span>
          <span className="brand-text">{site.name}<small>{site.tagline}</small></span>
        </div>
        <div className="muted">Research &amp; education only · No verified alpha · Not investment advice</div>
      </div>
    </footer>
  );
}

/* ------------------------------------------------------------------ shell -- */
function Shell() {
  const { data, error, loading } = useChfData();

  if (loading) {
    return (
      <div className="state">
        <div><div className="spinner" />Loading research artifacts…</div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="state">
        <div>
          <TriangleAlert size={28} color="#fbbf24" />
          <p>Could not load the baked data files.</p>
          <p className="mono" style={{ fontSize: 12 }}>Run: python3 frontend/scripts/build_data.py</p>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <Topbar meta={data.meta} />
      <div className="layout">
        <Sidebar />
        <main className="content">
          <ScrollToTop />
          <Routes>
            <Route path="/" element={<Overview data={data} />} />
            <Route path="/results" element={<ResultsPage data={data} />} />
            <Route path="/system" element={<SystemPage data={data} />} />
            <Route path="/agent/:slug" element={<AgentRoute data={data} />} />
            <Route path="/papertrade" element={<PaperTradePage data={data} />} />
            <Route path="/fundops" element={<FundOpsPage data={data} />} />
            <Route path="/methodology" element={<Methodology />} />
            <Route path="/404" element={<NotFound />} />
            <Route path="*" element={<Navigate to="/404" replace />} />
          </Routes>
          <Footer />
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <HashRouter>
      <Shell />
    </HashRouter>
  );
}
