import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import { Manrope } from "next/font/google";
import { clerkLocalization } from "@/components/auth-shell";
import "./globals.css";

const manrope = Manrope({
  subsets: ["latin"],
  variable: "--font-manrope",
  display: "swap",
});

export const metadata: Metadata = {
  title: "job.os | personal job-search OS",
  description: "Track applications, tailor resumes, never lie on your CV.",
};

// A stored choice wins; with no stored choice, follow the device. Runs before
// paint, so neither case flashes.
//
// This used to default to light and consult nothing else, so a phone set to
// dark loaded the site light and the browser force-darkened it. That is not a
// neutral transform: it repaints plain colours but cannot touch a
// `background-clip: text` gradient, so the landing headline kept its
// light-theme ink and rendered near-black on the darkened background, which is
// how this was found. The `color-scheme` declarations in globals.css are the
// other half, and the part that tells a browser to stop doing it at all.
const themeInit = `try{var s=localStorage.getItem('theme');if(s==='dark'||(!s&&matchMedia('(prefers-color-scheme: dark)').matches)){document.documentElement.classList.add('dark')}}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={manrope.variable}>
      <body className="min-h-[100dvh] antialiased">
        <ClerkProvider
          localization={clerkLocalization}
          // Tokens, not literals. These were hardcoded light hex values, so every
          // Clerk surface outside the auth pages, the account menu in the sidebar
          // most visibly, stayed white after the theme flipped to dark. Custom
          // properties resolve at paint time and follow the theme for free.
          appearance={{
            variables: {
              colorPrimary: "var(--color-accent-ink)",
              colorPrimaryForeground: "var(--color-on-accent)",
              colorBackground: "var(--color-surface-1)",
              colorInput: "var(--color-surface-2)",
              colorInputForeground: "var(--color-text)",
              colorForeground: "var(--color-text)",
              colorMutedForeground: "var(--color-text-muted)",
              colorNeutral: "var(--color-text)",
              borderRadius: "var(--radius-control)",
            },
          }}
        >
          <script dangerouslySetInnerHTML={{ __html: themeInit }} />
          {children}
        </ClerkProvider>
      </body>
    </html>
  );
}
