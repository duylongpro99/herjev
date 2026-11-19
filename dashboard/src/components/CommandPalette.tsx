import { useEffect, useMemo, useRef, useState } from "react";

import { LEVEL_RANK } from "../lanes";
import { message } from "../format";
import { STATUS_LABELS, TERMINAL } from "../tickets";
import { matches } from "../text";
import type { TicketSummary } from "../types";
import { Icon } from "./Icon";
import { LevelBadge } from "./LevelBadge";

export interface Command {
  group: string;
  label: string;
  ctx?: string;
  icon: "filter" | "board" | "arrow";
  run: () => void;
}

interface Item {
  key: string;
  group: string;
  label: string;
  ctx?: string;
  hay: string;
  icon?: Command["icon"];
  ticket?: TicketSummary;
  run: () => void;
}

interface Props {
  open: boolean;
  onClose: () => void;
  commands: Command[];
  tickets: TicketSummary[];
  onOpenTicket: (t: TicketSummary) => void;
}

/** Ctrl/⌘K: jump to any loaded ticket or run a board command, matched without diacritics. */
export function CommandPalette({ open, onClose, commands, tickets, onOpenTicket }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (open && !dlg.open) {
      setQuery("");
      setActive(0);
      dlg.showModal();
      inputRef.current?.focus();
    } else if (!open && dlg.open) {
      dlg.close();
    }
  }, [open]);

  const items = useMemo<Item[]>(() => {
    const cmds: Item[] = commands.map((c, i) => ({
      key: `c${i}`,
      group: c.group,
      label: c.label,
      ctx: c.ctx,
      icon: c.icon,
      hay: `${c.group} ${c.label} ${c.ctx ?? ""}`,
      run: c.run,
    }));
    const closed = (t: TicketSummary) => (TERMINAL.has(t.status) ? 1 : 0);
    const ticketItems: Item[] = [...tickets]
      .sort((a, b) => closed(a) - closed(b) || LEVEL_RANK[b.max_level] - LEVEL_RANK[a.max_level] || b.last_seen - a.last_seen)
      .map((t) => ({
        key: t.fingerprint,
        group: "Ticket",
        label: message(t.title),
        ctx: `${t.project}/${t.service || "—"} · ${STATUS_LABELS[t.status]}`,
        hay: `Ticket ${t.title} ${t.project} ${t.service} ${t.fingerprint} ${t.max_level} ${STATUS_LABELS[t.status]}`,
        ticket: t,
        run: () => onOpenTicket(t),
      }));
    return [...cmds, ...ticketItems].filter((it) => matches(it.hay, query));
  }, [commands, tickets, onOpenTicket, query]);

  const at = Math.min(active, Math.max(0, items.length - 1));
  useEffect(() => {
    listRef.current?.querySelector(`[aria-selected="true"]`)?.scrollIntoView({ block: "nearest" });
  }, [at, items]);

  const run = (i: number) => {
    const it = items[i];
    if (!it) return;
    onClose();
    it.run();
  };
  const step = (d: number) => items.length && setActive((at + d + items.length) % items.length);

  let lastGroup = "";
  return (
    <dialog
      ref={ref}
      className="palette"
      aria-label="Bảng lệnh"
      onClose={onClose}
      // A click on the backdrop lands on the dialog itself.
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="pal-input-wrap">
        <Icon name="search" />
        <input
          ref={inputRef}
          className="pal-input"
          type="text"
          placeholder="Tìm ticket, project, service hoặc lệnh…"
          autoComplete="off"
          spellCheck={false}
          role="combobox"
          aria-expanded="true"
          aria-controls="pal-list"
          aria-autocomplete="list"
          aria-label="Tìm ticket hoặc lệnh"
          aria-activedescendant={items.length ? `pal-${at}` : undefined}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              step(1);
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              step(-1);
            } else if (e.key === "Enter") {
              e.preventDefault();
              run(at);
            }
          }}
        />
      </div>
      <ul ref={listRef} className="pal-list" id="pal-list" role="listbox" aria-label="Kết quả">
        {items.length === 0 && <li className="pal-empty">Không có kết quả cho “{query}”.</li>}
        {items.map((it, i) => {
          const head = it.group !== lastGroup;
          lastGroup = it.group;
          return [
            head && (
              <li key={`g-${it.group}`} className="pal-group" role="presentation">
                {it.group}
              </li>
            ),
            <li
              key={it.key}
              id={`pal-${i}`}
              className="pal-item"
              role="option"
              aria-selected={i === at}
              onMouseMove={() => i !== at && setActive(i)}
              onClick={() => run(i)}
            >
              {it.ticket ? (
                <LevelBadge level={it.ticket.max_level} />
              ) : (
                <Icon name={it.icon!} size={16} play={i === at} />
              )}
              <span className="lbl">{it.label}</span>
              {it.ctx && <span className="ctx">{it.ctx}</span>}
            </li>,
          ];
        })}
      </ul>
      <div className="pal-foot" aria-hidden="true">
        <span>
          <kbd>↑</kbd>
          <kbd>↓</kbd> chọn
        </span>
        <span>
          <kbd>↵</kbd> thực hiện
        </span>
        <span>
          <kbd>esc</kbd> đóng
        </span>
        <span>
          <kbd>/</kbd> mở nhanh
        </span>
      </div>
    </dialog>
  );
}
