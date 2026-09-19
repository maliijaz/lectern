/** Small shared primitives. Deliberately plain — the interest belongs in the content. */

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { ApiError } from "../lib/api";

export function Card({
  children,
  className = "",
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return <div className={`card ${padded ? "p-4" : ""} ${className}`}>{children}</div>;
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
      <div className="min-w-0">
        <h1 className="text-xl font-bold tracking-tight truncate">{title}</h1>
        {subtitle && <p className="text-[color:var(--color-ink-soft)] text-sm mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className="animate-spin shrink-0"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.2" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-[color:var(--color-ink-soft)] text-sm py-8 justify-center">
      <Spinner />
      {label}…
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
  icon = "📄",
}: {
  title: string;
  body?: string;
  action?: ReactNode;
  icon?: string;
}) {
  return (
    <div className="text-center py-14 px-4">
      <div className="text-3xl mb-2">{icon}</div>
      <p className="font-semibold">{title}</p>
      {body && (
        <p className="text-[color:var(--color-ink-soft)] text-sm mt-1 max-w-md mx-auto">{body}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Shows the server's own message, plus its install hint when a dependency is missing. */
export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const isApi = error instanceof ApiError;
  const message = error instanceof Error ? error.message : String(error);
  const install = isApi ? error.install : "";

  return (
    <div
      role="alert"
      className="rounded-lg border px-3.5 py-3 text-sm"
      style={{
        borderColor: "color-mix(in srgb, var(--color-danger) 35%, transparent)",
        background: "var(--color-danger-soft)",
      }}
    >
      <div className="flex items-start gap-2">
        <span aria-hidden="true">⚠️</span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold" style={{ color: "var(--color-danger)" }}>
            {message}
          </p>
          {install && (
            <p className="mt-1.5 text-[color:var(--color-ink-soft)]">
              Install it with{" "}
              <code className="px-1.5 py-0.5 rounded bg-[color:var(--color-surface-2)] text-xs">
                {install}
              </code>
            </p>
          )}
          {onRetry && (
            <button className="btn mt-2.5" onClick={onRetry} type="button">
              Try again
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function ProgressBar({
  value,
  label,
  tone = "brand",
}: {
  value: number;
  label?: string;
  tone?: "brand" | "success" | "danger";
}) {
  const percent = Math.round(Math.min(1, Math.max(0, value)) * 100);
  const colour = `var(--color-${tone})`;
  return (
    <div>
      {label && (
        <div className="flex justify-between text-xs text-[color:var(--color-ink-soft)] mb-1.5">
          <span className="truncate pr-2">{label}</span>
          <span className="tabular-nums shrink-0">{percent}%</span>
        </div>
      )}
      <div
        className="h-1.5 rounded-full overflow-hidden"
        style={{ background: "var(--color-surface-2)" }}
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full transition-[width] duration-300"
          style={{ width: `${percent}%`, background: colour }}
        />
      </div>
    </div>
  );
}

const STATUS_TONES: Record<string, { bg: string; fg: string }> = {
  ready: { bg: "var(--color-success-soft)", fg: "var(--color-success)" },
  succeeded: { bg: "var(--color-success-soft)", fg: "var(--color-success)" },
  generating: { bg: "var(--color-brand-soft)", fg: "var(--color-brand)" },
  running: { bg: "var(--color-brand-soft)", fg: "var(--color-brand)" },
  parsing: { bg: "var(--color-brand-soft)", fg: "var(--color-brand)" },
  indexing: { bg: "var(--color-brand-soft)", fg: "var(--color-brand)" },
  queued: { bg: "var(--color-surface-2)", fg: "var(--color-ink-soft)" },
  uploaded: { bg: "var(--color-surface-2)", fg: "var(--color-ink-soft)" },
  draft: { bg: "var(--color-surface-2)", fg: "var(--color-ink-soft)" },
  failed: { bg: "var(--color-danger-soft)", fg: "var(--color-danger)" },
  cancelled: { bg: "var(--color-warn-soft)", fg: "var(--color-warn)" },
};

export function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONES[status] ?? STATUS_TONES.queued;
  return (
    <span className="chip" style={{ background: tone.bg, color: tone.fg }}>
      {status}
    </span>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="block text-xs text-[color:var(--color-ink-faint)] mt-1">{hint}</span>}
    </label>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <label className="flex items-start gap-2.5 cursor-pointer select-none py-1">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 w-4 h-4 accent-[color:var(--color-brand)] shrink-0"
      />
      <span className="min-w-0">
        <span className="text-sm">{label}</span>
        {hint && (
          <span className="block text-xs text-[color:var(--color-ink-faint)]">{hint}</span>
        )}
      </span>
    </label>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  width = "40rem",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[8vh] overflow-y-auto"
      style={{ background: "rgba(0,0,0,.45)" }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="card w-full shadow-xl"
        style={{ maxWidth: width }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-[color:var(--color-line)]">
          <h2 className="font-semibold">{title}</h2>
          <button className="btn !px-2 !py-1" onClick={onClose} type="button" aria-label="Close">
            ✕
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

/** Copy-to-clipboard button that confirms itself, then quietly resets. */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="btn"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1600);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? "✓ Copied" : label}
    </button>
  );
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: string; label: string; badge?: number }[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div
      className="flex gap-1 border-b overflow-x-auto"
      style={{ borderColor: "var(--color-line)" }}
      role="tablist"
    >
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab.id)}
            className="px-3 py-2 text-sm font-medium whitespace-nowrap -mb-px border-b-2 transition-colors"
            style={{
              borderColor: selected ? "var(--color-brand)" : "transparent",
              color: selected ? "var(--color-brand)" : "var(--color-ink-soft)",
            }}
          >
            {tab.label}
            {tab.badge !== undefined && (
              <span className="ml-1.5 text-xs text-[color:var(--color-ink-faint)]">{tab.badge}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
