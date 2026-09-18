# Testing Requirements Analysis — Talents Scooper / NeuLeap Resume Agent

Purpose: the types of testing this project needs, and the requirements a test-case requirements-generator tool should focus on.

## 1. System overview

**Stack**
- Backend: FastAPI + LangGraph, ~211 endpoints, PostgreSQL (38 tables), MinIO/S3, Azure OpenAI, Microsoft Graph (MSAL), APScheduler, Tesseract OCR, Langfuse
- Frontend: React 18 + TypeScript + Vite, React Query, Radix UI, Tailwind, 17 pages
- Deployment: Docker Compose, nginx, updater container (signed upgrade bundles)

**Current test coverage: none.** `requirements.txt` refers to `app/tests/`, but that folder does not exist.

**Pipeline flow**

```
Outlook mail (Graph poller) / Upload
  → L1 validate (pdf / docx / doc / img / zip / xlsx / links)
  → L2 integrity → identity → fuzzy dedup → store
  → L3 LLM extraction + OCR
  → ATS / NLRS scoring → Review Queue
  → Rounds: Screening → Round 1 → Round 2 → [Round 3 optional] → Final → Hired
  → Onboarding (9 stages)

Side modules: Chatbot (NL→SQL), JD Match, Question Banks, Interviewer portal,
Candidate slot booking portal, Interviewer pools, Backup / Upgrade / System wipe,
Notifications, Feedback, Token usage, Cron jobs
```

---

## 2. Testing types and requirements, by priority

### 2.1 Security (Priority: Critical)

| Area | Requirement to test | Reference |
|---|---|---|
| Unauthenticated endpoints | No `Depends(...)` guard and no global auth middleware on these routers. Every route must reject anonymous calls. | `app/api/chatbot.py`, `app/api/extraction.py`, `app/api/intake.py`, `app/api/branches.py`, `app/api/notifications.py` |
| Resume download (PII) | `GET /candidates/{id}/resume` must require auth | `app/api/branches.py:67` |
| RBAC | Role × endpoint grid for `recruiter`, `admin`, `sysadmin` (`require_admin`, `require_backoffice_access`, `require_sysadmin`) | `app/api/auth.py:169-185` |
| JWT | Tampered, expired and role-less tokens. A token with no `role` claim **defaults to admin**. Expiry is 30 days. | `app/api/auth.py:165`, `app/config.py:119` |
| Chatbot NL→SQL | Prompt injection, CTEs, `pg_sleep`, `pg_read_file`, system catalogs, SQL comments, multi-statement, non-allow-listed tables, LIMIT 200 enforcement, READ ONLY transaction | `app/api/chatbot.py`, `app/repositories/chatbot_repo.py` |
| Public token portals | OTP expiry, OTP reuse, brute force / rate limiting, token guessing, cross-candidate access (IDOR) | `app/api/interviewer.py`, `app/api/candidate_slots.py`, `app/services/candidate_slot.py` |
| File upload attacks | Zip bomb (ratio / entries / total / per-entry / depth 8), path traversal in zip entries, password-protected Office files, spoofed extensions, oversize files | `app/pipeline/mail/tools/validation.py`, `app/mail_config.py:57-80` |
| Configuration | CORS `allow_origins=["*"]`, committed `.env`, LLM keys stored in `secrets/*.env` | `app/main.py:176` |

### 2.2 Functional / business logic (Priority: Critical)

- **Stage machine:** only valid transitions allowed; auto-advance on Pass; optional Round 3 (`include_round3`); Final Pass → Hired; terminal states (`Hired`, `Rejected`, `Closed`, `Undecided`) freeze rounds; `Closed` / `Undecided` can be reactivated. See `app/stages.py`.
- **Candidate status lifecycle:** `Pending Extraction`, `Pending Resume`, `Active`, `Reapplication Review`, `Closed`, `Rejected`, `Hired`. See `app/constants/status.py`.
- **Dedup:** fuzzy name threshold (`token_sort_ratio`), email / phone match; duplicate vs reapplication vs duplicate-of-hired. See `app/pipeline/nodes/dedup.py`.
- **Review queue:** every `ReviewKind` routes correctly (11 kinds: invalid_file, excel_row, link_fetch, corrupt, not_resume, duplicate, duplicate_hired, reapplication, extraction_fail, failed, exhausted); resolve / restore / discard.
- **Scoring:** ATS and NLRS scores are deterministic; config edit → preview → save → revert. See `app/services/ats_scoring.py`, `app/services/nlrs.py`.
- **Onboarding:** 9-stage flow plus the default allocation checklist.
- **Archival:** candidate archive, nightly archival, batch archive / prune / restore.
- **Other modules:** source tags CRUD, email templates and draft review, auto-send, notifications (mark read / all read), feedback reports and XLSX export, question banks (create, duplicate, enrich, reseed, bulk-allocate, leaderboard), interviewer pools (create, archive, gather, sync, velocity).
- **Settings:** ~30 config endpoints, nearly all following a **save → reload → revert** pattern. Good candidates for template-generated tests.

### 2.3 File processing / data-driven (Priority: High)

- Formats: PDF (text), scanned PDF (OCR), DOCX, legacy DOC (LibreOffice conversion), JPG / PNG (OCR).
- Content variants: multi-column layouts, tables, non-English text, empty file, corrupt file, not-a-resume document.
- ZIP: nested zips, duplicate inner filenames (`john/resume.pdf` + `jane/resume.pdf` must both survive), mixed valid / invalid entries.
- Excel: rows with valid, missing, malformed and unreachable links.
- Boundary values from the `MAX_UPLOAD_BYTES` per-extension limits, `MAX_UPLOAD_ABSOLUTE_MB` (150), `ZIP_MAX_TOTAL_MB` (200), `ZIP_MAX_ENTRY_MB` (50), `ZIP_MAX_ENTRIES` (200), `ZIP_MAX_RATIO` (100).
- **The tool should output a test-data / fixture catalogue, not just test steps.**

### 2.4 AI / LLM quality (Priority: High)

- Extraction accuracy: field-level precision / recall against a golden resume set.
- Output schema validity; handling of malformed or partial LLM JSON.
- Chatbot: SQL correctness for the question, off-topic rejection message, destructive-intent rejection message, hallucination check on the narrated answer.
- JD match and JD explain relevance.
- Question-bank generation and interview-question relevance.
- Bias / fairness: scores shouldn't change with name, gender, college or location.
- Prompt / skill overrides (`SKILL.md` files): edit → rerun → revert.
- Token usage and cost tracking accuracy (Langfuse, `/token-usage`).

### 2.5 Integration (Priority: High)

- Microsoft Graph: inbox polling, folders and counts, discard / restore, calendar invites, sending mail, MSAL token refresh.
- Azure OpenAI, MinIO / S3 (bucket bootstrapping at startup), PostgreSQL (`init_db` on startup), Tesseract, LibreOffice.
- **Failure modes:** each dependency down, slow or returning errors; retry → `Exhausted` → review-queue path; startup must not crash when a dependency is unavailable.

### 2.6 Concurrency and scheduled jobs (Priority: High)

- Pipeline run-lock: manual `/pipeline/run-all` racing the cron run should return 409. See `app/api/orchestrator.py:80`.
- Cron jobs: pipeline run, nightly archival, slot reminders, interviewer pool sync. Test cron / timezone overrides and double execution.
- Slot booking races: two candidates booking the same slot; double-click on "book".
- Concurrent edits to the same candidate or round.

### 2.7 Data integrity and migration (Priority: High)

- Python enums ↔ SQL CHECK constraints ↔ TS constants stay in sync (`app/constants/status.py` ↔ `frontend/src/constants/status.ts`; `app/stages.py` ↔ `frontend/src/lib/rounds.ts`).
- Audit log is append-only; every candidate action is recorded.
- Archive tables mirror live tables (`candidate_master_archive`, `rounds_archive`).
- Destructive operations: **backup / restore, upgrade / rollback (signed bundles), system wipe**. Test upgrade from version N-1 with real data, a corrupt bundle and a bad signature.

### 2.8 UI / E2E (Priority: Medium-High)

- Critical flows:
  1. Login / forgot password / set password / change password
  2. Screening list → candidate detail → round verdict → stage advance
  3. Upload resume → pipeline → candidate appears
  4. Review queue resolve / restore
  5. Interviewer portal (OTP → feedback → question-bank marking)
  6. Candidate slot booking (OTP → book / help / remind me again)
  7. Settings save / revert
- Highest-risk pages by size: Screening (2,680 lines), Settings (2,251), Dashboard (2,057), InterviewAccess (1,620), QuestionBanks (1,403), IntakeMailMaster (1,294), Candidate (1,092).
- Responsive layouts (`BottomNav`, `useMediaQuery`), dark / light theme, `ErrorBoundary`, voice input (`useSpeechRecognition`), DOCX / PDF resume preview, charts (Recharts).
- Role-based UI visibility (`ProtectedRoute`, sidebar items per role).
- Accessibility: keyboard navigation, focus management in dialogs, contrast, screen-reader labels.

### 2.9 Performance (Priority: Medium)

- Pipeline throughput: resumes per minute across L1 → L3; LLM latency; OCR cost on scanned files.
- Large lists: pagination and filtering in Screening and Audit Logs; XLSX exports (audit logs, leaderboard, feedback).
- Chatbot response time with count queries.
- Graph poller load and Graph API throttling.

### 2.10 Other (Priority: Medium-Low)

- **Compatibility:** major browsers; email clients for the rendered templates.
- **Deployment:** docker-compose, nginx config, updater container, `scripts/fresh-env.*`, `upgrade.sh`, `rollback.sh`.
- **Compliance / privacy:** candidate PII retention, archival, wipe / deletion (GDPR / DPDP style).
- **Observability:** logging levels, audit trail completeness, health endpoints (`/intake/health`, `/backup/health`).

---

## 3. What the requirements-generator tool should extract

| # | Source in codebase | Generated requirement type |
|---|---|---|
| 1 | `@router.*` decorators + `Depends(...)` guards in `app/api/*.py` | API contract, authentication and RBAC requirements (flags unguarded routes) |
| 2 | `STAGE_ORDER`, `CandidateStatus`, `ReviewKind`, onboarding stages | State-transition tables (valid and invalid transitions) |
| 3 | Config constants (`ZIP_MAX_*`, `MAX_UPLOAD_BYTES`, `MAX_ROWS`, OTP TTL, JWT expiry) | Boundary-value requirements (min / max / just over) |
| 4 | Pydantic models (`app/models/schemas.py`) | Input validation requirements |
| 5 | External calls (Graph, OpenAI, S3, SMTP, OCR) | Integration and failure-mode requirements |
| 6 | LLM prompts / `SKILL.md` files | AI evaluation requirements with golden datasets |
| 7 | Frontend routes, pages and services | E2E flow requirements, tagged by role |
| 8 | Scheduler jobs and locks | Concurrency / scheduling requirements |

**Fields each generated requirement should include**

- Requirement ID
- Module / feature
- Test type (security, functional, UI, performance, ...)
- Priority / risk level
- Role (anonymous, recruiter, admin, sysadmin, interviewer, candidate)
- Preconditions
- Input / test data
- Steps
- Expected result
- Traceability (file and line)

---

## 4. Recommended order

1. **Security:** auth gaps + RBAC matrix + chatbot SQL + token portals
2. **Pipeline and state machine:** stages, statuses, dedup, review queue
3. **File processing:** formats, zip / excel handling, size limits
4. **LLM quality:** extraction accuracy, chatbot correctness, scoring fairness
5. Integration and concurrency
6. Data integrity, backup / upgrade / wipe
7. UI / E2E, then performance and the rest

These areas come first because a bug in any of them could leak candidate PII, lead to wrong hiring decisions or lose resumes.
