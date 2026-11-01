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

    def test_two_live_research_channels_and_curated_knowledge(self):
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            tools = Tools(tmp, load_agent('mobile-native-engineer'))
            with patch('horizon_core.research.search_web', side_effect=lambda q: {'query':q,'results':[]}) as search:
                result = tools.execute('research_mobile', {'query':'Android 16 WebView keyboard', 'platform':'android'})
            self.assertEqual(search.call_count, 2)
            self.assertIn('developer.android.com', result['live']['official']['query'])
            self.assertIn('github.com', result['live']['community']['query'])
            self.assertGreater(len(result['curated']),0)
            self.assertTrue(result['source_contents_untrusted'])

    def test_research_failure_is_not_fabricated_as_success(self):
        from unittest.mock import patch
        from horizon_core.research import ResearchError
        with TemporaryDirectory() as tmp:
            tools = Tools(tmp, load_agent('mobile-native-engineer'))
            with patch('horizon_core.research.search_web', side_effect=ResearchError('offline')):
                result = tools.execute('research_mobile', {'query':'SwiftUI safe area', 'platform':'ios'})
            self.assertEqual(result['live']['official']['status'],'unavailable')
            self.assertEqual(result['live']['community']['status'],'unavailable')

    def test_readonly_android_cli_adapter(self):
        from unittest.mock import patch, Mock
        with TemporaryDirectory() as tmp:
            tools = Tools(tmp, load_agent('mobile-native-engineer'))
            with patch('horizon_core.tools.subprocess.run', return_value=Mock(returncode=0, stdout='sdk: test',stderr='')) as command:
                result=tools.execute('android_cli',{'operation':'info'})
            self.assertTrue(result['passed'])
            self.assertIn('/usr/sbin/runuser',command.call_args.args[0][0])
            self.assertIn('horizon-worker',command.call_args.args[0])
            self.assertNotIn('GEMINI_API_KEY',command.call_args.kwargs['env'])
            with self.assertRaises(ValueError):
                tools.execute('android_cli',{'operation':'arbitrary-shell'})

    def test_input_bounds(self):
        with TemporaryDirectory() as tmp:
            tools = Tools(tmp, load_agent('mobile-native-engineer'))
            for query in ('x', 'x' * 161, None):
                with self.assertRaises(ValueError):
                    tools.execute('search_knowledge', {'query':query})
