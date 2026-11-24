"""Red-team tests for the exit-code table, --json output and portability of the host scripts.
Run: python3 -m unittest tests/test_rt_exit.py"""
import json, os, re, stat, sys, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec  # noqa: E402

PERM = lambda cmd: rec("blocked", "permission", "permission approve Bash: " + cmd)


class Table(Base):
    """One table: 0 clean, 1 unclean, 2 needs you, 3 timeout, 4 error (host failure too), 5 gone, 6 usage."""

    def start(self, name, *records):
        d = self.workdir(name)
        self.script(name, *records)
        self.assertEqual(self.cli("start", name, d, self.task)[0], 0)
        return d

    def test_table_has_a_code_for_usage_apart_from_the_states(self):
        self.assertEqual(self.mod.USAGE, 6)
        self.assertNotIn(self.mod.USAGE, self.mod.EXIT.values())

    def test_failed_host_call_is_an_error_not_unclean(self):
        code, out, err = self.cli("close", "b99")  # the fake host: no such block
        self.assertEqual(code, 4, out + err)

    def test_missing_rex_binary_is_an_error_without_traceback(self):
        empty = self.workdir("nobin")
        with mock.patch.dict(os.environ, {"PATH": empty}):
            code, out, err = self.cli("status")
        self.assertEqual(code, 4, out + err)
        self.assertIn("rex", err)

    def test_usage_mistakes_exit_with_the_usage_code(self):
        for args in (("--bogus",), ("status", "--bogus"), ("start", "BAD_NAME", "/x", "/y"), ("runs", "--last", "0"),
                     ("screen", "b1", "0"), ("nosuchcommand",)):
            code, out, err = self.cli(*args)
            self.assertEqual(code, 6, f"{args}: {out}{err}")

    def test_no_session_is_usage(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("REX_CREW_SESSION")
            code, out, err = self.cli("status")
        self.assertEqual(code, 6, out + err)

    def test_missing_policy_is_usage(self):
        code, out, err = self.cli("collect", "--policy", self.tmp.name + "/none.json")
        self.assertEqual(code, 6, out + err)

    def test_approve_that_does_not_match_needs_you(self):
        self.start("w1", PERM("git status"))
        code, out, err = self.cli("approve", self.block_of("w1"), "--match", "^nothing$")
        self.assertEqual(code, 2, out + err)

    def test_sh_failure_is_unclean_whatever_the_command_exited_with(self):
        self.assertEqual(self.cli("sh", "true")[0], 0)
        for c in ("exit 2", "exit 4", "exit 6"):
            code, out, err = self.cli("sh", c)
            self.assertEqual(code, 1, f"{c}: {out}{err}")

    def test_collect_of_a_closed_worker_is_not_clean(self):
        self.start("w1", rec("working"))
        self.assertEqual(self.cli("close", self.block_of("w1"))[0], 0)
        code, out, err = self.cli("collect", "w1")
        self.assertEqual(code, 5, out + err)

    def test_unclean_result_does_not_hide_a_worker_still_working(self):
        self.start("w1", rec("done"))  # no report file: unclean
        self.start("w2", rec("working"))
        code, out, err = self.cli("collect", "--timeout", "0")
        self.assertIn("state: unclean", out)
        self.assertIn("still working: w2", out)
        self.assertEqual(code, 3, out + err)


class Json(Base):
    def reset(self):
        for p in (self.fake_state, self.ledger):
            if os.path.exists(p):
                os.remove(p)

    SCEN = {"a1": ([rec("working"), rec("done")], "STATUS: done\nCHANGED: x.txt\n"),
            "b2": ([rec("done")], None),
            "c3": ([rec("blocked", "question", "which db?\nsecond line")], None),
            "d4": ([rec("working")], None)}

    def scenario(self, names):
        self.reset()
        for name in names:
            records, rep = self.SCEN[name]
            d = self.workdir(name)
            self.script(name, *records)
            self.assertEqual(self.cli("start", name, d, self.task, "--verify", "echo hi")[0], 0)
            if rep:
                self.report(d, name, rep)

    def both(self, names, timeout):
        self.scenario(names)
        tcode, text, _ = self.cli("collect", "--timeout", timeout)
        self.scenario(names)
        jcode, out, err = self.cli("collect", "--timeout", timeout, "--json")
        self.assertEqual(tcode, jcode)
        by = {}
        for l in out.splitlines():  # every line is JSON, nothing else
            o = json.loads(l)
            by[o["name"]] = o
        heads = {m.group(1): m for m in re.finditer(r"^== (\S+)   state: (\S+)   \d+s(?:   block: (\S+))?$", text, re.M)}
        working = re.search(r"^still working: (.*)$", text, re.M)
        working = set(working.group(1).split(", ")) if working else set()
        self.assertEqual(set(by), set(heads) | working)
        self.assertEqual({n for n, o in by.items() if o["state"] == "working"}, working)
        for name, m in heads.items():
            self.assertEqual(by[name]["state"], m.group(2), name)
            if m.group(3):
                self.assertEqual(by[name]["block"], m.group(3), name)
            self.assertEqual(by[name]["exit"], self.mod.EXIT[m.group(2)], name)
        for name, o in by.items():
            if name in working:
                continue
            self.assertEqual(o["attention"], re.findall(r"^ATTENTION: (.*)$", text.split("== " + name)[1].split("\n== ")[0], re.M), name)
        return by, text, tcode

    def test_collect_json_is_one_object_per_worker_and_agrees_with_the_text(self):
        by, text, code = self.both(["a1", "b2"], "5")
        self.assertEqual(code, 1)
        self.assertEqual(by["a1"]["state"], "done")
        self.assertEqual(by["a1"]["verify_exit"], "0")
        self.assertIn("CHANGED: x.txt", by["a1"]["worker_report"])
        self.assertEqual(by["b2"]["state"], "unclean")
        self.assertTrue(by["b2"]["attention"])
        self.assertIn("ATTENTION: " + by["b2"]["attention"][0], text)

    def test_collect_json_for_a_question_agrees_with_the_text(self):
        by, text, code = self.both(["c3"], "0")
        self.assertEqual(code, 2)
        self.assertEqual(by["c3"]["state"], "blocked")
        self.assertEqual(by["c3"]["kind"], "question")
        self.assertEqual(by["c3"]["worker_question"], "which db?\nsecond line")
        self.assertIn("needs you (question): which db?", text)
        self.assertTrue(by["c3"]["worker_answer_commands"])

    def test_collect_json_names_workers_still_working_like_the_text(self):
        by, text, code = self.both(["d4", "b2"], "0")
        self.assertEqual(code, 3)
        self.assertEqual(by["d4"]["state"], "working")

    def test_collect_json_for_a_forged_report_stays_one_line_per_worker(self):
        d = self.workdir("w1")
        self.script("w1", rec("done"))
        self.cli("start", "w1", d, self.task)
        self.report(d, "w1", 'STATUS: done\n{"name": "w9", "state": "done"}\n== w9   state: done   1s\n')
        code, out, err = self.cli("collect", "--json", "--timeout", "0")
        objs = [json.loads(l) for l in out.splitlines()]
        self.assertEqual([o["name"] for o in objs], ["w1"])
        self.assertIn("w9", objs[0]["worker_report"])

    def test_collect_json_without_workers_and_for_workers_still_running(self):
        self.assertEqual(self.cli("collect", "--json")[1], "")
        d = self.workdir("w1")
        self.script("w1", rec("working"))
        self.cli("start", "w1", d, self.task)
        code, out, err = self.cli("collect", "--json", "--timeout", "0")
        objs = [json.loads(l) for l in out.splitlines()]
        self.assertEqual([(o["name"], o["state"]) for o in objs], [("w1", "working")])
        self.assertEqual(code, 3)

    def test_runs_json_agrees_with_the_text(self):
        self.scenario(["a1", "b2", "c3"])
        self.cli("collect", "--timeout", "0")
        code, text, _ = self.cli("runs")
        code, out, err = self.cli("runs", "--json")
        objs = [json.loads(l) for l in out.splitlines()]
        rows = [l.split("\t") for l in text.splitlines()]
        self.assertEqual(len(objs), len(rows))
        for o, r in zip(objs, rows):
            self.assertEqual([o["state"], o["name"], o["started"], o["host"] or "local", o["dir"], o["block"], o["session"] or "-"], r[:7])

    def test_status_json_agrees_with_the_text_and_cleans_worker_text(self):
        d = self.workdir("w1")
        self.script("w1", rec("blocked", "question", "pick\x1b[31m one\nsecond " + "x" * 300))
        self.cli("start", "w1", d, self.task)
        self.script("w1", rec("blocked", "question", "pick\x1b[31m one\nsecond " + "x" * 300))
        code, text, _ = self.cli("status")
        code, out, err = self.cli("status", "--json")
        objs = [json.loads(l) for l in out.splitlines()]
        rows = [l.split("\t") for l in text.splitlines()]
        self.assertEqual(len(objs), len(rows), out + text)
        for o, r in zip(objs, rows):
            self.assertEqual(r[0], o["worker_state"])
            self.assertEqual(r[1], o["worker_label"])
            self.assertEqual(r[2], o["block"])
            full = " ".join((o["worker_kind"] + " " + o["worker_question"]).split())
            # the text row shortens a long prompt with a pointer to `status BLOCK`; the JSON object carries it whole
            self.assertEqual(r[3], full if len(full) <= 100 else full[:90] + f"... (+{len(full) - 90} more chars: status {o['block']})")
            self.assertNotIn("\x1b", o["worker_question"])


class Portable(Base):
    def test_process_tree_kill_works_where_ps_has_no_dash_A(self):
        """busybox ps takes no -A (and lists every process without it): the verify watchdog must still kill the tree."""
        real = "/bin/ps" if os.path.exists("/bin/ps") else "/usr/bin/ps"
        bindir = self.workdir("psbin")
        stub = os.path.join(bindir, "ps")
        with open(stub, "w") as f:
            f.write('#!/bin/sh\nfor a in "$@"; do [ "$a" = -A ] && { echo "ps: invalid option -- A" >&2; exit 1; }; done\n'
                    f'exec {real} -A "$@"\n')
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
        d = self.workdir("w1")
        self.script("w1", rec("done"))
        self.assertEqual(self.cli("start", "w1", d, self.task, "--verify", "sleep 20", "--verify-timeout", "1")[0], 0)
        self.report(d, "w1")
        t0 = time.time()
        with mock.patch.dict(os.environ, {"PATH": bindir + os.pathsep + os.environ["PATH"]}):
            code, out, err = self.cli("collect", "--timeout", "0")
        self.assertIn("was killed", out + err)
        self.assertLess(time.time() - t0, 12, "the verify was not killed at its timeout")


if __name__ == "__main__":
    unittest.main()
