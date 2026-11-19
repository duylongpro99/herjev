/** The `herjev://` link that a developer's local handler (`herjev handoff setup`) opens:
 * it carries only the ticket and occurrence ids, never log text, the portal or a token. */
export function handoffUrl(fp: string, occ: string | null): string {
  const q = `ticket=${encodeURIComponent(fp)}${occ ? `&occ=${encodeURIComponent(occ)}` : ""}`;
  return `herjev://handoff?${q}`;
}

/** File name for a downloaded bundle. */
export const handoffFileName = (fp: string, occ: string | null) => `herjev-${fp}-${occ ?? "latest"}.md`;
