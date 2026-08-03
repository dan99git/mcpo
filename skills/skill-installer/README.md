# skill-installer

MCPO-native skill for listing installed CLI and skill packages, planning pinned isolated Python and npm CLI installs, inspecting skill archives, and linting TOOL.yaml proposal archives without executing them. It requires explicit confirmation before mutations or npm lifecycle scripts and never auto-enables installed skills. Installed CLIs and valid tool proposals are not automatically exposed as MCP tools.

Python CLI installs accept binary wheels only. Source-distribution build hooks are not executed.
