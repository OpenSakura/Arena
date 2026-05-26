import { type ReactNode } from "react";
import { ThemeProvider } from "@/components/ThemeProvider";
import { I18nDocumentEffects } from "@/components/I18nDocumentEffects";
import { I18nProfileSync } from "@/components/I18nProfileSync";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider defaultTheme="system">
      <I18nDocumentEffects />
      <I18nProfileSync />
      {children}
    </ThemeProvider>
  );
}
