import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/inter/800.css";
import "@/globals.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { ArenaAuthProvider } from "@/auth/ArenaAuthProvider";
import { router, routerFutureConfig } from "./router";

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("Vite root element was not found.");
}

createRoot(rootElement).render(
  <StrictMode>
    <ArenaAuthProvider>
      <RouterProvider router={router} future={routerFutureConfig} />
    </ArenaAuthProvider>
  </StrictMode>,
);
