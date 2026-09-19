import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Card, ErrorBanner, Field, Loading, PageHeader, Spinner, Toggle } from "../components/ui";
import { api } from "../lib/api";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 border-b border-[color:var(--color-line)] pb-1.5">
      <dt className="text-[color:var(--color-ink-soft)]">{label}</dt>
      <dd className="font-medium text-right">{value}</dd>
    </div>
  );
}

export default function Settings() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });

  const [draft, setDraft] = useState<Record<string, any>>({});

  useEffect(() => {
    if (settings.data) setDraft(settings.data.values);
  }, [settings.data]);

  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.updateSettings(values),
    onSuccess: (updated) => {
      queryClient.setQueryData(["settings"], updated);
      queryClient.invalidateQueries({ queryKey: ["probe"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
      probe.mutate();
    },
  });

  const reset = useMutation({
    mutationFn: api.resetSettings,
    onSuccess: (updated) => {
      queryClient.setQueryData(["settings"], updated);
      setDraft(updated.values);
    },
  });

  const probe = useMutation({ mutationFn: api.probe });

  if (settings.isLoading) return <Loading />;
  if (settings.error) return <ErrorBanner error={settings.error} onRetry={settings.refetch} />;

  const set = (key: string, value: unknown) =>
    setDraft((previous) => ({ ...previous, [key]: value }));

  const editable = new Set(settings.data?.editable ?? []);
  const changed = Object.keys(draft).filter(
    (key) => editable.has(key) && draft[key] !== settings.data?.values[key],
  );
  const capabilities = Object.entries(status.data?.capabilities ?? {});
  const missing = capabilities.filter(([, value]) => !value.available);

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      <PageHeader
        title="Settings"
        subtitle="Everything here is stored on this machine. Nothing is sent anywhere."
      />

      <Card className="space-y-4">
        <h2 className="font-semibold">Language model</h2>

        <Field
          label="Where the model runs"
          hint="Ollama runs on your own machine with no account and no key."
        >
          <select
            className="field"
            value={draft.llm_provider ?? "ollama"}
            onChange={(event) => set("llm_provider", event.target.value)}
          >
            <option value="ollama">Ollama — local, offline</option>
            <option value="openai_compat">
              Any OpenAI-compatible endpoint — llama.cpp, vLLM, LM Studio, a hosted service
            </option>
          </select>
        </Field>

        <Field label="Address">
          <input
            className="field"
            value={draft.llm_base_url ?? ""}
            onChange={(event) => set("llm_base_url", event.target.value)}
            placeholder="http://localhost:11434"
          />
        </Field>

        <Field
          label="Model"
          hint={
            probe.data?.models_available?.length
              ? `Available: ${probe.data.models_available.slice(0, 6).join(", ")}`
              : "For Ollama, pull one first: ollama pull qwen3:8b"
          }
        >
          {probe.data?.models_available?.length ? (
            <select
              className="field"
              value={draft.llm_model ?? ""}
              onChange={(event) => set("llm_model", event.target.value)}
            >
              {!probe.data.models_available.includes(draft.llm_model) && (
                <option value={draft.llm_model}>{draft.llm_model} (not installed)</option>
              )}
              {probe.data.models_available.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="field"
              value={draft.llm_model ?? ""}
              onChange={(event) => set("llm_model", event.target.value)}
              placeholder="qwen3:8b"
            />
          )}
        </Field>

        {draft.llm_provider === "openai_compat" && (
          <Field label="API key" hint="Left blank for a local server that does not need one.">
            <input
              className="field"
              type="password"
              value={draft.llm_api_key === "********" ? "" : (draft.llm_api_key ?? "")}
              placeholder={draft.llm_api_key === "********" ? "•••••••• (saved)" : ""}
              onChange={(event) => set("llm_api_key", event.target.value)}
            />
          </Field>
        )}

        <div className="grid sm:grid-cols-3 gap-3">
          <Field label="Creativity" hint="0 is repeatable, 1 is varied.">
            <input
              type="number"
              step={0.1}
              min={0}
              max={2}
              className="field"
              value={draft.llm_temperature ?? 0.4}
              onChange={(event) => set("llm_temperature", Number(event.target.value))}
            />
          </Field>
          <Field label="Context window" hint="Tokens. A ceiling — see below.">
            <input
              type="number"
              className="field"
              value={draft.llm_num_ctx ?? 16384}
              onChange={(event) => set("llm_num_ctx", Number(event.target.value))}
            />
          </Field>
          <Field label="Timeout" hint="Seconds.">
            <input
              type="number"
              className="field"
              value={draft.llm_timeout ?? 600}
              onChange={(event) => set("llm_timeout", Number(event.target.value))}
            />
          </Field>
        </div>

        <Toggle
          checked={draft.llm_thinking ?? false}
          onChange={(value) => set("llm_thinking", value)}
          label="Let the model think out loud before answering"
          hint="Slower but sometimes deeper. Measured here: 47s with, 20s without, for the same question. Only affects reasoning models such as Qwen3."
        />

        <div className="flex flex-wrap items-center gap-2">
          <button
            className="btn"
            type="button"
            onClick={() => probe.mutate()}
            disabled={probe.isPending}
          >
            {probe.isPending ? <Spinner /> : "🔌"} Test connection
          </button>
          {probe.data && (
            <span
              className="text-sm"
              style={{ color: probe.data.ok ? "var(--color-success)" : "var(--color-danger)" }}
            >
              {probe.data.ok ? "✓ " : "✗ "}
              {probe.data.message}
              {probe.data.latency_ms ? ` (${Math.round(probe.data.latency_ms)}ms)` : ""}
            </span>
          )}
        </div>
        {probe.error && <ErrorBanner error={probe.error} />}
      </Card>

      <Card className="space-y-4">
        <h2 className="font-semibold">Reading your documents</h2>
        <Toggle
          checked={draft.ocr_enabled ?? true}
          onChange={(value) => set("ocr_enabled", value)}
          label="Read scanned pages with OCR"
          hint="Slower, but the only way to use a photographed or scanned handout."
        />
        <div className="grid sm:grid-cols-3 gap-3">
          <Field label="OCR languages" hint="Tesseract codes, comma separated.">
            <input
              className="field"
              value={draft.ocr_languages ?? "eng"}
              onChange={(event) => set("ocr_languages", event.target.value)}
            />
          </Field>
          <Field label="Passage size" hint="Tokens per chunk.">
            <input
              type="number"
              className="field"
              value={draft.chunk_tokens ?? 512}
              onChange={(event) => set("chunk_tokens", Number(event.target.value))}
            />
          </Field>
          <Field label="Passages retrieved" hint="Per search.">
            <input
              type="number"
              className="field"
              value={draft.retrieve_top_k ?? 12}
              onChange={(event) => set("retrieve_top_k", Number(event.target.value))}
            />
          </Field>
        </div>
        <p className="text-xs text-[color:var(--color-ink-faint)]">
          Changing the passage size only affects documents added afterwards. Re-add an existing
          document to apply it.
        </p>
      </Card>

      <Card className="space-y-3">
        <h2 className="font-semibold">Defaults</h2>
        <div className="grid sm:grid-cols-3 gap-3">
          <Field label="School or university">
            <input
              className="field"
              value={draft.default_institution ?? ""}
              onChange={(event) => set("default_institution", event.target.value)}
            />
          </Field>
          <Field label="Subject">
            <input
              className="field"
              value={draft.default_subject ?? ""}
              onChange={(event) => set("default_subject", event.target.value)}
            />
          </Field>
          <Field label="Class">
            <input
              className="field"
              value={draft.default_grade_level ?? ""}
              onChange={(event) => set("default_grade_level", event.target.value)}
            />
          </Field>
        </div>
      </Card>

      {status.data?.hardware && (
        <Card className="space-y-3">
          <h2 className="font-semibold">This computer</h2>

          <dl className="grid sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <Row label="Graphics" value={status.data.hardware.summary} />
            <Row
              label="Search indexing runs on"
              value={
                status.data.hardware.embeddings_device === "cuda"
                  ? "the GPU — about six times faster than the processor"
                  : "the processor"
              }
            />
            {status.data.llm.context ? (
              <Row
                label="Context window"
                value={`${status.data.llm.context.toLocaleString()} tokens`}
              />
            ) : null}
          </dl>

          {status.data.llm.context_reason && (
            <p className="text-xs text-[color:var(--color-ink-soft)]">
              {status.data.llm.context_reason}
            </p>
          )}

          {status.data.llm.placement?.loaded && (
            <div
              className="rounded-md px-3 py-2 text-sm"
              style={
                status.data.llm.placement.fully_on_gpu
                  ? { background: "var(--color-success-soft)", color: "var(--color-success)" }
                  : { background: "var(--color-warn-soft)", color: "var(--color-warn)" }
              }
            >
              {status.data.llm.placement.fully_on_gpu ? (
                <>
                  ✓ The model is running entirely on the graphics card (
                  {status.data.llm.placement.vram_gb} GB). This is as fast as this machine gets.
                </>
              ) : (
                <>
                  ⚠️ Only {Math.round((status.data.llm.placement.gpu_share ?? 0) * 100)}% of the
                  model fits on the graphics card; the rest is running on the processor, which
                  is several times slower. Lower the context window, or choose a smaller model.
                </>
              )}
            </div>
          )}

          <Toggle
            checked={draft.llm_auto_context ?? true}
            onChange={(value) => set("llm_auto_context", value)}
            label="Fit the context window to the graphics card automatically"
            hint="Turn this off only if you want the exact context size you set above."
          />
        </Card>
      )}

      <Card>
        <h2 className="font-semibold mb-3">What is installed</h2>
        <ul className="space-y-1.5 text-sm">
          {capabilities.map(([key, value]) => (
            <li key={key} className="flex items-start gap-2">
              <span aria-hidden="true" className="pt-0.5">
                {value.available ? "✅" : "⬜"}
              </span>
              <div className="min-w-0">
                <span className={value.available ? "" : "text-[color:var(--color-ink-soft)]"}>
                  {value.label}
                </span>
                <span className="text-xs text-[color:var(--color-ink-faint)] block">
                  {value.enables}
                </span>
                {!value.available && (
                  <code className="text-xs px-1.5 py-0.5 rounded bg-[color:var(--color-surface-2)] inline-block mt-0.5">
                    {value.install}
                  </code>
                )}
              </div>
            </li>
          ))}
        </ul>
        {missing.length === 0 && capabilities.length > 0 && (
          <p className="text-xs mt-3" style={{ color: "var(--color-success)" }}>
            Everything is installed — every feature and every export format is available.
          </p>
        )}
      </Card>

      {save.error && <ErrorBanner error={save.error} />}

      <div
        className="sticky bottom-0 py-3 flex flex-wrap items-center gap-2"
        style={{
          background:
            "linear-gradient(to top, var(--color-paper) 70%, color-mix(in srgb, var(--color-paper) 0%, transparent))",
        }}
      >
        <button
          className="btn btn-primary"
          disabled={changed.length === 0 || save.isPending}
          onClick={() =>
            save.mutate(Object.fromEntries(changed.map((key) => [key, draft[key]])))
          }
        >
          {save.isPending ? <Spinner /> : null}
          {changed.length ? `Save ${changed.length} change${changed.length > 1 ? "s" : ""}` : "Saved"}
        </button>
        <button
          className="btn"
          onClick={() => {
            if (confirm("Reset every setting back to the .env defaults?")) reset.mutate();
          }}
          disabled={reset.isPending}
        >
          Reset to defaults
        </button>
        {(settings.data?.overridden.length ?? 0) > 0 && (
          <span className="text-xs text-[color:var(--color-ink-faint)]">
            {settings.data!.overridden.length} setting
            {settings.data!.overridden.length > 1 ? "s" : ""} changed from the file defaults
          </span>
        )}
      </div>
    </div>
  );
}
