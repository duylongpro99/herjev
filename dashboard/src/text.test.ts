import { describe, expect, it } from "vitest";

import { fold, matches } from "./text";

describe("fold", () => {
  it("strips Vietnamese diacritics", () => {
    expect(fold("Lỗi kết nối")).toBe("loi ket noi");
  });

  it("maps đ and Đ to d", () => {
    expect(fold("Đ")).toBe("d");
    expect(fold("đường")).toBe("duong");
  });
});

describe("matches", () => {
  it("matches every word in any order, ignoring marks", () => {
    expect(matches("Lỗi kết nối cơ sở dữ liệu", "du lieu loi")).toBe(true);
    expect(matches("Lỗi kết nối", "timeout")).toBe(false);
  });

  it("treats an empty query as a match", () => {
    expect(matches("anything", "  ")).toBe(true);
  });
});
