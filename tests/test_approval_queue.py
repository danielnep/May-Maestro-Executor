import tempfile
import unittest
from pathlib import Path
from horizon_core.approval_queue import ApprovalQueue

A='a'*64;B='b'*64
class ApprovalQueueTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.q=ApprovalQueue(Path(self.temp.name)/'state.db')
 def tearDown(self):self.q.close();self.temp.cleanup()
 def proposal(self,**kw):
  data=dict(project='Ronda Pro',change='Ajustar hierarquia',before_sha=A,after_sha=B,tests={'mobile':True},ticket='ui-001');data.update(kw)
  return self.q.propose(**data)
 def test_full_lifecycle(self):
  r=self.proposal();self.assertEqual(r['status'],'awaiting_decision')
  self.assertEqual(self.q.propose(**self.proposal_args())['digest'],r['digest'])
  self.assertEqual(len(self.q.pending_events()),1)
  r=self.q.decide('ui-001',r['digest'],True,actor='owner:authenticated')
  self.assertEqual(r['status'],'approved')
  self.assertEqual(self.q.transition('ui-001',r['digest'],'executing')['status'],'executing')
  self.assertEqual(self.q.transition('ui-001',r['digest'],'completed',result='QA passed')['status'],'completed')
  self.assertEqual(len(self.q.pending_events()),4)
 def proposal_args(self):return dict(project='Ronda Pro',change='Ajustar hierarquia',before_sha=A,after_sha=B,tests={'mobile':True},ticket='ui-001')
 def test_reject_and_replay(self):
  r=self.proposal();self.q.decide('ui-001',r['digest'],False,actor='owner:authenticated')
  with self.assertRaises(ValueError):self.q.decide('ui-001',r['digest'],True,actor='owner:authenticated')
  with self.assertRaises(ValueError):self.q.transition('ui-001',r['digest'],'executing')
 def test_proposal_mutation_requires_new_ticket(self):
  r=self.proposal()
  with self.assertRaises(ValueError):self.proposal(after_sha='c'*64)
  with self.assertRaises(ValueError):self.q.decide('ui-001','e'*64,True,actor='owner:authenticated')
  self.assertEqual(self.q.get('ui-001')['status'],'awaiting_decision')
 def test_restart_persists_and_deduplicates(self):
  r=self.proposal();events=self.q.pending_events();self.q.close()
  self.q=ApprovalQueue(Path(self.temp.name)/'state.db')
  self.assertEqual(self.q.get('ui-001')['digest'],r['digest'])
  self.assertTrue(self.q.begin_delivery(events[0]['id']))
  self.assertFalse(self.q.begin_delivery(events[0]['id']))
  self.assertTrue(self.q.confirm_delivery(events[0]['id'],receipt='visible-ticket-ui-001'))
  self.assertFalse(self.q.confirm_delivery(events[0]['id'],receipt='visible-ticket-ui-001'))
  self.assertEqual(self.q.pending_events(),[])
 def test_uncertain_delivery_never_resends_automatically(self):
  self.proposal();event=self.q.pending_events()[0]
  self.assertTrue(self.q.begin_delivery(event['id']))
  self.assertFalse(self.q.begin_delivery(event['id']))
  self.assertTrue(self.q.delivery_uncertain(event['id']))
  self.assertEqual(self.q.pending_events(),[])
  self.assertEqual(len(self.q.unresolved_delivery()),1)
  with self.assertRaises(ValueError):self.q.confirm_delivery(event['id'],receipt='no')
 def test_failed_can_be_retried_only_after_approval(self):
  r=self.proposal();self.q.decide('ui-001',r['digest'],True,actor='owner:authenticated')
  self.q.transition('ui-001',r['digest'],'executing')
  self.q.transition('ui-001',r['digest'],'failed',result='Network failure')
  self.assertEqual(self.q.transition('ui-001',r['digest'],'executing')['status'],'executing')
if __name__=='__main__':unittest.main()
