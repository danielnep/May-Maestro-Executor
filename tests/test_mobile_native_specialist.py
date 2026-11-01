"""Mobile specialist contract: independent role, bounded sourced knowledge, unchanged web roles."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from horizon_core.registry import AGENT_IDS, load_agent
from horizon_core.tools import Tools

class MobileNativeTests(unittest.TestCase):
    def test_mobile_agent_is_distinct_from_web_and_existing_android(self):
        self.assertIn('mobile-native-engineer', AGENT_IDS)
        self.assertEqual(len(AGENT_IDS), len(set(AGENT_IDS)))
        native = load_agent('mobile-native-engineer')
        self.assertIn('iOS', native['instructions'])
        self.assertIn('APK', native['instructions'])
        self.assertIn('search_knowledge', native['tools'])
        self.assertNotIn('search_knowledge', load_agent('frontend-engineer')['tools'])
        self.assertNotIn('search_knowledge', load_agent('mobile-app-builder')['tools'])

    def test_forum_knowledge_is_retrieved_with_source_and_acceptance(self):
        with TemporaryDirectory() as tmp:
            tools = Tools(tmp, load_agent('mobile-native-engineer'))
            result = tools.execute('search_knowledge', {'query':'android webview keyboard ime'})
            self.assertGreater(len(result['matches']), 0)
            self.assertLessEqual(len(result['matches']), 5)
            ids = {item['id'] for item in result['matches']}
            self.assertIn('android-keyboard-ime-2026', ids)
            for item in result['matches']:
                self.assertTrue(item['source'].startswith('https://'))
                self.assertTrue(item['acceptance'])
                self.assertEqual(item['status'], 'observed_not_universal')

    def test_forum_knowledge_denied_for_web_agent(self):
        with TemporaryDirectory() as tmp:
            tools = Tools(tmp, load_agent('frontend-engineer'))
            with self.assertRaisesRegex(ValueError,'Capability'):
                tools.execute('search_knowledge', {'query':'android'})

    def test_input_bounds(self):
        with TemporaryDirectory() as tmp:
            tools = Tools(tmp, load_agent('mobile-native-engineer'))
            for query in ('x', 'x' * 161, None):
                with self.assertRaises(ValueError):
                    tools.execute('search_knowledge', {'query':query})
