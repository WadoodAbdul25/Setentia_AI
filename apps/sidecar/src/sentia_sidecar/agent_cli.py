from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sentia_sidecar.agent_runtime import (
    AgentAccess,
    AgentEvent,
    AgentRunRequest,
    create_agent_router,
)
from sentia_sidecar.logging_config import configure_logging
from sentia_sidecar.protocol import AgentProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentia-agent",
        description="Run either supported coding-agent SDK through Sentia's normalized adapter.",
    )
    parser.add_argument("provider", choices=[item.value for item in AgentProvider])
    parser.add_argument("prompt")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--model")
    parser.add_argument(
        "--max-turns",
        type=int,
        help="Claude-only turn cap; Codex does not currently expose this control.",
    )
    parser.add_argument("--max-budget-usd", type=float)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Allow workspace writes. Omit this flag for a read-only run.",
    )
    return parser


def _serialize(event: AgentEvent) -> dict[str, Any]:
    payload = asdict(event)
    payload["provider"] = event.provider.value
    payload["kind"] = event.kind.value
    return payload


async def _main(args: argparse.Namespace) -> int:
    provider = AgentProvider(args.provider)
    request = AgentRunRequest(
        prompt=str(args.prompt),
        workspace_path=Path(args.workspace),
        access=AgentAccess.WORKSPACE_WRITE if args.write else AgentAccess.READ_ONLY,
        model=str(args.model) if args.model else None,
        max_turns=int(args.max_turns) if args.max_turns else None,
        max_budget_usd=float(args.max_budget_usd) if args.max_budget_usd else None,
    )
    router = create_agent_router()
    failed = False
    async for event in router.run(provider, request):
        print(json.dumps(_serialize(event), default=str), flush=True)
        failed = failed or event.kind.value == "run_failed"
    return 1 if failed else 0


def main() -> None:
    configure_logging("INFO")
    args = _parser().parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
