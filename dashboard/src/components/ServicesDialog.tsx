import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api";
import { usePoll } from "../hooks";
import { resetChanges, toggleChanges } from "../services";
import type { Service, ServiceChange, ServiceList, SourceServices } from "../types";
import { Icon } from "./Icon";

function NameInput({ service, disabled, onSave }: {
  service: Service; disabled: boolean; onSave: (name: string) => void;
}) {
  const [value, setValue] = useState(service.display_name ?? "");
  useEffect(() => setValue(service.display_name ?? ""), [service.display_name]);
  const save = () => {
    if (value.trim() !== (service.display_name ?? "")) onSave(value);
  };
  return (
    <input
      className="svc-name"
      type="text"
      value={value}
      maxLength={100}
      placeholder={service.default_name}
      disabled={disabled}
      aria-label={`Tên hiển thị của ${service.label}`}
      onChange={(e) => setValue(e.target.value)}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") {
          // Esc reverts the field; a second Esc closes the dialog.
          if (value !== (service.display_name ?? "")) {
            e.preventDefault();
            setValue(service.display_name ?? "");
          }
        }
      }}
    />
  );
}

function SourceBlock({ source, busy, onChange }: {
  source: SourceServices; busy: boolean; onChange: (changes: ServiceChange[]) => void;
}) {
  const watched = source.services.filter((s) => s.watched).length;
  const total = source.services.filter((s) => !s.missing).length;
  return (
    <section className="svc-source" aria-labelledby={`svc-${source.name}`}>
      <div className="svc-head">
        <h3 id={`svc-${source.name}`}>{source.name}</h3>
        <span className="muted">
          {source.chosen
            ? `Chỉ theo dõi ${watched} service đã chọn.`
            : `Theo cấu hình: ${watched === total ? "mọi service" : `${watched}/${total} service`}.`}
        </span>
        {source.chosen && (
          <button type="button" className="btn sm" disabled={busy} onClick={() => onChange(resetChanges(source))}>
            Về cấu hình
          </button>
        )}
      </div>
      {source.services.length ? (
        <ul className="svc-list">
          {source.services.map((s) => (
            <li key={s.label} className={`svc-row${s.watched ? " on" : ""}`}>
              <input
                type="checkbox"
                checked={s.watched}
                disabled={busy}
                aria-label={`Theo dõi ${s.name}`}
                onChange={(e) => onChange(toggleChanges(source, s.label, e.target.checked))}
              />
              <NameInput service={s} disabled={busy}
                onSave={(name) => onChange([{ label: s.label, display_name: name }])} />
              <span className="svc-label mono faint" title={s.label}>
                {s.label !== s.name ? s.label : ""}
              </span>
              {s.missing && <span className="tag-manual" title="Nguồn không còn báo service này">không còn trong nguồn</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Watcher chưa lấy được danh sách service của nguồn này.</p>
      )}
    </section>
  );
}

/** Choose which services each source watches, and the name each is shown under. */
export function ServicesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const load = useCallback((signal: AbortSignal) => api.services(signal), []);
  const list = usePoll<ServiceList>(load, "services", open ? 15_000 : 0, !open);
  const [data, setData] = useState<ServiceList | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  useEffect(() => setData(list.data), [list.data]);

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (open && !dlg.open) {
      setFailure(null);
      list.reload();
      dlg.showModal();
    } else if (!open && dlg.open) {
      dlg.close();
    }
  }, [open]);

  const change = async (source: string, changes: ServiceChange[]) => {
    if (!changes.length) return;
    setBusy(true);
    setFailure(null);
    try {
      setData(await api.chooseServices(source, changes));
    } catch (e) {
      setFailure((e as Error).message);
      list.reload();
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog
      ref={ref}
      className="services"
      aria-labelledby="services-title"
      onClose={onClose}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <header className="svc-top">
        <h2 id="services-title">Service được theo dõi</h2>
        <button type="button" className="btn ghost icon" aria-label="Đóng" onClick={onClose}>
          <Icon name="close" />
        </button>
      </header>
      <div className="svc-body">
        {!data ? (
          list.error ? (
            <p className="error-text">Không tải được danh sách service: {list.error.message}</p>
          ) : (
            <div className="panel-loading" aria-busy="true"><span className="sk sk-line short" /></div>
          )
        ) : !data.sources.length ? (
          <p className="muted">
            Chưa có nguồn nào cho chọn service. Watcher công bố danh sách service của các nguồn loki khi đang chạy.
          </p>
        ) : (
          data.sources.map((s) => (
            <SourceBlock key={s.name} source={s} busy={busy} onChange={(changes) => change(s.name, changes)} />
          ))
        )}
        {failure && <p className="error-text" role="alert">Không lưu được: {failure}</p>}
      </div>
      <footer className="svc-foot">
        {data && !data.online && <span>Watcher đang không chạy: thay đổi được áp dụng khi nó chạy lại.</span>}
        <span>Bỏ chọn hết thì quay về cấu hình. Watcher áp dụng sau khoảng 15 giây.</span>
        <span>Tên mới áp dụng cho lỗi từ nay; lỗi của service đã đổi tên mở ticket mới.</span>
      </footer>
    </dialog>
  );
}
