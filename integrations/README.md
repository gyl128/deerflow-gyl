# Integrations Registry

This directory keeps agent-center integrations in a center-neutral registry.

Goals:
- Define skills, MCP servers, and model gateways once.
- Render DeerFlow-specific config from manifests.
- Keep future migration to a different agent center cheap.

Layout:
- `registry/skills/*.json`: skill manifests
- `registry/mcp/*.json`: MCP manifests
- `registry/model_gateways/*.json`: model gateway manifests, including qwenpool
- `dist/`: generated outputs for a target center

Current renderer:
- `scripts/render_integrations.py`

Generated DeerFlow assets:
- `integrations/dist/catalog.json`
- `integrations/dist/deerflow/extensions_config.generated.json`
- `integrations/dist/deerflow/models.generated.yaml`

Usage:

```bash
cd /root/deerflow/runtime/next
python3 scripts/render_integrations.py
```

Design notes:
- The registry is intentionally center-neutral. It stores stable IDs, paths, commands, health endpoints, and compatibility metadata.
- DeerFlow-specific details live under `targets.deerflow`.
- qwenpool is tracked as a `model_gateway`, not an MCP server, so it can be mapped into DeerFlow model config now and another center later.
