import { ClerkProvider } from "@clerk/nextjs";

export function AuthProvider({ children }: { children: React.ReactNode }) {
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
      {children}
    </ClerkProvider>
  );
}
