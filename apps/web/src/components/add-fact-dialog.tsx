"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, type ProfileFactCreate } from "@/lib/api";

type FactKind = "experience" | "project" | "skill" | "education" | "certification";

const KIND_OPTIONS: { value: FactKind; label: string }[] = [
  { value: "experience", label: "Experience" },
  { value: "project", label: "Project" },
  { value: "skill", label: "Skill" },
  { value: "education", label: "Education" },
  { value: "certification", label: "Certification" },
];

export function AddFactDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [kind, setKind] = useState<FactKind>("experience");
  const [title, setTitle] = useState("");
  const [org, setOrg] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [location, setLocation] = useState("");
  const [bullets, setBullets] = useState("");
  const [loading, setLoading] = useState(false);

  // Skills render as plain chips grouped by category (stored in `org`), so
  // they carry no dates or bullets.
  const isSkill = kind === "skill";
  const canSubmit = title.trim().length > 0;

  function reset() {
    setKind("experience");
    setTitle("");
    setOrg("");
    setStartDate("");
    setEndDate("");
    setLocation("");
    setBullets("");
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    try {
      const bulletLines = isSkill
        ? []
        : bullets
            .split("\n")
            .map((line) => line.trim())
            .filter((line) => line.length > 0)
            .map((text) => ({ text }));
      const input: ProfileFactCreate = {
        kind,
        title: title.trim(),
        org: org.trim() || null,
        start_date: isSkill ? null : startDate || null,
        end_date: isSkill ? null : endDate || null,
        location: location.trim() || null,
        bullets: bulletLines,
      };
      await api.createFact(input);
      toast.success(`Added: ${input.title}`);
      reset();
      onOpenChange(false);
      onCreated();
    } catch (err) {
      toast.error(`Couldn't add the fact: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2">
          <div className="glass max-h-[85vh] overflow-y-auto rounded-[var(--radius-card)] p-6">
            <div className="flex items-start justify-between">
              <div>
                <Dialog.Title className="text-lg font-medium">Add a fact</Dialog.Title>
                <Dialog.Description className="text-sm text-[color:var(--color-text-muted)]">
                  Add a single piece of evidence to your profile without re-uploading a resume.
                </Dialog.Description>
              </div>
              <Dialog.Close
                aria-label="Close"
                className="grid size-8 place-items-center rounded-md text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
              >
                <X className="size-4" aria-hidden="true" />
              </Dialog.Close>
            </div>

            <form onSubmit={onSubmit} className="mt-4 flex flex-col gap-3">
              <label
                htmlFor="fact-kind"
                className="text-xs font-medium text-[color:var(--color-text-muted)]"
              >
                Kind
              </label>
              <select
                id="fact-kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as FactKind)}
                className="field-control"
              >
                {KIND_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>

              <label
                htmlFor="fact-title"
                className="text-xs font-medium text-[color:var(--color-text-muted)]"
              >
                Title
              </label>
              <input
                id="fact-title"
                type="text"
                placeholder={
                  isSkill ? "e.g. Python" : "e.g. Senior Software Engineer"
                }
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                autoFocus
                required
                className="field-control"
              />

              <label
                htmlFor="fact-org"
                className="text-xs font-medium text-[color:var(--color-text-muted)]"
              >
                {isSkill ? "Category (optional)" : "Organization (optional)"}
              </label>
              <input
                id="fact-org"
                type="text"
                placeholder={
                  isSkill ? "e.g. Languages" : "e.g. Anthropic"
                }
                value={org}
                onChange={(e) => setOrg(e.target.value)}
                className="field-control"
              />

              <label
                htmlFor="fact-location"
                className="text-xs font-medium text-[color:var(--color-text-muted)]"
              >
                Location (optional)
              </label>
              <input
                id="fact-location"
                type="text"
                placeholder="e.g. Boston, MA"
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                className="field-control"
              />

              {!isSkill && (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-3">
                      <label
                        htmlFor="fact-start"
                        className="text-xs font-medium text-[color:var(--color-text-muted)]"
                      >
                        Start (optional)
                      </label>
                      <input
                        id="fact-start"
                        type="month"
                        value={startDate}
                        onChange={(e) => setStartDate(e.target.value)}
                        className="field-control"
                      />
                    </div>
                    <div className="flex flex-col gap-3">
                      <label
                        htmlFor="fact-end"
                        className="text-xs font-medium text-[color:var(--color-text-muted)]"
                      >
                        End (optional)
                      </label>
                      <input
                        id="fact-end"
                        type="month"
                        value={endDate}
                        onChange={(e) => setEndDate(e.target.value)}
                        className="field-control"
                      />
                    </div>
                  </div>

                  <label
                    htmlFor="fact-bullets"
                    className="text-xs font-medium text-[color:var(--color-text-muted)]"
                  >
                    Bullets (optional, one per line)
                  </label>
                  <textarea
                    id="fact-bullets"
                    placeholder={"Shipped X that improved Y by Z%\nLed a team of N engineers"}
                    value={bullets}
                    onChange={(e) => setBullets(e.target.value)}
                    rows={5}
                    className="field-control min-h-24 resize-y"
                  />
                </>
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
                  {loading ? "Saving…" : "Add fact"}
                </button>
              </div>
            </form>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
