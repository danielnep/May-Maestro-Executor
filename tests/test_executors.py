"""Unit and isolated integration tests for seven Horizon executors."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from maestro_exec.runner import Agent, PLUGIN_IDS, PROFILES, branch_safe, hash_obj, path_safe, verify_checkpoint

class ExecutorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/"README.md").write_text("# isolated fixture\n",encoding="utf-8")
        (self.root/"public").mkdir()
        (self.root/"public"/"styles.css").write_text(
            "@media (max-width:760px) {button:focus-visible {outline:2px solid blue;}}\n"
            "@media (prefers-reduced-motion:reduce) {button {transition:none}}\n",encoding="utf-8")
        (self.root/"index.html").write_text(
            '<!doctype html><html lang="pt-BR"><head><meta name="viewport" content="width=device-width">'
            '</head><body><main><button>Testar</button></main></body></html>',encoding="utf-8")
    def runner(self,slug):
        return Agent(slug,self.root,self.root/"evidencias"/slug,branch="feat/suite-20261018")
    def test_seven_unique_real_private_plugin_mappings(self):
        self.assertEqual(len(PROFILES),7);self.assertEqual(len(set(PLUGIN_IDS.values())),7)
        self.assertEqual(set(PLUGIN_IDS),set(PROFILES))
    def test_forbid_default_and_production_branches(self):
        for candidate in ("main","master","production","release","feature/test","feat/../main","feat/"):
            self.assertFalse(branch_safe(candidate),candidate)
        self.assertTrue(branch_safe("feat/suite-20261018"))
    def test_forbid_path_escape_and_secret_references(self):
        for path in ("../../etc/passwd","/root/.env",".env","secrets/key.txt",
                     ".github/workflows/deploy.yml","folder\\file","node_modules/x"):
            self.assertFalse(path_safe(self.root,path),path)
        self.assertTrue(path_safe(self.root,"public/styles.css"))
    def test_every_agent_generates_verifiable_checkpoint(self):
        for slug in PROFILES:
            with self.subTest(agent=slug):
                result=self.runner(slug).run()
                self.assertIn(result["status"],("passed","blocked"));self.assertEqual(result["agent"],slug)
                self.assertEqual(result["plugin_id"],PLUGIN_IDS[slug])
                self.assertFalse(result["main_changed"]);self.assertFalse(result["production_changed"])
                p=self.root/"evidencias"/slug/"checkpoint.json"
                self.assertTrue(verify_checkpoint(p));self.assertGreaterEqual(len(result["checks"]),2)
    def test_checkpoint_tamper_detection(self):
        agent=self.runner("corregedoria-may");agent.run()
        path=agent.evidence/"checkpoint.json"
        content=json.loads(path.read_text(encoding="utf-8"));content["status"]="failed"
        path.write_text(json.dumps(content),encoding="utf-8")
        self.assertFalse(verify_checkpoint(path))
    def test_maestro_dependency_plan(self):
        result=self.runner("may-maestro").run()
        plans=[x for x in result["checks"] if x["check"]=="plans"]
        self.assertIn("horizon-ui-guardian",plans[0]["next_agents"])
    def test_ui_guardian_assesses_mobile_styles(self):
        result=self.runner("horizon-ui-guardian").run()
        css=[x for x in result["checks"] if x["check"]=="css"][0]
        self.assertEqual(css["status"],"passed")
        self.assertGreater(css["styles"][0]["mobile_breakpoints"],0)
    def test_persona_detects_localization_issue(self):
        (self.root/"other.html").write_text('<html lang="en"><main>Oops</main></html>',encoding="utf-8")
        result=self.runner("may-persona").run()
        self.assertEqual([x for x in result["checks"] if x["check"]=="copy"][0]["status"],"warning")
    def test_readonly_roles_reject_implementation(self):
        for slug in ("may-maestro","corregedoria-may"):
            with self.assertRaises(ValueError):self.runner(slug).implement(self.root/"README.md",True)
    def test_implementation_requires_branch_and_explicit_permission(self):
        with self.assertRaises(ValueError):self.runner("horizon-skills").implement(self.root/"README.md",False)
    def test_implementation_rejects_without_real_worktree(self):
        with self.assertRaisesRegex(ValueError,"worktree"):
            self.runner("horizon-skills").implement(self.root/"README.md",True)
    def test_remote_url_blocked_for_visual_test(self):
        result=self.runner("horizon-ui-guardian").run(url="https://example.com")
        self.assertEqual([x for x in result["checks"] if x["check"]=="visual"][0]["status"],"blocked")
    def test_no_external_network_access_needed_for_test_suite(self):
        result=self.runner("horizon-project-studio").run()
        self.assertTrue(all("https" not in x.get("detail","") for x in result["checks"]))
    def test_every_agent_has_dedicated_playbook(self):
        root=Path(__file__).resolve().parents[1]
        for slug in PROFILES:
            path=root/"playbooks"/(slug+".md")
            with self.subTest(agent=slug):
                self.assertTrue(path.is_file())
                self.assertGreater(len(path.read_text(encoding="utf-8")),200)

    def test_hash_is_canonical(self):
        self.assertEqual(hash_obj({"a":1,"b":2}),hash_obj({"b":2,"a":1}))

if __name__=="__main__":
    unittest.main()

