import "./Dashboard.css";

// The backend keeps no request log or approval queue yet, so this page is a
// grayed-out shell. Fill the table from an /api/activity feed when it exists.
const FILTERS = ["All", "Needs you", "Actions", "Answers"];

export default function ActivityPanel() {
  return (
    <div className="dash">
      <div className="dash__head"><h2>Activity</h2><p>Everything ekko heard and did, and anything that needs your approval.</p></div>
      <div className="dash__off-note">Not available yet.</div>
      <div className="is-off" style={{ display: "flex", flexDirection: "column", gap: 22 }}>
        <div className="dash__pills">
          {FILTERS.map((l, i) => <button key={l} type="button" disabled className={`dash__pill${i === 0 ? " dash__pill--on" : ""}`}>{l}</button>)}
        </div>
        <div className="dash__table">
          <div className="dash__tr dash__tr--head"><span>Time</span><span>Request</span><span>Handled by</span><span style={{ textAlign: "right" }}>Tokens</span><span>Status</span></div>
        </div>
      </div>
    </div>
  );
}
