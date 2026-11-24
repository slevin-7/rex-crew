"""Red-team tests for size and clarity: option values that were accepted and then did the wrong thing.
Run: python3 -m unittest tests/test_rt_small.py"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402


class Small(Base):
    def test_start_refuses_a_verify_timeout_below_one(self):
        # sleep 0 / sleep -1 made the watchdog flag a timeout at once: a good verify was reported as killed
        for v in ("0", "-5"):
            d = self.workdir("w" + v.strip("-"))
            code, out, err = self.cli("start", "w1", d, self.task, "--verify", "true", "--verify-timeout", v)
            self.assertEqual(code, 2, f"{v}: {out}{err}")
            self.assertIn("--verify-timeout", err)
        self.assertEqual(self.fake()["blocks"], {})

    def test_approve_with_an_invalid_match_regex_is_a_clear_error(self):
        self.script("w1", rec("blocked", "permission", "approve Bash: ls"))
        self.assertEqual(self.cli("start", "w1", self.workdir(), self.task)[0], 0)
        code, out, err = self.cli("approve", self.block_of("w1"), "--match", "(")
        self.assertEqual(code, 2, out + err)
        self.assertIn("--match", err)
        self.assertEqual(self.sent(), [])

    def test_runs_last_must_be_positive(self):
        self.script("w1", rec("working"))
        self.assertEqual(self.cli("start", "w1", self.workdir(), self.task)[0], 0)
        for v in ("0", "-1"):  # -1 used to hide the oldest run, 0 meant 20
            code, out, err = self.cli("runs", "--last", v)
            self.assertEqual(code, 2, f"{v}: {out}{err}")
            self.assertIn("--last", err)

    def test_screen_lines_must_be_positive(self):
        for v in ("0", "-3"):  # a negative count used to skip the first lines, 0 printed everything
            code, out, err = self.cli("screen", "b1", v)
            self.assertEqual(code, 2, f"{v}: {out}{err}")
            self.assertIn("LINES", err)


if __name__ == "__main__":
    unittest.main()
