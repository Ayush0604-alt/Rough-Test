from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentic.capabilities.registry import CapabilityContext, get_capability
from agentic.config import Settings
from agentic.skills.pack import PackManifest, load_pack
from agentic.state import AgentState, initial_state
from agentic.store import ArtifactStore

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_APP = Path(__file__).parent / "fixtures" / "sample_app"
PACK_DIR = REPO_ROOT / "codebase-requirement-skills"

# The steps that turn a source tree into a graph with interaction boundaries in it. Named once
# because it is now five steps rather than one, and every test that needs a usable graph runs all
# of them — a graph without boundaries makes every downstream assertion vacuous, since nothing
# seeds a cluster and no requirement can trace anywhere.
EXTRACTION_STEPS = (
    "ingest",
    "graphify_extract",
    "adapt_graph",
    "propose_boundaries",
    "classify_boundaries",
    "discover_entry_points",
)


@pytest.fixture(scope="session")
def manifest() -> PackManifest:
    return load_pack(PACK_DIR)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(runs_dir=tmp_path / "runs", max_clusters=6, evidence_packet_chars=8000)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """A run directory holding a copy of the sample app, mirroring what `prepare_run` builds."""
    directory = tmp_path / "run"
    (directory / "artifacts").mkdir(parents=True)
    shutil.copytree(FIXTURE_APP, directory / "source")
    return directory


@pytest.fixture
def state(run_dir: Path) -> AgentState:
    return initial_state(
        run_id="test-run",
        pack="codebase-requirements",
        run_dir=str(run_dir),
        source_root=str(run_dir / "source"),
        repository_name="sample_app",
    )


@pytest.fixture
def runner(manifest: PackManifest, settings: Settings, state: AgentState):
    """Run one manifest step directly, threading artifacts through as the graph would.

    Lets a test exercise a capability in isolation without compiling or running the graph.
    """

    def _run(step_id: str, **payload):
        step = manifest.step(step_id)
        ctx = CapabilityContext(
            state=state,
            step=step,
            settings=settings,
            store=ArtifactStore(Path(state["run_dir"])),
            skill=manifest.skill_registry().load(step.skill),
            payload={"manifest": manifest, **payload},
        )
        result = run_step_or_stub(step, ctx)
        for name, value in result.artifacts.items():
            state.setdefault("artifacts", {})[name] = ctx.store.save(name, value)
        return result

    return _run


# --------------------------------------------------------------------------------------------
# The boundary classifier, stubbed
# --------------------------------------------------------------------------------------------

# Route, subscription, and schedule registrars, keyed by the undefined name a decorated handler
# references. This is a *stub of a model*, not production code: the real classifier is given the
# raw referenced names precisely so it can recognise a registrar nobody listed in advance. The list
# only has to cover the corpus fixtures.
_STUB_ENDPOINT = {"get", "post", "put", "patch", "delete", "route", "api_route", "head", "options"}
_STUB_SUBSCRIPTION = {"subscribe", "on", "handler", "listen", "consume"}
# Registrars whose name says the thing they register is a subcommand rather than a generic handler.
_STUB_CLI_REGISTRARS = ("parser", "cli", "command", "argparse")
_STUB_SCHEDULE = {"every", "schedule", "cron", "periodic", "interval"}


def stub_boundary_assignments(candidates: list[dict]) -> dict:
    """Classify candidates the way the model is asked to, from the same evidence.

    The suite runs the real pipeline with the model stubbed, so the one model call in the
    extraction phase needs a deterministic stand-in or no test can reach a boundary — and without
    boundaries, `plan_clusters` produces nothing and every downstream test is vacuous.

    Reads only the fields the real prompt is given: the referenced-but-undefined names, the name
    itself, and the inbound call count.
    """
    # A module that registers subcommands has a command-line entry as well as a script entry, and
    # they are the same function: `main` parses the arguments *and* is what the `__main__` guard
    # runs. One candidate can therefore carry two classes, which the adapter supports because each
    # boundary is identified by its class as well as its handler.
    has_subcommands = any(
        (candidate.get("registered_by") or []) and not (candidate.get("references_undefined_names") or [])
        for candidate in candidates
    )

    assignments = []
    for candidate in candidates:
        references = {
            name.rsplit(".", 1)[-1].casefold()
            for name in candidate.get("references_undefined_names") or []
        }
        bare = candidate["name"].strip(". ()").casefold()
        registrars = [name.casefold() for name in candidate.get("registered_by") or []]
        literals = [str(value) for value in candidate.get("literals_nearby") or []]
        routes = [value for value in literals if value.startswith("/")]
        effect = None
        identifier = None
        if references & _STUB_ENDPOINT or "responsewriter" in references:
            entry_class = "ENDPOINT"
            # The same read/write judgement the skill asks the model for, from the same evidence:
            # the registrar name says which. `effect` is what `_effect_agreement` compares a claim
            # against, so a stub that omits it silently disables a real correspondence check.
            verb = next(iter(sorted(references & _STUB_ENDPOINT)), None)
            if routes and verb:
                identifier = f"{verb.upper()} {routes[0]}"
            if references & {"get", "head", "options"}:
                effect = "read"
            elif references & {"post", "put", "patch", "delete"}:
                effect = "mutate"
        elif references & _STUB_SCHEDULE:
            entry_class = "SCHEDULED_JOB"
            identifier = literals[0] if literals else None
        elif references & _STUB_SUBSCRIPTION:
            entry_class = "EVENT_CONSUMER"
            identifier = literals[0] if literals else None
        elif registrars and any(
            hint in registrar for registrar in registrars for hint in _STUB_CLI_REGISTRARS
        ):
            entry_class = "CLI_COMMAND"
            identifier = literals[0] if literals else None
        elif candidate.get("module_level"):
            # A module-level control carrying its own label: a widget, a prompt, an input. What
            # the product calls it is the literal, which is what a reader would recognise.
            entry_class = "UI_ACTION"
            identifier = literals[0] if literals else None
        elif bare == "main" or (
            # An entry function in a file named for being one, which nothing in the repository
            # calls: what a `__main__` guard invokes. Graphify emits no node for the guard itself,
            # so the function it runs is the only thing there is to classify.
            not candidate["inbound_calls"]
            and not registrars
            and not references
            and Path(candidate["at"].split(":", 1)[0]).stem in {"main", "__main__"}
            # A private helper is not what a `__main__` guard runs, and an uncalled one is dead
            # code — which is exactly what sits in the sample fixture beside the real entry point.
            and not bare.startswith("_")
        ):
            entry_class = "SCRIPT_ENTRY"
        elif registrars:
            entry_class = "REGISTERED_ENTRY"
        else:
            entry_class = "NONE"
        assignments.append(
            {
                "qualified_name": candidate["qualified_name"],
                "entry_class": entry_class,
                "identifier": identifier,
                "effect": effect,
                "rationale": "stubbed classifier",
            }
        )
        if entry_class == "SCRIPT_ENTRY" and has_subcommands:
            assignments.append(
                {
                    "qualified_name": candidate["qualified_name"],
                    "entry_class": "CLI_ENTRY",
                    "identifier": None,
                    "effect": None,
                    "rationale": "stubbed classifier: also the command-line entry",
                }
            )
    return {"assignments": assignments}


def run_step_or_stub(spec, ctx):
    """Run one manifest step, standing in for the model where a step needs one.

    Lets a helper walk `manifest.steps` without special-casing the llm step inline in five files.
    """
    if spec.mode == "llm":
        if spec.id != "classify_boundaries":
            raise AssertionError(
                f"No stub for llm step {spec.id!r}; this helper only covers the extraction phase"
            )
        payload = ctx.store.load_json(ctx.state["artifacts"]["boundary_candidates"])
        from agentic.capabilities.registry import CapabilityResult

        return CapabilityResult(
            artifacts={"boundary_assignments": stub_boundary_assignments(payload["candidates"])}
        )
    return get_capability(spec.capability)(ctx)
