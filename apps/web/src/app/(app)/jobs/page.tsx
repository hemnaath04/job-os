"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow, parseISO } from "date-fns";
import { motion } from "framer-motion";
import {
  Bookmark,
  CheckCircle2,
  ExternalLink,
  KeyRound,
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
import { InfoChip, PageIntro } from "@/components/page-intro";
import { api } from "@/lib/api";
import {
  emptyDiscoveryResponse,
  FREE_SOURCES,
  KEYED_SOURCES,
  mergeDiscoveryResponses,
  NO_KEY_SOURCES,
  SOURCE_META,
  splitSources,
} from "@/lib/discover/sources";
import type {
  DiscoveryResult,
  DiscoverySearchRequest,
  DiscoverySearchResponse,
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

// Bumped to v2 when the free sources landed: v1 sessions persisted the old
// two-source default, which would have hidden the new boards from anyone who
// had ever run a search.
const STORAGE_KEY = "discover:state:v2";

// The FastAPI SavedSearch schema validates `sources` against a Literal of
// theirstack | github, so a saved query cannot carry the key-free selections.
// Keep them alongside, keyed by saved-search id, until the backend catches up.
const SAVED_NO_KEY_STORAGE_KEY = "discover:saved-no-key:v1";

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

function loadSavedNoKey(): Record<string, DiscoverySource[]> {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(SAVED_NO_KEY_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Record<string, DiscoverySource[]>) : {};
  } catch {
    return {};
  }
}

function saveSavedNoKey(id: string, sources: DiscoverySource[]) {
  if (typeof window === "undefined") return;
  try {
    const all = loadSavedNoKey();
    all[id] = sources;
    localStorage.setItem(SAVED_NO_KEY_STORAGE_KEY, JSON.stringify(all));
  } catch {
    /* non-critical: the saved search still runs its backend half */
  }
}

function forgetSavedNoKey(id: string) {
  if (typeof window === "undefined") return;
  try {
    const all = loadSavedNoKey();
    delete all[id];
    localStorage.setItem(SAVED_NO_KEY_STORAGE_KEY, JSON.stringify(all));
  } catch {
    /* non-critical */
  }
}

/**
 * Fan a query out to whichever backends the selected sources live on, then
 * merge. Both halves run in parallel and neither can sink the other: if one
 * rejects, its failure becomes an error row attributed to the sources it was
 * carrying, and the other half's results still render. Only a total failure
 * throws, which surfaces as the mutation's error toast.
 */
async function runSplitSearch(
  query: DiscoverySearchRequest,
): Promise<DiscoverySearchResponse> {
  const selected = query.sources ?? [];
  const { backend, noKey } = splitSources(selected);

  const [backendPart, noKeyPart] = await Promise.allSettled([
    backend.length
      ? api.discoverySearch({ ...query, sources: backend })
      : Promise.resolve(emptyDiscoveryResponse()),
    noKey.length
      ? api.discoverNoKey({
          sources: noKey,
          title_keywords: query.title_keywords ?? [],
          country_codes: query.country_codes ?? [],
          max_age_days: query.max_age_days,
          limit: query.limit,
        })
      : Promise.resolve(emptyDiscoveryResponse()),
  ]);

  if (backendPart.status === "rejected" && noKeyPart.status === "rejected") {
    throw backendPart.reason as Error;
  }

  // `limit` is what each backend was asked for, so the merged list is capped
  // to it as well. Otherwise picking 20 would quietly return up to 40.
  return combineParts(
    selected,
    backend,
    backendPart,
    noKey,
    noKeyPart,
    query.limit,
  );
}

function combineParts(
  selected: DiscoverySource[],
  backend: DiscoverySource[],
  backendPart: PromiseSettledResult<DiscoverySearchResponse>,
  noKey: DiscoverySource[],
  noKeyPart: PromiseSettledResult<DiscoverySearchResponse>,
  limit?: number,
): DiscoverySearchResponse {
  const parts: DiscoverySearchResponse[] = [];
  const failures: DiscoverySourceError[] = [];

  for (const [half, group] of [
    [backendPart, backend],
    [noKeyPart, noKey],
  ] as const) {
    if (half.status === "fulfilled") {
      parts.push(half.value);
      continue;
    }
    // Attribute the failure to every source that half was carrying, otherwise
    // the banner would report them as "returned 0 hits" and hide the reason.
    const message = (half.reason as Error)?.message ?? "request failed";
    for (const source of group) failures.push({ source, message });
  }

  const merged = mergeDiscoveryResponses(parts, selected, limit);
  merged.errors = [...merged.errors, ...failures];
  return merged;
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
  // Everything free is on by default. TheirStack stays off until the user
  // opts in, so a fresh install never burns credits on its first search.
  const [sources, setSources] = useState<DiscoverySource[]>(
    initial.sources ?? [...FREE_SOURCES],
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
    mutationFn: (body: DiscoverySearchRequest) => runSplitSearch(body),
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
      // The agent runs on FastAPI, which only knows theirstack and github, so
      // it can never name a key-free source. Take its opinion on the backend
      // half and leave the user's free-source selection alone.
      const nextSources = mergeSmartSources(filters.sources, sources);
      setSources(nextSources);
      if (explanation) toast.success(explanation);
      // Auto-run the search with the extracted filters.
      search.mutate({
        sources: nextSources,
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
    mutationFn: async (name: string) => {
      const query = currentQuery();
      const { backend, noKey } = splitSources(query.sources ?? []);
      // FastAPI rejects the key-free ids outright, so strip them from the
      // stored query and remember them locally against the new id.
      const saved = await api.createSavedSearch({
        name,
        query: { ...query, sources: backend },
      });
      saveSavedNoKey(saved.id, noKey);
      return saved;
    },
    onSuccess: () => {
      toast.success("Search saved");
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const runSaved = useMutation({
    mutationFn: async (s: SavedSearch) => {
      const noKey = loadSavedNoKey()[s.id] ?? [];
      const backend = s.query.sources ?? [];
      // runSavedSearch is what updates last_run_at / last_run_count upstream,
      // so keep using it for the backend half rather than replaying the query.
      const [backendPart, noKeyPart] = await Promise.allSettled([
        backend.length
          ? api.runSavedSearch(s.id)
          : Promise.resolve(emptyDiscoveryResponse()),
        noKey.length
          ? api.discoverNoKey({
              sources: noKey,
              title_keywords: s.query.title_keywords ?? [],
              country_codes: s.query.country_codes ?? [],
              max_age_days: s.query.max_age_days,
              limit: s.query.limit,
            })
          : Promise.resolve(emptyDiscoveryResponse()),
      ]);
      if (backendPart.status === "rejected" && noKeyPart.status === "rejected") {
        throw backendPart.reason as Error;
      }
      return combineParts(
        [...backend, ...noKey],
        backend,
        backendPart,
        noKey,
        noKeyPart,
        s.query.limit,
      );
    },
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
    onSuccess: (_data, id) => {
      forgetSavedNoKey(id);
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  function applySaved(s: SavedSearch) {
    setTitles((s.query.title_keywords ?? []).join(", "));
    setTechs((s.query.technology_slugs ?? []).join(", "));
    setCountry((s.query.country_codes ?? [])[0] ?? "");
    setMaxAgeDays(s.query.max_age_days ?? 30);
    setLimit(s.query.limit ?? 20);
    const restored = [...(s.query.sources ?? []), ...(loadSavedNoKey()[s.id] ?? [])];
    if (restored.length > 0) setSources(restored);
    runSaved.mutate(s);
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
    <div className="workspace-page max-w-7xl">
      <PageIntro
        eyebrow="Opportunity radar"
        title="Job finder"
        description="Translate plain-English intent into focused job-board searches, then bring the strongest roles into your private pipeline."
        icon={Radar}
      >
        <InfoChip tone="sage">{FREE_SOURCES.length} free sources</InfoChip>
        <InfoChip>{saved.length} saved searches</InfoChip>
        <InfoChip tone="clay">50-result guardrail</InfoChip>
      </PageIntro>

      {/* Smart search */}
      <form onSubmit={onSmartSubmit} className="workspace-panel mt-6 p-5 sm:p-6">
        <label className="flex items-center gap-1.5 text-sm font-medium">
          <Wand2 className="size-3.5 text-[color:var(--color-violet)]" /> Smart search
        </label>
        <p className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">
          Type a sentence. The fast agent extracts the filters and runs the search.
        </p>
        <div className="mt-2 flex gap-2">
          <input
            type="text"
            value={smartQuery}
            onChange={(e) => setSmartQuery(e.target.value)}
            placeholder="e.g. 'fullstack intern in Boston with Python and React from the last 2 weeks'"
            className="field-control flex-1 rounded-full"
          />
          <button
            type="submit"
            disabled={smart.isPending || !smartQuery.trim()}
            className="kinetic-button kinetic-button-primary disabled:opacity-50"
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
                  className="inline-flex items-center gap-1 hover:text-[color:var(--color-text)]"
                  title={s.last_run_count !== null ? `${s.last_run_count} last run` : ""}
                >
                  <Bookmark className="size-3 text-[color:var(--color-violet)]" />
                  {s.name}
                </button>
                <button
                  onClick={() => deleteSaved.mutate(s.id)}
                  className="ml-0.5 rounded-full p-1 text-[color:var(--color-text-dim)] opacity-0 transition group-hover:opacity-100 hover:bg-[color:var(--color-surface-hover)] hover:text-rose-300"
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
      <div className="workspace-panel mt-5 grid grid-cols-1 gap-5 p-5 sm:p-6 md:grid-cols-2">
        <div className="md:col-span-2">
          <label className="text-sm font-medium">Sources</label>
          <p className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">
            Everything in the first group is fetched live on every search and
            costs nothing. Add a key only if you want the extra coverage.
          </p>

          <div className="mt-3">
            <div className="text-[11px] font-medium uppercase tracking-wider text-[color:var(--color-mint)]">
              Free sources, no key needed
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {FREE_SOURCES.map((s) => (
                <SourceToggle
                  key={s}
                  active={sources.includes(s)}
                  onClick={() => toggleSource(s)}
                  label={SOURCE_META[s].label}
                  hint={SOURCE_META[s].hint}
                />
              ))}
            </div>
          </div>

          <div className="mt-4">
            <div className="text-[11px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
              Add a key for more coverage
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {KEYED_SOURCES.map((s) => (
                <SourceToggle
                  key={s}
                  active={sources.includes(s)}
                  onClick={() => toggleSource(s)}
                  label={SOURCE_META[s].label}
                  hint={SOURCE_META[s].hint}
                  badge="needs key"
                  keySteps={SOURCE_META[s].keySteps}
                  keyUrl={SOURCE_META[s].keyUrl}
                />
              ))}
            </div>
          </div>
        </div>
        <Field label="Title keywords" help="Comma-separated. e.g. 'software engineer, ml engineer'">
          <input
            type="text"
            value={titles}
            onChange={(e) => setTitles(e.target.value)}
            placeholder="software engineer intern"
            className="field-control"
          />
        </Field>
        <Field
          label="Technologies"
          help="Comma-separated slugs. TheirStack only; the free sources do not expose a tech filter."
        >
          <input
            type="text"
            value={techs}
            onChange={(e) => setTechs(e.target.value)}
            placeholder="python, fastapi"
            className="field-control"
          />
        </Field>
        <Field label="Country code" help="ISO-3166 alpha-2. e.g. US, CA, GB. Blank = global.">
          <input
            type="text"
            value={country}
            maxLength={2}
            onChange={(e) => setCountry(e.target.value.toUpperCase())}
            className="field-control uppercase"
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
              className="field-control"
            />
          </Field>
          <Field label="Limit">
            <input
              type="number"
              min={1}
              max={50}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value) || 20)}
              className="field-control"
            />
          </Field>
        </div>
        <div className="md:col-span-2 flex flex-wrap items-center gap-2">
          <button
            onClick={runSearch}
            disabled={search.isPending}
            className="kinetic-button kinetic-button-primary disabled:opacity-50"
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
            className="kinetic-button kinetic-button-secondary disabled:opacity-50"
          >
            <Bookmark className="size-3.5" /> Save search
          </button>
          {results !== null && (
            <button
              onClick={clearResults}
              className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-sm text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
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
              <div className="flex rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-0.5">
                {(Object.keys(SORT_LABEL) as SortMode[]).map((m) => {
                  const disabled = m === "location" && !userLocation;
                  return (
                    <button
                      key={m}
                      onClick={() => !disabled && setSort(m)}
                      className={
                        "rounded-full px-3 py-1 text-xs transition " +
                        (sort === m
                          ? "bg-gradient-brand font-semibold text-[color:var(--color-on-accent)]"
                          : "text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]") +
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
                  // source_id is only unique within a source, and the list is
                  // now merged across seven of them.
                  key={`${r.source}:${r.source_id || r.source_url}`}
                  result={r}
                  onImported={() => {
                    setResults((prev) =>
                      prev?.map((x) =>
                        x.source === r.source && x.source_id === r.source_id
                          ? { ...x, already_imported: true }
                          : x,
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
      className="workspace-panel workspace-panel-interactive flex h-full flex-col p-5"
    >
      <div className="flex items-start gap-3">
        <CompanyAvatar name={result.company_name ?? "?"} domain={result.company_domain} size={36} />
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
                className="shrink-0 text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
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
        <span className="rounded-full bg-[color:var(--color-surface-2)] px-1.5 py-0.5 uppercase tracking-wide">
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
              className="rounded-full bg-[color:var(--color-surface-2)] px-2 py-0.5 text-[10px] text-[color:var(--color-text-muted)]"
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
            className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1 text-[11px] hover:bg-[color:var(--color-surface-hover)] disabled:opacity-50"
          >
            {importJob.isPending ? <Loader2 className="size-3 animate-spin" /> : null}
            Import
          </button>
        )}
        <button
          onClick={() => tailorJob.mutate()}
          disabled={tailorJob.isPending || importJob.isPending}
          className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-3 py-1 text-[11px] font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.05] disabled:opacity-50"
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
  badge,
  keySteps,
  keyUrl,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  hint: string;
  badge?: string;
  keySteps?: string[];
  keyUrl?: string;
}) {
  const [showHelp, setShowHelp] = useState(false);

  return (
    <div className="relative">
      <button
        onClick={onClick}
        aria-pressed={active}
        className={`flex w-full flex-col items-start rounded-[var(--radius-card)] border px-3 py-2 text-left text-xs transition ${
          active
            ? "border-[color:var(--color-purple)]/60 bg-[color:var(--color-purple)]/15 text-[color:var(--color-text)] shadow-[0_10px_24px_-18px_rgba(233,198,74,.45)]"
            : "border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-2)]"
        }`}
      >
        <span className="flex items-center gap-1.5">
          <span className="text-sm font-medium">{label}</span>
          {badge && (
            <span className="rounded-full border border-amber-400/40 bg-amber-400/10 px-1.5 py-px text-[9px] uppercase tracking-wide text-amber-300">
              {badge}
            </span>
          )}
        </span>
        <span className="text-[10px] text-[color:var(--color-text-dim)]">{hint}</span>
      </button>

      {keySteps && keySteps.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setShowHelp((v) => !v)}
            aria-expanded={showHelp}
            className="mt-1 inline-flex items-center gap-1 text-[10px] text-[color:var(--color-text-dim)] underline decoration-dotted underline-offset-2 hover:text-[color:var(--color-text)]"
          >
            <KeyRound className="size-2.5" /> How to get a key
          </button>
          {showHelp && (
            <div className="glass absolute left-0 top-full z-20 mt-1 w-72 rounded-[var(--radius-card)] border border-[color:var(--color-border)] p-3 text-[11px] leading-relaxed text-[color:var(--color-text-muted)] shadow-lg">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="font-medium text-[color:var(--color-text)]">
                  Set up {label}
                </span>
                <button
                  type="button"
                  onClick={() => setShowHelp(false)}
                  aria-label="Close"
                  className="rounded-full p-0.5 hover:bg-[color:var(--color-surface-hover)]"
                >
                  <X className="size-3" />
                </button>
              </div>
              <ol className="list-decimal space-y-1 pl-4">
                {keySteps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
              {keyUrl && (
                <a
                  href={keyUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-flex items-center gap-1 text-[color:var(--color-violet)] hover:underline"
                >
                  Open {new URL(keyUrl).hostname}
                  <ExternalLink className="size-2.5" />
                </a>
              )}
            </div>
          )}
        </>
      )}
    </div>
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

/**
 * The smart-search agent only knows the FastAPI sources, so treat its answer
 * as authoritative for that half and preserve whatever key-free sources the
 * user already had switched on.
 */
function mergeSmartSources(
  fromAgent: DiscoverySource[] | undefined,
  current: DiscoverySource[],
): DiscoverySource[] {
  const { noKey } = splitSources(current);
  if (!fromAgent || fromAgent.length === 0) return current;
  const merged = [...splitSources(fromAgent).backend, ...noKey];
  return merged.length > 0 ? merged : current;
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
  if (NO_KEY_SOURCES.includes(source)) {
    if (lower.includes("timed out")) {
      return `${msg}. The board was slow to answer; try again.`;
    }
    if (lower.includes("404")) {
      return `${msg}. A board slug in the curated list has moved or closed.`;
    }
  }
  return msg.length > 200 ? msg.slice(0, 200) + "…" : msg;
}
