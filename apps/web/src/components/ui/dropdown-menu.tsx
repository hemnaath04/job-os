"use client";

import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Themed Radix dropdown, styled to match the Select panel so the app has one
 * popover vocabulary rather than two. Used for the row of actions that would
 * otherwise be a row of buttons competing with the content they act on.
 */
export const DropdownMenu = DropdownPrimitive.Root;
export const DropdownMenuTrigger = DropdownPrimitive.Trigger;

export function DropdownMenuContent({
  className,
  align = "end",
  sideOffset = 6,
  children,
  ...props
}: ComponentPropsWithoutRef<typeof DropdownPrimitive.Content>) {
  return (
    <DropdownPrimitive.Portal>
      <DropdownPrimitive.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "z-50 min-w-44 overflow-hidden rounded-[var(--radius-card)] p-1.5",
          "border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)]",
          "shadow-[var(--shadow-glass-hover)]",
          "select-panel",
          className,
        )}
        {...props}
      >
        {children}
      </DropdownPrimitive.Content>
    </DropdownPrimitive.Portal>
  );
}

export function DropdownMenuItem({
  className,
  icon,
  destructive = false,
  children,
  ...props
}: ComponentPropsWithoutRef<typeof DropdownPrimitive.Item> & {
  icon?: ReactNode;
  destructive?: boolean;
}) {
  return (
    <DropdownPrimitive.Item
      className={cn(
        "flex cursor-pointer select-none items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5",
        "text-sm outline-none transition-colors duration-100",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        destructive
          ? "text-[color:var(--color-rose-ink)] data-[highlighted]:bg-[color:var(--color-rose)]/12"
          : "text-[color:var(--color-text)] data-[highlighted]:bg-[color:var(--color-accent-soft)]",
        className,
      )}
      {...props}
    >
      {icon && <span className="shrink-0 opacity-70">{icon}</span>}
      {children}
    </DropdownPrimitive.Item>
  );
}

export function DropdownMenuSeparator() {
  return <DropdownPrimitive.Separator className="my-1 h-px bg-[color:var(--color-border)]" />;
}

export function DropdownMenuLabel({ children }: { children: ReactNode }) {
  return (
    <DropdownPrimitive.Label className="px-2.5 py-1 text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
      {children}
    </DropdownPrimitive.Label>
  );
}
