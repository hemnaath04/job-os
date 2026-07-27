import { SignUp } from "@clerk/nextjs";
import { BackendReadiness } from "@/components/backend-readiness";
import { BrandMark } from "@/components/brand-mark";

export default function SignUpPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-10">
      <BackendReadiness />
      <div className="flex flex-col items-center">
        <div className="mb-6 flex items-center gap-2">
          <BrandMark className="drop-shadow-[0_12px_16px_rgba(233,198,74,.28)]" />
          <span className="font-mono text-sm tracking-tight">job.os</span>
        </div>
        <SignUp
          path="/sign-up"
          signInUrl="/sign-in"
          forceRedirectUrl="/dashboard"
          appearance={{
            variables: {
              colorPrimary: "#8A6D12",
              colorBackground: "#FFFFFF",
              colorInputBackground: "#FFFFFF",
              colorInputText: "#2A2530",
              colorText: "#2A2530",
              colorTextSecondary: "#6C6470",
              colorNeutral: "#2A2530",
              borderRadius: "0.75rem",
            },
            elements: {
              formFieldInput:
                "text-[color:var(--color-text)] placeholder:text-[color:var(--color-text-dim)] bg-[#FFFFFF] border-[color:var(--color-border)]",
              formFieldLabel: "text-zinc-700",
            },
          }}
        />
      </div>
    </main>
  );
}
