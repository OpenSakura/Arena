/**
 * frontend/src/app/page.tsx
 *
 * Landing page with sakura theming, feature cards, and call-to-action.
 */

"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";

/* ---------- Inline SVG icons ---------- */

function SakuraPetal({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path
        d="M12 2C12 2 9.5 6.5 9.5 10C9.5 12.5 10.5 14 12 15C13.5 14 14.5 12.5 14.5 10C14.5 6.5 12 2 12 2Z"
        fill="currentColor"
        opacity="0.8"
      />
      <path
        d="M12 15C10.5 16 8 16.5 5.5 15.5C3 14.5 2 12 2 12C2 12 4 14.5 7 15C9 15.3 11 14.8 12 15Z"
        fill="currentColor"
        opacity="0.6"
      />
      <path
        d="M12 15C13.5 16 16 16.5 18.5 15.5C21 14.5 22 12 22 12C22 12 20 14.5 17 15C15 15.3 13 14.8 12 15Z"
        fill="currentColor"
        opacity="0.6"
      />
      <path
        d="M12 15C11 16.5 11 19 12 21.5C12 21.5 12 21.5 12 21.5C12 21.5 12 21.5 12 21.5C13 19 13 16.5 12 15Z"
        fill="currentColor"
        opacity="0.5"
      />
      <circle cx="12" cy="15" r="1.5" fill="currentColor" opacity="0.9" />
    </svg>
  );
}

function IconBlindTest({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
      <line x1="1" y1="1" x2="23" y2="23" />
      <rect x="7" y="10" width="4" height="6" rx="1" opacity="0.4" />
      <rect x="13" y="10" width="4" height="6" rx="1" opacity="0.4" />
    </svg>
  );
}

function IconVote({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z" />
      <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
    </svg>
  );
}

function IconChart({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
      <path d="M4 4l4 4 4-2 4 2 4-4" opacity="0.5" />
    </svg>
  );
}

/* ---------- Feature card icon map ---------- */

const FEATURES = [
  {
    title: "Blind A/B",
    description: "Two models translate the same text. You judge without knowing which is which.",
    Icon: IconBlindTest,
  },
  {
    title: "Community Voting",
    description: "Vote on accuracy, fluency, style, and naturalness to build consensus.",
    Icon: IconVote,
  },
  {
    title: "Elo & BT Rankings",
    description: "Models are ranked using Elo and Bradley-Terry with 95% confidence intervals.",
    Icon: IconChart,
  },
] as const;

/* ---------- How it works steps ---------- */

function IconStep1({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  );
}

function IconStep2({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <rect x="3" y="3" width="7" height="18" rx="1.5" />
      <rect x="14" y="3" width="7" height="18" rx="1.5" />
      <path d="M6.5 8h0" opacity="0.5" />
      <path d="M17.5 8h0" opacity="0.5" />
    </svg>
  );
}

function IconStep3({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}

function IconStep4({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M12 20V10" />
      <path d="M18 20V4" />
      <path d="M6 20v-4" />
      <path d="M2 20h20" />
    </svg>
  );
}

const STEPS = [
  {
    step: 1,
    title: "Source Text",
    description: "A Japanese passage is selected from our curated task pool.",
    Icon: IconStep1,
  },
  {
    step: 2,
    title: "Blind Translation",
    description: "Two models translate the same text. Identities are hidden.",
    Icon: IconStep2,
  },
  {
    step: 3,
    title: "Cast Your Vote",
    description: "You read both outputs and pick the better translation.",
    Icon: IconStep3,
  },
  {
    step: 4,
    title: "Rankings Update",
    description: "Models are re-ranked using Elo/BT after each vote.",
    Icon: IconStep4,
  },
] as const;

export default function HomePage() {
  return (
    <div className="relative flex flex-col items-center p-6">
      {/* ======= Hero Section ======= */}
      <section className="relative flex min-h-[75vh] w-full flex-col items-center justify-center text-center">
        {/* Decorative dot grid */}
        <div className="pointer-events-none absolute inset-0 bg-dot-grid opacity-30" />

        {/* Decorative glow orbs - sakura tinted */}
        <div className="pointer-events-none absolute left-1/4 top-1/4 -translate-x-1/2 -translate-y-1/2 h-[500px] w-[500px] rounded-full bg-primary/[0.04] blur-[120px]" />
        <div className="pointer-events-none absolute right-1/4 bottom-1/4 translate-x-1/2 translate-y-1/2 h-[400px] w-[400px] rounded-full bg-sakura-deep/[0.03] blur-[100px]" />
        <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 h-[300px] w-[300px] rounded-full bg-sakura/[0.025] blur-[80px]" />

        {/* Floating sakura petals (decorative) */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 1.5, duration: 1 }}
          >
            <SakuraPetal className="absolute top-[15%] left-[10%] h-4 w-4 text-primary/[0.12] animate-float" />
            <SakuraPetal className="absolute top-[25%] right-[15%] h-3 w-3 text-sakura/[0.10] animate-float [animation-delay:2s]" />
            <SakuraPetal className="absolute bottom-[30%] left-[20%] h-3.5 w-3.5 text-sakura-soft/[0.08] animate-float [animation-delay:4s]" />
            <SakuraPetal className="absolute top-[60%] right-[10%] h-3 w-3 text-primary/[0.10] animate-float [animation-delay:3s]" />
            <SakuraPetal className="absolute top-[40%] left-[70%] h-2.5 w-2.5 text-sakura-soft/[0.07] animate-float [animation-delay:5s]" />
            <SakuraPetal className="absolute top-[75%] left-[35%] h-3 w-3 text-primary/[0.09] animate-float [animation-delay:1.5s]" />
          </motion.div>
        </div>

        <div
          className="relative w-full max-w-3xl"
        >
          {/* Hero card */}
          <div className="rounded-3xl border border-foreground/10 dark:border-foreground/[0.06] bg-background/40 p-10 sm:p-12 backdrop-blur-2xl shadow-2xl shadow-black/10 dark:shadow-black/40 relative overflow-hidden">
            {/* Subtle top highlight */}
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/20 to-transparent rounded-t-3xl" />

            {/* Subtle inner glow */}
            <div className="absolute inset-0 bg-gradient-to-b from-primary/[0.02] to-transparent pointer-events-none" />

            {/* Sakura icon mark */}
            <div
              className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-primary/15 bg-primary/[0.08]"
            >
              <SakuraPetal className="h-7 w-7 text-primary" />
            </div>

            {/* Tagline badge */}
            <div
              className="mb-5 flex justify-center"
            >
              <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-primary/[0.06] px-3.5 py-1 text-[11px] font-semibold uppercase tracking-widest text-primary/80">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary/60 animate-pulse" />
                Open-source translation arena
              </span>
            </div>

            <h1
              className="mb-3 text-5xl sm:text-6xl font-extrabold tracking-tight"
            >
              <span className="bg-gradient-to-br from-zinc-950 via-zinc-700 to-zinc-500 dark:from-white dark:via-zinc-200 dark:to-zinc-500 bg-clip-text text-transparent">
                Open
              </span>
              <span className="bg-gradient-to-br from-sakura-deep via-sakura to-sakura-soft bg-clip-text text-transparent">
                Sakura
              </span>
              <span className="bg-gradient-to-br from-zinc-700 to-zinc-500 dark:from-zinc-300 dark:to-zinc-600 bg-clip-text text-transparent">
                {" "}Arena
              </span>
            </h1>

            <div
              className="mx-auto mb-6 h-px w-40 bg-gradient-to-r from-transparent via-primary/40 to-transparent"
            />

            <p
              className="mx-auto mb-8 max-w-xl text-base sm:text-lg leading-relaxed text-muted-foreground"
            >
              Pairwise, blind comparisons of JP&gt;ZH light-novel style translations.
              Vote on which output is better and help the community measure and improve translation models.
            </p>

            <div
              className="flex flex-wrap items-center justify-center gap-4"
            >
              <Link href="/battle/new">
                <Button size="lg" className="rounded-full px-8 text-base shadow-lg shadow-primary/20 hover:shadow-primary/30 hover:scale-[1.02] transition-all duration-200 animate-border-glow">
                  Start a Battle
                </Button>
              </Link>
              <Link href="/leaderboard">
                <Button variant="outline" size="lg" className="rounded-full px-8 text-base bg-background/50 hover:bg-background/80 border-border/50 hover:scale-[1.02] transition-all duration-200">
                  View Leaderboard
                </Button>
              </Link>
            </div>

            {/* Mini language badges */}
            <div
              className="mt-6 flex justify-center gap-2"
            >
              <span className="lang-badge-jp">JP</span>
              <span className="text-muted-foreground/30 text-xs">→</span>
              <span className="lang-badge-zh">ZH</span>
            </div>
          </div>

          {/* Feature cards */}
          <div
            className="mt-6 grid grid-cols-1 sm:grid-cols-3 gap-4"
          >
            {FEATURES.map((feature, i) => (
              <FeatureCard
                key={feature.title}
                title={feature.title}
                description={feature.description}
                Icon={feature.Icon}
              />
            ))}
          </div>
        </div>
      </section>

      {/* ======= How it Works Section ======= */}
      <section className="relative w-full max-w-4xl mt-20 mb-8">
        <div
          className="text-center mb-12"
        >
          <h2 className="text-2xl sm:text-3xl font-bold heading-gradient mb-3">How it Works</h2>
          <p className="text-sm text-muted-foreground max-w-md mx-auto">
            Four simple steps from source text to community-driven model rankings.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {STEPS.map((step, i) => (
            <div
              key={step.step}
              className="glass-panel p-5 text-center group hover:border-foreground/[0.12] transition-all duration-300 relative overflow-hidden hover-lift"
            >
              {/* Step number */}
              <div className="absolute top-3 right-3 text-[10px] font-bold uppercase tracking-widest text-muted-foreground/30">
                Step {step.step}
              </div>

              {/* Hover glow */}
              <div className="absolute inset-0 bg-gradient-to-br from-primary/[0.03] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none" />

              <div className="relative">
                <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-primary/15 bg-primary/[0.08] group-hover:bg-primary/[0.12] group-hover:border-primary/25 transition-all duration-300">
                  <step.Icon className="h-5 w-5 text-primary/80" />
                </div>
                <div className="text-sm font-semibold text-foreground/90 mb-1.5">{step.title}</div>
                <div className="text-xs leading-relaxed text-muted-foreground">{step.description}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Connecting line (desktop only) */}
        <div className="hidden lg:block absolute top-[calc(50%+12px)] left-[12.5%] w-[75%] pointer-events-none">
          <div
            className="h-px bg-gradient-to-r from-primary/5 via-primary/15 to-primary/5 origin-left"
          />
        </div>
      </section>

      {/* ======= Community Stats Section ======= */}
      <section className="w-full max-w-3xl mb-8">
        <div
          className="glass-panel-accent p-8"
        >
          <div className="grid grid-cols-3 gap-6 text-center">
            <StatBlock label="Rating System" value="Elo + BT" />
            <StatBlock label="Confidence" value="95% CI" />
            <StatBlock label="Voting" value="Blind A/B" />
          </div>
          <div className="divider-fade mt-6 mb-5" />
          <p className="text-center text-xs text-muted-foreground/60 leading-relaxed max-w-lg mx-auto">
            Join the community in evaluating JP→ZH translation quality. Every vote contributes to more accurate model rankings.
          </p>
        </div>
      </section>
    </div>
  );
}

function StatBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="group">
      <div className="text-lg sm:text-xl font-bold text-foreground/90 group-hover:text-primary transition-colors duration-200">
        {value}
      </div>
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60 mt-1">
        {label}
      </div>
    </div>
  );
}

function FeatureCard({
  title,
  description,
  Icon,
}: {
  title: string;
  description: string;
  Icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div
      className="glass-panel p-5 text-left group hover:border-foreground/[0.12] transition-all duration-300 relative overflow-hidden hover-lift"
    >
      {/* Hover glow effect */}
      <div className="absolute inset-0 bg-gradient-to-br from-primary/[0.03] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none" />

      <div className="relative">
        <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-xl border border-primary/15 bg-primary/[0.08] group-hover:bg-primary/[0.12] group-hover:border-primary/25 transition-all duration-300">
          <Icon className="h-4.5 w-4.5 text-primary/80" />
        </div>
        <div className="text-sm font-semibold text-foreground/90 mb-1.5">{title}</div>
        <div className="text-xs leading-relaxed text-muted-foreground">{description}</div>
      </div>
    </div>
  );
}
