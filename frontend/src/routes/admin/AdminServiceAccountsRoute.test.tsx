// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createTestI18n, TestI18nProvider } from "@/i18n/test-utils";
import AdminServiceAccountsRoute from "./AdminServiceAccountsRoute";

const useAuthHeadersMock = vi.fn();
const apiGetMock = vi.fn();
const apiPostMock = vi.fn();
const apiPatchMock = vi.fn();

vi.mock("@/hooks/useAuthHeaders", () => ({
  useAuthHeaders: () => useAuthHeadersMock(),
}));

vi.mock("@/lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
  apiPost: (...args: unknown[]) => apiPostMock(...args),
  apiPatch: (...args: unknown[]) => apiPatchMock(...args),
}));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  useAuthHeadersMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPatchMock.mockReset();

  useAuthHeadersMock.mockReturnValue({
    authStatus: "unauthenticated",
    sessionError: null,
    csrfToken: null,
  });
});

function authenticatedSession() {
  useAuthHeadersMock.mockReturnValue({
    authStatus: "authenticated",
    csrfToken: "csrf-token",
    sessionError: null,
  });
}

function serviceAccountRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "sa-1",
    name: "Bot One",
    description: "Bot description",
    enabled: true,
    scopes: ["battle:create"],
    tokens: [
      {
        id: "tok-1",
        service_account_id: "sa-1",
        token_prefix: "tok_abc",
        status: "active",
        scopes: ["battle:create"],
        expires_at: null,
        last_used_at: null,
        created_at: "2026-02-18T00:00:00Z",
        revoked_at: null,
      }
    ],
    created_at: "2026-02-18T00:00:00Z",
    updated_at: "2026-02-18T00:00:00Z",
    ...overrides,
  };
}

async function renderAdminServiceAccountsRoute(locale: "en" | "zh" = "en") {
  const i18n = await createTestI18n(locale);
  return render(
    <TestI18nProvider i18n={i18n}>
      <AdminServiceAccountsRoute />
    </TestI18nProvider>,
  );
}

describe("AdminServiceAccountsRoute", () => {
  it("does not load accounts when unauthenticated", async () => {
    await renderAdminServiceAccountsRoute();
    await screen.findByText("Service Accounts");
    expect(apiGetMock).not.toHaveBeenCalled();
    await screen.findByText("No service accounts found.");
  });

  it("renders service account labels from the Chinese catalog", async () => {
    await renderAdminServiceAccountsRoute("zh");

    await screen.findByRole("heading", { name: "服务账号" });
    expect(screen.getByText("创建服务账号")).toBeDefined();
    expect(screen.getByLabelText("名称")).toBeDefined();
    expect(screen.getByLabelText("描述（可选）")).toBeDefined();
    expect(screen.getByRole("button", { name: "创建" })).toBeDefined();
    expect(screen.getByText("暂无服务账号。" )).toBeDefined();
  });

  it("loads and renders accounts when authenticated", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ service_accounts: [serviceAccountRecord()] });

    await renderAdminServiceAccountsRoute();
    await screen.findByText("Bot One");

    expect(apiGetMock).toHaveBeenCalledWith("/admin/service-accounts");
  });

  it("creates a new service account", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ service_accounts: [] });
    apiPostMock.mockResolvedValue(serviceAccountRecord({ id: "sa-2", name: "Bot Two" }));

    await renderAdminServiceAccountsRoute();
    await screen.findByText("No service accounts found.");

    const user = userEvent.setup();
    const nameInput = screen.getByLabelText("Name");
    await user.type(nameInput, "Bot Two");
    
    await user.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/service-accounts",
        { name: "Bot Two", description: null, enabled: true }
      );
    });
    expect(apiPostMock.mock.calls[0]).toHaveLength(2);

    await screen.findByText("Bot Two");
  });

  it("shows exact one-time warning and hides token after dismiss", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ service_accounts: [serviceAccountRecord({ tokens: [] })] });
    apiPostMock.mockResolvedValue({
      service_account: serviceAccountRecord(),
      token: {
        id: "tok-2",
        service_account_id: "sa-1",
        token_prefix: "tok_def",
        status: "active",
        scopes: ["battle:create"],
        expires_at: null,
        last_used_at: null,
        created_at: "2026-02-18T00:00:00Z",
        revoked_at: null,
      },
      plaintext_token: "pt_secret_token_123"
    });

    await renderAdminServiceAccountsRoute();
    await screen.findByText("Bot One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Tokens" }));
    await user.click(screen.getByRole("button", { name: "New Token" }));
    
    await user.click(screen.getByRole("checkbox", { name: /Create battles/ }));

    await user.click(screen.getByRole("button", { name: "Confirm Create" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/service-accounts/sa-1/tokens",
        { scopes: ["battle:create"], expires_at: null },
      );
    });
    expect(apiPostMock.mock.calls[0]).toHaveLength(2);

    await screen.findByText("Copy now. This token will not be shown again.");
    await screen.findByText("pt_secret_token_123");

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("pt_secret_token_123")).toBeNull();
  });

  it("localizes service account scope labels while preserving scope ids in the token payload", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ service_accounts: [serviceAccountRecord({ tokens: [] })] });
    apiPostMock.mockResolvedValue({
      service_account: serviceAccountRecord(),
      token: {
        id: "tok-2",
        service_account_id: "sa-1",
        token_prefix: "tok_def",
        status: "active",
        scopes: ["battle:create", "vote:create"],
        expires_at: null,
        last_used_at: null,
        created_at: "2026-02-18T00:00:00Z",
        revoked_at: null,
      },
      plaintext_token: "pt_secret_token_123",
    });

    await renderAdminServiceAccountsRoute("zh");
    await screen.findByText("Bot One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "令牌" }));
    await user.click(screen.getByRole("button", { name: "新建令牌" }));

    const battleCreateScope = screen.getByRole("checkbox", { name: /创建对战/ }) as HTMLInputElement;
    const voteCreateScope = screen.getByRole("checkbox", { name: /提交投票/ }) as HTMLInputElement;
    expect(battleCreateScope.value).toBe("battle:create");
    expect(voteCreateScope.value).toBe("vote:create");
    expect(screen.getByText("允许创建新的翻译对战。" )).toBeDefined();
    expect(screen.getByText("battle:create")).toBeDefined();
    expect(screen.getByText("vote:create")).toBeDefined();

    await user.click(battleCreateScope);
    await user.click(voteCreateScope);
    await user.click(screen.getByRole("button", { name: "确认创建" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/service-accounts/sa-1/tokens",
        { scopes: ["battle:create", "vote:create"], expires_at: null },
      );
    });
    expect(apiPostMock.mock.calls[0]).toHaveLength(2);
  });

  it("revokes token correctly", async () => {
    authenticatedSession();
    apiGetMock.mockResolvedValue({ service_accounts: [serviceAccountRecord()] });
    apiPostMock.mockResolvedValue({ token_id: "tok-1", revoked: true });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    await renderAdminServiceAccountsRoute();
    await screen.findByText("Bot One");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Tokens" }));
    await screen.findByText("tok_abc...");
    
    await user.click(screen.getByRole("button", { name: "Revoke" }));

    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith(
        "/admin/service-account-tokens/tok-1/revoke",
        {},
      );
    });
    expect(apiPostMock.mock.calls[0]).toHaveLength(2);
    
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
    });
  });
});
