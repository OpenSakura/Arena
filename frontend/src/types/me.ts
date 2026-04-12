import { isRecord } from "@/lib/typeGuards";

export type MeUser = {
  id: string;
  oidc_issuer: string;
  oidc_sub: string;
  created_at: string;
};

export type MeProfile = {
  display_name?: string | null;
  ui_language?: string | null;
  zh_variant?: string | null;
  jp_proficiency?: Record<string, unknown> | null;
  translation_experience?: Record<string, unknown> | null;
  consents?: Record<string, unknown> | null;
  completed_at?: string | null;
};

export type MeResponse = {
  authenticated: boolean;
  is_admin?: boolean;
  user?: MeUser | null;
  profile?: MeProfile | null;
};

function isMeUser(value: unknown): value is MeUser {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.oidc_issuer === "string" &&
    typeof value.oidc_sub === "string" &&
    typeof value.created_at === "string"
  );
}

function isMeProfile(value: unknown): value is MeProfile {
  return (
    isRecord(value) &&
    (value.display_name === undefined || value.display_name === null || typeof value.display_name === "string") &&
    (value.ui_language === undefined || value.ui_language === null || typeof value.ui_language === "string") &&
    (value.zh_variant === undefined || value.zh_variant === null || typeof value.zh_variant === "string") &&
    (value.jp_proficiency === undefined || value.jp_proficiency === null || isRecord(value.jp_proficiency)) &&
    (value.translation_experience === undefined || value.translation_experience === null || isRecord(value.translation_experience)) &&
    (value.consents === undefined || value.consents === null || isRecord(value.consents)) &&
    (value.completed_at === undefined || value.completed_at === null || typeof value.completed_at === "string")
  );
}

export function isMeResponse(value: unknown): value is MeResponse {
  if (!isRecord(value) || typeof value.authenticated !== "boolean") {
    return false;
  }

  if ("is_admin" in value && typeof value.is_admin !== "boolean") {
    return false;
  }

  if ("user" in value && value.user !== null && value.user !== undefined && !isMeUser(value.user)) {
    return false;
  }

  if (
    "profile" in value &&
    value.profile !== null &&
    value.profile !== undefined &&
    !isMeProfile(value.profile)
  ) {
    return false;
  }

  return true;
}

export function parseMeResponse(value: unknown): MeResponse {
  if (!isMeResponse(value)) {
    throw new Error("Invalid /me response");
  }

  return value;
}
