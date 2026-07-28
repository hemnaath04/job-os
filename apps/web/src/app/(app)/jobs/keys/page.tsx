"use client";

import {
  ArrowLeft,
  CheckCircle2,
  ExternalLink,
  Eye,
  EyeOff,
  KeyRound,
  Save,
  Server,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { InfoChip, PageIntro } from "@/components/page-intro";
import {
  clearProviderKey,
  hasKey,
  isByoSource,
  loadKeys,
  saveKeys,
  type ByoSource,
  type DiscoveryKeys,
} from "@/lib/discover/keys";
import { BYO_KEY_SOURCES, SOURCE_META } from "@/lib/discover/sources";

const PROVIDERS: ByoSource[] = BYO_KEY_SOURCES.filter(isByoSource);

export default function ConnectSourcesPage() {
  return (
    <div className="workspace-page max-w-5xl">
      <PageIntro
        eyebrow="Bring your own key"
        title="Connect job sources"
        description="Two more job feeds are one free signup away. Paste the key here and the Job finder starts querying them on every search."
        icon={KeyRound}
        action={
          <Link href="/jobs" className="kinetic-button kinetic-button-secondary">
            <ArrowLeft className="size-3.5" /> Back to Job finder
          </Link>
        }
      >
        <InfoChip tone="sage">Stored in this browser only</InfoChip>
        <InfoChip>Free tiers, no card</InfoChip>
      </PageIntro>

      <p className="mt-6 text-sm leading-6 text-[color:var(--color-text-muted)]">
        Keys never leave your machine except to reach the provider you gave them
        to. They live in this browser&apos;s local storage, ride along with each
        search request, and are not written to the database or the server logs.
        Clearing site data, or the Remove button below, wipes them.
      </p>

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        {PROVIDERS.map((source) => (
          <ProviderCard key={source} source={source} />
        ))}
        <ServerKeyCard />
      </div>
    </div>
  );
}

function ProviderCard({ source }: { source: ByoSource }) {
  const meta = SOURCE_META[source];
  const fields = meta.keyFields ?? [];
  const [draft, setDraft] = useState<DiscoveryKeys>({});
  const [connected, setConnected] = useState(false);
  const [revealed, setRevealed] = useState(false);

  // Seeded in an effect rather than useState: localStorage does not exist
  // during the server render, and a mismatch would blank the inputs on hydrate.
  useEffect(() => {
    const stored = loadKeys();
    const seeded: DiscoveryKeys = {};
    for (const field of fields) seeded[field.name] = stored[field.name] ?? "";
    setDraft(seeded);
    setConnected(hasKey(source, stored));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  function onSave() {
    const patch: DiscoveryKeys = {};
    for (const field of fields) {
      patch[field.name] = (draft[field.name] ?? "").trim();
    }
    const missing = fields.filter((field) => !patch[field.name]);
    if (missing.length > 0) {
      toast.error(`Enter the ${missing.map((f) => f.label).join(" and ")}`);
      return;
    }
    setConnected(hasKey(source, saveKeys(patch)));
    setRevealed(false);
    toast.success(`${meta.label} connected`, {
      description: "Switch it on in the Job finder sources list.",
    });
  }

  function onRemove() {
    clearProviderKey(source);
    const cleared: DiscoveryKeys = {};
    for (const field of fields) cleared[field.name] = "";
    setDraft(cleared);
    setConnected(false);
    toast.success(`${meta.label} key removed`);
  }

  return (
    <section className="workspace-panel flex flex-col p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold tracking-tight">{meta.label}</h2>
          <p className="mt-1 text-xs text-[color:var(--color-text-muted)]">
            {meta.hint}
          </p>
        </div>
        {connected ? (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[color:var(--color-mint)]/10 px-2.5 py-1 text-[11px] font-medium text-[color:var(--color-mint)]">
            <CheckCircle2 className="size-3" /> Connected
          </span>
        ) : (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-amber-400/40 bg-amber-400/10 px-2.5 py-1 text-[11px] font-medium text-amber-300">
            Not connected
          </span>
        )}
      </div>

      {meta.keyUrl && (
        <a
          href={meta.keyUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-4 inline-flex w-fit items-center gap-1.5 rounded-full bg-gradient-brand px-3 py-1.5 text-xs font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition hover:scale-[1.02]"
        >
          Get a key <ExternalLink className="size-3" />
        </a>
      )}

      {meta.keySteps && meta.keySteps.length > 0 && (
        <ol className="mt-4 list-decimal space-y-1.5 pl-4 text-xs leading-relaxed text-[color:var(--color-text-muted)]">
          {meta.keySteps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      )}

      <div className="mt-5 space-y-3">
        {fields.map((field) => (
          <div key={field.name}>
            <label
              htmlFor={`${source}-${field.name}`}
              className="text-xs font-medium text-[color:var(--color-text)]"
            >
              {field.label}
            </label>
            <input
              id={`${source}-${field.name}`}
              type={revealed ? "text" : "password"}
              value={draft[field.name] ?? ""}
              onChange={(e) =>
                setDraft((prev) => ({ ...prev, [field.name]: e.target.value }))
              }
              placeholder={field.placeholder}
              autoComplete="off"
              spellCheck={false}
              className="field-control mt-1.5 font-mono text-xs"
            />
          </div>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          onClick={onSave}
          className="kinetic-button kinetic-button-primary"
        >
          <Save className="size-3.5" /> Save
        </button>
        <button
          type="button"
          onClick={() => setRevealed((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
        >
          {revealed ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
          {revealed ? "Hide" : "Show"}
        </button>
        {connected && (
          <button
            type="button"
            onClick={onRemove}
            className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] px-3 py-1.5 text-xs text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-rose-300"
          >
            <Trash2 className="size-3.5" /> Remove
          </button>
        )}
      </div>
    </section>
  );
}

/**
 * TheirStack is keyed too, but its key is an environment variable on the API
 * service, so there is nothing to type here. Shown anyway so the sources list
 * and this page tell the same story.
 */
function ServerKeyCard() {
  const meta = SOURCE_META.theirstack;

  return (
    <section className="workspace-panel flex flex-col p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold tracking-tight">{meta.label}</h2>
          <p className="mt-1 text-xs text-[color:var(--color-text-muted)]">
            {meta.hint}
          </p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1 text-[11px] font-medium text-[color:var(--color-text-muted)]">
          <Server className="size-3" /> Server key
        </span>
      </div>

      <p className="mt-4 text-xs leading-relaxed text-[color:var(--color-text-muted)]">
        This one is configured on the API service rather than in your browser,
        so there is no key to paste here. It charges credits per result, which
        is why it stays switched off until you ask for it.
      </p>

      {meta.keySteps && meta.keySteps.length > 0 && (
        <ol className="mt-4 list-decimal space-y-1.5 pl-4 text-xs leading-relaxed text-[color:var(--color-text-muted)]">
          {meta.keySteps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      )}

      {meta.keyUrl && (
        <a
          href={meta.keyUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-4 inline-flex w-fit items-center gap-1 text-xs text-[color:var(--color-violet)] hover:underline"
        >
          Open {new URL(meta.keyUrl).hostname}
          <ExternalLink className="size-3" />
        </a>
      )}
    </section>
  );
}
