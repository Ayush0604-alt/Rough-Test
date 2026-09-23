"""Instruction artifacts — prose that *is* the behaviour.

The tests that matter most here are the negative ones. This module's whole risk is a false `LOADS`
edge: because that edge is walked when gathering evidence, one wrong link lets a requirement ground
itself in an instruction file that has nothing to do with it — which is the exact fabrication the
rest of the pipeline exists to make impossible. Three successive loader-matching rules were tried
against a real repository and each produced edges that were entirely coincidental; every one of
those cases is pinned below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic.capabilities.instructions import (
    _glob_matches_artifact,
    _inside_a_string_literal,
    _loader_literals,
    _quoted_runs,
    _rule_candidates,
    _sections,
    classify_markdown_role,
)
from agentic.capabilities.graph_ops import WORKFLOW_EDGE_KINDS
from agentic.schemas.core import KnowledgeGraph

EXTRACTION = (
    "ingest", "graphify_extract", "adapt_graph", "extract_declarations",
    "extract_instruction_artifacts",
)


# --------------------------------------------------------------------------------------------
# Role: instruction, documentation, or neither
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".agents/skills/brandkit/SKILL.md",
        "codebase-requirement-skills/synthesis/SKILL.md",
        "prompts/intake.md",
        "src/llm/templates/summarise.md",
        "AGENTS.md",
        "CLAUDE.md",
        "app/system-prompt.md",
        ".claude/notes.md",
    ],
)
def test_instruction_files_are_recognised_wherever_they_sit(path: str) -> None:
    """Location first, then name — because those are the two things projects actually use.

    A file under `.agents/skills/` is an instruction artifact whatever it is called, and a file
    called `SKILL.md` is one wherever it sits.
    """
    assert classify_markdown_role(path) == "instruction"


@pytest.mark.parametrize(
    "path",
    ["README.md", "docs/README.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE.md"],
)
def test_documentation_stays_documentation(path: str) -> None:
    assert classify_markdown_role(path) == "documentation"


def test_a_readme_inside_a_skill_directory_is_still_documentation() -> None:
    """A README *about* a skill pack describes it; it is not handed to a model as instructions.

    Name beats location for this one case, which is why documentation names are checked first.
    """
    assert classify_markdown_role(".agents/skills/README.md") == "documentation"


def test_code_is_not_markdown() -> None:
    assert classify_markdown_role("app/api/routes.py") == ""
    assert classify_markdown_role("schema.sql") == ""


# --------------------------------------------------------------------------------------------
# Structure, not flattened prose
# --------------------------------------------------------------------------------------------

SKILL = """---
name: full-output-enforcement
description: Enforces complete code generation and bans placeholder patterns.
---

# Full-Output Enforcement

## Baseline

Treat every task as production-critical. Do not optimize for brevity.

## Banned Output Patterns

The following patterns are hard failures. Never produce them:

- Never emit a placeholder comment in place of code.
- Always count the deliverables before responding.

```python
# ... rest of code        <- an example of a banned pattern, not a rule
# TODO: implement here
```

## Quick Check

The output must feel:
"""


def test_headings_are_carried_as_the_files_own_structure() -> None:
    sections = _sections(SKILL.splitlines())
    titles = [section["title"] for section in sections]
    assert titles == [
        "Full-Output Enforcement", "Baseline", "Banned Output Patterns", "Quick Check"
    ]
    # Every heading carries the line it sits on, so a rule beneath it can be cited precisely.
    assert all(section["line"] > 0 for section in sections)


def test_only_normative_statements_become_rules() -> None:
    """The counterpart of a schema constraint: a declared prohibition, not explanatory prose."""
    sections = _sections(SKILL.splitlines())
    rules = _rule_candidates(SKILL.splitlines(), sections)
    statements = [rule["text"] for rule in rules]

    assert "Never emit a placeholder comment in place of code." in statements
    assert "Always count the deliverables before responding." in statements
    assert "Do not optimize for brevity." in " ".join(statements)
    # Explanation is not a rule.
    assert not any("production-critical" in text and "Do not" not in text for text in statements)


def test_a_rule_records_the_section_that_states_it() -> None:
    sections = _sections(SKILL.splitlines())
    rules = _rule_candidates(SKILL.splitlines(), sections)
    placeholder = next(rule for rule in rules if "placeholder" in rule["text"])
    assert placeholder["section"] == "Banned Output Patterns"
    assert placeholder["modality"] == "never"


def test_examples_inside_a_code_fence_are_not_rules() -> None:
    """A banned-pattern example illustrates a rule; it is not one.

    Counting each one would turn a single declared prohibition into a dozen requirements.
    """
    rules = _rule_candidates(SKILL.splitlines(), _sections(SKILL.splitlines()))
    assert not any("rest of code" in rule["text"] for rule in rules)
    assert not any("TODO" in rule["text"] for rule in rules)


def test_a_line_that_introduces_a_list_is_not_itself_a_rule() -> None:
    """"The output must feel:" is a complete-looking requirement that says nothing.

    The real rule is in the list beneath it, and each item there is judged on its own.
    """
    rules = _rule_candidates(SKILL.splitlines(), _sections(SKILL.splitlines()))
    assert not any(rule["text"].endswith(":") for rule in rules)
    assert not any("must feel" in rule["text"] for rule in rules)


# --------------------------------------------------------------------------------------------
# The LOADS edge — where a wrong answer is worse than no answer
# --------------------------------------------------------------------------------------------

ARTIFACT = "repo-PROD/.agents/skills/brandkit/SKILL.md"


def test_the_repository_root_is_never_a_match_literal() -> None:
    """It appears in every artifact's path, so it links a loader to all of them at once.

    Measured on a real run: `resume-agent-PROD` was a literal for all nineteen artifacts, so any
    line mentioning the repository name produced nineteen false edges.
    """
    assert not any("repo-PROD" == literal for literal in _loader_literals(ARTIFACT))


def test_a_bare_directory_name_is_never_a_match_literal() -> None:
    """A directory is named after a domain concept, and a concept is an ordinary English word.

    On a real run `extraction` matched `"extraction": result,` in an API response and `queue`
    matched a route path, a comment and a docstring. All twelve surviving edges were coincidences.
    """
    literals = _loader_literals("repo/app/extraction/prompts/layer3.md")
    assert "extraction" not in literals
    assert "prompts" not in literals
    assert all("/" in literal or literal.endswith(".md") for literal in literals)


def test_a_bare_conventional_filename_is_never_a_match_literal() -> None:
    """`SKILL.md` identifies the convention, not any one file."""
    assert "SKILL.md" not in _loader_literals(ARTIFACT)


def test_every_literal_is_path_shaped() -> None:
    for literal in _loader_literals(ARTIFACT):
        assert "/" in literal or literal.endswith((".md", ".markdown", ".mdx", ".txt")), literal


def test_a_path_in_a_string_counts_and_a_path_in_prose_does_not() -> None:
    literal = "skills/brandkit/SKILL.md"
    assert _inside_a_string_literal('path = "skills/brandkit/SKILL.md"', literal)
    assert not _inside_a_string_literal("path = skills/brandkit/SKILL.md", literal)


def test_backticks_are_not_string_delimiters() -> None:
    """Markdown prose markup, not code — and this project's own comments use it constantly.

    Treating a backtick as a quote made every sentence mentioning a glob look like a loader.
    """
    assert _quoted_runs("# see `*/SKILL.md` for the pattern") == []


@pytest.mark.parametrize(
    "pattern, expected",
    [
        ("*/SKILL.md", True),
        ("**/SKILL.md", True),
        ("repo-PROD/.agents/skills/*/SKILL.md", True),
        ("*/*.md", True),
        # Names a file type rather than a location — would link one loader to every artifact.
        ("*.md", False),
        ("*", False),
        ("prompts/*.md", False),
        ("app/api/*.py", False),
    ],
)
def test_a_glob_must_name_a_location(pattern: str, expected: bool) -> None:
    """A registry globs rather than naming each file, which is the dominant loader shape.

    This project's own skill loader is `root.glob("*/SKILL.md")`, so without glob support the only
    detectable loader would be one that hardcodes a single path — the rarer case.
    """
    assert _glob_matches_artifact(pattern, ARTIFACT) is expected


def test_the_grounding_decision_is_recorded_in_one_place() -> None:
    """LOADS being walkable is what lets an instruction file ground a requirement.

    This test exists so that removing it is a deliberate act with a failing test attached, rather
    than a quiet change of policy. See the module docstring for the reasoning and for how to
    reverse it.
    """
    assert "LOADS" in WORKFLOW_EDGE_KINDS


# --------------------------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------------------------


def _graph(state) -> KnowledgeGraph:
    return KnowledgeGraph.model_validate(
        json.loads(Path(state["artifacts"]["graph"]).read_text(encoding="utf-8"))
    )


def test_switching_instruction_extraction_off_reproduces_the_previous_graph(
    runner, state, settings
) -> None:
    for step_id in ("ingest", "graphify_extract", "adapt_graph", "extract_declarations"):
        runner(step_id)
    before = _graph(state)

    settings.instruction_artifacts = False
    runner("extract_instruction_artifacts")
    after = _graph(state)

    assert len(after.entities) == len(before.entities)
    assert {entity.id for entity in after.entities} == {entity.id for entity in before.entities}


def test_extraction_only_adds_and_never_rewrites(runner, state) -> None:
    for step_id in ("ingest", "graphify_extract", "adapt_graph", "extract_declarations"):
        runner(step_id)
    before = {entity.id: entity for entity in _graph(state).entities}

    runner("extract_instruction_artifacts")
    after = {entity.id: entity for entity in _graph(state).entities}

    for entity_id, entity in before.items():
        assert entity_id in after
        assert after[entity_id] == entity


def test_an_artifact_carries_its_declared_identity(runner, state, tmp_path) -> None:
    """Read for what it declares about itself, not as fifty lines of leading text."""
    skills = Path(state["source_root"]) / ".agents" / "skills" / "tone"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(SKILL, encoding="utf-8")

    for step_id in EXTRACTION:
        runner(step_id)
    graph = _graph(state)

    artifacts = [e for e in graph.entities if e.kind == "INSTRUCTION_ARTIFACT"]
    assert artifacts, "a skill file produced no instruction artifact"
    artifact = next(e for e in artifacts if "tone" in (e.location.file if e.location else ""))
    assert artifact.name == "full-output-enforcement"
    assert "placeholder" in artifact.attributes["description"].lower() or artifact.attributes[
        "description"
    ]
    assert "Banned Output Patterns" in artifact.attributes["sections"]

    rules = [e for e in graph.entities if e.kind == "INSTRUCTION_RULE"]
    assert rules, "a skill file stating prohibitions produced no rules"
    # A rule is weaker evidence than a parsed constraint: an instruction asks a model to comply,
    # where a schema constraint is enforced by the database whatever the code does.
    assert all(rule.confidence < 1.0 for rule in rules)
    assert all(rule.location is not None and rule.location.line_start for rule in rules)
