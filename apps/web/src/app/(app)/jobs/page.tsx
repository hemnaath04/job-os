"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow, parseISO } from "date-fns";
import { motion } from "framer-motion";
import {
  Bookmark,
  CheckCircle2,
  ExternalLink,
  Loader2,
  MapPin,
  Radar,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { CompanyAvatar } from "@/components/company-avatar";
import { api } from "@/lib/api";
import type {
  DiscoveryResult,
  DiscoverySearchRequest,
  DiscoverySource,
  SavedSearch,
} from "@/lib/types";

export default function DiscoverPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.getSettings(),
  });
  const { data: saved = [] } = useQuery({
    queryKey: ["saved-searches"],
    queryFn: () => api.listSavedSearches(),
  });

  const [titles, setTitles] = useState("");
  const [techs, setTechs] = useState("");
  const [country, setCountry] = useState("US");
  const [maxAgeDays, setMaxAgeDays] = useState(30);
  const [limit, setLimit] = useState(20);
  const [sources, setSources] = useState<DiscoverySource[]>([
    "theirstack",
    "github",
  ]);
  const [results, setResults] = useState<DiscoveryResult[] | null>(null);

  const search = useMutation({
    mutationFn: (body: DiscoverySearchRequest) => api.discoverySearch(body),
    onSuccess: (data) => {
      setResults(data);
      if (data.length === 0) toast("No results", { description: "Try widening the filters." });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  function currentQuery(): DiscoverySearchRequest {
    return {
      sources,
      title_keywords: splitCsv(titles),
      technology_slugs: splitCsv(techs),
      country_codes: country ? [country.toUpperCase()] : [],
      max_age_days: maxAgeDays,
      limit,
      page: 0,
    };
  }

  const saveSearch = useMutation({
    mutationFn: (name: string) =>
      api.createSavedSearch({ name, query: currentQuery() }),
    onSuccess: () => {
      toast.success("Search saved");
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const runSaved = useMutation({
    mutationFn: (id: string) => api.runSavedSearch(id),
    onSuccess: (data) => {
      setResults(data);
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
      if (data.length === 0)
        toast("No results", { description: "Saved query returned nothing today." });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const deleteSaved = useMutation({
    mutationFn: (id: string) => api.deleteSavedSearch(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved-searches"] }),
    onError: (err: Error) => toast.error(err.message),
  });

  function applySaved(s: SavedSearch) {
    setTitles((s.query.title_keywords ?? []).join(", "));
    setTechs((s.query.technology_slugs ?? []).join(", "));
    setCountry((s.query.country_codes ?? [])[0] ?? "");
    setMaxAgeDays(s.query.max_age_days ?? 30);
    setLimit(s.query.limit ?? 20);
    if (s.query.sources && s.query.sources.length > 0) setSources(s.query.sources);
    runSaved.mutate(s.id);
  }

  function onSaveClick() {
    const name = window.prompt("Name this search (e.g. 'SWE intern · Boston')");
    if (!name) return;
    saveSearch.mutate(name.trim());
  }

  function toggleSource(s: DiscoverySource) {
    setSources((prev) => {
      if (prev.includes(s)) {
        if (prev.length === 1) return prev; // keep at least one source on
        return prev.filter((x) => x !== s);
      }
      return [...prev, s];
    });
  }

  function runSearch() {
    search.mutate(currentQuery());
  }

  return (
    <div className="mx-auto max-w-5xl px-8 py-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-medium tracking-tight">Discover</h1>
          <p className="text-sm text-[color:var(--color-text-muted)]">
            TheirStack — searches across LinkedIn/Lever/Greenhouse/etc. Each
            result burns 1 credit on import, so the page size is capped at 50.
            {settings?.default_function && (
              <>
                {" "}Default function: <code>{settings.default_function}</code>.
              </>
            )}
          </p>
        </div>
        <Radar className="size-5 text-[color:var(--color-violet)]" />
      </header>

      {saved.length > 0 && (
        <div className="mt-6">
          <div className="text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
            Saved searches
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {saved.map((s) => (
              <div
                key={s.id}
                className="group glass inline-flex items-center gap-1 rounded-full pl-3 pr-1 py-1 text-xs"
              >
                <button
                  onClick={() => applySaved(s)}
                  className="inline-flex items-center gap-1 hover:text-white"
                  title={s.last_run_count !== null ? `${s.last_run_count} last run` : ""}
                >
                  <Bookmark className="size-3 text-[color:var(--color-violet)]" />
                  {s.name}
                </button>
                <button
                  onClick={() => deleteSaved.mutate(s.id)}
                  className="ml-0.5 rounded-full p-1 text-[color:var(--color-text-dim)] opacity-0 transition group-hover:opacity-100 hover:bg-white/[0.06] hover:text-rose-300"
                  aria-label={`Delete saved search ${s.name}`}
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="glass mt-6 grid grid-cols-1 gap-4 rounded-[var(--radius-card)] p-5 md:grid-cols-2">
        <div className="md:col-span-2">
          <label className="text-sm font-medium">Sources</label>
          <p className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">
            TheirStack costs 1 credit per imported job; GitHub is free and
            re-fetched live from the SimplifyJobs READMEs on every search (they
            update daily).
          </p>
          <div className="mt-2 flex gap-2">
            <SourceToggle
              active={sources.includes("theirstack")}
              onClick={() => toggleSource("theirstack")}
              label="TheirStack"
              hint="LinkedIn / Lever / Greenhouse / Ashby"
            />
            <SourceToggle
              active={sources.includes("github")}
              onClick={() => toggleSource("github")}
              label="GitHub"
              hint="SimplifyJobs internships + new-grad"
            />
          </div>
        </div>
        <Field label="Title keywords" help="Comma-separated. e.g. 'software engineer, ml engineer'">
          <input
            type="text"
            value={titles}
            onChange={(e) => setTitles(e.target.value)}
            placeholder="software engineer intern"
            className="glass w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
          />
        </Field>
        <Field label="Technologies" help="Comma-separated slugs. e.g. 'python, pytorch'">
          <input
            type="text"
            value={techs}
            onChange={(e) => setTechs(e.target.value)}
            placeholder="python, fastapi"
            className="glass w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
          />
        </Field>
        <Field label="Country code" help="ISO-3166 alpha-2. e.g. US, CA, GB. Blank = global.">
          <input
            type="text"
            value={country}
            maxLength={2}
            onChange={(e) => setCountry(e.target.value.toUpperCase())}
            className="glass w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm uppercase outline-none focus:border-[#CCFF00]/60"
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Max age (days)">
            <input
              type="number"
              min={1}
              max={180}
              value={maxAgeDays}
              onChange={(e) => setMaxAgeDays(Number(e.target.value) || 30)}
              className="glass w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
            />
          </Field>
          <Field label="Limit">
            <input
              type="number"
              min={1}
              max={50}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value) || 20)}
              className="glass w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
            />
          </Field>
        </div>
        <div className="md:col-span-2 flex items-center gap-2">
          <button
            onClick={runSearch}
            disabled={search.isPending}
            className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-4 py-1.5 text-sm font-medium text-black shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
          >
            {search.isPending ? (
              <>
                <Loader2 className="size-3.5 animate-spin" /> Searching…
              </>
            ) : (
              <>
                <Search className="size-3.5" /> Search
              </>
            )}
          </button>
          <button
            onClick={onSaveClick}
            disabled={saveSearch.isPending}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm hover:bg-white/[0.06] disabled:opacity-50"
            title="Save current filters as a named search"
          >
            <Bookmark className="size-3.5" /> Save search
          </button>
        </div>
      </div>

      {results !== null && (
        <div className="mt-6 space-y-3">
          {results.length === 0 ? (
            <div className="text-sm text-[color:var(--color-text-muted)]">No results.</div>
          ) : (
            results.map((r) => (
              <ResultCard
                key={r.source_id || r.source_url}
                result={r}
                onImported={() => {
                  // Mark this card as imported so the button flips, without
                  // forcing a full search refetch (which would burn credits).
                  setResults((prev) =>
                    prev?.map((x) =>
                      x.source_id === r.source_id ? { ...x, already_imported: true } : x,
                    ) ?? null,
                  );
                  qc.invalidateQueries({ queryKey: ["jobs"] });
                }}
                onTailored={(jobId) => router.push(`/tailor?job_id=${jobId}`)}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

function ResultCard({
  result,
  onImported,
  onTailored,
}: {
  result: DiscoveryResult;
  onImported: () => void;
  onTailored: (jobId: string) => void;
}) {
  const importPayload = () => ({
    source: result.source,
    source_id: result.source_id,
    source_url: result.source_url,
    title: result.title,
    description: result.description,
    company_name: result.company_name,
    company_domain: result.company_domain,
    location: result.location,
    posted_at: result.posted_at,
  });

  const importJob = useMutation({
    mutationFn: () => api.discoveryImport(importPayload()),
    onSuccess: () => {
      toast.success("Imported to your jobs");
      onImported();
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const tailorJob = useMutation({
    mutationFn: async () => {
      // 1. Import (idempotent — backend dedupes on source+source_id).
      const job = await api.discoveryImport(importPayload());
      // 2. Create an application so the job lands in /applications too.
      //    Swallow 409 (already exists) so re-clicks stay smooth.
      try {
        await api.createApplication({ job_id: job.id, status: "ready_to_apply" });
      } catch (e) {
        const msg = (e as Error).message;
        if (!msg.includes("409")) throw e;
      }
      return job;
    },
    onSuccess: (job) => {
      onImported();
      onTailored(job.id);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="glass hover-lift rounded-[var(--radius-card-lg)] p-4"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <CompanyAvatar name={result.company_name ?? "?"} size={36} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="truncate text-base font-medium">{result.title}</h3>
              {result.source_url && (
                <a
                  href={result.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[color:var(--color-text-muted)] hover:text-white"
                >
                  <ExternalLink className="size-3.5" />
                </a>
              )}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[color:var(--color-text-muted)]">
              {result.company_name && (
                <span className="inline-flex items-center gap-1">
                  {result.company_name}
                </span>
              )}
            {result.location && (
              <span className="inline-flex items-center gap-1">
                <MapPin className="size-3" /> {result.location}
              </span>
            )}
            {result.posted_at && (
              <span>
                posted {formatDistanceToNow(parseISO(result.posted_at), { addSuffix: true })}
              </span>
            )}
            <span className="rounded-full bg-white/[0.04] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[color:var(--color-text-dim)]">
              {result.source_label || result.source}
            </span>
          </div>
            {result.technologies.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {result.technologies.slice(0, 8).map((t) => (
                  <span
                    key={t}
                    className="rounded-full bg-white/[0.04] px-2 py-0.5 text-[10px] text-[color:var(--color-text-muted)]"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          {result.already_imported ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--color-mint)]/10 px-3 py-1.5 text-xs text-[color:var(--color-mint)]">
              <CheckCircle2 className="size-3" /> Imported
            </span>
          ) : (
            <button
              onClick={() => importJob.mutate()}
              disabled={importJob.isPending || tailorJob.isPending}
              className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs hover:bg-white/[0.06] disabled:opacity-50"
            >
              {importJob.isPending ? (
                <Loader2 className="size-3 animate-spin" />
              ) : null}
              Import
            </button>
          )}
          <button
            onClick={() => tailorJob.mutate()}
            disabled={tailorJob.isPending || importJob.isPending}
            className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-3 py-1.5 text-xs font-medium text-black shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
            title="Import + create application + open the tailoring agent"
          >
            {tailorJob.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Sparkles className="size-3" />
            )}
            Tailor →
          </button>
        </div>
      </div>
      <p className="mt-3 line-clamp-3 text-xs text-[color:var(--color-text-muted)]">
        {result.description.slice(0, 400)}
        {result.description.length > 400 ? "…" : ""}
      </p>
    </motion.div>
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

function splitCsv(s: string): string[] {
  return s
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

function SourceToggle({
  active,
  onClick,
  label,
  hint,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  hint: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex flex-col items-start rounded-[var(--radius-card)] border px-3 py-2 text-left text-xs transition ${
        active
          ? "border-[color:var(--color-purple)]/60 bg-[color:var(--color-purple)]/15 text-white shadow-[0_0_24px_-8px_var(--color-purple)]"
          : "border-white/10 bg-white/[0.02] text-[color:var(--color-text-muted)] hover:bg-white/[0.04]"
      }`}
    >
      <span className="text-sm font-medium">{label}</span>
      <span className="text-[10px] text-[color:var(--color-text-dim)]">{hint}</span>
    </button>
  );
}
