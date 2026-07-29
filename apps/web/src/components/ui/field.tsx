"use client";

import { useId } from "react";

/**
 * Props a `Field` hands to its control so the visible label actually names it.
 * Spread them onto the input, textarea, or Select inside the field.
 */
export type FieldControlProps = {
  id: string;
  "aria-describedby": string | undefined;
};

/**
 * A labelled form row. The label is bound to the control with `htmlFor`, and
 * the help text is bound with `aria-describedby`, so the control has a real
 * accessible name and its hint is read out with it. Children are a function so
 * the generated ids reach the control without every caller inventing one.
 */
export function Field({
  label,
  help,
  className,
  children,
}: {
  label: string;
  help?: string;
  className?: string;
  children: (control: FieldControlProps) => React.ReactNode;
}) {
  const generated = useId();
  const controlId = `${generated}-control`;
  const helpId = help ? `${generated}-help` : undefined;

  return (
    <div className={className}>
      <label htmlFor={controlId} className="text-sm font-medium">
        {label}
      </label>
      {help && (
        <p id={helpId} className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">
          {help}
        </p>
      )}
      <div className="mt-2">{children({ id: controlId, "aria-describedby": helpId })}</div>
    </div>
  );
}

/**
 * The same row for a set of controls rather than one, for example a row of
 * toggle buttons. A `<label>` can only name a single form control, so the group
 * is named with `role="group"` plus `aria-labelledby` instead.
 */
export function FieldGroup({
  label,
  help,
  className,
  children,
}: {
  label: string;
  help?: string;
  className?: string;
  children: React.ReactNode;
}) {
  const generated = useId();
  const labelId = `${generated}-label`;
  const helpId = help ? `${generated}-help` : undefined;

  return (
    <div className={className}>
      <span id={labelId} className="block text-sm font-medium">
        {label}
      </span>
      {help && (
        <p id={helpId} className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">
          {help}
        </p>
      )}
      <div role="group" aria-labelledby={labelId} aria-describedby={helpId} className="mt-2">
        {children}
      </div>
    </div>
  );
}
