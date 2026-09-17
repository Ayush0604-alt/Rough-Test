# 🔍 Architectural Verification Report: Uploaded Diagram vs. Codebase

> **File:** `MDfiles/check.md`  
> **Document Purpose:** Detailed verification and accuracy audit comparing the uploaded architecture diagram (*"UI-Aware Test Generation and Execution Architecture"*) against the actual `TraceBackAnalysis` codebase.

---

## 📑 Table of Contents

1. [Executive Summary & Verdict](#1-executive-summary--verdict)
2. [Visual vs. Codebase Mapping Matrix](#2-visual-vs-codebase-mapping-matrix)
3. [Deep Dive: What IS in the Codebase](#3-deep-dive-what-is-in-the-codebase)
   - [Box 1: Existing Codebase (Pack 1 & Pack 2)](#box-1-existing-codebase-pack-1--pack-2)
4. [Deep Dive: What is NOT in the Codebase](#4-deep-dive-what-is-not-in-the-codebase)
   - [Box 2: New User Inputs](#box-2-new-user-inputs)
   - [Phase: UI Discovery and Analysis (Steps 3–6)](#phase-ui-discovery-and-analysis-steps-36)
   - [Phase: Test Realization and Preparation (Steps 7–9)](#phase-test-realization-and-preparation-steps-79)
   - [Phase: Test Execution (Steps 10–12)](#phase-test-execution-steps-1012)
   - [Phase: Final Outputs (Step 13)](#phase-final-outputs-step-13)
5. [Architectural Alignment Analysis](#5-architectural-alignment-analysis)
   - [Is the Diagram Accurate to the Project's Vision?](#is-the-diagram-accurate-to-the-projects-vision)
   - [Design Principles: Framework Agnosticism vs. Live Adapters](#design-principles-framework-agnosticism-vs-live-adapters)
6. [Blueprint: Implementing the Diagram as Pack 3](#6-blueprint-implementing-the-diagram-as-pack-3)
   - [Proposed Directory & Pack Structure](#proposed-directory--pack-structure)
   - [State & Schema Additions](#state--schema-additions)
   - [Graph Orchestration Flow](#graph-orchestration-flow)

---

## 1. Executive Summary & Verdict

### 🎯 Core Verdict
* **As an Architectural Target / Future Extension (Pack 3):** **100% ACCURATE & HIGHLY ALIGNED.**  
  The diagram represents the exact downstream **UI / Playwright Adapter Pack** envisioned in the project's design docs. In fact, Box 1 is explicitly titled `"1. Existing Codebase (Unchanged)"` and lists the exact published artifacts (`requirements.json` and `test_specs.json`) produced by the current system.
* **As Current Codebase Implementation:** **PARTIALLY IMPLEMENTED (Box 1 is 100% complete; Boxes 2–13 are 0% implemented).**  
  The current repository is strictly a **static codebase reverse-engineering and test-specification engine**. It has no web browser automation, live crawler, DOM inspector, Playwright execution engine, or live failure classifier.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            STATUS AT A GLANCE                                │
├──────────────────────────────────────┬───────────────────────────────────────┤
│ Tier / Component                     │ Codebase Status                       │
├──────────────────────────────────────┼───────────────────────────────────────┤
│ 1. Existing Codebase (Unchanged)     │ ✅ 100% Implemented (Pack 1 & Pack 2) │
│ 2. New User Inputs (URL, Auth, etc.) │ ❌ 0% Not Implemented                │
│ 3. Runtime Input Extension           │ ❌ 0% Not Implemented                │
│ 4. Playwright UI Discovery           │ ❌ 0% Not Implemented                │
│ 5. UI Knowledge Graph Construction   │ ❌ 0% Not Implemented                │
│ 6. UI Discovery Gate & Gap Report    │ ❌ 0% Not Implemented                │
│ 7. UI-Aware Test Realization         │ ❌ 0% Not Implemented                │
│ 8. Executable Test Plan              │ ❌ 0% Not Implemented                │
│ 9. Pre-Execution Gate & Blocked Rep. │ ❌ 0% Not Implemented                │
│ 10. Playwright Execution             │ ❌ 0% Not Implemented                │
│ 11. Result Collection                │ ❌ 0% Not Implemented                │
│ 12. Failure Classification           │ ❌ 0% Not Implemented                │
│ 13. Final Reports (Drift / Live Ex.) │ ❌ 0% Not Implemented                │
└──────────────────────────────────────┴───────────────────────────────────────┘
```

---

## 2. Visual vs. Codebase Mapping Matrix

The table below breaks down each box, step, and deliverable shown in the uploaded diagram against the existing codebase:

| Diagram Component | Sub-Items / Functionality | Present in Code? | Existing Code References / Details | Missing Code / Gaps |
|---|---|:---:|---|---|
| **Box 1: Existing Codebase** | • Codebase analysis<br>• Requirements gen<br>• Framework-independent test gen<br>• `requirements.json`<br>• `test_specs.json` | **YES** ✅ | • [codebase-requirement-skills/pack.yaml](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/codebase-requirement-skills/pack.yaml)<br>• [test-generation-skills/pack.yaml](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/test-generation-skills/pack.yaml)<br>• [src/agentic/capabilities/ingest.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/capabilities/ingest.py)<br>• [src/agentic/capabilities/graphify.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/capabilities/graphify.py)<br>• [src/agentic/capabilities/testgen/](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/capabilities/testgen/) | Fully implemented. Produces evidence-grounded BRD and abstract test suites. |
| **Box 2: New User Inputs** | • Website URL<br>• Environment<br>• Auth reference<br>• User roles<br>• Crawl policy | **NO** ❌ | • [app.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/app.py) & [src/agentic/runtime.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/runtime.py) only accept local directories / ZIPs / artifacts folders. | Need input schemas, UI fields, and CLI args for live URL, env variables, secrets/tokens, roles, and crawl safety limits. |
| **Step 3: Runtime Input Extension** | • Validate URL<br>• Validate environment<br>• Validate roles & policies | **NO** ❌ | None | Need deterministic validator node ensuring target URL is reachable, auth secrets exist, and crawl policies are bounded. |
| **Step 4: Playwright UI Discovery** | • Launch browser<br>• Authenticate<br>• Crawl reachable screens<br>• Inspect DOM & accessibility info | **NO** ❌ | In [src/agentic/capabilities/testgen/quality.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/capabilities/testgen/quality.py#L53), `playwright` is only in a forbidden framework keyword filter. | Playwright is not a dependency in [pyproject.toml](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/pyproject.toml). No crawler or headless browser runtime exists. |
| **Step 5: UI Knowledge Graph Construction** | • Screens & Elements<br>• Transitions<br>• Navigation paths<br>• Screenshots & positions | **NO** ❌ | Current graph is `CodeGraph` ([src/agentic/schemas/core.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/schemas/core.py)), an AST/dependency model of source code. | No `UIGraph`, `ScreenNode`, `UIElement`, or `TransitionEdge` models. No DOM tree to knowledge graph compiler. |
| **Step 6: UI Discovery Gate** | • Validate screen graph<br>• Report blocked areas<br>• `crawl_gaps.md` | **NO** ❌ | Existing gates: `gate_boundaries`, `gate_requirements`, `gate_input`, `gate_tests`. | Need a gate node evaluating screen coverage and writing `crawl_gaps.md` when pages are unreachable or auth fails. |
| **Step 7: UI-Aware Test Realization** | • Match requirements/tests to UI screens<br>• Add missing navigation steps<br>• Bind actions to semantic elements | **NO** ❌ | Pack 2 purposely creates abstract steps (e.g. *"submit a removal request"*). | Need an adapter node/LLM step to resolve abstract actions into concrete UI selectors and navigation sequences. |
| **Step 8: Executable Test Plan** | • Starting screen<br>• Navigation path<br>• Element bindings<br>• Inputs, Actions, Assertions | **NO** ❌ | Current schema is `TestSuite` / `TestCase` in [src/agentic/schemas/testspec.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/schemas/testspec.py). | Need `ExecutableTestSuite` / `PlaywrightTestPlan` schema containing concrete selectors (CSS, XPath, accessibility-role), timeouts, and fixtures. |
| **Step 9: Pre-Execution Gate** | • Check navigation completeness<br>• Selector uniqueness<br>• Auth & Test data readiness<br>• Blocked Test Report | **NO** ❌ | None | Need deterministic pre-flight check verifying all required DOM selectors exist in the UI graph before launching execution. |
| **Step 10: Playwright Execution** | • Create browser context<br>• Authenticate<br>• Execute typed actions<br>• Perform assertions<br>• Capture evidence | **NO** ❌ | None | Need Playwright test runner worker pool executing actions against live browser instances with retry/timeout management. |
| **Step 11: Result Collection** | • Screenshots<br>• Videos<br>• Playwright traces (.zip)<br>• Console & network logs | **NO** ❌ | [src/agentic/store.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/store.py) stores JSON/MD run artifacts. | Need media artifact collector for `.webm`, `.png`, trace `.zip`, and `.har` network captures stored under `runs/<run-id>/artifacts/execution/`. |
| **Step 12: Failure Classification** | • Application failure<br>• Locator drift<br>• Navigation failure<br>• Authentication failure<br>• Test-data failure<br>• Environment failure | **NO** ❌ | None | Need deterministic/LLM diagnostic classifier that analyzes Playwright exceptions, trace logs, and DOM snapshots to pinpoint root causes. |
| **Step 13: Final Reports** | • Execution report (`execution.md`)<br>• Requirement coverage<br>• UI drift report (`ui_drift.md`)<br>• Failed/blocked test summary | **NO** ❌ | Deliverable renderer in [src/agentic/capabilities/testgen/render.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/capabilities/testgen/render.py) only renders abstract test specs & coverage. | Need markdown and HTML report renderers for live execution passes, failure logs, and UI drift analysis. |

---

## 3. Deep Dive: What IS in the Codebase

The left side of the diagram (**Box 1: Existing Codebase**) represents everything currently implemented across the 2 skill packs:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     BOX 1: EXISTING CODEBASE (100% PRESENT)                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   1. CODEBASE ANALYSIS                                                          │
│      ├── Repository Ingestion: src/agentic/capabilities/ingest.py               │
│      ├── AST / Tree-sitter extraction: graphifyy library                        │
│      ├── Structural CodeGraph: src/agentic/capabilities/graphify.py             │
│      └── Boundary Discovery: src/agentic/capabilities/boundaries.py             │
│                                                                                 │
│   2. REQUIREMENTS GENERATION (Pack 1: codebase-requirement-skills)              │
│      ├── Evidence-grounded synthesis: src/agentic/capabilities/claims.py        │
│      ├── Consolidation & conflict resolution: capabilities/resolution.py        │
│      ├── Quality Gates & Repair: capabilities/gates.py, quarantine.py           │
│      └── Output Artifacts: requirements.json (brd.json), requirements.md,       │
│                            traceability.md, quarantine.md                       │
│                                                                                 │
│   3. FRAMEWORK-INDEPENDENT TEST GENERATION (Pack 2: test-generation-skills)     │
│      ├── Input Qualification: capabilities/testgen/qualify.py, gates.py         │
│      ├── Signal & Risk Analysis: capabilities/testgen/signals.py, batching.py   │
│      ├── Test Design LLM Fan-out: test-generation-skills/skills/risk-based-...  │
│      ├── Assembly & Deduplication: capabilities/testgen/assemble.py, dedupe.py  │
│      ├── Test Quality Gate & Repair: capabilities/testgen/validate.py           │
│      └── Output Artifacts: test_specs.json (test_suite.json),                   │
│                            test_specifications.md, test_coverage.md            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Why the Existing Codebase Stops at Framework-Independent Tests
In [test-generation-skills/pack.yaml](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/test-generation-skills/pack.yaml#L10-L14) and [src/agentic/capabilities/testgen/quality.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/capabilities/testgen/quality.py#L52-L60), there is an explicit architectural rule:
> *"Turn a generated requirements artifact into traceable, risk-aware test specifications that are independent of any test framework, UI, protocol, or language... Framework-specific adapters are a later, separate pack."*

The codebase enforces this by actively filtering out and repairing any tests that contain words like `playwright`, `selenium`, `cypress`, `click`, `button`, `endpoint`, or `DELETE request`.

---

## 4. Deep Dive: What is NOT in the Codebase

Everything from **Box 2 through Step 13** in the diagram is **not yet in the codebase**. Here is an exact breakdown of what each missing component entails:

### Box 2: New User Inputs
* **Missing Components:**
  - Live target URL (e.g. `https://staging.app.io`)
  - Environment descriptor (e.g. `staging`, `local-docker`, `production`)
  - Authentication credentials / storage state (cookies, bearer tokens, login credentials)
  - User roles (e.g. `admin`, `standard_user`, `anonymous`)
  - Crawl policy (depth limits, URL exclusion regexes, safe-method constraints to prevent destructive mutations like `POST /delete-all`)
* **Current State:** The Streamlit UI ([app.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/app.py)) and CLI ([src/agentic/cli.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/cli.py)) only accept target directory paths or ZIP archives.

---

### Phase: UI Discovery and Analysis (Steps 3–6)

#### Step 3: Runtime Input Extension
* **Missing Components:** A deterministic node validating:
  - Network reachability and DNS resolution of the provided URL
  - Presence of required secrets/credentials
  - Sanitization and enforcement of crawl policies (e.g. preventing the crawler from leaving the target domain)

#### Step 4: Playwright UI Discovery
* **Missing Components:**
  - Headless/headed Playwright browser management (`playwright.async_api` or subprocess workers)
  - Form-based login and session persistence (`storageState`)
  - Intelligent web crawler discovering routes, modals, dynamic single-page application (SPA) states
  - DOM tree inspector and Accessibility Tree extractor (`aria-snapshot`, semantic roles)

#### Step 5: UI Knowledge Graph Construction
* **Missing Components:**
  - A graph schema modeling UI topology:
    - **Nodes:** `ScreenNode` (page/route/state), `UIElementNode` (buttons, inputs, tables, dropdowns with accessibility roles, bounding box coordinates, CSS selectors)
    - **Edges:** `TransitionEdge` (click/type/navigation actions that transition from Screen A to Screen B)
  - Visual artifacts: Annotated viewport screenshots marking interactable elements with bounding boxes

#### Step 6: UI Discovery Gate & Gap Report (`crawl_gaps.md`)
* **Missing Components:**
  - Deterministic gate checking whether critical functional areas (hinted by `requirements.json`) were discovered in the live UI
  - Branching: If key routes are blocked or undiscovered (e.g., 403 Forbidden, broken auth), halts execution and generates `crawl_gaps.md`

---

### Phase: Test Realization and Preparation (Steps 7–9)

#### Step 7: UI-Aware Test Realization
* **Missing Components:**
  - The adapter engine that bridges **Abstract Requirements/Tests** (`test_specs.json`) and the **Live UI Graph** (`ui_graph.json`):
    - Maps abstract intent (e.g. *"User submits valid login details"*) to concrete UI actions (e.g. `fill(#email, "user@test.com")`, `fill(#password, "...")`, `click(#submit-btn)`)
    - Automatically synthesizes missing intermediate navigation steps (e.g. clicking through menus to reach a deep settings page)
    - Selects robust, semantic selectors (preferring `getByRole`, `getByLabel`, `getByTestId` over brittle XPath)

#### Step 8: Executable Test Plan
* **Missing Components:**
  - Concrete executable test specification data structure:
    - Target starting URL / screen
    - Ordered sequence of typed Playwright actions (`click`, `fill`, `selectOption`, `press`, `hover`)
    - Assertions (`toBeVisible`, `toHaveText`, `toHaveURL`, `toHaveCount`)
    - Fixture and test data bindings

#### Step 9: Pre-Execution Gate & Blocked Test Report
* **Missing Components:**
  - Pre-flight validation gate verifying:
    - Selector uniqueness (ensuring selectors do not match multiple ambiguous elements)
    - Navigation path continuity
    - Test data availability
  - Blocked branch: Unresolvable test cases are quarantined into a **Blocked Test Report** without wasting browser execution time

---

### Phase: Test Execution (Steps 10–12)

#### Step 10: Playwright Execution
* **Missing Components:**
  - Parallel Playwright execution worker harness
  - Isolated browser contexts per test case to avoid state contamination
  - Dynamic timeout, retry, and screenshot capture on assertion failures

#### Step 11: Result Collection
* **Missing Components:**
  - Storage of rich execution artifacts in `runs/<run-id>/artifacts/execution/`:
    - Full-page screenshots at each assertion step
    - Video recordings (`.webm`) of failed test runs
    - Playwright trace zip files for time-travel debugging
    - Browser console logs and network activity logs (HAR files)

#### Step 12: Failure Classification
* **Missing Components:**
  - Diagnostic engine that analyzes failed steps and categorizes errors into distinct failure buckets:
    1. **Application Failure:** Assertion failed because the system behaved incorrectly (real bug in target app).
    2. **Locator Drift:** UI element changed or moved (selector out of date).
    3. **Navigation Failure:** Target page did not load or route timed out.
    4. **Authentication Failure:** Session expired or unauthorized.
    5. **Test-Data Failure:** Seed data missing or invalid.
    6. **Environment Failure:** 500 error, network timeout, or server down.

---

### Phase: Final Outputs (Step 13)

#### Step 13: Final Reports
* **Missing Components:**
  - **`execution_report.md`:** Summary of total tests run, passed, failed, blocked, and execution duration
  - **`requirement_coverage.md` (Live):** Realized coverage showing which requirements passed in the live UI
  - **`ui_drift_report.md`:** Discrepancies between what the codebase specifies vs. what the live UI exposes
  - **`failures_and_blockers.md`:** Detailed triage report with embedded screenshots and failure classifications

---

## 5. Architectural Alignment Analysis

### Is the Diagram Accurate to the Project's Vision?
**Yes, remarkably so.** The diagram is an exemplary specification for **Pack 3** of the `TraceBackAnalysis` system.

1. **Pack Chaining Paradigm:**  
   The entire system was architected around chaining packs via published JSON artifacts:
   ```
   Codebase ──► [Pack 1: Codebase Analysis] ──► requirements.json
                                                        │
                                                        ▼
                                            [Pack 2: Testcase Gen] ──► test_specs.json
                                                                            │
                                                                            ▼
                                                                [Pack 3: UI-Aware Playwright] ──► execution_report.md
   ```
2. **Quality Gates & Bounded Repair:**  
   The diagram adopts the exact same architectural pattern used in Packs 1 and 2:
   - **Deterministic Pre-conditions & Ingestion**
   - **Quality Gates** (`UI Discovery Gate`, `Pre-Execution Gate`)
   - **Explicit Failure Reports** (`Discovery Gap Report`, `Blocked Test Report`)
3. **Artifact-Centric State:**  
   Heavy payloads (screenshots, DOM trees, traces, video recordings) are saved to disk in `runs/<run-id>/artifacts/`, matching the `ArtifactStore` design in [src/agentic/store.py](file:///c:/Users/ayush/Downloads/TraceBackAnalysis/src/agentic/store.py).

---

## 6. Blueprint: Implementing the Diagram as Pack 3

To implement the architecture in the uploaded image, here is the exact implementation blueprint that fits natively into the existing codebase:

### 1. Add Dependencies to `pyproject.toml`
```toml
dependencies = [
  # existing dependencies...
  "playwright>=1.49,<2",
  "beautifulsoup4>=4.12,<5",
]
```

### 2. Create the New Skill Pack Directory: `playwright-ui-skills/`
```
playwright-ui-skills/
├── pack.yaml                     # Pack manifest chaining from testcase-generation
└── skills/
    ├── ui-discovery/
    │   └── SKILL.md              # Crawler & DOM discovery rules
    ├── ui-realization/
    │   └── SKILL.md              # Mapping abstract specs to Playwright actions
    ├── pre-execution-gate/
    │   └── SKILL.md              # Selector & readiness verification
    ├── test-execution/
    │   └── SKILL.md              # Playwright execution & evidence capture
    └── failure-classification/
        └── SKILL.md              # Diagnostic classification rules
```

### 3. Pack Manifest Declaration (`playwright-ui-skills/pack.yaml`)
```yaml
pack: playwright-ui-execution
version: 1
description: >-
  Crawl live web application UI using Playwright, map abstract test specifications
  to concrete UI interactions, execute tests, and classify failures.

action_label: Discover UI and execute tests

consumes_pack: testcase-generation
consumes_artifact: test_specs.json

steps:
  # Phase 1: Input Extension & Validation
  - id: validate_runtime_inputs
    skill: ui-discovery
    mode: deterministic
    capability: validate_ui_inputs
    produces: [ui_config]
    on_fail: halt

  # Phase 2: UI Discovery
  - id: crawl_ui
    skill: ui-discovery
    mode: deterministic
    capability: discover_ui_playwright
    consumes: [ui_config, requirement_set]
    produces: [ui_knowledge_graph]
    on_fail: halt

  # Phase 3: Discovery Gate
  - id: gate_discovery
    skill: ui-discovery
    mode: gate
    capability: validate_ui_discovery
    consumes: [ui_knowledge_graph, requirement_set]
    produces: [discovery_gate]
    on_fail: halt # Generates crawl_gaps.md

  # Phase 4: Test Realization
  - id: realize_ui_tests
    skill: ui-realization
    mode: llm
    fan_out: test_batches
    consumes: [test_suite, ui_knowledge_graph]
    produces: [executable_test_plan]

  # Phase 5: Pre-Execution Gate
  - id: gate_pre_execution
    skill: pre-execution-gate
    mode: gate
    capability: validate_executable_plan
    consumes: [executable_test_plan, ui_knowledge_graph]
    produces: [execution_gate]
    on_fail: halt # Generates blocked_tests.md

  # Phase 6: Playwright Execution
  - id: execute_playwright
    skill: test-execution
    mode: deterministic
    capability: run_playwright_suite
    consumes: [executable_test_plan, ui_config]
    produces: [raw_execution_results, traces, screenshots]

  # Phase 7: Failure Classification
  - id: classify_failures
    skill: failure-classification
    mode: deterministic
    capability: classify_execution_failures
    consumes: [raw_execution_results]
    produces: [classified_results]

  # Phase 8: Final Reports
  - id: render_ui_reports
    skill: failure-classification
    mode: deterministic
    capability: render_ui_execution_deliverables
    consumes: [classified_results, execution_gate, discovery_gate]
    produces: [deliverables]
```

### 4. New Python Capabilities in `src/agentic/capabilities/ui/`
* `discovery.py`: Headless Playwright crawler with accessibility snapshot extractor.
* `graph.py`: Builds `UIGraph` (nodes = screens & elements, edges = transitions).
* `realizer.py`: Binds abstract test actions (`submit`, `select`) to robust Playwright locators (`getByRole`, `locator`).
* `executor.py`: Parallel test runner capturing screenshots, HAR, video, and trace zips.
* `classifier.py`: Categorizes errors into Application / Locator Drift / Navigation / Auth / Environment.
* `render.py`: Formats `execution_report.md`, `crawl_gaps.md`, `ui_drift.md`, and `blocked_tests.md`.

---

## 7. Summary Comparison Table

| Feature / Dimension | Current Codebase (`TraceBackAnalysis`) | Uploaded Architecture Diagram | Gap Analysis |
|---|---|---|---|
| **Primary Input** | Source Code (Files / ZIP) | `requirements.json` + `test_specs.json` + Target Web URL | Needs URL & Auth inputs |
| **Analysis Type** | Static Code Analysis (AST, Call Graph, Imports) | Dynamic UI Discovery (DOM, Accessibility Tree, Routes) | Needs Playwright crawler |
| **Primary Output** | Requirements BRD (`requirements.md`) & Abstract Tests (`test_specifications.md`) | Executed Test Results (`execution_report.md`), Traces, Drift Report | Needs Playwright test runner |
| **Framework Independence** | **Strictly Independent** (Playwright/Selenium keywords forbidden) | **Playwright-Specific Realization & Execution** | Intended downstream adapter |
| **Quality Gates** | Code Graph Gate, BRD Gate, Test Spec Gate | UI Discovery Gate, Pre-Execution Gate | Direct 1:1 architectural fit |
| **Failure Handling** | Bounded LLM Repair Loop & Quarantine | Blocked Test Reports & 6-Way Failure Classification | Direct 1:1 architectural fit |

---

> 💡 **Conclusion:**  
> The uploaded image is an **exceptionally well-conceived, architecturally sound design for the next evolution (Pack 3) of this project**. It accurately assumes the existence of the current codebase's outputs (Box 1) and correctly defines the downstream UI discovery, realization, execution, and triage layers needed for end-to-end testing.
