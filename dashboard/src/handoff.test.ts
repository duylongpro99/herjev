import { describe, expect, test } from "vitest";
import { handoffFileName, handoffUrl } from "./handoff";

describe("handoffUrl", () => {
  test("hex ids pass through unchanged", () => {
    expect(handoffUrl("0a1b2c3d4e5f6789", "abcdef012345")).toBe("herjev://handoff?ticket=0a1b2c3d4e5f6789&occ=abcdef012345");
  });

  test("without an occurrence the handler takes the newest", () => {
    expect(handoffUrl("0a1b", null)).toBe("herjev://handoff?ticket=0a1b");
  });

  test("odd input is encoded so it cannot add parameters", () => {
    expect(handoffUrl("a&portal=https://evil", "x y#z")).toBe(
      "herjev://handoff?ticket=a%26portal%3Dhttps%3A%2F%2Fevil&occ=x%20y%23z",
    );
  });
});

test("handoffFileName names the ticket and occurrence", () => {
  expect(handoffFileName("0a1b", "ff")).toBe("herjev-0a1b-ff.md");
  expect(handoffFileName("0a1b", null)).toBe("herjev-0a1b-latest.md");
});
