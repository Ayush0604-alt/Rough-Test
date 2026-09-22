# Change log — Add semantic meaning to the graph from docstrings and comments

Implements the proposal in [graphify.md §15](../capabilities/graphify.md#15-proposal-add-semantic-meaning-to-the-graph-from-docstrings-and-comments).

**One line:** Graphify already found every docstring/comment in the codebase, but only kept an
~80-character truncated label on a disconnected `RATIONALE` node no workflow walk ever reached.
This change reads the *full* text back from source and attaches it to the entity it describes
(`docstring` attribute), so it reaches evidence packets, the correspondence checker, and a new
`module_intent` summary. No LLM call, deterministic, every word traces to a source line.

**Status:** Implemented and tested on branch `1/Add-semantics-to-graph`. Not yet committed —
all changes below are in the working tree. Base commit: `d7a59fe` (local `main` is at the same
commit, so there is currently no divergence to reconcile — this doc is for when that changes, or
as a reviewer's map of the diff).

---

## Files changed

| File | Kind of change | Risk |
|---|---|---|
| `src/agentic/capabilities/graphify.py` | Additive — new functions + ~15 new lines wired into an existing function | Low |
| `src/agentic/config.py` | Additive — 2 new settings, both with safe defaults | Low |
| `src/agentic/capabilities/graph_ops.py` | 1-line change — added a key to an existing tuple | Low |
| `src/agentic/llm/prompt.py` | Additive — 1 new import, 1 new constant, ~15 new lines in an existing function | Low |
| `codebase-requirement-skills/evidence-grounded-requirement-synthesis/SKILL.md` | 1-line addition to prose (a skill doc read by the LLM, not code) | None |
| `tests/test_graphify_adapter.py` | Additive — 15 new test functions + 4 new imports | None (test-only) |

**Nothing was deleted, renamed, or had its signature changed.** Every change is either a new
function, a new optional dict key, or a new config field with a default that preserves today's
behaviour when left untouched (see "Backward compatibility" below). This is what makes the change
low-risk to merge — a `git diff` on `main` should apply cleanly unless `main` has *also* touched
one of the exact same regions listed below.

---

## 1. `src/agentic/capabilities/graphify.py` (+210 lines)

### New module-level constants (all new names, no collisions with anything existing)
- `_DOCSTRING_ATTACHED_KINDS = {"FUNCTION", "METHOD", "CLASS", "FILE"}`
- `_LICENSE_HEADER`, `_CODE_LIKE_ENDING`, `_PY_TRIPLE_QUOTE`, `_BLOCK_COMMENT_OPEN`,
  `_LINE_COMMENT_PREFIX` — regexes for the new reader.

### New functions (all new, none override or shadow an existing name)
- `_docstring_limit(kind, base) -> int` — CLASS/FILE get 2× the FUNCTION/METHOD cap.
- `_index_rationale_sources(nodes_by_id, edges) -> dict[str, list[dict]]` — groups Graphify's
  `rationale_for` edges by target, in source-line order.
- `_cached_lines(source_root, relative, cache) -> list[str]` — reads a file's lines once and
  caches them, keyed by relative path.
- `_read_rationale(lines, start_line) -> str` — reads the **full** docstring/comment block
  forward from its first line, using the delimiter on that line: Python `"""`/`'''`, JSDoc-style
  `/** */` (covers Java/JS/TS/Go/C#/C/C++/Kotlin/PHP), or a run of `#`/`//`/`--` line comments.
  Returns `""` when the shape isn't recognised (falls back to Graphify's own label — see below).
- `_clean_rationale_text(text) -> str` — strips comment markers, redacts secrets (reuses the
  existing `_SECRET` regex), drops the whole block if it's a licence/boilerplate header or looks
  like commented-out code, collapses blank-line runs into paragraph breaks.
- `_attach_docstring(source_root, rationales, line_cache) -> tuple[str, list[int]] | None` —
  combines and locates the cleaned text for one entity; returns `None` if nothing usable survived.

### Change inside the existing `adapt_graphify_graph()` capability
Two small insertions, both **additive**, in the function's existing body:

1. Right after `language_of = {...}` (around the top of the function): builds `doc_sources` (via
   `_index_rationale_sources`) and a `docstring_line_cache`, gated by the new `graph_docstrings`
   setting. When the setting is off, `doc_sources = {}` and nothing downstream changes at all.
2. Inside the existing per-node loop, right after the existing `content_outline` block: if the
   node's (already-computed) `kind` is one of `_DOCSTRING_ATTACHED_KINDS` and it has rationale
   sources, sets two **new** keys on the entity's `attributes` dict:
   - `attributes["docstring"]` — the full cleaned text, capped by `docstring_max_chars`
     (doubled for CLASS/FILE).
   - `attributes["docstring_at"]` — `[start_line, end_line]` the text was read from, for audit.

**Nothing existing in this loop was removed or reordered.** `kind`, `qualified_name`, `location`,
and every other attribute are computed exactly as before; this only adds two new dict keys when
applicable.

**Important note for the model output — `Entity.attributes` is a free-form dict on the schema
already** (`agentic.schemas.core.Entity`), so no schema/Pydantic model change was needed. Any code
that iterates `entity.attributes` unconditionally (rather than looking up a specific key) will now
see two more keys on some entities. Grep confirms nothing in the repo does a strict/closed
iteration over `attributes` that this would break — it's read via `.get("docstring")` everywhere.

---

## 2. `src/agentic/config.py` (+7 lines)

Added to `Settings`, directly after `graphify_timeout_seconds`:

```python
graph_docstrings: bool = True
docstring_max_chars: int = 600
```

- `graph_docstrings` is the kill switch for the whole feature (default **on**).
- `docstring_max_chars` is the per-FUNCTION/METHOD cap (default 600; CLASS/FILE get 1200).

**Both are new fields with defaults** — nothing reads them today except the new code in
`graphify.py`, and `pydantic_settings` allows unset env vars to fall back to the default, so no
`.env` file needs to change. If `main` has since added its *own* new `Settings` fields in the same
area, this is a textbook additive merge (git will show a 3-way "both added lines near each other"
conflict at worst, not a logical conflict).

---

## 3. `src/agentic/capabilities/graph_ops.py` (+2 lines, −1 line)

One line changed inside `compact_entity()`'s `keep` table:

```diff
- "FILE": ("extension", "content_outline"),
+ "FILE": ("extension", "content_outline", "docstring"),
```

This is the line that decides which attributes of a FILE entity are allowed to reach an evidence
packet. Before this change, a FILE's `docstring` attribute (even if some other code had set it)
would have been silently dropped by `compact_entity`. **If `main` has changed this same `keep`
dict** (e.g. added another key to `"FILE"`, or reordered the table), resolve by keeping both
additions — order inside the tuple doesn't matter, only membership does.

The `"docstring"` key was already read for other kinds (`FUNCTION`, `METHOD`, `CLASS`, `MODULE`)
before this change — this only adds `FILE` to that list. The `prose = {"docstring", "description"}`
truncation-to-1200-chars rule a few lines below already applied to it automatically, no change
needed there.

---

## 4. `src/agentic/llm/prompt.py` (+20 lines)

- **New import**: `from agentic.capabilities.graph_ops import GraphIndex` — check `main` hasn't
  introduced a different import ordering/linting rule here (this repo doesn't appear to enforce
  strict isort, but worth a glance).
- **New constant**: `MODULE_INTENT_FILES = 20`, next to the existing `DOC_FILES = 4`.
- **New logic inside `_render_graph()`** (the function that builds the `"graph"` artifact summary
  handed to every LLM step): builds a `module_intent` list — FILE entities with a `docstring`,
  ranked by graph degree (most-connected files first), capped at 20 — and adds it as a **new key**
  `"module_intent"` in the dict this function returns, alongside the existing
  `"declared_documentation"` key.

**This changes the shape of the `"graph"` prompt artifact** by adding one new top-level key. If
any downstream code (or a skill's prompt text) asserts an *exact* set of keys on this dict, that
would need updating — a repo-wide check found none; every consumer reads specific keys.

---

## 5. `codebase-requirement-skills/evidence-grounded-requirement-synthesis/SKILL.md` (+2/−1 lines)

Appended one sentence to the existing "Never invent…" paragraph in the **Evidence precedence**
section:

> If a docstring and the code it sits beside disagree, trust the code and say so — the docstring
> may simply be stale.

This is prose read by the LLM at run time (via `build_system_prompt`), not code — zero risk of a
code-level merge conflict, but if `main` has independently edited this same paragraph, reconcile
by keeping both edits' intent.

---

## 6. `tests/test_graphify_adapter.py` (+241 lines)

- **New imports** added to the existing `from agentic.capabilities.graphify import (...)` block:
  `_attach_docstring`, `_clean_rationale_text`, `_index_rationale_sources`, `_read_rationale`.
  If `main` has also added imports to this same block, git will conflict on the import list —
  resolve by keeping the union of both sets, alphabetised as the rest of the block is.
- **15 new test functions** appended at the end of the file (after the last existing test,
  `test_every_entity_location_resolves_and_every_symbol_verifies`), covering:
  - Each delimiter shape: Python triple-quote, JSDoc `/** */`, `//` block, `#` block, single-line
    docstring.
  - The fallback path when a rationale's first line isn't a recognised shape.
  - Secret redaction inside a docstring.
  - A licence header block being dropped outright.
  - Multiple rationale blocks on one target combined in line order.
  - End-to-end: the real `sample_app` fixture's module docstring and a function docstring both
    reach their entities in full (not Graphify's truncated ~80-char label).
  - The `docstring_max_chars` cap is honoured.
  - The `graph_docstrings` switch fully disables the feature.

No existing test was modified or removed.

---

## Backward compatibility — what stays exactly the same

- **With `graph_docstrings` at its default (`True`)**: every existing entity, relationship,
  `kind_counts`, `extraction_coverage`, and `metadata["extraction"]` value is unchanged — this only
  *adds* two new attribute keys (`docstring`, `docstring_at`) to some FUNCTION/METHOD/CLASS/FILE
  entities that didn't have them before.
- **The RATIONALE nodes and `EXPLAINS`/`rationale_for` edges are untouched** — they still exist,
  still carry Graphify's own truncated label, exactly as before. This feature only *copies* the
  fuller text elsewhere; it changes nothing about how RATIONALE nodes themselves are built.
- **`_assert_structurally_sound`, `AdapterError`, entity/edge counts, dropped-edge counting** — all
  unchanged; the new code runs entirely inside the existing per-node loop and never adds or removes
  entities/edges itself.
- **Setting `graph_docstrings=False`** reproduces the exact pre-change graph (verified by
  `test_graph_docstrings_can_be_switched_off`).

## What actually changes in behaviour (with defaults on)

- Evidence packets for FUNCTION/METHOD/CLASS/FILE entities that have a real docstring/comment in
  source will now include that full text (previously: nothing, since the attribute never existed).
- The `"graph"` prompt artifact gains a `module_intent` list.
- Packet size grows — the proposal measured **≈+31%** characters on its reference repository. This
  is expected and is exactly what `docstring_max_chars` and `graph_docstrings` exist to tune down
  if a run's token budget is tight.
- The synthesis skill now has one extra instruction: trust code over a disagreeing docstring.

## Test results

- `tests/test_graphify_adapter.py`: all 66 tests pass (including the 15 new ones).
- Full suite (`pytest -q`): **664 passed, 1 failed.**
  - The 1 failure — `tests/test_entry_point_adapters.py::test_registered_handlers_become_entry_points`
    (expects `entry_points_added == 3`, gets `4`) — **is pre-existing and unrelated to this change.**
    Verified by `git stash`-ing every change in this branch and re-running that single test: it
    fails identically on the untouched `main` commit (`d7a59fe`). Do not attribute this failure to
    the docstring work when triaging CI.

## Merge checklist

1. If `main` has touched `graphify.py` in the `classify_kind`/`_verifiable_symbol`/
   `adapt_graphify_graph` neighbourhood, or added new node-loop logic, re-check the insertion
   points listed in section 1 above still make sense contextually (they're additive, but line
   numbers will have shifted).
2. If `main` has added its own `Settings` fields, just keep both — no field name collision here.
3. If `main` has changed `compact_entity`'s `FILE` tuple or the `prose` set in `graph_ops.py`,
   keep `"docstring"` in the `FILE` tuple's union.
4. If `main` has changed `_render_graph()`'s return dict in `prompt.py`, keep the `module_intent`
   key and the `GraphIndex` import.
5. Re-run `tests/test_graphify_adapter.py` after resolving conflicts — it's the fast, targeted
   signal for this feature specifically.
6. Re-run the full suite once and confirm the *only* failure (if any) is the pre-existing
   `test_registered_handlers_become_entry_points` one noted above; anything else is a real
   regression from the merge.
