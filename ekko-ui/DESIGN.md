# ekko-ui design conventions

Conventions established while building out the panels. Follow these for new panels/sections instead of inventing new patterns.

## Stack

- React 19 + TypeScript, Vite, Tauri desktop shell.
- No router — `App.tsx` switches panels via `useState<Section>` and a `renderPanel` switch.
- No Tailwind, no CSS-in-JS — plain CSS files per component, shared primitives in `src/components/panels/Panels.css`.
- Design tokens live in `src/styles/theme.css` as CSS custom properties (`--space-*`, `--radius-*`, `--color-*`, `--shadow-*`, `--font-*`). Dark mode is automatic via `@media (prefers-color-scheme: dark)` redefining the same tokens — never hardcode a light/dark pair manually.

## Mock data convention

Panels that aren't wired to a real backend yet (most of them) use a **typed local `const` at module scope**, not a fetch or a separate mock-data module:

```ts
const LLM_USAGE = [
  { label: "Total calls", value: "142" },
  ...
] as const;
```

Only add a comment where the mock is non-obvious (e.g. why a number is hardcoded to look "live"). Don't build a mock API layer for a single panel.

## Layout: grids, not stacked rows

Any group of same-shaped items (stat metrics, status entries, config fields, toggles) is a **responsive CSS grid**, never a hand-stacked column of full-width rows — stacked rows waste horizontal space on anything wider than a phone.

- Always `repeat(auto-fit, minmax(<floor>, 1fr))` — never a fixed column count — so it reflows on resize/sidebar collapse.
- Reuse the existing grid classes before adding a new one:
  - `.panel__stat-grid` / `.panel__stat-tile` — stat metrics (`minmax(180px, 1fr)`)
  - `.panel__card-grid` / `.panel__status-card` — status/health entries, label on top + value below (`minmax(280px, 1fr)`)
  - `.panel__config-grid` — labeled form fields (`minmax(240px, 1fr)`)
  - `.panel__toggle-grid` — toggle switches, 2–3 per row (`minmax(220px, 1fr)`)
- All grid items share the same card look: `border: 1px solid var(--color-border)`, `border-radius: var(--radius-sm)`, `background: var(--color-bg-elevated)`, padding from the `--space-*` scale. Don't introduce a new visual style per section.
- A panel that needs grids wider than the default 520px cap gets `className="panel panel--wide"` (900px) on its root — don't widen `.panel` globally, other panels are fine narrow.

## Form inputs

- Every input/select gets `className="panel__input"` and `width: 100%` so it fills its grid cell — never let an input shrink-wrap and leave dead space beside it.
- Label sits directly above its input, both wrapped in `.panel__field` (flex column).

## Status indicators

- Use the existing `status-dot` / `status-dot--{idle,listening,processing,speaking,error}` classes (defined in `TopBar.css`) for any listening/processing/speaking/error style state — don't invent new status colors. `listening` (green) and `error` (red) also double as a generic enabled/disabled indicator where useful.

## Toggles

- There is no checkbox in the UI — anywhere a boolean is user-facing, use the `ToggleSwitch` pattern (checkbox-driven CSS pill, `.panel__toggle` / `.panel__toggle-input` / `.panel__toggle-track`, `--color-accent` when checked) instead of a raw `<input type="checkbox">`. Keep it a small local component in the panel file that uses it rather than promoting it to a shared component until a second panel needs it.
- A block of related boolean kill-switches gets its own bordered `.panel__subsection` with a heading, visually separated from regular tunable fields.

## What to avoid

- No new npm dependencies for things a few lines of CSS/React cover (charts, toggles, grids).
- No global state library — local `useState` per panel is enough; nothing here is shared across panels yet.
- No persistence/backend wiring for mock panels unless explicitly asked — mock config fields are local-only and should say so in a comment if it's not obvious from context.
