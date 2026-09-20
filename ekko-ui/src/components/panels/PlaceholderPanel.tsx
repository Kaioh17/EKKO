import "./Panels.css";

function PlaceholderPanel({ section }: { section: string }) {
  return (
    <div className="panel">
      <h2>{section}</h2>
      <p className="panel__empty">No configurable settings yet.</p>
    </div>
  );
}

export default PlaceholderPanel;
