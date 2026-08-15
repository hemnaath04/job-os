import { OAuthConsent, Show } from "@clerk/nextjs";
import type { Metadata } from "next";

// Required on any page hosting the OAuth consent flow, so the cross-origin
// request that lands here (from Claude, or whichever MCP client) can still be
// validated.
export const metadata: Metadata = {
  referrer: "strict-origin-when-cross-origin",
};

export default function OAuthConsentPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <Show when="signed-in">
        <OAuthConsent />
      </Show>
    </main>
  );
}
