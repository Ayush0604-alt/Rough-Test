# Questions you should have after reading this project — and their answers

The questions a careful person asks once they have understood *what* the system does and start
asking *why it is built this way*. Every answer is drawn from the source and points at the module
that owns the decision.

Read alongside `Architecture.md` (what exists) and `decisions.md` (what was chosen and whether it
was right). This file is the "why does that make sense?" layer between them.

---

## Contents

- [A. The product](#a-the-product) — Q1–Q7
- [B. The knowledge graph](#b-the-knowledge-graph) — Q8–Q17
- [C. Interaction boundaries](#c-interaction-boundaries) — Q18–Q26
- [D. Cost, fan-out and evidence packets](#d-cost-fan-out-and-evidence-packets) — Q27–Q34
- [E. The classification model](#e-the-classification-model) — Q35–Q42
- [F. Consolidation](#f-consolidation) — Q43–Q52
- [G. Determinism and the model's job](#g-determinism-and-the-models-job) — Q53–Q59
- [H. Gates, repair, quarantine](#h-gates-repair-quarantine) — Q60–Q68
- [I. Honesty mechanisms](#i-honesty-mechanisms) — Q69–Q76
- [J. The skill-pack architecture](#j-the-skill-pack-architecture) — Q77–Q86
- [K. Pack 2: requirements to test specifications](#k-pack-2-requirements-to-test-specifications) — Q87–Q96
- [L. Operating it](#l-operating-it) — Q97–Q104
- [M. What reaches the model, and why not all of it](#m-what-reaches-the-model-and-why-not-all-of-it) — Q105–Q111

---

## A. The product

### Q1. What problem does this actually solve?

Somebody has a codebase nobody wrote the requirements for — an inherited system, an acquisition, a
product that outgrew its documentation, a compliance audit that needs a specification. The normal
answer is "a business analyst reads the code for three weeks and writes a document that is mostly
right". This system produces that document in one run, and — crucially — makes every claim in it
**checkable in two clicks** against the file and line it came from.

The value is not the speed. It is that a reverse-engineered requirements document is normally
*unfalsifiable*: you cannot tell which sentences came from the code and which from the analyst's
imagination. Here every sentence carries its citation, and a fabricated one is caught before
publication.

### Q2. Why not just give the whole codebase to a large-context model and ask for requirements?

Four reasons, in order of how badly each one bites:

1. **You cannot check the output.** The model will produce a fluent, plausible document. Some of it
   will describe code that does not exist. Nothing tells you which parts.
2. **Output ceilings, not input ceilings, are the binding constraint.** Even with a million-token
   context, the *reply* is capped. At ~120 tokens per written requirement an 8k ceiling holds about
   sixty items including their criteria and citations. A platform with two hundred real capabilities
   silently comes back with five. `capabilities/assemble.py` documents exactly this happening.
3. **Cost scales with the repository**, not with the number of things worth describing.
4. **It is not reproducible.** Run it twice and you get two different documents with different
   counts and different taxonomies — which is what happened here before the tiers were pinned down
   (`capabilities/claims.py:_tier_from_the_graph` records the symptom: "across repeated runs on one
   unchanged codebase the same findings came back typed entirely functional, then mostly business,
   then a mix").

### Q3. Why is an *empty* requirements document ever a correct answer?

Because a repository with no interaction boundary is a library, and saying so beats inventing a
purpose. `ingest_repository` halts when nothing is in a parseable language; Gate 1 warns when no
boundary is found; and the deliverables always render so the gap analysis can explain why coverage
is thin. The rule is stated in the README: *"An empty requirements set is a valid outcome when the
document says why."*

This is the single clearest signal of the project's posture: it would rather publish nothing than
publish something it cannot stand behind.

### Q4. Is this a UI testing tool?

No — and this trips up nearly everyone who arrives at the repository. It never executes the target
code and never runs a test. PACK 2 writes **test specifications**: prose cases with preconditions,
steps and expected results, deliberately forbidden from naming any framework, protocol, browser or
language (`capabilities/testgen/quality.py:FRAMEWORK_WORDS`, enforced at the gate and again at
render).

The UI-testing idea comes from the fact that this specification layer is designed so a *later,
separate pack* can turn one spec into a Playwright test, a pytest test, or a manual script. That
pack does not exist in `src/`, though `MDfiles/Pack3.md` and `MDfiles/work.md` design it in detail.

### Q5. Why two packs instead of one pipeline?

Because they have genuinely different inputs, different failure modes, and different audiences. PACK
1 reads code and answers "what does this product do?"; PACK 2 reads *a document* and answers "what
would you check to know it does?". Splitting them means you can re-run test generation against an
edited requirements document without re-analysing the codebase, and it means a third pack can attach
to either one.

The split is real, not cosmetic: `schemas/testgen.py` imports **nothing** from `schemas/brd.py`, and
says so in its docstring. PACK 2 consumes the *published artifact* (`requirements.json`), not the
other pack's internal models.

### Q6. What is the actual deliverable — the document, or the traceability?

The traceability. `requirements.md` is what a stakeholder signs; `traceability_matrix.csv`,
`gap_analysis.md` and `requirement_analysis.md` are what make it trustworthy. The README's own
verification instruction is the giveaway: *"pick three rows at random, and confirm the cited file
and line range actually say what the requirement claims."*

That is why the Evidence column was moved **into** the requirements document rather than left in a
companion file — "a requirement whose evidence is one click away is one nobody checks"
(`capabilities/render.py:_evidence_cell`).

### Q7. Who is each output document for?

| Document | Reader | What they do with it |
|---|---|---|
| `requirements.md` | stakeholder / BA | sign off on what the product does |
| `requirements.json` | the next pack, or a tool | machine consumption; **the inter-pack contract** |
| `traceability_matrix.csv` | reviewer / auditor | spot-check claims against code |
| `gap_analysis.md` | the team | see what the analysis *missed*, file by file |
| `requirement_analysis.md` | reviewer | audit the judgements that did **not** make it in |
| `technical_specification.md` / `api_documentation.md` / `workflows.mmd` | engineers | orientation |

---

## B. The knowledge graph

### Q8. Why are we creating a knowledge graph at all? Why not feed source files to the model?

This is the central architectural question, and there are four distinct answers — each one alone
would justify the graph.

**1. Cost becomes linear in the number of interesting places, not in the size of the repository.**
Source text has no structure you can budget against. A graph does: you can walk outward from one
entry point, stop after six hops or 250 nodes, and serialise exactly that neighbourhood into a
40,000-character packet (`capabilities/graph_ops.py`). A 500-file repository and a 5,000-file
repository with the same number of endpoints cost roughly the same.

**2. It makes fabrication detectable by construction.** The model is only ever shown facts that came
out of the graph — a qualified name, a file, a line range. If it cites something it was not shown,
Gate 2 catches it, because every citation is re-resolved against the real file system
(`capabilities/verify.py:verify_location`). Give the model raw source and it can paraphrase a line
number into existence with nothing to check it against.

**3. "Reachable from outside" is a graph question and nothing else.** The core grounding rule —
every requirement must trace to an interaction boundary — is *reachability*. You cannot answer it by
reading files. You answer it by walking edges, which is what `boundary_downstream()` does.

**4. It is the stable contract that let extraction be replaced.** When the Python-only AST
extractors were swapped for Graphify, *nothing* downstream of `plan_clusters` changed, because the
contract between extraction and the rest of the pipeline is `KnowledgeGraph` and nothing else. The
adapter's whole job is to satisfy that contract (`capabilities/graphify.py` docstring).

### Q9. What does the graph know that the file system does not?

Relationships, and their certainty. A file system tells you `orders.py` exists. The graph tells you
that `create_order` is called by nothing in this repository (so a framework probably invokes it),
that it calls `validate_payment` which calls `charge_card`, that `charge_card` references an
external symbol the repository never defines, and that the whole chain is reachable from a node
classified `ENDPOINT`. Every one of those facts is load-bearing somewhere downstream.

### Q10. Why does every node and edge carry `provenance` and `confidence`?

Because a claim should be worded as cautiously as its evidence deserves, and that requires knowing
how certain the extraction was. The four levels (`schemas/core.py:Provenance`) are:

```
EXTRACTED  syntactically certain from parsing
RESOLVED   a reference matched exactly one real definition
INFERRED   derived by heuristic
AMBIGUOUS  matched several candidates, or an unclear external symbol
```

These are read by Gate 1 (`HIGH_AMBIGUITY` fires above 30%), by the confidence recalculator
(`PARSED_EVIDENCE = 0.10` only for `EXTRACTED`), and by the hedging pass (a claim resting entirely
on non-extracted evidence gets an assumption saying it rests on prose beside the code, not the code).
Uncertainty propagates rather than being laundered into a clean-looking document.

### Q11. Why are `symbol` and `scope` separate fields on a location?

Because they make different promises and are checked differently:

- **`symbol` asserts a name that appears at these lines.** Verification re-reads the file and
  rejects the citation if it does not. This is *the* anti-fabrication check.
- **`scope` names the enclosing definition the lines sit inside.** Its name lives at the `def` or
  `class` header, which may be hundreds of lines above, so it carries no such promise and is never
  line-checked.

Collapse them and every statement deep inside a long function looks fabricated
(`schemas/core.py:SourceLocation`).

### Q12. Why does every inventoried file get a FILE entity, even ones the extractor produced nothing for?

Two regressions, both recorded in `capabilities/graphify.py`:

- `--code-only` skips documentation, so a README is absent from Graphify's payload. But a README is
  the clearest statement a project makes about its own purpose, it is where
  `declared_documentation` comes from for the `elevate` step, and a requirement citing it was being
  rejected as citing nothing.
- A file in a language Graphify cannot parse would otherwise vanish entirely, rather than appearing
  as something that was inventoried and not analysed — which is the honest report.

### Q13. Why are there RATIONALE nodes (docstrings and comments) in a *code* graph?

Because prose states intent that code cannot. A module docstring is often the single richest
statement of what a part of the product is *for*, and which cases it deliberately refuses. But it is
treated as **supporting** evidence only, never the sole warrant for a requirement:
`correspondence._NOT_CODE_KINDS` excludes RATIONALE from "something the extractors understood sits
here", so a claim anchored only to a comment has not been shown to correspond to an implementation.
The prose still reaches `vocabulary()`, where it helps the anchoring check.

### Q14. Why does the adapter have to reconstruct line *ranges*?

Because Graphify emits a start line and never a range (`"source_location": "L26"`). Without
reconstruction every citation is one line long, evidence packets become unreadable, and the
synthesis instruction to "cite at the span of the definition it rests on" has nothing to work with.

`_derive_line_ends()` does it in two passes — a sibling bound (a definition cannot extend past the
next one; the last runs to EOF), then a containment repair iterated to a fixed point so a class
extends to the end of its last method — plus a special case for docstring nodes, which are *inside*
the definition they describe rather than siblings of it. Counting them as siblings truncated every
documented function to the lines above its own docstring.

The result is deliberately an **over**-approximation, marked `line_end_estimated: True`: a span
slightly too long still contains the evidence, whereas one too short silently drops it.

### Q15. Why is `REGISTERS` a different edge from `CALLS`?

Because a handler *handed to a framework* — `router.add("refund", handler)`,
`builder.add_node("sql_agent", run_sql)`, a task queue, a callback table — is never **called** by
any code in the repository. A call-graph walk cannot reach it, so it landed in no evidence packet and
no requirement was written about it.

Graphify emits `indirect_call` for this, and mapping it to `CALLS` would make every registered
handler look like ordinary called-from-here code — which disqualifies it as a boundary candidate.
Mapping it to `REGISTERS` keeps it a candidate *and* keeps it traversable. The edge carries lower
confidence on purpose: the reference is certain, that the framework invokes it is an inference.

### Q16. Why are IMPORTS and CONTAINS excluded from workflow traversal?

Because they connect almost everything to everything. Follow imports and every cluster becomes the
whole graph, which defeats the entire point of bounded packets. `WORKFLOW_EDGE_KINDS` is deliberately
narrow: `CALLS`, `ENFORCES`, `PERFORMS`, `HANDLES`, `READS_CONFIG`, `EXPOSES`, `TRIGGERS`,
`REGISTERS`.

The cost of that narrowness is handled separately: `declared_policy()` reaches module-level
configuration through containment edges specifically, because a module-level constant is in scope for
every function in its module but hangs off the module by an edge no workflow walk follows — and that
is the one place a codebase states policy in its own words.

### Q17. Why does an unknown Graphify relation become `RELATED_TO` instead of being dropped?

`RELATED_TO` is in no traversal set, so an unrecognised relation adds a visible node pair rather than
silently reshaping a cluster. You can see it in the metadata (`relationship_kinds`) and decide
whether it should be mapped. Dropping it would hide a change in the extractor's vocabulary.

---

## C. Interaction boundaries

### Q18. What *is* an interaction boundary, and why is it the central concept?

A point where the system is consumed from outside: an HTTP endpoint, a CLI command, a `__main__`
block, a UI control, a scheduled job, a message consumer, a registered handler.

It is central because it is the operational definition of "a requirement". A requirement says what
somebody outside the system can cause or observe. Code that no outside actor can reach is
*implementation*, however sophisticated it looks. So the boundary set determines what can be
described at all — which is why `capabilities/entry_points.py` opens by calling entry-point discovery
"the single highest-leverage thing in the pipeline: a class of entry point nobody detects is a class
of capability nobody documents."

### Q19. Why must every requirement trace to a boundary?

Because otherwise you get a document describing the codebase's *internals* as though they were
product commitments — "the system shall construct a session object", "the system shall cache the
parsed configuration". Those are true of this implementation and would vanish in a rewrite. Gate 2
rejects a business/functional/criterion claim whose trace reaches no boundary
(`NO_INTERACTION_BOUNDARY`), which is the single strongest filter against over-extraction.

Note the supplementary families are exempt: a schema invariant is enforced *beneath* every boundary
rather than at one, and an integration is depended upon rather than exposed.

### Q20. Why is "nothing in this repository calls it" the primary detection signal?

Because it is a **shape**, not a framework list. A handler is invoked by a framework, a runtime, or a
person — never by the code around it. So on a call graph built only from this repository, a route
handler, a CLI command, a scheduled job and a UI callback all share one property: **in-degree zero on
`CALLS`**. That holds for Express, Spring, Sinatra, Flask, gin, and frameworks nobody here has heard
of (`capabilities/boundaries.py` docstring).

The alternative — recognise `@app.route`, `@GetMapping`, `http.HandleFunc`, … — is precise and
endless: every new framework is a code change, and you are always behind.

### Q21. If the signal is that good, why is a model involved at all?

Because in-degree zero **over-collects**. Dead code, a helper only a test uses, and a public function
of a library all look identical to a route handler. Something has to decide which uncalled functions
are actually reached from outside, and that judgement needs to read names, nearby string literals,
and referenced decorators the way a person would.

That is precisely why the classifier is given a `NONE` option and told explicitly that most
candidates are not boundaries. Without a way to decline, a classifier promotes helpers to boundaries
(`schemas/boundaries.py:BoundaryClass`).

### Q22. Why is the model allowed to say *what* something is but never *where* it is?

This is the general safety pattern of the whole system: **let the model classify, never let it
locate.**

The classifier returns a `qualified_name` and a class. `ClassifiedBoundaryAdapter` resolves that name
against the graph and takes the location from the entity it found. A hallucinated name resolves to
nothing and is dropped. So the worst case is a *missing* boundary, not a *fabricated citation* — and
a missing boundary is reported by the coverage report, while a fabricated citation would be a lie in
a signed document.

The same pattern appears in `elevate` (told to leave `evidence` empty, because it is shown only
titles) and in `apply_repairs` (the returned item's classification fields are overwritten from the
original).

### Q23. Why do classified boundaries carry confidence 0.55?

Because that a model reading call degrees and referenced names identified a boundary is an
*inference*, and deliberately lower than any syntactic detector would have been. Gate 1 additionally
warns when more than half the boundaries rest on judgement (`INFERRED_BOUNDARY_SHARE`) — which under
Graphify is the normal state, since nothing in its output marks a route. The warning exists so the
gap analysis can say which document rests on judgement and which on syntax, rather than letting a
reader assume the latter.

### Q24. What is `MODULE_SURFACE` and why does it exist?

A script-style UI — Streamlit, Gradio, a notebook-shaped app — declares its **entire** interaction
surface as top-level widget calls. An extractor that models definitions sees a graph of helper
functions, no boundary, no cluster, and produces an empty document: the product looks like a library
with nothing exposed. That is a total failure, not a partial one.

`_module_surface()` closes it with a shape test rather than a framework list — a module-level line
whose call result is **consumed** (assigned, or tested in a condition) and which carries a string
literal, calling something the file did not define:

```
uploaded = st.file_uploader("Choose a medical PDF")   → a way in    (value is used)
if st.button("Add to Index"):                         → a way in    (value is tested)
st.success("Done!")                                   → display     (value discarded)
RETENTION_DAYS = int(os.getenv("X"))                  → a constant  (ALL-CAPS, excluded)
```

### Q25. Why does the planner walk *backwards* across `EXPOSES` sometimes?

Because a boundary node is often just a *declaration*. A route declares that a path exists; the
behaviour a requirement must describe lives in the handler that serves it. If the walk started at the
route and went forward, the packet would be a single node and the requirement would be written from
nothing.

But falling back to the container *unconditionally* would be worse: every control on a single-script
UI is exposed by the same module, so all of them would return the whole module and collapse into one
undifferentiated cluster. So the rule is conditional — a boundary that declares its own workflow uses
it; one that declares nothing steps back across `EXPOSES` (`GraphIndex.workflow_from`).

### Q26. Why does registering an entry-point adapter also extend `BOUNDARY_KINDS`?

Because those two facts belong together. A class the planner will not seed a cluster from is a class
whose adapter cannot produce a requirement. Keeping the boundary set in a separate hand-edited
literal is exactly how `SCHEDULED_JOB` came to be declared for years with nothing able to produce one
(`capabilities/entry_points.py:entry_point_adapter`). An adapter now brings its own classes with it.

---

## D. Cost, fan-out and evidence packets

### Q27. Why one model call per boundary instead of one call for the whole codebase?

Because it makes cost **linear in the number of interaction boundaries** rather than in the size of
the codebase, and because each call carries only its own slice — so a repository with 200 endpoints
costs 200 small calls instead of one impossible one.

It also isolates failure: a worker that fails costs one boundary's analysis, not the run. The node
wrapper handles this explicitly (`graph/nodes.py`: "One boundary failing must not discard every other
boundary's analysis").

### Q28. What exactly is an "evidence packet"?

A compact JSON record list built from the graph — never source text. Per node: kind, qualified name,
`file:Lstart-Lend:symbol`, provenance, confidence, a per-kind whitelist of attributes with
truncation budgets, and up to 12 outgoing workflow edges. Bounded at
`evidence_packet_chars = 40_000`.

```
{"n": {"k":"FUNCTION","q":"app/orders.py::create_order","at":"app/orders.py:L44-L88:create_order",
       "p":"EXTRACTED","c":1.0,"a":{"parameters":[...],"docstring":"Create a new order…"}},
 "out": [["CALLS","app/pay.py::charge",  "EXTRACTED",1.0,"app/orders.py:L61"], …]}
```

The attribute whitelist is per-kind and budgeted per-key: prose that states intent (a docstring, a
description) gets 1,200 characters, because at 200 a module docstring is cut off mid-sentence, *"which
is worse than useless: it reads as a complete thought that says something other than what the author
wrote"* (`graph_ops.compact_entity`).

### Q29. Why must the model never see raw source?

Three reasons. It would blow the cost model (source is unbounded, a packet is not). It would let the
model cite any line it read, whether or not that line supports the claim. And it would tempt it into
describing *how the code works* rather than what the product does — which is the failure mode the
entire abstraction test exists to prevent.

### Q30. Why split an oversized neighbourhood across packets instead of truncating it?

Because a boundary that reaches most of an application is **ordinary, not pathological** — one chat
entry point dispatching through a graph of handlers is the common case. And the tail of a
breadth-first walk is the *deepest, most specific* work: exactly the requirements nobody else would
write.

Truncating dropped that silently. `build_packets()` now slices across up to six packets, each
dispatched as its own cluster (`<id>#1`, `<id>#2`, …), and anything still unplaced is counted and
reported in the gap analysis.

### Q31. Why stratified sampling instead of just taking the first 80 clusters?

Because the incoming order cannot be trusted. Boundaries of one kind routinely carry the same
degree, so the primary sort key is constant and the whole ordering collapses to the alphabetical
tiebreak on `qualified_name`. Where that name begins with a class discriminator — an HTTP verb, a
queue name, a command — the list arrives **grouped by class**, and `groups[:limit]` removes whole
classes rather than thinning each one.

In the run that exposed this, **147 state-changing endpoints were never planned** because they sorted
after the reads. `_stratified()` allocates the budget proportionally with a floor of one per class,
largest-remainder first, and reports every class **starved** (zero clusters) or **thinned**.

The general lesson is worth keeping: *a cap applied to an ordering you did not design is a
categorical filter, not a sample.*

### Q32. Why were the traversal bounds raised from 4 hops/120 nodes to 6/250?

Because reaching a **registered** handler costs three hops before any work is described — entry
point, wrapper, the builder that registers it — so the handler itself sat exactly on the old horizon
and everything it called lay past it. That is not a rare shape; it is how routers, task queues and
agent graphs are all built.

Measured: 35% → 88% of functions reaching an evidence packet on a multi-agent codebase, with every
module represented; and a small gain rather than a regression on a directly-wired one (17% → 19%).
Raising the horizon was only affordable once oversized neighbourhoods were split rather than
truncated (Q30).

### Q33. Why are declaration files given a *separate* budget instead of competing for the same slots?

Because a schema and an HTTP verb are not competing samples of one population. Pooling them was tried
and there is no ordering that works: served last, declarations are starved exactly as before the fix;
served first, they starve the largest class of endpoints.

A packet is grown outward from a boundary, so evidence is only ever seen if something *calls* it —
the right question for behaviour and the wrong one for a declaration. A schema states the values a
column may take whether or not a handler touches it. In one measured run 444 entities were extracted
from a schema and not one requirement cited it. `declaration_groups()` gives such files a separate
quarter-budget (`graph_ops.build_boundary_clusters`).

### Q34. Why are several boundaries sometimes merged into one packet?

Because several boundaries routinely share one workflow — two routes on the same handler, an upload
control and the button that consumes it. Sending near-identical packets separately produces
near-identical claims that survive as duplicates, *because each one is independently true*. Grouping
by workflow signature costs one call instead of several and lets the model tell them apart in a
single pass; the others are named in `also_exposed_by` so none is silently dropped.

---

## E. The classification model

### Q35. Why three tiers, and why do they form a chain?

```
Business Requirement ──realised by──► Functional Requirement ──constrained by──► Business Rule
      (why)                                (what)                          (under what constraint)
```

Because two claims can both be typed correctly while the document never says **which outcome a
capability serves** — and that is the failure the link exists to prevent. The chain is what makes the
taxonomy structural rather than decorative: each item names its parent one tier up, and Gate 2 checks
the link.

### Q36. Why ask for `abstraction_basis` *before* `requirement_type`?

This is the best piece of prompt engineering in the project. Asked only for a *type*, the taxonomy
offered nowhere to put a behaviour that was interesting but not required of the product — and
discarding it would throw away real evidence. So every internal helper, conditional and variable
assignment got promoted to a functional requirement.

Asking for the **grounds** first gives `implementation_detail` a destination (`observation`), which is
kept in the traceability model and never published as a requirement. Over-extraction stops being the
path of least resistance (`schemas/brd.py` module docstring).

The general principle: **over-extraction is usually a symptom of a taxonomy with no bin for "not
this."**

### Q37. What are the seven bases, and how does one become a tier?

```
business_outcome      → business               (why the product exists)
external_capability   → functional             (what an outside actor can do and observe)
internal_behaviour    → system                 (what the system must do, however built)
domain_constraint     → business_rule          (a rule the business imposes)
platform_constraint   → technical_constraint   (a limit the platform imposes)
quality_property      → non_functional         (a measurable property)
implementation_detail → observation            (true of this code, not required)
```

`BASIS_TYPES` is a **total function** from basis to tier, so the grounds and the class can never
disagree. Where the stored type contradicts the basis, the basis wins
(`claims._agree_with_the_grounds`), and `published_kind()` derives the published type from the basis
rather than the stored field — so a document derived this way *cannot* publish a type that
contradicts its own grounds.

### Q38. Why is the section the same thing as the tier?

Because two vocabularies for one distinction always eventually disagree. Earlier versions carried
seven types kept separate from four document sections, so an item could be filed in one place and
typed another. Collapsing them removes the possibility (`schemas/brd.py:TIER_SECTIONS`, and
`claims._file_by_tier` which physically moves items into their tier's section).

### Q39. Why do DATA / INT / NFR / RPT sit *outside* the chain?

Because they assert genuinely different things, and folding them into the functional tier both
mislabelled and hid them. None takes a parent — **an integration is depended upon, not "realised by"
anything**. A data invariant is enforced beneath every boundary rather than at one.

| Family | Id | Asserts |
|---|---|---|
| Data | `DATA-nn` | an invariant the stored data must hold |
| Integration | `INT-nn` | an external system depended on, and the behaviour when it is unavailable |
| Non-functional | `NFR-nn` | a quality property the code fixes **concretely** — never an aspiration |
| Reporting | `RPT-nn` | a view, export or summary someone reads |

### Q40. Why is a success criterion not a tier?

Because it is an observable condition **on** a requirement, not a requirement. Publishing it as
`functional` — which is what happened while the tier was derived from the section alone — told every
downstream reader that an observable condition was a capability. It is rendered as an acceptance
criterion beneath the requirement it validates and published as `success_criterion`.

### Q41. Why may a functional requirement be the parent of another functional requirement?

Because products decompose that way — a workflow with named stages, a feature with parts — and
forbidding it forced a choice between two wrong answers: publish the stages flat, or invent an
outcome to hold them. It also cost real structure whenever an unsupported outcome was demoted to a
capability: everything beneath it was orphaned by the demotion (`schemas/brd.py:PARENT_RULES`).

### Q42. Why is `priority` derived from the code rather than from importance?

Because nobody asked a stakeholder. The derivation is mechanical and says so wherever published:
`M` when nothing guards the behaviour or its flag defaults on; `S` when a configuration flag, role
check or default-off setting stands in front of it; `C` when the code itself calls it legacy,
deprecated, superseded or opt-in. A stakeholder ranking would be an invention.

---

## F. Consolidation

### Q43. Why isn't synthesis enough? What is the consolidation stage *for*?

Because synthesis reasons about one boundary at a time and validation judges one claim at a time.
**Neither is in a position to ask what the candidates add up to** — and without that question the
output is a behaviour inventory: as many requirements as the code had interesting places, in the
order they were found, each one true and the set as a whole unreadable.

Every question in the stage needs the whole candidate set:

| Step | The question it answers |
|---|---|
| `cluster_candidates` | which candidates describe **one capability**, wherever in the repository? |
| `consolidate` | what level is each claim at, which are duplicates, which parent links mean anything? |
| `check_correspondence` | does the claim say what the code it cites actually does? |
| `score_confidence` | how far can this be trusted, given its evidence? |
| `strip_implementation` | which words are this repository's own machinery? |
| `resolve` | what happens to each candidate — kept, reclassified, merged, refiled, rejected? |
| `derive_criteria` | what observable conditions would show each **final** requirement is met? |

### Q44. Why couldn't the fan-out workers deduplicate among themselves?

Because a worker sees one interaction boundary and cannot know that another worker described the same
capability from the other side of it. The only place that comparison can happen is after all of them
have finished, over all of their output at once (`capabilities/clustering.py`).

### Q45. Why does clustering compare *wording* rather than shared code?

Because requiring shared code was too strict in exactly the case that matters. Every comparison
before this stage required two candidates to share a citation span or a call-path node — and **two
workers describing one capability from the entry point and from the work behind it cite different
lines by construction**. Their wording was therefore never compared and both were published.

Clustering is deliberately evidence-blind: it reduces each statement to a signature read off the
sentence's own shape (actor / action / object, which the synthesis skill already prescribes) and
groups by what they *say*, wherever in the repository they came from.

### Q46. Why is containment checked *before* similarity?

Because a broad capability and the narrow one beneath it share most of the narrow one's words.
Scoring them on similarity alone reads a hierarchy as a duplicate **and deletes the parent**
(`clustering.relate`). Containment additionally requires a genuine difference in breadth — the
broader must have at least three more terms — because two sentences of the same length that contain
one another are a restatement, not a parent and a child.

### Q47. Why does a merge require the same *verb*?

Because word overlap measures how alike two sentences read; it cannot tell that one word carries the
entire difference:

```
"Staff shall be able to delete a supplier bill"     five of six content words shared
"Staff shall be able to view   a supplier bill"     similarity 0.83  vs threshold 0.65
```

Without `_same_action` as a veto, a whole CRUD surface folds into one requirement, keeping whichever
wording happened to sort last (`capabilities/consolidate.py`).

### Q48. Why is the cross-tier merge threshold so much higher (0.85 vs 0.65)?

Because the tiers disagreeing is *itself* evidence that two workers saw different things. Measured:
an outcome and the capability realising it score about 0.67 against each other — they share their
actor and their object and differ only in the verb. At the same-tier threshold a cross-tier merge
would delete the outcome and keep the mechanism, which is the opposite of what a requirements
document is for.

A declared parent link is never merged at all: it is the analysis's own statement that these two are
a hierarchy rather than a repetition.

### Q49. Why is a claim only a "business rule" if something *declared* it to be one?

Because a codebase states a handful of rules and branches on a thousand conditions. Typing every
condition as a rule buries the handful: in one measured run **1,642 rule entities were extracted and
1,632 of them were ordinary conditionals**, nearly half plain null guards. A reader given that
document cannot find the nine constraints that were actually decided.

The elegant part is the inversion. Detecting guards means recognising null checks, empty checks and
not-found branches — which cannot be done without knowing a language's syntax, and that knowledge is
forbidden in shared code. So the question is inverted: **nothing is a rule unless something it cites
was declared to be one** (a schema `CHECK`, a named constant, explanatory prose). A guard fails the
test by having nothing to show, without this code ever knowing what a guard looks like.

Demoted claims become internal behaviour with a note attached — still published, still carrying every
citation.

### Q50. Why collapse "parameterised families"?

Because a codebase that raises the same generic failure for twenty resources produces twenty findings
differing only in the resource named. Published separately they read as twenty rules and bury the
real ones; published as one, naming all twenty subjects and keeping all twenty citations, they say
the same thing and can be reviewed.

The test is purely structural — same sentence, one differing token position, at least three members —
so it cannot know what the varying token means and works the same on any repository. The verb is
checked explicitly, because *"record / view / update / delete / archive a supplier bill"* is five
capabilities in one frame, not one requirement written five times.

### Q51. Why are acceptance criteria derived **last**, after everything else?

Because a criterion written against a candidate that is later merged, reclassified or dropped either
disappears with it or follows it onto a requirement it was never about. Criteria are still arriving
until the merge and the family collapse have finished, which is also why the criteria tidy-up
(removing repeats and restatements) runs at the very end of consolidation.

### Q52. Why does criteria *extraction* run before the merge, though?

A deliberate departure from the obvious order. A success condition reads very like the requirement it
validates — same actor, same object — so the merge pass sees a near-duplicate and absorbs it into the
statement. The information survives, but in the wrong place: it should be a criterion a reviewer can
tick off, not a clause inside the requirement it was proving. Taking criteria out of the pool first
means similarity is never measured against them.

---

## G. Determinism and the model's job

### Q53. What, precisely, is the model allowed to decide?

Five things, and nothing else:

1. **Which graph nodes are interaction boundaries** (`classify_boundaries`) — it names a node, never
   a location.
2. **What requirements one boundary's evidence supports** (`synthesize`) — the bulk of the work.
3. **What the product is for, and which capabilities realise which outcome** (`elevate`) — from
   titles only, and forbidden from citing.
4. **Whether cited facts support a statement** (the entailment half of `check_correspondence`) —
   optional and skippable.
5. **How to reword a rejected claim** (`repair`) — rejected items only, and its classification fields
   are overwritten from the original afterwards.

Everything else — routing, merging, counting, id assignment, gates, quarantine, rendering — is
deterministic Python.

### Q54. Why is routing never model-driven?

Because a gate must not be reasoned past. A supervisor LLM choosing the next node could route around
a failed gate because continuing looked more helpful — and a model's helpfulness bias is
indistinguishable from unreliability at exactly that moment. Here the router reads the manifest and
the state, so it **structurally cannot** (`graph/router.py`).

There is a fully-written `advisory-routing-orchestration/SKILL.md` in the requirements pack marked
`status: not-wired`, with a header stating that nothing loads it. It documents the road not taken.

### Q55. Why is assembly deterministic — what was wrong with a model merge?

Arithmetic, not style. The old `reduce` step was one call handed every finding from every cluster and
asked to return the finished document. With an 8,000-token output ceiling and ~120 tokens per written
requirement, the reply holds about sixty items *including* every criterion, persona, rationale and
citation. A platform whose clusters produced two hundred grounded findings therefore had them
compressed to a handful — **and the compression was silent**, because nothing counted what went in
against what came out.

The findings did not need re-emitting: a worker had already written each one in full, against evidence
it could see. So assembly copies. The metric `findings_carried` exists specifically because that
number used to vanish.

The general rule: **if a model step's output must grow with its input, it is not a model step.**
Copying is deterministic work; judgement is the model's work.

### Q56. Where else does that same output-ceiling failure appear?

Three more times, fixed the same way each time:

| Step | Was | Now |
|---|---|---|
| `repair` | typed `ProductBRD` — re-emit the whole document to fix three wordings | `RepairedItems` — return only what you fixed |
| `repair_tests` | typed as the whole suite | `RepairedTestCases`, plus a 12-case-per-call cap |
| prompt input | the whole `brd` / `test_suite` reached the prompt and arrived truncated | `_render_brd` / `_render_test_suite` send only the rejected items |

### Q57. Why does `elevate` get *titles* instead of statements?

So that its input and output stay small however large the repository grows. The requirements are
already written out in full, already grounded, already checked — it does not need to read them. It
needs to answer three questions no single cluster could: what the product is, what outcomes it
exists to deliver, and which capability realises which. Those answers are short.

It sends back **titles** in its bindings, which is how hundreds of requirements attach to the top
tier without any of them being restated.

### Q58. Why is `elevate` forbidden from writing citations?

Because it is shown titles, never files or line numbers, so any citation it writes is a guess — and a
guessed citation is caught by validation, which then deletes the outcome and **orphans every
capability beneath it**. A run whose synthesis produced fourteen capabilities and two outcomes
published one capability and no outcome at all.

An outcome's warrant is not a line of code; it is that these capabilities, each already cited, add up
to it. So `ground_outcomes()` derives the citation from the children — which is both honest and
checkable — and re-derives it every time the set changes (after resolution, after repairs, after
quarantine), because a capability removed later must take its citation off the outcome with it.

### Q59. Why is a gate deterministic Python rather than an LLM judge?

Because a gate that could be argued with is not a gate (`capabilities/gates.py` docstring). Also
because these are *facts*, not judgements: does this file exist, is this line within the file, does
this symbol appear within 25 lines, does this trace reach a boundary, does this statement contain a
file path. An LLM judge would be slower, non-deterministic, and would occasionally be talked out of a
correct rejection.

---

## H. Gates, repair, quarantine

### Q60. Why two gates and not one?

They ask different questions of different artifacts at different times. Gate 1 asks *can we trust the
graph?* before any money is spent on model calls — a broken graph means everything downstream is
built on nothing, so it halts. Gate 2 asks *can we trust these claims?* after synthesis, and it
re-checks everything **independently of how it was produced**, re-resolving every citation against the
file system and every graph path against the graph.

### Q61. Why does the repair step see only the rejected claims?

Because handing it the whole document was ruinous in both directions. On input the document blew past
the 60,000-character artifact cap and arrived as a *truncated preview*, so the model was asked to fix
a document it could only partly see. On output it was expected to re-emit every requirement inside
one bounded reply, which past a few dozen is arithmetically impossible — so a fraction came back and
nothing recorded the loss. **A rejection over one phrase could delete most of the analysis.**

Now it receives ~2k characters of rejected claims and returns only what it fixed; `apply_repairs`
merges by id and carries everything else across untouched. A claim the gate never rejected cannot be
altered there at all — which is the property that makes the loop safe to run.

### Q62. Why does `apply_repairs` overwrite the returned item's classification fields?

Because a repair is asked for **wording**. Everything that decides where an item sits — its id,
title, abstraction basis, tier, parent, relation, area, priority, disposition, cluster — was settled
by stages that saw the whole candidate set.

Left to the model these came back altered: *a rule returned with the grounds of an outcome*, which is
a contradiction no rewording can fix, so the gate rejected it again on every pass until the budget ran
out and the claim was deleted. **A repair pass must not be able to fail a claim it was asked to
save.**

### Q63. Why is a wording failure treated completely differently from a grounding failure?

Because they mean opposite things:

- **A grounding failure** (no evidence, a line that does not exist, a trace reaching no boundary)
  means the claim was never established. Remove it — a weaker wording over the same missing evidence
  would still read as a finding.
- **A wording failure** (the statement names the implementation) means the claim **was** established
  and is described badly. Deleting it discards checked evidence over a phrase.

That second case used to be handled for acceptance criteria and not for statements. Measured
consequence: *"On one measured run fourteen functional requirements entered the gate and one left,
and the thirteen losses were almost entirely this."* (`capabilities/quarantine.py`.)

The principle: **a rejection over wording costs a wording, not a requirement.**

### Q64. Why is the repair budget bounded at all?

Because an unbounded loop over a rejection the model cannot fix never terminates, and some rejections
genuinely cannot be fixed by rewording. The budget (3 for requirements, 4 for tests) buys a few
honest attempts and then hands over to deterministic quarantine, which drops what could not be
grounded so a thinner, honest document can still be published.

The attempt is counted **even when the repair call fails**, so the loop still terminates and reaches
the exhausted path.

### Q65. Why is a failed repair call not fatal?

Because halting there threw away every grounded requirement in the set over one bad call, and
produced no deliverables at all. When a repair fails, the claims it was asked to fix are still the
ones the gate rejected, and quarantine already knows how to drop those. A recovery step must not be
able to destroy the run it exists to rescue (`graph/nodes.py:make_llm_node`).

### Q66. Why is an unresolved parent link a *warning* and not an error?

Because the claim itself is fine. It was cited, traced and boundary-checked; the thing it pointed at
was removed by something else. Treated as an error it cost the **child** its place — one rejected
claim taking a grounded one down with it, and then that one's children in turn. What a dangling link
actually costs is the reader's ability to see *why* the product does this, and that is exactly what a
warning is for. The link is cleared by the final structural pass.

### Q67. Why does "whatever deletes an item own the links into it"?

Because a deletion that leaves a child naming the deleted parent produces an unresolvable link — and
the gate rejects the **child** for it. So a repair that correctly removed one ungrounded claim spent
the next round of the budget deleting a perfectly good one beneath it. Both `apply_repairs` and
`quarantine` now clear the links into whatever they removed.

### Q68. Why is a citation fault on a *business requirement* not fatal in quarantine?

Because an outcome's citation is **derived** from the capabilities beneath it, not authored by
anyone. A fault in it is a fault in a child. Removing the child repairs the outcome; removing the
outcome throws away a whole level of the document over a mistake it did not make — and orphans every
capability under it (`quarantine.DERIVED_CITATION`).

---

## I. Honesty mechanisms

### Q69. Why recompute confidence instead of using the model's own number?

Because in one measured run the model reported exactly **1.0 on 40 of 60 requirements** — a document
declaring itself certain about two thirds of its contents, none of which any person had read. A
number that is 1.0 whatever the evidence carries no information, and the low-confidence review list
built from it was therefore empty of precisely the items most worth reviewing.

The replacement is computed from observable properties: the correspondence verdict, whether the
citation names a symbol or gestures at a range, how much independent code corroborates it, whether
the evidence was parsed or inferred from prose, whether a declared constraint stands behind it. The
model's original number is kept in `model_confidence` so the two can be compared and **the
recalibration itself can be judged**.

### Q70. Why is the confidence ceiling 0.95 and not 1.0?

Because nothing in the document has been reviewed by a person, and saying otherwise is a lie the
document tells its reader (`capabilities/confidence.py:CEILING`). It is a small detail that says a
great deal about the project's posture.

### Q71. Why is nothing ever deleted?

Because losing reverse-engineering evidence is the one outcome worse than filing it under the wrong
heading. A candidate that is not a requirement becomes an observation or a criterion; a duplicate is
merged into its twin and recorded in `superseded`; a claim that cannot be published moves to
`requirement_analysis.md` **with its evidence and the reason**; a folded test case's id is recorded on
the case that absorbed it.

The point of `superseded` in particular: *a merge that leaves no trace is indistinguishable from a
claim having been dropped, and the difference matters to anyone auditing coverage.*

### Q72. Why publish doubtful claims in a *separate document* rather than flagging them in place?

The earlier behaviour marked them `status: "rejected"` and published them in the requirements list
beside the claims that passed, so a reader could not tell them apart without reading a field most
tools ignore. The module corrects its own earlier reasoning: *"a reader can check a doubtful claim
and cannot recover a deleted one… is sound, but it argues for **partitioning** the output, not for
putting everything in one list."*

Two documents is the only arrangement honest to both audiences: the stakeholder sees only what the
analysis can stand behind; the reviewer opens the companion and audits every judgement.

### Q73. What is the objective/interpretive split, and why does it matter?

Some correspondence checks answer a question with one right answer (is there a citation? does
anything recognised sit there? is the span small enough?). Others weigh a sentence against code and
**can be wrong** — and the anchoring check is wrong in a *predictable direction*, because the
synthesis skill asks for requirements in the product's business words and then this compares them
against the code's identifiers. A claim written exactly as instructed can share no vocabulary with
the thing it correctly describes.

Since a rejected claim now *leaves* the document, a check that can be wrong must not be able to delete
a requirement on its own: it makes the claim an **open question** instead. *Uncertain and wrong are
different answers and get different dispositions.*

### Q74. Why does a jargon **linter** exist when the prompt already forbids jargon?

*"A prompt instruction is not a check: models comply most of the time, and 'most of the time' is
exactly how a file path reaches a document that a stakeholder signs off on."*
(`capabilities/jargon.py`.)

It is also applied to **acceptance criteria**, not just statements — they are what a reviewer signs
off against, item by item, and checking only the statement let *"the answer is produced by the
retrieval pipeline"* reach a published document under a statement that had been carefully kept free
of exactly that.

### Q75. Why does the linter have a list of ordinary English words it refuses to flag?

Because matching a statement against every qualified name in the graph catches project vocabulary the
generic patterns miss — a class called `Ledger` reads like a business noun until you notice it is also
a symbol. But a codebase also names its functions after the plainest verbs in the language, and a
requirement cannot describe what someone does without using them.

Rejecting those is wrong in both directions at once: the statement is rejected for saying "update",
which is the only word for what the person is doing — and the repair, having been told which word to
remove, produces *"A user shall be able to the next action."* A rejection over vocabulary turning a
correct requirement into a broken one.

### Q76. Why does the gap analysis list source files that **no** requirement describes?

Because coverage counted by construct hides the shape of what is missing. A document can describe 88%
of the functions in a repository and still omit an entire capability, because the omitted part is one
file whose work nobody wrote up. A reader checking whether the analysis understood their system looks
for **the file they know is important** — so that is the unit reported
(`capabilities/render.py:_uncovered_files`).

---

## J. The skill-pack architecture

### Q77. Why is the workflow data instead of code?

So that adding a second analysis capability — test generation, threat modelling, migration analysis —
is a directory of markdown and YAML rather than graph surgery. `graph/builder.py` names no step, no
skill, and no artifact; everything comes from the manifest.

The claim is *tested*, not asserted: `tests/test_extensibility.py` authors a genuinely new pack at
runtime and runs it. If it ever needed a change under `src/agentic/graph/` to pass, the abstraction
has leaked.

### Q78. Does that abstraction actually hold?

About 90%. Four leaks, two of them avoidable:

1. `capabilities/registry.load_builtin_capabilities()` hard-codes imports of **both** shipped packs'
   modules — a third pack must add an import line. *(avoidable)*
2. `schemas/registry.SCHEMAS` hard-codes both packs' output models. *(avoidable)*
3. `llm/prompt.py` registers renderers named `brd`, `test_suite`, `clusters`, `test_batches` —
   but this is a documented extension point (`@renders("name")`).
4. `app.py:_views` knows `brd` and `test_suite` — but tabs are artifact-driven with a fallback.

Worth knowing before you start pack 3: you will touch two engine files, and only two.

### Q79. Why do `SKILL.md` files carry no orchestration metadata?

So they stay portable to any tool that reads the standard skill format. Everything about ordering,
gating and fan-out lives in `pack.yaml`. A skill file has only `name` / `description` frontmatter and
a Markdown body.

### Q80. Why are skills loaded from disk at call time instead of baked into the code?

So editing a `SKILL.md` changes behaviour on the next run — no restart, no code change. The registry
caches on file mtime specifically to make that work while Streamlit is running.

### Q81. Why record a skill's **sha256** even on deterministic steps that never load it?

Because a deterministic step implements its skill in Python, and the two can drift. Recording that
`validate_requirements` was governed by `requirement-and-graph-validation-gates@<sha>` makes that
drift **auditable rather than invisible**, and lets every deliverable state which version of which
instructions produced it.

### Q82. Why does the manifest validate every reference at *load* time?

So a typo fails before ingestion rather than twenty minutes into a run, with a half-populated run
directory left behind. Every skill, capability and schema a manifest names is resolved at load;
`consumes` is checked against what earlier steps `produce`; every `next` / `repair_step` /
`exhausted_step` must name a real step.

That last check is what lets `CapabilityContext.artifact_path` treat a runtime miss as a genuine
defect and raise, rather than returning `None` and having every capability handle it.

### Q83. Why does graph state carry *paths* instead of payloads?

Because LangGraph checkpoints the **entire state** after every superstep. A multi-megabyte knowledge
graph held inline would be re-serialised at every node and would make the UI crawl.

The consequence is a happy accident: because capabilities read prior work through the store rather
than through state, every one of them is unit-testable by constructing a context around a temp
directory — which is what most of `tests/` does.

### Q84. Why does the *downstream* pack declare the chain?

So a new pack announces what it follows without anything upstream needing to know it exists. PACK 2
declares `consumes_pack: codebase-requirements` and `consumes_artifact: requirements.json`; PACK 1
knows nothing about it. A third pack declaring the same about PACK 2's output chains the same way for
free, and `app.py` names no pack at all.

`successor_packs()` additionally checks the required artifact is **present on disk**, so a run that
halted before publishing anything is never offered as an input.

### Q85. Why does PACK 2 read `requirements.json` instead of importing PACK 1's models?

Because the requirements pack is frozen, and a downstream pack reaching into its internal models would
be coupled to decisions it does not control. `schemas/testgen.py` imports nothing from
`schemas/brd.py` and says so.

The reading posture matters as much as the boundary: nothing raises, every problem becomes a
`ReadIssue` carrying the **array position**, only `id` and `statement` are load-bearing, and unknown
keys are **preserved** in `extras` so a future upstream improvement cannot break ingestion.

### Q86. Why can't PACK 2 reuse `ingest_repository`?

Because it halts with `NO_ANALYSABLE_SOURCE` on a directory containing no parseable source — which is
exactly what an `artifacts/` folder is. The comment says so directly in `test-generation-skills/pack.yaml`.

---

## K. Pack 2: requirements to test specifications

### Q87. Why qualify requirements *before* designing anything?

Because *"should this be tested at all"* is exactly the decision that must not sit inside a model
call: **a model asked to design tests will always find something to say.** Qualification is
deterministic, ordered, first-match-wins, and runs before any model is involved
(`capabilities/testgen/qualify.py`).

### Q88. Why produce a record for requirements that get **no** tests?

Because a coverage report that only accounts for what was tested cannot answer *"why is there no test
for this?"* — which is the first question a reviewer asks. Every requirement gets a
`QualificationRecord` with a band, a reason code and a reason, and two of the six deliverables
(`excluded_requirements.md`, `clarifications.md`) exist to publish them.

### Q89. Why does PACK 2 **discard** acceptance criteria that PACK 1 generated?

This is the most interesting interaction between the two packs. When PACK 1's evidence fixed no
observable outcome, `hedging.py` wrote a criterion like:

> *"Exercising the on-screen control produces the outcome this requirement describes."*

That reads as a criterion and asserts nothing. A test derived from it checks nothing. PACK 2 detects
them two independent ways — by template prefix and by the assumption PACK 1 records alongside them,
"because either signal alone could drift" — and discards them. On the rich fixture that removes **59
of 235 criteria** and empties 59 items that appear criteria-bearing.

### Q90. Why must test dimensions be "earned" rather than swept?

Because "cover every dimension" turns one requirement into eleven cases and a 190-item set into an
unusable suite. `positive` is free for anything with a statement; every other dimension must be
activated by something the requirement actually **says**.

### Q91. Why were three of those activation patterns narrowed after measurement?

Each for a reason, not to hit a number:

- `state_transition` matched generic create/update/delete and fired on 76 of 190 — but for "delete an
  item" the positive case *already* asserts the item is gone, so a transition case is the same test
  twice. Now requires a status vocabulary.
- `authorization` matched `admin`/`role` and fired on 34 through the single word "administrator" —
  but naming **who** performs an action says nothing about whether anyone else is refused.
  Generating an authorization case from that **invents a rule the requirement never states**.
- `data_integrity` matched `persist`/`stored` and fired on 31 — persisting is what a write does, not
  a guarantee anyone stated. Now requires an invariant.

The middle one is the general principle: **a test that asserts something the requirement does not say
is a fabrication in test's clothing.**

### Q92. Why is `rationale` excluded from the text that activates dimensions?

Because it is the one field where PACK 1 permits technical vocabulary. Reading it would let an
implementation detail activate a test dimension.

### Q93. Why is the case budget computed before the model runs?

So the designer works inside a plan it did not make. Budget is `min(max(1, dimensions), cap)` with
caps of 8/5/2 by risk and a hard 2 for internal behaviour — deliberately **not** combinatorial,
because several inputs failing for one reason are one case, not one per value.

Measured: the internal-behaviour routing rule alone takes 184 testable requirements from **485**
projected cases down to **280**.

### Q94. Why must test specifications name no framework, protocol or language?

Because the specification layer has to survive being turned into API tests, UI tests, unit tests,
agent-trajectory evaluations, or a manual script — and *"a model that has already decided it is
talking HTTP cannot become any of the others"* (`schemas/testspec.py`).

Three fields exist purely for a future adapter (`interface_hint`, `automation_readiness`,
`TestInput.value_kind`) with an explicit rule attached: **no logic in this pack branches on any of
them.** If one ever starts steering behaviour, the independence claim is gone.

### Q95. Why is `positive_only` the most dangerous coverage verdict?

Because it reads as covered and the behaviour most likely to be wrong has no test at all. A
requirement that states a refusal, an error or a limit and got only a happy-path case looks fine on
every count-based metric. `analyse_coverage` singles it out for exactly that reason.

### Q96. Why does the test repair loop re-enter at `dedupe` rather than at the gate?

Because a repaired case may now duplicate one it did not before, and coverage was measured against
the pre-repair suite. Both have to be recomputed.

---

## L. Operating it

### Q97. Why is the target snapshotted into the run directory?

So a run is reproducible and the target cannot change under the analysis half-way through. Every
citation resolves against a tree that cannot move, and the graph, the packets and the gates all agree
about what the file system said.

The unexpected dividend: because `prepare_run` accepts *any* directory, pointing PACK 2 at a PACK 1
run's `artifacts/` folder works with **zero** additional code.

### Q98. Why does the run halt when nothing is in a parseable language?

Because analysing it would mean guessing from file names, which is precisely the failure this gate
prevents. Note how much narrower the door is now: it used to close on any repository without Python
in it; it now closes only on one with no source in any of the languages Graphify parses, which in
practice means documentation, data or assets alone.

### Q99. What should I do when a run halts?

Read `runs/<id>/artifacts/run_failed.md` — the halt node writes the reason, the failing gate's first
three ERROR issues, and every step that completed. Then check, in order: is `graphify` on PATH
(`pip install "graphifyy[sql]"` — note the double *y* in the PyPI name); are all four
`AZURE_OPENAI_*` values set (preflight should have caught this before any work); and did Gate 1 fail
on `UNRESOLVABLE_LOCATION`, which usually means the source tree moved or the extractor cited a path
that is not in the tree.

### Q100. Why is Graphify an external binary rather than a library?

Because it covers 36 languages via local tree-sitter parsing with no API calls, and the alternative —
maintaining per-language AST extractors in this repository — was the constraint the swap existed to
remove. Every language other than Python used to reach the graph as a bare FILE entity and produce no
requirement at all.

The price is real and worth knowing: Graphify emits **structure, not intent**. It has no `kind` field,
no line ranges, and nothing that marks a route or a command — which is why roughly 600 lines of
`graphify.py` exist, and why boundary detection now needs a model call.

### Q101. Why does extraction failure **halt** the run rather than warn?

Because under a full swap Graphify is the only extractor. Continuing past a failed extraction would
publish a document built on nothing and report it as success.

### Q102. What are the 13 boolean settings for?

Each consolidation pass can be switched off individually through `.env`, so its effect on the corpus
metrics can be isolated and measured. They all default **on** — the config comment explains that six
of them once shipped switched off, and the capabilities behind three were not named by the manifest at
all, *"so the workflow ran extraction, synthesis and validation with no consolidation stage between
them. That is what made the output a behaviour inventory."*

### Q103. How do I know whether the output is any good?

The test suite proves the machinery; it does not prove any individual requirement is right. For that:
open `traceability_matrix.csv`, pick three rows at random, and confirm the cited file **and line
range** actually say what the requirement claims. The Requirements tab in the UI does this for you —
every claim expands to show the real source at the cited lines.

Then read `gap_analysis.md` for what was missed, and `requirement_analysis.md` for what was rejected
and why. Those two are where a thin or wrong run confesses.

### Q104. If I change a threshold, how do I know I did not make things worse?

Run `python -m tests.harness`. It measures the ground-truthed fixture corpus and prints the delta
against a frozen baseline; `--freeze` rewrites the baseline once you accept a change. Structure and
pipeline baselines are kept apart because *"the two answer different questions and move for different
reasons"* — one says what the extractors found, the other what the document made of it.

Bear in mind roughly forty thresholds are scattered across the modules and each is defended in a
comment where it lives; there is no single table of them yet.

---

## M. What reaches the model, and why not all of it

Section B explains why a graph exists and section D explains why packets are budgeted. This section
answers the question that follows from both: *given that we built a graph of the whole repository,
how much of it actually reaches a model, and what would it take to send all of it?*

Every number below is measured from the stored run `runs/2584e3a7-…` — a 381-file, two-language
repository with 5,084 graph entities — not estimated.

### Q105. How is the knowledge graph actually built, step by step?

Four steps, three of them deterministic.

**1. `ingest` → `inventory`.** Walks the source root and records every file with its path,
extension, language, size, `sha256` and a `parseable` flag (`capabilities/ingest.py`). It halts
rather than continuing when there is nothing analysable, *"rather than fabricating a document from
file names."* Note what this step does **not** do: it lists `schema.sql`, `.env.example`,
`docker-compose.yml`, `requirements.txt` and `Dockerfile` — and nothing ever opens them. See Q111.

**2. `graphify_extract` → `graphify_raw`.** Shells out to the `graphify` binary
(`capabilities/graphify.py:graphify_extract`), which parses 36 languages with local tree-sitter
grammars — no API calls, no second model vendor, fully reproducible. Three flags matter:

```
--code-only       structure only; document/PDF concept extraction is a later increment
--no-cluster      Graphify's own Leiden community pass is off, because this pipeline
                  partitions the graph itself around interaction boundaries, and two
                  competing partitions would both try to feed the evidence packets
--no-gitignore    an uploaded snapshot often carries a .gitignore from a different
                  checkout layout, which would silently exclude the source under analysis
```

Failure **halts** the run: under a full swap Graphify is the only extractor, so continuing *"would
publish a document built on nothing and report it as success."*

**3. `adapt_graph` → `graph`.** The largest deterministic module in the project (~800 lines) and the
one that does the real work, because **Graphify emits structure, not intent**: no `kind` field, no
line ranges, and nothing that marks a route, a command or a schema. The adapter therefore has to:

- **Classify every node's kind** (`classify_kind`). Ordered most-certain-first because signals
  overlap. Two hard-won details live here: the `()` label suffix is checked *before* Graphify's own
  `_callable` flag, because the Go grammar does not mark a `func` as callable and a Go repository
  classified on that flag alone *"lands every function under SYMBOL, is excluded from call
  traversal, and produces an empty document"*; and file nodes are matched by **path suffix** rather
  than basename, because any repository with a repeated `__init__.py` or `index.ts` otherwise lost
  every file node to `SYMBOL`.
- **Reconstruct line ranges.** Graphify reports a start line and never an end, so `line_end` is
  estimated — 4,076 of 5,084 entities in this run carry `line_end_estimated`.
- **Attach docstrings** (added 2026-09-18; see `changes/add_semantics_to_graph.md`). Reads the full
  comment block back from source and hangs it on the entity it describes. 923 entities in this run
  carry a `docstring`. This is currently the *only* semantic content in the graph.

**4. Boundary discovery folds classifications back in.** `propose_boundary_candidates` ranks nodes
worth asking about from graph shape alone, `classify_boundaries` asks a model to name them (fanned
out one call per batch of 120 since 2026-09-18 — see `changes/batch_boundary_classification.md`),
`collect_boundary_assignments` reduces the replies, and `discover_entry_points` writes them back as
`ENDPOINT`, `REGISTERED_ENTRY`, `UI_ACTION` and `SCRIPT_ENTRY` nodes.

What comes out, for this repository:

```
FUNCTION         1781      ENDPOINT            157
RATIONALE         928      REGISTERED_ENTRY    109
CLASS             450      EXTERNAL_SYMBOL     256
SYMBOL            417      UI_ACTION            26
MODULE_SURFACE    406      SCRIPT_ENTRY          1
FILE              381      METHOD              172
                                    total: 5,084 entities / 11,813 relationships

CALLS 3681   CONTAINS 2769   IMPORTS 2658   REFERENCES 1200
EXPLAINS 928   EXPOSES 293   INHERITS 174   REGISTERS 59   RELATED_TO 50   TRIGGERS 1
```

**Read that list carefully: it is a call graph.** Who calls whom, who contains whom, who imports
whom. That is the single most important fact about what the model later has to work with.

### Q106. What exactly are we passing to the model, and what never reaches it?

One **evidence packet** per cluster, and nothing else — no source text, by design (Q29). A packet is
a list of records, each one node plus its outgoing workflow edges. Verbatim, from this run:

```json
{"n": {"k":"CLASS", "q":"…/services/chatbot.ts::ChatResult",
       "at":"…/services/chatbot.ts:L3-16:ChatResult",
       "p":"EXTRACTED", "c":1.0, "a":{}},
 "out": [["EXPOSES","./api","INFERRED",0.55,"…:L3-16:ChatResult"]]}
```

`k` kind, `q` qualified name, `at` file:lines:symbol, `p` provenance, `c` confidence, `a`
attributes. Each `out` entry is `[edge kind, target, provenance, confidence, evidence location]`.

Measured, across all 84 clusters of this run:

```
distinct entities reaching any packet      634  of 5,084   =  12.5%
median packet                              9 records / 3,343 chars
largest packet                             67 records / 39,948 chars
packet budget (evidence_packet_chars)      40,000 chars
average packet                             8,103 chars  →  20% of budget used
packets reporting omissions                0 of 84
```

Two filters decide the 12.5%, both in `capabilities/graph_ops.py`:

```python
NOISE_KINDS = {"IMPORTED_SYMBOL", "EXTERNAL_SYMBOL"}                    # line 55
WORKFLOW_EDGE_KINDS = {"CALLS","ENFORCES","PERFORMS","HANDLES",
                       "READS_CONFIG","EXPOSES","TRIGGERS","REGISTERS"} # line 45
```

So four kinds **never reach any packet** in this run — all 381 `FILE`, all 417 `SYMBOL`, all 928
`RATIONALE`, all 256 `EXTERNAL_SYMBOL` — and `IMPORTS`, `CONTAINS`, `REFERENCES` and `INHERITS` are
never walked. Both exclusions were correct for a workflow traversal: *"Import and containment edges
connect almost everything to everything and would make every cluster the whole graph."* `RATIONALE`
is now genuinely redundant, since the docstring change copies its text onto the entity it describes.
`EXTERNAL_SYMBOL` is a different matter — see Q109.

The other constraint is that clustering **seeds only from interaction boundaries**
(`build_boundary_clusters`), so anything no boundary reaches is invisible unless
`declaration_groups` picks it up. That second path exists and does not currently fire — Q111.

### Q107. How is the graph helping us, concretely?

Section B/Q8 gives the four designed reasons. What is worth recording here is which of them the
graph is **actually delivering today**:

| Designed property | Delivered? |
|---|---|
| Cost linear in interesting places, not repository size | **Yes.** 84 bounded calls for a 381-file repo; the same repo at 5× the files with the same endpoint count costs roughly the same. |
| Fabrication detectable by construction | **Yes.** Every citation is re-resolved against the real file system in Gate 2 (`capabilities/verify.py:verify_location`). This is the property the whole design rests on and it holds. |
| "Reachable from outside" answerable at all | **Yes.** `boundary_downstream()` answers it by walking edges; no amount of file reading would. |
| Stable contract that let extraction be replaced | **Yes, and proven.** The Python-only AST extractors were swapped for Graphify and nothing downstream of `plan_clusters` changed. |
| **Enough semantic content to write a full requirements document** | **No.** This is the gap. |

That last row is the honest one. The graph makes the document *trustworthy*; it does not currently
make it *complete*. Across all 20 stored runs:

```
                              populated in
functional_requirements          19/20
business_requirements            13/20
system_requirements               5/20
out_of_scope, open_questions      3/20
──────────────────────────────────────────
business_rules                    0/20
success_criteria                  0/20
technical_constraints             0/20
data_requirements                 0/20
integration_requirements          0/20
non_functional_requirements       0/20
reporting_requirements            0/20
assumptions                       0/20
```

Seven requirement families have **never** been produced — not sparse, never. And the synthesis step
returns 1.42 findings per cluster against its own stated ceiling of 34 (14 functional + 12 rules +
8 criteria), so it is running at roughly 4% of designed yield.

The cause is not that the prompt fails to ask. `pack.yaml` lines 305–320 ask for all four missing
families with good definitions (*"a timeout, a size cap, a retry bound, a concurrency guard, an
authorisation gate, a masked secret"*). The cause is that a packet of nine records with `"a":{}` and
no source text **cannot contain a timeout value**, and the master skill correctly forbids guessing
one. The 0/20 is the system being honest, not broken.

### Q108. What prevents us passing the whole graph to the model?

Three things, in order of how absolute they are.

**1. It does not fit, by a factor of four.** Serialising every entity of this graph at current
packet density:

```
4,828 records · 1,871,936 chars · ≈535,000 input tokens
```

The Azure deployment is a 128k-context model (`llm/client.py`). The largest stored run is ~4× this
repository, so ~2M tokens. There is no window this fits in, and no near-term window that fits the
next repository up.

**2. The architecture exists specifically to prevent it.** `llm/prompt.py:127`, on the `graph`
artifact renderer: *"The whole graph must never enter a prompt — that is precisely the cost failure
this architecture exists to prevent. Detailed evidence arrives through cluster packets instead."*
Sending everything would also destroy the fan-out parallelism and make every run's cost a function
of repository size rather than of interaction surface.

**3. A single flat dump is worse evidence even where it fits.** A model given 5,000 unordered nodes
has no signal about which are entry points, what calls what, or which subgraph is one workflow.
Packets are ordered breadth-first from a boundary precisely so that structure is legible. This is
why *"the whole graph in one call"* is not simply a bigger version of the current design — it is a
worse one.

**But note what is *not* on this list: cost.** See Q109.

### Q109. If every node did reach the model, what would improve — and what would it cost?

The right question is not "can the whole graph fit in one call" (no, Q108) but "**can every node
reach some call**" — the same distinction `changes/batch_boundary_classification.md` drew one layer
down, where the fix for a truncating cap was to partition rather than to select.

Measured, for full coverage of every meaningful node (dropping `RATIONALE` as redundant and
`SYMBOL` as low-value):

```
                         today          full coverage
records reaching a model    634 / 5,084    3,483 / 3,483
LLM calls                    84             ~39
chars carried             680,660        1,553,276
avg budget utilisation       20%            ~100%
```

**Full coverage is cheaper than what we do today: ~39 well-packed calls instead of 84
quarter-empty ones.** 2.3× the evidence for roughly half the calls. The 40,000-char budget has never
been the binding constraint — zero of 84 packets reported an omission. The constraint is the
traversal: 84 boundaries each walk a short distance along `WORKFLOW_EDGE_KINDS` and dead-end.

What would improve, in order of expected effect:

1. **`integration_requirements` become possible at all.** `EXTERNAL_SYMBOL` is the only
   external-system signal the graph carries, and all 256 of them are filtered out by `NOISE_KINDS`.
   The prompt asks for *"one per external system this cluster depends on"* while every node that
   names one is deleted before the packet is built. The fix is not to make them walkable — that
   would make every cluster the whole graph, exactly as the comment warns — but to attach a
   deduplicated `depends_on` list to each packet as a fact rather than a node.
2. **Non-boundary machinery gets described.** Background jobs, scheduled tasks, migrations, admin
   scripts and data pipelines are reachable from no boundary and therefore produce nothing today.
3. **Declaration files get read.** Which is where the remaining six empty families live — Q111.

And the drawbacks, honestly:

- **Consolidation has never run at that volume.** 2.3× the evidence means more findings, and
  `consolidate` / `resolve` / `score_confidence` are tuned against ~119 findings, not ~300. Expect
  the first full-coverage run to look noisier before it looks deeper.
- **A packet with no boundary needs different instructions.** The synthesis prompt's whole frame is
  *"work outward from the boundary"*, which is meaningless for a residual cluster. That is a new
  section in the synthesis skill, not new code — but it is not free.
- **Nothing currently measures completeness**, so there is no baseline to prove the change helped.
  `graph_nodes_covered` is already emitted as a metric by `plan_clusters` and nothing reads it.

### Q110. How do people solve this in practice today?

The problem — a structured corpus far larger than any context window, where an answer needs global
coverage — is well-trodden. The established approaches, and where this project sits against each:

**1. Graph partitioning plus map-reduce over the parts.** Split the graph into communities (Leiden,
Louvain), summarise each part independently, then reduce the summaries. This is the core of
Microsoft's GraphRAG and of most "global question over a large corpus" designs. **This project
already does this**, and does it better than a generic community pass: it partitions around
interaction boundaries, which is semantically meaningful for the question being asked, and it is
explicitly why `--no-cluster` disables Graphify's own Leiden pass. The gap is only that the
partition does not cover the whole graph.

**2. Hierarchical summarisation.** Summarise leaves, then summarise the summaries, so the top level
sees the whole system at low resolution and detail is fetched on demand. **This project already has
it** as the `synthesize` → `cluster_candidates` → `digest_areas` → `elevate` ladder; `area_digest`
is deliberately *"titles only"* so the top tier sees every area without seeing every finding.

**3. Retrieval instead of enumeration.** Embed nodes, retrieve the top-k relevant to each question,
and accept that you never see the rest. This is the mainstream RAG answer and it is **the wrong fit
here**, because the deliverable is a *complete* requirements document — "the relevant 5%" is exactly
the failure mode being fixed. Retrieval solves "answer this question"; it does not solve "enumerate
everything this system does."

**4. Agentic traversal — give the model tools, not a dump.** Rather than serialising the graph,
expose `neighbours(node)`, `callers(node)`, `read_lines(file, a, b)` and let the model walk where it
decides to. This is the direction most code-understanding agents have moved (and how Claude Code
itself works on a repository). Strengths: unbounded effective context, and the model pulls exactly
the detail it needs. Costs: many round trips per cluster, non-deterministic coverage, and — decisive
for this project — **it breaks the fabrication guarantee**, because the model chooses what it sees
and there is no fixed evidence set to check a citation against. Worth considering as an *opt-in
deepening pass* on clusters flagged thin, never as the default path.

**5. Deterministic code intelligence instead of a model.** Sourcegraph's SCIP/LSIF indexes, Meta's
Glean, CodeQL, Semgrep and tree-sitter stacks all answer structural questions — what calls this,
what implements that, where is this config key read — precisely, cheaply and with no context window
at all. **The lesson this project should take is that the answer to "the graph is too big" is often
"extract more, ask the model less."** Every fact moved from a model step into a deterministic
extractor is a fact that costs nothing, never hallucinates, and does not consume context. That is
exactly what Q111 describes.

**6. Prompt caching and context compression.** Shared prefixes cached across fan-out calls (for this
project, the ~5,600-token system prompt is byte-identical across every worker and sits at the
prefix, so it is a textbook candidate), plus schema-level compaction — which `compact_entity`
already does: *"the packet is evidence, not a copy of the source."* Worth noting nothing in `src/`
declares caching explicitly (`grep` finds no `cache_control`), so whether Azure's automatic prefix
caching is actually being hit is currently unverified and unmeasured.

**7. Bigger windows.** Real, and not the answer. Window growth has been roughly linear while
codebases are unbounded; the 2M-token graph above would still not fit, cost would scale with
repository size rather than interaction surface, and recall degrades in the middle of very long
contexts. Every serious system still partitions.

The short version: **the industry answer is partition + hierarchy + deterministic extraction, and
this project has the first two already.** What it is missing is the third.

### Q111. So what is the actual bottleneck — the graph, the packets, or the prompt?

None of the three. It is **one missing producer**, and the evidence for that is unusually clean.

`capabilities/graph_ops.py:570`, `declaration_groups()` — a mechanism built for exactly the problem
of declarations no call path reaches:

> A packet is grown outwards from a boundary, so evidence is only ever seen if something calls it.
> That is the right question for behaviour and the wrong one for a declaration: a schema states the
> values a column may take whether or not a handler happens to touch it, and a README sentence
> stating a limit is not called by anything at all. **Such evidence was extracted and then dropped
> on the floor.**

It is fully wired: called from `plan_clusters`, budgeted separately at `max_clusters // 4` (with a
long comment explaining why pooling the two budgets is wrong), switchable via
`declaration_clusters: bool = True`, ranked richest-file-first to avoid the alphabetical-truncation
defect, and reported in both `messages` and `metrics`. Then, at line 41:

```python
DECLARATION_KINDS = {"DOMAIN_POLICY", "DATA_ENTITY", "CONFIG_KEY"}
```

**No producer emits any of those three kinds.** So the ranking key
`-sum(1 for e in entities if e.kind in DECLARATION_KINDS)` is always zero, the sort collapses to the
file path, and the comment three lines above it explains that path-ordering *is* the exact defect
`_stratified` exists to correct. The mechanism is live, correct, and inert.

The same is true one layer up. `compact_entity` carries hand-written attribute whitelists for seven
kinds nothing emits:

```
BUSINESS_RULE   predicate, when_true, status_code, allowed_values, constraint, table…
CONFIG_KEY      source, value, secret_like
DATA_ENTITY     description, declares
DATA_FIELD      table, type, not_null, default, references
DATA_INDEX      table, columns, unique, where
ERROR_HANDLER   exception, behavior
DATA_OPERATION  operation, call
```

`render.py` renders them, `gates.py` checks them, `claims.py` reads them. They are orphaned
contracts from the pre-Graphify AST extractors: the swap bought 36 languages and paid with every
semantic node kind, and nothing downstream was told. Those seven kinds map one-to-one onto the seven
empty requirement families.

And the inputs are already on disk and already inventoried. For the run measured throughout this
section, `inventory.json` lists `app/db/schema.sql`, `app/models/schemas.py`, `.env.example`,
`docker-compose.yml`, `requirements.txt`, `package.json` and three `Dockerfile`s — every one of
them hashed, recorded, and never opened by any step.

So the ranked fix is:

1. **Add `extract_declarations`**, a deterministic capability between `adapt_graph` and
   `propose_boundaries`, emitting `CONFIG_KEY` from `.env*`/YAML/TOML/INI, `DATA_ENTITY`/`DATA_FIELD`/
   `DATA_INDEX` from DDL, migrations and ORM models, and integration facts from dependency
   manifests. No model call, no tree-sitter. This turns on three subsystems that already exist.
   Start with `CONFIG_KEY` alone — smallest parser, and the sole evidence class for the most
   conspicuous gap.
2. **Attach `depends_on` to packets** from `EXTERNAL_SYMBOL`, without making those nodes walkable.
3. **Add a residual clustering pass** so every meaningful node reaches some packet, using the 80% of
   the packet budget currently going unspent (Q109).
4. **Make completeness a gate.** Per requirement family, in `final_check`: was anything produced,
   and if not, was it because no evidence existed or because no extractor can produce that
   evidence? That distinction is what would have surfaced all of the above on run 1 instead of run
   20.

One line is worth checking before any of it, because it is nearly free: `pack.yaml:277` tells the
synthesis worker *"set `requirement_type` — `functional` or `business_rule`, **the two tiers
available to you here**"*, while the same prompt asks for seven further families and states that
this field *"is what gets published."* A model obeying that sentence literally cannot emit anything
else — which matches the 19/20 vs 0/20 split almost exactly. It is unlikely to be the whole cause,
given the evidence starvation described above, but it is thirty minutes to rule in or out.

---

## The five answers worth remembering

1. **The graph exists so that cost is bounded and fabrication is detectable.** Both properties come
   from the same decision: the model only ever sees facts that came out of a structure you can
   re-check.
2. **Let the model classify; never let it locate, route, or re-emit.** Every time that line was
   crossed, the code records what broke.
3. **If a step's output must grow with its input, it is not a model step.** Copying is deterministic
   work.
4. **Over-extraction is a taxonomy problem.** Give "not a requirement" a destination and it stops
   being the path of least resistance.
5. **A rejection over wording costs a wording, not a requirement.** And a claim that cannot be
   published is refiled with its reason, never deleted.
