import { useQuery } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api } from "./lib/api";
import ArtifactDetail from "./pages/ArtifactDetail";
import Dashboard from "./pages/Dashboard";
import DocumentDetail from "./pages/DocumentDetail";
import Documents from "./pages/Documents";
import Generate from "./pages/Generate";
import Library from "./pages/Library";
import QuestionBank from "./pages/QuestionBank";
import Settings from "./pages/Settings";

const NAV = [
  { to: "/", label: "Home", icon: "🏠", end: true },
  { to: "/generate", label: "Create", icon: "✨" },
  { to: "/library", label: "Library", icon: "📚" },
  { to: "/documents", label: "Sources", icon: "📄" },
  { to: "/question-bank", label: "Questions", icon: "❓" },
  { to: "/settings", label: "Settings", icon: "⚙️" },
];

function Banner({
  tone,
  action,
  children,
}: {
  tone: "warn" | "danger";
  action: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className="px-4 py-2 text-sm flex flex-wrap items-center gap-2 justify-center"
      style={{
        background: `var(--color-${tone}-soft)`,
        color: `var(--color-${tone})`,
      }}
    >
      <span aria-hidden="true">⚠️</span>
      <span>{children}</span>
      <NavLink to="/settings" className="underline font-semibold">
        {action}
      </NavLink>
    </div>
  );
}

/** Warns at the top of every page about the two things that quietly ruin the experience:
 *  a model backend that is not reachable, and a model that is not actually on the GPU. */
function ConnectionBanner() {
  const { data } = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: 60_000,
  });
  const { data: probe } = useQuery({
    queryKey: ["probe"],
    queryFn: api.probe,
    refetchInterval: 120_000,
    retry: false,
  });

  if (!data) return null;

  // An unreachable backend is the loudest problem, so it wins the banner.
  if (probe && !probe.ok) {
    return (
      <Banner tone="warn" action="Open settings">
        {probe.message}
      </Banner>
    );
  }

  // The quiet problem: the model loaded, but only partly onto the graphics card. Nothing
  // fails — everything is just several times slower — so without saying so here, a
  // teacher would conclude the product is slow rather than that it is misconfigured.
  const placement = data.llm.placement;
  if (placement?.loaded && placement.fully_on_gpu === false) {
    const share = Math.round((placement.gpu_share ?? 0) * 100);
    return (
      <Banner tone="warn" action="Fix this">
        Only {share}% of the model fits on your graphics card, so the rest is running on the
        processor and generation will be slow.
      </Banner>
    );
  }

  return null;
}

export default function App() {
  return (
    <div className="min-h-full flex flex-col">
      <header
        className="sticky top-0 z-40 border-b backdrop-blur"
        style={{
          borderColor: "var(--color-line)",
          background: "color-mix(in srgb, var(--color-paper) 88%, transparent)",
        }}
      >
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-4">
          <NavLink to="/" className="flex items-center gap-2 font-bold shrink-0">
            <span aria-hidden="true">📚</span>
            <span className="hidden sm:inline">Teacher Assistant</span>
          </NavLink>
          <nav className="flex items-center gap-0.5 overflow-x-auto ml-auto">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className="px-2.5 py-1.5 rounded-md text-sm font-medium whitespace-nowrap transition-colors"
                style={({ isActive }) => ({
                  background: isActive ? "var(--color-brand-soft)" : "transparent",
                  color: isActive ? "var(--color-brand)" : "var(--color-ink-soft)",
                })}
              >
                <span aria-hidden="true" className="mr-1">
                  {item.icon}
                </span>
                <span className="hidden md:inline">{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <ConnectionBanner />

      <main className="flex-1 max-w-6xl w-full mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/generate" element={<Generate />} />
          <Route path="/generate/:kind" element={<Generate />} />
          <Route path="/library" element={<Library />} />
          <Route path="/artifacts/:id" element={<ArtifactDetail />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/documents/:id" element={<DocumentDetail />} />
          <Route path="/question-bank" element={<QuestionBank />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <footer
        className="border-t py-4 text-center text-xs text-[color:var(--color-ink-faint)]"
        style={{ borderColor: "var(--color-line)" }}
      >
        Teacher Assistant — runs entirely on your machine. Nothing is sent anywhere.
      </footer>
    </div>
  );
}
