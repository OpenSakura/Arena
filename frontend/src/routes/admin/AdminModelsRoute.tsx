/**
 * frontend/src/routes/admin/AdminModelsRoute.tsx
 *
 * Admin: model registry CRUD.
 *
 * Notes:
 * - This UI should be protected (admins only).
 */


import { useEffect, useReducer } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useAuthHeaders } from "@/hooks/useAuthHeaders";
import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api";
import { parseJsonObjectOrNull, parseNumberOrNull } from "@/lib/adminParsers";
import { isRecord } from "@/lib/typeGuards";

type ModelAdmin = {
  id: string;
  display_name: string;
  provider_type: string;
  model_name: string;
  base_url: string;
  enabled: boolean;
  visibility: string;
  tags: Record<string, unknown> | null;
  temperature: number | null;
  frequency_penalty: number | null;
  presence_penalty: number | null;
  system_prompt: string | null;
  user_prompt: string | null;
  params: Record<string, unknown> | null;
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
};

type ListModelsResponse = { models: ModelAdmin[] };

type ModelTestResponse = {
  ok: boolean;
  note?: string;
  model_id: string;
  has_api_key: boolean;
};

type EditState = {
  id: string;
  display_name: string;
  provider_type: string;
  model_name: string;
  base_url: string;
  enabled: boolean;
  visibility: string;
  tagsText: string;
  temperatureText: string;
  frequencyPenaltyText: string;
  presencePenaltyText: string;
  systemPromptText: string;
  userPromptText: string;
  paramsText: string;
  apiKeyText: string;
  clearApiKey: boolean;
};

type CreateFormState = {
  displayName: string;
  providerType: string;
  modelName: string;
  baseUrl: string;
  enabled: boolean;
  visibility: string;
  apiKey: string;
  temperature: string;
  frequencyPenalty: string;
  presencePenalty: string;
  systemPrompt: string;
  userPrompt: string;
  tagsText: string;
  paramsText: string;
};

type State = {
  models: ModelAdmin[];
  loading: boolean;
  errorText: string | null;
  creating: boolean;
  create: CreateFormState;
  edit: EditState | null;
  savingEdit: boolean;
  testResult: ModelTestResponse | null;
};

type Action =
  | { type: "LOAD_START" }
  | { type: "LOAD_SUCCESS"; models: ModelAdmin[] }
  | { type: "LOAD_ERROR"; error: string }
  | { type: "SET_CREATE_FIELD"; field: keyof CreateFormState; value: string | boolean }
  | { type: "CREATE_START" }
  | { type: "CREATE_SUCCESS"; created: ModelAdmin }
  | { type: "CREATE_ERROR"; error: string }
  | { type: "START_EDIT"; edit: EditState }
  | { type: "CLOSE_EDIT" }
  | { type: "SET_EDIT_FIELD"; field: keyof EditState; value: string | boolean }
  | { type: "SAVE_EDIT_START" }
  | { type: "SAVE_EDIT_SUCCESS"; updated: ModelAdmin }
  | { type: "SAVE_EDIT_ERROR"; error: string }
  | { type: "DELETE_SUCCESS"; id: string }
  | { type: "DELETE_ERROR"; error: string }
  | { type: "TEST_SUCCESS"; result: ModelTestResponse }
  | { type: "TEST_ERROR"; error: string }
  | { type: "CLEAR_TEST_RESULT" };

const INITIAL_CREATE_FORM: CreateFormState = {
  displayName: "",
  providerType: "openai_compat",
  modelName: "",
  baseUrl: "",
  enabled: true,
  visibility: "public",
  apiKey: "",
  temperature: "",
  frequencyPenalty: "",
  presencePenalty: "",
  systemPrompt: "",
  userPrompt: "",
  tagsText: "",
  paramsText: "",
};

const INITIAL_STATE: State = {
  models: [],
  loading: true,
  errorText: null,
  creating: false,
  create: INITIAL_CREATE_FORM,
  edit: null,
  savingEdit: false,
  testResult: null,
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "LOAD_START":
      return { ...state, loading: true, errorText: null };
    case "LOAD_SUCCESS":
      return { ...state, loading: false, models: action.models, errorText: null };
    case "LOAD_ERROR":
      return { ...state, loading: false, errorText: action.error };
    case "SET_CREATE_FIELD":
      return {
        ...state,
        create: { ...state.create, [action.field]: action.value },
      };
    case "CREATE_START":
      return { ...state, creating: true, errorText: null };
    case "CREATE_SUCCESS":
      return {
        ...state,
        creating: false,
        models: [action.created, ...state.models],
        create: {
          ...INITIAL_CREATE_FORM,
          providerType: state.create.providerType,
          enabled: state.create.enabled,
          visibility: state.create.visibility,
        },
      };
    case "CREATE_ERROR":
      return { ...state, creating: false, errorText: action.error };
    case "START_EDIT":
      return { ...state, edit: action.edit, testResult: null };
    case "CLOSE_EDIT":
      return { ...state, edit: null };
    case "SET_EDIT_FIELD":
      return state.edit
        ? {
            ...state,
            edit: { ...state.edit, [action.field]: action.value },
          }
        : state;
    case "SAVE_EDIT_START":
      return { ...state, savingEdit: true, errorText: null, testResult: null };
    case "SAVE_EDIT_SUCCESS":
      return {
        ...state,
        savingEdit: false,
        models: state.models.map((model) => (model.id === action.updated.id ? action.updated : model)),
        edit: state.edit ? toEditState(action.updated) : state.edit,
      };
    case "SAVE_EDIT_ERROR":
      return { ...state, savingEdit: false, errorText: action.error };
    case "DELETE_SUCCESS":
      return {
        ...state,
        errorText: null,
        testResult: null,
        models: state.models.filter((model) => model.id !== action.id),
        edit: state.edit?.id === action.id ? null : state.edit,
      };
    case "DELETE_ERROR":
      return { ...state, errorText: action.error };
    case "TEST_SUCCESS":
      return { ...state, errorText: null, testResult: action.result };
    case "TEST_ERROR":
      return { ...state, errorText: action.error };
    case "CLEAR_TEST_RESULT":
      return { ...state, testResult: null };
    default:
      return state;
  }
}

function isModelAdmin(value: unknown): value is ModelAdmin {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.display_name === "string" &&
    typeof value.provider_type === "string" &&
    typeof value.model_name === "string" &&
    typeof value.base_url === "string" &&
    typeof value.enabled === "boolean" &&
    typeof value.visibility === "string" &&
    (value.tags === null || isRecord(value.tags)) &&
    (typeof value.temperature === "number" || value.temperature === null) &&
    (typeof value.frequency_penalty === "number" || value.frequency_penalty === null) &&
    (typeof value.presence_penalty === "number" || value.presence_penalty === null) &&
    (typeof value.system_prompt === "string" || value.system_prompt === null) &&
    (typeof value.user_prompt === "string" || value.user_prompt === null) &&
    (value.params === null || isRecord(value.params)) &&
    typeof value.has_api_key === "boolean" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string"
  );
}

function isModelTestResponse(value: unknown): value is ModelTestResponse {
  return (
    isRecord(value) &&
    typeof value.ok === "boolean" &&
    typeof value.model_id === "string" &&
    typeof value.has_api_key === "boolean" &&
    (value.note === undefined || typeof value.note === "string")
  );
}

function parseListModelsResponse(value: unknown): ListModelsResponse {
  if (!isRecord(value) || !Array.isArray(value.models)) {
    throw new Error("Invalid models response");
  }

  const models = value.models.filter(isModelAdmin);
  if (models.length !== value.models.length) {
    throw new Error("Invalid models response");
  }

  return { models };
}

function parseModelAdmin(value: unknown): ModelAdmin {
  if (!isModelAdmin(value)) {
    throw new Error("Invalid model response");
  }

  return value;
}

function parseModelTestResponse(value: unknown): ModelTestResponse {
  if (!isModelTestResponse(value)) {
    throw new Error("Invalid model test response");
  }

  return value;
}

function toEditState(model: ModelAdmin): EditState {
  return {
    id: model.id,
    display_name: model.display_name,
    provider_type: model.provider_type,
    model_name: model.model_name,
    base_url: model.base_url,
    enabled: model.enabled,
    visibility: model.visibility,
    tagsText: model.tags ? JSON.stringify(model.tags, null, 2) : "",
    temperatureText: model.temperature === null ? "" : String(model.temperature),
    frequencyPenaltyText:
      model.frequency_penalty === null ? "" : String(model.frequency_penalty),
    presencePenaltyText:
      model.presence_penalty === null ? "" : String(model.presence_penalty),
    systemPromptText: model.system_prompt || "",
    userPromptText: model.user_prompt || "",
    paramsText: model.params ? JSON.stringify(model.params, null, 2) : "",
    apiKeyText: "",
    clearApiKey: false,
  };
}

export default function AdminModelsRoute() {
  const { headers } = useAuthHeaders();
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!headers) {
        dispatch({ type: "LOAD_SUCCESS", models: [] });
        return;
      }

      dispatch({ type: "LOAD_START" });
      try {
        const res = parseListModelsResponse(await apiGet("/admin/models?limit=1000", { headers }));
        if (cancelled) return;
        dispatch({ type: "LOAD_SUCCESS", models: res.models });
      } catch (err) {
        if (cancelled) return;
        dispatch({
          type: "LOAD_ERROR",
          error: err instanceof Error ? err.message : "Failed to load models",
        });
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [headers]);

  async function handleCreate() {
    if (!headers) return;

    dispatch({ type: "CREATE_START" });
    try {
      if (!state.create.displayName.trim()) throw new Error("display_name is required");
      if (!state.create.providerType.trim()) throw new Error("provider_type is required");
      if (!state.create.modelName.trim()) throw new Error("model_name is required");
      if (!state.create.baseUrl.trim()) throw new Error("base_url is required");

      const payload: Record<string, unknown> = {
        display_name: state.create.displayName.trim(),
        provider_type: state.create.providerType.trim(),
        model_name: state.create.modelName.trim(),
        base_url: state.create.baseUrl.trim(),
        enabled: state.create.enabled,
        visibility: state.create.visibility,
      };

      if (state.create.apiKey.trim()) payload.api_key = state.create.apiKey.trim();

      const temp = parseNumberOrNull(state.create.temperature);
      const fp = parseNumberOrNull(state.create.frequencyPenalty);
      const pp = parseNumberOrNull(state.create.presencePenalty);
      if (temp !== null) payload.temperature = temp;
      if (fp !== null) payload.frequency_penalty = fp;
      if (pp !== null) payload.presence_penalty = pp;

      const tags = parseJsonObjectOrNull(state.create.tagsText);
      const params = parseJsonObjectOrNull(state.create.paramsText);
      if (tags) payload.tags = tags;
      if (params) payload.params = params;

      payload.system_prompt = state.create.systemPrompt.trim() || null;
      payload.user_prompt = state.create.userPrompt.trim() || null;

      const created = parseModelAdmin(await apiPost("/admin/models", payload, { headers }));
      dispatch({ type: "CREATE_SUCCESS", created });
    } catch (err) {
      dispatch({
        type: "CREATE_ERROR",
        error: err instanceof Error ? err.message : "Failed to create model",
      });
    }
  }

  async function handleSaveEdit() {
    if (!headers || !state.edit) return;

    dispatch({ type: "SAVE_EDIT_START" });
    try {
      if (!state.edit.display_name.trim()) throw new Error("display_name is required");
      if (!state.edit.provider_type.trim()) throw new Error("provider_type is required");
      if (!state.edit.model_name.trim()) throw new Error("model_name is required");
      if (!state.edit.base_url.trim()) throw new Error("base_url is required");

      const patch: Record<string, unknown> = {
        display_name: state.edit.display_name.trim(),
        provider_type: state.edit.provider_type.trim(),
        model_name: state.edit.model_name.trim(),
        base_url: state.edit.base_url.trim(),
        enabled: state.edit.enabled,
        visibility: state.edit.visibility,
      };

      patch.temperature = parseNumberOrNull(state.edit.temperatureText);
      patch.frequency_penalty = parseNumberOrNull(state.edit.frequencyPenaltyText);
      patch.presence_penalty = parseNumberOrNull(state.edit.presencePenaltyText);
      patch.system_prompt = state.edit.systemPromptText.trim() || null;
      patch.user_prompt = state.edit.userPromptText.trim() || null;
      patch.tags = parseJsonObjectOrNull(state.edit.tagsText);
      patch.params = parseJsonObjectOrNull(state.edit.paramsText);

      if (state.edit.clearApiKey) {
        patch.api_key = null;
      } else if (state.edit.apiKeyText.trim()) {
        patch.api_key = state.edit.apiKeyText.trim();
      }

      const updated = parseModelAdmin(
        await apiPut(`/admin/models/${encodeURIComponent(state.edit.id)}`, patch, { headers }),
      );

      dispatch({ type: "SAVE_EDIT_SUCCESS", updated });
    } catch (err) {
      dispatch({
        type: "SAVE_EDIT_ERROR",
        error: err instanceof Error ? err.message : "Failed to save model",
      });
    }
  }

  async function handleDelete(id: string) {
    if (!headers) return;
    if (!confirm("Delete this model?")) return;

    dispatch({ type: "CLEAR_TEST_RESULT" });
    try {
      await apiDelete(`/admin/models/${encodeURIComponent(id)}`, { headers });
      dispatch({ type: "DELETE_SUCCESS", id });
    } catch (err) {
      dispatch({
        type: "DELETE_ERROR",
        error: err instanceof Error ? err.message : "Failed to delete model",
      });
    }
  }

  async function handleTest(id: string) {
    if (!headers) return;

    dispatch({ type: "CLEAR_TEST_RESULT" });
    try {
      const result = parseModelTestResponse(
        await apiPost(`/admin/models/${encodeURIComponent(id)}/test`, {}, { headers }),
      );
      dispatch({ type: "TEST_SUCCESS", result });
    } catch (err) {
      dispatch({
        type: "TEST_ERROR",
        error: err instanceof Error ? err.message : "Failed to test model",
      });
    }
  }

  const { models, loading, errorText, creating, create, edit, savingEdit, testResult } = state;

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-2.5">
        <h2 className="heading-gradient text-xl">Model Registry</h2>
        <span className="text-xs text-muted-foreground font-mono">/admin/models</span>
      </div>

      {errorText ? <p className="m-0 text-sm text-destructive">{errorText}</p> : null}

      <section className="glass-panel-accent p-5">
          <div className="section-header mb-1">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="section-header-icon" aria-hidden>
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Create model
          </div>
          <div className="mt-2.5 grid gap-2.5">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-display-name">
                  Display name
                </label>
                <input
                  id="create-display-name"
                  value={create.displayName}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "displayName", value: e.target.value })}
                  className="input-premium"
                  placeholder="e.g., gpt-4o-mini (gateway)"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-provider-type">
                  Provider type
                </label>
                <input
                  id="create-provider-type"
                  value={create.providerType}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "providerType", value: e.target.value })}
                  className="input-premium"
                  placeholder="openai_compat"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-model-name">
                  Model name
                </label>
                <input
                  id="create-model-name"
                  value={create.modelName}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "modelName", value: e.target.value })}
                  className="input-premium"
                  placeholder="e.g., gpt-4o-mini"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-base-url">
                  Base URL
                </label>
                <input
                  id="create-base-url"
                  value={create.baseUrl}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "baseUrl", value: e.target.value })}
                  className="input-premium"
                  placeholder="https://gateway.example.com (or .../v1)"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-api-key">
                  API key (optional)
                </label>
                <input
                  id="create-api-key"
                  type="password"
                  value={create.apiKey}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "apiKey", value: e.target.value })}
                  className="input-premium"
                  placeholder="stored encrypted at rest"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-temp">
                  temperature
                </label>
                <input
                  id="create-temp"
                  value={create.temperature}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "temperature", value: e.target.value })}
                  className="input-premium"
                  placeholder="(optional)"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-fp">
                  frequency_penalty
                </label>
                <input
                  id="create-fp"
                  value={create.frequencyPenalty}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "frequencyPenalty", value: e.target.value })}
                  className="input-premium"
                  placeholder="(optional)"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-pp">
                  presence_penalty
                </label>
                <input
                  id="create-pp"
                  value={create.presencePenalty}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "presencePenalty", value: e.target.value })}
                  className="input-premium"
                  placeholder="(optional)"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-system-prompt">
                  system_prompt (leave blank for default)
                </label>
                <textarea
                  id="create-system-prompt"
                  value={create.systemPrompt}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "systemPrompt", value: e.target.value })}
                  className="textarea-premium"
                  rows={4}
                  placeholder="You are an expert translator..."
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-user-prompt">
                  user_prompt (leave blank for default)
                </label>
                <textarea
                  id="create-user-prompt"
                  value={create.userPrompt}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "userPrompt", value: e.target.value })}
                  className="textarea-premium"
                  rows={4}
                  placeholder={"Translate the following from {{ source_lang }} to {{ target_lang }}:\n{{ source_text }}"}
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Supported prompt tokens:{" "}
              <code className="font-mono">{"{{ source_text }}"}</code>,{" "}
              <code className="font-mono">{"{{ source_lang }}"}</code>,{" "}
              <code className="font-mono">{"{{ target_lang }}"}</code>.
              Leave both prompts blank to use the built-in defaults.
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-tags">
                  tags (JSON object)
                </label>
                <textarea
                  id="create-tags"
                  value={create.tagsText}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "tagsText", value: e.target.value })}
                  className="textarea-premium"
                  rows={4}
                  placeholder='{"family":"openai","tier":"cheap"}'
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-visibility">
                  visibility
                </label>
                <select
                  id="create-visibility"
                  value={create.visibility}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "visibility", value: e.target.value })}
                  className="input-premium"
                >
                  <option value="public">public</option>
                  <option value="private">private</option>
                </select>

                <label className="label-premium mt-2.5 flex items-center">
                  <input
                    type="checkbox"
                    checked={create.enabled}
                    onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "enabled", value: e.target.checked })}
                    className="mr-2"
                  />
                  enabled
                </label>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-params">
                  params (JSON object)
                </label>
                <textarea
                  id="create-params"
                  value={create.paramsText}
                  onChange={(e) => dispatch({ type: "SET_CREATE_FIELD", field: "paramsText", value: e.target.value })}
                  className="textarea-premium"
                  rows={4}
                  placeholder='{"route":"jp2zh","top_p":0.95,"max_tokens":1024}'
                />
              </div>
            </div>

            <div className="flex items-center gap-2.5">
              <button type="button" onClick={() => void handleCreate()} disabled={creating} className="btn-primary-action">
                {creating ? "Creating..." : "Create"}
              </button>
              <span className="text-xs text-muted-foreground">
                Model API keys are never returned by the backend.
              </span>
            </div>
          </div>
        </section>

      <section className="glass-panel p-5">
          <div className="flex items-baseline justify-between gap-2.5 section-header">
            <span>Models</span>
            {loading ? <Skeleton className="h-3 w-16" /> : null}
          </div>

          {models.length === 0 && !loading ? (
            <p className="mt-2.5 mb-0 text-muted-foreground">No models yet.</p>
          ) : null}

          {models.length > 0 ? (
            <table className="mt-2.5 w-full border-collapse">
              <thead>
                <tr>
                  <th className="th-premium">Name</th>
                  <th className="th-premium">Model</th>
                  <th className="th-premium">Visibility</th>
                  <th className="th-premium">Enabled</th>
                  <th className="th-premium">Key</th>
                  <th className="th-premium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m.id} className="tr-premium">
                    <td className="td-premium">
                      <div className="font-semibold">{m.display_name}</div>
                      <div className="text-xs text-muted-foreground">{m.id}</div>
                    </td>
                    <td className="td-premium">
                      <div>{m.model_name}</div>
                      <div className="text-xs text-muted-foreground">{m.provider_type}</div>
                    </td>
                    <td className="td-premium">{m.visibility}</td>
                    <td className="td-premium">{m.enabled ? "yes" : "no"}</td>
                    <td className="td-premium">{m.has_api_key ? "yes" : "no"}</td>
                    <td className="td-premium">
                      <div className="flex flex-wrap gap-2">
                        <button type="button" className="btn-action" onClick={() => dispatch({ type: "START_EDIT", edit: toEditState(m) })}>
                          Edit
                        </button>
                        <button type="button" className="btn-action" onClick={() => void handleTest(m.id)}>
                          Test
                        </button>
                        <button type="button" className="btn-danger" onClick={() => void handleDelete(m.id)}>
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </section>

      {edit ? (
        <section className="glass-panel-accent p-5">
          <div className="flex items-baseline justify-between gap-2.5">
            <div className="section-header">Edit model</div>
            <button type="button" onClick={() => dispatch({ type: "CLOSE_EDIT" })} className="btn-action">
              Close
            </button>
          </div>

          <div className="mt-1.5 text-xs text-muted-foreground">{edit.id}</div>

          <div className="mt-2.5 grid gap-2.5">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-display-name">
                  display_name
                </label>
                <input
                  id="edit-display-name"
                  value={edit.display_name}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "display_name", value: e.target.value })}
                  className="input-premium"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-provider-type">
                  provider_type
                </label>
                <input
                  id="edit-provider-type"
                  value={edit.provider_type}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "provider_type", value: e.target.value })}
                  className="input-premium"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-model-name">
                  model_name
                </label>
                <input
                  id="edit-model-name"
                  value={edit.model_name}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "model_name", value: e.target.value })}
                  className="input-premium"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-base-url">
                  base_url
                </label>
                <input
                  id="edit-base-url"
                  value={edit.base_url}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "base_url", value: e.target.value })}
                  className="input-premium"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-visibility">
                  visibility
                </label>
                <select
                  id="edit-visibility"
                  value={edit.visibility}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "visibility", value: e.target.value })}
                  className="input-premium"
                >
                  <option value="public">public</option>
                  <option value="private">private</option>
                </select>
              </div>
            </div>

            <label className="flex items-center text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={edit.enabled}
                onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "enabled", value: e.target.checked })}
                className="mr-2"
              />
              enabled
            </label>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-temp">
                  temperature
                </label>
                <input
                  id="edit-temp"
                  value={edit.temperatureText}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "temperatureText", value: e.target.value })}
                  className="input-premium"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-fp">
                  frequency_penalty
                </label>
                <input
                  id="edit-fp"
                  value={edit.frequencyPenaltyText}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "frequencyPenaltyText", value: e.target.value })}
                  className="input-premium"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-pp">
                  presence_penalty
                </label>
                <input
                  id="edit-pp"
                  value={edit.presencePenaltyText}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "presencePenaltyText", value: e.target.value })}
                  className="input-premium"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-system-prompt">
                  system_prompt (leave blank for default)
                </label>
                <textarea
                  id="edit-system-prompt"
                  value={edit.systemPromptText}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "systemPromptText", value: e.target.value })}
                  rows={4}
                  className="textarea-premium"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-user-prompt">
                  user_prompt (leave blank for default)
                </label>
                <textarea
                  id="edit-user-prompt"
                  value={edit.userPromptText}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "userPromptText", value: e.target.value })}
                  rows={4}
                  className="textarea-premium"
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Supported prompt tokens:{" "}
              <code className="font-mono">{"{{ source_text }}"}</code>,{" "}
              <code className="font-mono">{"{{ source_lang }}"}</code>,{" "}
              <code className="font-mono">{"{{ target_lang }}"}</code>.
              Leave both prompts blank to use the built-in defaults.
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-tags">
                  tags (JSON object)
                </label>
                <textarea
                  id="edit-tags"
                  value={edit.tagsText}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "tagsText", value: e.target.value })}
                  rows={4}
                  className="textarea-premium"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-params">
                  params (JSON object)
                </label>
                <textarea
                  id="edit-params"
                  value={edit.paramsText}
                  onChange={(e) => dispatch({ type: "SET_EDIT_FIELD", field: "paramsText", value: e.target.value })}
                  rows={4}
                  className="textarea-premium"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-api-key">
                  new api_key (optional)
                </label>
                <input
                  id="edit-api-key"
                  type="password"
                  value={edit.apiKeyText}
                  onChange={(e) => {
                    dispatch({ type: "SET_EDIT_FIELD", field: "apiKeyText", value: e.target.value });
                    dispatch({ type: "SET_EDIT_FIELD", field: "clearApiKey", value: false });
                  }}
                  className="input-premium"
                  placeholder="leave blank to keep"
                  disabled={edit.clearApiKey}
                />
              </div>
              <label className="label-premium flex items-center gap-2.5">
                <input
                  type="checkbox"
                  checked={edit.clearApiKey}
                  onChange={(e) => {
                    dispatch({ type: "SET_EDIT_FIELD", field: "clearApiKey", value: e.target.checked });
                    if (e.target.checked) {
                      dispatch({ type: "SET_EDIT_FIELD", field: "apiKeyText", value: "" });
                    }
                  }}
                />
                clear api_key
              </label>
            </div>

            <div className="flex items-center gap-2.5">
              <button type="button" onClick={() => void handleSaveEdit()} disabled={savingEdit} className="btn-primary-action">
                {savingEdit ? "Saving..." : "Save"}
              </button>
              <button type="button" onClick={() => void handleTest(edit.id)} className="btn-action">
                Test
              </button>
              <button type="button" onClick={() => void handleDelete(edit.id)} className="btn-danger">
                Delete
              </button>
              {testResult ? (
                <span className="text-xs text-muted-foreground">
                  Test: {testResult.ok ? "ok" : "fail"} ({testResult.note ?? ""})
                </span>
              ) : null}
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
