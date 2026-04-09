export default function LeaderboardLoading() {
  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      <div className="animate-pulse space-y-4">
        <div className="h-8 w-48 rounded bg-muted/60" />
        <div className="h-64 rounded bg-muted/40" />
      </div>
    </div>
  );
}
