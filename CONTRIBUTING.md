# Contributing to Sentia

## Local setup

Sentia requires Node.js 22+, pnpm 10, Python 3.12+, uv, and VS Code stable.
Native voice capture and playback additionally require macOS and the Xcode
Command Line Tools.

```bash
corepack enable
corepack prepare pnpm@10.15.1 --activate
pnpm install --frozen-lockfile
uv sync --frozen
pnpm verify
```

Open the repository in VS Code and use the **Run Sentia Extension** launch
configuration to test the editor experience.

## Pull requests

- Keep changes focused and explain the user-visible outcome.
- Add or update tests for behavioral changes.
- Run `pnpm verify` before requesting review.
- Do not commit API keys, `.env` files, local databases, generated bundles, or
  native binaries.
- Preserve Sentia's read-only defaults and explicit approval boundaries.

See [README.md](README.md) for the architecture and
[docs/PRODUCT_CONTRACT.md](docs/PRODUCT_CONTRACT.md) for product invariants.
