import type { Level } from "../types";

export function LevelBadge({ level }: { level: Level }) {
  return <span className={`lvl lvl-${level}`}>{level.toUpperCase()}</span>;
}
