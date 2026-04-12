/**
 * frontend/src/app/admin/models/page.tsx
 *
 * Admin: model registry CRUD.
 *
 * Notes:
 * - This UI should be protected (admins only).
 */

"use client";

import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api";
import { parseJsonObjectOrNull, parseNumberOrNull } from "@/lib/adminParsers";
import { useAuthHeaders } from "@/hooks/useAuthHeaders";

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
  params: Record<string, unknown> | null;
  prompt_template_id: string | null;
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
  paramsText: string;
  promptTemplateId: string;
  apiKeyText: string;
  clearApiKey: boolean;
};

export default function AdminModelsPage() {
  const { headers } = useAuthHeaders();

  const [models, setModels] = useState<ModelAdmin[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorText, setErrorText] = useState<string | null>(null);

  const [creating, setCreating] = useState(false);
  const [createDisplayName, setCreateDisplayName] = useState("");
  const [createProviderType, setCreateProviderType] = useState("openai_compat");
  const [createModelName, setCreateModelName] = useState("");
  const [createBaseUrl, setCreateBaseUrl] = useState("");
  const [createEnabled, setCreateEnabled] = useState(true);
  const [createVisibility, setCreateVisibility] = useState("public");
  const [createApiKey, setCreateApiKey] = useState("");
  const [createPromptTemplateId, setCreatePromptTemplateId] = useState("");

  const [createTemperature, setCreateTemperature] = useState("");
  const [createFrequencyPenalty, setCreateFrequencyPenalty] = useState("");
  const [createPresencePenalty, setCreatePresencePenalty] = useState("");
  const [createTagsText, setCreateTagsText] = useState("");
  const [createParamsText, setCreateParamsText] = useState("");

  const [edit, setEdit] = useState<EditState | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  const [testResult, setTestResult] = useState<ModelTestResponse | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!headers) {
        setLoading(false);
        return;
      }

      setLoading(true);
      setErrorText(null);
      try {
        const res = (await apiGet("/admin/models", { headers })) as ListModelsResponse;
        if (cancelled) return;
        setModels(res.models);
      } catch (err) {
        if (cancelled) return;
        setErrorText(err instanceof Error ? err.message : "Failed to load models");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [headers]);

  function startEdit(model: ModelAdmin) {
    setTestResult(null);
    setEdit({
      id: model.id,
      display_name: model.display_name,
      provider_type: model.provider_type,
      model_name: model.model_name,
      base_url: model.base_url,
      enabled: model.enabled,
      visibility: model.visibility,
      tagsText: model.tags ? JSON.stringify(model.tags, null, 2) : "",
      temperatureText: model.temperature === null ? "" : String(model.temperature),
      frequencyPenaltyText: model.frequency_penalty === null ? "" : String(model.frequency_penalty),
      presencePenaltyText: model.presence_penalty === null ? "" : String(model.presence_penalty),
      paramsText: model.params ? JSON.stringify(model.params, null, 2) : "",
      promptTemplateId: model.prompt_template_id ?? "",
      apiKeyText: "",
      clearApiKey: false,
    });
  }

  async function handleCreate() {
    if (!headers) return;

    setCreating(true);
    setErrorText(null);
    try {
      if (!createDisplayName.trim()) throw new Error("display_name is required");
      if (!createProviderType.trim()) throw new Error("provider_type is required");
      if (!createModelName.trim()) throw new Error("model_name is required");
      if (!createBaseUrl.trim()) throw new Error("base_url is required");

      const payload: Record<string, unknown> = {
        display_name: createDisplayName.trim(),
        provider_type: createProviderType.trim(),
        model_name: createModelName.trim(),
        base_url: createBaseUrl.trim(),
        enabled: createEnabled,
        visibility: createVisibility,
      };

      if (createApiKey.trim()) payload.api_key = createApiKey.trim();
      if (createPromptTemplateId.trim()) payload.prompt_template_id = createPromptTemplateId.trim();

      const temp = parseNumberOrNull(createTemperature);
      const fp = parseNumberOrNull(createFrequencyPenalty);
      const pp = parseNumberOrNull(createPresencePenalty);
      if (temp !== null) payload.temperature = temp;
      if (fp !== null) payload.frequency_penalty = fp;
      if (pp !== null) payload.presence_penalty = pp;

      const tags = parseJsonObjectOrNull(createTagsText);
      const params = parseJsonObjectOrNull(createParamsText);
      if (tags) payload.tags = tags;
      if (params) payload.params = params;

      const created = (await apiPost("/admin/models", payload, { headers })) as ModelAdmin;
      setModels((prev) => [created, ...prev]);

      // Reset the minimum fields; keep defaults.
      setCreateDisplayName("");
      setCreateModelName("");
      setCreateBaseUrl("");
      setCreateApiKey("");
      setCreatePromptTemplateId("");
      setCreateTemperature("");
      setCreateFrequencyPenalty("");
      setCreatePresencePenalty("");
      setCreateTagsText("");
      setCreateParamsText("");
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to create model");
    } finally {
      setCreating(false);
    }
  }

  async function handleSaveEdit() {
    if (!headers || !edit) return;

    setSavingEdit(true);
    setErrorText(null);
    setTestResult(null);
    try {
      if (!edit.display_name.trim()) throw new Error("display_name is required");
      if (!edit.provider_type.trim()) throw new Error("provider_type is required");
      if (!edit.model_name.trim()) throw new Error("model_name is required");
      if (!edit.base_url.trim()) throw new Error("base_url is required");

      const patch: Record<string, unknown> = {
        display_name: edit.display_name.trim(),
        provider_type: edit.provider_type.trim(),
        model_name: edit.model_name.trim(),
        base_url: edit.base_url.trim(),
        enabled: edit.enabled,
        visibility: edit.visibility,
        prompt_template_id: edit.promptTemplateId.trim() ? edit.promptTemplateId.trim() : null,
      };

      const temp = parseNumberOrNull(edit.temperatureText);
      const fp = parseNumberOrNull(edit.frequencyPenaltyText);
      const pp = parseNumberOrNull(edit.presencePenaltyText);
      patch.temperature = temp;
      patch.frequency_penalty = fp;
      patch.presence_penalty = pp;

      patch.tags = parseJsonObjectOrNull(edit.tagsText);
      patch.params = parseJsonObjectOrNull(edit.paramsText);

      if (edit.clearApiKey) {
        patch.api_key = null;
      } else if (edit.apiKeyText.trim()) {
        patch.api_key = edit.apiKeyText.trim();
      }

      const updated = (await apiPut(`/admin/models/${encodeURIComponent(edit.id)}`, patch, {
        headers,
      })) as ModelAdmin;

      setModels((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
      setEdit((prev) => (prev ? { ...prev, apiKeyText: "", clearApiKey: false } : prev));
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to save model");
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleDelete(id: string) {
    if (!headers) return;
    if (!confirm("Delete this model?")) return;

    setErrorText(null);
    setTestResult(null);
    try {
      await apiDelete(`/admin/models/${encodeURIComponent(id)}`, { headers });
      setModels((prev) => prev.filter((m) => m.id !== id));
      setEdit((prev) => (prev?.id === id ? null : prev));
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to delete model");
    }
  }

  async function handleTest(id: string) {
    if (!headers) return;
    setErrorText(null);
    setTestResult(null);
    try {
      const res = (await apiPost(`/admin/models/${encodeURIComponent(id)}/test`, {}, { headers })) as ModelTestResponse;
      setTestResult(res);
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to test model");
    }
  }

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
                  value={createDisplayName}
                  onChange={(e) => setCreateDisplayName(e.target.value)}
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
                  value={createProviderType}
                  onChange={(e) => setCreateProviderType(e.target.value)}
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
                  value={createModelName}
                  onChange={(e) => setCreateModelName(e.target.value)}
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
                  value={createBaseUrl}
                  onChange={(e) => setCreateBaseUrl(e.target.value)}
                  className="input-premium"
                  placeholder="https://gateway.example.com (or .../v1)"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-api-key">
                  API key (optional)
                </label>
                <input
                  id="create-api-key"
                  type="password"
                  value={createApiKey}
                  onChange={(e) => setCreateApiKey(e.target.value)}
                  className="input-premium"
                  placeholder="stored encrypted at rest"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-prompt-template-id">
                  Prompt template id (optional)
                </label>
                <input
                  id="create-prompt-template-id"
                  value={createPromptTemplateId}
                  onChange={(e) => setCreatePromptTemplateId(e.target.value)}
                  className="input-premium"
                  placeholder="uuid"
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
                  value={createTemperature}
                  onChange={(e) => setCreateTemperature(e.target.value)}
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
                  value={createFrequencyPenalty}
                  onChange={(e) => setCreateFrequencyPenalty(e.target.value)}
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
                  value={createPresencePenalty}
                  onChange={(e) => setCreatePresencePenalty(e.target.value)}
                  className="input-premium"
                  placeholder="(optional)"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="create-tags">
                  tags (JSON object)
                </label>
                <textarea
                  id="create-tags"
                  value={createTagsText}
                  onChange={(e) => setCreateTagsText(e.target.value)}
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
                  value={createVisibility}
                  onChange={(e) => setCreateVisibility(e.target.value)}
                  className="input-premium"
                >
                  <option value="public">public</option>
                  <option value="private">private</option>
                </select>

                <label className="label-premium mt-2.5 flex items-center">
                  <input
                    type="checkbox"
                    checked={createEnabled}
                    onChange={(e) => setCreateEnabled(e.target.checked)}
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
                  value={createParamsText}
                  onChange={(e) => setCreateParamsText(e.target.value)}
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
                        <button type="button" className="btn-action" onClick={() => startEdit(m)}>
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
            <button type="button" onClick={() => setEdit(null)} className="btn-action">
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
                  onChange={(e) => setEdit((prev) => (prev ? { ...prev, display_name: e.target.value } : prev))}
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
                  onChange={(e) => setEdit((prev) => (prev ? { ...prev, provider_type: e.target.value } : prev))}
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
                  onChange={(e) => setEdit((prev) => (prev ? { ...prev, model_name: e.target.value } : prev))}
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
                  onChange={(e) => setEdit((prev) => (prev ? { ...prev, base_url: e.target.value } : prev))}
                  className="input-premium"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-visibility">
                  visibility
                </label>
                <select
                  id="edit-visibility"
                  value={edit.visibility}
                  onChange={(e) => setEdit((prev) => (prev ? { ...prev, visibility: e.target.value } : prev))}
                  className="input-premium"
                >
                  <option value="public">public</option>
                  <option value="private">private</option>
                </select>
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-prompt-template-id">
                  prompt_template_id
                </label>
                <input
                  id="edit-prompt-template-id"
                  value={edit.promptTemplateId}
                  onChange={(e) =>
                    setEdit((prev) => (prev ? { ...prev, promptTemplateId: e.target.value } : prev))
                  }
                  className="input-premium"
                  placeholder="uuid or blank"
                />
              </div>
            </div>

            <label className="flex items-center text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={edit.enabled}
                onChange={(e) => setEdit((prev) => (prev ? { ...prev, enabled: e.target.checked } : prev))}
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
                  onChange={(e) => setEdit((prev) => (prev ? { ...prev, temperatureText: e.target.value } : prev))}
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
                  onChange={(e) =>
                    setEdit((prev) => (prev ? { ...prev, frequencyPenaltyText: e.target.value } : prev))
                  }
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
                  onChange={(e) =>
                    setEdit((prev) => (prev ? { ...prev, presencePenaltyText: e.target.value } : prev))
                  }
                  className="input-premium"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-tags">
                  tags (JSON object)
                </label>
                <textarea
                  id="edit-tags"
                  value={edit.tagsText}
                  onChange={(e) => setEdit((prev) => (prev ? { ...prev, tagsText: e.target.value } : prev))}
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
                  onChange={(e) =>
                    setEdit((prev) => (prev ? { ...prev, paramsText: e.target.value } : prev))
                  }
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
                  onChange={(e) => setEdit((prev) => (prev ? { ...prev, apiKeyText: e.target.value, clearApiKey: false } : prev))}
                  className="input-premium"
                  placeholder="leave blank to keep"
                  disabled={edit.clearApiKey}
                />
              </div>
              <label className="label-premium flex items-center gap-2.5">
                <input
                  type="checkbox"
                  checked={edit.clearApiKey}
                  onChange={(e) => setEdit((prev) => (prev ? { ...prev, clearApiKey: e.target.checked, apiKeyText: e.target.checked ? "" : prev.apiKeyText } : prev))}
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
