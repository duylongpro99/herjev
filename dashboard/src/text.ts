/**
 * Lower-case `s` with Vietnamese diacritics removed, so "Lỗi kết nối" matches "loi ket noi".
 * `đ` is a letter of its own, not a base letter plus a mark, so NFD leaves it alone.
 */
export function fold(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase();
}

/** True when every whitespace-separated word of `query` appears somewhere in `hay`. */
export function matches(hay: string, query: string): boolean {
  const h = fold(hay);
  return fold(query).split(/\s+/).filter(Boolean).every((w) => h.includes(w));
}
