import { useEffect } from "react";
import { useTranslation } from "react-i18next";

interface ErrorBoundaryProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function ErrorBoundary({ error, reset }: ErrorBoundaryProps) {
  const { t } = useTranslation();

  useEffect(() => {
    console.error("Unhandled error:", error);
  }, [error]);

  return (
    <div className="flex min-h-[400px] flex-col items-center justify-center gap-4">
      <h2 className="text-xl font-semibold">{t("errors.boundary.title")}</h2>
      <p className="text-muted-foreground text-sm">
        {error.message || t("errors.boundary.unexpected")}
      </p>
      <button
        onClick={reset}
        className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90"
      >
        {t("errors.boundary.tryAgain")}
      </button>
    </div>
  );
}
