import { describe, it, expect, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import { I18nDocumentEffects } from "./I18nDocumentEffects";

describe("I18nDocumentEffects", () => {
  const originalTitle = document.title;
  const originalLang = document.documentElement.lang;
  const originalDir = document.documentElement.dir;

  afterEach(() => {
    document.title = originalTitle;
    document.documentElement.lang = originalLang;
    document.documentElement.dir = originalDir;
  });

  it("sets document effects correctly for default route", async () => {
    const i18n = await createTestI18n("zh");

    const router = createMemoryRouter([
      {
        path: "/",
        element: <I18nDocumentEffects />
      }
    ]);

    render(
      <TestI18nProvider i18n={i18n}>
        <RouterProvider router={router} />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(document.documentElement.lang).toBe("zh");
      expect(document.documentElement.dir).toBe("ltr"); // Chinese is LTR
      expect(document.title).toBe("OpenSakura Arena");
    });
  });

  it("sets localized route title from route title handle", async () => {
    const i18n = await createTestI18n("zh");

    const router = createMemoryRouter([
      {
        path: "/",
        handle: { titleKey: "routes.leaderboard" },
        element: <I18nDocumentEffects />
      }
    ]);

    render(
      <TestI18nProvider i18n={i18n}>
        <RouterProvider router={router} />
      </TestI18nProvider>
    );

    await waitFor(() => {
      expect(document.title).toBe("排行榜 | OpenSakura Arena");
    });
  });
});
