import type { TurnClass } from "../../api/client";

const LABEL: Record<TurnClass, string> = { keep: "Keep", ephemeral: "Ephemeral", drop: "Drop" };

// Colour = value in the window: cyan keep, yellow ephemeral, orange drop (see Panels.css).
function ClassTag({ cls }: { cls: TurnClass }) {
  return <span className={`cls-tag cls-tag--${cls}`}>{LABEL[cls]}</span>;
}

export default ClassTag;
