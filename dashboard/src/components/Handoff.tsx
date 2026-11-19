import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { handoffFileName, handoffUrl } from "../handoff";

type Done = { text: string; error?: boolean } | null;

/** Copies the text that ``text`` resolves to. The clipboard write starts inside the click,
 * with the text still loading: Safari refuses a write that begins after an await. */
async function copyLater(text: Promise<string>): Promise<void> {
  const clipboard = navigator.clipboard;
  // No clipboard API on plain http from a non-local host.
  if (!clipboard) throw new Error("trình duyệt không cho chép ở trang này");
  if (typeof ClipboardItem !== "undefined" && clipboard.write) {
    const blob = text.then((t) => new Blob([t], { type: "text/plain" }));
    await clipboard.write([new ClipboardItem({ "text/plain": blob })]);
  } else {
    await clipboard.writeText(await text);
  }
}

/** Hand the occurrence on screen to the developer's own coding agent: one click through the
 * local `herjev://` handler, or Copy prompt / Download .md, which need no installation.
 * All three deliver the same bundle, rendered by the portal. Keyed by ticket and occurrence,
 * so a result never lands on another one. */
export function Handoff({ fp, occ }: { fp: string; occ: string | null }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Done>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true; // again after StrictMode's test unmount
    return () => void (alive.current = false);
  }, []);
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(null), 2400);
    return () => clearTimeout(t);
  }, [done]);

  const report = (d: Done) => alive.current && setDone(d);

  const copy = async () => {
    if (!occ) return;
    setBusy(true);
    let fetchError: Error | null = null;
    const text = api.handoffText(fp, occ).catch((e: Error) => {
      fetchError = e;
      throw e;
    });
    try {
      await copyLater(text);
      report({ text: "Đã chép prompt" });
    } catch (e) {
      const err = fetchError ?? (e as Error);
      report({ text: fetchError ? `Không lấy được nội dung: ${err.message}` : `Không chép được: ${err.message}`, error: true });
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const download = async () => {
    if (!occ) return;
    setBusy(true);
    try {
      const text = await api.handoffText(fp, occ);
      const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = handoffFileName(fp, occ);
      document.body.append(a);
      a.click();
      a.remove();
      // Revoking at once can cancel the download in some browsers.
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      report({ text: "Đã tải .md" });
    } catch (e) {
      report({ text: `Không lấy được nội dung: ${(e as Error).message}`, error: true });
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  return (
    <section className="section" aria-labelledby="handoff-title">
      <h3 id="handoff-title">Handoff cho coding agent</h3>
      <div className="send-form">
        {occ ? (
          <a className="btn primary" href={handoffUrl(fp, occ)}>Mở trong coding agent</a>
        ) : (
          <button type="button" className="btn primary" disabled>Mở trong coding agent</button>
        )}
        <button type="button" className="btn ghost" disabled={!occ || busy} onClick={copy}>Copy prompt</button>
        <button type="button" className="btn ghost" disabled={!occ || busy} onClick={download}>Tải .md</button>
        <p className="faint send-note">
          Lần đầu? Chạy <code>herjev handoff setup</code> trên máy của bạn (docs/handoff.md).
        </p>
      </div>
      <p className={done?.error ? "error-text" : "muted"} role="status" aria-live="polite">{done?.text ?? ""}</p>
    </section>
  );
}
