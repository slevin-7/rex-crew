"""Guards around every key rex-crew types (24.11.2026, ideas from rex-orchestrator): only panes it started, an
emergency stop, the agent must still be in front, no message the agent would take as something else, no Return into
a dialog or before the input box shows the text. Run: python3 -m unittest tests/test_rt_guards.py"""
import json, os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402

SHELL_IN_FRONT = {"child": {"pid": 1, "name": "login"}, "foreground": {"pid": 77, "name": "-zsh"}, "last_exit": None}


class Guards(Base):
    def fake_set(self, key, label, value):
        st = self.fake()
        st.setdefault(key, {})[label] = value
        with open(self.fake_state, "w") as f:
            json.dump(st, f)

    def started(self, *records, name="w1", screen=None, proc=None):
        self.script(name, *records)
        if screen is not None:
            self.fake_set("screens", name, screen)
        if proc is not None:
            self.fake_set("procs", name, proc)
        code, out, err = self.cli("start", name, self.workdir(name), self.task)
        self.assertEqual(code, 0, out + err)
        return self.block_of(name)

    def keys(self, b):
        return [s["value"] for s in self.fake()["sent"] if s["block"] == b]

    # -- whose pane ---------------------------------------------------------------------------------------
    def test_keys_only_into_panes_rex_crew_started(self):
        for cmd in (("send", "b77", "hi"), ("approve", "b77"), ("answer", "b77", "1"), ("deny", "b77"), ("close", "b77")):
            code, out, err = self.cli(*cmd)
            self.assertEqual(code, 6, f"{cmd}: {out}{err}")
            self.assertIn("not started by rex-crew", err)
        self.assertEqual(self.sent(), [])

    def test_force_reaches_a_pane_the_user_named(self):
        st = self.fake()
        st["blocks"]["b77"] = {"label": "mine", "output": "", "closed": False, "session": "s1", "script": [rec("idle")]}
        with open(self.fake_state, "w") as f:
            json.dump(st, f)
        code, out, err = self.cli("send", "--force", "b77", "please", "look")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.keys("b77"), ["please look", "enter"])

    # -- emergency stop -----------------------------------------------------------------------------------
    def test_stop_file_stops_typing_starting_and_approving(self):
        b = self.started(rec("idle"))
        open(os.path.join(self.tmp.name, "stop"), "w").close()
        code, out, err = self.cli("send", b, "next")
        self.assertEqual(code, 2, out + err)
        self.assertIn("stopped", err)
        code, out, err = self.cli("start", "w2", self.workdir("w2"), self.task)
        self.assertEqual(code, 2, out + err)
        self.assertNotIn("w2", [r["name"] for r in self.ledger_runs()])
        self.assertEqual(self.keys(b), [])

    def test_stop_file_keeps_collect_from_approving_but_deny_still_works(self):
        b = self.started(rec("blocked", "permission", "approve Bash: git status"))
        open(os.path.join(self.tmp.name, "stop"), "w").close()
        code, out, err = self.cli("collect", "--policy", self.policy([r"^Bash: git status$"]), "--timeout", "0")
        self.assertEqual(code, 2, out + err)
        self.assertNotIn("1", self.keys(b))
        self.assertEqual(self.cli("deny", b)[0], 0)
        self.assertEqual(self.keys(b), ["escape"])

    # -- the agent is gone, the record stays -------------------------------------------------------------
    def test_no_text_into_a_shell_that_took_over_the_pane(self):
        b = self.started(rec("idle"), proc=SHELL_IN_FRONT)
        code, out, err = self.cli("send", b, "rm", "-rf", "build")
        self.assertEqual(code, 5, out + err)
        self.assertIn("shell", err)
        self.assertEqual(self.keys(b), [])

    def test_an_exited_program_or_another_owner_is_gone_too(self):
        b = self.started(rec("idle"), proc={"child": None, "foreground": None, "last_exit": 0})
        self.assertEqual(self.cli("send", b, "next")[0], 5)
        b3 = self.started(rec("idle"), name="w3", proc={"last_exit": None, "home": "/Users/x"})  # what a real host said after /exit
        self.assertEqual(self.cli("send", b3, "next")[0], 5)
        self.assertEqual(self.keys(b3), [])
        b2 = self.started(rec("idle", owner={"pid": 100}), name="w2")  # the fake's default agent in front has pid 4242
        code, out, err = self.cli("send", b2, "next")
        self.assertEqual(code, 5, out + err)
        self.assertEqual(self.keys(b) + self.keys(b2), [])

    def test_no_approval_for_a_leftover_prompt(self):
        b = self.started(rec("blocked", "permission", "approve Bash: git status"), proc=SHELL_IN_FRONT)
        code, out, err = self.cli("approve", b)
        self.assertEqual(code, 5, out + err)
        code, out, err = self.cli("collect", "--policy", self.policy([r"^Bash: git status$"]), "--timeout", "0")
        self.assertEqual(self.keys(b), [])

    # -- what may be typed --------------------------------------------------------------------------------
    def test_messages_the_agent_would_take_as_something_else_are_refused(self):
        b = self.started(rec("idle"))
        for text in ("!rm -rf /", "  !ls", "2 tests are red", "/permissions", "/per", "/ADD-DIR /"):
            code, out, err = self.cli("send", b, text)
            self.assertEqual(code, 6, f"{text}: {out}{err}")
            self.assertIn("reword", err)
        self.assertEqual(self.keys(b), [])
        self.assertEqual(self.cli("send", b, "/review the parser")[0], 0)  # not a command that widens permissions

    def test_a_codex_worker_may_get_a_leading_digit(self):
        self.script("cx", rec("idle", app="codex"))
        self.assertEqual(self.cli("start", "cx", self.workdir("cx"), self.task, "--agent", "codex")[0], 0)
        code, out, err = self.cli("send", self.block_of("cx"), "2 tests are red")
        self.assertEqual(code, 0, out + err)

    # -- dialogs and the input box ------------------------------------------------------------------------
    def test_no_return_into_a_dialog_the_status_does_not_show(self):
        dialog = "⏺ Bash(rm -rf build)\n" + "─" * 40 + "\n Do you want to proceed?\n ❯ 1. Yes\n   2. No\n Esc to cancel\n"
        b = self.started(rec("idle"), screen=dialog)
        code, out, err = self.cli("send", b, "next")
        self.assertEqual(code, 2, out + err)
        self.assertIn("dialog", err)
        self.assertEqual(self.keys(b), [])

    def test_a_list_in_the_transcript_is_no_dialog(self):
        screen = "⏺ Two options:\n ❯ 1. first\n   2. second\n" + "─" * 40 + "\n❯ \n" + "─" * 40 + "\n  ⏵⏵ auto mode on\n"
        b = self.started(rec("idle"), screen=screen)
        code, out, err = self.cli("send", b, "take", "the", "first")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.keys(b), ["take the first", "enter"])

    def test_no_return_when_the_input_box_does_not_show_the_text(self):
        b = self.started(rec("idle"))
        with mock.patch.dict(os.environ, {"FAKE_REX_NO_ECHO": "1"}):
            code, out, err = self.cli("send", b, "write", "the", "report")
        self.assertEqual(code, 2, out + err)
        self.assertIn("not submitted", err)
        self.assertEqual(self.keys(b), ["write the report"])  # typed, never sent with Return

    def test_unit_checks(self):
        m = self.mod
        self.assertTrue(m.shown("x\n" + "─" * 20 + "\n❯ [Pasted text #1 +40 lines]\n" + "─" * 20, "a" * 9000))
        self.assertTrue(m.shown("❯ Schreib den Bericht in die\n  Datei report.md", "Schreib den Bericht in die Datei report.md"))
        self.assertFalse(m.shown("❯ ", "Schreib den Bericht"))
        self.assertTrue(m.shown("❯ ", "äöü ß"))  # nothing to compare: not a reason to block
        self.assertIsNone(m.risky("fix the 2 failing tests"))
        self.assertIsNone(m.risky("2 tests are red", claude=False))

    def test_session_from_rex_block_when_rex_session_is_gone(self):
        # Rex server preview.23 sets only REX_BLOCK in new terminals; rex whoami names the session of that block
        b = self.started(rec("idle"))
        with mock.patch.dict(os.environ, {"REX_BLOCK": "block:orch", "FAKE_REX_WHOAMI_SESSION": "s1"}):
            os.environ.pop("REX_CREW_SESSION"); os.environ.pop("REX_SESSION", None)
            code, out, err = self.cli("send", b, "next", "step")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.keys(b), ["next step", "enter"])
        with mock.patch.dict(os.environ, {"REX_BLOCK": "block:orch"}):
            os.environ.pop("REX_CREW_SESSION"); os.environ.pop("REX_SESSION", None)
            code, out, err = self.cli("status")
        self.assertEqual(code, 6, out + err)  # rex cannot say: the old message, no guess
        self.assertIn("no session", err)


    # -- a worker named instead of its pane (25.11.2026: `send w-144-rest ...` was refused after fanout) ---------------
    def test_send_and_approve_take_the_workers_name(self):
        b = self.started(rec("idle"), name="w-144-rest")
        code, out, err = self.cli("send", "w-144-rest", "weiter")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.keys(b), ["weiter", "enter"])
        code, out, err = self.cli("send", "w-unknown", "weiter")
        self.assertEqual(code, 6, out + err)

    # -- approve presses the one-time Yes, wherever the dialog puts it (24.11.2026) ----------------------------------
    READ_OUTSIDE = ("Read file\n❯ 1. Yes, and keep allowing any reads outside the working directories\n"
                    "  2. Yes, allow reading from Downloads/ during this session\n  3. No\n  4. Yes, but ask again next time\n")

    def test_approve_picks_the_one_time_yes_not_option_1(self):
        b = self.started(rec("blocked", "permission", "approve Read: /tmp/x"), screen=self.READ_OUTSIDE)
        code, out, err = self.cli("approve", b)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.keys(b), ["4"])

    def test_approve_presses_nothing_when_only_lasting_yes_is_offered(self):
        b = self.started(rec("blocked", "permission", "approve Read: /tmp/x"),
                         screen="❯ 1. Yes, and keep allowing reads\n  2. No\n")
        code, out, err = self.cli("approve", b)
        self.assertNotEqual(code, 0, out + err)
        self.assertEqual(self.keys(b), [])


if __name__ == "__main__":
    unittest.main()
