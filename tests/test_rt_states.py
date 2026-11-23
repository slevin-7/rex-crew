"""Red-team tests for the wait logic (tick, trust, collect, show, exit codes). Run: python3 -m unittest tests/test_rt_states.py"""
import json, sys, time, unittest
from unittest import mock

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402


class States(Base):
    def start(self, name, script, d=None, *extra):
        d = d or self.workdir(name)
        self.script(name, *script)
        self.assertEqual(self.cli("start", name, d, self.task, *extra)[0], 0)
        return d

    def age(self, name, seconds):
        """Pretend the worker was started `seconds` ago."""
        runs = self.ledger_runs()
        for r in runs:
            if r["name"] == name:
                r["t0"] = time.time() - seconds
        with open(self.ledger, "w") as f:
            json.dump(runs, f)

    def sleeps(self):
        calls = []
        self.mod.time.sleep = lambda s: calls.append(s)  # restored by Base's patcher
        return calls

    # -- unknown names -------------------------------------------------------------------------
    def test_collect_unknown_name_is_an_error_not_success(self):
        self.start("w1", [rec("working")])
        code, out, err = self.cli("collect", "w1x", "--timeout", "0")
        self.assertNotEqual(code, 0, out + err)
        self.assertIn("w1x", out + err)

    def test_collect_finished_name_reports_its_state_not_success(self):
        d = self.start("w1", [rec("working"), rec("done")])
        self.cli("collect")  # no report file: unclean
        code, out, err = self.cli("collect", "w1")
        self.assertEqual(code, 1, out + err)
        self.assertIn("unclean", out + err)

    # -- startup heuristics --------------------------------------------------------------------
    def test_fast_worker_done_after_20s_is_not_a_startup_block(self):
        d = self.start("w1", [rec("done")])
        self.report(d, "w1")
        self.age("w1", 20)  # finished between two collects; we never saw it working
        code, out, err = self.cli("collect", "--timeout", "1")
        self.assertNotEqual(code, 2, out)
        self.assertNotIn("startup", out)
        self.assertNotIn("state: blocked", out)

    def test_unknown_state_is_not_reported_as_startup_dialog(self):
        self.start("w1", [rec("starting")])
        self.age("w1", 20)
        code, out, _ = self.cli("collect", "--timeout", "1")
        self.assertNotIn("startup", out)

    # -- timeout 0 -----------------------------------------------------------------------------
    def test_timeout_zero_still_looks_once(self):
        d = self.start("w1", [rec("done")])
        self.report(d, "w1")
        self.age("w1", 60)
        code, out, err = self.cli("collect", "--timeout", "0")
        self.assertEqual(code, 0, out + err)
        self.assertIn("state: done", out)

    # -- several workers ----------------------------------------------------------------------
    def test_blocked_plus_error_in_one_pass_hands_back_at_once(self):
        self.start("w1", [rec("working"), rec("blocked", "auth", "login expired")])
        self.start("w2", [rec("working"), rec("error", msg="boom")])
        self.start("w3", [rec("working")])
        calls = self.sleeps()
        code, out, _ = self.cli("collect", "--timeout", "5")
        self.assertIn("needs you (auth): login expired", out)
        self.assertIn("boom", out)
        self.assertIn("still working: w3", out)
        self.assertEqual(calls[1:], [], "kept waiting although a worker needs a person")
        self.assertIn(code, (2, 4))

    def test_several_workers_ending_in_one_pass_are_all_reported(self):
        for n in ("w1", "w2", "w3"):
            d = self.start(n, [rec("working"), rec("done")])
            self.report(d, n, f"STATUS: done\nCHANGED: {n}\n")
        code, out, err = self.cli("collect")
        self.assertEqual(code, 0, out + err)
        for n in ("w1", "w2", "w3"):
            self.assertIn(f"CHANGED: {n}", out)

    # -- double key press ----------------------------------------------------------------------
    def test_prompt_that_stays_after_the_key_is_not_pressed_again_at_once(self):
        prompt = rec("blocked", "permission", "approve Bash: git status")
        self.start("w1", [rec("working"), prompt])
        self.cli("collect", "--policy", self.policy([r"^Bash: git status"]), "--timeout", "1")
        self.assertEqual(len(self.sent("1")), 1)

    def test_trust_dialog_that_stays_is_not_pressed_twice_at_once(self):
        d = self.workdir("t")
        self.start("w1", [rec("blocked", "permission", "Do you trust this folder?")], d, "--trust")
        st = self.fake()
        st["blocks"][self.block_of("w1")]["output"] = f"Do you trust the files in {d}?\n1. Yes\n2. No\n"
        with open(self.fake_state, "w") as f:
            json.dump(st, f)
        self.cli("collect", "--timeout", "1")
        self.assertEqual(len(self.sent("enter")), 1)

    # -- flaky host / hostile records -----------------------------------------------------------
    def test_one_failed_status_call_is_not_gone(self):
        d = self.start("w1", [rec("working"), rec("done")])
        self.report(d, "w1")
        real, n = self.mod.Rex.status, []
        def flaky(rex, block):
            n.append(1)
            return {"state": "gone"} if len(n) == 2 else real(rex, block)
        with mock.patch.object(self.mod.Rex, "status", flaky):
            code, out, err = self.cli("collect")
        self.assertEqual(code, 0, out + err)
        self.assertIn("state: done", out)

    def test_error_then_recovery_can_be_collected_again(self):
        d = self.start("w1", [rec("working"), rec("error", msg="overloaded"), rec("working"), rec("done")])
        self.report(d, "w1")
        self.assertEqual(self.cli("collect")[0], 4)
        code, out, err = self.cli("collect")
        self.assertEqual(code, 0, out + err)
        self.assertIn("state: done", out)

    def test_non_string_msg_does_not_crash_collect(self):
        self.start("w1", [rec("working"), {"state": "blocked", "kind": "permission", "msg": 42}])
        code, out, err = self.cli("collect", "--policy", self.policy([r"."]))
        self.assertEqual(code, 2, out + err)
        self.assertNotIn("Traceback", err)

    def test_record_without_kind_or_msg_blocks_with_a_readable_line(self):
        self.start("w1", [rec("working"), rec("blocked")])
        code, out, _ = self.cli("collect")
        self.assertEqual(code, 2)
        self.assertNotIn("needs you ():", out)


if __name__ == "__main__":
    unittest.main()
