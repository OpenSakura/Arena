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

export { Skeleton };
