const pad = (n: number) => String(n).padStart(2, "0");

/** "14:15" today, "10-11 14:15" on other days (local time). */
export function shortTime(epoch: number): string {
  const d = new Date(epoch * 1000);
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const today = new Date();
  return d.toDateString() === today.toDateString() ? hm : `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
}

export function fullTime(epoch: number): string {
  const d = new Date(epoch * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${shortTime(epoch).slice(-5)}:${pad(d.getSeconds())}`;
}

export function ms(value: number | null | undefined): string {
  if (value == null) return "";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
}

export function ago(epoch: number, now = Date.now() / 1000): string {
  const s = Math.max(0, now - epoch);
  if (s < 60) return `${Math.round(s)} giây trước`;
  if (s < 3600) return `${Math.round(s / 60)} phút trước`;
  if (s < 86400) return `${Math.round(s / 3600)} giờ trước`;
  return `${Math.round(s / 86400)} ngày trước`;
}

/** "45s", "12p", "3g", "2n": the age on a card, where space is tight. */
export function agoShort(epoch: number, now = Date.now() / 1000): string {
  const s = Math.max(0, Math.round(now - epoch));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}p`;
  if (s < 86400) return `${Math.round(s / 3600)}g`;
  return `${Math.round(s / 86400)}n`;
}

export const URGENCY_LABELS = ["ignore", "investigate soon", "act within the hour", "page now"];

// "2026-10-29 10:31:28.789 UTC [1629819] FATAL:  " and the like, in any combination.
const LOG_PREFIX =
  /^\s*(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s?(?:Z|UTC|GMT|[+-]\d{2}:?\d{2}))?\s*)?(?:\[\d+\]\s*)?(?:(?:FATAL|PANIC|ERROR|WARN(?:ING)?|INFO|DEBUG|LOG):\s*)?/i;

/** A log line's message without its timestamp, pid and level, for titles in tight spaces. */
export function message(title: string): string {
  const rest = title.replace(LOG_PREFIX, "").trim();
  return rest || title;
}
