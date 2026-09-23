"""Instruction artifacts: prose that *is* the behaviour, not prose *about* it.

For a conventional product, a Markdown file describes the code and the code is the product. For an
LLM-powered product that is inverted: a skill file, a prompt file or an agent configuration **is**
the implementation, and the code around it opens the file, concatenates it and posts it to an API.
Every refusal, every limit, every rule a user would actually experience lives in the Markdown.

The supporting-outline reader this replaces for such files encoded the conventional assumption in a
hardcoded filename whitelist — `dockerfile`, `docker-compose*`, `requirements*`, a handful of build
manifests, and `readme*`. Anything else returned **nothing at all**, not a truncated outline. Its
docstring records that it was *"ported unchanged in behaviour"* from the deleted Python extractor,
which is exactly when that assumption was still safe.

Measured on two stored runs before this module existed:

    analysed repository   24 markdown nodes · 17 of them agent skill files
                           1 carried any text (the README, because of its name)
                           0 edges to anything
    this tool, on itself  137 markdown nodes ·  54 SKILL.md nodes
                           0 carried any text ·  0 edges to anything

The second line is the one that matters: this project deliberately keeps its instructions in
Markdown *"never inside prompt string literals"*, and analysing itself it read none of them. It
would have documented the loader and said nothing about what is loaded.

## What this module does, and the schema analogy it follows

A schema file became tables, columns and constraints rather than a blob of SQL text. An instruction
file gets the same treatment rather than fifty lines of flattened prose:

    INSTRUCTION_ARTIFACT   the file, with its declared name, description and section headings
    INSTRUCTION_RULE       one explicit normative statement — "never produce X", "always do Y"

`INSTRUCTION_RULE` is the counterpart of a schema's `CHECK` constraint: not any sentence, only one
that states a prohibition or an obligation outright. Ordinary explanatory prose is deliberately
left as part of the artifact rather than promoted to a rule, for the same reason most conditionals
are not business rules — promoting everything would bury the declarations that matter.

## Why the LOADS edge is the important part

Before this, these files appeared in the graph as file-nodes with **zero edges** — as orphaned as
the schema nodes were before the declared-policy extractor existed, and unreachable for the same
reason: nothing calls a Markdown file.

A `LOADS` edge from the code that opens an instruction file to the artifact itself is what makes a
requirement able to trace from observed behaviour back to the instruction that causes it. It runs
in the same direction as the evidence walk — outward from a boundary, through the calls, to the
loader, and then to what the loader loads — so no traversal rule had to be inverted to make it
work.

## The grounding decision this encodes

`LOADS` is in `WORKFLOW_EDGE_KINDS`, which means **a requirement may be grounded in an instruction
file when traced code loads it.** That is a deliberate policy choice, and the alternative is
defensible: leave `LOADS` out and LLM-native products stay undocumentable by this pipeline.

It was chosen for the same reason a schema constraint became citable. Nothing *calls* a schema
either; a declared constraint earns its place because the database enforces it. An instruction file
earns its place because the model is given it on every request — the enforcement is weaker, which
is why rules from here carry `RESOLVED` provenance and reduced confidence rather than the
`EXTRACTED` certainty a parsed constraint gets. An instruction *asks*; a constraint *enforces*.
Requirements resting on one should be worded as declared policy, not guaranteed behaviour.

To reverse the decision: remove `"LOADS"` from `WORKFLOW_EDGE_KINDS` in `graph_ops`. The artifacts
and rules stay in the graph and stay visible in declaration clusters; they simply stop being able
to ground a claim on their own.
"""

from __future__ import annotations

import re

from collections import Counter
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable

from agentic.capabilities.graph_ops import GraphBuilder, stable_id
from agentic.capabilities.registry import CapabilityContext, CapabilityResult, capability
from agentic.schemas.core import Entity, KnowledgeGraph, Provenance, SourceLocation

# Directory names that mean "the files under here are instructions to a model". Matched anywhere in
# the path, because every convention nests them differently: `.agents/skills/x/SKILL.md`,
# `prompts/summarise.md`, `src/llm/templates/intake.md`.
_INSTRUCTION_DIRECTORIES = {
    "skills", "skill", "prompts", "prompt", ".agents", "agents", ".claude",
    "instructions", "personas", "playbooks", "templates",
}

# Filenames that are instruction artifacts wherever they sit. These are conventions, not guesses:
# each is an established name for "the instructions a model is given".
_INSTRUCTION_NAMES = {
    "skill.md", "agents.md", "claude.md", "agent.md", "system.md",
    "instructions.md", "persona.md", "prompt.md",
}

# Filename *prefixes* that carry the same meaning with a suffix attached — `PROMPT_intake.md`,
# `system-prompt.md`.
_INSTRUCTION_PREFIXES = ("prompt", "system-prompt", "system_prompt", "skill", "persona")

# Documentation that describes the product rather than instructing a model. Read as before, as
# supporting evidence — never as an instruction artifact, however long or detailed.
_DOCUMENTATION_NAMES = ("readme", "changelog", "contributing", "license", "licence", "code_of_conduct")

_INSTRUCTION_SUFFIXES = {".md", ".markdown", ".mdx", ".txt"}

# Frontmatter naming the artifact, the same shape this project's own skill loader reads.
_FRONTMATTER = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n", re.S)
_FRONTMATTER_FIELD = re.compile(r"^(?P<key>[A-Za-z_][\w-]{0,40})\s*:\s*(?P<value>.*)$")

_HEADING = re.compile(r"^(?P<hashes>#{1,4})\s+(?P<title>.+?)\s*#*\s*$")

# A normative statement: prose that states an obligation or a prohibition outright. The Markdown
# counterpart of a `CHECK` constraint, and deliberately narrow — "the system reads its settings at
# start-up" is explanation, "never return a partial file" is a declared rule.
#
# Anchored to the start of a sentence or a list item so a `must` buried mid-clause in an
# explanatory paragraph does not promote the whole paragraph to a rule.
_MODALITY = re.compile(
    r"""(?ix)
    (?P<modality>
        never | always | must\s+not | must | do\s+not | don't | shall\s+not | shall
      | required\s+to | may\s+not | cannot | is\s+forbidden | is\s+banned | are\s+banned
      | are\s+hard\s+failures | no\s+exceptions
    )\b
    """
)

# Lines that look normative but are structure or code rather than prose.
_NOT_PROSE = re.compile(r"^\s*(?:```|~~~|\||<|\{|\[|#{1,6}\s|---\s*$)")

_MAX_SECTIONS = 40
_MAX_RULES_PER_ARTIFACT = 30
_MIN_RULE_CHARS = 20
_MAX_RULE_CHARS = 400
_MAX_ARTIFACT_BYTES = 256_000

# How far a loader reference may be from a literal naming an artifact and still be treated as the
# code that loads it. A glob and the call that runs it sit within a line or two of each other.
_LOADER_WINDOW = 4

# Literals that identify an instruction artifact when a loader names them. A registry globs
# `*/SKILL.md` rather than naming each file, so the filename and the directory both count.
_MAX_LOADERS_PER_ARTIFACT = 6


def classify_markdown_role(path: str) -> str:
    """`instruction`, `documentation`, or `""` for a file this module has no opinion about.

    Role comes from location and name, in that order of authority, because location is what a
    project actually uses to mean "these are instructions": a file under `.agents/skills/` is an
    instruction artifact whatever it is called, and a file called `SKILL.md` is one wherever it
    sits.
    """
    lowered = path.replace("\\", "/").lower()
    name = lowered.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    suffix = f".{name.rsplit('.', 1)[-1]}" if "." in name else ""

    if suffix not in _INSTRUCTION_SUFFIXES:
        return ""

    # Documentation wins on name even inside an instruction directory: a README *about* a skill
    # pack is documentation, not an instruction handed to a model.
    if stem.startswith(_DOCUMENTATION_NAMES):
        return "documentation"

    parts = set(lowered.split("/")[:-1])
    if parts & _INSTRUCTION_DIRECTORIES:
        return "instruction"
    if name in _INSTRUCTION_NAMES or stem.startswith(_INSTRUCTION_PREFIXES):
        return "instruction"
    return "documentation"


def _frontmatter(text: str) -> tuple[dict[str, str], int]:
    """Declared fields and the line the body starts on."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return {}, 1
    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if field := _FRONTMATTER_FIELD.match(line.strip()):
            fields[field.group("key").lower()] = field.group("value").strip().strip("'\"")
    return fields, text[: match.end()].count("\n") + 1


def _sections(lines: list[str]) -> list[dict[str, Any]]:
    """Every heading with the line it sits on — the artifact's own structure.

    Headings rather than prose because a heading is what the file's author chose as the unit of
    instruction, and because it is short enough to carry into a packet at full fidelity instead of
    truncated mid-sentence.
    """
    found: list[dict[str, Any]] = []
    fenced = False
    for number, raw in enumerate(lines, start=1):
        # A `#` inside a fenced block is a comment in whatever language the block is written in,
        # not a heading. Without this, a skill file carrying a Python or shell example gained
        # phantom sections named after its own comments — `# TODO: implement here` became a
        # heading, and a rule beneath a real heading was then attributed to the wrong section.
        if raw.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        if match := _HEADING.match(raw):
            found.append(
                {
                    "title": match.group("title").strip(),
                    "level": len(match.group("hashes")),
                    "line": number,
                }
            )
        if len(found) >= _MAX_SECTIONS:
            break
    return found


def _section_for(sections: list[dict[str, Any]], line: int) -> str:
    """The heading a line sits under, so a rule can say which part of the file states it."""
    current = ""
    for section in sections:
        if section["line"] <= line:
            current = section["title"]
        else:
            break
    return current


def _rule_candidates(lines: list[str], sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Statements that declare an obligation or a prohibition.

    Inside fenced code blocks nothing counts: a banned-pattern example is an illustration of a
    rule, not a rule, and treating each one as a requirement would turn one declared prohibition
    into a dozen.
    """
    found: list[dict[str, Any]] = []
    fenced = False
    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or not stripped or _NOT_PROSE.match(raw):
            continue

        # Strip list markers and emphasis so the statement reads as a sentence.
        text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", stripped)
        text = text.replace("**", "").replace("`", "").strip()
        if not (_MIN_RULE_CHARS <= len(text) <= _MAX_RULE_CHARS):
            continue

        # A line ending in a colon introduces the rule rather than stating it — "The output must
        # feel:", "Every generated board must answer:". Captured alone it reads as a complete
        # requirement that says nothing, which is worse than skipping it: the real rule is in the
        # list beneath, and each of those items is judged on its own by the loop.
        if text.endswith((":", "—", "-")):
            continue

        match = _MODALITY.search(text)
        if match is None:
            continue
        # Only near the start of the statement. A `must` deep inside an explanatory sentence is
        # describing something, not declaring it.
        if match.start() > 80:
            continue

        found.append(
            {
                "text": text,
                "modality": " ".join(match.group("modality").split()).lower(),
                "line": number,
                "section": _section_for(sections, number),
            }
        )
        if len(found) >= _MAX_RULES_PER_ARTIFACT:
            break
    return found


def _loader_literals(artifact_path: str) -> set[str]:
    """The strings a loader would name to reach this artifact — **path-shaped only**.

    **Precision matters far more than recall here.** A false LOADS edge lets a requirement ground
    itself in an instruction file that has nothing to do with it, which is the exact fabrication the
    rest of this pipeline exists to prevent. A missing edge only costs an artifact the ability to
    ground a claim on its own; it still reaches a model through its declaration cluster, and the
    step reports out loud that no loader was found.

    Two looser rules were tried against a real repository and both produced edges that were
    entirely false:

    - **Every path segment.** The repository root was then a literal for every artifact, so any
      line mentioning the repository name linked to all nineteen at once.
    - **The distinctive leaf directory.** A directory is named after a domain concept, and a
      domain concept is an ordinary English word. `extraction` matched `"extraction": result,` in
      an API response; `queue` matched a comment, a route path `@router.get("/queue")`, and a
      docstring. All twelve surviving edges were coincidental name collisions.

    So a literal must be **path-shaped**: it carries a separator, or it is a filename with an
    instruction suffix. That is what distinguishes naming a *file* from mentioning a *concept*, and
    a loader has no way to open a file without naming a path to it.
    """
    normalised = artifact_path.replace("\\", "/")
    parts = normalised.split("/")
    name = parts[-1]
    directories = parts[:-1]

    literals: set[str] = {normalised}

    # A filename only counts when it names this artifact rather than the shared convention. A bare
    # `SKILL.md` identifies the convention and would link one loader to every artifact.
    if name.casefold() not in _INSTRUCTION_NAMES:
        literals.add(name)

    # Path fragments, deepest first. Each carries a separator, so it cannot match a bare word.
    for depth in (1, 2, 3):
        if len(directories) >= depth:
            literals.add("/".join(directories[-depth:] + [name]))
    if directories:
        literals.add("/".join(directories[-min(2, len(directories)):]))

    def path_shaped(literal: str) -> bool:
        if "/" in literal:
            return True
        lowered = literal.casefold()
        return any(lowered.endswith(suffix) for suffix in _INSTRUCTION_SUFFIXES)

    root = directories[0].casefold() if directories else ""
    return {
        literal
        for literal in literals
        if len(literal) >= 6 and literal.casefold() != root and path_shaped(literal)
    }


@capability("extract_instruction_artifacts")
def extract_instruction_artifacts(ctx: CapabilityContext) -> CapabilityResult:
    """Read instruction files as structure, and connect each to the code that loads it.

    Runs after the declared-policy extractor and before boundary proposal, so instruction rules
    exist when candidates are ranked and when declaration clusters look for policy no call path
    reaches.

    Additive: only new entities and edges, so switching `instruction_artifacts` off reproduces the
    previous graph exactly.
    """
    settings = ctx.settings
    graph = ctx.load("graph", KnowledgeGraph)

    if not getattr(settings, "instruction_artifacts", True):
        return CapabilityResult(
            artifacts={"graph": graph.model_dump(mode="json")},
            metrics={"instruction_artifacts": 0},
            messages=[
                "Instruction-artifact extraction is switched off. Skill and prompt files remain "
                "in the graph as files with no content, so a product whose behaviour is defined "
                "in Markdown cannot be described."
            ],
        )

    inventory = ctx.load("inventory")
    records = [item for item in (inventory.get("files") or []) if item.get("path")]
    root = ctx.source_root

    builder = GraphBuilder(graph.repository_name)
    for entity in graph.entities:
        builder.add_entity(entity)
    for relationship in graph.relationships:
        builder._relationships.setdefault(relationship.id, relationship)

    file_ids = {
        entity.location.file: entity.id
        for entity in graph.entities
        if entity.kind == "FILE" and entity.location is not None
    }

    artifacts: list[tuple[str, str, set[str]]] = []
    roles: Counter[str] = Counter()
    rule_count = 0
    unreadable: list[str] = []

    for record in records:
        path = str(record["path"])
        role = classify_markdown_role(path)
        if role != "instruction":
            if role:
                roles["documentation"] += 1
            continue

        try:
            full = (root / path).read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            unreadable.append(path)
            continue
        if len(full.encode("utf-8", errors="ignore")) > _MAX_ARTIFACT_BYTES:
            unreadable.append(path)
            continue

        lines = full.splitlines()
        fields, body_line = _frontmatter(full)
        sections = _sections(lines)
        rules = _rule_candidates(lines, sections)

        declared_name = fields.get("name") or (
            sections[0]["title"] if sections else Path(path).stem
        )
        artifact_id = stable_id("instruction", path)
        builder.add_entity(
            Entity(
                id=artifact_id,
                kind="INSTRUCTION_ARTIFACT",
                name=declared_name,
                qualified_name=path,
                location=SourceLocation(
                    file=path, line_start=1, line_end=max(1, len(lines)), symbol=None
                ),
                # The file and its headings are read syntactically, so EXTRACTED. The weaker claim
                # is what the instructions *achieve*, and that is carried by each rule's own
                # provenance below, not by the artifact.
                provenance=Provenance.EXTRACTED,
                confidence=1.0,
                attributes={
                    "artifact_role": "instruction",
                    "declared_name": declared_name,
                    "description": fields.get("description", ""),
                    "sections": [section["title"] for section in sections],
                    "section_lines": [section["line"] for section in sections],
                    "body_starts_at": body_line,
                    "rule_count": len(rules),
                },
            )
        )
        roles["instruction"] += 1
        if owner := file_ids.get(path):
            builder.relate(owner, artifact_id, "DECLARES", provenance=Provenance.EXTRACTED)

        for rule in rules:
            rule_id = stable_id("instruction_rule", path, rule["line"])
            builder.add_entity(
                Entity(
                    id=rule_id,
                    kind="INSTRUCTION_RULE",
                    name=rule["text"][:80],
                    qualified_name=f"{path}::L{rule['line']}",
                    location=SourceLocation(
                        file=path,
                        line_start=rule["line"],
                        line_end=rule["line"],
                        symbol=None,
                        scope=rule["section"] or None,
                    ),
                    # RESOLVED, not EXTRACTED, and this is the honest part of the whole module: the
                    # *line* is parsed with certainty, but a rule in an instruction file is a
                    # request to a model, not a constraint a runtime enforces. A schema's CHECK
                    # holds whatever the code does; this holds only as far as the model complies.
                    provenance=Provenance.RESOLVED,
                    confidence=0.7,
                    attributes={
                        "rule_source": "instruction_file",
                        "modality": rule["modality"],
                        "statement": rule["text"],
                        "section": rule["section"],
                        "description": rule["text"],
                    },
                )
            )
            builder.relate(artifact_id, rule_id, "ENFORCES", provenance=Provenance.EXTRACTED)
            rule_count += 1

        artifacts.append((path, artifact_id, _loader_literals(path)))

    # --- Connect each artifact to the code that loads it ------------------------------------
    #
    # Without this the artifacts are orphans with no edge to anything, which is precisely the state
    # the schema nodes were in before a producer existed for them. A LOADS edge is what lets a
    # requirement trace from behaviour back to the instruction that causes it.
    loaders = 0
    if artifacts:
        loaders = _link_loaders(builder, graph, records, root, artifacts)

    metadata = dict(graph.metadata)
    metadata["instructions"] = {
        "artifacts": roles["instruction"],
        "rules": rule_count,
        "loaders": loaders,
        "documentation_files": roles["documentation"],
    }
    updated = KnowledgeGraph(
        schema_version=graph.schema_version,
        repository_name=graph.repository_name,
        entities=builder.entities,
        relationships=builder.relationships,
        metadata=metadata,
    )

    messages: list[str] = []
    if roles["instruction"]:
        messages.append(
            f"{roles['instruction']} instruction artifact(s) read as structure, carrying "
            f"{rule_count} declared rule(s). These files state behaviour the surrounding code only "
            "delivers, and were previously present in the graph with no content and no edges."
        )
        if loaders:
            messages.append(
                f"{loaders} load relationship(s) found between code and an instruction artifact, "
                "so a requirement resting on one can trace back to an interaction boundary."
            )
        else:
            messages.append(
                "No code was found loading any instruction artifact, so none can ground a "
                "requirement on its own. They are still read as declared policy in a cluster of "
                "their own — a rule nothing loads is a rule nothing applies."
            )
    if unreadable:
        messages.append(
            f"{len(unreadable)} instruction file(s) could not be read or exceeded the size cap: "
            + ", ".join(sorted(unreadable)[:5])
            + "."
        )

    return CapabilityResult(
        artifacts={"graph": updated.model_dump(mode="json")},
        metrics={
            "instruction_artifacts": roles["instruction"],
            "instruction_rules": rule_count,
            "instruction_loaders": loaders,
        },
        messages=messages,
    )


# A path named in a comment or a docstring is a description; a path a loader uses is a string it
# passes to something. Requiring quotes around the match is what separates the two, and it is why
# `# Close the mirror row in the Needs-Review queue` stopped counting as a load.
# Glob metacharacters. A registry does not name each instruction file — it globs the directory
# that holds them (`root.glob("*/SKILL.md")` is exactly how this project loads its own skills), so
# without this the only detectable loader is one that hardcodes a single path, which is the rarer
# shape.
_GLOB_CHARS = ("*", "?", "[")


def _glob_matches_artifact(pattern: str, artifact_path: str) -> bool:
    """True when a quoted glob would match this artifact.

    Tested against every trailing slice of the artifact's path, because a glob is written relative
    to whatever root the loader was handed — `*/SKILL.md` is rooted at the pack directory and knows
    nothing of the repository above it.
    """
    if not any(character in pattern for character in _GLOB_CHARS):
        return False
    normalised = artifact_path.replace("\\", "/")
    cleaned = pattern.replace("\\", "/").lstrip("./")
    # A glob must name a *location*, not just a file type. `*.md` matches every Markdown file in
    # the project and would link one loader to every artifact at once — the same defect as matching
    # a bare `SKILL.md`, which identifies the convention rather than any one file. Requiring a
    # separator is what makes `*/SKILL.md` acceptable and `*.md` not.
    if "/" not in cleaned:
        return False
    parts = normalised.split("/")
    return any(
        fnmatch("/".join(parts[index:]), cleaned) for index in range(len(parts))
    )


def _inside_a_string_literal(line: str, literal: str) -> bool:
    """True only when `literal` sits inside a quoted run on this line.

    A path named in a comment or a docstring is a description of a file; a path a loader uses
    is a string it passes to something. Requiring quotes is what separates the two, and it is
    why a comment mentioning a directory stopped counting as a load.

    Scanned rather than matched with a regex: a backreference for the closing quote plus
    escape handling is unreadable, and this loop is both obvious and easy to widen later.
    """
    return any(literal in run for run in _quoted_runs(line))



# Quote characters that actually delimit a string. **Backticks are deliberately absent.** They
# delimit a template literal in JavaScript, but in Python, Ruby and shell docstrings they are
# markdown prose markup — and this project's own comments use them constantly, so including them
# made every sentence mentioning `*/SKILL.md` look like code loading that file.
QUOTE_CHARACTERS = ("'", '"')

# A line that begins a comment is prose *about* code, not code. Checked because a comment
# containing a real quoted path still parses as a quoted string: on this project's own source,
# `# that holds them ("*/SKILL.md" is exactly how ...)` was read as a loader.
_COMMENT_START = re.compile(r"^\s*(?:#|//|--|/\*|\*\s)")



def _quoted_runs(line: str) -> list[str]:
    """Every quoted run on a line, so a glob and a literal path are found the same way."""
    found: list[str] = []
    for quote in QUOTE_CHARACTERS:
        index = 0
        while True:
            opening = line.find(quote, index)
            if opening < 0:
                break
            closing = line.find(quote, opening + 1)
            if closing < 0:
                break
            found.append(line[opening + 1 : closing])
            index = closing + 1
    return found


def _link_loaders(
    builder: GraphBuilder,
    graph: KnowledgeGraph,
    records: list[dict[str, Any]],
    root: Path,
    artifacts: list[tuple[str, str, set[str]]],
) -> int:
    """Emit a LOADS edge from the code that names an instruction artifact to the artifact.

    Matching is on string literals in parseable source, because that is how a loader actually
    reaches one of these files — by naming it, or by globbing the directory it sits in. The nearest
    enclosing definition is preferred as the edge's source over the file, so the walk arrives at a
    function a boundary can reach rather than at a file nothing calls.
    """
    definitions = [
        entity
        for entity in graph.entities
        if entity.kind in {"FUNCTION", "METHOD", "CLASS"}
        and entity.location is not None
        and entity.location.line_start
    ]
    by_file: dict[str, list[Entity]] = {}
    for entity in definitions:
        by_file.setdefault(entity.location.file, []).append(entity)
    for entries in by_file.values():
        entries.sort(key=lambda item: item.location.line_start or 0)

    file_ids = {
        entity.location.file: entity.id
        for entity in graph.entities
        if entity.kind == "FILE" and entity.location is not None
    }

    linked = 0
    per_artifact: Counter[str] = Counter()

    for record in records:
        if not record.get("parseable"):
            continue
        path = str(record["path"])
        try:
            lines = (root / path).read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        except OSError:
            continue

        for number, raw in enumerate(lines, start=1):
            # Fast path. Every match sits inside quotes and is either path-shaped or a glob, so a
            # line with no quote, or with neither a separator nor a wildcard, cannot match any
            # artifact and the per-artifact loop can be skipped. On a 381-file repository this is
            # most lines, and it took this step from minutes to under a second.
            if not any(quote in raw for quote in QUOTE_CHARACTERS):
                continue
            if "/" not in raw and not any(character in raw for character in _GLOB_CHARS):
                continue
            # Prose about a path is not a load of it.
            if _COMMENT_START.match(raw):
                continue
            for artifact_path, artifact_id, literals in artifacts:
                if per_artifact[artifact_id] >= _MAX_LOADERS_PER_ARTIFACT:
                    continue
                matched = next(
                    (
                        literal
                        for literal in literals
                        if literal in raw and _inside_a_string_literal(raw, literal)
                    ),
                    None,
                )
                if matched is None:
                    # A registry globs the directory rather than naming the file. Checked second
                    # because an exact path is the stronger evidence when both are present.
                    matched = next(
                        (
                            f"glob:{run}"
                            for run in _quoted_runs(raw)
                            if _glob_matches_artifact(run, artifact_path)
                        ),
                        None,
                    )
                if matched is None:
                    continue

                # The enclosing definition, so the edge starts somewhere a boundary walk reaches.
                source_id = file_ids.get(path)
                enclosing = None
                for entity in by_file.get(path, []):
                    start = entity.location.line_start or 0
                    end = entity.location.line_end or start
                    if start <= number <= max(end, start) + _LOADER_WINDOW:
                        enclosing = entity
                if enclosing is not None:
                    source_id = enclosing.id
                if source_id is None:
                    continue

                builder.relate(
                    source_id,
                    artifact_id,
                    "LOADS",
                    # The literal is parsed with certainty; that this line is what *loads* the file
                    # is an inference from the name appearing here.
                    provenance=Provenance.RESOLVED,
                    confidence=0.7,
                    evidence=[
                        SourceLocation(file=path, line_start=number, line_end=number, symbol=None)
                    ],
                    # The literal that matched is recorded, not just the target. Without it a
                    # reviewer cannot tell a real load from a coincidental name collision, and
                    # this edge is the one that lets an instruction file ground a requirement.
                    attributes={"loads": artifact_path, "matched_literal": matched},
                )
                per_artifact[artifact_id] += 1
                linked += 1

    return linked


def instruction_rules_for(
    graph: KnowledgeGraph, member_ids: Iterable[str]
) -> list[dict[str, Any]]:
    """Declared rules from instruction artifacts these members load.

    Used by the module tier, where an instruction file is most legible: a skill loaded by one part
    of the product states that part's behaviour, and a rule shown to every module at once would be
    restated by every module at once.
    """
    members = set(member_ids)
    index = {entity.id: entity for entity in graph.entities}

    reached: set[str] = set()
    for relationship in graph.relationships:
        if relationship.kind == "LOADS" and relationship.source in members:
            reached.add(relationship.target)
    if not reached:
        return []

    found: list[dict[str, Any]] = []
    for relationship in graph.relationships:
        if relationship.kind != "ENFORCES" or relationship.source not in reached:
            continue
        rule = index.get(relationship.target)
        if rule is None or rule.kind != "INSTRUCTION_RULE":
            continue
        found.append(
            {
                "kind": "INSTRUCTION_RULE",
                "artifact": index[relationship.source].name
                if relationship.source in index
                else "",
                "modality": rule.attributes.get("modality"),
                "statement": rule.attributes.get("statement"),
                "section": rule.attributes.get("section"),
                "at": rule.location.compact() if rule.location else None,
            }
        )
    return sorted(found, key=lambda item: (str(item["artifact"]), str(item["at"])))[:40]
