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
  Lock,
  MapPin,
  Plug,
  Radar,
  Search,
  Sparkles,
  Wand2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { CompanyAvatar } from "@/components/company-avatar";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { Field } from "@/components/ui/field";
import { api } from "@/lib/api";
import {
  buildProfileVocab,
  scoreJob,
  type FitResult,
  type ProfileVocab,
} from "@/lib/discover/fit-score";
import { reportFailure } from "@/lib/errors";
import {
  CUSTOM_SOURCES_CHANGED_EVENT,
  hasAcceptedTerms,
  loadCustomSources,
  setCustomEnabled,
  type CustomSource,
} from "@/lib/discover/custom-sources";
import {
  hasKey,
  isByoSource,
  KEYS_CHANGED_EVENT,
  loadKeys,
  type DiscoveryKeys,
} from "@/lib/discover/keys";
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

type SortMode = "fit" | "recency" | "location";
const SORT_LABEL: Record<SortMode, string> = {
  fit: "Best fit",
  recency: "Recency",
  location: "My location",
};

// Bumped to v2 when the free sources landed: v1 sessions persisted the old
// two-source default, which would have hidden the new boards from anyone who
// had ever run a search.
const STORAGE_KEY = "discover:state:v2";

// The FastAPI SavedSearch schema validates `sources` against a Literal of
// theirstack | github, so a saved query cannot carry the selections that run
// through /api/discover: the key-free boards or the bring-your-own-key APIs.
// Keep them alongside, keyed by saved-search id, until the backend catches up.
// Only the source ids are stored here, never a key.
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
    // localStorage, not sessionStorage: results must survive closing and
    // reopening the browser, not just navigation and reload. They persist
    // until the user hits Clear results.
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as PersistedState) : {};
  } catch {
    return {};
  }
}

function saveState(s: PersistedState) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* quota: fine, treat as ephemeral */
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
 * The half of a selection that /api/discover serves: the key-free boards plus
 * whichever bring-your-own-key sources actually have a key pasted. A selected
 * source with no key is dropped rather than sent, so the route never has to
 * answer for a credential the user never gave it.
 */
function routeSources(
  selected: DiscoverySource[],
  keys: DiscoveryKeys,
): DiscoverySource[] {
  const { noKey, byoKey } = splitSources(selected);
  return [...noKey, ...byoKey.filter((s) => hasKey(s, keys))];
}

/**
 * The custom endpoints a search should hit. Read fresh rather than from state,
 * for the same reason the keys are, and gated on the acceptance: a revoked
 * acceptance must stop the fetching even if a stale toggle is still lit.
 *
 * Custom sources apply globally by their enabled flag rather than per saved
 * search, so this is the one answer for both search paths.
 */
function enabledCustomSources(): CustomSource[] {
  if (!hasAcceptedTerms()) return [];
  return loadCustomSources().filter((s) => s.enabled);
}

function toCustomPayload(sources: CustomSource[]) {
  return sources.map((s) => ({
    id: s.id,
    name: s.name,
    url: s.url,
    auth_header: s.authHeader,
    auth_value: s.authValue,
  }));
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
  keys: DiscoveryKeys,
  custom: CustomSource[],
): Promise<DiscoverySearchResponse> {
  const selected = query.sources ?? [];
  const { backend } = splitSources(selected);
  const route = routeSources(selected, keys);
  // Custom endpoints ride the same /api/discover call as the key-free and
  // bring-your-own-key sources, so they share that half's fate as well.
  const routeGroup: string[] = [...route, ...custom.map((s) => `custom:${s.id}`)];

  const [backendPart, routePart] = await Promise.allSettled([
    backend.length
      ? api.discoverySearch({ ...query, sources: backend })
      : Promise.resolve(emptyDiscoveryResponse()),
    routeGroup.length
      ? api.discoverNoKey({
          sources: route,
          title_keywords: query.title_keywords ?? [],
          country_codes: query.country_codes ?? [],
          max_age_days: query.max_age_days,
          limit: query.limit,
          keys,
          custom_sources: toCustomPayload(custom),
        })
      : Promise.resolve(emptyDiscoveryResponse()),
  ]);

  if (backendPart.status === "rejected" && routePart.status === "rejected") {
    throw backendPart.reason as Error;
  }

  // `limit` is what each backend was asked for, so the merged list is capped
  // to it as well. Otherwise picking 20 would quietly return up to 40.
  return combineParts(
    [...selected, ...routeGroup.filter((s) => s.startsWith("custom:"))],
    backend,
    backendPart,
    routeGroup,
    routePart,
    query.limit,
  );
}

function combineParts(
  selected: string[],
  backend: DiscoverySource[],
  backendPart: PromiseSettledResult<DiscoverySearchResponse>,
  route: string[],
  routePart: PromiseSettledResult<DiscoverySearchResponse>,
  limit?: number,
): DiscoverySearchResponse {
  const parts: DiscoverySearchResponse[] = [];
  const failures: DiscoverySourceError[] = [];

  for (const [half, group] of [
    [backendPart, backend],
    [routePart, route],
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
  // The verified profile powers the per-job fit score. Loaded once; the score
  // is computed client-side so a whole page ranks instantly and for free.
  const { data: facts = [] } = useQuery({
    queryKey: ["facts"],
    queryFn: () => api.listFacts(),
  });

  // Hydrate from localStorage so the result list survives nav, reload, and
  // closing and reopening the browser, until Clear results.
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
  // Default to fit so the strongest matches lead. When the profile is empty the
  // fit branch falls back to recency, so this is safe for a fresh account too.
  const [sort, setSort] = useState<SortMode>(initial.sort ?? "fit");

  const [smartQuery, setSmartQuery] = useState<string>("");

  // Pasted keys live in localStorage, which the server render cannot see, so
  // start empty and pick them up on mount. The event fires from /jobs/keys in
  // this tab; `storage` covers the same page open in another one.
  const [keys, setKeys] = useState<DiscoveryKeys>({});
  // Same story for the user's own endpoints and the acceptance that unlocks
  // them: both live in localStorage and are edited on /jobs/keys.
  const [customSources, setCustomSources] = useState<CustomSource[]>([]);
  const [customAccepted, setCustomAccepted] = useState(false);
  useEffect(() => {
    const refresh = () => {
      const next = loadKeys();
      setKeys(next);
      // A source whose key was just removed cannot run, so un-select it rather
      // than leaving it lit and silently skipped.
      setSources((prev) => {
        const kept = prev.filter((s) => !isByoSource(s) || hasKey(s, next));
        return kept.length > 0 && kept.length < prev.length ? kept : prev;
      });
      setCustomSources(loadCustomSources());
      setCustomAccepted(hasAcceptedTerms());
    };
    refresh();
    window.addEventListener(KEYS_CHANGED_EVENT, refresh);
    window.addEventListener(CUSTOM_SOURCES_CHANGED_EVENT, refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener(KEYS_CHANGED_EVENT, refresh);
      window.removeEventListener(CUSTOM_SOURCES_CHANGED_EVENT, refresh);
      window.removeEventListener("storage", refresh);
    };
  }, []);

  // Persist on any change so reload restores state.
  useEffect(() => {
    saveState({ titles, techs, country, maxAgeDays, limit, sources, results, sort });
  }, [titles, techs, country, maxAgeDays, limit, sources, results, sort]);

  const search = useMutation({
    // Read the keys at call time rather than closing over state: the user may
    // have connected a source in another tab since this page mounted.
    mutationFn: (body: DiscoverySearchRequest) =>
      runSplitSearch(body, loadKeys(), enabledCustomSources()),
    onSuccess: (data) => {
      setResults(data.results);
      setSourceCounts(data.source_counts ?? {});
      setSourceErrors(data.errors ?? []);
      if (data.results.length === 0)
        toast("No results", { description: "Try widening the filters." });
    },
    onError: (err: Error) => reportFailure("run that search", err),
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
    onError: (err: Error) => reportFailure("read that sentence", err),
  });

  const saveSearch = useMutation({
    mutationFn: async (name: string) => {
      const query = currentQuery();
      const { backend, noKey, byoKey } = splitSources(query.sources ?? []);
      // FastAPI rejects the key-free ids outright, so strip them from the
      // stored query and remember them locally against the new id.
      const saved = await api.createSavedSearch({
        name,
        query: { ...query, sources: backend },
      });
      saveSavedNoKey(saved.id, [...noKey, ...byoKey]);
      return saved;
    },
    onSuccess: () => {
      toast.success("Search saved");
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
    },
    onError: (err: Error) => reportFailure("save that search", err),
  });

  const runSaved = useMutation({
    mutationFn: async (s: SavedSearch) => {
      const stored = loadSavedNoKey()[s.id] ?? [];
      const savedKeys = loadKeys();
      const route = routeSources(stored, savedKeys);
      // Custom sources are not tied to a saved search: a saved query re-runs
      // whichever of them are switched on right now.
      const custom = enabledCustomSources();
      const routeGroup: string[] = [
        ...route,
        ...custom.map((c) => `custom:${c.id}`),
      ];
      const backend = s.query.sources ?? [];
      // runSavedSearch is what updates last_run_at / last_run_count upstream,
      // so keep using it for the backend half rather than replaying the query.
      const [backendPart, routePart] = await Promise.allSettled([
        backend.length
          ? api.runSavedSearch(s.id)
          : Promise.resolve(emptyDiscoveryResponse()),
        routeGroup.length
          ? api.discoverNoKey({
              sources: route,
              title_keywords: s.query.title_keywords ?? [],
              country_codes: s.query.country_codes ?? [],
              max_age_days: s.query.max_age_days,
              limit: s.query.limit,
              keys: savedKeys,
              custom_sources: toCustomPayload(custom),
            })
          : Promise.resolve(emptyDiscoveryResponse()),
      ]);
      if (backendPart.status === "rejected" && routePart.status === "rejected") {
        throw backendPart.reason as Error;
      }
      return combineParts(
        [...backend, ...routeGroup],
        backend,
        backendPart,
        routeGroup,
        routePart,
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
    onError: (err: Error) => reportFailure("run that saved search", err),
  });

  const deleteSaved = useMutation({
    mutationFn: (id: string) => api.deleteSavedSearch(id),
    onSuccess: (_data, id) => {
      forgetSavedNoKey(id);
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
    },
    onError: (err: Error) => reportFailure("delete that saved search", err),
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

  function toggleCustom(source: CustomSource) {
    setCustomSources(setCustomEnabled(source.id, !source.enabled));
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
  const userLocation = (settings?.default_location ?? "").toLowerCase().trim();
  const vocab: ProfileVocab = useMemo(() => buildProfileVocab(facts), [facts]);
  // Score every result once, keyed the same way the card list is keyed, so both
  // the sort and each card's badge read from the same computation.
  const fitByKey = useMemo(() => {
    const m = new Map<string, FitResult>();
    if (!results || !vocab.ready) return m;
    for (const r of results) m.set(resultKey(r), scoreJob(r, vocab));
    return m;
  }, [results, vocab]);
  const sortedResults = useMemo(() => {
    if (!results) return null;
    const copy = [...results];
    if (sort === "fit") {
      copy.sort((a, b) => {
        const fb = fitByKey.get(resultKey(b))?.score ?? -1;
        const fa = fitByKey.get(resultKey(a))?.score ?? -1;
        return fb - fa || tsOrZero(b.posted_at) - tsOrZero(a.posted_at);
      });
    } else if (sort === "recency") {
      copy.sort((a, b) => tsOrZero(b.posted_at) - tsOrZero(a.posted_at));
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
  }, [results, sort, userLocation, fitByKey]);

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
        <label htmlFor="smart-search" className="flex items-center gap-1.5 text-sm font-medium">
          <Wand2 className="size-3.5 text-[color:var(--color-violet)]" aria-hidden="true" /> Smart search
        </label>
        <p id="smart-search-help" className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">
          Type a sentence. The fast agent extracts the filters and runs the search.
        </p>
        <div className="mt-2 flex gap-2">
          <input
            id="smart-search"
            aria-describedby="smart-search-help"
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
                  className="ml-0.5 rounded-full p-1 text-[color:var(--color-text-dim)] opacity-0 transition group-hover:opacity-100 hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-rose-ink)]"
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
        {/* A set of toggles rather than one control, so the heading names a
            group instead of pretending to be a <label> for a single input. */}
        <div className="md:col-span-2" role="group" aria-labelledby="sources-label">
          <span id="sources-label" className="block text-sm font-medium">Sources</span>
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
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-[11px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
                Add a key for more coverage
              </div>
              <Link
                href="/jobs/keys"
                className="inline-flex items-center gap-1 text-[11px] text-[color:var(--color-violet)] hover:underline"
              >
                <KeyRound className="size-2.5" /> Connect job sources
              </Link>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {KEYED_SOURCES.map((s) => {
                const meta = SOURCE_META[s];
                // The bring-your-own-key sources are only selectable once a key
                // is in this browser; until then the tile is a route to the
                // page that asks for one.
                if (meta.credential === "byo") {
                  return (
                    <ByoSourceToggle
                      key={s}
                      connected={hasKey(s, keys)}
                      active={sources.includes(s) && hasKey(s, keys)}
                      onToggle={() => toggleSource(s)}
                      label={meta.label}
                      hint={meta.hint}
                    />
                  );
                }
                return (
                  <SourceToggle
                    key={s}
                    active={sources.includes(s)}
                    onClick={() => toggleSource(s)}
                    label={meta.label}
                    hint={meta.hint}
                    badge="needs key"
                    keySteps={meta.keySteps}
                    keyUrl={meta.keyUrl}
                  />
                );
              })}
            </div>
          </div>

          {customSources.length > 0 && (
            <div className="mt-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-[11px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
                  Your custom sources
                </div>
                <Link
                  href="/jobs/keys#custom"
                  className="inline-flex items-center gap-1 text-[11px] text-[color:var(--color-violet)] hover:underline"
                >
                  <Plug className="size-2.5" /> Add a custom source
                </Link>
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                {customSources.map((s) => (
                  <CustomSourceToggle
                    key={s.id}
                    source={s}
                    accepted={customAccepted}
                    active={customAccepted && s.enabled}
                    onToggle={() => toggleCustom(s)}
                  />
                ))}
              </div>
              <Link
                href="/jobs/keys#custom"
                className="mt-2 inline-flex items-center gap-1 text-[10px] text-[color:var(--color-text-dim)] underline decoration-dotted underline-offset-2 hover:text-[color:var(--color-text)]"
              >
                <Plug className="size-2.5" /> Manage custom sources
              </Link>
            </div>
          )}
        </div>
        <Field label="Title keywords" help="Comma-separated. e.g. 'software engineer, ml engineer'">
          {(control) => (
            <input
              {...control}
              type="text"
              value={titles}
              onChange={(e) => setTitles(e.target.value)}
              placeholder="software engineer intern"
              className="field-control"
            />
          )}
        </Field>
        <Field
          label="Technologies"
          help="Comma-separated slugs. TheirStack only; the free sources do not expose a tech filter."
        >
          {(control) => (
            <input
              {...control}
              type="text"
              value={techs}
              onChange={(e) => setTechs(e.target.value)}
              placeholder="python, fastapi"
              className="field-control"
            />
          )}
        </Field>
        <Field label="Country code" help="ISO-3166 alpha-2. e.g. US, CA, GB. Blank = global.">
          {(control) => (
            <input
              {...control}
              type="text"
              value={country}
              maxLength={2}
              onChange={(e) => setCountry(e.target.value.toUpperCase())}
              className="field-control uppercase"
            />
          )}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Max age (days)">
            {(control) => (
              <input
                {...control}
                type="number"
                min={1}
                max={180}
                value={maxAgeDays}
                onChange={(e) => setMaxAgeDays(Number(e.target.value) || 30)}
                className="field-control"
              />
            )}
          </Field>
          <Field label="Limit">
            {(control) => (
              <input
                {...control}
                type="number"
                min={1}
                max={50}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value) || 20)}
                className="field-control"
              />
            )}
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

      {/* Per-source warnings, surfaced any time a source returned 0 hits or
          threw (e.g. THEIRSTACK_API_KEY missing in Render). Prevents the
          "only SimplifyJobs is showing" mystery state where one source silently
          fails. */}
      {sortedResults !== null && (sourceErrors.length > 0 || hasEmptySource(sources, sourceCounts)) && (
        <div className="notice notice-caution mt-4 p-3 text-xs">
          {sourceErrors.map((e) => (
            <div key={e.source} className="flex items-start gap-2">
              <span className="opacity-70">⚠</span>
              <div>
                <span className="font-semibold uppercase tracking-wider">
                  {e.source}
                </span>{" "}
                <span className="notice-detail">
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
                  <span className="opacity-70">·</span>
                  <div>
                    <span className="font-semibold uppercase tracking-wider">
                      {s}
                    </span>{" "}
                    <span className="notice-detail">
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
            {/* A search replaces the list in place, so say how much came back. */}
            <div role="status" className="text-sm text-[color:var(--color-text-muted)]">
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
              <span id="sort-by-label" className="text-xs text-[color:var(--color-text-dim)]">Sort by</span>
              <div
                role="group"
                aria-labelledby="sort-by-label"
                className="flex rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-0.5"
              >
                {(Object.keys(SORT_LABEL) as SortMode[]).map((m) => {
                  const disabled = m === "location" && !userLocation;
                  return (
                    <button
                      key={m}
                      onClick={() => !disabled && setSort(m)}
                      aria-pressed={sort === m}
                      // Kept focusable so the reason it cannot be used stays
                      // reachable; the click is already guarded above.
                      aria-disabled={disabled || undefined}
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
              No roles matched these filters. Widen the title keywords, raise the
              max age, or clear the country code to search globally.
            </div>
          ) : (
            <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {sortedResults.map((r) => (
                <ResultCard
                  // source_id is only unique within a source, and the list is
                  // now merged across seven of them.
                  key={resultKey(r)}
                  result={r}
                  fit={fitByKey.get(resultKey(r))}
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

// The at-a-glance match. Colour tracks the number so a strong fit reads green
// before the digits are even parsed. The tooltip spells out the ratio behind it.
function FitBadge({ fit }: { fit: FitResult }) {
  const tone =
    fit.score >= 75
      ? "border-[color:var(--color-mint)]/35 bg-[color:var(--color-mint)]/15 text-[color:var(--color-mint-ink)]"
      : fit.score >= 50
        ? "border-[color:var(--color-accent-border)] bg-[color:var(--color-accent)]/30 text-[color:var(--color-accent-ink)]"
        : "border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-dim)]";
  const total = fit.matched.length + fit.gaps.length;
  return (
    <span
      className={`shrink-0 self-start rounded-full border px-2 py-0.5 text-[11px] font-semibold tabular-nums ${tone}`}
      title={
        `Fit to your profile: you match ${fit.matched.length} of the ${total} skills this role names. ` +
        (total < 8
          ? "This posting names few skills, so the score is held down: there is not much here to judge on."
          : "")
      }
    >
      {fit.score}% fit
    </span>
  );
}

function ResultCard({
  result,
  fit,
  onImported,
  onTailored,
  onGoToApplications,
}: {
  result: DiscoveryResult;
  fit?: FitResult;
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
    onError: (err: Error) => reportFailure("import that job", err),
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
    onError: (err: Error) => reportFailure("start tailoring for that job", err),
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
                aria-label={`Open the ${result.title} posting`}
                className="shrink-0 text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
              >
                <ExternalLink className="size-3.5" aria-hidden="true" />
              </a>
            )}
          </div>
          {result.company_name && (
            <div className="mt-0.5 truncate text-xs text-[color:var(--color-text-muted)]">
              {result.company_name}
            </div>
          )}
        </div>
        {fit?.confident ? (
          <FitBadge fit={fit} />
        ) : !result.description ? (
          // Some sources list a role without its description: the SimplifyJobs
          // tables carry a title and a link and nothing else, and the JD is only
          // fetched on import. A title alone is too little to score honestly, so
          // say that rather than showing no badge and letting it read as 0% fit.
          <span
            className="shrink-0 self-start rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2 py-0.5 text-[11px] text-[color:var(--color-text-dim)]"
            title="This source lists the role without a description, so there is nothing to score against yet. Import it and job.os fetches the full posting."
          >
            score on import
          </span>
        ) : null}
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

      {fit && fit.confident && (fit.matched.length > 0 || fit.gaps.length > 0) ? (
        // When we can judge fit, the skills the posting names are more useful
        // shown against the profile than as raw source tags: what you already
        // back, and what this role wants that you do not.
        <div className="mt-2 flex flex-col gap-1">
          {fit.matched.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              {fit.matched.slice(0, 5).map((t) => (
                <span
                  key={t}
                  className="rounded-full border border-[color:var(--color-mint)]/30 bg-[color:var(--color-mint)]/10 px-2 py-0.5 text-[10px] text-[color:var(--color-mint-ink)]"
                >
                  {t}
                </span>
              ))}
            </div>
          )}
          {fit.gaps.length > 0 && (
            <div className="flex flex-wrap items-center gap-1 text-[10px] text-[color:var(--color-text-dim)]">
              <span className="uppercase tracking-wide">Gaps</span>
              {fit.gaps.slice(0, 3).map((t) => (
                <span key={t} className="rounded-full bg-[color:var(--color-surface-2)] px-2 py-0.5">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      ) : result.technologies.length > 0 ? (
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
      ) : null}

      <div className="mt-auto flex items-center justify-between gap-2 pt-3">
        {result.already_imported ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--color-mint)]/10 px-3 py-1 text-[11px] text-[color:var(--color-mint-ink)]">
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
          className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-3 py-1 text-[11px] font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
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

// Shared source-tile styling. Selected reads as a clear jasmine fill with a gold
// border and a check, not the faint mauve tint it used to be, and the label
// stays at full text colour in both states so it is legible on the light theme.
const TILE_BASE =
  "flex w-full flex-col items-start rounded-[var(--radius-card)] border px-3 py-2 text-left text-xs text-[color:var(--color-text)] transition";
const TILE_ACTIVE =
  "border-[color:var(--color-accent-ink)]/45 bg-[color:var(--color-accent)]/40 shadow-[0_10px_24px_-18px_rgba(233,198,74,.6)]";
const TILE_INACTIVE =
  "border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] hover:border-[color:var(--color-accent-border)] hover:bg-[color:var(--color-surface-hover)]";

function tileClass(active: boolean) {
  return `${TILE_BASE} ${active ? TILE_ACTIVE : TILE_INACTIVE}`;
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
      <button onClick={onClick} aria-pressed={active} className={tileClass(active)}>
        <span className="flex w-full items-center gap-1.5">
          <span className="text-sm font-medium">{label}</span>
          {badge && (
            <span className="rounded-full border border-[color:var(--color-amber)]/45 bg-[color:var(--color-amber)]/12 px-1.5 py-px text-[9px] font-medium uppercase tracking-wide text-[color:var(--color-amber-ink)]">
              {badge}
            </span>
          )}
          {active && (
            <CheckCircle2 className="ml-auto size-4 shrink-0 text-[color:var(--color-accent-ink)]" />
          )}
        </span>
        <span className="mt-0.5 text-[10px] text-[color:var(--color-text-muted)]">{hint}</span>
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

/**
 * A source whose key lives in this browser. Locked until the key is pasted:
 * clicking an unconnected tile goes to /jobs/keys rather than selecting a
 * source that would be dropped from the request anyway.
 */
function ByoSourceToggle({
  active,
  connected,
  onToggle,
  label,
  hint,
}: {
  active: boolean;
  connected: boolean;
  onToggle: () => void;
  label: string;
  hint: string;
}) {
  const tile = tileClass(active);

  const heading = (
    <>
      <span className="flex w-full items-center gap-1.5">
        <span className="text-sm font-medium">{label}</span>
        {connected ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-mint)]/40 bg-[color:var(--color-mint)]/10 px-1.5 py-px text-[9px] uppercase tracking-wide text-[color:var(--color-mint-ink)]">
            <span className="size-1 rounded-full bg-[color:var(--color-mint)]" />
            connected
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-amber)]/45 bg-[color:var(--color-amber)]/12 px-1.5 py-px text-[9px] uppercase tracking-wide text-[color:var(--color-amber-ink)]">
            <Lock className="size-2" /> add key
          </span>
        )}
        {active && (
          <CheckCircle2 className="ml-auto size-4 shrink-0 text-[color:var(--color-accent-ink)]" />
        )}
      </span>
      <span className="text-[10px] text-[color:var(--color-text-muted)]">{hint}</span>
    </>
  );

  return (
    <div className="relative">
      {connected ? (
        <button onClick={onToggle} aria-pressed={active} className={tile}>
          {heading}
        </button>
      ) : (
        <Link href="/jobs/keys" className={tile}>
          {heading}
        </Link>
      )}
      <Link
        href="/jobs/keys"
        className="mt-1 inline-flex items-center gap-1 text-[10px] text-[color:var(--color-text-dim)] underline decoration-dotted underline-offset-2 hover:text-[color:var(--color-text)]"
      >
        <KeyRound className="size-2.5" />
        {connected ? "Manage key" : "Paste your free key"}
      </Link>
    </div>
  );
}

/**
 * A feed the user hosts themselves. Locked until the terms on /jobs/keys are
 * accepted: clicking an ungated tile goes there rather than selecting a source
 * the search would drop anyway.
 */
function CustomSourceToggle({
  source,
  accepted,
  active,
  onToggle,
}: {
  source: CustomSource;
  accepted: boolean;
  active: boolean;
  onToggle: () => void;
}) {
  const tile = tileClass(active);

  const heading = (
    <>
      <span className="flex w-full items-center gap-1.5">
        <span className="text-sm font-medium">{source.name}</span>
        <span className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-violet)]/40 bg-[color:var(--color-violet)]/10 px-1.5 py-px text-[9px] uppercase tracking-wide text-[color:var(--color-violet)]">
          custom
        </span>
        {!accepted && (
          <span className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-amber)]/45 bg-[color:var(--color-amber)]/12 px-1.5 py-px text-[9px] uppercase tracking-wide text-[color:var(--color-amber-ink)]">
            <Lock className="size-2" /> accept terms
          </span>
        )}
        {active && (
          <CheckCircle2 className="ml-auto size-4 shrink-0 text-[color:var(--color-accent-ink)]" />
        )}
      </span>
      <span className="text-[10px] text-[color:var(--color-text-muted)]">
        {hostnameOf(source.url)}
      </span>
    </>
  );

  if (!accepted) {
    return (
      <Link href="/jobs/keys#custom" className={tile}>
        {heading}
      </Link>
    );
  }
  return (
    <button onClick={onToggle} aria-pressed={active} className={tile}>
      {heading}
    </button>
  );
}

/** The tile shows where a custom source points without showing the full path. */
function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
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

// The same identity the card list keys on, so the fit map and the rendered
// cards line up. source_id is only unique within a source.
function resultKey(r: DiscoveryResult): string {
  return `${r.source}:${r.source_id || r.source_url}`;
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
  const { noKey, byoKey } = splitSources(current);
  if (!fromAgent || fromAgent.length === 0) return current;
  const merged = [...splitSources(fromAgent).backend, ...noKey, ...byoKey];
  return merged.length > 0 ? merged : current;
}

function prettyError(source: DiscoverySource | string, msg: string): string {
  const lower = msg.toLowerCase();
  // A custom endpoint is the user's own deployment, so every one of these
  // points at something they can go and fix.
  if (source.startsWith("custom:")) {
    if (lower.includes("timed out")) {
      return "Your endpoint did not answer in time.";
    }
    if (lower.includes("too large")) {
      return "Your endpoint returned too much data.";
    }
    if (lower.includes("blocked host") || lower.includes("https")) {
      return "That endpoint URL is not allowed (must be a public https URL).";
    }
    if (
      lower.includes("rejected") ||
      lower.includes("not found") ||
      lower.includes("error")
    ) {
      return `job.os could not reach your custom endpoint: ${msg}.`;
    }
    return msg;
  }
  if (source === "theirstack") {
    if (lower.includes("not configured") || lower.includes("api_key")) {
      return "TheirStack is not enabled on the server yet.";
    }
    if (lower.includes("401") || lower.includes("unauthorized")) {
      return "TheirStack rejected the request (401). The server key needs to be refreshed.";
    }
    if (lower.includes("402") || lower.includes("credit")) {
      return "Out of TheirStack credits. Top up the account or fall back to GitHub.";
    }
  }
  if (isByoSource(source as DiscoverySource)) {
    if (lower.includes("add a key")) {
      return "No key saved in this browser yet. Add one on the Connect job sources page.";
    }
    if (lower.includes("key rejected")) {
      return `${msg}. Re-copy the key on the Connect job sources page.`;
    }
    if (lower.includes("quota")) {
      return `${msg}. The provider is rate-limiting; try again shortly.`;
    }
  }
  if ((NO_KEY_SOURCES as string[]).includes(source)) {
    if (lower.includes("timed out")) {
      return `${msg}. The board was slow to answer; try again.`;
    }
    if (lower.includes("404")) {
      return `${msg}. A board slug in the curated list has moved or closed.`;
    }
  }
  return msg.length > 200 ? msg.slice(0, 200) + "…" : msg;
}
