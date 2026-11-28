"""A Rex server restart can give a worker's pane a new id: collect follows the pane by its label. Run: python3 -m unittest tests/test_rt_relink.py"""
import json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402


class TestRelink(Base):
    def restart(self, old, label, *records, extra=()):
        """The pane OLD is gone; a pane labelled LABEL (and any EXTRA labels) is there under a new id."""
        st = self.fake()
        st["blocks"][old]["closed"] = True
        for i, lab in enumerate((label, *extra)):
            st["blocks"][f"n{i}"] = {"label": lab, "output": "", "closed": False, "session": "s1",
                                    "script": list(records), "prog": []}
        with open(self.fake_state, "w") as f:
            json.dump(st, f)

    def test_collect_follows_the_pane_under_its_new_id(self):
        d = self.workdir()
        self.script("w1", rec("working"))
        self.cli("start", "w1", d, self.task)
        old = self.block_of("w1")
        self.restart(old, "w1", rec("working"), rec("done"))
        self.report(d, "w1")
        code, out, err = self.cli("collect")
        self.assertEqual(code, 0, out + err)
        self.assertIn("state: done", out)
        self.assertIn("a Rex server restart", err)
        self.assertEqual(self.block_of("w1"), "n0", "the ledger now knows the new id")

    def test_two_panes_with_the_name_are_not_guessed(self):
        d = self.workdir()
        self.script("w1", rec("working"))
        self.cli("start", "w1", d, self.task)
        self.restart(self.block_of("w1"), "w1", rec("working"), extra=("w1",))
        code, out, _ = self.cli("collect", "--timeout", "5")
        self.assertEqual(code, 5, out)  # gone: which of the two is the worker cannot be told
        self.assertIn("state: gone", out)


if __name__ == "__main__":
    unittest.main()
