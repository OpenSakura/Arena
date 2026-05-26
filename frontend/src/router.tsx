import { createBrowserRouter, Navigate, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { RootLayout } from "./layouts/RootLayout";
import { AdminLayout } from "./routes/admin/AdminLayout";
import AdminModelsRoute from "./routes/admin/AdminModelsRoute";
import AdminTasksRoute from "./routes/admin/AdminTasksRoute";
import AdminServiceAccountsRoute from "./routes/admin/AdminServiceAccountsRoute";
import HomePage from "./routes/HomePage";
import BattleRoute from "./routes/BattleRoute";
import LeaderboardRoute from "./routes/LeaderboardRoute";
import OnboardingRoute from "./routes/OnboardingRoute";

function AuthErrorRoute() {
  const { t } = useTranslation();
  const location = useLocation();
  const searchParams = new URLSearchParams(location.search);
  const message = searchParams.get("message") || t("auth.errorRoute.defaultMessage");

  return (
    <div className="mx-auto max-w-xl px-6 py-16 text-center">
      <h1 className="text-2xl font-semibold text-foreground">{t("routes.authError")}</h1>
      <p className="mt-3 text-sm text-muted-foreground">{message}</p>
    </div>
  );
}

export const routerFutureConfig = {
  v7_startTransition: true,
  v7_relativeSplatPath: true,
} as const;

export const router = createBrowserRouter([
  {
    path: "/",
    element: <RootLayout />,
    handle: { titleKey: "routes.home" },
    children: [
      { index: true, element: <HomePage />, handle: { titleKey: "routes.home" } },
      { path: "battle/:battleId", element: <BattleRoute />, handle: { titleKey: "routes.battle" } },
      { path: "leaderboard", element: <LeaderboardRoute />, handle: { titleKey: "routes.leaderboard" } },
      { path: "onboarding", element: <OnboardingRoute />, handle: { titleKey: "routes.onboarding" } },
      { path: "auth/error", element: <AuthErrorRoute />, handle: { titleKey: "routes.authError" } },
      {
        path: "admin",
        element: <AdminLayout />,
        handle: { titleKey: "routes.admin" },
        children: [
          { index: true, element: <Navigate to="/admin/models" replace />, handle: { titleKey: "routes.adminModels" } },
          { path: "models", element: <AdminModelsRoute />, handle: { titleKey: "routes.adminModels" } },
          { path: "tasks", element: <AdminTasksRoute />, handle: { titleKey: "routes.adminTasks" } },
          { path: "service-accounts", element: <AdminServiceAccountsRoute />, handle: { titleKey: "routes.adminServiceAccounts" } },
        ],
      },
    ],
  },
], { future: routerFutureConfig });
