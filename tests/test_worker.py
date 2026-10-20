"""Integration proofs use local fixtures and explicit fake model responses, no API."""
import json
import hashlib
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from horizon_core.publish import publish
from horizon_core.gateway import GatewayError
from horizon_core.registry import AGENT_IDS
from horizon_core.state import State
from horizon_core.worker import PersistenceError, Worker


def action(name, args=None, summary='Fixture model decision'):
    return {'action': name, 'args': args or {}, 'summary': summary,
            'metadata': {'model': 'fake-model', 'test_only': True}}


class FakeGateway:
    def __init__(self, decide):
        self.decide, self.calls = decide, []

    def complete(self, agent, messages):
        context = json.loads(messages[0]['content'])
        self.calls.append((agent['id'], context))
        return self.decide(agent, context)


class WorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'worktree'
        self.root.mkdir()
        (self.root / 'README.md').write_text('fixture repository documentation', encoding='utf-8')
        subprocess.run(['git', 'init', str(self.root)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'switch', '-c', 'feat/test'], check=True, capture_output=True)
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.state = State(self.path)
        self._fixture_visual = patch('horizon_core.worker.requires_visual_review', return_value=False)
        self._fixture_visual.start()
        self.addCleanup(self._fixture_visual.stop)

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_vision_provider_outage_does_not_abort_other_ui_qa_steps(self):
        self.state.enqueue('vision-outage', 'qa-engineer', 'fixture',
                           'Audit a captured screenshot and report its unavailable provider')
        def decide(agent, context):
            if not context['history']:
                return action('inspect_image', {'index': 0, 'question': 'Inspect image layout'})
            evidence = context['history'][-1]['outcome']
            self.assertEqual(evidence['tool_error'], 'VisionUnavailable')
            self.assertFalse(evidence['actual_image_analyzed'])
            self.assertEqual(evidence['status'], 429)
            self.assertEqual(evidence['reason'], 'vision_unavailable')
            return action('finish', summary='Vision unavailable; DOM QA remains required')
        gateway = FakeGateway(decide)
        gateway.inspect_image = lambda *args: (_ for _ in ()).throw(
            GatewayError('Vision quota exhausted', provider='gemini',
                         reason='vision_unavailable', retryable=True, status=429))
        with patch('horizon_core.tools.Tools.evidence_image',
                   return_value=b'PNG evidence provided by browser proof'):
            result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['checkpoint']['history'][0]['action'], 'inspect_image')
        self.assertEqual(result['checkpoint']['history'][0]['outcome']['tool_error'], 'VisionUnavailable')
        self.assertEqual(len(gateway.calls), 2)

    def test_invalid_model_protocol_can_correct_itself_without_replaying_tool(self):
        def decide(agent, context):
            if not context['history']:
                return action('read_file', {'path': 'README.md'})
            if len(context['history']) == 1:
                error = GatewayError('invalid response', retryable=True, provider='deepseek', reason='invalid_action')
                error.shape = {'object': True, 'exact_keys': False, 'action_permitted': True,
                               'args_object': True, 'summary_text': True}
                raise error
            self.assertEqual([entry['action'] for entry in context['history']], ['read_file', 'protocol_error'])
            self.assertFalse(context['history'][-1]['outcome']['shape']['exact_keys'])
            return action('finish')
        gateway = FakeGateway(decide)
        self.state.enqueue('protocol', 'backend-engineer', 'fixture', 'Read and recover from invalid protocol')
        result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(len(gateway.calls), 3)
        self.assertEqual(result['checkpoint']['step'], 2)

    def test_specialist_may_question_defers_without_consultation_spin(self):
        self.state.enqueue('consult-qa', 'qa-engineer', 'fixture',
                           'Determine a safe test strategy',
                           payload={'may_public_context_allowed': True})
        def decide(agent, context):
            if not context['history']:
                return action('consult_may', {'question': 'What regression checks are needed?'})
            self.assertEqual(context['history'][1]['action'], 'read_file')
            return action('finish', summary='May read-only suggestion checked')
        gateway = FakeGateway(decide)
        reply = {'decision_untrusted': {'action': 'read_file',
                    'args': {'path': 'README.md'}, 'summary': 'Read before editing'},
                 'source': 'public_owner_comment'}
        with patch('horizon_core.worker.request_consultation',
                   return_value={'status': 'requested', 'ticket': 'safe-key', 'issue': 7}), \
             patch('horizon_core.worker.read_consultation', return_value=None):
            first = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(first['status'], 'queued')
        self.assertEqual(first['checkpoint']['pending_may'], {'ticket':'safe-key','round':0})
        self.assertEqual(first['attempts'], 0)
        self.assertEqual(len(gateway.calls), 1)
        self.assertIsNone(Worker(self.state, gateway, self.root).run_one())
        with patch('horizon_core.state.time.time', return_value=first['not_before']+1), \
             patch('horizon_core.worker.read_consultation', return_value=reply):
            completed = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(completed['status'], 'succeeded')
        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(completed['checkpoint']['history'][1]['outcome']['content'],
                         'fixture repository documentation')

    def test_no_providers_accepts_valid_owner_tool_action_without_another_llm(self):
        self.state.enqueue('fallback-qa', 'qa-engineer', 'fixture',
                           'Recover from provider outage')
        raised = GatewayError('Both models unavailable', retryable=True,
                              provider='all', reason='providers_unavailable')
        gateway = FakeGateway(lambda agent, context: (_ for _ in ()).throw(raised))
        with patch('horizon_core.worker.request_consultation',
                   return_value={'status': 'requested', 'ticket': 'fallback', 'issue': 8}), \
             patch('horizon_core.worker.read_consultation', return_value=None):
            queued = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(queued['checkpoint']['pending_may'], {'ticket':'fallback','round':0})
        self.assertEqual(queued['attempts'], 0)
        allowed = {'decision_untrusted': {'action': 'finish', 'args': {},
                    'summary': 'May response is a result, not code or tests'},
                   'source':'public_owner_comment'}
        with patch('horizon_core.state.time.time', return_value=queued['not_before']+1), \
             patch('horizon_core.worker.read_consultation', return_value=allowed):
            completed = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(completed['status'], 'succeeded')
        self.assertEqual(len(gateway.calls), 1)

    def test_three_distinct_may_decisions_can_replace_unavailable_models(self):
        self.state.enqueue('triple-fallback', 'qa-engineer', 'fixture',
                           'Use May when Gemini and DeepSeek are both down')
        gateway = FakeGateway(lambda agent, context: (_ for _ in ()).throw(
            GatewayError('providers gone', provider='all',
                         reason='providers_unavailable', retryable=True)))
        asked = []
        def fake_request(task, reason, *, round_index=0, state=None):
            asked.append(round_index)
            return {'status': 'requested', 'ticket': 'slot-'+str(round_index), 'issue': 20+round_index}
        def fake_read(task_id, *, agent=None, round_index=0):
            decisions = [
                {'action': 'read_file', 'args': {'path': 'README.md'}, 'summary': 'Inspect source'},
                {'action': 'remember', 'args': {'key': 'reviewed', 'value': True}, 'summary': 'Remember result'},
                {'action': 'finish', 'args': {}, 'summary': 'Read-only May fallback completed'}
            ]
            return {'decision_untrusted': decisions[round_index], 'source':'public_owner_comment'}
        with patch('horizon_core.worker.request_consultation', side_effect=fake_request), \
             patch('horizon_core.worker.read_consultation', return_value=None):
            job = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(job['status'], 'queued')
        for index in range(3):
            with patch('horizon_core.state.time.time', return_value=job['not_before']+1), \
                 patch('horizon_core.worker.request_consultation', side_effect=fake_request), \
                 patch('horizon_core.worker.read_consultation', side_effect=fake_read):
                job = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(asked, [0, 1, 2])
        self.assertEqual(len(gateway.calls), 3)
        self.assertTrue(self.state.recall('fixture', 'qa-engineer')['reviewed'])
        self.assertEqual(job['attempts'], 1)

    def test_may_batched_plan_survives_checkpoint_and_uses_no_more_model_calls(self):
        self.state.enqueue('batched-fallback', 'qa-engineer', 'fixture',
                           'Recover through multiple May steps')
        outage = GatewayError('providers gone', provider='all',
                              reason='providers_unavailable', retryable=True)
        gateway = FakeGateway(lambda agent, context: (_ for _ in ()).throw(outage))
        with patch('horizon_core.worker.request_consultation',
                   return_value={'status': 'requested', 'ticket': 'batch', 'issue': 44}):
            queued = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(queued['status'], 'queued')
        actions = [
            {'action': 'read_file', 'args': {'path': 'README.md'}, 'summary': 'Inspect source'},
            {'action': 'remember', 'args': {'key': 'read', 'value': True}, 'summary': 'Remember proof'},
            {'action': 'finish', 'args': {}, 'summary': 'May batch finished after evidence'}
        ]
        with patch('horizon_core.state.time.time', return_value=queued['not_before'] + 1), \
             patch('horizon_core.worker.read_consultation',
                   return_value={'decisions_untrusted': actions, 'source': 'public_owner_comment'}):
            sliced = Worker(self.state, gateway, self.root, slice_seconds=0.000000001).run_one()
        self.assertEqual(sliced['status'], 'queued')
        self.assertEqual(sliced['checkpoint']['may_plan'], actions[1:])
        self.assertNotIn('pending_may', sliced['checkpoint'])
        self.assertEqual(len(gateway.calls), 1)
        with patch('horizon_core.state.time.time', return_value=sliced['not_before'] + 1), \
             patch('horizon_core.worker.read_consultation',
                   side_effect=AssertionError('already consumed reply')):
            finished = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(finished['status'], 'succeeded')
        self.assertEqual([row['action'] for row in finished['checkpoint']['history']],
                         ['may_provider_fallback', 'read_file', 'remember'])
        self.assertNotIn('may_plan', finished['checkpoint'])
        self.assertEqual(finished['result']['summary'], actions[-1]['summary'])
        self.assertEqual(len(gateway.calls), 1)
        self.assertTrue(self.state.recall('fixture', 'qa-engineer')['read'])

    def test_may_batch_checks_all_permissions_before_first_action(self):
        self.state.enqueue('invalid-batch', 'qa-engineer', 'fixture',
                           'Do not execute an unapproved batch')
        gateway = FakeGateway(lambda a, c: (_ for _ in ()).throw(
            GatewayError('providers gone', provider='all',
                         reason='providers_unavailable', retryable=True)))
        with patch('horizon_core.worker.request_consultation',
                   return_value={'status': 'requested', 'ticket': 'bad-batch', 'issue': 45}):
            queued = Worker(self.state, gateway, self.root).run_one()
        decisions = [
            {'action': 'read_file', 'args': {'path': 'README.md'}, 'summary': 'Read'},
            {'action': 'delete_file', 'args': {'path': 'README.md'}, 'summary': 'Not permitted'}
        ]
        with patch('horizon_core.state.time.time', return_value=queued['not_before'] + 1), \
             patch('horizon_core.worker.read_consultation',
                   return_value={'decisions_untrusted': decisions}):
            denied = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(denied['status'], 'failed')
        self.assertEqual([row['action'] for row in denied['checkpoint']['history']],
                         ['may_provider_fallback'])
        self.assertEqual((self.root / 'README.md').read_text(), 'fixture repository documentation')

    def test_provider_outage_diagnosis_rechecks_models_without_duplicate_ticket(self):
        self.state.enqueue('narrative-fallback', 'qa-engineer', 'fixture',
                           'Await executable May decision')
        gateway = FakeGateway(lambda a, c: (_ for _ in ()).throw(
            GatewayError('providers gone', provider='all',
                         reason='providers_unavailable', retryable=True)))
        with patch('horizon_core.worker.request_consultation',
                   return_value={'status': 'requested', 'ticket': 'pending-action', 'issue': 46}):
            queued = Worker(self.state, gateway, self.root).run_one()
        with patch('horizon_core.state.time.time', return_value=queued['not_before'] + 1), \
             patch('horizon_core.worker.read_consultation',
                   return_value={'advice_untrusted': 'Check AI quota first'}), \
             patch('horizon_core.worker.request_consultation',
                   side_effect=AssertionError('never open another ticket')):
            waiting = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(waiting['status'], 'queued')
        self.assertEqual(waiting['checkpoint']['pending_may']['ticket'], 'pending-action')
        self.assertGreater(waiting['checkpoint']['pending_may']['provider_retry_after'],
                           queued['not_before'])
        self.assertEqual(waiting['attempts'], 0)
        self.assertEqual(len(gateway.calls), 2)

    def test_unanswered_provider_consultation_recovers_automatically(self):
        self.state.enqueue('outage-recovered', 'may-maestro', 'fixture',
                           'Recover an urgent task after model outage')
        call_count = [0]
        def decide(agent, context):
            call_count[0] += 1
            if call_count[0] == 1:
                raise GatewayError('upstream temporarily down', provider='all',
                                   reason='providers_unavailable', retryable=True)
            if call_count[0] == 2:
                return action('read_file', {'path': 'README.md'})
            self.assertEqual(context['history'][-1]['action'], 'read_file')
            return action('finish', summary='Recovered using the actual model')
        gateway = FakeGateway(decide)
        with patch('horizon_core.worker.request_consultation',
                   return_value={'status': 'requested', 'ticket': 'original-ticket', 'issue': 50}):
            first = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(first['checkpoint']['pending_may']['ticket'], 'original-ticket')
        with patch('horizon_core.state.time.time', return_value=first['not_before']+1), \
             patch('horizon_core.worker.read_consultation', return_value=None), \
             patch('horizon_core.worker.request_consultation',
                   side_effect=AssertionError('do not create another issue')):
            recovered = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(recovered['status'], 'succeeded')
        self.assertEqual(recovered['attempts'], 1)
        self.assertNotIn('pending_may', recovered['checkpoint'])
        self.assertEqual(len(gateway.calls), 3)
        self.assertEqual(recovered['result']['summary'],
                         'Recovered using the actual model')

    def test_unanswered_provider_consultation_throttles_retry_and_keeps_ticket(self):
        self.state.enqueue('outage-still-down', 'may-maestro', 'fixture',
                           'Keep the blocked coding task recoverable')
        gateway = FakeGateway(lambda a, c: (_ for _ in ()).throw(
            GatewayError('providers gone', provider='all',
                         reason='providers_unavailable', retryable=True)))
        with patch('horizon_core.worker.request_consultation',
                   return_value={'status': 'requested', 'ticket': 'same-ticket', 'issue': 47}):
            first = Worker(self.state, gateway, self.root).run_one()
        with patch('horizon_core.state.time.time', return_value=first['not_before']+1), \
             patch('horizon_core.worker.read_consultation', return_value=None), \
             patch('horizon_core.worker.request_consultation',
                   side_effect=AssertionError('no duplicate consultations')):
            waiting = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(waiting['status'], 'queued')
        self.assertEqual(waiting['attempts'], 0)
        self.assertEqual(waiting['checkpoint']['pending_may']['ticket'], 'same-ticket')
        self.assertEqual(len(gateway.calls), 2)
        retry_after = waiting['checkpoint']['pending_may']['provider_retry_after']
        self.assertGreater(retry_after, waiting['not_before'])
        with patch('horizon_core.state.time.time', return_value=waiting['not_before']-1), \
             patch('horizon_core.worker.request_consultation',
                   side_effect=AssertionError('no new consultation')), \
             patch('horizon_core.worker.read_consultation',
                   side_effect=AssertionError('task not due yet')):
            self.assertIsNone(Worker(self.state, gateway, self.root).run_one())
        self.assertEqual(len(gateway.calls), 2)
        with patch('horizon_core.state.time.time', return_value=waiting['not_before']+1), \
             patch('horizon_core.worker.read_consultation', return_value=None), \
             patch('horizon_core.worker.request_consultation',
                   side_effect=AssertionError('must keep old issue')):
            polled = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(polled['status'], 'queued')
        self.assertEqual(polled['checkpoint']['pending_may']['ticket'], 'same-ticket')
        # Checking May's ticket before the 15min model retry must not hit APIs.
        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(polled['checkpoint']['pending_may']['provider_retry_after'], retry_after)
        with patch('horizon_core.state.time.time', return_value=retry_after+1), \
             patch('horizon_core.worker.read_consultation', return_value=None), \
             patch('horizon_core.worker.request_consultation',
                   side_effect=AssertionError('still the same original issue')):
            again = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(again['status'], 'queued')
        self.assertEqual(again['checkpoint']['pending_may']['ticket'], 'same-ticket')
        self.assertEqual(len(gateway.calls), 3)

    def test_may_answer_unblocks_before_next_provider_retry(self):
        self.state.enqueue('outage-may-rescue', 'may-maestro', 'fixture',
                           'Use a valid May instruction even while providers remain down')
        gateway = FakeGateway(lambda a, c: (_ for _ in ()).throw(
            GatewayError('providers gone', provider='all',
                         reason='providers_unavailable', retryable=True)))
        with patch('horizon_core.worker.request_consultation',
                   return_value={'status': 'requested', 'ticket': 'original-owner-issue', 'issue': 47}):
            initial = Worker(self.state, gateway, self.root).run_one()
        with patch('horizon_core.state.time.time', return_value=initial['not_before']+1), \
             patch('horizon_core.worker.read_consultation', return_value=None):
            waiting = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(len(gateway.calls), 2)
        self.assertGreater(waiting['checkpoint']['pending_may']['provider_retry_after'],
                           waiting['not_before'])
        owner_action = {'decision_untrusted': {
            'action': 'finish', 'args': {}, 'summary': 'Owner action consumed from original issue'},
            'source': 'public_owner_comment'}
        with patch('horizon_core.state.time.time', return_value=waiting['not_before']+1), \
             patch('horizon_core.worker.read_consultation', return_value=owner_action), \
             patch('horizon_core.worker.request_consultation',
                   side_effect=AssertionError('must not create more tickets')):
            completed = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(completed['status'], 'succeeded')
        self.assertEqual(len(gateway.calls), 2)
        self.assertNotIn('pending_may', completed['checkpoint'])

    def test_consultation_cooldown_keeps_question_until_sent_without_extra_llm(self):
        self.state.enqueue('cooldown-specialist', 'qa-engineer', 'fixture',
                           'Ask for a safe test action',
                           payload={'may_public_context_allowed': True})
        gateway = FakeGateway(
            lambda agent, context: action('consult_may',
                {'question': 'Which isolated regression check should run first?'})
            if not context['history'] else action('finish', summary='Consultation consumed'))
        calls = []
        def fake_request(task, reason, *, round_index=0, question=None, state=None):
            calls.append((reason, question, round_index))
            if len(calls) == 1:
                return {'status': 'cooldown', 'retry_after_seconds': 180}
            return {'status': 'requested', 'ticket': 'resumed-consult', 'issue': 42}

        with patch('horizon_core.worker.request_consultation', side_effect=fake_request):
            first = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(first['status'], 'queued')
        self.assertEqual(first['attempts'], 0)
        self.assertEqual(first['checkpoint']['step'], 0)
        self.assertEqual(first['checkpoint']['deferred_may_request']['question'],
                         'Which isolated regression check should run first?')
        self.assertEqual(len(gateway.calls), 1)
        self.assertIsNone(Worker(self.state, gateway, self.root).run_one())

        with patch('horizon_core.state.time.time', return_value=first['not_before']+1), \
             patch('horizon_core.worker.request_consultation', side_effect=fake_request):
            resumed = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(resumed['checkpoint']['pending_may'],
                         {'ticket': 'resumed-consult', 'round': 0})
        self.assertNotIn('deferred_may_request', resumed['checkpoint'])
        self.assertEqual([row['action'] for row in resumed['checkpoint']['history']],
                         ['consult_may'])
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(calls[0], calls[1])

    def test_provider_outage_cooldown_resumes_without_repeating_failed_apis(self):
        self.state.enqueue('cooldown-provider', 'qa-engineer', 'fixture',
                           'Recover safely when upstream models fail')
        gateway = FakeGateway(lambda agent, context: (_ for _ in ()).throw(
            GatewayError('both unavailable', provider='all',
                         reason='providers_unavailable', retryable=True)))
        calls = []
        def fake_request(task, reason, *, round_index=0, question=None, state=None):
            calls.append((reason, round_index))
            if len(calls) == 1:
                return {'status': 'cooldown', 'retry_after_seconds': 120}
            return {'status': 'requested', 'ticket': 'resumed-provider', 'issue': 43}

        with patch('horizon_core.worker.request_consultation', side_effect=fake_request):
            first = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(first['status'], 'queued')
        self.assertEqual(first['attempts'], 0)
        self.assertEqual(first['checkpoint']['deferred_may_request'],
                         {'reason': 'providers_unavailable', 'round': 0})
        with patch('horizon_core.state.time.time', return_value=first['not_before']+1), \
             patch('horizon_core.worker.request_consultation', side_effect=fake_request):
            second = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(second['checkpoint']['pending_may']['ticket'], 'resumed-provider')
        self.assertEqual(second['attempts'], 0)
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(calls, [('providers_unavailable', 0)] * 2)

    def test_real_external_direct_chat_response_resumes_offline_specialist(self):
        self.state.enqueue("browser-direct-offline", "qa-engineer", "fixture",
                           "Use May after upstream reasoning providers are unavailable")
        failure = GatewayError("Both models unavailable",
                               provider="all", reason="providers_unavailable",
                               retryable=True)
        gateway = FakeGateway(lambda agent, context: (_ for _ in ()).throw(failure))
        direct = {"status": "direct_pending", "transport": "kernel",
                  "ticket": "0123456789abcdef0123", "agent": "qa-engineer"}
        with patch('horizon_core.worker.request_consultation', return_value=direct):
            first = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(first["status"], "queued")
        self.assertEqual(first["attempts"], 0)
        self.assertEqual(first["checkpoint"]["pending_may"]["transport"], "kernel")
        self.assertLessEqual(first["not_before"], 2000000000)
        self.assertEqual(len(gateway.calls), 1)

        reply = {"source": "chatgpt_web_untrusted", "advice_untrusted": "",
                 "decision_untrusted": {"action": "read_file",
                     "args": {"path": "README.md"}, "summary": "Read repository documentation"}}
        with patch('horizon_core.state.time.time',
                   return_value=first['not_before']+1), \
             patch('horizon_core.worker.read_direct_consultation', return_value=reply), \
             patch('horizon_core.worker.request_consultation', return_value=direct):
            second = Worker(self.state, gateway, self.root).run_one()
        # A genuine returned action was ACL-validated and executed, without an
        # additional model call. A subsequent unavailable model may ask again.
        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(second["checkpoint"]["history"][1]["action"], "read_file")
        self.assertEqual(second["checkpoint"]["history"][1]["outcome"]["content"],
                         "fixture repository documentation")

    def test_direct_chat_cannot_grant_unauthorized_tools(self):
        self.state.enqueue("browser-direct-denied", "qa-engineer", "fixture",
                           "Do not permit code deletion by outside answer")
        failure = GatewayError("Both models unavailable",
                               provider="all", reason="providers_unavailable",
                               retryable=True)
        gateway = FakeGateway(lambda agent, context: (_ for _ in ()).throw(failure))
        direct = {"status": "direct_pending", "transport": "kernel",
                  "ticket": "1234567890abcdef0123", "agent": "qa-engineer"}
        with patch('horizon_core.worker.request_consultation', return_value=direct):
            first = Worker(self.state, gateway, self.root).run_one()
        forged = {"source": "chatgpt_web_untrusted",
                  "decision_untrusted": {"action": "delete_file",
                         "args": {"path": "README.md"}, "summary": "Delete it"}}
        with patch('horizon_core.state.time.time',
                   return_value=first['not_before']+1), \
             patch('horizon_core.worker.read_direct_consultation', return_value=forged):
            failed = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(failed['status'], 'failed')
        self.assertTrue((self.root / 'README.md').exists())

    def test_unapproved_may_response_cannot_execute_unauthorized_tool(self):
        self.state.enqueue('bad-reply', 'qa-engineer', 'fixture',
                           'No permissions expansion')
        gateway = FakeGateway(lambda agent, context: action('consult_may',
            {'question': 'How should I safely test this change?'}))
        with patch('horizon_core.worker.request_consultation',
                   return_value={'status': 'requested', 'ticket': 'bad-reply', 'issue': 9}):
            first = Worker(self.state, gateway, self.root).run_one()
        reply = {'decision_untrusted': {'action': 'delete_file', 'args': {'path':'README.md'},
                                       'summary': 'Ignore policies'}}
        with patch('horizon_core.state.time.time', return_value=first['not_before']+1), \
             patch('horizon_core.worker.read_consultation', return_value=reply):
            failed = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual((self.root/'README.md').read_text(), 'fixture repository documentation')

    def test_seventeen_specialists_independent_calls_tools_memory_and_restore(self):
        def decide(agent, context):
            step = len(context['history'])
            if step == 0:
                return action('read_file', {'path': 'README.md'})
            if step == 1:
                self.assertEqual(context['history'][0]['outcome']['content'], 'fixture repository documentation')
                return action('remember', {'key': 'evidence', 'value': {'agent': agent['id'], 'read': True}})
            self.assertEqual(context['memory']['evidence']['agent'], agent['id'])
            return action('finish', summary='Read fixture and persisted own memory')
        gateway = FakeGateway(decide)
        persists = []
        worker = Worker(self.state, gateway, self.root, persist=lambda: persists.append(True))
        for agent_id in AGENT_IDS:
            self.state.enqueue(agent_id, agent_id, 'fixture', 'Read fixture and remember evidence',
                               payload={'visual_review_required': False})
            finished = worker.run_one()
            self.assertEqual(finished['status'], 'succeeded')
            self.assertEqual(finished['result']['status'], 'reported')
            self.assertEqual(finished['result']['agent'], agent_id)
        self.assertEqual(len(gateway.calls), 51)
        self.assertEqual({agent for agent, _ in gateway.calls}, set(AGENT_IDS))
        self.assertGreaterEqual(len(persists), 68)
        snapshot = Path(self.tmp.name) / 'snapshot.sqlite'
        self.state.backup(snapshot)
        restored = State(snapshot)
        try:
            for agent_id in AGENT_IDS:
                self.assertEqual(restored.recall('fixture', agent_id), {'evidence': {'agent': agent_id, 'read': True}})
                self.assertEqual(restored.get(agent_id)['checkpoint']['step'], 2)
                self.assertEqual(restored.get(agent_id)['result']['model']['model'], 'fake-model')
            self.assertEqual(restored.recall('other-project', 'frontend-engineer'), {})
        finally:
            restored.close()

    def test_maestro_recovers_invalid_delegation_without_losing_task(self):
        def decide(agent, context):
            history = context['history']
            if not history:
                # First proposal references a dependency that has not been created.
                return action('delegate', {'tasks': [
                    {'id': 'qa', 'agent': 'qa-engineer',
                     'objective': 'Test a newly implemented synthetic app',
                     'depends_on': ['backend']}
                ]})
            if len(history) == 1:
                outcome = history[0]['outcome']
                self.assertEqual(outcome['reason'], 'invalid_delegation')
                self.assertEqual(outcome['tool_error'], 'ValueError')
                self.assertNotIn('delegated', outcome)
                self.assertEqual(self.state.tasks(project='recoverable'), [self.state.get('fix-plan')])
                # Correct the dependency ordering on the next independent call.
                return action('delegate', {'tasks': [
                    {'id': 'backend', 'agent': 'backend-engineer',
                     'objective': 'Implement synthetic app with deterministic tests',
                     'depends_on': []},
                    {'id': 'qa', 'agent': 'qa-engineer',
                     'objective': 'Execute genuine checks after implementation',
                     'depends_on': ['backend']}
                ]})
            self.assertEqual(len(history), 2)
            self.assertEqual(len(history[-1]['outcome']['delegated']), 2)
            return action('finish', summary='Plan corrected and specialist tasks enqueued')
        self.state.enqueue('fix-plan', 'may-maestro', 'recoverable',
                           'Delegate validated backend and QA responsibilities')
        gateway = FakeGateway(decide)
        result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(len(gateway.calls), 3)
        self.assertEqual(result['checkpoint']['step'], 2)
        self.assertEqual(result['result']['delegated'], ['fix-plan:backend', 'fix-plan:qa'])
        self.assertEqual(self.state.get('fix-plan:backend')['status'], 'queued')
        self.assertEqual(self.state.get('fix-plan:qa')['status'], 'queued')

    def test_maestro_recovers_bad_plan_shape_from_model(self):
        def decide(agent, context):
            if not context['history']:
                return action('delegate', {'tasks': [None]})
            self.assertEqual(context['history'][0]['outcome']['reason'], 'invalid_delegation')
            return action('finish', summary='Rejected malformed delegation without external effects')
        self.state.enqueue('bad-shape', 'may-maestro', 'synthetic',
                           'Reject structurally malformed plan without crashing')
        result = Worker(self.state, FakeGateway(decide), self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['checkpoint']['step'], 1)
        self.assertEqual(len(self.state.tasks(project='synthetic')), 1)

    def test_maestro_delegates_dag_frontend_qa_corregedoria_results(self):
        plan = [
            {'id': 'frontend', 'agent': 'frontend-engineer', 'objective': 'Write sample UI fixture'},
            {'id': 'qa', 'agent': 'qa-engineer', 'objective': 'Test sample UI fixture', 'depends_on': ['frontend']},
            {'id': 'review', 'agent': 'corregedoria-may', 'objective': 'Review QA evidence report', 'depends_on': ['qa']},
        ]
        def decide(agent, context):
            agent_id, history = agent['id'], context['history']
            if agent_id == 'may-maestro':
                return action('delegate', {'tasks': plan}) if not history else action('finish', summary='Delegated, not verified')
            if agent_id == 'frontend-engineer':
                return action('write_file', {'path': 'app/ui.py', 'content': 'TITLE = "Ronda Pro"\n'}) if not history else action('finish', summary='Wrote UI fixture')
            if agent_id == 'qa-engineer':
                self.assertEqual(context['dependencies']['job:frontend']['agent'], 'frontend-engineer')
                return action('run_check', {'name': 'python-unittest'}) if not history else action('finish', summary='Report failed test honestly')
            report = context['dependencies']['job:qa']
            self.assertFalse(report['verification'][0]['passed'])
            self.assertEqual(report['verification'][0]['exit_code'], 1)
            return action('finish', summary='QA reported failure; delivery not verified')
        self.state.enqueue('job', 'may-maestro', 'fixture', 'Delegate fixture implementation and review',
                           payload={'branch': 'feat/test', 'allowed_paths': ['app/*.py']})
        gateway = FakeGateway(decide)
        worker = Worker(self.state, gateway, self.root)
        with patch('horizon_core.tools.shutil.which', return_value='/usr/bin/docker'), patch(
            'horizon_core.tools.Tools._docker_check', return_value={
                'check': 'python-unittest', 'exit_code': 1, 'passed': False, 'output': 'FAIL: fixture assertion'}):
            for expected in ['job', 'job:frontend', 'job:qa', 'job:review']:
                result = worker.run_one()
                self.assertEqual(result['task_id'], expected)
                self.assertEqual(result['status'], 'succeeded')
        self.assertEqual((self.root / 'app/ui.py').read_text(), 'TITLE = "Ronda Pro"\n')
        self.assertFalse(self.state.get('job:qa')['result']['verification'][0]['passed'])
        self.assertEqual(self.state.get('job:qa')['result']['status'], 'reported')
        self.assertIsNone(worker.run_one())

    def test_retryable_gateway_failure_resumes_history_without_repeating_tool(self):
        attempts = []
        def decide(agent, context):
            attempts.append(len(context['history']))
            if not context['history']:
                return action('remember', {'key': 'completed-step', 'value': 1})
            if len(attempts) == 2:
                raise GatewayError('quota reached', retryable=True)
            self.assertEqual(context['memory']['completed-step'], 1)
            return action('finish')
        gateway = FakeGateway(decide)
        worker = Worker(self.state, gateway, self.root)
        self.state.enqueue('retry', 'frontend-engineer', 'fixture', 'Resume previously persisted checkpoint')
        with patch('horizon_core.state.time.time', return_value=1000):
            result = worker.run_one()
            self.assertEqual(result['status'], 'queued')
            self.assertEqual(result['checkpoint']['step'], 1)
            self.assertEqual(result['not_before'], 1005)
            self.assertIsNone(worker.run_one())
        # New worker + fresh connection simulates process loss between invocations.
        reopened = State(self.path)
        try:
            with patch('horizon_core.state.time.time', return_value=1006):
                result = Worker(reopened, gateway, self.root).run_one()
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual(result['attempts'], 2)
            self.assertEqual(attempts, [0, 1, 1])
            self.assertEqual(sum(event['kind'] == 'checkpoint' for event in reopened.events('retry')), 1)
        finally:
            reopened.close()

    def test_both_providers_unavailable_preserves_checkpoint_and_safe_failure_details(self):
        self.state.enqueue('providers', 'frontend-engineer', 'fixture', 'Keep durable history when both providers fail')
        safe_failures = [
            {'provider': 'gemini', 'reason': 'quota', 'status': 429, 'retryable': True},
            {'provider': 'deepseek', 'reason': 'authentication', 'status': 401, 'retryable': False},
        ]
        def decide(agent, context):
            if not context['history']:
                return action('remember', {'key': 'saved', 'value': True})
            failures = [dict(safe_failures[0], upstream_body='do-not-store-provider-body'), safe_failures[1]]
            raise GatewayError('do-not-store-error-message-or-key', retryable=True,
                               provider='all', reason='providers_unavailable', failures=failures)
        gateway = FakeGateway(decide)
        with patch('horizon_core.state.time.time', return_value=1000):
            task = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(task['status'], 'queued')
        self.assertEqual(task['checkpoint']['step'], 1)
        self.assertEqual(task['checkpoint']['history'][0]['outcome'], {'remembered': 'saved'})
        self.assertEqual(self.state.recall('fixture', 'frontend-engineer'), {'saved': True})
        self.assertEqual(task['error'], {'kind': 'GatewayError', 'provider': 'all',
            'reason': 'providers_unavailable', 'status': None, 'retryable': True, 'failures': safe_failures})
        persisted = json.dumps(self.state.events('providers'))
        self.assertNotIn('do-not-store', persisted)
        self.assertNotIn('upstream_body', persisted)

    def test_failed_tool_stays_in_verification_even_when_model_finishes(self):
        gateway = FakeGateway(lambda agent, context: action('run_check', {'name': 'python-unittest'})
                              if not context['history'] else action('finish', summary='Could not run Docker check'))
        self.state.enqueue('check', 'qa-engineer', 'fixture', 'Record unavailable verification tool')
        with patch('horizon_core.tools.shutil.which', return_value=None):
            result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['result']['status'], 'reported')
        self.assertEqual(result['result']['verification'][0]['tool_error'], 'RuntimeError')
        self.assertNotIn('passed', result['result']['verification'][0])

    def test_lost_worktree_recovers_checkpoint_and_publishes_succeeded_workspace(self):
        subprocess.run(['git', '-C', str(self.root), 'add', 'README.md'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '-m', 'fixture base'], check=True, capture_output=True)
        baseline = Path(self.tmp.name) / 'baseline.git'
        subprocess.run(['git', 'clone', '--bare', str(self.root), str(baseline)], check=True, capture_output=True)
        payload = {'branch': 'feat/test', 'allowed_paths': ['app/*.py']}
        self.state.enqueue('workspace', 'frontend-engineer', 'fixture', 'Write recoverable UI and finish report', payload=payload)
        calls = []
        def decide(agent, context):
            calls.append(len(context['history']))
            if len(context['history']) == 0:
                return action('write_file', {'path': 'app/ui.py', 'content': 'TITLE = "first"\n'})
            if len(context['history']) == 1:
                return action('write_file', {'path': 'app/ui.py', 'content': 'TITLE = "final"\n'})
            if len(calls) == 3:
                raise GatewayError('fake interrupted provider', retryable=True)
            self.assertEqual((self.root / 'app/ui.py').read_text(), 'TITLE = "final"\n')
            return action('finish', summary='Recovered UI written by prior attempt')
        gateway = FakeGateway(decide)
        with patch('horizon_core.state.time.time', return_value=1000):
            result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'queued')
        mutation = self.state.recall('fixture', '__workspace__')['app/ui.py']
        self.assertEqual(mutation['content'], 'TITLE = "final"\n')
        self.assertEqual(mutation['expected_sha256'], hashlib.sha256(b'').hexdigest())
        # Recreate from the unchanged Git baseline: generated files exist only in SQLite.
        def lose_disk_and_clone():
            shutil.rmtree(self.root)
            subprocess.run(['git', 'clone', str(baseline), str(self.root)], check=True, capture_output=True)
            self.assertFalse((self.root / 'app/ui.py').exists())
        lose_disk_and_clone()
        reopened = State(self.path)
        try:
            with patch('horizon_core.state.time.time', return_value=1006):
                finished = Worker(reopened, gateway, self.root).run_one()
            self.assertEqual(finished['status'], 'succeeded')
            self.assertEqual(calls, [0, 1, 2, 2])
            self.assertEqual((self.root / 'app/ui.py').read_text(), 'TITLE = "final"\n')
            # Succeeded tasks are no longer claimable, but Publisher must still restore them.
            lose_disk_and_clone()
            self.assertIsNone(Worker(reopened, gateway, self.root).run_one())
            base_sha = subprocess.run(['git', '-C', str(self.root), 'rev-parse', 'HEAD'],
                                      check=True, capture_output=True, text=True).stdout.strip()
            requests = []
            class FakeGitHub:
                def _request(inner, method, path, body=None):
                    requests.append((method, path, body))
                    if (method, path) == ('GET', ''):
                        return {'default_branch': 'main'}
                    if method == 'GET' and path.startswith('/git/ref/heads/'):
                        return {'object': {'sha': base_sha}}
                    if method == 'GET' and path.startswith('/git/commits/'):
                        return {'tree': {'sha': 'base-tree'}}
                    if (method, path) == ('POST', '/git/trees'):
                        self.assertEqual(body['tree'], [{'path': 'app/ui.py', 'mode': '100644',
                            'type': 'blob', 'content': 'TITLE = "final"\n'}])
                        return {'sha': 'updated-tree'}
                    if (method, path) == ('POST', '/git/commits'):
                        self.assertEqual(body['parents'], [base_sha])
                        return {'sha': 'generated-commit'}
                    if (method, path) == ('PATCH', '/git/refs/heads/feat/test'):
                        self.assertEqual(body, {'sha': 'generated-commit', 'force': False})
                        return {}
                    raise AssertionError((method, path, body))
            published = publish(reopened, self.root, 'fixture/repository', api=FakeGitHub())
            self.assertTrue(published['published'])
            self.assertEqual(published['files'], ['app/ui.py'])
            self.assertEqual((self.root / 'app/ui.py').read_text(), 'TITLE = "final"\n')
            self.assertEqual(reopened.get('workspace')['status'], 'succeeded')
            self.assertEqual(len(requests), 6)
        finally:
            reopened.close()

    def test_persistence_failure_after_complete_preserves_succeeded_and_does_not_fail(self):
        self.state.enqueue('completed', 'frontend-engineer', 'fixture', 'Persist a completed report safely')
        gateway = FakeGateway(lambda agent, context: action('finish', summary='Completed local fixture'))
        persist_calls = []
        def persist():
            persist_calls.append(True)
            if len(persist_calls) == 2:
                raise OSError('fake remote write rejected after local commit')
        with patch.object(self.state, 'fail', wraps=self.state.fail) as fail, self.assertRaises(PersistenceError):
            Worker(self.state, gateway, self.root, persist=persist).run_one()
        fail.assert_not_called()
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(self.state.get('completed')['status'], 'succeeded')
        self.assertEqual(self.state.get('completed')['result']['summary'], 'Completed local fixture')
        self.assertIsNone(self.state.get('completed')['error'])

    def test_no_automatic_task_step_ceiling_while_context_stays_compact(self):
        self.state.enqueue('long', 'frontend-engineer', 'fixture', 'Reason through more than twelve fixture decisions')
        observed = []
        def decide(agent, context):
            count = context['completed_steps']
            observed.append(count)
            self.assertIsNone(context['remaining_steps'])
            self.assertLessEqual(len(context['history']), 4)
            if count == 18:
                self.assertEqual(context['history'][0]['outcome'], {'remembered': 'step-14'})
                self.assertEqual(context['history'][-1]['outcome'], {'remembered': 'step-17'})
                self.assertEqual(context['history_omitted'], 14)
                return action('finish')
            return action('remember', {'key': 'step-' + str(count), 'value': count})
        gateway = FakeGateway(decide)
        result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['result']['steps'], 19)
        self.assertEqual(observed, list(range(19)))
        self.assertEqual(len(result['checkpoint']['history']), 18)

    def test_long_model_then_tool_renews_before_tool_and_finishes_with_live_lease(self):
        from horizon_core.tools import Tools
        self.state.enqueue('long-lease', 'frontend-engineer', 'fixture', 'Keep lease across slow model and slow tool')
        clock = [1000]
        def decide(agent, context):
            clock[0] += 1200
            return action('read_file', {'path': 'README.md'}) if not context['history'] else action('finish')
        original_execute = Tools.execute
        tool_lease = []
        def slow_tool(tools, name, args):
            task = self.state.get('long-lease')
            tool_lease.append(task['lease_until'])
            self.assertEqual(task['lease_until'], clock[0] + 1800)
            clock[0] += 800
            self.assertGreater(task['lease_until'], clock[0])
            return original_execute(tools, name, args)
        with patch('horizon_core.state.time.time', side_effect=lambda: clock[0]), patch.object(
                Tools, 'execute', autospec=True, side_effect=slow_tool):
            result = Worker(self.state, FakeGateway(decide), self.root).run_one()
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['checkpoint']['step'], 1)
        self.assertEqual(tool_lease, [4000])
        self.assertEqual(clock[0], 4200)
        renewals = [event for event in self.state.events('long-lease') if event['kind'] == 'lease_renewed']
        self.assertTrue(any(event['created_at'] == 2200 for event in renewals))

    def test_time_slices_resume_fresh_workers_without_consuming_retry_attempts(self):
        self.state.enqueue('sliced', 'frontend-engineer', 'fixture', 'Resume cooperative slices until actual completion', max_attempts=1)
        lengths = []
        def decide(agent, context):
            count = context['completed_steps']
            lengths.append(count)
            if count >= 5:
                return action('finish')
            return action('remember', {'key': 'step-' + str(count), 'value': count})
        gateway = FakeGateway(decide)
        for index in range(5):
            reopened = State(self.path)
            try:
                # Each invocation may complete one action before its host timeslice ends.
                with patch('horizon_core.worker.time.monotonic', side_effect=[0, 2]):
                    result = Worker(reopened, gateway, self.root, slice_seconds=1).run_one()
                self.assertEqual(result['status'], 'queued')
                self.assertEqual(result['attempts'], 0)
                self.assertEqual(result['checkpoint']['step'], index + 1)
                self.assertIsNone(result['lease_owner'])
                self.assertIsNone(result['error'])
            finally:
                reopened.close()
        final = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(final['status'], 'succeeded')
        self.assertEqual(final['attempts'], 1)
        self.assertEqual(lengths, list(range(6)))
        self.assertEqual(sum(event['kind'] == 'yielded' for event in self.state.events('sliced')), 5)
        self.assertGreaterEqual(sum(event['kind'] == 'lease_renewed' for event in self.state.events('sliced')), 11)

    def test_remote_persistence_failure_stops_before_model_or_tool(self):
        self.state.enqueue('persist', 'frontend-engineer', 'fixture', 'Stop if remote checkpoint is unavailable')
        gateway = FakeGateway(lambda agent, context: action('finish'))
        def failed_persist():
            raise OSError('fake remote failure')
        with self.assertRaises(PersistenceError):
            Worker(self.state, gateway, self.root, persist=failed_persist).run_one()
        self.assertEqual(gateway.calls, [])
        self.assertEqual(self.state.get('persist')['status'], 'running')

    def test_unauthorized_model_tool_fails_task_and_budget_is_terminal(self):
        self.state.enqueue('denied', 'corregedoria-may', 'fixture', 'Attempt unauthorized fixture modification')
        gateway = FakeGateway(lambda agent, context: action('write_file', {'path': 'README.md', 'content': 'forbidden'}))
        result = Worker(self.state, gateway, self.root).run_one()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual((self.root / 'README.md').read_text(), 'fixture repository documentation')
        self.state.enqueue('budget', 'frontend-engineer', 'fixture', 'Exercise bounded fixture reasoning')
        gateway = FakeGateway(lambda agent, context: action('read_file', {'path': 'README.md'}))
        result = Worker(self.state, gateway, self.root, max_steps=2).run_one()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['reason'], 'step_limit_exceeded')


if __name__ == '__main__':
    unittest.main()
