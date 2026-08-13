/**
 * What an ATS adapter has to provide.
 *
 * The shape is deliberately thin. An adapter says where the form is and, if it
 * knows something the generic resolver cannot work out, offers a hint. It never
 * decides what to fill and never touches the DOM, so adding an ATS cannot
 * introduce a way around the safety invariants: the planner and the filler are
 * the same code for every adapter.
 *
 * Adding one is a single file plus a line in registry.ts.
 */
import type { AtsId, FieldKey, RawField } from "../core/types.ts";

export interface DetectContext {
  readonly url: URL;
  readonly document: Document;
}

export interface AtsAdapter {
  readonly id: AtsId;
  /** Shown in the review panel header. */
  readonly label: string;

  /**
   * Is this page an application form for this ATS? Must be cheap and must not
   * modify anything. Prefer a host match plus one DOM signature, so a job
   * board that merely links to Greenhouse does not match.
   */
  detect(ctx: DetectContext): boolean;

  /**
   * The container(s) holding the application fields. Narrowing here keeps the
   * site's own search box and newsletter signup out of the plan.
   */
  formRoots(doc: Document): Element[];

  /**
   * Every candidate control inside a root. The default in `collectStandard`
   * covers native controls; override only when the ATS builds custom widgets.
   */
  collectFields(root: Element): RawField[];

  /**
   * A canonical key this adapter knows from a stable attribute, when the
   * generic label resolver would not get there. Return null to defer.
   * Hints are still subject to the free-text gate.
   */
  fieldKeyHint?(field: RawField): FieldKey | null;
}
