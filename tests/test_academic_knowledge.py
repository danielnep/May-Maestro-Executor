import json
from pathlib import Path
import tempfile
import unittest
from horizon_core.academic_knowledge import ROOT, catalogue, read_knowledge
from horizon_core.registry import AGENT_IDS, load_agent
from horizon_core.tools import Tools
from horizon_core.gateway import validate_action


class AcademicKnowledgeTests(unittest.TestCase):
    def test_all_17_have_real_access_on_both_provider_paths(self):
        for ident in AGENT_IDS:
            agent = load_agent(ident)
            self.assertIn('Academic working set', agent['instructions'])
            self.assertIn('Academic working set', agent['_groq_instructions'])
            self.assertLess(len(agent['_groq_instructions'].encode()), 12500)
            validate_action({'action': 'read_knowledge', 'args': {}, 'summary': 'study'}, agent['tools'])
            with tempfile.TemporaryDirectory() as unrelated:
                tool = Tools(unrelated, agent)
                index = tool.execute('read_knowledge', {})
                self.assertTrue(index['sources'])
                result = tool.execute('read_knowledge', {'source_id': index['sources'][0]['id']})
                self.assertIn('Aplicação Horizon', result['content'])
                self.assertFalse(result['full_book_ingested'])
                self.assertTrue(result['untrusted'])

    def test_tampered_source_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = json.loads((ROOT/'manifest.json').read_text())
            manifest['entries'] = manifest['entries'][:1]
            (root/'manifest.json').write_text(json.dumps(manifest))
            (root/manifest['entries'][0]['path']).write_text('replaced document')
            with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
                catalogue(root)

    def test_invalid_source_and_range_rejected(self):
        for args in ({'source_id': '../../etc/passwd'},
                     {'source_id': 'hci-stanford', 'start_line': True},
                     {'source_id': 'hci-stanford', 'end_line': 101}):
            with self.assertRaises(ValueError):
                read_knowledge('ui-ux-designer', args)

    def test_sources_have_citations_and_current_ui_research(self):
        rows = catalogue()
        self.assertEqual(len(rows), 12)
        self.assertTrue(any(r['kind'] == 'textbook' for r in rows))
        ui = read_knowledge('ui-ux-designer', {})['sources']
        self.assertIn('material-research', {r['id'] for r in ui})
        self.assertIn('hci-stanford', {r['id'] for r in ui})
