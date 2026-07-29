import { SignIn } from "@clerk/nextjs";
import { AuthShell, clerkAppearance } from "@/components/auth-shell";
import { BackendReadiness } from "@/components/backend-readiness";

export default function SignInPage() {
  return (
    <AuthShell headline="Your job search, kept honest.">
      <BackendReadiness />
      {/* Clerk's own widget, not a lookalike form. It renders the social
          buttons, the password field and every error state itself, so there is
          nothing here to keep in sync with the real auth flow. */}
      <SignIn
        path="/sign-in"
        signUpUrl="/sign-up"
        forceRedirectUrl="/dashboard"
        appearance={clerkAppearance}
      />
    </AuthShell>
  );
}
