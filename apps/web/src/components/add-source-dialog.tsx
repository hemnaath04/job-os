"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { CheckCircle2, FileUp, Github, Loader2, Search, X } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { withTimeout } from "@/lib/async";
import { reportFailure } from "@/lib/errors";
import type { JsonResume } from "@/lib/types";

type Mode = "blank" | "github" | "upload";

interface GitHubRepo {
  id: number;
  name: string;
  html_url: string;
  description: string | null;
  language: string | null;
  fork: boolean;
}

// GitHub's public REST API, called straight from the browser -- no server-side
// fetch, no account connection, nothing job.os ever sees or stores except the
// repos the user actually picks. Public, unauthenticated, CORS-enabled.
const GITHUB_FETCH_TIMEOUT_MS = 20_000;

/**
 * A new general-purpose source resume — blank, or seeded from a GitHub
 * account's real public repos so there's something concrete to tailor from
 * instead of an empty page. The seeded version is a starting point, not a
 * finished resume: "Edit with AI" is what turns a repo name and description
 * into real bullets, backed by whatever the user actually verifies.
 */
export function AddSourceDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [mode, setMode] = useState<Mode>("blank");
  const [name, setName] = useState("");
  const [baseRole, setBaseRole] = useState("");
  const [creating, setCreating] = useState(false);

  const [username, setUsername] = useState("");
  const [repos, setRepos] = useState<GitHubRepo[] | null>(null);
  const [fetchingRepos, setFetchingRepos] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  function reset() {
    setName("");
    setBaseRole("");
    setUsername("");
    setRepos(null);
    setSelected(new Set());
    setUploadFile(null);
  }

  function close() {
    reset();
    onOpenChange(false);
    onCreated();
  }

  async function fetchRepos(e: React.FormEvent) {
    e.preventDefault();
    const handle = username.trim().replace(/^@/, "");
    if (!handle) return;
    setFetchingRepos(true);
    setRepos(null);
    setSelected(new Set());
    try {
      const res = await withTimeout(
        fetch(
          `https://api.github.com/users/${encodeURIComponent(handle)}/repos?sort=updated&per_page=100&type=owner`,
        ),
        GITHUB_FETCH_TIMEOUT_MS,
        "GitHub did not answer in time. Try again in a moment.",
      );
      if (res.status === 404) throw new Error(`No GitHub user named "${handle}".`);
      if (res.status === 403) {
        throw new Error(
          "GitHub's public API is rate-limited right now (it allows a limited number of unauthenticated requests per hour). Try again shortly.",
        );
      }
      if (!res.ok) throw new Error(`GitHub returned ${res.status}.`);
      const all = (await res.json()) as GitHubRepo[];
      const owned = all.filter((r) => !r.fork);
      if (owned.length === 0) {
        toast("No public, non-fork repos found for that account.");
      }
      setRepos(owned);
      // A sensible starting selection rather than none or all: the API already
      // sorted by recently updated, so the first few are what's most likely
      // still relevant to lead with.
      setSelected(new Set(owned.slice(0, 6).map((r) => r.id)));
    } catch (err) {
      reportFailure("fetch that GitHub account's repos", err);
    } finally {
      setFetchingRepos(false);
    }
  }

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function createBlank() {
    if (!name.trim()) return;
    setCreating(true);
    try {
      await api.createResume({
        name: name.trim(),
        base_role: baseRole.trim() || null,
        is_master: false,
      });
      toast.success("Source resume created");
      close();
    } catch (err) {
      reportFailure("create that source resume", err);
    } finally {
      setCreating(false);
    }
  }

  async function createFromGithub() {
    if (!repos) return;
    const picked = repos.filter((r) => selected.has(r.id));
    if (picked.length === 0) return;
    setCreating(true);
    try {
      const handle = username.trim().replace(/^@/, "");
      const resume = await api.createResume({
        name: `${handle} — GitHub projects`,
        base_role: null,
        is_master: false,
      });
      const jsonResume: JsonResume = {
        projects: picked.map((repo) => ({
          name: repo.name,
          description: repo.description ?? undefined,
          url: repo.html_url,
          keywords: repo.language ? [repo.language] : [],
          highlights: repo.description ? [repo.description] : [],
        })),
      };
      await api.createVersion(resume.id, jsonResume, {
        revisionNote: `Seeded from ${picked.length} GitHub repo${picked.length === 1 ? "" : "s"}`,
      });
      toast.success(
        `Created from ${picked.length} GitHub repo${picked.length === 1 ? "" : "s"}`,
        { description: "Open it and use Edit with AI to turn these into real bullets." },
      );
      close();
    } catch (err) {
      reportFailure("create that resume from GitHub", err);
    } finally {
      setCreating(false);
    }
  }

  async function createFromUpload() {
    if (!uploadFile) return;
    setCreating(true);
    try {
      // The same AI-parsed import "Set master" already uses on the library
      // page, just without a master_filename -- so this always lands as a
      // plain (non-master) resume, which is exactly a source. sourceLabel
      // names the resume in the library; falls back to the filename, minus
      // its extension, when the Name field is left blank.
      const { items } = await api.importResumes(
        [uploadFile],
        name.trim() || uploadFile.name.replace(/\.[^.]+$/, ""),
      );
      const item = items[0];
      if (!item?.imported) {
        throw new Error(item?.note || "Could not read that file as a resume.");
      }
      toast.success("Source resume created from upload");
      close();
    } catch (err) {
      reportFailure("import that resume", err);
    } finally {
      setCreating(false);
    }
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2">
          <div className="glass rounded-[var(--radius-card)] p-6">
            <div className="flex items-start justify-between">
              <div>
                <Dialog.Title className="text-lg font-medium">Add a source</Dialog.Title>
                <Dialog.Description className="text-sm text-[color:var(--color-text-muted)]">
                  A new general-purpose resume identity — blank, seeded from your real
                  GitHub projects, or parsed from a resume file you already have.
                </Dialog.Description>
              </div>
              <Dialog.Close
                aria-label="Close"
                className="grid size-8 shrink-0 place-items-center rounded-md text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
              >
                <X className="size-4" aria-hidden="true" />
              </Dialog.Close>
            </div>

            <div
              role="group"
              aria-label="Source type"
              className="mt-4 inline-flex rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-0.5 text-xs"
            >
              {(["blank", "github", "upload"] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  aria-pressed={mode === m}
                  className={`inline-flex items-center gap-1 rounded-full px-3 py-1.5 transition ${
                    mode === m
                      ? "bg-[color:var(--color-surface-hover)] text-[color:var(--color-text)] shadow-sm"
                      : "text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
                  }`}
                >
                  {m === "github" && <Github className="size-3" />}
                  {m === "upload" && <FileUp className="size-3" />}
                  {m === "blank" ? "Blank" : m === "github" ? "From GitHub" : "From a file"}
                </button>
              ))}
            </div>

            {mode === "blank" ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  createBlank();
                }}
                className="mt-4 flex flex-col gap-3"
              >
                <label
                  htmlFor="source-name"
                  className="text-xs font-medium text-[color:var(--color-text-muted)]"
                >
                  Name
                </label>
                <input
                  id="source-name"
                  type="text"
                  placeholder="e.g. SWE, ML, AI"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoFocus
                  className="field-control"
                />
                <label
                  htmlFor="source-role"
                  className="text-xs font-medium text-[color:var(--color-text-muted)]"
                >
                  Base role (optional)
                </label>
                <input
                  id="source-role"
                  type="text"
                  value={baseRole}
                  onChange={(e) => setBaseRole(e.target.value)}
                  className="field-control"
                />
                <div className="mt-2 flex justify-end gap-2">
                  <Dialog.Close className="product-button product-button-secondary">
                    Cancel
                  </Dialog.Close>
                  <button
                    type="submit"
                    disabled={creating || !name.trim()}
                    className="product-button product-button-primary disabled:opacity-50"
                  >
                    {creating && <Loader2 className="size-3.5 animate-spin" />}
                    {creating ? "Creating…" : "Create"}
                  </button>
                </div>
              </form>
            ) : mode === "github" ? (
              <div className="mt-4 flex flex-col gap-3">
                <form onSubmit={fetchRepos} className="flex gap-2">
                  <label htmlFor="gh-username" className="sr-only">
                    GitHub username
                  </label>
                  <input
                    id="gh-username"
                    type="text"
                    placeholder="GitHub username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    autoFocus
                    className="field-control flex-1"
                  />
                  <button
                    type="submit"
                    disabled={fetchingRepos || !username.trim()}
                    className="product-button product-button-secondary shrink-0 disabled:opacity-50"
                  >
                    {fetchingRepos ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Search className="size-3.5" />
                    )}
                    Fetch
                  </button>
                </form>
                <p className="text-xs text-[color:var(--color-text-dim)]">
                  Reads public repos straight from GitHub&apos;s own API. No account
                  connection, and nothing is sent to job.os&apos;s servers.
                </p>
                {repos && repos.length > 0 && (
                  <>
                    <div className="max-h-64 space-y-1 overflow-y-auto rounded-lg border border-[color:var(--color-border)] p-2">
                      {repos.map((repo) => (
                        <label
                          key={repo.id}
                          className="flex cursor-pointer items-start gap-2 rounded-md p-2 text-sm hover:bg-[color:var(--color-surface-hover)]"
                        >
                          <input
                            type="checkbox"
                            checked={selected.has(repo.id)}
                            onChange={() => toggle(repo.id)}
                            className="mt-1"
                          />
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5 font-medium">
                              {repo.name}
                              {repo.language && (
                                <span className="rounded-full bg-[color:var(--color-surface-2)] px-1.5 py-0.5 text-[10px] font-normal text-[color:var(--color-text-dim)]">
                                  {repo.language}
                                </span>
                              )}
                            </span>
                            {repo.description && (
                              <span className="mt-0.5 block truncate text-xs text-[color:var(--color-text-muted)]">
                                {repo.description}
                              </span>
                            )}
                          </span>
                        </label>
                      ))}
                    </div>
                    <p className="text-xs text-[color:var(--color-text-dim)]">
                      {selected.size} selected
                    </p>
                  </>
                )}
                <div className="mt-2 flex justify-end gap-2">
                  <Dialog.Close className="product-button product-button-secondary">
                    Cancel
                  </Dialog.Close>
                  <button
                    type="button"
                    onClick={createFromGithub}
                    disabled={creating || !repos || selected.size === 0}
                    className="product-button product-button-primary disabled:opacity-50"
                  >
                    {creating ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <CheckCircle2 className="size-3.5" />
                    )}
                    {creating
                      ? "Creating…"
                      : `Create from ${selected.size || ""} repo${selected.size === 1 ? "" : "s"}`}
                  </button>
                </div>
              </div>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  createFromUpload();
                }}
                className="mt-4 flex flex-col gap-3"
              >
                <label
                  htmlFor="source-upload-name"
                  className="text-xs font-medium text-[color:var(--color-text-muted)]"
                >
                  Name (optional — defaults to the filename)
                </label>
                <input
                  id="source-upload-name"
                  type="text"
                  placeholder="e.g. SWE, ML, AI"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="field-control"
                />
                <input
                  ref={fileInput}
                  type="file"
                  accept=".pdf,.docx,.json,application/pdf,application/json"
                  className="hidden"
                  onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
                />
                <button
                  type="button"
                  onClick={() => fileInput.current?.click()}
                  className="flex items-center gap-2 rounded-lg border border-dashed border-[color:var(--color-border)] px-3 py-2.5 text-left text-sm text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
                >
                  <FileUp className="size-3.5 shrink-0" />
                  {uploadFile ? uploadFile.name : "Choose a PDF, DOCX, or JSON resume…"}
                </button>
                <p className="text-xs text-[color:var(--color-text-dim)]">
                  Parsed by the same AI extraction the library&apos;s own resume import
                  uses. Never becomes the protected master from here — that still needs
                  its own upload on the library page.
                </p>
                <div className="mt-2 flex justify-end gap-2">
                  <Dialog.Close className="product-button product-button-secondary">
                    Cancel
                  </Dialog.Close>
                  <button
                    type="submit"
                    disabled={creating || !uploadFile}
                    className="product-button product-button-primary disabled:opacity-50"
                  >
                    {creating && <Loader2 className="size-3.5 animate-spin" />}
                    {creating ? "Importing…" : "Create"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
