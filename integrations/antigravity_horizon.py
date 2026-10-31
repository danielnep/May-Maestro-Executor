"""Opt-in Antigravity SDK launcher for Horizon roles.

Runs in the isolated google-antigravity virtualenv. No worker or secrets
are changed by import or doctor. A model request requires an operator-set
HORIZON_ANTIGRAVITY_ENABLE=1 and an existing Gemini key in the environment.
Read-only tool policy is enforced even on execution.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
AGENTS = REPO / "agents"
BINARY_NAME = "localharness"


def doctor() -> dict:
    """Offline-only dependency check, with no secret values or model calls."""
    try:
        version = importlib.metadata.version("google-antigravity")
        from google.antigravity import Agent, LocalAgentConfig  # noqa: F401
        from google.antigravity.connections.local import LocalConnectionStrategy
        runtime = (Path(sys.prefix) / "lib" / ("python" + f"{sys.version_info.major}.{sys.version_info.minor}")
                   / "site-packages" / "google" / "antigravity" / "bin" / BINARY_NAME)
        binary_present = runtime.is_file()
        status = "installed" if binary_present else "binary_missing"
    except (ImportError, importlib.metadata.PackageNotFoundError):
        version, binary_present, status = None, False, "sdk_missing"
    role_names = sorted(
        p.name for p in AGENTS.iterdir()
        if p.is_dir() and (p / "agent.json").is_file()
    ) if AGENTS.is_dir() else []
    return {"status": status, "sdk_version": version,
            "runtime_binary_present": binary_present,
            "registered_roles": len(role_names), "enabled": os.getenv("HORIZON_ANTIGRAVITY_ENABLE") == "1",
            "model_request_sent": False}


async def run_read_only(role: str, prompt: str, workspace: str) -> dict:
    if os.getenv("HORIZON_ANTIGRAVITY_ENABLE") != "1":
        raise RuntimeError("Antigravity disabled. Enable explicitly for an approved task.")
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY missing; no requests sent.")
    if role not in {
        p.name for p in AGENTS.iterdir() if p.is_dir() and (p / "agent.json").is_file()
    }:
        raise ValueError("Unknown Horizon role")
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Workspace must be a directory")
    if os.geteuid() == 0:
        raise RuntimeError("Run as an unprivileged Horizon worker, never as root")
    allowed = Path("/var/lib/horizon-worker/workspace").resolve(strict=True)
    if not root.is_relative_to(allowed):
        raise ValueError("Workspace must remain inside the Horizon worker directory")
    from google.antigravity import Agent, LocalAgentConfig
    from google.antigravity.hooks.policy import allow, deny

    instructions = (AGENTS / role / "instructions.md").read_text(encoding="utf-8")
    safe_rules = (AGENTS / "skills" / "antigravity-harness" / "SKILL.md").read_text(encoding="utf-8")
    config = LocalAgentConfig(
        system_instructions=(instructions + "\n\n" + safe_rules +
            "\n\nREAD-ONLY REVIEW ONLY. Do not write files, run shell, "
            "invoke risky external tools or access secrets."),
        policies=[deny("*"), allow("view_file"), allow("list_directory")],
    )
    original = Path.cwd()
    try:
        os.chdir(root)
        async with Agent(config) as agent:
            response = await agent.chat(prompt)
            answer = await response.text()
        return {"role": role, "mode": "read_only", "result": answer, "status": "received"}
    finally:
        os.chdir(original)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--agent", default="may-maestro")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--prompt", default="")
    args = parser.parse_args()
    if args.doctor:
        print(json.dumps(doctor(), sort_keys=True))
        return 0
    if not args.execute or not args.prompt or not args.workspace:
        parser.error("Use --doctor or --execute with --workspace and --prompt.")
    try:
        print(json.dumps(asyncio.run(run_read_only(args.agent, args.prompt, args.workspace)),
                         ensure_ascii=False))
        return 0
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc), "model_request_sent": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
