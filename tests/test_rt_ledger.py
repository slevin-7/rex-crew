"""Red-team tests for the run ledger. Run: python3 -m unittest tests/test_rt_ledger.py"""
import json, os, subprocess, sys, tempfile, textwrap, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, SCRIPT, rec  # noqa: E402


def run(name, **kw):
    r = {"id": f"1-{name}", "name": name, "dir": f"/x/{name}", "host": "", "session": "s1", "block": f"b-{name}",
         "state": "open", "t0": 0, "started": "2026-01-01 00:00"}
    r.update(kw)
    return r


class TestLedger(Base):
    def write_ledger(self, content):
        with open(self.ledger, "w") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))

    def test_ledger_that_is_not_a_list_does_not_crash_start(self):
        self.write_ledger({"runs": []})
        code, out, err = self.cli("start", "w1", self.workdir(), self.task)
        self.assertEqual(code, 0, out + err)
        self.assertEqual([r["name"] for r in self.ledger_runs()], ["w1"])

    def test_corrupt_ledger_is_kept_not_silently_destroyed(self):
        self.write_ledger('[{"id": "1-old", "name": "old", "dir": "/x", "host": "", "session": "s1", "bl')
        code, out, err = self.cli("start", "w1", self.workdir(), self.task)
        self.assertEqual(code, 0, out + err)
        kept = [f for f in os.listdir(os.path.dirname(self.ledger)) if f.startswith("runs.json.corrupt")]
        self.assertTrue(kept, "the unreadable ledger must be moved aside, not overwritten")
        self.assertIn("corrupt", err.lower())

    def test_entries_missing_keys_do_not_crash_commands(self):
        self.write_ledger([{"id": "old", "name": "ancient"}, "junk", 7, run("ok", dir="/y")])
        code, out, err = self.cli("runs", "--all")
        self.assertEqual(code, 0, out + err)
        self.assertIn("ok", out)
        code, out, err = self.cli("start", "w1", self.workdir(), self.task)
        self.assertEqual(code, 0, out + err)
        self.assertIn("w1", [r.get("name") for r in self.ledger_runs() if isinstance(r, dict)])

    def test_collect_ignores_malformed_entries(self):
        self.write_ledger([{"id": "old", "name": "ancient"}])
        code, out, err = self.cli("collect")
        self.assertEqual(code, 0, out + err)
        self.assertIn("no open workers", out)

    def test_concurrent_updates_are_not_lost(self):
        prog = textwrap.dedent("""
            import importlib.machinery, importlib.util, sys
            l = importlib.machinery.SourceFileLoader("m", sys.argv[1])
            m = importlib.util.module_from_spec(importlib.util.spec_from_loader(l.name, l)); l.exec_module(m)
            for i in range(25):
                n = f"p{sys.argv[2]}n{i}"
                m.ledger_update({"id": n, "name": n, "dir": "/x", "host": "", "session": "s1", "block": "b", "state": "open"})
        """)
        procs = [subprocess.Popen([sys.executable, "-c", prog, SCRIPT, str(k)], env=self.env) for k in range(6)]
        for p in procs:
            self.assertEqual(p.wait(), 0)
        self.assertEqual(len(self.ledger_runs()), 6 * 25)

    def test_same_name_twice_in_one_second_keeps_both(self):
        with mock.patch.object(self.mod.time, "time", lambda: 1000.0):
            self.cli("start", "w1", self.workdir("a"), self.task)
            self.cli("start", "w1", self.workdir("b"), self.task)
        runs = self.ledger_runs()
        self.assertEqual(len(runs), 2)
        self.assertEqual(len({r["block"] for r in runs}), 2)

    def test_cap_never_drops_live_workers(self):
        self.write_ledger([run("live", state="blocked")] + [run(f"d{i}", state="done") for i in range(299)])
        self.mod.ledger_update(run("new"))
        self.mod.ledger_update(run("new2"))
        names = [r["name"] for r in self.ledger_runs()]
        self.assertIn("live", names)
        self.assertLessEqual(len(names), 300)

    def test_unwritable_state_stops_before_a_pane_is_started(self):
        blocker = os.path.join(self.tmp.name, "afile")
        open(blocker, "w").close()
        self.mod.LEDGER = os.path.join(blocker, "sub", "runs.json")
        code, out, err = self.cli("start", "w1", self.workdir(), self.task)
        self.assertNotEqual(code, 0)
        self.assertIn("ledger", err.lower())
        self.assertNotIn("Traceback", err)
        self.assertEqual([b for b in self.fake()["blocks"].values() if b["label"] == "w1"], [],
                         "no orphan worker may be started when it cannot be recorded")

    def test_runs_last_counts_only_this_session(self):
        mine = [run("m1", session="s1")]
        others = [run(f"o{i}", session="s9") for i in range(30)]
        self.write_ledger(mine + others)
        code, out, err = self.cli("runs")
        self.assertEqual(code, 0, out + err)
        self.assertIn("m1", out)

    def test_dead_pane_does_not_block_its_directory_forever(self):
        d = self.workdir()
        self.script("w1", "GONE")
        self.cli("start", "w1", d, self.task)
        code, out, err = self.cli("start", "w2", d, self.task)
        self.assertEqual(code, 0, out + err)

    def test_other_sessions_and_hosts_do_not_leak_into_collect(self):
        d = self.workdir()
        self.write_ledger([run("alien", session="s9", dir=d), run("far", host="other", dir=d)])
        code, out, err = self.cli("collect")
        self.assertIn("no open workers", out)
        self.assertEqual(self.fake()["sent"], [])


if __name__ == "__main__":
    unittest.main()
