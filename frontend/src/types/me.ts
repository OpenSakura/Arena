export type MeResponse = {
  authenticated: boolean;
  is_admin?: boolean;
  profile?: Record<string, unknown> | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isMeResponse(value: unknown): value is MeResponse {
  if (!isRecord(value) || typeof value.authenticated !== "boolean") {
    return false;
  }

  if ("is_admin" in value && typeof value.is_admin !== "boolean") {
    return false;
  }

  if (
    "profile" in value &&
    value.profile !== null &&
    value.profile !== undefined &&
    !isRecord(value.profile)
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
