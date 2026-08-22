"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow, parseISO } from "date-fns";
import { motion } from "framer-motion";
import {
  Bookmark,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  Loader2,
  MapPin,
  Radar,
  Search,
  ShieldAlert,
  Sparkles,
  Wand2,
  X,
} from "lucide-react";
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
import { indexHitToDiscoveryResult } from "@/lib/discover/index-results";
import { detectEligibilityFlags } from "@/lib/discover/work-auth";
import { reportFailure } from "@/lib/errors";
import {
  BACKEND_SOURCES,
  emptyDiscoveryResponse,
  FREE_SOURCES,
  mergeDiscoveryResponses,
  NO_KEY_SOURCES,
} from "@/lib/discover/sources";
import type {
  DiscoveryResult,
  DiscoverySearchRequest,
  DiscoverySearchResponse,
  DiscoverySource,
  DiscoverySourceError,
  IndexMatchScore,
  IndexSearchRequest,
  IndexSearchResponse,
  SavedSearch,
} from "@/lib/types";

/**
 * The one source list every search runs against. No per-user picker: which
 * sources are live is a code change here (and the constant below), not a
 * runtime UI toggle -- there is one operator of this deployment, not many
 * tenants who each want their own board selection or custom feed. TheirStack
 * costs real money per call, so it needs an explicit `true` here rather than
 * defaulting on just because it is otherwise configured on the server.
 */
const LIVE_SOURCES: DiscoverySource[] = [...FREE_SOURCES];

/**
 * Default and ceiling for the "Limit" field. 20 (the old default) hid most of
 * a search's real results: `mergeDiscoveryResponses` caps the FINAL merged
 * list to exactly this number, so with several sources each returning a
 * couple dozen matches a 20-cap discarded most of them silently -- the banner
 * still showed each source's own true count, which was the tell. Matches
 * `DEFAULT_LIMIT`/`MAX_LIMIT` in job_index.py and no-key-sources.ts, which
 * were already this generous on the backend; only this page's own default
 * and the "Limit" field's ceiling were still the old, tighter numbers.
 */
const DEFAULT_RESULT_LIMIT = 60;
const MAX_RESULT_LIMIT = 200;
const ENABLE_THEIRSTACK = false;
if (ENABLE_THEIRSTACK) LIVE_SOURCES.push("theirstack");

/**
 * A permanent entry in the live fan-out, wired through job.os's existing
 * custom-endpoint plumbing (still used server-side; see /api/discover) --
 * just no longer user-editable now that the picker is gone. A dev adds or
 * removes an entry here directly, rather than through a runtime toggle.
 *
 * freehire.me (github.com/strelov1/freehire) is a keyless, MIT-licensed
 * public API that beats every one of the standalone scraper's own 7 ATS
 * sources on its own numbers; verified live 2026-08-21 (real, current
 * postings, HTTP 200, no auth needed).
 */
const DEV_CUSTOM_SOURCES: { id: string; name: string; url: string }[] = [
  {
    id: "freehire",
    name: "freehire.me",
    url:
      "https://freehire.me/api/v1/jobs/search?seniority=intern&employment_type=internship" +
      "&category=software_engineering,backend,frontend,fullstack,mobile,devops,sre," +
      "network_engineering,data_engineering,data_science,data_analytics,ml_ai,ai_engineering," +
      "qa,security,hardware,embedded,architecture,solutions_engineering&posted_within_days=120",
  },
];

// Adapts the server's AI-authoritative score to the shape the client
// heuristic already returns, so ResultCard/sorting need no separate
// rendering path for the two -- see fitByKey's own comment for the handoff
// contract this exists to serve.
function serverMatchToFitResult(match: IndexMatchScore): FitResult {
  return {
    score: match.overall,
    matched: match.matched_skills,
    gaps: match.missing_skills,
    confident: match.confidence === "high",
  };
}

type SortMode = "fit" | "recency" | "location";
const SORT_LABEL: Record<SortMode, string> = {
  fit: "Best fit",
  recency: "Recency",
  location: "My location",
};

// Bumped to v3 when source selection was removed: v1/v2 sessions persisted a
// `sources` array that no longer means anything (the set is fixed in code
// now), and replaying a stale one would look like a silent, unexplained
// filter.
const STORAGE_KEY = "discover:state:v3";

type PersistedState = {
  titles: string;
  techs: string;
  country: string;
  maxAgeDays: number;
  limit: number;
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

/**
 * Merge the index half with whichever of the backend/route halves actually
 * ran. Shared by the main search and a saved-search re-run, which differ only
 * in how the backend half is produced (a fresh /discovery/search call vs.
 * /discovery/saved/{id}/run, which also bumps last_run_at/last_run_count).
 * `routeGroup` is a superset of the typed `DiscoverySource[]` sources actually
 * sent, since a custom endpoint (freehire) reports as "custom:<id>", which no
 * union can enumerate.
 */
function combineSearchParts(
  indexPart: PromiseSettledResult<IndexSearchResponse>,
  backendPart: PromiseSettledResult<DiscoverySearchResponse>,
  backend: DiscoverySource[],
  routePart: PromiseSettledResult<DiscoverySearchResponse>,
  routeGroup: string[],
  limit: number | undefined,
): DiscoverySearchResponse {
  const parts: DiscoverySearchResponse[] = [];
  const failures: DiscoverySourceError[] = [];

  if (indexPart.status === "fulfilled") {
    parts.push({
      results: indexPart.value.results.map(indexHitToDiscoveryResult),
      source_counts: { index: indexPart.value.results.length },
      errors: [],
    });
  } else {
    failures.push({ source: "index", message: (indexPart.reason as Error)?.message ?? "request failed" });
  }
  for (const [half, group] of [
    [backendPart, backend as string[]],
    [routePart, routeGroup],
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

  if (parts.length === 0) throw new Error(failures[0]?.message ?? "search failed");

  // `limit` is what each half was asked for, so the merged list is capped to
  // it as well. Otherwise picking 20 would quietly return up to 60.
  const merged = mergeDiscoveryResponses(parts, ["index", ...backend, ...routeGroup], limit);
  merged.errors = [...merged.errors, ...failures];
  return merged;
}

/**
 * Every search -- the sentence box, the advanced-filters button, and a saved
 * search re-run alike -- answers from both the pre-built index and the fixed
 * live source set at once and returns one merged, deduped list. There used
 * to be a second, manually-triggered "also search live sources" action with
 * its own button and its own source picker; that was two searches wearing
 * one page, not one search, so it is gone. Both halves run in parallel and
 * neither can sink the other: if one rejects, its failure becomes an error
 * row and the other half's results still render. Only a total failure
 * throws, which surfaces as the mutation's error toast.
 */
async function runUnifiedSearch(
  filters: DiscoverySearchRequest,
): Promise<DiscoverySearchResponse> {
  const backend = LIVE_SOURCES.filter((s) => BACKEND_SOURCES.includes(s));
  const route = LIVE_SOURCES.filter((s) => NO_KEY_SOURCES.includes(s));
  const routeGroup: string[] = [
    ...route,
    ...DEV_CUSTOM_SOURCES.map((s) => `custom:${s.id}`),
  ];

  const [indexPart, backendPart, routePart] = await Promise.allSettled([
    api.indexSearch({
      title_keywords: filters.title_keywords ?? [],
      query: (filters.technology_slugs ?? []).join(" ") || undefined,
      country_codes: filters.country_codes ?? [],
      max_age_days: filters.max_age_days,
      limit: filters.limit,
    }),
    backend.length
      ? api.discoverySearch({ ...filters, sources: backend })
      : Promise.resolve(emptyDiscoveryResponse()),
    routeGroup.length
      ? api.discoverNoKey({
          sources: route,
          title_keywords: filters.title_keywords ?? [],
          country_codes: filters.country_codes ?? [],
          max_age_days: filters.max_age_days,
          limit: filters.limit,
          custom_sources: DEV_CUSTOM_SOURCES,
          // The feed renders a description clamp and fit-score reads the same
          // text, so browsing needs it. Ingest and alerts do not, which is why
          // this is opt-in per caller rather than on by default.
          hydrate_descriptions: true,
        })
      : Promise.resolve(emptyDiscoveryResponse()),
  ]);

  return combineSearchParts(indexPart, backendPart, backend, routePart, routeGroup, filters.limit);
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
  const [limit, setLimit] = useState<number>(initial.limit ?? DEFAULT_RESULT_LIMIT);
  const [results, setResults] = useState<DiscoveryResult[] | null>(initial.results ?? null);
  const [sourceCounts, setSourceCounts] = useState<Record<string, number>>({});
  const [sourceErrors, setSourceErrors] = useState<DiscoverySourceError[]>([]);
  // Default to fit so the strongest matches lead. When the profile is empty the
  // fit branch falls back to recency, so this is safe for a fresh account too.
  const [sort, setSort] = useState<SortMode>(initial.sort ?? "fit");
  // Collapsed by default: most searches just want the sentence box; refining
  // by title/tech/country/age is the exception, not the default view.
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [smartQuery, setSmartQuery] = useState<string>("");

  // Persist on any change so reload restores state.
  useEffect(() => {
    saveState({ titles, techs, country, maxAgeDays, limit, results, sort });
  }, [titles, techs, country, maxAgeDays, limit, results, sort]);

  // The one search: every call answers from the pre-built index and the fixed
  // live source set at once, merged into one deduped list. Used by the
  // sentence box, the advanced-filters button, and the initial blank-page load.
  const search = useMutation({
    mutationFn: (body: DiscoverySearchRequest) => runUnifiedSearch(body),
    onSuccess: (data) => {
      setResults(data.results);
      setSourceCounts(data.source_counts ?? {});
      setSourceErrors(dropLeverNoise(data.errors ?? []));
      if (data.results.length === 0)
        toast("No results", { description: "Try widening the filters." });
    },
    onError: (err: Error) => reportFailure("run that search", err),
  });

  // Land on a page with something to look at rather than an empty form: only
  // when there is no persisted state from a previous visit.
  useEffect(() => {
    if (results === null) search.mutate(currentQuery());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function currentQuery(): DiscoverySearchRequest {
    return {
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
      if (explanation) toast.success(explanation);
      search.mutate({
        title_keywords: filters.title_keywords ?? [],
        technology_slugs: filters.technology_slugs ?? [],
        country_codes: filters.country_codes ?? [],
        max_age_days: filters.max_age_days ?? 30,
        limit: filters.limit ?? DEFAULT_RESULT_LIMIT,
      });
    },
    onError: (err: Error) => reportFailure("read that sentence", err),
  });

  const saveSearch = useMutation({
    mutationFn: (name: string) =>
      api.createSavedSearch({
        name,
        // FastAPI's SavedSearch schema only knows the backend sources
        // (theirstack/github); the fixed no-key/index halves re-run on
        // whatever LIVE_SOURCES says at the time, not what was true when saved.
        query: { ...currentQuery(), sources: LIVE_SOURCES.filter((s) => BACKEND_SOURCES.includes(s)) },
      }),
    onSuccess: () => {
      toast.success("Search saved");
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
    },
    onError: (err: Error) => reportFailure("save that search", err),
  });

  const runSaved = useMutation({
    mutationFn: async (s: SavedSearch) => {
      const backend = s.query.sources ?? [];
      const route = LIVE_SOURCES.filter((src) => NO_KEY_SOURCES.includes(src));
      const routeGroup: string[] = [
        ...route,
        ...DEV_CUSTOM_SOURCES.map((c) => `custom:${c.id}`),
      ];
      const [indexPart, backendPart, routePart] = await Promise.allSettled([
        api.indexSearch({
          title_keywords: s.query.title_keywords ?? [],
          query: (s.query.technology_slugs ?? []).join(" ") || undefined,
          country_codes: s.query.country_codes ?? [],
          max_age_days: s.query.max_age_days,
          limit: s.query.limit,
        }),
        // runSavedSearch is what updates last_run_at / last_run_count upstream,
        // so keep using it for the backend half rather than replaying the query.
        backend.length ? api.runSavedSearch(s.id) : Promise.resolve(emptyDiscoveryResponse()),
        routeGroup.length
          ? api.discoverNoKey({
              sources: route,
              title_keywords: s.query.title_keywords ?? [],
              country_codes: s.query.country_codes ?? [],
              max_age_days: s.query.max_age_days,
              limit: s.query.limit,
              custom_sources: DEV_CUSTOM_SOURCES,
              hydrate_descriptions: true,
            })
          : Promise.resolve(emptyDiscoveryResponse()),
      ]);
      return combineSearchParts(indexPart, backendPart, backend, routePart, routeGroup, s.query.limit);
    },
    onSuccess: (data) => {
      setResults(data.results);
      setSourceCounts(data.source_counts ?? {});
      setSourceErrors(dropLeverNoise(data.errors ?? []));
      qc.invalidateQueries({ queryKey: ["saved-searches"] });
      if (data.results.length === 0)
        toast("No results", { description: "Saved query returned nothing today." });
    },
    onError: (err: Error) => reportFailure("run that saved search", err),
  });

  const deleteSaved = useMutation({
    mutationFn: (id: string) => api.deleteSavedSearch(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved-searches"] }),
    onError: (err: Error) => reportFailure("delete that saved search", err),
  });

  function applySaved(s: SavedSearch) {
    setTitles((s.query.title_keywords ?? []).join(", "));
    setTechs((s.query.technology_slugs ?? []).join(", "));
    setCountry((s.query.country_codes ?? [])[0] ?? "");
    setMaxAgeDays(s.query.max_age_days ?? 30);
    setLimit(s.query.limit ?? DEFAULT_RESULT_LIMIT);
    runSaved.mutate(s);
  }

  function onSaveClick() {
    const name = window.prompt("Name this search (e.g. 'SWE intern · Boston')");
    if (!name) return;
    saveSearch.mutate(name.trim());
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
  //
  // The handoff contract, one job at a time (docs/job-enrichment.md): a
  // result carrying `match` was scored server-side against the AI-extracted
  // job facts and is authoritative -- render it and skip the client lexicon
  // entirely, even when `vocab` itself is not ready, since the server score
  // does not depend on it. Only a result with no `match` yet (not enriched,
  // or no signed-in profile signal to score against) falls back to the
  // client estimate. The two must never both render for the same job.
  const fitByKey = useMemo(() => {
    const m = new Map<string, FitResult>();
    if (!results) return m;
    for (const r of results) {
      if (r.match) m.set(resultKey(r), serverMatchToFitResult(r.match));
      else if (vocab.ready) m.set(resultKey(r), scoreJob(r, vocab));
    }
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
        <InfoChip tone="sage">Indexed, updated overnight</InfoChip>
        <InfoChip>{saved.length} saved searches</InfoChip>
        <InfoChip tone="clay">50-result guardrail</InfoChip>
      </PageIntro>

      {/* The one search box: a sentence goes to the fast agent, which
          extracts structured filters (hydrated into the advanced panel below
          so the user can see and adjust what it read), then every search --
          this one, the advanced-filters button, and a saved search alike --
          answers from the pre-built index and the fixed live source set at
          once, merged into one deduped list. There is no separate "search
          live sources" action and no source picker: which sources run is a
          code change (see LIVE_SOURCES above), not something shown here. */}
      <form onSubmit={onSmartSubmit} className="workspace-panel mt-6 p-5 sm:p-6">
        <label htmlFor="smart-search" className="flex items-center gap-1.5 text-sm font-medium">
          <Wand2 className="size-3.5 text-[color:var(--color-violet)]" aria-hidden="true" /> Search
        </label>
        <p id="smart-search-help" className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">
          Type a sentence, e.g. &ldquo;fullstack intern in Boston with Python and React
          from the last 2 weeks&rdquo;. The fast agent extracts the filters and runs the
          search.
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          <input
            id="smart-search"
            aria-describedby="smart-search-help"
            type="text"
            value={smartQuery}
            onChange={(e) => setSmartQuery(e.target.value)}
            placeholder="software engineer intern, python, remote"
            className="field-control min-w-0 flex-1 rounded-full"
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
            Search
          </button>
          {results !== null && (
            <button
              type="button"
              onClick={clearResults}
              className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-sm text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
            >
              <X className="size-3.5" /> Clear
            </button>
          )}
        </div>
      </form>

      <button
        onClick={() => setShowAdvanced((v) => !v)}
        aria-expanded={showAdvanced}
        className="mt-4 flex items-center gap-1.5 text-xs font-medium text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
      >
        <ChevronRight
          className={`size-3.5 transition-transform ${showAdvanced ? "rotate-90" : ""}`}
        />
        Advanced filters
        <span className="text-[color:var(--color-text-dim)]">
          (refine by title, tech, country, age, and result limit)
        </span>
      </button>

      {showAdvanced && (
        <>
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
          help="Comma-separated slugs. Folded into the index's free-text query; not every live source exposes a tech filter."
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
                max={MAX_RESULT_LIMIT}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value) || DEFAULT_RESULT_LIMIT)}
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
        </>
      )}

      {/* Per-source errors only (e.g. THEIRSTACK_API_KEY missing on the backend).
          Zero-hit sources are expected noise on a narrow filter, not worth a
          banner, so they go to the console instead; see the search mutation's
          onSuccess. */}
      {sortedResults !== null && sourceErrors.length > 0 && (
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
  // Read off the posting's own words, so it costs nothing and cannot be wrong
  // about what the employer said. Computed per render because it is a few
  // regexes over text already in memory.
  const eligibility = detectEligibilityFlags(result);

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
      <div className="flex items-start justify-between gap-3">
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
        ) : (
          <span />
        )}
        {result.source_url && (
          <a
            href={result.source_url}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open the ${result.title} posting`}
            className="shrink-0 text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text)]"
          >
            <ExternalLink className="size-4" aria-hidden="true" />
          </a>
        )}
      </div>

      <h3 className="mt-3 line-clamp-2 text-lg font-medium leading-tight tracking-[-0.01em] text-[color:var(--color-text)]">
        {result.title}
      </h3>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-[color:var(--color-text-dim)]">
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

      {/* Above the skills, because it outranks them: no amount of skill overlap
          makes a cleared or ITAR role winnable on a student visa, and this is
          the cheaper question to answer before tailoring anything. */}
      {eligibility.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {eligibility.map((flag) => (
            <span
              key={flag.kind}
              title={flag.detail}
              className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-rose-ink)]/35 bg-[color:var(--color-rose)]/10 px-2 py-0.5 text-[10px] font-medium text-[color:var(--color-rose-ink)]"
            >
              <ShieldAlert className="size-3" aria-hidden="true" />
              {flag.label}
            </span>
          ))}
        </div>
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

      {/* The seam the reference card marks with a divider: content above is
          about the role, everything below is about who's hiring and what to
          do next. */}
      <div className="mt-auto border-t border-[color:var(--color-border)] pt-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <CompanyAvatar name={result.company_name ?? "?"} domain={result.company_domain} size={36} />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-[color:var(--color-text)]">
                {result.company_name || "Unknown company"}
              </p>
              {result.already_imported && (
                <p className="flex items-center gap-1 text-[11px] text-[color:var(--color-mint-ink)]">
                  <CheckCircle2 className="size-3" /> In Applications
                </p>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {!result.already_imported && (
              <button
                onClick={() => importJob.mutate()}
                disabled={importJob.isPending || tailorJob.isPending}
                title="Fetches and parses the posting, usually 5-10s"
                className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-[11px] hover:bg-[color:var(--color-surface-hover)] disabled:opacity-50"
              >
                {importJob.isPending ? <Loader2 className="size-3 animate-spin" /> : null}
                Import
              </button>
            )}
            <button
              onClick={() => tailorJob.mutate()}
              disabled={tailorJob.isPending || importJob.isPending}
              className="inline-flex items-center gap-1 rounded-full bg-[color:var(--color-text)] px-3.5 py-1.5 text-[11px] font-medium text-[color:var(--color-bg)] transition enabled:hover:opacity-85 disabled:opacity-50"
              title="Import + create application + open the tailoring agent"
            >
              {tailorJob.isPending ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Sparkles className="size-3" />
              )}
              Tailor
            </button>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

/**
 * Lever's own boards are the noisiest of the fixed source set: some
 * companies' Lever postings run to several megabytes of embedded HTML/CSS
 * (over the 1.0MB cap `custom-fetch.ts` enforces), and Lever itself is
 * slower to answer than the other boards on a bad day. That is routine,
 * board-by-board partial failure inherent to fanning out to 100+ boards
 * every search, not something wrong with THIS search that the user needs to
 * see a warning banner about -- so it goes to the console instead, the same
 * "quiet unless it's actionable" treatment zero-hit sources already get.
 * Every other source's errors still render in the banner unchanged.
 */
function dropLeverNoise(errors: DiscoverySourceError[]): DiscoverySourceError[] {
  return errors.filter((e) => {
    if (e.source !== "lever") return true;
    console.warn("[jobs] lever board fan-out:", e.message);
    return false;
  });
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

function prettyError(source: DiscoverySource | string, msg: string): string {
  const lower = msg.toLowerCase();
  if (source === "index") {
    return `The index search failed: ${msg}.`;
  }
  if (source.startsWith("custom:")) {
    const name = DEV_CUSTOM_SOURCES.find((s) => `custom:${s.id}` === source)?.name ?? source;
    if (lower.includes("timed out")) return `${name} did not answer in time.`;
    if (lower.includes("too large")) return `${name} returned too much data.`;
    return `Could not reach ${name}: ${msg}.`;
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
