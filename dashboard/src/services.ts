import type { ServiceChange, SourceServices } from "./types";

/** The changes that tick or untick one service. While the config decides, the first tick
 * turns its current set into an explicit choice, so unticking one keeps the others. */
export function toggleChanges(source: SourceServices, label: string, on: boolean): ServiceChange[] {
  if (source.chosen) return [{ label, watched: on }];
  return source.services
    .filter((s) => !s.missing)
    .map((s) => ({ label: s.label, watched: s.label === label ? on : s.watched }))
    .filter((c) => c.watched || c.label === label);
}

/** The changes that hand the choice back to the config: every watched row unticked. */
export const resetChanges = (source: SourceServices): ServiceChange[] =>
  source.services.filter((s) => s.watched).map((s) => ({ label: s.label, watched: false }));
