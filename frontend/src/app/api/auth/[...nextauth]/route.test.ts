import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const nextAuthMock = vi.fn();
const authentikProviderMock = vi.fn();

vi.mock("next-auth", () => ({
  default: nextAuthMock,
}));

vi.mock("next-auth/providers/authentik", () => ({
  default: authentikProviderMock,
}));

afterEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  delete process.env.AUTHENTIK_ISSUER;
  delete process.env.AUTHENTIK_CLIENT_ID;
  delete process.env.AUTHENTIK_CLIENT_SECRET;
});

beforeEach(() => {
  nextAuthMock.mockReset();
  authentikProviderMock.mockReset();
  authentikProviderMock.mockImplementation((config) => ({ id: "authentik", config }));
});

describe("nextauth route", () => {
  it("configures the Authentik provider from env vars", async () => {
    process.env.AUTHENTIK_ISSUER = "https://auth.example/application/o/arena/";
    process.env.AUTHENTIK_CLIENT_ID = "arena-client";
    process.env.AUTHENTIK_CLIENT_SECRET = "super-secret";

    const handler = vi.fn();
    nextAuthMock.mockReturnValue(handler);

    const route = await import("./route");

    expect(route.GET).toBe(handler);
    expect(route.POST).toBe(handler);

    expect(authentikProviderMock).toHaveBeenCalledWith({
      issuer: "https://auth.example/application/o/arena/",
      clientId: "arena-client",
      clientSecret: "super-secret",
      authorization: { params: { scope: "openid email profile offline_access" } },
    });
  });

  it("stores account access_token in jwt callback", async () => {
    nextAuthMock.mockReturnValue(vi.fn());

    await import("./route");
    const config = nextAuthMock.mock.calls[0][0] as {
      callbacks: {
        jwt: (params: {
          token: Record<string, unknown>;
          account?: { access_token?: string | null; refresh_token?: string | null; expires_at?: number | null };
        }) => Promise<Record<string, unknown>>;
      };
    };

    const token = { sub: "user-1" };
    const updated = await config.callbacks.jwt({
      token,
      account: { access_token: "token-abc", refresh_token: "refresh-xyz", expires_at: 1700000000 },
    });

    expect(updated).toMatchObject({ sub: "user-1", accessToken: "token-abc", refreshToken: "refresh-xyz" });
    expect(updated.accessTokenExpires).toBe(1700000000 * 1000);

    const unchanged = await config.callbacks.jwt({
      token: { sub: "user-1", accessToken: "keep-me", accessTokenExpires: Date.now() + 3600_000 },
      account: undefined,
    });

    expect(unchanged).toMatchObject({ sub: "user-1", accessToken: "keep-me" });
  });

  it("copies accessToken from jwt token onto session", async () => {
    nextAuthMock.mockReturnValue(vi.fn());

    await import("./route");
    const config = nextAuthMock.mock.calls[0][0] as {
      callbacks: {
        session: (params: {
          session: Record<string, unknown>;
          token: Record<string, unknown>;
        }) => Promise<Record<string, unknown>>;
      };
    };

    const session = { user: { name: "alice" } };
    const updated = await config.callbacks.session({
      session,
      token: { accessToken: "token-xyz" },
    });

    expect(updated).toBe(session);
    expect(updated).toEqual({
      user: { name: "alice" },
      accessToken: "token-xyz",
    });
  });
});
