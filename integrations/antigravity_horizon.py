"""Sandboxed Antigravity SDK executor for Horizon, opt-in only.

The Maestro owns orchestration, persistence and tool execution. This process
produces one proposed JSON action; it NEVER executes write/shell tools itself.
No credentials are printed, and the SDK lives outside the production venv.
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
ALLOWED_ROOT = Path("/var/lib/horizon-worker/workspace")
BINARY_NAME = "localharness"
MAX_INPUT = 24000


def doctor() -> dict:
    """Offline dependency discovery. No model request and no secret content."""
    try:
        version = importlib.metadata.version("google-antigravity")
        from google.antigravity import Agent, LocalAgentConfig  # noqa: F401
        runtime = (Path(sys.prefix) / "lib" /
                   f"python{sys.version_info.major}.{sys.version_info.minor}" /
                   "site-packages/google/antigravity/bin" / BINARY_NAME)
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
            "registered_roles": len(role_names),
            "enabled": os.getenv("HORIZON_ANTIGRAVITY_ENABLE") == "1",
            "model_request_sent": False}


def validate_request(payload: dict) -> tuple[str, str, Path]:
    if not isinstance(payload, dict):
        raise ValueError("Request must be an object")
    role, prompt, workspace = (payload.get("role"), payload.get("prompt"),
                               payload.get("workspace"))
    if not isinstance(role, str) or role not in {
        p.name for p in AGENTS.iterdir()
        if p.is_dir() and (p / "agent.json").is_file()
    }:
        raise ValueError("Unregistered specialist")
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= MAX_INPUT:
        raise ValueError("Invalid prompt size")
    if not isinstance(workspace, str) or not workspace:
        raise ValueError("Missing workspace")
    root = Path(workspace).resolve(strict=True)
    allowed = ALLOWED_ROOT.resolve(strict=True)
    if not root.is_dir() or not root.is_relative_to(allowed):
        raise ValueError("Workspace is outside the authorized directory")
    return role, prompt, root


async def propose_action(payload: dict) -> str:
    """Return one model-proposed action, not an executed action."""
    if os.getenv("HORIZON_ANTIGRAVITY_ENABLE") != "1":
        raise RuntimeError("Antigravity is disabled")
    if os.geteuid() == 0:
        raise RuntimeError("Antigravity must run as an unprivileged user")
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("Gemini credential not configured")
    role, prompt, root = validate_request(payload)

    from google.antigravity import Agent, LocalAgentConfig, types
    from google.antigravity.hooks.policy import allow, deny

    instructions = (AGENTS / role / "instructions.md").read_text(encoding="utf-8")
    safe_rules = (AGENTS / "skills/antigravity-harness/SKILL.md").read_text(encoding="utf-8")
    app_root = root / ".horizon-antigravity"
    app_root.mkdir(mode=0o700, exist_ok=True)
    config = LocalAgentConfig(
        workspaces=[str(root)],
        app_data_dir=str(app_root / "app"),
        save_dir=str(app_root / "sessions"),
        budget_config=types.BudgetConfig(max_model_calls=2, max_tool_calls=5,
                                         max_output_tokens=2200),
        capabilities=types.CapabilitiesConfig(enable_subagents=False),
        system_instructions=(instructions + "\n\n" + safe_rules +
            "\n\nONE ACTION ONLY. Return exactly JSON with action, args, summary. "
            "Do NOT execute write, shell, MCP or other side-effecting tools. "
            "The Horizon Maestro validates and executes approved actions. "
            "Only view_file and list_directory tools may be invoked in this "
            "read-only SDK session. Never read environment files or secrets."),
        policies=[deny("*"), allow("view_file"), allow("list_directory")],
    )
    original = Path.cwd()
    try:
        os.chdir(root)
        async with Agent(config) as agent:
            result = await agent.chat(prompt)
            return await result.text()
    finally:
        os.chdir(original)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--gateway", action="store_true")
    args = parser.parse_args()
    if args.doctor:
        print(json.dumps(doctor(), sort_keys=True))
        return 0
    if not args.gateway:
        parser.error("Use --doctor or --gateway (JSON request via stdin)")
    try:
        raw = sys.stdin.read(MAX_INPUT + 3000)
        if len(raw) > MAX_INPUT + 2000:
            raise ValueError("Input too long")
        result = asyncio.run(propose_action(json.loads(raw)))
        print(json.dumps({"status": "proposed", "proposal": result}))
        return 0
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__}))
        return 2
    except Exception as exc:
        # SDK exceptions may contain URLs, server responses or credentials.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
