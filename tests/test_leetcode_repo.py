from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REAL_REPO = Path(__file__).resolve().parents[1]
SCRIPT = REAL_REPO / "scripts" / "leetcode_repo.py"
SPEC = importlib.util.spec_from_file_location("leetcode_repo", SCRIPT)
assert SPEC and SPEC.loader
AUTOMATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUTOMATION
SPEC.loader.exec_module(AUTOMATION)


def run(command, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    completed = subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {' '.join(str(part) for part in command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *arguments], repo, check=check)


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    result = git(path, "init", "-b", "main", check=False)
    if result.returncode != 0:
        git(path, "init")
        git(path, "checkout", "-b", "main")
    git(path, "config", "user.name", "Automation Test")
    git(path, "config", "user.email", "automation@example.invalid")


def empty_database(extra=None):
    value = {"schema_version": 1, "problems": []}
    if extra:
        value.update(extra)
    return value


def record(problem_id: int, solution: str, code: bytes, **extra):
    value = {
        "id": problem_id,
        "slug": "two-sum",
        "title": "Two Sum",
        "title_zh": "两数之和",
        "difficulty": "Easy",
        "topics": ["Array", "Hash Table"],
        "primary_topic": "array",
        "language": "cpp",
        "solution": solution,
        "acceptance": {"status": "unverified", "source": "fixture"},
        "source": {
            "kind": "fixture",
            "original_path": solution,
            "commit": None,
            "sha256": hashlib.sha256(code).hexdigest(),
        },
        "analysis": {
            "approach": "哈希表记录已见元素",
            "time_complexity": "O(n)",
            "space_complexity": "O(n)",
            "pitfalls": ["返回下标而不是数值"],
            "author": "Codex",
        },
    }
    value.update(extra)
    return value


class TemporaryRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        init_repo(self.repo)
        (self.repo / "metadata").mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_database(self, value) -> None:
        (self.repo / "metadata" / "problems.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def test_generate_readmes_from_metadata(self) -> None:
        code = b"class Solution {};\n"
        solution = "problems/0001-two-sum/solution.cpp"
        solution_path = self.repo / solution
        solution_path.parent.mkdir(parents=True)
        solution_path.write_bytes(code)
        self.write_database({"schema_version": 1, "problems": [record(1, solution, code)]})

        changed = AUTOMATION.generate_readmes(self.repo)

        self.assertIn("README.md", changed)
        root = (self.repo / "README.md").read_text(encoding="utf-8")
        problem_readme = (solution_path.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("Solved: **1**", root)
        self.assertIn("Verified: **0**", root)
        self.assertIn("Unverified: **1**", root)
        self.assertIn("## 自动同步", root)
        self.assertIn("## 我的代码", problem_readme)
        self.assertIn("## Codex 分析", problem_readme)
        self.assertEqual([], AUTOMATION.generate_readmes(self.repo, check=True))

    def test_json_bundle_import_is_exact_and_verified(self) -> None:
        self.write_database(empty_database({"future_schema_note": {"keep": True}}))
        exact_code = "class Solution {\r\npublic:\r\n  int answer = 42;\r\n};\r\n"
        bundle = self.repo / "submission-0042.json"
        bundle.write_text(
            json.dumps(
                {
                    "id": 42,
                    "slug": "trapping-rain-water",
                    "title": "Trapping Rain Water",
                    "title_zh": "接雨水",
                    "difficulty": "Hard",
                    "topics": ["Array", "Two Pointers"],
                    "primary_topic": "Two Pointers",
                    "language": "C++",
                    "status": "Accepted",
                    "code": exact_code,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        results, _ = AUTOMATION.organize(self.repo, [bundle])

        self.assertEqual("new", results[0].action)
        solution = self.repo / "problems" / "0042-trapping-rain-water" / "solution.cpp"
        self.assertEqual(exact_code.encode("utf-8"), solution.read_bytes())
        data = json.loads((self.repo / "metadata" / "problems.json").read_text(encoding="utf-8"))
        self.assertEqual({"keep": True}, data["future_schema_note"])
        self.assertEqual("verified", data["problems"][0]["acceptance"]["status"])

    def test_conflict_preserves_solution_and_unknown_fields(self) -> None:
        original = b"class Solution { int original; };\n"
        solution = "problems/0001-two-sum/solution.cpp"
        path = self.repo / solution
        path.parent.mkdir(parents=True)
        path.write_bytes(original)
        problem = record(1, solution, original, custom_field={"keep": "me"})
        self.write_database(empty_database({"top_unknown": 17}) | {"problems": [problem]})
        replacement = b"class Solution { int newer; };\n"
        bundle = self.repo / "bundle-conflict.json"
        bundle.write_text(
            json.dumps(
                {
                    "id": 1,
                    "slug": "two-sum",
                    "title": "Two Sum",
                    "language": "cpp",
                    "status": "Accepted",
                    "code": replacement.decode("utf-8"),
                }
            ),
            encoding="utf-8",
        )

        results, _ = AUTOMATION.organize(self.repo, [bundle])

        self.assertEqual("submission", results[0].action)
        self.assertEqual(original, path.read_bytes())
        alternative = path.parent / "submissions" / f"{hashlib.sha256(replacement).hexdigest()[:12]}.cpp"
        self.assertEqual(replacement, alternative.read_bytes())
        data = json.loads((self.repo / "metadata" / "problems.json").read_text(encoding="utf-8"))
        self.assertEqual(17, data["top_unknown"])
        self.assertEqual({"keep": "me"}, data["problems"][0]["custom_field"])

    def test_reject_conflict_changes_nothing(self) -> None:
        original = b"original\n"
        solution = "problems/0001-two-sum/solution.cpp"
        path = self.repo / solution
        path.parent.mkdir(parents=True)
        path.write_bytes(original)
        self.write_database({"schema_version": 1, "problems": [record(1, solution, original)]})
        metadata_before = (self.repo / "metadata" / "problems.json").read_bytes()
        bundle = self.repo / "bundle-reject.json"
        bundle.write_text(
            json.dumps({"id": 1, "slug": "two-sum", "language": "cpp", "code": "different\n"}),
            encoding="utf-8",
        )

        with self.assertRaises(AUTOMATION.AutomationError):
            AUTOMATION.organize(self.repo, [bundle], conflict="reject")

        self.assertEqual(original, path.read_bytes())
        self.assertEqual(metadata_before, (self.repo / "metadata" / "problems.json").read_bytes())
        self.assertFalse((path.parent / "submissions").exists())

    def test_recursive_leethub_import_preserves_every_auxiliary_byte_before_consume(self) -> None:
        self.write_database(empty_database())
        directory = self.repo / "LeetCode" / "Easy" / "C++" / "1-two-sum"
        directory.mkdir(parents=True)
        first = b"class Solution { int first; };\n"
        second = b"class Solution { int second; };\n"
        (directory / "solution.cpp").write_bytes(first)
        (directory / "solution-20260807.cpp").write_bytes(second)
        readme = b"# Original LeetHub statement\r\n"
        notes = b"my notes\x00stay exact\n"
        optimized = b"class Solution { int optimized; };\r\n"
        (directory / "README.md").write_bytes(readme)
        (directory / "NOTES.md").write_bytes(notes)
        (directory / "optimized.cpp").write_bytes(optimized)
        (directory / "metadata.json").write_text(
            json.dumps({"id": 1, "title": "Two Sum", "slug": "two-sum", "language": "cpp", "status": "Accepted"}),
            encoding="utf-8",
        )

        results, consumed = AUTOMATION.organize(self.repo, [self.repo], consume=True)

        self.assertEqual(["new", "submission"], [item.action for item in results])
        self.assertEqual(["LeetCode/Easy/C++/1-two-sum"], consumed)
        self.assertFalse(directory.exists())
        canonical = self.repo / "problems" / "0001-two-sum" / "solution.cpp"
        self.assertEqual(first, canonical.read_bytes())
        alternative = canonical.parent / "submissions" / f"{hashlib.sha256(second).hexdigest()[:12]}.cpp"
        self.assertEqual(second, alternative.read_bytes())
        self.assertEqual(optimized, (canonical.parent / "source" / "optimized.cpp").read_bytes())
        self.assertEqual(readme, (canonical.parent / "source" / "README.md").read_bytes())
        self.assertEqual(notes, (canonical.parent / "source" / "NOTES.md").read_bytes())
        self.assertTrue((canonical.parent / "source" / "metadata.json").is_file())

    def test_unverified_directory_and_missing_canonical_mismatch_are_rejected(self) -> None:
        self.write_database(empty_database())
        unverified = self.repo / "1-two-sum"
        unverified.mkdir()
        (unverified / "solution.cpp").write_bytes(b"unverified\n")
        with self.assertRaises(AUTOMATION.AutomationError):
            AUTOMATION.organize(self.repo, [unverified])
        self.assertFalse((self.repo / "problems").exists())

        original = b"original\n"
        solution = "problems/0001-two-sum/solution.cpp"
        self.write_database({"schema_version": 1, "problems": [record(1, solution, original)]})
        bundle = self.repo / "submission-restore.json"
        bundle.write_text(
            json.dumps(
                {"id": 1, "slug": "two-sum", "language": "cpp", "status": "Accepted", "code": "different\n"}
            ),
            encoding="utf-8",
        )
        with self.assertRaises(AUTOMATION.AutomationError):
            AUTOMATION.organize(self.repo, [bundle])
        self.assertFalse((self.repo / solution).exists())

    def test_frontend_problem_id_wins_over_internal_question_id(self) -> None:
        self.assertEqual(
            42,
            AUTOMATION._problem_id({"questionFrontendId": "42", "questionId": "987654"}),
        )

    def test_secret_scanner_and_solution_guard(self) -> None:
        code = b"class Solution {};\n"
        solution = "problems/0001-two-sum/solution.cpp"
        path = self.repo / solution
        path.parent.mkdir(parents=True)
        path.write_bytes(code)
        self.write_database({"schema_version": 1, "problems": [record(1, solution, code)]})
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "chore: test fixture")
        path.write_bytes(b"overwritten\n")
        git(self.repo, "add", solution)
        self.assertTrue(AUTOMATION.guard_canonical_solutions(self.repo))
        git(self.repo, "reset", "--", solution)
        secret = "gh" + "p_" + "A" * 30
        (self.repo / "config.txt").write_text(f"token={secret}\n", encoding="utf-8")
        issues = AUTOMATION.scan_secrets(self.repo)
        self.assertTrue(any("GitHub token" in issue for issue in issues))
        (self.repo / "leetcode-auth.txt").write_text(
            "LEETCODE_SESSION=" + "A" * 40 + "\n", encoding="utf-8"
        )
        issues = AUTOMATION.scan_secrets(self.repo)
        self.assertTrue(any("LeetCode/browser session" in issue for issue in issues))
        fine_grained = "github_pat_" + "A" * 30
        (self.repo / "fine-grained.txt").write_text(fine_grained + "\n", encoding="utf-16")
        issues = AUTOMATION.scan_secrets(self.repo)
        self.assertTrue(any("GitHub fine-grained token" in issue for issue in issues))


@unittest.skipUnless(shutil.which("git") and (shutil.which("python") or shutil.which("py")), "Git and Python required")
class EndToEndBareRemoteTest(unittest.TestCase):
    def test_sync_commit_push_and_non_fast_forward_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            repo = temporary / "work"
            remote = temporary / "remote.git"
            init_repo(repo)
            git(temporary, "init", "--bare", str(remote))

            (repo / "scripts").mkdir()
            shutil.copy2(SCRIPT, repo / "scripts" / "leetcode_repo.py")
            shutil.copytree(REAL_REPO / ".githooks", repo / ".githooks")
            shutil.copy2(REAL_REPO / ".gitignore", repo / ".gitignore")
            shutil.copy2(REAL_REPO / ".gitattributes", repo / ".gitattributes")
            (repo / "metadata").mkdir()
            (repo / "metadata" / "problems.json").write_text(
                json.dumps(empty_database(), indent=2) + "\n", encoding="utf-8"
            )
            run([sys.executable, "scripts/leetcode_repo.py", "generate"], repo)
            git(repo, "add", ".")
            git(repo, "commit", "-m", "chore: initialize automation fixture")
            git(repo, "remote", "add", "origin", str(remote))
            git(repo, "push", "-u", "origin", "main")
            run([sys.executable, "scripts/leetcode_repo.py", "install-hooks"], repo)

            incoming = repo / "15-3sum"
            incoming.mkdir()
            accepted_code = b"class Solution { public: int threeSum = 3; };\n"
            (incoming / "15-3sum.cpp").write_bytes(accepted_code)
            (incoming / "metadata.json").write_text(
                json.dumps(
                    {
                        "id": 15,
                        "slug": "3sum",
                        "title": "3Sum",
                        "title_zh": "三数之和",
                        "difficulty": "Medium",
                        "topics": ["Array", "Two Pointers"],
                        "language": "cpp",
                        "status": "Accepted",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            sync = run(
                [sys.executable, "scripts/leetcode_repo.py", "sync", "--source", ".", "--consume"], repo
            )
            self.assertIn("push", sync.stdout)
            self.assertFalse(incoming.exists())
            local_head = git(repo, "rev-parse", "HEAD").stdout.strip()
            remote_head = git(repo, "--git-dir", str(remote), "rev-parse", "refs/heads/main").stdout.strip()
            self.assertEqual(local_head, remote_head)
            stored = git(repo, "--git-dir", str(remote), "show", "main:problems/0015-3sum/solution.cpp").stdout
            self.assertEqual(accepted_code.decode("utf-8"), stored)
            remote_metadata = json.loads(
                git(repo, "--git-dir", str(remote), "show", "main:metadata/problems.json").stdout
            )
            self.assertEqual("verified", remote_metadata["problems"][0]["acceptance"]["status"])

            competitor = temporary / "competitor"
            git(temporary, "clone", "--branch", "main", str(remote), str(competitor))
            git(competitor, "config", "user.name", "Competing Writer")
            git(competitor, "config", "user.email", "competitor@example.invalid")
            (competitor / "remote-note.txt").write_text("remote advanced\n", encoding="utf-8")
            git(competitor, "add", "remote-note.txt")
            git(competitor, "commit", "-m", "docs: advance remote fixture")
            git(competitor, "push")

            (repo / "local-note.txt").write_text("diverging local commit\n", encoding="utf-8")
            git(repo, "add", "local-note.txt")
            git(repo, "commit", "-m", "docs: diverge local fixture")
            rejected = git(repo, "push", "--force", "origin", "main", check=False)
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("non-fast-forward", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
