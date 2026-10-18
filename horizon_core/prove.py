"""Live, synthetic acceptance checks. No client data and no agent CLI."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from .gateway import ModelGateway, GatewayError
from .github_state import GitHubState
from .railway import RailwayBroker
from .registry import AGENT_IDS, load_agent
from .state import State
from .tools import Tools


def providers(state, persist):
    gateway = ModelGateway(state=state)
    results = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'README.md').write_text('Horizon live acceptance fixture: public synthetic data only.\n')
        for agent_id in AGENT_IDS:
            agent = load_agent(agent_id)
            task_id = 'live:' + agent_id
            state.enqueue(task_id, agent_id, 'synthetic-live-team', 'Read a real file using an independently generated model action')
            task = state.claim('live-proof', lease_seconds=1800)
            if not task or task['task_id'] != task_id:
                raise RuntimeError('Acceptance queue was not isolated')
            try:
                action = gateway.complete(agent, [{'role': 'user', 'content':
                    'Este é um teste real e isolado do seu contrato. O único arquivo é README.md. '
                    'Sua próxima ação deve ser read_file com args {"path":"README.md"}. '
                    'Em summary diga em português qual critério da sua especialidade você avaliará após a leitura. '
                    'Não conclua a tarefa antes de ler; não delegue neste teste; não invente resultado. Retorne JSON.'}])
                if action['action'] != 'read_file' or action['args'].get('path') != 'README.md':
                    raise RuntimeError('Model did not select the required file tool')
                actual = Tools(root, agent).execute(action['action'], action['args'])
                state.checkpoint(task_id, 'live-proof', {'model_action': action, 'tool_result': actual})
                state.remember('synthetic-live-team', agent_id, 'last_verified_file', actual['path'])
                assert state.recall('synthetic-live-team', agent_id)['last_verified_file'] == 'README.md'
                evidence = {'agent': agent_id, 'model': action['metadata'], 'tool': 'read_file',
                            'memory_recovered': True, 'checkpoint': True,
                            'limitation': 'One real independent decision/tool call; not proof of a complete autonomous delivery.'}
                state.complete(task_id, 'live-proof', evidence)
                persist()
                results.append(evidence)
                print(json.dumps(evidence, ensure_ascii=False), flush=True)
            except Exception as error:
                failure = error.as_dict() if isinstance(error, GatewayError) else {'kind': type(error).__name__}
                state.fail(task_id, 'live-proof', failure, retryable=False)
                persist()
                print(json.dumps({'agent': agent_id, 'failure': failure}), flush=True)
                raise RuntimeError('Live specialist acceptance failed; inspect sanitized evidence') from None
    return {'specialists_proven': len(results), 'calls': gateway.calls, 'results': results}


def railway(state, persist):
    broker = RailwayBroker(state, persist)
    first = broker.ensure()
    second = broker.ensure()
    if first['sandbox_id'] != second['sandbox_id'] or not second.get('reused'):
        raise RuntimeError('Managed sandbox reuse was not confirmed')
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'tests').mkdir()
        (root / 'README.md').write_text('Synthetic Railway acceptance fixture')
        (root / '.env').write_text('SYNTHETIC_ONLY=not-a-real-key')
        (root / 'tests' / 'test_isolation.py').write_text(
            'import os,unittest\nfrom pathlib import Path\n'
            'class Isolation(unittest.TestCase):\n'
            ' def test_private_context_not_mounted(self):\n'
            '  self.assertFalse(Path(".env").exists())\n'
            '  self.assertFalse(Path(".git").exists())\n'
            '  for name in ("RAILWAY_API_TOKEN","GITHUB_TOKEN","GEMINI_API_KEY","DEEPSEEK_API_KEY"):\n'
            '   self.assertFalse(os.getenv(name))\n'
            '  self.assertTrue(Path("README.md").exists())\n')
        check = Tools(root, {'tools': ['run_check']}, sandbox_client=broker).execute('run_check', {'name': 'python-unittest'})
        if not check.get('passed'):
            print(json.dumps(check), flush=True)
            raise RuntimeError('Railway sandbox check failed')
        result = {'sandbox_id': first['sandbox_id'], 'created': first.get('created'),
                  'reused': True, 'isolation_check': check, 'provider_calls': 0}
        state.remember('__runtime__', 'railway', 'live_proof', result)
        persist()
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('target', choices=('providers', 'railway'))
    parser.add_argument('--repo', required=True)
    parser.add_argument('--run-id', type=int, required=True)
    args = parser.parse_args()
    remote = GitHubState(args.repo, branch=f'agent-state/{args.target}-proof-{args.run_id}')
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / 'proof.sqlite3'
        if remote.pull(database):
            raise RuntimeError('Use a fresh run for live acceptance')
        state = State(database)
        persist = lambda: remote.push(database)
        try:
            result = providers(state, persist) if args.target == 'providers' else railway(state, persist)
            print(json.dumps({'acceptance': args.target, 'passed': True, 'evidence_branch': remote.branch, **result}, ensure_ascii=False))
        finally:
            state.close()


if __name__ == '__main__':
    main()
