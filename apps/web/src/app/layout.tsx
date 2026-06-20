import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

export const metadata: Metadata = {
  title: "job.os — personal job-search OS",
  description: "Track applications, tailor resumes, never lie on your CV.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider
      appearance={{
        variables: {
          colorPrimary: "#7C5CFF",
          colorBackground: "#0A0A0F",
          colorInputBackground: "#13131B",
          colorInputText: "#F5F5FA",
          colorText: "#F5F5FA",
          colorTextSecondary: "#A1A1AE",
          colorNeutral: "#F5F5FA",
          borderRadius: "0.75rem",
        },
      }}
    >
      <html lang="en" suppressHydrationWarning>
        <body className="min-h-screen antialiased">{children}</body>
      </html>
    </ClerkProvider>
  );
}
