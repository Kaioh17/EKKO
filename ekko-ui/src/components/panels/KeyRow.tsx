import { useState } from "react";
import { deleteKey, setKey, type KeyName, type KeyStatus } from "../../api/client";

export default function KeyRow({ name, status, onChange }: { name: KeyName; status: KeyStatus[KeyName] | undefined; onChange: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const run = (call: () => Promise<void>) =>
    call()
      .then(() => {
        setValue("");
        setError(null);
        onChange();
      })
      .catch((e: Error) => setError(e.message));

  return (
    <div className="panel__field models__key">
      <span className="panel__label">
        {name} · {status?.set ? (status.last4 ? `set, ends ${status.last4}` : "set") : "not set"}
      </span>
      <div className="models__key-row">
        <input
          className="panel__input panel__mono"
          type="password"
          autoComplete="off"
          placeholder={status?.set ? "Paste a new key to replace" : "Paste key"}
          value={value}
          onChange={(e) => setValue(e.target.value.trim())}
        />
        <button type="button" className="panel__button" disabled={!value} onClick={() => run(() => setKey(name, value))}>
          Save
        </button>
        <button type="button" className="panel__button" disabled={!status?.set} onClick={() => run(() => deleteKey(name))}>
          Remove
        </button>
      </div>
      {error && <span className="panel__warning">{error}</span>}
    </div>
  );
}
