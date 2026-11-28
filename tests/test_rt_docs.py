"""Red-team tests: the documentation (README.md, SKILL.md, --help, error texts) against the code.
Run: python3 -m unittest tests/test_rt_docs.py"""
import io, json, os, re, shlex, subprocess, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec, HERE

ROOT = os.path.join(HERE, "..")
README = open(os.path.join(ROOT, "README.md")).read()
SKILL = open(os.path.join(ROOT, "skills", "rex-crew", "SKILL.md")).read()
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def fences(doc, lang):
    return re.findall(r"```" + lang + r"\n(.*?)```", doc, re.S)


def doc_commands(doc):
    """Every complete rex-crew command line the document shows: fenced sh blocks and inline code spans."""
    found = []
    for block in fences(doc, "sh"):
        for line in re.sub(r"\\\n\s*", " ", block).splitlines():
            found.append(line)
    found += re.findall(r"`([^`\n]*rex-crew[^`\n]*)`", doc)
    out = []
    for line in found:
        line = line.replace('"$here/rex-crew"', "rex-crew")
        i = line.find("rex-crew ")
        if i < 0 or "[" in line or "BLOCK ..." in line or line.lstrip().startswith("#") or "~/src" in line:
            continue
        cmd = line[i:]
        if "$(" in line[:i]:  # A=$(rex-crew checkout ...)
            cmd = cmd.rsplit(")", 1)[0]
        try:
            words = shlex.split(cmd, comments=True)
        except ValueError:
            continue
        if words and words[0] == "rex-crew" and len(words) > 1 and words[1] != "...":
            out.append(words[1:])
    return out


class TestDocExamplesParse(Base):
    def parse(self, args):
        seen = []
        real = self.mod.argparse.ArgumentParser.parse_args

        def spy(p, argv=None, ns=None):
            ns = real(p, argv, ns)
            seen.append(ns)
            raise SystemExit(0)
        with mock.patch.object(self.mod.argparse.ArgumentParser, "parse_args", spy):
            code, out, err = self.cli(*args)
        return code, err, seen

    def test_every_command_line_in_the_docs_parses(self):
        n = 0
        for name, doc in (("README", README), ("SKILL", SKILL)):
            for words in doc_commands(doc):
                if words[0] in ("selftest", "--version"):  # both answer before the parser returns
                    continue
                code, err, seen = self.parse(words)
                self.assertEqual((code, len(seen)), (0, 1), f"{name}: rex-crew {' '.join(words)}: {err}")
                n += 1
        self.assertGreater(n, 15)  # the extraction really found the examples

    def test_documented_options_are_real(self):
        for words in doc_commands(README) + doc_commands(SKILL):
            if words[0] == "run":
                self.assertIn("--timeout", self.help("run"))

    def help(self, cmd):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["rex-crew", cmd, "--help"]), mock.patch.object(sys, "stdout", out):
            with self.assertRaises(SystemExit) as c:
                self.mod.main()
        self.assertEqual(c.exception.code, 0)
        return out.getvalue()

    def test_every_subcommand_has_help_that_explains_its_options(self):
        for cmd in ("run", "start", "collect", "runs", "checkout", "sh", "send", "approve", "answer", "deny",
                    "screen", "status", "close", "session"):
            text = self.help(cmd)
            for opt in re.findall(r"^\s+(--[a-z]+)", text, re.M):
                m = re.search(r"^\s+" + opt + r"[^\n]*\n?((?:\s{10,}[^\n]*\n)*)", text, re.M)
                same_line = re.search(r"^\s+" + opt + r"(?: \S+)?\s{2,}\S", text, re.M)
                self.assertTrue(same_line or (m and m.group(1).strip()), f"{cmd} {opt} has no help text")

    def test_run_and_start_help_show_how_claude_arguments_are_passed(self):
        for cmd in ("run", "start"):
            self.assertRegex(self.help(cmd), r"-- .*(CLAUDE|claude)")

    def test_top_level_help_lists_selftest(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["rex-crew", "--help"]), mock.patch.object(sys, "stdout", out):
            with self.assertRaises(SystemExit):
                self.mod.main()
        self.assertIn("selftest", out.getvalue())


class TestDocTables(Base):
    def test_skill_exit_table_matches_exit(self):
        rows = re.findall(r"^\| (\d) \| `?([a-z]+)`? \|", SKILL, re.M)
        table = {state: int(code) for code, state in rows if state != "no"}
        self.assertEqual(table, self.mod.EXIT)
        self.assertIn("| 6 |", SKILL)

    def test_readme_exit_line_matches_exit(self):
        line = re.search(r"Exit status of `run` and `collect`:(.*?)\n\n", README, re.S).group(1)
        for code in (0, 1, 2, 3, 4, 5, 6):
            self.assertIn("`%d`" % code, line)

    def test_documented_policies_are_valid_and_not_unsafe(self):
        for doc in (README, SKILL):
            for block in fences(doc, "json"):
                p = json.loads(block)
                for pat in p["approve"] + p["never"]:
                    re.compile(pat)
                for pat in p["approve"]:
                    self.assertFalse(pat.endswith(r"\b)") or pat.endswith(r"\b"), f"approve pattern ends in \\b: {pat}")
                ap = [re.compile(x) for x in p["approve"]]
                nv = [re.compile(x) for x in p["never"]]
                self.assertTrue(self.mod.routine("Bash: npm test", ap, nv))
                self.assertFalse(self.mod.routine("Bash: npm test:publish", ap, nv))
                self.assertFalse(self.mod.routine("Bash: git push", ap, nv))

    def test_example_policy_file_does_what_the_docs_say(self):
        ap, nv = self.mod.load_policy(os.path.join(ROOT, "policy.example.json"))
        self.assertTrue(self.mod.routine("Bash: git status", ap, nv))
        self.assertTrue(self.mod.routine("Bash: npm test", ap, nv))
        self.assertFalse(self.mod.routine("Bash: npm run build:publish", ap, nv))
        self.assertFalse(self.mod.routine("Bash: git push", ap, nv))
        self.assertFalse(self.mod.routine("Bash: cat ~/.ssh/id_rsa", ap, nv))


class TestDocumentedOutput(Base):
    def git_repo(self):
        d = self.workdir("repo")
        env = {**os.environ, **GIT_ENV}
        for c in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "retry: back off on 429"]):
            subprocess.run(["git", *c], cwd=d, env=env, check=True, capture_output=True)
        return d

    def test_run_output_has_the_lines_the_docs_show(self):
        d = self.git_repo()
        self.script("w1", rec("working"), rec("done"))
        self.assertEqual(self.cli("start", "w1", d, self.task, "--verify", "true")[0], 0)
        self.report(d, "w1", "STATUS: done\nCHANGED: a: b\nVERIFIED: x\nCOMMIT: none\nOPEN: none\nNEXT: none\n")
        code, out, err = self.cli("collect")
        self.assertEqual(code, 0, out + err)
        shown = [l for l in re.search(r"```text\n(== .*?)```", SKILL, re.S).group(1).splitlines()]
        patterns = {
            "== ": r"^== w1   state: done   \d+s$",
            "git: ": r"^git: [0-9a-f]+ .* · branch main · dirty \d+$",
            "verify: ": r"^verify: exit 0$",
            "report:": r"^report:$",
        }
        for prefix, rx in patterns.items():
            doc_line = [l for l in shown if l.startswith(prefix)][0]
            real_line = [l for l in out.splitlines() if l.startswith(prefix)][0]
            self.assertRegex(real_line, rx)
            # the documented line has the same fields as the real one (names differ, shape does not)
            self.assertEqual(doc_line.count(" · "), real_line.count(" · "), f"docs show: {doc_line!r}, tool prints: {real_line!r}")
        for key in ("STATUS:", "CHANGED:", "VERIFIED:", "COMMIT:", "OPEN:", "NEXT:"):
            self.assertTrue(any(l.startswith("| " + key) for l in shown))  # report lines are delimited as data
        self.assertTrue(any(l.startswith("auto-approved (") for l in shown))
        # README shows the same example
        readme_git = [l for l in README.splitlines() if l.startswith("git: ")][0]
        self.assertEqual(readme_git.count(" · "), 2, readme_git)


class TestPolicyFileErrors(Base):
    def write(self, text):
        p = f"{self.tmp.name}/bad-policy.json"
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_run_with_missing_policy_stops_before_a_pane_opens(self):
        d = self.workdir()
        self.script("w1", rec("working"))
        code, out, err = self.cli("run", "w1", d, self.task, "--policy", "/nonexistent/policy.json")
        self.assertEqual(code, 6, out + err)
        self.assertIn("policy", err)
        self.assertEqual(self.fake()["blocks"], {})
        self.assertFalse(os.path.exists(self.ledger))

    def test_collect_with_missing_policy_is_a_clean_error(self):
        self.script("w1", rec("working"))
        self.cli("start", "w1", self.workdir(), self.task)
        code, out, err = self.cli("collect", "--policy", "/nonexistent/policy.json")
        self.assertEqual(code, 6, out + err)
        self.assertIn("policy", err)

    def test_broken_policies_are_refused_with_what_to_fix(self):
        for text in ("not json", "[]", '{"approve": "^Bash: git status"}', '{"approve": ["("]}',
                     '{"aprove": ["^Bash: ls"]}', '{"approve": [""]}', '{"never": [1]}'):
            code, out, err = self.cli("collect", "--policy", self.write(text))
            self.assertEqual(code, 6, f"{text}: {out}{err}")
            self.assertIn("policy", err, text)
            self.assertIn("approve", err, f"{text}: the message does not say what a policy looks like")

    def test_a_string_instead_of_a_list_does_not_approve_everything(self):
        # "approve": "^Bash: git status" used to be read as one pattern per character, and 'B' matches any Bash prompt.
        d = self.workdir()
        prompt = rec("blocked", "permission", "approve Bash: curl http://x.invalid")
        self.script("w1", rec("working"), prompt)
        self.cli("start", "w1", d, self.task)
        code, out, err = self.cli("collect", "--policy", self.write('{"approve": "^Bash: git status"}'))
        self.assertEqual(self.sent(), [])
        self.assertNotEqual(code, 0)


class TestUncleanRecovery(Base):
    def unclean(self):
        d = self.workdir()
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", d, self.task)
        return self.cli("collect")  # no report: unclean

    def test_unclean_worker_keeps_its_pane_so_screen_and_send_work(self):
        code, out, err = self.unclean()
        self.assertEqual(code, 1)
        blk = self.block_of("w1")
        self.assertFalse(self.fake()["blocks"][blk]["closed"], "the docs say: screen, then send")
        self.assertIn(f"block: {blk}", out)

    def test_send_after_unclean_makes_collect_wait_for_the_new_turn(self):
        self.unclean()
        blk = self.block_of("w1")
        code, out, err = self.cli("send", blk, "write", "the", "report")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.ledger_runs()[-1]["state"], "open")
        code, out, err = self.cli("collect", "--timeout", "0")
        self.assertNotIn("already finished", out)

    def test_close_works_on_an_unclean_worker(self):
        self.unclean()
        blk = self.block_of("w1")
        self.assertEqual(self.cli("close", blk)[0], 0)
        self.assertEqual(self.ledger_runs()[-1]["state"], "closed")


class TestErrorsSayWhatNext(Base):
    def err(self, *args):
        code, out, err = self.cli(*args)
        self.assertNotEqual(code, 0, out)
        return err

    def test_messages_name_the_next_command(self):
        d = self.workdir()
        self.script("w1", rec("working"))
        self.cli("start", "w1", d, self.task)
        b = self.block_of("w1")
        for args in (("send", b, "x"), ("approve", b), ("deny", b), ("answer", b, "1")):
            self.assertRegex(self.err(*args), r"rex-crew (status|collect|screen|approve|deny|answer)", args)
        blocked = rec("blocked", "permission", "approve Bash: ls")
        self.script("w2", rec("working"), blocked)
        self.cli("start", "w2", self.workdir("o"), self.task)
        b2 = self.block_of("w2")
        self.cli("collect", "--timeout", "0")
        self.assertRegex(self.err("approve", b2, "--match", "zzz"), r"rex-crew (approve|deny)")

    def test_start_stdin_task(self):
        d = self.workdir()
        self.script("w1", rec("working"))
        with mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"from stdin\n"))):
            code, out, err = self.cli("start", "w1", d, "-")
        self.assertEqual(code, 0, err)
        self.assertTrue(open(f"{d}/.rex-crew/task-w1.md").read().startswith("from stdin"))


if __name__ == "__main__":
    unittest.main()
