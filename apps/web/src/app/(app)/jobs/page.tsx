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
  Wand2,
  X,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { CompanyAvatar } from "@/components/company-avatar";
import { api } from "@/lib/api";
import type {
  DiscoveryResult,
  DiscoverySearchRequest,
  DiscoverySource,
  DiscoverySourceError,
  SavedSearch,
} from "@/lib/types";

type SortMode = "recency" | "relevance" | "location";
const SORT_LABEL: Record<SortMode, string> = {
  recency: "Recency",
  relevance: "Relevance",
  location: "My location",
};

const STORAGE_KEY = "discover:state:v1";

type PersistedState = {
  titles: string;
  techs: string;
  country: string;
  maxAgeDays: number;
  limit: number;
  sources: DiscoverySource[];
  results: DiscoveryResult[] | null;
  sort: SortMode;
};

function loadState(): Partial<PersistedState> {
  if (typeof window === "undefined") return {};
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as PersistedState) : {};
  } catch {
    return {};
  }
}

function saveState(s: PersistedState) {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* quota — fine, treat as ephemeral */
  }
}

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

  // Hydrate from sessionStorage so the result list survives nav + reload.
  const initial = useMemo(loadState, []);
  const [titles, setTitles] = useState<string>(initial.titles ?? "");
  const [techs, setTechs] = useState<string>(initial.techs ?? "");
  const [country, setCountry] = useState<string>(initial.country ?? "US");
  const [maxAgeDays, setMaxAgeDays] = useState<number>(initial.maxAgeDays ?? 30);
  const [limit, setLimit] = useState<number>(initial.limit ?? 20);
  const [sources, setSources] = useState<DiscoverySource[]>(
    initial.sources ?? ["theirstack", "github"],
  );
  const [results, setResults] = useState<DiscoveryResult[] | null>(initial.results ?? null);
  const [sourceCounts, setSourceCounts] = useState<Record<string, number>>({});
  const [sourceErrors, setSourceErrors] = useState<DiscoverySourceError[]>([]);
  const [sort, setSort] = useState<SortMode>(initial.sort ?? "recency");

  const [smartQuery, setSmartQuery] = useState<string>("");

  // Persist on any change so reload restores state.
  useEffect(() => {
    saveState({ titles, techs, country, maxAgeDays, limit, sources, results, sort });
  }, [titles, techs, country, maxAgeDays, limit, sources, results, sort]);

  const search = useMutation({
    mutationFn: (body: DiscoverySearchRequest) => api.discoverySearch(body),
    onSuccess: (data) => {
      setResults(data.results);
      setSourceCounts(data.source_counts ?? {});
      setSourceErrors(data.errors ?? []);
      if (data.results.length === 0)
        toast("No results", { description: "Try widening the filters." });
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

  const smart = useMutation({
    mutationFn: (q: string) => api.smartSearch(q),
    onSuccess: ({ filters, explanation }) => {
      // Hydrate the form fields so the user can see what was extracted.
      setTitles((filters.title_keywords ?? []).join(", "));
      setTechs((filters.technology_slugs ?? []).join(", "));
      setCountry((filters.country_codes ?? [])[0] ?? "");
      if (filters.max_age_days) setMaxAgeDays(filters.max_age_days);
      if (filters.limit) setLimit(filters.limit);
      if (filters.sources && filters.sources.length > 0) setSources(filters.sources);
      if (explanation) toast.success(explanation);
      // Auto-run the search with the extracted filters.
      search.mutate({
        sources: filters.sources ?? sources,
        title_keywords: filters.title_keywords ?? [],
        technology_slugs: filters.technology_slugs ?? [],
        country_codes: filters.country_codes ?? [],
        max_age_days: filters.max_age_days ?? 30,
        limit: filters.limit ?? 20,
        page: 0,
      });
    },
    onError: (err: Error) => toast.error(err.message),
  });

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
      setResults(data.results);
      setSourceCounts(data.source_counts ?? {});
      setSourceErrors(data.errors ?? []);
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
      if (data.results.length === 0)
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
        if (prev.length === 1) return prev;
        return prev.filter((x) => x !== s);
      }
      return [...prev, s];
    });
  }

  function runSearch() {
    search.mutate(currentQuery());
  }

  function clearResults() {
    setResults(null);
    setSourceCounts({});
    setSourceErrors([]);
  }

  function onSmartSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!smartQuery.trim()) return;
    smart.mutate(smartQuery.trim());
  }

  // Sort cached results client-side. Recompute on sort change.
  const titleKeywords = splitCsv(titles);
  const userLocation = (settings?.default_location ?? "").toLowerCase().trim();
  const sortedResults = useMemo(() => {
    if (!results) return null;
    const copy = [...results];
    if (sort === "recency") {
      copy.sort((a, b) => tsOrZero(b.posted_at) - tsOrZero(a.posted_at));
    } else if (sort === "relevance") {
      copy.sort(
        (a, b) => relevanceScore(b, titleKeywords) - relevanceScore(a, titleKeywords),
      );
    } else if (sort === "location") {
      copy.sort((a, b) => {
        const aHit = userLocation && (a.location ?? "").toLowerCase().includes(userLocation);
        const bHit = userLocation && (b.location ?? "").toLowerCase().includes(userLocation);
        if (aHit && !bHit) return -1;
        if (!aHit && bHit) return 1;
        return tsOrZero(b.posted_at) - tsOrZero(a.posted_at);
      });
    }
    return copy;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results, sort, titles, userLocation]);

  return (
    <div className="mx-auto max-w-6xl px-8 py-6">
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

      {/* Smart search */}
      <form onSubmit={onSmartSubmit} className="mt-6">
        <label className="flex items-center gap-1.5 text-sm font-medium">
          <Wand2 className="size-3.5 text-[color:var(--color-violet)]" /> Smart search
        </label>
        <p className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">
          Type a sentence — Claude extracts the filters and runs the search.
        </p>
        <div className="mt-2 flex gap-2">
          <input
            type="text"
            value={smartQuery}
            onChange={(e) => setSmartQuery(e.target.value)}
            placeholder="e.g. 'fullstack intern in Boston with Python and React from the last 2 weeks'"
            className="glass flex-1 rounded-full border border-white/10 bg-white/[0.03] px-4 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
          />
          <button
            type="submit"
            disabled={smart.isPending || !smartQuery.trim()}
            className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-4 py-2 text-sm font-semibold text-black shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
          >
            {smart.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Sparkles className="size-3.5" />
            )}
            Ask
          </button>
        </div>
      </form>

      {/* Saved searches */}
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

      {/* Manual filters form */}
      <div className="glass mt-6 grid grid-cols-1 gap-4 rounded-[var(--radius-card)] p-5 md:grid-cols-2">
        <div className="md:col-span-2">
          <label className="text-sm font-medium">Sources</label>
          <p className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">
            TheirStack costs 1 credit per imported job; GitHub is free and
            re-fetched live from the SimplifyJobs READMEs on every search.
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
        <div className="md:col-span-2 flex flex-wrap items-center gap-2">
          <button
            onClick={runSearch}
            disabled={search.isPending}
            className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-4 py-1.5 text-sm font-semibold text-black shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
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
          >
            <Bookmark className="size-3.5" /> Save search
          </button>
          {results !== null && (
            <button
              onClick={clearResults}
              className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-[color:var(--color-text-muted)] hover:bg-white/[0.06] hover:text-white"
            >
              <X className="size-3.5" /> Clear results
            </button>
          )}
        </div>
      </div>

      {/* Per-source warnings — surfaced any time a source returned 0 hits or
          threw (e.g. THEIRSTACK_API_KEY missing in Render). Prevents the
          "only SimplifyJobs is showing" mystery state where one source silently
          fails. */}
      {sortedResults !== null && (sourceErrors.length > 0 || hasEmptySource(sources, sourceCounts)) && (
        <div className="glass mt-4 rounded-[var(--radius-card)] border border-amber-400/30 p-3 text-xs">
          {sourceErrors.map((e) => (
            <div key={e.source} className="flex items-start gap-2">
              <span className="text-amber-300">⚠</span>
              <div>
                <span className="font-semibold uppercase tracking-wider text-amber-300">
                  {e.source}
                </span>{" "}
                <span className="text-[color:var(--color-text-muted)]">
                  {prettyError(e.source, e.message)}
                </span>
              </div>
            </div>
          ))}
          {hasEmptySource(sources, sourceCounts) &&
            sources
              .filter((s) => (sourceCounts[s] ?? 0) === 0 && !sourceErrors.find((e) => e.source === s))
              .map((s) => (
                <div key={s} className="flex items-start gap-2">
                  <span className="text-amber-300">·</span>
                  <div>
                    <span className="font-semibold uppercase tracking-wider text-amber-300">
                      {s}
                    </span>{" "}
                    <span className="text-[color:var(--color-text-muted)]">
                      returned 0 hits with these filters.
                    </span>
                  </div>
                </div>
              ))}
        </div>
      )}

      {/* Results */}
      {sortedResults !== null && (
        <div className="mt-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-[color:var(--color-text-muted)]">
              {sortedResults.length} result{sortedResults.length === 1 ? "" : "s"}
              {Object.keys(sourceCounts).length > 0 && (
                <span className="ml-2 text-xs text-[color:var(--color-text-dim)]">
                  ({Object.entries(sourceCounts)
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(" · ")})
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-[color:var(--color-text-dim)]">Sort by</span>
              <div className="flex rounded-full border border-white/10 bg-white/[0.03] p-0.5">
                {(Object.keys(SORT_LABEL) as SortMode[]).map((m) => {
                  const disabled = m === "location" && !userLocation;
                  return (
                    <button
                      key={m}
                      onClick={() => !disabled && setSort(m)}
                      className={
                        "rounded-full px-3 py-1 text-xs transition " +
                        (sort === m
                          ? "bg-gradient-brand font-semibold text-black"
                          : "text-[color:var(--color-text-muted)] hover:text-white") +
                        (disabled ? " cursor-not-allowed opacity-40" : "")
                      }
                      title={
                        disabled
                          ? "Set 'default location' in Settings to use this sort"
                          : undefined
                      }
                    >
                      {SORT_LABEL[m]}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {sortedResults.length === 0 ? (
            <div className="mt-4 text-sm text-[color:var(--color-text-muted)]">
              No results.
            </div>
          ) : (
            <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {sortedResults.map((r) => (
                <ResultCard
                  key={r.source_id || r.source_url}
                  result={r}
                  onImported={() => {
                    setResults((prev) =>
                      prev?.map((x) =>
                        x.source_id === r.source_id ? { ...x, already_imported: true } : x,
                      ) ?? null,
                    );
                    qc.invalidateQueries({ queryKey: ["jobs"] });
                    qc.invalidateQueries({ queryKey: ["applications"] });
                  }}
                  onTailored={(jobId) => router.push(`/tailor?job_id=${jobId}`)}
                  onGoToApplications={() => router.push("/applications")}
                />
              ))}
            </div>
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
  onGoToApplications,
}: {
  result: DiscoveryResult;
  onImported: () => void;
  onTailored: (jobId: string) => void;
  onGoToApplications: () => void;
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
    mutationFn: async () => {
      const job = await api.discoveryImport(importPayload());
      // Also create an Application as wishlist so the user sees it in
      // /applications. Swallow 409s for repeat clicks.
      try {
        await api.createApplication({ job_id: job.id, status: "wishlist" });
      } catch (e) {
        const msg = (e as Error).message;
        if (!msg.includes("409")) throw e;
      }
      return job;
    },
    onSuccess: () => {
      toast.success("Added to Applications", {
        description: "Wishlist column · /applications",
        action: {
          label: "View",
          onClick: () => onGoToApplications(),
        },
      });
      onImported();
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const tailorJob = useMutation({
    mutationFn: async () => {
      const job = await api.discoveryImport(importPayload());
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
      className="glass hover-lift flex h-full flex-col rounded-[var(--radius-card-lg)] p-4"
    >
      <div className="flex items-start gap-3">
        <CompanyAvatar name={result.company_name ?? "?"} size={36} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <h3 className="line-clamp-2 text-sm font-medium leading-snug">
              {result.title}
            </h3>
            {result.source_url && (
              <a
                href={result.source_url}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 text-[color:var(--color-text-muted)] hover:text-white"
              >
                <ExternalLink className="size-3.5" />
              </a>
            )}
          </div>
          {result.company_name && (
            <div className="mt-0.5 truncate text-xs text-[color:var(--color-text-muted)]">
              {result.company_name}
            </div>
          )}
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-[color:var(--color-text-dim)]">
        {result.location && (
          <span className="inline-flex items-center gap-1">
            <MapPin className="size-3" /> {result.location}
          </span>
        )}
        {result.posted_at && (
          <span>
            {formatDistanceToNow(parseISO(result.posted_at), { addSuffix: true })}
          </span>
        )}
        <span className="rounded-full bg-white/[0.04] px-1.5 py-0.5 uppercase tracking-wide">
          {result.source_label || result.source}
        </span>
      </div>

      {result.description && (
        <p className="mt-2 line-clamp-3 text-xs text-[color:var(--color-text-muted)]">
          {result.description.slice(0, 240)}
          {result.description.length > 240 ? "…" : ""}
        </p>
      )}

      {result.technologies.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {result.technologies.slice(0, 6).map((t) => (
            <span
              key={t}
              className="rounded-full bg-white/[0.04] px-2 py-0.5 text-[10px] text-[color:var(--color-text-muted)]"
            >
              {t}
            </span>
          ))}
        </div>
      )}

      <div className="mt-auto flex items-center justify-between gap-2 pt-3">
        {result.already_imported ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--color-mint)]/10 px-3 py-1 text-[11px] text-[color:var(--color-mint)]">
            <CheckCircle2 className="size-3" /> In Applications
          </span>
        ) : (
          <button
            onClick={() => importJob.mutate()}
            disabled={importJob.isPending || tailorJob.isPending}
            className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-[11px] hover:bg-white/[0.06] disabled:opacity-50"
          >
            {importJob.isPending ? <Loader2 className="size-3 animate-spin" /> : null}
            Import
          </button>
        )}
        <button
          onClick={() => tailorJob.mutate()}
          disabled={tailorJob.isPending || importJob.isPending}
          className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-3 py-1 text-[11px] font-semibold text-black shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.05] disabled:opacity-50"
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

function splitCsv(s: string): string[] {
  return s
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

function tsOrZero(s: string | null | undefined): number {
  return s ? new Date(s).getTime() : 0;
}

function relevanceScore(r: DiscoveryResult, keywords: string[]): number {
  if (keywords.length === 0) return tsOrZero(r.posted_at);
  const haystack = `${r.title} ${r.description}`.toLowerCase();
  let score = 0;
  for (const k of keywords) {
    const needle = k.toLowerCase();
    if (!needle) continue;
    if (r.title.toLowerCase().includes(needle)) score += 3;
    if (haystack.includes(needle)) score += 1;
  }
  return score * 1e12 + tsOrZero(r.posted_at);
}

function hasEmptySource(
  sources: DiscoverySource[],
  counts: Record<string, number>,
): boolean {
  return sources.some((s) => (counts[s] ?? 0) === 0);
}

function prettyError(source: DiscoverySource, msg: string): string {
  const lower = msg.toLowerCase();
  if (source === "theirstack") {
    if (lower.includes("not configured") || lower.includes("api_key")) {
      return (
        "API key is not configured on the server. Add THEIRSTACK_API_KEY in " +
        "the Render dashboard for the job-os-api service."
      );
    }
    if (lower.includes("401") || lower.includes("unauthorized")) {
      return "Server rejected the TheirStack key (401). Rotate it in TheirStack and update Render.";
    }
    if (lower.includes("402") || lower.includes("credit")) {
      return "Out of TheirStack credits. Top up the account or fall back to GitHub.";
    }
  }
  return msg.length > 200 ? msg.slice(0, 200) + "…" : msg;
}
