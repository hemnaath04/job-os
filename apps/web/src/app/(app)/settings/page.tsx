"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { Field, type FieldControlProps, FieldGroup } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import { reportFailure } from "@/lib/errors";
import type {
  Resume,
  SeniorityLevel,
  UserSettings,
  UserSettingsPatch,
  WorkAuthorizationStatus,
  WorkEligibility,
  WorkModel,
} from "@/lib/types";

const THEMES: { value: UserSettings["theme"]; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

const FUNCTIONS = ["swe", "ml", "ai", "data", "research", "sre", "infra", "security", "pm", "design"];
const LEVELS: SeniorityLevel[] = ["intern", "new-grad", "mid", "senior", "staff"];

const WORK_AUTHORIZATIONS: { value: WorkAuthorizationStatus; label: string }[] = [
  { value: "us_citizen", label: "US citizen" },
  { value: "permanent_resident", label: "Permanent resident" },
  { value: "f1_student", label: "F-1 student" },
  { value: "visa_holder_needs_transfer", label: "Visa holder, needs transfer" },
  { value: "needs_sponsorship", label: "Needs sponsorship" },
  { value: "other", label: "Other" },
];

/**
 * The four questions a posting can ask, each as its own answer.
 *
 * Worded as things the user knows about themselves, not as legal tests. The
 * gate that reads these will refuse to write a resume for some postings, so
 * every one of them has to be a question the person can answer honestly
 * without a lawyer, and the help text says who actually decides where the
 * answer is not theirs to give.
 *
 * `invert` is for the one field whose stored sense is the opposite of the
 * checkbox: `us_person_for_export_control` defaults true so that an untouched
 * account is never flagged, but the box a user ticks reads better as the
 * exception rather than the rule.
 */
const ELIGIBILITY_QUESTIONS: {
  key: keyof Omit<WorkEligibility, "status">;
  label: string;
  help: string;
  invert?: boolean;
}[] = [
  {
    key: "may_work_without_sponsorship_now",
    label: "I believe I can start work now without the employer filing anything",
    help: "Worded as a belief because that is all it can be. Authorization like CPT is granted by your school for one specific employer and date range, against an offer you already have, so nobody can answer this in advance for jobs you have not applied to. Nothing is ever skipped because of this answer: it only changes how a sponsorship note is worded, and leaving it unticked costs you nothing.",
  },
  {
    key: "needs_future_sponsorship",
    label: "I will need sponsorship to keep working later",
    help: "A posting saying you must be authorized \u201cnow or in the future\u201d is asking about this. It is the one answer that stops a resume being written.",
  },
  {
    key: "us_person_for_export_control",
    label: "I am not a US person for export control (ITAR/EAR)",
    help: "Only ever shown as a note, never used to skip a job. Export control law does not restrict who an employer may hire, and postings carry this text as boilerplate, so it is surfaced for you to check rather than acted on.",
    invert: true,
  },
  {
    key: "clearance_eligible",
    label: "I could hold a US security clearance",
    help: "Clearance eligibility is limited to US citizens, with no temporary or interim route, so a posting requiring one is the clearest case where a resume cannot help.",
  },
];

const WORK_MODELS: { value: WorkModel; label: string }[] = [
  { value: "onsite", label: "Onsite" },
  { value: "hybrid", label: "Hybrid" },
  { value: "remote", label: "Remote" },
];

const JOB_AGES = [7, 14, 30, 60, 90, 180];

/** Matches the API bound on `salary_floor`. Clamped rather than rejected. */
const SALARY_CEILING = 100_000_000;

const CURRENCY_CODE = /^[A-Za-z]{3}$/;

const DEFAULT_SETTINGS: UserSettings = {
  theme: "light",
  default_resume_id: null,
  default_function: null,
  default_level: null,
  default_location: null,
  timezone: null,
  target_titles: [],
  work_eligibility: {
    status: null,
    may_work_without_sponsorship_now: false,
    needs_future_sponsorship: false,
    // True, and it is the value that FLAGS NOTHING. Every default here lets a
    // posting through, so an account that never opens this page behaves
    // exactly as it did before eligibility existed.
    us_person_for_export_control: true,
    clearance_eligible: false,
  },
  salary_floor: null,
  salary_currency: "USD",
  seniority_range: { min: null, max: null },
  work_models: [],
  target_companies: [],
  excluded_companies: [],
  max_job_age_days: 30,
  locations: [],
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

/**
 * What gets sent on save. `default_location` is derived rather than edited: the
 * single-location field predates the list that replaced it and is still read by
 * the Job Finder, so the page keeps it pointing at the first location instead of
 * leaving it on whatever city was there before.
 */
function toPayload(form: UserSettings): UserSettingsPatch {
  const payload: UserSettingsPatch = { ...form, default_location: form.locations[0] ?? null };
  // A cleared or half-typed currency box is not an instruction to change the
  // currency. Sent as-is it would fail validation and take the whole save with
  // it, so the key is left out and the stored value stands.
  if (!CURRENCY_CODE.test(form.salary_currency)) delete payload.salary_currency;
  return payload;
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data: settings, isError: loadFailed } = useQuery({
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
  // Separate from `hydrated`, which a local edit also sets: this one is only ever
  // true because the server answered. Save is gated on it, because saving before
  // then would write the defaults above over real preferences, which is how this
  // page once blanked them. Widening the schema widened that blast radius from
  // six fields to fifteen.
  const [loadedFromServer, setLoadedFromServer] = useState(false);
  // Reflect the actual active theme (client-controlled) in the Theme control,
  // rather than whatever the server last stored.
  useEffect(() => {
    const isDark = document.documentElement.classList.contains("dark");
    setForm((prev) => ({ ...prev, theme: isDark ? "dark" : "light" }));
  }, []);
  useEffect(() => {
    if (settings && !hydrated) {
      // Defaults underneath the response, not just on top of state: a backend
      // one deploy behind can answer without a field this page now edits, and
      // spreading that straight in would leave a control bound to undefined.
      setForm((prev) => ({ ...DEFAULT_SETTINGS, ...settings, theme: prev.theme }));
      setHydrated(true);
      setLoadedFromServer(true);
    }
  }, [settings, hydrated]);

  const save = useMutation({
    mutationFn: (body: UserSettingsPatch) => api.patchSettings(body),
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

  function toggleWorkModel(model: WorkModel) {
    update(
      "work_models",
      form.work_models.includes(model)
        ? form.work_models.filter((m) => m !== model)
        : [...form.work_models, model],
    );
  }

  /**
   * Keeps the seniority band the right way round by moving the other end. The
   * API rejects an inverted range, and failing a whole save over the order two
   * selects happened to be touched in teaches the user nothing.
   */
  function setEligibility<K extends keyof WorkEligibility>(key: K, value: WorkEligibility[K]) {
    update("work_eligibility", { ...form.work_eligibility, [key]: value });
  }

  function setSeniority(edge: "min" | "max", level: SeniorityLevel | null) {
    const next = { ...form.seniority_range, [edge]: level };
    if (next.min && next.max && LEVELS.indexOf(next.min) > LEVELS.indexOf(next.max)) {
      if (edge === "min") next.max = level;
      else next.min = level;
    }
    update("seniority_range", next);
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
          <button
            onClick={() => save.mutate(toPayload(form))}
            disabled={save.isPending || !loadedFromServer}
            title={loadedFromServer ? undefined : "Waiting for your saved preferences to load"}
            className="kinetic-button kinetic-button-primary disabled:opacity-50"
          >
            <Save className="size-3.5" /> {save.isPending ? "Saving…" : "Save changes"}
          </button>
        }
      >
        <InfoChip tone="sage">Private to your account</InfoChip>
        <InfoChip>Autosynced across devices</InfoChip>
        {loadFailed && (
          <InfoChip tone="clay">
            Could not load your saved preferences, so saving is off until they load
          </InfoChip>
        )}
      </PageIntro>

      <div className="mt-6 grid gap-5 lg:grid-cols-2">
        <section className="workspace-panel p-6">
          <SectionHeader title="Appearance" />
          <FieldGroup label="Theme" help="Switches the whole app between light and dark.">
            <div className="flex gap-2">
              {THEMES.map((t) => (
                <Chip key={t.value} pressed={form.theme === t.value} onClick={() => update("theme", t.value)}>
                  {t.label}
                </Chip>
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
          <SectionHeader title="Target roles" />
          <Field label="Titles" help="The roles you want. Press Enter after each one. Search uses these so you do not retype them.">
            {(control) => <TagInput control={control} value={form.target_titles} onChange={(v) => update("target_titles", v)} placeholder="Software Engineer" />}
          </Field>
          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <Field label="Seniority from">
              {(control) => <Select {...control} value={form.seniority_range.min ?? ""} onChange={(v) => setSeniority("min", (v || null) as SeniorityLevel | null)} options={[{ value: "", label: "Any" }, ...LEVELS.map((l) => ({ value: l, label: l }))]} />}
            </Field>
            <Field label="Seniority to">
              {(control) => <Select {...control} value={form.seniority_range.max ?? ""} onChange={(v) => setSeniority("max", (v || null) as SeniorityLevel | null)} options={[{ value: "", label: "Any" }, ...LEVELS.map((l) => ({ value: l, label: l }))]} />}
            </Field>
          </div>
        </section>

        <section className="workspace-panel p-6">
          <SectionHeader title="Eligibility" />
          <Field label="Work authorization" help="Compared against what a posting requires, so a role that cannot hire you is stopped before a resume is written for it. Leave every answer blank and nothing is ever stopped.">
            {(control) => <Select {...control} value={form.work_eligibility.status ?? ""} onChange={(v) => setEligibility("status", (v || null) as WorkAuthorizationStatus | null)} options={[{ value: "", label: "Not specified" }, ...WORK_AUTHORIZATIONS]} />}
          </Field>
          {/* Shown only once a status is chosen. Before that these four
              questions have no context and answering them would be guessing;
              the gate also ignores them until a status is set, so an unanswered
              form and a hidden one behave identically. */}
          {form.work_eligibility.status && (
            <FieldGroup
              label="What a posting can ask"
              help="Each one is compared against a clause a posting might carry. Nothing here is worked out for you, and nothing you leave unticked is assumed."
              className="mt-5"
            >
              <div className="flex flex-col gap-3">
                {ELIGIBILITY_QUESTIONS.map((question) => {
                  const stored = form.work_eligibility[question.key];
                  const checked = question.invert ? !stored : Boolean(stored);
                  return (
                    <label key={question.key} className="flex gap-2.5 text-sm">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) =>
                          setEligibility(
                            question.key,
                            question.invert ? !e.target.checked : e.target.checked,
                          )
                        }
                        className="mt-0.5 size-4 shrink-0 accent-[color:var(--color-accent)]"
                      />
                      <span className="min-w-0">
                        <span className="block text-[color:var(--color-text)]">{question.label}</span>
                        <span className="mt-0.5 block text-xs leading-relaxed text-[color:var(--color-text-dim)]">
                          {question.help}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </FieldGroup>
          )}
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
          <Field label="Locations" help="Cities, regions, or Remote. Press Enter after each one. Leave empty to search anywhere." className="mt-5">
            {(control) => <TagInput control={control} value={form.locations} onChange={(v) => update("locations", v)} placeholder="Boston, MA" />}
          </Field>
          <FieldGroup label="Work model" help="Leave all off to accept any of the three." className="mt-5">
            <div className="flex gap-2">
              {WORK_MODELS.map((m) => (
                <Chip key={m.value} pressed={form.work_models.includes(m.value)} onClick={() => toggleWorkModel(m.value)}>
                  {m.label}
                </Chip>
              ))}
            </div>
          </FieldGroup>
          <Field label="Posted within" help="How old a posting can be and still show up." className="mt-5">
            {(control) => <Select {...control} value={String(form.max_job_age_days)} onChange={(v) => update("max_job_age_days", Number(v))} options={JOB_AGES.map((d) => ({ value: String(d), label: `${d} days` }))} />}
          </Field>
        </section>

        <section className="workspace-panel p-6">
          <SectionHeader title="Compensation" />
          <div className="grid gap-5 sm:grid-cols-[2fr_1fr]">
            <Field label="Salary floor" help="Lowest base pay per year you would accept. Leave empty for no floor.">
              {(control) => <input {...control} type="number" inputMode="numeric" min={0} max={SALARY_CEILING} step={1000} value={form.salary_floor ?? ""} onChange={(e) => update("salary_floor", parseSalary(e.target.value))} className="field-control" />}
            </Field>
            <Field label="Currency">
              {(control) => <input {...control} type="text" maxLength={3} autoCapitalize="characters" placeholder="USD" value={form.salary_currency} onChange={(e) => update("salary_currency", e.target.value.toUpperCase())} className="field-control" />}
            </Field>
          </div>
        </section>

        <section className="workspace-panel p-6">
          <SectionHeader title="Companies" />
          <Field label="Target companies" help="Surfaced first when they are hiring. Press Enter after each one. This ranks results, it does not hide anything.">
            {(control) => <TagInput control={control} value={form.target_companies} onChange={(v) => update("target_companies", v)} placeholder="Anthropic" />}
          </Field>
          <Field label="Excluded companies" help="Dropped from results entirely. Press Enter after each one." className="mt-5">
            {(control) => <TagInput control={control} value={form.excluded_companies} onChange={(v) => update("excluded_companies", v)} placeholder="Agency you never want to hear from" />}
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

/**
 * An empty box means no floor, which is a different answer from zero. Anything
 * unparseable is read the same way rather than kept as NaN, which would fail the
 * whole save; anything past the API's ceiling is clamped for the same reason.
 */
function parseSalary(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < 0) return null;
  return Math.min(Math.round(value), SALARY_CEILING);
}

function SectionHeader({ title }: { title: string }) {
  return (
    <h2 className="section-kicker mb-5 border-b border-[color:var(--color-border)] pb-3">
      {title}
    </h2>
  );
}

/**
 * The page's toggle pill. Extracted from the Theme row so the work model row
 * cannot drift from it; the markup it renders is unchanged.
 */
function Chip({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={pressed}
      className={`rounded-full border px-3 py-1 text-xs ${
        pressed
          ? "border-[color:var(--color-purple)]/50 bg-[color:var(--color-purple)]/15 text-[color:var(--color-text)] shadow-[0_10px_24px_-18px_rgba(233,198,74,.45)]"
          : "border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
      }`}
    >
      {children}
    </button>
  );
}

/**
 * A list of short strings, edited as removable chips.
 *
 * Enter commits what is typed, and so does leaving the box: a value typed and
 * then abandoned in favour of the Save button is the likeliest way to lose one,
 * and losing a target title silently is worse than adding one twice.
 *
 * A comma is deliberately not a delimiter. It would be a reasonable one for
 * titles and companies and a wrong one for the field that needs this most:
 * "Boston, MA" is one location, and splitting it would leave a user with two
 * chips that match nothing and no obvious sign of why.
 *
 * Duplicates are folded case-insensitively here as well as on the server, so
 * the list a user sees is the list that gets stored.
 */
function TagInput({
  value,
  onChange,
  placeholder,
  control,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
  control: FieldControlProps;
}) {
  const [draft, setDraft] = useState("");

  function commit(raw: string) {
    const entry = raw.trim();
    setDraft("");
    if (!entry) return;
    if (value.some((existing) => existing.toLowerCase() === entry.toLowerCase())) return;
    onChange([...value, entry]);
  }

  return (
    <div>
      {value.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-2">
          {value.map((entry) => (
            <li key={entry}>
              <button
                type="button"
                onClick={() => onChange(value.filter((v) => v !== entry))}
                aria-label={`Remove ${entry}`}
                className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-purple)]/50 bg-[color:var(--color-purple)]/15 px-3 py-1 text-xs text-[color:var(--color-text)]"
              >
                {entry}
                <X className="size-3 text-[color:var(--color-text-muted)]" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        {...control}
        type="text"
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit(draft);
          } else if (e.key === "Backspace" && !draft && value.length > 0) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={() => commit(draft)}
        className="field-control"
      />
    </div>
  );
}
