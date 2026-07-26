"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Award,
  BadgeCheck,
  Briefcase,
  ExternalLink,
  FolderGit2,
  GraduationCap,
  Library,
  ShieldCheck,
  Sparkles,
  Upload,
} from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { api } from "@/lib/api";
import type { ProfileFact } from "@/lib/types";

const KIND_ORDER: ProfileFact["kind"][] = [
  "experience",
  "project",
  "education",
  "skill",
  "certification",
  "publication",
  "award",
  "leadership",
  "volunteering",
];

const KIND_META: Record<
  ProfileFact["kind"],
  { label: string; icon: React.ComponentType<{ className?: string }> }
> = {
  experience: { label: "Experience", icon: Briefcase },
  project: { label: "Projects", icon: FolderGit2 },
  education: { label: "Education", icon: GraduationCap },
  skill: { label: "Skills", icon: Sparkles },
  certification: { label: "Certifications", icon: Award },
  publication: { label: "Publications", icon: Library },
  award: { label: "Awards", icon: Award },
  leadership: { label: "Leadership", icon: Briefcase },
  volunteering: { label: "Volunteering", icon: Briefcase },
};

export default function ProfilePage() {
  const { data: facts = [], isLoading, refetch } = useQuery({
    queryKey: ["facts"],
    queryFn: () => api.listFacts(),
  });

  const grouped = facts.reduce<Record<string, ProfileFact[]>>((acc, f) => {
    (acc[f.kind] ??= []).push(f);
    return acc;
  }, {});
  const verifiedCount = facts.filter((fact) => fact.verified).length;

  return (
    <div className="workspace-page max-w-6xl">
      <PageIntro
        eyebrow="Verified evidence vault"
        title="Career profile"
        description="The source of truth behind every generated resume. Experience, projects, education, and skills remain traceable to evidence you control."
        icon={ShieldCheck}
        action={<UploadResumeButton onDone={() => refetch()} />}
      >
        <InfoChip tone="sage">{verifiedCount} verified facts</InfoChip>
        <InfoChip>{Object.keys(grouped).length} evidence groups</InfoChip>
        <InfoChip tone="clay">{facts.reduce((sum, fact) => sum + fact.bullets.length, 0)} bullets</InfoChip>
      </PageIntro>

      {isLoading && (
        <div className="loading-surface mt-6" />
      )}

      {!isLoading && facts.length === 0 && <EmptyState />}

      <div className="mt-7 space-y-10">
        {KIND_ORDER.filter((k) => grouped[k]?.length).map((kind) => (
          <Section key={kind} kind={kind} items={grouped[kind] ?? []} />
        ))}
      </div>
    </div>
  );
}

function Section({
  kind,
  items,
}: {
  kind: ProfileFact["kind"];
  items: ProfileFact[];
}) {
  const meta = KIND_META[kind];
  const Icon = meta.icon;

  if (kind === "skill") return <SkillsBlock items={items} />;

  return (
    <section>
      <div className="mb-3 flex items-center gap-2">
        <Icon className="size-4 text-[color:var(--color-violet)]" />
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-text-muted)]">
          {meta.label}
        </h2>
        <span className="font-mono text-xs text-[color:var(--color-text-dim)]">
          {items.length}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2">
        {items.map((f) => (
          <FactCard key={f.id} fact={f} />
        ))}
      </div>
    </section>
  );
}

function SkillsBlock({ items }: { items: ProfileFact[] }) {
  // Group by category (stored in `org`).
  const byCat = items.reduce<Record<string, ProfileFact[]>>((acc, f) => {
    (acc[f.org ?? "Other"] ??= []).push(f);
    return acc;
  }, {});
  return (
    <section>
      <div className="mb-3 flex items-center gap-2">
        <Sparkles className="size-4 text-[color:var(--color-violet)]" />
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[color:var(--color-text-muted)]">
          Skills
        </h2>
        <span className="font-mono text-xs text-[color:var(--color-text-dim)]">
          {items.length}
        </span>
      </div>
      <div className="workspace-panel p-5">
        {Object.entries(byCat).map(([cat, skills]) => (
          <div
            key={cat}
            className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1.5 border-b border-white/[0.04] py-2 last:border-b-0"
          >
            <div className="text-sm font-medium text-[color:var(--color-text)]">
              {cat}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {skills.map((s) => (
                <span
                  key={s.id}
                  className="rounded-full border border-white/[0.06] bg-white/[0.03] px-2 py-0.5 text-xs text-[color:var(--color-text-muted)]"
                >
                  {s.title}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function FactCard({ fact }: { fact: ProfileFact }) {
  const subtitle = formatRange(fact.start_date, fact.end_date);
  return (
    <div className="workspace-panel workspace-panel-interactive p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{fact.title}</h3>
            {fact.verified && (
              <BadgeCheck
                className="size-3.5 shrink-0 text-[color:var(--color-mint)]"
                aria-label="verified"
              />
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[color:var(--color-text-muted)]">
            {fact.org && <span>{fact.org}</span>}
            {fact.location && (
              <>
                <span className="text-[color:var(--color-text-dim)]">·</span>
                <span>{fact.location}</span>
              </>
            )}
            {subtitle && (
              <>
                <span className="text-[color:var(--color-text-dim)]">·</span>
                <span className="font-mono">{subtitle}</span>
              </>
            )}
            {fact.source_url && (
              <a
                href={fact.source_url}
                target="_blank"
                rel="noreferrer"
                className="ml-1 inline-flex items-center gap-0.5 text-[color:var(--color-violet)] hover:underline"
              >
                <ExternalLink className="size-3" />
                link
              </a>
            )}
          </div>
        </div>
      </div>
      {fact.bullets.length > 0 && (
        <ul className="mt-3 space-y-1.5 text-sm text-[color:var(--color-text)]">
          {fact.bullets.map((b) => (
            <li key={b.id} className="flex gap-2">
              <span className="mt-1.5 inline-block size-1 shrink-0 rounded-full bg-[color:var(--color-violet)]" />
              <span className="leading-relaxed">{b.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function formatRange(start: string | null, end: string | null): string {
  if (!start && !end) return "";
  const fmt = (s: string | null) => {
    if (!s) return "";
    const [y, m] = s.split("-");
    if (!m) return y;
    const month = [
      "Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ][parseInt(m, 10) - 1];
    return `${month} ${y}`;
  };
  const s = fmt(start);
  const e = fmt(end);
  if (!e) return `${s} to Present`;
  if (!s) return e;
  return `${s} to ${e}`;
}

function UploadResumeButton({ onDone }: { onDone: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  async function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const report = await api.uploadResumeForm(file, { replaceExisting: false });
      toast.success(
        `Imported: +${report.facts_created} facts, +${report.bullets_created} bullets` +
          (report.facts_skipped ? ` (${report.facts_skipped} already existed)` : ""),
      );
      onDone();
    } catch (err) {
      toast.error(`Upload failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
      if (ref.current) ref.current.value = "";
    }
  }

  return (
    <>
      <input
        ref={ref}
        type="file"
        accept=".pdf,.docx,.json"
        className="hidden"
        onChange={onPick}
      />
      <button
        onClick={() => ref.current?.click()}
        disabled={busy}
        className="kinetic-button kinetic-button-secondary disabled:opacity-50"
      >
        <Upload className="size-3.5" />
        {busy ? "Importing…" : "Upload resume"}
      </button>
    </>
  );
}

function EmptyState() {
  return (
    <div className="workspace-panel mt-6 p-10 text-center">
      <Upload className="mx-auto size-6 text-[color:var(--color-violet)]" />
      <h3 className="mt-3 text-base font-medium">No profile data yet</h3>
      <p className="mx-auto mt-1 max-w-md text-sm text-[color:var(--color-text-muted)]">
        Click <strong>Upload resume</strong> above and drop in your master PDF
        (or DOCX). Claude will extract experience, projects, skills, and
        certifications into the verified knowledge base.
      </p>
    </div>
  );
}
