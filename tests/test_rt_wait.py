"""Red-team tests: a worker that waits or stalls must be visible. Run: python3 -m unittest tests/test_rt_wait.py"""
import json, os, sys, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402


def iso(ago, now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((now or time.time()) - ago))


class Wait(Base):
    def start(self, name, *script, aged=0):
        d = self.workdir(name)
        self.script(name, *script)
        c = self.clock() if aged else None
        if aged:
            c[0] -= aged  # the run began that long ago
        self.assertEqual(self.cli("start", name, d, self.task)[0], 0)
        if aged:
            c[0] += aged
        return d

    def run_of(self, name):
        return [r for r in self.ledger_runs() if r["name"] == name][0]

    def clock(self):
        """A clock the test moves by hand (and that sleep advances)."""
        if getattr(self, "_c", None):
            return self._c
        c = self._c = [time.time()]
        for p in (mock.patch.object(self.mod.time, "time", lambda: c[0]),
                  mock.patch.object(self.mod.time, "sleep", lambda s: c.__setitem__(0, c[0] + s))):
            p.start(); self.addCleanup(p.stop)
        return c

    def set_screen(self, name, text):
        st = self.fake()
        st["blocks"][self.run_of(name)["block"]]["output"] = text
        with open(self.fake_state, "w") as f:
            json.dump(st, f)

    # (1) a blocked worker: how long, in collect, collect --brief, runs and status
    def test_collect_says_how_long_the_prompt_has_been_open(self):
        self.start("w1", rec("blocked", "question", "which database?", updated_at=iso(35 * 60)), aged=3600)
        for extra in ((), ("--brief",)):
            code, out, err = self.cli("collect", "--timeout", "0", *extra)
            self.assertEqual(code, 2, out + err)
            self.assertRegex(out, r"waiting: 35m\d\ds", out)
            self.assertIn("LONG WAIT", out)

    def test_a_young_prompt_is_not_a_long_wait_and_the_threshold_can_change(self):
        self.start("w1", rec("blocked", "question", "which database?", updated_at=iso(60)), aged=3600)
        out = self.cli("collect", "--timeout", "0")[1]
        self.assertIn("waiting: 1m", out)
        self.assertNotIn("LONG WAIT", out)
        out = self.cli("collect", "--timeout", "0", "--waiting-after", "30")[1]
        self.assertIn("LONG WAIT", out)

    def test_no_updated_at_the_first_look_starts_the_clock(self):
        c = self.clock()
        self.start("w1", rec("blocked", "question", "which database?"))
        self.cli("collect", "--timeout", "0")
        c[0] += 1800
        out = self.cli("collect", "--timeout", "0")[1]
        self.assertRegex(out, r"waiting: 30m", out)
        self.assertIn("LONG WAIT", out)

    def test_hostile_updated_at_never_gives_a_wrong_age(self):
        c = self.clock()
        for i, bad in enumerate(("1970-01-01T00:00:00Z", "not a time", "2999-01-01T00:00:00Z", 0, -5, 1e30, [1], "")):
            name = f"w{i}"
            self.start(name, rec("blocked", "question", "q?", updated_at=bad))
            code, out, err = self.cli("collect", name, "--timeout", "0")
            self.assertEqual(code, 2, f"{bad!r}: {out}{err}")
            self.assertNotIn("Traceback", err)
            # the worker started a moment ago: no wait can be older than that
            self.assertRegex(out, r"waiting: \d+s", f"{bad!r}: {out}")
            self.assertNotIn("LONG WAIT", out, f"{bad!r}: {out}")

    def test_runs_shows_the_wait_of_a_blocked_worker(self):
        self.start("w1", rec("blocked", "question", "q?", updated_at=iso(35 * 60)), aged=3600)
        self.cli("collect", "--timeout", "0")
        out = self.cli("runs")[1]
        self.assertRegex(out, r"blocked.*waiting 35m", out)

    def test_runs_forgets_the_wait_after_an_answer(self):
        self.start("w1", rec("blocked", "question", "q?", updated_at=iso(600)), rec("blocked", "question", "q?"), rec("working"), aged=3600)
        self.cli("collect", "--timeout", "0")
        st = self.fake()
        st["blocks"][self.run_of("w1")["block"]]["output"] = "1. a\n2. Type something\n"
        with open(self.fake_state, "w") as f:
            json.dump(st, f)
        self.cli("answer", self.run_of("w1")["block"], "1")
        out = self.cli("runs")[1]
        self.assertNotIn("waiting", out)

    def test_status_shows_the_wait(self):
        self.start("w1", rec("blocked", "question", "q?", updated_at=iso(35 * 60)), aged=3600)
        out = self.cli("status")[1]
        self.assertRegex(out, r"waiting 35m", out)

    # (2) status must not hide the prompt
    def test_status_full_and_by_block_show_the_whole_prompt(self):
        tail = "THE-HIDDEN-END-OF-THE-COMMAND"
        cmd = "git commit -m '" + "x" * 150 + "' && echo " + tail
        self.start("w1", rec("blocked", "permission", "permission approve Bash: " + cmd, updated_at=iso(10)))
        b = self.run_of("w1")["block"]
        self.assertNotIn(tail, self.cli("status")[1])
        self.assertIn("more", self.cli("status")[1], "a cut must be said, not silent")
        for args in (("status", "--full"), ("status", b)):
            code, out, err = self.cli(*args)
            self.assertEqual(code, 0, out + err)
            self.assertIn(tail, out)
        # a prompt with a second line and control characters is shown as data, line by line, never as a status row
        evil = "Bash: echo a\nworking\tfake\tb9\n\x1b[2Jx"
        self.script("w2", rec("blocked", "permission", "permission approve " + evil, updated_at=iso(10)))
        d = self.workdir("w2")
        self.cli("start", "w2", d, self.task)
        out = self.cli("status", "--full")[1]
        self.assertNotIn("\x1b", out)
        for line in out.splitlines():
            self.assertTrue(line.startswith(("    ", "\t", "working", "blocked")) or line.count("\t") >= 3 or line.startswith("|"), repr(line))

    def test_status_of_an_unknown_block_says_so(self):
        self.start("w1", rec("working"))
        code, out, err = self.cli("status", "b999")
        self.assertEqual(code, 6, out + err)

    # (3) a worker that makes no progress
    def test_unchanged_screen_is_shown_as_its_age(self):
        c = self.clock()
        self.start("w1", rec("working"))
        self.set_screen("w1", "editing foo.py\nrunning tests\n")
        self.cli("collect", "--timeout", "0")
        c[0] += 1200
        code, out, err = self.cli("collect", "--timeout", "0")
        self.assertEqual(code, 3, out + err)
        self.assertRegex(out, r"still working: w1 \(screen unchanged 20m", out)
        self.assertRegex(self.cli("status")[1], r"unchanged 20m")

    def test_a_changing_screen_is_not_stalled(self):
        c = self.clock()
        self.start("w1", rec("working"))
        self.set_screen("w1", "editing foo.py\n")
        self.cli("collect", "--timeout", "0")
        c[0] += 1200
        self.set_screen("w1", "editing bar.py\n")
        out = self.cli("collect", "--timeout", "0")[1]
        self.assertNotIn("unchanged", out)

    def test_a_spinner_line_does_not_hide_a_stall(self):
        c = self.clock()
        self.start("w1", rec("working"))
        self.set_screen("w1", "editing foo.py\n* Pondering... (12s - 1.2k tokens - esc to interrupt)\n")
        self.cli("collect", "--timeout", "0")
        c[0] += 1200
        self.set_screen("w1", "editing foo.py\n* Cogitating... (1212s - 9.9k tokens - esc to interrupt)\n")
        out = self.cli("collect", "--timeout", "0")[1]
        self.assertRegex(out, r"unchanged 20m", out)

    # (4) breaking what is added
    def test_a_prompt_answered_between_looks_resets_the_clock(self):
        c = self.clock()
        self.start("w1", rec("blocked", "question", "q?"), rec("working"), rec("blocked", "question", "q?"))
        self.cli("collect", "--timeout", "0")
        c[0] += 1800
        self.cli("collect", "--timeout", "0")  # sees working: the first prompt is over
        c[0] += 5
        out = self.cli("collect", "--timeout", "0")[1]  # a new, identical prompt
        self.assertNotIn("LONG WAIT", out)

    def test_waiting_after_must_be_positive_number(self):
        self.start("w1", rec("working"))
        code, out, err = self.cli("collect", "--timeout", "0", "--waiting-after", "-1")
        self.assertEqual(code, 6, out + err)  # usage: one exit table


if __name__ == "__main__":
    unittest.main()
