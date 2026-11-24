"""Red-team tests for fanout and integrate. Run: python3 -m unittest tests/test_rt_fanout.py"""
import json, os, subprocess, sys, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import rec
from test_rt_host import GIT_ENV, HostBase

ENV = {**os.environ, **GIT_ENV}


def git(repo, *args, check=True):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=ENV)
    if check and p.returncode:
        raise AssertionError(f"git {args}: {p.stderr}")
    return p.stdout.strip()


class GitBase(HostBase):
    def repo(self, name="repo", files=None):
        d = self.workdir(name)
        if os.path.exists(f"{d}/.git"):
            return d
        subprocess.run(["git", "init", "-q", d], check=True, env=ENV)
        for f, text in (files or {"base.txt": "base\n"}).items():
            self.put(d, f, text)
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", "init")
        return d

    def put(self, d, f, text):
        os.makedirs(os.path.dirname(os.path.join(d, f)) or d, exist_ok=True)
        with open(os.path.join(d, f), "w") as fh:
            fh.write(text)

    def branch(self, repo, name, files, delete=()):
        """crew/NAME with one commit that writes files (and deletes some), made without touching the checked-out branch."""
        main = git(repo, "branch", "--show-current")
        git(repo, "checkout", "-q", "-b", f"crew/{name}")
        for f, text in files.items():
            self.put(repo, f, text)
        for f in delete:
            os.remove(os.path.join(repo, f))
        if files or delete:
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", f"work of {name}")
        git(repo, "checkout", "-q", main)

    def clean(self, repo):
        return git(repo, "status", "--porcelain") == ""

    def branches(self, repo):
        return git(repo, "branch", "--list", "crew/*")


class TestIntegrate(GitBase):
    def test_merges_every_branch_in_order(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        self.branch(repo, "b", {"b.txt": "b\n"})
        code, out, err = self.cli("integrate", repo, "a", "b")
        self.assertEqual(code, 0, out + err)
        self.assertIn("crew/a: merged", out)
        self.assertIn("crew/b: merged", out)
        self.assertTrue(os.path.exists(f"{repo}/a.txt") and os.path.exists(f"{repo}/b.txt"))
        self.assertTrue(self.clean(repo))

    def test_conflict_stops_names_the_files_and_leaves_the_tree_clean(self):
        repo = self.repo()
        self.branch(repo, "a", {"base.txt": "from a\n", "a.txt": "a\n"})
        self.branch(repo, "b", {"base.txt": "from b\n", "dä ta.txt": "x\n"})
        self.branch(repo, "c", {"c.txt": "c\n"})
        code, out, err = self.cli("integrate", repo, "a", "b", "c")
        self.assertEqual(code, 1, out + err)
        self.assertIn("crew/a: merged", out)
        self.assertRegex(out, r"crew/b: conflict.*base\.txt")
        self.assertIn("crew/c: not attempted", out)
        self.assertNotIn("crew/c: merged", out)
        self.assertTrue(self.clean(repo), git(repo, "status"))
        self.assertFalse(os.path.exists(f"{repo}/c.txt"))
        self.assertEqual(open(f"{repo}/base.txt").read(), "from a\n")
        self.assertFalse(os.path.exists(git(repo, "rev-parse", "--git-path", "MERGE_HEAD")))

    def test_conflict_files_are_listed_readably_even_for_odd_names(self):
        repo = self.repo(files={"dä ta.txt": "base\n"})
        self.branch(repo, "a", {"dä ta.txt": "a\n"})
        self.branch(repo, "b", {"dä ta.txt": "b\n"})
        code, out, _ = self.cli("integrate", repo, "a", "b")
        self.assertEqual(code, 1)
        self.assertIn("dä ta.txt", out)

    def test_delete_modify_conflict_is_a_conflict(self):
        repo = self.repo()
        self.branch(repo, "a", {}, delete=["base.txt"])
        self.branch(repo, "b", {"base.txt": "changed\n"})
        code, out, _ = self.cli("integrate", repo, "a", "b")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"crew/b: conflict.*base\.txt")
        self.assertTrue(self.clean(repo))

    def test_failed_verify_rolls_that_merge_back_and_stops(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        self.branch(repo, "b", {"bad.txt": "x\n"})
        self.branch(repo, "c", {"c.txt": "c\n"})
        after_a = None
        code, out, err = self.cli("integrate", repo, "a", "b", "c", "--verify", "test ! -e bad.txt; r=$?; echo checked; exit $r")
        self.assertEqual(code, 1, out + err)
        self.assertIn("crew/a: merged", out)
        self.assertRegex(out, r"crew/b: verify failed \(exit 1\)")
        self.assertIn("checked", out)
        self.assertIn("crew/c: not attempted", out)
        self.assertFalse(os.path.exists(f"{repo}/bad.txt"), "the merge that failed verify is still in the tree")
        self.assertTrue(os.path.exists(f"{repo}/a.txt"))
        self.assertTrue(self.clean(repo))
        self.assertEqual(git(repo, "rev-list", "--count", "--merges", "HEAD"), "1")

    def test_verify_that_leaves_files_behind_does_not_leave_the_tree_dirty(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        code, out, _ = self.cli("integrate", repo, "a", "--verify", "touch build-junk; exit 5")
        self.assertEqual(code, 1)
        self.assertIn("verify failed (exit 5)", out)
        self.assertTrue(self.clean(repo), git(repo, "status", "--porcelain"))

    def test_verify_runs_after_each_merge_in_the_repo(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        self.branch(repo, "b", {"b.txt": "b\n"})
        log = f"{self.tmp.name}/verify.log"
        code, out, err = self.cli("integrate", repo, "a", "b", "--verify", f"ls *.txt | tr '\\n' ' ' >> {log}; echo >> {log}")
        self.assertEqual(code, 0, out + err)
        self.assertEqual([l.strip() for l in open(log)], ["a.txt base.txt", "a.txt b.txt base.txt"])

    def test_hung_verify_is_bounded(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        t0 = time.time()
        code, out, _ = self.cli("integrate", repo, "a", "--verify", "sleep 37", "--verify-timeout", "1")
        self.assertLess(time.time() - t0, 20)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"verify (did not finish|failed).*")
        self.assertIn("1s", out)
        self.assertTrue(self.clean(repo))
        self.assertFalse(os.path.exists(f"{repo}/a.txt"))

    def test_dirty_tree_is_refused_before_anything_is_merged(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        self.put(repo, "base.txt", "my uncommitted edit\n")
        head = git(repo, "rev-parse", "HEAD")
        code, out, err = self.cli("integrate", repo, "a")
        self.assertEqual(code, 2, out + err)
        self.assertIn("not clean", err)
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)
        self.assertEqual(open(f"{repo}/base.txt").read(), "my uncommitted edit\n")

    def test_untracked_file_counts_as_dirty(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        self.put(repo, "a.txt", "mine\n")
        code, _, err = self.cli("integrate", repo, "a")
        self.assertEqual(code, 2)
        self.assertEqual(open(f"{repo}/a.txt").read(), "mine\n")

    def test_missing_branch_refuses_everything_up_front(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        head = git(repo, "rev-parse", "HEAD")
        code, _, err = self.cli("integrate", repo, "a", "typo")
        self.assertEqual(code, 2)
        self.assertIn("crew/typo", err)
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head, "a was merged although typo does not exist")

    def test_detached_head_is_refused(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        git(repo, "checkout", "-q", "--detach")
        code, _, err = self.cli("integrate", repo, "a")
        self.assertEqual(code, 2)
        self.assertIn("detached", err)

    def test_merge_in_progress_is_refused(self):
        repo = self.repo()
        self.branch(repo, "a", {"base.txt": "a\n"})
        git(repo, "commit", "-q", "--allow-empty", "-m", "x")
        self.put(repo, "base.txt", "m\n"); git(repo, "commit", "-qam", "m")
        git(repo, "merge", "crew/a", check=False)
        code, _, err = self.cli("integrate", repo, "a")
        self.assertEqual(code, 2)

    def test_bad_names_cannot_reach_git(self):
        repo = self.repo()
        for bad in ("--abort", "../x", "A b", "a;rm", "-x", ""):
            code, _, err = self.cli("integrate", repo, bad)
            self.assertEqual(code, 6, bad)

    def test_same_name_twice_is_refused(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        code, _, err = self.cli("integrate", repo, "a", "a")
        self.assertEqual(code, 6)
        self.assertIn("twice", err)

    def test_the_current_branch_cannot_be_merged_into_itself(self):
        repo = self.repo()
        git(repo, "checkout", "-q", "-b", "crew/a")
        code, _, err = self.cli("integrate", repo, "a")
        self.assertEqual(code, 2)
        self.assertIn("current branch", err)

    def test_a_branch_without_new_commits_is_not_reported_as_merged(self):
        repo = self.repo()
        self.branch(repo, "a", {})          # the worker never committed
        self.branch(repo, "b", {"b.txt": "b\n"})
        code, out, _ = self.cli("integrate", repo, "a", "b")
        self.assertNotIn("crew/a: merged", out)
        self.assertIn("crew/a: nothing to merge", out)
        self.assertIn("crew/b: merged", out)
        self.assertEqual(code, 1)

    def test_second_integrate_of_the_same_branch_has_nothing_to_merge(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        self.assertEqual(self.cli("integrate", repo, "a")[0], 0)
        code, out, _ = self.cli("integrate", repo, "a")
        self.assertIn("nothing to merge", out)
        self.assertEqual(code, 1)

    def test_uncommitted_work_in_the_workers_checkout_is_called_out(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        wt = f"{self.tmp.name}/wt-a"
        git(repo, "worktree", "add", "-q", wt, "crew/a")
        self.put(wt, "unsaved.txt", "never committed\n")
        code, out, _ = self.cli("integrate", repo, "a")
        self.assertIn("crew/a: merged", out)
        self.assertRegex(out, r"uncommitted")
        self.assertFalse(os.path.exists(f"{repo}/unsaved.txt"))

    def test_relative_repo_and_odd_repo_path(self):
        repo = self.repo("my repo's")
        self.branch(repo, "a", {"a.txt": "a\n"})
        self.chdir(repo)
        code, out, err = self.cli("integrate", ".", "a")
        self.assertEqual(code, 0, out + err)

    def test_not_a_git_repository(self):
        code, _, err = self.cli("integrate", self.workdir("plain"), "a")
        self.assertEqual(code, 2)
        self.assertIn("not a git repository", err)

    def test_a_branch_named_like_a_tag_merges_the_branch(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        git(repo, "tag", "crew/a", "HEAD")        # an ambiguous name must not change what is merged
        code, out, err = self.cli("integrate", repo, "a")
        self.assertEqual(code, 0, out + err)
        self.assertTrue(os.path.exists(f"{repo}/a.txt"))


class TestIntegrateHostile(GitBase):
    def test_hidden_untracked_files_still_count_as_dirty(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        git(repo, "config", "status.showUntrackedFiles", "no")  # status would hide the file; a failed verify then cleans it away
        self.put(repo, "notes.txt", "my only copy\n")
        code, out, err = self.cli("integrate", repo, "a", "--verify", "false")
        self.assertEqual(code, 2, out + err)
        self.assertIn("not clean", err)
        with open(f"{repo}/notes.txt") as f:
            self.assertEqual(f.read(), "my only copy\n")

    def test_empty_verify_is_an_error_not_a_skipped_check(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        code, _, err = self.cli("integrate", repo, "a", "--verify", "")
        self.assertEqual(code, 6)
        self.assertIn("verify", err)
        self.assertFalse(os.path.exists(f"{repo}/a.txt"))

    def test_verify_timeout_must_be_positive(self):
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        for bad in ("0", "-3"):
            code, _, err = self.cli("integrate", repo, "a", "--verify", "true", "--verify-timeout", bad)
            self.assertEqual(code, 6, bad)
            self.assertIn("verify-timeout", err)
        self.assertFalse(os.path.exists(f"{repo}/a.txt"))

    def test_unrelated_history_is_a_failed_merge_with_a_clean_tree(self):
        repo = self.repo()
        sha = git(repo, "commit-tree", "HEAD^{tree}", "-m", "unrelated")
        git(repo, "branch", "crew/a", sha)
        code, out, _ = self.cli("integrate", repo, "a")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"crew/a: merge failed: .*unrelated")
        self.assertTrue(self.clean(repo))

    def test_a_huge_conflict_is_still_reported_and_the_tree_is_clean(self):
        files = {f"f{i}.txt": "base\n" for i in range(80)}
        repo = self.repo(files=files)
        self.branch(repo, "a", {f: "a\n" for f in files})
        self.branch(repo, "b", {f: "b\n" for f in files})
        code, out, _ = self.cli("integrate", repo, "a", "b")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"crew/b: conflict: f\d+\.txt")
        self.assertRegex(out, r"and \d+ more")
        self.assertIn("stopped at crew/b", out)
        self.assertTrue(self.clean(repo))


class TestDocumentedPlan(GitBase):
    def test_the_plan_in_the_docs_is_accepted_by_the_loader(self):
        import re
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        for doc in (os.path.join(root, "README.md"), os.path.join(root, "skills", "rex-crew", "SKILL.md")):
            with open(doc) as f:
                blocks = re.findall(r"```jsonc\n(.*?)```", f.read(), re.S)
            self.assertEqual(len(blocks), 1, doc)
            d = self.workdir("docplan")
            with open(f"{d}/api.md", "w") as f:
                f.write("task\n")
            with open(f"{d}/plan.json", "w") as f:
                f.write(blocks[0])
            plan = self.mod.load_plan(f"{d}/plan.json")
            self.assertEqual([w["name"] for w in plan["workers"]], ["api", "docs"])


class TestFanout(GitBase):
    def plan(self, **kw):
        repo = kw.pop("repo_path", None) or self.repo()
        os.makedirs(f"{self.tmp.name}/plans", exist_ok=True)
        with open(f"{self.tmp.name}/plans/a.md", "w") as f:
            f.write("task for a\n")
        p = {"repo": repo, "base": git(repo, "branch", "--show-current", check=False) or "HEAD", "claude_args": ["--permission-mode", "acceptEdits"],
             "verify": "true", "workers": [{"name": "a", "task_file": "a.md"}, {"name": "b", "task": "inline task for b"}]}
        p.update(kw)
        path = f"{self.tmp.name}/plans/plan.json"
        with open(path, "w") as f:
            json.dump(p, f)
        return path, p

    def nothing_left(self, repo):
        self.assertEqual(self.branches(repo), "")
        self.assertFalse(os.path.exists(f"{self.tmp.name}/.rex-crew-worktrees"), os.listdir(f"{self.tmp.name}"))
        live = [r for r in (self.ledger_runs() if os.path.exists(self.ledger) else []) if r["state"] in ("open", "starting")]
        self.assertEqual(live, [])
        self.assertEqual([b for b in self.fake()["blocks"].values() if not b["closed"]], [])

    def test_creates_a_checkout_per_worker_and_starts_them_all(self):
        path, p = self.plan(copy=["gen.txt"])
        self.put(p["repo"], "gen.txt", "generated\n")
        code, out, err = self.cli("fanout", path)
        self.assertEqual(code, 0, out + err)
        runs = {r["name"]: r for r in self.ledger_runs()}
        self.assertEqual(sorted(runs), ["a", "b"])
        for n, text in (("a", "task for a\n"), ("b", "inline task for b")):
            self.assertTrue(runs[n]["dir"].endswith(f"/.rex-crew-worktrees/repo/{n}"), runs[n]["dir"])
            self.assertEqual(runs[n]["state"], "open")
            self.assertEqual(runs[n]["verify"], "true")
            self.assertTrue(open(f"{runs[n]['dir']}/.rex-crew/task-{n}.md").read().startswith(text))
            self.assertEqual(open(f"{runs[n]['dir']}/gen.txt").read(), "generated\n")
            self.assertEqual(git(runs[n]["dir"], "branch", "--show-current"), f"crew/{n}")
        self.assertEqual(sorted(b["label"] for b in self.fake()["blocks"].values() if not b["closed"]), ["a", "b"])
        self.assertIn("block:", out)

    def test_claude_args_reach_every_worker(self):
        path, _ = self.plan(claude_args=["--permission-mode", "acceptEdits"])
        calls = []
        real = self.mod.launch
        self.mod.launch = lambda rex, a, agent_args, task, claim: (calls.append(list(agent_args)), real(rex, a, agent_args, task, claim))[1]
        self.assertEqual(self.cli("fanout", path)[0], 0)
        self.assertEqual(calls, [["--permission-mode", "acceptEdits"]] * 2)

    def test_base_ref_is_where_the_branches_start(self):
        repo = self.repo()
        base = git(repo, "rev-parse", "HEAD")
        self.put(repo, "later.txt", "x\n"); git(repo, "add", "-A"); git(repo, "commit", "-qm", "later")
        path, _ = self.plan(repo_path=repo, base=base)
        self.assertEqual(self.cli("fanout", path)[0], 0)
        self.assertEqual(git(repo, "rev-parse", "crew/a"), base)

    def bad(self, expect, **kw):
        path, p = self.plan(**kw)
        code, out, err = self.cli("fanout", path)
        self.assertEqual(code, 6, f"{kw}: {out}{err}")
        self.assertIn(expect, err, kw)
        self.nothing_left(p["repo"])

    def test_invalid_plans_are_refused_whole_and_create_nothing(self):
        ok = {"name": "ok", "task": "t"}
        self.bad("NAME", workers=[ok, {"name": "Bad Name", "task": "t"}])
        self.bad("NAME", workers=[ok, {"name": "../evil", "task": "t"}])
        self.bad("twice", workers=[ok, {"name": "ok", "task": "t"}])
        self.bad("task", workers=[ok, {"name": "b"}])
        self.bad("task", workers=[ok, {"name": "b", "task": "t", "task_file": "a.md"}])
        self.bad("task", workers=[ok, {"name": "b", "task": "  \n"}])
        self.bad("cannot read", workers=[ok, {"name": "b", "task_file": "nope.md"}])
        self.bad("unknown", workers=[ok, {"name": "b", "task": "t", "taks": "typo"}])
        self.bad("unknown", verfy="make test")
        self.bad("workers", workers=[])
        self.bad("copy", copy=["../secret"])
        self.bad("copy", copy=["/etc/passwd"])
        self.bad("copy", copy=["does-not-exist"])
        self.bad("claude_args", claude_args="--permission-mode acceptEdits")
        self.bad("base", base="no-such-ref")
        self.bad("base", base="--output=/tmp/x")

    def test_empty_verify_or_a_task_that_cannot_be_encoded_is_refused(self):
        self.bad("verify", verify="")
        self.bad("verify", verify=["make"])
        self.bad("task", workers=[{"name": "a", "task": "broken \ud800 text"}])

    def test_broken_plan_files(self):
        for text, expect in (("not json", "plan"), ("[]", "plan"), ('{"workers": []}', "repo")):
            path = f"{self.tmp.name}/bad.json"
            open(path, "w").write(text)
            code, _, err = self.cli("fanout", path)
            self.assertEqual(code, 6, text)
            self.assertIn(expect, err)
        self.assertEqual(self.cli("fanout", "/nonexistent/plan.json")[0], 6)

    def test_repo_that_is_not_a_git_repository(self):
        path, _ = self.plan(repo_path=self.workdir("plain"), base="HEAD")
        code, _, err = self.cli("fanout", path)
        self.assertEqual(code, 6)
        self.assertFalse(os.path.exists(f"{self.tmp.name}/.rex-crew-worktrees"))

    def test_existing_branch_or_checkout_refuses_the_whole_plan(self):
        repo = self.repo()
        git(repo, "branch", "crew/b")
        path, _ = self.plan(repo_path=repo)
        code, _, err = self.cli("fanout", path)
        self.assertEqual(code, 6)
        self.assertIn("crew/b", err)
        self.assertEqual(git(repo, "branch", "--list", "crew/a"), "", "a was created although b is refused")
        self.assertFalse(os.path.exists(f"{self.tmp.name}/.rex-crew-worktrees"))
        self.assertEqual([b for b in self.fake()["blocks"].values() if not b["closed"]], [])

    def test_a_live_worker_of_the_same_name_refuses_the_plan(self):
        d = self.workdir("elsewhere")
        self.script("a", rec("working"))
        self.assertEqual(self.cli("start", "a", d, self.task)[0], 0)
        path, p = self.plan()
        code, _, err = self.cli("fanout", path)
        self.assertEqual(code, 6)
        self.assertIn("a", err)
        self.assertEqual(self.branches(p["repo"]), "")

    def test_a_start_that_fails_removes_everything_the_plan_created(self):
        path, p = self.plan(workers=[{"name": n, "task": "t"} for n in "abc"])
        real, n = self.mod.launch, [0]

        def flaky(*a, **k):
            n[0] += 1
            if n[0] == 3:
                self.mod.die("rex run failed: boom")
            return real(*a, **k)
        with mock.patch.object(self.mod, "launch", flaky):
            code, out, err = self.cli("fanout", path)
        self.assertEqual(code, 4, out + err)
        self.assertIn("boom", err)
        self.assertIn("rolled back", err)
        self.nothing_left(p["repo"])

    def test_a_checkout_that_fails_removes_the_earlier_ones(self):
        path, p = self.plan(workers=[{"name": n, "task": "t"} for n in "abc"])
        real, n = self.mod.checkout, [0]

        def flaky(*a, **k):
            n[0] += 1
            if n[0] == 3:
                self.mod.die("checkout failed: disk full")
            return real(*a, **k)
        with mock.patch.object(self.mod, "checkout", flaky):
            code, out, err = self.cli("fanout", path)
        self.assertEqual(code, 4, out + err)
        self.nothing_left(p["repo"])

    def test_interrupt_rolls_back_too(self):
        path, p = self.plan()
        real, n = self.mod.launch, [0]

        def stop(*a, **k):
            n[0] += 1
            if n[0] == 2:
                raise KeyboardInterrupt
            return real(*a, **k)
        with mock.patch.object(self.mod, "launch", stop):
            with self.assertRaises(KeyboardInterrupt):
                self.cli("fanout", path)
        self.nothing_left(p["repo"])

    def test_rollback_does_not_remove_a_path_the_plan_did_not_create(self):
        path, p = self.plan(workers=[{"name": n, "task": "t"} for n in "ab"])
        real, n = self.mod.checkout, [0]
        mine = f"{self.tmp.name}/.rex-crew-worktrees/repo/b"

        def racing(*a, **k):
            n[0] += 1
            if n[0] == 2:  # somebody else made the directory after the up-front check
                os.makedirs(mine)
                open(f"{mine}/precious.txt", "w").write("keep me")
            return real(*a, **k)
        with mock.patch.object(self.mod, "checkout", racing):
            code, out, err = self.cli("fanout", path)
        self.assertEqual(code, 6, out + err)
        self.assertEqual(open(f"{mine}/precious.txt").read(), "keep me")
        self.assertEqual(self.branches(p["repo"]), "")

    def test_fanout_then_integrate_end_to_end(self):
        path, p = self.plan()
        self.assertEqual(self.cli("fanout", path)[0], 0)
        runs = {r["name"]: r for r in self.ledger_runs()}
        for n in "ab":
            self.put(runs[n]["dir"], f"{n}.txt", n)
            git(runs[n]["dir"], "add", "-A")
            git(runs[n]["dir"], "commit", "-qm", f"work {n}")
        code, out, err = self.cli("integrate", p["repo"], "a", "b", "--verify", "test -e a.txt || test -e b.txt")
        self.assertEqual(code, 0, out + err)
        self.assertTrue(os.path.exists(f"{p['repo']}/a.txt") and os.path.exists(f"{p['repo']}/b.txt"))


if __name__ == "__main__":
    unittest.main()
