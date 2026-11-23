"""Red-team tests for `collect` with several workers. Run: python3 -m unittest tests/test_rt_collect.py"""
import json, sys, unittest
from unittest import mock

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402

PERM = lambda cmd: rec("blocked", "permission", "permission approve Bash: " + cmd)


class Collect(Base):
    def start(self, name, script):
        d = self.workdir(name)
        self.script(name, *script)
        self.assertEqual(self.cli("start", name, d, self.task)[0], 0)
        return d

    def run_of(self, name):
        return [r for r in self.ledger_runs() if r["name"] == name][0]

    # (a) a question for the orchestrator must not stall the other workers' routine prompts
    def test_others_are_served_again_before_a_question_is_handed_back(self):
        self.start("w1", [rec("blocked", "question", "which database?")])
        # each pass reads the status twice (tick, then the check before the key), so every prompt appears twice
        self.start("w2", [PERM("git status"), PERM("git status"), PERM("git log -1"), PERM("git log -1"), rec("working")])
        pol = self.policy([r"^Bash: git (status|log)\b"])
        code, out, err = self.cli("collect", "--policy", pol, "--timeout", "30")
        self.assertEqual(code, 2, out + err)
        self.assertIn("which database?", out)
        b2 = self.run_of("w2")["block"]
        presses = [s for s in self.sent("1") if s["block"] == b2]
        self.assertEqual(len(presses), 2, "w2's second routine prompt stalled while w1 waited for the orchestrator")

    def test_no_linger_when_nothing_was_pressed(self):
        self.start("w1", [rec("blocked", "question", "q?")])
        self.start("w2", [rec("working")])
        calls = []
        self.mod.time.sleep = lambda s: calls.append(s)
        code, out, _ = self.cli("collect", "--timeout", "30")
        self.assertEqual(code, 2)
        self.assertEqual(calls, [], "waited although there was nothing to serve")

    # (b) collect must not overwrite what another process changed meanwhile
    def test_close_during_collect_is_not_overwritten(self):
        d1 = self.start("w1", [rec("working"), rec("done")])
        self.report(d1, "w1")
        self.start("w2", [rec("working")])
        b2 = self.run_of("w2")["block"]
        real, once = self.mod.finish, []

        def finish(rex, run):
            if not once:
                once.append(1)
                self.assertEqual(self.cli("close", b2)[0], 0)  # another process, while collect works
            return real(rex, run)
        with mock.patch.object(self.mod, "finish", finish):
            self.cli("collect", "--timeout", "1")
        self.assertEqual(self.run_of("w2")["state"], "closed")

    # (c) a host failure while reading one result must not lose the others
    def test_host_failure_in_finish_keeps_the_other_results(self):
        d1 = self.start("w1", [rec("working"), rec("done")])
        d2 = self.start("w2", [rec("working"), rec("done")])
        self.report(d1, "w1", "STATUS: done\nCHANGED: first\n")
        self.report(d2, "w2", "STATUS: done\nCHANGED: second\n")
        real = self.mod.Rex.sh

        def sh(rex, script):
            if "report-w1" in script:
                self.mod.die("could not run a command on the host: flaky")
            return real(rex, script)
        with mock.patch.object(self.mod.Rex, "sh", sh):
            code, out, err = self.cli("collect", "--timeout", "1")
        self.assertIn("CHANGED: second", out)
        self.assertIn("w1", out + err)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.run_of("w1")["state"], "open", "the unread result must stay collectable")
        self.assertEqual(self.run_of("w2")["state"], "done")
        code, out, err = self.cli("collect", "w1", "--timeout", "0")
        self.assertIn("CHANGED: first", out)

    def test_failing_key_press_does_not_abort_the_loop(self):
        self.start("w1", [PERM("git status")])
        d2 = self.start("w2", [rec("working"), rec("done")])
        self.report(d2, "w2")
        real = self.mod.Rex.type

        def typ(rex, block, text):
            self.mod.die("rex send failed: pane busy")
        with mock.patch.object(self.mod.Rex, "type", typ):
            code, out, err = self.cli("collect", "--policy", self.policy([r"git status"]), "--timeout", "1")
        self.assertIn("== w2", out)

    # (d) the printed state must be the state that counts
    def test_unclean_result_is_not_shown_as_done(self):
        self.start("w1", [rec("working"), rec("done")])  # no report file
        code, out, _ = self.cli("collect", "--timeout", "1")
        self.assertEqual(code, 1)
        self.assertNotIn("state: done", out)
        self.assertIn("state: unclean", out)

    # (e) a blocked worker outranks an error in the exit code
    def test_blocked_outranks_error_in_the_exit_code(self):
        self.start("w1", [rec("blocked", "question", "which one?")])
        self.start("w2", [rec("error", msg="boom")])
        code, out, _ = self.cli("collect", "--timeout", "0")
        self.assertIn("boom", out)
        self.assertEqual(code, 2, "a question for the orchestrator was hidden by another worker's error")

    # (f) idle for one look between two turns is not the end
    def test_single_idle_blip_is_not_the_end(self):
        d = self.start("w1", [rec("working"), rec("idle"), rec("working")])
        self.report(d, "w1")
        code, out, _ = self.cli("collect", "--timeout", "1")
        self.assertNotIn("== w1", out)
        self.assertIn("still working: w1", out)

    def test_sustained_idle_is_the_end(self):
        d = self.start("w1", [rec("working"), rec("idle")])
        self.report(d, "w1")
        code, out, _ = self.cli("collect", "--timeout", "30")
        self.assertEqual(code, 0, out)
        self.assertIn("state: done", out)


if __name__ == "__main__":
    unittest.main()
