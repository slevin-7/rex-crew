"""Cheap subagents and the advisor: --subagent-model and --advisor shape the claude worker's start line.
Run: python3 -m unittest tests/test_rt_subagents.py"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_rt_codex as rc  # noqa: E402  (module import: its test classes must not run twice)

FORCED = '--settings \'{"env":{"CLAUDE_CODE_SUBAGENT_MODEL":"haiku","CLAUDE_CODE_SUBAGENT_MODEL_FORCE":"1","CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH":"1"}}\''


class Subagents(rc.Codex):
    plan = rc.Fanout.plan

    def go(self, *extra):
        self.script("w1")
        return self.cli("start", "w1", self.workdir("w1"), self.task, *extra)

    def test_subagent_model_and_advisor_reach_claude(self):
        self.assertEqual(self.go("--subagent-model", "haiku", "--advisor", "opus")[0], 0)
        line = self.prog("w1")
        self.assertIn(FORCED, line)
        self.assertIn("--advisor opus", line)
        self.assertLess(line.index("exec claude"), line.index(FORCED))

    def test_without_the_options_nothing_is_added(self):
        self.go()
        self.assertNotIn("SUBAGENT", self.prog("w1"))
        self.assertNotIn("--advisor", self.prog("w1"))

    def test_a_model_that_is_not_a_plain_name_is_refused(self):
        code, _, err = self.go("--subagent-model", "haiku; rm -rf ~")
        self.assertEqual(code, 6, err)
        self.assertIn("model alias", err)

    def test_own_settings_and_subagent_model_do_not_mix(self):
        code, _, err = self.go("--subagent-model", "haiku", "--", "--settings", "{}")
        self.assertEqual(code, 6, err)
        self.assertIn("your --settings", err)

    def test_codex_has_no_advisor(self):
        code, _, err = self.go("--agent", "codex", "--advisor", "opus")
        self.assertEqual(code, 6, err)
        self.assertIn("Codex has no advisor", err)

    def test_codex_gets_its_own_subagent_keys(self):
        self.assertEqual(self.go("--agent", "codex", "--subagent-model", "gpt-5-mini")[0], 0)
        line = self.prog("w1")
        self.assertIn("-c 'agents.default_subagent_model=\"gpt-5-mini\"' -c 'agents.default_subagent_reasoning_effort=\"low\"'", line)
        self.assertNotIn("CLAUDE_CODE", line)
        self.assertLess(line.index("default_subagent_model"), line.index(" -- 'Read"))

    def test_fanout_plan_passes_them_to_claude_workers(self):
        path = self.plan(subagent_model="haiku", advisor="opus")
        self.script("a"); self.script("b")
        self.assertEqual(self.cli("fanout", path)[0], 0)
        for n in ("a", "b"):
            self.assertIn(FORCED, self.prog(n))
            self.assertIn("--advisor opus", self.prog(n))


if __name__ == "__main__":
    unittest.main()
