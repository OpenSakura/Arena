import { apiGet, apiPost, apiPatch } from "@/lib/api";
import { isRecord } from "@/lib/typeGuards";

export type ServiceAccountToken = {
  id: string;
  service_account_id: string;
  token_prefix: string;
  status: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
  revoked_at: string | null;
};

export type ServiceAccount = {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  scopes: string[];
  tokens: ServiceAccountToken[];
  created_at: string;
  updated_at: string;
};

export type CreateTokenResponse = {
  service_account: ServiceAccount;
  token: ServiceAccountToken;
  plaintext_token: string;
};

export function isServiceAccountToken(value: unknown): value is ServiceAccountToken {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.service_account_id === "string" &&
    typeof value.token_prefix === "string" &&
    typeof value.status === "string" &&
    Array.isArray(value.scopes) &&
    value.scopes.every((s) => typeof s === "string") &&
    (typeof value.expires_at === "string" || value.expires_at === null) &&
    (typeof value.last_used_at === "string" || value.last_used_at === null) &&
    typeof value.created_at === "string" &&
    (typeof value.revoked_at === "string" || value.revoked_at === null)
  );
}

export function isServiceAccount(value: unknown): value is ServiceAccount {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    (typeof value.description === "string" || value.description === null) &&
    typeof value.enabled === "boolean" &&
    Array.isArray(value.scopes) &&
    value.scopes.every((s) => typeof s === "string") &&
    Array.isArray(value.tokens) &&
    value.tokens.every(isServiceAccountToken) &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string"
  );
}

export async function listServiceAccounts(headers: HeadersInit): Promise<ServiceAccount[]> {
  const res = await apiGet<unknown>("/admin/service-accounts", { headers });
  if (!isRecord(res) || !Array.isArray(res.service_accounts)) {
    throw new Error("Invalid response format");
  }
  return res.service_accounts.filter(isServiceAccount);
}

export async function createServiceAccount(
  payload: { name: string; description: string | null; enabled: boolean },
  headers: HeadersInit,
): Promise<ServiceAccount> {
  const res = await apiPost<unknown>("/admin/service-accounts", payload, { headers });
  if (!isServiceAccount(res)) throw new Error("Invalid create response");
  return res;
}

export async function updateServiceAccount(
  id: string,
  payload: { name?: string; description?: string | null; enabled?: boolean },
  headers: HeadersInit,
): Promise<ServiceAccount> {
  const res = await apiPatch<unknown>(`/admin/service-accounts/${encodeURIComponent(id)}`, payload, { headers });
  if (!isServiceAccount(res)) throw new Error("Invalid update response");
  return res;
}

export async function createServiceAccountToken(
  id: string,
  payload: { scopes: string[]; expires_at: string | null },
  headers: HeadersInit,
): Promise<CreateTokenResponse> {
  const res = await apiPost<unknown>(`/admin/service-accounts/${encodeURIComponent(id)}/tokens`, payload, { headers });
  if (
    !isRecord(res) ||
    !isServiceAccount(res.service_account) ||
    !isServiceAccountToken(res.token) ||
    typeof res.plaintext_token !== "string"
  ) {
    throw new Error("Invalid create token response");
  }
  return {
    service_account: res.service_account as ServiceAccount,
    token: res.token as ServiceAccountToken,
    plaintext_token: res.plaintext_token,
  };
}

export async function revokeServiceAccountToken(
  tokenId: string,
  headers: HeadersInit,
): Promise<{ token_id: string; revoked: boolean }> {
  const res = await apiPost<unknown>(`/admin/service-account-tokens/${encodeURIComponent(tokenId)}/revoke`, {}, { headers });
  if (!isRecord(res) || typeof res.token_id !== "string" || typeof res.revoked !== "boolean") {
    throw new Error("Invalid revoke response");
  }
  return {
    token_id: res.token_id,
    revoked: res.revoked,
  };
}
