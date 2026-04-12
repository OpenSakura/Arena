/**
 * frontend/src/app/template.tsx
 *
 * Page transition wrapper. Re-mounts on every route change,
 * triggering a subtle fade + slide-up entry animation.
 */

"use client";

import { motion } from "framer-motion";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

export default function Template({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "";

  if (pathname.startsWith("/admin")) {
    return <>{children}</>;
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
}
