"""A worker that asks in plain text (Codex has no question dialog) ends its turn: collect and watch must say it asks.
Run: python3 -m unittest tests/test_rt_asks.py"""
import json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402

CODEX = "• Tests pass.\n• Should I also update the docs?\n\n› Implement {feature}\n  gpt-5-codex · 80% left\n"


class TestAsks(Base):
    def screen(self, label, text):
        st = self.fake()
        st.setdefault("screens", {})[label] = text
        with open(self.fake_state, "w") as f:
            json.dump(st, f)

    def test_collect_shows_the_question_of_a_worker_without_report(self):
        self.screen("w1", CODEX)
        self.script("w1", rec("working", app="codex"), rec("done", app="codex"))
        self.cli("start", "w1", self.workdir(), self.task, "--agent", "codex")
        code, out, err = self.cli("collect")
        self.assertEqual(code, 1, out + err)
        self.assertIn("last words end with a question", out)
        self.assertIn("rex-crew send w1 TEXT", out)
        self.assertIn("question:\n| • Should I also update the docs?", out)

    def test_a_worker_that_just_ends_is_not_said_to_ask(self):
        self.screen("w1", "Done, all committed.\n› \n")
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", self.workdir(), self.task)
        _, out, _ = self.cli("collect")
        self.assertNotIn("question", out)
        self.assertIn("wrote no report", out)

    def test_watch_says_the_worker_may_wait(self):
        self.screen("w1", CODEX)
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", self.workdir(), self.task)
        rex = self.mod.Rex(None, "s1")
        runs, memo = self.mod.mine(rex), {}
        lines = [l for _ in range(3) for _, l in self.mod.watch_events(rex, runs, memo, self.mod.time.time())]
        self.assertTrue(any("end with a question" in l and "Should" not in l for l in lines), lines)


if __name__ == "__main__":
    unittest.main()
