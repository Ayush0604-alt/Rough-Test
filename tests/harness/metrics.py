"""The measurements every work package reports a delta on.

Nothing here knows about a language, a framework, or a file extension. Entry-point classes come
from the graph and from each fixture's `ground_truth.json`; effect classification comes from a
declared attribute on the entity, never from parsing its name. A metric that had to be taught
about a stack would be a metric that silently permits a stack-specific fix.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from tests.conftest import run_step_or_stub

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "tests" / "fixtures" / "corpus"
# Ground truth lives outside the analysed tree. Keeping it inside made every fixture report a
# JSON file it does not really contain, and fed the harness's own answer key to the extractors.
TRUTH = REPO_ROOT / "tests" / "fixtures" / "corpus_truth"

# Effects a mutating entry point may declare. HTTP verbs are the common case but the set is not
# HTTP-specific: an adapter for any paradigm records `effect` on the entity it creates, and
# anything it does not classify is counted as `unknown` rather than guessed at.
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
MUTATE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def load_ground_truth(fixture: Path) -> dict[str, Any]:
    return json.loads((TRUTH / f"{fixture.name}.json").read_text(encoding="utf-8"))


def _effect_of(entity: dict[str, Any]) -> str:
    """How an entry point is classified read vs mutate.

    Preference order is deliberate. A declared `effect` attribute wins, so an adapter can state
    the answer for a paradigm where it is not derivable. Only then does the HTTP `method`
    attribute apply, and it is read from the attribute the extractor recorded — never parsed back
    out of the qualified name, which would tie this metric to one naming convention.
    """
    attributes = entity.get("attributes") or {}
    declared = str(attributes.get("effect") or "").lower()
    if declared in {"read", "mutate"}:
        return declared
    method = str(attributes.get("method") or "").upper()
    if method in READ_METHODS:
        return "read"
    if method in MUTATE_METHODS:
        return "mutate"
    return "unknown"


@dataclass
class StructureMetrics:
    """Coverage of entry points, measured without a single model call."""

    fixture: str
    halted: str | None = None
    languages_inventoried: dict[str, int] = field(default_factory=dict)
    parseable_files: int = 0
    inventory_only_files: int = 0
    # class -> effect -> count
    present: dict[str, dict[str, int]] = field(default_factory=dict)
    detected: dict[str, dict[str, int]] = field(default_factory=dict)
    selected: dict[str, dict[str, int]] = field(default_factory=dict)
    clusters: int = 0
    graph_entities: int = 0
    detected_entities: int = 0
    entities_by_kind: dict[str, int] = field(default_factory=dict)
    missed_entry_points: list[str] = field(default_factory=list)
    selected_ids: list[str] = field(default_factory=list)
    detected_but_unselected: list[str] = field(default_factory=list)

    @staticmethod
    def _totals(nested: dict[str, dict[str, int]]) -> dict[str, int]:
        return {klass: sum(by_effect.values()) for klass, by_effect in nested.items()}

    def class_coverage(self, stage: str) -> dict[str, float]:
        """Share of each entry-point class reached, ignoring effect.

        Effect is not derivable for every paradigm -- a scheduled job has no verb -- so class
        coverage is the metric that applies everywhere, and the effect split is reported
        alongside it wherever the extractor was able to state one.
        """
        present = self._totals(self.present)
        have = self._totals(getattr(self, stage))
        return {
            klass: (have.get(klass, 0) / total if total else 0.0)
            for klass, total in present.items()
        }

    def coverage(self, stage: str, klass: str, effect: str) -> float:
        """Selected-or-detected as a share of what the fixture actually contains."""
        have = getattr(self, stage).get(klass, {}).get(effect, 0)
        total = self.present.get(klass, {}).get(effect, 0)
        return have / total if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "halted": self.halted,
            "languages_inventoried": self.languages_inventoried,
            "parseable_files": self.parseable_files,
            "inventory_only_files": self.inventory_only_files,
            "present": self.present,
            "detected": self.detected,
            "selected": self.selected,
            "clusters": self.clusters,
            "graph_entities": self.graph_entities,
            "detected_entities": self.detected_entities,
            "entities_by_kind": self.entities_by_kind,
            "missed_entry_points": self.missed_entry_points,
            "selected_ids": self.selected_ids,
            "detected_but_unselected": self.detected_but_unselected,
            "class_coverage_detected": self.class_coverage("detected"),
            "class_coverage_selected": self.class_coverage("selected"),
        }


@dataclass
class DocumentMetrics:
    """What a finished requirements document actually contains."""

    source: str
    requirements: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    distinct_units_cited: int = 0
    file_types_cited: dict[str, int] = field(default_factory=dict)
    duplicate_titles: int = 0
    duplication_rate: float = 0.0
    citations: int = 0
    citations_resolved: int = 0
    citation_resolution_rate: float = 1.0
    empty_acceptance_criteria: int = 0
    empty_assumptions: int = 0
    empty_open_questions: int = 0
    confidence_histogram: dict[str, int] = field(default_factory=dict)
    relation_without_parent: int = 0
    # What the corpus can grade and a raw count cannot: the authored claims are half true and
    # half deliberately false, so "more requirements published" is only progress when the extra
    # ones are the supported half. These two say which half survived.
    supported_published: int = 0
    contradicted_published: int = 0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _candidates(entity) -> list[str]:
    attributes = entity.attributes or {}
    return [
        value.strip().casefold()
        for value in (
            entity.qualified_name,
            entity.name,
            str(attributes.get("handler") or ""),
            str(attributes.get("label") or ""),
            str(attributes.get("path") or ""),
        )
        if value and value.strip()
    ]


def _match_all(truth_items: list[dict], found: list) -> dict[str, str]:
    """Pair each entry point a fixture declares with the entity that is it.

    A fixture cites an entry point the way a reader would — a route, a topic, a command name —
    and the system may record that string in the qualified name, in a handler, or in a declared
    label. Checking each is what lets ground truth stay written in the domain's language rather
    than the extractor's.

    Exact matches are taken first and each entity is claimed at most once. Containment alone is
    ambiguous in exactly the case that matters: "GET /orders" is a substring of
    "GET /orders/{order_id}", so a greedy pass pairs the collection route with the item route,
    leaves the real one unclaimed, and reports a false miss.
    """
    matched: dict[str, str] = {}
    claimed: set[str] = set()

    for item in truth_items:
        needle = item["id"].strip().casefold()
        for entity in found:
            if entity.qualified_name in claimed:
                continue
            if needle in _candidates(entity):
                matched[item["id"]] = entity.qualified_name
                claimed.add(entity.qualified_name)
                break

    for item in truth_items:
        if item["id"] in matched:
            continue
        needle = item["id"].strip().casefold()
        # Most specific first, so a loose containment cannot steal a better-fitting entity.
        ranked = sorted(
            (
                entity
                for entity in found
                if entity.qualified_name not in claimed
                and any(needle in value for value in _candidates(entity))
            ),
            key=lambda entity: min(len(value) for value in _candidates(entity)),
        )
        if ranked:
            matched[item["id"]] = ranked[0].qualified_name
            claimed.add(ranked[0].qualified_name)
    return matched


def _nest(counter: Counter) -> dict[str, dict[str, int]]:
    nested: dict[str, dict[str, int]] = defaultdict(dict)
    for (klass, effect), count in counter.items():
        nested[klass][effect] = count
    return dict(nested)


def measure_structure(
    fixture: Path, *, max_clusters: int | None = None
) -> StructureMetrics:
    """Run the deterministic prefix over one fixture and measure entry-point coverage.

    `max_clusters` is exposed so a small fixture can be made to hit the cap deliberately. The
    selection defect only appears once a cap binds, and a corpus of realistic size would otherwise
    never reproduce it.
    """
    # Imported here so importing the harness does not drag in the whole pipeline.
    from agentic.capabilities.registry import CapabilityContext
    from agentic.config import Settings
    from agentic.runtime import prepare_run
    from agentic.schemas.core import KnowledgeGraph, RepositoryInventory
    from agentic.skills.pack import load_pack
    from agentic.state import initial_state
    from agentic.store import ArtifactStore

    truth = load_ground_truth(fixture)
    result = StructureMetrics(fixture=truth["name"])
    result.present = _nest(
        Counter((item["class"], item["effect"]) for item in truth["entry_points"])
    )

    workspace = Path(tempfile.mkdtemp())
    try:
        manifest = load_pack(REPO_ROOT / "codebase-requirement-skills")
        settings = Settings(runs_dir=workspace / "runs")
        if max_clusters is not None:
            settings = settings.model_copy(update={"max_clusters": max_clusters})
        prepared = prepare_run(source=fixture, settings=settings)
        state = initial_state(
            run_id="harness",
            pack="codebase-requirements",
            run_dir=str(prepared.run_dir),
            source_root=str(prepared.source_root),
            repository_name=truth["name"],
        )
        store = ArtifactStore(prepared.run_dir)

        def step(step_id: str):
            spec = manifest.step(step_id)
            ctx = CapabilityContext(
                state=state,
                step=spec,
                settings=settings,
                store=store,
                skill=manifest.skill_registry().load(spec.skill),
                payload={"manifest": manifest},
            )
            outcome = run_step_or_stub(spec, ctx)
            for name, value in (outcome.artifacts or {}).items():
                state["artifacts"][name] = store.save(name, value)
            return outcome

        # Read the prefix from the manifest rather than restating it. A hardcoded list silently
        # stops exercising any step added later, which is exactly what happened: a new extraction
        # step was inserted and every measurement here kept reporting the world without it.
        deterministic_prefix = []
        for spec in manifest.steps:
            if spec.id == "gate_graph":
                break
            deterministic_prefix.append(spec.id)

        for step_id in deterministic_prefix:
            outcome = step(step_id)
            if outcome.halt_reason:
                result.halted = outcome.halt_reason
                if "inventory" in state["artifacts"]:
                    inventory = store.load_model(
                        state["artifacts"]["inventory"], RepositoryInventory
                    )
                    result.languages_inventoried = dict(inventory.languages)
                    result.parseable_files = sum(1 for f in inventory.files if f.parseable)
                    result.inventory_only_files = len(inventory.files) - result.parseable_files
                result.missed_entry_points = [item["id"] for item in truth["entry_points"]]
                return result

        inventory = store.load_model(state["artifacts"]["inventory"], RepositoryInventory)
        result.languages_inventoried = dict(inventory.languages)
        result.parseable_files = sum(1 for f in inventory.files if f.parseable)
        result.inventory_only_files = len(inventory.files) - result.parseable_files

        graph = store.load_model(state["artifacts"]["graph"], KnowledgeGraph)
        result.graph_entities = len(graph.entities)
        result.entities_by_kind = dict(Counter(e.kind for e in graph.entities))

        from agentic.capabilities.graph_ops import BOUNDARY_KINDS

        found = [e for e in graph.entities if e.kind in BOUNDARY_KINDS]
        result.detected_entities = len(found)

        # Coverage is measured by matching each entry point the fixture ACTUALLY contains to
        # something the system found, and then grouping by the fixture's class — not by the
        # system's. The two vocabularies legitimately differ: a fixture calls something a
        # scheduled job because that is what it is, while the adapter that found it calls it a
        # registered entry because that is all the syntax proves. Grading on the system's labels
        # would score a correct detection as a miss, and would quietly reward renaming.
        matched = _match_all(truth["entry_points"], found)
        result.detected = _nest(
            Counter(
                (item["class"], item["effect"])
                for item in truth["entry_points"]
                if item["id"] in matched
            )
        )

        step("gate_graph")
        clusters = step("plan_clusters").artifacts["clusters"]
        result.clusters = len(clusters)

        chosen: set[str] = set()
        for cluster in clusters:
            chosen.add(cluster["boundary"])
            chosen.update(cluster.get("also_exposed_by") or [])
        result.selected = _nest(
            Counter(
                (item["class"], item["effect"])
                for item in truth["entry_points"]
                if matched.get(item["id"]) in chosen
            )
        )
        result.selected_ids = sorted(e.qualified_name for e in found if e.qualified_name in chosen)
        result.detected_but_unselected = sorted(
            e.qualified_name for e in found if e.qualified_name not in chosen
        )
        result.missed_entry_points = [
            item["id"] for item in truth["entry_points"] if item["id"] not in matched
        ]
        return result
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def measure_document(path: Path, source_root: Path | None = None) -> DocumentMetrics:
    """Measure a finished `requirements.json`."""
    from agentic.capabilities.verify import verify_location
    from agentic.schemas.core import SourceLocation

    records = json.loads(Path(path).read_text(encoding="utf-8"))
    result = DocumentMetrics(source=str(path), requirements=len(records))
    if not records:
        return result

    result.by_type = dict(Counter(r.get("type") for r in records))

    units: set[tuple] = set()
    extensions: Counter = Counter()
    citations = 0
    resolved = 0
    for record in records:
        for citation in record.get("evidence") or []:
            citations += 1
            units.add((citation.get("file"), citation.get("symbol")))
            extensions[str(citation.get("file", "")).rsplit(".", 1)[-1]] += 1
            if source_root is not None:
                check = verify_location(
                    SourceLocation(
                        file=citation["file"],
                        line_start=citation.get("line_start"),
                        line_end=citation.get("line_end"),
                        symbol=citation.get("symbol"),
                    ),
                    source_root,
                )
                resolved += 1 if check.ok else 0
    result.distinct_units_cited = len(units)
    result.file_types_cited = dict(extensions)
    result.citations = citations
    result.citations_resolved = resolved if source_root is not None else citations
    result.citation_resolution_rate = (
        result.citations_resolved / citations if citations else 1.0
    )

    titles = Counter(" ".join(str(r.get("title", "")).lower().split()) for r in records)
    result.duplicate_titles = sum(count - 1 for count in titles.values() if count > 1)
    result.duplication_rate = result.duplicate_titles / len(records)

    result.empty_acceptance_criteria = sum(
        1 for r in records if not r.get("acceptance_criteria")
    )
    result.empty_assumptions = sum(1 for r in records if not r.get("assumptions"))
    result.empty_open_questions = sum(1 for r in records if not r.get("open_questions"))
    result.confidence_histogram = dict(
        Counter(str(r.get("confidence")) for r in records)
    )
    result.relation_without_parent = sum(
        1 for r in records if r.get("parent_relation") and not r.get("parent_id")
    )
    return result


def _is_fixture_truth(path: Path) -> bool:
    """Whether a file in the truth directory describes a corpus fixture.

    Decided by shape rather than by filename. A naming convention only holds while everyone
    remembers it: the frozen baseline was excluded by an underscore prefix, and the very next file
    added to this directory had no underscore and became a phantom fixture with no source tree.
    A file that does not name a fixture and list its entry points is not one, whatever it is
    called, and this cannot be got wrong by adding a file.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(loaded, dict)
        and isinstance(loaded.get("name"), str)
        and isinstance(loaded.get("entry_points"), list)
    )


def corpus_fixtures() -> list[Path]:
    """Every fixture with a ground-truth file."""
    return sorted(CORPUS / path.stem for path in TRUTH.glob("*.json") if _is_fixture_truth(path))


# ------------------------------------------------------------------------------------------
# The document pipeline, measured without a model
# ------------------------------------------------------------------------------------------

CORRESPONDENCE_TRUTH = TRUTH / "correspondence.json"


def authored_claims(fixture: str) -> list[dict]:
    """Every ground-truth claim written for a fixture, true and false alike.

    Using authored claims rather than a stored model reply is what makes this suite runnable in
    CI. The deterministic half of the document pipeline -- consolidation, correspondence,
    recalibration, hedging -- is the half these packages changed, and it can be exercised in full
    against claims whose correctness is known by construction.
    """
    if not CORRESPONDENCE_TRUTH.exists():
        return []
    loaded = json.loads(CORRESPONDENCE_TRUTH.read_text(encoding="utf-8"))
    entry = loaded.get(fixture) or {}
    return [
        spec
        for key in ("supported", "contradicted", "entailment_only")
        for spec in entry.get(key, [])
    ]


def claim_verdicts(fixture: str) -> dict[str, bool]:
    """Claim title -> whether the corpus authored it as a true claim about that fixture."""
    if not CORRESPONDENCE_TRUTH.exists():
        return {}
    entry = (json.loads(CORRESPONDENCE_TRUTH.read_text(encoding="utf-8"))).get(fixture) or {}
    verdicts: dict[str, bool] = {}
    for key, supported in (("supported", True), ("entailment_only", True),
                           ("contradicted", False)):
        for spec in entry.get(key, []):
            verdicts[" ".join(spec["title"].split()).casefold()] = supported
    return verdicts


def measure_pipeline(fixture: Path) -> DocumentMetrics:
    """Run the deterministic document passes over the authored claims and measure what comes out.

    This is the half of the corpus grading that `measure_structure` cannot reach. Structure metrics
    say what the extractors found; these say what the document made of it -- whether claims are
    checkable, whether uncertainty is recorded, whether the same claim was published twice, and
    whether the confidence figures carry any information.
    """
    import tempfile

    from agentic.capabilities.confidence import recalibrate_confidence
    from agentic.capabilities.consolidate import consolidate_requirements
    from agentic.capabilities.correspondence import validate_correspondence
    from agentic.capabilities.hedging import hedge_requirements
    from agentic.capabilities.leakage import review_implementation_leakage
    from agentic.capabilities.resolution import resolve_candidates
    from agentic.capabilities.registry import CapabilityContext
    from agentic.capabilities.render import build_requirement_set
    from agentic.config import Settings
    from agentic.runtime import prepare_run
    from agentic.schemas.brd import TIER_SECTIONS, BRDItem, ProductBRD
    from agentic.schemas.core import SourceLocation
    from agentic.skills.pack import load_pack
    from agentic.state import initial_state
    from agentic.store import ArtifactStore

    name = fixture.name
    specs = authored_claims(name)
    empty = DocumentMetrics(source=f"pipeline:{name}")
    if not specs:
        return empty

    workspace = Path(tempfile.mkdtemp())
    settings = Settings(runs_dir=workspace / "runs", correspondence_entailment=False)
    prepared = prepare_run(source=fixture, settings=settings)
    state = initial_state(
        run_id="h",
        pack="codebase-requirements",
        run_dir=str(prepared.run_dir),
        source_root=str(prepared.source_root),
        repository_name=name,
    )
    store = ArtifactStore(prepared.run_dir)
    manifest = load_pack(REPO_ROOT / "codebase-requirement-skills")

    for spec in manifest.steps:
        ctx = CapabilityContext(
            state=state,
            step=spec,
            settings=settings,
            store=store,
            skill=manifest.skill_registry().load(spec.skill),
            payload={"manifest": manifest},
        )
        outcome = run_step_or_stub(spec, ctx)
        if outcome.halt_reason:
            return empty
        for artifact, value in (outcome.artifacts or {}).items():
            state["artifacts"][artifact] = store.save(artifact, value)
        if spec.id == "discover_entry_points":
            break

    # Claims are filed by the tier they declare, exactly as a fan-out worker would file them.
    sections: dict[str, list[BRDItem]] = defaultdict(list)
    for index, spec in enumerate(specs):
        item = BRDItem(
            title=spec["title"],
            statement=spec["statement"],
            rationale="Authored corpus claim.",
            confidence=1.0,
            requirement_type=spec.get("requirement_type", "functional"),
            abstraction_basis=_BASIS_FOR.get(spec.get("requirement_type", "functional")),
            evidence=[SourceLocation(**where) for where in spec["evidence"]],
            graph_path=[f"claim.{index}"],
        )
        sections[TIER_SECTIONS.get(item.requirement_type, "functional_requirements")].append(item)

    brd = ProductBRD(**sections).assign_ids()
    paths = {"brd": store.save("brd_h", brd), "graph": state["artifacts"]["graph"]}

    class _Ctx:
        def __init__(self) -> None:
            self.settings = settings
            self.state = {"artifacts": paths}
            self.step = None
            self.store = store

        def load(self, artifact, model=None):
            return store.load_model(paths[artifact], model)

    ctx = _Ctx()
    # The same order the manifest declares, so the graded numbers describe the pipeline that
    # actually runs. Leakage and resolution were absent here for the same reason they were absent
    # from the manifest, and their absence is what the grading could not see.
    for stage in (
        consolidate_requirements,
        validate_correspondence,
        recalibrate_confidence,
        review_implementation_leakage,
        resolve_candidates,
        hedge_requirements,
    ):
        paths["brd"] = store.save("brd_h", stage(ctx).artifacts["brd"])

    final = store.load_model(paths["brd"], ProductBRD).assign_ids()
    published = build_requirement_set(final, "harness")
    rendered = workspace / "requirements.json"
    rendered.write_text(json.dumps(published, indent=2), encoding="utf-8")

    measured = measure_document(rendered, Path(prepared.source_root))
    measured.source = f"pipeline:{name}"

    # Grade which half survived, not how many did. Half the authored claims are deliberately
    # false, so a pipeline that published all of them scored better on a raw count than one that
    # removed the false ones -- which is the wrong way round, and it is why the resolution stage
    # looked like a regression the first time it was graded.
    verdicts = claim_verdicts(name)
    for item in final.all_items():
        supported = verdicts.get(" ".join((item.title or "").split()).casefold())
        if supported is True:
            measured.supported_published += 1
        elif supported is False:
            measured.contradicted_published += 1
    return measured


_BASIS_FOR = {
    "business": "business_outcome",
    "functional": "external_capability",
    "system": "internal_behaviour",
    "business_rule": "domain_constraint",
    "technical_constraint": "platform_constraint",
    "non_functional": "quality_property",
    "observation": "implementation_detail",
}
