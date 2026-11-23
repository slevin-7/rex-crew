"""Red-team tests: quoting in the approval policy (parts, routine). Run: python3 -m unittest tests/test_rt_quote.py"""
import os, random, re, shutil, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base

ANY = [re.compile(r"^Bash: ")]
LS = [re.compile(r"^Bash: (ls|grep|echo|git status|cat)(\s|$)")]


class TestQuotedOperators(Base):
    def ok(self, cmd, approve=LS, never=()):
        return self.mod.routine("Bash: " + cmd, approve, list(never))

    def test_quoted_operators_are_plain_text(self):
        for c in ("grep -c '<<<<<<<' FILE", "grep ';' FILE", "grep \"a|b\" f", "grep a\\;b f", "echo \"x > y\"",
                  "grep '&&' f", "grep -c '>>>>>>>' f", "echo '2>&1'", "grep '#' f", "echo \"it's\"", "grep \\> f",
                  "grep '$(' f", "grep '`' f", "echo \"\\$(x)\"", "grep '\\' f"):
            self.assertTrue(self.ok(c), c)
            self.assertEqual(len(self.mod.parts("Bash: " + c)), 1, c)

    def test_real_operators_still_count(self):
        self.assertEqual(self.mod.parts("Bash: ls 'a b' ; echo \"c\" && cat d | grep e"),
                         ["Bash: ls a b", "Bash: echo c", "Bash: cat d", "Bash: grep e"])
        for c in ("ls 'a'; rm x", "ls \"a\"\nrm x", "ls 'a' > f", "ls 'a' >f", "ls x 2>f", "ls <<< 'x'", "ls 'a' & rm x",
                  "echo \"$(rm x)\"", "echo `rm x`", "ls <(rm x)"):
            self.assertFalse(self.ok(c), c)

    def test_quotes_do_not_hide_the_redirection_rewrite(self):
        # the text 2>&1 inside quotes is an argument, not something to cut out of the command
        self.assertEqual(self.mod.parts("Bash: echo \"a 2>&1\""), ["Bash: echo a 2>&1"])
        self.assertEqual(self.mod.parts("Bash: ls x 2>&1 | cat"), ["Bash: ls x", "Bash: cat"])
        self.assertTrue(self.ok("ls x 2>/dev/null"))
        self.assertFalse(self.ok("ls x >/dev/nullx"))

    def test_line_continuation(self):
        self.assertEqual(self.mod.parts("Bash: ls \\\n-la"), ["Bash: ls -la"])
        self.assertEqual(self.mod.parts("Bash: git \\\npush"), ["Bash: git push"])


class TestHiddenSecondCommand(Base):
    def ok(self, cmd, approve=LS):
        return self.mod.routine("Bash: " + cmd, approve, [])

    def test_apostrophe_in_a_comment_does_not_hide_the_next_line(self):
        # a comment runs to the end of its line: the apostrophe opens no quote, and rm is a second command
        c = "ls # it's\nrm -rf x"
        self.assertEqual(self.mod.parts("Bash: " + c), ["Bash: ls", "Bash: rm -rf x"])
        self.assertFalse(self.ok(c))
        self.assertTrue(self.ok("ls # it's fine"))
        self.assertTrue(self.ok("ls # a; b"))

    def test_unbalanced_quotes_are_never_routine(self):
        for c in ("ls 'a", "ls \"a\nrm x", "ls 'a ; rm x"):
            self.assertFalse(self.ok(c), c)

    def test_ansi_c_quoting_is_not_read_as_plain_quotes(self):
        for c in ("ls $'\\'' ; rm x ; echo $'\\''", "ls $'a'", "ls $\"a\""):
            self.assertFalse(self.ok(c), c)

    def test_comment_ends_at_a_newline_even_after_a_backslash(self):
        # some shells continue the comment, some do not: seeing the next line as a command is the safe reading
        self.assertEqual(self.mod.parts("Bash: ls # c \\\nrm x"), ["Bash: ls", "Bash: rm x"])

    def test_comment_does_not_hide_a_substitution(self):
        self.assertFalse(self.ok("ls # $(rm x)"))


def sh_ok(cmd):
    return subprocess.run(["sh", "-n", "-c", cmd], capture_output=True).returncode == 0


class TestAgainstARealShell(Base):
    """Generate command lines, let a real sh run them with a/b/c as logging functions, and compare."""
    SAFE = ["x", "y1", "-n", "f.txt", "';'", "'&&'", "'|'", "'>'", "'<<<'", "'2>&1'", "\"a;b\"", "\"x > y\"", "'#'",
            "\"it's\"", "'say \"hi\"'", "a\\;b", "\\>", "\\|", "\\#", "x#y", "''", "\"\"", "'$(x)'", "'`'", "\"\\$(x)\"",
            "\"\\\"\"", "'\\'", "'a b'", "'a\nb'", "'&'", "\"<\"", "'*'", "'?'", "\"(\""]
    UNSAFE = [">f", "> f", ">>f", "2>f", ">\"f\"", ">'f'", "&>f", ">&2", "2>&1", ">/dev/null", "2>/dev/null", "> /dev/null",
              "<<< s", "<f", "$(a)", "`a`", "\"$(a x)\"", "\"`a`\"", ">&2f", ">/dev/nullx", "2>&1x",
              "<(a)", ">(a)", "$'\\''", "$'a'"]
    OPS_SAFE = [" ; ", ";", " && ", " | ", "\n", " &\n", " & ", " |& ", "\n\n", ";\n", " ;; "]
    OPS_ALL = OPS_SAFE + [" || "]

    def run_real(self, cmd, home):
        work = tempfile.mkdtemp(dir=home)
        log = os.path.join(home, "log")
        open(log, "w").close()
        prog = "a(){ echo a >>\"$LOG\"; }; b(){ echo b >>\"$LOG\"; }; c(){ echo c >>\"$LOG\"; }\n" + cmd + "\nwait\n"
        subprocess.run(["sh", "-c", prog], cwd=work, env={"LOG": log, "PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=10)
        with open(log) as f:
            ran = len(f.read().split())
        return ran, os.listdir(work)

    def gen(self, rnd, unsafe, ops):
        out = []
        for k in range(rnd.randint(1, 4)):
            words = [rnd.choice("abc")]
            for _ in range(rnd.randint(0, 3)):
                words.append(rnd.choice(self.SAFE + (self.UNSAFE if unsafe and rnd.random() < 0.5 else [])))
            out.append(" ".join(words))
            op = rnd.choice(ops)
            if rnd.random() < 0.25:  # a comment swallows the rest of its line: only a newline may follow it
                out[-1] += rnd.choice([" # c", " # it's", " # a; b", " #x"])
                op = "\n"
            out.append(op)
        out.pop()
        if rnd.random() < 0.2:
            out.append(rnd.choice([" ", "\n"]) + "# tail ' \" ; a")
        return "".join(out)

    def check(self, seed, n, unsafe, ops, exact):
        rnd, home, bad, tried = random.Random(seed), tempfile.mkdtemp(), [], 0
        try:
            for _ in range(n):
                cmd = self.gen(rnd, unsafe, ops)
                if not sh_ok(cmd):
                    continue
                tried += 1
                ran, files = self.run_real(cmd, home)
                text = "Bash: " + cmd
                ok = self.mod.routine(text, ANY, [])
                k = len([p for p in self.mod.parts(text) if p != "Bash: <redirection>"])
                if ok and (k < ran or files):
                    bad.append((cmd, "approved with %d commands, the shell ran %d, wrote %s" % (k, ran, files)))
                # a comment that mentions a substitution is refused on purpose (cheap insurance, rare)
                if exact and not (ok and k == ran) and not (not ok and re.search(r"(^|\s)#[^\n]*(`|\$\()", cmd)):
                    bad.append((cmd, "false refusal or wrong split: approved=%s tool=%d shell=%d" % (ok, k, ran)))
        finally:
            shutil.rmtree(home, ignore_errors=True)
        self.assertGreater(tried, n // 3)
        self.assertEqual(bad[:5], [], "%d of %d lines" % (len(bad), tried))

    def test_nothing_is_approved_that_the_shell_runs_as_more_or_writes(self):
        self.check(1, 700, True, self.OPS_ALL, False)
        self.check(2, 400, True, self.OPS_SAFE, False)

    def test_quoted_text_is_never_split_or_refused(self):
        self.check(3, 700, False, self.OPS_SAFE, True)


if __name__ == "__main__":
    unittest.main()
