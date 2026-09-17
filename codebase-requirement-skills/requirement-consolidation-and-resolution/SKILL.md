---
name: requirement-consolidation-and-resolution
description: Turn a repository-wide pile of requirement candidates into a settled requirement set — grouped by business capability rather than by source layout, deduplicated across the whole repository, freed of implementation leakage, with every conflict decided and every candidate given a disposition. Use between requirement synthesis and requirement validation, whenever candidates were produced independently by more than one pass over the code.
---

# Requirement Consolidation and Resolution

## Purpose

Synthesis answers "what does this code let someone do?" one slice of the repository at a time.
Nobody asks the next question — "taken together, what does this product require?" — and without it
the output is a **behaviour inventory**: as many requirements as the code had interesting places,
in the order they were found, each one true and the set as a whole unreadable.

This skill owns that second question. It is deliberately a separate responsibility from synthesis
(which cannot see the whole repository) and from validation (which judges claims rather than
reshaping them).

## The test that decides everything here

> **Would this statement still be true if the product were rebuilt from scratch, in a different
> language, on a different stack, by a different team?**

If yes, it is a requirement. If no, it is an *observation about the current implementation* — kept,
cited, and published where implementation detail belongs, never in the requirements list.

Apply the test to the statement, not to the code behind it. A function existing, an endpoint being
declared, a field being checked before it is read, a value being normalised, a record being written
through one library rather than another: none of these is a requirement merely because the code
does it.

## Six decisions, in order

Each one needs the whole candidate set, and each is a separate judgement. Later decisions depend on
earlier ones having been made, so the order is not arbitrary.

### 1. Group by capability, not by code location

A group is a **thing the product does for someone**. Candidates belong in the same group when they
serve the same outcome for the same actor, *wherever in the repository they were found*.

Grouping must never be read off:

- the file, folder, module, package, or class the evidence sits in;
- the entry point that happens to reach the behaviour;
- the order in which candidates were extracted;
- a fixed list of categories carried over from some other product.

Two symptoms say the grouping is really code location wearing a business name:

- **two groups with the same meaning under different names** — a product does not have two
  separately-managed halves whose headings are synonyms. Merge them.
- **one group holding unrelated outcomes** — a single control that sets off screening, routing,
  answering and record-keeping produced one group where the reader needs four. Split it.

The vocabulary of the groups comes from the product being analysed. There is no standard list.

### 2. Decide the level of every candidate

Three levels, and a level is decided by what the statement *asserts*, never by how important it
feels or which pass found it:

| Level | Asserts | Test |
|---|---|---|
| Outcome | why the product exists | survives redesign; several capabilities add up to it |
| Capability | what someone can do and observe | externally observable and separately testable |
| Rule | under what constraint | a triggering condition, a required behaviour, a consequence |

Everything that fits none of them is an implementation observation.

An **outcome is only written where the evidence supports one**: two or more distinct capabilities
must genuinely add up to it, and the statement must stay true when any one of them is rebuilt. An
outcome standing over a single capability is that capability restated more grandly — publish the
capability at the top of the chain instead and say so. Never invent an outcome to have somewhere
to hang an orphan.

### 3. Merge duplicates by meaning, not by text

Two candidates are the same requirement when their **actor, action, object and outcome** agree,
even when their wording, their evidence and their level do not. Independent passes over one
repository describe one capability from the entry point and from the work behind it *by
construction*: they cite different lines, so any rule requiring shared evidence before comparing
them will never compare them.

When merging:

- keep one canonical requirement, at the **more abstract** of the two levels;
- keep the fuller statement;
- union the evidence and the graph traces — nothing cited is lost;
- keep the acceptance criteria of both, then remove the ones that now repeat each other;
- record on the survivor what it absorbed, so a reader can see two findings were folded together
  rather than one being dropped.

A superseded candidate does not stay in the document beside its survivor. One requirement, one
entry.

Watch for the case a pairwise merge cannot reach: many statements too distinct to be duplicates
yet **identical in shape** — the same sentence with one subject changed. A dozen of those are one
requirement written a dozen times; collapse them into one that names every subject.

### 4. Settle conflicts against the evidence

Different passes reading the same evidence can reach incompatible conclusions. Publishing both is
the worst available answer: the document then contradicts itself and blames the reader.

Compare each reading against the cited evidence and decide:

- **keep** the best-supported reading;
- **merge** readings that are compatible and partial;
- **downgrade** a reading the evidence only weakly supports;
- **record as an open question** a case the evidence genuinely cannot settle.

Never publish a reading the evidence does not support as a finalised requirement.

### 5. Keep the implementation out of the statement

The requirement says what must be true. The citation says where that was read. They are different
fields and the detail belongs in the second one.

Strip from a statement, and from every acceptance criterion: route paths and verbs, function,
class, module and variable names, framework, library and datastore names, storage mechanics,
serialisation formats, and the names of controls on a screen. What is stripped is **recorded as an
implementation observation on the same requirement**, never deleted, so the detail stays traceable.

Which words those are is a property of the repository being analysed, not of a fixed list: a word
that is machinery in one product is the domain's own vocabulary in another. Ask the code what its
own machinery is called.

Where the interface *is* the requirement — an integration the product depends on, a contract another
system codes against — say so in a requirement whose subject is the interface, and keep it out of
the business and functional statements.

### 6. Give every candidate a disposition

Detection is not resolution. Every candidate leaves this stage with exactly one decision recorded
against it:

| Disposition | Meaning |
|---|---|
| keep | published as it stands |
| reword | published, with implementation detail moved to the observations |
| merge | folded into a canonical requirement, which names it |
| reclassify | published, at a different level than it claimed |
| observation | retained for traceability, not published as a requirement |
| open question | the evidence cannot settle it; published in the analysis artifact |
| reject | contradicted by its own evidence |

**The requirements document holds requirements.** Rejected candidates, unresolved questions,
duplicate clusters and implementation observations go to a companion analysis artifact, with their
evidence and the reason. Nothing is deleted; nothing that was not settled is published as though it
were.

## Acceptance criteria come last

Write them only after the requirement set is final. A criterion attached to a candidate that is
about to be merged, reclassified or dropped either disappears with it or follows it onto a
requirement it was never about.

A criterion must:

- describe an **observable outcome** — true or false after a described action, checkable by someone
  who has never seen the code;
- use only evidence that belongs to the **final** requirement it hangs from;
- cover the success path, and where the evidence fixes them, the refusal, error and boundary
  behaviour too;
- not restate the requirement — a criterion that cannot be failed checks nothing;
- not name the implementation, held to exactly the same bar as the statement.

Where no observable condition can be read off the evidence, record an open question instead of
writing a criterion that says nothing.

## Identifiers

Stamp identifiers **after** consolidation, never before. An id assigned to a candidate that is
later merged, reclassified or dropped either vanishes from the document or, worse, is silently
reused by whatever ends up in that position. Ids are the reader's handle on the finished set, so
they are the last thing decided.

## What must be true when this stage finishes

- Every published requirement is at the right level, and every level a reader sees is the level the
  item claims.
- No two published requirements assert the same thing.
- Every parent link names a requirement that exists and a relation the two levels can hold; no
  cycles; no relation without a parent.
- No requirement statement names the implementation.
- Every acceptance criterion belongs to the requirement it hangs from.
- Nothing rejected, unresolved, or classified as an observation appears in the requirements list.
- Every candidate that left the document is recorded somewhere a reviewer can find it, with the
  reason.

## What this skill must never do

- Reduce the requirement count to make the document look tidier. Consolidation removes
  *duplication*, not coverage.
- Discard evidence. A candidate that is not a requirement is refiled, never deleted.
- Invent a grouping, an outcome, or a hierarchy that the evidence does not support. An honest gap
  is reported; a guessed structure is a false statement about the product.
