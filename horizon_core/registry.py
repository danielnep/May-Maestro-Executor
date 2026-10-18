"""Load independently configured specialists and their explicit tool contracts."""
import json
from pathlib import Path

AGENTS_ROOT = Path(__file__).resolve().parent.parent / "agents"
AGENT_IDS = (
    "may-maestro", "horizon-project-studio", "software-architect", "frontend-engineer",
    "backend-engineer", "mobile-app-builder", "ui-ux-designer", "horizon-ui-guardian",
    "database-engineer", "integration-engineer", "qa-engineer", "security-engineer",
    "devops-engineer", "horizon-evolution-agent", "horizon-skills", "corregedoria-may", "may-persona",
)


def load_agent(agent_id, root=None):
    if agent_id not in AGENT_IDS:
        raise ValueError("Unknown specialist: " + str(agent_id))
    directory = Path(root or AGENTS_ROOT) / agent_id
    config = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
    if config.get("id") != agent_id:
        raise ValueError("Specialist identity mismatch")
    config["instructions"] = (directory / "instructions.md").read_text(encoding="utf-8")
    config["tools"] = json.loads((directory / "tools.json").read_text(encoding="utf-8"))
    return config


def list_agents():
    return [load_agent(agent_id) for agent_id in AGENT_IDS]
