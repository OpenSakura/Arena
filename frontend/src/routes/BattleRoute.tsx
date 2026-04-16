import { useParams } from "react-router-dom";
import { BattleView } from "@/components/BattleView";

export default function BattleRoute() {
  const { battleId } = useParams<{ battleId: string }>();

  if (!battleId) {
    return <div>Invalid battle ID</div>;
  }

  return <BattleView battleId={battleId} />;
}
