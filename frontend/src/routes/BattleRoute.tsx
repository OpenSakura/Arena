import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { BattleView } from "@/components/BattleView";

export default function BattleRoute() {
  const { t } = useTranslation();
  const { battleId } = useParams<{ battleId: string }>();

  if (!battleId) {
    return <div>{t("battle.errors.invalidBattleId")}</div>;
  }

  return <BattleView battleId={battleId} />;
}
