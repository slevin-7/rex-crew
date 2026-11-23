"""Red-team tests for the known leftovers: host timeouts, answer/send text, ledger litter, double collect.
Run: python3 -m unittest tests/test_rt_rest.py"""
import json, os, sys, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402


class Rest(Base):
    def start(self, name, script, d=None):
        d = d or self.workdir(name)
        self.script(name, *script)
        self.assertEqual(self.cli("start", name, d, self.task)[0], 0)
        return d

    def age(self, name, seconds):
        runs = self.ledger_runs()
        for r in runs:
            if r["name"] == name:
                r["t0"] = time.time() - seconds
        with open(self.ledger, "w") as f:
            json.dump(runs, f)

    def hang(self, spec):
        p = mock.patch.dict(os.environ, {"FAKE_REX_HANG": spec, "REX_CREW_TIMEOUT": "1"})
        p.start(); self.addCleanup(p.stop)

    # -- a host that stops answering ---------------------------------------------------------------
    def test_silent_host_is_a_bounded_error_naming_the_host(self):
        self.hang("capture")
        t = time.time()
        code, out, err = self.cli("-S", "hostx", "screen", "b1")
        self.assertLess(time.time() - t, 20)
        self.assertNotEqual(code, 0)
        self.assertIn("hostx", err)

    def test_silent_host_during_collect_is_reported_not_hung(self):
        self.start("w1", [rec("working")])
        self.hang("block")
        t = time.time()
        code, out, err = self.cli("collect", "--timeout", "0")
        self.assertLess(time.time() - t, 20)
        self.assertIn(code, (3, 4), out + err)

    def test_start_that_hangs_after_creating_the_pane_leaves_no_unrecorded_worker(self):
        d = self.workdir("w1")
        self.script("w1", rec("working"))
        self.hang("run:w1")
        code, out, err = self.cli("start", "w1", d, self.task)
        self.assertNotEqual(code, 0, out)
        recorded = {r["block"] for r in self.ledger_runs() if r["state"] in ("open", "blocked", "timeout")}
        for b, blk in self.fake()["blocks"].items():
            if blk["label"] == "w1":
                self.assertTrue(blk["closed"] or b in recorded, f"pane {b} runs a worker nobody knows about")

    # -- answer / send / sh text ---------------------------------------------------------------------
    def test_numeric_free_text_answer_is_possible(self):
        self.start("w1", [rec("blocked", "question", "which year?")])
        b = self.block_of("w1")
        st = self.fake()
        st["blocks"][b]["output"] = "Which year?\n1. 2023\n2. 2025\n3. Type something\n"
        with open(self.fake_state, "w") as f:
            json.dump(st, f)
        code, out, err = self.cli("answer", b, "--text", "2024")
        self.assertEqual(code, 0, out + err)
        vals = [s["value"] for s in self.fake()["sent"] if s["block"] == b]
        self.assertEqual(vals, ["3", "2024", "enter"])

    def test_send_text_starting_with_a_dash(self):
        self.start("w1", [rec("idle")])
        b = self.block_of("w1")
        for text in (["-1"], ["-x", "now"], ["--force"]):
            code, out, err = self.cli("send", b, *text)
            self.assertEqual(code, 0, out + err)
            self.assertEqual([s for s in self.sent() if s["kind"] == "text"][-1]["value"], " ".join(text))

    def test_sh_command_starting_with_a_dash(self):
        code, out, err = self.cli("sh", "-x")
        self.assertNotIn("usage:", err)
        code, out, err = self.cli("-s", "s1", "sh", "--", "echo", "hi")
        self.assertNotIn("usage:", err)

    def test_run_still_splits_claude_args(self):
        d = self.workdir("w1")
        self.script("w1", rec("working"))
        code, out, err = self.cli("start", "w1", d, self.task, "--", "--permission-mode", "acceptEdits")
        self.assertEqual(code, 0, out + err)

    # -- ledger litter ----------------------------------------------------------------------------------
    def test_failed_starts_leave_no_gone_entries(self):
        for _ in range(5):
            self.cli("start", "w1", self.workdir("w1") + "/missing/x", self.task)
        self.cli("start", "w2", self.workdir("w2"), self.task)
        gone = [r for r in self.ledger_runs() if r["state"] == "gone" and not r.get("block")]
        self.assertEqual(gone, [])

    def test_old_corrupt_files_are_pruned(self):
        d = os.path.dirname(self.ledger)
        for i in range(12):
            with open(f"{d}/runs.json.corrupt-{1700000000 + i}", "w") as f:
                f.write("x")
        with open(self.ledger, "w") as f:
            f.write("{not json")
        self.cli("runs")
        left = sorted(x for x in os.listdir(d) if ".corrupt-" in x)
        self.assertLessEqual(len(left), 5)
        self.assertEqual(len([x for x in left if x.endswith(tuple(str(1700000000 + i) for i in range(12)))]), len(left) - 1)

    # -- two collects ------------------------------------------------------------------------------------
    def test_two_collects_do_not_report_one_worker_twice(self):
        d = self.start("w1", [rec("done")])
        self.report(d, "w1")
        self.age("w1", 100)
        real, outs = self.mod.tick, []

        def tick(rex, run, policy):
            res = real(rex, run, policy)
            if res and not outs:  # the other process looks at the same moment, before this one wrote anything
                outs.append("")
                outs[0] = self.cli("collect")[1]
            return res
        with mock.patch.object(self.mod, "tick", tick):
            code, out, err = self.cli("collect")
        self.assertEqual((out + outs[0]).count("== w1"), 1, outs[0] + "\n---\n" + out)

    # -- when is a worker finished ------------------------------------------------------------------------
    def test_timeout_zero_does_not_say_still_working_for_an_idle_finished_worker(self):
        d = self.start("w1", [rec("idle")])
        self.report(d, "w1")
        self.age("w1", 100)
        code, out, err = self.cli("collect", "--timeout", "0")
        self.assertNotIn("still working", out)
        self.assertEqual(code, 0, out + err)

    def test_young_done_without_working_is_collected(self):
        d = self.start("w1", [rec("done")])
        self.report(d, "w1")
        t = time.time()
        code, out, err = self.cli("collect", "--timeout", "2")
        self.assertEqual(code, 0, out + err)
        self.assertIn("state: done", out)


if __name__ == "__main__":
    unittest.main()
