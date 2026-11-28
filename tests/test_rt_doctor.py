"""doctor: is the host ready, and which live workers come back after a Rex restart. Run: python3 -m unittest tests/test_rt_doctor.py"""
import json, os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402

RULES = """bindings: 2
resume rules: 2
  claude (built in)
    command: claude --resume <uuid>
    dir: ~/code (init.lua:2)
    flags: 44 kept, 12 dropped
  not in force:
    codex removed at init.lua:1
"""


class TestDoctor(Base):
    def host(self, hook=True, rules=RULES, resume=None):
        home = f"{self.tmp.name}/home"
        os.makedirs(f"{home}/.claude", exist_ok=True)
        os.makedirs(f"{home}/.local/bin", exist_ok=True)  # first on the PATH doctor gives the host: the fake rex and claude, never real ones
        if not os.path.exists(f"{home}/.local/bin/rex"):
            os.symlink(f"{self.tmp.name}/bin/rex", f"{home}/.local/bin/rex")
            with open(f"{home}/.local/bin/claude", "w") as f:
                f.write("#!/bin/sh\necho '2.1.332 (Claude Code)'\n")
            os.chmod(f"{home}/.local/bin/claude", 0o755)
        with open(f"{home}/.claude/settings.json", "w") as f:
            f.write('{"hooks": {"SessionStart": [{"hooks": [{"command": "rex __internal session-start --agent claude"}]}]}}' if hook else "{}")
        p = mock.patch.dict(os.environ, {"HOME": home})
        p.start(); self.addCleanup(p.stop)
        st = self.fake()
        st["config_check"], st["resume"] = rules, resume or {}
        with open(self.fake_state, "w") as f:
            json.dump(st, f)

    def test_ready_host_and_a_worker_that_comes_back(self):
        self.script("w1", rec("working"))
        self.cli("start", "w1", self.workdir(), self.task)
        self.host(resume={"w1": [{"chosen": True, "trusted": True, "rule": "claude"}]})
        code, out, err = self.cli("doctor")
        self.assertEqual(code, 0, out + err)
        self.assertIn("ok    Rex's Claude Code hook is installed", out)
        self.assertIn("resume rule for claude is in force (dir: ~/code", out)
        self.assertIn("worker w1", out)
        self.assertIn("comes back after a Rex restart", out)

    def test_missing_hook_rule_and_an_untrusted_worker_exit_1(self):
        self.script("w1", rec("working"))
        self.cli("start", "w1", self.workdir(), self.task)
        rules = "resume rules: 0\n  not in force:\n    claude left out for a mistake\n"
        self.host(hook=False, rules=rules, resume={"w1": [{"chosen": True, "trusted": False, "untrusted_reason": "flag_not_listed"}]})
        code, out, err = self.cli("doctor")
        self.assertEqual(code, 1, out + err)
        self.assertIn("FAIL  Rex's Claude Code hook is missing", out)
        self.assertIn("FAIL  no Rex resume rule for claude", out)  # "claude" under "not in force" does not count
        self.assertIn("does not come back after a Rex restart (flag_not_listed", out)

    def test_worker_without_a_record_and_codex_worker(self):
        self.script("w1", rec("working")); self.script("w2", rec("working"))
        self.cli("start", "w1", self.workdir("a"), self.task)
        self.cli("start", "w2", self.workdir("b"), self.task, "--agent", "codex")
        self.host()
        code, out, _ = self.cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("w1", out); self.assertIn("has not told Rex how to start it again", out)
        self.assertIn("note  worker w2", out)


    def test_debug_log_only_while_its_file_exists(self):
        self.host()
        log = os.path.join(os.path.dirname(self.ledger), "debug.log")
        self.cli("doctor")
        self.assertFalse(os.path.exists(log))
        open(log, "w").close()
        code, out, _ = self.cli("doctor")
        self.assertIn(f"rex-crew {self.mod.VERSION}; debug log on", out)
        with open(log) as f:
            lines = f.read().splitlines()
        self.assertTrue(lines and all(" exit " in l and "rex-crew" in l for l in lines), lines)
        self.assertTrue(any(" run -s s1 " in l for l in lines), lines)

    def test_version(self):
        code, out, _ = self.cli("--version")
        self.assertEqual((code, out.strip()), (0, f"rex-crew {self.mod.VERSION}"))


if __name__ == "__main__":
    unittest.main()
