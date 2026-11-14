# Panel collaboration

The user requested this collaboration structure for this workspace:

```text
User ↔ MAIN ↔ MANAGER
                  ↔ PLAN
                  ↔ FE
                  ↔ BE
                  ↔ REVIEW
                  ↔ TESTER
```

## Routing

- MAIN receives user requests and forwards development, review, and testing tasks to MANAGER. MAIN reports MANAGER's evidence and blockers to the user. MAIN may manage panel names/layout directly.
- MANAGER is the sole coordinator for PLAN, FE, BE, REVIEW, and TESTER. It obtains a reviewed execution plan, assigns scoped tasks against its accepted version, resolves dependencies, routes questions/results, and integrates the final report. It does not duplicate workers' implementation work.
- PLAN, FE, BE, REVIEW, and TESTER receive assignments from MANAGER and report questions, blockers, and results to MANAGER. All inter-panel feedback passes through MANAGER; do not assign work to one another or use the former BE-owned review routing.
- Use the current `orca skills get orchestration` guide and structured Run/Dispatch messaging. A dispatched worker follows its exact injected Task/Dispatch instructions. Use `send` for worker guidance, `ask` for blocking questions, and `worker_done` with evidence for completion.
- `.coordination/panels.json` records current handles and routing. Handles are runtime-scoped: after restart/staleness, rediscover the existing panels instead of creating duplicates. MANAGER owns this routing record after initial setup.

## Ownership and concurrency

| Panel | Owned work |
|---|---|
| MAIN | User communication, forwarding requests, panel administration |
| MANAGER | Assignments, coordination records, dependency resolution, consolidated reports |
| PLAN | Only the assigned versioned plan file under `.coordination/plans/`; no product edits, runtime operations, or direct assignments |
| BE | `BE/`: Java, server configuration/security, Gradle, scripts/tools, backend integration checks; root README and .gitignore |
| FE | `FE/`: chat HTML/CSS/JavaScript, FE-only tests and usage documentation |
| REVIEW | Read-only plan executability, code/security and test-design/evidence review |
| TESTER | `BE/tests/scenarios/`, `BE/tests/e2e/` test-only files and `BE/test-results/` results; assigned real API/MCP/browser verification and execution-environment custody after explicit handoff |

- MANAGER may refine ownership for a task, but two panels must not edit the same files concurrently.
- While REVIEW examines a stable set of files, FE/BE continue independent work. Changes to the reviewed files require notifying MANAGER and identifying what needs re-review.
- MANAGER appoints one owner for each shared build/server/port-sensitive verification. Avoid concurrent clean/build processes or competing servers on the same ports.
- Preserve existing work during migration. Do not reset orchestration state, kill panels, or duplicate ongoing assignments merely to change coordinators.
- Keep the panels open. Do not create more panels/agents/worktrees unless the user requests them or an actual conflict requires an authorized change.

## Reviewed planning gate

Policy ID: `plan-gate-20261114/v2`. The first configuration cycle and its evidence are recorded in `.coordination/plan-execution.json`; MANAGER owns that execution record and `panels.json`, while PLAN exclusively owns its assigned plan document.

1. For a new substantive user task, MAIN forwards the request to MANAGER, which asks PLAN for a minimal plan covering outcome/scope, observed facts and assumptions, deliverables, each panel's work, dependencies and safe parallel work, verification/completion criteria, and actions that genuinely require additional authority. Ordinary notifications, status questions, ACKs and evidence delivery do not create new plans.
2. PLAN sends a versioned draft to MANAGER. MANAGER sends that exact version to REVIEW, which checks requirement coverage, feasibility, safety/authority, file/runtime conflicts and verification sufficiency using stable finding IDs and explicit blocking status. MANAGER relays feedback to PLAN; PLAN preserves prior versions and records responses. Repeat until REVIEW explicitly judges the exact version executable with no blocking findings.
3. MANAGER records the accepted version and binds each scoped execution assignment to its Task/Dispatch, owner, dependencies, verification criteria and evidence in the execution record before distributing work. REVIEW's executability judgement does not replace user approval or grant new authority. Existing approval boundaries remain unchanged. A material requirement or assumption change reopens only the affected scope through PLAN/REVIEW; unaffected work continues.
4. After implementation, retain the existing REVIEW/TESTER defect-fix-reverification cycle. After three failed fixes of the same finding, stop that branch and report the doubtful assumption and evidence; never loop indefinitely. PLAN verifies plan conformance read-only and never operates runtime or assigns another panel.
5. MANAGER sends MAIN the final integrated result only when the request's work, required checks/reviews and requested artifacts are complete. Do not repeatedly send routine progress to MAIN. Immediately report a genuine blocker, needed approval or actual interruption/failure; answer MAIN's explicit status questions. Distinguish independently completed scopes and do not count configuration completion as product/artifact completion.

For this initial gate configuration, preserve the already-completed product tests and real screenshots. Demonstrate the actual draft → review → feedback → revision → accepted version → applied policy cycle, collect PLAN/REVIEW/FE/BE/TESTER ACKs tied to the stable applied policy version, and obtain both PLAN and REVIEW application checks. Evidence-only additions to the execution record do not change the policy; substantive policy changes require affected roles to re-ACK and REVIEW to recheck.

## Completion and scope

- Reports include changed files, commands actually run, results, unresolved findings, and limitations. A planned or unexecuted check is not a passed check.
- MANAGER forwards review findings to the owning implementer and requests relevant verification after fixes. Do not mark the overall task complete while required checks or material findings remain unresolved.
- When no independent work remains, yield for Orca notifications instead of polling or sleeping in a loop.
- Keep the demo minimal: Spring AI MCP server, Spring MVC API, and a plain HTML chat client. The current domain is User → Role → Policy → Resource self-access, visible/requestable catalogs and protected administrator relationship management, with API-owned authorization, transactional audit and verified-user delegation. The user additionally requested local-profile MySQL via project Dockerfile/Compose and Flyway-managed schema/seed data. Do not add unrelated infrastructure, external LLM calls, or fake successful MCP responses.
- Existing user approval requirements remain in effect, including approval for global installation/configuration changes, external writes/deployment, git commit/push/PR, and destructive changes. Local scoped implementation and verification are authorized.

## Source layout

- Backend application modules, Gradle wrapper/build, scripts, and tools belong in `BE/`. Backend commands run from `BE/`.
- The only maintained chat sources are `FE/chat.html`, `FE/chat.js`, and `FE/chat.css`; frontend tests and documentation stay under `FE/`.
- The MCP build includes only those three deployable files as same-origin static resources. Never package the entire FE directory, tests, documentation, credentials, or private data.
- During migration, FE moves its owned sources first and reports completion; BE then moves the backend tree and updates build references. BE owns development builds and checks until handoff. After MANAGER sends TEST_READY with exact processes/artifacts/ports, TESTER exclusively owns the agreed server/container/port-sensitive runtime verification and browser session; FE does not control that session. Build changes require another serialized handoff.
- Root `README.md`, `AGENTS.md`, `spring-boot-mcp-guide.md`, and `.coordination/` remain shared workspace material. MANAGER owns coordination records and this collaboration policy.

## Runtime verification handoff

- BE preserves current processes until a safe handoff, reporting PID, port, cwd, new artifact path/hash, profile, fixture paths (no values), Compose project/volume and DB readiness.
- MANAGER grants one explicit runtime owner at a time. TESTER starts real API/MCP servers from the handed-off build and reports exact inputs, expected/actual outputs and passed/failed/blocked/unexecuted distinctions.
- TESTER may edit only its dedicated `BE/tests/e2e/` files and sanitized result artifacts; product fixes and BE/scripts/verify.py edits remain with BE, FE fixes with FE.
- TESTER only stops processes it started or was explicitly handed. No user-volume deletion, down -v/prune, unrelated process stops, global install/login/config changes. REVIEW stays read-only.

## File-based test requests and fixes

- MAIN receives natural-language requests such as “테스트 해봐”, “테스트 실행”, “재테스트”; MANAGER selects scope/build/environment and sends TEST_READY to TESTER. The user need not know filenames or commands.
- TESTER maintains executable JSON scenarios in `BE/tests/scenarios/` and a minimal registered-handler runner in `BE/tests/e2e/`, with list/all/ID/category selection. Invalid schema, duplicate/unknown IDs and missing handlers fail explicitly; no arbitrary eval/shell DSL.
- Each immutable run writes sanitized `BE/test-results/<run-id>/report.json` and `summary.md` with PASS/FAIL/BLOCKED/SKIPPED, profile/build identity, expected/actual differences and evidence. FAIL/BLOCKED must not produce overall success. Never save tokens/keys/passwords.
- TESTER reports only to MANAGER; MANAGER forwards results to MAIN and routes defects by scenario ID/reproduction/owner/fix scope/verification criterion to BE or FE. TESTER reruns relevant scenarios and regressions after a new build, linking prior reports without overwrite. REVIEW checks code/security/evidence.
- Scoped fixes in this loop are user-authorized. After three failed fixes to the same issue, stop that branch, name the doubtful assumption and report it; do not hide failures or loop indefinitely.
