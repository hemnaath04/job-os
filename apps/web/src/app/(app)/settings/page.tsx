"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { InfoChip, PageIntro } from "@/components/page-intro";
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
      <div className="workspace-page max-w-6xl">
        <div className="loading-surface" />
      </div>
    );
  }

  function update<K extends keyof UserSettings>(key: K, value: UserSettings[K]) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  const candidateResumes = resumes.filter((r: Resume) => !r.is_master);

  return (
    <div className="workspace-page max-w-6xl">
      <PageIntro
        eyebrow="Personal operating system"
        title="Preferences"
        description="Set the defaults that shape discovery and tailoring. Everything here stays attached to your private user record."
        icon={SlidersHorizontal}
        action={
          <button onClick={() => save.mutate(form)} disabled={save.isPending} className="kinetic-button kinetic-button-primary disabled:opacity-50">
            <Save className="size-3.5" /> {save.isPending ? "Saving…" : "Save changes"}
          </button>
        }
      >
        <InfoChip tone="sage">Private to your account</InfoChip>
        <InfoChip>Autosynced across devices</InfoChip>
      </PageIntro>

      <div className="mt-6 grid gap-5 lg:grid-cols-2">
        <section className="workspace-panel p-6">
          <SectionHeader title="Appearance" />
          <Field label="Theme" help="Affects the app shell. Light mode is a stub today.">
          <div className="flex gap-2">
            {THEMES.map((t) => (
              <button
                key={t.value}
                onClick={() => update("theme", t.value)}
                className={`rounded-full border px-3 py-1 text-xs ${
                  form.theme === t.value
                    ? "border-[color:var(--color-purple)]/50 bg-[color:var(--color-purple)]/15 text-[color:var(--color-text)] shadow-[0_10px_24px_-18px_rgba(233,198,74,.45)]"
                    : "border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          </Field>
        </section>

        <section className="workspace-panel p-6">
          <SectionHeader title="Tailoring defaults" />
          <Field label="Default target resume" help="Auto-select this role-specific resume when tailoring.">
            <Select value={form.default_resume_id ?? ""} onChange={(v) => update("default_resume_id", v || null)} options={[{ value: "", label: "None" }, ...candidateResumes.map((r: Resume) => ({ value: r.id, label: `${r.name}${r.base_role ? ` · ${r.base_role}` : ""}` }))]} />
          </Field>
        </section>

        <section className="workspace-panel p-6">
          <SectionHeader title="Discovery defaults" />
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Function" help="Pre-fills the role filter.">
              <Select value={form.default_function ?? ""} onChange={(v) => update("default_function", v || null)} options={[{ value: "", label: "Any" }, ...FUNCTIONS.map((f) => ({ value: f, label: f }))]} />
            </Field>
            <Field label="Level">
              <Select value={form.default_level ?? ""} onChange={(v) => update("default_level", v || null)} options={[{ value: "", label: "Any" }, ...LEVELS.map((l) => ({ value: l, label: l }))]} />
            </Field>
          </div>
          <Field label="Location" help="City, region, or Remote.">
            <input type="text" value={form.default_location ?? ""} onChange={(e) => update("default_location", e.target.value || null)} className="field-control" />
          </Field>
        </section>

        <section className="workspace-panel p-6">
          <SectionHeader title="Schedule & updates" />
          <Field label="Timezone" help="IANA name, for example America/New_York.">
            <input type="text" placeholder="America/New_York" value={form.timezone ?? ""} onChange={(e) => update("timezone", e.target.value || null)} className="field-control" />
          </Field>
          <Field label="Weekly summary email" help="Preview only. No email is sent yet.">
            <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-4 text-sm">
              <input type="checkbox" checked={form.weekly_summary_email} onChange={(e) => update("weekly_summary_email", e.target.checked)} className="mt-0.5 size-4 accent-[#8A6D12]" />
              <span className="text-[color:var(--color-text-muted)]">Send a weekly digest of applications and upcoming follow-ups.</span>
            </label>
          </Field>
        </section>
      </div>
    </div>
  );
}

function SectionHeader({ title }: { title: string }) {
  return (
    <h2 className="section-kicker mb-5 border-b border-[color:var(--color-border)] pb-3">
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
    <div className="mt-5 first:mt-0">
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
      className="field-control"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
