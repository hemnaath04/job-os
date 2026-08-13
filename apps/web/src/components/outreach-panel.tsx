"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Check, Copy, Loader2, Send, Sparkles, Trash2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { reportFailure } from "@/lib/errors";
import type {
  Application,
  ContactRelationship,
  OutreachContact,
  OutreachDraft,
  OutreachVariant,
} from "@/lib/types";

/**
 * Outreach for one application: who to write to, what to say, and what already
 * went out.
 *
 * Two things in here are deliberate rather than decorative. The provenance list
 * under a draft is always shown, because a message the user cannot trace is a
 * message they have to re-check by hand before sending. And "shared school" and
 * "shared employer" are labelled as claims that only get used when the verified
 * profile backs them, because a user who types Stripe into that box and sees it
 * silently ignored would otherwise assume the feature is broken.
 */

const VARIANTS: { value: OutreachVariant; label: string; hint: string }[] = [
  {
    value: "cold_hiring_manager",
    label: "Cold, hiring manager",
    hint: "They own the decision and did not ask to hear from you.",
  },
  {
    value: "referral_ask",
    label: "Referral ask",
    hint: "An engineer who cannot hire you but can refer you.",
  },
  {
    value: "alumni",
    label: "Alumni",
    hint: "Needs a school on the contact that matches your verified profile.",
  },
  {
    value: "post_application_followup",
    label: "Follow up on the application",
    hint: "Already applied. Add one new thing, then ask about timing.",
  },
];

const RELATIONSHIPS: { value: ContactRelationship; label: string }[] = [
  { value: "hiring_manager", label: "Hiring manager" },
  { value: "recruiter", label: "Recruiter" },
  { value: "engineer", label: "Engineer" },
  { value: "alumni", label: "Alumni" },
  { value: "other", label: "Other" },
];

export function OutreachPanel({
  application,
  open,
  onOpenChange,
}: {
  application: Application;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [variant, setVariant] = useState<OutreachVariant>("cold_hiring_manager");
  const [note, setNote] = useState("");
  const [draft, setDraft] = useState<OutreachDraft | null>(null);
  const [copied, setCopied] = useState(false);

  const contactsKey = ["outreach-contacts", application.id];
  const historyKey = ["outreach-history", application.id];

  const { data: contacts = [], isLoading } = useQuery({
    queryKey: contactsKey,
    queryFn: () => api.listOutreachContacts(application.id),
    enabled: open,
  });
  const { data: history = [] } = useQuery({
    queryKey: historyKey,
    queryFn: () => api.outreachHistory(application.id),
    enabled: open,
  });

  const selected = contacts.find((contact) => contact.id === selectedId) ?? null;

  const drafting = useMutation({
    mutationFn: () =>
      api.draftOutreach(selected!.id, { variant, note: note.trim() || undefined }),
    onSuccess: (result) => {
      setDraft(result);
      setCopied(false);
      queryClient.invalidateQueries({ queryKey: historyKey });
    },
    onError: (error) => reportFailure("draft that message", error),
  });

  const logging = useMutation({
    mutationFn: () =>
      api.logOutreachSent(selected!.id, {
        variant,
        channel: selected?.email ? "email" : "linkedin",
        subject: draft?.subject,
        body: draft?.body,
      }),
    onSuccess: () => {
      toast.success("Logged as sent", {
        description: draft?.follow_up.label ?? undefined,
      });
      queryClient.invalidateQueries({ queryKey: contactsKey });
      queryClient.invalidateQueries({ queryKey: historyKey });
    },
    onError: (error) => reportFailure("log that message as sent", error),
  });

  const removing = useMutation({
    mutationFn: (contactId: string) => api.deleteOutreachContact(contactId),
    onSuccess: () => {
      setSelectedId(null);
      setDraft(null);
      queryClient.invalidateQueries({ queryKey: contactsKey });
    },
    onError: (error) => reportFailure("remove that contact", error),
  });

  async function copyDraft() {
    if (!draft) return;
    await navigator.clipboard.writeText(`${draft.subject}\n\n${draft.body}`);
    setCopied(true);
    toast.success("Copied. Paste it into your own client and send it there.");
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-full max-w-3xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto">
          <div className="glass rounded-[var(--radius-card)] p-6">
            <div className="flex items-start justify-between">
              <div>
                <Dialog.Title className="text-lg font-medium">Outreach</Dialog.Title>
                <Dialog.Description className="text-sm text-[color:var(--color-text-muted)]">
                  {application.job.title}
                  {application.job.company?.name ? ` at ${application.job.company.name}` : ""}
                </Dialog.Description>
              </div>
              <Dialog.Close
                aria-label="Close"
                className="grid size-8 place-items-center rounded-md text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
              >
                <X className="size-4" aria-hidden="true" />
              </Dialog.Close>
            </div>

            <section className="mt-5">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-muted)]">
                People
              </h3>
              {isLoading ? (
                <div className="loading-surface mt-2 h-16" />
              ) : contacts.length === 0 ? (
                <p className="mt-2 text-sm text-[color:var(--color-text-muted)]">
                  Nobody yet. Find a name on the company&apos;s own team page or the
                  posting, then add them below. Nothing here searches for people.
                </p>
              ) : (
                <ul className="mt-2 flex flex-col gap-1.5">
                  {contacts.map((contact) => (
                    <ContactRow
                      key={contact.id}
                      contact={contact}
                      active={contact.id === selectedId}
                      onSelect={() => {
                        setSelectedId(contact.id);
                        setDraft(null);
                      }}
                      onRemove={() => removing.mutate(contact.id)}
                    />
                  ))}
                </ul>
              )}
            </section>

            <AddContactForm
              applicationId={application.id}
              onAdded={(contact) => {
                queryClient.invalidateQueries({ queryKey: contactsKey });
                setSelectedId(contact.id);
                setDraft(null);
              }}
            />

            {selected && (
              <section className="mt-6 border-t border-[color:var(--color-border)] pt-5">
                <h3 className="text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-muted)]">
                  Draft to {selected.full_name}
                </h3>

                <label
                  htmlFor="outreach-variant"
                  className="mt-3 block text-xs font-medium text-[color:var(--color-text-muted)]"
                >
                  Kind of message
                </label>
                <select
                  id="outreach-variant"
                  value={variant}
                  onChange={(event) => setVariant(event.target.value as OutreachVariant)}
                  className="field-control mt-1"
                >
                  {VARIANTS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-[color:var(--color-text-muted)]">
                  {VARIANTS.find((option) => option.value === variant)?.hint}
                </p>

                <label
                  htmlFor="outreach-note"
                  className="mt-3 block text-xs font-medium text-[color:var(--color-text-muted)]"
                >
                  Anything to steer it (optional)
                </label>
                <input
                  id="outreach-note"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="They spoke at the Boston Go meetup last month"
                  className="field-control mt-1"
                />
                <p className="mt-1 text-xs text-[color:var(--color-text-muted)]">
                  Context only. It is not evidence, so nothing in the message can rest
                  on it.
                </p>

                {selected.do_not_contact && (
                  <p className="mt-3 rounded-md bg-[color:var(--color-rose)]/12 p-2 text-xs text-[color:var(--color-rose-ink)]">
                    Marked do not contact. Nothing can be drafted or logged for them.
                  </p>
                )}

                <div className="mt-4 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => drafting.mutate()}
                    disabled={drafting.isPending || selected.do_not_contact}
                    className="product-button product-button-primary disabled:opacity-50"
                  >
                    {drafting.isPending ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Sparkles className="size-3.5" />
                    )}
                    {drafting.isPending ? "Drafting…" : "Draft a message"}
                  </button>
                  {selected.messages_sent > 0 && (
                    <span className="text-xs text-[color:var(--color-text-muted)]">
                      {selected.messages_sent} already sent
                      {selected.last_sent_at
                        ? `, last ${formatDistanceToNow(new Date(selected.last_sent_at), {
                            addSuffix: true,
                          })}`
                        : ""}
                    </span>
                  )}
                </div>

                {draft && (
                  <DraftView
                    draft={draft}
                    copied={copied}
                    onCopy={copyDraft}
                    onLogSent={() => logging.mutate()}
                    logging={logging.isPending}
                  />
                )}
              </section>
            )}

            {history.length > 0 && (
              <section className="mt-6 border-t border-[color:var(--color-border)] pt-5">
                <h3 className="text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-muted)]">
                  History
                </h3>
                <ul className="mt-2 flex flex-col gap-1">
                  {history.map((row, index) => (
                    <li
                      key={`${row.occurred_at}-${index}`}
                      className="flex items-baseline justify-between gap-3 text-xs text-[color:var(--color-text-muted)]"
                    >
                      <span>
                        <span
                          className={
                            row.kind === "outreach_sent"
                              ? "font-medium text-[color:var(--color-text)]"
                              : ""
                          }
                        >
                          {row.kind === "outreach_sent" ? "Sent" : "Drafted"}
                        </span>{" "}
                        {row.variant?.replace(/_/g, " ")} to {row.contact_name ?? "someone"}
                      </span>
                      <span className="shrink-0">
                        {formatDistanceToNow(new Date(row.occurred_at), { addSuffix: true })}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ContactRow({
  contact,
  active,
  onSelect,
  onRemove,
}: {
  contact: OutreachContact;
  active: boolean;
  onSelect: () => void;
  onRemove: () => void;
}) {
  return (
    <li>
      <div
        className={
          "flex items-center justify-between gap-2 rounded-lg border px-3 py-2 transition " +
          (active
            ? "border-[color:var(--color-violet)]/40 bg-[color:var(--color-surface-2)]"
            : "border-[color:var(--color-border)] hover:bg-[color:var(--color-surface-hover)]")
        }
      >
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={active}
          className="flex-1 text-left"
        >
          <span className="text-sm font-medium">{contact.full_name}</span>
          {contact.title && (
            <span className="text-sm text-[color:var(--color-text-muted)]">
              {" "}
              · {contact.title}
            </span>
          )}
          <span className="block text-xs text-[color:var(--color-text-muted)]">
            {contact.email ?? contact.linkedin_url ?? "No address yet"}
            {/* Said out loud rather than implied. An address the user found and
                one a provider guessed carry different odds of bouncing. */}
            {contact.email_source === "user_provided" && " · you found this"}
            {contact.email_source === "provider_inferred" &&
              ` · guessed from a domain pattern${
                contact.confidence ? ` (${contact.confidence}% confident)` : ""
              }`}
            {contact.messages_sent > 0 && ` · ${contact.messages_sent} sent`}
          </span>
        </button>
        <button
          type="button"
          onClick={onRemove}
          title="Remove contact"
          aria-label={`Remove ${contact.full_name}`}
          className="inline-flex items-center justify-center rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-1 text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-rose)]/12 hover:text-[color:var(--color-rose-ink)]"
        >
          <Trash2 className="size-3" />
        </button>
      </div>
    </li>
  );
}

function AddContactForm({
  applicationId,
  onAdded,
}: {
  applicationId: string;
  onAdded: (contact: OutreachContact) => void;
}) {
  const [fullName, setFullName] = useState("");
  const [title, setTitle] = useState("");
  const [email, setEmail] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [relationship, setRelationship] = useState<ContactRelationship>("hiring_manager");
  const [sharedSchool, setSharedSchool] = useState("");
  const [sharedEmployer, setSharedEmployer] = useState("");
  const [referredBy, setReferredBy] = useState("");
  const [saving, setSaving] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!fullName.trim()) return;
    setSaving(true);
    try {
      const contact = await api.addOutreachContact(applicationId, {
        full_name: fullName.trim(),
        title: title.trim() || null,
        email: email.trim() || null,
        linkedin_url: linkedin.trim() || null,
        evidence_url: evidenceUrl.trim() || null,
        relationship_kind: relationship,
        shared_school: sharedSchool.trim() || null,
        shared_employer: sharedEmployer.trim() || null,
        referred_by: referredBy.trim() || null,
      });
      setFullName("");
      setTitle("");
      setEmail("");
      setLinkedin("");
      setEvidenceUrl("");
      setSharedSchool("");
      setSharedEmployer("");
      setReferredBy("");
      onAdded(contact);
    } catch (error) {
      reportFailure("add that contact", error);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mt-5 border-t border-[color:var(--color-border)] pt-5">
      <h3 className="text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-muted)]">
        Add someone
      </h3>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <Field label="Name" id="contact-name">
          <input
            id="contact-name"
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            placeholder="Priya Raman"
            className="field-control"
          />
        </Field>
        <Field label="Title" id="contact-title">
          <input
            id="contact-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Engineering Manager, Payments"
            className="field-control"
          />
        </Field>
        <Field label="Email" id="contact-email">
          <input
            id="contact-email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="priya@example.com"
            className="field-control"
          />
        </Field>
        <Field label="LinkedIn" id="contact-linkedin">
          <input
            id="contact-linkedin"
            value={linkedin}
            onChange={(event) => setLinkedin(event.target.value)}
            placeholder="https://www.linkedin.com/in/…"
            className="field-control"
          />
        </Field>
        <Field label="Where you found them" id="contact-evidence">
          <input
            id="contact-evidence"
            value={evidenceUrl}
            onChange={(event) => setEvidenceUrl(event.target.value)}
            placeholder="https://company.com/team"
            className="field-control"
          />
        </Field>
        <Field label="Relationship" id="contact-relationship">
          <select
            id="contact-relationship"
            value={relationship}
            onChange={(event) =>
              setRelationship(event.target.value as ContactRelationship)
            }
            className="field-control"
          >
            {RELATIONSHIPS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <p className="mt-3 text-xs text-[color:var(--color-text-muted)]">
        The three below are used only when your own verified profile backs them. If
        you record a school they went to and nothing in your profile matches it, the
        message will not mention it, on purpose.
      </p>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        <Field label="School you share" id="contact-school">
          <input
            id="contact-school"
            value={sharedSchool}
            onChange={(event) => setSharedSchool(event.target.value)}
            className="field-control"
          />
        </Field>
        <Field label="Employer you share" id="contact-employer">
          <input
            id="contact-employer"
            value={sharedEmployer}
            onChange={(event) => setSharedEmployer(event.target.value)}
            className="field-control"
          />
        </Field>
        <Field label="Who referred you" id="contact-referrer">
          <input
            id="contact-referrer"
            value={referredBy}
            onChange={(event) => setReferredBy(event.target.value)}
            className="field-control"
          />
        </Field>
      </div>

      <div className="mt-3 flex justify-end">
        <button
          type="submit"
          disabled={saving || !fullName.trim()}
          className="product-button product-button-secondary disabled:opacity-50"
        >
          {saving && <Loader2 className="size-3.5 animate-spin" />}
          {saving ? "Saving…" : "Add contact"}
        </button>
      </div>
    </form>
  );
}

function DraftView({
  draft,
  copied,
  onCopy,
  onLogSent,
  logging,
}: {
  draft: OutreachDraft;
  copied: boolean;
  onCopy: () => void;
  onLogSent: () => void;
  logging: boolean;
}) {
  return (
    <div className="mt-4 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-4">
      <p className="text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-muted)]">
        Subject
      </p>
      <p className="mt-1 text-sm font-medium">{draft.subject}</p>

      <p className="mt-3 text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-muted)]">
        Message
      </p>
      <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">{draft.body}</p>
      <p className="mt-2 text-xs text-[color:var(--color-text-muted)]">
        {draft.word_count} words, cap {draft.word_cap}
      </p>

      {draft.warnings.map((warning) => (
        <p
          key={warning}
          className="mt-2 rounded-md bg-[color:var(--color-amber)]/12 p-2 text-xs text-[color:var(--color-text)]"
        >
          {warning}
        </p>
      ))}

      {draft.note && (
        <p className="mt-2 text-xs italic text-[color:var(--color-text-muted)]">
          {draft.note}
        </p>
      )}

      {/* Always visible. A claim the user cannot trace back to a verified row is
          one they have to re-check by hand before they dare send it. */}
      <details className="mt-3" open>
        <summary className="cursor-pointer text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-muted)]">
          What backs this ({draft.provenance.length})
        </summary>
        <ul className="mt-2 flex flex-col gap-2">
          {draft.provenance.map((row) => (
            <li key={`${row.evidence_id}-${row.phrase}`} className="text-xs">
              <span className="font-medium text-[color:var(--color-text)]">
                “{row.phrase}”
              </span>
              <span className="block text-[color:var(--color-text-muted)]">
                {row.evidence_kind}: {row.evidence_text}
              </span>
            </li>
          ))}
        </ul>
        {draft.shared_context_used.length > 0 && (
          <ul className="mt-2 flex flex-col gap-1">
            {draft.shared_context_used.map((entry) => (
              <li key={entry.id} className="text-xs text-[color:var(--color-text-muted)]">
                Common ground used: {entry.claim}
              </li>
            ))}
          </ul>
        )}
      </details>

      <p className="mt-3 text-xs text-[color:var(--color-amber)]">
        {draft.follow_up.label}
        {draft.follow_up.suggested_at
          ? `, around ${new Date(draft.follow_up.suggested_at).toLocaleDateString()}`
          : ""}
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button type="button" onClick={onCopy} className="product-button product-button-secondary">
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          type="button"
          onClick={onLogSent}
          disabled={logging}
          className="product-button product-button-primary disabled:opacity-50"
        >
          {logging ? <Loader2 className="size-3.5 animate-spin" /> : <Send className="size-3.5" />}
          I sent this
        </button>
        <span className="text-xs text-[color:var(--color-text-muted)]">
          Nothing is sent from here. Send it yourself, then log it so you do not
          write to them twice.
        </span>
      </div>
    </div>
  );
}

function Field({
  label,
  id,
  children,
}: {
  label: string;
  id: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-[color:var(--color-text-muted)]">
        {label}
      </label>
      {children}
    </div>
  );
}
