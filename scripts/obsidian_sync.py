#!/usr/bin/env python3
"""Incremental bridge from the LeetCode repository to an Obsidian vault.

This script only performs mechanical work: fast-forward pulls, change detection,
checkpointing, and validation.  Codex remains responsible for reading the real
solution and writing or updating the semantic notes listed by ``prepare``.

The vault is deliberately restricted to ``学习/算法`` plus the hidden
``.leetcode-sync`` state directory.  No diary or unrelated vault directory is
read by this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
STATE_DIRECTORY = ".leetcode-sync"
STATE_FILENAME = "state.json"
PENDING_FILENAME = "pending.json"
ALGORITHM_DIRECTORY = Path("学习") / "算法"
WIKILINK_PATTERN = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")


class SyncError(RuntimeError):
    """An expected, user-actionable sync failure."""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_hash(value: Any) -> str:
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(content)


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_child(parent: Path, relative: str | Path) -> Path:
    candidate = (parent / Path(relative)).resolve()
    if not is_inside(candidate, parent):
        raise SyncError(f"路径越过允许范围：{relative}")
    return candidate


def run_git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise SyncError(f"git {' '.join(arguments)} 失败：{detail}")
    return completed


def require_git_repository(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not (resolved / ".git").exists():
        raise SyncError(f"{label}不是 Git 仓库：{resolved}")
    return resolved


def require_vault_without_remote(vault: Path) -> None:
    remotes = [line.strip() for line in run_git(vault, "remote").stdout.splitlines() if line.strip()]
    if remotes:
        raise SyncError(f"Obsidian Vault 存在 remote（{', '.join(remotes)}）；为防止上传，已拒绝同步")


def pull_ff_only(repo: Path) -> None:
    status = run_git(repo, "status", "--porcelain").stdout.strip()
    if status:
        raise SyncError("LeetCode 仓库有未提交改动；拒绝在不清楚范围时 pull")
    upstream = run_git(
        repo,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        check=False,
    )
    if upstream.returncode != 0:
        raise SyncError("LeetCode 当前分支没有 upstream；无法安全执行 pull --ff-only")
    run_git(repo, "pull", "--ff-only")


def atomic_write_text(path: Path, content: str) -> bool:
    encoded = content.encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent), delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def load_json(path: Path, label: str) -> MutableMapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SyncError(f"缺少{label}：{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"无法读取{label} {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise SyncError(f"{label}根对象必须是 JSON object")
    return value


def state_path(vault: Path) -> Path:
    return vault / STATE_DIRECTORY / STATE_FILENAME


def pending_path(vault: Path) -> Path:
    return vault / STATE_DIRECTORY / PENDING_FILENAME


def empty_state() -> MutableMapping[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_repo": "",
        "last_source_commit": None,
        "last_sync_at": None,
        "problems": {},
    }


def load_state(vault: Path) -> MutableMapping[str, Any]:
    path = state_path(vault)
    if not path.exists():
        return empty_state()
    value = load_json(path, "同步状态")
    if value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("problems"), dict):
        raise SyncError(f"不支持的同步状态格式：{path}")
    return value


def repository_web_url(repo: Path) -> Optional[str]:
    completed = run_git(repo, "remote", "get-url", "origin", check=False)
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    match = re.fullmatch(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?", value)
    if match:
        return "https://github.com/" + match.group(1)
    match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?", value)
    if match:
        return "https://github.com/" + match.group(1)
    return None


def load_problem_snapshots(repo: Path) -> Dict[str, Dict[str, Any]]:
    database = load_json(repo / "metadata" / "problems.json", "LeetCode metadata")
    if database.get("schema_version") != 1 or not isinstance(database.get("problems"), list):
        raise SyncError("metadata/problems.json 必须包含 schema_version=1 和 problems 数组")
    web_url = repository_web_url(repo)
    snapshots: Dict[str, Dict[str, Any]] = {}
    for problem in database["problems"]:
        if not isinstance(problem, dict) or not isinstance(problem.get("id"), int):
            raise SyncError("metadata 中存在无效题目记录")
        problem_id = str(problem["id"])
        if problem_id in snapshots:
            raise SyncError(f"metadata 题号重复：LC{problem_id}")
        solution_relative = str(problem.get("solution", ""))
        solution = safe_child(repo, solution_relative)
        if not solution.is_file():
            raise SyncError(f"LC{problem_id} 的 solution 不存在：{solution_relative}")
        solution_hash = sha256_bytes(solution.read_bytes())
        expected_hash = str(problem.get("source", {}).get("sha256", "")).lower()
        if expected_hash != solution_hash:
            raise SyncError(f"LC{problem_id} 原始 solution 哈希不匹配；拒绝继续")

        submission_hashes = []
        for submission in problem.get("submissions", []):
            if not isinstance(submission, dict):
                raise SyncError(f"LC{problem_id} submission 记录无效")
            relative = str(submission.get("path", ""))
            path = safe_child(repo, relative)
            if not path.is_file():
                raise SyncError(f"LC{problem_id} submission 不存在：{relative}")
            actual = sha256_bytes(path.read_bytes())
            expected = str(submission.get("sha256") or submission.get("source", {}).get("sha256", "")).lower()
            if expected and expected != actual:
                raise SyncError(f"LC{problem_id} submission 哈希不匹配：{relative}")
            submission_hashes.append({"path": relative, "sha256": actual})
        submission_hashes.sort(key=lambda item: (item["path"], item["sha256"]))

        metadata_hash = canonical_hash(problem)
        fingerprint = {
            "metadata_hash": metadata_hash,
            "solution_hash": solution_hash,
            "submissions": submission_hashes,
        }
        snapshot: Dict[str, Any] = {
            "id": problem["id"],
            "slug": problem.get("slug"),
            "title": problem.get("title"),
            "title_zh": problem.get("title_zh"),
            "difficulty": str(problem.get("difficulty", "")).lower(),
            "topics": problem.get("topics", []),
            "language": problem.get("language"),
            "solution": solution_relative,
            "acceptance": problem.get("acceptance", {}),
            "analysis": problem.get("analysis", {}),
            "metadata_hash": metadata_hash,
            "solution_hash": solution_hash,
            "submissions": submission_hashes,
            "content_hash": canonical_hash(fingerprint),
        }
        if web_url:
            snapshot["github_url"] = f"{web_url}/blob/main/{solution_relative}"
        snapshots[problem_id] = snapshot
    return snapshots


def prepare(repo: Path, vault: Path, *, pull: bool = True) -> MutableMapping[str, Any]:
    repo = require_git_repository(repo, "LeetCode 仓库")
    vault = require_git_repository(vault, "Obsidian Vault")
    require_vault_without_remote(vault)
    if pull:
        pull_ff_only(repo)

    head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    snapshots = load_problem_snapshots(repo)
    state = load_state(vault)
    stored = state.get("problems", {})
    changed = [snapshots[key] for key in sorted(snapshots, key=int) if stored.get(key, {}).get("content_hash") != snapshots[key]["content_hash"]]
    removed_ids = sorted((set(stored) - set(snapshots)), key=int)
    pending: MutableMapping[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "source_repo": str(repo),
        "source_commit": head,
        "problems": changed,
        "removed_ids": removed_ids,
    }
    path = pending_path(vault)
    if changed or removed_ids:
        atomic_write_text(path, json.dumps(pending, ensure_ascii=False, indent=2) + "\n")
    elif path.exists():
        path.unlink()
    print(f"待同步题目：{len(changed)}；来源 commit：{head[:12]}")
    if removed_ids:
        print("metadata 中已不存在但 checkpoint 仍保留：" + ", ".join(f"LC{item}" for item in removed_ids))
    return pending


def parse_frontmatter(text: str) -> Dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result: Dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return result
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
        if match:
            result[match.group(1)] = match.group(2).strip('"\'')
    return {}


def algorithm_markdown_files(vault: Path) -> Iterable[Path]:
    root = vault / ALGORITHM_DIRECTORY
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.md") if path.is_file())


def scan_leetcode_notes(vault: Path) -> Tuple[Dict[str, Path], Dict[Path, str]]:
    notes: Dict[str, Path] = {}
    contents: Dict[Path, str] = {}
    for path in algorithm_markdown_files(vault):
        text = path.read_text(encoding="utf-8-sig")
        contents[path] = text
        properties = parse_frontmatter(text)
        problem_id = properties.get("leetcode_id")
        if not problem_id:
            continue
        if not problem_id.isdigit() or int(problem_id) <= 0:
            raise SyncError(f"无效 leetcode_id：{path.relative_to(vault)}")
        normalized = str(int(problem_id))
        if normalized in notes:
            raise SyncError(
                f"LC{normalized} 存在重复笔记：{notes[normalized].relative_to(vault)} 与 {path.relative_to(vault)}"
            )
        notes[normalized] = path
    return notes, contents


def expected_verification(snapshot: Mapping[str, Any]) -> str:
    acceptance_status = str(snapshot.get("acceptance", {}).get("status", ""))
    if acceptance_status == "verified":
        return "verified"
    if acceptance_status == "unverified_historical_import":
        return "historical-unverified"
    return "unverified"


def validate_problem_note(
    vault: Path,
    path: Path,
    snapshot: Mapping[str, Any],
    source_commit: Optional[str] = None,
) -> None:
    text = path.read_text(encoding="utf-8-sig")
    properties = parse_frontmatter(text)
    problem_id = str(snapshot["id"])
    if properties.get("type") != "leetcode":
        raise SyncError(f"LC{problem_id} 笔记缺少 type: leetcode：{path.relative_to(vault)}")
    if properties.get("leetcode_id") != problem_id:
        raise SyncError(f"LC{problem_id} 笔记题号不一致：{path.relative_to(vault)}")
    if properties.get("status") not in {"solved", "review"}:
        raise SyncError(f"LC{problem_id} status 必须是 solved 或 review")
    if properties.get("review") not in {"true", "false"}:
        raise SyncError(f"LC{problem_id} review 必须明确为 true 或 false")
    if properties.get("difficulty") != snapshot.get("difficulty"):
        raise SyncError(f"LC{problem_id} difficulty 与 metadata 不一致")
    if properties.get("language") != snapshot.get("language"):
        raise SyncError(f"LC{problem_id} language 与 metadata 不一致")
    verification = expected_verification(snapshot)
    if properties.get("verification") != verification:
        raise SyncError(f"LC{problem_id} verification 必须是 {verification}")
    if source_commit is not None and properties.get("source_commit") != source_commit:
        raise SyncError(f"LC{problem_id} source_commit 与 checkpoint 来源不一致")
    required_sections = ("## 我的代码", "## Codex 对代码的解释", "## 复杂度", "## 易错点", "## 关联")
    missing = [section for section in required_sections if section not in text]
    if missing:
        raise SyncError(f"LC{problem_id} 笔记缺少章节：{', '.join(missing)}")
    if str(snapshot["solution"]) not in text:
        raise SyncError(f"LC{problem_id} 笔记缺少真实 solution 路径引用")


def validate_wikilinks(vault: Path, contents: Mapping[Path, str]) -> None:
    algorithm_root = vault / ALGORITHM_DIRECTORY
    by_stem: Dict[str, list[Path]] = {}
    for path in contents:
        by_stem.setdefault(path.stem, []).append(path)
    errors = []
    for source, text in contents.items():
        for raw in WIKILINK_PATTERN.findall(text):
            target = raw.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/")
            if not target:
                continue
            if target.lower().endswith(".md"):
                target = target[:-3]
            if "/" in target:
                candidate = safe_child(vault, target + ".md")
                if not candidate.is_file():
                    errors.append(f"{source.relative_to(vault)} -> [[{raw}]]")
            else:
                matches = by_stem.get(target, [])
                if len(matches) != 1:
                    errors.append(f"{source.relative_to(vault)} -> [[{raw}]]（匹配 {len(matches)} 个）")
    if errors:
        raise SyncError("算法区存在无法解析或有歧义的 Wikilink：\n" + "\n".join(errors))
    if algorithm_root.exists() and not is_inside(algorithm_root, vault):
        raise SyncError("算法目录越过 Vault 边界")


def finalize(repo: Path, vault: Path) -> MutableMapping[str, Any]:
    repo = require_git_repository(repo, "LeetCode 仓库")
    vault = require_git_repository(vault, "Obsidian Vault")
    require_vault_without_remote(vault)
    path = pending_path(vault)
    pending = load_json(path, "待同步清单")
    if pending.get("schema_version") != SCHEMA_VERSION or not isinstance(pending.get("problems"), list):
        raise SyncError("待同步清单格式无效")
    current_head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    if current_head != pending.get("source_commit"):
        raise SyncError("LeetCode HEAD 在 prepare 后发生变化；请重新 prepare")
    current = load_problem_snapshots(repo)
    notes, contents = scan_leetcode_notes(vault)
    state = load_state(vault)
    synced_at = now_iso()
    synced_ids = []
    for snapshot in pending["problems"]:
        problem_id = str(snapshot.get("id"))
        if problem_id not in current or current[problem_id]["content_hash"] != snapshot.get("content_hash"):
            raise SyncError(f"LC{problem_id} 在 prepare 后发生变化；请重新 prepare")
        note = notes.get(problem_id)
        if note is None:
            raise SyncError(f"LC{problem_id} 尚无 Obsidian 算法笔记")
        validate_problem_note(vault, note, snapshot, str(pending["source_commit"]))
        state["problems"][problem_id] = {
            "note": note.relative_to(vault).as_posix(),
            "source_commit": pending["source_commit"],
            "synced_at": synced_at,
            "content_hash": snapshot["content_hash"],
            "metadata_hash": snapshot["metadata_hash"],
            "solution_hash": snapshot["solution_hash"],
            "submissions": snapshot.get("submissions", []),
        }
        synced_ids.append(problem_id)
    validate_wikilinks(vault, contents)
    state["schema_version"] = SCHEMA_VERSION
    state["source_repo"] = str(repo)
    state["last_source_commit"] = pending["source_commit"]
    state["last_sync_at"] = synced_at
    atomic_write_text(state_path(vault), json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    path.unlink()
    print(f"checkpoint 已更新：{len(synced_ids)} 道题；算法区 Wikilink 校验通过")
    return state


def validate(repo: Path, vault: Path) -> None:
    repo = require_git_repository(repo, "LeetCode 仓库")
    vault = require_git_repository(vault, "Obsidian Vault")
    require_vault_without_remote(vault)
    state = load_state(vault)
    snapshots = load_problem_snapshots(repo)
    notes, contents = scan_leetcode_notes(vault)
    for problem_id, record in state["problems"].items():
        note = notes.get(problem_id)
        if note is None:
            raise SyncError(f"checkpoint 中的 LC{problem_id} 笔记不存在")
        snapshot = snapshots.get(problem_id)
        if snapshot is None:
            continue
        validate_problem_note(vault, note, snapshot, str(record.get("source_commit", "")))
        if record.get("note") != note.relative_to(vault).as_posix():
            raise SyncError(f"LC{problem_id} checkpoint 的 note 路径过期")
    validate_wikilinks(vault, contents)
    unsynced = [key for key, value in snapshots.items() if state["problems"].get(key, {}).get("content_hash") != value["content_hash"]]
    if unsynced:
        raise SyncError("存在尚未同步的题目：" + ", ".join(f"LC{item}" for item in sorted(unsynced, key=int)))
    print(f"验证通过：{len(state['problems'])} 道题已 checkpoint；Vault 无 remote")


def show_status(vault: Path) -> None:
    vault = require_git_repository(vault, "Obsidian Vault")
    require_vault_without_remote(vault)
    state = load_state(vault)
    pending = pending_path(vault)
    pending_count = 0
    if pending.exists():
        value = load_json(pending, "待同步清单")
        pending_count = len(value.get("problems", []))
    print(
        f"已同步：{len(state['problems'])}；待处理：{pending_count}；"
        f"上次来源 commit：{str(state.get('last_source_commit') or '无')[:12]}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LeetCode → Obsidian 增量同步机械层")
    parser.add_argument("command", choices=("prepare", "finalize", "validate", "status"))
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]), help="LeetCode 本地仓库")
    parser.add_argument("--vault", required=True, help="Obsidian Vault 路径")
    parser.add_argument("--no-pull", action="store_true", help="prepare 时跳过 git pull --ff-only（仅测试/离线检查）")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo)
    vault = Path(args.vault)
    try:
        if args.command == "prepare":
            prepare(repo, vault, pull=not args.no_pull)
        elif args.command == "finalize":
            finalize(repo, vault)
        elif args.command == "validate":
            validate(repo, vault)
        else:
            show_status(vault)
        return 0
    except SyncError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
