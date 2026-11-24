"""Randomized stress of rex-crew against tests/fake_rex (red team: fuzz).

A seeded generator builds worker lifecycles (random status records, some malformed; panes that vanish; rex calls that
fail, time out or answer garbage; reports and verify commands of every kind; several workers; a second collect or a
send/answer/close arriving in the middle of one) and checks invariants that must always hold:
  1. no Python traceback reaches the user
  2. a key is pressed only for a prompt the policy approves at that moment (or by an explicit user command), and not
     twice for one prompt within ten seconds
  3. exit status 0 only if every collected worker is clean
  4. every started pane is in the ledger or closed
  5. the ledger stays valid JSON and keeps every live worker
  6. collect returns within its timeout plus a small margin
Time is virtual (a worker's sleep costs nothing) and the fake runs in-process, so a few hundred seeds take seconds.
Run: python3 -m unittest tests/test_rt_fuzz.py     One seed: FUZZ_SEED=17 python3 -m unittest tests/test_rt_fuzz.py
"""
import contextlib, importlib.machinery, importlib.util, io, json, os, random, re, subprocess, sys, types, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_rex_crew import Base, rec, FAKE  # noqa: E402

APPROVED = ["Bash: git status", "Bash: git log -1"]  # the oracle: exactly these two are routine under POLICY
UNAPPROVED = ["Bash: git push", "Bash: git status; rm -rf x", "Bash: git status $(rm x)", "Write: /etc/passwd", "Bash: ls",
              "Bash: git status > /tmp/x", "Bash: cd /tmp && git status", "Bash: git status\nBash: rm y", "Bash: git statusx"]
POLICY = {"approve": [r"^Bash: git (status|log)\b(?!x)(?![^\s;&|])"], "never": [r"git push", r"\brm\b"]}
TIMEOUT_RPC = 3  # REX_CREW_TIMEOUT in the fuzz


class Clock:
    """Virtual time: sleep() moves it, nothing waits."""
    def __init__(self):
        self.t = 1.8e9
    def time(self):
        return self.t
    def sleep(self, s):
        self.t += s
    def __getattr__(self, n):
        return getattr(__import__("time"), n)


def load_fake():
    loader = importlib.machinery.SourceFileLoader("fake_rex_in_process", FAKE)
    m = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    with mock.patch.dict(os.environ, {"FAKE_REX_STATE": "/nonexistent"}):
        loader.exec_module(m)
    return m


class Fuzz(Base):
    """One seed = one setUp/tearDown pair; all state is rebuilt per seed."""

    def begin(self, seed):
        self.seed, self.rng = seed, random.Random(seed)
        self.viol, self.events, self.actor, self.snap, self.calls = [], [], [], {}, 0
        self.warned = set()
        self.clock = Clock()
        self.mod.time = self.clock
        self.fakemod = load_fake()
        self.fakemod.PATH = self.fake_state
        self.rate = self.rng.choice([0, 0, 0.02, 0.06, 0.15])
        self.nested = None  # (call number, op) fired once inside the next outer op
        self.workers = {}
        os.environ["REX_CREW_TIMEOUT"] = str(TIMEOUT_RPC)
        shim = types.SimpleNamespace(run=self.rex_run, TimeoutExpired=subprocess.TimeoutExpired,
                                     CompletedProcess=subprocess.CompletedProcess)
        self.mod.subprocess = shim

    # -- the fake host, in-process, with injected faults -------------------------------------------------
    def fake_call(self, argv):
        out, err, rc = io.StringIO(), io.StringIO(), 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                self.fakemod.dispatch(argv)
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        return rc, out.getvalue(), err.getvalue()

    def rex_run(self, cmd, capture_output=True, text=True, timeout=None, **kw):
        argv = list(cmd[1:])
        if argv[:1] == ["-S"]:
            argv = argv[2:]
        self.calls += 1
        if self.nested and self.calls >= self.nested[0] and not getattr(self, "in_nested", False):
            _, op = self.nested
            self.nested, self.in_nested = None, True
            try:
                op()
            finally:
                self.in_nested = False
        fault = self.rng.choice(["timeout_before", "timeout_after", "fail", "garbage", "cut", "warn"]) if self.rng.random() < self.rate else None
        if fault in ("timeout_before", "fail") and os.environ.get("FUZZ_TRACE"):
            print(f"[{self.calls}] {fault} {' '.join(argv)[:90]}", file=sys.__stderr__)
        if fault == "timeout_before":
            self.clock.t += timeout or 0
            raise subprocess.TimeoutExpired(cmd, timeout)
        if fault == "fail":
            return subprocess.CompletedProcess(cmd, 1, "", "rex: connection reset")
        self.snapshot(argv)
        rc, out, err = self.fake_call(argv)
        self.log_event(argv, rc, out)
        if fault == "timeout_after":
            self.clock.t += timeout or 0
            raise subprocess.TimeoutExpired(cmd, timeout)
        if fault == "garbage":
            out = "<<not json>>"
        elif fault == "cut":
            out = out[: len(out) // 2]
        elif fault == "warn":
            out = "warning: host is slow\n" + out
        self.clock.t += 0.05
        if os.environ.get("FUZZ_TRACE"):
            print(f"[{self.calls}] {fault or 'ok'} {' '.join(argv)[:90]} -> {rc} {out[:70]!r}", file=sys.__stderr__)
        return subprocess.CompletedProcess(cmd, rc, out, err)

    def snapshot(self, argv):
        """What the report file held when a finish script read it (the oracle for 'clean')."""
        if argv[:1] == ["run"] and "--wait" in argv:
            m = re.search(r"-f ([^ ]*report-(\w+)\.md)", " ".join(argv))
            if m:
                name = m.group(2)
                try:
                    with open(os.path.join(self.workers[name]["dir"], ".rex-crew", f"report-{name}.md"), "rb") as f:
                        self.snap[name] = f.read()
                except (OSError, KeyError):
                    self.snap[name] = None

    def log_event(self, argv, rc, out):
        c = argv[:1]
        # only a program_status read is a status read: `block call process` (is the agent still in front?) says nothing about prompts
        if argv[:2] == ["block", "call"] and rc == 0 and argv[-1] == "program_status":
            blk = argv[argv.index("-b") + 1]
            try:
                r = json.loads(out)["records"]
                r = r[0] if r else None
            except (ValueError, KeyError, TypeError):
                r = None
            self.events.append(("status", blk, r, self.clock.t, self.actor[-1] if self.actor else None))
        elif c in (["send"], ["send-key"]) and rc == 0:
            blk = argv[argv.index("-b") + 1]
            self.events.append(("send" if c == ["send"] else "key", blk, argv[-1], self.clock.t, self.actor[-1] if self.actor else None))

    # -- generator ---------------------------------------------------------------------------------------
    def odd_record(self):
        return self.rng.choice([5, "blocked", [], None, {"state": 5}, {"state": None}, {"state": ["a"]}, {"msg": 7}, {},
                                {"state": "blocked"}, {"state": "blocked", "kind": 5, "msg": 7}, {"state": "blocked", "kind": "permission"},
                                {"state": "blocked", "kind": "permission", "msg": "permission approve "},
                                {"state": "working", "msg": {"a": 1}}, {"state": "error"}, {"state": "error", "msg": None},
                                {"state": "blocked", "kind": "question", "msg": None}, {"state": "done", "msg": "x" * 5000},
                                {"state": "blocked", "kind": "permission", "app": "other", "msg": "permission approve Bash: git status"}])

    def prompt(self):
        t = self.rng.choice(APPROVED + UNAPPROVED)
        return rec("blocked", "permission", "permission approve " + t)

    def script_for(self):
        out = []
        for _ in range(self.rng.randint(1, 9)):
            k = self.rng.random()
            if k < 0.25: out.append(rec("working"))
            elif k < 0.40: out.append(rec(self.rng.choice(["idle", "done"])))
            elif k < 0.60: out.append(self.prompt())
            elif k < 0.67: out.append(rec("blocked", "question", "which one?"))
            elif k < 0.72: out.append(rec("error", msg="boom"))
            elif k < 0.80: out.append(self.odd_record())
            elif k < 0.85 and out: out.append("GONE")
            else: out.append(rec("working"))
        if self.rng.random() < 0.6:  # most workers end properly
            out += [rec("working"), rec("done")]
        return out

    REPORTS = [None, "STATUS: done\nCHANGED: x\n", "STATUS: done\nCHANGED: x\n", "STATUS: done\nCHANGED: x\n", "STATUS: failed\nOPEN: y\n",
               "", "CHANGED: only\n", "STATUS: done | blocked | failed\n", "STATUS: done\nSTATUS: failed\n", b"\xff\xfe\x00STATUS: done\n",
               "STATUS: done\n" + "x" * 150000, "STATUS: blocked\n", "status: done\n"]

    def make_worker(self, i):
        name, d = f"w{i}", self.workdir(f"w{i}")
        verify = self.rng.choice([None, None, "true", "false", "echo hi; exit 3", "sleep 30"])
        w = {"name": name, "dir": d, "verify": verify, "keep": self.rng.random() < 0.2, "report": self.rng.choice(self.REPORTS),
             "late": self.rng.random() < 0.15, "script": self.script_for()}
        self.workers[name] = w
        self.script(name, *w["script"])
        if w["report"] is not None and not w["late"]:
            self.write_report(w)
        return w

    def write_report(self, w):
        os.makedirs(f"{w['dir']}/.rex-crew", exist_ok=True)
        r = w["report"]
        with open(f"{w['dir']}/.rex-crew/report-{w['name']}.md", "wb") as f:
            f.write(r if isinstance(r, bytes) else r.encode())

    # -- running one command -----------------------------------------------------------------------------
    def op(self, *args, policy=None):
        self.actor.append((args[0], policy))
        t0, err = self.clock.t, ""
        out, errb, code = io.StringIO(), io.StringIO(), 0
        pre = [r["block"] for r in self.ledger_blocks()]
        with mock.patch.object(sys, "argv", ["rex-crew", *args]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(errb):
            try:
                self.mod.main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            except BaseException as e:  # a traceback: invariant 1
                import traceback
                self.viol.append(f"1 traceback in {' '.join(args)}: {''.join(traceback.format_exception_only(type(e), e)).strip()} "
                                 f"@ {traceback.extract_tb(e.__traceback__)[-1].lineno}")
                code = 70
        self.actor.pop()
        out, errb = out.getvalue(), errb.getvalue()
        for m in re.finditer(r"could not check for a half-started pane named (\w+)|could not close the half-started pane \S+ \((\w+)\)", errb):
            self.warned.add(m.group(1) or m.group(2))  # the user was told that a pane of this name may be left over
        if "Traceback (most recent call last)" in out + errb:
            self.viol.append(f"1 traceback text in output of {' '.join(args)}")
        if args[0] == "collect":
            self.check_collect(args, code, out, errb, self.clock.t - t0)
        return code, out, errb

    def ledger_blocks(self):
        try:
            with open(self.ledger) as f:
                d = json.load(f)
            return [r for r in d if isinstance(r, dict) and r.get("block")]
        except (OSError, ValueError):
            return []

    # -- invariants ----------------------------------------------------------------------------------------
    def check_collect(self, args, code, out, err, elapsed):
        timeout = int(args[args.index("--timeout") + 1]) if "--timeout" in args else 900
        nw = max(1, len(self.workers))
        if elapsed > timeout + 25 + nw * 60:
            self.viol.append(f"6 collect {' '.join(args)} took {elapsed:.0f}s virtual (timeout {timeout})")
        if code == 0:
            states = re.findall(r"^== (\S+)\s+state: (\w+)", out, re.M)
            bad = [(n, s) for n, s in states if s != "done"]
            if bad or "ATTENTION" in out or "still working" in out or "needs you" in out:
                self.viol.append(f"3 exit 0 although {bad or 'attention/still working/needs you'}: {out[:200]!r}")
            for n, s in states:
                w = self.workers.get(n)
                snap = self.snap.get(n)
                if w and s == "done" and (snap != b"STATUS: done\nCHANGED: x\n" or w["verify"] not in (None, "true")):
                    self.viol.append(f"3 exit 0 for {n} whose report was {str(snap)[:40]!r} and verify {w['verify']!r}")

    def check_presses(self):
        last_status, last_press = {}, {}
        for kind, blk, val, t, actor in self.events:
            if kind == "status":
                last_status[(blk, id(actor))] = val  # what this very command last read: another command's read is not its own
                continue
            who = actor[0] if actor else "?"
            seen = last_status.get((blk, id(actor)))
            if kind == "key":
                if who not in ("send", "deny", "answer"):
                    self.viol.append(f"2 key {val!r} pressed by {who} on {blk}")
                continue
            if who in ("send", "answer"):
                continue
            if val != "1":
                self.viol.append(f"2 text {val!r} typed by {who} on {blk}")
                continue
            text = (seen or {}).get("msg", "") if isinstance(seen, dict) else ""
            text = re.sub(r"^.*?\bapprove ", "", str(text), count=1, flags=re.S)
            ok = isinstance(seen, dict) and seen.get("state") == "blocked" and seen.get("kind") == "permission" \
                and seen.get("app", "claude-code") == "claude-code"
            if not ok:
                self.viol.append(f"2 key 1 pressed by {who} on {blk} while the last status was {seen!r}")
            elif who == "collect" and text not in APPROVED:
                self.viol.append(f"2 collect pressed 1 for the unapproved prompt {text!r} on {blk}")
            elif who == "collect" and not actor[1]:
                self.viol.append(f"2 collect pressed 1 on {blk} without a policy")
            lp = last_press.get(blk)
            if who == "collect" and lp and lp[0] == seen and t - lp[1] < 10:  # a person who approves twice meant it
                self.viol.append(f"2 second key 1 within {t - lp[1]:.0f}s for the same prompt on {blk} ({lp[2]} then {who})")
            last_press[blk] = (seen, t, who)

    def check_state(self, where):
        st = self.fake()
        try:
            with open(self.ledger) as f:
                led = json.load(f)
            if not isinstance(led, list):
                raise ValueError("not a list")
        except FileNotFoundError:
            led = []
        except ValueError as e:
            self.viol.append(f"5 ledger invalid after {where}: {e}")
            return
        known = {r.get("block") for r in led if isinstance(r, dict)}
        for bid, b in st["blocks"].items():
            if b["closed"] or b["label"] == "crew-sh":
                continue
            if bid not in known and b["label"] in self.warned:
                continue  # unrecorded, but the user was told in so many words (the host failed twice in a row)
            if bid not in known:
                self.viol.append(f"4 pane {bid} ({b['label']}) is open and in no ledger entry after {where}")
            else:
                r = [r for r in led if isinstance(r, dict) and r.get("block") == bid][0]
                if r.get("state") in ("gone", "starting"):
                    self.viol.append(f"5 live pane {bid} ({b['label']}) is recorded as {r.get('state')} after {where}")

    # -- one seed -----------------------------------------------------------------------------------------
    def play(self, seed):
        self.begin(seed)
        n = self.rng.randint(1, 4)
        pol = self.policy(POLICY["approve"], POLICY["never"])
        for i in range(n):
            w = self.make_worker(i)
            args = ["start", w["name"], w["dir"], self.task] + (["--keep"] if w["keep"] else []) + (["--verify", w["verify"], "--verify-timeout", "1"] if w["verify"] else [])
            self.op(*args)
            self.check_state(f"start {w['name']}")
        names = list(self.workers)
        for _ in range(self.rng.randint(1, 5)):
            b = lambda: (self.rng.choice([r["block"] for r in self.ledger_blocks()] or ["b99"]))
            kind = self.rng.choice(["collect", "collect", "collect", "collect1", "send", "answer", "approve", "deny", "close", "status", "runs"])
            pick = self.rng.choice(names)
            timeout = self.rng.choice(["0", "1", "20", "60"])
            def make(kind=kind, blk=b(), pick=pick, timeout=timeout, withpol=self.rng.random() < 0.8):
                pa = ["--policy", pol] if withpol else []
                return {"collect": lambda: self.op("collect", *pa, "--timeout", timeout, policy=withpol),
                        "collect1": lambda: self.op("collect", pick, *pa, "--timeout", timeout, policy=withpol),
                        "send": lambda: self.op("send", blk, "hello", "there"), "answer": lambda: self.op("answer", blk, "2"),
                        "approve": lambda: self.op("approve", blk), "deny": lambda: self.op("deny", blk),
                        "close": lambda: self.op("close", blk), "status": lambda: self.op("status"), "runs": lambda: self.op("runs")}[kind]
            if self.rng.random() < 0.4:  # something else arrives in the middle of this command
                self.nested = (self.calls + self.rng.randint(1, 12), make(kind=self.rng.choice(["collect", "collect1", "send", "answer", "close", "approve", "deny"]),
                                                                       timeout=self.rng.choice(["0", "1"])))
            make()()
            self.nested = None
            for w in self.workers.values():  # a late report shows up
                if w["late"] and w["report"] is not None:
                    self.write_report(w)
            self.check_state(kind)
        self.op("collect", "--policy", pol, "--timeout", "60", policy=True)
        self.check_state("final collect")
        self.check_presses()
        return self.viol

    def test_seeds(self):
        only = os.environ.get("FUZZ_SEED")
        seeds = [int(only)] if only else range(int(os.environ.get("FUZZ_FROM", "0")), int(os.environ.get("FUZZ_FROM", "0")) + int(os.environ.get("FUZZ_N", "300")))
        failures = {}
        for seed in seeds:
            self.setUp_again()
            try:
                v = self.play(seed)
            except BaseException as e:  # the test's own bug or an escaped crash: show it with the seed
                import traceback
                v = ["0 harness/tool crash: " + traceback.format_exc()[-600:]]
            if v:
                failures[seed] = v
            self.tearDown_again()
        if failures:  # one line per kind of violation, with the first seed that shows it
            kinds = {}
            for seed, vs in failures.items():
                for v in vs:
                    kinds.setdefault(re.sub(r"/\S+|\d+(\.\d+)?s\b|b\d+", "·", v)[:160], seed)
            self.fail("%d seeds violate invariants (kind: first seed):\n%s" % (len(failures), "\n".join(f"  seed {s}: {k}" for k, s in kinds.items())))

    # Base.setUp builds a fresh tmp dir, env and module; a seed needs exactly that, so run it again per seed.
    def setUp_again(self):
        self._saved_env = dict(os.environ)
        self._saved = (self.tmp, self.mod)
        self.cleanups_before = len(self._cleanups)
        Base.setUp(self)

    def tearDown_again(self):
        while len(self._cleanups) > self.cleanups_before:
            fn, a, k = self._cleanups.pop()
            fn(*a, **k)
        os.environ.clear(); os.environ.update(self._saved_env)
        self.tmp, self.mod = self._saved


# ---- fixed regression tests: one per defect the fuzz found ----------------------------------------------------------

PERM = lambda cmd: rec("blocked", "permission", "permission approve Bash: " + cmd)


class Regress(Base):
    def setUp(self):
        super().setUp()
        alt = os.environ.get("RT_FUZZ_SCRIPT")  # run the tests against another copy of the tool (to see them fail first)
        if alt:
            loader = importlib.machinery.SourceFileLoader("rex_crew_alt", alt)
            self.mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
            loader.exec_module(self.mod)
            sl = mock.patch.object(self.mod.time, "sleep", lambda s: None)
            sl.start(); self.addCleanup(sl.stop)

    def flaky(self, damage):
        """damage(argv, n) -> None | 'garbage' | 'cut' | 'fail': what the host does to the n-th call (the work is done first)."""
        real, n = subprocess.run, [0]

        def run(cmd, *a, **k):
            n[0] += 1
            how = damage([c for c in cmd[1:]], n[0])
            if how == "fail":
                return subprocess.CompletedProcess(cmd, 1, "", "rex: connection reset")
            p = real(cmd, *a, **k)
            if how == "garbage":
                return subprocess.CompletedProcess(cmd, 0, "<<not json>>", "")
            if how == "cut":
                return subprocess.CompletedProcess(cmd, 0, p.stdout[: len(p.stdout) // 2], "")
            return p
        return mock.patch.object(self.mod.subprocess, "run", run)

    def start(self, name, script):
        d = self.workdir(name)
        self.script(name, *script)
        self.assertEqual(self.cli("start", name, d, self.task)[0], 0)
        return d

    def ledger_state(self, name):
        return [r for r in self.ledger_runs() if r["name"] == name][0]["state"]

    def open_panes(self, label=None):
        return [b for b in self.fake()["blocks"].values() if not b["closed"] and b["label"] != "crew-sh" and label in (None, b["label"])]

    # seed 6: rex answered the start with something that is not JSON: a traceback, and the pane kept running
    def test_garbled_start_answer_is_an_error_and_the_pane_is_closed(self):
        d = self.workdir("w1")
        with self.flaky(lambda argv, n: "garbage" if argv[:1] == ["run"] and "--label" in argv and "w1" in argv else None):
            code, out, err = self.cli("start", "w1", d, self.task)
        self.assertEqual(code, 4, out + err)
        self.assertIn("did not say which pane", err)
        self.assertEqual(self.open_panes(), [], "the pane that was started is still running and in no ledger entry")

    # seed 268: the same, and the check for strays gets a garbled answer too
    def test_stray_check_asks_again(self):
        d = self.workdir("w1")

        def damage(argv, n):
            if argv[:1] == ["run"] and "w1" in argv:
                return "cut"
            if argv[:2] == ["block", "ls"] and not hasattr(self, "ls_seen"):
                self.ls_seen = True
                return "cut"
        with self.flaky(damage):
            code, out, err = self.cli("start", "w1", d, self.task)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.open_panes(), [], err)

    # seed 1466: the close of the stray pane failed once; it was reported as closed and kept running
    def test_stray_close_is_retried_and_not_reported_when_it_failed(self):
        d = self.workdir("w1")

        def damage(argv, n):
            if argv[:1] == ["run"] and "w1" in argv:
                return "garbage"
            if argv[:2] == ["block", "close"] and not hasattr(self, "close_seen"):
                self.close_seen = True
                return "fail"
        with self.flaky(damage):
            code, out, err = self.cli("start", "w1", d, self.task)
        self.assertEqual(self.open_panes(), [], err)
        self.assertIn("closed the half-started pane", err)

    # seed 957: a status record from the terminal with fields that are not strings
    def test_status_with_odd_records_prints_no_traceback(self):
        self.start("w1", [{"state": ["a"]}])
        self.start("w2", [{"state": None, "kind": ["x"], "msg": 7}])
        code, out, err = self.cli("status", "--all")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out.strip().splitlines()), 2)

    # seed 502: two failed status calls in a row read as 'the pane is gone': the live worker was dropped from the ledger
    def test_failed_status_calls_do_not_make_a_live_pane_gone(self):
        self.start("w1", [rec("working")])
        with self.flaky(lambda argv, n: "fail" if argv[:2] == ["block", "call"] and n < 8 else None):
            code, out, err = self.cli("collect", "--timeout", "1")
        self.assertEqual(self.ledger_state("w1"), "open", out + err)
        self.assertEqual(len(self.open_panes("w1")), 1)
        self.assertNotIn("state: gone", out)

    def test_a_pane_that_is_really_gone_is_still_reported_gone(self):
        self.start("w1", ["GONE"])
        code, out, err = self.cli("collect", "--timeout", "5")
        self.assertEqual(code, 5, out + err)
        self.assertEqual(self.ledger_state("w1"), "gone")

    # seed 383: a failed status call between two looks at the same prompt made collect forget its key: it pressed again
    def test_flaky_status_does_not_make_collect_press_twice(self):
        self.start("w1", [PERM("git status")])
        pol = self.policy([r"^Bash: git status"])
        with self.flaky(lambda argv, n: "fail" if argv[:2] == ["block", "call"] and n % 3 == 0 else None):
            self.cli("collect", "--policy", pol, "--timeout", "1")
        self.assertEqual(len(self.sent("1")), 1, "the same prompt got more than one key within ten seconds")

    # seed 383: a second collect that runs while the first one types its key presses for the same prompt too
    def test_second_collect_does_not_press_for_the_same_prompt(self):
        self.start("w1", [PERM("git status")])
        pol, real, inner = self.policy([r"^Bash: git status"]), self.mod.Rex.type, []

        def typ(rex, block, text):
            if not inner:
                inner.append(1)
                self.cli("collect", "--policy", pol, "--timeout", "0")  # the other process, while our key is on its way
            return real(rex, block, text)
        with mock.patch.object(self.mod.Rex, "type", typ):
            self.cli("collect", "--policy", pol, "--timeout", "0")
        self.assertEqual(len(self.sent("1")), 1)

    # seed 383: the orchestrator approved by hand; the collect right after must not press for the same prompt again
    def test_collect_after_a_manual_approve_does_not_press_again(self):
        self.start("w1", [PERM("git status")])
        block = self.ledger_runs()[0]["block"]
        self.assertEqual(self.cli("approve", block)[0], 0)
        self.cli("collect", "--policy", self.policy([r"^Bash: git status"]), "--timeout", "0")
        self.assertEqual(len(self.sent("1")), 1)


if __name__ == "__main__":
    unittest.main()
