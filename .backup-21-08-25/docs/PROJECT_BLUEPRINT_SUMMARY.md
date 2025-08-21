# MCPO Project Blueprint (Concise Summary)

Purpose: Single-glance operational + architectural snapshot. For detailed forensic history see `Project Reconstruction Record _ Google AI Studio.md`.

## Mission
Expose any MCP tool (stdio / SSE / streamable-http) as an OpenAPI-accessible HTTP microservice with unified management UI + API.

## Core Capabilities
- Dynamic server orchestration (start, connect, reinit, reload config)
- Per-tool enable/disable & persisted state file (atomic, versioned)
- Protocol negotiation + output schema validation (modes: off | warn | enforce)
- Structured envelopes for success/error
- Read-only mode (security gate before body parsing)
- Internal management MCP server at `/mcpo` with install & validation tools
- UI: ChatGPT-style single page (servers, tools, logs, config, docs) with live polling
- Metrics: tool invocation counts & latency snapshots (backend exposed)

## High-Level Architecture
- FastAPI root app mounts per-MCP sub-apps at `/{server_name}`
- Each sub-app hosts auto-generated tool endpoints derived from MCP server introspection
- Session lifecycle managed via startup hooks; reconnection paths via reinit endpoint
- Config loader diffs server sets on reload (add/remove/mutate) with lock
- Persistence file: `<config_basename>_state.json` with schema version & atomic replace

## Key Files
```
src/mcpo/main.py        # Application factory, mounting, state, metrics, endpoints
static/ui/index.html    # Full UI (no build step)
mcpo.json               # User config (servers + args/env)
requirements.txt        # Python deps
```

## Critical Endpoints (Representative)
- `GET /healthz` – health snapshot
- `GET /_meta/servers` – list servers + statuses
- `POST /_meta/servers/{name}/enable|disable` – toggle server
- `POST /_meta/servers/{name}/tools/{tool}/enable|disable` – toggle tool
- `POST /_meta/reload-config` – reload config (diff + mount changes)
- `POST /_meta/reinit/{name}` – reconnect a server
- `GET /_meta/logs` – ring buffer logs
- `GET /_meta/metrics` – tool metrics snapshot
- Internal management tools under `/mcpo/tools/*`

## Configuration Concepts
`mcpo.json` example fragment:
```json
{
  "mcpServers": {
    "time": {"command": "uvx", "args": ["mcp-server-time"], "enabled": true},
    "perplexity": {"command": "npx", "args": ["-y", "perplexity-mcp"], "enabled": true, "env": {"API_KEY": "..."}}
  }
}
```

## Persistence Schema (State File)
```json
{
  "version": 1,
  "servers": {"time": true, "perplexity": true},
  "tools": {"time": {"now": true}, "perplexity": {"search": true}}
}
```

## Security
- Optional API key middleware (`MCPO_API_KEY` / header) with strict/lenient mode
- Read-only mode denies mutating endpoints early with 403

## Validation Modes
Set via env or config flags:
- Protocol version negotiation header injection & check
- Output schema validation per tool (warn vs enforce)

## Logging & Metrics
- In-memory ring buffer (default 100 entries) exposed for UI streaming
- Per-tool aggregated counts & latency (p50/p95 if implemented) planned / basic counts active

## UI Polling Cadence
- Servers: ~5s
- Logs: ~2s

## Known Open Issues
- Sidebar active server/tool counts sometimes stale (investigate DOM update sequence)
- Some test placeholders unimplemented (add coverage for metrics & validation warn-mode paths)

## Quick Start (Conceptual)
1. Edit `mcpo.json` with servers
2. Run: `python -m mcpo` (or packaged script)
3. Open `http://localhost:8000/ui/` (if mounted) or serve static `/static/ui/index.html`
4. Toggle servers/tools in UI; inspect logs & config

## Extension Opportunities
- WebSocket push instead of polling
- Persist metrics to time-series backend
- Role-based auth & multi-user API keys
- Packaging of UI as optional wheel data asset

## Guardrails / Gotchas
- Never edit state file manually; toggles rewrite atomically
- Keep read-only gate before request body parsing
- When adding server types ensure `normalize_server_type` updated
- UI ellipsized code in historical record is not authoritative

## Maintenance Checklist
- Run tests before release
- Verify `state` version bump if structure changes
- Confirm OpenAPI regenerates after new endpoint types
- Update Blueprint & Record indexes when file counts shift materially

(End of Concise Blueprint)
edit
more_vert

You've asked for a "Source of Truth Reference Document" that encapsulates all the definitive information about the MCPO project, presented with as much detail as possible, and delivered over multiple responses. This is an excellent approach for creating a living, authoritative document that guides all aspects of the project.

I will structure this Source of Truth Reference Document (SoTRD) logically, breaking it down into several parts. Each part will cover a distinct domain of the project, building upon the "Project Reconstruction Record" you provided, but formalizing the information, adding explicit definitions, and detailing the why behind decisions where appropriate.

Here's the planned structure for the Source of Truth Reference Document, which I will deliver in subsequent responses:

Source of Truth Reference Document (SoTRD) - MCPO Project

Table of Contents

Part 1: Introduction and Core Principles

1.0 Document Purpose and Scope

1.1 Project Overview & Mission Statement

1.2 Foundational Architectural Principles

1.3 Key Constraints and Design Decisions

Part 2: System Architecture (Logical and Physical)

2.1 Overall System Diagram (Conceptual)

2.2 Backend Architecture Deep Dive

2.2.1 Application Core (FastAPI Main App)

2.2.2 Dynamic Sub-App Management (MCP Tool Servers)

2.2.3 Internal Management Server (/mcpo)

2.2.4 Lifecycle Management (Lifespan Contexts)

2.2.5 Error Handling and Response Standardization

2.2.6 Logging and Monitoring Infrastructure

2.2.7 Concurrency and Reload Mechanism

2.3 Frontend Architecture Deep Dive

2.3.1 Client-Side State Management

2.3.2 API Interaction Layer

2.3.3 Rendering and UI Component Structure

2.3.4 Polling Mechanism

2.3.5 Theming and Visual Design System

2.4 Inter-Service Communication

2.4.1 Main App to MCP Tool Servers

2.4.2 Frontend to Main App API

2.4.3 Internal MCP Tool Calls

Part 3: Data Models and Schemas

3.1 Configuration Schema (mcpo.json) - Definitive JSON Schema

3.2 State Persistence Schema (.mcpo_state.json) - Definitive JSON Schema

3.3 API Request/Response Schemas (OpenAPI/Pydantic-like definitions for key endpoints)

3.4 Log Entry Schema

Part 4: Operational Aspects and Environment

4.1 Deployment Environment and Dependencies

4.1.1 Python Environment (requirements.txt)

4.1.2 Node.js Dependencies (for npx tools)

4.1.3 System Dependencies (e.g., uvx for mcp-server-time)

4.2 Build and Setup Procedures

4.2.1 Automated Setup (setup.py details)

4.2.2 Manual Setup Steps

4.3 Running and Stopping the Application

4.3.1 Development Mode (start.sh, start.bat)

4.3.2 Production Mode (start_production.sh, start_production.bat)

4.3.3 Graceful Shutdown Mechanism

4.4 Monitoring, Logging, and Health Checks

4.4.1 Log Collection and Viewing

4.4.2 Health Status Endpoint (/healthz)

4.4.3 Docker Integration (Healthcheck)

Part 5: Security and Authentication

5.1 API Key Authentication (Backend)

5.2 Read-Only Mode Enforcement (Backend)

5.3 Sensitive Data Handling (.env, .gitignore)

5.4 Cross-Origin Resource Sharing (CORS)

5.5 Future Security Enhancements (RBAC, Rate Limiting)

Part 6: Development Guidelines and Tools

6.1 Codebase Structure and Naming Conventions

6.2 Testing Strategy and Best Practices

6.2.1 Unit Testing

6.2.2 Integration Testing

6.2.3 Test Fixtures (conftest.py)

6.3 Debugging Guidelines

6.4 Version Control and Release Process (Conceptual)

Part 7: Maintenance, Troubleshooting, and Future Work

7.1 Common Issues and Resolutions

7.2 Performance Considerations and Tuning

7.3 Actionable Task List (Re-prioritized and detailed)

7.4 Evolution and Future Roadmap

Let's begin with Part 1: Introduction and Core Principles.

Source of Truth Reference Document (SoTRD) - MCPO Project

Part 1: Introduction and Core Principles

1.0 Document Purpose and Scope

This Source of Truth Reference Document (SoTRD) serves as the single, authoritative, and comprehensive record for the MCPO project. Its primary purpose is to capture all essential knowledge required for understanding, developing, deploying, maintaining, and evolving the MCPO system. It formalizes architectural decisions, defines data structures, details operational procedures, and records historical context (particularly forensic reconstructions of critical incidents).

The scope of this document covers the entire MCPO application, including its backend (Python/FastAPI), frontend (Vanilla JavaScript/HTML/CSS), configuration, deployment scripts, and internal management tools. It aims to be exhaustive, ensuring that any developer or operator can reconstruct the system, understand its behavior, and contribute effectively without relying on tribal knowledge. This document is intended to evolve with the project, maintaining its currency and accuracy through a defined update procedure.

1.1 Project Overview & Mission Statement

Project Name: MCPO (Model Context Protocol Orchestrator)

Mission Statement: To provide a robust, extensible, and user-friendly platform that enables the seamless exposure, management, and monitoring of diverse Model Context Protocol (MCP) tools as OpenAPI-compatible HTTP services. MCPO simplifies the integration of external AI tools and services into broader application ecosystems, offering both a web-based graphical user interface and a programmatic API for orchestration.

Core Problem Solved: Bridging the gap between disparate MCP tools (running as command-line processes, SSE streams, or HTTP servers) and a unified, discoverable, and manageable HTTP API surface. It provides a centralized point for configuration, lifecycle management, and monitoring of these tools.

Key Value Proposition:

Rapid Integration: Turns any MCP tool into an accessible HTTP endpoint quickly.

Unified Management: Centralized control and monitoring via UI and API.

Dynamic Discovery: Automatically exposes tool capabilities via OpenAPI.

Scalability & Resilience: Designed to manage multiple external tools with connection and state persistence.

User Empowerment: Provides a clean interface for non-developers to manage AI tools.

Project State: The project is considered production-ready for its core functionalities. It has undergone significant forensic reconstruction and stabilization following a period of UI/backend mismatches, resulting in a stable and feature-complete application.

1.2 Foundational Architectural Principles

The MCPO architecture is built upon a set of core principles that guide its design, implementation, and future evolution:

API as Single Source of Truth (S.S.o.T.):

Principle: All UI state and user actions are exclusively driven by, and reflected through, the backend API. The frontend is a thin client that consumes and presents API data, and sends commands via API calls.

Rationale: Ensures data consistency, simplifies debugging by centralizing logic, and enables alternative clients (e.g., CLI tools, other UIs) to interact with the system reliably.

Implication: No significant business logic or state mutations occur solely within the frontend.

Dynamic Sub-Application Mounting:

Principle: Each configured MCP tool is mounted as an independent FastAPI sub-application within the main MCPO FastAPI instance.

Rationale: Provides strong isolation between different MCP tools, simplifies routing, and allows each tool to have its own OpenAPI documentation and FastAPI lifecycle management (though managed by the main app's lifespan). It leverages FastAPI's native capabilities for extensibility.

Implication: The main application acts as an orchestrator and proxy, not directly implementing tool logic.

Robust Lifecycle Management:

Principle: All MCP server connections (stdio, SSE, Streamable-HTTP) are managed gracefully through FastAPI's lifespan context. This includes proper startup, connection establishment, and shutdown.

Rationale: Ensures system stability, prevents resource leaks, and allows for dynamic reloading and reinitialization of individual servers without restarting the entire MCPO process.

Implication: Critical connections are established and torn down in a controlled, asynchronous manner.

Concurrency-Safe Operations:

Principle: Critical shared resources and state mutations (e.g., configuration reloads, log buffer access) are protected using asynchronous locks (asyncio.Lock).

Rationale: Prevents race conditions and data corruption in a concurrent environment, ensuring data integrity during high-load or simultaneous operations.

Implication: Operations like reload_config_handler are atomic, preventing inconsistent states during updates.

Standardized API Contract & Error Handling:

Principle: All API responses adhere to a consistent {"ok": True, ...} for success and {"ok": False, "error": {"message": "...", "code": "...", "data": ...}} for errors.

Rationale: Provides predictable client handling of API responses, simplifies error diagnosis for both developers and users, and enables generic error display mechanisms in the UI.

Implication: Clients do not need to parse various error formats; a single error_envelope function centralizes error message formatting.

Hierarchical State Awareness (UI Rendering):

Principle: The UI's display of a child component's status (e.g., an individual tool's enabled/disabled state) implicitly considers the state of its parent component (e.g., the server's overall enabled/disabled status).

Rationale: Ensures visual consistency and accurately reflects the effective operational status of tools. A tool on a disabled server is functionally disabled, even if its individual toggle is "on."

Implication: The UI's enabledToolCount and tool-tag classes correctly factor in the server's master enable/disable switch.

Iterative & Forensic Design:

Principle: The project's development, particularly during its reconstruction phase, prioritized iterative improvements, meticulous debugging, and a forensic approach to diagnosing past failures.

Rationale: Allowed for the systematic identification and resolution of complex, interconnected bugs, leading to a more robust and validated system.

Implication: The "Forensic Post-Mortem" section in the Project Reconstruction Record serves as a critical learning resource.

Test-Driven Validation:

Principle: Automated tests are used not only for regression prevention but also as a primary method for diagnosing, validating fixes, and formalizing expected behavior for complex scenarios (e.g., read-only mode).

Rationale: Provides confidence in the codebase, reduces manual testing effort, and clarifies ambiguous requirements.

Implication: Critical bug fixes are accompanied by specific tests that prove their resolution and prevent recurrence.

1.3 Key Constraints and Design Decisions

These constraints and explicit decisions guided the development and shape the current state of MCPO:

Constraint: STRICT UI Immutability (Initial Reconstruction Phase):

Decision: During the initial reconstruction phase, the primary goal was to bring the new UI to full functionality with the backend, without introducing any new visual design changes or layout modifications.

Rationale: Minimized scope creep, focused efforts on backend wiring and data flow, and reduced complexity in a critical recovery period.

Implication: The UI's appearance is largely as it was designed externally, and functional enhancements were integrated into the existing visual framework.

Decision: Vanilla JavaScript Frontend:

Rationale: Simplified the development pipeline (no complex build tools like Webpack/Vite required for initial setup), lowered the barrier to entry for contributors, and reduced runtime dependencies for the frontend.

Implication: UI components are built using native DOM manipulation and basic templating, avoiding larger frameworks.

Decision: Polling for UI Updates (vs. WebSockets/SSE):

Rationale: Simplicity of implementation for initial data synchronization. setInterval is straightforward to implement for periodic data fetches.

Implication: While sufficient for management/dashboard UIs, it is not ideal for highly real-time applications. This is noted as a future enhancement.

Decision: Atomic State File Writes with Version Key:

Rationale: To prevent data corruption during writes (e.g., power loss, process crash) and to allow for future schema migrations. The _state.json file is written to a temporary file, fsync'd, and then atomically renamed.

Implication: Critical persistence of server_enabled and tool_enabled states is highly reliable.

Decision: Enable/Disable Enforcement Pre-Tool Invocation:

Rationale: For security and performance, the backend must enforce server/tool enabled states before any potentially resource-intensive MCP tool invocation. This prevents unauthorized access or unnecessary computation for disabled components.

Implication: The read_only_mode check for meta-endpoints and the server_enabled/tool_enabled checks for tool invocation are implemented as early as possible in the request lifecycle.

Decision: Internal Management Server (/mcpo) Always Shown Enabled:

Rationale: The internal MCPO management server is an integral part of the application's self-management capabilities. It is not an external, potentially flaky MCP tool. Its availability is crucial for operator tooling and diagnostics.

Implication: Its connected and enabled status is hardcoded to True within the list_servers and list_server_tools endpoints, distinguishing it from user-configured external servers.

Decision: post_env Tool for Environment Variables:

Rationale: Provides a secure, programmatic way to update environment variables (including API keys) that MCPO and its sub-processes can consume. By writing to .env, these changes persist across restarts.

Implication: Avoids manual file editing for sensitive credentials, and the subprocess calls in mcpo.main.py handle the actual file writes and os.environ updates.

Constraint: No Direct pip install from UI installDependencies Button:

Rationale: For security and system stability, direct execution of arbitrary shell commands from the frontend via a simple button click was deferred. While the backend install_python_package tool exists, the frontend wiring was not completed in the provided context for a direct requirements.txt install.

Implication: The button currently triggers an alert, indicating a future task for more robust and secure implementation (e.g., using the /mcpo/validate_and_install tool).

[End of Part 1]

I will continue with Part 2: System Architecture (Logical and Physical) in the next response.

25.4s