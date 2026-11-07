import { describe, expect, it } from "vitest";
import { chordFromEvent, chordProblem, formatChord, type ChordEvent } from "./chord";

const ev = (code: string, mods: Partial<ChordEvent> = {}): ChordEvent => ({
  code,
  key: "",
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  metaKey: false,
  ...mods,
});

describe("chordFromEvent", () => {
  it("spells chords the way the keyboard package does, modifiers in a fixed order", () => {
    expect(chordFromEvent(ev("Space", { ctrlKey: true, altKey: true })).chord).toBe("ctrl+alt+space");
    expect(chordFromEvent(ev("KeyT", { altKey: true, ctrlKey: true })).chord).toBe("ctrl+alt+t");
    expect(chordFromEvent(ev("KeyX", { metaKey: true, shiftKey: true, ctrlKey: true })).chord).toBe("ctrl+shift+windows+x");
  });

  it("names the main keys", () => {
    const c = (code: string) => chordFromEvent(ev(code, { ctrlKey: true })).chord;
    expect(c("Digit1")).toBe("ctrl+1");
    expect(c("F9")).toBe("ctrl+f9");
    expect(c("ArrowUp")).toBe("ctrl+up");
    expect(c("PageUp")).toBe("ctrl+page up");
    expect(c("Slash")).toBe("ctrl+/");
    expect(c("NumpadEnter")).toBe("ctrl+enter");
  });

  it("is not a chord yet while only modifiers are down, but reports them for the live preview", () => {
    const r = chordFromEvent(ev("ControlLeft", { ctrlKey: true }));
    expect(r.chord).toBeNull();
    expect(r.held).toEqual(["ctrl"]);
    expect(chordFromEvent(ev("AltRight", { ctrlKey: true, altKey: true })).held).toEqual(["ctrl", "alt"]);
  });

  it("ignores keys it can't name", () => {
    expect(chordFromEvent(ev("MediaPlayPause", { ctrlKey: true })).chord).toBeNull();
    expect(chordFromEvent(ev("Escape", { ctrlKey: true })).chord).toBeNull();
  });
});

describe("chordProblem", () => {
  it("accepts a sensible chord and a lone function key", () => {
    expect(chordProblem("ctrl+alt+space")).toBeNull();
    expect(chordProblem("f9")).toBeNull();
  });

  it("explains why a bare or shifted letter won't do", () => {
    expect(chordProblem("w")).toMatch(/Ctrl, Alt/);
    expect(chordProblem("shift+a")).toMatch(/Ctrl, Alt/);
  });

  it("refuses the chords ekko already uses, whatever the spelling order", () => {
    expect(chordProblem("ctrl+alt+w")).toMatch(/Manual wake/);
    expect(chordProblem("alt+ctrl+q")).toMatch(/Hard stop/);
  });
});

describe("formatChord", () => {
  it("writes a chord for people", () => {
    expect(formatChord("ctrl+alt+space")).toEqual(["Ctrl", "Alt", "Space"]);
    expect(formatChord("ctrl+page up")).toEqual(["Ctrl", "Page Up"]);
    expect(formatChord("f9")).toEqual(["F9"]);
    expect(formatChord("ctrl+windows+x")).toEqual(["Ctrl", "Win", "X"]);
  });
});
