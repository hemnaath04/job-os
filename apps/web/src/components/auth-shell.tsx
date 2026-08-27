import { GrainPanel } from "@/components/marketing/grain-panel";
import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/shell/theme-toggle";

/**
 * Two-column frame for the auth routes: the real Clerk widget on one side, a
 * grain-gradient panel carrying the product's claim on the other. Collapses to
 * a single column below lg, where the panel becomes a short banner rather than
 * a wasted half-screen.
 *
 * `marketing-dark` is doing quiet but important work here. It redefines the
 * same custom properties the app themes with, and `clerkAppearance` below is
 * written entirely in var() references, so Clerk's own inputs, dividers and
 * buttons render dark without a second appearance object to maintain.
 *
 * The panel carries the product's own claim and nothing invented. No user
 * count, no testimonials, no stock faces: this is a tool with a handful of
 * accounts on it, and a fabricated number on the sign-in page would be the
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
    <main className="min-h-[100dvh] p-3">
      {/* Full height only where there are two columns to fill. Stacked on a
          phone this forced a viewport-tall grid into two rows, so the brand
          panel took half the screen and the sign-up form was left with less
          room than it needs: the password field sat under the browser chrome
          with nothing to scroll, because the page was exactly one viewport
          tall. Content height on mobile lets it scroll like a page.

          100dvh, not 100vh: on mobile Safari and Brave, 100vh is the viewport
          with the address bar hidden, which is taller than what you can
          actually see. */}
      <div className="grid gap-3 lg:min-h-[calc(100dvh-1.5rem)] lg:grid-cols-[1.02fr_0.98fr]">
        {/* Form column */}
        <section className="flex items-center justify-center rounded-[1.25rem] border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] px-6 py-12 sm:px-10 lg:px-14">
          <div className="w-full max-w-[31rem]">
            <Wordmark />
            <div className="animate-rise-in">{children}</div>
          </div>
        </section>

        {/* Brand panel. Ordered first on small screens so the product says what
            it is before asking for an email. */}
        <GrainPanel className="order-first flex min-h-[11rem] rounded-[1.25rem] p-8 sm:min-h-[15rem] sm:p-10 lg:order-last lg:min-h-0 lg:p-12">
          <div className="relative z-10 flex h-full w-full flex-col justify-between gap-10">
            <h1 className="max-w-[18ch] text-balance text-3xl font-medium leading-[1.05] tracking-[-0.04em] text-white sm:text-4xl lg:text-[3.25rem]">
              {headline}
            </h1>

            <p className="max-w-sm text-pretty text-sm leading-6 text-white/80">
              Track every application, tailor a resume to the role it is for,
              and keep every claim traceable to evidence you control.
            </p>
          </div>
        </GrainPanel>
      </div>
    </main>
  );
}

/** The wordmark above the form. Not a link: there is nowhere better to go from
 *  a half-finished sign-in than the page you are already on. */
function Wordmark() {
  return (
    <div className="mb-10 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2.5">
        <BrandMark className="drop-shadow-[0_12px_16px_rgba(255,231,135,.28)]" />
        <span className="font-mono text-sm tracking-tight text-[color:var(--color-text)]">
          job.os
        </span>
      </div>
      <ThemeToggle />
    </div>
  );
}

/**
 * Clerk appearance mapped onto our tokens.
 *
 * Every value is a CSS custom property rather than a literal, which is the
 * whole point: the previous version hardcoded #FFFFFF and zinc text, so the
 * widget stayed light while the rest of the app went dark. Variables resolve at
 * paint time, so this follows the theme for free, including the dark scope the
 * auth routes render inside.
 */
export const clerkAppearance = {
  variables: {
    colorPrimary: "var(--color-accent-ink)",
    // The button fill is jasmine, so its label has to be the dark ink the rest
    // of the app puts on that colour. Without this Clerk picks white and the
    // Continue button reads as blank: pale yellow on pale yellow.
    colorPrimaryForeground: "var(--color-on-accent)",
    colorBackground: "var(--color-surface-1)",
    colorInputBackground: "var(--color-surface-2)",
    colorInputText: "var(--color-text)",
    colorText: "var(--color-text)",
    colorTextSecondary: "var(--color-text-muted)",
    colorNeutral: "var(--color-text)",
    colorDanger: "var(--color-rose-ink)",
    colorSuccess: "var(--color-mint-ink)",
    colorWarning: "var(--color-amber-ink)",
    borderRadius: "var(--radius-control)",
    fontFamily: "var(--font-sans)",
    // Scale the whole widget up. Clerk sizes itself from these two, and they
    // are the only reliable way in: utility classes passed through `elements`
    // lose to Clerk's own generated CSS, which is why the first attempt at
    // restyling the button did nothing.
    fontSize: "0.95rem",
    spacing: "1.15rem",
  },
  elements: {
    // The widget sits inside our own column, so it brings no card of its own.
    rootBox: "!w-full",
    cardBox: "!w-full !shadow-none !border-0",
    card: "!bg-transparent !shadow-none !border-0 !p-0",
    headerTitle:
      "!text-[color:var(--color-text)] !text-3xl !tracking-[-0.035em] !font-medium",
    headerSubtitle: "text-[color:var(--color-text-muted)]",
    socialButtonsBlockButton:
      "border border-[color:var(--color-border-strong)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text)] hover:bg-[color:var(--color-surface-hover)]",
    dividerLine: "bg-[color:var(--color-border)]",
    dividerText: "text-[color:var(--color-text-dim)]",
    formFieldLabel: "text-[color:var(--color-text)]",
    formFieldInput:
      "field-control placeholder:text-[color:var(--color-text-dim)]",
    // Colour comes from `variables` above, not from here. These only nudge the
    // things Clerk leaves alone, and they carry `!` because a bare utility
    // class loses the specificity fight with Clerk's own stylesheet.
    formButtonPrimary: "!font-semibold !normal-case !tracking-normal",
    footerActionText: "text-[color:var(--color-text-muted)]",
    footerActionLink:
      "text-[color:var(--color-accent-ink)] underline decoration-from-font underline-offset-2",
    identityPreviewText: "text-[color:var(--color-text)]",
    formFieldSuccessText: "text-[color:var(--color-mint-ink)]",
    formFieldErrorText: "text-[color:var(--color-rose-ink)]",
  },
} as const;

/**
 * The widget was greeting people with "Sign in to My Application", which is
 * Clerk's placeholder application name showing through.
 *
 * The real fix is one field in the Clerk dashboard, since `{{applicationName}}`
 * feeds every other surface Clerk renders too, including its emails. This only
 * covers the two headings a visitor actually reads on the way in, so the
 * dashboard field is still worth setting.
 */
export const clerkLocalization = {
  signIn: {
    start: {
      title: "Sign in to job.os",
      subtitle: "Pick up where you left off.",
    },
  },
  signUp: {
    start: {
      title: "Create your job.os account",
      subtitle: "Bring a resume and the evidence behind it.",
    },
  },
};
