"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { withTimeout } from "@/lib/async";
import { reportFailure } from "@/lib/errors";

type Mode = "url" | "text";

// A slow or JS-heavy posting page, plus the parse pass on top of it, is
// exactly the "crosses into the API container" case withTimeout exists for --
// without a ceiling, an unreachable URL left this dialog spinning on
// "Importing…" forever, since a plain fetch() has no default timeout of its
// own and the underlying request has nothing to bound it either.
const IMPORT_TIMEOUT_MS = 60 * 1_000;

export function AddJobDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [mode, setMode] = useState<Mode>("url");
  const [url, setUrl] = useState("");
  const [jdText, setJdText] = useState("");
  const [company, setCompany] = useState("");
  const [loading, setLoading] = useState(false);

  function reset() {
    setUrl("");
    setJdText("");
    setCompany("");
  }

  const canSubmit = mode === "url" ? url.trim().length > 0 : jdText.trim().length > 0;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    try {
      const job = await withTimeout(
        mode === "url"
          ? api.jobFromUrl(url.trim())
          : api.jobFromText(jdText.trim(), company.trim() || undefined),
        IMPORT_TIMEOUT_MS,
        "That page took too long to load. It may be slow, JS-heavy, or " +
          "unreachable -- try again, or use 'Paste description' instead.",
      );
      // The backend already dedupes the job itself (same URL/text returns the
      // existing row, not a new one) and rejects a repeat application for it
      // with a 409 -- this is that "already added" case, not a real failure.
      let alreadyAdded = false;
      try {
        await api.createApplication({ job_id: job.id, status: "wishlist" });
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 409) throw err;
        alreadyAdded = true;
      }
      toast.success(alreadyAdded ? `Already in your list: ${job.title}` : `Added: ${job.title}`);
      reset();
      onOpenChange(false);
      onCreated();
    } catch (err) {
      reportFailure("add that job", err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2">
          <div className="glass rounded-[var(--radius-card)] p-6">
            <div className="flex items-start justify-between">
              <div>
                <Dialog.Title className="text-lg font-medium">Add a job</Dialog.Title>
                <Dialog.Description className="text-sm text-[color:var(--color-text-muted)]">
                  Import from a URL, or paste the description for postings behind a login.
                </Dialog.Description>
              </div>
              <Dialog.Close
                aria-label="Close"
                className="grid size-8 place-items-center rounded-md text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
              >
                <X className="size-4" aria-hidden="true" />
              </Dialog.Close>
            </div>

            <div
              role="group"
              aria-label="Import method"
              className="mt-4 inline-flex rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-0.5 text-xs"
            >
              {(["url", "text"] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  aria-pressed={mode === m}
                  className={`rounded-full px-3 py-1.5 transition ${
                    mode === m
                      ? "bg-[color:var(--color-surface-hover)] text-[color:var(--color-text)] shadow-sm"
                      : "text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
                  }`}
                >
                  {m === "url" ? "From URL" : "Paste description"}
                </button>
              ))}
            </div>

            <form onSubmit={onSubmit} className="mt-4 flex flex-col gap-3">
              {mode === "url" ? (
                <>
                  <label
                    htmlFor="job-url"
                    className="text-xs font-medium text-[color:var(--color-text-muted)]"
                  >
                    Job URL
                  </label>
                  <input
                    id="job-url"
                    type="url"
                    placeholder="https://jobs.lever.co/anthropic/..."
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    autoFocus
                    className="field-control"
                  />
                </>
              ) : (
                <>
                  <label
                    htmlFor="job-jd"
                    className="text-xs font-medium text-[color:var(--color-text-muted)]"
                  >
                    Job description
                  </label>
                  <textarea
                    id="job-jd"
                    placeholder="Paste the full posting here — role, responsibilities, requirements…"
                    value={jdText}
                    onChange={(e) => setJdText(e.target.value)}
                    autoFocus
                    rows={8}
                    className="field-control min-h-32 resize-y"
                  />
                  <label
                    htmlFor="job-company"
                    className="text-xs font-medium text-[color:var(--color-text-muted)]"
                  >
                    Company (optional)
                  </label>
                  <input
                    id="job-company"
                    type="text"
                    placeholder="Only used if we can't read it from the text"
                    value={company}
                    onChange={(e) => setCompany(e.target.value)}
                    className="field-control"
                  />
                </>
              )}
              {/* Fetching the page and parsing it both happen synchronously
                  here, so the wait is real, not a hang. Naming a range keeps
                  it from reading as broken partway through. */}
              {loading && (
                <p className="text-xs text-[color:var(--color-text-dim)]">
                  Usually 5-10s. A slow or JS-heavy posting page can take longer.
                </p>
              )}
              <div className="mt-2 flex justify-end gap-2">
                <Dialog.Close className="product-button product-button-secondary">
                  Cancel
                </Dialog.Close>
                <button
                  type="submit"
                  disabled={loading || !canSubmit}
                  className="product-button product-button-primary disabled:opacity-50"
                >
                  {loading && <Loader2 className="size-3.5 animate-spin" />}
                  {loading ? "Importing…" : "Add to wishlist"}
                </button>
              </div>
            </form>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
