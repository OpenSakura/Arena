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

  it("copies error from jwt token onto session when present", async () => {
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
      token: { accessToken: undefined, error: "RefreshTokenExpired" },
    });

    expect(updated).toEqual({ user: { name: "alice" }, accessToken: undefined, error: "RefreshTokenExpired" });
  });
});

describe("jwt refresh paths", () => {
  type JwtCallback = (params: {
    token: Record<string, unknown>;
    account?: Record<string, unknown>;
  }) => Promise<Record<string, unknown>>;

  async function getJwtCallback(): Promise<JwtCallback> {
    nextAuthMock.mockReturnValue(vi.fn());
    await import("./route");
    const config = nextAuthMock.mock.calls[0][0] as { callbacks: { jwt: JwtCallback } };
    return config.callbacks.jwt;
  }

  const expiredToken = {
    sub: "user-1",
    accessToken: "old-token",
    refreshToken: "refresh-abc",
    accessTokenExpires: Date.now() - 10_000,
  };

  it("returns RefreshTokenMissing when refreshToken is absent", async () => {
    process.env.AUTHENTIK_ISSUER = "https://auth.example/";
    const jwt = await getJwtCallback();

    const result = await jwt({
      token: { sub: "user-1", accessToken: "old", accessTokenExpires: Date.now() - 1 },
    });

    expect(result.error).toBe("RefreshTokenMissing");
    expect(result.accessToken).toBeUndefined();
    expect(result.accessTokenExpires).toBe(0);
  });

  it("returns RefreshTokenMissing when AUTHENTIK_ISSUER is absent", async () => {
    delete process.env.AUTHENTIK_ISSUER;
    const jwt = await getJwtCallback();

    const result = await jwt({
      token: { ...expiredToken },
    });

    expect(result.error).toBe("RefreshTokenMissing");
  });

  it("returns RefreshDiscoveryFailed when OIDC discovery endpoint returns non-ok", async () => {
    process.env.AUTHENTIK_ISSUER = "https://auth.example/application/o/arena/";
    process.env.AUTHENTIK_CLIENT_ID = "arena-client";
    process.env.AUTHENTIK_CLIENT_SECRET = "super-secret";

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("not found", { status: 404 }),
    );

    const jwt = await getJwtCallback();
    const result = await jwt({ token: { ...expiredToken } });

    expect(result.error).toBe("RefreshDiscoveryFailed");
    expect(result.accessTokenExpires).toBe(0);
  });

  it("returns RefreshTokenExpired when token endpoint returns an error", async () => {
    process.env.AUTHENTIK_ISSUER = "https://auth.example/application/o/arena/";
    process.env.AUTHENTIK_CLIENT_ID = "arena-client";
    process.env.AUTHENTIK_CLIENT_SECRET = "super-secret";

    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify({ token_endpoint: "https://auth.example/token" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "invalid_grant" }), {
        status: 400,
        headers: { "content-type": "application/json" },
      }),
    );

    const jwt = await getJwtCallback();
    const result = await jwt({ token: { ...expiredToken } });

    expect(result.error).toBe("RefreshTokenExpired");
    expect(result.accessTokenExpires).toBe(0);
  });

  it("returns RefreshTokenError when fetch throws a network error", async () => {
    process.env.AUTHENTIK_ISSUER = "https://auth.example/application/o/arena/";
    process.env.AUTHENTIK_CLIENT_ID = "arena-client";
    process.env.AUTHENTIK_CLIENT_SECRET = "super-secret";

    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("network failure"));

    const jwt = await getJwtCallback();
    const result = await jwt({ token: { ...expiredToken } });

    expect(result.error).toBe("RefreshTokenError");
  });

  it("rotates tokens on a successful refresh", async () => {
    process.env.AUTHENTIK_ISSUER = "https://auth.example/application/o/arena/";
    process.env.AUTHENTIK_CLIENT_ID = "arena-client";
    process.env.AUTHENTIK_CLIENT_SECRET = "super-secret";

    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify({ token_endpoint: "https://auth.example/token" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          access_token: "new-access-token",
          refresh_token: "new-refresh-token",
          expires_in: 3600,
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );

    const jwt = await getJwtCallback();
    const result = await jwt({ token: { ...expiredToken } });

    expect(result.error).toBeUndefined();
    expect(result.accessToken).toBe("new-access-token");
    expect(result.refreshToken).toBe("new-refresh-token");
    expect(typeof result.accessTokenExpires).toBe("number");
    expect(result.accessTokenExpires as number).toBeGreaterThan(Date.now());
  });

  it("preserves old refresh token when server does not return a new one", async () => {
    process.env.AUTHENTIK_ISSUER = "https://auth.example/application/o/arena/";
    process.env.AUTHENTIK_CLIENT_ID = "arena-client";
    process.env.AUTHENTIK_CLIENT_SECRET = "super-secret";

    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify({ token_endpoint: "https://auth.example/token" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    fetchSpy.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ access_token: "new-access-token", expires_in: 1800 }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );

    const jwt = await getJwtCallback();
    const result = await jwt({ token: { ...expiredToken } });

    expect(result.accessToken).toBe("new-access-token");
    expect(result.refreshToken).toBe("refresh-abc");
  });
});
