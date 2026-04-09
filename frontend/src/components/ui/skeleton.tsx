import { cn } from "@/lib/utils";

/**
 * Skeleton placeholder for loading states.
 *
 * Variants:
 *  - default: rounded rectangle (text line, button, etc.)
 *  - circle: perfect circle (avatar, icon)
 *  - card: full glass-panel shaped skeleton
 */
function Skeleton({
  className,
  variant = "default",
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  variant?: "default" | "circle" | "card";
}) {
  return (
    <div
      className={cn(
        "shimmer rounded-md bg-muted/60",
        variant === "circle" && "rounded-full aspect-square",
        variant === "card" && "rounded-xl min-h-[120px]",
        className,
      )}
      {...props}
    />
  );
}

/**
 * Pre-composed skeleton group for a table with N rows.
 */
function SkeletonTable({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {/* header */}
      <div className="flex gap-4 mb-4">
        <Skeleton className="h-4 w-1/4" />
        <Skeleton className="h-4 w-1/6" />
        <Skeleton className="h-4 w-1/5" />
        <Skeleton className="h-4 w-1/6" />
      </div>
      {/* rows */}
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-4 items-center">
          <Skeleton className="h-8 w-1/4" />
          <Skeleton className="h-8 w-1/6" />
          <Skeleton className="h-8 w-1/5" />
          <Skeleton className="h-8 w-1/6" />
        </div>
      ))}
    </div>
  );
}

/**
 * Pre-composed skeleton for a form with labeled fields.
 */
function SkeletonForm({ fields = 4 }: { fields?: number }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {Array.from({ length: fields }).map((_, i) => (
        <div key={i} className="space-y-2">
          <Skeleton className="h-3.5 w-20" />
          <Skeleton className="h-9 w-full" />
        </div>
      ))}
    </div>
  );
}

export { Skeleton, SkeletonTable, SkeletonForm };
