import { useEffect, useReducer } from "react";
import { useTranslation } from "react-i18next";

import { Skeleton } from "@/components/ui/skeleton";
import { useAuthHeaders } from "@/hooks/useAuthHeaders";
import {
  ServiceAccount,
  listServiceAccounts,
  createServiceAccount,
  updateServiceAccount,
  createServiceAccountToken,
  revokeServiceAccountToken,
} from "@/lib/serviceAccounts";

const ALL_SCOPES = [
  "battle:create",
  "battle:read",
  "battle:execute",
  "vote:create",
];

const SCOPE_I18N_KEYS: Record<string, string> = {
  "battle:create": "battleCreate",
  "battle:read": "battleRead",
  "battle:execute": "battleExecute",
  "vote:create": "voteCreate",
};

const SERVICE_ACCOUNT_ERROR_KEYS: Record<string, string> = {
  "admin.serviceAccounts.errors.nameRequired": "admin.serviceAccounts.errors.nameRequired",
  "admin.serviceAccounts.errors.scopeRequired": "admin.serviceAccounts.errors.scopeRequired",
  "Invalid response format": "admin.serviceAccounts.errors.invalidResponseFormat",
  "Invalid create response": "admin.serviceAccounts.errors.invalidCreateResponse",
  "Invalid update response": "admin.serviceAccounts.errors.invalidUpdateResponse",
  "Invalid create token response": "admin.serviceAccounts.errors.invalidCreateTokenResponse",
  "Invalid revoke response": "admin.serviceAccounts.errors.invalidRevokeResponse",
};

type CreateFormState = {
  name: string;
  description: string;
  enabled: boolean;
};

type EditFormState = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
};

type TokenCreateFormState = {
  serviceAccountId: string;
  scopes: string[];
  expiresAt: string; // YYYY-MM-DDTHH:mm
};

type State = {
  accounts: ServiceAccount[];
  loading: boolean;
  errorText: string | null;
  creating: boolean;
  create: CreateFormState;
  edit: EditFormState | null;
  savingEdit: boolean;
  expandedAccountId: string | null;
  creatingTokenFor: string | null;
  tokenCreate: TokenCreateFormState;
  creatingToken: boolean;
  newPlaintextToken: string | null;
  revokingTokenId: string | null;
};

type Action =
  | { type: "LOAD_START" }
  | { type: "LOAD_SUCCESS"; accounts: ServiceAccount[] }
  | { type: "LOAD_ERROR"; error: string }
  | { type: "SET_CREATE_FIELD"; field: keyof CreateFormState; value: string | boolean }
  | { type: "CREATE_START" }
  | { type: "CREATE_SUCCESS"; created: ServiceAccount }
  | { type: "CREATE_ERROR"; error: string }
  | { type: "START_EDIT"; edit: EditFormState }
  | { type: "CLOSE_EDIT" }
  | { type: "SET_EDIT_FIELD"; field: keyof EditFormState; value: string | boolean }
  | { type: "SAVE_EDIT_START" }
  | { type: "SAVE_EDIT_SUCCESS"; updated: ServiceAccount }
  | { type: "SAVE_EDIT_ERROR"; error: string }
  | { type: "TOGGLE_EXPAND"; id: string }
  | { type: "START_CREATE_TOKEN"; id: string }
  | { type: "CLOSE_CREATE_TOKEN" }
  | { type: "SET_TOKEN_CREATE_FIELD"; field: keyof TokenCreateFormState; value: unknown }
  | { type: "CREATE_TOKEN_START" }
  | { type: "CREATE_TOKEN_SUCCESS"; account: ServiceAccount; plaintext: string }
  | { type: "CREATE_TOKEN_ERROR"; error: string }
  | { type: "DISMISS_PLAINTEXT_TOKEN" }
  | { type: "REVOKE_TOKEN_START"; tokenId: string }
  | { type: "REVOKE_TOKEN_SUCCESS"; accountId: string; tokenId: string; revokedAt: string }
  | { type: "REVOKE_TOKEN_ERROR"; error: string };

const INITIAL_CREATE_FORM: CreateFormState = {
  name: "",
  description: "",
  enabled: true,
};

const INITIAL_TOKEN_CREATE_FORM: TokenCreateFormState = {
  serviceAccountId: "",
  scopes: [],
  expiresAt: "",
};

const INITIAL_STATE: State = {
  accounts: [],
  loading: true,
  errorText: null,
  creating: false,
  create: INITIAL_CREATE_FORM,
  edit: null,
  savingEdit: false,
  expandedAccountId: null,
  creatingTokenFor: null,
  tokenCreate: INITIAL_TOKEN_CREATE_FORM,
  creatingToken: false,
  newPlaintextToken: null,
  revokingTokenId: null,
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "LOAD_START":
      return { ...state, loading: true, errorText: null };
    case "LOAD_SUCCESS":
      return { ...state, loading: false, accounts: action.accounts, errorText: null };
    case "LOAD_ERROR":
      return { ...state, loading: false, errorText: action.error };
    case "SET_CREATE_FIELD":
      return { ...state, create: { ...state.create, [action.field]: action.value } };
    case "CREATE_START":
      return { ...state, creating: true, errorText: null };
    case "CREATE_SUCCESS":
      return {
        ...state,
        creating: false,
        accounts: [action.created, ...state.accounts],
        create: INITIAL_CREATE_FORM,
      };
    case "CREATE_ERROR":
      return { ...state, creating: false, errorText: action.error };
    case "START_EDIT":
      return { ...state, edit: action.edit };
    case "CLOSE_EDIT":
      return { ...state, edit: null };
    case "SET_EDIT_FIELD":
      return state.edit ? { ...state, edit: { ...state.edit, [action.field]: action.value } } : state;
    case "SAVE_EDIT_START":
      return { ...state, savingEdit: true, errorText: null };
    case "SAVE_EDIT_SUCCESS":
      return {
        ...state,
        savingEdit: false,
        accounts: state.accounts.map((a) => (a.id === action.updated.id ? action.updated : a)),
        edit: null,
      };
    case "SAVE_EDIT_ERROR":
      return { ...state, savingEdit: false, errorText: action.error };
    case "TOGGLE_EXPAND":
      return { ...state, expandedAccountId: state.expandedAccountId === action.id ? null : action.id };
    case "START_CREATE_TOKEN":
      return {
        ...state,
        creatingTokenFor: action.id,
        tokenCreate: { ...INITIAL_TOKEN_CREATE_FORM, serviceAccountId: action.id },
        newPlaintextToken: null,
      };
    case "CLOSE_CREATE_TOKEN":
      return { ...state, creatingTokenFor: null, tokenCreate: INITIAL_TOKEN_CREATE_FORM };
    case "SET_TOKEN_CREATE_FIELD":
      return { ...state, tokenCreate: { ...state.tokenCreate, [action.field]: action.value } };
    case "CREATE_TOKEN_START":
      return { ...state, creatingToken: true, errorText: null };
    case "CREATE_TOKEN_SUCCESS":
      return {
        ...state,
        creatingToken: false,
        creatingTokenFor: null,
        tokenCreate: INITIAL_TOKEN_CREATE_FORM,
        newPlaintextToken: action.plaintext,
        accounts: state.accounts.map((a) => (a.id === action.account.id ? action.account : a)),
      };
    case "CREATE_TOKEN_ERROR":
      return { ...state, creatingToken: false, errorText: action.error };
    case "DISMISS_PLAINTEXT_TOKEN":
      return { ...state, newPlaintextToken: null };
    case "REVOKE_TOKEN_START":
      return { ...state, revokingTokenId: action.tokenId, errorText: null };
    case "REVOKE_TOKEN_SUCCESS":
      return {
        ...state,
        revokingTokenId: null,
        accounts: state.accounts.map((a) => {
          if (a.id !== action.accountId) return a;
          return {
            ...a,
            tokens: a.tokens.map((t) =>
              t.id === action.tokenId ? { ...t, revoked_at: action.revokedAt, status: "revoked" } : t
            ),
          };
        }),
      };
    case "REVOKE_TOKEN_ERROR":
      return { ...state, revokingTokenId: null, errorText: action.error };
    default:
      return state;
  }
}

export default function AdminServiceAccountsRoute() {
  const { t } = useTranslation();
  const { authStatus } = useAuthHeaders();
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  function errorMessage(err: unknown, fallbackKey: string) {
    if (!(err instanceof Error)) return t(fallbackKey);
    const translationKey = SERVICE_ACCOUNT_ERROR_KEYS[err.message];
    return translationKey ? t(translationKey) : err.message;
  }

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (authStatus !== "authenticated") {
        dispatch({ type: "LOAD_SUCCESS", accounts: [] });
        return;
      }
      dispatch({ type: "LOAD_START" });
      try {
        const accounts = await listServiceAccounts();
        if (cancelled) return;
        dispatch({ type: "LOAD_SUCCESS", accounts });
      } catch (err) {
        if (cancelled) return;
        dispatch({ type: "LOAD_ERROR", error: errorMessage(err, "admin.serviceAccounts.errors.loadFailed") });
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [authStatus]);

  // Make sure we don't leak token state if component unmounts
  useEffect(() => {
    return () => {
      dispatch({ type: "DISMISS_PLAINTEXT_TOKEN" });
    };
  }, []);

  async function handleCreate() {
    if (authStatus !== "authenticated") return;
    dispatch({ type: "CREATE_START" });
    try {
      if (!state.create.name.trim()) throw new Error("admin.serviceAccounts.errors.nameRequired");
      const created = await createServiceAccount({
        name: state.create.name.trim(),
        description: state.create.description.trim() || null,
        enabled: state.create.enabled,
      });
      dispatch({ type: "CREATE_SUCCESS", created });
    } catch (err) {
      dispatch({ type: "CREATE_ERROR", error: errorMessage(err, "admin.serviceAccounts.errors.createFailed") });
    }
  }

  async function handleSaveEdit() {
    if (authStatus !== "authenticated" || !state.edit) return;
    dispatch({ type: "SAVE_EDIT_START" });
    try {
      if (!state.edit.name.trim()) throw new Error("admin.serviceAccounts.errors.nameRequired");
      const updated = await updateServiceAccount(state.edit.id, {
        name: state.edit.name.trim(),
        description: state.edit.description.trim() || null,
        enabled: state.edit.enabled,
      });
      dispatch({ type: "SAVE_EDIT_SUCCESS", updated });
    } catch (err) {
      dispatch({ type: "SAVE_EDIT_ERROR", error: errorMessage(err, "admin.serviceAccounts.errors.saveFailed") });
    }
  }

  async function handleCreateToken() {
    if (authStatus !== "authenticated" || !state.tokenCreate.serviceAccountId) return;
    dispatch({ type: "CREATE_TOKEN_START" });
    try {
      if (state.tokenCreate.scopes.length === 0) throw new Error("admin.serviceAccounts.errors.scopeRequired");
      let expires_at: string | null = null;
      if (state.tokenCreate.expiresAt) {
        expires_at = new Date(state.tokenCreate.expiresAt).toISOString();
      }
      const res = await createServiceAccountToken(state.tokenCreate.serviceAccountId, {
        scopes: state.tokenCreate.scopes,
        expires_at,
      });
      dispatch({ type: "CREATE_TOKEN_SUCCESS", account: res.service_account, plaintext: res.plaintext_token });
    } catch (err) {
      dispatch({ type: "CREATE_TOKEN_ERROR", error: errorMessage(err, "admin.serviceAccounts.errors.createTokenFailed") });
    }
  }

  async function handleRevokeToken(accountId: string, tokenId: string) {
    if (authStatus !== "authenticated") return;
    if (!confirm(t("admin.serviceAccounts.confirmRevoke"))) return;
    dispatch({ type: "REVOKE_TOKEN_START", tokenId });
    try {
      await revokeServiceAccountToken(tokenId);
      dispatch({ type: "REVOKE_TOKEN_SUCCESS", accountId, tokenId, revokedAt: new Date().toISOString() });
    } catch (err) {
      dispatch({ type: "REVOKE_TOKEN_ERROR", error: errorMessage(err, "admin.serviceAccounts.errors.revokeTokenFailed") });
    }
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-2.5">
        <h2 className="heading-gradient text-xl">{t("admin.serviceAccounts.title")}</h2>
        <span className="text-xs text-muted-foreground font-mono">/admin/service-accounts</span>
      </div>

      {state.errorText ? <p className="m-0 text-sm text-destructive">{state.errorText}</p> : null}

      {state.newPlaintextToken ? (
        <section className="glass-panel-accent p-5 border-emerald-500/50 bg-emerald-500/10">
          <div className="section-header mb-1 text-emerald-500">{t("admin.serviceAccounts.token.createdTitle")}</div>
          <div className="mt-2 text-sm text-muted-foreground">
            {t("admin.serviceAccounts.token.createdWarning")}
          </div>
          <div className="mt-3 flex items-center gap-2">
            <code className="px-3 py-1.5 bg-background border border-border rounded font-mono text-sm break-all">
              {state.newPlaintextToken}
            </code>
          </div>
          <div className="mt-4">
            <button type="button" className="btn-action" onClick={() => dispatch({ type: "DISMISS_PLAINTEXT_TOKEN" })}>
              {t("admin.serviceAccounts.actions.dismiss")}
            </button>
          </div>
        </section>
      ) : null}

      <section className="glass-panel-accent p-5">
        <div className="section-header mb-1">{t("admin.serviceAccounts.create.title")}</div>
        <div className="mt-2.5 grid gap-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="create-service-account-name">{t("admin.serviceAccounts.form.name")}</label>
              <input
                id="create-service-account-name"
                value={state.create.name}
                onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "name", value: e.target.value })}
                className="input-premium"
                placeholder={t("admin.serviceAccounts.form.namePlaceholder")}
              />
            </div>
            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="create-service-account-description">{t("admin.serviceAccounts.form.descriptionOptional")}</label>
              <input
                id="create-service-account-description"
                value={state.create.description}
                onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "description", value: e.target.value })}
                className="input-premium"
              />
            </div>
          </div>
          <div className="flex items-center gap-2.5">
            <label className="label-premium flex items-center">
              <input
                type="checkbox"
                checked={state.create.enabled}
                onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "enabled", value: e.target.checked })}
                className="mr-2"
              />
              {t("admin.serviceAccounts.form.enabled")}
            </label>
          </div>
          <div>
            <button type="button" onClick={() => void handleCreate()} disabled={state.creating} className="btn-primary-action">
              {state.creating ? t("admin.serviceAccounts.actions.creating") : t("admin.serviceAccounts.actions.create")}
            </button>
          </div>
        </div>
      </section>

      <section className="glass-panel p-5">
        <div className="flex items-baseline justify-between gap-2.5 section-header">
          <span>{t("admin.serviceAccounts.list.title")}</span>
          {state.loading ? <Skeleton className="h-3 w-16" /> : null}
        </div>
        {state.accounts.length === 0 && !state.loading ? (
          <p className="mt-2.5 mb-0 text-muted-foreground">{t("admin.serviceAccounts.list.empty")}</p>
        ) : null}
        {state.accounts.length > 0 ? (
          <div className="mt-4 grid gap-4">
            {state.accounts.map((a) => (
              <div key={a.id} className="border border-border/50 rounded-lg bg-background/50 overflow-hidden">
                <div className="p-4 flex items-center justify-between hover:bg-muted/30 transition-colors">
                  <div>
                    <div className="font-semibold text-sm flex items-center gap-2">
                      {a.name}
                      {!a.enabled && <span className="text-xs bg-destructive/20 text-destructive px-1.5 py-0.5 rounded">{t("admin.serviceAccounts.values.disabled")}</span>}
                    </div>
                    <div className="text-xs text-muted-foreground font-mono mt-0.5">{a.id}</div>
                  </div>
                  <div className="flex gap-2">
                    <button type="button" className="btn-action text-xs" onClick={() => dispatch({ type: "START_EDIT", edit: { id: a.id, name: a.name, description: a.description || "", enabled: a.enabled } })}>{t("admin.serviceAccounts.actions.edit")}</button>
                    <button type="button" className="btn-action text-xs" onClick={() => dispatch({ type: "TOGGLE_EXPAND", id: a.id })}>
                      {state.expandedAccountId === a.id ? t("admin.serviceAccounts.actions.hideTokens") : t("admin.serviceAccounts.actions.tokens")}
                    </button>
                  </div>
                </div>

                {state.expandedAccountId === a.id ? (
                  <div className="p-4 border-t border-border/50 bg-background/80">
                    <div className="flex justify-between items-center mb-3">
                      <div className="text-sm font-semibold">{t("admin.serviceAccounts.token.listTitle")}</div>
                      <button type="button" className="btn-primary-action text-xs py-1" onClick={() => dispatch({ type: "START_CREATE_TOKEN", id: a.id })}>
                        {t("admin.serviceAccounts.actions.newToken")}
                      </button>
                    </div>

                    {state.creatingTokenFor === a.id ? (
                      <div className="mb-4 p-3 border border-border/50 rounded-lg bg-muted/20">
                        <div className="text-sm font-medium mb-2">{t("admin.serviceAccounts.token.createTitle")}</div>
                        <div className="grid gap-3">
                          <div>
                            <label className="label-premium">{t("admin.serviceAccounts.token.scopesLabel")}</label>
                            <div className="mt-1 flex flex-wrap gap-3">
                              {ALL_SCOPES.map((scope) => (
                                <label key={scope} className="flex items-start text-sm gap-1.5 max-w-xs">
                                  <input
                                    type="checkbox"
                                    value={scope}
                                    checked={state.tokenCreate.scopes.includes(scope)}
                                    onChange={(e) => {
                                      const newScopes = e.target.checked
                                        ? [...state.tokenCreate.scopes, scope]
                                        : state.tokenCreate.scopes.filter((s) => s !== scope);
                                      dispatch({ type: "SET_TOKEN_CREATE_FIELD", field: "scopes", value: newScopes });
                                    }}
                                  />
                                  <span>
                                    <span>{t(`admin.serviceAccounts.scopes.${SCOPE_I18N_KEYS[scope]}.label`)}</span>
                                    <span className="block text-[11px] text-muted-foreground">{t(`admin.serviceAccounts.scopes.${SCOPE_I18N_KEYS[scope]}.description`)}</span>
                                    <code className="block font-mono text-[11px] text-muted-foreground">{scope}</code>
                                  </span>
                                </label>
                              ))}
                            </div>
                          </div>
                          <div>
                            <label className="label-premium" htmlFor="create-token-expires-at">{t("admin.serviceAccounts.token.expiresAtOptional")}</label>
                            <input
                              id="create-token-expires-at"
                              type="datetime-local"
                              value={state.tokenCreate.expiresAt}
                              onChange={(e) => dispatch({ type: "SET_TOKEN_CREATE_FIELD", field: "expiresAt", value: e.target.value })}
                              className="input-premium max-w-sm block mt-1"
                            />
                          </div>
                          <div className="flex gap-2">
                            <button type="button" disabled={state.creatingToken} className="btn-primary-action py-1 px-3 text-sm" onClick={() => void handleCreateToken()}>
                              {state.creatingToken ? t("admin.serviceAccounts.actions.creating") : t("admin.serviceAccounts.actions.confirmCreate")}
                            </button>
                            <button type="button" className="btn-action py-1 px-3 text-sm" onClick={() => dispatch({ type: "CLOSE_CREATE_TOKEN" })}>
                              {t("admin.serviceAccounts.actions.cancel")}
                            </button>
                          </div>
                        </div>
                      </div>
                    ) : null}

                    {a.tokens.length === 0 ? (
                      <div className="text-xs text-muted-foreground">{t("admin.serviceAccounts.token.empty")}</div>
                    ) : (
                      <div className="grid gap-2">
                        {a.tokens.map((token) => (
                          <div key={token.id} className="flex items-center justify-between text-sm p-2 bg-background border border-border/30 rounded">
                            <div>
                              <div className="font-mono">{token.token_prefix}...</div>
                              <div className="text-xs text-muted-foreground mt-0.5">
                                {t("admin.serviceAccounts.token.statusScopes", { status: token.status, scopes: token.scopes.join(", ") })}
                                {token.expires_at ? t("admin.serviceAccounts.token.expires", { date: new Date(token.expires_at).toLocaleString() }) : ""}
                              </div>
                            </div>
                            <div>
                              {token.status === "active" ? (
                                <button
                                  type="button"
                                  disabled={state.revokingTokenId === token.id}
                                  onClick={() => void handleRevokeToken(a.id, token.id)}
                                  className="text-xs text-destructive hover:underline"
                                >
                                  {state.revokingTokenId === token.id ? t("admin.serviceAccounts.actions.revoking") : t("admin.serviceAccounts.actions.revoke")}
                                </button>
                              ) : null}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}
      </section>

      {state.edit ? (
        <section className="glass-panel-accent p-5">
          <div className="flex items-baseline justify-between gap-2.5">
            <div className="section-header">{t("admin.serviceAccounts.edit.title")}</div>
            <button type="button" onClick={() => dispatch({ type: "CLOSE_EDIT" })} className="btn-action">
              {t("admin.serviceAccounts.actions.close")}
            </button>
          </div>
          <div className="mt-1.5 text-xs text-muted-foreground">{state.edit.id}</div>
          <div className="mt-2.5 grid gap-3">
            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="edit-service-account-name">{t("admin.serviceAccounts.form.name")}</label>
              <input
                id="edit-service-account-name"
                value={state.edit.name}
                onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "name", value: e.target.value })}
                className="input-premium"
              />
            </div>
            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="edit-service-account-description">{t("admin.serviceAccounts.form.description")}</label>
              <input
                id="edit-service-account-description"
                value={state.edit.description}
                onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "description", value: e.target.value })}
                className="input-premium"
              />
            </div>
            <label className="flex items-center text-xs text-muted-foreground mt-2">
              <input
                type="checkbox"
                checked={state.edit.enabled}
                onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "enabled", value: e.target.checked })}
                className="mr-2"
              />
              {t("admin.serviceAccounts.form.enabled")}
            </label>
            <div className="mt-2">
              <button type="button" onClick={() => void handleSaveEdit()} disabled={state.savingEdit} className="btn-primary-action">
                {state.savingEdit ? t("admin.serviceAccounts.actions.saving") : t("admin.serviceAccounts.actions.save")}
              </button>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
