/**
 * frontend/src/app/admin/tasks/page.tsx
 *
 * Admin: tasks/task sets curation.
 */

"use client";

import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { apiDelete, apiGet, apiPost, apiPut, getBackendBaseUrl } from "@/lib/api";
import { parseJsonObjectOrNull } from "@/lib/adminParsers";
import { useAuthHeaders } from "@/hooks/useAuthHeaders";

type TaskSet = {
  id: string;
  name: string;
  description: string | null;
  metadata: Record<string, unknown> | null;
};

type Task = {
  id: string;
  task_set_id: string | null;
  source_lang: string;
  target_lang: string;
  source_text: string;
  metadata: Record<string, unknown> | null;
};

type ListTaskSetsResponse = { task_sets: TaskSet[] };
type ListTasksResponse = { tasks: Task[] };

type ImportResponse = {
  ok: boolean;
  imported: number;
  task_set_id: string | null;
  filename: string;
};

type EditTaskSetState = {
  id: string;
  name: string;
  description: string;
  metadataText: string;
};

type EditTaskState = {
  id: string;
  task_set_id: string | "";
  source_lang: string;
  target_lang: string;
  source_text: string;
  metadataText: string;
};

export default function AdminTasksPage() {
  const { headers } = useAuthHeaders();

  const [taskSets, setTaskSets] = useState<TaskSet[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskSetId, setSelectedTaskSetId] = useState<string | "">("");

  const [editingSet, setEditingSet] = useState<EditTaskSetState | null>(null);
  const [savingSet, setSavingSet] = useState(false);
  const [editingTask, setEditingTask] = useState<EditTaskState | null>(null);
  const [savingTask, setSavingTask] = useState(false);

  const [loadingSets, setLoadingSets] = useState(true);
  const [loadingTasks, setLoadingTasks] = useState(true);
  const [errorText, setErrorText] = useState<string | null>(null);

  const [creatingSet, setCreatingSet] = useState(false);
  const [newSetName, setNewSetName] = useState("");
  const [newSetDescription, setNewSetDescription] = useState("");
  const [newSetMetadataText, setNewSetMetadataText] = useState("");

  const [creatingTask, setCreatingTask] = useState(false);
  const [taskSourceText, setTaskSourceText] = useState("");
  const [taskSourceLang, setTaskSourceLang] = useState("ja");
  const [taskTargetLang, setTaskTargetLang] = useState("zh");
  const [taskMetadataText, setTaskMetadataText] = useState("");

  const [importing, setImporting] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importSourceLang, setImportSourceLang] = useState("ja");
  const [importTargetLang, setImportTargetLang] = useState("zh");
  const [importResult, setImportResult] = useState<ImportResponse | null>(null);

  const [visibleCount, setVisibleCount] = useState(50);
  const TASKS_PAGE_SIZE = 50;

  useEffect(() => {
    let cancelled = false;

    async function loadTaskSets() {
      if (!headers) {
        setLoadingSets(false);
        return;
      }
      setLoadingSets(true);
      setErrorText(null);
      try {
        const res = (await apiGet("/admin/task-sets", { headers })) as ListTaskSetsResponse;
        if (cancelled) return;
        setTaskSets(res.task_sets);
      } catch (err) {
        if (cancelled) return;
        setErrorText(err instanceof Error ? err.message : "Failed to load task sets");
      } finally {
        if (!cancelled) setLoadingSets(false);
      }
    }

    void loadTaskSets();
    return () => {
      cancelled = true;
    };
  }, [headers]);

  useEffect(() => {
    let cancelled = false;

    async function loadTasks() {
      if (!headers) {
        setLoadingTasks(false);
        return;
      }
      setLoadingTasks(true);
      setErrorText(null);
      try {
        const qs = selectedTaskSetId ? `?task_set_id=${encodeURIComponent(selectedTaskSetId)}` : "";
        const res = (await apiGet(`/admin/tasks${qs}`, { headers })) as ListTasksResponse;
        if (cancelled) return;
        setTasks(res.tasks);
        setVisibleCount(TASKS_PAGE_SIZE);
      } catch (err) {
        if (cancelled) return;
        setErrorText(err instanceof Error ? err.message : "Failed to load tasks");
      } finally {
        if (!cancelled) setLoadingTasks(false);
      }
    }

    void loadTasks();
    return () => {
      cancelled = true;
    };
  }, [headers, selectedTaskSetId]);

  useEffect(() => {
    if (!selectedTaskSetId) {
      setEditingSet(null);
      return;
    }

    const found = taskSets.find((s) => s.id === selectedTaskSetId);
    if (!found) {
      setEditingSet(null);
      return;
    }

    setEditingSet({
      id: found.id,
      name: found.name,
      description: found.description ?? "",
      metadataText: found.metadata ? JSON.stringify(found.metadata, null, 2) : "",
    });
  }, [selectedTaskSetId, taskSets]);

  async function handleCreateTaskSet() {
    if (!headers) return;

    setCreatingSet(true);
    setErrorText(null);
    try {
      if (!newSetName.trim()) throw new Error("name is required");

      const payload: Record<string, unknown> = {
        name: newSetName.trim(),
        description: newSetDescription.trim() ? newSetDescription.trim() : null,
        metadata: parseJsonObjectOrNull(newSetMetadataText),
      };

      const created = (await apiPost("/admin/task-sets", payload, { headers })) as TaskSet;
      setTaskSets((prev) => [created, ...prev]);
      setNewSetName("");
      setNewSetDescription("");
      setNewSetMetadataText("");
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to create task set");
    } finally {
      setCreatingSet(false);
    }
  }

  async function handleSaveSelectedTaskSet() {
    if (!headers || !editingSet) return;
    setSavingSet(true);
    setErrorText(null);
    try {
      if (!editingSet.name.trim()) throw new Error("name is required");

      const payload: Record<string, unknown> = {
        name: editingSet.name.trim(),
        description: editingSet.description.trim() ? editingSet.description.trim() : null,
        metadata: parseJsonObjectOrNull(editingSet.metadataText),
      };

      const updated = (await apiPut(
        `/admin/task-sets/${encodeURIComponent(editingSet.id)}`,
        payload,
        { headers },
      )) as TaskSet;

      setTaskSets((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      setEditingSet({
        id: updated.id,
        name: updated.name,
        description: updated.description ?? "",
        metadataText: updated.metadata ? JSON.stringify(updated.metadata, null, 2) : "",
      });
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to update task set");
    } finally {
      setSavingSet(false);
    }
  }

  async function handleDeleteSelectedTaskSet() {
    if (!headers || !editingSet) return;
    if (!confirm("Delete this task set? (must be empty)")) return;

    setErrorText(null);
    try {
      await apiDelete(`/admin/task-sets/${encodeURIComponent(editingSet.id)}`, { headers });

      setTaskSets((prev) => prev.filter((s) => s.id !== editingSet.id));
      setSelectedTaskSetId("");
      setEditingSet(null);
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to delete task set");
    }
  }

  async function handleCreateTask() {
    if (!headers) return;

    setCreatingTask(true);
    setErrorText(null);
    try {
      if (!taskSourceText.trim()) throw new Error("source_text is required");
      const payload: Record<string, unknown> = {
        task_set_id: selectedTaskSetId ? selectedTaskSetId : null,
        source_lang: taskSourceLang.trim() ? taskSourceLang.trim() : "ja",
        target_lang: taskTargetLang.trim() ? taskTargetLang.trim() : "zh",
        source_text: taskSourceText,
        metadata: parseJsonObjectOrNull(taskMetadataText),
      };

      const created = (await apiPost("/admin/tasks", payload, { headers })) as Task;
      setTasks((prev) => [created, ...prev]);
      setTaskSourceText("");
      setTaskMetadataText("");
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to create task");
    } finally {
      setCreatingTask(false);
    }
  }

  function startEditTask(task: Task) {
    setEditingTask({
      id: task.id,
      task_set_id: task.task_set_id ?? "",
      source_lang: task.source_lang,
      target_lang: task.target_lang,
      source_text: task.source_text,
      metadataText: task.metadata ? JSON.stringify(task.metadata, null, 2) : "",
    });
  }

  async function handleSaveTaskEdit() {
    if (!headers || !editingTask) return;

    setSavingTask(true);
    setErrorText(null);
    try {
      if (!editingTask.source_text.trim()) throw new Error("source_text is required");
      if (!editingTask.source_lang.trim()) throw new Error("source_lang is required");
      if (!editingTask.target_lang.trim()) throw new Error("target_lang is required");

      const payload: Record<string, unknown> = {
        task_set_id: editingTask.task_set_id ? editingTask.task_set_id : null,
        source_lang: editingTask.source_lang.trim(),
        target_lang: editingTask.target_lang.trim(),
        source_text: editingTask.source_text,
        metadata: parseJsonObjectOrNull(editingTask.metadataText),
      };

      const updated = (await apiPut(
        `/admin/tasks/${encodeURIComponent(editingTask.id)}`,
        payload,
        { headers },
      )) as Task;

      setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      setEditingTask(null);
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to update task");
    } finally {
      setSavingTask(false);
    }
  }

  async function handleDeleteTask(id: string) {
    if (!headers) return;
    if (!confirm("Delete this task?")) return;

    setErrorText(null);
    try {
      await apiDelete(`/admin/tasks/${encodeURIComponent(id)}`, { headers });
      setTasks((prev) => prev.filter((t) => t.id !== id));
      setEditingTask((prev) => (prev?.id === id ? null : prev));
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to delete task");
    }
  }

  async function handleImportJsonl() {
    if (!headers) return;
    if (!importFile) {
      setErrorText("Select a .jsonl file first");
      return;
    }

    setImporting(true);
    setErrorText(null);
    setImportResult(null);
    try {
      const form = new FormData();
      form.append("file", importFile);

      const qs = new URLSearchParams();
      if (selectedTaskSetId) qs.set("task_set_id", selectedTaskSetId);
      if (importSourceLang.trim()) qs.set("source_lang", importSourceLang.trim());
      if (importTargetLang.trim()) qs.set("target_lang", importTargetLang.trim());

      const path = `/admin/tasks/import-jsonl?${qs.toString()}`;
      const res = await fetch(`${getBackendBaseUrl()}${path}`, {
        method: "POST",
        credentials: "include",
        headers,
        body: form,
      });

      if (!res.ok) {
        let detail: string | null = null;
        const ct = res.headers.get("content-type") ?? "";
        try {
          if (ct.includes("application/json")) {
            const data = await res.json() as Record<string, unknown>;
            detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data);
          } else {
            detail = await res.text();
          }
        } catch { /* ignore parse errors */ }
        throw new Error(`POST ${path} failed: ${res.status}${detail ? ` - ${detail}` : ""}`);
      }

      const body = (await res.json()) as ImportResponse;
      setImportResult(body);

      // Refresh task list after import.
      const refreshed = (await apiGet(
        `/admin/tasks${selectedTaskSetId ? `?task_set_id=${encodeURIComponent(selectedTaskSetId)}` : ""}`,
        { headers },
      )) as ListTasksResponse;
      setTasks(refreshed.tasks);
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to import tasks");
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-2.5">
        <h2 className="heading-gradient text-xl">Tasks & Task Sets</h2>
        <span className="text-xs text-muted-foreground font-mono">/admin/tasks</span>
      </div>

      {errorText ? <p className="m-0 text-sm text-destructive">{errorText}</p> : null}

      <section className="glass-panel-accent p-5">
          <div className="section-header mb-1">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="section-header-icon" aria-hidden>
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Create task set
          </div>
          <div className="mt-2.5 grid gap-2.5">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="new-set-name">
                  name
                </label>
                <input
                  id="new-set-name"
                  value={newSetName}
                  onChange={(e) => setNewSetName(e.target.value)}
                  className="input-premium"
                  placeholder="e.g., public_jp_ln_samples"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="new-set-desc">
                  description
                </label>
                <input
                  id="new-set-desc"
                  value={newSetDescription}
                  onChange={(e) => setNewSetDescription(e.target.value)}
                  className="input-premium"
                  placeholder="optional"
                />
              </div>
            </div>
            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="new-set-metadata">
                metadata (optional JSON object)
              </label>
              <textarea
                id="new-set-metadata"
                value={newSetMetadataText}
                onChange={(e) => setNewSetMetadataText(e.target.value)}
                className="textarea-premium"
                rows={4}
                placeholder='{"license":"public","source":"curated"}'
              />
            </div>
            <div className="flex items-center gap-2.5">
              <button
                type="button"
                onClick={() => void handleCreateTaskSet()}
                disabled={creatingSet}
                className="btn-primary-action"
              >
                {creatingSet ? "Creating..." : "Create"}
              </button>
            </div>
          </div>
        </section>

      <section className="glass-panel p-5">
          <div className="flex items-center justify-between gap-2.5 section-header">
            <span>Task sets</span>
            {loadingSets ? <Skeleton className="h-3 w-16" /> : null}
          </div>

          <div className="mt-2.5 grid gap-1.5">
            <label className="label-premium flex items-center gap-2.5">
              <input
                type="radio"
                checked={selectedTaskSetId === ""}
                onChange={() => setSelectedTaskSetId("")}
              />
              <span>All tasks</span>
            </label>

            {taskSets.map((s) => (
              <label key={s.id} className="label-premium flex items-center gap-2.5">
                <input
                  type="radio"
                  checked={selectedTaskSetId === s.id}
                  onChange={() => setSelectedTaskSetId(s.id)}
                />
                <span className="font-semibold text-foreground">{s.name}</span>
                <span className="text-xs text-muted-foreground">{s.id}</span>
              </label>
            ))}
          </div>

          {editingSet ? (
            <div className="mt-3.5 grid gap-2.5 border-t border-border pt-3.5">
              <div className="section-header">Edit selected task set</div>
              <div className="text-xs text-muted-foreground">{editingSet.id}</div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="grid gap-1.5">
                  <label className="label-premium" htmlFor="edit-set-name">
                    name
                  </label>
                  <input
                    id="edit-set-name"
                    value={editingSet.name}
                    onChange={(e) =>
                      setEditingSet((prev) => (prev ? { ...prev, name: e.target.value } : prev))
                    }
                    className="input-premium"
                  />
                </div>
                <div className="grid gap-1.5">
                  <label className="label-premium" htmlFor="edit-set-desc">
                    description
                  </label>
                  <input
                    id="edit-set-desc"
                    value={editingSet.description}
                    onChange={(e) =>
                      setEditingSet((prev) => (prev ? { ...prev, description: e.target.value } : prev))
                    }
                    className="input-premium"
                  />
                </div>
              </div>

              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-set-meta">
                  metadata (JSON object)
                </label>
                <textarea
                  id="edit-set-meta"
                  value={editingSet.metadataText}
                  onChange={(e) =>
                    setEditingSet((prev) => (prev ? { ...prev, metadataText: e.target.value } : prev))
                  }
                  rows={4}
                  className="textarea-premium"
                />
              </div>

              <div className="flex items-center gap-2.5">
                <button
                  type="button"
                  onClick={() => void handleSaveSelectedTaskSet()}
                  disabled={savingSet}
                  className="btn-primary-action"
                >
                  {savingSet ? "Saving..." : "Save"}
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteSelectedTaskSet()}
                  className="btn-danger"
                >
                  Delete
                </button>
              </div>
            </div>
          ) : null}
        </section>

      <section className="glass-panel-accent p-5">
          <div className="section-header mb-1">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="section-header-icon" aria-hidden>
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Create single task
          </div>
          <div className="mt-2.5 grid gap-2.5">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="task-source-lang">
                  source_lang
                </label>
                <input
                  id="task-source-lang"
                  value={taskSourceLang}
                  onChange={(e) => setTaskSourceLang(e.target.value)}
                  className="input-premium"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="task-target-lang">
                  target_lang
                </label>
                <input
                  id="task-target-lang"
                  value={taskTargetLang}
                  onChange={(e) => setTaskTargetLang(e.target.value)}
                  className="input-premium"
                />
              </div>
            </div>

            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="task-source-text">
                source_text
              </label>
              <textarea
                id="task-source-text"
                value={taskSourceText}
                onChange={(e) => setTaskSourceText(e.target.value)}
                className="textarea-premium"
                rows={6}
                placeholder="Japanese source text"
              />
            </div>

            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="task-metadata">
                metadata (optional JSON object)
              </label>
              <textarea
                id="task-metadata"
                value={taskMetadataText}
                onChange={(e) => setTaskMetadataText(e.target.value)}
                className="textarea-premium"
                rows={4}
                placeholder='{"work":"...","chapter":"..."}'
              />
            </div>

            <div className="flex items-center gap-2.5">
              <button
                type="button"
                onClick={() => void handleCreateTask()}
                disabled={creatingTask}
                className="btn-primary-action"
              >
                {creatingTask ? "Creating..." : "Create"}
              </button>
              <span className="text-xs text-muted-foreground">
                Task set: {selectedTaskSetId ? selectedTaskSetId : "(none)"}
              </span>
            </div>
          </div>
        </section>

      <section className="glass-panel-accent p-5">
          <div className="section-header mb-1">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="section-header-icon" aria-hidden>
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            Import tasks (.jsonl)
          </div>
          <div className="mt-2.5 grid gap-2.5">
            <input
              type="file"
              accept=".jsonl"
              onChange={(e) => setImportFile(e.target.files?.[0] ?? null)}
              className="text-muted-foreground"
            />

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="import-source-lang">
                  default source_lang
                </label>
                <input
                  id="import-source-lang"
                  value={importSourceLang}
                  onChange={(e) => setImportSourceLang(e.target.value)}
                  className="input-premium"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="import-target-lang">
                  default target_lang
                </label>
                <input
                  id="import-target-lang"
                  value={importTargetLang}
                  onChange={(e) => setImportTargetLang(e.target.value)}
                  className="input-premium"
                />
              </div>
            </div>

            <div className="flex items-center gap-2.5">
              <button
                type="button"
                onClick={() => void handleImportJsonl()}
                disabled={importing}
                className="btn-primary-action"
              >
                {importing ? "Importing..." : "Import"}
              </button>
              <span className="text-xs text-muted-foreground">
                Task set: {selectedTaskSetId ? selectedTaskSetId : "(none)"}
              </span>
            </div>

            {importResult ? (
              <div className="text-xs text-muted-foreground">
                Imported {importResult.imported} tasks from {importResult.filename}
              </div>
            ) : null}
          </div>
        </section>

      <section className="glass-panel p-5">
          <div className="flex items-center justify-between gap-2.5 section-header">
            <span>Tasks</span>
            {loadingTasks ? <Skeleton className="h-3 w-16" /> : null}
          </div>

          {!loadingTasks ? (
            <div className="mt-1.5 text-xs text-muted-foreground">
              Showing {tasks.length} task(s)
              {selectedTaskSetId ? ` for task_set_id=${selectedTaskSetId}` : ""}
            </div>
          ) : null}

          {tasks.length > 0 ? (
            <table className="mt-2.5 w-full border-collapse">
              <thead>
                <tr>
                  <th className="th-premium">id</th>
                  <th className="th-premium">lang</th>
                  <th className="th-premium">text</th>
                  <th className="th-premium">actions</th>
                </tr>
              </thead>
              <tbody>
                {tasks.slice(0, visibleCount).map((t) => (
                  <tr key={t.id} className="tr-premium">
                    <td className="td-premium">
                      <div className="font-mono text-xs">
                        {t.id}
                      </div>
                      {t.task_set_id ? (
                        <div className="text-xs text-muted-foreground">set: {t.task_set_id}</div>
                      ) : null}
                    </td>
                    <td className="td-premium">
                      {t.source_lang} -&gt; {t.target_lang}
                    </td>
                    <td className="td-premium">
                      <div className="whitespace-pre-wrap leading-relaxed">
                        {t.source_text.length > 240 ? `${t.source_text.slice(0, 240)}...` : t.source_text}
                      </div>
                    </td>

                    <td className="td-premium">
                      <div className="flex flex-wrap gap-2">
                        <button type="button" className="btn-action" onClick={() => startEditTask(t)}>
                          Edit
                        </button>
                        <button
                          type="button"
                          className="btn-danger"
                          onClick={() => void handleDeleteTask(t.id)}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}

          {editingTask ? (
            <div className="mt-3.5 grid gap-2.5 border-t border-border pt-3.5">
              <div className="flex items-baseline justify-between gap-2.5">
                <div className="section-header">Edit task</div>
                <button type="button" onClick={() => setEditingTask(null)} className="btn-action">
                  Close
                </button>
              </div>

              <div className="text-xs text-muted-foreground">{editingTask.id}</div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="grid gap-1.5">
                  <label className="label-premium" htmlFor="edit-task-set-id">
                    task_set_id
                  </label>
                  <select
                    id="edit-task-set-id"
                    value={editingTask.task_set_id}
                    onChange={(e) =>
                      setEditingTask((prev) => (prev ? { ...prev, task_set_id: e.target.value } : prev))
                    }
                    className="input-premium"
                  >
                    <option value="">(none)</option>
                    {taskSets.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="grid gap-1.5">
                    <label className="label-premium" htmlFor="edit-task-source-lang">
                      source_lang
                    </label>
                    <input
                      id="edit-task-source-lang"
                      value={editingTask.source_lang}
                      onChange={(e) =>
                        setEditingTask((prev) => (prev ? { ...prev, source_lang: e.target.value } : prev))
                      }
                      className="input-premium"
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <label className="label-premium" htmlFor="edit-task-target-lang">
                      target_lang
                    </label>
                    <input
                      id="edit-task-target-lang"
                      value={editingTask.target_lang}
                      onChange={(e) =>
                        setEditingTask((prev) => (prev ? { ...prev, target_lang: e.target.value } : prev))
                      }
                      className="input-premium"
                    />
                  </div>
                </div>
              </div>

              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-task-source-text">
                  source_text
                </label>
                <textarea
                  id="edit-task-source-text"
                  value={editingTask.source_text}
                  onChange={(e) =>
                    setEditingTask((prev) => (prev ? { ...prev, source_text: e.target.value } : prev))
                  }
                  className="textarea-premium"
                  rows={6}
                />
              </div>

              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-task-metadata">
                  metadata (JSON object)
                </label>
                <textarea
                  id="edit-task-metadata"
                  value={editingTask.metadataText}
                  onChange={(e) =>
                    setEditingTask((prev) => (prev ? { ...prev, metadataText: e.target.value } : prev))
                  }
                  className="textarea-premium"
                  rows={4}
                />
              </div>

              <div className="flex items-center gap-2.5">
                <button
                  type="button"
                  onClick={() => void handleSaveTaskEdit()}
                  disabled={savingTask}
                  className="btn-primary-action"
                >
                  {savingTask ? "Saving..." : "Save"}
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteTask(editingTask.id)}
                  className="btn-danger"
                >
                  Delete
                </button>
              </div>
            </div>
          ) : null}

          {tasks.length > visibleCount ? (
            <div className="mt-2.5 flex items-center gap-3">
              <button
                type="button"
                onClick={() => setVisibleCount((prev) => prev + TASKS_PAGE_SIZE)}
                className="btn-action"
              >
                Show more
              </button>
              <span className="text-xs text-muted-foreground">
                Showing {visibleCount} of {tasks.length} tasks
              </span>
            </div>
          ) : null}
        </section>
    </div>
  );
}
