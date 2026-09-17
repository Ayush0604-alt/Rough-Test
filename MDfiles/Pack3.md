# 🚀 Pack 3: Transforming Pack 2 Specifications into Live UI Tests (with MCP Integration)

> **File:** `MDfiles/Pack3.md`  
> **Pack ID:** `playwright-ui-execution`  
> **Input Artifacts Consumed:** `test_specs.json` & `requirements.json` (from Pack 1 & Pack 2)  
> **New Runtime Inputs:** Target Web Application URL, Environment, Auth Credentials, Crawl Policy  
> **Primary Output:** Live test execution results, failure root-cause triage, video recordings, Playwright traces, and UI drift reports.  
> **Protocol Support:** Native support for **Model Context Protocol (MCP)** for process-decoupled browser automation.

---

## 📑 Table of Contents

1. [The Core Problem: Bridging the Semantic Gap](#1-the-core-problem-bridging-the-semantic-gap)
2. [What Pack 2 Gives Pack 3 (The Input Contract)](#2-what-pack-2-gives-pack-3-the-input-contract)
3. [The 5-Stage Transformation Pipeline (How It Actually Works)](#3-the-5-stage-transformation-pipeline)
   - [Stage 1: Target Binding & Preflight Check](#stage-1-target-binding--preflight-check)
   - [Stage 2: Live UI Discovery & Knowledge Graph Construction](#stage-2-live-ui-discovery--knowledge-graph-construction)
   - [Stage 3: Test Realization (Abstract Specs ➔ Concrete DOM Locators)](#stage-3-test-realization-abstract-specs-➔-concrete-dom-locators)
   - [Stage 4: Automated Pathfinding & Navigation Injection](#stage-4-automated-pathfinding--navigation-injection)
   - [Stage 5: Live Playwright Execution & Rich Evidence Capture](#stage-5-live-playwright-execution--rich-evidence-capture)
   - [Stage 6: 6-Way Diagnostic Failure Classification](#stage-6-6-way-diagnostic-failure-classification)
4. [🔌 Model Context Protocol (MCP) Integration Architecture](#4--model-context-protocol-mcp-integration-architecture)
   - [Why Use MCP in Pack 3?](#why-use-mcp-in-pack-3)
   - [MCP Architecture & Data Flow](#mcp-architecture--data-flow)
   - [Implementation Modes: Off-the-Shelf vs. Custom MCP Server](#implementation-modes-off-the-shelf-vs-custom-mcp-server)
   - [MCP Tool Signatures & JSON-RPC Protocol](#mcp-tool-signatures--json-rpc-protocol)
   - [Multi-Modal Vision Feedback Loop via MCP](#multi-modal-vision-feedback-loop-via-mcp)
5. [Complete Concrete Worked Example (Step-by-Step)](#5-complete-concrete-worked-example)
   - [A. The Abstract Test Specification from Pack 2](#a-the-abstract-test-specification-from-pack-2)
   - [B. What Playwright Discovers from the Live UI](#b-what-playwright-discovers-from-the-live-ui)
   - [C. The Realized Executable Test Plan](#c-the-realized-executable-test-plan)
   - [D. The Executable Playwright Test Script](#d-the-executable-playwright-test-script)
   - [E. Execution Evidence & Diagnostic Output](#e-execution-evidence--diagnostic-output)
6. [Summary of Pack 3 Deliverables](#6-summary-of-pack-3-deliverables)

---

## 1. The Core Problem: Bridging the Semantic Gap

Pack 2 outputs **technology-independent test specifications** (human-readable and structured). However, a browser automation engine like Playwright cannot execute a sentence like *"User submits removal request for invalid ID"*.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              THE SEMANTIC GAP                                │
├──────────────────────────────────────┬───────────────────────────────────────┤
│ Pack 2 Specification (Abstract)      │ Playwright Requirement (Concrete)     │
├──────────────────────────────────────┼───────────────────────────────────────┤
│ "Navigate to user management"        │ `await page.goto('/admin/users')`     │
│ "Enter invalid user ID"              │ `await page.fill('#user-id', '-999')` │
│ "Click delete confirm button"        │ `await page.click('button.btn-danger')`│
│ "Verify error banner is displayed"   │ `expect(page.locator('.alert-error')) │
│                                      │  .toBeVisible()`                      │
└──────────────────────────────────────┴───────────────────────────────────────┘
```

**Pack 3 exists to bridge this gap automatically.** It crawls the live web app, maps abstract intents to real DOM elements, generates the exact Playwright commands, executes them in real browser instances, and classifies failures.

---

## 2. What Pack 2 Gives Pack 3 (The Input Contract)

Pack 3 reads `runs/<run-id>/artifacts/test_specs.json`. Here is what each test case looks like when passed into Pack 3:

```json
{
  "id": "TC-AUTH-002",
  "scenario_id": "SCEN-AUTH-001",
  "objective": "Verify authentication is refused when an invalid password is supplied",
  "dimension": "negative",
  "risk": "high",
  "interface_hint": "http_endpoint",
  "automation_readiness": "ready",
  "preconditions": [
    "User account 'test_user' exists in storage with status ACTIVE"
  ],
  "inputs": [
    {
      "name": "username",
      "value_kind": "valid",
      "example": "test_user"
    },
    {
      "name": "password",
      "value_kind": "invalid",
      "example": "WrongPassword#999"
    }
  ],
  "steps": [
    "Navigate to the login interface",
    "Enter valid username and incorrect password",
    "Submit the authentication form"
  ],
  "expected_results": [
    "Authentication is refused with an invalid credentials notice",
    "No session token is issued",
    "User remains on login interface"
  ],
  "covers_criterion": "0",
  "requirement_id": "FR-AUTH-01"
}
```

---

## 3. The 5-Stage Transformation Pipeline

```
                               PACK 3 PIPELINE
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 1. Target    │ ──► │ 2. UI Graph  │ ──► │ 3. Realizer  │ ──► │ 4. Execution │ ──► 5. Triage
│    Binding   │     │    Discovery │     │    (Mapping) │     │    & Video   │     & Drift
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
```

---

### Stage 1: Target Binding & Preflight Check
* **Inputs:** `test_specs.json` + User Inputs (`target_url: "https://staging.app.io"`, `auth_credentials`, `crawl_policy`).
* **What Happens:**
  1. A preflight network check verifies that `https://staging.app.io` is up and responsive.
  2. If credentials are provided, Playwright launches a temporary browser, performs the login flow once, and saves the session cookies to `storageState.json`.

---

### Stage 2: Live UI Discovery & Knowledge Graph Construction
* **What Happens:**
  1. A headless Chromium crawler explores the live site starting from `target_url`.
  2. For every page, route, or modal dialog, it captures:
     - The URL path and page title.
     - Viewport screenshot with element bounding boxes.
     - All interactable DOM elements (`<button>`, `<input>`, `<a>`, `<select>`).
     - **Multi-tiered stable locators:**
       1. Accessibility Role: `getByRole('button', { name: 'Sign In' })`
       2. Semantic Test ID: `getByTestId('login-submit')`
       3. Semantic Label: `getByLabel('Password')`
       4. Unique CSS / ID: `#submit-btn`
  3. Constructs a **`UIGraph`** (screens as nodes, click transitions as directed edges).
  4. **Discovery Gate:** If key pages (e.g. `/login` or `/dashboard`) fail to load (HTTP 403 or 500), it writes `crawl_gaps.md` and halts.

---

### Stage 3: Test Realization (Abstract Specs ➔ Concrete DOM Locators)
* **What Happens:**
  - An LLM worker combines the **Abstract Test Case** (`test_specs.json`) with the **Discovered UI Graph** (`ui_graph.json`):
  
  | Abstract Test Spec (Pack 2) | Discovered DOM Match (UI Graph) | Concrete Playwright Action (Pack 3) |
  |---|---|---|
  | *"Navigate to login interface"* | `Screen: /login` | `page.goto("https://staging.app.io/login")` |
  | *"Enter valid username"* | `<input id="username" type="text">` | `page.fill("#username", "test_user")` |
  | *"Enter incorrect password"* | `<input id="password" type="password">`| `page.fill("#password", "WrongPassword#999")` |
  | *"Submit the form"* | `<button type="submit">Sign In</button>` | `page.click("button:has-text('Sign In')")` |
  | *"Invalid credentials notice"* | `<div class="toast-error">...</div>` | `expect(page.locator(".toast-error")).to_be_visible()` |

---

### Stage 4: Automated Pathfinding & Navigation Injection
* **The Problem:** Many tests must run on deep screens (e.g. `/settings/billing/invoices`). If a test spec starts directly on that screen, how does the browser get there?
* **The Solution:** Pack 3 runs **NetworkX Dijkstra shortest-path navigation** on the `UIGraph`:
  ```
  Home Screen (/) ──► Click "Settings" ──► Click "Billing" ──► Deep Screen (/settings/billing)
  ```
* It automatically prepends these navigation clicks as setup steps before executing the actual test case.

---

### Stage 5: Live Playwright Execution & Rich Evidence Capture
* **What Happens:**
  1. Spawns isolated browser contexts with `record_video_dir` and Playwright tracing enabled.
  2. Executes the realized actions with explicit 5-second action timeouts.
  3. Evaluates assertions against the live DOM.
  4. Captures complete forensic evidence in `runs/<run-id>/artifacts/execution/`:
     - Full-page step screenshots (`.png`)
     - Video recordings (`.webm`)
     - Full debug traces (`trace_TC-AUTH-002.zip`)
     - Console error logs and network HAR files.

---

### Stage 6: 6-Way Diagnostic Failure Classification
When a test fails, Pack 3 analyzes the error stack, DOM snapshot, and HTTP traffic to pinpoint the exact failure category:

```
                            FAILURE DIAGNOSTIC TREE
                                 Test Failed?
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
      [HTTP 500 / 502?]      [Selector Timeout?]     [Redirect to /login?]
              │                       │                       │
      ENVIRONMENT FAILURE        LOCATOR DRIFT           AUTH FAILURE
              │                       │                       │
              ▼                       ▼                       ▼
      [Assertion Mismatch?]  [404 Route Error?]      [Duplicate Key Error?]
              │                       │                       │
      APPLICATION FAILURE     NAVIGATION FAILURE       TEST-DATA FAILURE
```

---

## 4. 🔌 Model Context Protocol (MCP) Integration Architecture

Integrating **Model Context Protocol (MCP)** into Pack 3 provides a standardized, process-isolated mechanism for AI models to discover, control, and inspect browser automation engines via JSON-RPC.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ LANGGRAPH AGENT (Pack 3 Engine)                                             │
│ • Reads test_specs.json & ui_graph.json                                     │
│ • Runs MCP Client (`mcp.ClientSession`)                                     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       │ Standard MCP Protocol (JSON-RPC 2.0)
                                       │ Transports: stdio subprocess or SSE
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ PLAYWRIGHT MCP SERVER (`@modelcontextprotocol/server-playwright` / custom)  │
│ • Manages Headless Chromium Instances                                       │
│ • Exposes standardized MCP tools: `playwright_navigate`, `playwright_click` │
│ • Returns multi-modal responses (text + inline base64 screenshots)          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       │ Headless Automation & CDP
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LIVE WEB APPLICATION (https://staging.app.io)                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 💡 Why Use MCP in Pack 3?

1. **Process Isolation & Resilience:** Browser crashes, memory leaks, or unhandled JS exceptions occur inside the isolated **MCP Server process**, keeping the main Python runtime and Streamlit app 100% stable.
2. **Multi-Modal Vision Integration:** MCP natively supports returning image payloads (`image/png`). Vision LLMs (e.g. Gemini 3.7 Flash, GPT-4o) receive viewport screenshots directly in tool responses to visually verify button alignment, modal dialogs, and layout drift.
3. **Cross-Agent Reusability:** The exact same Playwright MCP server can be attached to Claude Desktop, Antigravity IDE, Cursor, or your custom LangGraph agent without rewriting browser integration code.

---

### Implementation Modes: Off-the-Shelf vs. Custom MCP Server

#### Option A: Standard Off-the-Shelf MCP Server (`@modelcontextprotocol/server-playwright`)
Pack 3 launches the official Node.js Playwright MCP server as a stdio subprocess:
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server_params = StdioServerParameters(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-playwright"],
)

async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
```

#### Option B: Custom TraceBack MCP Server (`traceback-ui-mcp`)
You construct a domain-specific Python MCP server (`pip install mcp`) exposing higher-level testing capabilities:
* `crawl_ui_route`: Crawls a route and returns structured `UIGraph` nodes.
* `get_accessibility_tree`: Returns the ARIA snapshot tree.
* `execute_test_case`: Executes a complete realized `ExecutableTestCase` and returns trace zips + pass/fail verdicts.
* `classify_failure`: Runs the diagnostic 6-way classification.

---

### MCP Tool Signatures & JSON-RPC Protocol Example

When the Pack 3 realizer executes a test case, it communicates with the MCP server via standard JSON-RPC:

#### 1. Fill Input Form Field (Agent ➔ MCP Server):
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "playwright_fill",
    "arguments": {
      "selector": "input[name='user_email']",
      "value": "invalid-email-address"
    }
  },
  "id": 101
}
```

#### 2. Click Button Response (MCP Server ➔ Agent):
```json
{
  "jsonrpc": "2.0",
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Successfully clicked button.btn-primary. Navigation completed to /admin/users."
      }
    ]
  },
  "id": 102
}
```

#### 3. Viewport Screenshot Request (Agent ➔ MCP Server):
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "playwright_screenshot",
    "arguments": { "fullPage": true }
  },
  "id": 103
}
```

#### 4. Multi-Modal Vision Response (MCP Server ➔ Agent):
```json
{
  "jsonrpc": "2.0",
  "result": {
    "content": [
      {
        "type": "image",
        "data": "iVBORw0KGgoAAAANSUhEUgAAB...",
        "mimeType": "image/png"
      }
    ]
  },
  "id": 103
}
```

---

## 5. Complete Concrete Worked Example

Here is a full, end-to-end trace showing how one real test case is transformed and executed:

### A. The Abstract Test Specification from Pack 2
From `test_specs.json`:
```json
{
  "id": "TC-USER-004",
  "objective": "Verify user cannot be created with an invalid email format",
  "dimension": "validation",
  "inputs": [
    {"name": "email", "value_kind": "invalid", "example": "invalid-email-address"}
  ],
  "steps": [
    "Open Add User modal",
    "Enter invalid email string into email field",
    "Click save button"
  ],
  "expected_results": [
    "Validation error message 'Invalid email format' is displayed",
    "User is not added to the user table"
  ]
}
```

---

### B. What Playwright Discovers from the Live UI
From `ui_graph.json`:
```json
{
  "screens": [
    {
      "id": "screen_users",
      "url": "https://staging.app.io/admin/users",
      "elements": [
        {"id": "btn_add_user", "role": "button", "selector": "#btn-new-user", "text": "Add New User"},
        {"id": "input_email", "role": "textbox", "selector": "input[name='user_email']", "label": "Email Address"},
        {"id": "btn_save", "role": "button", "selector": "button.btn-primary", "text": "Save User"},
        {"id": "msg_error", "role": "alert", "selector": ".invalid-feedback", "text": "Invalid email format"}
      ]
    }
  ]
}
```

---

### C. The Realized Executable Test Plan
From `executable_test_plan.json`:
```json
{
  "test_id": "TC-USER-004",
  "target_url": "https://staging.app.io/admin/users",
  "navigation_prerequisites": [
    {"action": "goto", "target": "https://staging.app.io/dashboard"},
    {"action": "click", "target": "a:has-text('User Management')"}
  ],
  "test_steps": [
    {"action": "click", "selector": "#btn-new-user", "description": "Open Add User modal"},
    {"action": "fill", "selector": "input[name='user_email']", "value": "invalid-email-address"},
    {"action": "click", "selector": "button.btn-primary", "description": "Click save button"}
  ],
  "assertions": [
    {"type": "to_be_visible", "selector": ".invalid-feedback"},
    {"type": "to_have_text", "selector": ".invalid-feedback", "expected": "Invalid email format"}
  ]
}
```

---

### D. The Executable Playwright Test Script (Generated & Executed)
Pack 3 runs this code inside its execution harness:

```python
import pytest
from playwright.async_api import async_playwright, expect

async def test_TC_USER_004():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state="runs/run_01/artifacts/storageState.json",
            record_video_dir="runs/run_01/artifacts/execution/videos/"
        )
        page = await context.new_page()
        await context.tracing.start(screenshots=True, snapshots=True)

        try:
            # 1. Navigation Prerequisites
            await page.goto("https://staging.app.io/dashboard")
            await page.click("a:has-text('User Management')")
            await page.wait_for_url("**/admin/users")

            # 2. Execute Test Steps
            await page.click("#btn-new-user")
            await page.fill("input[name='user_email']", "invalid-email-address")
            await page.click("button.btn-primary")

            # 3. Perform Falsifiable Assertions
            error_locator = page.locator(".invalid-feedback")
            await expect(error_locator).to_be_visible(timeout=5000)
            await expect(error_locator).to_have_text("Invalid email format")

            status = "PASSED"

        except Exception as e:
            status = "FAILED"
            await page.screenshot(path="runs/run_01/artifacts/execution/screenshots/fail_TC_USER_004.png")
            raise e

        finally:
            await context.tracing.stop(path="runs/run_01/artifacts/execution/traces/trace_TC_USER_004.zip")
            await context.close()
            await browser.close()
```

---

### E. Execution Evidence & Diagnostic Output
If the test fails because the developers renamed the field to `input[name="email_addr"]`:
* **Raw Error:** `TimeoutError: locator.fill: Timeout 5000ms exceeded waiting for input[name='user_email']`
* **Pack 3 Diagnostic Classifier Verdict:**
  ```json
  {
    "test_id": "TC-USER-004",
    "verdict": "FAILED",
    "classification": "LOCATOR_DRIFT",
    "root_cause": "The element 'input[name=user_email]' could not be found on /admin/users. DOM inspection indicates field was renamed to 'input[name=email_addr]'.",
    "is_application_bug": false,
    "evidence": {
      "screenshot": "artifacts/execution/screenshots/fail_TC_USER_004.png",
      "video": "artifacts/execution/videos/TC_USER_004.webm",
      "trace": "artifacts/execution/traces/trace_TC_USER_004.zip"
    }
  }
  ```

---

## 6. Summary of Pack 3 Deliverables

When Pack 3 finishes executing, the user receives 4 standardized deliverables in `runs/<run-id>/artifacts/`:

1. **`execution_report.md`:** Summary of passed, failed, and blocked tests, execution times, and overall pass rate.
2. **`ui_drift_report.md`:** Discrepancies between what the source code specification expected vs. what the live UI actually exposes (e.g. missing buttons, renamed labels, changed field validations).
3. **`failures_and_blockers.md`:** Detailed triage report categorizing all failures into the 6 buckets with links to screenshots, videos, and trace zips.
4. **Streamlit UI Video Player & MCP Visualizer:** An interactive tab in `app.py` where developers can watch video replays of failed tests, view MCP JSON-RPC logs, and launch the Playwright trace viewer with one click.

---

> 💡 **Key Takeaway:**  
> Pack 3 takes the **abstract "what to test"** from Pack 2, discovers the **concrete "where to click"** from the live UI (optionally via **MCP JSON-RPC**), executes the suite using Playwright, and gives you **actionable video/trace evidence and root-cause failure triage**.
