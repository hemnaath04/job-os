import { AuroraBackground } from "@/components/aurora-background";
import { BrandMark } from "@/components/brand-mark";

/**
 * Two-column frame for the auth routes: a branded panel on one side and the
 * real Clerk widget on the other. Collapses to a single column below lg, where
 * the brand panel becomes a compact header rather than a wasted half-screen.
 *
 * The panel carries the product's own claim and nothing invented. No user
 * count, no testimonials, no stock faces: this is a personal tool with one
 * account on it, and a fabricated number on the sign-in page would be the
 * first thing it ever said to anyone.
 */
export function AuthShell({
  headline,
  children,
}: {
  headline: string;
  children: React.ReactNode;
}) {
  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      {/* Brand panel */}
      <section className="relative isolate flex flex-col justify-between overflow-hidden border-b border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-6 py-10 lg:border-b-0 lg:border-r lg:px-12 lg:py-14">
        <AuroraBackground />

        <div className="relative flex items-center gap-2.5">
          <BrandMark className="drop-shadow-[0_12px_16px_rgba(233,198,74,.28)]" />
          <span className="font-mono text-sm tracking-tight text-[color:var(--color-text)]">
            job.os
          </span>
        </div>

        <div className="relative mt-8 lg:mt-0">
          <h1 className="max-w-md text-balance text-2xl font-semibold leading-tight tracking-[-0.03em] text-[color:var(--color-text)] lg:text-[2rem]">
            {headline}
          </h1>
          <p className="mt-3 max-w-sm text-pretty text-sm leading-6 text-[color:var(--color-text)]">
            Track every application, tailor a resume to the role it is for, and
            keep every claim traceable to evidence you control.
          </p>
        </div>

        <p className="relative mt-8 text-xs text-[color:var(--color-text-muted)] lg:mt-0">
          Your data stays attached to your own account.
        </p>
      </section>

      {/* Form column: the real Clerk widget goes here. */}
      <section className="flex items-center justify-center px-6 py-12 lg:px-12">
        <div className="w-full max-w-[25rem]">{children}</div>
      </section>
    </main>
  );
}

/**
 * Clerk appearance mapped onto our tokens.
 *
 * Every value is a CSS custom property rather than a literal, which is the
 * whole point: the previous version hardcoded #FFFFFF and zinc text, so the
 * widget stayed light while the rest of the app went dark. Variables resolve at
 * paint time, so this follows the theme for free.
 */
export const clerkAppearance = {
  variables: {
    colorPrimary: "var(--color-accent-ink)",
    colorBackground: "var(--color-surface-1)",
    colorInputBackground: "var(--color-surface-1)",
    colorInputText: "var(--color-text)",
    colorText: "var(--color-text)",
    colorTextSecondary: "var(--color-text-muted)",
    colorNeutral: "var(--color-text)",
    colorDanger: "var(--color-rose-ink)",
    colorSuccess: "var(--color-mint-ink)",
    colorWarning: "var(--color-amber-ink)",
    borderRadius: "var(--radius-control)",
    fontFamily: "var(--font-sans)",
  },
  elements: {
    // The widget sits inside our own column, so it brings no card of its own.
    rootBox: "w-full",
    cardBox: "w-full shadow-none border-0",
    card: "bg-transparent shadow-none border-0 p-0",
    headerTitle: "text-[color:var(--color-text)]",
    headerSubtitle: "text-[color:var(--color-text-muted)]",
    socialButtonsBlockButton:
      "border border-[color:var(--color-border-strong)] bg-[color:var(--color-surface-1)] text-[color:var(--color-text)] hover:bg-[color:var(--color-surface-2)]",
    dividerLine: "bg-[color:var(--color-border)]",
    dividerText: "text-[color:var(--color-text-dim)]",
    formFieldLabel: "text-[color:var(--color-text)]",
    formFieldInput:
      "field-control placeholder:text-[color:var(--color-text-dim)]",
    formButtonPrimary:
      "product-button product-button-primary w-full normal-case tracking-normal",
    footerActionText: "text-[color:var(--color-text-muted)]",
    footerActionLink:
      "text-[color:var(--color-accent-ink)] underline decoration-from-font underline-offset-2",
    identityPreviewText: "text-[color:var(--color-text)]",
    formFieldSuccessText: "text-[color:var(--color-mint-ink)]",
    formFieldErrorText: "text-[color:var(--color-rose-ink)]",
  },
} as const;
