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
                  Paste the URL — we&apos;ll fetch the JD and parse it.
                </Dialog.Description>
              </div>
              <Dialog.Close className="rounded-md p-1 text-[color:var(--color-text-muted)] hover:bg-white/[0.05] hover:text-white">
                <X className="size-4" />
              </Dialog.Close>
            </div>

            <form onSubmit={onSubmit} className="mt-5 flex flex-col gap-3">
              <input
                type="url"
                placeholder="https://jobs.lever.co/anthropic/..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
                autoFocus
                className="w-full rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none placeholder:text-[color:var(--color-text-dim)] focus:border-[#7C5CFF]/50 focus:bg-white/[0.05]"
              />
              <div className="mt-2 flex justify-end gap-2">
                <Dialog.Close className="rounded-full border border-white/10 px-4 py-1.5 text-sm hover:bg-white/[0.04]">
                  Cancel
                </Dialog.Close>
                <button
                  type="submit"
                  disabled={loading}
                  className="flex items-center gap-1.5 rounded-full bg-[#7C5CFF] px-4 py-1.5 text-sm font-medium text-white hover:bg-[#8C6CFF] disabled:opacity-50"
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
