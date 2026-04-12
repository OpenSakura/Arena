/**
 * frontend/src/app/admin/prompts/page.tsx
 *
 * Admin: prompt template versioning.
 */

"use client";

import { useEffect, useReducer } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useAuthHeaders } from "@/hooks/useAuthHeaders";
import { apiDelete, apiGet, apiPost } from "@/lib/api";
import { parseJsonObjectOrNull } from "@/lib/adminParsers";
import { isRecord } from "@/lib/typeGuards";

type PromptTemplateAdmin = {
  id: string;
  name: string;
  version: number;
  template_text: string;
  input_schema: Record<string, unknown> | null;
  content_hash: string;
  created_at: string;
};

type ListPromptsResponse = { prompt_templates: PromptTemplateAdmin[] };

type State = {
  templates: PromptTemplateAdmin[];
  loading: boolean;
  errorText: string | null;
  creating: boolean;
  name: string;
  templateText: string;
  inputSchemaText: string;
};

type Action =
  | { type: "LOAD_START" }
  | { type: "LOAD_SUCCESS"; templates: PromptTemplateAdmin[] }
  | { type: "LOAD_ERROR"; error: string }
  | { type: "SET_NAME"; value: string }
  | { type: "SET_TEMPLATE_TEXT"; value: string }
  | { type: "SET_INPUT_SCHEMA_TEXT"; value: string }
  | { type: "CREATE_START" }
  | { type: "CREATE_SUCCESS"; created: PromptTemplateAdmin }
  | { type: "CREATE_ERROR"; error: string }
  | { type: "DELETE_SUCCESS"; id: string }
  | { type: "DELETE_ERROR"; error: string };

const INITIAL_STATE: State = {
  templates: [],
  loading: true,
  errorText: null,
  creating: false,
  name: "",
  templateText: "",
  inputSchemaText: "",
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "LOAD_START":
      return { ...state, loading: true, errorText: null };
    case "LOAD_SUCCESS":
      return { ...state, loading: false, templates: action.templates, errorText: null };
    case "LOAD_ERROR":
      return { ...state, loading: false, errorText: action.error };
    case "SET_NAME":
      return { ...state, name: action.value };
    case "SET_TEMPLATE_TEXT":
      return { ...state, templateText: action.value };
    case "SET_INPUT_SCHEMA_TEXT":
      return { ...state, inputSchemaText: action.value };
    case "CREATE_START":
      return { ...state, creating: true, errorText: null };
    case "CREATE_SUCCESS":
      return {
        ...state,
        creating: false,
        templates: [action.created, ...state.templates],
        name: "",
        templateText: "",
        inputSchemaText: "",
      };
    case "CREATE_ERROR":
      return { ...state, creating: false, errorText: action.error };
    case "DELETE_SUCCESS":
      return {
        ...state,
        errorText: null,
        templates: state.templates.filter((template) => template.id !== action.id),
      };
    case "DELETE_ERROR":
      return { ...state, errorText: action.error };
    default:
      return state;
  }
}

function isPromptTemplateAdmin(value: unknown): value is PromptTemplateAdmin {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.version === "number" &&
    typeof value.template_text === "string" &&
    (value.input_schema === null || isRecord(value.input_schema)) &&
    typeof value.content_hash === "string" &&
    typeof value.created_at === "string"
  );
}

function parseListPromptsResponse(value: unknown): ListPromptsResponse {
  if (!isRecord(value) || !Array.isArray(value.prompt_templates)) {
    throw new Error("Invalid prompt templates response");
  }

  const promptTemplates = value.prompt_templates.filter(isPromptTemplateAdmin);
  if (promptTemplates.length !== value.prompt_templates.length) {
    throw new Error("Invalid prompt templates response");
  }

  return { prompt_templates: promptTemplates };
}

function parsePromptTemplateAdmin(value: unknown): PromptTemplateAdmin {
  if (!isPromptTemplateAdmin(value)) {
    throw new Error("Invalid prompt template response");
  }

  return value;
}

export default function AdminPromptsPage() {
  const { headers } = useAuthHeaders();
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!headers) {
        dispatch({ type: "LOAD_SUCCESS", templates: [] });
        return;
      }

      dispatch({ type: "LOAD_START" });
      try {
        const res = parseListPromptsResponse(await apiGet("/admin/prompt-templates", { headers }));
        if (cancelled) return;
        dispatch({ type: "LOAD_SUCCESS", templates: res.prompt_templates });
      } catch (err) {
        if (cancelled) return;
        dispatch({
          type: "LOAD_ERROR",
          error: err instanceof Error ? err.message : "Failed to load prompt templates",
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
      if (!state.name.trim()) throw new Error("name is required");
      if (!state.templateText.trim()) throw new Error("template_text is required");

      const payload: Record<string, unknown> = {
        name: state.name.trim(),
        template_text: state.templateText,
      };
      const schema = parseJsonObjectOrNull(state.inputSchemaText);
      if (schema) payload.input_schema = schema;

      const created = parsePromptTemplateAdmin(
        await apiPost("/admin/prompt-templates", payload, { headers }),
      );
      dispatch({ type: "CREATE_SUCCESS", created });
    } catch (err) {
      dispatch({
        type: "CREATE_ERROR",
        error: err instanceof Error ? err.message : "Failed to create prompt template",
      });
    }
  }

  async function handleDelete(id: string) {
    if (!headers) return;
    if (!confirm("Delete this prompt template?")) return;

    try {
      await apiDelete(`/admin/prompt-templates/${encodeURIComponent(id)}`, { headers });
      dispatch({ type: "DELETE_SUCCESS", id });
    } catch (err) {
      dispatch({
        type: "DELETE_ERROR",
        error: err instanceof Error ? err.message : "Failed to delete prompt template",
      });
    }
  }

  const { templates, loading, errorText, creating, name, templateText, inputSchemaText } = state;

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-2.5">
        <h2 className="heading-gradient text-xl">Prompt Templates</h2>
        <span className="text-xs text-muted-foreground font-mono">/admin/prompt-templates</span>
      </div>

      {errorText ? <p className="m-0 text-sm text-destructive">{errorText}</p> : null}

      <section className="glass-panel-accent p-5">
          <div className="section-header mb-1">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="section-header-icon" aria-hidden>
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Create prompt template (new version)
          </div>
          <div className="mt-2.5 grid gap-2.5">
            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="prompt-name">
                name
              </label>
              <input
                id="prompt-name"
                value={name}
                onChange={(e) => dispatch({ type: "SET_NAME", value: e.target.value })}
                className="input-premium"
                placeholder="e.g., jp2zh_vn_translation"
              />
            </div>
            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="prompt-template-text">
                template_text
              </label>
              <textarea
                id="prompt-template-text"
                value={templateText}
                onChange={(e) => dispatch({ type: "SET_TEMPLATE_TEXT", value: e.target.value })}
                className="textarea-premium"
                rows={10}
                placeholder="System prompt template only (sent as the system message). The user message will be the task source text. Use {{ var }} placeholders supported by the backend renderer."
              />
            </div>
            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="prompt-input-schema">
                input_schema (optional JSON object)
              </label>
              <textarea
                id="prompt-input-schema"
                value={inputSchemaText}
                onChange={(e) => dispatch({ type: "SET_INPUT_SCHEMA_TEXT", value: e.target.value })}
                className="textarea-premium"
                rows={5}
                placeholder='{"type":"object","properties":{"source_text":{"type":"string"}}}'
              />
            </div>
            <div className="flex items-center gap-2.5">
              <button type="button" onClick={() => void handleCreate()} disabled={creating} className="btn-primary-action">
                {creating ? "Creating..." : "Create"}
              </button>
              <span className="text-xs text-muted-foreground">
                Creating the same name again auto-increments version.
              </span>
            </div>
          </div>
        </section>

      <section className="glass-panel p-5">
          <div className="flex items-center justify-between gap-2.5 section-header">
            <span>Prompt templates</span>
            {loading ? <Skeleton className="h-3 w-16" /> : null}
          </div>

          {templates.length === 0 && !loading ? (
            <p className="mt-2.5 mb-0 text-muted-foreground">No prompt templates yet.</p>
          ) : null}

          {templates.length > 0 ? (
            <table className="mt-2.5 w-full border-collapse">
              <thead>
                <tr>
                  <th className="th-premium">Name</th>
                  <th className="th-premium">Version</th>
                  <th className="th-premium">Hash</th>
                  <th className="th-premium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {templates.map((t) => (
                  <tr key={t.id} className="tr-premium">
                    <td className="td-premium">
                      <div className="font-semibold">{t.name}</div>
                      <div className="text-xs text-muted-foreground">{t.id}</div>
                    </td>
                    <td className="td-premium">v{t.version}</td>
                    <td className="td-premium font-mono text-xs">
                      {t.content_hash.slice(0, 16)}...
                    </td>
                    <td className="td-premium">
                      <button type="button" className="btn-danger" onClick={() => void handleDelete(t.id)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </section>
    </div>
  );
}
