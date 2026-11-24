"""Red-team review of the merged script: places where two fixes touch the same thing. Run: python3 -m unittest tests/test_rt_review.py"""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402


class Review(Base):
    def start(self, name, script, d=None, *extra):
        d = d or self.workdir(name)
        self.script(name, *script)
        code, out, err = self.cli("start", name, d, self.task, *extra)
        self.assertEqual(code, 0, out + err)
        return d

    def state_of(self, name):
        return [r for r in self.ledger_runs() if r["name"] == name][-1]["state"]

    def test_one_failed_status_call_does_not_free_the_directory(self):
        # tick() says one failed status call is not a vanished pane; start() must agree.
        d = self.start("w1", [rec("working")])
        real, n = self.mod.Rex.status, []
        def flaky(rex, block):
            n.append(1)
            return {"state": "gone"} if len(n) == 1 else real(rex, block)
        self.script("w2", rec("working"))
        with mock.patch.object(self.mod.Rex, "status", flaky):
            code, out, err = self.cli("start", "w2", d, self.task)
        self.assertEqual(code, 6, out + err)
        self.assertEqual(self.state_of("w1"), "open")

    def test_really_gone_pane_still_frees_the_directory(self):
        d = self.start("w1", ["GONE"])
        self.script("w2", rec("working"))
        self.assertEqual(self.cli("start", "w2", d, self.task)[0], 0)
        self.assertEqual(self.state_of("w1"), "gone")

    def test_errored_worker_still_holds_its_directory(self):
        d = self.start("w1", [rec("error", msg="overloaded")])
        self.assertEqual(self.cli("collect", "--timeout", "0")[0], 4)
        self.assertEqual(self.state_of("w1"), "error")
        self.script("w2", rec("working"))
        code, out, err = self.cli("start", "w2", d, self.task)
        self.assertEqual(code, 6, out + err)

    def test_close_marks_an_errored_worker_closed(self):
        self.start("w1", [rec("error", msg="overloaded")])
        self.cli("collect", "--timeout", "0")
        self.assertEqual(self.cli("close", self.block_of("w1"))[0], 0)
        self.assertEqual(self.state_of("w1"), "closed")

    def test_run_waits_only_for_its_own_worker(self):
        # another live worker with the same NAME (other directory) must not hold up or colour 'run NAME'
        self.start("w1", [rec("working")])
        d2 = self.workdir("other")
        self.script("w1", rec("working"), rec("done"))
        code, out, err = self.cli("run", "w1", d2, self.task, "--timeout", "3")
        self.assertNotEqual(code, 3, out + err)  # unclean (no report), but finished
        self.assertNotIn("still working", out)


if __name__ == "__main__":
    unittest.main()
