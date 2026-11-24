"""Red-team tests for the skill as the product (lessons a worker run taught). Run: python3 -m unittest tests/test_rt_teach.py"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import rec
from test_rt_fanout import GitBase


class TestVerifyThatDidNotRun(GitBase):
    def done(self, *extra):
        d = self.workdir()
        self.script("w1", rec("done"))
        self.assertEqual(self.cli("start", "w1", d, self.task, *extra)[0], 0)
        self.report(d, "w1")

    def test_no_verify_is_said_not_left_out(self):
        # a clean-looking result without any verify line reads as "verified"; it must say the check did not run
        self.done()
        code, out, err = self.cli("collect")
        self.assertIn("verify: not run", out)
        self.assertEqual(code, 0, out + err)

    def test_no_verify_is_said_in_brief_too(self):
        self.done()
        code, out, err = self.cli("collect", "--brief")
        self.assertIn("verify: not run", out)

    def test_a_verify_that_ran_is_not_called_unrun(self):
        self.done("--verify", "true")
        code, out, err = self.cli("collect")
        self.assertIn("verify: exit 0", out)
        self.assertNotIn("not run", out)


class TestSilentWorker(GitBase):
    def test_send_to_a_pane_that_reports_working_points_at_screen_and_close(self):
        # "Wait with collect" sent the orchestrator in a circle when the worker was waiting on a prompt status does not show
        d = self.workdir()
        self.script("w1", rec("working"))
        self.assertEqual(self.cli("start", "w1", d, self.task)[0], 0)
        code, out, err = self.cli("send", self.block_of("w1"), "y")
        self.assertEqual(code, 6, out + err)
        self.assertIn("screen", err)
        self.assertIn("close", err)
        self.assertEqual(self.sent(), [])


if __name__ == "__main__":
    unittest.main()
