import { SignUp } from "@clerk/nextjs";
import { AuthShell, clerkAppearance } from "@/components/auth-shell";
import { BackendReadiness } from "@/components/backend-readiness";

export default function SignUpPage() {
  return (
    <AuthShell headline="Start from evidence, not a blank page.">
      <BackendReadiness />
      <SignUp
        path="/sign-up"
        signInUrl="/sign-in"
        forceRedirectUrl="/dashboard"
        appearance={clerkAppearance}
      />
    </AuthShell>
  );
}
