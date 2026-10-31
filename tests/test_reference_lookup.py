import tempfile,sqlite3,unittest
from pathlib import Path
from horizon_core.reference_lookup import search
from horizon_core.registry import load_agent,AGENT_IDS
from horizon_core.tools import Tools
class ReferenceLookupTests(unittest.TestCase):
 def test_all_agents_have_scoped_tool(self):
  for agent in AGENT_IDS:self.assertIn('search_references',load_agent(agent)['tools'])
 def test_scoped_search(self):
  with tempfile.TemporaryDirectory() as folder:
   db=Path(folder)/'docs.db';c=sqlite3.connect(db)
   c.execute('CREATE VIRTUAL TABLE docs USING fts5(agent UNINDEXED, source UNINDEXED,updated UNINDEXED,content)')
   c.execute('INSERT INTO docs VALUES (?,?,?,?)',('horizon-ui-guardian','WCAG','today','contrast and mobile reflow'))
   c.execute('INSERT INTO docs VALUES (?,?,?,?)',('security-engineer','OWASP','today','contrast and phishing'))
   c.commit();c.close()
   result=search('horizon-ui-guardian','contrast mobile',db)
   self.assertEqual(len(result['results']),1);self.assertEqual(result['results'][0]['source'],'WCAG')
   self.assertEqual(len(search('backend-engineer','contrast',db)['results']),0)
 def test_missing_index_fails_closed(self):
  self.assertFalse(search('horizon-ui-guardian','contrast','/no/such/file')['available'])
 def test_bad_arguments(self):
  with self.assertRaises(ValueError):search('horizon-ui-guardian','',index='/tmp/unknown')
  with self.assertRaises(ValueError):search('../escape','contrast',index='/tmp/unknown')
