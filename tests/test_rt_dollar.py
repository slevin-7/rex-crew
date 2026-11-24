"""Red-team tests: false refusals of "$", env prefixes and timeout/time wrappers, and the soundness of all three.
Run: python3 -m unittest tests/test_rt_dollar.py"""
import contextlib, io, json, os, random, re, shutil, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base

READ = [r"^Bash: (grep|rg|sed|ls|cat|wc|wait|kt|a|b|c|python3|git status|make)(\s|$)"]


class Mixin:
    def routine(self, text, approve=READ, never=(), env=()):
        approve = self.mod.Patterns(re.compile(x) for x in approve)
        approve.env = tuple(env)
        return self.mod.routine(text, approve, [re.compile(x) for x in never])


class TestDollar(Mixin, Base):
    INERT = [
        r'grep -n "wait \$wp\|kt \$" FILE',          # the reported prompt: escaped inside double quotes
        "grep -n 'wait $wp\\|kt $' FILE",            # single quotes
        'grep -n "kt $" FILE',                       # $ right before the closing quote
        "grep -n 'a$' FILE", "grep -E a$ FILE", 'grep -E "a$|b" FILE', 'grep "(a)$" f', "sed -n '/x$/p' f",
        r'rg "foo\$" f', 'grep "a$." f', 'grep "a$/b" f', 'grep -c "$" f', 'grep "^$" f', 'grep -E "^$|x" f',
        "grep ' $' f", 'grep "a$ b" f', 'grep "$:x" f', "grep -e a$ -e 'b$' f", r"grep a\$b f", 'grep "x$"y f',
        "grep '\\$X' f", "grep '${X}' f", 'grep "a\\$1" f', "grep a$ | wc -l",
        "grep x # see $HOME and '", "grep x f # costs $X", "grep x #'\n# $X\ngrep y",  # a comment is never read by sh
    ]
    REAL = [
        'grep "$HOME" f', 'grep "${X}" f', 'grep "a$1" f', "grep $X f", r'grep "\\$X" f', 'grep "a$$" f',
        'grep "$@" f', 'grep "$#" f', 'grep "$?" f', 'grep "$!" f', 'grep "$-" f', 'grep "$*" f', 'grep "$_" f',
        'grep "$[1+1]" f', 'grep "$(id)" f', 'grep "$(id" f', 'grep "x"$X f', "grep 'a'$X f", "grep '\\'$X f",
        "grep $'\\x41' f", 'grep $"x" f', 'grep "wait \\$wp\\|kt $wp" f', r'grep "a\\\\$X" f', 'grep ${X:-b} f',
        'grep "$"$X f', "grep a '$' $X", 'ls $0', 'ls "$9"', 'ls x$X$', r'grep "\$"$X f',
        "grep \"x\" ; echo $X | wc", 'grep "x\\\n$X" f',
        "grep x #'\ngrep $X", 'grep x #"\ngrep $X', "grep ''#x $X f", "grep x # '\ngrep $X",  # a comment must not swallow the next line
    ]

    def test_inert_dollar_is_routine(self):
        for t in self.INERT:
            self.assertTrue(self.routine("Bash: " + t), t)

    def test_real_expansion_is_refused(self):
        for t in self.REAL:
            self.assertFalse(self.routine("Bash: " + t), t)


class TestEnv(Mixin, Base):
    def test_default_refuses(self):
        self.assertFalse(self.routine("Bash: FOO=1 python3 -m unittest"))

    def test_listed_name_is_stripped(self):
        self.assertTrue(self.routine("Bash: FOO=1 python3 -m unittest", env=["FOO"]))
        self.assertTrue(self.routine("Bash: FOO=1 BAR=x.y python3 -m unittest", env=["FOO", "BAR"]))
        self.assertTrue(self.routine("Bash: FOO='1' python3 -m unittest; FOO=2 make", env=["FOO"]))

    def test_unlisted_name_is_refused(self):
        self.assertFalse(self.routine("Bash: BAZ=1 python3 -m unittest", env=["FOO"]))
        self.assertFalse(self.routine("Bash: FOO=1 BAZ=1 python3 -m unittest", env=["FOO"]))
        self.assertFalse(self.routine("Bash: foo=1 python3 -m unittest", env=["FOO"]))

    def test_value_must_be_plain(self):
        for v in ("$X", "'a b'", "$(id)", "~", "*", "a;b", "a\\ b", "a=b c", "\"$X\"", "x'y z'"):
            self.assertFalse(self.routine("Bash: FOO=" + v + " python3 -m unittest", env=["FOO"]), v)

    def test_quoted_value_is_one_word(self):
        # FOO='x python3' -m unittest runs a command called -m, never python3
        self.assertFalse(self.routine("Bash: FOO='x python3' -m unittest", env=["FOO"]))
        self.assertFalse(self.routine("Bash: FOO='x make' ; echo", env=["FOO"], approve=[r"^Bash: make"]))

    def test_assignment_alone_is_not_a_command(self):
        self.assertFalse(self.routine("Bash: FOO=1", env=["FOO"]))
        self.assertFalse(self.routine("Bash: FOO=1; python3 x", env=["FOO"]))
        # a second, unlisted assignment is not a command either, even when the policy would approve anything after it
        self.assertFalse(self.routine("Bash: FOO=1 BAZ=2 make", env=["FOO"], approve=[r"^Bash: BAZ=2 make"]))

    def test_dangerous_names_are_never_stripped(self):
        for n in ("LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_FRAMEWORK_PATH", "GIT_DIR", "GIT_SSH_COMMAND",
                  "GIT_CONFIG_COUNT", "PATH", "PYTHONPATH", "PYTHONSTARTUP", "BASH_ENV", "ENV", "IFS", "HOME", "NODE_OPTIONS",
                  "path", "Git_Dir", "dyld_x"):
            self.assertFalse(self.routine(f"Bash: {n}=x python3 -m unittest", env=[n]), n)
            self.assertFalse(self.routine(f"Bash: {n}=x python3 -m unittest", env=["FOO", n]), n)

    def test_policy_file_env_key(self):
        def load(obj):
            p = os.path.join(self.tmp.name, "p.json")
            with open(p, "w") as f:
                json.dump(obj, f)
            return self.mod.load_policy(p)
        approve, never = load({"approve": ["^Bash: python3"], "env": ["FOO", "CI"]})
        self.assertEqual(sorted(approve.env), ["CI", "FOO"])
        self.assertTrue(self.mod.routine("Bash: CI=1 python3 -m unittest", approve, never))
        self.assertFalse(self.mod.routine("Bash: X=1 python3 -m unittest", approve, never))
        approve, never = load({"approve": ["^Bash: python3"]})
        self.assertFalse(self.mod.routine("Bash: CI=1 python3 -m unittest", approve, never))

    def test_policy_file_rejects_bad_env(self):
        for bad in (["LD_PRELOAD"], ["DYLD_X"], ["GIT_DIR"], ["PATH"], ["PYTHONPATH"], "FOO", [1], ["a b"], ["FOO=1"], [""], ["F-O"]):
            p = os.path.join(self.tmp.name, "p.json")
            with open(p, "w") as f:
                json.dump({"approve": ["^Bash: x"], "env": bad}, f)
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
                self.mod.load_policy(p)
            self.assertEqual(cm.exception.code, 6, bad)  # usage: one exit table

    def test_never_still_sees_the_whole_command(self):
        self.assertFalse(self.routine("Bash: FOO=1 python3 evil.py", env=["FOO"], never=[r"evil"]))
        self.assertFalse(self.routine("Bash: FOO=1 python3 x", env=["FOO"], never=[r"^Bash: FOO="]))


class TestWrappers(Mixin, Base):
    def test_wrapped_command_is_judged(self):
        for t in ("timeout 100 python3 -m unittest", "time python3 x", "time -p python3 x", "timeout 5s make", "timeout 1.5m make",
                  "timeout -k 5 100 make", "timeout -s KILL 10 make", "timeout --preserve-status 10 make", "time timeout 9 make",
                  "timeout 9 time make", "timeout --kill-after=5 10 make", "timeout 100 git status"):
            self.assertTrue(self.routine("Bash: " + t), t)

    def test_wrapped_unapproved_is_refused(self):
        for t in ("timeout 100 rm -rf x", "time rm x", "timeout 5 time rm x", "time timeout 5 sh -c a", "timeout 5 sh x",
                  "timeout 100 git push", "timeout 5 ssh x"):
            self.assertFalse(self.routine("Bash: " + t), t)

    def test_never_sees_the_wrapped_command(self):
        self.assertFalse(self.routine("Bash: timeout 100 git push", approve=[r"^Bash: "], never=[r"^Bash: git push"]))
        self.assertFalse(self.routine("Bash: time rm x", approve=[r"^Bash: "], never=[r"^Bash: rm\b"]))

    def test_wrapper_alone_or_odd_is_not_approved(self):
        for t in ("timeout", "timeout 5", "time", "timeout abc make", "timeout -o 5 make", "timeout 5 -- make", "timeout -s make",
                  "time -o f make", "time -l make", "timeout -k 5 make", "timeout 5 5 make", "timeout --foo 5 make", "timeout -9 make"):
            self.assertFalse(self.routine("Bash: " + t, approve=[r"^Bash: make(\s|$)"]), t)

    def test_wrappers_do_not_hide_a_chain(self):
        self.assertFalse(self.routine("Bash: timeout 5 make; rm x"))
        self.assertFalse(self.routine("Bash: time make | sh"))
        self.assertFalse(self.routine("Bash: timeout 5 make && rm x"))

    def test_wrapper_with_env(self):
        self.assertTrue(self.routine("Bash: FOO=1 timeout 5 make", env=["FOO"]))
        self.assertTrue(self.routine("Bash: FOO=1 time make", env=["FOO"]))
        self.assertFalse(self.routine("Bash: FOO=1 timeout 5 make"))
        self.assertFalse(self.routine("Bash: BAZ=1 timeout 5 make", env=["FOO"]))
        # after the wrapper an assignment is just a (nonexistent) command name, not an env prefix: nothing to strip
        self.assertFalse(self.routine("Bash: timeout 5 FOO=1 make", env=["FOO"]))

    def test_quoted_wrapper_words(self):
        # 'time' quoted is /usr/bin/time, whose -o writes a file: the option list must stay closed
        self.assertFalse(self.routine("Bash: 'time' -o out make"))
        self.assertTrue(self.routine("Bash: timeout '5' make"))


# ---- randomized soundness: the tool must never see fewer commands or expansions than sh does -----------

WORDS = ["a", "b", "c", "x", "y", "'$X'", '"$X"', r"\$X", r'"\$X"', "$X", "${X}", "$1", "$$", "$", '"$"', "'$'", '"a$"', "$.", "$/",
         "x$", "$@", "$#", "$?", "$-", "$!", "$*", "$[1+1]", "$_", '"$(echo hi)"', "'a b'", '"a b"', "{x,y}", "{1..3}", "*", "$U", '"$U"',
         r'"\\$X"', r"'\'", "$'q'", '$"q"', r"x\ y", "#c", "a#", "#'", '#"', "''#x", "2>&1", ">/dev/null", "FOO=1", "BAR='x'", "BAZ=2", "LD_PRELOAD=z",
         "GIT_DIR=q", "PATH=/nonexistent", "FOO=$X", "FOO='a b'", "time", "time -p", "timeout 5", "timeout -k 1 5", "timeout 5s",
         "timeout -s KILL 5", "$X x", "\\\n", "\\", '"', "'", "$", "!", "-n", "-p", "5", "`echo a`"]
SEPS = [" ", " ", " ", " ", "  ", "\t", " ; ", " && ", " | ", " || ", "\n", ";"]  # no "&": a background job writes its trace after we looked (a & b is checked below)


class TestSoundness(Mixin, Base):
    def setUp(self):
        super().setUp()
        d = self.bin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        # one append per command: the commands of a pipeline run at the same time and must not interleave
        body = ('#!/bin/sh\ne=\nfor v in FOO BAR BAZ LD_PRELOAD GIT_DIR; do eval "[ -n \\"\\$${$v+set}\\" ]" && e="$e $v"; done\n'
                'a=; for x; do a="$a$(printf "\\037")$x"; done\n'
                'printf "T%s\\n" "$(basename "$0")$(printf "\\036")$e$a" >> "$TRACE"\nexit 0\n')
        for n in "abcxy":
            with open(os.path.join(d, n), "w") as f:
                f.write(body)
            os.chmod(os.path.join(d, n), 0o755)
        with open(os.path.join(d, "timeout"), "w") as f:
            f.write('#!/bin/sh\nwhile :; do case "$1" in -k|-s) shift 2;; -v|--preserve-status|--foreground|--kill-after=*|--signal=*) shift;; *) break;; esac; done\nshift\nexec "$@"\n')
        os.chmod(os.path.join(d, "timeout"), 0o755)

    def run_sh(self, text):
        trace = os.path.join(self.bin, "trace")
        if os.path.exists(trace):
            os.unlink(trace)
        open(trace, "w").close()
        wd = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, wd, True)
        env = {"PATH": self.bin + ":/usr/bin:/bin", "TRACE": trace, "X": "a", "HOME": wd}
        try:
            subprocess.run(["sh", "-c", text, "sh", "a"], env=env, cwd=wd, timeout=10, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return None
        calls = []
        with open(trace) as fh:
            for line in fh.read().split("\n"):
                if line.startswith("T") and "\x1e" in line:
                    name, rest = line[1:].split("\x1e", 1)
                    env, *args = rest.split("\x1f")
                    calls.append({"name": name, "args": args, "env": env.split()})
        return calls

    def gen(self, rnd):
        return "".join(rnd.choice(WORDS) + rnd.choice(SEPS) for _ in range(rnd.randint(1, 6)))

    def policy(self, allowed):
        approve = self.mod.Patterns([re.compile(r"^Bash: [abcxy](\s|$)")])
        approve.env = tuple(allowed)
        return approve

    def check(self, text, allowed_env):
        if not self.mod.routine("Bash: " + text, self.policy(allowed_env), []):
            return None
        judged = self.mod.judged("Bash: " + text, allowed_env)
        calls = self.run_sh(text)
        if calls is None:
            return None
        known = set(judged)
        for c in calls:
            line = "Bash: " + " ".join([c["name"]] + c["args"])
            if line not in known:
                return f"sh ran {line!r} but the tool judged {sorted(known)!r}"
            bad = [e for e in c["env"] if e not in allowed_env]
            if bad:
                return f"sh ran {line!r} with {bad} set"
        return None

    def test_random_soundness(self):
        rnd = random.Random(int(os.environ.get("RT_DOLLAR_SEED", 20261124)))
        seen_ok, failures = 0, []
        for _ in range(int(os.environ.get("RT_DOLLAR_N", 1500))):
            text = self.gen(rnd)
            for allowed in ((), ("FOO", "BAR")):
                why = self.check(text, allowed)
                if why:
                    failures.append((text, allowed, why))
                elif self.mod.routine("Bash: " + text, self.policy(allowed), []):
                    seen_ok += 1
        self.assertEqual(failures[:5], [])
        self.assertGreater(seen_ok, 50)  # the generator must reach routine commands, or the test proves nothing

    def test_background_job_is_a_command_of_its_own(self):
        self.assertEqual(self.mod.judged("Bash: a & b"), ["Bash: a", "Bash: b"])
        self.assertFalse(self.routine("Bash: a & rm x"))
        self.assertFalse(self.routine("Bash: timeout 5 a & rm x"))

    def test_soundness_regressions(self):
        for text, allowed in [("a $X", ()), ('a "$X"', ()), ("a $1", ()), ("a $$", ()), ("FOO=1 a", ("FOO",)), ("timeout 5 a b", ()),
                              ("time a", ()), ("FOO='x a' -m", ("FOO",)), ("a '$X'", ()), (r"a \$X", ()), ("a $", ()), ("a $[1+1]", ())]:
            self.assertIsNone(self.check(text, allowed), text)
