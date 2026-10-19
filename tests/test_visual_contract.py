"""Only actual post-edit screenshots + Gemini image review satisfy visual QA."""
import unittest
from horizon_core.registry import load_agent
from horizon_core.visual_contract import requires_visual_review, visual_review_status

SHA = 'a'*64


def captured():
    return {'action':'run_check','outcome':{
        'check':'browser-playwright',
        'screenshots':[{'path':'/tmp/evidence/mobile.png','sha256':SHA}],
        'passed':True}}


def inspected():
    return {'action':'inspect_image','outcome':{'actual_image_provided':True,
        'summary':'Screenshot of mobile interface inspected'}}


class VisualContractTest(unittest.TestCase):
    def test_visual_specialists_have_both_browser_and_image_tools(self):
        for specialist in ('ui-ux-designer','horizon-ui-guardian','frontend-engineer',
                           'mobile-app-builder','qa-engineer'):
            with self.subTest(specialist=specialist):
                tools=load_agent(specialist)['tools']
                self.assertIn('run_check',tools)
                self.assertIn('inspect_image',tools)

    def test_default_visual_only_for_ui_reviewers(self):
        self.assertTrue(requires_visual_review({'agent':'ui-ux-designer','payload':{}}))
        self.assertTrue(requires_visual_review({'agent':'horizon-ui-guardian','payload':{}}))
        self.assertFalse(requires_visual_review({'agent':'backend-engineer','payload':{}}))
        self.assertTrue(requires_visual_review({'agent':'frontend-engineer',
                                                'payload':{'visual_review_required':True}}))
        self.assertFalse(requires_visual_review({'agent':'ui-ux-designer',
                                                 'payload':{'visual_review_required':False}}))
        with self.assertRaises(ValueError):
            requires_visual_review({'agent':'ui-ux-designer',
                                    'payload':{'visual_review_required':'no'}})

    def test_capture_must_be_followed_by_review_after_latest_edit(self):
        self.assertTrue(visual_review_status([captured(),inspected()])['verified'])
        self.assertFalse(visual_review_status([inspected(),captured()])['verified'])
        self.assertFalse(visual_review_status([captured(),inspected(),
                                               {'action':'write_file','outcome':{}}])['verified'])
        self.assertTrue(visual_review_status([captured(),inspected(),
                                               {'action':'write_file','outcome':{}},
                                               captured(),inspected()])['verified'])
        self.assertFalse(visual_review_status([{
            'action':'run_check','outcome':{'check':'node-test',
            'screenshots':[{'path':'/tmp/a.png','sha256':SHA}]}},inspected()])['verified'])
        self.assertFalse(visual_review_status([{
            'action':'run_check','outcome':{'check':'browser-playwright',
            'screenshots':[{'path':'/tmp/a.png','sha256':'incorrect'}]}},inspected()])['verified'])

if __name__=='__main__':
    unittest.main()
