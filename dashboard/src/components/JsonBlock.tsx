import { useState } from "react";

function pretty(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

/** Raw request/response text, pretty-printed when it is JSON, with a copy button. */
export function JsonBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const shown = pretty(text);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(shown);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard unavailable (plain http on a non-local host) */
    }
  };
  return (
    <div className="json">
      <button type="button" className="btn copy" onClick={copy}>
        {copied ? "Đã chép" : "Chép"}
      </button>
      <pre>{shown}</pre>
    </div>
  );
}
