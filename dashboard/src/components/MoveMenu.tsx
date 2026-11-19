import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { moveTargets, STATUS_LABELS } from "../tickets";
import type { TicketStatus, Transitions } from "../types";

interface Props {
  status: TicketStatus;
  transitions: Transitions | undefined;
  onMove: (to: TicketStatus) => void;
  disabled?: boolean;
  /** Names the ticket for screen readers, since every card has a "Chuyển…" button. */
  title: string;
}

/** "Chuyển…": the moves the server allows from `status`, for keyboard and touch users. */
export function MoveMenu({ status, transitions, onMove, disabled, title }: Props) {
  const targets = moveTargets(transitions, status);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLUListElement>(null);

  const close = (refocus: boolean) => {
    setOpen(false);
    setPos(null);
    if (refocus) btn.current?.focus();
  };

  // Below the button, right-aligned with it; above when there is no room below.
  useLayoutEffect(() => {
    if (!open || !btn.current || !menu.current) return;
    const r = btn.current.getBoundingClientRect();
    const mw = menu.current.offsetWidth;
    const mh = menu.current.offsetHeight;
    const left = Math.max(8, Math.min(r.right - mw, document.documentElement.clientWidth - mw - 8));
    const top = r.bottom + mh + 8 > window.innerHeight && r.top - mh - 4 > 0 ? r.top - mh - 4 : r.bottom + 4;
    setPos({ left, top });
    menu.current.querySelector("button")?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!menu.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) close(false);
    };
    const onResize = () => close(false);
    document.addEventListener("pointerdown", onDown);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onResize, true);
    };
  }, [open]);

  if (!targets.length) return null;
  // The card is draggable, and React events bubble out of the portal into it: presses and
  // keys here belong to the menu.
  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();
  return (
    <>
      <button
        ref={btn}
        type="button"
        className="move-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Chuyển trạng thái: ${title.slice(0, 60)}`}
        disabled={disabled}
        onPointerDown={stop}
        onMouseDown={stop}
        onTouchStart={stop}
        onKeyDown={stop}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          if (open) close(true);
          else setOpen(true);
        }}
      >
        Chuyển…
      </button>
      {open &&
        createPortal(
          <ul
            ref={menu}
            className="menu"
            role="menu"
            style={pos ?? { visibility: "hidden" }}
            onPointerDown={stop}
            onMouseDown={stop}
            onTouchStart={stop}
            onKeyDown={(e) => {
              e.stopPropagation();
              const items = [...(menu.current?.querySelectorAll("button") ?? [])];
              const i = items.indexOf(document.activeElement as HTMLButtonElement);
              if (e.key === "ArrowDown") {
                e.preventDefault();
                items[(i + 1) % items.length]?.focus();
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                items[(i - 1 + items.length) % items.length]?.focus();
              } else if (e.key === "Escape") {
                e.preventDefault();
                close(true);
              } else if (e.key === "Tab") {
                close(true);
              }
            }}
          >
            <li role="presentation" className="menu-title">
              Chuyển sang
            </li>
            {targets.map((to) => (
              <li key={to} role="presentation">
                <button
                  type="button"
                  role="menuitem"
                  className={`st-${to}`}
                  onClick={() => {
                    close(true);
                    onMove(to);
                  }}
                >
                  <span className="dot" aria-hidden="true" />
                  {STATUS_LABELS[to]}
                </button>
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </>
  );
}
