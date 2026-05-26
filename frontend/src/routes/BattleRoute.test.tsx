import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import BattleRoute from "./BattleRoute";
import { BattleView } from "@/components/BattleView";

vi.mock("@/components/BattleView", () => ({
  BattleView: vi.fn(() => <div data-testid="mock-battle-view">Mocked BattleView</div>),
}));

async function renderBattleRoute({
  initialEntry,
  routePath,
  locale = "en",
}: {
  initialEntry: string;
  routePath: string;
  locale?: "en" | "zh";
}) {
  const i18n = await createTestI18n(locale);

  return render(
    <TestI18nProvider i18n={i18n}>
      <MemoryRouter 
        initialEntries={[initialEntry]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path={routePath} element={<BattleRoute />} />
        </Routes>
      </MemoryRouter>
    </TestI18nProvider>
  );
}

describe("BattleRoute", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders localized invalid ID when no battleId is present", async () => {
    await renderBattleRoute({ initialEntry: "/battle/", routePath: "/battle/", locale: "zh" });

    expect(screen.getByText("无效的对战 ID")).toBeDefined();
  });

  it("passes battleId from URL to BattleView", async () => {
    const testBattleId = "test-battle-123";
    await renderBattleRoute({ initialEntry: `/battle/${testBattleId}`, routePath: "/battle/:battleId" });

    expect(screen.getByTestId("mock-battle-view")).toBeDefined();
    expect(BattleView).toHaveBeenCalledWith(
      expect.objectContaining({ battleId: testBattleId }),
      expect.anything()
    );
  });

  it("passes 'new' battleId to BattleView when visiting /battle/new", async () => {
    await renderBattleRoute({ initialEntry: "/battle/new", routePath: "/battle/:battleId" });

    expect(screen.getByTestId("mock-battle-view")).toBeDefined();
    expect(BattleView).toHaveBeenCalledWith(
      expect.objectContaining({ battleId: "new" }),
      expect.anything()
    );
  });
});
