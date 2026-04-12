"use client";

export default function BattleError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="mx-auto max-w-5xl px-6 py-12 text-center">
      <h2 className="text-xl font-bold mb-4">Battle failed to load</h2>
      <p className="text-sm text-muted-foreground mb-6">
        {error.digest
          ? "An unexpected error occurred."
          : error.message || "An unexpected error occurred."}
      </p>
      <button
        type="button"
        onClick={reset}
        className="rounded-full border border-border px-4 py-2 text-sm hover:bg-foreground/5 transition-colors"
      >
        Try again
      </button>
    </div>
  );
}
