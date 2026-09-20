import { useEffect, useState } from "react";
import { clearShortTermMemory, getShortTermMemory, type ShortMemory } from "../../api/client";

function ShortTermMemory() {
  const [memory, setMemory] = useState<ShortMemory | null>(null);
  const [loading, setLoading] = useState(true);

  function refresh() {
    setLoading(true);
    getShortTermMemory()
      .then(setMemory)
      .catch(() => setMemory(null))
      .finally(() => setLoading(false));
  }

  useEffect(refresh, []);

  async function handleClear() {
    await clearShortTermMemory();
    refresh();
  }

  if (loading) return <p className="panel__empty">Loading…</p>;
  if (!memory) return <p className="panel__empty">No short-term memory turn pending.</p>;

  return (
    <>
      <div className="panel__field">
        <span className="panel__label">Prior question</span>
        <span className="panel__value">{memory.prior_question}</span>
      </div>
      <div className="panel__field">
        <span className="panel__label">Prior answer</span>
        <span className="panel__value">{memory.prior_answer}</span>
      </div>
      <div className="panel__field">
        <span className="panel__label">Follow-up</span>
        <span className="panel__value">{memory.follow_up}</span>
      </div>
      <div className="panel__field">
        <span className="panel__label">Follow-up answer</span>
        <span className="panel__value">{memory.follow_up_answer ?? "(none yet)"}</span>
      </div>
      <div className="panel__field">
        <span className="panel__label">Status</span>
        <span className="panel__value">{memory.status}</span>
      </div>
      {memory.status === "active" && (
        <button type="button" className="panel__button" onClick={handleClear}>
          Clear
        </button>
      )}
    </>
  );
}

export default ShortTermMemory;
