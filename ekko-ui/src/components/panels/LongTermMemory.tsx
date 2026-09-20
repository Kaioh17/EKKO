import { useEffect, useState } from "react";
import { getLongTermMemory, type LongTermMemory as LongTermMemoryData } from "../../api/client";

function LongTermMemory() {
  const [memory, setMemory] = useState<LongTermMemoryData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getLongTermMemory()
      .then(setMemory)
      .catch(() => setMemory(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="panel__empty">Loading…</p>;
  if (!memory) return <p className="panel__empty">Could not load MEMORY.md.</p>;

  const sections = Object.entries(memory.sections).filter(([, lines]) => lines.length > 0);
  if (!sections.length) return <p className="panel__empty">MEMORY.md is empty.</p>;

  function jumpTo(section: string) {
    document.getElementById(sectionId(section))?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <>
      <div className="panel__jump">
        {sections.map(([section, lines]) => (
          <button key={section} type="button" className="panel__jump-item" onClick={() => jumpTo(section)}>
            {section} ({lines.length})
          </button>
        ))}
      </div>
      {sections.map(([section, lines]) => (
        <div className="panel__field" key={section} id={sectionId(section)}>
          <span className="panel__section-heading">
            {section} ({lines.length})
          </span>
          <ul className="panel__list">
            {lines.map((line, i) => (
              <li className="panel__list-item" key={i}>
                <span>{line.text}</span>
                {line.date && <span className="panel__mono">{line.date}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  );
}

function sectionId(section: string) {
  return `mem-section-${section.toLowerCase().replace(/\s+/g, "-")}`;
}

export default LongTermMemory;
