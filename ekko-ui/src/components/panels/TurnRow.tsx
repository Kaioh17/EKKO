import type { ReactNode } from "react";
import type { DayTurn } from "../../api/client";
import ClassTag from "./ClassTag";

// One logged exchange: question, class tag, the answer (children), and delete.
function TurnRow({ turn, busy, onDelete, children }: { turn: DayTurn; busy?: boolean; onDelete: () => void; children: ReactNode }) {
  return (
    <li className={`panel__list-item cls-row cls-row--${turn.cls}`}>
      <div className="panel__stack">
        <strong>
          {turn.user} <ClassTag cls={turn.cls} />
        </strong>
        {children}
        <span className="panel__mono panel__label">
          {turn.ts.slice(0, 16).replace("T", " ")} · {turn.source} · {turn.kind}
        </span>
      </div>
      <button type="button" className="panel__button panel__button--danger" disabled={busy} onClick={onDelete}>
        Delete
      </button>
    </li>
  );
}

export default TurnRow;
