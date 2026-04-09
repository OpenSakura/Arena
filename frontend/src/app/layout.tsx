/**
 * frontend/src/app/layout.tsx
 *
 * Root layout for the Next.js App Router.
 */

import "./globals.css";

import type { ReactNode } from "react";
import type { Metadata } from "next";
import { Inter } from "next/font/google";

import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { Providers } from "@/components/Providers";

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "OpenSakura Arena",
    template: "%s | OpenSakura Arena",
  },
  description: "JP→ZH translation arena with pairwise blind voting.",
  icons: {
    icon: [{ url: "/icon.svg", type: "image/svg+xml" }],
  },
  openGraph: {
    title: "OpenSakura Arena",
    description: "JP→ZH translation arena with pairwise blind voting.",
    siteName: "OpenSakura Arena",
    type: "website",
    locale: "en_US",
  },
  twitter: {
    card: "summary",
    title: "OpenSakura Arena",
    description: "JP→ZH translation arena with pairwise blind voting.",
  },
  other: {
    "theme-color": "#ec4899",
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={inter.className} suppressHydrationWarning>
      <head>
      </head>
      <body className="antialiased min-h-screen flex flex-col noise-overlay">
        <Providers>
          <Header />
          <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8">
            {children}
          </main>
          <Footer />
        </Providers>
      </body>
    </html>
  );
}
