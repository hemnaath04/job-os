import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import { Manrope } from "next/font/google";
import "./globals.css";

const manrope = Manrope({
  subsets: ["latin"],
  variable: "--font-manrope",
  display: "swap",
});

export const metadata: Metadata = {
  title: "job.os — personal job-search OS",
  description: "Track applications, tailor resumes, never lie on your CV.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider
      appearance={{
        variables: {
          colorPrimary: "#9AA7FF",
          colorBackground: "#101215",
          colorInputBackground: "#191C22",
          colorInputText: "#F1EEE8",
          colorText: "#F1EEE8",
          colorTextSecondary: "#A9AAA6",
          colorNeutral: "#F1EEE8",
          borderRadius: "0.75rem",
        },
      }}
    >
      <html lang="en" suppressHydrationWarning className={manrope.variable}>
        <body className="min-h-screen antialiased">{children}</body>
      </html>
    </ClerkProvider>
  );
}
