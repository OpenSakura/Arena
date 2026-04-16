import { createBrowserRouter, Navigate } from "react-router-dom";
import { RootLayout } from "./layouts/RootLayout";
import { AdminLayout } from "./routes/admin/AdminLayout";
import AdminModelsRoute from "./routes/admin/AdminModelsRoute";
import AdminTasksRoute from "./routes/admin/AdminTasksRoute";
import HomePage from "./routes/HomePage";
import BattleRoute from "./routes/BattleRoute";
import LeaderboardRoute from "./routes/LeaderboardRoute";
import OnboardingRoute from "./routes/OnboardingRoute";

const Placeholder = ({ name }: { name: string }) => <div>{name}</div>;

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
      { path: "auth/callback", element: <Placeholder name="Auth Callback" /> },
      { path: "auth/silent-callback", element: <Placeholder name="Auth Silent Callback" /> },
      { path: "auth/logout-callback", element: <Placeholder name="Auth Logout Callback" /> },
      {
        path: "admin",
        element: <AdminLayout />,
        children: [
          { index: true, element: <Navigate to="/admin/models" replace /> },
          { path: "models", element: <AdminModelsRoute /> },
          { path: "tasks", element: <AdminTasksRoute /> },
        ],
      },
    ],
  },
], { future: routerFutureConfig });
