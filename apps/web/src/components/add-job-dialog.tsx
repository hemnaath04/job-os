"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";

export function AddJobDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setLoading(true);
    try {
      const job = await api.jobFromUrl(url.trim());
      await api.createApplication({ job_id: job.id, status: "wishlist" });
      toast.success(`Added: ${job.title}`);
      setUrl("");
      onOpenChange(false);
      onCreated();
    } catch (err) {
      toast.error(`Failed: ${(err as Error).message}`);
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
                  Paste a job URL and we&apos;ll import its details.
                </Dialog.Description>
              </div>
              <Dialog.Close className="rounded-md p-1 text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]">
                <X className="size-4" />
              </Dialog.Close>
            </div>

            <form onSubmit={onSubmit} className="mt-5 flex flex-col gap-3">
              <label htmlFor="job-url" className="text-xs font-medium text-[color:var(--color-text-muted)]">
                Job URL
              </label>
              <input
                id="job-url"
                type="url"
                placeholder="https://jobs.lever.co/anthropic/..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
                autoFocus
                className="field-control"
              />
              <div className="mt-2 flex justify-end gap-2">
                <Dialog.Close className="product-button product-button-secondary">
                  Cancel
                </Dialog.Close>
                <button
                  type="submit"
                  disabled={loading}
                  className="product-button product-button-primary disabled:opacity-50"
                >
                  {loading && <Loader2 className="size-3.5 animate-spin" />}
                  {loading ? "Fetching…" : "Add to wishlist"}
                </button>
              </div>
            </form>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
