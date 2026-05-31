import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuthHeaders } from "@/hooks/useAuthHeaders";
import { apiGet, apiPost, isApiUnauthorizedError } from "@/lib/api";

type ModelOption = {
  id: string;
  display_name: string;
};

type Stats = {
  available_admin_count: number;
  available_recycled_count: number;
  available_total_count: number;
  generating_count: number;
  failed_count: number;
  voted_consumed_count: number;
  total_count: number;
  max_job_size: number;
  latest_job: Job | null;
};

type Job = {
  id: string;
  status: string;
  requested_count: number;
  completed_count: number;
  failed_count: number;
  model_ids: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
};

export default function AdminBattlePrepopulationRoute() {
  const { t } = useTranslation();
  const { authStatus } = useAuthHeaders();
  const [stats, setStats] = useState<Stats | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(false);
  
  const [amount, setAmount] = useState("10");
  const [model1, setModel1] = useState("");
  const [model2, setModel2] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const fetchAll = async () => {
    try {
      setLoading(true);
      const [statsRes, modelsRes, jobsRes] = await Promise.all([
        apiGet("/admin/battle-prepopulation/stats"),
        apiGet("/admin/battle-prepopulation/model-options"),
        apiGet("/admin/battle-prepopulation/jobs?limit=20"),
      ]);
      setStats(statsRes as Stats);
      setModels((modelsRes as { models: ModelOption[] }).models);
      setJobs((jobsRes as { jobs: Job[] }).jobs);
    } catch (err: unknown) {
      setApiError(isApiUnauthorizedError(err) ? t("admin.layout.guards.sessionExpired") : err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (authStatus === "authenticated") {
      fetchAll();
    }
  }, [authStatus]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setApiError(null);

    const amt = parseInt(amount, 10);
    const maxAmt = stats?.max_job_size ?? 50;
    if (isNaN(amt) || amt < 1 || amt > maxAmt) {
      setFormError(t("admin.battlePrepopulation.errors.invalidAmount"));
      return;
    }

    const selectedModels = [model1, model2].filter(Boolean);
    if (selectedModels.length === 2 && selectedModels[0] === selectedModels[1]) {
      setFormError(t("admin.battlePrepopulation.errors.invalidModelSelection"));
      return;
    }

    try {
      setSubmitting(true);
      const newJob = await apiPost("/admin/battle-prepopulation/jobs", {
        amount: amt,
        model_ids: selectedModels,
      });
      setJobs(prev => newJob ? [newJob as Job, ...prev] : prev);
      
      const statsRes = await apiGet("/admin/battle-prepopulation/stats");
      setStats(statsRes as Stats);

      setAmount("");
      setModel1("");
      setModel2("");
    } catch (err: unknown) {
      setApiError(isApiUnauthorizedError(err) ? t("admin.layout.guards.sessionExpired") : err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (authStatus !== "authenticated") {
    return (
      <div className="grid gap-6">
        <h2 className="heading-gradient text-xl">{t("admin.battlePrepopulation.title")}</h2>
      </div>
    );
  }

  return (
    <div className="grid gap-6">
      <h2 className="heading-gradient text-xl">{t("admin.battlePrepopulation.title")}</h2>
      
      {loading && !stats ? (
        <div className="glass-panel p-6">
          <Skeleton className="h-6 w-32" />
        </div>
      ) : stats ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <div className="glass-panel p-4">
            <p className="text-sm font-medium">{t("admin.battlePrepopulation.stats.availableAdmin")}: {stats.available_admin_count}</p>
          </div>
          <div className="glass-panel p-4">
            <p className="text-sm font-medium">{t("admin.battlePrepopulation.stats.availableRecycled")}: {stats.available_recycled_count}</p>
          </div>
          <div className="glass-panel p-4">
            <p className="text-sm font-medium">{t("admin.battlePrepopulation.stats.availableTotal")}: {stats.available_total_count}</p>
          </div>
          <div className="glass-panel p-4">
            <p className="text-sm font-medium">{t("admin.battlePrepopulation.stats.generating")}: {stats.generating_count}</p>
          </div>
          <div className="glass-panel p-4">
            <p className="text-sm font-medium">{t("admin.battlePrepopulation.stats.failed")}: {stats.failed_count}</p>
          </div>
          <div className="glass-panel p-4">
            <p className="text-sm font-medium">{t("admin.battlePrepopulation.stats.votedConsumed")}: {stats.voted_consumed_count}</p>
          </div>
          <div className="glass-panel p-4">
            <p className="text-sm font-medium">{t("admin.battlePrepopulation.stats.total")}: {stats.total_count}</p>
          </div>
          <div className="glass-panel p-4">
            <p className="text-sm font-medium">{t("admin.battlePrepopulation.stats.maxJobSize")}: {stats.max_job_size}</p>
          </div>
          <div className="glass-panel p-4 md:col-span-2 lg:col-span-4">
            <p className="text-sm font-medium">{t("admin.battlePrepopulation.stats.latestJob")}: {stats.latest_job?.status || t("admin.battlePrepopulation.jobs.none")}</p>
          </div>
        </div>
      ) : null}

      <div className="glass-panel p-6">
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label htmlFor="amount" className="mb-2 block text-sm font-medium">{t("admin.battlePrepopulation.form.amountLabel")}</label>
            <input
              id="amount"
              type="number"
              value={amount}
              onChange={e => setAmount(e.target.value)}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="model1" className="mb-2 block text-sm font-medium">{t("admin.battlePrepopulation.form.model1Label")}</label>
              <select
                id="model1"
                value={model1}
                onChange={e => setModel1(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="">{t("admin.battlePrepopulation.form.modelEmptyOption")}</option>
                {models.map(m => (
                  <option key={`m1-${m.id}`} value={m.id}>{m.display_name}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="model2" className="mb-2 block text-sm font-medium">{t("admin.battlePrepopulation.form.model2Label")}</label>
              <select
                id="model2"
                value={model2}
                onChange={e => setModel2(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="">{t("admin.battlePrepopulation.form.modelEmptyOption")}</option>
                {models.map(m => (
                  <option key={`m2-${m.id}`} value={m.id}>{m.display_name}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">{t("admin.battlePrepopulation.form.modelSelectionHelp")}</p>
          </div>

          {formError && <p className="text-sm text-destructive">{formError}</p>}
          {apiError && <p className="text-sm text-destructive">{apiError}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="inline-flex w-fit items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {submitting ? t("admin.battlePrepopulation.form.submitting") : t("admin.battlePrepopulation.form.submit")}
          </button>
        </form>
      </div>

      <div className="glass-panel p-6">
        <h3 className="mb-4 font-medium">{t("admin.battlePrepopulation.jobs.title")}</h3>
        {jobs.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("admin.battlePrepopulation.jobs.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border/50">
                  <th className="pb-2 font-medium">{t("admin.battlePrepopulation.jobs.headers.id")}</th>
                  <th className="pb-2 font-medium">{t("admin.battlePrepopulation.jobs.headers.status")}</th>
                  <th className="pb-2 font-medium">{t("admin.battlePrepopulation.jobs.headers.progress")}</th>
                  <th className="pb-2 font-medium">{t("admin.battlePrepopulation.jobs.headers.failed")}</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map(job => (
                  <tr key={job.id} className="border-b border-border/10">
                    <td className="py-2">{job.id}</td>
                    <td className="py-2">{job.status}</td>
                    <td className="py-2">{job.completed_count} / {job.requested_count}</td>
                    <td className="py-2">{t("admin.battlePrepopulation.jobs.failedCount", { count: job.failed_count })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
