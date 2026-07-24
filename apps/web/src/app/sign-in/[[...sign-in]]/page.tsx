import { SignIn } from "@clerk/nextjs";
import { AuthProvider } from "@/components/auth-provider";
import { BackendWakeup } from "@/components/backend-wakeup";
import { BrandMark } from "@/components/brand-mark";

export default function SignInPage() {
  return (
    <AuthProvider>
      <BackendWakeup />
      <main className="flex min-h-screen items-center justify-center px-6 py-10">
        <div className="flex flex-col items-center">
          <div className="mb-6 flex items-center gap-2">
            <BrandMark className="drop-shadow-[0_0_14px_rgba(204,255,0,0.28)]" />
            <span className="font-mono text-sm tracking-tight">job.os</span>
          </div>
          <SignIn
            path="/sign-in"
            signUpUrl="/sign-up"
            forceRedirectUrl="/dashboard"
            appearance={{
              variables: {
                colorPrimary: "#CCFF00",
                colorBackground: "#13131B",
                colorInputBackground: "#1A1A24",
                colorInputText: "#F5F5FA",
                colorText: "#F5F5FA",
                colorTextSecondary: "#A1A1AE",
                colorNeutral: "#F5F5FA",
                borderRadius: "0.75rem",
              },
              elements: {
                formFieldInput:
                  "text-white placeholder:text-zinc-500 bg-[#1A1A24] border-white/10",
                formFieldLabel: "text-zinc-200",
              },
            }}
          />
        </div>
      </main>
    </AuthProvider>
  );
}
