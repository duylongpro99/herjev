import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import { ago, fullTime, shortTime } from "../format";
import { POLL_MS, usePoll } from "../hooks";
import type { ChannelList, ChannelSend, SendStatus } from "../types";

/** How often the history reloads while a send is still open: the watcher picks sends up every few seconds. */
const OPEN_POLL_MS = 3_000;

const SEND_LABELS: Record<SendStatus, string> = {
  pending: "đang chờ watcher",
  sending: "đang gửi",
  sent: "đã gửi",
  failed: "lỗi",
  expired: "hết hạn",
};

const isOpen = (s: ChannelSend) => s.status === "pending" || s.status === "sending";

/** Send the ticket's newest occurrence to channels by hand, and what became of earlier sends.
 * The watcher delivers them with the same message as an automatic alert. */
export function ChannelSends({ fp }: { fp: string }) {
  const loadChannels = useCallback((signal: AbortSignal) => api.channels(signal), []);
  const list = usePoll<ChannelList>(loadChannels, "channels");
  const [open, setOpen] = useState(false);
  const loadSends = useCallback((signal: AbortSignal) => api.ticketSends(fp, signal), [fp]);
  const history = usePoll(loadSends, fp, open ? OPEN_POLL_MS : POLL_MS);
  const items = history.data?.items ?? [];
  const anyOpen = items.some(isOpen);
  useEffect(() => setOpen(anyOpen), [anyOpen]);

  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const channels = list.data?.channels ?? [];
  const online = list.data?.online ?? false;
  const chosen = picked.filter((n) => channels.some((c) => c.name === n));
  const reason = !list.data
    ? list.error
      ? `Không tải được danh sách kênh: ${list.error.message}`
      : null
    : !online
      ? "Watcher đang không chạy, nên chưa gửi được."
      : !channels.length
        ? "Watcher chưa công bố kênh nào."
        : null;

  const toggle = (name: string, on: boolean) =>
    setPicked((prev) => (on ? [...prev.filter((n) => n !== name), name] : prev.filter((n) => n !== name)));

  const send = async () => {
    setBusy(true);
    setFailure(null);
    try {
      await api.sendTicket(fp, chosen);
      setPicked([]);
      history.reload();
    } catch (e) {
      setFailure((e as Error).message);
      list.reload();
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="section sends" aria-labelledby="sends-title">
      <h3 id="sends-title">Gửi tới kênh</h3>
      {reason ? (
        <p className="muted">{reason}</p>
      ) : list.data ? (
        <div className="send-form">
          <fieldset className="send-channels">
            <legend className="sr-only">Chọn kênh</legend>
            {channels.map((c) => (
              <label key={c.name} className="send-channel">
                <input type="checkbox" checked={chosen.includes(c.name)} disabled={busy}
                  onChange={(e) => toggle(c.name, e.target.checked)} />
                <span>{c.name}</span>
                <span className="faint">{c.type}</span>
                {!c.auto && <span className="tag-manual" title="auto: false — không nhận cảnh báo tự động">chỉ gửi tay</span>}
              </label>
            ))}
          </fieldset>
          <button type="button" className="btn primary" disabled={busy || !chosen.length} onClick={send}>
            {busy ? "Đang gửi…" : chosen.length > 1 ? `Gửi tới ${chosen.length} kênh` : "Gửi"}
          </button>
          <p className="faint send-note">Gửi lần xảy ra mới nhất, cùng nội dung với cảnh báo tự động.</p>
        </div>
      ) : (
        <div className="panel-loading" aria-busy="true">
          <span className="sk sk-line short" />
        </div>
      )}
      {failure && <p className="error-text" role="alert">Không gửi được: {failure}</p>}
      {items.length > 0 && (
        <ul className="send-list" aria-live="polite">
          {items.map((s) => (
            <li key={s.id} className="send-row">
              <span className="mono num" title={fullTime(s.requested_at)}>{shortTime(s.requested_at)}</span>
              <span className="send-name">{s.channel}</span>
              <span className={`send-${s.status}`} title={s.finished_at ? `xong ${ago(s.finished_at)}` : undefined}>
                {SEND_LABELS[s.status] ?? s.status}
              </span>
              {s.error && <span className="send-error">{s.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
