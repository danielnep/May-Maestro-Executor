import json
import unittest
from horizon_core.groq_context import fit
from horizon_core.groq_minute_budget import estimated_input_tokens


class ContextFit(unittest.TestCase):
    def test_small_messages_are_preserved(self):
        messages=[{'role':'system','content':'Role and tools'},{'role':'user','content':'Inspect the bug'}]
        self.assertEqual(fit(messages),messages)

    def test_oversized_failure_history_fits_without_changing_permissions(self):
        scope={'repository':'danielnep/example','branch':'fix/example','allowed_paths':['src/**'],'recovery_for':'original-task'}
        ctx={'objective':'Fix exact assertion failure','scope':scope,'history':[{'action':'run_check','outcome':{'output':'error details '*10000}}]*4}
        messages=[{'role':'system','content':'Specialist ID: qa-engineer\n'+('role instructions '*1200)+'\nAllowed actions: ["read_file","run_check"]'},
                  {'role':'user','content':json.dumps(ctx)}]
        out=fit(messages)
        self.assertLessEqual(estimated_input_tokens(out),5200)
        self.assertEqual(json.loads(out[1]['content'])['scope'],scope)
        self.assertIn('Allowed actions: ["read_file","run_check"]',out[0]['content'])
        self.assertIn('qa-engineer',out[0]['content'])
        self.assertEqual(len(json.loads(messages[1]['content'])['history']),4)

    def test_latest_failed_command_remains_available(self):
        ctx={'objective':'Fix test','scope':{},'history':[{'action':'old','outcome':'old '*5000},
            {'action':'run_check','outcome':{'failed_test':'css-hygiene.test.mjs','output':'assertion failed '*10000}}]}
        out=fit([{'role':'system','content':'qa role'},{'role':'user','content':json.dumps(ctx)}])
        self.assertIn('run_check',out[1]['content']);self.assertIn('css-hygiene.test.mjs',out[1]['content'])


if __name__=='__main__':unittest.main()
