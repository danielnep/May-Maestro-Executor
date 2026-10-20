"""Independent specialist direct-chat command (no GitHub Actions queue).

Examples:
  python -m horizon_core.chat_direct_cli --agent horizon-skills --task-id proof-1 --checkin
  python -m horizon_core.chat_direct_cli --agent qa-engineer --task-id qa-123 --public-approved --question "What public test should run first?"
  python -m horizon_core.chat_direct_cli --agent qa-engineer --task-id qa-123 --read

The environment containing the process must be authorized for the same KERNEL
project as may-chatgpt-72h and must expose KERNEL_API_KEY as a secure secret.
Never pass session cookies, CDP URLs or API keys on the command line.
"""
import argparse
import json
import os

from .direct_chat import execute, execute_local
from .may_consultation import _public_question, ticket
from .registry import load_agent

CHECKIN_TEXT = ("Horizon specialist availability check. Please answer with a "
                "short acknowledgement. This message proves only chat connectivity.")


def main(argv=None, *, transport=execute):
    parser = argparse.ArgumentParser(description="Horizon specialist direct ChatGPT transport")
    parser.add_argument("--agent", required=True, help="One of the registered specialist IDs")
    parser.add_argument("--task-id", required=True, help="Public or synthetic task identifier")
    parser.add_argument("--round", type=int, default=0, help="Nonnegative consultation round")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--checkin", action="store_true")
    mode.add_argument("--question", help="Public technical question, 12-1400 characters")
    mode.add_argument("--read", action="store_true")
    parser.add_argument("--local-browser", action="store_true",
                        help="Agent already runs inside the authenticated KERNEL browser VM")
    parser.add_argument("--public-approved", action="store_true",
                        help="The owner has authorized the question to enter external chat")
    args = parser.parse_args(argv)
    agent = load_agent(args.agent)  # Check both identity and real specialist instructions.
    key = ticket(args.task_id, agent["id"], args.round)
    reason = "checkin" if args.checkin else "specialist_question"
    mode_name = "read" if args.read else "send"
    if args.checkin:
        question = CHECKIN_TEXT
    elif args.read:
        question = ""
    else:
        task = {"payload": {"may_public_context_allowed": args.public_approved}}
        question = _public_question(task, args.question, "specialist_question")
        if question is None:
            parser.error("Question is not authorized public, or failed privacy validation")
    selected_transport = execute_local if args.local_browser and transport is execute else transport
    result = selected_transport(agent["id"], key, question=question, reason=reason,
                                mode=mode_name)
    # Never log the response body as an authenticated browser trace; only
    # success/status and the receipt are written to the CLI output by default.
    print(json.dumps({"agent": agent["id"], "ticket": key,
                      "status": result.get("status"), "chat": "Horizon | Central dos Agentes"},
                     ensure_ascii=False))
    return 0 if result.get("status") in ("sent", "answered", "pending_interleaved",
                                       "send_unconfirmed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
