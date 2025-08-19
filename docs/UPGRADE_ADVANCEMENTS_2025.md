# MCPO 2025 Upgrade & Advancement Plan

_Last reviewed: 2025-08-19_

## 1. Purpose
Capture mid-2025 MCP protocol and OpenAPI (3.1 / JSON Schema 2020-12) advancements, assess current mcpo alignment, and define a phased, low-risk upgrade roadmap that preserves backward compatibility while positioning mcpo as a standards-forward orchestration proxy.

## 2. Summary of External Advancements
### 2.1 MCP Protocol (June–Aug 2025)
| # | Change | Essence | Why It Matters |
|---|--------|---------|----------------|
| 1 | JSON-RPC batching removed | One request per tool call | Simpler, predictable execution / rate control |
| 2 | Structured tool output + outputSchema | Tool responses must conform to declared schema | Enables reliable automation & validation |
| 3 | OAuth 2.1 resource server model | Resource indicators + token introspection required | Scoped access; prevents token misuse |
| 4 | Elicitation flows | Server can pause and request additional user input schema | Interactive, multi-step tools |
| 5 | Resource links in outputs | Canonical references / IDs in tool responses | Composability & chaining |
| 6 | Required MCP-Protocol-Version header | Must accompany every client request post-handshake | Negotiation & forward compat |
| 7 | Title field required (human-facing metadata) | Display names distinct from identifiers | Better UX / localization |
| 8 | Lifecycle hooks promoted to MUST | create/update/activate/suspend/delete normalized | Uniform operational controls |
| 9 | Security tightening | OAuth 2.1 best practices, remove ambiguous batching | Reduced attack surface |
|10 | Expanded completion / context inputs | Richer LLM-contextual interactions | Higher quality tool orchestration |

### 2.2 OpenAPI & Tool Server Ecosystem
| Area | Advancement | Relevance to mcpo |
|------|------------|-------------------|
| JSON Schema 2020-12 | Full parity (OpenAPI 3.1) | Richer tool arg/response modeling |
| Webhooks | Native webhook-only specs | Future async callback pattern |
| Optional paths | Component-only or webhook-only docs | Modularization for partial bundles |
| Server blocks & variables | Multi-env & tenant substitution | Provide dev/staging/prod endpoints |
| Security schemes breadth | OAuth2 / mTLS / API key unified | Enterprise adoption path |
| Streaming | SSE & event streaming normalization | Better LLM incremental output |
| Async jobs | Standardized long-running job patterns | Progress + resumption semantics |
| Reserved x-oas-* | Namespace governance | Extension hygiene |
| Codegen maturity | Multi-env SDK scaffolding | Faster client integration |

## 3. Current mcpo Alignment Matrix
| Capability | Present Now | Status | Gap Notes |
|------------|-------------|--------|-----------|
| One-call execution (no batching) | Yes | Aligned | N/A |
| Structured tool output schema enforcement | Partial (basic normalization) | Gap | Need schema extraction + validation layer |
| MCP-Protocol-Version header propagation | Injected outbound (basic) | Partial | Add negotiation + inbound validation |
| OAuth 2.1 resource server | No | Gap | Requires discovery + token introspection + resource indicators |
| Elicitation support | No | Gap | Requires stateful suspended call envelopes |
| Resource links surfacing | Minimal (opaque content passthrough) | Gap | Parse & annotate link types in OpenAPI |
| Human-friendly titles | Basic (tool name reuse) | Partial | Add title/description field mapping & localization hook |
| Lifecycle operations (activate/suspend/delete) | Disable/enable boolean only | Partial | Expand to full lifecycle states |
| Read-only mode | Yes | Aligned | Consider admin-endpoint hard-disable flag |
| Metrics (counters) | Stub (calls/errors) | Partial | Add latency histograms, per-tool breakdown, Prometheus format |
| Async / long-running job pattern | No | Gap | Introduce /jobs with polling & optional callbacks |
| Webhooks / event channel | No (polling UI) | Gap | SSE/WebSocket + optional webhook registration |
| OpenAPI 3.1 emission | Verify | Gap | Ensure 3.1 spec & JSON Schema constructs; remove legacy nullable |
| Streaming response annotation | Not explicit | Gap | Mark stream endpoints (content-type, examples) |
| Security hardening warnings | Basic | Partial | Startup risk audit & banner when insecure |
| Extension namespace hygiene | Not enforced | Gap | Reserve project prefix (e.g., x-mcpo-*) |
| Tool output validation | Minimal | Gap | JSON Schema validator per endpoint |
| E2E contract tests (schema) | Limited | Gap | Add spectral / schema roundtrip tests |

## 4. Phased Upgrade Roadmap
### Phase 1 (Stabilization & Compliance) – Low risk
- OpenAPI 3.1 spec output + JSON Schema fidelity
- MCP-Protocol-Version negotiation & rejection on mismatch
- Enhanced structured output (derive + validate outputSchema)
- Security startup audit (warn/no run without auth unless --allow-insecure explicitly)
- Metrics expansion (per-tool counters; basic latency buckets in-memory)

### Phase 2 (Security & Interaction)
- OAuth 2.1 resource server integration (resource metadata endpoint, introspection client, resource indicators)
- Elicitation framework (suspend token + resume endpoint)
- Resource link parsing & enrichment (hyperlink in docs; typed objects in responses)
- Title & description enrichment (metadata translation layer)

### Phase 3 (Advanced Orchestration)
- Async job abstraction (/jobs POST -> job id, /jobs/{id} status, cancellation)
- Streaming & incremental output annotation; SSE channel for tool progress
- Webhook subscription registry (optional signed callbacks)
- Lifecycle state machine (states: created -> initialized -> active -> suspended -> retired)
- Prometheus /metrics exporter; optional OTEL hooks

### Phase 4 (Enterprise & Ecosystem)
- Multi-tenant server variables & env server blocks in spec
- Policy/RBAC layer (role = admin|invoke)
- Signed audit log trail (hash chain / external sink)
- Schema evolution versioning (tool schema digest & compatibility matrix)

## 5. Design Notes (Key Features)
### 5.1 Structured Output Validation
- Extract tool output descriptors from MCP server handshake (extend existing tool discovery)
- Convert to JSON Schema 2020-12 nodes (mapping union constructs, constraints)
- Attach to OpenAPI responses ($ref components)
- Validate runtime output (fastjsonschema or jsonschema library) behind feature flag (--validate-output)

### 5.2 MCP-Protocol-Version Handling
- Track server-supported range (e.g., 2025.06 … 2025.08)
- Reject requests missing header with 426 Upgrade (custom) + envelope code protocol_version_required
- Provide /_meta/protocol endpoint summarizing negotiated version

### 5.3 OAuth 2.1 Resource Server
- New config block: oauth: { issuer, introspection_url, audience, resources: [...] }
- On request: validate Bearer token (introspection or JWKS) & resource indicator match
- Cache introspection results w/ TTL + revocation check pipeline

### 5.4 Elicitation Flow
- Tool returns elicitation envelope: { ok:false, needs:{ schema, reason }, resumeToken }
- Client responds via POST /{server}/{tool}/resume { resumeToken, data } (mapped to underlying continuation call)
- Timeout + expiry semantics (configurable)

### 5.5 Resource Links
- Detect structured fields like { type:"resource_link", id, resourceType }
- Annotate OpenAPI oneOf with resourceLink schema
- UI/doc hyperlink to follow-on invocation templates

### 5.6 Async Jobs
- If tool declares longRunning: true OR estimatedDuration > threshold -> route to job manager
- Persist minimal job state (id, status, progress, result/error)
- Provide cancellation and purge endpoints

### 5.7 Streaming
- Support chunked transfer / SSE for incremental content events { event:"delta", data:{...} }
- Document via OpenAPI: text/event-stream content type

### 5.8 Metrics Expansion
- Per-tool counters: calls_total, errors_total{code}
- Latency summary (min/max/avg/p95) kept in fixed-size window (ring buffer)
- Optional /_meta/metrics?format=prom for Prometheus exposition

### 5.9 Lifecycle Management
- Add endpoints: POST /_meta/servers/{srv}/suspend, POST /_meta/servers/{srv}/activate, DELETE /_meta/servers/{srv} (retire)
- State machine guard rails (cannot activate retired)

### 5.10 OpenAPI 3.1 Emission
- Set openapi: 3.1.0
- Use type: ["string", "null"] instead of nullable
- Reuse JSON Schema vocabulary keywords (if/then/else, dependentSchemas) where available from MCP tool metadata

## 6. Security Impact & Mitigations
| Feature | New Risk | Mitigation |
|---------|----------|-----------|
| OAuth integration | Token validation latency | Caching + async introspection pool |
| Elicitation | Sensitive data capture risk | Block secret-like key patterns; redact logs |
| Streaming | Response injection | Strict content-type & delimit frames; sanitize text |
| Webhooks | SSRF / callback abuse | Require verified HTTPS + HMAC signature secret |
| Resource links | Enumeration risk | Scope link exposure to authorized caller |
| Async jobs | Persistence leakage | TTL cleanup + size quotas |

## 7. Migration & Compatibility
- All new features behind additive config flags (default off).
- Provide deprecation notices (warning logs) before enforcing mandatory version headers if absent.
- Grace period for structured output validation (warn mode -> enforce mode).

## 8. Testing Strategy Additions
| Area | Test Type |
|------|-----------|
| Output schema validation | Golden schema + invalid output regression |
| OAuth resource indicators | Token w/ wrong audience -> 403 |
| Elicitation flow | Missing param -> elicitation -> resume success |
| Streaming | SSE stream ordering & finalization token |
| Async jobs | Status transitions + cancellation race |
| Metrics | Per-tool counter increments & latency bucket sanity |
| Protocol version | Missing / mismatched header responses |

## 9. Open Questions
1. Should elicitation resume be idempotent or consume token? (Recommend single-use.)
2. Minimum viable persistence layer for jobs (in-memory vs pluggable backend)?
3. Will we expose partial reasoning chains (privacy implications)?
4. Do we adopt OpenTelemetry immediately or after Prometheus baseline?

## 10. Quick Implementation Checklist
- [ ] Emit OpenAPI 3.1 document & validate via spectral in CI
- [ ] Add protocol version negotiation & rejection path
- [ ] Derive + validate output schemas (warn mode)
- [ ] Expand metrics (per-tool) + Prometheus draft
- [ ] OAuth 2.1 resource server skeleton (config + validator)
- [ ] Elicitation envelope + resume endpoint
- [ ] Resource link detection & doc enrichment
- [ ] Async jobs infrastructure & spec pattern
- [ ] SSE streaming channel + documentation
- [ ] Lifecycle state machine & extra endpoints
- [ ] Security hardening audit log & startup warnings

## 11. Adoption Guidance
Implement Phase 1 wholly before starting OAuth & elicitation (they introduce the most architectural surface). Avoid parallelizing Phase 2 security changes with streaming job control to minimize concurrency risk.

## 12. References
- MCP Protocol Release Notes (Jun–Aug 2025)
- OpenAPI Specification v3.1.0
- RFC 8707 (Resource Indicators for OAuth 2.0)
- JSON Schema 2020-12 Core & Validation

---
_This document is a living roadmap. Update alongside CHANGELOG entries when phases reach completion._
