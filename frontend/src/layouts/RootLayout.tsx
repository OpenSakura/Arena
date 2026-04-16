import { Outlet, useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { Providers } from "@/components/Providers";

export function RootLayout() {
  const location = useLocation();

  const isAdmin = location.pathname.startsWith("/admin");

  return (
    <Providers>
      <div className="antialiased min-h-screen flex flex-col noise-overlay">
        <Header />
        <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8">
          {isAdmin ? (
            <Outlet />
          ) : (
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, ease: "easeOut" }}
            >
              <Outlet />
            </motion.div>
          )}
        </main>
        <Footer />
      </div>
    </Providers>
  );
}
