"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, Check, Columns2, ExternalLink, Maximize2, X } from "lucide-react";
import { useState } from "react";
import { TemplatePreview, useStoredFileUrl } from "@/components/template-preview";
import type { ResumeTemplate } from "@/lib/types";

/**
 * Pick the look a resume renders with, from previews rather than from names.
 *
 * The card shows the real sample render, and the sample render is produced by
 * the same code path that will render the user's resume, so what they choose
 * from is what they get. Two of the seven are two-column designs that some
 * applicant tracking systems parse badly; the card says so rather than leaving
 * somebody to find out from a silent rejection.
 */
export function TemplatePicker({
  templates,
  value,
  onChange,
  onRemove,
  removingId,
  selectable = true,
}: {
  templates: ResumeTemplate[];
  /** Empty string means the app's default, which is Jake's Resume. */
  value: string;
  onChange: (templateId: string) => void;
  /** Omit to hide removal. Builtins are never removable. */
  onRemove?: (templateId: string) => void;
  removingId?: string | null;
  /**
   * False on the library page, where there is nothing to select: choosing a look
   * belongs to the run that uses it. The card then opens the full render instead
   * of looking clickable and doing nothing.
   */
  selectable?: boolean;
}) {
  const [previewing, setPreviewing] = useState<ResumeTemplate | null>(null);

  if (templates.length === 0) {
    return (
      <div className="workspace-panel px-5 py-4 text-xs text-[color:var(--color-text-dim)]">
        No templates are available yet. Run the seeding script to add the seven
        that ship with the app.
      </div>
    );
  }

  return (
    <>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {templates.map((template) => {
          const selected = template.id === value;
          return (
            <li key={template.id}>
              <div
                className={`group relative flex h-full flex-col overflow-hidden rounded-[var(--radius-card)] border transition ${
                  selected
                    ? "border-[color:var(--color-violet)] ring-1 ring-[color:var(--color-violet)]"
                    : "border-[color:var(--color-border)] hover:border-[color:var(--color-text-dim)]"
                }`}
              >
                <button
                  type="button"
                  onClick={() =>
                    selectable
                      ? onChange(selected ? "" : template.id)
                      : setPreviewing(template)
                  }
                  aria-pressed={selectable ? selected : undefined}
                  aria-label={
                    selectable
                      ? `Use the ${template.name} template`
                      : `View the ${template.name} sample render`
                  }
                  className="block w-full cursor-pointer bg-white text-left"
                >
                  <span className="block aspect-[8.5/11] w-full overflow-hidden">
                    <TemplatePreview template={template} />
                  </span>
                </button>

                {selected && (
                  <span className="pointer-events-none absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-[color:var(--color-violet)] px-2 py-0.5 text-[10px] font-medium text-white">
                    <Check className="size-3" aria-hidden="true" /> Selected
                  </span>
                )}

                <button
                  type="button"
                  onClick={() => setPreviewing(template)}
                  className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface)]/90 px-2 py-0.5 text-[10px] text-[color:var(--color-text-muted)] opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100 hover:text-[color:var(--color-text)]"
                >
                  <Maximize2 className="size-3" aria-hidden="true" /> Full size
                </button>

                <div className="flex flex-1 flex-col gap-1 px-3 py-2">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-xs font-semibold leading-4">{template.name}</span>
                    {template.kind === "custom" ? (
                      <span className="shrink-0 rounded-full bg-[color:var(--color-surface-2)] px-1.5 py-0.5 text-[10px] text-[color:var(--color-text-dim)]">
                        Yours
                      </span>
                    ) : null}
                  </div>
                  {template.columns === 2 && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-[color:var(--color-amber-ink,var(--color-text-muted))]">
                      <Columns2 className="size-3" aria-hidden="true" /> Two column
                    </span>
                  )}
                  {onRemove && template.kind === "custom" && (
                    <button
                      type="button"
                      onClick={() => onRemove(template.id)}
                      disabled={removingId === template.id}
                      className="mt-auto self-start text-[10px] text-[color:var(--color-text-dim)] underline decoration-dotted transition hover:text-[color:var(--color-rose-ink)] disabled:opacity-50"
                    >
                      Remove
                    </button>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <TemplateDetailDialog
        template={previewing}
        onOpenChange={(open) => !open && setPreviewing(null)}
      />
    </>
  );
}

/** The full sample render, plus where the design came from and what it costs. */
export function TemplateDetailDialog({
  template,
  onOpenChange,
}: {
  template: ResumeTemplate | null;
  onOpenChange: (open: boolean) => void;
}) {
  const pdf = useStoredFileUrl(template?.preview_pdf_file_id);

  return (
    <Dialog.Root open={!!template} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex h-[90vh] w-full max-w-5xl -translate-x-1/2 -translate-y-1/2 flex-col">
          {template && (
            <div className="glass flex min-h-0 flex-1 flex-col rounded-[var(--radius-card)] p-5">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <Dialog.Title className="text-lg font-medium">{template.name}</Dialog.Title>
                  <Dialog.Description className="mt-1 text-sm text-[color:var(--color-text-muted)]">
                    {template.description ||
                      "A template built from your own upload."}
                  </Dialog.Description>
                </div>
                <Dialog.Close
                  aria-label="Close"
                  className="grid size-8 shrink-0 place-items-center rounded-md text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
                >
                  <X className="size-4" aria-hidden="true" />
                </Dialog.Close>
              </div>

              {template.ats_note && (
                <p className="mt-3 flex items-start gap-2 rounded-lg bg-[color:var(--color-surface-2)] px-3 py-2 text-xs leading-5 text-[color:var(--color-text-muted)]">
                  <AlertTriangle
                    className="mt-0.5 size-3.5 shrink-0 text-[color:var(--color-text-dim)]"
                    aria-hidden="true"
                  />
                  {template.ats_note}
                </p>
              )}

              <div className="mt-3 min-h-0 flex-1 overflow-hidden rounded-lg border border-[color:var(--color-border)] bg-white">
                {pdf.url ? (
                  <iframe
                    src={pdf.url}
                    title={`${template.name}, rendered with sample data`}
                    className="h-full w-full border-0"
                  />
                ) : (
                  <div className="grid h-full place-items-center text-xs text-[color:var(--color-text-dim)]">
                    {pdf.failed
                      ? "The stored sample render could not be loaded."
                      : "Loading the sample render…"}
                  </div>
                )}
              </div>

              <p className="mt-3 text-[11px] leading-5 text-[color:var(--color-text-dim)]">
                Rendered from invented sample data, not your resume.
                {template.author ? ` Design by ${template.author}.` : ""}
                {template.licence ? ` Licensed ${template.licence}.` : ""}
                {template.upstream ? (
                  <>
                    {" "}
                    <a
                      href={template.upstream}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-flex items-center gap-1 underline decoration-dotted hover:text-[color:var(--color-text-muted)]"
                    >
                      Source <ExternalLink className="size-3" aria-hidden="true" />
                    </a>
                  </>
                ) : null}
                {template.changes ? ` Adapted here: ${template.changes}` : ""}
                {template.notes ? ` Notes from the build: ${template.notes}` : ""}
              </p>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
