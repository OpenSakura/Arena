/**
 * frontend/src/components/Footer.tsx
 *
 * Site footer with project links, GitHub, and sakura branding.
 */

import Link from "next/link";

function SakuraIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path
        d="M12 2C12 2 9.5 6.5 9.5 10C9.5 12.5 10.5 14 12 15C13.5 14 14.5 12.5 14.5 10C14.5 6.5 12 2 12 2Z"
        fill="currentColor"
        opacity="0.85"
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
      <circle cx="12" cy="15" r="1.5" fill="currentColor" opacity="0.9" />
    </svg>
  );
}

function GitHubIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden>
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z" />
    </svg>
  );
}

export function Footer() {
  return (
    <footer className="mt-auto border-t border-border/30 bg-background/30 backdrop-blur-sm">
      {/* Gradient separator */}
      <div className="h-px bg-gradient-to-r from-transparent via-primary/10 to-transparent" />

      <div className="mx-auto max-w-7xl px-6 py-8">
        <div className="grid grid-cols-1 gap-8 sm:grid-cols-3">
          {/* Brand column */}
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <SakuraIcon className="h-5 w-5 text-primary/70" />
              <span className="text-sm font-bold bg-gradient-to-r from-sakura-deep to-sakura-soft bg-clip-text text-transparent">
                OpenSakura Arena
              </span>
            </div>
            <p className="text-xs leading-relaxed text-muted-foreground/60 max-w-[240px]">
              Open-source platform for blind pairwise evaluation of JP→ZH translation models. Powered by community votes.
            </p>
            <div className="flex items-center gap-2 mt-1">
              <span className="lang-badge-jp">JP</span>
              <span className="text-muted-foreground/30 text-[10px]">→</span>
              <span className="lang-badge-zh">ZH</span>
            </div>
          </div>

          {/* Navigation column */}
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/40 mb-3">
              Navigation
            </div>
            <nav aria-label="Footer navigation" className="grid gap-2 text-sm text-muted-foreground">
              <Link href="/battle/new" className="transition-colors hover:text-foreground w-fit">
                Battle
              </Link>
              <Link href="/leaderboard" className="transition-colors hover:text-foreground w-fit">
                Leaderboard
              </Link>
              <Link href="/onboarding" className="transition-colors hover:text-foreground w-fit">
                Profile
              </Link>
            </nav>
          </div>

          {/* Links column */}
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/40 mb-3">
              Project
            </div>
            <nav aria-label="Project links" className="grid gap-2 text-sm text-muted-foreground">
              <a
                href="https://github.com/OpenSakura"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 transition-colors hover:text-foreground w-fit group"
              >
                <GitHubIcon className="h-3.5 w-3.5 group-hover:text-foreground transition-colors" />
                GitHub
              </a>

            </nav>
          </div>
        </div>

        {/* Bottom bar */}
        <div className="divider-fade mt-8 mb-4" />
        <div className="flex flex-col sm:flex-row items-center justify-between gap-2 text-[11px] text-muted-foreground/40">
          <span>Built with Next.js, FastAPI & community passion</span>
          <span>Elo + Bradley-Terry rating systems</span>
        </div>
      </div>
    </footer>
  );
}
