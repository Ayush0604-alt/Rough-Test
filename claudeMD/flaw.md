# The flaw, and what changed because of it

A design history of the five capability areas documented in [capabilities/](capabilities/), written
at the level of *what each stage is for* rather than what it is called. For each: what the earlier
version did, what the current version does instead, and what that swap changes about the design.

The five areas, in pipeline order:

| Area | Doc | State |
|---|---|---|
| Ingestion | [ingestion.md](capabilities/ingestion.md) | **Unchanged.** Its recorded flaws are still open. |
| Structural extraction | [graphify.md](capabilities/graphify.md) | Changed — intent now reaches the graph |
| Boundary proposal and classification | [boundary_candidates.md](capabilities/boundary_candidates.md) | Changed — batched instead of truncated |
| Declared policy | [declarations.md](capabilities/declarations.md) | **New** — the missing producer |
| Product modules | [modules.md](capabilities/modules.md) | **New** — the missing altitude |

---

## 0. The one flaw underneath all of them

Read the five documents together and the same failure appears in four different costumes. Every
one of them is a variant of:

> **A stage was asked for something the pipeline had made structurally impossible to supply, and
> the honest answer — nothing — looked exactly like the answer a healthy pipeline would give.**

That is the flaw. Not a bug, not a wrong threshold: a *shape* of mistake this architecture is
unusually good at hiding, precisely because its central virtue is refusing to invent. Every stage
is instructed to return nothing rather than guess. So when a stage genuinely cannot see the
evidence it needs, it declines — correctly, quietly, and indistinguishably from a stage that looked
and found the product had nothing to declare.

The evidence that this went unnoticed is not anecdotal. Across twenty stored runs, seven of the
thirteen requirement families the document is designed to publish had **never once** been
populated. Not thinly — never. Business rules, success criteria, technical constraints, data
requirements, integration requirements, quality requirements, reporting requirements: zero, twenty
times out of twenty. Meanwhile every requirement that *was* published was individually
well-evidenced and passed every check, because the checks measured whether each claim was true and
nothing measured whether the document was complete.

The four costumes:

1. **Consumers without a producer.** Six kinds of evidence were read by four different downstream
   stages and emitted by none. The stages that read them had been written for an earlier extractor
   and were never told it had gone.
2. **Selection instead of partition.** Three separate stages ranked a list, kept a prefix, and
   discarded the rest — so on a large repository the discarded portion was the majority.
3. **A missing altitude.** Two analysis levels existed where three were needed, and the level in
   between did bookkeeping rather than thinking.
4. **An instruction that contradicted itself.** A stage was asked for ten kinds of finding and told
   in the same breath that only two of them could be returned.

Each section below is one of these, in the area where it lived.

---

## 1. Ingestion — unchanged, and its flaws are still open

**What it does.** Takes a directory or an uploaded archive, validates every archive entry before
anything touches disk, then walks the tree once: classify each file by language, hash it, decide
whether the run has anything worth analysing at all. If not, it stops with a stated reason rather
than letting a later stage manufacture a document out of filenames.

**What changed: nothing.** This is worth stating plainly rather than leaving as an absence. The
documented weaknesses are all still present — the eligible-language list covers fourteen of the
roughly thirty-six the extractor can actually parse, so a repository written only in Swift or Dart
is turned away at the door by the one stage that had no reason to; the tree walk descends into
dependency and build directories before filtering them out; hashing is sequential; a success
message still describes a Python-only world.

**But its role in the flaw changed.** Ingestion was always the stage that *listed* the schema file,
the settings file and the dependency manifest — recording each one's path, language and hash. For
the entire life of the pipeline, **nothing ever opened them.** They were inventoried and ignored.
That is not an ingestion bug; ingestion did its job. It is the clearest single illustration of
costume 1: the evidence was on disk, catalogued, and unread, because the stage that would have read
it had been removed and nothing noticed.

---

## 2. Structural extraction — from connections to connections-plus-intent

**Earlier version.** The extractor answers one question across many languages: *what connects to
what.* It reads declarations and calls and produces a graph of them. It does not mark a route, a
command, a schema or a limit, and it reports where a definition starts but not where it ends. The
adapting stage exists to turn that into the canonical shape everything downstream depends on:
classifying each node's kind (since none is given), reconstructing the end of every definition, and
keeping the contract stable enough that swapping the extractor was invisible past that one stage.

It also found every docstring and marked comment in the codebase — and did three things with each
that added up to losing it:

- kept only the first ~80 characters, the rest remaining solely in the source file;
- put that fragment on a node of its own rather than on the thing it described;
- connected it with a relationship the workflow traversal does not follow, pointing in the
  direction the traversal does not go.

On one measured run: 928 such nodes extracted, **none** reaching any evidence packet. Of the code
records that did reach packets, 61% had a docstring in the source and **none** carried it.

Meanwhile the stage that trims a node down for a packet already made room for docstring text, and
the stage that checks whether a written requirement corresponds to its code already read it —
with a comment explaining that leaving it out *"made the anchoring check reject claims that quoted
the function's own words."* Both had been written for the old extractor. Both read nothing for the
life of the new one. **Costume 1, in its purest form.**

**Current version.** The adapting stage now goes back to the source file and reads the *whole*
comment block, cleans it, and attaches it to the entity it describes along with the exact line
range it came from. The separate nodes and their relationships are untouched — nothing that
depended on them changed. The text is simply also placed where the rest of the pipeline was already
looking for it.

**What this changes about the design.** Before, the graph was a pure structural artifact: it knew
*how* the code connects and nothing about *why* any of it exists. Intent had to be reverse-engineered
by a model from identifiers and control flow. After, the graph carries — deterministically, with
every word traceable to a line, and with no model call — the developers' own statement of purpose
*beside* the code that implements it. That is the exact evidence pairing the synthesis stage asks
for and previously could never be given.

The deeper design shift is about where semantics are allowed to come from. The pipeline's
governing rule is that the model classifies and never locates or invents. Attaching intent
deterministically means the *meaning* in the graph is now as checkable as the structure: a claim
resting on a docstring cites a line range that can be re-read. Had this been done by asking a model
to summarise, the same words would have entered the document unverifiable.

The honest limit: a docstring is a claim by its author, not a fact about behaviour. The synthesis
instruction now says in as many words that where prose and code disagree, the code wins and the
disagreement is stated. That is a soft guard, not a mechanism, and it is the one place this change
trades a little rigour for a lot of reach.

---

## 3. Boundary proposal and classification — from a cap to a partition

**Earlier version.** Finding the points where a system is consumed from outside used to be
syntactic: a parser recognised a route decorator, a main guard, a command registration. The
replacement extractor emits no such marks, so the signal had to become structural — *nothing in
this repository calls it* is a property a route handler, a scheduled job and a UI callback all
share, in any framework, including frameworks nobody has heard of. That stage ranks every node
worth asking about, attaches the shape evidence a human reader would use, and hands the judgement
to a model.

Then it kept the first 120 and threw the rest away.

On the largest stored run that meant **1,868 of 1,988 candidates — 94% — were never shown to the
classifier at all.** And the loss was not random: because the ranking was categorical, 147
state-changing endpoints sorted behind reads and lost every tiebreak together. A whole class of
candidate could be asked about zero times.

**Current version.** The same ranking now decides *which batch* a candidate lands in rather than
whether it survives. The list is cut into batches and the classification stage fans out one model
call per batch — the mechanism the synthesis stage already used per cluster — with a reduce step
folding the replies back into exactly the shape the next stage always consumed, so nothing
downstream knows anything changed.

**What this changes about the design.** Two alternatives were considered and rejected, and the
reasoning is the valuable part. Raising the cap moves the cliff without removing it. Choosing a
*better* 120 by stratified sampling fixes the categorical skew but still discards real candidates.
Batching is stronger than both, and the reason is categorical rather than quantitative:

> When nothing is selected out, the failure mode "an entire class of candidate loses every tiebreak
> and is never asked about" cannot occur — not *less often*, but structurally cannot, because there
> is no selection step left to be unfair.

That sentence is the design principle this change introduced, and the two changes in the sections
below are both applications of it. The ranking survives, but demoted from gatekeeper to scheduler:
it now only decides what a partial or early-failing run gets through first.

The costs are real and were accepted knowingly. Classification cost now scales with repository size
instead of being capped — roughly seventeen model calls where there was one, on that run. Loss under
partial failure becomes stochastic and partial rather than deterministic and documented: a batch
that fails takes its candidates with it, quietly, via a warning. And because batches are contiguous
slices of a ranked list they are *homogeneous* rather than representative, so the proportion of real
boundaries differs sharply from the first batch to the last while the instruction telling the model
"most candidates are not boundaries" stays the same. The stratifier's other virtue — mixing
categories so no group is concentrated — was not carried across to batch composition. That was a
choice for priority over homogeneity, and it is not recorded anywhere as one.

---

## 4. Declared policy — the producer that was never rebuilt

**Earlier version.** There was none, and that absence had a shape.

When the extractor was swapped, a set of adapters went with it: the ones that read a schema, a
settings file and project documentation into nodes representing stored collections, configuration
values and stated policy. The new extractor answers *who calls whom* and carries no declarations at
all. The stages downstream were never told.

So the pipeline arrived at a state that is worth describing carefully, because it is the flaw at
full strength:

- The stage that trims nodes for a packet carried hand-written rules for six kinds of declaration
  node, listing exactly which attributes of each were worth keeping.
- The stage that renders the document had sections for them.
- The structural gate checked for them.
- And a mechanism existed specifically to give declarations their own evidence packets — because,
  as its own description puts it, a packet grown outward from a boundary only ever sees what
  something calls, which *"is the right question for behaviour and the wrong one for a
  declaration: a schema states the values a column may take whether or not a handler happens to
  touch it… Such evidence was extracted and then dropped on the floor."*

That mechanism was fully wired: invoked, given its own budget, switchable, reported in metrics, and
ranked so that the files declaring the most were read first. **It ranked every file at zero**,
because the ranking counted node kinds nothing produced. With the count constant, the ordering
collapsed to alphabetical file path — which is precisely the truncation defect the stratified
sampler exists to correct, reintroduced not by a bad comparator but by an empty set.

A live, correct, carefully-reasoned mechanism, running on nothing, for the life of the new
extractor.

**Current version.** A new deterministic stage between adaptation and boundary proposal reads
declared policy off the lines that state it: configuration keys and their values from settings and
environment files; stored collections, their columns and the guarantees attached to them from
schema and migration files; enumerated vocabularies from schema constraints; and external systems
from dependency manifests. No model call, no grammar — a line that says `TIMEOUT=30` is fully
described by reading it.

On the reference repository this produced 633 nodes from files that were already in the inventory
and had never been opened, including policy that no call graph can express:

```
user role         accepts only admin, recruiter, sysadmin
review state      accepts only open, resolved, discarded, approved
archive threshold 30 days
```

On a held-out fixture deliberately kept outside the tuning corpus — a declaration-style web
application with no conventional definitions for the extractor to key on — the graph went from *two
files and nothing else* to four configured limits, two collections, six columns and a vocabulary,
with every citation still resolving against the real file system.

**What this changes about the design.** Three things, in increasing order of importance.

First, seven requirement families become *possible*. They were being asked for; the prompts
describe them well. There was simply no evidence class in the graph that could ground one, and the
no-invention rule correctly forbade guessing. Twenty empty runs were the system being honest.

Second, and more interesting: **it changes where authority comes from.** A constraint declared in a
schema is enforced by the database whatever the application code does. It is therefore *stronger*
evidence than any conditional a model might find in a handler — and it is the one kind of business
rule the business actually wrote down. Before this stage, the document's strongest available
evidence was inferred behaviour. Now the pipeline can cite policy at its source.

Third, it re-establishes a principle the extractor swap had quietly abandoned: that the answer to
"the model cannot see enough" is often "extract more deterministically" rather than "show the model
more". Every fact moved out of a model step and into a deterministic reader is a fact that costs no
tokens, cannot be hallucinated, and does not consume context. That is the direction the wider
industry took for code intelligence, and this pipeline had drifted away from it by accident rather
than by decision.

The limits are worth stating as plainly as the gains. The readers are a long tail: nested
configuration is deliberately not walked, object-model and newer schema formats are not read yet,
and unusual value shapes are dropped rather than guessed. A declared value is also not a *used*
value — nothing here proves any code reads it — so a quality requirement resting on one asserts
declared policy, not observed behaviour. And each miss is an absence, which is exactly the
indistinguishable-from-healthy failure this whole document is about; the only reason it is now
survivable is section 6.

---

## 5. Product modules — the altitude that did bookkeeping

**Earlier version.** Two analysis levels. At the bottom, one model call per interaction boundary,
each seeing a bounded neighbourhood — a median of **nine** graph nodes. At the top, one call over a
digest of the whole repository, which by deliberate design contains area names and requirement
*titles* only, because sending the full statements again is what had made the previous merge step
unaffordable.

Between them sat a stage that grouped those titles by area code. It is deterministic. It reasons
about nothing. Its own description says: *titles only, and reading them is not what the remaining
call is for.*

So **no stage anywhere looked at a part of the product as a whole** — and a whole class of true
statement had nowhere to be written:

- that every write path in one part goes through a single storage layer;
- that nothing in a part accepts a request without an identifying token;
- that a retry bound is the same across all six handlers of a part;
- that one part is the only thing that talks to a given external system.

Each is true of a part and invisible from inside any one of its boundaries. Each belongs to a
family that had never been populated.

There was a second, sharper symptom. The bottom level was instructed: *clusters covering the same
part of the product must use the same code.* **Parallel workers cannot obey that.** They run
simultaneously and none sees another's reply, so agreement was being requested where it could only
be coordinated. The result on the reference run was four separate "areas" for the same batch
subsystem and fourteen areas holding exactly one requirement each.

**Current version.** A deterministic partition groups boundaries into product modules, and a new
fan-out level analyses one module per call, above the boundaries and below the product.

The partition was chosen by measurement, and the two rejected options are as instructive as the
winner:

| Candidate | Outcome | Why rejected |
|---|---|---|
| Group by directory | One group of 1,081 nodes for the request layer, another of 1,299 for the service layer | A **layer** split, not a feature split. Describing "all the services" is a horizontal slice through every feature at once — worse evidence than a single boundary, not better. |
| Detect communities in the code graph | One community of 1,244 nodes spanning request, service, storage and extraction layers; another covering the entire frontend | A **hairball**. A layered application is densely interconnected by construction, so modularity-based clustering cannot separate features in it. |
| Group boundaries by how much of the code they reach in common | Scheduling, review queue, question bank, intake, booking, backup, notifications — each recognisable | **Features.** Adopted. |

The reason the third works is not subtle, and it is the design insight of this change: **an
interaction boundary is the product's own unit of offered function**, so boundaries that run the
same code are offering parts of the same feature. Feature structure is recoverable from behaviour
even when it is entirely absent from the directory tree — which, in a layered codebase, it is.

**What this changes about the design.**

The altitude itself is the headline, but the area-code inversion is what pays off first. Codes are
now assigned *downward* from the partition rather than invented upward by isolated workers. That is
the only form of "use the same code for the same part" a set of parallel workers can actually
follow, and it fixes the fragmentation structurally rather than by asking harder.

The module level is also the only place two kinds of evidence make sense. A boundary packet is a
walk along call-like relationships, so it can carry neither an external system (deliberately
excluded from traversal, because dependency relationships connect nearly everything to nearly
everything) nor a configuration value (which sits on no call path at all). A module can hold both.
Without them, a module worker would be a boundary worker with a wider view and nothing new to say.

Two implementation choices are worth recording because both obvious approaches were wrong. Scoping
a module's declared policy by *file* gives every module nothing, since a schema file holds no code
and therefore appears in no module's files. Scoping by *directory ancestry* gives every module the
same list, since repository-level policy is genuinely in scope for all of them — on the reference
run, twenty-one modules each shown the same sixty declarations, which is an invitation to publish
the same invariant twenty-one times and leave deduplication to clean up work that should never have
been commissioned. The resolution was to **partition the declarations too**: each goes to one
module, and one belonging to no module belongs to the repository and is described once in its own
right. Third application of the principle from section 3.

The risks are concentrated in one place. On the reference run, 191 of 293 boundaries overlapped
with nothing at the chosen similarity threshold and fell into a residual group that gets no
module-level analysis — reported, not hidden, and an honest statement that most of that
repository's boundaries are one-off behaviours. The threshold itself is measured on a single
repository and has no tuning harness. And duplication *across* altitudes is managed by instruction
rather than by construction: a module worker is told to name the boundary-level requirements it
generalises so that an empty generalisation can be collapsed, but nothing prevents one being
written.

---

## 6. Coverage and completeness — the two changes that make the rest legible

These two have no capability document of their own, which is itself a gap worth naming. They belong
to the stage that turns the graph into evidence packets and to the stage that checks the finished
document.

**Earlier version — coverage.** Packets were grown outward from interaction boundaries. That
answers *what can an outside actor cause to run*, which is exactly the right question for a
requirement and exactly the wrong one for coverage. Anything no route, command, job or control
reaches — a scheduled task, a migration, an administrative subsystem, a data pipeline — reached no
model at all.

Measured: **634 of 5,084 nodes, 12.5%**, reached any packet. And the packets that were built ran at
**20% of their character budget**, with not one of eighty-four reporting that it had to omit
anything. The packets were not trimmed; they were nearly empty, and the room to carry more was
already being paid for and left unspent. What limited the evidence reaching the model was the
traversal, not the budget — a distinction invisible unless something divides the two numbers, and
nothing did.

**Current version — coverage.** After boundary and declaration packets are built, whatever
meaningful code remains unreached is grouped by directory, ranked by connectivity, and given
packets of its own, carrying a marker that says there is no boundary above it — so the worker drops
the "work outward from the boundary" frame instead of inventing a boundary for code nothing reaches,
which the requirement gate would reject anyway. Coverage moved from 12.5% to **67.3%**.

**Earlier version — completeness.** The structural gate checks the graph. The requirement gate
checks that every surviving claim is grounded, resolving each citation against the real file system.
**Neither ever checked that the document was complete.** A document with seven families permanently
empty passed everything, because every claim in it was individually well-evidenced. The quality bar
was all precision and no recall.

**Current version — completeness.** A final reporting stage answers, per family, the question that
actually matters to a reader: is this empty because the product **declares nothing** of this kind —
an honest result, and common — or because evidence for it is sitting in the graph **unused**, which
is a defect in this pipeline and is now named as one. Reported, never enforced: a thin document is a
fact about a codebase as often as it is a bug, and deciding which is a person's job.

**What this changes about the design.** This is the change that converts the whole class of flaw
from invisible to visible. Everything above is a story about a stage declining honestly and nobody
being able to tell. This is the instrument that tells. It is also the reason the remaining
limitations — the reader long tail, the language list, the unread formats — are now *survivable*:
each one shows up as "evidence unused" or "nothing declared" rather than as an empty section
indistinguishable from a correct one.

It is worth being blunt that this should have existed first. Had a completeness report been in place
from the beginning, this document would have been written after run one rather than run twenty.

---

## 7. The pattern worth carrying forward

Four of the changes above are the same move:

| Stage | Was | Is |
|---|---|---|
| Boundary candidates | Rank, keep 120, discard the rest | Rank, batch all of them, classify every batch |
| Graph coverage | Reach outward from boundaries; the unreached are unseen | Reach outward, then group and cover the remainder |
| Module policy | Scope by ancestry; everyone sees everything | Partition; each declaration belongs to exactly one |
| Area codes | Each worker invents one, agreement requested | Assigned once from above, agreement structural |

**Selection was replaced by partition, in four different places, for one reason:** a selection step
must decide what not to look at, and that decision is made with less information than the step it is
protecting. Partition costs more calls and removes an entire category of failure, and the category
it removes is the one that cannot be detected from the output.

The second pattern is quieter and more dangerous. Three of these flaws were **live mechanisms
running on nothing** — a ranking key over kinds nobody emitted, attribute whitelists matching no
node, a grouping stage that had no analysis above it to serve. None of them errored. All of them
had thoughtful comments explaining designs that were correct and inert. The lesson is not "write
more tests"; every one of these had passing tests. It is that **a pipeline that refuses to invent
needs a measure of how much it declined**, or its silence is unreadable. That measure did not exist
until section 6.

---

## 8. Still open

- **Ingestion is untouched.** Its language list still turns away repositories the extractor could
  handle, and it is the stage with the least excuse for a false negative, since it is the one that
  ends the run.
- **The packet-building and final-check stages have no capability document.** Two of the changes
  described here live in code nobody has written a design doc for, which is how the coverage
  number went unexamined in the first place.
- **The extraction step sequence is duplicated in three test files**, only one of which derives it
  from the manifest. Every change to the pipeline's shape has had to edit all three by hand; the
  first one that forgets will silently stop testing the real pipeline.
- **The new module level has never run against a real model.** The partition, the declarations and
  the coverage numbers are measured. The module analysis stage's *output quality* is not — every
  result quoted here comes from deterministic stages and stubbed model calls.
- **Boundary batches are homogeneous, not representative**, so the base rate of real boundaries
  differs from first batch to last while the instruction does not. Unquantified.
- **Cross-altitude duplication is managed by instruction.** The consolidation stages have never run
  at the volume or across the tiers this design now produces. The first full run should be expected
  to look noisier before it looks deeper.
