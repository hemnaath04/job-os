"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plug, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { ApifyUsage, Resume, UserSettings, UserSettingsPatch } from "@/lib/types";

const THEMES: { value: UserSettings["theme"]; label: string }[] = [
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
  { value: "system", label: "System" },
];

const FUNCTIONS = ["swe", "ml", "ai", "data", "research", "sre", "infra", "security", "pm", "design"];
const LEVELS = ["intern", "new-grad", "mid", "senior", "staff"];

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.getSettings(),
  });
  const { data: resumes = [] } = useQuery({
    queryKey: ["resumes"],
    queryFn: () => api.listResumes(),
  });

  const [form, setForm] = useState<UserSettings | null>(null);
  // Apify token is entered here but never returned by the API, so it lives in
  // its own transient field (blank unless the user is typing a new key).
  const [apifyToken, setApifyToken] = useState<string>("");

  const { data: apifyUsage } = useQuery({
    queryKey: ["apify-usage"],
    queryFn: () => api.apifyUsage(),
    // Only meaningful once a key exists; refetch is cheap enough to always run.
    staleTime: 60_000,
  });

  // Hydrate form when settings load.
  useEffect(() => {
    if (settings && !form) setForm(settings);
  }, [settings, form]);

  const save = useMutation({
    mutationFn: (body: UserSettingsPatch) => api.patchSettings(body),
    onSuccess: (data) => {
      toast.success("Saved");
      qc.setQueryData(["settings"], data);
      setForm(data); // keep local form (incl. apify_configured) in sync
      setApifyToken("");
      qc.invalidateQueries({ queryKey: ["apify-usage"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const removeApifyKey = useMutation({
    mutationFn: () => api.patchSettings({ apify_api_token: "" }),
    onSuccess: (data) => {
      toast.success("Apify key removed");
      qc.setQueryData(["settings"], data);
      setForm(data);
      setApifyToken("");
      qc.invalidateQueries({ queryKey: ["apify-usage"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  function handleSave() {
    if (!form) return;
    // `apify_configured` is read-only (derived server-side) — don't echo it back.
    const { apify_configured: _configured, ...rest } = form;
    const body: UserSettingsPatch = { ...rest };
    // Only send the token when the user actually typed one this session.
    if (apifyToken.trim()) body.apify_api_token = apifyToken.trim();
    save.mutate(body);
  }

  if (isLoading || !form) {
    return (
      <div className="mx-auto max-w-2xl px-8 py-6 text-sm text-[color:var(--color-text-muted)]">
        loading…
      </div>
    );
  }

  function update<K extends keyof UserSettings>(key: K, value: UserSettings[K]) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  const candidateResumes = resumes.filter((r: Resume) => !r.is_master);

  return (
    <div className="mx-auto max-w-2xl px-8 py-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-medium tracking-tight">Settings</h1>
          <p className="text-sm text-[color:var(--color-text-muted)]">
            Preferences and defaults. Stored on your user record — no shared
            state.
          </p>
        </div>
        <button
          onClick={handleSave}
          disabled={save.isPending}
          className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-4 py-1.5 text-sm font-semibold text-black shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
        >
          <Save className="size-3.5" /> {save.isPending ? "Saving…" : "Save"}
        </button>
      </header>

      <div className="mt-8 space-y-6">
        <SectionHeader title="Appearance" />
        <Field label="Theme" help="Affects the app shell. Light mode is a stub today.">
          <div className="flex gap-2">
            {THEMES.map((t) => (
              <button
                key={t.value}
                onClick={() => update("theme", t.value)}
                className={`rounded-full border px-3 py-1 text-xs ${
                  form.theme === t.value
                    ? "border-[color:var(--color-purple)]/50 bg-[color:var(--color-purple)]/15 text-white shadow-[0_0_20px_-8px_var(--color-purple)]"
                    : "border-white/10 bg-white/[0.03] text-[color:var(--color-text-muted)] hover:text-white"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </Field>

        <SectionHeader title="Tailoring defaults" />
        <Field
          label="Default target resume"
          help="The Tailor page auto-selects this resume when you arrive without one chosen."
        >
          <Select
            value={form.default_resume_id ?? ""}
            onChange={(v) => update("default_resume_id", v || null)}
            options={[
              { value: "", label: "— none —" },
              ...candidateResumes.map((r: Resume) => ({
                value: r.id,
                label: `${r.name}${r.base_role ? ` · ${r.base_role}` : ""}`,
              })),
            ]}
          />
        </Field>

        <SectionHeader title="Discovery defaults" />
        <Field
          label="Function"
          help="Pre-fills Discover-search function filter (swe, ml, etc.)."
        >
          <Select
            value={form.default_function ?? ""}
            onChange={(v) => update("default_function", v || null)}
            options={[
              { value: "", label: "— any —" },
              ...FUNCTIONS.map((f) => ({ value: f, label: f })),
            ]}
          />
        </Field>
        <Field label="Level">
          <Select
            value={form.default_level ?? ""}
            onChange={(v) => update("default_level", v || null)}
            options={[
              { value: "", label: "— any —" },
              ...LEVELS.map((l) => ({ value: l, label: l })),
            ]}
          />
        </Field>
        <Field
          label="Location"
          help="Free-text city or region — e.g. 'Boston' or 'Remote'."
        >
          <input
            type="text"
            value={form.default_location ?? ""}
            onChange={(e) => update("default_location", e.target.value || null)}
            className="w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-[#0A0A0A] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
          />
        </Field>

        <SectionHeader title="Integrations" />
        <Field
          label="Apify API token"
          help="Optional. Unlocks per-board scraping (LinkedIn, Indeed, Glassdoor, Google Jobs, ZipRecruiter, Naukri) on the Discover page. Grab it from apify.com › Settings › Integrations. Stored on your user record; never shown again."
        >
          <div className="flex gap-2">
            <input
              type="password"
              autoComplete="off"
              value={apifyToken}
              onChange={(e) => setApifyToken(e.target.value)}
              placeholder={
                form.apify_configured
                  ? "•••••••••• saved — paste a new token to replace"
                  : "apify_api_xxxxxxxxxxxxxxxxxxxx"
              }
              className="w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-[#0A0A0A] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
            />
            {form.apify_configured && (
              <button
                onClick={() => removeApifyKey.mutate()}
                disabled={removeApifyKey.isPending}
                title="Remove the stored Apify token"
                className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-[color:var(--color-text-muted)] hover:bg-white/[0.06] hover:text-rose-300 disabled:opacity-50"
              >
                <Trash2 className="size-3.5" /> Remove
              </button>
            )}
          </div>
          <ApifyStatus configured={form.apify_configured} usage={apifyUsage} />
        </Field>

        <SectionHeader title="Other" />
        <Field label="Timezone" help="IANA name — e.g. 'America/New_York'.">
          <input
            type="text"
            placeholder="America/New_York"
            value={form.timezone ?? ""}
            onChange={(e) => update("timezone", e.target.value || null)}
            className="w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-[#0A0A0A] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
          />
        </Field>
        <Field label="Weekly summary email" help="Stub — no email is sent yet.">
          <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.weekly_summary_email}
              onChange={(e) => update("weekly_summary_email", e.target.checked)}
              className="size-4 accent-[#CCFF00]"
            />
            <span className="text-[color:var(--color-text-muted)]">
              Send me a weekly digest of applications + upcoming follow-ups.
            </span>
          </label>
        </Field>
      </div>
    </div>
  );
}

function SectionHeader({ title }: { title: string }) {
  return (
    <h2 className="border-b border-white/[0.06] pb-1 text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
      {title}
    </h2>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="text-sm font-medium">{label}</label>
      {help && (
        <p className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">{help}</p>
      )}
      <div className="mt-2">{children}</div>
    </div>
  );
}

function ApifyStatus({
  configured,
  usage,
}: {
  configured: boolean;
  usage: ApifyUsage | undefined;
}) {
  if (!configured) {
    return (
      <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-[color:var(--color-text-dim)]">
        <Plug className="size-3.5" /> No token yet — the Apify job boards stay off
        until you add one.
      </p>
    );
  }
  if (!usage) {
    return (
      <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-[color:var(--color-text-dim)]">
        <Loader2 className="size-3.5 animate-spin" /> Checking Apify balance…
      </p>
    );
  }
  if (!usage.valid) {
    return (
      <p className="mt-2 text-xs text-rose-300">
        {usage.error ?? "Apify rejected this token. Rotate it and re-save."}
      </p>
    );
  }
  const max = usage.max_monthly_usd ?? 0;
  const used = usage.used_usd ?? 0;
  const remaining = usage.remaining_usd ?? 0;
  const pct = max > 0 ? Math.min(100, Math.max(0, (used / max) * 100)) : 0;
  const resets = usage.cycle_end
    ? new Date(usage.cycle_end).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      })
    : null;
  return (
    <div className="glass mt-3 rounded-[var(--radius-card)] p-3 text-xs">
      <div className="flex items-baseline justify-between">
        <span className="text-[color:var(--color-text-muted)]">
          Credits remaining this cycle
        </span>
        <span className="text-sm font-semibold text-[#CCFF00]">
          ${remaining.toFixed(2)}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className="h-full rounded-full bg-gradient-brand"
          style={{ width: `${100 - pct}%` }}
        />
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[color:var(--color-text-dim)]">
        <span>
          ≈{" "}
          <span className="font-medium text-[color:var(--color-text-muted)]">
            {usage.est_searches_left ?? 0}
          </span>{" "}
          searches left
          {usage.est_results_per_search
            ? ` (~${usage.est_results_per_search} results each)`
            : ""}
        </span>
        <span>
          ${used.toFixed(2)} of ${max.toFixed(2)} used
          {resets ? ` · resets ${resets}` : ""}
        </span>
      </div>
    </div>
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-[#0A0A0A] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
