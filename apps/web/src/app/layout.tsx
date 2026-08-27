import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import { Manrope } from "next/font/google";
import { clerkLocalization } from "@/components/auth-shell";
import { themeInit } from "@/lib/theme-init";
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
