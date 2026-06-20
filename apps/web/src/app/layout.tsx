import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
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
          colorPrimary: "#CCFF00",
          colorBackground: "#000000",
          colorInputBackground: "#0A0A0A",
          colorInputText: "#F5F5F5",
          colorText: "#F5F5F5",
          colorTextSecondary: "#A1A1A1",
          colorNeutral: "#F5F5F5",
          borderRadius: "0.75rem",
        },
      }}
    >
      <html lang="en" suppressHydrationWarning className={inter.variable}>
        <body className="min-h-screen antialiased">{children}</body>
      </html>
    </ClerkProvider>
  );
}
