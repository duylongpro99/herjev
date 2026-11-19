import { type FormEvent, useState } from "react";

import { api } from "../api";

export function TokenGate({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    const ok = await api.login(token).catch(() => false);
    setBusy(false);
    if (ok) onDone();
    else setError("Token không đúng.");
  };

  return (
    <div className="gate" role="dialog" aria-modal="true" aria-label="Đăng nhập dashboard">
      <form onSubmit={submit}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">h</span>
          herjev
        </div>
        <span className="muted">Dashboard này yêu cầu token (HERJEV_DASHBOARD_TOKEN).</span>
        <input
          className="input"
          type="password"
          autoFocus
          autoComplete="current-password"
          aria-label="Token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        {error && <span className="error-text">{error}</span>}
        <button className="btn primary" type="submit" disabled={busy || !token}>
          Đăng nhập
        </button>
      </form>
    </div>
  );
}
