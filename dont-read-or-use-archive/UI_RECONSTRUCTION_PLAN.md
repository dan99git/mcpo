# MCPO UI Reconstruction Plan (Simplified)

Status: Draft (simplified per request)
Scope: Rebuild the original single-file UI (as represented best by recovered v3) without introducing new modular structures or additional abstraction layers. Goal is faithful restoration, not redesign.

---
## 1. Objective
Convert the recovered monolithic inline prototype (v3) into a modular, testable, accessible, and maintainable UI served from `static/ui/` using existing backend meta endpoints.

## 2. Current Assets (Present in Repo)
```
static/ui/index.html (minimal existing)
static/ui/index-recovered-latest*.html (forensic artifacts)
static/ui/css/{base.css,components.css,layout.css}
static/ui/js/{config.js,init.js,logs.js,navigation.js,servers.js,state.js,theming.js,toast.js,ui_counts.js}
static/ui/docs/mcp-guide.html
static/ui/logos/*
```
Backend endpoints already implemented (confirmed via grep):
```
GET  /_meta/servers
GET  /_meta/servers/{server_name}/tools
POST /_meta/servers/{server_name}/enable
POST /_meta/servers/{server_name}/disable
POST /_meta/servers/{server_name}/tools/{tool_name}/enable
POST /_meta/servers/{server_name}/tools/{tool_name}/disable
POST /_meta/servers          (add server) *config mode
DELETE /_meta/servers/{server_name}
GET  /_meta/logs
GET  /_meta/config/content
GET  /_meta/requirements/content
POST /_meta/requirements/save
POST /_meta/config/save (if implemented; verify or add)
```

## 3. Recovered Variants Summary
| Variant | Integrity | Usefulness | Issues |
|---------|-----------|-----------|--------|
| v1 (index-recovered-latest.html) | Corrupted (duplicate docs, prose) | Low | Broken DOM, ellipses, missing anchors |
| v2 (index-recovered-latest-v2.html) | Partial | Medium | Missing IDs, raw JSON fragment, stub JS |
| v3 (index-recovered-latest-v3.html) | Cohesive | High | Inline monolith, no error UX, no a11y, no sanitization |

v3 is selected baseline specification.

## 4. Restoration Targets (Single-File Focus)
We will reconstruct a single `index.html` (or adopt `index-recovered-latest-v3.html` renamed) containing:
1. Sidebar (logo, nav items: MCP Guide, MCP Tools, Logs, Configuration, Documentation)
2. Status counters (server count, tool count, uptime, version)
3. Theme toggle with persistence (`localStorage: mcpo-theme`)
4. Tools page: card list of servers, expandable tools list, docs link, enable/disable toggles, >40 tools warning
5. Logs page: live polling view
6. Configuration page: `mcpo.json` editor + requirements editor with save/validate/reset
7. Documentation page: static quick-start content
8. External documentation loader page for `mcp-guide.html`
9. Polling (servers every 5s, logs every 2s)
10. LocalStorage persistence of current page (`mcpo-current-page`)

All logic remains inline for now (simplified) except for existing auxiliary JS files already in `static/ui/js/` which we will not refactor further.

## 5. Minimal Additions (Allowable)
- A sentinel test to detect accidental truncation or loss of required IDs.
- Optional comment banner at top of `index.html` describing recovery provenance.

No new abstraction modules or file splits beyond what already exists in the repository.

## 6. Data Contracts (UI Assumptions)
Server List (`GET /_meta/servers`)
```
{
  "ok": true,
  "servers": [
    {"name": "time", "enabled": true, "connected": true, "type": "stdio" }
  ]
}
```
Server Tools (`GET /_meta/servers/{name}/tools`)
```
{ "ok": true, "tools": [ {"name": "get_current_time", "enabled": true} ] }
```
Logs (`GET /_meta/logs`)
```
{ "ok": true, "logs": [ {"timestamp": "2025-08-20T10:00:00Z", "level": "INFO", "message": "..."} ] }
```
Config Content (`GET /_meta/config/content`) → `{ "ok": true, "content": "{\n  ...json...\n}" }`
Config Save (`POST /_meta/config/save`) → `{ "ok": true }`
Requirements Content (`GET /_meta/requirements/content`) → `{ "ok": true, "content": "package==1.0" }`
Requirements Save (`POST /_meta/requirements/save`) → `{ "ok": true }`
Enable/Disable → `{ "ok": true }` or `{ "ok": false, "error": "..." }`

## 7. Sentinel Integrity Test (Planned)
`tests/test_ui_integrity.py` assertions:
- `static/ui/index.html` contains: `id="server-config-editor"`, `id="logs-content"`, `id="tools-warning"`, `data-page="tools"` nav item, `<script src="js/api.js">` (once modularized).
- No duplicate `<!DOCTYPE html>`.
- Fails if size < threshold (e.g. 5 KB) to catch truncation.

## 8. Security & Hardening Tasks
| Task | Description | Priority |
|------|-------------|----------|
| Sanitize dynamic text | Use textContent for tool/server names | P0 |
| Enforce same-origin fetch | Reject absolute external URLs | P1 |
| Rate-limit toggle spam (client side debounce) | Avoid hammering backend | P2 |
| Visibility-based polling | Pause when tab hidden | P2 |
| Replace alerts | Route through toast UI | P1 |
| Add CSP meta (optional) | Inline script reduction prerequisite | P3 |

## 9. Accessibility Tasks
- Add `role="switch"` + `aria-checked` to toggles.
- Add `aria-expanded` on server expandable rows.
- Provide skip-to-content link.
- Ensure color contrast meets WCAG (check disabled tool-tag contrast).

## 10. Execution Steps (Simplified)
1. Copy `static/index-recovered-latest-v3.html` to `static/ui/index.html` (backup existing minimal file if needed).
2. Remove any obsolete/redundant recovered variants (`index-recovered-latest*.html`) after verification commit.
3. Insert provenance comment & integrity markers (IDs already present).
4. Add sentinel test ensuring core IDs & strings exist and file size threshold met.
5. Manual pass: verify each endpoint call returns expected shape; adjust inline JS only if mismatch (no structural refactor).
6. Add warning comment discouraging over-modularization until baseline stable.

## 11. Acceptance Criteria
- Restored single-file UI (`static/ui/index.html`) contains all functional sections listed in Restoration Targets.
- Server & tool toggles work against existing endpoints.
- Logs update at configured interval.
- Config and requirements editors load & save.
- Theme & page persistence survive reload.
- Sentinel test passes (IDs present, size >= threshold, single DOCTYPE).

## 12. Risks & Mitigations
| Risk | Mitigation |
|------|-----------|
| Future accidental overwrite/truncation | Sentinel test + provenance comment |
| Endpoint shape drift | Minimal inline guard (length / key presence) before rendering |
| Over-polling CPU/network | Optionally raise intervals later (out of scope now) |
| XSS via doc loader | Limit to known relative path; avoid inserting unsanitized external HTML beyond `mcp-guide.html` |

## 13. Immediate Next Actions
1. Promote v3 recovered file to active `index.html` (after backup).
2. Insert provenance & integrity comment banner.
3. Implement sentinel test.
4. Quick manual run-through to confirm each endpoint call path.

Generated automatically (simplified). Edit as needed before execution.
