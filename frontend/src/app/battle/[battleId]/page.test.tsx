import { describe, expect, it } from "vitest";

import BattlePage from "./page";

describe("BattlePage", () => {
  it("passes battleId route param to BattleView", async () => {
    const element = await BattlePage({ params: Promise.resolve({ battleId: "battle-xyz" }) });

    // The component wraps BattleView in a Suspense boundary.
    const battleView = element.props.children;
    expect(battleView.props.battleId).toBe("battle-xyz");
  });
});
