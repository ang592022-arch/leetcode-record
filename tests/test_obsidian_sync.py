from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REAL_REPO = Path(__file__).resolve().parents[1]
SCRIPT = REAL_REPO / "scripts" / "obsidian_sync.py"
SPEC = importlib.util.spec_from_file_location("obsidian_sync", SCRIPT)
assert SPEC and SPEC.loader
SYNC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SYNC
SPEC.loader.exec_module(SYNC)


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def init_repo(path: Path, branch: str = "main") -> None:
    path.mkdir(parents=True)
    completed = subprocess.run(["git", "init", "-b", branch], cwd=str(path), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        git(path, "init")
        git(path, "checkout", "-b", branch)
    git(path, "config", "user.name", "Sync Test")
    git(path, "config", "user.email", "sync@example.invalid")


class IncrementalObsidianSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.repo = root / "leetcode"
        self.vault = root / "vault"
        init_repo(self.repo)
        init_repo(self.vault, "master")
        code = b"class Solution { public: int answer = 42; };\n"
        solution = "problems/0042-answer/solution.cpp"
        path = self.repo / solution
        path.parent.mkdir(parents=True)
        path.write_bytes(code)
        (self.repo / "metadata").mkdir()
        self.problem = {
            "id": 42,
            "slug": "answer",
            "title": "Answer",
            "title_zh": "答案",
            "difficulty": "Easy",
            "topics": ["Array"],
            "primary_topic": "Array",
            "language": "cpp",
            "solution": solution,
            "acceptance": {"status": "verified", "source": "fixture"},
            "source": {
                "kind": "fixture",
                "original_path": solution,
                "commit": "fixture",
                "sha256": hashlib.sha256(code).hexdigest(),
            },
            "analysis": {
                "approach": "return the answer",
                "time_complexity": "O(1)",
                "space_complexity": "O(1)",
                "pitfalls": "none",
                "author": "Codex",
            },
        }
        self.write_metadata()
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "solve: LC42 Answer")
        (self.vault / "AGENTS.md").write_text("local only\n", encoding="utf-8")
        git(self.vault, "add", ".")
        git(self.vault, "commit", "-m", "chore: initialize vault")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_metadata(self) -> None:
        (self.repo / "metadata" / "problems.json").write_text(
            json.dumps({"schema_version": 1, "problems": [self.problem]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_note(self) -> None:
        note = self.vault / "学习" / "算法" / "LeetCode" / "LC42 答案.md"
        note.parent.mkdir(parents=True)
        source_commit = git(self.repo, "rev-parse", "HEAD")
        note.write_text(
            f"""---
type: leetcode
leetcode_id: 42
status: solved
review: false
difficulty: easy
language: cpp
verification: verified
source_commit: {source_commit}
---

# LC42 答案

## 我的代码

`problems/0042-answer/solution.cpp`

## Codex 对代码的解释

根据当前 AC 实现，可以归纳为直接返回答案。

## 复杂度

- 时间：O(1)
- 空间：O(1)

## 易错点

- 无。

## 关联

- [[学习/算法/算法索引|算法索引]]
""",
            encoding="utf-8",
        )
        index = self.vault / "学习" / "算法" / "算法索引.md"
        index.write_text("# 算法索引\n\n- [[学习/算法/LeetCode/LC42 答案|LC42 答案]]\n", encoding="utf-8")

    def test_initial_sync_then_second_prepare_is_noop(self) -> None:
        first = SYNC.prepare(self.repo, self.vault, pull=False)
        self.assertEqual([42], [item["id"] for item in first["problems"]])
        self.assertTrue(SYNC.pending_path(self.vault).is_file())
        self.write_note()

        state = SYNC.finalize(self.repo, self.vault)
        self.assertIn("42", state["problems"])
        self.assertFalse(SYNC.pending_path(self.vault).exists())
        SYNC.validate(self.repo, self.vault)

        state_before = SYNC.state_path(self.vault).read_bytes()
        second = SYNC.prepare(self.repo, self.vault, pull=False)
        self.assertEqual([], second["problems"])
        self.assertEqual(state_before, SYNC.state_path(self.vault).read_bytes())
        self.assertFalse(SYNC.pending_path(self.vault).exists())

    def test_metadata_change_is_detected_once(self) -> None:
        SYNC.prepare(self.repo, self.vault, pull=False)
        self.write_note()
        SYNC.finalize(self.repo, self.vault)
        self.problem["analysis"]["pitfalls"] = "changed explanation"
        self.write_metadata()
        git(self.repo, "add", "metadata/problems.json")
        git(self.repo, "commit", "-m", "docs: clarify LC42 notes")

        pending = SYNC.prepare(self.repo, self.vault, pull=False)
        self.assertEqual([42], [item["id"] for item in pending["problems"]])

    def test_vault_remote_is_rejected(self) -> None:
        git(self.vault, "remote", "add", "origin", str(Path(self.temporary.name) / "remote.git"))
        with self.assertRaises(SYNC.SyncError):
            SYNC.prepare(self.repo, self.vault, pull=False)

    def test_wrong_verification_is_rejected(self) -> None:
        SYNC.prepare(self.repo, self.vault, pull=False)
        self.write_note()
        note = self.vault / "学习" / "算法" / "LeetCode" / "LC42 答案.md"
        note.write_text(
            note.read_text(encoding="utf-8").replace("verification: verified", "verification: historical-unverified"),
            encoding="utf-8",
        )
        with self.assertRaises(SYNC.SyncError):
            SYNC.finalize(self.repo, self.vault)


if __name__ == "__main__":
    unittest.main()
