"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Archive,
  ArchiveRestore,
  Award,
  BadgeCheck,
  Briefcase,
  ExternalLink,
  FolderGit2,
  GraduationCap,
  Library,
  Plus,
  ShieldCheck,
  Sparkles,
  Pencil,
  Trash2,
  Upload,
} from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { AddFactDialog } from "@/components/add-fact-dialog";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { api } from "@/lib/api";
import { reportFailure } from "@/lib/errors";
import type { FactBullet, ProfileFact } from "@/lib/types";

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
  const [addOpen, setAddOpen] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const { data: facts = [], isLoading, refetch } = useQuery({
    queryKey: ["facts"],
    queryFn: () => api.listFacts(),
  });
  const {
    data: archivedFacts = [],
    isLoading: archivedLoading,
    refetch: refetchArchived,
  } = useQuery({
    queryKey: ["facts", "archived"],
    queryFn: () => api.listArchivedFacts(),
    enabled: showArchived,
  });

  const grouped = facts.reduce<Record<string, ProfileFact[]>>((acc, f) => {
    (acc[f.kind] ??= []).push(f);
    return acc;
  }, {});
  const verifiedCount = facts.filter((fact) => fact.verified).length;

  async function handleToggleVerified(fact: ProfileFact) {
    try {
      await api.verifyFact(fact.id, !fact.verified);
      await refetch();
    } catch (error) {
      reportFailure(
        fact.verified ? "un-verify this fact" : "verify this fact",
        error,
      );
    }
  }

  async function handleRestore(fact: ProfileFact) {
    try {
      await api.restoreFact(fact.id);
      await Promise.all([refetchArchived(), refetch()]);
      toast.success(`Restored "${fact.title}"`);
    } catch (error) {
      reportFailure("restore this fact", error);
    }
  }

  return (
    <div className="workspace-page max-w-6xl">
      <AddFactDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onCreated={() => refetch()}
      />
      <PageIntro
        eyebrow="Verified evidence vault"
        title="Career profile"
        description="The source of truth behind every generated resume. Experience, projects, education, and skills remain traceable to evidence you control."
        icon={ShieldCheck}
        action={
          <div className="flex items-center gap-2">
            <button
              onClick={() => setAddOpen(true)}
              className="kinetic-button kinetic-button-secondary"
            >
              <Plus className="size-3.5" />
              Add fact
            </button>
            <UploadResumeButton onDone={() => refetch()} />
          </div>
        }
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
          <Section
            key={kind}
            kind={kind}
            items={grouped[kind] ?? []}
            onToggleVerified={handleToggleVerified}
            onDeleted={() => refetch()}
          />
        ))}
      </div>

      <div className="mt-10 border-t border-[color:var(--color-border)] pt-6">
        <button
          type="button"
          onClick={() => setShowArchived((v) => !v)}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text-muted)]"
        >
          <Archive className="size-3.5" />
          {showArchived ? "Hide archived" : "Show archived"}
        </button>
        {showArchived && (
          <div className="mt-4">
            {archivedLoading && <div className="loading-surface" />}
            {!archivedLoading && archivedFacts.length === 0 && (
              <p className="text-sm text-[color:var(--color-text-dim)]">
                Nothing archived.
              </p>
            )}
            {!archivedLoading && archivedFacts.length > 0 && (
              <div className="grid grid-cols-1 gap-2">
                {archivedFacts.map((fact) => (
                  <ArchivedFactRow
                    key={fact.id}
                    fact={fact}
                    onRestore={handleRestore}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ArchivedFactRow({
  fact,
  onRestore,
}: {
  fact: ProfileFact;
  onRestore: (fact: ProfileFact) => void;
}) {
  const subtitle = formatRange(fact.start_date, fact.end_date);
  return (
    <div className="workspace-panel flex items-center justify-between gap-3 p-4 opacity-70">
      <div className="min-w-0">
        <h3 className="truncate text-sm font-medium">{fact.title}</h3>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[color:var(--color-text-dim)]">
          {fact.org && <span>{fact.org}</span>}
          {subtitle && (
            <>
              <span>·</span>
              <span className="font-mono">{subtitle}</span>
            </>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={() => onRestore(fact)}
        className="kinetic-button kinetic-button-secondary shrink-0 text-xs"
      >
        <ArchiveRestore className="size-3.5" />
        Restore
      </button>
    </div>
  );
}

function Section({
  kind,
  items,
  onToggleVerified,
  onDeleted,
}: {
  kind: ProfileFact["kind"];
  items: ProfileFact[];
  onToggleVerified: (fact: ProfileFact) => void;
  onDeleted: () => void;
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
          <FactCard
            key={f.id}
            fact={f}
            onToggleVerified={onToggleVerified}
            onDeleted={onDeleted}
          />
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
            // The 10rem label column leaves too little for the chips on a
            // phone, so the category stacks above them until there is room.
            className="grid grid-cols-1 gap-y-1.5 border-b border-[color:var(--color-border)] py-2 last:border-b-0 sm:grid-cols-[10rem_1fr] sm:gap-x-4"
          >
            <div className="text-sm font-medium text-[color:var(--color-text)]">
              {cat}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {skills.map((s) => (
                <span
                  key={s.id}
                  className="rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2 py-0.5 text-xs text-[color:var(--color-text-muted)]"
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

function FactCard({
  fact,
  onToggleVerified,
  onDeleted,
}: {
  fact: ProfileFact;
  onToggleVerified: (fact: ProfileFact) => void;
  onDeleted: () => void;
}) {
  const subtitle = formatRange(fact.start_date, fact.end_date);
  const [deleting, setDeleting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [title, setTitle] = useState(fact.title);
  const [org, setOrg] = useState(fact.org ?? "");
  const [location, setLocation] = useState(fact.location ?? "");
  // The technologies a project was built with. Stored on the fact's payload,
  // which the tailor's relevance scorer already reads, so a project with none
  // declared scores zero against a job description written in technology
  // nouns however relevant it actually is. This field is the only way to say
  // so, and until now there was no way at all.
  const [tech, setTech] = useState(
    (((fact.payload?.keywords as string[] | undefined) ?? []).join(", ")),
  );

  function startEditing() {
    setTitle(fact.title);
    setOrg(fact.org ?? "");
    setLocation(fact.location ?? "");
    setTech((((fact.payload?.keywords as string[] | undefined) ?? []).join(", ")));
    setEditing(true);
  }

  async function handleSave() {
    if (!title.trim()) {
      toast.error("A fact needs a title");
      return;
    }
    setSaving(true);
    try {
      await api.updateFact(fact.id, {
        title: title.trim(),
        org: org.trim() || null,
        location: location.trim() || null,
        payload: {
          keywords: tech
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean),
        },
      });
      toast.success(`Updated "${title.trim()}"`);
      setEditing(false);
      onDeleted();
    } catch (error) {
      reportFailure("save this fact", error);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (
      !window.confirm(
        `Archive "${fact.title}"? It will remain stored and can be restored from "Show archived".`,
      )
    ) {
      return;
    }
    setDeleting(true);
    try {
      await api.deleteFact(fact.id);
      toast.success(`Archived "${fact.title}"`);
      onDeleted();
    } catch (error) {
      reportFailure("archive this fact", error);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="workspace-panel workspace-panel-interactive p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{fact.title}</h3>
            <button
              type="button"
              onClick={() => onToggleVerified(fact)}
              aria-pressed={fact.verified}
              title={fact.verified ? "Verified, click to un-verify" : "Not verified, click to verify"}
              className={
                fact.verified
                  ? "inline-flex shrink-0 items-center rounded-full p-0.5 text-[color:var(--color-mint)] hover:bg-[color:var(--color-surface-2)]"
                  : "inline-flex shrink-0 items-center gap-1 rounded-full border border-[color:var(--color-border)] px-1.5 py-0.5 text-[10px] font-medium text-[color:var(--color-text-dim)] hover:bg-[color:var(--color-surface-2)]"
              }
            >
              {fact.verified ? (
                <BadgeCheck className="size-3.5" role="img" aria-label="Verified" />
              ) : (
                "Verify"
              )}
            </button>
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
        <button
          type="button"
          onClick={startEditing}
          disabled={editing}
          aria-label={`Edit "${fact.title}"`}
          title="Edit this fact"
          className="flex size-8 shrink-0 items-center justify-center rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-dim)] transition hover:border-[color:var(--color-accent-border)] hover:text-[color:var(--color-accent-ink)] disabled:opacity-50"
        >
          <Pencil className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={handleDelete}
          disabled={deleting}
          aria-label={`Archive "${fact.title}"`}
          title="Archive this fact"
          className="flex size-8 shrink-0 items-center justify-center rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-rose)]/10 hover:text-[color:var(--color-rose-ink)] disabled:opacity-50"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
      {editing && (
        <div className="mt-3 flex flex-col gap-2 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-3">
          <label className="text-[11px] font-medium text-[color:var(--color-text-dim)]">
            Title
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="field-control mt-1 !min-h-8 !py-1.5 !text-xs"
            />
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-[11px] font-medium text-[color:var(--color-text-dim)]">
              Organisation
              <input
                value={org}
                onChange={(e) => setOrg(e.target.value)}
                className="field-control mt-1 !min-h-8 !py-1.5 !text-xs"
              />
            </label>
            <label className="text-[11px] font-medium text-[color:var(--color-text-dim)]">
              Location
              <input
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                className="field-control mt-1 !min-h-8 !py-1.5 !text-xs"
              />
            </label>
          </div>
          <label className="text-[11px] font-medium text-[color:var(--color-text-dim)]">
            Technologies, comma separated
            <input
              value={tech}
              onChange={(e) => setTech(e.target.value)}
              placeholder="Python, FastAPI, LLM Integration, Embeddings"
              className="field-control mt-1 !min-h-8 !py-1.5 !text-xs"
            />
          </label>
          {/* Said plainly, because it is not obvious that a field on a
              profile page decides what a tailored resume leads with. */}
          <p className="text-[11px] leading-relaxed text-[color:var(--color-text-dim)]">
            Tailoring ranks a project by how much of the job description its own
            text matches. A project with nothing listed here cannot match, however
            relevant it is.
          </p>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="rounded-lg border border-[color:var(--color-accent-border)] px-2.5 py-1.5 text-[11px] text-[color:var(--color-accent-ink)] transition-colors hover:bg-[color:var(--color-accent)]/20 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save"}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              disabled={saving}
              className="rounded-lg border border-[color:var(--color-border)] px-2.5 py-1.5 text-[11px] text-[color:var(--color-text-muted)] transition-colors hover:border-[color:var(--color-border-strong)] hover:text-[color:var(--color-text)] disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {fact.bullets.length > 0 && (
        <ul className="mt-3 space-y-1.5 text-sm text-[color:var(--color-text)]">
          {fact.bullets.map((b) => (
            <BulletRow key={b.id} bullet={b} onSaved={onDeleted} />
          ))}
        </ul>
      )}
    </div>
  );
}

// What a bullet is allowed to be before it wraps to a third rendered line and
// starts crowding out a whole other bullet. Mirrors BULLET_MAX_WORDS in
// apps/api resume_writing.py, which is where the resume is actually measured.
const BULLET_MAX_WORDS = 30;

function countWords(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

/**
 * One bullet, editable in place.
 *
 * Adding and deleting a bullet both worked; changing one word did not, so
 * fixing a typo meant deleting the bullet and typing it again. That gap was not
 * cosmetic. A bullet is the only prose on a tailored resume its owner actually
 * wrote, and the tailor prints it as it stands: eleven of this profile's fifteen
 * bullets are over the word cap and seven of fifteen open with the same verb,
 * and the resume inherits every one of those. No rule can fix them, because
 * shortening a claim means deciding which part of it to drop.
 *
 * So the word count is shown while editing rather than after saving. It is the
 * one number that says why a bullet keeps coming back flagged, and it is not
 * useful anywhere the person cannot act on it.
 */
function BulletRow({ bullet, onSaved }: { bullet: FactBullet; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(bullet.text);
  const [saving, setSaving] = useState(false);
  const words = countWords(text);
  const over = words > BULLET_MAX_WORDS;
  const unchanged = text.trim() === bullet.text.trim();

  async function handleSave() {
    if (!text.trim()) {
      toast.error("A bullet needs some text");
      return;
    }
    setSaving(true);
    try {
      await api.updateBullet(bullet.id, { text: text.trim() });
      toast.success("Bullet updated");
      setEditing(false);
      onSaved();
    } catch (error) {
      reportFailure("save this bullet", error);
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <li className="group flex gap-2">
        <span className="mt-1.5 inline-block size-1 shrink-0 rounded-full bg-[color:var(--color-violet)]" />
        <button
          type="button"
          onClick={() => {
            setText(bullet.text);
            setEditing(true);
          }}
          title="Edit this bullet"
          className="-mx-1 rounded px-1 text-left leading-relaxed transition-colors hover:bg-[color:var(--color-surface-2)]"
        >
          {bullet.text}
          {countWords(bullet.text) > BULLET_MAX_WORDS && (
            // Only on the ones that are over. A count beside every bullet is
            // noise; a count beside the four that keep getting flagged is the
            // whole message.
            <span className="ml-1.5 whitespace-nowrap text-[10px] text-[color:var(--color-text-dim)]">
              {countWords(bullet.text)}w
            </span>
          )}
        </button>
      </li>
    );
  }

  return (
    <li className="flex gap-2">
      <span className="mt-1.5 inline-block size-1 shrink-0 rounded-full bg-[color:var(--color-violet)]" />
      <div className="min-w-0 flex-1 space-y-1.5">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          autoFocus
          className="field-control w-full !text-xs"
        />
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || unchanged}
            className="rounded-lg border border-[color:var(--color-accent-border)] px-2.5 py-1.5 text-[11px] text-[color:var(--color-accent-ink)] transition-colors hover:bg-[color:var(--color-accent)]/20 disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save"}
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            disabled={saving}
            className="rounded-lg border border-[color:var(--color-border)] px-2.5 py-1.5 text-[11px] text-[color:var(--color-text-muted)] transition-colors hover:border-[color:var(--color-border-strong)] hover:text-[color:var(--color-text)] disabled:opacity-50"
          >
            Cancel
          </button>
          <span
            className={
              over
                ? "text-[11px] text-[color:var(--color-clay)]"
                : "text-[11px] text-[color:var(--color-text-dim)]"
            }
          >
            {words} of {BULLET_MAX_WORDS} words
            {over && ", the resume prints it as it stands"}
          </span>
        </div>
      </div>
    </li>
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
      reportFailure("import that resume", err);
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
