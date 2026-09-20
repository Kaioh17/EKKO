import "./CentralStage.css";
import type { ReactNode } from "react";

interface CentralStageProps {
  children?: ReactNode;
}

/**
 * Reserved region for whatever Ekko surfaces next (waveform, transcript,
 * chat). Pass children to render live content later without touching
 * the surrounding layout; with none, it shows an idle empty state.
 */
function CentralStage({ children }: CentralStageProps) {
  return (
    <section className={`central-stage${children ? "" : " central-stage--empty"}`}>
      {children ?? (
        <div className="central-stage__empty">
          <svg
            className="central-stage__icon"
            width="40"
            height="40"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <circle cx="12" cy="12" r="9" />
            <circle cx="12" cy="12" r="4" />
          </svg>
          <p>Nothing to show yet</p>
        </div>
      )}
    </section>
  );
}

export default CentralStage;
