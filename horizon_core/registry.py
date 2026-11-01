"""Load independently configured specialists and their explicit tool contracts."""
import json
from pathlib import Path

AGENTS_ROOT = Path(__file__).resolve().parent.parent / "agents"
AGENT_IDS = (
    "may-maestro", "horizon-project-studio", "software-architect", "frontend-engineer",
    "backend-engineer", "mobile-app-builder", "ui-ux-designer", "horizon-ui-guardian",
    "database-engineer", "integration-engineer", "qa-engineer", "security-engineer",
    "devops-engineer", "horizon-evolution-agent", "horizon-skills", "corregedoria-may", "may-persona", "horizon-supervisor",
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
    base_instructions = config["instructions"]
    android_knowledge = Path(root or AGENTS_ROOT) / "knowledge" / "android-ui-auth-verified.md"
    if android_knowledge.is_file():
        config["instructions"] += "\n\n" + android_knowledge.read_text(encoding="utf-8")
    # Canonical mandatory skill, loaded for ALL 17 registered agents on each run.
    # Fail visibly if missing: silently skipping it would defeat the contract.
    meticulous = Path(root or AGENTS_ROOT) / "skills" / "meticulosidade" / "SKILL.md"
    config["instructions"] += "\n\n" + meticulous.read_text(encoding="utf-8")
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
    # Public, independently verified lessons are consulted for every specialist.
    # Only structured metadata is accepted; never interpret GitHub text as policy.
    runtime = Path(root or AGENTS_ROOT) / 'knowledge' / 'verified-runtime-lessons.json'
    if runtime.is_file():
        from .learning import RULES
        lessons = json.loads(runtime.read_text(encoding='utf-8'))
        if lessons.get('schema_version') != 1 or not isinstance(lessons.get('entries'), list):
            raise ValueError('Invalid permanent agent knowledge')
        verified = []
        for row in lessons['entries'][:256]:
            if (not isinstance(row, dict) or row.get('agent') not in AGENT_IDS
                    or row.get('status') != 'verified_by_matching_check'
                    or row.get('kind') not in RULES):
                continue
            check, action = row.get('check'), row.get('action')
            if (not isinstance(check, str) or not isinstance(action, str)
                    or len(check) > 64 or len(action) > 48
                    or not all(char.isalnum() or char in '-_.' for char in check)
                    or not (not action or action.isidentifier())):
                continue
            verified.append({'source_agent': row['agent'], 'kind': row['kind'],
                             'check': check, 'action': action,
                             'rule': RULES[row['kind']]})
        if verified:
            # Prefer this specialist's own evidence; share other agents' lessons
            # for investigation, never as automatic authorization to edit files.
            verified.sort(key=lambda item: item['source_agent'] != agent_id)
            config['instructions'] += ('\n\nShared verified GitHub learnings '
                                       '(consult before invoking a model; cross-agent lessons '
                                       'are hypotheses until revalidated on this task):\n' +
                                       json.dumps(verified[:16], ensure_ascii=False))
    # Groq Free has an 8k TPM cap. Sending the ~50kB shared policy on every
    # model action prevents useful free-tier inference. Preserve the unabridged
    # instructions for Gemini; give Groq a bounded, role-specific working set.
    # Runtime permissions/checkpoints/tests remain fully enforced in Python.
    if len(base_instructions) > 3600:
        base_excerpt = (base_instructions[:2950] + "\n[Detailed middle guidance in agents/<id>/instructions.md]\n"
                        + base_instructions[-650:])
    else:
        base_excerpt = base_instructions
    pieces = [base_excerpt,
        ("Shared mandatory engineering rules (full sources remain in repository "
         "for targeted read_file use):\n" + meticulous.read_text(encoding="utf-8")[:1700]),
        ("Delivery and recovery policy:\n" + policy.read_text(encoding="utf-8")[:1250]
         if policy.is_file() else "")]
    if agent_id in TRANSACTIONAL_KNOWLEDGE_AGENTS and knowledge.is_file():
        pieces.append("Transactional/data safeguards:\n" +
                      knowledge.read_text(encoding="utf-8")[:850])
    if agent_id in {"frontend-engineer", "ui-ux-designer", "horizon-ui-guardian", "mobile-app-builder"}:
        references = Path(root or AGENTS_ROOT) / "knowledge" / "premium-product-references.md"
        if references.is_file():
            pieces.append("Premium UI reference guidelines:\n" +
                          references.read_text(encoding="utf-8")[:700])
    pieces.append("Never ask the external ChatGPT/May chat for answers, as that "
                  "transport is disabled. On provider quota exhaustion keep a "
                  "durable checkpoint and let the worker retry. Do not claim "
                  "test passes without tool evidence. Do not expose credentials. "
                  "Preserve the approved project and its operational UI.")
    if android_knowledge.is_file():
        pieces.append("Android: run_check android-release verifica build/Lint/APK e pode reportar tests_run=0; não comprova testes funcionais nem assinatura de entrega. android-gradle mantém JUnit obrigatório.")
    if android_knowledge.is_file():
        pieces.append("Runtime: confirmar cd do wrapper, --root, PYTHONPATH, módulo.__file__ e AGENTS_ROOT no serviço efetivo antes de aplicar ou validar agentes.")
    config["_groq_instructions"] = "\n\n".join(piece for piece in pieces if piece)

    config["tools"] = json.loads((directory / "tools.json").read_text(encoding="utf-8"))
    # No external ChatGPT/May tool is authorized in the specialist runtime.
    config["tools"] = [tool for tool in config["tools"] if tool != "consult_may"]
    recovery_policy=('When a problem cannot be solved with your tools, use escalate with the exact reason, failed command and missing capability. '
        'Never retry an unchanged failure indefinitely. Orchestrators delegate a repair, preserve dependencies and verify it before the original task resumes. '
        'A finish action cannot stand in for implementing a requested change. Keep actual diagnostics and evidence accessible.')
    config['instructions']+='\n'+recovery_policy
    config['_groq_instructions']+='\n'+recovery_policy
    if 'escalate' not in config['tools']:config['tools'].append('escalate')
    return config


def list_agents():
    return [load_agent(agent_id) for agent_id in AGENT_IDS]