import { createBrowserRouter, Navigate, useLocation } from "react-router-dom";
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
  const location = useLocation();
  const searchParams = new URLSearchParams(location.search);
  const message = searchParams.get("message") || "Authentication could not be completed. Please try again.";

  return (
    <div className="mx-auto max-w-xl px-6 py-16 text-center">
      <h1 className="text-2xl font-semibold text-foreground">Authentication error</h1>
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
    children: [
      { index: true, element: <HomePage /> },
      { path: "battle/:battleId", element: <BattleRoute /> },
      { path: "leaderboard", element: <LeaderboardRoute /> },
      { path: "onboarding", element: <OnboardingRoute /> },
      { path: "auth/error", element: <AuthErrorRoute /> },
      {
        path: "admin",
        element: <AdminLayout />,
        children: [
          { index: true, element: <Navigate to="/admin/models" replace /> },
          { path: "models", element: <AdminModelsRoute /> },
          { path: "tasks", element: <AdminTasksRoute /> },
          { path: "service-accounts", element: <AdminServiceAccountsRoute /> },
        ],
      },
    ],
  },
], { future: routerFutureConfig });
