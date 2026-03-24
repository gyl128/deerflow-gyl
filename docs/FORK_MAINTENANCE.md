# DeerFlow Fork Maintenance

This repository is a private maintenance fork built from the upstream DeerFlow project.

## Remotes

- `upstream`: official DeerFlow repository
- `origin`: private repository for this fork

## Patch classes

- `bugfix`: fixes to DeerFlow behavior that may be suitable for upstream contribution
- `integration`: local integrations such as Telegram proxy handling and external MCP services
- `policy`: local risk-bearing behavior changes such as expanded filesystem access

## Local-only integrations

- Video distiller is an external project and is not part of this repository.
- Keep external project code outside this repo and only maintain the MCP integration points here.

## Sensitive configuration

Do not commit real secrets or machine-local runtime configuration.

Keep these out of version control:

- `.env`
- `config.yaml`
- `extensions_config.json`
- `frontend/.env.local`

Restore them from local backups when setting up a machine.

## Upgrade workflow

1. Fetch `upstream/main`.
2. Check whether upstream already fixed any local `bugfix` patches.
3. Reapply remaining patches in this order:
   - `bugfix`
   - `integration`
   - `policy`
4. Re-run local validation for Web, Gateway, LangGraph, Feishu, Telegram, MCP, and sandbox behavior.

## High-risk local policy

This fork currently includes a local sandbox policy patch that permits host filesystem access beyond `/mnt/user-data`.

Treat that patch as local-only and review it separately during every upgrade.
