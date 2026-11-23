"""Red-team tests, host boundary pass 2. Run: python3 -m unittest tests/test_rt_host2.py"""
import os, subprocess, sys, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import Base, SCRIPT, rec
from test_rt_host import GIT_ENV, HostBase, TestCheckout


class TestVerifyHang(HostBase):
    def test_hung_verify_is_bounded_and_killed(self):
        d = self.workdir()
        pidfile = f"{self.tmp.name}/verify.pid"
        self.script("w1", rec("working"), rec("done"))
        verify = f"sh -c 'echo $$ > {pidfile}; exec sleep 37'"
        self.cli("start", "w1", d, self.task, "--verify", verify, "--verify-timeout", "1")
        self.report(d, "w1")
        t0 = time.time()
        code, out, err = self.cli("collect")
        self.assertLess(time.time() - t0, 20, "finish waited for the hung verify command")
        self.assertEqual(code, 1, out + err)
        self.assertIn("ATTENTION: verify did not finish within 1s and was killed", out)
        with open(pidfile) as fh:
            pid = int(fh.read())
        for _ in range(30):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.2)
        else:
            os.kill(pid, 9)
            self.fail("the hung verify command is still running")

    def test_fast_verify_is_unaffected_by_the_watchdog(self):
        t0 = time.time()
        code, out, _ = self.finished(self.workdir(), "w1", "--verify", "echo fine; exit 3")
        self.assertIn("verify: exit 3", out)
        self.assertIn("fine", out)
        self.assertLess(time.time() - t0, 20)  # the watchdog's sleep must not be waited for


class TestStatusLine(HostBase):
    def collect_with(self, text):
        d = self.workdir()
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", d, self.task)
        self.report(d, "w1", text)
        return self.cli("collect")

    def test_report_without_status_line_is_not_clean(self):
        code, out, _ = self.collect_with("CHANGED: a\nVERIFIED: yes\n")
        self.assertEqual(code, 1)
        self.assertIn("ATTENTION: the report has no STATUS line", out)

    def test_template_status_line_is_not_clean(self):
        code, out, _ = self.collect_with("STATUS: done | blocked | failed\nCHANGED: a\n")
        self.assertEqual(code, 1)
        self.assertIn("no STATUS line", out)

    def test_real_status_lines_still_work(self):
        self.assertEqual(self.collect_with("STATUS: done\nCHANGED: a\n")[0], 0)

    def test_status_with_decoration(self):
        self.assertEqual(self.collect_with("STATUS: **done**\n")[0], 0)

    def test_failed_status_after_template_line(self):
        code, out, _ = self.collect_with("STATUS: done | blocked | failed\nSTATUS: failed\n")
        self.assertEqual(code, 1)
        self.assertIn("reports STATUS failed", out)


class TestCheckoutCleanup(TestCheckout):
    def branches(self, repo):
        return subprocess.run(["git", "-C", repo, "branch", "--list", "crew/*"], capture_output=True, text=True).stdout.strip()

    def test_failed_copy_leaves_nothing_and_retry_works(self):
        repo = self.repo()
        code, out, err = self.cli("checkout", "w1", repo, "--copy", "does-not-exist")
        self.assertEqual(code, 1)
        self.assertIn("removed", err)
        self.assertEqual(self.branches(repo), "")
        self.assertFalse(os.path.exists(f"{self.tmp.name}/.rex-crew-worktrees/repo/w1"))
        code, out, err = self.cli("checkout", "w1", repo)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(os.path.isdir(out.strip()))

    def test_second_checkout_of_the_same_name_is_a_clear_message(self):
        repo = self.repo()
        self.assertEqual(self.cli("checkout", "w1", repo)[0], 0)
        code, _, err = self.cli("checkout", "w1", repo)
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertNotIn("fatal", err)

    def test_existing_branch_without_worktree_says_what_to_do(self):
        repo = self.repo()
        subprocess.run(["git", "-C", repo, "branch", "crew/w1"], check=True, env={**os.environ, **GIT_ENV})
        code, _, err = self.cli("checkout", "w1", repo)
        self.assertEqual(code, 1)
        self.assertIn("--branch", err)


class TestStartRace(HostBase):
    def test_two_simultaneous_starts_for_one_directory(self):
        d = self.workdir()
        self.script("a", rec("working"))
        self.script("b", rec("working"))
        procs = [subprocess.Popen([sys.executable, SCRIPT, "start", n, d, self.task], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for n in ("a", "b")]
        codes = sorted(p.wait(30) for p in procs)
        for p in procs:
            p.stdout.close(); p.stderr.close()
        self.assertEqual(codes, [0, 2])
        self.assertEqual(len([r for r in self.ledger_runs() if r["state"] == "open"]), 1)

    def test_failed_start_does_not_block_the_directory(self):
        d = self.workdir()
        code, _, _ = self.cli("start", "a", d, "/nonexistent/task.md")
        self.assertEqual(code, 2)
        self.script("b", rec("working"))
        self.assertEqual(self.cli("start", "b", d, self.task)[0], 0)

    def test_half_started_claim_is_released_when_the_upload_fails(self):
        gone = f"{self.tmp.name}/missing"
        code, _, _ = self.cli("start", "a", gone, self.task)
        self.assertEqual(code, 1)
        self.assertEqual([r for r in self.ledger_runs() if r["state"] in ("starting", "open")], [])


class TestSymlinkDir(HostBase):
    def test_symlink_and_real_path_are_one_directory(self):
        real = self.workdir("real")
        link = f"{self.tmp.name}/link"
        os.symlink(real, link)
        self.script("a", rec("working"))
        self.script("b", rec("working"))
        self.assertEqual(self.cli("start", "a", link, self.task)[0], 0)
        code, _, err = self.cli("start", "b", real, self.task)
        self.assertEqual(code, 2, err)
        self.assertIn("already has a live worker", err)

    def test_real_path_then_symlink(self):
        real = self.workdir("real")
        link = f"{self.tmp.name}/link"
        os.symlink(real, link)
        self.script("a", rec("working"))
        self.assertEqual(self.cli("start", "a", real, self.task)[0], 0)
        self.assertEqual(self.cli("start", "b", link, self.task)[0], 2)

    def test_symlink_dir_works_for_the_report(self):
        real = self.workdir("real")
        link = f"{self.tmp.name}/link"
        os.symlink(real, link)
        self.script("w1", rec("working"), rec("done"))
        self.assertEqual(self.cli("start", "w1", link, self.task)[0], 0)
        self.report(real, "w1")  # start clears old reports; a result without one is "unclean", so write it after start
        code, out, _ = self.cli("collect")
        self.assertIn("state: done", out)


class TestShFaithful(HostBase):
    def test_quotes_newlines_and_exit_status(self):
        code, out, _ = self.cli("sh", "printf '%s\\n' \"it's\" 'two $x'; printf 'a\\n\\nb\\n'; exit 5")
        self.assertEqual(code, 5)
        self.assertEqual(out.splitlines(), ["it's", "two $x", "a", "", "b"])

    def test_cut_output_is_announced(self):
        with mock.patch.dict(os.environ, {"FAKE_REX_KEEP_LINES": "4"}):
            code, out, err = self.cli("sh", "seq 1 50")
        self.assertIn("output is cut", err)
        self.assertEqual(out.split(), ["47", "48", "49", "50"])

    def test_uncut_output_has_no_note_and_no_marker(self):
        code, out, err = self.cli("sh", "seq 1 3")
        self.assertEqual(err, "")
        self.assertEqual(out.split(), ["1", "2", "3"])
        self.assertNotIn("@@", out)


class TestFinishCut(HostBase):
    def test_cut_host_output_is_not_read_as_no_report(self):
        d = self.workdir()
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", d, self.task, "--verify", "seq 1 30")
        self.report(d, "w1")
        with mock.patch.dict(os.environ, {"FAKE_REX_KEEP_LINES": "10"}):
            code, out, _ = self.cli("collect")
        self.assertEqual(code, 1)
        self.assertIn("output is incomplete", out)
        self.assertNotIn("wrote no report file", out)

    def test_directory_removed_before_finish(self):
        d = self.workdir()
        self.script("w1", rec("working"), rec("done"))
        self.cli("start", "w1", d, self.task)
        subprocess.run(["rm", "-rf", d], check=True)
        code, out, _ = self.cli("collect")
        self.assertEqual(code, 1)
        self.assertIn("directory is gone", out)


if __name__ == "__main__":
    unittest.main()
