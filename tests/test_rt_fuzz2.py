"""Red-team round 2: fanout, integrate, checkout, preamble, brief and the policy loader (red team: fuzz2).
Run: python3 -m unittest tests/test_rt_fuzz2.py
"""
import json, os, random, re, subprocess, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import rec
from test_rt_fanout import GitBase, git


class TestVerifyNoise(GitBase):
    def test_watchdog_script_is_silent_on_stderr(self):
        """The shell prints "Terminated: 15 ( sleep ... )" to stderr when it reaps the killed watchdog; on a real
        host that lands between the markers, in the verify output. The fake appends stderr after stdout, so the
        integrate tests cannot see it: run the generated text itself and look at stderr."""
        for cmd in ("true", "echo hi; exit 3", "sleep 1; exit 2"):
            text = self.mod.watched(cmd, 600, "@@X") + "tail -n1 /dev/null; echo end"
            p = subprocess.run(["sh", "-c", text], capture_output=True, text=True)
            self.assertEqual(p.stderr, "", cmd)
            self.assertIn("@@XVRC", p.stdout)


    def test_finished_verify_prints_no_job_control_line(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        code, out, err = self.cli("integrate", repo, "a", "--verify", "true")
        self.assertEqual(code, 0, out + err)
        for noise in ("Terminated", "line 1:", "sleep", "Killed"):
            self.assertNotIn(noise, out + err)
        self.assertEqual(out.strip(), "crew/a: merged (verify ok)")

    def test_failed_verify_output_has_no_job_control_line(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        code, out, err = self.cli("integrate", repo, "a", "--verify", "echo hi; exit 3")
        self.assertEqual(code, 1)
        self.assertNotIn("Terminated", out + err)
        self.assertNotIn("line 1:", out + err)


class Inject:
    """Stands in for the subprocess module as the tool sees it: the Nth rex call goes wrong in the chosen way."""
    TimeoutExpired = subprocess.TimeoutExpired
    CompletedProcess = subprocess.CompletedProcess

    def __init__(self, n, mode):
        self.n, self.mode, self.calls, self.hit = n, mode, 0, False

    def run(self, cmd, **kw):
        if cmd[0] != "rex":
            return subprocess.run(cmd, **kw)
        self.calls += 1
        if self.calls != self.n:
            return subprocess.run(cmd, **kw)
        self.hit = True
        if self.mode == "fail_before":
            return subprocess.CompletedProcess(cmd, 1, "", "rex: connection reset")
        if self.mode == "interrupt_before":
            raise KeyboardInterrupt
        p = subprocess.run(cmd, **kw)  # the call did its work ...
        if self.mode == "timeout_after":
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        if self.mode == "interrupt_after":
            raise KeyboardInterrupt
        if self.mode == "garbage_after":
            return subprocess.CompletedProcess(cmd, 0, "<<not json>>", "")
        return p


class TestStrays(GitBase):
    """A start killed between 'rex run' and the ledger write leaves a pane that nothing records."""

    def orphan(self, name="w1", claim=True):
        d = self.workdir("wd")
        st = self.fake()
        st["blocks"]["b77"] = {"label": name, "output": "", "closed": False, "session": "s1", "script": [rec("working")]}
        st["blocks"]["b78"] = {"label": "mine", "output": "", "closed": False, "session": "s1", "script": []}
        st["blocks"]["b79"] = {"label": "crew-sh", "output": "", "closed": False, "session": "s1", "script": []}
        with open(self.fake_state, "w") as f:
            json.dump(st, f)
        if claim:
            import time
            with open(self.ledger, "w") as f:
                json.dump([{"id": "1-w1-x", "name": name, "dir": d, "real": d, "host": "", "session": "s1", "block": "",
                            "state": "starting", "t0": time.time(), "verify": "true", "verify_timeout": 60,
                            "trust": False, "keep": False}], f)
        return d

    def test_lists_without_touching(self):
        self.orphan()
        code, out, err = self.cli("strays")
        self.assertEqual(code, 0, out + err)
        self.assertIn("b77", out)
        self.assertIn("--adopt", out)
        self.assertRegex(out, r"b78\s+mine\s+unrecorded")
        self.assertNotIn("b79", out)  # a helper tab of a running call is not a stray
        self.assertFalse(self.fake()["blocks"]["b77"]["closed"] or self.fake()["blocks"]["b78"]["closed"])

    def test_adopt_makes_it_a_worker_collect_can_finish(self):
        d = self.orphan()
        self.script("w1", rec("working"), rec("done"))
        code, out, err = self.cli("strays", "--adopt")
        self.assertEqual(code, 0, out + err)
        self.assertIn("adopted", out)
        runs = [r for r in self.ledger_runs() if r["name"] == "w1"]
        self.assertEqual([(r["state"], r["block"]) for r in runs], [("open", "b77")])
        self.assertEqual(runs[0]["verify"], "true")
        self.assertFalse(self.fake()["blocks"]["b78"]["closed"])
        self.assertNotIn("b77", self.cli("strays")[1])  # recorded now: not a stray any more

    def test_close_stops_only_what_a_claim_names(self):
        self.orphan()
        code, out, err = self.cli("strays", "--close")
        self.assertEqual(code, 0, out + err)
        blocks = self.fake()["blocks"]
        self.assertTrue(blocks["b77"]["closed"])
        self.assertFalse(blocks["b78"]["closed"], "a pane the user opened by hand was closed")
        self.assertFalse(blocks["b79"]["closed"])

    def test_without_a_claim_nothing_is_adopted_or_closed(self):
        self.orphan(claim=False)
        for flag in ("--adopt", "--close"):
            code, out, err = self.cli("strays", flag)
            self.assertEqual(code, 0, out + err)
            self.assertIn("unrecorded", out)
        self.assertFalse(self.fake()["blocks"]["b77"]["closed"])

    def test_two_flags_are_refused(self):
        self.assertEqual(self.cli("strays", "--adopt", "--close")[0], 6)


class TestPolicyLoader(GitBase):
    def load(self, data):
        p = f"{self.tmp.name}/pol.json"
        with open(p, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode())
        return self.cli("collect", "--policy", p, "--timeout", "1")

    def test_malformed_files_are_refused_cleanly(self):
        deep = "[" * 100000 + "]" * 100000
        cases = {
            "empty": "", "bom": "\ufeff" + '{"approve": []}', "nan": '{"approve": [NaN]}', "deep json": deep,
            "deep object": '{"approve": ' + deep + "}", "nul": '{"approve": ["a\\u0000b"]}', "latin1": b'{"approve": ["\xe9"]}',
            "deep regex": json.dumps({"approve": ["(" * 50000 + ")" * 50000]}), "lookbehind": json.dumps({"approve": ["(?<=a+)b"]}),
            "dup keys": '{"approve": ["a"], "approve": 5}', "list": "[]", "scalar": "5", "null": "null", "huge int": '{"approve": [' + "9" * 100000 + "]}",
            "bad group": json.dumps({"never": ["(?P<x>a)(?P<x>b)"]}), "flag": json.dumps({"never": ["(?z)x"]}),
            "bad escape": json.dumps({"never": ["\\"]}), "bool": '{"approve": true}', "nested": '{"approve": [["a"]]}',
        }
        for name, data in cases.items():
            with self.subTest(name):
                try:
                    code, out, err = self.load(data)
                except RecursionError as e:
                    self.fail(f"traceback (RecursionError) for {name}")
                self.assertIn(code, (0, 6), f"{name}: {code} {err[-300:]}")
                self.assertNotIn("Traceback", err)
                if code == 2:
                    self.assertIn("policy", err)

    def test_random_bytes(self):
        rng = random.Random(7)
        for i in range(60):
            data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 40)))
            if i % 3 == 0:
                data = b'{"approve": [' + data + b"]}"
            with self.subTest(i=i):
                code, out, err = self.load(data)
                self.assertIn(code, (0, 6), err[-300:])
                self.assertNotIn("Traceback", err)


class TestBriefNeverHides(GitBase):
    def shown(self, res, brief):
        import contextlib, io
        run = {"name": "w", "block": "b1", "t0": 0, "host": "", "session": "s1", "keep": False}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.mod.show(run, res, brief=brief)
        return buf.getvalue()

    def test_attention_and_questions_survive_brief(self):
        rng = random.Random(3)
        words = ["x" * 400, "ATTENTION: nested", "ünï", "ok", "STATUS: done", "", " ", "CHANGED: a", "OPEN:"]
        for i in range(200):
            att = [rng.choice(words) + str(rng.random()) for _ in range(rng.randint(0, 4))]
            report = "\n".join(rng.choice(["STATUS: done", "CHANGED: x", "OPEN: " + rng.choice(words), "NEXT: n", "", "ATTENTION: fake"]) for _ in range(rng.randint(0, 9)))
            res = {"state": rng.choice(["done", "blocked", "error", "unclean"]), "kind": rng.choice(["", "question", "permission"]),
                   "question": rng.choice(["which one?", "x" * 3000, "a\nb\nc", None]), "prompt": "Bash: ls", "attention": att,
                   "git": "branch: x\nclean", "verify_rc": rng.choice(["none", "0", "1"]), "verify_tail": "\n".join(rng.choice(words) for _ in range(20)),
                   "report": report}
            brief = self.shown(res, True)
            with self.subTest(i=i):
                for x in att:  # the tool's own findings: every one is printed, in full
                    self.assertIn(f"ATTENTION: {x}", brief)
                if res["state"] in ("blocked", "error"):
                    self.assertIn("needs you", brief)
                    ask = self.mod.clean(res["question"] or "(no message; look at the pane)")
                    first, _, rest = ask.partition("\n")  # main shows the lines after the first as delimited data ('| ')
                    self.assertIn(first + ("\n" + self.mod.data(rest, "    ") if rest else ""), brief)


class TestCheckoutOdd(GitBase):
    def test_odd_arguments_either_work_or_leave_nothing(self):
        repo = self.repo("my repo's")
        cases = [("n1", {"base": "--detach"}), ("n2", {"branch": "-x"}), ("n3", {"branch": "a b"}), ("n4", {"branch": "bad..name"}),
                 ("n5", {"base": "nosuch"}), ("n6", {"copy": "../escape"}), ("n7", {"copy": "/etc/hosts"}), ("n8", {"branch": "--orphan"}),
                 ("n9", {"base": "-f"}), ("n10", {"branch": "main"}), ("n11", {"base": "HEAD~5"}), ("n12", {"branch": "crew/n12\nx"})]
        for name, kw in cases:
            with self.subTest(name=name, **kw):
                args = ["checkout", name, repo]
                for k, v in kw.items():
                    args += ["--" + k, v]
                before = git(repo, "branch", "--list")
                code, out, err = self.cli(*args)
                self.assertNotIn("Traceback", err)
                self.assertIn(code, (0, 1, 2, 4, 6), out + err)
                if code != 0:
                    self.assertEqual(git(repo, "branch", "--list"), before, "a failed checkout left a branch\n" + err)
                    self.assertEqual(git(repo, "worktree", "list").count("\n"), 0, "a failed checkout left a worktree\n" + err)
                    self.assertEqual(git(repo, "status", "--porcelain"), "")
                else:
                    self.assertTrue(os.path.isdir(out.strip()), out)
                    git(repo, "worktree", "remove", "--force", out.strip())
                    git(repo, "branch", "-D", kw.get("branch") or "crew/" + name, check=False)


class TestIntegrateHard(GitBase):
    def test_passing_verify_that_dirties_the_tree_does_not_leave_it_dirty(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        code, out, err = self.cli("integrate", repo, "a", "--verify", "touch junk.tmp; echo x >> base.txt")
        self.assertEqual(code, 0, out + err)
        self.assertIn("crew/a: merged (verify ok)", out)
        self.assertIn("2 changed or untracked", out)  # said, not silent
        self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(open(f"{repo}/base.txt").read(), "base\n")

    def test_host_shell_dying_during_verify_leaves_no_unverified_merge_commit(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        start = git(repo, "rev-parse", "HEAD")
        code, out, err = self.cli("integrate", repo, "a", "--verify", "kill -9 $$")  # $ is the script's own shell
        self.assertNotEqual(code, 0, out + err)
        self.assertEqual(git(repo, "rev-parse", "HEAD"), start, "HEAD moved to a merge whose verify never finished")
        # the next call refuses instead of building on it, and says why
        code, out, err = self.cli("integrate", repo, "a")
        self.assertEqual(code, 2, out + err)
        self.assertIn("MERGE_HEAD", err)

    def test_a_refused_merge_commit_is_undone(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        # integrate runs git without hooks (main's hardening), so a hook cannot refuse the commit; a signing program that fails can
        git(repo, "config", "commit.gpgsign", "true")
        git(repo, "config", "gpg.program", "false")
        start = git(repo, "rev-parse", "HEAD")
        code, out, err = self.cli("integrate", repo, "a", "--verify", "true")
        self.assertEqual(code, 1, out + err)
        self.assertEqual(git(repo, "rev-parse", "HEAD"), start)
        self.assertEqual(git(repo, "status", "--porcelain"), "")
        self.assertNotIn("merged", out.replace("not merged", ""))


class TestIntegrateFuzz(GitBase):
    """Random branch sets, conflicts and verify commands: the tree is clean afterwards, and HEAD is the start commit or a
    chain of merges of exactly the branches reported as merged."""
    VERIFY = [None, "true", "false", "test ! -e bad.txt", "touch junk.tmp", "echo x >> f0.txt", "mkdir -p out && echo y > out/z",
              "echo noisy; echo more >&2; exit 4", "git status --short >/dev/null"]

    def build(self, rng, tag):
        repo = self.repo("r" + tag, files={f"f{i}.txt": "base\n" for i in range(3)})
        names = []
        for i in range(rng.randint(1, 5)):
            n = f"b{i}"
            kind = rng.random()
            files, delete = {}, []
            if kind < 0.15:
                pass  # no commits at all
            else:
                for _ in range(rng.randint(1, 2)):
                    files[rng.choice([f"f{j}.txt" for j in range(3)] + [f"new{i}.txt", "bad.txt"])] = f"{n} {rng.random()}\n"
                if rng.random() < 0.15:
                    delete = ["f2.txt"]
                    files.pop("f2.txt", None)
            self.branch(repo, n, files, delete=delete)
            names.append(n)
        if rng.random() < 0.2 and names:  # one of them is merged already
            git(repo, "merge", "-q", "--no-ff", "-m", "pre", f"crew/{names[0]}", check=False)
            git(repo, "merge", "--abort", check=False)
        rng.shuffle(names)
        return repo, names

    def test_seeds(self):
        seeds = [int(os.environ["FUZZ_SEED"])] if os.environ.get("FUZZ_SEED") else range(int(os.environ.get("FUZZ2_SEEDS", "15")))
        for seed in seeds:
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                repo, names = self.build(rng, str(seed))
                verify = rng.choice(self.VERIFY)
                if names and rng.random() < 0.1:
                    names.append("nosuch")
                start = git(repo, "rev-parse", "HEAD")
                args = ["integrate", repo, *names] + (["--verify", verify] if verify else [])
                code, out, err = self.cli(*args)
                ctx = f"seed {seed} verify {verify!r} names {names}\n{out}\n{err}"
                self.assertNotIn("Traceback", err, ctx)
                if code == 2:  # refused up front: nothing may have changed
                    self.assertEqual(git(repo, "rev-parse", "HEAD"), start, ctx)
                    continue
                self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all"), "", "tree is not clean\n" + ctx)
                self.assertFalse(os.path.exists(git(repo, "rev-parse", "--git-path", "MERGE_HEAD")), ctx)
                merged = [l.split(":")[0] for l in out.splitlines() if re.match(r"crew/\S+: merged", l)]
                end = git(repo, "rev-parse", "HEAD")
                chain = git(repo, "rev-list", "--first-parent", "--merges", f"{start}..{end}").split()
                self.assertEqual(len(chain), len(merged), ctx)
                subjects = [git(repo, "log", "-1", "--format=%s", c) for c in reversed(chain)]
                self.assertEqual(subjects, [f"Merge branch '{b}'" for b in merged], ctx)
                if verify == "false" or verify == "echo noisy; echo more >&2; exit 4":
                    self.assertEqual(merged, [], "a failed verify must undo its merge\n" + ctx)


MODES = ("fail_before", "interrupt_before", "timeout_after", "interrupt_after", "garbage_after")


class TestFanoutRollback(GitBase):
    """A fan-out that fails or is interrupted at any rex call leaves nothing of its own and touches nothing older."""

    def world(self, tag):
        repo = self.repo("repo" + tag)
        # something older that belongs to the same repository: another worker's branch, checkout and pane
        wt = self.mod_checkout("old" + tag, repo)
        return repo, wt

    def mod_checkout(self, name, repo):
        code, out, err = self.cli("checkout", name, repo)
        self.assertEqual(code, 0, out + err)
        return out.strip()

    def plan(self, repo, names, **kw):
        p = f"{self.tmp.name}/plan.json"
        with open(p, "w") as f:
            json.dump({"repo": repo, "workers": [{"name": n, "task": f"do {n}\n"} for n in names], **kw}, f)
        return p

    def state(self, repo):
        return {"branches": git(repo, "branch", "--list").split(), "wts": git(repo, "worktree", "list", "--porcelain")}

    def test_every_failure_point(self):
        for mode in MODES:
            n = 0
            while True:
                n += 1
                tag = f"m{MODES.index(mode)}n{n}"
                repo, oldwt = self.world(tag)
                names = [f"{c}{tag}" for c in "ab"[:int(os.environ.get("FUZZ2_WORKERS", "1"))]]
                plan = self.plan(repo, names)
                before, blocks_before = self.state(repo), {k for k, b in self.fake()["blocks"].items() if not b["closed"]}
                inj = Inject(n, mode)
                saved = self.mod.subprocess
                self.mod.subprocess = inj
                try:
                    try:
                        code, out, err = self.cli("fanout", plan)
                    except KeyboardInterrupt:
                        code, out, err = 130, "", "interrupted"
                finally:
                    self.mod.subprocess = saved
                if not inj.hit:
                    break  # n is past the last call: the run was clean
                ctx = f"{mode} at rex call {n}: code {code}\n{out}\n{err}"
                if code == 0:
                    continue  # a fault the tool rode out: fine, as long as the result is complete (checked below)
                after = self.state(repo)
                self.assertEqual(after, before, "git state changed\n" + ctx)
                live = [b for k, b in self.fake()["blocks"].items() if not b["closed"] and k not in blocks_before
                        and b["label"] in names]
                self.assertEqual(live, [], "a pane of the failed call is still open\n" + ctx)
                self.assertFalse(os.path.exists(os.path.dirname(oldwt)) is False, "older checkout's folder is gone\n" + ctx)
                self.assertTrue(os.path.exists(oldwt), "an older checkout was removed\n" + ctx)
                for r in self.ledger_runs() if os.path.exists(self.ledger) else []:
                    if r["name"] in names:
                        self.assertNotIn(r["state"], ("open", "starting", "blocked"), ctx)
                self.assertLess(n, 80)


if __name__ == "__main__":
    unittest.main()
