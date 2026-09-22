# `declarations.py` — Phase 1b(ii): declared policy, the evidence a call graph cannot carry

> **Added 2026-09-22.** Six node kinds — `CONFIG_KEY`, `DATA_ENTITY`, `DATA_FIELD`, `DATA_INDEX`,
> `DOMAIN_POLICY`, `EXTERNAL_SERVICE` — were read by `compact_entity`, `render.py`, `gates.py` and
> `graph_ops.declaration_groups`, and **emitted by nothing**. They were orphaned when the
> Python-only AST extractors were replaced by Graphify, which buys 36 languages and carries no
> declarations. This module is the missing producer. The change itself — measurements, diff, and
> the twenty stored runs that showed the failure — is in
> [changes/declared_policy_and_module_tier.md](../changes/declared_policy_and_module_tier.md).

Module: [src/agentic/capabilities/declarations.py](../../src/agentic/capabilities/declarations.py)
Registered capability: `extract_declarations` (step `extract_declarations`,
`on_fail: warn_and_continue`)
Setting: `declaration_extraction: bool = True` — off reproduces the previous graph exactly
Consumers: `compact_entity` (packet attributes), `declaration_groups` (cluster seeds),
`partition_modules` (`declared_policy` per module), `external_dependencies` (`depends_on`),
`render.py`, `gates.py`, `report_family_coverage`
Pack: [codebase-requirement-skills/pack.yaml](../../codebase-requirement-skills/pack.yaml) — Phase 1b(ii)

---

## 1. Why this exists

Graphify answers **who calls whom**. That is the right question for behaviour and the wrong one for
policy, because a codebase states most of its policy in files nothing calls:

| Where policy lives | What it states | Reachable by a call graph? |
|---|---|---|
| `schema.sql`, migrations | what values a column may hold, what must be unique | No |
| `.env`, `settings.yml` | a timeout, a size cap, a retry bound, a feature flag | No |
| `requirements.txt`, `package.json` | which external systems the product depends on | No |

`graph_ops.declaration_groups` was written for exactly this problem. Its docstring is explicit:

> A packet is grown outwards from a boundary, so evidence is only ever seen if something calls it.
> That is the right question for behaviour and the wrong one for a declaration: a schema states the
> values a column may take whether or not a handler happens to touch it… **Such evidence was
> extracted and then dropped on the floor.**

It is fully wired — called from `plan_clusters`, budgeted separately, switchable, ranked
richest-file-first, reported in metrics. And it ranked every file at **zero**, because:

```python
DECLARATION_KINDS = {"DOMAIN_POLICY", "DATA_ENTITY", "CONFIG_KEY"}   # graph_ops.py:46
```

had no producer, so `-sum(1 for e in entities if e.kind in DECLARATION_KINDS)` was constant and the
sort collapsed to the file path — the precise defect `_stratified` exists to correct, reintroduced
by an empty set rather than a bad comparator. The mechanism was live, correct, and inert.

## 2. What it emits

| Kind | From | Attributes | Grounds |
|---|---|---|---|
| `CONFIG_KEY` | `.env*`, `*.yml`, `*.toml`, `*.ini`, `*.cfg`, `settings.*`, `constants.*` | `source`, `value`, `secret_like` | `non_functional_requirements`, `technical_constraints` |
| `DATA_ENTITY` | `CREATE TABLE` in `*.sql`, migration dirs | `declares`, `description` | `data_requirements` |
| `DATA_FIELD` | columns | `table`, `type`, `not_null`, `default`, `unique`, `primary_key`, `references` | `data_requirements` |
| `DATA_INDEX` | `CREATE [UNIQUE] INDEX` | `table`, `columns`, `unique`, `description` | `data_requirements` |
| `DOMAIN_POLICY` | `CHECK (col IN (…))` | `rule_source`, `table`, `columns`, `allowed_values`, `constraint` | `business_rules` |
| `EXTERNAL_SERVICE` | runtime deps in dependency manifests | `ecosystem`, `declared_version`, `source` | `integration_requirements` |

Edges: `DECLARES` from the owning `FILE` (so `declaration_groups` can group by file as it
expects), `DECLARES` from table to column, `ENFORCES` from table to policy.

## 3. Measured result

On the 381-file reference repository (`runs/2584e3a7…`), from files that were **already in the
inventory and never opened**:

```
CONFIG_KEY  141    DATA_ENTITY  34    DATA_FIELD  363
DATA_INDEX   35    DOMAIN_POLICY 4    EXTERNAL_SERVICE 56     total 633 nodes
12 configuration keys named a credential; every value redacted
```

Real policy that was previously invisible:

```
app_users.role         accepts only admin, recruiter, sysadmin
review_queue.state     accepts only open, resolved, discarded, approved
email_drafts.status    accepts only draft, ready, sent
ARCHIVE_AFTER_DAYS = 30
```

On the held-out Ruby/Sinatra fixture (`tests/fixtures/new_stack`, deliberately outside the tuning
corpus) the graph went from *two files and nothing else* to four configured limits, two tables, six
columns and a vocabulary — with the gate still passing and 19/19 citations resolving.
`test_harness_scored.py` previously pinned that emptiness and said in as many words that it
"should be rewritten to assert the requirement that is now producible"; it now does.

## 4. Design rules it holds to

**Deterministic, no grammar, no model.** Every fact is a declaration read off the line that states
it. A regex reading `TIMEOUT=30` is not a shortcut around parsing — it is the whole of what that
line says. A format the module cannot read contributes nothing rather than guesses, and
`report_family_coverage` says so out loud.

**A value is only policy when the line fixes one.** `${DATABASE_URL}`, `<your-key>`, `changeme`,
`TODO`, an empty value and free prose all fix nothing. Publishing one as a declared operating
instruction asserts a limit the product does not have, which is worse than an empty section
because nothing downstream can tell it is wrong.

**A credential's value never leaves the file.** That `PG_PASSWORD` is required is a real
requirement — the product authenticates. The secret is redacted at extraction, before it can reach
a graph, a packet or a document, whatever the declared value looks like.

**Purely additive.** Only `add_entity`/`relate`; nothing Graphify produced is removed or rewritten.
`declaration_extraction=False` reproduces the previous graph node-for-node, and
`test_declarations.py` pins both properties.

**Runtime dependencies only.** A dev dependency is a fact about the build, not about a system the
running product integrates with.

## 5. Benefits

- **Seven requirement families become writable at all.** They had prompts asking for them
  (`pack.yaml` lines 305–320) and no evidence class that could ground them, so the model correctly
  returned `[]` — twenty runs out of twenty.
- **Three dormant subsystems switch on** with no changes of their own: `declaration_groups` starts
  ranking non-degenerately, `compact_entity`'s six attribute whitelists start matching nodes, and
  `render.py`'s declaration sections start rendering.
- **Schema constraints outrank code.** A `CHECK … IN` is enforced by the database whatever the
  application does, so it is stronger evidence than any conditional a model might find in a
  handler — and it is the one kind of business rule the business actually wrote down.
- **Zero model cost.** Deterministic, so it adds no tokens and cannot hallucinate.
- **Language-independent.** A table, a column, a config key and a dependency are the same concepts
  in any stack, so this does not reintroduce the Python-only lock the Graphify swap removed.

## 6. Drawbacks and limits — read this before trusting a thin section

- **The parsers are a long tail and the honest failure is silence.** Nested YAML is deliberately
  not walked (a flattened `timeout` under an unknown parent is not a fact about the product);
  ORM-model and Prisma schemas are not read yet; exotic SQL dialects will miss columns. Each miss
  is an *absent* node, and only `report_family_coverage` distinguishes "nothing declared" from
  "we could not read what was declared."
- **`_MEANINGFUL_VALUE` will reject real values.** It accepts magnitudes, switches, identifiers,
  paths and URLs. A value shaped unusually — a cron expression, a JSON blob, a comma-separated
  list — is dropped rather than published. Deliberate, and a real recall cost.
- **A declared value is not necessarily a used value.** `.env.example` states an intended default;
  nothing here proves any code reads it. There are no `READS_CONFIG` edges from these nodes, so a
  quality requirement resting on one is asserting *declared* policy, not observed behaviour. This
  is why `EXTERNAL_SERVICE` is `RESOLVED`/0.9 rather than `EXTRACTED`/1.0 — a manifest proves a
  dependency is declared, not that a call path reaches it.
- **Packet size grows.** 633 more nodes compete for packet budget with behavioural evidence. The
  per-file cap (120) and the per-module cap (60) bound it, but a repository with a very large
  schema will spend real budget on columns.
- **`_is_config_file` matches on path shape.** `app/settings.py` is read; a settings module named
  something else is not, and a module that merely *contains* `config` in its stem is read even if
  it is ordinary code. Matching more eagerly is the expensive mistake — every `UPPER_CASE = value`
  in ordinary code would become declared product policy — so this errs towards missing.
- **Dev/runtime split is manifest-shaped, not universal.** `package.json` and `requirements.txt`
  separate them; a bare `requirements.txt` that mixes them does not, and test libraries will
  appear as external systems.
