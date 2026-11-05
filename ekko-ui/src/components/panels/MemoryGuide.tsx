import ClassTag from "./ClassTag";

// Static help for the memory page. Examples are real shapes from the log, not live data.
const FLOW = [
  ["Now", "What the AI sees on its next reply: the newest chats, in full, up to a 4,000-token budget."],
  ["Today", "Everything from the last 24 hours, including chats that no longer fit in Now."],
  ["Archive", "One short summary per past day. The AI sees only the titles and opens one when you ask about that day."],
  ["Long Term", "Permanent facts about you (MEMORY.md). Nothing here expires."],
] as const;

const CLASSES = [
  {
    cls: "keep",
    means: "Instructions you may want again.",
    example: "A recipe, a how-to, anything with steps or amounts.",
    happens: "Offered in Review so you can save it before it ages out.",
  },
  {
    cls: "ephemeral",
    means: "Useful now, worthless tomorrow.",
    example: "\"How is the system doing?\", a stock price, small talk.",
    happens: "Stays in Now while it fits, then is summarized and archived.",
  },
  {
    cls: "drop",
    means: "Nothing worth recalling.",
    example: "\"Sorry, I didn't catch that.\" or a reply that was empty.",
    happens: "Removed when you press Trim noise.",
  },
] as const;

const ACTIONS = [
  ["Update summary now", "Folds chats that no longer fit in Now into the \"Today so far\" note. Does nothing until something has scrolled out."],
  ["Trim noise (n)", "Deletes the Drop chats to free tokens. Keep and Ephemeral are never touched."],
  ["Delete / Delete all", "Removes chats from the log for good. No undo."],
  ["Save to file / memory", "In Review: keep an instruction for good. A file holds the whole answer (saved to the folder you choose, ~/ekko-sandbox by default). Memory adds a short note to Long Term, so the AI remembers it exists."],
] as const;

function MemoryGuide() {
  return (
    <section className="guide" aria-label="How memory works">
      <h3 className="panel__section-heading">1. Where a chat goes</h3>
      <ol className="guide__flow">
        {FLOW.map(([tab, text]) => (
          <li key={tab}>
            <strong>{tab}</strong>
            <span>{text}</span>
          </li>
        ))}
      </ol>
      <p className="panel__label">
        A chat moves left to right as it ages: Now, then Today, then (after midnight) a line in Archive. Only Long Term
        and files you save yourself last forever.
      </p>

      <h3 className="panel__section-heading">2. The three colours</h3>
      <p className="panel__label">Every chat gets a tag so you can see what is worth keeping at a glance.</p>
      <ul className="guide__classes">
        {CLASSES.map((c) => (
          <li key={c.cls} className={`cls-row cls-row--${c.cls}`}>
            <ClassTag cls={c.cls} />
            <strong>{c.means}</strong>
            <span>For example: {c.example}</span>
            <span>
              <em>What happens:</em> {c.happens}
            </span>
          </li>
        ))}
      </ul>

      <h3 className="panel__section-heading">3. The buttons</h3>
      <dl className="guide__actions">
        {ACTIONS.map(([name, text]) => (
          <div key={name}>
            <dt>{name}</dt>
            <dd>{text}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export default MemoryGuide;
