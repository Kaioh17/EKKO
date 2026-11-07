// Turns a key press into a hotkey chord written the way the `keyboard` package
// (and listener/hotkey.py, which validates it) spells it: "ctrl+alt+space".

export interface ChordEvent {
  code: string;
  key: string;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  metaKey: boolean;
}

const MODIFIER_CODE = /^(Control|Alt|Shift|Meta|OS)(Left|Right)?$/;

const NAMED: Record<string, string> = {
  Space: "space", Enter: "enter", NumpadEnter: "enter", Tab: "tab", Backspace: "backspace", Delete: "delete",
  Insert: "insert", Home: "home", End: "end", PageUp: "page up", PageDown: "page down",
  ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
  Slash: "/", Comma: ",", Period: ".", Semicolon: ";", Quote: "'", BracketLeft: "[", BracketRight: "]",
  Backslash: "\\", Minus: "-", Equal: "=", Backquote: "`",
};

function mainKey(code: string): string | null {
  const letter = /^Key([A-Z])$/.exec(code);
  if (letter) return letter[1].toLowerCase();
  const digit = /^Digit(\d)$/.exec(code);
  if (digit) return digit[1];
  if (/^F([1-9]|1\d|2[0-4])$/.test(code)) return code.toLowerCase();
  return NAMED[code] ?? null;
}

/** `held`: the modifiers down now (for a live "Ctrl + Alt + …" preview).
 *  `chord`: set once a main key is pressed with them, else null. */
export function chordFromEvent(e: ChordEvent): { chord: string | null; held: string[] } {
  const held = [e.ctrlKey && "ctrl", e.altKey && "alt", e.shiftKey && "shift", e.metaKey && "windows"].filter(Boolean) as string[];
  if (MODIFIER_CODE.test(e.code)) return { chord: null, held };
  const key = mainKey(e.code);
  return { chord: key ? [...held, key].join("+") : null, held };
}

const RESERVED: Record<string, string> = { "ctrl+alt+w": "Manual wake", "ctrl+alt+q": "Hard stop" };

/** Why a chord can't be saved, or null. The same rules listener/hotkey.py enforces,
 *  so the page can say so before the save is refused. */
export function chordProblem(chord: string): string | null {
  const parts = chord.split("+");
  const key = parts[parts.length - 1];
  const mods = parts.slice(0, -1);
  const functionKey = /^f\d+$/.test(key);
  if (!functionKey && !mods.some((m) => m === "ctrl" || m === "alt" || m === "windows")) {
    return "Add Ctrl, Alt or Windows, otherwise it would trigger while you type.";
  }
  const canonical = [...["ctrl", "alt", "shift", "windows"].filter((m) => mods.includes(m)), key].join("+");
  return RESERVED[canonical] ? `${formatChord(canonical).join("+")} is already used for ${RESERVED[canonical]}.` : null;
}

const LABEL: Record<string, string> = { windows: "Win" };

/** The pieces of a chord for display, one per key cap. */
export function formatChord(chord: string): string[] {
  return chord.split("+").map((p) => LABEL[p] ?? p.replace(/\b\w/g, (c) => c.toUpperCase()));
}
