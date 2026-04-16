import { useContext } from "react";

import { ArenaAuthContext, type ArenaAuthContextValue } from "@/auth/ArenaAuthProvider";

export function useArenaAuth(): ArenaAuthContextValue {
  const value = useContext(ArenaAuthContext);
  if (!value) {
    throw new Error("useArenaAuth must be used within an ArenaAuthProvider");
  }
  return value;
}
