"""Codex workers: the facts observed on a real host (codex-cli 0.166.0) as fixtures. Run: python3 -m unittest tests/test_rt_codex.py"""
import contextlib, json, os, sys, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402


def folder_dialog(d):
    return f"Folder access\n{d}\nTrust this folder?\n› 1. Trust and continue\n  2. Back to Agent Command Center\n"


HOOKS_DIALOG = ("Hooks need review\n2 hooks are new or changed.\n"
                "› 1. Review hooks\n  2. Trust all and continue\n  3. Continue without trusting (hooks won't run)\n")


def cx(state, kind=None, msg=None, **kw):
    return rec(state, kind, msg, app="codex", **kw)


class Codex(Base):
    def start(self, name, script=(), d=None, *extra):
        d = d or self.workdir(name)
        self.script(name, *script)
        self.assertEqual(self.cli("start", name, d, self.task, "--agent", "codex", *extra)[0], 0)
        return d

    def set_output(self, name, text):
        st = self.fake()
        st["blocks"][self.block_of(name)]["output"] = text
        with open(self.fake_state, "w") as f:
            json.dump(st, f)

    def edit_run(self, name, **fields):
        runs = self.ledger_runs()
        for r in runs:
            if r["name"] == name:
                r.update(fields)
        with open(self.ledger, "w") as f:
            json.dump(runs, f)

    def old(self, name, seconds=60):
        """The worker started `seconds` ago, and we pressed a key that long ago."""
        r = [r for r in self.ledger_runs() if r["name"] == name][-1]
        self.edit_run(name, t0=time.time() - seconds, **({"startup_at": r["startup_at"] - seconds} if "startup_at" in r else {}))

    def hidden_codex(self):
        """No codex anywhere the host looks: not on PATH, not in the directories rex-crew adds (a real one may be installed here)."""
        os.remove(os.path.join(self.tmp.name, "bin", "codex"))
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(self.mod, "HOST_PATH", os.path.join(self.tmp.name, "none")))
        stack.enter_context(mock.patch.dict(os.environ, {"PATH": os.path.join(self.tmp.name, "bin") + ":/usr/bin:/bin"}))
        return stack

    def keys(self):
        return [(s["kind"], s["value"]) for s in self.sent()]

    def prog(self, name):
        return self.fake()["blocks"][self.block_of(name)]["prog"][-1]


class Launch(Codex):
    def test_codex_is_started_with_the_prompt_as_its_first_message(self):
        self.start("w1")
        line = self.prog("w1")
        self.assertIn("exec codex 'Read .rex-crew/task-w1.md in this directory and carry it out completely.'", line)
        self.assertNotIn("claude", line)

    def test_claude_stays_the_default(self):
        d = self.workdir()
        self.script("w1", rec("working"))
        self.cli("start", "w1", d, self.task)
        self.assertIn("exec claude 'Read", self.prog("w1"))
        self.assertEqual(self.ledger_runs()[-1]["agent"], "claude")

    def test_arguments_after_the_dashes_go_to_codex_and_the_prompt_stays_last(self):
        d = self.workdir()
        self.script("w1")
        self.cli("start", "w1", d, self.task, "--agent", "codex", "--", "--model", "o3", "--image", "a b.png")
        line = self.prog("w1")
        self.assertRegex(line, r"exec codex --model o3 --image 'a b.png' -- 'Read .*completely\.'$")

    def test_ledger_runs_and_status_show_the_agent(self):
        self.start("w1", [cx("working")])
        self.assertEqual(self.ledger_runs()[-1]["agent"], "codex")
        self.assertIn("codex", self.cli("runs")[1].strip().split("\t"))
        o = json.loads(self.cli("runs", "--json")[1].splitlines()[0])
        self.assertEqual(o["agent"], "codex")
        out = self.cli("status")[1]
        self.assertIn("app codex", out)
        self.assertEqual(json.loads(self.cli("status", "--json")[1].splitlines()[0])["worker_app"], "codex")

    def test_unknown_agent_is_a_usage_error_and_creates_nothing(self):
        d = self.workdir()
        code, out, err = self.cli("start", "w1", d, self.task, "--agent", "gemini")
        self.assertEqual(code, 6, out + err)
        self.assertIn("gemini", err)
        self.assertEqual(self.fake()["blocks"], {})
        self.assertFalse(os.path.exists(self.ledger))
        self.assertFalse(os.path.exists(f"{d}/.rex-crew"))

    def test_unknown_agent_reaching_start_directly_is_refused_before_anything_is_created(self):
        d = self.workdir()
        code, out, err = self.cli("run", "w1", d, self.task, "--agent", "codex", "--codex-hooks", "maybe")
        self.assertEqual(code, 6, out + err)
        code, out, err = self.cli("start", "w1", d, self.task, "--codex-hooks", "skip")  # claude: nothing to decide
        self.assertEqual(code, 6, out + err)
        self.assertEqual(self.fake()["blocks"], {})

    def test_codex_missing_on_the_host_says_so_and_leaves_nothing(self):
        d = self.workdir()
        with self.hidden_codex():
            code, out, err = self.cli("start", "w1", d, self.task, "--agent", "codex")
        self.assertEqual(code, 4, out + err)
        self.assertIn("codex is not installed", err)
        self.assertFalse(os.path.exists(f"{d}/.rex-crew/task-w1.md"))
        self.assertNotIn("w1", [r["name"] for r in self.ledger_runs() if r["state"] != "gone"])
        self.assertEqual([b for b in self.fake()["blocks"].values() if b["label"] == "w1"], [])
        # and a second try is not blocked by a leftover claim
        self.assertEqual(self.cli("start", "w1", d, self.task)[0], 0)


class Fanout(Codex):
    def plan(self, **kw):
        repo = os.path.join(self.tmp.name, "repo")
        if not os.path.isdir(repo):
            os.makedirs(repo)
            for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                      ["commit", "-q", "--allow-empty", "-m", "i"]):
                __import__("subprocess").run(["git", "-C", repo, *c], check=True)
        p = {"repo": repo, "workers": [{"name": "a", "task": "do a"}, {"name": "b", "task": "do b"}], **kw}
        path = os.path.join(self.tmp.name, "plan.json")
        with open(path, "w") as f:
            json.dump(p, f)
        return path

    def test_plan_agent_and_a_worker_that_overrides_it(self):
        path = self.plan(agent="codex", workers=[{"name": "a", "task": "x"}, {"name": "b", "task": "y", "agent": "claude"}])
        self.script("a"); self.script("b")
        code, out, err = self.cli("fanout", path)
        self.assertEqual(code, 0, out + err)
        self.assertEqual({r["name"]: r["agent"] for r in self.ledger_runs()}, {"a": "codex", "b": "claude"})
        self.assertIn("exec codex", self.prog("a"))
        self.assertIn("exec claude", self.prog("b"))

    def test_agent_args_and_the_old_claude_args_name(self):
        for key in ("agent_args", "claude_args"):
            self.setUp()
            path = self.plan(**{key: ["--permission-mode", "acceptEdits"]})
            self.script("a"); self.script("b")
            self.assertEqual(self.cli("fanout", path)[0], 0)
            self.assertIn("acceptEdits", self.prog("a"))

    def test_the_plans_arguments_are_not_given_to_a_worker_of_another_agent(self):
        path = self.plan(agent_args=["--permission-mode", "acceptEdits"],
                         workers=[{"name": "a", "task": "x"}, {"name": "b", "task": "y", "agent": "codex"},
                                  {"name": "c", "task": "z", "agent": "codex", "agent_args": ["--model", "o3"]}])
        for n in "abc":
            self.script(n)
        self.assertEqual(self.cli("fanout", path)[0], 0)
        self.assertIn("acceptEdits", self.prog("a"))
        self.assertNotIn("acceptEdits", self.prog("b"))
        self.assertIn("--model o3 --", self.prog("c"))

    def refused(self, **kw):
        path = self.plan(**kw)
        code, out, err = self.cli("fanout", path)
        self.assertEqual(code, 6, out + err)
        self.assertEqual(self.fake()["blocks"], {}, "nothing may be created")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, ".rex-crew-worktrees")))
        return err

    def test_wrong_agent_name_in_a_plan_is_refused_before_anything_is_created(self):
        self.assertIn("gemini", self.refused(agent="gemini"))
        self.assertIn("worker b", self.refused(workers=[{"name": "a", "task": "x"}, {"name": "b", "task": "y", "agent": "Codex"}]))
        self.assertIn("agent", self.refused(agent=["codex"]))

    def test_both_argument_names_at_once_is_refused(self):
        self.assertIn("agent_args", self.refused(agent_args=["-x"], claude_args=["-y"]))

    def test_claude_args_for_a_codex_plan_is_refused(self):
        self.assertIn("claude_args", self.refused(agent="codex", claude_args=["-y"]))

    def test_bad_codex_hooks_value_is_refused(self):
        self.assertIn("codex_hooks", self.refused(codex_hooks="yes"))

    def test_codex_missing_on_the_host_stops_the_plan_before_any_checkout(self):
        path = self.plan(agent="codex")
        with self.hidden_codex():
            code, out, err = self.cli("fanout", path)
        self.assertEqual(code, 6, out + err)
        self.assertIn("codex is not installed", err)
        self.assertEqual(self.fake()["blocks"].keys() - {k for k, b in self.fake()["blocks"].items() if b["label"] == "crew-sh"}, set())

    def test_codex_hooks_decision_reaches_only_the_codex_workers(self):
        path = self.plan(codex_hooks="skip", workers=[{"name": "a", "task": "x", "agent": "codex"}, {"name": "b", "task": "y"}])
        self.script("a"); self.script("b")
        self.assertEqual(self.cli("fanout", path)[0], 0)
        self.assertEqual({r["name"]: r["codex_hooks"] for r in self.ledger_runs()}, {"a": "skip", "b": None})


class Startup(Codex):
    def collect(self, *extra):
        return self.cli("collect", "--timeout", "1", *extra)

    def test_no_record_and_no_dialog_yet_is_just_waiting(self):
        self.start("w1")
        code, out, err = self.collect()
        self.assertEqual(code, 3, out + err)  # still working: young, nothing to press
        self.assertEqual(self.keys(), [])

    def test_folder_dialog_with_trust_is_confirmed_with_enter_alone(self):
        d = self.start("w1", [], None, "--trust")
        self.set_output("w1", folder_dialog(d))
        self.old("w1", 20)
        code, out, err = self.collect()
        self.assertEqual(self.keys(), [("key", "enter")], out + err)  # no Down: the pre-selected option is the right one
        self.assertEqual(code, 3, out + err)
        self.assertEqual(self.ledger_runs()[-1]["approved"], [f"folder trust: {d}"])

    def test_the_same_dialog_is_not_pressed_twice_within_ten_seconds(self):
        d = self.start("w1", [], None, "--trust")
        self.set_output("w1", folder_dialog(d))
        self.old("w1", 20)
        self.collect(); self.old("w1", 0); self.collect()
        self.assertEqual(self.keys(), [("key", "enter")])

    def test_folder_dialog_without_trust_goes_back_with_screen_and_choices(self):
        d = self.start("w1")
        self.set_output("w1", folder_dialog(d))
        self.old("w1", 20)
        code, out, err = self.collect()
        self.assertEqual(code, 2, out + err)
        self.assertEqual(self.keys(), [])
        self.assertIn("needs you (startup)", out)
        self.assertIn("Trust this folder?", out)
        self.assertRegex(out, r"answer b\d+ 1 ")
        self.assertRegex(out, r"answer b\d+ 2 ")

    def test_folder_dialog_naming_another_directory_is_never_confirmed(self):
        d = self.start("w1", [], None, "--trust")
        for other in (d + "2", d + "/sub", "/elsewhere" + d, os.path.dirname(d), "/somewhere/else"):
            self.set_output("w1", folder_dialog(other))
            self.old("w1", 20)
            code, out, err = self.collect()
            self.assertEqual(self.keys(), [], other)
            self.assertEqual(code, 2, out + err)
            self.edit_run("w1", state="open")

    def test_folder_dialog_with_the_path_wrapped_over_two_lines_still_counts(self):
        d = self.start("w1", [], None, "--trust")
        self.set_output("w1", folder_dialog(d[:12] + "\n" + d[12:]))
        self.old("w1", 20)
        self.collect()
        self.assertEqual(self.keys(), [("key", "enter")])

    def test_folder_dialog_of_a_symlinked_directory_is_confirmed_by_its_real_path(self):
        real = self.workdir("real")
        link = os.path.join(self.tmp.name, "link")
        os.symlink(real, link)
        self.start("w1", [], link, "--trust")
        self.set_output("w1", folder_dialog(os.path.realpath(real)))
        self.old("w1", 20)
        self.collect()
        self.assertEqual(self.keys(), [("key", "enter")])

    def test_hooks_dialog_is_never_answered_by_default(self):
        d = self.start("w1", [], None, "--trust")
        self.set_output("w1", HOOKS_DIALOG)
        self.old("w1", 20)
        code, out, err = self.collect()
        self.assertEqual(code, 2, out + err)
        self.assertEqual(self.keys(), [])
        self.assertIn("needs you (startup)", out)
        self.assertIn("Hooks need review", out)
        self.assertIn("3. Continue without trusting", out)
        for n in "123":
            self.assertRegex(out, rf"answer b\d+ {n} ")
        self.assertIn("screen b", out)
        self.assertIn("close b", out)

    def test_when_both_dialogs_are_on_screen_the_one_drawn_last_is_the_open_one(self):
        d = self.start("w1", [], None, "--trust", "--codex-hooks", "skip")
        self.set_output("w1", folder_dialog(d) + HOOKS_DIALOG)  # the folder dialog is stale text above the hooks dialog
        self.old("w1", 20)
        self.collect()
        self.assertEqual(self.keys(), [("text", "3")])  # not Enter: that would pick "Review hooks"
        self.setUp()
        d = self.start("w1", [], None, "--trust", "--codex-hooks", "skip")
        self.set_output("w1", HOOKS_DIALOG + folder_dialog(d))
        self.old("w1", 20)
        self.collect()
        self.assertEqual(self.keys(), [("key", "enter")])

    def test_hooks_dialog_with_an_advance_decision(self):
        for decision, digit in (("skip", "3"), ("trust", "2")):
            self.setUp()
            self.start("w1", [], None, "--codex-hooks", decision)
            self.set_output("w1", HOOKS_DIALOG)
            self.old("w1", 20)
            code, out, err = self.collect()
            self.assertEqual(self.keys(), [("text", digit)], decision + out + err)
            self.assertIn(f"codex hooks dialog: {decision}", self.ledger_runs()[-1]["approved"][0])

    def test_hooks_dialog_twice_with_ask_is_handed_back_each_time(self):
        self.start("w1")
        for _ in range(2):
            self.set_output("w1", HOOKS_DIALOG)
            self.old("w1", 20)
            code, out, err = self.collect()
            self.assertEqual(code, 2, out + err)
            self.edit_run("w1", state="open")
        self.assertEqual(self.keys(), [])

    def test_hooks_dialog_twice_with_skip_is_pressed_again_but_only_three_times(self):
        self.start("w1", [], None, "--codex-hooks", "skip")
        outs = []
        for _ in range(5):
            self.set_output("w1", HOOKS_DIALOG)
            self.old("w1", 20)
            outs.append(self.collect())
        self.assertEqual(self.keys(), [("text", "3")] * 3)
        self.assertEqual(outs[3][0], 2, outs[3])  # the dialog stays: a person looks
        self.assertIn("needs you (startup)", outs[3][1])

    def test_a_person_chooses_with_answer_during_the_startup_dialog(self):
        self.start("w1")
        self.set_output("w1", HOOKS_DIALOG)
        self.old("w1", 20)
        self.collect()
        b = self.block_of("w1")
        code, out, err = self.cli("answer", b, "3")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.keys(), [("text", "3")])  # the digit and nothing else: no Return
        self.assertEqual(self.ledger_runs()[-1]["state"], "open")
        code, out, err = self.collect()  # young again: no second dialog report straight away
        self.assertEqual(code, 3, out + err)

    def test_answer_refuses_a_number_the_screen_does_not_offer_and_free_text(self):
        self.start("w1")
        self.set_output("w1", HOOKS_DIALOG)
        b = self.block_of("w1")
        for args in (("7",), ("3", "--text"), ("yes",)):
            code, out, err = self.cli("answer", b, *args)
            self.assertEqual(code, 6, out + err)
        self.assertEqual(self.keys(), [])

    def test_answer_without_a_record_only_works_for_a_live_worker_of_this_session(self):
        d = self.start("w1")
        self.set_output("w1", HOOKS_DIALOG)
        st = self.fake()
        st["blocks"]["b99"] = {"label": "x", "output": HOOKS_DIALOG, "closed": False, "session": "s1", "script": []}
        with open(self.fake_state, "w") as f:
            json.dump(st, f)
        code, out, err = self.cli("answer", "b99", "2")  # a pane this ledger never started
        self.assertEqual(code, 6, out + err)
        self.assertEqual(self.keys(), [])

    def test_unknown_screen_without_a_record_goes_back_to_a_person(self):
        self.start("w1", [], None, "--trust", "--codex-hooks", "skip")
        self.set_output("w1", "Something new\n1. Do it\n2. Not that\n")
        self.old("w1", 20)
        code, out, err = self.collect()
        self.assertEqual(code, 2, out + err)
        self.assertEqual(self.keys(), [])
        self.assertIn("Something new", out)

    def test_claude_trust_is_not_used_for_codex(self):
        # a codex record that says blocked with the claude dialog text must not get Down+Enter
        d = self.start("w1", [cx("blocked", "permission", "trust this folder")], None, "--trust")
        self.set_output("w1", f"Do you trust the files in {d}?\n1. Yes, proceed\n2. No, exit\n")
        code, out, err = self.collect()
        self.assertEqual(self.keys(), [])
        self.assertEqual(code, 2, out + err)


class Turn(Codex):
    def test_working_then_done_with_report_brief_and_json(self):
        d = self.start("w1", [cx("working"), cx("working"), cx("done")], None, "--verify", "echo verified")
        self.report(d, "w1", "STATUS: done\nCHANGED: a.txt: added\nOPEN: nothing\n")
        code, out, err = self.cli("collect", "--brief")
        self.assertEqual(code, 0, out + err)
        self.assertIn("CHANGED: a.txt: added", out)
        self.assertIn("1 done", out)
        self.assertTrue(self.fake()["blocks"][self.block_of("w1")]["closed"])

    def test_json_names_the_agent(self):
        d = self.start("w1", [cx("working"), cx("done")])
        self.report(d, "w1")
        code, out, err = self.cli("collect", "--json")
        o = json.loads(out.splitlines()[0])
        self.assertEqual((code, o["state"], o["agent"]), (0, "done", "codex"), out + err)
        self.assertIn("added", o["worker_report"] + "added")

    def test_done_without_report_is_unclean_as_for_claude(self):
        self.start("w1", [cx("working"), cx("done")])
        code, out, err = self.cli("collect")
        self.assertEqual(code, 1, out + err)
        self.assertIn("wrote no report file", out)

    def test_stalled_and_waiting_times_work(self):
        self.start("w1", [cx("working")])
        self.edit_run("w1", scr=["sig", time.time() - 1800, time.time()])
        code, out, err = self.cli("collect", "--timeout", "0")
        self.assertIn("screen unchanged", out)

    def test_a_permission_prompt_is_never_approved_whatever_the_policy_says(self):
        prompt = cx("blocked", "permission", "approve Bash: git status --short")
        self.start("w1", [prompt, prompt])
        self.set_output("w1", "Allow command?\n  git status --short\n1. Yes\n2. No\n")
        code, out, err = self.cli("collect", "--policy", self.policy([".*", "^Bash: git status"]), "--timeout", "1")
        self.assertEqual(self.keys(), [])
        self.assertEqual(code, 2, out + err)
        self.assertIn("needs you (permission)", out)
        self.assertIn("Allow command?", out)  # the screen comes with it
        self.assertNotIn(" approve b", out)  # and no copy-paste line that would press for it
        self.assertIn("screen b", out)

    def test_the_record_decides_too_a_claude_started_pane_that_reports_codex(self):
        d = self.workdir("w1")
        prompt = cx("blocked", "permission", "approve Bash: git status --short")
        self.script("w1", prompt, prompt)
        self.cli("start", "w1", d, self.task)  # started as claude, but the record says codex
        code, out, err = self.cli("collect", "--policy", self.policy([".*"]), "--timeout", "1")
        self.assertEqual(self.keys(), [])
        self.assertEqual(code, 2, out + err)

    def test_manual_approve_is_refused_for_codex(self):
        prompt = cx("blocked", "permission", "approve Bash: git status --short")
        self.start("w1", [prompt, prompt])
        code, out, err = self.cli("approve", self.block_of("w1"))
        self.assertEqual(code, 6, out + err)
        self.assertEqual(self.keys(), [])

    def test_claude_workers_are_still_auto_approved(self):
        d = self.workdir("w2")
        prompt = rec("blocked", "permission", "approve Bash: git status --short", app="claude-code")
        self.script("w2", prompt, prompt, rec("working"), rec("done"))
        self.cli("start", "w2", d, self.task)
        self.report(d, "w2")
        code, out, err = self.cli("collect", "--policy", self.policy([r"^Bash: git status"]))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.sent("1")), 1)


if __name__ == "__main__":
    unittest.main()
