"use client";

import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "@/lib/utils";

export type SelectOption = { value: string; label: string };

// Radix forbids an empty-string Item value; map "" to/from a private sentinel
// so callers can keep using "" for the "none / any / pick one" option.
const EMPTY = " empty";
const toInner = (v: string) => (v === "" ? EMPTY : v);
const fromInner = (v: string) => (v === EMPTY ? "" : v);

/**
 * Themed, accessible dropdown built on Radix Select (keyboard nav, typeahead,
 * ARIA, no native OS popup). Drop-in for the old native <select>: same
 * value / onChange / options contract.
 */
export function Select({
  value,
  onChange,
  options,
  disabled,
  placeholder = "Select",
  className,
  id,
  compact = false,
  "aria-label": ariaLabel,
  "aria-describedby": ariaDescribedBy,
}: {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  id?: string;
  /**
   * Toolbar sizing: a filter pill rather than a form field. `field-control` is
   * a 42px-tall full-width input, which is right for a form and wrong for a
   * row of filters above a dense list -- six of them at form height push the
   * first row of data off the fold.
   */
  compact?: boolean;
  "aria-label"?: string;
  "aria-describedby"?: string;
}) {
  return (
    <SelectPrimitive.Root
      value={toInner(value)}
      onValueChange={(v) => onChange(fromInner(v))}
      disabled={disabled}
    >
      <SelectPrimitive.Trigger
        id={id}
        aria-label={ariaLabel}
        aria-describedby={ariaDescribedBy}
        className={cn(
          "group flex items-center text-left",
          "disabled:cursor-not-allowed disabled:opacity-50",
          compact
            ? cn(
                "h-8 justify-between gap-1.5 rounded-lg px-2.5 text-xs",
                "border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)]",
                "text-[color:var(--color-text-muted)] transition-colors duration-150",
                "hover:border-[color:var(--color-border-strong)] hover:text-[color:var(--color-text)]",
                "data-[state=open]:border-[color:var(--color-accent-border)] data-[state=open]:text-[color:var(--color-text)]",
              )
            : cn(
                "field-control justify-between gap-2",
                "data-[state=open]:border-[color:var(--color-accent-border)] data-[state=open]:shadow-[0_0_0_3px_rgba(255,231,135,0.45)]",
              ),
          className,
        )}
      >
        <SelectPrimitive.Value placeholder={placeholder} />
        <SelectPrimitive.Icon asChild>
          <ChevronDown
            className={cn(
              "shrink-0 text-[color:var(--color-text-dim)] transition-transform duration-[var(--dur)] ease-[var(--ease-out)] group-data-[state=open]:rotate-180",
              compact ? "size-3.5" : "size-4",
            )}
          />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>

      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={6}
          className={cn(
            "z-50 w-[var(--radix-select-trigger-width)] overflow-hidden rounded-[var(--radius-card)]",
            "max-h-[min(20rem,var(--radix-select-content-available-height))]",
            "border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] shadow-[var(--shadow-glass-hover)]",
            // Not data-[state=open]: prefixed. Tailwind can only put a variant
            // on a utility it owns, and this is a plain class, so the prefixed
            // form silently compiles to nothing. Radix does not mount Content
            // until the select is open, so the plain class is already correct.
            "select-panel",
          )}
        >
          <SelectPrimitive.ScrollUpButton className="flex h-6 items-center justify-center text-[color:var(--color-text-dim)]">
            <ChevronUp className="size-4" />
          </SelectPrimitive.ScrollUpButton>

          <SelectPrimitive.Viewport className="p-1.5">
            {options.map((o, index) => (
              <SelectPrimitive.Item
                key={o.value}
                value={toInner(o.value)}
                // Position in the list, read by .select-option to stagger the
                // entrance. Capped so a long list does not make the last option
                // wait, and so scrolling a 50-option select is not a slideshow.
                style={
                  { "--select-index": Math.min(index, 8) } as React.CSSProperties
                }
                className={cn(
                  "select-option relative flex cursor-pointer select-none items-center rounded-[var(--radius-control)] py-2 pl-8 pr-3 text-sm",
                  "text-[color:var(--color-text)] outline-none transition-colors",
                  "data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
                  "data-[highlighted]:bg-[color:var(--color-accent-soft)] data-[highlighted]:text-[color:var(--color-text)]",
                  "data-[state=checked]:font-semibold",
                )}
              >
                <SelectPrimitive.ItemIndicator className="absolute left-2.5 inline-flex items-center">
                  <Check className="size-4 text-[color:var(--color-accent-ink)]" />
                </SelectPrimitive.ItemIndicator>
                <SelectPrimitive.ItemText>{o.label}</SelectPrimitive.ItemText>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>

          <SelectPrimitive.ScrollDownButton className="flex h-6 items-center justify-center text-[color:var(--color-text-dim)]">
            <ChevronDown className="size-4" />
          </SelectPrimitive.ScrollDownButton>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}
