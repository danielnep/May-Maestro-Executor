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
TRANSACTIONAL_KNOWLEDGE_AGENTS = frozenset(('backend-engineer', 'database-engineer',
    'software-architect', 'qa-engineer', 'security-engineer', 'horizon-evolution-agent',
    'horizon-skills', 'corregedoria-may'))


def load_agent(agent_id, root=None):
    if agent_id not in AGENT_IDS:
        raise ValueError("Unknown specialist: " + str(agent_id))
    directory = Path(root or AGENTS_ROOT) / agent_id
    config = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
    if config.get("id") != agent_id:
        raise ValueError("Specialist identity mismatch")
    config["instructions"] = (directory / "instructions.md").read_text(encoding="utf-8")
    knowledge = Path(root or AGENTS_ROOT) / 'knowledge' / 'transactional-integrity.md'
    if agent_id in TRANSACTIONAL_KNOWLEDGE_AGENTS and knowledge.is_file():
        config["instructions"] += '\n\n' + knowledge.read_text(encoding='utf-8')
    policy = Path(root or AGENTS_ROOT) / 'knowledge' / 'delivery-principles.md'
    if policy.is_file():
        config["instructions"] += '\n\n' + policy.read_text(encoding='utf-8')
    if agent_id in {'frontend-engineer', 'ui-ux-designer', 'horizon-ui-guardian', 'mobile-app-builder', 'software-architect'}:
        references = Path(root or AGENTS_ROOT) / 'knowledge' / 'premium-product-references.md'
        if references.is_file():
            config['instructions'] += '\n\n' + references.read_text(encoding='utf-8')
    playbook = Path(root or AGENTS_ROOT) / 'knowledge' / 'verified-error-playbook.json'
    if playbook.is_file():
        rules = json.loads(playbook.read_text(encoding='utf-8'))
        if rules.get('schema_version') != 1 or not isinstance(rules.get('entries'), list):
            raise ValueError('Invalid verified error playbook')
        config["instructions"] += ('\n\nShared incident playbook (versioned, public, sanitized; '
                                   'consult before any model diagnosis, never assume a matching '
                                   'rule proves root cause):\n' +
                                   json.dumps(rules['entries'], ensure_ascii=False))
    config["tools"] = json.loads((directory / "tools.json").read_text(encoding="utf-8"))
    return config


def list_agents():
    return [load_agent(agent_id) for agent_id in AGENT_IDS]
