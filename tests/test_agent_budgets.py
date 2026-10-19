"""Budget, zero-LLM inspection and loop safety regression tests."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from horizon_core.context_budget import compact_history, compact_memory
from horizon_core.state import State
from horizon_core.tools import Tools
from horizon_core.registry import load_agent
from horizon_core.gateway import GatewayError
from horizon_core.worker import Worker


def action(name, args=None):
    return {'action': name, 'args': args or {}, 'summary': 'fixture'}


class Gateway:
    def __init__(self, factory):
        self.factory = factory
        self.calls = 0
    def complete(self, agent, messages):
        self.calls += 1
        return self.factory(json.loads(messages[0]['content']))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git','init',str(self.root)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['git','-C',str(self.root),'switch','-c','fix/task'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        (self.root/'README.md').write_text('fixture\n')
        self.state = State(':memory:')
        self.addCleanup(self.state.close)

    def test_three_repeated_invalid_actions_stop_without_fourth_request(self):
        def bad(context):
            err=GatewayError('invalid',provider='gemini',reason='invalid_action')
            err.shape={'object':True,'exact_keys':False}
            raise err
        gateway=Gateway(bad)
        self.state.enqueue('task','frontend-engineer','scope','Fix action protocol')
        result=Worker(self.state,gateway,self.root).run_one()
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['reason'],'retry_limit_exceeded')
        self.assertEqual(gateway.calls,3)
        self.assertEqual(result['checkpoint']['step'],3)

    def test_three_failed_checks_stop_but_source_edit_resets_counter(self):
        attempts=[]
        def go(ctx):
            n=ctx['completed_steps']
            if n>=3: return action('finish')
            return action('run_check',{'name':'python-unittest'})
        gateway=Gateway(go)
        self.state.enqueue('checks','qa-engineer','scope','Verify unit checks')
        with patch.object(Tools,'run_check',return_value={'check':'python-unittest','passed':False,'tests_run':1,'exit_code':1}):
            result=Worker(self.state,gateway,self.root).run_one()
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['reason'],'retry_limit_exceeded')
        self.assertEqual(result['checkpoint']['failure_counts']['check:python-unittest'],3)
        self.assertEqual(gateway.calls,3)

    def test_identical_write_does_not_reset_failed_check_counter(self):
        source=self.root/'src'/'feature.py'
        source.parent.mkdir()
        source.write_text('VALUE = 1\\n')
        def decide(ctx):
            n=ctx['completed_steps']
            if n == 1:
                return action('write_file',{'path':'src/feature.py','content':'VALUE = 1\\n'})
            return action('finish') if n >= 4 else action('run_check',{'name':'python-unittest'})
        gateway=Gateway(decide)
        self.state.enqueue('noop','frontend-engineer','scope','Avoid fraudulent retry reset',
                           payload={'branch':'fix/task','allowed_paths':['src/**']})
        with patch.object(Tools,'run_check',
                          return_value={'check':'python-unittest','passed':False,'tests_run':1,'exit_code':1}):
            result=Worker(self.state,gateway,self.root).run_one()
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error']['reason'],'retry_limit_exceeded')
        self.assertEqual(result['checkpoint']['failure_counts']['check:python-unittest'],3)
        self.assertEqual(gateway.calls,4)

    def test_genuine_file_change_allows_fresh_three_check_budget(self):
        source=self.root/'src'/'feature.py'
        source.parent.mkdir()
        source.write_text('VALUE = 1\\n')
        def decide(ctx):
            n=ctx['completed_steps']
            if n == 1:
                return action('write_file',{'path':'src/feature.py','content':'VALUE = 2\\n'})
            return action('finish') if n >= 4 else action('run_check',{'name':'python-unittest'})
        gateway=Gateway(decide)
        self.state.enqueue('changed','frontend-engineer','scope','Allow correction to be tested',
                           payload={'branch':'fix/task','allowed_paths':['src/**']})
        with patch.object(Tools,'run_check',
                          return_value={'check':'python-unittest','passed':False,'tests_run':1,'exit_code':1}):
            result=Worker(self.state,gateway,self.root).run_one()
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(gateway.calls,5)

    def test_context_limited_but_checkpoint_keeps_full_tool_evidence(self):
        long='data:'+'x'*20000
        latest=[{'action':'read_file','summary':'inspect','outcome':{'path':'src/ui.js','content':long}} for _ in range(12)]
        compact=compact_history(latest)
        self.assertEqual(len(compact),4)
        self.assertTrue(len(json.dumps(compact))<16000)
        self.assertIn('truncated',str(compact))
        self.assertEqual(latest[0]['outcome']['content'],long)
        m={str(x):'y'*2500 for x in range(14)}
        reduced=compact_memory(m)
        self.assertEqual(len(reduced),8)
        self.assertTrue(all(v.get('truncated') for v in reduced.values()))

    def test_surgical_search_reads_small_excerpts_and_keeps_location(self):
        folder=self.root/'src'
        folder.mkdir()
        (folder/'backend.js').write_text('line\n'*700+'function processAlert() {}\n'+'last\n'*700)
        tools=Tools(self.root,load_agent('frontend-engineer'))
        response=tools.execute('read_file',{'path':'src/backend.js','search':'processAlert'})
        self.assertEqual(response['total_matches'],1)
        self.assertEqual(response['matches'][0]['start_line'],698)
        self.assertTrue(len(json.dumps(response))<1500)
        listed=tools.execute('list_files',{'filter':'backend','limit':5})
        self.assertEqual(listed['files'],['src/backend.js'])
        self.assertEqual(listed['limit'],5)
        with self.assertRaisesRegex(ValueError,'line range'):
            tools.execute('read_file',{'path':'src/backend.js','search':'processAlert','start_line':1,'end_line':2})


if __name__=='__main__':
    unittest.main()
