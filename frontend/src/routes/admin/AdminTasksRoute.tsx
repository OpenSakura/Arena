/**
 * frontend/src/routes/admin/AdminTasksRoute.tsx
 *
 * Admin: tasks/task sets curation.
 */

import { useEffect, useReducer } from "react";
import { useTranslation } from "react-i18next";

import { Skeleton } from "@/components/ui/skeleton";
import { useAuthHeaders } from "@/hooks/useAuthHeaders";
import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api";
import { parseJsonObjectOrNull } from "@/lib/adminParsers";
import { isRecord } from "@/lib/typeGuards";

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

type State = {
  taskSets: TaskSet[];
  tasks: Task[];
  selectedTaskSetId: string | "";
  editingSet: EditTaskSetState | null;
  savingSet: boolean;
  editingTask: EditTaskState | null;
  savingTask: boolean;
  loadingSets: boolean;
  loadingTasks: boolean;
  errorText: string | null;
  creatingSet: boolean;
  newSetName: string;
  newSetDescription: string;
  newSetMetadataText: string;
  creatingTask: boolean;
  taskSourceText: string;
  taskSourceLang: string;
  taskTargetLang: string;
  taskMetadataText: string;
  importing: boolean;
  importFile: File | null;
  importSourceLang: string;
  importTargetLang: string;
  importResult: ImportResponse | null;
  visibleCount: number;
};

type Action =
  | { type: "LOAD_TASK_SETS_START" }
  | { type: "LOAD_TASK_SETS_SUCCESS"; taskSets: TaskSet[] }
  | { type: "LOAD_TASK_SETS_ERROR"; error: string }
  | { type: "LOAD_TASKS_START" }
  | { type: "LOAD_TASKS_SUCCESS"; tasks: Task[]; visibleCount: number }
  | { type: "LOAD_TASKS_ERROR"; error: string }
  | { type: "SET_SELECTED_TASK_SET_ID"; value: string | "" }
  | { type: "SYNC_EDITING_SET" }
  | { type: "SET_NEW_SET_NAME"; value: string }
  | { type: "SET_NEW_SET_DESCRIPTION"; value: string }
  | { type: "SET_NEW_SET_METADATA_TEXT"; value: string }
  | { type: "CREATE_SET_START" }
  | { type: "CREATE_SET_SUCCESS"; created: TaskSet }
  | { type: "CREATE_SET_ERROR"; error: string }
  | { type: "SET_EDITING_SET_FIELD"; field: keyof EditTaskSetState; value: string }
  | { type: "SAVE_SET_START" }
  | { type: "SAVE_SET_SUCCESS"; updated: TaskSet }
  | { type: "SAVE_SET_ERROR"; error: string }
  | { type: "DELETE_SET_SUCCESS"; id: string }
  | { type: "DELETE_SET_ERROR"; error: string }
  | { type: "SET_TASK_SOURCE_TEXT"; value: string }
  | { type: "SET_TASK_SOURCE_LANG"; value: string }
  | { type: "SET_TASK_TARGET_LANG"; value: string }
  | { type: "SET_TASK_METADATA_TEXT"; value: string }
  | { type: "CREATE_TASK_START" }
  | { type: "CREATE_TASK_SUCCESS"; created: Task }
  | { type: "CREATE_TASK_ERROR"; error: string }
  | { type: "START_EDIT_TASK"; task: EditTaskState }
  | { type: "CLOSE_EDIT_TASK" }
  | { type: "SET_EDIT_TASK_FIELD"; field: keyof EditTaskState; value: string }
  | { type: "SAVE_TASK_START" }
  | { type: "SAVE_TASK_SUCCESS"; updated: Task }
  | { type: "SAVE_TASK_ERROR"; error: string }
  | { type: "DELETE_TASK_SUCCESS"; id: string }
  | { type: "DELETE_TASK_ERROR"; error: string }
  | { type: "SET_IMPORT_FILE"; value: File | null }
  | { type: "SET_IMPORT_SOURCE_LANG"; value: string }
  | { type: "SET_IMPORT_TARGET_LANG"; value: string }
  | { type: "IMPORT_START" }
  | { type: "IMPORT_SUCCESS"; result: ImportResponse; tasks: Task[] }
  | { type: "IMPORT_ERROR"; error: string }
  | { type: "SHOW_MORE"; amount: number };

const TASKS_PAGE_SIZE = 50;

const TASK_ROUTE_ERROR_KEYS: Record<string, string> = {
  "admin.tasksRoute.errors.invalidTaskSetsResponse": "admin.tasksRoute.errors.invalidTaskSetsResponse",
  "admin.tasksRoute.errors.invalidTasksResponse": "admin.tasksRoute.errors.invalidTasksResponse",
  "admin.tasksRoute.errors.invalidTaskSetResponse": "admin.tasksRoute.errors.invalidTaskSetResponse",
  "admin.tasksRoute.errors.invalidTaskResponse": "admin.tasksRoute.errors.invalidTaskResponse",
  "admin.tasksRoute.errors.invalidImportResponse": "admin.tasksRoute.errors.invalidImportResponse",
  "admin.tasksRoute.errors.nameRequired": "admin.tasksRoute.errors.nameRequired",
  "admin.tasksRoute.errors.sourceTextRequired": "admin.tasksRoute.errors.sourceTextRequired",
  "admin.tasksRoute.errors.sourceLangRequired": "admin.tasksRoute.errors.sourceLangRequired",
  "admin.tasksRoute.errors.targetLangRequired": "admin.tasksRoute.errors.targetLangRequired",
  "admin.tasksRoute.errors.invalidJsonSyntax": "admin.tasksRoute.errors.invalidJsonSyntax",
  "admin.tasksRoute.errors.expectedJsonObject": "admin.tasksRoute.errors.expectedJsonObject",
  "Invalid JSON syntax": "admin.tasksRoute.errors.invalidJsonSyntax",
  "Expected a JSON object": "admin.tasksRoute.errors.expectedJsonObject",
};

const INITIAL_STATE: State = {
  taskSets: [],
  tasks: [],
  selectedTaskSetId: "",
  editingSet: null,
  savingSet: false,
  editingTask: null,
  savingTask: false,
  loadingSets: true,
  loadingTasks: true,
  errorText: null,
  creatingSet: false,
  newSetName: "",
  newSetDescription: "",
  newSetMetadataText: "",
  creatingTask: false,
  taskSourceText: "",
  taskSourceLang: "ja",
  taskTargetLang: "zh",
  taskMetadataText: "",
  importing: false,
  importFile: null,
  importSourceLang: "ja",
  importTargetLang: "zh",
  importResult: null,
  visibleCount: TASKS_PAGE_SIZE,
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "LOAD_TASK_SETS_START":
      return { ...state, loadingSets: true, errorText: null };
    case "LOAD_TASK_SETS_SUCCESS":
      return { ...state, loadingSets: false, taskSets: action.taskSets, errorText: null };
    case "LOAD_TASK_SETS_ERROR":
      return { ...state, loadingSets: false, errorText: action.error };
    case "LOAD_TASKS_START":
      return { ...state, loadingTasks: true, errorText: null };
    case "LOAD_TASKS_SUCCESS":
      return {
        ...state,
        loadingTasks: false,
        tasks: action.tasks,
        visibleCount: action.visibleCount,
        errorText: null,
      };
    case "LOAD_TASKS_ERROR":
      return { ...state, loadingTasks: false, errorText: action.error };
    case "SET_SELECTED_TASK_SET_ID":
      return { ...state, selectedTaskSetId: action.value };
    case "SYNC_EDITING_SET": {
      if (!state.selectedTaskSetId) {
        return { ...state, editingSet: null };
      }

      const found = state.taskSets.find((taskSet) => taskSet.id === state.selectedTaskSetId);
      if (!found) {
        return { ...state, editingSet: null };
      }

      return {
        ...state,
        editingSet: {
          id: found.id,
          name: found.name,
          description: found.description ?? "",
          metadataText: found.metadata ? JSON.stringify(found.metadata, null, 2) : "",
        },
      };
    }
    case "SET_NEW_SET_NAME":
      return { ...state, newSetName: action.value };
    case "SET_NEW_SET_DESCRIPTION":
      return { ...state, newSetDescription: action.value };
    case "SET_NEW_SET_METADATA_TEXT":
      return { ...state, newSetMetadataText: action.value };
    case "CREATE_SET_START":
      return { ...state, creatingSet: true, errorText: null };
    case "CREATE_SET_SUCCESS":
      return {
        ...state,
        creatingSet: false,
        taskSets: [action.created, ...state.taskSets],
        newSetName: "",
        newSetDescription: "",
        newSetMetadataText: "",
      };
    case "CREATE_SET_ERROR":
      return { ...state, creatingSet: false, errorText: action.error };
    case "SET_EDITING_SET_FIELD":
      return state.editingSet
        ? { ...state, editingSet: { ...state.editingSet, [action.field]: action.value } }
        : state;
    case "SAVE_SET_START":
      return { ...state, savingSet: true, errorText: null };
    case "SAVE_SET_SUCCESS": {
      const taskSets = state.taskSets.map((taskSet) =>
        taskSet.id === action.updated.id ? action.updated : taskSet,
      );
      return {
        ...state,
        savingSet: false,
        taskSets,
        editingSet: {
          id: action.updated.id,
          name: action.updated.name,
          description: action.updated.description ?? "",
          metadataText: action.updated.metadata ? JSON.stringify(action.updated.metadata, null, 2) : "",
        },
      };
    }
    case "SAVE_SET_ERROR":
      return { ...state, savingSet: false, errorText: action.error };
    case "DELETE_SET_SUCCESS":
      return {
        ...state,
        taskSets: state.taskSets.filter((taskSet) => taskSet.id !== action.id),
        selectedTaskSetId: state.selectedTaskSetId === action.id ? "" : state.selectedTaskSetId,
        editingSet: state.editingSet?.id === action.id ? null : state.editingSet,
        errorText: null,
      };
    case "DELETE_SET_ERROR":
      return { ...state, errorText: action.error };
    case "SET_TASK_SOURCE_TEXT":
      return { ...state, taskSourceText: action.value };
    case "SET_TASK_SOURCE_LANG":
      return { ...state, taskSourceLang: action.value };
    case "SET_TASK_TARGET_LANG":
      return { ...state, taskTargetLang: action.value };
    case "SET_TASK_METADATA_TEXT":
      return { ...state, taskMetadataText: action.value };
    case "CREATE_TASK_START":
      return { ...state, creatingTask: true, errorText: null };
    case "CREATE_TASK_SUCCESS":
      return {
        ...state,
        creatingTask: false,
        tasks: [action.created, ...state.tasks],
        taskSourceText: "",
        taskMetadataText: "",
      };
    case "CREATE_TASK_ERROR":
      return { ...state, creatingTask: false, errorText: action.error };
    case "START_EDIT_TASK":
      return { ...state, editingTask: action.task };
    case "CLOSE_EDIT_TASK":
      return { ...state, editingTask: null };
    case "SET_EDIT_TASK_FIELD":
      return state.editingTask
        ? { ...state, editingTask: { ...state.editingTask, [action.field]: action.value } }
        : state;
    case "SAVE_TASK_START":
      return { ...state, savingTask: true, errorText: null };
    case "SAVE_TASK_SUCCESS":
      return {
        ...state,
        savingTask: false,
        tasks: state.tasks.map((task) => (task.id === action.updated.id ? action.updated : task)),
        editingTask: null,
      };
    case "SAVE_TASK_ERROR":
      return { ...state, savingTask: false, errorText: action.error };
    case "DELETE_TASK_SUCCESS":
      return {
        ...state,
        tasks: state.tasks.filter((task) => task.id !== action.id),
        editingTask: state.editingTask?.id === action.id ? null : state.editingTask,
        errorText: null,
      };
    case "DELETE_TASK_ERROR":
      return { ...state, errorText: action.error };
    case "SET_IMPORT_FILE":
      return { ...state, importFile: action.value };
    case "SET_IMPORT_SOURCE_LANG":
      return { ...state, importSourceLang: action.value };
    case "SET_IMPORT_TARGET_LANG":
      return { ...state, importTargetLang: action.value };
    case "IMPORT_START":
      return { ...state, importing: true, errorText: null, importResult: null };
    case "IMPORT_SUCCESS":
      return {
        ...state,
        importing: false,
        importResult: action.result,
        tasks: action.tasks,
      };
    case "IMPORT_ERROR":
      return { ...state, importing: false, errorText: action.error };
    case "SHOW_MORE":
      return { ...state, visibleCount: state.visibleCount + action.amount };
    default:
      return state;
  }
}

function isTaskSet(value: unknown): value is TaskSet {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    (typeof value.description === "string" || value.description === null) &&
    (value.metadata === null || isRecord(value.metadata))
  );
}

function isTask(value: unknown): value is Task {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    (typeof value.task_set_id === "string" || value.task_set_id === null) &&
    typeof value.source_lang === "string" &&
    typeof value.target_lang === "string" &&
    typeof value.source_text === "string" &&
    (value.metadata === null || isRecord(value.metadata))
  );
}

function isImportResponse(value: unknown): value is ImportResponse {
  return (
    isRecord(value) &&
    typeof value.ok === "boolean" &&
    typeof value.imported === "number" &&
    (typeof value.task_set_id === "string" || value.task_set_id === null) &&
    typeof value.filename === "string"
  );
}

function parseTaskSetsResponse(value: unknown): ListTaskSetsResponse {
  if (!isRecord(value) || !Array.isArray(value.task_sets)) {
    throw new Error("admin.tasksRoute.errors.invalidTaskSetsResponse");
  }

  const taskSets = value.task_sets.filter(isTaskSet);
  if (taskSets.length !== value.task_sets.length) {
    throw new Error("admin.tasksRoute.errors.invalidTaskSetsResponse");
  }

  return { task_sets: taskSets };
}

function parseTasksResponse(value: unknown): ListTasksResponse {
  if (!isRecord(value) || !Array.isArray(value.tasks)) {
    throw new Error("admin.tasksRoute.errors.invalidTasksResponse");
  }

  const tasks = value.tasks.filter(isTask);
  if (tasks.length !== value.tasks.length) {
    throw new Error("admin.tasksRoute.errors.invalidTasksResponse");
  }

  return { tasks };
}

function parseTaskSet(value: unknown): TaskSet {
  if (!isTaskSet(value)) {
    throw new Error("admin.tasksRoute.errors.invalidTaskSetResponse");
  }

  return value;
}

function parseTask(value: unknown): Task {
  if (!isTask(value)) {
    throw new Error("admin.tasksRoute.errors.invalidTaskResponse");
  }

  return value;
}

function parseImportResponse(value: unknown): ImportResponse {
  if (!isImportResponse(value)) {
    throw new Error("admin.tasksRoute.errors.invalidImportResponse");
  }

  return value;
}

function toEditTaskState(task: Task): EditTaskState {
  return {
    id: task.id,
    task_set_id: task.task_set_id ?? "",
    source_lang: task.source_lang,
    target_lang: task.target_lang,
    source_text: task.source_text,
    metadataText: task.metadata ? JSON.stringify(task.metadata, null, 2) : "",
  };
}

export default function AdminTasksRoute() {
  const { t } = useTranslation();
  const { authStatus } = useAuthHeaders();
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  function errorMessage(err: unknown, fallbackKey: string) {
    if (!(err instanceof Error)) return t(fallbackKey);
    const translationKey = TASK_ROUTE_ERROR_KEYS[err.message];
    return translationKey ? t(translationKey) : err.message;
  }

  useEffect(() => {
    let cancelled = false;

    async function loadTaskSets() {
      if (authStatus !== "authenticated") {
        dispatch({ type: "LOAD_TASK_SETS_SUCCESS", taskSets: [] });
        return;
      }

      dispatch({ type: "LOAD_TASK_SETS_START" });
      try {
        const res = parseTaskSetsResponse(await apiGet("/admin/task-sets?limit=1000"));
        if (cancelled) return;
        dispatch({ type: "LOAD_TASK_SETS_SUCCESS", taskSets: res.task_sets });
      } catch (err) {
        if (cancelled) return;
        dispatch({
          type: "LOAD_TASK_SETS_ERROR",
          error: errorMessage(err, "admin.tasksRoute.errors.loadTaskSetsFailed"),
        });
      }
    }

    void loadTaskSets();
    return () => {
      cancelled = true;
    };
  }, [authStatus]);

  useEffect(() => {
    let cancelled = false;

    async function loadTasks() {
      if (authStatus !== "authenticated") {
        dispatch({ type: "LOAD_TASKS_SUCCESS", tasks: [], visibleCount: TASKS_PAGE_SIZE });
        return;
      }

      dispatch({ type: "LOAD_TASKS_START" });
      try {
        const qs = state.selectedTaskSetId
          ? `?task_set_id=${encodeURIComponent(state.selectedTaskSetId)}`
          : "";
        const res = parseTasksResponse(await apiGet(`/admin/tasks${qs ? qs + "&limit=1000" : "?limit=1000"}`));
        if (cancelled) return;
        dispatch({
          type: "LOAD_TASKS_SUCCESS",
          tasks: res.tasks,
          visibleCount: TASKS_PAGE_SIZE,
        });
      } catch (err) {
        if (cancelled) return;
        dispatch({
          type: "LOAD_TASKS_ERROR",
          error: errorMessage(err, "admin.tasksRoute.errors.loadTasksFailed"),
        });
      }
    }

    void loadTasks();
    return () => {
      cancelled = true;
    };
  }, [authStatus, state.selectedTaskSetId]);

  useEffect(() => {
    dispatch({ type: "SYNC_EDITING_SET" });
  }, [state.selectedTaskSetId, state.taskSets]);

  async function handleCreateTaskSet() {
    if (authStatus !== "authenticated") return;

    dispatch({ type: "CREATE_SET_START" });
    try {
      if (!state.newSetName.trim()) throw new Error("admin.tasksRoute.errors.nameRequired");

      const payload: Record<string, unknown> = {
        name: state.newSetName.trim(),
        description: state.newSetDescription.trim() ? state.newSetDescription.trim() : null,
        metadata: parseJsonObjectOrNull(state.newSetMetadataText),
      };

      const created = parseTaskSet(await apiPost("/admin/task-sets", payload));
      dispatch({ type: "CREATE_SET_SUCCESS", created });
    } catch (err) {
      dispatch({
        type: "CREATE_SET_ERROR",
        error: errorMessage(err, "admin.tasksRoute.errors.createTaskSetFailed"),
      });
    }
  }

  async function handleSaveSelectedTaskSet() {
    if (authStatus !== "authenticated" || !state.editingSet) return;

    dispatch({ type: "SAVE_SET_START" });
    try {
      if (!state.editingSet.name.trim()) throw new Error("admin.tasksRoute.errors.nameRequired");

      const payload: Record<string, unknown> = {
        name: state.editingSet.name.trim(),
        description: state.editingSet.description.trim() ? state.editingSet.description.trim() : null,
        metadata: parseJsonObjectOrNull(state.editingSet.metadataText),
      };

      const updated = parseTaskSet(
        await apiPut(`/admin/task-sets/${encodeURIComponent(state.editingSet.id)}`, payload),
      );
      dispatch({ type: "SAVE_SET_SUCCESS", updated });
    } catch (err) {
      dispatch({
        type: "SAVE_SET_ERROR",
        error: errorMessage(err, "admin.tasksRoute.errors.updateTaskSetFailed"),
      });
    }
  }

  async function handleDeleteSelectedTaskSet() {
    if (authStatus !== "authenticated" || !state.editingSet) return;
    if (!confirm(t("admin.tasksRoute.confirmDeleteTaskSet"))) return;

    try {
      await apiDelete(`/admin/task-sets/${encodeURIComponent(state.editingSet.id)}`);
      dispatch({ type: "DELETE_SET_SUCCESS", id: state.editingSet.id });
    } catch (err) {
      dispatch({
        type: "DELETE_SET_ERROR",
        error: errorMessage(err, "admin.tasksRoute.errors.deleteTaskSetFailed"),
      });
    }
  }

  async function handleCreateTask() {
    if (authStatus !== "authenticated") return;

    dispatch({ type: "CREATE_TASK_START" });
    try {
      if (!state.taskSourceText.trim()) throw new Error("admin.tasksRoute.errors.sourceTextRequired");

      const payload: Record<string, unknown> = {
        task_set_id: state.selectedTaskSetId ? state.selectedTaskSetId : null,
        source_lang: state.taskSourceLang.trim() ? state.taskSourceLang.trim() : "ja",
        target_lang: state.taskTargetLang.trim() ? state.taskTargetLang.trim() : "zh",
        source_text: state.taskSourceText,
        metadata: parseJsonObjectOrNull(state.taskMetadataText),
      };

      const created = parseTask(await apiPost("/admin/tasks", payload));
      dispatch({ type: "CREATE_TASK_SUCCESS", created });
    } catch (err) {
      dispatch({
        type: "CREATE_TASK_ERROR",
        error: errorMessage(err, "admin.tasksRoute.errors.createTaskFailed"),
      });
    }
  }

  async function handleSaveTaskEdit() {
    if (authStatus !== "authenticated" || !state.editingTask) return;

    dispatch({ type: "SAVE_TASK_START" });
    try {
      if (!state.editingTask.source_text.trim()) throw new Error("admin.tasksRoute.errors.sourceTextRequired");
      if (!state.editingTask.source_lang.trim()) throw new Error("admin.tasksRoute.errors.sourceLangRequired");
      if (!state.editingTask.target_lang.trim()) throw new Error("admin.tasksRoute.errors.targetLangRequired");

      const payload: Record<string, unknown> = {
        task_set_id: state.editingTask.task_set_id ? state.editingTask.task_set_id : null,
        source_lang: state.editingTask.source_lang.trim(),
        target_lang: state.editingTask.target_lang.trim(),
        source_text: state.editingTask.source_text,
        metadata: parseJsonObjectOrNull(state.editingTask.metadataText),
      };

      const updated = parseTask(
        await apiPut(`/admin/tasks/${encodeURIComponent(state.editingTask.id)}`, payload),
      );
      dispatch({ type: "SAVE_TASK_SUCCESS", updated });
    } catch (err) {
      dispatch({
        type: "SAVE_TASK_ERROR",
        error: errorMessage(err, "admin.tasksRoute.errors.updateTaskFailed"),
      });
    }
  }

  async function handleDeleteTask(id: string) {
    if (authStatus !== "authenticated") return;
    if (!confirm(t("admin.tasksRoute.confirmDeleteTask"))) return;

    try {
      await apiDelete(`/admin/tasks/${encodeURIComponent(id)}`);
      dispatch({ type: "DELETE_TASK_SUCCESS", id });
    } catch (err) {
      dispatch({
        type: "DELETE_TASK_ERROR",
        error: errorMessage(err, "admin.tasksRoute.errors.deleteTaskFailed"),
      });
    }
  }

  async function handleImportJsonl() {
    if (authStatus !== "authenticated") return;
    if (!state.importFile) {
      dispatch({ type: "IMPORT_ERROR", error: t("admin.tasksRoute.errors.selectJsonlFirst") });
      return;
    }

    dispatch({ type: "IMPORT_START" });
    try {
      const form = new FormData();
      form.append("file", state.importFile);

      const qs = new URLSearchParams();
      if (state.selectedTaskSetId) qs.set("task_set_id", state.selectedTaskSetId);
      if (state.importSourceLang.trim()) qs.set("source_lang", state.importSourceLang.trim());
      if (state.importTargetLang.trim()) qs.set("target_lang", state.importTargetLang.trim());

      const path = `/admin/tasks/import-jsonl?${qs.toString()}`;
      const body = parseImportResponse(await apiPost(path, form));

      const refreshed = parseTasksResponse(
        await apiGet(
          `/admin/tasks${
            state.selectedTaskSetId
              ? `?task_set_id=${encodeURIComponent(state.selectedTaskSetId)}&limit=1000`
              : "?limit=1000"
          }`,
        ),
      );
      dispatch({ type: "IMPORT_SUCCESS", result: body, tasks: refreshed.tasks });
    } catch (err) {
      dispatch({
        type: "IMPORT_ERROR",
        error: errorMessage(err, "admin.tasksRoute.errors.importTasksFailed"),
      });
    }
  }

  const {
    taskSets,
    tasks,
    selectedTaskSetId,
    editingSet,
    savingSet,
    editingTask,
    savingTask,
    loadingSets,
    loadingTasks,
    errorText,
    creatingSet,
    newSetName,
    newSetDescription,
    newSetMetadataText,
    creatingTask,
    taskSourceText,
    taskSourceLang,
    taskTargetLang,
    taskMetadataText,
    importing,
    importSourceLang,
    importTargetLang,
    importResult,
    visibleCount,
  } = state;

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-2.5">
        <h2 className="heading-gradient text-xl">{t("admin.tasksRoute.title")}</h2>
        <span className="text-xs text-muted-foreground font-mono">/admin/tasks</span>
      </div>

      {errorText ? <p className="m-0 text-sm text-destructive">{errorText}</p> : null}

      <section className="glass-panel-accent p-5">
          <div className="section-header mb-1">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="section-header-icon" aria-hidden>
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            {t("admin.tasksRoute.createSet.title")}
          </div>
          <div className="mt-2.5 grid gap-2.5">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="new-set-name">
                  {t("admin.tasksRoute.form.name")}
                </label>
                <input
                  id="new-set-name"
                  value={newSetName}
                  onChange={(e) => dispatch({ type: "SET_NEW_SET_NAME", value: e.target.value })}
                  className="input-premium"
                  placeholder={t("admin.tasksRoute.form.namePlaceholder")}
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="new-set-desc">
                  {t("admin.tasksRoute.form.description")}
                </label>
                <input
                  id="new-set-desc"
                  value={newSetDescription}
                  onChange={(e) => dispatch({ type: "SET_NEW_SET_DESCRIPTION", value: e.target.value })}
                  className="input-premium"
                  placeholder={t("admin.tasksRoute.form.optionalPlaceholder")}
                />
              </div>
            </div>
            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="new-set-metadata">
                {t("admin.tasksRoute.form.metadataOptional")}
              </label>
              <textarea
                id="new-set-metadata"
                value={newSetMetadataText}
                onChange={(e) => dispatch({ type: "SET_NEW_SET_METADATA_TEXT", value: e.target.value })}
                className="textarea-premium"
                rows={4}
                placeholder={t("admin.tasksRoute.form.setMetadataPlaceholder")}
              />
            </div>
            <div className="flex items-center gap-2.5">
              <button
                type="button"
                onClick={() => void handleCreateTaskSet()}
                disabled={creatingSet}
                className="btn-primary-action"
              >
                {creatingSet ? t("admin.tasksRoute.actions.creating") : t("admin.tasksRoute.actions.create")}
              </button>
            </div>
          </div>
        </section>

      <section className="glass-panel p-5">
          <div className="flex items-center justify-between gap-2.5 section-header">
            <span>{t("admin.tasksRoute.taskSets.title")}</span>
            {loadingSets ? <Skeleton className="h-3 w-16" /> : null}
          </div>

          <div className="mt-2.5 grid gap-1.5">
            <label className="label-premium flex items-center gap-2.5">
              <input
                type="radio"
                checked={selectedTaskSetId === ""}
                onChange={() => dispatch({ type: "SET_SELECTED_TASK_SET_ID", value: "" })}
              />
              <span>{t("admin.tasksRoute.taskSets.allTasks")}</span>
            </label>

            {taskSets.map((s) => (
              <label key={s.id} className="label-premium flex items-center gap-2.5">
                <input
                  type="radio"
                  checked={selectedTaskSetId === s.id}
                  onChange={() => dispatch({ type: "SET_SELECTED_TASK_SET_ID", value: s.id })}
                />
                <span className="font-semibold text-foreground">{s.name}</span>
                <span className="text-xs text-muted-foreground">{s.id}</span>
              </label>
            ))}
          </div>

          {editingSet ? (
            <div className="mt-3.5 grid gap-2.5 border-t border-border pt-3.5">
              <div className="section-header">{t("admin.tasksRoute.editSet.title")}</div>
              <div className="text-xs text-muted-foreground">{editingSet.id}</div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="grid gap-1.5">
                  <label className="label-premium" htmlFor="edit-set-name">
                    {t("admin.tasksRoute.form.name")}
                  </label>
                  <input
                    id="edit-set-name"
                    value={editingSet.name}
                    onChange={(e) => dispatch({ type: "SET_EDITING_SET_FIELD", field: "name", value: e.target.value })}
                    className="input-premium"
                  />
                </div>
                <div className="grid gap-1.5">
                  <label className="label-premium" htmlFor="edit-set-desc">
                    {t("admin.tasksRoute.form.description")}
                  </label>
                  <input
                    id="edit-set-desc"
                    value={editingSet.description}
                    onChange={(e) => dispatch({ type: "SET_EDITING_SET_FIELD", field: "description", value: e.target.value })}
                    className="input-premium"
                  />
                </div>
              </div>

              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-set-meta">
                  {t("admin.tasksRoute.form.metadata")}
                </label>
                <textarea
                  id="edit-set-meta"
                  value={editingSet.metadataText}
                  onChange={(e) => dispatch({ type: "SET_EDITING_SET_FIELD", field: "metadataText", value: e.target.value })}
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
                  {savingSet ? t("admin.tasksRoute.actions.saving") : t("admin.tasksRoute.actions.save")}
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteSelectedTaskSet()}
                  className="btn-danger"
                >
                  {t("admin.tasksRoute.actions.delete")}
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
            {t("admin.tasksRoute.createTask.title")}
          </div>
          <div className="mt-2.5 grid gap-2.5">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="task-source-lang">
                  {t("admin.tasksRoute.form.sourceLang")}
                </label>
                <input
                  id="task-source-lang"
                  value={taskSourceLang}
                  onChange={(e) => dispatch({ type: "SET_TASK_SOURCE_LANG", value: e.target.value })}
                  className="input-premium"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="task-target-lang">
                  {t("admin.tasksRoute.form.targetLang")}
                </label>
                <input
                  id="task-target-lang"
                  value={taskTargetLang}
                  onChange={(e) => dispatch({ type: "SET_TASK_TARGET_LANG", value: e.target.value })}
                  className="input-premium"
                />
              </div>
            </div>

            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="task-source-text">
                {t("admin.tasksRoute.form.sourceText")}
              </label>
              <textarea
                id="task-source-text"
                value={taskSourceText}
                onChange={(e) => dispatch({ type: "SET_TASK_SOURCE_TEXT", value: e.target.value })}
                className="textarea-premium"
                rows={6}
                placeholder={t("admin.tasksRoute.form.sourceTextPlaceholder")}
              />
            </div>

            <div className="grid gap-1.5">
              <label className="label-premium" htmlFor="task-metadata">
                {t("admin.tasksRoute.form.metadataOptional")}
              </label>
              <textarea
                id="task-metadata"
                value={taskMetadataText}
                onChange={(e) => dispatch({ type: "SET_TASK_METADATA_TEXT", value: e.target.value })}
                className="textarea-premium"
                rows={4}
                placeholder={t("admin.tasksRoute.form.taskMetadataPlaceholder")}
              />
            </div>

            <div className="flex items-center gap-2.5">
              <button
                type="button"
                onClick={() => void handleCreateTask()}
                disabled={creatingTask}
                className="btn-primary-action"
              >
                {creatingTask ? t("admin.tasksRoute.actions.creating") : t("admin.tasksRoute.actions.create")}
              </button>
              <span className="text-xs text-muted-foreground">
                {t("admin.tasksRoute.status.taskSet", {
                  id: selectedTaskSetId ? selectedTaskSetId : t("admin.tasksRoute.values.none"),
                })}
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
            {t("admin.tasksRoute.import.title")}
          </div>
          <div className="mt-2.5 grid gap-2.5">
            <input
              type="file"
              accept=".jsonl"
              aria-label={t("admin.tasksRoute.import.fileAriaLabel")}
              onChange={(e) => dispatch({ type: "SET_IMPORT_FILE", value: e.target.files?.[0] ?? null })}
              className="text-muted-foreground"
            />

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="import-source-lang">
                  {t("admin.tasksRoute.form.defaultSourceLang")}
                </label>
                <input
                  id="import-source-lang"
                  value={importSourceLang}
                  onChange={(e) => dispatch({ type: "SET_IMPORT_SOURCE_LANG", value: e.target.value })}
                  className="input-premium"
                />
              </div>
              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="import-target-lang">
                  {t("admin.tasksRoute.form.defaultTargetLang")}
                </label>
                <input
                  id="import-target-lang"
                  value={importTargetLang}
                  onChange={(e) => dispatch({ type: "SET_IMPORT_TARGET_LANG", value: e.target.value })}
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
                {importing ? t("admin.tasksRoute.actions.importing") : t("admin.tasksRoute.actions.import")}
              </button>
              <span className="text-xs text-muted-foreground">
                {t("admin.tasksRoute.status.taskSet", {
                  id: selectedTaskSetId ? selectedTaskSetId : t("admin.tasksRoute.values.none"),
                })}
              </span>
            </div>

            {importResult ? (
              <div className="text-xs text-muted-foreground">
                {t("admin.tasksRoute.status.imported", {
                  count: importResult.imported,
                  filename: importResult.filename,
                })}
              </div>
            ) : null}
          </div>
        </section>

      <section className="glass-panel p-5">
          <div className="flex items-center justify-between gap-2.5 section-header">
            <span>{t("admin.tasksRoute.tasks.title")}</span>
            {loadingTasks ? <Skeleton className="h-3 w-16" /> : null}
          </div>

          {!loadingTasks ? (
            <div className="mt-1.5 text-xs text-muted-foreground">
              {selectedTaskSetId
                ? t("admin.tasksRoute.status.showingTasksForSet", {
                    count: tasks.length,
                    taskSetId: selectedTaskSetId,
                  })
                : t("admin.tasksRoute.status.showingTasks", { count: tasks.length })}
            </div>
          ) : null}

          {tasks.length > 0 ? (
            <table className="mt-2.5 w-full border-collapse">
              <thead>
                <tr>
                  <th className="th-premium">{t("admin.tasksRoute.tasks.headers.id")}</th>
                  <th className="th-premium">{t("admin.tasksRoute.tasks.headers.lang")}</th>
                  <th className="th-premium">{t("admin.tasksRoute.tasks.headers.text")}</th>
                  <th className="th-premium">{t("admin.tasksRoute.tasks.headers.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {tasks.slice(0, visibleCount).map((task) => (
                  <tr key={task.id} className="tr-premium">
                    <td className="td-premium">
                      <div className="font-mono text-xs">
                        {task.id}
                      </div>
                      {task.task_set_id ? (
                        <div className="text-xs text-muted-foreground">
                          {t("admin.tasksRoute.tasks.setLabel", { id: task.task_set_id })}
                        </div>
                      ) : null}
                    </td>
                    <td className="td-premium">
                      {task.source_lang} -&gt; {task.target_lang}
                    </td>
                    <td className="td-premium">
                      <div className="whitespace-pre-wrap leading-relaxed">
                        {task.source_text.length > 240 ? `${task.source_text.slice(0, 240)}...` : task.source_text}
                      </div>
                    </td>

                    <td className="td-premium">
                      <div className="flex flex-wrap gap-2">
                        <button type="button" className="btn-action" onClick={() => dispatch({ type: "START_EDIT_TASK", task: toEditTaskState(task) })}>
                          {t("admin.tasksRoute.actions.edit")}
                        </button>
                        <button
                          type="button"
                          className="btn-danger"
                          onClick={() => void handleDeleteTask(task.id)}
                        >
                          {t("admin.tasksRoute.actions.delete")}
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
                <div className="section-header">{t("admin.tasksRoute.editTask.title")}</div>
                <button type="button" onClick={() => dispatch({ type: "CLOSE_EDIT_TASK" })} className="btn-action">
                  {t("admin.tasksRoute.actions.close")}
                </button>
              </div>

              <div className="text-xs text-muted-foreground">{editingTask.id}</div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="grid gap-1.5">
                  <label className="label-premium" htmlFor="edit-task-set-id">
                    {t("admin.tasksRoute.form.taskSetId")}
                  </label>
                  <select
                    id="edit-task-set-id"
                    value={editingTask.task_set_id}
                    onChange={(e) => dispatch({ type: "SET_EDIT_TASK_FIELD", field: "task_set_id", value: e.target.value })}
                    className="input-premium"
                  >
                    <option value="">{t("admin.tasksRoute.values.none")}</option>
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
                      {t("admin.tasksRoute.form.sourceLang")}
                    </label>
                    <input
                      id="edit-task-source-lang"
                      value={editingTask.source_lang}
                      onChange={(e) => dispatch({ type: "SET_EDIT_TASK_FIELD", field: "source_lang", value: e.target.value })}
                      className="input-premium"
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <label className="label-premium" htmlFor="edit-task-target-lang">
                      {t("admin.tasksRoute.form.targetLang")}
                    </label>
                    <input
                      id="edit-task-target-lang"
                      value={editingTask.target_lang}
                      onChange={(e) => dispatch({ type: "SET_EDIT_TASK_FIELD", field: "target_lang", value: e.target.value })}
                      className="input-premium"
                    />
                  </div>
                </div>
              </div>

              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-task-source-text">
                  {t("admin.tasksRoute.form.sourceText")}
                </label>
                <textarea
                  id="edit-task-source-text"
                  value={editingTask.source_text}
                  onChange={(e) => dispatch({ type: "SET_EDIT_TASK_FIELD", field: "source_text", value: e.target.value })}
                  className="textarea-premium"
                  rows={6}
                />
              </div>

              <div className="grid gap-1.5">
                <label className="label-premium" htmlFor="edit-task-metadata">
                  {t("admin.tasksRoute.form.metadata")}
                </label>
                <textarea
                  id="edit-task-metadata"
                  value={editingTask.metadataText}
                  onChange={(e) => dispatch({ type: "SET_EDIT_TASK_FIELD", field: "metadataText", value: e.target.value })}
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
                  {savingTask ? t("admin.tasksRoute.actions.saving") : t("admin.tasksRoute.actions.save")}
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteTask(editingTask.id)}
                  className="btn-danger"
                >
                  {t("admin.tasksRoute.actions.delete")}
                </button>
              </div>
            </div>
          ) : null}

          {tasks.length > visibleCount ? (
            <div className="mt-2.5 flex items-center gap-3">
              <button
                type="button"
                onClick={() => dispatch({ type: "SHOW_MORE", amount: TASKS_PAGE_SIZE })}
                className="btn-action"
              >
                {t("admin.tasksRoute.actions.showMore")}
              </button>
              <span className="text-xs text-muted-foreground">
                {t("admin.tasksRoute.status.showingVisible", {
                  visible: visibleCount,
                  total: tasks.length,
                })}
              </span>
            </div>
          ) : null}
        </section>
    </div>
  );
}
