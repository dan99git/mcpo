## MCPO Fork – Architectural Decisions & Rationale

Status: Living document (initial extraction 2025-08-19)
Scope: This file records intent, invariants, trade‑offs, and deferred work **for this enhanced fork** of `open-webui/mcpo`.

Legend:  
✅ Decided / Implemented | 🟡 Partial | ⏸ Deferred | 🔍 Needs validation | ❌ Rejected

---
### 1. Foundational Intent
| Item | Decision | Status | Notes |
|------|----------|--------|-------|
| Core purpose | Turn mcpo into a *self-manageable multi-tool orchestration layer* (UI + API) | ✅ | Adds config mutation, dependency management, dynamic servers |
| Primary persona | Local / LAN operator (MLOps engineer / power user) | ✅ | Security model assumes trusted environment |
| Non-goals | Full RBAC, hosted SaaS, deep analytics dashboard, advanced auth flows | ✅ | Keep surface tight |

**One-liner:** “A locally operated MCP-to-OpenAPI multi-server proxy with real-time management, persistence, and structured results.”

---
### 2. Architecture & Boundaries
| Topic | Decision | Status | Rationale |
|-------|----------|--------|-----------|
| Endpoint style | Per-tool dynamic FastAPI routes | ✅ | OpenAPI discoverability, tool clarity |
| Reload concurrency | Global async reload lock | ✅ | Prevent inconsistent mount state |
| Reinit vs reload | Separate `/reinit` for handshake refresh | ✅ | Avoid full process restart |
| Config source of truth | `mcpo.json` (static definitions only) | ✅ | Operational state separate |
| Internal admin tools | Exposed via same auth boundary (optional API key) | 🟡 | Acceptable for local trust; flag planned to disable |

**Invariants:** (1) Responses always envelope; (2) Tool call never exceeds enforced timeout; (3) Dynamic server add/remove is atomic or rolled back.

---
### 3. Configuration & State
| Aspect | Decision | Status | Notes |
|--------|----------|--------|-------|
| Enabled flags persistence | Sidecar `<config>_state.json` minimal schema | 🟡 | Only booleans + timestamp stored now |
| Embed operational state in config | Avoid | ✅ | Keeps `mcpo.json` portable & clean |
| Merge rules | Default enable missing entries; drop stale | ✅ | Forward compatibility |
| Planned enrichment | Add lastInit, failure counts, schema version | ⏸ | Needs schema version field |

---
### 4. Tool Execution Model
| Item | Decision | Status | Notes |
|------|----------|--------|-------|
| Session lifecycle | Initialize on mount; reinit explicit; graceful teardown via lifespan | ✅ | Predictable handshake control |
| Timeout strategy | Default + per-request override + max clamp | ✅ | Reliability + abuse guard |
| Auto circuit breaker | Not yet (planned) | ⏸ | Will pair with failure counters |
| Parallelism limit | None explicit | 🔍 | Potential future scheduling / queueing |

---
### 5. Schema & API Surface
| Feature | Decision | Status | Notes |
|---------|----------|--------|-------|
| anyOf & multi-type arrays | Supported (Union generation) | ✅ | Tests cover union cases |
| Enum propagation | Exposed via `json_schema_extra` | ✅ | Improves docs fidelity |
| Circular refs | Fallback → `Any` | ✅ | Prevent crash; ForwardRef future |
| Underscore params | Auto-alias stripping leading underscores | ✅ | Pydantic v2 naming constraints |
| Extended compositions (allOf) | Out of scope currently | ⏸ | Add if upstream tools demand |

---
### 6. Security Model
| Area | Decision | Status | Notes |
|------|----------|--------|-------|
| Auth mechanism | Optional single shared API key (Bearer/Basic) | ✅ | Simplicity for local use |
| Admin endpoint separation | Not implemented yet | ⏸ | Planned flag `--no-admin-tools` |
| Read-only mode | Planned (`MCPO_READ_ONLY=1`) | ⏸ | Will disable mutation endpoints |
| Package install tool | Allowed (local trust) | 🟡 | Document risk clearly |
| Command whitelist | Deferred | ⏸ | Introduce if remote exposure grows |

Threat posture: Low external exposure; emphasize documentation over heavy enforcement.

---
### 7. Dependencies & Runtime
| Decision | Status | Notes |
|----------|--------|-------|
| Python ≥3.11 required | ✅ | Modern async + Pydantic performance |
| Node runtime optional? | Contextual (needed for npm-based MCP servers) | 🟡 | Document clearly |
| Unused auth libs (jwt/passlib) | Candidate removal / extras group | 🔍 | Clarify before 0.1.0 |
| Lockfile (`uv.lock`) | Committed for reproducibility | ✅ | Core deps stable; runtime add-ons via requirements.txt |

---
### 8. Testing Strategy
| Area | Coverage Intent | Status | Gap Action |
|------|-----------------|--------|-----------|
| Timeout paths | Functional edge cases | ✅ | — |
| Structured output | Base + simple types | ✅ | Add mixed/complex items |
| Management mutations | Enable/disable/add/remove/reinit | 🔍 | Write integration tests |
| Concurrency reload | Serial lock behavior | ⏸ | Simulate simultaneous POSTs |
| Persistence merge | Load + merge semantics | ⏸ | Add state file test |

Target: Confidence over raw %; focus next on mutation flows & persistence.

---
### 9. Observability & Ops
| Feature | Decision | Status | Notes |
|---------|----------|--------|-------|
| Log buffer (INFO+) | Implemented | ✅ | Basic in-memory ring |
| Structured log fields | Recommended pattern documented | 🟡 | Not all code paths structured |
| Metrics endpoint | Planned simple JSON counters | ⏸ | Add after mutation tests |
| Backoff on init failures | Planned exponential | ⏸ | Avoid hammering failing servers |

---
### 10. UI / UX Philosophy
| Principle | Implementation | Status |
|-----------|---------------|--------|
| “No placeholders” | Only real, wired controls shown | ✅ |
| Polling updates | Chosen for simplicity | ✅ |
| Live events (SSE/WebSocket) | Future | ⏸ |
| Accessibility & scaling | TODO items listed (keyboard focus, virtualization) | 🔍 |

---
### 11. Release & Versioning
| Decision | Status | Notes |
|----------|--------|-------|
| Semantic versioning | Adopted (0.0.x pre‑0.1.0) | ✅ | 0.1.0 = stability + tests for mgmt flows |
| Known good snapshot list | To be added in README | 🔍 | Helps reproduce environment |
| Changelog discipline | Maintained; ensure drift minimized | 🟡 | Sync diary vs changelog regularly |

---
### 12. Risk & Failure Modes
| Risk | Mitigation | Status |
|------|-----------|--------|
| Config corruption | Validation + backup write | ✅ |
| Hanging tool | Timeout + upper bound | ✅ |
| Partial reload state | Atomic diff + rollback | ✅ |
| Abuse of admin endpoints | Local trust assumption | 🟡 |
| Runaway failing server | Circuit breaker missing | ⏸ |

---
### 13. Documentation & Governance
| Source | Role | Notes |
|--------|------|-------|
| README.md | User-facing quickstart + capability summary | Keep concise |
| project-diary.md | Deep rationale & roadmap | Primary reasoning source |
| CHANGELOG.md | Release delta log | Avoid architectural rationale here |
| DECISIONS.md | Canonical decisions ledger | This file; update when direction changes |

Conflict rule: **Rationale → diary/decisions; Behavior summary → README; Release notes → CHANGELOG.**

---
### 14. Performance & Scaling
| Aspect | Current | Planned |
|--------|---------|---------|
| Tool count threshold | UI warning > 40 enabled tools | ✅ | Virtualize tool tags if > N |
| Schema generation | On discovery (cached per model name) | ✅ | Hash to skip rebuilds |
| Endpoint explosion mitigation | One route per tool | 🟡 | Optional generic `/invoke` beyond threshold (⏸) |

---
### 15. Extensibility
| Feature | Decision | Status |
|---------|----------|--------|
| Pre/post hooks | Not implemented | ⏸ |
| Additional transports | Support stdio / sse / streamable-http | ✅ |
| Plugin system | Not planned short-term | ⏸ |
| Structured streaming output | Format reserved (`collection`) | 🟡 |

---
### 16. Migration & Compatibility
| Area | Strategy | Status |
|------|----------|--------|
| Config evolution | Unknown keys ignored; additive changes safe | ✅ |
| State file evolution | Version field (planned) | ⏸ |
| CLI backwards compatibility | Preserve existing flags; add new behind defaults | ✅ |

---
### 17. Open Questions / Pending Decisions
| ID | Question | Current Stance | Trigger to Decide |
|----|----------|----------------|------------------|
| Q1 | Implement `--no-admin-tools`? | Likely yes | Before wider public promotion |
| Q2 | Remove jwt/passlib now or move to extras? | Lean remove | Pre 0.1.0 |
| Q3 | Circuit breaker thresholds? | Not set | After failure telemetry exists |
| Q4 | Switch to event push for UI? | Later | Performance complaints / scaling |
| Q5 | Enrich state file schema? | Yes | After persistence tests merge |
| Q6 | Metric format: JSON vs Prometheus? | Start JSON | Community asks |
| Q7 | Add generic invoke fallback? | Defer | Endpoint scale > threshold |

---
### 18. Immediate Focus (Execution Queue)
1. Add mgmt mutation & persistence tests (enable/disable, add/remove, reinit, state reload).
2. Introduce `--no-admin-tools` flag (skip mutation + install endpoints when set).
3. Audit all 403 paths for `code="disabled"` consistency.
4. README: Fork Differences + Known Gaps + Known Good Versions.
5. Decide on jwt/passlib removal; update dependencies accordingly.

---
### 19. Update Process
When a decision changes:
1. Amend relevant table row (do not delete old rows—strike through if reversed).  
2. Reference commit hash in Notes if impactful.  
3. If it alters user-facing behavior, sync README + CHANGELOG.  

---
### 20. Meta
Maintainer Intent: Favor **clarity over premature abstraction**, **atomic reloads over partial patches**, and **documented gaps over hidden complexity**.

_Generated initial draft from retrospective analysis. Refine iteratively._
