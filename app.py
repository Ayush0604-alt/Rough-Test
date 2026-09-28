"""Streamlit front end for the skill-pack agent.

The tab that matters is Requirements: every claim opens to show the actual source at the cited
lines. A requirements document you can check against the code in two clicks is the whole point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from agentic.capabilities.verify import read_slice
from agentic.config import get_settings
from agentic.graph.builder import describe_topology
from agentic.runtime import (
    available_packs,
    get_manifest,
    preflight,
    prepare_run,
    stream_run,
    successor_packs,
)
from agentic.schemas.brd import ProductBRD
from agentic.schemas.core import GateReport, KnowledgeGraph, RepositoryInventory, SourceLocation
from agentic.schemas.testspec import CoverageReport, TestSuite
from agentic.store import ArtifactStore

st.set_page_config(page_title="Skill-Driven Codebase Analyst", layout="wide")

STATUS_ICON = {
    "passed": ":white_check_mark:",
    "passed_with_warnings": ":warning:",
    "failed": ":x:",
    "running": ":hourglass_flowing_sand:",
}


def _load(state: dict[str, Any], name: str, model=None):
    path = (state.get("artifacts") or {}).get(name)
    if not path or not Path(path).exists():
        return None
    store = ArtifactStore(Path(state["run_dir"]))
    return store.load_model(path, model) if model else store.load_json(path)


# --------------------------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------------------------


# Set when a finished run has a pack that consumes its output. Applied to the sidebar widgets on
# the next rerun, because Streamlit will not let a widget's value be changed after it exists.
PENDING_CHAIN = "_pending_chain"

# The offer itself, which outlives the pre-fill: the sidebar is only kept consistent, while the
# button a reader actually presses lives beside the results it follows on from.
CHAIN_OFFER = "chain_offer"


def _apply_pending_chain() -> None:
    """Pre-select the next pack and its input, once, before the widgets are built.

    Chaining by hand meant copying a run directory path out of the log and pasting it back in, with
    the pack selector still on the pack that just finished — which is how a test-generation run
    came to be pointed at a codebase three times running.
    """
    pending = st.session_state.pop(PENDING_CHAIN, None)
    if not pending:
        return
    st.session_state["pack_choice"] = pending["pack"]
    # The previous run may have been started from an upload; the next one reads a directory.
    st.session_state["source_mode"] = "Local folder"
    st.session_state["source_path"] = pending["source"]
    # Kept, not popped. Pre-filling the sidebar told nobody anything: the message sat in a corner
    # of the sidebar while the main area still showed the finished run, so the next step read as
    # "nothing happened". The offer now survives to be rendered as a button above the results.
    st.session_state[CHAIN_OFFER] = pending


def sidebar() -> dict[str, Any]:
    settings = get_settings()
    _apply_pending_chain()

    with st.sidebar:
        st.title("Codebase Analyst")
        st.caption("Skill packs compile into a LangGraph workflow. Adding a pack adds no code.")

        packs = available_packs()
        if not packs:
            st.error("No skill pack found. A pack is a directory containing a `pack.yaml`.")
            st.stop()
        names = sorted(packs)
        st.session_state.setdefault("pack_choice", names[0])
        pack = st.selectbox("Skill pack", names, key="pack_choice")

        st.divider()
        st.subheader("Target codebase")
        st.session_state.setdefault("source_mode", "Local folder")
        mode = st.radio(
            "Source", ["Local folder", "Upload .zip"], horizontal=True, key="source_mode"
        )
        source_path, upload = None, None
        if mode == "Local folder":
            source_path = (
                st.text_input(
                    "Folder path", placeholder=r"C:\path\to\repository", key="source_path"
                )
                .strip()
                .strip('"')
            )
        else:
            upload = st.file_uploader("Repository archive", type=["zip"])

        st.divider()
        if settings.llm_configured:
            st.success(f"Azure OpenAI ready. Deployment: `{settings.azure_openai_deployment}`")
        else:
            missing = ", ".join(settings.missing_llm_settings())
            st.warning(
                f"Azure OpenAI incomplete. Missing: {missing}. Deterministic steps still run; "
                "the analysis will stop when it reaches a model step."
            )

        run_clicked = st.button("Run analysis", type="primary", width="stretch")

    return {
        "pack": pack,
        "source_path": source_path,
        "upload": upload,
        "run_clicked": run_clicked,
    }


# --------------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------------


def execute(pack: str, *, source_path: str | None = None, upload: Any = None) -> None:
    """Run one pack against one target.

    The target is passed in rather than read back off the sidebar widgets, so the hand-off button
    can start the next run directly instead of asking the reader to go and press another button.
    """
    settings = get_settings()
    manifest = get_manifest(pack)

    # A new run supersedes any offer left over from the last one.
    st.session_state.pop(CHAIN_OFFER, None)

    # Stop before ingesting anything, rather than after several minutes of extraction and
    # validation, when the run cannot possibly complete.
    if problems := preflight(manifest, settings):
        for problem in problems:
            st.error(problem)
        return

    try:
        if upload is not None:
            prepared = prepare_run(
                archive=upload, archive_name=upload.name, settings=settings
            )
        elif source_path:
            prepared = prepare_run(source=source_path, settings=settings)
        else:
            st.error("Choose a folder or upload an archive first.")
            return
    except Exception as exc:
        st.error(f"Could not read the target: {exc}")
        return

    st.session_state["repository_name"] = prepared.repository_name
    st.session_state["pack"] = pack

    total = len(manifest.steps)
    progress = st.progress(0.0, text="Starting")
    tracker = st.empty()
    seen: list[str] = []
    # Seed with the run's identity. `stream_run` yields only per-node *updates*, so values set
    # once at the start would otherwise never appear in the accumulated state.
    state: dict[str, Any] = {
        "run_id": prepared.run_id,
        "run_dir": str(prepared.run_dir),
        "source_root": str(prepared.source_root),
        "repository_name": prepared.repository_name,
        "pack": pack,
        "status": "running",
    }

    try:
        for node_name, update in stream_run(pack, prepared, settings=settings):
            for key, value in update.items():
                if key in {"completed_steps", "step_records", "fragments"}:
                    state[key] = (state.get(key) or []) + list(value or [])
                elif key in {"artifacts", "gate_reports", "metrics"}:
                    state[key] = {**(state.get(key) or {}), **(value or {})}
                else:
                    state[key] = value
            if node_name not in seen:
                seen.append(node_name)
            progress.progress(
                min(len(seen) / max(total, 1), 1.0), text=f"Completed: {node_name}"
            )
            tracker.dataframe(
                _records_table(state.get("step_records") or []),
                width="stretch",
                hide_index=True,
            )
    except Exception as exc:
        st.error(f"The run stopped unexpectedly: {type(exc).__name__}: {exc}")
        state.setdefault("status", "failed")
        state.setdefault("halt_reason", str(exc))

    progress.empty()
    st.session_state["final_state"] = state

    # Hand the run's output to whichever pack declares it consumes it. Generic: the successor is
    # read from the manifests, so a third pack declaring `consumes_pack` chains the same way and
    # nothing here names a pack. The results just produced survive the rerun because they are
    # already in `final_state`.
    if state.get("status") in {"passed", "passed_with_warnings"}:
        following = successor_packs(pack, prepared.run_dir)
        if following:
            next_pack, artifacts = following[0]
            st.session_state[PENDING_CHAIN] = {
                "pack": next_pack,
                "source": str(artifacts),
                "from_pack": pack,
                # Asked of the manifest, so this module names no pack and a third pack declaring
                # `consumes_pack` is offered with its own wording for free.
                "label": get_manifest(next_pack).action_label or f"Run {next_pack}",
            }
    st.rerun()


def chain_panel() -> dict[str, Any] | None:
    """The hand-off, offered where the reader is already looking.

    A finished run that another pack consumes used to announce itself in the sidebar while the main
    area still showed the run that had just ended, so the next step read as nothing having
    happened. The offer is a single primary button, and pressing it starts the next run — rather
    than pre-filling three widgets and hoping the reader finds a fourth.
    """
    offer = st.session_state.get(CHAIN_OFFER)
    if not offer:
        return None

    with st.container(border=True):
        left, right = st.columns([3, 1], vertical_alignment="center")
        with left:
            st.markdown(f"**{offer['label']}**")
            st.caption(
                f"`{offer['from_pack']}` finished. `{offer['pack']}` reads its published output — "
                "no need to point it anywhere."
            )
        with right:
            pressed = st.button(
                offer["label"], type="primary", width="stretch", key="chain_go"
            )
            if st.button("Dismiss", width="stretch", key="chain_dismiss"):
                st.session_state.pop(CHAIN_OFFER, None)
                st.rerun()
    return offer if pressed else None


def _records_table(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "Status": (
                f"{STATUS_ICON.get(record.get('status', ''), '')} "
                f"{record.get('status', '').replace('_', ' ')}"
            ).strip(),
            "Step": record.get("step", ""),
            "Mode": record.get("mode", ""),
            "Skill": record.get("skill", ""),
            "ms": round(float(record.get("duration_ms", 0)), 1),
            "Notes": "; ".join(record.get("messages") or [])[:160],
        }
        for record in records
    ]


# --------------------------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------------------------


def _cost_panel(state: dict[str, Any]) -> None:
    """What this run spent on models, read from the `llm_usage.json` the run wrote on finishing."""
    run_dir = state.get("run_dir")
    path = Path(run_dir) / "artifacts" / "llm_usage.json" if run_dir else None
    if path is None or not path.exists():
        st.caption("Model usage was not recorded for this run.")
        return
    usage = ArtifactStore(Path(run_dir)).load_json(str(path))
    total = usage.get("total") or {}
    st.subheader("Model usage and cost")
    columns = st.columns(4)
    columns[0].metric("Estimated cost", f"${float(total.get('estimated_usd', 0)):.4f}")
    columns[1].metric("Model calls", f"{int(total.get('calls', 0)):,}")
    columns[2].metric("Input tokens", f"{int(total.get('input_tokens', 0)):,}")
    columns[3].metric(
        "Cached input",
        f"{int(total.get('cached_input_tokens', 0)):,}",
        help="Input tokens the provider served from its prompt cache, billed at a lower rate.",
    )
    st.dataframe(
        [
            {
                "Step": name,
                "Calls": row.get("calls", 0),
                "Failed": row.get("failed_calls", 0),
                "Input tokens": row.get("input_tokens", 0),
                "Cached input": row.get("cached_input_tokens", 0),
                "Output tokens": row.get("output_tokens", 0),
                "Est. USD": round(float(row.get("estimated_usd", 0)), 4),
            }
            for name, row in (usage.get("steps") or {}).items()
        ],
        width="stretch",
        hide_index=True,
    )
    models = usage.get("models") or {}
    if models:
        st.caption(
            "Priced per model: "
            + "; ".join(
                # No "$" here: Streamlit reads a pair of them in markdown as LaTeX.
                f"{name} ({row.get('rate_source', '')}: "
                f"input {row['rates_usd_per_million']['input']} USD/M, "
                f"output {row['rates_usd_per_million']['output']} USD/M)"
                for name, row in models.items()
                if row.get("rates_usd_per_million")
            )
            + ". " + str(usage.get("note", ""))
        )


def tab_run(state: dict[str, Any], manifest) -> None:
    status = state.get("status", "running")
    columns = st.columns(4)
    columns[0].metric("Status", status.replace("_", " "))
    columns[1].metric("Steps completed", len(state.get("completed_steps") or []))
    # Named for what the fan-out actually was, decided by what the run produced
    # rather than by which pack it was: the test pack fans out over batches of
    # requirements, not over code.
    artifacts = state.get("artifacts") or {}
    fan_label = "Boundary clusters" if "graph" in artifacts else "Design batches"
    columns[2].metric(fan_label, len(state.get("fragments") or []))
    columns[3].metric("Repair attempts", state.get("repair_attempts", 0))

    if state.get("halt_reason"):
        st.error(state["halt_reason"])
        st.caption(
            "The workflow stops rather than producing a weaker document. A gate failure means "
            "the evidence could not support a trustworthy result."
        )

    _cost_panel(state)

    st.subheader("Steps")
    st.dataframe(
        _records_table(state.get("step_records") or []),
        width="stretch",
        hide_index=True,
    )

    with st.expander("Compiled workflow (from `pack.yaml`)"):
        st.dataframe(describe_topology(manifest), width="stretch", hide_index=True)
        st.caption(
            "Routing is deterministic and read from the manifest. No model decides what runs "
            "next, so no model can route around a failed gate."
        )


def tab_inventory(state: dict[str, Any]) -> None:
    inventory: RepositoryInventory | None = _load(state, "inventory", RepositoryInventory)
    if inventory is None:
        st.info("No inventory was produced.")
        return
    columns = st.columns(3)
    columns[0].metric("Files", len(inventory.files))
    columns[1].metric("Analysed in depth", sum(item.parseable for item in inventory.files))
    columns[2].metric("Total size", f"{inventory.total_bytes / 1024:,.0f} KB")
    if inventory.languages:
        st.bar_chart(inventory.languages)
    st.dataframe(
        [
            {
                "Path": item.path,
                "Language": item.language,
                "Bytes": item.size,
                "Analysis": "deep" if item.parseable else "inventory only",
            }
            for item in inventory.files
        ],
        width="stretch",
        hide_index=True,
    )


def tab_graph(state: dict[str, Any]) -> None:
    graph: KnowledgeGraph | None = _load(state, "graph", KnowledgeGraph)
    if graph is None:
        st.info("No knowledge graph was produced.")
        return
    metrics = (state.get("gate_reports", {}).get("gate_graph") or {}).get("metrics", {})

    columns = st.columns(4)
    columns[0].metric("Entities", len(graph.entities))
    columns[1].metric("Relationships", len(graph.relationships))
    columns[2].metric(
        "Extraction coverage", f"{float(metrics.get('extraction_coverage', 1.0)):.0%}"
    )
    columns[3].metric(
        "Ambiguity",
        f"{float(metrics.get('ambiguity_ratio', 0.0)):.0%}",
        help=(
            "Share of references that matched several candidates rather than resolving to one. "
            "High ambiguity means extraction is guessing more than it is resolving."
        ),
    )

    left, right = st.columns(2)
    left.write("**Entity kinds**")
    left.dataframe(
        [{"Kind": k, "Count": v} for k, v in (metrics.get("entity_kinds") or {}).items()],
        width="stretch",
        hide_index=True,
    )
    right.write("**Relationship kinds**")
    right.dataframe(
        [{"Kind": k, "Count": v} for k, v in (metrics.get("relationship_kinds") or {}).items()],
        width="stretch",
        hide_index=True,
    )

    diagram = (state.get("artifacts") or {}).get("file:workflows.mmd")
    if diagram and Path(diagram).exists():
        st.subheader("Reconstructed workflow")
        st.code(Path(diagram).read_text(encoding="utf-8"), language="mermaid")


def tab_gates(state: dict[str, Any]) -> None:
    reports = state.get("gate_reports") or {}
    if not reports:
        st.info("No gate has run yet.")
        return
    for name, raw in reports.items():
        report = GateReport.model_validate(raw)
        st.subheader(f"{name} - {'passed' if report.valid else 'FAILED'}")
        columns = st.columns(3)
        columns[0].metric("Checks", report.checks_run)
        columns[1].metric("Errors", report.errors)
        columns[2].metric("Warnings", report.warnings)
        if report.issues:
            with st.expander(f"{len(report.issues)} issue(s)"):
                st.dataframe(
                    [
                        {
                            "Severity": issue.severity.value,
                            "Code": issue.code,
                            "Subject": issue.subject_id or issue.entity_id or "",
                            "Message": issue.message,
                        }
                        for issue in report.issues
                    ],
                    width="stretch",
                    hide_index=True,
                )


def tab_requirements(state: dict[str, Any]) -> None:
    brd: ProductBRD | None = _load(state, "brd", ProductBRD)
    if brd is None:
        st.info("No requirements were produced.")
        return

    source_root = Path(state["source_root"]) if state.get("source_root") else None

    if brd.product_purpose:
        st.subheader("Product purpose")
        st.write(brd.product_purpose.statement)
    else:
        st.warning(
            "A product purpose could not be established from the evidence. Saying so is the "
            "correct outcome; inventing a plausible one is not."
        )
    if brd.business_problem:
        st.subheader("Business context")
        st.write(brd.business_problem.statement)

    # Every family the schema publishes, not a hand-maintained subset. Looking a heading up by
    # `.get` with a derived fallback is what stops this drifting again: adding a section to the
    # schema used to break this tab with a KeyError, and the supplementary families never
    # appeared here at all.
    for section, items in brd.every_section().items():
        st.subheader(f"{_section_label(section)} ({len(items)})")
        if section == "observations" and items:
            st.caption(
                "Behaviour the code exhibits that the evidence does not show to be required of "
                "the product. Kept as evidence and excluded from the requirement set."
            )
        if not items:
            st.caption("Nothing in this section could be grounded in the evidence.")
            continue
        for item in items:
            with st.container(border=True):
                st.markdown(f"**{item.id} - {item.title}**")
                st.write(item.statement)
                meta = st.columns(3)
                meta[0].caption(f"Confidence {item.confidence:.2f}")
                meta[1].caption(f"{len(item.evidence)} citation(s)")
                if item.parent_id:
                    # The relation is the point of the link; "realises" was only ever right for a
                    # capability under an outcome, and read as nonsense on a rule or observation.
                    relation = item.parent_relation or "relates to"
                    meta[2].caption(f"{relation.capitalize()} {item.parent_id} — {item.parent}")
                if item.abstraction_basis:
                    st.caption(f"Classified on: {item.abstraction_basis.replace('_', ' ')}")
                if item.rationale:
                    st.caption(item.rationale)
                _render_evidence(item, source_root)


SECTION_LABELS = {
    "business_requirements": "Business requirements",
    "functional_requirements": "Functional requirements",
    "system_requirements": "System requirements",
    "business_rules": "Business rules",
    "technical_constraints": "Technical constraints",
    "success_criteria": "Success criteria",
    "data_requirements": "Data requirements",
    "integration_requirements": "Integration requirements",
    "non_functional_requirements": "Non-functional requirements",
    "reporting_requirements": "Reporting requirements",
    "observations": "Implementation observations (not requirements)",
}


def _section_label(section: str) -> str:
    """A heading for a section, deriving one rather than failing on an unknown name."""
    return SECTION_LABELS.get(section, section.replace("_", " ").capitalize())


def _render_evidence(item, source_root: Path | None) -> None:
    """Open every citation to the real source, so a reader can check the claim in place."""
    if not item.evidence or source_root is None:
        return
    with st.expander("Evidence"):
        if item.graph_path:
            st.caption("Trace: " + " -> ".join(item.graph_path))
        for location in item.evidence:
            citation = SourceLocation.model_validate(location.model_dump())
            start, lines = read_slice(source_root, citation, context=2)
            st.markdown(f"`{citation.compact()}`")
            if lines:
                st.code(
                    "\n".join(
                        f"{start + offset:>5} | {text}" for offset, text in enumerate(lines)
                    ),
                    language="python",
                )
            else:
                st.error("This citation does not resolve to readable source.")


VERDICT_NOTE = {
    "sufficient": "every activated dimension and usable criterion is covered",
    "positive_only": "states a refusal or a limit, but only the happy path is tested",
    "thin": "some activated dimensions are uncovered",
    "none": "planned for, and got nothing",
    "deferred": "routed away, with a reason",
    "excluded": "never eligible for a case",
}

RISK_ICON = {"high": ":red_circle:", "medium": ":large_orange_circle:", "low": ":white_circle:"}


def tab_tests(state: dict[str, Any]) -> None:
    """The test pack's own output, read the way the requirements tab reads.

    Its result used to be visible only as raw Markdown under Deliverables, which made the second
    pack feel like it had produced a file rather than a reviewable set of cases.
    """
    suite: TestSuite | None = _load(state, "test_suite", TestSuite)
    if suite is None:
        st.info("No test suite was produced.")
        return

    coverage: CoverageReport | None = _load(state, "coverage", CoverageReport)
    cases = suite.cases()

    columns = st.columns(4)
    columns[0].metric("Scenarios", len(suite.scenarios))
    columns[1].metric("Test cases", len(cases))
    columns[2].metric("Requirements covered", len(suite.covered_requirement_ids()))
    columns[3].metric(
        "Deferred",
        len(suite.deferred),
        help="Requirements deliberately given no case, each with a recorded reason.",
    )

    if coverage and coverage.verdicts:
        st.subheader("Coverage")
        st.dataframe(
            [
                {
                    "Verdict": verdict.replace("_", " "),
                    "Requirements": count,
                    "Meaning": VERDICT_NOTE.get(verdict, ""),
                }
                for verdict, count in sorted(
                    coverage.verdicts.items(), key=lambda pair: -pair[1]
                )
            ],
            width="stretch",
            hide_index=True,
        )
        if uncovered := coverage.uncovered_criteria():
            with st.expander(f"{len(uncovered)} acceptance criterion/criteria with no case"):
                st.dataframe(
                    [
                        {"Requirement": entry.requirement_id, "Criterion": entry.criterion_text}
                        for entry in uncovered
                    ],
                    width="stretch",
                    hide_index=True,
                )

    if not suite.scenarios:
        st.warning("No scenario could be designed from this requirement set.")

    for scenario in suite.scenarios:
        st.subheader(f"{scenario.title or scenario.id} ({len(scenario.cases)})")
        traced = ", ".join([scenario.requirement_id, *scenario.also_covers]).strip(", ")
        st.caption(f"Traces to {traced}" if traced else "Traces to nothing recorded.")
        if scenario.context:
            st.write(scenario.context)
        if scenario.preconditions:
            st.caption("Shared preconditions: " + "; ".join(scenario.preconditions))

        for case in scenario.cases:
            with st.container(border=True):
                st.markdown(f"**{case.id} - {case.title}**")
                st.write(case.objective)
                meta = st.columns(3)
                meta[0].caption(
                    f"{RISK_ICON.get(case.risk, '')} {case.risk.capitalize()} risk".strip()
                )
                meta[1].caption(f"{case.dimension.replace('_', ' ').capitalize()}")
                meta[2].caption(f"Derived: {case.derivation}")
                if case.requirement_ids:
                    st.caption("Validates " + ", ".join(case.requirement_ids))
                if case.risk_reason:
                    st.caption(case.risk_reason)
                _render_case_detail(case)

    if suite.deferred:
        st.subheader(f"Deferred ({len(suite.deferred)})")
        st.caption(
            "Requirements that will not get a case. Recorded as honest gaps rather than dropped, "
            "so the set states what it does not check."
        )
        st.dataframe(
            [
                {
                    "Requirement": entry.requirement_id,
                    "Route": entry.route,
                    "Reason": entry.reason,
                }
                for entry in suite.deferred
            ],
            width="stretch",
            hide_index=True,
        )


def _render_case_detail(case) -> None:
    """Steps and expected results, folded away so a scenario stays scannable."""
    with st.expander("Steps and expected results"):
        if case.preconditions:
            st.markdown("**Preconditions**")
            for line in case.preconditions:
                st.markdown(f"- {line}")
        if case.inputs:
            st.markdown("**Inputs**")
            st.dataframe(
                [entry.model_dump() for entry in case.inputs],
                width="stretch",
                hide_index=True,
            )
        if case.steps:
            st.markdown("**Steps**")
            for index, line in enumerate(case.steps, start=1):
                st.markdown(f"{index}. {line}")
        if case.expected_results:
            st.markdown("**Expected results**")
            for line in case.expected_results:
                st.markdown(f"- {line}")
        if case.assumptions:
            st.markdown("**Assumptions**")
            st.caption("Supplied by the designer beyond what the requirement states.")
            for line in case.assumptions:
                st.markdown(f"- {line}")
        if case.notes:
            st.markdown("**Notes**")
            st.caption(case.notes)


def tab_deliverables(state: dict[str, Any]) -> None:
    files = {
        name.removeprefix("file:"): path
        for name, path in (state.get("artifacts") or {}).items()
        if name.startswith("file:")
    }
    if not files:
        st.info("No deliverables were produced.")
        return
    mimes = {".md": "text/markdown", ".csv": "text/csv", ".mmd": "text/plain"}
    for name, path in sorted(files.items()):
        file_path = Path(path)
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8")
        with st.expander(f"{name} ({len(text):,} characters)"):
            st.download_button(
                "Download",
                data=text,
                file_name=name,
                mime=mimes.get(file_path.suffix, "text/plain"),
                key=f"download-{name}",
            )
            if name.endswith(".csv"):
                st.text(text[:20_000])
            elif name.endswith(".mmd"):
                st.code(text, language="mermaid")
            else:
                st.markdown(text)


# --------------------------------------------------------------------------------------------


def landing(pack: str) -> None:
    """The pre-run view, speaking for whichever pack is selected.

    It used to open with the requirements pack's headline whatever was chosen, so selecting the
    test pack produced a page describing a different job than the one about to run.
    """
    manifest = get_manifest(pack)
    st.title(manifest.action_label or "Analyse a codebase")
    st.markdown(manifest.description)
    if manifest.consumes_pack:
        st.info(
            f"This pack reads what `{manifest.consumes_pack}` publishes. Either point it at that "
            f"run's `artifacts/` folder, or run `{manifest.consumes_pack}` first and take the "
            "hand-off offered when it finishes."
        )
    else:
        st.caption(
            "Every statement this produces traces to a real file and line. A claim that cannot be "
            "grounded in reachable, executable code is dropped rather than reworded."
        )
    st.subheader("Compiled workflow")
    st.dataframe(describe_topology(manifest), width="stretch", hide_index=True)
    with st.expander("Skills governing this pack"):
        registry = manifest.skill_registry()
        for name in registry.available():
            skill = registry.load(name)
            st.markdown(f"**{name}** - `{skill.sha256[:12]}`")
            st.caption(skill.description)


def _views(state: dict[str, Any], manifest) -> list[tuple[str, Any]]:
    """The tabs this run actually earned.

    A fixed six-tab list was written for the requirements pack, so a test run showed an empty
    "Requirements" tab and no view of its own suite at all. Driven by the artifacts on disk, each
    pack gets the tabs it produced and none it did not — and a third pack needs no change here as
    long as it publishes an artifact one of these views knows how to read.
    """
    artifacts = state.get("artifacts") or {}
    views: list[tuple[str, Any]] = [("Run", lambda: tab_run(state, manifest))]
    if "inventory" in artifacts:
        views.append(("Inventory", lambda: tab_inventory(state)))
    if "graph" in artifacts:
        views.append(("Knowledge graph", lambda: tab_graph(state)))
    if state.get("gate_reports"):
        views.append(("Gates", lambda: tab_gates(state)))
    if "brd" in artifacts:
        views.append(("Requirements", lambda: tab_requirements(state)))
    if "test_suite" in artifacts:
        views.append(("Test cases", lambda: tab_tests(state)))
    views.append(("Deliverables", lambda: tab_deliverables(state)))
    return views


def main() -> None:
    choice = sidebar()
    if choice["run_clicked"]:
        execute(
            choice["pack"], source_path=choice["source_path"], upload=choice["upload"]
        )

    state = st.session_state.get("final_state")
    if not state:
        landing(choice["pack"])
        return

    manifest = get_manifest(state.get("pack") or choice["pack"])

    heading, status = st.columns([3, 1], vertical_alignment="center")
    heading.title(st.session_state.get("repository_name", "Analysis"))
    run_status = state.get("status", "running")
    status.metric(
        "Run", f"{STATUS_ICON.get(run_status, '')} {run_status.replace('_', ' ')}".strip()
    )
    st.caption(f"Produced by `{manifest.pack}`")

    # Offered before the results, not after: it is what to do next, and burying it under six tabs
    # is what made the hand-off invisible in the first place.
    if accepted := chain_panel():
        execute(accepted["pack"], source_path=accepted["source"])

    views = _views(state, manifest)
    for tab, (_, render) in zip(st.tabs([label for label, _ in views]), views):
        with tab:
            render()


main()
