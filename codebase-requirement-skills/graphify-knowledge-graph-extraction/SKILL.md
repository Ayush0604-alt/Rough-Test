---
name: graphify-knowledge-graph-extraction
description: Build a structured knowledge graph (entities, relationships, provenance, confidence, source locations) from a codebase in any language by running the Graphify extractor and normalising its output into the canonical schema, then classify which of its nodes are interaction boundaries. Use when building a code knowledge graph as input to requirements or documentation generation.
---

# Graphify Knowledge Graph Extraction

## Purpose

Turn a project folder into a structured graph you can reason over, instead of reasoning directly
over raw files. This graph is what later grounds every requirement to real code.

Extraction is delegated to [Graphify](https://github.com/Graphify-Labs/graphify), which parses 36
languages with tree-sitter locally and deterministically. This workflow does not implement parsing
for any language, and must not add one: the point of the delegation is that a Java, Go, Ruby, or
TypeScript repository is analysed exactly as well as a Python one.

## Ingestion (do this first, for any codebase)

- Never execute, import, or eval target code. Read and parse only.
- If given an archive: reject path traversal (`../`), absolute paths, symlinks, encrypted entries,
  and anything with an absurd compression ratio (zip-bomb) before extracting.
- Skip VCS directories (`.git`, `.hg`, `.svn`), editor directories (`.idea`, `.vscode`), dependency/
  build output (`node_modules`, `vendor`, `dist`, `build`, `target`, `.venv`, `venv`,
  `__pycache__`), and generated runtime state you can recognize (embedding/vector-db caches, log
  directories, etc.) — these are not source evidence.
- Classify every remaining file by extension/language. Anything the extractor produces no nodes for
  still gets a `FILE` entity — inventory it, don't skip it silently.

## The graph schema (language-agnostic)

```text
Entity:
  id            content-addressed, e.g. sha1(kind + qualified_name + file)[:20]
  kind          FILE | CLASS | FUNCTION | METHOD | SYMBOL | RATIONALE | EXTERNAL_SYMBOL |
                MODULE_SURFACE |
                ENDPOINT | CLI_ENTRY | CLI_COMMAND | SCRIPT_ENTRY | UI_ACTION |
                SCHEDULED_JOB | EVENT_CONSUMER | REGISTERED_ENTRY
  name          short name
  qualified_name  fully-qualified path, e.g. "app/routes/users.py::create_user"
  language
  location      {file, line_start, line_end, symbol}
  provenance    EXTRACTED | RESOLVED | INFERRED | AMBIGUOUS
  confidence    0.0–1.0
  attributes    kind-specific details

Relationship:
  id            content-addressed from (source, target, kind, evidence line)
  source, target  entity ids
  kind          CONTAINS | IMPORTS | CALLS | REGISTERS | INHERITS | REFERENCES |
                EXPLAINS | TRIGGERS | EXPOSES | RELATED_TO
  provenance, confidence, evidence[location], attributes
```

**De-duplication:** identity is content-addressed, so re-extracting the same construct converges on
the same id. When a duplicate entity is produced, keep the higher-confidence version. When a
duplicate relationship is produced, keep the first one.

**Confidence and provenance are not decoration** — they must reflect real extraction certainty:

| Provenance | When to use it |
|---|---|
| `EXTRACTED` | Syntactically certain from parsing (a function exists, a file imports another) |
| `RESOLVED` | A call/reference was matched to exactly one real definition |
| `AMBIGUOUS` | A call/reference matched multiple candidates, or resolved to an unclear external symbol |
| `INFERRED` | Derived without a syntactic guarantee — a boundary classification, a comment-derived rationale |

Graphify tags its own edges `EXTRACTED` or `INFERRED` and those tags are carried through unchanged.
It does not distinguish a resolved call from a syntactically certain one, so `RESOLVED` does not
appear on an edge from this extractor — a mapped `CALLS` edge is `EXTRACTED`.

## Extraction technique

One command, every language:

```
graphify extract <source_root> --code-only --no-cluster --no-gitignore --out <run>/graphify
```

- `--code-only` keeps extraction local and deterministic: tree-sitter only, no API calls, no
  second model vendor, and a reproducible result. Documentation, PDFs and images are inventoried
  but not concept-extracted.
- `--no-cluster` because this workflow partitions the graph itself, around interaction boundaries.
  Graphify's own community clustering would compete with that partition, and only one of them can
  feed the evidence packets.
- `--no-gitignore` because an uploaded snapshot often carries a `.gitignore` written for a
  different checkout layout, which would silently exclude the source under analysis.
- SQL requires the `graphifyy[sql]` extra. Without it `.sql` files contribute nothing at all.

**Normalisation is where the judgement is.** Graphify's payload has no `type` field on a node, so
the canonical kind is classified from what it does supply — whether the label is the file's
basename, whether it carries a callable marker, whether it has a source file at all. Do not assume
a language sets those markers consistently; several do not.

**Every entity needs a real source location (file + line).** An entity that cannot be located
should not be created — except an `EXTERNAL_SYMBOL`, which by definition names something defined
outside this repository and correctly has no location.

**Graphify reports a start line and no end line.** End lines are reconstructed, and are an
over-approximation: bounded by the next definition in the file, then extended to cover everything
the node contains. Treat a cited span as "the evidence is inside here", not as an exact extent.

## Interaction boundaries

An interaction boundary is where the system is consumed from outside — a route, a command, a
scheduled job, a control on a screen. Every requirement this workflow publishes must trace to one,
which makes boundary detection the highest-leverage step in the pipeline: a class of boundary
nobody detects is a class of capability nobody documents.

Graphify does not model boundaries. It emits structure, not intent — nothing in its output says
"this function is a route handler". So boundaries are identified from graph shape and named by
judgement, in two stages.

**Stage 1, deterministic — propose candidates.** A handler is invoked by a framework, a runtime, or
a person, never by the code beside it. On a call graph built from this repository alone, a route
handler, a CLI command, a scheduled job and a UI callback therefore share one property: nothing
here calls them. That is a shape, not a framework list, and it holds for frameworks nobody has
heard of. It over-collects — dead code looks identical — which is why stage 2 must be free to
decline.

A second shape catches what the first misses: a function *registered* somewhere rather than called.
Graphify distinguishes the two, so a subcommand handed to a parser or a route handler handed to a
router stays a candidate even though something in this repository names it.

**Stage 2 — classify each candidate.** See the section below.

### Classifying a boundary candidate

You are given a list of candidate definitions from one repository. For each, you are told its
qualified name, where it is, what contains it, how many callers it has *inside this repository*,
how many calls it makes, and which names it references but does not define.

`module_level` is true for a candidate that is not a definition at all but a top-level statement
whose result is used — `uploaded_file = st.file_uploader("Choose a medical PDF")`,
`if st.button("Add to Index"):`, `query = st.chat_input("Ask a question…")`. `call` names the call
itself. For a script-style application — Streamlit, Gradio, a notebook-shaped app — these *are* the
product's interaction surface: there are no routes and no `__main__` guard, and every control lives
here. Such a candidate is usually a `UI_ACTION`, and its `identifier` is the label the literal
carries, because that is the product's own word for what it offers someone.

Not every module-level call is a control. The extractor already excludes calls whose result is
discarded, which removes rendering and logging, but a value read out of a dictionary or a
configuration lookup can still reach you. Ask the usual question: does this offer someone outside
the system a way in? If not, `NONE`.

`literals_nearby` holds the string literals written beside the definition and beside whatever
registers it — the route in `@router.post("/orders")`, the subcommand name in
`add_parser("report")`, the topic in `@broker.subscribe("order.placed")`, the schedule in
`@scheduler.every("24h")`. This is how a boundary gets a name a reader would recognise. Combine it
with the registrar: a handler referencing `post` whose nearby literal is `/orders` is
`POST /orders`. Use a literal only when it plainly names the thing being registered; a help string
or a field name sits in the same window and names something else.

`registered_by` names anything that hands this function somewhere as a value rather than calling
it — a parser being given a subcommand handler, a router being given a route handler, a scheduler
being given a job. A function that is registered is reached from outside even though something in
this repository names it.

The referenced-names field is where a decorator, an annotation, or a registration call appears. A function
decorated `@app.post("/invoices")` references an undefined `post`; a Spring method annotated
`@GetMapping` references an undefined `GetMapping`; a Go handler passed to `http.HandleFunc`
references an undefined `HandleFunc`. Read those names and decide what they mean. Do not expect a
fixed set — the point of showing you the raw names is that an unfamiliar framework is as legible
as a familiar one.

Return one assignment per candidate you can classify, choosing `entry_class` from:

- `ENDPOINT` — reachable over a network protocol at an address. Put the method and path in
  `identifier` when the evidence shows them (`POST /invoices`).
- `CLI_ENTRY` — the command-line program itself: the thing a person types. Usually one.
- `CLI_COMMAND` — one subcommand of a `CLI_ENTRY`, registered with a parser. `identifier` is the
  subcommand's own name (`report`, `reconcile`), not the program's.
- `SCRIPT_ENTRY` — the top-level thing a runtime starts. Usually one per executable.
- `UI_ACTION` — a control on a screen. `identifier` is the label the user sees.
- `SCHEDULED_JOB` — started by a clock or a timer rather than a person. `identifier` is the
  schedule or the job name.
- `EVENT_CONSUMER` — invoked when a message or event arrives on a topic, queue, or channel.
  `identifier` is the topic or event name.
- `REGISTERED_ENTRY` — handed to a framework to be called later, where the evidence does not say
  which of the above it is: a task, or a handler whose channel the evidence does not name.
- `NONE` — none of the above.

**`NONE` is the common, correct answer, and returning it costs nothing.** Most candidates are
ordinary internal functions that happen to be uncalled — dead code, a helper only tests use, a
public function of a library. A boundary is where something *outside the system* reaches in. If you
cannot name what outside thing reaches this and how, the answer is `NONE`. Promoting a helper to a
boundary is far more expensive than declining one: every boundary seeds an evidence packet and a
model call, and a wrongly promoted helper produces a confident requirement about a capability the
product does not offer.

**Never invent an identifier.** Use a literal that is actually in `literals_nearby`, or leave
`identifier` null. A route path reconstructed from a function name is a fabrication, and it will be
published as though the product declared it.

**Never state a location.** You name a node; the location comes from the graph. Any file or line
you write is discarded.

Set `effect` to `read` or `mutate` only where the evidence shows which. Leave it null otherwise.

Give `rationale` as one short sentence naming the evidence that decided it — "nothing here calls
it and it references `post`" — not a restatement of the class you chose.

## Rules and constraints

- Extraction must run to completion even when individual files fail to parse — record the failure
  and continue; one bad file must never abort the whole pass.
- Prefer under-claiming to over-claiming: `AMBIGUOUS` / low confidence is the honest answer when
  extraction genuinely cannot resolve something, not a forced `RESOLVED`.
- Documentation and deployment files (`README*`, `Dockerfile`, `docker-compose*`, `package.json`,
  `pyproject.toml`, `go.mod`, `pom.xml`, …) are captured as a short redacted `content_outline` on
  the `FILE` entity. This can *support* a claim alongside real code evidence, but it must never be
  the only evidence for a requirement — a README claim with no corresponding code path is not
  implemented functionality.
- Redact anything whose name matches `api_key|password|secret|token`.

## Output format

A single JSON document with `entities: []`, `relationships: []`, and `metadata` (files seen, files
that produced nodes, extraction coverage, languages present, entity and relationship kind counts).
This is what `evidence-grounded-requirement-synthesis` reads next.

## Limitations to state plainly in the final report

- **Behavioural extraction is gone.** Graphify models structure — definitions, calls, imports,
  containment. It does not model a conditional as a business rule, an exception handler as error
  handling, a persistence call as a data operation, or an environment read as configuration. The
  previous Python-only extractor did, and the requirements document is correspondingly thinner in
  its business-rule, data-requirement, and non-functional families. Say so rather than letting a
  reader infer the product has no business rules.
- **Declaration-style code is partly recovered, and partly not.** Graphify keys on `class` / `def`
  / `func` declarations and models no module-level statement, so a script-style application would
  otherwise have no interaction surface at all. A `MODULE_SURFACE` node is therefore read directly
  from the source for each top-level call whose result is consumed — which recovers Streamlit,
  Gradio and notebook-shaped products, whose every control lives there. What it does not recover is
  a DSL that takes a block rather than a consumed value: a Sinatra `get '/loans' do`, a Rails
  routes file. Those still yield a file node and nothing else.
- **SQL yields tables, triggers, and foreign keys, but not CHECK constraints or enumerated
  vocabularies.** A database-enforced policy that used to be extractable is not any more.
- **A route path is read from the source, not from the graph.** Graphify records that a handler
  references `post`, never the `"/orders"` handed to it, so the literals beside a definition and
  beside its registration are read directly and offered as evidence. Where a registration happens
  far from the definition and nothing links the two — a Go handler passed to `http.HandleFunc`
  inside `main` — the route is visible at the registration site but cannot be attached to the
  handler, and the boundary is named after the function instead.
- **Boundary classification is inferred, not parsed.** A boundary the previous extractor found
  syntactically — a decorator with a literal path — is now a judgement from call degrees and
  referenced names. It is marked `INFERRED` at low confidence, and both the boundary and every
  requirement resting on it should be read accordingly.
- Dynamically registered routes, reflection-based wiring, and runtime dependency injection remain
  invisible to static analysis.
- No execution, no tests run, no runtime traces — "implemented" here means "present and reachable
  in the source," not "verified correct at runtime."
