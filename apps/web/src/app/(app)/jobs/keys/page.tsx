"use client";

import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  ExternalLink,
  Eye,
  EyeOff,
  KeyRound,
  Pencil,
  Plug,
  Save,
  Server,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { InfoChip, PageIntro } from "@/components/page-intro";
import {
  acceptTerms,
  getAcceptedAt,
  isValidCustomUrl,
  loadCustomSources,
  removeCustomSource,
  revokeTerms,
  setCustomEnabled,
  upsertCustomSource,
  type CustomSource,
} from "@/lib/discover/custom-sources";
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

      <CustomSourcesSection />
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
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[color:var(--color-mint)]/10 px-2.5 py-1 text-[11px] font-medium text-[color:var(--color-mint-ink)]">
            <CheckCircle2 className="size-3" /> Connected
          </span>
        ) : (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[color:var(--color-amber)]/45 bg-[color:var(--color-amber)]/12 px-2.5 py-1 text-[11px] font-medium text-[color:var(--color-amber-ink)]">
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
            className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] px-3 py-1.5 text-xs text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-rose-ink)]"
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
        TheirStack runs on the server, so there is no key to enter here. It
        uses paid credits per result, which is why it stays off until you turn
        it on.
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

/**
 * Custom sources are the one feature here that fetches something job.os knows
 * nothing about, so the acceptance below is a gate rather than a notice: until
 * it is recorded there is no form to fill in, and revoking it switches every
 * source off again.
 */
function CustomSourcesSection() {
  const [acceptedAt, setAcceptedAt] = useState<string | null>(null);
  const [sources, setSources] = useState<CustomSource[]>([]);
  const [agreed, setAgreed] = useState(false);

  // Seeded in an effect for the same reason ProviderCard does it: localStorage
  // does not exist during the server render.
  useEffect(() => {
    setAcceptedAt(getAcceptedAt());
    setSources(loadCustomSources());
  }, []);

  function onAccept() {
    acceptTerms();
    setAcceptedAt(getAcceptedAt());
  }

  function onRevoke() {
    revokeTerms();
    setAcceptedAt(null);
    setAgreed(false);
    setSources(loadCustomSources());
    toast.success("Custom sources revoked", {
      description: "Every custom source was switched off.",
    });
  }

  return (
    <section id="custom" className="workspace-panel mt-5 scroll-mt-24 p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold tracking-tight">
            Custom sources
          </h2>
          <p className="mt-1 text-xs text-[color:var(--color-text-muted)]">
            Connect a job feed you host yourself. job.os fetches the endpoint and
            shows what it returns.
          </p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1 text-[11px] font-medium text-[color:var(--color-text-muted)]">
          <Plug className="size-3" /> Your endpoint
        </span>
      </div>

      {acceptedAt === null ? (
        <div className="notice notice-caution mt-5 p-5">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <ShieldAlert className="size-4" /> You are responsible for custom
            sources
          </h3>
          <p className="mt-2 text-xs leading-relaxed text-[color:var(--color-text-muted)]">
            Custom sources connect a job feed that you build or control. job.os
            only fetches the endpoint you provide and shows what it returns; it
            never contacts the underlying job sites itself. Before you can add
            one, please read and accept these terms.
          </p>
          <ul className="mt-3 list-disc space-y-2 pl-5 text-xs leading-relaxed text-[color:var(--color-text-muted)]">
            <li>
              You are solely responsible for the sources you connect, for any
              scraping or data collection they perform, and for complying with
              the terms of service, robots rules, rate limits, intellectual
              property rights, and applicable laws of every website or service
              involved.
            </li>
            <li>
              Your endpoint, and any scraping behind it, runs on infrastructure
              you operate. job.os does not host, run, endorse, or verify custom
              sources or their data.
            </li>
            <li>
              Custom sources are provided as is, with no warranty of any kind.
              To the maximum extent permitted by law, job.os and its author
              disclaim all liability for any loss, damage, or claim arising from
              your use of custom sources.
            </li>
            <li>
              You agree to indemnify and hold harmless job.os and its author
              from any third-party claim, demand, or proceeding arising out of
              the sources you connect or the data they return.
            </li>
            <li>
              This is not legal advice. If you are unsure whether a source is
              permitted, do not connect it.
            </li>
          </ul>
          <label className="mt-4 flex items-start gap-2 text-xs text-[color:var(--color-text)]">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(e) => setAgreed(e.target.checked)}
              className="mt-0.5 size-3.5 accent-[color:var(--color-violet)]"
            />
            I have read and agree to these terms.
          </label>
          <button
            onClick={onAccept}
            disabled={!agreed}
            className="kinetic-button kinetic-button-primary mt-4 disabled:opacity-50"
          >
            <CheckCircle2 className="size-3.5" /> Accept and continue
          </button>
        </div>
      ) : (
        <>
          <div className="mt-5 flex flex-wrap items-center gap-3 rounded-[var(--radius-card)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-4 py-3">
            <span className="inline-flex items-center gap-1.5 text-xs text-[color:var(--color-mint)]">
              <CheckCircle2 className="size-3.5" />
              Accepted on {new Date(acceptedAt).toLocaleString()}
            </span>
            <button
              type="button"
              onClick={onRevoke}
              className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] px-3 py-1.5 text-xs text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-rose-ink)]"
            >
              <Trash2 className="size-3.5" /> Revoke
            </button>
          </div>

          <CustomSourceManager sources={sources} onChange={setSources} />
        </>
      )}

      <EndpointContract />
    </section>
  );
}

function CustomSourceManager({
  sources,
  onChange,
}: {
  sources: CustomSource[];
  onChange: (next: CustomSource[]) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [authHeader, setAuthHeader] = useState("");
  const [authValue, setAuthValue] = useState("");
  const [revealed, setRevealed] = useState(false);

  function resetForm() {
    setEditingId(null);
    setName("");
    setUrl("");
    setAuthHeader("");
    setAuthValue("");
    setRevealed(false);
  }

  function onEdit(source: CustomSource) {
    setEditingId(source.id);
    setName(source.name);
    setUrl(source.url);
    setAuthHeader(source.authHeader ?? "");
    setAuthValue(source.authValue ?? "");
    setRevealed(false);
  }

  function onSave() {
    if (!name.trim()) {
      toast.error("Give the source a name");
      return;
    }
    if (!isValidCustomUrl(url)) {
      toast.error("Enter a public https endpoint URL");
      return;
    }
    onChange(
      upsertCustomSource({
        id: editingId ?? undefined,
        name,
        url,
        authHeader,
        authValue,
      }),
    );
    toast.success(editingId ? "Custom source updated" : "Custom source added", {
      description: "Switch it on in the Job finder sources list.",
    });
    resetForm();
  }

  function onRemove(source: CustomSource) {
    onChange(removeCustomSource(source.id));
    if (editingId === source.id) resetForm();
    toast.success(`${source.name} removed`);
  }

  return (
    <>
      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label
            htmlFor="custom-name"
            className="text-xs font-medium text-[color:var(--color-text)]"
          >
            Name
          </label>
          <input
            id="custom-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My scraper"
            autoComplete="off"
            className="field-control mt-1.5 text-xs"
          />
        </div>
        <div className="sm:col-span-2">
          <label
            htmlFor="custom-url"
            className="text-xs font-medium text-[color:var(--color-text)]"
          >
            Endpoint URL
          </label>
          <input
            id="custom-url"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://my-scraper.vercel.app/api/jobs"
            autoComplete="off"
            spellCheck={false}
            className="field-control mt-1.5 font-mono text-xs"
          />
          <p className="mt-1 text-[11px] text-[color:var(--color-text-dim)]">
            Must be a public https URL. job.os POSTs the search filters to it on
            every search.
          </p>
        </div>
        <div>
          <label
            htmlFor="custom-auth-header"
            className="text-xs font-medium text-[color:var(--color-text)]"
          >
            Auth header name (optional)
          </label>
          <input
            id="custom-auth-header"
            type="text"
            value={authHeader}
            onChange={(e) => setAuthHeader(e.target.value)}
            placeholder="x-custom-source-key"
            autoComplete="off"
            spellCheck={false}
            className="field-control mt-1.5 font-mono text-xs"
          />
        </div>
        <div>
          <label
            htmlFor="custom-auth-value"
            className="text-xs font-medium text-[color:var(--color-text)]"
          >
            Auth header value (optional)
          </label>
          <input
            id="custom-auth-value"
            type={revealed ? "text" : "password"}
            value={authValue}
            onChange={(e) => setAuthValue(e.target.value)}
            placeholder="your shared secret"
            autoComplete="off"
            spellCheck={false}
            className="field-control mt-1.5 font-mono text-xs"
          />
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button onClick={onSave} className="kinetic-button kinetic-button-primary">
          <Save className="size-3.5" /> {editingId ? "Update source" : "Add source"}
        </button>
        <button
          type="button"
          onClick={() => setRevealed((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
        >
          {revealed ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
          {revealed ? "Hide" : "Show"}
        </button>
        {editingId && (
          <button
            type="button"
            onClick={resetForm}
            className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] px-3 py-1.5 text-xs text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
          >
            Cancel edit
          </button>
        )}
      </div>

      {sources.length > 0 && (
        <ul className="mt-5 space-y-2">
          {sources.map((source) => (
            <li
              key={source.id}
              className="flex flex-wrap items-center gap-3 rounded-[var(--radius-card)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{source.name}</div>
                <div className="truncate font-mono text-[11px] text-[color:var(--color-text-dim)]">
                  {hostnameOf(source.url)}
                </div>
              </div>
              <button
                type="button"
                onClick={() => onChange(setCustomEnabled(source.id, !source.enabled))}
                aria-pressed={source.enabled}
                className={
                  "rounded-full border px-3 py-1 text-[11px] transition " +
                  (source.enabled
                    ? "border-[color:var(--color-mint)]/40 bg-[color:var(--color-mint)]/10 text-[color:var(--color-mint-ink)]"
                    : "border-[color:var(--color-border)] text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text)]")
                }
              >
                {source.enabled ? "Enabled" : "Disabled"}
              </button>
              <button
                type="button"
                onClick={() => onEdit(source)}
                className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] px-3 py-1 text-[11px] text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
              >
                <Pencil className="size-3" /> Edit
              </button>
              <button
                type="button"
                onClick={() => onRemove(source)}
                className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] px-3 py-1 text-[11px] text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-rose-ink)]"
              >
                <Trash2 className="size-3" /> Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/** The contract an endpoint has to satisfy, kept next to the form that needs it. */
function EndpointContract() {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-5 border-t border-[color:var(--color-border)] pt-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-[color:var(--color-text-muted)] transition hover:text-[color:var(--color-text)]"
      >
        {open ? (
          <ChevronDown className="size-3.5" />
        ) : (
          <ChevronRight className="size-3.5" />
        )}
        Endpoint contract
      </button>

      {open && (
        <div className="mt-3 space-y-3 text-xs leading-relaxed text-[color:var(--color-text-muted)]">
          <p>
            job.os sends a POST to your URL with a JSON body. If you set an auth
            header above, it is sent with the request. Nothing else is added.
          </p>
          <pre className="overflow-x-auto rounded-[var(--radius-card)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-3 font-mono text-[11px] leading-relaxed">
{`{
  "title_keywords": ["software engineer"],
  "location": "Boston" | null,
  "country_codes": ["US"],
  "max_age_days": 30 | null,
  "limit": 20
}`}
          </pre>
          <p>
            Answer with HTTP 200 and either a bare array or an object with a
            results array. Only title and url are required; every other field
            may be null or absent.
          </p>
          <pre className="overflow-x-auto rounded-[var(--radius-card)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-3 font-mono text-[11px] leading-relaxed">
{`{
  "results": [
    {
      "title": "Software Engineer",
      "url": "https://example.com/jobs/123",
      "company": "Example Inc" | null,
      "company_domain": "example.com" | null,
      "location": "Boston, MA" | null,
      "country_code": "US" | null,
      "posted_at": "2026-07-01T00:00:00Z" | null,
      "description": "..." | null,
      "id": "123" | null
    }
  ]
}`}
          </pre>
          <p>
            job.os re-applies the search filters to whatever comes back, so a
            source may return a whole board and let job.os narrow it. Responses
            larger than about 2 MB, or slower than 15 seconds, are dropped.
          </p>
          <p>
            Want a ready-made scraper? Use the Zyte starter template in
            examples/custom-scraper-zyte of the repo.
          </p>
        </div>
      )}
    </div>
  );
}

function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}
