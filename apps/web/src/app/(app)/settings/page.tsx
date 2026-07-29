"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { Field, FieldGroup } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import { reportFailure } from "@/lib/errors";
import type { Resume, UserSettings } from "@/lib/types";

const THEMES: { value: UserSettings["theme"]; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

const FUNCTIONS = ["swe", "ml", "ai", "data", "research", "sre", "infra", "security", "pm", "design"];
const LEVELS = ["intern", "new-grad", "mid", "senior", "staff"];

const DEFAULT_SETTINGS: UserSettings = {
  theme: "light",
  default_resume_id: null,
  default_function: null,
  default_level: null,
  default_location: null,
  timezone: null,
};

function applyTheme(theme: UserSettings["theme"]) {
  if (typeof document === "undefined") return;
  const dark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  try {
    if (theme === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", theme);
  } catch {
    /* storage unavailable; class still applied for this session */
  }
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.getSettings(),
    retry: 1,
  });
  const { data: resumes = [] } = useQuery({
    queryKey: ["resumes"],
    queryFn: () => api.listResumes(),
  });

  // Open immediately with defaults; hydrate from the server when it responds,
  // but never block the page on a slow or unavailable backend.
  const [form, setForm] = useState<UserSettings>(DEFAULT_SETTINGS);
  const [hydrated, setHydrated] = useState(false);
  // Reflect the actual active theme (client-controlled) in the Theme control,
  // rather than whatever the server last stored.
  useEffect(() => {
    const isDark = document.documentElement.classList.contains("dark");
    setForm((prev) => ({ ...prev, theme: isDark ? "dark" : "light" }));
  }, []);
  useEffect(() => {
    if (settings && !hydrated) {
      setForm((prev) => ({ ...settings, theme: prev.theme }));
      setHydrated(true);
    }
  }, [settings, hydrated]);

  const save = useMutation({
    mutationFn: (body: Partial<UserSettings>) => api.patchSettings(body),
    onSuccess: (data) => {
      toast.success("Saved");
      qc.setQueryData(["settings"], data);
    },
    onError: (err: Error) => reportFailure("save your preferences", err),
  });

  function update<K extends keyof UserSettings>(key: K, value: UserSettings[K]) {
    setHydrated(true);
    setForm((prev) => ({ ...prev, [key]: value }));
    if (key === "theme") applyTheme(value as UserSettings["theme"]);
  }

  const candidateResumes = resumes.filter((r: Resume) => !r.is_master);

  return (
    <div className="workspace-page max-w-6xl">
      <PageIntro
        eyebrow="Personal operating system"
        title="Settings"
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
          <FieldGroup label="Theme" help="Switches the whole app between light and dark.">
            <div className="flex gap-2">
              {THEMES.map((t) => (
                <button
                  key={t.value}
                  onClick={() => update("theme", t.value)}
                  aria-pressed={form.theme === t.value}
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
          </FieldGroup>
        </section>

        <section className="workspace-panel p-6">
          <SectionHeader title="Tailoring defaults" />
          <Field label="Default target resume" help="Auto-select this role-specific resume when tailoring.">
            {(control) => <Select {...control} value={form.default_resume_id ?? ""} onChange={(v) => update("default_resume_id", v || null)} options={[{ value: "", label: "None" }, ...candidateResumes.map((r: Resume) => ({ value: r.id, label: `${r.name}${r.base_role ? ` · ${r.base_role}` : ""}` }))]} />}
          </Field>
        </section>

        <section className="workspace-panel p-6">
          <SectionHeader title="Discovery defaults" />
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Function" help="Pre-fills the role filter.">
              {(control) => <Select {...control} value={form.default_function ?? ""} onChange={(v) => update("default_function", v || null)} options={[{ value: "", label: "Any" }, ...FUNCTIONS.map((f) => ({ value: f, label: f }))]} />}
            </Field>
            <Field label="Level">
              {(control) => <Select {...control} value={form.default_level ?? ""} onChange={(v) => update("default_level", v || null)} options={[{ value: "", label: "Any" }, ...LEVELS.map((l) => ({ value: l, label: l }))]} />}
            </Field>
          </div>
          <Field label="Location" help="City, region, or Remote." className="mt-5">
            {(control) => <input {...control} type="text" autoComplete="address-level2" value={form.default_location ?? ""} onChange={(e) => update("default_location", e.target.value || null)} className="field-control" />}
          </Field>
        </section>

        <section className="workspace-panel p-6">
          <SectionHeader title="Schedule" />
          <Field label="Timezone" help="IANA name, for example America/New_York.">
            {(control) => <input {...control} type="text" placeholder="America/New_York" value={form.timezone ?? ""} onChange={(e) => update("timezone", e.target.value || null)} className="field-control" />}
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


