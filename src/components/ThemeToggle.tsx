"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

type Theme = "light" | "dark";

const THEME_STORAGE_KEY = "chatbot.theme";

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // The visual switch still works if storage is unavailable.
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    const applied = document.documentElement.dataset.theme;
    setTheme(applied === "dark" ? "dark" : "light");
  }, []);

  const isDark = theme === "dark";

  function toggleTheme() {
    const nextTheme: Theme = isDark ? "light" : "dark";
    applyTheme(nextTheme);
    setTheme(nextTheme);
  }

  return (
    <button
      type="button"
      onClick={toggleTheme}
      className="focus-ring rounded-lg p-1.5 text-[var(--fg-muted)] transition-colors hover:bg-[var(--bg-subtle)] hover:text-[var(--fg)]"
      aria-label={isDark ? "切换到浅色模式" : "切换到深色模式"}
      title={isDark ? "浅色模式" : "深色模式"}
    >
      {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  );
}
