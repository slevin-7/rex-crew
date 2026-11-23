"""Red-team tests from a usability walk through SKILL.md. Run: python3 -m unittest tests/test_rt_skilltest.py"""
import json, os, subprocess, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_rex_crew import rec
from test_rt_fanout import GitBase, git


class Probe(GitBase):
    def plan(self, repo, **kw):
        p = {"repo": repo, "workers": [{"name": "w1", "task": "do it"}], **kw}
        path = f"{self.tmp.name}/plan.json"
        with open(path, "w") as f:
            json.dump(p, f)
        return path

    def probe(self, **kw):
        repo = self.repo()
        self.script("w1", rec("working"))
        return self.cli("fanout", self.plan(repo, **kw)), repo


class TestPlanTypes(Probe):
    def test_trust_must_be_a_boolean(self):
        (code, out, err), _ = self.probe(trust="false")
        self.assertEqual(code, 2, out + err)

    def test_copy_must_be_a_list(self):
        (code, out, err), repo = self.probe(copy="base.txt")
        self.assertEqual(code, 2, out + err)

    def test_claude_args_must_be_a_list_of_strings(self):
        (code, out, err), _ = self.probe(claude_args="--permission-mode")
        self.assertEqual(code, 2, out + err)

    def test_verify_timeout_must_be_a_positive_number(self):
        (code, out, err), _ = self.probe(verify="true", verify_timeout="soon")
        self.assertEqual(code, 2, out + err)
        (code, out, err), _ = self.probe(verify="true", verify_timeout=-5)
        self.assertEqual(code, 2, out + err)


class TestExitCodes(Probe):
    def start(self, *recs):
        d = self.workdir()
        self.script("w1", *recs)
        self.assertEqual(self.cli("start", "w1", d, self.task)[0], 0)
        return self.block_of("w1")

    def test_approve_on_a_question_has_nothing_to_act_on(self):
        b = self.start(rec("blocked", "question", "Which? 1. a 2. b"))
        self.assertEqual(self.cli("approve", b)[0], 6)

    def test_answer_on_a_working_worker_exits_6(self):
        b = self.start(rec("working"))
        self.assertEqual(self.cli("answer", b, "1")[0], 6)

    def test_collect_unknown_name_exits_6(self):
        self.assertEqual(self.cli("collect", "nobody")[0], 6)


class TestQuestionText(Probe):
    def test_a_question_that_says_approve_is_shown_whole(self):
        # question() cut everything before "approve " off a worker's own question
        q = "Should I approve the schema change or drop the table? 1. Approve 2. Drop"
        d = self.workdir()
        self.script("w1", rec("blocked", "question", q))
        self.assertEqual(self.cli("start", "w1", d, self.task)[0], 0)
        code, out, err = self.cli("collect")
        self.assertEqual(code, 2, out + err)
        self.assertIn(f"needs you (question): {q}", out)


class TestIntegrateTwice(Probe):
    def test_an_already_merged_branch_is_not_a_missing_commit(self):
        # re-running integrate after a crash said "did the worker commit?" about a branch that is merged
        repo = self.repo()
        self.branch(repo, "a", {"a.txt": "a\n"})
        self.assertEqual(self.cli("integrate", repo, "a")[0], 0)
        code, out, err = self.cli("integrate", repo, "a")
        self.assertIn("already merged", out)
        self.assertNotIn("did the worker commit", out)
        self.assertEqual(code, 1, out + err)  # still exit 1, as test_rt_fanout pins

    def test_a_branch_without_commits_still_says_so(self):
        repo = self.repo()
        git(repo, "branch", "crew/idle")
        code, out, err = self.cli("integrate", repo, "idle")
        self.assertIn("did the worker commit", out)
        self.assertEqual(code, 1)


class TestWalkthrough(unittest.TestCase):
    def test_walkthrough_from_skill_md_runs_green(self):
        here = os.path.dirname(os.path.abspath(__file__))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("REX_", "FAKE_REX"))}
        p = subprocess.run(["sh", os.path.join(here, "walkthrough.sh")], capture_output=True, text=True, env=env, timeout=120)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out)
        for br in ("one", "two", "three"):
            self.assertIn(f"crew/{br}: merged (verify ok)", out)
        self.assertIn("needs you (permission): Bash: make deploy", out)
        self.assertIn("needs you (question): Which database?", out)
        self.assertIn("tally: 3 workers collected and integrated", out)
