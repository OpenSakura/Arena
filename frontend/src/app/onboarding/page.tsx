/**
 * frontend/src/app/onboarding/page.tsx
 *
 * User onboarding/profile capture.
 *
 * Notes:
 * - Anonymous voting is allowed, but logged-in users can optionally add
 *   language/experience info to improve downstream filtering.
 */

"use client";

import { useEffect, useState } from "react";

import { apiGet, apiPut } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useAuthHeaders } from "@/hooks/useAuthHeaders";
import { parseMeResponse } from "@/types/me";

const JLPT_LEVELS = ["unknown", "N1", "N2", "N3", "N4", "N5"] as const;
type JlptLevel = (typeof JLPT_LEVELS)[number];

const EXPERIENCE_YEARS = ["unknown", "0", "<1", "1-3", "3-5", "5+"] as const;
type ExperienceYears = (typeof EXPERIENCE_YEARS)[number];

const EXPERIENCE_ROLES = ["translator", "editor", "qc", "tl"] as const;
type ExperienceRole = (typeof EXPERIENCE_ROLES)[number];

export default function OnboardingPage() {
  const { authStatus, accessToken, headers } = useAuthHeaders();
  const canSave = authStatus === "authenticated" && Boolean(accessToken);

  const [displayName, setDisplayName] = useState("");
  const [uiLanguage, setUiLanguage] = useState("en");
  const [zhVariant, setZhVariant] = useState("zh-Hans");

  const [jlpt, setJlpt] = useState<JlptLevel>("unknown");
  const [experienceYears, setExperienceYears] = useState<ExperienceYears>("unknown");
  const [experienceRoles, setExperienceRoles] = useState<ExperienceRole[]>([]);
  const [consentResearch, setConsentResearch] = useState(false);

  const [loadingProfile, setLoadingProfile] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!canSave) return;

      setLoadingProfile(true);
      setErrorText(null);
      try {
        const me = parseMeResponse(await apiGet("/me", { headers }));
        if (cancelled) return;
        const profile = me.profile ?? {};

        setDisplayName((profile.display_name as string) ?? "");
        setUiLanguage((profile.ui_language as string) ?? "en");
        setZhVariant((profile.zh_variant as string) ?? "zh-Hans");

        const jpProf = (profile.jp_proficiency as Record<string, unknown>) ?? null;
        const profileJlpt = (jpProf?.jlpt as string) ?? "unknown";
        setJlpt((JLPT_LEVELS as readonly string[]).includes(profileJlpt) ? (profileJlpt as JlptLevel) : "unknown");

        const tx = (profile.translation_experience as Record<string, unknown>) ?? null;
        const jpZh = (tx?.jp_zh as Record<string, unknown>) ?? null;
        const years = (jpZh?.years as string) ?? "unknown";
        setExperienceYears(
          (EXPERIENCE_YEARS as readonly string[]).includes(years) ? (years as ExperienceYears) : "unknown",
        );

        const roles = (jpZh?.roles as unknown) ?? [];
        if (Array.isArray(roles)) {
          const filtered = roles.filter((r): r is ExperienceRole => EXPERIENCE_ROLES.includes(r));
          setExperienceRoles(filtered);
        }

        const consents = (profile.consents as Record<string, unknown>) ?? null;
        setConsentResearch(Boolean(consents?.research_use));
      } catch (err) {
        if (cancelled) return;
        setErrorText(err instanceof Error ? err.message : "Failed to load profile");
      } finally {
        if (!cancelled) setLoadingProfile(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [canSave, headers]);

  function toggleRole(role: ExperienceRole) {
    setExperienceRoles((prev) => (prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]));
  }

  async function handleSave() {
    if (!canSave) return;

    setSaving(true);
    setErrorText(null);
    try {
      const payload = {
        display_name: displayName.trim() ? displayName.trim() : null,
        ui_language: uiLanguage.trim() ? uiLanguage.trim() : null,
        zh_variant: zhVariant.trim() ? zhVariant.trim() : null,
        jp_proficiency: jlpt === "unknown" ? null : { jlpt },
        translation_experience:
          experienceYears === "unknown" && experienceRoles.length === 0
            ? null
            : {
                jp_zh: {
                  years: experienceYears === "unknown" ? null : experienceYears,
                  roles: experienceRoles,
                },
              },
        consents: { research_use: consentResearch },
      };

      const res = parseMeResponse(await apiPut("/me/profile", payload, { headers }));
      setSavedAt((res.profile?.completed_at as string) ?? new Date().toISOString());
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "Failed to save profile");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto grid max-w-3xl gap-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-primary/15 bg-primary/[0.08]">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-4.5 w-4.5 text-primary/80" aria-hidden>
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
            <circle cx="12" cy="7" r="4" />
          </svg>
        </div>
        <div>
          <h2 className="heading-gradient text-3xl">Profile</h2>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            Add a little context about your language background. This helps with offline analysis and filtering.
          </p>
        </div>
      </div>

      {/* Auth notice */}
      {authStatus === "loading" ? (
        <div className="glass-panel p-6">
          <div className="flex items-center gap-3">
            <div className="h-4 w-4 rounded-full shimmer bg-muted/60" />
            <span className="text-sm text-muted-foreground">Checking login...</span>
          </div>
        </div>
      ) : !canSave ? (
        <div className="glass-panel-accent p-6">
          <div className="flex items-center gap-2.5">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5 text-amber-600 dark:text-amber-400/70" aria-hidden>
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <div className="font-semibold text-foreground">Login required to save</div>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground ml-[30px]">
            You can battle and vote anonymously, but profile info is only stored for logged-in
            users.
          </p>
        </div>
      ) : null}

      {/* Form */}
      <section className={`glass-panel-accent p-6 ${canSave && !loadingProfile ? "opacity-100" : "opacity-50 pointer-events-none"}`}>
        <div className="grid gap-6">
          {/* Section: Identity */}
          <div>
            <div className="flex items-center gap-2 mb-4">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 text-primary/60" aria-hidden>
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Identity</span>
            </div>

            {/* Display name */}
            <div className="grid gap-2">
              <label className="label-premium" htmlFor="display-name">
                Display name (optional)
              </label>
              <input
                id="display-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                disabled={!canSave}
                placeholder="e.g., N1 translator"
                className="input-premium"
              />
            </div>
          </div>

          <div className="divider-fade" />

          {/* Section: Language preferences */}
          <div>
            <div className="flex items-center gap-2 mb-4">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 text-primary/60" aria-hidden>
                <path d="M4 7V4h16v3" />
                <path d="M9 20h6" />
                <path d="M12 4v16" />
              </svg>
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Language Preferences</span>
            </div>

            {/* UI language + ZH variant */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="grid gap-2">
                <label className="label-premium" htmlFor="ui-language">
                  UI language
                </label>
                <select
                  id="ui-language"
                  value={uiLanguage}
                  onChange={(e) => setUiLanguage(e.target.value)}
                  disabled={!canSave}
                  className="input-premium"
                >
                  <option value="en">English</option>
                  <option value="zh">Chinese</option>
                  <option value="ja">Japanese</option>
                </select>
              </div>

              <div className="grid gap-2">
                <label className="label-premium" htmlFor="zh-variant">
                  Chinese variant
                </label>
                <select
                  id="zh-variant"
                  value={zhVariant}
                  onChange={(e) => setZhVariant(e.target.value)}
                  disabled={!canSave}
                  className="input-premium"
                >
                  <option value="zh-Hans">Simplified (zh-Hans)</option>
                  <option value="zh-Hant">Traditional (zh-Hant)</option>
                  <option value="unknown">Unknown</option>
                </select>
              </div>
            </div>
          </div>

          <div className="divider-fade" />

          {/* Section: Experience */}
          <div>
            <div className="flex items-center gap-2 mb-4">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 text-primary/60" aria-hidden>
                <line x1="18" y1="20" x2="18" y2="10" />
                <line x1="12" y1="20" x2="12" y2="4" />
                <line x1="6" y1="20" x2="6" y2="14" />
              </svg>
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Experience</span>
            </div>

            {/* JLPT + Experience years */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 mb-5">
              <div className="grid gap-2">
                <label className="label-premium" htmlFor="jlpt">
                  Japanese proficiency (self-reported)
                </label>
                <select
                  id="jlpt"
                  value={jlpt}
                  onChange={(e) => setJlpt(e.target.value as JlptLevel)}
                  disabled={!canSave}
                  className="input-premium"
                >
                  {JLPT_LEVELS.map((lvl) => (
                    <option key={lvl} value={lvl}>
                      {lvl}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid gap-2">
                <label className="label-premium" htmlFor="experience-years">
                  JP-&gt;ZH experience (years)
                </label>
                <select
                  id="experience-years"
                  value={experienceYears}
                  onChange={(e) => setExperienceYears(e.target.value as ExperienceYears)}
                  disabled={!canSave}
                  className="input-premium"
                >
                  {EXPERIENCE_YEARS.map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Roles */}
            <div className="grid gap-3">
              <div className="label-premium">Roles</div>
              <div className="flex flex-wrap gap-2">
                {EXPERIENCE_ROLES.map((role) => {
                  const active = experienceRoles.includes(role);
                  return (
                    <button
                      key={role}
                      type="button"
                      onClick={() => toggleRole(role)}
                      disabled={!canSave}
                      className={`rounded-full border px-4 py-1.5 text-xs font-semibold uppercase tracking-wider transition-all duration-200 ${
                        active
                          ? "border-primary/30 bg-primary/15 text-primary shadow-sm shadow-primary/10"
                          : "border-border bg-foreground/5 text-muted-foreground hover:bg-foreground/10 hover:text-foreground hover:border-foreground/15"
                      } ${!canSave ? "cursor-not-allowed" : "cursor-pointer"}`}
                    >
                      {role}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="divider-fade" />

          {/* Section: Consent */}
          <div>
            <div className="flex items-center gap-2 mb-4">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 text-primary/60" aria-hidden>
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              </svg>
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Consent</span>
            </div>

            <label className="flex items-start gap-3 cursor-pointer group">
              <input
                type="checkbox"
                checked={consentResearch}
                onChange={(e) => setConsentResearch(e.target.checked)}
                disabled={!canSave}
                className="mt-0.5 h-4 w-4 rounded border-border accent-primary"
              />
              <span className="text-sm text-muted-foreground leading-relaxed group-hover:text-foreground/80 transition-colors">
                Allow using my profile answers for offline filtering/research.
              </span>
            </label>
          </div>

          {/* Save */}
          <div className="flex flex-wrap items-center gap-4 border-t border-border pt-6">
            <Button
              type="button"
              onClick={() => void handleSave()}
              disabled={!canSave || saving}
              className="rounded-full px-6 shadow-lg shadow-primary/20 hover:shadow-primary/30 hover:scale-[1.01] transition-all"
            >
              {saving ? "Saving..." : "Save profile"}
            </Button>

            {loadingProfile ? (
              <span className="text-sm text-muted-foreground flex items-center gap-2">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary/60 shimmer" />
                Loading...
              </span>
            ) : null}
            {savedAt ? (
              <span className="text-xs text-emerald-600 dark:text-emerald-400/80 flex items-center gap-1.5">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5" aria-hidden>
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                Saved successfully
              </span>
            ) : null}
          </div>

          {errorText ? (
            <p className="text-sm text-destructive flex items-center gap-2">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5 shrink-0" aria-hidden>
                <circle cx="12" cy="12" r="10" />
                <line x1="15" y1="9" x2="9" y2="15" />
                <line x1="9" y1="9" x2="15" y2="15" />
              </svg>
              {errorText}
            </p>
          ) : null}
        </div>
      </section>
    </div>
  );
}
