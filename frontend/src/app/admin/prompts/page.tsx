/**
 * frontend/src/app/admin/prompts/page.tsx
 *
 * Admin: prompt template versioning.
 */

"use client";

import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { apiDelete, apiGet, apiPost } from "@/lib/api";
import { parseJsonObjectOrNull } from "@/lib/adminParsers";
import { useAuthHeaders } from "@/hooks/useAuthHeaders";

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

export default function AdminPromptsPage() {
  const { headers } = useAuthHeaders();

  const [templates, setTemplates] = useState<PromptTemplateAdmin[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorText, setErrorText] = useState<string | null>(null);

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [templateText, setTemplateText] = useState("");
  const [inputSchemaText, setInputSchemaText] = useState("");

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
        const res = (await apiGet("/admin/prompt-templates", { headers })) as ListPromptsResponse;
        if (cancelled) return;
        setTemplates(res.prompt_templates);
      } catch (err) {
        if (cancelled) return;
        setErrorText(err instanceof Error ? err.message : "Failed to load prompt templates");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [headers]);

  async function handleCreate() {
    if (!headers) return;
    setCreating(true);
    setErrorText(null);
    try {
      if (!name.trim()) throw new Error("name is required");
      if (!templateText.trim()) throw new Error("template_text is required");

      const payload: Record<string, unknown> = {
        name: name.trim(),
        template_text: templateText,
      };
      const schema = parseJsonObjectOrNull(inputSchemaText);
      if (schema) payload.input_schema = schema;

      const created = (await apiPost("/admin/prompt-templates", payload, { headers })) as PromptTemplateAdmin;
      setTemplates((prev) => [created, ...prev]);
      setName("");
      setTemplateText("");
      setInputSchemaText("");
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to create prompt template");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string) {
    if (!headers) return;
    if (!confirm("Delete this prompt template?")) return;

    setErrorText(null);
    try {
      await apiDelete(`/admin/prompt-templates/${encodeURIComponent(id)}`, { headers });
      setTemplates((prev) => prev.filter((t) => t.id !== id));
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to delete prompt template");
    }
  }

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
                onChange={(e) => setName(e.target.value)}
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
                onChange={(e) => setTemplateText(e.target.value)}
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
                onChange={(e) => setInputSchemaText(e.target.value)}
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
