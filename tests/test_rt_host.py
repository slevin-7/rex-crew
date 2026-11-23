"""Red-team tests for everything that crosses to the host. Run: python3 -m unittest tests/test_rt_host.py"""
import importlib.machinery, importlib.util, os, subprocess, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, rec

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


class HostBase(Base):
    def setUp(self):
        super().setUp()
        alt = os.environ.get("RT_SCRIPT")  # run these tests against another copy of the script
        if alt:
            loader = importlib.machinery.SourceFileLoader("rex_crew_alt", alt)
            self.mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
            loader.exec_module(self.mod)
            sl = mock.patch.object(self.mod.time, "sleep", lambda s: None)
            sl.start(); self.addCleanup(sl.stop)

    def finished(self, d, name="w1", *start_extra):
        self.script(name, rec("working"), rec("done"))
        self.assertEqual(self.cli("start", name, d, self.task, *start_extra)[0], 0)
        return self.cli("collect")

    def chdir(self, d):
        old = os.getcwd()
        os.chdir(d)
        self.addCleanup(os.chdir, old)


class TestTaskUpload(HostBase):
    def upload(self, data, d=None):
        d = d or self.workdir()
        with open(self.task, "wb") as f:
            f.write(data)
        self.script("w1", rec("working"))
        code, out, err = self.cli("start", "w1", d, self.task)
        self.assertEqual(code, 0, out + err)
        with open(f"{d}/.rex-crew/task-w1.md", "rb") as f:
            return f.read()

    def test_non_utf8_task_arrives_byte_for_byte(self):
        got = self.upload(b"caf\xe9 \xff\xfe\n")
        self.assertTrue(got.startswith(b"caf\xe9 \xff\xfe\n"))

    def test_crlf_task_keeps_its_line_ends(self):
        self.assertTrue(self.upload(b"one\r\ntwo\r\n").startswith(b"one\r\ntwo\r\n"))

    def test_task_sizes_around_the_piece_boundary(self):
        # base64 pieces are 30000 characters = 22500 raw bytes
        proto = len(self.mod.REPORT_PROTOCOL.format(report=".rex-crew/report-w1.md").encode())
        for n in (22500 - proto - 1, 22500 - proto, 22500 - proto + 1, 22500 * 2 - proto, 70000):
            d = self.workdir(f"t{n}")
            body = b"x" * n
            self.assertTrue(self.upload(body, d).startswith(body), n)

    def test_missing_task_file_is_a_clean_error(self):
        code, _, err = self.cli("start", "w1", self.workdir(), "/nonexistent/task.md")
        self.assertEqual(code, 2)
        self.assertIn("cannot read the task", err)

    def test_awkward_directory_names(self):
        for i, name in enumerate(["sp ace", "q'uote", 'd"q', "dol$HOME", "back$(x)tick\x60id\x60", "trail", "new\nline"]):
            d = self.workdir(name)
            self.script(f"w{i}", rec("working"))
            code, out, err = self.cli("start", f"w{i}", d + ("/" if name == "trail" else ""), self.task)
            self.assertEqual(code, 0, f"{name!r}: {out}{err}")
            self.assertTrue(os.path.exists(f"{d}/.rex-crew/task-w{i}.md"), name)

    def test_not_a_git_repository_still_works(self):
        d = self.workdir()
        code, out, _ = self.finished(d)
        self.assertEqual(code, 1)
        self.assertIn("ATTENTION: the worker wrote no report file", out)
        self.assertTrue(os.path.exists(f"{d}/.rex-crew/task-w1.md"))


class TestRexNoise(HostBase):
    def test_warning_before_json_does_not_break_start_or_collect(self):
        d = self.workdir()
        self.script("w1", rec("working"), rec("done"))
        with mock.patch.dict(os.environ, {"FAKE_REX_WARN": "rex: warning: new version available {upgrade}"}):
            code, out, err = self.cli("start", "w1", d, self.task)
            self.assertEqual(code, 0, out + err)
            self.report(d, "w1")  # after start: start clears an old report
            code, out, err = self.cli("collect")
        self.assertEqual(code, 0, out + err)
        self.assertIn("state: done", out)

    def test_bracket_warning_before_json(self):
        d = self.workdir()
        self.script("w1", rec("working"))
        with mock.patch.dict(os.environ, {"FAKE_REX_WARN": "[warn] clock skew"}):
            self.assertEqual(self.cli("start", "w1", d, self.task)[0], 0)
            code, out, _ = self.cli("status")
        self.assertEqual(code, 0)
        self.assertIn("working", out)


class TestFinish(HostBase):
    def test_report_symlink_to_a_secret_is_not_read(self):
        d = self.workdir()
        secret = f"{self.tmp.name}/secret.txt"
        with open(secret, "w") as f:
            f.write("STATUS: done\nTOPSECRET-TOKEN\n")
        os.makedirs(f"{d}/.rex-crew")
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", d, self.task)
        os.symlink(secret, f"{d}/.rex-crew/report-w1.md")
        code, out, _ = self.cli("collect")
        self.assertNotIn("TOPSECRET", out)
        self.assertEqual(code, 1)
        self.assertIn("symlink", out)

    def test_huge_report_is_cut(self):
        d = self.workdir()
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", d, self.task)
        self.report(d, "w1", "STATUS: done\n" + "A" * 3_000_000 + "\nEND-OF-REPORT\n")
        code, out, _ = self.cli("collect")
        self.assertLess(len(out), 200_000)
        self.assertNotIn("END-OF-REPORT", out)
        self.assertEqual(code, 1)
        self.assertIn("ATTENTION: the report is longer than", out)

    def test_binary_report_does_not_crash(self):
        d = self.workdir()
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", d, self.task)
        os.makedirs(f"{d}/.rex-crew", exist_ok=True)
        with open(f"{d}/.rex-crew/report-w1.md", "wb") as f:
            f.write(b"STATUS: done\n\x00\xff\xfe\x80binary\n")
        code, out, err = self.cli("collect")
        self.assertIn("state:", out, err)

    def test_empty_report_counts_as_no_report(self):
        d = self.workdir()
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", d, self.task)
        self.report(d, "w1", "")
        code, out, _ = self.cli("collect")
        self.assertEqual(code, 1)
        self.assertIn("no report", out)

    def test_verify_with_trailing_comment(self):
        code, out, _ = self.finished(self.workdir(), "w1", "--verify", "echo fine # this is a comment")
        self.assertIn("verify: exit 0", out)

    def test_verify_with_quotes_and_newlines(self):
        d = self.workdir()
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", d, self.task, "--verify", "echo \"it's\"\necho 'two $x'\nexit 4")
        self.report(d, "w1")
        code, out, _ = self.cli("collect")
        self.assertIn("verify: exit 4", out)
        self.assertIn("it's", out)
        self.assertIn("two $x", out)

    def test_verify_printing_megabytes_on_one_line(self):
        d = self.workdir()
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", d, self.task, "--verify", "head -c 3000000 /dev/zero | tr '\\0' 'x'; echo")
        self.report(d, "w1")
        code, out, _ = self.cli("collect")
        self.assertLess(len(out), 20_000)
        self.assertIn("verify: exit 0", out)

    def test_git_state_in_a_bare_repository_and_plain_repository(self):
        env = {**os.environ, **GIT_ENV}
        bare = self.workdir("bare.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True, env=env)
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", bare, self.task)
        self.report(bare, "w1")
        code, out, err = self.cli("collect")
        self.assertEqual(code, 0, out + err)
        repo = self.workdir("repo")
        subprocess.run(["git", "init", "-q", repo], check=True, env=env)
        open(f"{repo}/f", "w").write("x")
        self.script("w2", rec("working"), rec("done"))
        self.cli("start", "w2", repo, self.task)
        self.report(repo, "w2")
        code, out, _ = self.cli("collect", "w2")
        self.assertIn("ATTENTION: uncommitted changes", out)
        self.assertIn("dirty 1", out)  # .rex-crew/ is kept out of git: only f is dirty


class TestCheckout(HostBase):
    def repo(self, name="repo"):
        d = self.workdir(name)
        env = {**os.environ, **GIT_ENV}
        subprocess.run(["git", "init", "-q", d], check=True, env=env)
        subprocess.run(["git", "-C", d, "commit", "-q", "--allow-empty", "-m", "init"], check=True, env=env)
        return d

    def test_relative_repo_path_is_resolved_on_the_host(self):
        repo = self.repo()
        self.chdir(repo)
        code, out, err = self.cli("checkout", "w1", ".")
        self.assertEqual(code, 0, out + err)
        wt = out.strip()
        self.assertEqual(os.path.realpath(wt), os.path.realpath(f"{self.tmp.name}/.rex-crew-worktrees/repo/w1"))
        self.assertTrue(os.path.isdir(wt))

    def test_awkward_repo_name_and_copy(self):
        repo = self.repo("my repo's")
        os.makedirs(f"{repo}/gen dir")
        open(f"{repo}/gen dir/a.txt", "w").write("generated")
        code, out, err = self.cli("checkout", "w1", repo + "/", "--copy", "gen dir")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(open(f"{out.strip()}/gen dir/a.txt").read(), "generated")

    def test_checkout_name_cannot_escape(self):
        code, _, err = self.cli("checkout", "../evil", self.repo())
        self.assertEqual(code, 2)

    def test_not_a_git_repository_is_a_clean_failure(self):
        code, _, err = self.cli("checkout", "w1", self.workdir("plain"))
        self.assertEqual(code, 1)
        self.assertIn("checkout failed", err)


class TestSh(HostBase):
    def test_sh_returns_output_and_exit_status(self):
        code, out, _ = self.cli("sh", "echo hi; exit 7")
        self.assertEqual(code, 7)
        self.assertIn("hi", out)


if __name__ == "__main__":
    unittest.main()
