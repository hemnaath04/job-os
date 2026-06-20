import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-10">
      <div className="flex flex-col items-center">
        <div className="mb-6 flex items-center gap-2">
          <div className="size-7 rounded-md bg-gradient-to-br from-[#CCFF00] to-[#FFFF00] shadow-[0_0_30px_-5px_#CCFF00]" />
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
  );
}
