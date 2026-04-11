// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthHeaders } from "./useAuthHeaders";

const useSessionMock = vi.fn();

vi.mock("next-auth/react", () => ({
  useSession: () => useSessionMock(),
}));

function Probe() {
  const { headers, authStatus } = useAuthHeaders();

  return (
    <div>
      <span>{authStatus}</span>
      <span>{headers?.Authorization ?? "no-auth"}</span>
    </div>
  );
}

describe("useAuthHeaders", () => {
  beforeEach(() => {
    useSessionMock.mockReset();
  });

  it("updates headers immediately when the session becomes authenticated", () => {
    useSessionMock
      .mockReturnValueOnce({ data: null, status: "loading" })
      .mockReturnValue({
        data: { accessToken: "admin-token" },
        status: "authenticated",
      });

    const { rerender } = render(<Probe />);
    expect(screen.getByText("no-auth")).toBeDefined();

    rerender(<Probe />);

    expect(screen.getByText("Bearer admin-token")).toBeDefined();
  });

  it("keeps auth headers unset while unauthenticated", async () => {
    useSessionMock.mockReturnValue({
      data: null,
      status: "unauthenticated",
    });

    render(<Probe />);

    await waitFor(() => {
      expect(screen.getByText("unauthenticated")).toBeDefined();
    });
    expect(screen.getByText("no-auth")).toBeDefined();
  });
});
