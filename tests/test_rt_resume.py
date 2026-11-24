"""Red-team tests: a new conversation resumes orchestration from the ledger. Run: python3 -m unittest tests/test_rt_resume.py"""
import os, subprocess, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402
from test_rt_ledger import run  # noqa: E402


class TestResume(Base):
    def seed(self):
        with open(self.ledger, "w") as f:
            import json
            json.dump([run("old", state="blocked", session="s7", host="buildbox", t0=self.mod.time.time() - 7200)], f)

    def test_runs_without_a_session_lists_everything_with_session_and_age(self):
        self.seed()
        with mock.patch.dict(os.environ):
            os.environ.pop("REX_CREW_SESSION")
            code, out, err = self.cli("runs")
        self.assertEqual(code, 0, out + err)
        cols = out.strip().split("\t")
        self.assertEqual(cols[1], "old")
        self.assertIn("buildbox", cols)
        self.assertIn("s7", cols)
        self.assertIn("2h", cols)

    def test_runs_in_the_wrong_session_says_where_the_live_workers_are(self):
        self.seed()
        code, out, err = self.cli("runs")  # session s1 has no run, s7 on buildbox has a live one
        self.assertEqual(code, 0, out + err)
        self.assertIn("-S buildbox -s s7", out + err)

    def test_collect_in_the_wrong_session_says_where_the_live_workers_are(self):
        self.seed()
        code, out, err = self.cli("collect")
        self.assertIn("-S buildbox -s s7", out + err)

    def test_gone_worker_still_shows_what_it_left_on_disk(self):
        d = self.workdir()
        self.script("w1", rec("working"), "GONE")
        self.assertEqual(self.cli("start", "w1", d, self.task)[0], 0)
        self.report(d, "w1", "STATUS: done\nCHANGED: kept\n")
        code, out, err = self.cli("collect")
        self.assertEqual(code, 5, out + err)
        self.assertIn("state: gone", out)
        self.assertIn("CHANGED: kept", out)
        self.assertIn(d, out)


class TestWalkthrough(unittest.TestCase):
    def test_resume_walkthrough_runs_green(self):
        here = os.path.dirname(os.path.abspath(__file__))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("REX_", "FAKE_REX"))}
        p = subprocess.run(["sh", os.path.join(here, "walkthrough_resume.sh")], capture_output=True, text=True, env=env, timeout=180)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("state: unclean", out)
        self.assertIn("state: gone", out)
        self.assertIn("tally: 4 workers resumed", out)


if __name__ == "__main__":
    unittest.main()
