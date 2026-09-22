# Routing

Stage 3: turns a transcribed command into a validated intent, and runs
the handler script for it. Sits between `listener/`'s Whisper output and
the PowerShell handlers in `scripts/`.

```
transcript --> intent match --> slot extraction --> IntentBundle --> handler
```

Matching is sentence-embedding similarity (`all-MiniLM-L6-v2`), not regex
and not an LLM. Regex was the original plan and Whisper's phrasing varies
too much for it. An LLM was considered and rejected for the routing
decision itself: it's non-deterministic, it costs hundreds of ms on an
already multi-stage pipeline, and its failure mode is confidently naming
the wrong action, which is the worst possible failure for something with
shell access. The closed intent set in `intents.yaml` is what
structurally guarantees only pre-approved actions can run; a prompted
model would guarantee that by instruction only. See `intent_routing.md`
for the full reasoning.

**One exception to "matching is embedding similarity": a literal trigger
phrase wins outright.** `route.py`'s `_trigger_match` checks the
transcript for any `free_text` or `transcript` slot's own trigger phrase
(e.g. `web_search`'s "search for") before the embedding matcher runs at all,
and routes there directly if one is found, skipping the threshold
entirely. This isn't a second matching strategy competing with the
first, a trigger phrase is a hand-authored exact string the config author
chose specifically because saying it means the intent, the same
certainty a `closed_vocabulary` alias already has. It exists because
embedding similarity measurably failed at this for `web_search`:
whatever the query is about ("search for how to walk in PS1") can pull
the whole-sentence embedding far from every example even with the
trigger word right there in the transcript. See the comment on
`web_search` in `intents.yaml`.

`file_operation` needs the same escape hatch for the same reason and gets
more out of it: every real file command carries an arbitrary name
("fall2027", "dune"), and one out-of-vocabulary token is enough to pull a
clear command under threshold -- "make a new directory called fall2027"
scored 0.563 with all five nearest examples being `file_operation`'s own.
Unlike a `free_text` trigger, a `transcript` slot's trigger is not
stripped, since the slot is the whole utterance either way -- so it costs
nothing to declare one.

## Usage

Run from the repo root (or anywhere, paths resolve relative to the files
themselves regardless of cwd):

```powershell
python routing\config.py                          # validate intents.yaml
python routing\route.py "open task manager"       # route one transcript
python routing\route.py --explain "fire up steam" # ...and show why it scored that way
python routing\route.py --batch listener\captures\transcripts.jsonl
python routing\calibrate.py                       # measure the threshold
python routing\execute.py --dry-run "launch vs code"
python routing\execute.py "launch vs code"        # actually opens it
```

The first run downloads the MiniLM model (~90MB, cached locally after
that), same as the speechbrain fetch in `voice_auth/enroll.py` and the
openWakeWord download in `listener/vad_listener.py`.

## Adding a command

1. Add the intent to `intents.yaml` with several example phrasings. Vary
   the wording, including how you'd actually say it when you're not
   thinking about it. Near-duplicates don't help; different phrasings do.
2. Write the handler as a `.ps1` in `scripts/`, following the exit-code
   convention in `scripts/README.md`.
3. Add test phrases to `calibration_phrases.yaml` (different wordings
   from the examples, or the numbers are circular) and re-run
   `calibrate.py`. New intents move the score distribution, so the
   threshold is worth re-checking every time.

The embedding cache rebuilds itself when `intents.yaml` changes, it
stores the file's SHA-256 and compares on load. No manual step.

## The threshold

`matcher.DEFAULT_THRESHOLD`, currently **0.58**, measured rather than
guessed. From `calibrate.py` against `calibration_phrases.yaml`:

| | score |
|---|---|
| lowest genuine command | 0.668 (`"open up the process list"`) |
| highest impostor | 0.502 (EKKO's own greeting, re-heard by its own mic) |

0.58 sits in the middle of that gap. This is the same discipline the
speaker verification threshold ended up needing (see `readme.md`, Stage
1): a borrowed generic number was badly wrong there, and only measuring
real score gaps produced a usable one.

It's calibrated against 38 hand-written phrases, not months of real
usage. Grow `calibration_phrases.yaml` from whatever shows up near the
boundary in `logs/routing.jsonl` and re-run.

### Known limitation: negation

`"close task manager"` scores **0.753** against the example
`"open task manager"`. Sentence embeddings barely encode negation, so no
threshold separates a command from its own opposite, and this one sits
above the genuine floor.

It's tolerated because every intent in the current set is an open/launch
action, so the worst case is Task Manager opening when you asked for it
to close. **Adding a destructive or state-reversing intent invalidates
that reasoning** and needs an explicit check, not a threshold nudge.

### Known limitation: one intent per utterance

"Open task manager and ghelper" scores against both and returns whichever
wins, not both. Multi-intent splitting is deliberately out of scope: it
doubles the failure surface, and phrases like "turn it up and down" are
genuinely ambiguous. Where a *slot* is ambiguous ("open chrome and
discord") the result is `missing_slot` rather than a coin flip.

## The four outcomes

`IntentBundle.status`, and what the response layer says for each
(`feedback.speech.RESPONSES`, mapped in `execute.RESPONSE_KEYS`):

| status | meaning | response key |
|---|---|---|
| `matched` | intent found, all slots filled | `command_confirmed` / `already_done` / `error`, by handler exit code |
| `no_match` | nothing scored above the threshold | `not_understood` |
| `missing_slot` | intent matched, but a slot couldn't be filled or was ambiguous | `missing_slot` |
| `empty_transcript` | Whisper returned nothing (a noise-only segment) | *(silence)* |

`missing_slot` is kept separate from `no_match` on purpose: "open" alone
is a different failure from "what's the weather", and it's the one case
where EKKO knows enough to ask a useful question back.

## Privacy

**`logs/routing.jsonl` records the text of everything routed, matched or
not.** Every transcript that reaches this layer is appended there with
its score and outcome.

That's deliberate, it's what makes threshold calibration possible and
what would make the Tier 2 classifier upgrade free later (see
`intent_routing.md`), and it never leaves the machine. But it is a
plaintext record of things said out loud in this room, so it's worth
knowing about rather than discovering. `--no-log` turns it off, and
`--batch` never logs.

`listener/captures/transcripts.jsonl` already keeps a similar record one
stage earlier.

## Files

- `intents.yaml` — the closed intent set. Editing this is the only way to
  widen what EKKO can do.
- `config.py` — loads and validates it; reports every problem at once,
  since it's hand-edited
- `bundle.py` — `IntentBundle`, the typed contract the execution layer
  accepts instead of a raw string
- `matcher.py` — embed / cosine / threshold, plus the cached embedding
  matrix (`.index_cache.pt`)
- `slots.py` — slot extraction. Three slot types: `closed_vocabulary`
  (exact match over a fixed value+alias list, e.g. `open_app`'s `app`),
  `free_text` (whatever follows a trigger phrase, e.g. `web_search`'s
  `query`), and `transcript` (the whole utterance, raw and un-normalised,
  e.g. `file_operation`'s `instruction`). The latter two are deliberate,
  narrow exceptions to the closed-vocabulary rule: a search query can't be
  a fixed list, and a file request needs its verb and its filename intact
  (`normalise()` would turn `notes.txt` into `notes txt`). Both are safe
  for one narrow reason only — the handlers that receive them never put
  them on a shell line. See the comments on `web_search` and
  `file_operation` in `intents.yaml`
- `route.py` — `Router`, the orchestrator; string in, bundle out
- `calibrate.py` — measures where the threshold belongs, mirrors
  `voice_auth/diagnose.py`
- `calibration_phrases.yaml` — labelled should-match / should-not-match
  phrases
- `execute.py` — runs a matched bundle's handler. The only place in EKKO
  that starts a process.
- `logs/routing.jsonl` — append-only decision log, see Privacy above

## Wired into the listener

`listener/vad_listener.py` calls `Router.route()` on every transcribed
command and runs what it matches, then speaks the outcome. Connecting it
was a deliberate separate decision, because it's the point where the
permission boundary in `readme.md` stops being theoretical.

What bounds it, in order: a segment only reaches the router if the wake
word fired **and** the speaker embedding matched; the router can only
return an intent from `intents.yaml`; that can only name a `.ps1` under
`scripts/`; and `execute.py` re-checks that last part at run time. There
is no path from speech to an arbitrary command. `--no-execute` on the
listener keeps routing, logging and spoken outcomes but runs nothing,
which is the flag for watching what it would do first.

Everything here is still testable standalone with a string, no mic
required — that's what the CLIs above are for.
