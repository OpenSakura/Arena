import { useEffect, useReducer } from "react";
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
  const { headers } = useAuthHeaders();
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!headers) {
        dispatch({ type: "LOAD_SUCCESS", accounts: [] });
        return;
      }
      dispatch({ type: "LOAD_START" });
      try {
        const accounts = await listServiceAccounts(headers);
        if (cancelled) return;
        dispatch({ type: "LOAD_SUCCESS", accounts });
      } catch (err) {
        if (cancelled) return;
        dispatch({ type: "LOAD_ERROR", error: err instanceof Error ? err.message : "Failed to load accounts" });
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [headers]);

  // Make sure we don't leak token state if component unmounts
  useEffect(() => {
    return () => {
      dispatch({ type: "DISMISS_PLAINTEXT_TOKEN" });
    };
  }, []);

  async function handleCreate() {
    if (!headers) return;
    dispatch({ type: "CREATE_START" });
    try {
      if (!state.create.name.trim()) throw new Error("name is required");
      const created = await createServiceAccount({
        name: state.create.name.trim(),
        description: state.create.description.trim() || null,
        enabled: state.create.enabled,
      }, headers);
      dispatch({ type: "CREATE_SUCCESS", created });
    } catch (err) {
      dispatch({ type: "CREATE_ERROR", error: err instanceof Error ? err.message : "Failed to create" });
    }
  }

  async function handleSaveEdit() {
    if (!headers || !state.edit) return;
    dispatch({ type: "SAVE_EDIT_START" });
    try {
      if (!state.edit.name.trim()) throw new Error("name is required");
      const updated = await updateServiceAccount(state.edit.id, {
        name: state.edit.name.trim(),
        description: state.edit.description.trim() || null,
        enabled: state.edit.enabled,
      }, headers);
      dispatch({ type: "SAVE_EDIT_SUCCESS", updated });
    } catch (err) {
      dispatch({ type: "SAVE_EDIT_ERROR", error: err instanceof Error ? err.message : "Failed to save" });
    }
  }

  async function handleCreateToken() {
    if (!headers || !state.tokenCreate.serviceAccountId) return;
    dispatch({ type: "CREATE_TOKEN_START" });
    try {
      if (state.tokenCreate.scopes.length === 0) throw new Error("at least one scope is required");
      let expires_at: string | null = null;
      if (state.tokenCreate.expiresAt) {
        expires_at = new Date(state.tokenCreate.expiresAt).toISOString();
      }
      const res = await createServiceAccountToken(state.tokenCreate.serviceAccountId, {
        scopes: state.tokenCreate.scopes,
        expires_at,
      }, headers);
      dispatch({ type: "CREATE_TOKEN_SUCCESS", account: res.service_account, plaintext: res.plaintext_token });
    } catch (err) {
      dispatch({ type: "CREATE_TOKEN_ERROR", error: err instanceof Error ? err.message : "Failed to create token" });
    }
  }

  async function handleRevokeToken(accountId: string, tokenId: string) {
    if (!headers) return;
    if (!confirm("Are you sure you want to revoke this token?")) return;
    dispatch({ type: "REVOKE_TOKEN_START", tokenId });
    try {
      await revokeServiceAccountToken(tokenId, headers);
      dispatch({ type: "REVOKE_TOKEN_SUCCESS", accountId, tokenId, revokedAt: new Date().toISOString() });
    } catch (err) {
      dispatch({ type: "REVOKE_TOKEN_ERROR", error: err instanceof Error ? err.message : "Failed to revoke token" });
    }
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-2.5">
        <h2 className="heading-gradient text-xl">Service Accounts</h2>
        <span className="text-xs text-muted-foreground font-mono">/admin/service-accounts</span>
      </div>

      {state.errorText ? <p className="m-0 text-sm text-destructive">{state.errorText}</p> : null}

      {state.newPlaintextToken ? (
        <section className="glass-panel-accent p-5 border-emerald-500/50 bg-emerald-500/10">
          <div className="section-header mb-1 text-emerald-500">Token created</div>
          <div className="mt-2 text-sm text-muted-foreground">
            Copy now. This token will not be shown again.
          </div>
          <div className="mt-3 flex items-center gap-2">
            <code className="px-3 py-1.5 bg-background border border-border rounded font-mono text-sm break-all">
              {state.newPlaintextToken}
            </code>
          </div>
          <div className="mt-4">
            <button type="button" className="btn-action" onClick={() => dispatch({ type: "DISMISS_PLAINTEXT_TOKEN" })}>
              Dismiss
            </button>
          </div>
        </section>
      ) : null}

      <section className="glass-panel-accent p-5">
        <div className="section-header mb-1">Create service account</div>
        <div className="mt-2.5 grid gap-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <label className="label-premium">Name</label>
              <input
                value={state.create.name}
                onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "name", value: e.target.value })}
                className="input-premium"
                placeholder="e.g., CI/CD Bot"
              />
            </div>
            <div className="grid gap-1.5">
              <label className="label-premium">Description (optional)</label>
              <input
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
              enabled
            </label>
          </div>
          <div>
            <button type="button" onClick={() => void handleCreate()} disabled={state.creating} className="btn-primary-action">
              {state.creating ? "Creating..." : "Create"}
            </button>
          </div>
        </div>
      </section>

      <section className="glass-panel p-5">
        <div className="flex items-baseline justify-between gap-2.5 section-header">
          <span>Accounts</span>
          {state.loading ? <Skeleton className="h-3 w-16" /> : null}
        </div>
        {state.accounts.length === 0 && !state.loading ? (
          <p className="mt-2.5 mb-0 text-muted-foreground">No service accounts found.</p>
        ) : null}
        {state.accounts.length > 0 ? (
          <div className="mt-4 grid gap-4">
            {state.accounts.map((a) => (
              <div key={a.id} className="border border-border/50 rounded-lg bg-background/50 overflow-hidden">
                <div className="p-4 flex items-center justify-between hover:bg-muted/30 transition-colors">
                  <div>
                    <div className="font-semibold text-sm flex items-center gap-2">
                      {a.name}
                      {!a.enabled && <span className="text-xs bg-destructive/20 text-destructive px-1.5 py-0.5 rounded">disabled</span>}
                    </div>
                    <div className="text-xs text-muted-foreground font-mono mt-0.5">{a.id}</div>
                  </div>
                  <div className="flex gap-2">
                    <button type="button" className="btn-action text-xs" onClick={() => dispatch({ type: "START_EDIT", edit: { id: a.id, name: a.name, description: a.description || "", enabled: a.enabled } })}>Edit</button>
                    <button type="button" className="btn-action text-xs" onClick={() => dispatch({ type: "TOGGLE_EXPAND", id: a.id })}>
                      {state.expandedAccountId === a.id ? "Hide Tokens" : "Tokens"}
                    </button>
                  </div>
                </div>

                {state.expandedAccountId === a.id ? (
                  <div className="p-4 border-t border-border/50 bg-background/80">
                    <div className="flex justify-between items-center mb-3">
                      <div className="text-sm font-semibold">Tokens</div>
                      <button type="button" className="btn-primary-action text-xs py-1" onClick={() => dispatch({ type: "START_CREATE_TOKEN", id: a.id })}>
                        New Token
                      </button>
                    </div>

                    {state.creatingTokenFor === a.id ? (
                      <div className="mb-4 p-3 border border-border/50 rounded-lg bg-muted/20">
                        <div className="text-sm font-medium mb-2">Create Token</div>
                        <div className="grid gap-3">
                          <div>
                            <label className="label-premium">Scopes</label>
                            <div className="mt-1 flex flex-wrap gap-3">
                              {ALL_SCOPES.map((scope) => (
                                <label key={scope} className="flex items-center text-sm gap-1.5">
                                  <input
                                    type="checkbox"
                                    checked={state.tokenCreate.scopes.includes(scope)}
                                    onChange={(e) => {
                                      const newScopes = e.target.checked
                                        ? [...state.tokenCreate.scopes, scope]
                                        : state.tokenCreate.scopes.filter((s) => s !== scope);
                                      dispatch({ type: "SET_TOKEN_CREATE_FIELD", field: "scopes", value: newScopes });
                                    }}
                                  />
                                  {scope}
                                </label>
                              ))}
                            </div>
                          </div>
                          <div>
                            <label className="label-premium">Expires At (optional)</label>
                            <input
                              type="datetime-local"
                              value={state.tokenCreate.expiresAt}
                              onChange={(e) => dispatch({ type: "SET_TOKEN_CREATE_FIELD", field: "expiresAt", value: e.target.value })}
                              className="input-premium max-w-sm block mt-1"
                            />
                          </div>
                          <div className="flex gap-2">
                            <button type="button" disabled={state.creatingToken} className="btn-primary-action py-1 px-3 text-sm" onClick={() => void handleCreateToken()}>
                              {state.creatingToken ? "Creating..." : "Confirm Create"}
                            </button>
                            <button type="button" className="btn-action py-1 px-3 text-sm" onClick={() => dispatch({ type: "CLOSE_CREATE_TOKEN" })}>
                              Cancel
                            </button>
                          </div>
                        </div>
                      </div>
                    ) : null}

                    {a.tokens.length === 0 ? (
                      <div className="text-xs text-muted-foreground">No tokens.</div>
                    ) : (
                      <div className="grid gap-2">
                        {a.tokens.map((t) => (
                          <div key={t.id} className="flex items-center justify-between text-sm p-2 bg-background border border-border/30 rounded">
                            <div>
                              <div className="font-mono">{t.token_prefix}...</div>
                              <div className="text-xs text-muted-foreground mt-0.5">
                                {t.status} • scopes: {t.scopes.join(", ")}
                                {t.expires_at ? ` • expires: ${new Date(t.expires_at).toLocaleString()}` : ""}
                              </div>
                            </div>
                            <div>
                              {t.status === "active" ? (
                                <button
                                  type="button"
                                  disabled={state.revokingTokenId === t.id}
                                  onClick={() => void handleRevokeToken(a.id, t.id)}
                                  className="text-xs text-destructive hover:underline"
                                >
                                  {state.revokingTokenId === t.id ? "..." : "Revoke"}
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
            <div className="section-header">Edit service account</div>
            <button type="button" onClick={() => dispatch({ type: "CLOSE_EDIT" })} className="btn-action">
              Close
            </button>
          </div>
          <div className="mt-1.5 text-xs text-muted-foreground">{state.edit.id}</div>
          <div className="mt-2.5 grid gap-3">
            <div className="grid gap-1.5">
              <label className="label-premium">Name</label>
              <input
                value={state.edit.name}
                onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "name", value: e.target.value })}
                className="input-premium"
              />
            </div>
            <div className="grid gap-1.5">
              <label className="label-premium">Description</label>
              <input
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
              enabled
            </label>
            <div className="mt-2">
              <button type="button" onClick={() => void handleSaveEdit()} disabled={state.savingEdit} className="btn-primary-action">
                {state.savingEdit ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
