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
  title: "job.os | personal job-search OS",
  description: "Track applications, tailor resumes, never lie on your CV.",
};

// Runs before paint to apply the saved (or system) theme with no flash.
const themeInit = `try{var t=localStorage.getItem('theme');if(t==='dark'||(!t&&window.matchMedia('(prefers-color-scheme:dark)').matches)){document.documentElement.classList.add('dark')}}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider
      appearance={{
        variables: {
          colorPrimary: "#8A6D12",
          colorBackground: "#FFFFFF",
          colorInputBackground: "#FFFFFF",
          colorInputText: "#2A2530",
          colorText: "#2A2530",
          colorTextSecondary: "#6C6470",
          colorNeutral: "#2A2530",
          borderRadius: "0.7rem",
        },
      }}
    >
      <html lang="en" suppressHydrationWarning className={manrope.variable}>
        <body className="min-h-screen antialiased">
          <script dangerouslySetInnerHTML={{ __html: themeInit }} />
          {children}
        </body>
      </html>
    </ClerkProvider>
  );
}
