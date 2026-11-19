import { LANE_GROUPS, type LaneGroup } from "../lanes";
import { COLUMN_ORDER, STATUS_LABELS, TERMINAL } from "../tickets";
import type { TicketColumn, TicketStatus } from "../types";

/** In-progress tickets one team can work on at once; the meter turns red past it. */
export const WIP_LIMIT = 5;

interface Props {
  columns: TicketColumn[];
  group: LaneGroup;
  loadingMore: Partial<Record<TicketStatus, boolean>>;
  onLoadMore: (status: TicketStatus) => void;
}

/** The sticky header row: one cell per status with its total, the WIP meter and "Tải thêm". */
export function ColumnHeads({ columns, group, loadingMore, onLoadMore }: Props) {
  const groupLabel = LANE_GROUPS.find((g) => g.value === group)?.label ?? "";
  return (
    <div className="colhead">
      <div>{groupLabel} · theo mức nặng nhất</div>
      {COLUMN_ORDER.map((status) => {
        const column = columns.find((c) => c.status === status)!;
        const left = Math.max(0, column.total - column.items.length);
        return (
          <div key={status} className={`st-${status}${TERMINAL.has(status) ? " terminal" : ""}`}>
            <div className="name">
              {STATUS_LABELS[status]} <span className="count num">{column.total}</span>
            </div>
            {status === "inprogress" ? (
              <div className={`wip${column.total > WIP_LIMIT ? " over" : ""}`} title="Số ticket đang xử lý so với giới hạn WIP">
                <span className="num">
                  {column.total} / giới hạn {WIP_LIMIT}
                </span>
                <span className="wip-meter" aria-hidden="true">
                  <span style={{ width: `${Math.min(100, Math.round((column.total / WIP_LIMIT) * 100))}%` }} />
                </span>
              </div>
            ) : (
              TERMINAL.has(status) && <div className="hint">Trạng thái cuối</div>
            )}
            {column.next_before && (
              <button type="button" className="link-btn more" onClick={() => onLoadMore(status)} disabled={loadingMore[status]}>
                {loadingMore[status] ? "Đang tải…" : `Tải thêm · còn ${left}`}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
