import { describe, expect, it } from "vitest";

import { agoShort, message } from "./format";

describe("message", () => {
  it("drops a postgres-style timestamp, pid and level", () => {
    expect(message('2026-10-29 10:31:28.789 UTC [1629819] FATAL:  password authentication failed for user "wog"')).toBe(
      'password authentication failed for user "wog"',
    );
  });

  it("drops an ISO timestamp with offset and a lower-case level", () => {
    expect(message("2026-10-29T03:30:23+07:00 error: relation missing")).toBe("relation missing");
  });

  it("leaves a plain message alone", () => {
    expect(message("Operation failed with internal error.")).toBe("Operation failed with internal error.");
  });

  it("keeps the title when nothing follows the prefix", () => {
    expect(message("2026-10-29 10:31:28 UTC [12] FATAL: ")).toBe("2026-10-29 10:31:28 UTC [12] FATAL: ");
  });

  it("does not eat a level word that is not a prefix", () => {
    expect(message("ERROR budget exceeded")).toBe("ERROR budget exceeded");
  });
});

describe("agoShort", () => {
  it("picks the largest whole unit", () => {
    expect(agoShort(1000, 1045)).toBe("45s");
    expect(agoShort(1000, 1000 + 12 * 60)).toBe("12p");
    expect(agoShort(1000, 1000 + 3 * 3600)).toBe("3g");
    expect(agoShort(1000, 1000 + 2 * 86400)).toBe("2n");
  });

  it("clamps a future time to zero", () => {
    expect(agoShort(2000, 1000)).toBe("0s");
  });
});
