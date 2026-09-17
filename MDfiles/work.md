# 🏗️ Implementation Roadmap: UI-Aware Test Generation & Playwright Execution

> **File:** `MDfiles/work.md`  
> **Objective:** Comprehensive, step-by-step engineering blueprint and execution sequence to build the **UI-Aware Test Discovery, Realization, and Live Playwright Execution Engine** (Pack 3 / `playwright-ui-execution`) on top of the existing `TraceBackAnalysis` codebase.

---

## 📑 Table of Contents

1. [Architectural Overview & Integration Strategy](#1-architectural-overview--integration-strategy)
2. [Recommended Tech Stack & Dependencies](#2-recommended-tech-stack--dependencies)
3. [Strict Sequential Execution Plan (First What, Then What)](#3-strict-sequential-execution-plan)
   - [Phase 0: Environment Setup, Manifest & Schema Foundation](#phase-0-environment-setup-manifest--schema-foundation)
   - [Phase 1: Input Extension & Runtime Configuration (Boxes 2 & 3)](#phase-1-input-extension--runtime-configuration-boxes-2--3)
   - [Phase 2: Playwright UI Discovery & Knowledge Graph Construction (Boxes 4, 5, 6)](#phase-2-playwright-ui-discovery--knowledge-graph-construction-boxes-4-5-6)
   - [Phase 3: UI-Aware Test Realization & Executable Test Planning (Boxes 7, 8, 9)](#phase-3-ui-aware-test-realization--executable-test-planning-boxes-7-8-9)
   - [Phase 4: Live Playwright Execution & Rich Evidence Collection (Boxes 10 & 11)](#phase-4-live-playwright-execution--rich-evidence-collection-boxes-10--11)
   - [Phase 5: Diagnostic Failure Classification (Box 12)](#phase-5-diagnostic-failure-classification-box-12)
   - [Phase 6: Final Deliverables Rendering & Streamlit UI Integration (Box 13)](#phase-6-final-deliverables-rendering--streamlit-ui-integration-box-13)
4. [Master File Modification & Creation Inventory](#4-master-file-modification--creation-inventory)
5. [End-to-End Testing & Verification Strategy](#5-end-to-end-testing--verification-strategy)

---

## 1. Architectural Overview & Integration Strategy

### 🔗 How Pack 3 Chains Off Existing Packs
The existing codebase is completely modular and driven by YAML manifests. Adding this entire pipeline requires **zero modifications** to the existing Pack 1 (`codebase-requirements`) or Pack 2 (`testcase-generation`). 

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│ EXISTING CODEBASE (Unchanged)                                                     │
│ [Pack 1: Codebase Analysis] ──► requirements.json                                 │
│                                       │                                           │
│                                       ▼                                           │
│ [Pack 2: Testcase Generation] ──► test_specs.json                                 │
└───────────────────────────────────────┬───────────────────────────────────────────┘
                                        │ (Pack Chaining Contract)
                                        ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│ NEW EXTENSION: PACK 3 (playwright-ui-execution)                                   │
│                                                                                   │
│ 1. New Inputs ──► URL, Environment, Auth Credentials, Crawl Policy               │
│ 2. Discovery  ──► Headless Playwright Crawl ➔ UI Knowledge Graph ➔ Gate           │
│ 3. Realizer   ──► Map Abstract Specs to Concrete Selectors ➔ Executable Plan ➔ Gate│
│ 4. Execution  ──► Parallel Playwright Runner ➔ Captures Video, Traces, Screenshots│
│ 5. Triage     ──► 6-Way Failure Classifier ➔ Application vs Drift vs Auth Failure │
│ 6. Reporting  ──► execution_report.md, ui_drift_report.md, blocked_tests.md       │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Recommended Tech Stack & Dependencies

| Category | Technology / Library | Purpose & Rationale |
|---|---|---|
| **Headless Automation** | `playwright>=1.49.0` | Industry-standard headless browser runner (Chromium, Firefox, WebKit), network interception, video recording, trace collection. |
| **DOM & Accessibility** | `beautifulsoup4>=4.12.0`, `lxml` | Fast parsing of DOM nodes, accessibility tree inspection via `page.accessibility.snapshot()`. |
| **Graph Modeling & Navigation** | `networkx>=3.2` | Graph data structure to model screens as nodes, transitions as directed edges, and compute shortest navigation paths. |
| **Data Validation & Schemas** | `pydantic>=2.10`, `pydantic-settings` | Strongly-typed schemas for UI Graph, Playwright actions, test plans, and failure triage. |
| **LLM & Orchestration** | `langgraph`, `langchain-openai` | Orchestrates the multi-phase execution graph with deterministic gating and parallel LLM workers. |
| **Image & Screenshot Markup** | `Pillow>=10.0` | Annotates full-page viewport screenshots with bounding boxes for identified UI elements. |
| **Frontend UI** | `streamlit>=1.46` | Updates the existing `app.py` dashboard to input target URLs, trigger live runs, and view test traces & videos. |

---

## 3. Strict Sequential Execution Plan

Follow this exact chronological sequence to build, test, and integrate the architecture.

---

### Phase 0: Environment Setup, Manifest & Schema Foundation

> **Goal:** Establish the foundational dependencies, Pydantic data models, and pack manifest so LangGraph can compile the new pipeline.

#### Step 0.1: Install Dependencies
Update `pyproject.toml` with the required libraries and install Playwright browser binaries:
```bash
# Add dependencies to pyproject.toml
pip install "playwright>=1.49" "beautifulsoup4>=4.12" "networkx>=3.2" "Pillow>=10.0"

# Install Playwright browser engines
playwright install chromium
```

#### Step 0.2: Define Core Pydantic Schemas (`src/agentic/schemas/ui.py`)
Create the strongly-typed contracts for all UI discovery, realization, and execution payloads:
* `UIElement`: Tag, semantic role (`button`, `input`, `combobox`), CSS selector, XPath, accessibility label, bounding box `(x, y, width, height)`.
* `ScreenNode`: Unique route/hash/modal state, URL pattern, list of `UIElement`s, screenshot path.
* `TransitionEdge`: Source screen ➔ Action (click/type) ➔ Target screen.
* `UIGraph`: Directed multi-graph of `ScreenNode`s and `TransitionEdge`s.
* `PlaywrightAction`: Action kind (`click`, `fill`, `select_option`, `check`, `press`), target selector, input value, timeout.
* `ExecutableTestCase`: Resolved starting URL, navigation step list, concrete Playwright actions, expected DOM assertions.
* `ExecutableTestSuite`: Collection of executable test cases.
* `ExecutionResult`: Status (`PASSED`, `FAILED`, `BLOCKED`, `TIMED_OUT`), duration, error message, stack trace, screenshot path, trace `.zip` path.
* `FailureClassification`: 6 failure buckets (`APPLICATION_FAILURE`, `LOCATOR_DRIFT`, `NAVIGATION_FAILURE`, `AUTHENTICATION_FAILURE`, `TEST_DATA_FAILURE`, `ENVIRONMENT_FAILURE`).

#### Step 0.3: Create Pack Manifest (`playwright-ui-skills/pack.yaml`)
Declare the pack manifest declaring dependency on Pack 2:
```yaml
pack: playwright-ui-execution
version: 1
description: Discover UI using Playwright, map abstract test specifications to concrete UI elements, execute tests, and classify failures.
action_label: Discover UI and execute tests
consumes_pack: testcase-generation
consumes_artifact: test_specs.json
```

---

### Phase 1: Input Extension & Runtime Configuration (Boxes 2 & 3)

> **Goal:** Accept live target environment settings, authenticate safely, and validate input parameters before launching browser processes.

```
┌─────────────────────────┐      ┌─────────────────────────┐
│   Box 2: User Inputs    │ ──►  │ Step 3: Runtime Ext.    │
│ URL, Env, Auth, Roles   │      │ (Deterministic Check)   │
└─────────────────────────┘      └─────────────────────────┘
```

#### Step 1.1: Web Target & Auth Configuration Model (`src/agentic/schemas/ui_config.py`)
* Create `UIConfig` model:
  - `target_url`: Base URL of the running web application.
  - `environment`: Name (`local`, `staging`, `preview`).
  - `auth_type`: `NONE`, `FORM_LOGIN`, `STORAGE_STATE`, `HTTP_BASIC`, `BEARER_TOKEN`.
  - `auth_credentials`: Username/password or storage state JSON path.
  - `user_roles`: List of testing roles (`admin`, `standard`, `guest`).
  - `crawl_policy`: `max_depth` (default 4), `max_screens` (default 50), `allowed_domains`, `ignore_patterns` (e.g. `/logout`, `/delete-account`), `safe_methods_only: bool`.

#### Step 1.2: Runtime Input Extension Node (`src/agentic/capabilities/ui/inputs.py`)
* **Capability:** `validate_ui_inputs()` (Deterministic)
* **Logic:**
  1. Sends a preflight HTTP `GET / HEAD` request to verify `target_url` is accessible and resolves DNS.
  2. Validates that supplied credentials or storage cookies exist and are non-empty.
  3. Enforces crawl safety bounds (rejects destructive paths).
* **Output:** `ui_config.json` artifact stored in run directory.

#### Step 1.3: Update Entry Points (`app.py` & `src/agentic/cli.py`)
* Add UI input widgets in Streamlit sidebar:
  - Target URL input field.
  - Environment selector dropdown.
  - Optional Login Credentials / Cookie file uploader.
* Add CLI argument options:
  - `agentic run playwright-ui-execution runs/<run-id>/artifacts --url https://staging.app.com`

---

### Phase 2: Playwright UI Discovery & Knowledge Graph Construction (Boxes 4, 5, 6)

> **Goal:** Crawl the live web application using headless Chromium, discover routes/screens, extract interactable DOM elements, build a navigable UI Knowledge Graph, and validate discovery completeness.

```
┌─────────────────────────┐      ┌─────────────────────────┐      ┌─────────────────────────┐
│ Step 4: Playwright      │ ──►  │ Step 5: UI Knowledge    │ ──►  │ Step 6: UI Discovery   │
│ UI Discovery (Crawler)  │      │ Graph Construction      │      │ Gate & crawl_gaps.md    │
└─────────────────────────┘      └─────────────────────────┘      └─────────────────────────┘
```

#### Step 2.1: Headless Browser & Authentication Manager (`src/agentic/capabilities/ui/browser.py`)
* Creates an async Playwright session:
  - Launches headless Chromium with configurable viewport (1280x800).
  - Executes login flow if `auth_type == FORM_LOGIN` and saves storage state (`storageState.json`).
  - Injects auth cookies / headers for all subsequent contexts.

#### Step 2.2: Safe Autonomous Web Crawler (`src/agentic/capabilities/ui/discovery.py`)
* **Capability:** `discover_ui_playwright()` (Deterministic)
* **Logic:**
  1. Starts at `target_url`.
  2. Uses breadth-first search (BFS) queue bounded by `max_depth` and `max_screens`.
  3. For each visited page:
     - Waits for `networkidle` and DOM stability.
     - Captures full-page viewport screenshot (`screen_xxx.png`).
     - Extracts all interactable elements: `<button>`, `<a>`, `<input>`, `<select>`, `<textarea>`, `[role="button"]`, `[tabindex]`.
     - Generates robust multi-tiered selectors:
       1. Semantic Accessibility Role (`getByRole('button', { name: 'Submit' })`)
       2. Unique `data-testid` or `id` attributes
       3. Semantic Label (`getByLabel('Password')`)
       4. Resilient CSS selector
     - Clicks safe navigation links (anchors, menu items) to discover child screens.
     - Detects modal dialogs and SPA route changes (`history.pushState`).

#### Step 2.3: UI Knowledge Graph Construction (`src/agentic/capabilities/ui/graph.py`)
* **Capability:** `construct_ui_knowledge_graph()` (Deterministic)
* **Logic:**
  1. Initializes a NetworkX `DiGraph`.
  2. Adds `ScreenNode` for every distinct URL / modal state.
  3. Adds `TransitionEdge` for every navigation action that transitions screen $A \to B$.
  4. Stores element bounding box coordinates for visual traceability.
* **Output:** `ui_graph.json` artifact.

#### Step 2.4: UI Discovery Gate & Gap Report (`src/agentic/capabilities/ui/gates.py`)
* **Capability:** `validate_ui_discovery()` (Gate Mode)
* **Logic:**
  1. Compares discovered screens in `ui_graph.json` against expected capability areas in `requirements.json`.
  2. Checks for blocked routes (e.g. 401/403 errors, unexpected redirects to `/login`).
  3. **Branching Decision:**
     - **Discovery Sufficient:** Proceeds to Step 7.
     - **Discovery Incomplete:** Halts execution and compiles `crawl_gaps.md` (detailing unreachable functional areas, broken links, or auth failures).

---

### Phase 3: UI-Aware Test Realization & Executable Test Planning (Boxes 7, 8, 9)

> **Goal:** Bridge the semantic gap between **Abstract Test Specifications** (`test_specs.json`) and **Live DOM Elements** (`ui_graph.json`), generating a deterministic, executable Playwright test suite.

```
┌─────────────────────────┐      ┌─────────────────────────┐      ┌─────────────────────────┐
│ Step 7: UI-Aware Test   │ ──►  │ Step 8: Executable      │ ──►  │ Step 9: Pre-Execution   │
│ Realization (LLM + AST) │      │ Test Plan Assembly      │      │ Gate & blocked_tests.md │
└─────────────────────────┘      └─────────────────────────┘      └─────────────────────────┘
```

#### Step 3.1: UI-Aware Test Realizer (`src/agentic/capabilities/ui/realize.py`)
* **Mode:** `llm` (Fan-Out: one worker per test scenario)
* **Prompt & Schema:** `playwright-ui-skills/skills/ui-realization/SKILL.md` ➔ `RealizedTestPlan`
* **Inputs:** `test_specs.json` (abstract test cases) + `ui_graph.json` (discovered DOM selectors).
* **Logic:**
  1. For each abstract action (e.g. *"Submit removal request for user ID 1024"*):
     - Matches the target screen in `ui_graph.json`.
     - Identifies the matching input element (`input#user-id`) and action button (`button#delete-confirm`).
     - Maps abstract inputs to concrete Playwright operations:
       - `fill("input#user-id", "1024")`
       - `click("button#delete-confirm")`
  2. For each expected assertion (e.g. *"User is removed and success notice displayed"*):
     - Maps to Playwright assertions:
       - `expect(page.locator(".alert-success")).to_be_visible()`
       - `expect(page.locator(".alert-success")).to_have_text("User deleted successfully")`

#### Step 3.2: Pathfinding & Navigation Synthesizer (`src/agentic/capabilities/ui/pathfinding.py`)
* **Capability:** `synthesize_navigation_paths()` (Deterministic)
* **Logic:**
  - Uses NetworkX Dijkstra shortest-path algorithm on `ui_graph.json` to find the exact sequence of clicks needed to navigate from the home screen (`/`) to the deep screen where the test starts.
  - Automatically prepends these prerequisite navigation steps to the test case.

#### Step 3.3: Executable Test Plan Assembly (`src/agentic/capabilities/ui/assemble.py`)
* **Capability:** `assemble_executable_test_plan()` (Deterministic)
* **Output:** `executable_test_plan.json` containing complete, self-contained, typed Playwright test cases.

#### Step 3.4: Pre-Execution Gate & Blocked Test Report (`src/agentic/capabilities/ui/gates.py`)
* **Capability:** `validate_pre_execution()` (Gate Mode)
* **Logic:**
  1. Verifies that all targeted CSS selectors exist uniquely in the UI graph (prevents ambiguous locator errors).
  2. Verifies authentication cookies and required test fixtures are available.
  3. **Branching Decision:**
     - **Ready:** Passes valid test cases to Step 10.
     - **Blocked:** Identifies test cases with missing locators or missing test data, quarantines them into `blocked_tests.md`, and proceeds with the ready subset.

---

### Phase 4: Live Playwright Execution & Rich Evidence Collection (Boxes 10 & 11)

> **Goal:** Execute the realized test cases against the live browser, capturing video, traces, network logs, and screenshots for forensic validation.

```
┌─────────────────────────┐      ┌─────────────────────────┐
│ Step 10: Playwright     │ ──►  │ Step 11: Result &       │
│ Live Execution Harness  │      │ Artifact Collection     │
└─────────────────────────┘      └─────────────────────────┘
```

#### Step 4.1: Parallel Playwright Execution Engine (`src/agentic/capabilities/ui/executor.py`)
* **Capability:** `run_playwright_execution()` (Deterministic)
* **Logic:**
  1. Spawns isolated `BrowserContext` instances (configurable concurrency, e.g. 4 workers).
  2. Enables context recording options:
     - `record_video_dir`: Saves `.webm` video of each test run.
     - `tracing.start(screenshots=True, snapshots=True, sources=True)`: Captures full time-travel debug traces.
  3. Iterates through the test case steps:
     - Navigates to starting route.
     - Executes typed actions (`click`, `fill`, `select_option`) with explicit 5-second action timeouts.
     - Evaluates assertions (`expect(locator).to_be_visible()`).
  4. On step failure:
     - Immediately captures a full-page failure screenshot (`fail_TC_001.png`).
     - Stops trace recording and saves `trace_TC_001.zip`.
     - Captures browser console logs and failed network requests (HTTP 4xx/5xx).
* **Output:** `raw_execution_results.json`.

#### Step 4.2: Result & Artifact Storage (`src/agentic/capabilities/ui/collector.py`)
* Moves all media artifacts into `runs/<run-id>/artifacts/execution/`:
  - `runs/<run-id>/artifacts/execution/screenshots/*.png`
  - `runs/<run-id>/artifacts/execution/videos/*.webm`
  - `runs/<run-id>/artifacts/execution/traces/*.zip`
  - `runs/<run-id>/artifacts/execution/logs/console.log`

---

### Phase 5: Diagnostic Failure Classification (Box 12)

> **Goal:** Automatically analyze failed tests to determine the precise root cause (preventing test flakiness from being misdiagnosed as code bugs).

```
┌────────────────────────────────────────────────────────┐
│ Step 12: Diagnostic Failure Classifier (6 Categories)  │
└────────────────────────────────────────────────────────┘
```

#### Step 5.1: Failure Classifier Engine (`src/agentic/capabilities/ui/classifier.py`)
* **Capability:** `classify_test_failures()` (Deterministic Heuristics + Targeted LLM Analysis)
* **Inputs:** `raw_execution_results.json`, DOM snapshot, Playwright trace logs.
* **Classification Rules:**

| Failure Bucket | Detection Signature / Heuristic | Meaning |
|---|---|---|
| **1. Application Failure** | Page loaded successfully, selector was found, but assertion failed (e.g. expected `"Success"`, got `"500 Server Error"` or wrong calculation). | **Real bug in target software.** |
| **2. Locator Drift** | Playwright timed out waiting for selector (`TimeoutError: locator.click: Timeout 5000ms exceeded`), but target page loaded. | **UI changed / selector out of date.** |
| **3. Navigation Failure** | Page failed to reach target URL (`net::ERR_CONNECTION_REFUSED`, `404 Not Found`, infinite redirect loop). | **Routing / broken link issue.** |
| **4. Authentication Failure** | Redirected to `/login`, received HTTP `401 Unauthorized` or `403 Forbidden`. | **Expired token or permission mismatch.** |
| **5. Test-Data Failure** | Form rejected input due to duplicate key, foreign key constraint, or missing pre-seeded user. | **Test fixture / database seed issue.** |
| **6. Environment Failure** | Gateway timeout (HTTP 504), server crash (HTTP 502/503), or network disconnect. | **Infrastructure / staging server down.** |

* **Output:** `classified_results.json`.

---

### Phase 6: Final Deliverables Rendering & Streamlit UI Integration (Box 13)

> **Goal:** Render human-readable markdown summaries, coverage reports, UI drift analyses, and integrate interactive triage into the Streamlit UI.

```
┌────────────────────────────────────────────────────────┐
│ Step 13: Final Reports & Streamlit UI Visualizer       │
└────────────────────────────────────────────────────────┘
```

#### Step 6.1: Markdown & JSON Deliverables Renderer (`src/agentic/capabilities/ui/render.py`)
* Generates 4 final published reports in `runs/<run-id>/artifacts/`:
  1. **`execution_report.md`:** Executive scorecard (Total tests, Passed, Failed, Blocked, Execution Time, Pass Rate %).
  2. **`ui_drift_report.md`:** Discrepancies between what the codebase specification declared vs. what the live UI actually exposes (e.g. missing buttons, renamed labels, changed field validations).
  3. **`failures_and_blockers.md`:** Detailed triage table grouped by failure category with embedded screenshots and error logs.
  4. **`crawl_gaps.md` / `blocked_tests.md`:** Full accounting of unreached pages or unexecutable tests.

#### Step 6.2: Streamlit UI Integration (`app.py`)
* Add a dedicated **"🧪 Live UI Execution"** tab to the Streamlit app:
  - **Live Progress Bar:** Shows real-time passing/failing test steps.
  - **Video Replayer:** Embedded video player to watch `.webm` recordings of failed tests.
  - **Trace Viewer Launcher:** One-click button to inspect Playwright traces via `playwright show-trace`.
  - **Failure Classification Badges:** Color-coded badges (`Bug`, `Locator Drift`, `Auth Error`) for fast debugging.

---

## 4. Master File Modification & Creation Inventory

Here is the exact file inventory to implement:

```
TraceBackAnalysis/
├── pyproject.toml                                   # [MODIFY] Add playwright, beautifulsoup4, networkx
├── app.py                                           # [MODIFY] Add URL input widgets & video replay tab
│
├── playwright-ui-skills/                            # [NEW] Pack 3 Root Directory
│   ├── pack.yaml                                    # [NEW] Pack manifest chaining from testcase-generation
│   └── skills/
│       ├── ui-discovery/SKILL.md                    # [NEW] Crawler & DOM discovery instructions
│       ├── ui-realization/SKILL.md                  # [NEW] Abstract-to-selector mapping rules
│       ├── pre-execution-gate/SKILL.md              # [NEW] Selector validation rules
│       ├── test-execution/SKILL.md                  # [NEW] Playwright runner instructions
│       └── failure-classification/SKILL.md          # [NEW] 6-way failure classification rules
│
└── src/agentic/
    ├── schemas/
    │   ├── ui.py                                    # [NEW] UIGraph, ScreenNode, ExecutableTestCase schemas
    │   └── ui_config.py                             # [NEW] Target URL, credentials, crawl policy schemas
    │
    └── capabilities/
        └── ui/                                      # [NEW] UI Capabilities Subpackage
            ├── __init__.py                          # [NEW] Package init
            ├── inputs.py                            # [NEW] Runtime URL & auth validator
            ├── browser.py                           # [NEW] Playwright context & session manager
            ├── discovery.py                         # [NEW] Headless web crawler
            ├── graph.py                             # [NEW] UI Knowledge Graph constructor
            ├── pathfinding.py                       # [NEW] NetworkX shortest-path navigation solver
            ├── realize.py                           # [NEW] Abstract-to-DOM locator mapper
            ├── assemble.py                          # [NEW] Executable test plan assembler
            ├── gates.py                             # [NEW] Discovery Gate & Pre-Execution Gate
            ├── executor.py                          # [NEW] Parallel Playwright test runner
            ├── collector.py                         # [NEW] Screenshot, video, & trace archiver
            ├── classifier.py                        # [NEW] 6-way failure diagnostic engine
            └── render.py                            # [NEW] Final markdown & JSON report renderers
```

---

## 5. End-to-End Testing & Verification Strategy

To verify this implementation end-to-end:

### 1. Build a Local Mock Web App (`tests/mock_app/app.py`)
Create a lightweight FastAPI/Flask web app with:
- Login screen (`/login`)
- Dashboard with navigation links (`/dashboard`, `/users`, `/settings`)
- Form submissions with valid/invalid inputs
- One intentional UI bug (e.g. a 500 error on a specific button)
- One intentional locator drift (e.g. button ID changed from `#submit` to `#btn-save`)

### 2. Run the Full 3-Pack Pipeline
```bash
# Step 1: Reverse-engineer requirements from source code (Pack 1)
agentic run codebase-requirements tests/mock_app/

# Step 2: Generate abstract test specifications (Pack 2)
agentic run testcase-generation runs/<run-id>/artifacts/

# Step 3: Discover live UI and execute Playwright tests (Pack 3)
agentic run playwright-ui-execution runs/<run-id>/artifacts/ --url http://localhost:8000
```

### 3. Verify Expected Outputs
- `runs/<run-id>/artifacts/ui_graph.json` contains full screen/element topology.
- `runs/<run-id>/artifacts/execution/videos/*.webm` contains playable test recordings.
- `runs/<run-id>/artifacts/execution_report.md` correctly reports passes and failures.
- `runs/<run-id>/artifacts/failures_and_blockers.md` accurately classifies the intentional bug as **Application Failure** and the changed ID as **Locator Drift**.

---

> 🎯 **Summary:**  
> By implementing this roadmap in the exact sequence specified above (Phases 0 through 6), you transform `TraceBackAnalysis` from a static analysis tool into a **complete, autonomous, closed-loop End-to-End Test Generation and Live Browser Execution Platform**.
