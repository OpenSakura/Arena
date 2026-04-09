/**
 * frontend/src/app/battle/[battleId]/page.tsx
 *
 * Battle page (A/B translation comparison).
 *
 * Notes:
 * - Streams outputs via SSE.
 * - Keeps model identities hidden until after vote submission.
 */

import { Suspense } from "react";
import { BattleView } from "@/components/BattleView";

export default async function BattlePage({
  params: paramsPromise,
}: {
  params: Promise<{ battleId: string }>;
}) {
  const params = await paramsPromise;
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <BattleView battleId={params.battleId} />
    </Suspense>
  );
}
