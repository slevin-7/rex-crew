"""A worker that ended its turn is not always finished: it can wait on its own background job (Claude Code starts its
next turn when the job ends) or stop at the usage limit. collect and watch must say which.
Run: python3 -m unittest tests/test_rt_paused.py"""
import json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402

JOB = "● Started the tests in the background; I'll check when they finish.\n────────────\n❯ \n────────────\n  ⏵⏵ auto mode on · 1 shell\n"
LIMIT = "● Working on it\n  ⎿  You've hit your 5-hour limit · resets 3pm (Europe/Berlin)\n────────────\n❯ \n────────────\n"


class TestPaused(Base):
    def screen(self, label, text):
        st = self.fake()
        st.setdefault("screens", {})[label] = text
        with open(self.fake_state, "w") as f:
            json.dump(st, f)

    def test_paused_reads_the_footer_and_the_limit_line(self):
        p = self.mod.paused
        self.assertEqual(p(JOB), ("job", "1 shell"))
        self.assertEqual(p(LIMIT), ("limit", "resets 3pm"))
        self.assertEqual(p("Codex: You've hit your usage limit. Try again at 3:05 PM.\n› \n"), ("limit", "resets 3:05 PM"))
        self.assertIsNone(p("I ran 1 shell command.\n❯ \n"))  # words above the input box are not the footer
        self.assertIsNone(p("Done.\n❯ \n  ? for shortcuts\n"))

    def test_collect_keeps_waiting_on_a_worker_with_its_own_job(self):
        self.screen("w1", JOB)
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", self.workdir(), self.task)
        code, out, err = self.cli("collect", "--timeout", "1")
        self.assertEqual(code, 3, out + err)
        self.assertIn("waits on its own background job: 1 shell", out)

    def test_collect_names_the_usage_limit(self):
        self.screen("w1", LIMIT)
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", self.workdir(), self.task)
        code, out, err = self.cli("collect")
        self.assertEqual(code, 1, out + err)
        self.assertIn("usage limit (resets 3pm)", out)
        self.assertIn("rex-crew send w1 continue", out)

    def test_watch_is_quiet_on_a_job_and_names_the_limit(self):
        for label, text in (("w1", JOB), ("w2", LIMIT)):
            self.screen(label, text)
            self.script(label, rec("working"), rec("done"))
            self.cli("start", label, self.workdir(label), self.task)
        rex = self.mod.Rex(None, "s1")
        runs, memo = self.mod.mine(rex), {}
        lines = [l for _ in range(3) for _, l in self.mod.watch_events(rex, runs, memo, self.mod.time.time())]
        self.assertFalse(any("w1" in l for l in lines), lines)
        self.assertTrue(any("w2" in l and "usage limit (resets 3pm)" in l for l in lines), lines)


if __name__ == "__main__":
    unittest.main()
