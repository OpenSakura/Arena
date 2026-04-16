import * as React from "react";

type Theme = "dark" | "light" | "system";

type ThemeProviderProps = {
  children: React.ReactNode;
  defaultTheme?: Theme;
  storageKey?: string;
};

type ThemeProviderState = {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  systemTheme: "dark" | "light";
  mounted: boolean;
};

const initialState: ThemeProviderState = {
  theme: "system",
  setTheme: () => null,
  systemTheme: "light",
  mounted: false,
};

const ThemeProviderContext = React.createContext<ThemeProviderState>(initialState);

export function ThemeProvider({
  children,
  defaultTheme = "system",
  storageKey = "theme",
}: ThemeProviderProps) {
  const [mounted, setMounted] = React.useState(false);
  const [theme, setThemeState] = React.useState<Theme>(() => {
    try {
      if (typeof window !== "undefined") {
        return (localStorage.getItem(storageKey) as Theme) || defaultTheme;
      }
    } catch {
      // ignore
    }
    return defaultTheme;
  });

  const [systemTheme, setSystemTheme] = React.useState<"dark" | "light">("light");

  React.useEffect(() => {
    setMounted(true);
    if (typeof window !== "undefined" && window.matchMedia) {
      setSystemTheme(window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    }
  }, []);

  React.useEffect(() => {
    if (!mounted) return;
    const root = window.document.documentElement;
    root.classList.remove("light", "dark");
    const activeTheme = theme === "system" ? systemTheme : theme;
    root.classList.add(activeTheme);
  }, [theme, systemTheme, mounted]);

  React.useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = (e: MediaQueryListEvent) => {
      setSystemTheme(e.matches ? "dark" : "light");
    };
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  const setTheme = React.useCallback(
    (newTheme: Theme) => {
      try {
        if (typeof window !== "undefined") {
          localStorage.setItem(storageKey, newTheme);
        }
      } catch {
        // ignore
      }
      setThemeState(newTheme);
    },
    [storageKey]
  );

  return (
    <ThemeProviderContext.Provider value={{ theme, setTheme, systemTheme, mounted }}>
      {children}
    </ThemeProviderContext.Provider>
  );
}

export function useTheme() {
  const context = React.useContext(ThemeProviderContext);
  if (!context) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }

  const currentTheme = context.theme === "system" ? context.systemTheme : context.theme;

  const toggleTheme = React.useCallback(() => {
    context.setTheme(currentTheme === "dark" ? "light" : "dark");
  }, [context.setTheme, currentTheme]);

  return {
    theme: currentTheme,
    mounted: context.mounted,
    setTheme: context.setTheme,
    toggleTheme,
  };
}
