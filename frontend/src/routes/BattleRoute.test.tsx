import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import BattleRoute from "./BattleRoute";
import { BattleView } from "@/components/BattleView";

vi.mock("@/components/BattleView", () => ({
  BattleView: vi.fn(() => <div data-testid="mock-battle-view">Mocked BattleView</div>),
}));

describe("BattleRoute", () => {
  it("renders invalid ID when no battleId is present", () => {
    render(
      <MemoryRouter 
        initialEntries={["/battle/"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/battle/" element={<BattleRoute />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText("Invalid battle ID")).toBeDefined();
  });

  it("passes battleId from URL to BattleView", () => {
    const testBattleId = "test-battle-123";
    render(
      <MemoryRouter 
        initialEntries={[`/battle/${testBattleId}`]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/battle/:battleId" element={<BattleRoute />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId("mock-battle-view")).toBeDefined();
    expect(BattleView).toHaveBeenCalledWith(
      expect.objectContaining({ battleId: testBattleId }),
      expect.anything()
    );
  });

  it("passes 'new' battleId to BattleView when visiting /battle/new", () => {
    render(
      <MemoryRouter 
        initialEntries={[`/battle/new`]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/battle/:battleId" element={<BattleRoute />} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId("mock-battle-view")).toBeDefined();
    expect(BattleView).toHaveBeenCalledWith(
      expect.objectContaining({ battleId: "new" }),
      expect.anything()
    );
  });
});
