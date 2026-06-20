"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Resume, UserSettings } from "@/lib/types";

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

  // Hydrate form when settings load.
  useEffect(() => {
    if (settings && !form) setForm(settings);
  }, [settings, form]);

  const save = useMutation({
    mutationFn: (body: Partial<UserSettings>) => api.patchSettings(body),
    onSuccess: (data) => {
      toast.success("Saved");
      qc.setQueryData(["settings"], data);
    },
    onError: (err: Error) => toast.error(err.message),
  });

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
          onClick={() => save.mutate(form)}
          disabled={save.isPending}
          className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-4 py-1.5 text-sm font-medium text-black shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
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
            className="glass w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
          />
        </Field>

        <SectionHeader title="Other" />
        <Field label="Timezone" help="IANA name — e.g. 'America/New_York'.">
          <input
            type="text"
            placeholder="America/New_York"
            value={form.timezone ?? ""}
            onChange={(e) => update("timezone", e.target.value || null)}
            className="glass w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
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
      className="glass w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
