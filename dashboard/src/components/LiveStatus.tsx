import { useEffect, useState } from "react";

import { ago } from "../format";
import type { Status } from "../types";
import { Icon } from "./Icon";

const STALE_SECONDS = 60;

/** Whether the watcher process wrote its heartbeat recently. */
export function WatcherStatus({ status }: { status: Status | null }) {
  const at = status?.watcher?.updated_at;
  if (!status) return <span className="watcher faint">watcher: …</span>;
  if (!at) {
    return (
      <span className="watcher">
        <span className="dot idle" aria-hidden="true" />
        watcher: chưa chạy
      </span>
    );
  }
  const fresh = Date.now() / 1000 - at < STALE_SECONDS;
  return (
    <span className="watcher" title={status.db_path}>
      <span className={`dot ${fresh ? "ok" : "bad"}`} aria-hidden="true" />
      watcher: {fresh ? "ok" : `không cập nhật từ ${ago(at)}`}
    </span>
  );
}

interface Props {
  /** When the board last loaded, in ms since the epoch. */
  updatedAt: number | null;
  error: Error | null;
  onRefresh: () => void;
}

function since(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 5) return "vừa xong";
  if (s < 60) return `${s} giây trước`;
  return `${Math.round(s / 60)} phút trước`;
}

/** How fresh the board is, ticking every second, with a manual refresh. */
export function LiveStatus({ updatedAt, error, onRefresh }: Props) {
  const [now, setNow] = useState(Date.now());
  const [spinning, setSpinning] = useState(false);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => setSpinning(false), [updatedAt, error]);

  const state = error ? "bad" : updatedAt ? "ok" : "idle";
  const text = error ? "Mất kết nối" : updatedAt ? `Cập nhật ${since(now - updatedAt)}` : "Đang tải…";
  return (
    <div className={`live live-${state}`} title={error?.message}>
      {/* Keyed by the load time so the pulse replays on every update. */}
      <span key={updatedAt ?? 0} className="live-dot" aria-hidden="true" />
      <span className="live-text" aria-live="polite">
        {text}
      </span>
      <button
        type="button"
        className={`btn ghost icon${spinning ? " spinning" : ""}`}
        aria-label="Tải lại"
        title="Tải lại"
        onClick={() => {
          setSpinning(true);
          onRefresh();
        }}
      >
        <Icon name="refresh" play={spinning} />
      </button>
    </div>
  );
}
