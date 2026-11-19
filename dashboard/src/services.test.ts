import { describe, expect, test } from "vitest";

import { resetChanges, toggleChanges } from "./services";
import type { Service, SourceServices } from "./types";

const svc = (label: string, watched: boolean, missing = false): Service =>
  ({ label, name: label, default_name: label, display_name: null, watched, missing });

describe("service choices", () => {
  test("while the config decides, unticking one keeps the others watched", () => {
    const source: SourceServices = { name: "loki", chosen: false, services: [svc("a", true), svc("b", true), svc("c", false)] };
    expect(toggleChanges(source, "b", false)).toEqual([{ label: "a", watched: true }, { label: "b", watched: false }]);
    expect(toggleChanges(source, "c", true)).toEqual([
      { label: "a", watched: true }, { label: "b", watched: true }, { label: "c", watched: true }]);
  });

  test("once chosen, a tick changes only that service", () => {
    const source: SourceServices = { name: "loki", chosen: true, services: [svc("a", true), svc("b", false)] };
    expect(toggleChanges(source, "b", true)).toEqual([{ label: "b", watched: true }]);
  });

  test("back to the config unticks every watched service, gone ones included", () => {
    const source: SourceServices = { name: "loki", chosen: true, services: [svc("a", true), svc("b", false), svc("c", true, true)] };
    expect(resetChanges(source)).toEqual([{ label: "a", watched: false }, { label: "c", watched: false }]);
  });
});
