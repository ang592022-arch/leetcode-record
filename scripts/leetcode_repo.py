#!/usr/bin/env python3
"""LeetCode repository automation using only the Python standard library.

The metadata file is the source of truth.  This module deliberately treats every
existing solution file as immutable: a different accepted submission is stored
under ``submissions/`` or rejected, never copied over ``solution.*``.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
REQUIRED_PROBLEM_FIELDS = (
    "id",
    "slug",
    "title",
    "title_zh",
    "difficulty",
    "topics",
    "primary_topic",
    "language",
    "solution",
    "acceptance",
    "source",
    "analysis",
)
LANGUAGE_EXTENSIONS = {
    "cpp": "cpp",
    "c": "c",
    "python": "py",
    "java": "java",
    "javascript": "js",
    "typescript": "ts",
    "go": "go",
    "rust": "rs",
    "csharp": "cs",
    "kotlin": "kt",
    "swift": "swift",
    "ruby": "rb",
    "php": "php",
    "scala": "scala",
}
EXTENSION_LANGUAGES = {value: key for key, value in LANGUAGE_EXTENSIONS.items()}
CODE_EXTENSIONS = set(EXTENSION_LANGUAGES)
REAL_REPOSITORY_MODE = "real_records"
FIXTURE_SOURCE_KINDS = {"fixture", "test", "test_fixture", "synthetic", "simulation"}
PROVENANCE_SOURCE_KINDS = {"legacy_repository", "leethub"}
PROTECTED_TOP_LEVEL = {
    ".git",
    ".github",
    ".githooks",
    "metadata",
    "problems",
    "scripts",
    "tests",
}


class AutomationError(RuntimeError):
    """An expected, user-actionable automation failure."""


@dataclasses.dataclass
class Submission:
    problem_id: int
    slug: str
    title: str
    title_zh: str
    difficulty: str
    topics: List[str]
    primary_topic: str
    language: str
    code: bytes
    accepted: bool
    acceptance_source: str
    source_kind: str
    original_path: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.code).hexdigest()


@dataclasses.dataclass
class ImportResult:
    action: str
    problem_id: int
    title: str
    changed_paths: List[str]
    solution_path: str


def repository_root(value: Optional[str] = None) -> Path:
    if value:
        root = Path(value).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[1]
    if not (root / ".git").exists():
        raise AutomationError(f"不是 Git 仓库：{root}")
    return root


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def safe_repo_path(repo: Path, relative: str) -> Path:
    candidate = (repo / Path(relative)).resolve()
    if not _is_inside(candidate, repo):
        raise AutomationError(f"元数据路径越过仓库边界：{relative}")
    return candidate


def posix_relative(path: Path, repo: Path) -> str:
    resolved = path.resolve()
    if _is_inside(resolved, repo):
        return resolved.relative_to(repo.resolve()).as_posix()
    return resolved.as_posix()


def atomic_write_bytes(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent), delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def atomic_write_text(path: Path, content: str) -> bool:
    return atomic_write_bytes(path, content.encode("utf-8"))


def load_database(repo: Path) -> MutableMapping[str, Any]:
    path = repo / "metadata" / "problems.json"
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "problems": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutomationError(f"无法读取 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AutomationError("metadata/problems.json 根对象必须是 JSON object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise AutomationError(f"不支持 schema_version={data.get('schema_version')!r}，当前只支持 1")
    if not isinstance(data.get("problems"), list):
        raise AutomationError("metadata/problems.json 的 problems 必须是数组")
    return data


def _language_policy(data: Mapping[str, Any]) -> Tuple[set[str], set[str]]:
    raw = data.get("language_policy", {})
    if raw in (None, ""):
        raw = {}
    if not isinstance(raw, Mapping):
        raise AutomationError("language_policy 必须是 object")
    allowed_raw = raw.get("allowed_languages", [])
    sources_raw = raw.get("automatic_verified_sources", [])
    if not isinstance(allowed_raw, list) or not isinstance(sources_raw, list):
        raise AutomationError("language_policy 的 allowed_languages 和 automatic_verified_sources 必须是数组")
    allowed = {str(item).strip().lower() for item in allowed_raw if str(item).strip()}
    sources = {str(item).strip().lower() for item in sources_raw if str(item).strip()}
    unknown = sorted(allowed - set(LANGUAGE_EXTENSIONS))
    if unknown:
        raise AutomationError("language_policy 包含不支持的语言：" + ", ".join(unknown))
    return allowed, sources


def _source_kind(value: Any) -> str:
    return str(value or "").strip().lower()


def _verified_source_kinds(problem: Mapping[str, Any]) -> set[str]:
    kinds: set[str] = set()
    source = problem.get("source", {})
    if isinstance(source, Mapping):
        kinds.add(_source_kind(source.get("kind")))
    submissions = problem.get("submissions", [])
    if isinstance(submissions, list):
        for submission in submissions:
            if not isinstance(submission, Mapping):
                continue
            acceptance = submission.get("acceptance", {})
            source = submission.get("source", {})
            if (
                isinstance(acceptance, Mapping)
                and acceptance.get("status") == "verified"
                and isinstance(source, Mapping)
            ):
                kinds.add(_source_kind(source.get("kind")))
    evidence_items = problem.get("accepted_evidence", [])
    if isinstance(evidence_items, list):
        for evidence in evidence_items:
            if not isinstance(evidence, Mapping):
                continue
            acceptance = evidence.get("acceptance", {})
            source = evidence.get("source", {})
            if (
                isinstance(acceptance, Mapping)
                and acceptance.get("status") == "verified"
                and isinstance(source, Mapping)
            ):
                kinds.add(_source_kind(source.get("kind")))
    return {kind for kind in kinds if kind}


def is_counted_verified(problem: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    acceptance = problem.get("acceptance", {})
    if not isinstance(acceptance, Mapping) or acceptance.get("status") != "verified":
        return False
    kinds = _verified_source_kinds(problem)
    if not kinds or kinds & FIXTURE_SOURCE_KINDS:
        return False
    _, automatic_sources = _language_policy(data)
    if data.get("repository_mode") == REAL_REPOSITORY_MODE:
        return bool(kinds & automatic_sources)
    return True


def _git_blob_bytes(repo: Path, commit: Any, original_path: Any, label: str) -> bytes:
    commit_text = str(commit or "").strip().lower()
    original_text = str(original_path or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit_text) or not original_text:
        raise AutomationError(f"{label} 缺少可验证的 Git commit/original_path")
    completed = subprocess.run(
        ["git", "show", f"{commit_text}:{original_text}"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AutomationError(f"{label} 无法从 Git 历史读取原始代码：{detail}")
    return completed.stdout


def _validate_stored_code(
    repo: Path,
    relative: str,
    language: str,
    source: Mapping[str, Any],
    label: str,
) -> None:
    expected_extension = LANGUAGE_EXTENSIONS[language]
    actual_extension = Path(relative).suffix.lower().lstrip(".")
    if actual_extension != expected_extension:
        raise AutomationError(
            f"{label} 语言/扩展名不一致：metadata={language}，路径={relative}；禁止自动转换语言"
        )
    path = safe_repo_path(repo, relative)
    if not path.is_file():
        raise AutomationError(f"{label} 文件不存在：{relative}")
    content = path.read_bytes()
    expected_sha = str(source.get("sha256", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise AutomationError(f"{label} source.sha256 无效")
    actual_sha = hashlib.sha256(content).hexdigest()
    if actual_sha != expected_sha:
        raise AutomationError(f"{label} 原始代码哈希不匹配：{relative}；拒绝继续自动化")
    if "bytes" in source and source.get("bytes") != len(content):
        raise AutomationError(f"{label} source.bytes 与真实文件大小不一致：{relative}")
    if _source_kind(source.get("kind")) in PROVENANCE_SOURCE_KINDS:
        historical = _git_blob_bytes(repo, source.get("commit"), source.get("original_path"), label)
        if historical != content:
            raise AutomationError(f"{label} 与来源 Git blob 不是字节级一致：{relative}")


def validate_database(data: Mapping[str, Any], require_files: bool = False, repo: Optional[Path] = None) -> None:
    if data.get("schema_version") != SCHEMA_VERSION or not isinstance(data.get("problems"), list):
        raise AutomationError("metadata/problems.json 必须包含 schema_version=1 和 problems 数组")
    real_mode = data.get("repository_mode") == REAL_REPOSITORY_MODE
    allowed_languages, automatic_sources = _language_policy(data)
    if real_mode and (not allowed_languages or not automatic_sources):
        raise AutomationError("real_records 仓库必须声明允许语言和自动 Accepted 来源")
    seen_ids: Dict[int, int] = {}
    seen_solutions: Dict[str, int] = {}
    for index, problem in enumerate(data["problems"]):
        if not isinstance(problem, dict):
            raise AutomationError(f"problems[{index}] 必须是 object")
        missing = [field for field in REQUIRED_PROBLEM_FIELDS if field not in problem]
        if missing:
            raise AutomationError(f"problems[{index}] 缺少字段：{', '.join(missing)}")
        if not isinstance(problem["id"], int) or isinstance(problem["id"], bool) or problem["id"] <= 0:
            raise AutomationError(f"problems[{index}].id 必须是正整数")
        if not isinstance(problem["topics"], list):
            raise AutomationError(f"problems[{index}].topics 必须是数组")
        for object_field in ("acceptance", "source", "analysis"):
            if not isinstance(problem[object_field], dict):
                raise AutomationError(f"problems[{index}].{object_field} 必须是 object")
        acceptance_missing = [field for field in ("status", "source") if field not in problem["acceptance"]]
        source_missing = [field for field in ("kind", "original_path", "commit", "sha256") if field not in problem["source"]]
        analysis_missing = [
            field
            for field in ("approach", "time_complexity", "space_complexity", "pitfalls", "author")
            if field not in problem["analysis"]
        ]
        if acceptance_missing:
            raise AutomationError(f"LC{problem['id']} acceptance 缺少：{', '.join(acceptance_missing)}")
        if source_missing:
            raise AutomationError(f"LC{problem['id']} source 缺少：{', '.join(source_missing)}")
        if analysis_missing:
            raise AutomationError(f"LC{problem['id']} analysis 缺少：{', '.join(analysis_missing)}")
        language = str(problem["language"]).strip().lower()
        if language not in LANGUAGE_EXTENSIONS:
            raise AutomationError(f"LC{problem['id']} language 无效：{problem['language']}")
        if allowed_languages and language not in allowed_languages:
            raise AutomationError(f"LC{problem['id']} 使用了仓库策略不允许的语言：{language}")
        source_kind = _source_kind(problem["source"].get("kind"))
        if real_mode and source_kind in FIXTURE_SOURCE_KINDS:
            raise AutomationError(f"LC{problem['id']} 是测试/模拟 fixture，禁止进入真实记录")
        if real_mode and problem["acceptance"].get("status") == "verified" and not is_counted_verified(problem, data):
            raise AutomationError(f"LC{problem['id']} 标记 verified，但没有允许的真实 Accepted 来源")
        if problem["id"] in seen_ids:
            raise AutomationError(f"题号重复：LC{problem['id']}")
        seen_ids[problem["id"]] = index
        solution = str(problem["solution"])
        if solution in seen_solutions:
            raise AutomationError(f"solution 路径重复：{solution}")
        seen_solutions[solution] = index
        expected_extension = LANGUAGE_EXTENSIONS[language]
        if Path(solution).suffix.lower().lstrip(".") != expected_extension:
            raise AutomationError(
                f"LC{problem['id']} 语言/solution 扩展名不一致：{language} vs {solution}；禁止语言转换"
            )
        submissions = problem.get("submissions", [])
        if not isinstance(submissions, list):
            raise AutomationError(f"LC{problem['id']} submissions 必须是数组")
        for submission_index, submission in enumerate(submissions):
            if not isinstance(submission, dict):
                raise AutomationError(f"LC{problem['id']} submissions[{submission_index}] 必须是 object")
            submission_language = str(submission.get("language", "")).strip().lower()
            submission_path = str(submission.get("path", ""))
            submission_source = submission.get("source", {})
            submission_acceptance = submission.get("acceptance", {})
            if submission_language not in LANGUAGE_EXTENSIONS or not isinstance(submission_source, dict):
                raise AutomationError(f"LC{problem['id']} submission 语言或来源无效：{submission_path}")
            if Path(submission_path).suffix.lower().lstrip(".") != LANGUAGE_EXTENSIONS[submission_language]:
                raise AutomationError(f"LC{problem['id']} submission 语言/扩展名不一致：{submission_path}")
            if allowed_languages and submission_language not in allowed_languages:
                raise AutomationError(f"LC{problem['id']} submission 使用了不允许的语言：{submission_language}")
            if real_mode and submission_language != language:
                raise AutomationError(f"LC{problem['id']} submission 与 canonical 语言不同；禁止跨语言替换或混入")
            submission_kind = _source_kind(submission_source.get("kind"))
            if real_mode and submission_kind in FIXTURE_SOURCE_KINDS:
                raise AutomationError(f"LC{problem['id']} submission 是测试/模拟 fixture，禁止进入真实记录")
            if (
                real_mode
                and isinstance(submission_acceptance, Mapping)
                and submission_acceptance.get("status") == "verified"
                and submission_kind not in automatic_sources
            ):
                raise AutomationError(f"LC{problem['id']} submission 没有允许的 Accepted 来源：{submission_kind}")
            if require_files:
                if repo is None:
                    raise AutomationError("内部错误：require_files 需要 repo")
                _validate_stored_code(repo, submission_path, submission_language, submission_source, f"LC{problem['id']} submission")
        accepted_evidence = problem.get("accepted_evidence", [])
        if not isinstance(accepted_evidence, list):
            raise AutomationError(f"LC{problem['id']} accepted_evidence 必须是数组")
        for evidence_index, evidence in enumerate(accepted_evidence):
            if not isinstance(evidence, dict):
                raise AutomationError(f"LC{problem['id']} accepted_evidence[{evidence_index}] 必须是 object")
            evidence_language = str(evidence.get("language", "")).strip().lower()
            evidence_source = evidence.get("source", {})
            evidence_acceptance = evidence.get("acceptance", {})
            evidence_sha = str(evidence.get("sha256", "")).lower()
            if evidence_language not in LANGUAGE_EXTENSIONS or not isinstance(evidence_source, dict):
                raise AutomationError(f"LC{problem['id']} Accepted evidence 语言或来源无效")
            if allowed_languages and evidence_language not in allowed_languages:
                raise AutomationError(f"LC{problem['id']} Accepted evidence 使用了不允许的语言：{evidence_language}")
            if real_mode and evidence_language != language:
                raise AutomationError(f"LC{problem['id']} Accepted evidence 与 canonical 语言不同")
            evidence_kind = _source_kind(evidence_source.get("kind"))
            if real_mode and evidence_kind not in automatic_sources:
                raise AutomationError(f"LC{problem['id']} Accepted evidence 来源不允许：{evidence_kind}")
            if not isinstance(evidence_acceptance, Mapping) or evidence_acceptance.get("status") != "verified":
                raise AutomationError(f"LC{problem['id']} Accepted evidence 必须标记 verified")
            if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha):
                raise AutomationError(f"LC{problem['id']} Accepted evidence sha256 无效")
            original_extension = Path(str(evidence_source.get("original_path", ""))).suffix.lower().lstrip(".")
            if original_extension and original_extension != LANGUAGE_EXTENSIONS[evidence_language]:
                raise AutomationError(f"LC{problem['id']} Accepted evidence 来源扩展名与语言冲突")
            if require_files and evidence_kind in PROVENANCE_SOURCE_KINDS:
                if repo is None:
                    raise AutomationError("内部错误：require_files 需要 repo")
                original_bytes = _git_blob_bytes(
                    repo,
                    evidence_source.get("commit"),
                    evidence_source.get("original_path"),
                    f"LC{problem['id']} Accepted evidence",
                )
                if hashlib.sha256(original_bytes).hexdigest() != evidence_sha:
                    raise AutomationError(f"LC{problem['id']} Accepted evidence 与来源 Git blob 不一致")
        if require_files:
            if repo is None:
                raise AutomationError("内部错误：require_files 需要 repo")
            _validate_stored_code(repo, solution, language, problem["source"], f"LC{problem['id']} canonical")


def save_database(repo: Path, data: MutableMapping[str, Any], dry_run: bool = False) -> bool:
    validate_database(data)
    data["problems"].sort(key=lambda item: (int(item["id"]), str(item["slug"])))
    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if dry_run:
        path = repo / "metadata" / "problems.json"
        return not path.exists() or path.read_text(encoding="utf-8-sig") != rendered
    return atomic_write_text(repo / "metadata" / "problems.json", rendered)


def slugify(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "untitled"


def normalize_language(value: Any, suffix: str = "") -> str:
    text = str(value or "").strip().lower().replace("_", "").replace("-", "")
    aliases = {
        "c++": "cpp",
        "cplusplus": "cpp",
        "cpp17": "cpp",
        "cpp20": "cpp",
        "python3": "python",
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "golang": "go",
        "rs": "rust",
        "c#": "csharp",
        "cs": "csharp",
        "kt": "kotlin",
    }
    if text in LANGUAGE_EXTENSIONS:
        return text
    if text in aliases:
        return aliases[text]
    ext = suffix.lower().lstrip(".")
    if ext in EXTENSION_LANGUAGES:
        return EXTENSION_LANGUAGES[ext]
    raise AutomationError(f"不支持的语言：{value or suffix}")


def submission_language(value: Any, suffix: str = "") -> str:
    explicit = normalize_language(value) if str(value or "").strip() else None
    inferred = normalize_language("", suffix) if str(suffix or "").strip() else None
    if explicit and inferred and explicit != inferred:
        raise AutomationError(
            f"submission 语言与文件扩展名冲突：metadata={explicit}，extension={suffix}；禁止转换语言"
        )
    if explicit is not None:
        return explicit
    if inferred is not None:
        return inferred
    raise AutomationError("submission 缺少可验证的语言和文件扩展名")


def normalize_difficulty(value: Any) -> str:
    text = str(value or "Unknown").strip().lower()
    return {"easy": "Easy", "medium": "Medium", "hard": "Hard"}.get(text, "Unknown")


def normalize_topic(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("slug") or value.get("translatedName") or ""
    return str(value).strip()


def _first(mapping: Mapping[str, Any], names: Sequence[str], default: Any = None) -> Any:
    for name in names:
        current: Any = mapping
        found = True
        for component in name.split("."):
            if not isinstance(current, Mapping) or component not in current:
                found = False
                break
            current = current[component]
        if found and current not in (None, ""):
            return current
    return default


def _problem_id(mapping: Mapping[str, Any], fallback: Optional[int] = None) -> int:
    raw = _first(
        mapping,
        (
            "questionFrontendId",
            "frontendQuestionId",
            "question.questionFrontendId",
            "id",
            "questionId",
            "question_id",
        ),
        fallback,
    )
    try:
        result = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise AutomationError(f"无法确定 LeetCode 题号：{raw!r}") from exc
    if result <= 0:
        raise AutomationError(f"LeetCode 题号必须为正整数：{result}")
    return result


def _accepted_status(mapping: Mapping[str, Any]) -> Tuple[bool, str]:
    raw = _first(mapping, ("status", "statusDisplay", "state", "acceptance.status", "submission.status"), None)
    if raw is None and isinstance(mapping.get("accepted"), bool):
        raw = mapping["accepted"]
    if isinstance(raw, bool):
        return raw, "bundle:accepted"
    normalized = str(raw or "").strip().lower().replace("_", " ")
    accepted = normalized in {"accepted", "ac"}
    return accepted, f"bundle:status={raw}" if raw is not None else "source:unverified"


def _topics(mapping: Mapping[str, Any]) -> List[str]:
    raw = _first(mapping, ("topics", "topicTags", "question.topicTags"), [])
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list):
        raw = []
    result: List[str] = []
    for item in raw:
        topic = normalize_topic(item)
        if topic and topic not in result:
            result.append(topic)
    return result


def _metadata_from_directory(directory: Path) -> Dict[str, Any]:
    for name in ("metadata.json", "problem.json", "info.json", ".leetcode.json"):
        path = directory / name
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AutomationError(f"无法读取 LeetHub metadata：{path}: {exc}") from exc
            if not isinstance(value, dict):
                raise AutomationError(f"LeetHub metadata 必须是 object：{path}")
            return value
    return {}


def _candidate_code_files(directory: Path) -> List[Path]:
    candidates = [
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower().lstrip(".") in CODE_EXTENSIONS
        and "optimized" not in path.stem.lower()
    ]
    directory_stem = directory.name.lower()

    def preference(path: Path) -> Tuple[int, str]:
        stem = path.stem.lower()
        if stem == "solution":
            return (0, path.name)
        if stem == directory_stem:
            return (1, path.name)
        return (2, path.name)

    return sorted(candidates, key=preference)


def _candidate_code_file(directory: Path) -> Optional[Path]:
    candidates = _candidate_code_files(directory)
    return candidates[0] if candidates else None


DIRECTORY_PATTERN = re.compile(r"^(?P<id>\d{1,7})(?:[.\s_-]+)(?P<name>.+)$")


def is_leethub_directory(path: Path) -> bool:
    return path.is_dir() and bool(DIRECTORY_PATTERN.match(path.name)) and _candidate_code_file(path) is not None


def _has_leethub_git_provenance(repo: Path, code_path: Path) -> bool:
    """Trust a directory import only when Git records LeetHub as that code file's writer."""
    if not _is_inside(code_path, repo):
        return False
    relative = code_path.resolve().relative_to(repo.resolve()).as_posix()
    completed = subprocess.run(
        ["git", "log", "-1", "--format=%an%x00%ae%x00%B", "--", relative],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        return False
    author_name, _, remainder = completed.stdout.partition("\x00")
    author_email, _, message = remainder.partition("\x00")
    is_bot = "[bot]" in author_name.lower() or "[bot]" in author_email.lower()
    return not is_bot and bool(re.search(r"\bLeetHub\b", message, re.IGNORECASE))


def submission_from_directory(directory: Path, repo: Path, selected_code: Optional[Path] = None) -> Submission:
    match = DIRECTORY_PATTERN.match(directory.name)
    if not match:
        raise AutomationError(f"目录名不能识别题号：{directory}")
    metadata = _metadata_from_directory(directory)
    code_path = selected_code or _candidate_code_file(directory)
    code_value = _first(metadata, ("code", "content", "submissionCode", "submission.code"), None)
    if code_path is None and not isinstance(code_value, str):
        raise AutomationError(f"目录中没有支持的解答文件：{directory}")
    if code_path is not None:
        code = code_path.read_bytes()
        language = submission_language(_first(metadata, ("language", "lang", "submission.language"), ""), code_path.suffix)
    else:
        code = str(code_value).encode("utf-8")
        language = submission_language(_first(metadata, ("language", "lang", "submission.language"), ""))
    problem_id = _problem_id(metadata, int(match.group("id")))
    fallback_slug = slugify(match.group("name"))
    title = str(_first(metadata, ("title", "question.title"), match.group("name").replace("-", " ").strip()))
    slug = slugify(_first(metadata, ("slug", "titleSlug", "title_slug", "question.titleSlug"), fallback_slug))
    title_zh = str(_first(metadata, ("title_zh", "titleZh", "translatedTitle", "question.translatedTitle"), title))
    topics = _topics(metadata)
    primary = slugify(_first(metadata, ("primary_topic", "primaryTopic"), topics[0] if topics else "uncategorized"))
    accepted, accepted_source = _accepted_status(metadata)
    if not accepted and code_path is not None and _has_leethub_git_provenance(repo, code_path):
        accepted = True
        accepted_source = "git:LeetHub commit"
    return Submission(
        problem_id=problem_id,
        slug=slug,
        title=title,
        title_zh=title_zh,
        difficulty=normalize_difficulty(_first(metadata, ("difficulty", "question.difficulty"), "Unknown")),
        topics=topics,
        primary_topic=primary,
        language=language,
        code=code,
        accepted=accepted,
        acceptance_source=accepted_source,
        source_kind="leethub",
        original_path=posix_relative(code_path or directory, repo),
    )


def submissions_from_directory(directory: Path, repo: Path) -> List[Submission]:
    candidates = _candidate_code_files(directory)
    if not candidates:
        return [submission_from_directory(directory, repo)]
    return [submission_from_directory(directory, repo, candidate) for candidate in candidates]


def _bundle_entries(value: Any) -> List[Mapping[str, Any]]:
    if isinstance(value, list):
        entries = value
    elif isinstance(value, dict):
        entries = None
        for key in ("submissions", "entries", "items"):
            if isinstance(value.get(key), list):
                entries = value[key]
                break
        if entries is None and isinstance(value.get("submission"), dict):
            entries = [value["submission"]]
        if entries is None:
            entries = [value]
    else:
        raise AutomationError("JSON bundle 根节点必须是 object 或 array")
    if not all(isinstance(entry, dict) for entry in entries):
        raise AutomationError("JSON bundle 中的每个 submission 必须是 object")
    return list(entries)


def submissions_from_bundle(bundle: Path, repo: Path) -> List[Submission]:
    try:
        value = json.loads(bundle.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutomationError(f"无法读取 JSON bundle：{bundle}: {exc}") from exc
    results: List[Submission] = []
    for index, entry in enumerate(_bundle_entries(value)):
        code_value = _first(entry, ("code", "content", "submissionCode", "submission.code"), None)
        code_path: Optional[Path] = None
        if not isinstance(code_value, str):
            reference = _first(entry, ("file", "filename", "code_path", "solution"), None)
            if reference:
                code_path = (bundle.parent / str(reference)).resolve()
                if not _is_inside(code_path, bundle.parent) or not code_path.is_file():
                    raise AutomationError(f"bundle code 路径无效或越界：{reference}")
                code = code_path.read_bytes()
            else:
                raise AutomationError(f"JSON bundle 第 {index + 1} 项缺少 code 或有效代码文件")
        else:
            code = code_value.encode("utf-8")
        language = submission_language(
            _first(entry, ("language", "lang", "submission.language"), ""),
            code_path.suffix if code_path else Path(str(_first(entry, ("filename", "file"), ""))).suffix,
        )
        problem_id = _problem_id(entry)
        title = str(_first(entry, ("title", "question.title"), f"LeetCode {problem_id}"))
        slug = slugify(_first(entry, ("slug", "titleSlug", "title_slug", "question.titleSlug"), title))
        title_zh = str(_first(entry, ("title_zh", "titleZh", "translatedTitle", "question.translatedTitle"), title))
        topics = _topics(entry)
        primary = slugify(_first(entry, ("primary_topic", "primaryTopic"), topics[0] if topics else "uncategorized"))
        accepted, accepted_source = _accepted_status(entry)
        results.append(
            Submission(
                problem_id=problem_id,
                slug=slug,
                title=title,
                title_zh=title_zh,
                difficulty=normalize_difficulty(_first(entry, ("difficulty", "question.difficulty"), "Unknown")),
                topics=topics,
                primary_topic=primary,
                language=language,
                code=code,
                accepted=accepted,
                acceptance_source=accepted_source,
                source_kind="json_bundle",
                original_path=f"{posix_relative(bundle, repo)}#{index + 1}",
            )
        )
    return results


def git_output(repo: Path, arguments: Sequence[str], check: bool = True) -> str:
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
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise AutomationError(f"git {' '.join(arguments)} 失败：{detail}")
    return completed.stdout.strip()


def current_commit(repo: Path) -> Optional[str]:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="ascii",
        errors="ignore",
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _source_object(submission: Submission, repo: Path, sha256: Optional[str] = None) -> Dict[str, Any]:
    return {
        "kind": submission.source_kind,
        "original_path": submission.original_path,
        "commit": current_commit(repo),
        "sha256": sha256 or submission.sha256,
    }


def _new_problem_record(submission: Submission, repo: Path, solution: str, *, legacy_sha: Optional[str] = None) -> Dict[str, Any]:
    if legacy_sha is None:
        acceptance = {"status": "verified" if submission.accepted else "unverified", "source": submission.acceptance_source}
        source = _source_object(submission, repo)
    else:
        acceptance = {"status": "unverified", "source": "existing-local-file"}
        source = {
            "kind": "legacy",
            "original_path": solution,
            "commit": current_commit(repo),
            "sha256": legacy_sha,
        }
    return {
        "id": submission.problem_id,
        "slug": submission.slug,
        "title": submission.title,
        "title_zh": submission.title_zh,
        "difficulty": submission.difficulty,
        "topics": submission.topics,
        "primary_topic": submission.primary_topic,
        "language": submission.language,
        "solution": solution,
        "acceptance": acceptance,
        "source": source,
        "analysis": {
            "approach": "",
            "time_complexity": "",
            "space_complexity": "",
            "pitfalls": [],
            "author": "Codex",
        },
    }


def _find_problem(data: Mapping[str, Any], submission: Submission) -> Optional[MutableMapping[str, Any]]:
    by_slug: Optional[MutableMapping[str, Any]] = None
    for raw in data["problems"]:
        problem = raw
        if int(problem.get("id", -1)) == submission.problem_id:
            return problem
        if str(problem.get("slug", "")) == submission.slug:
            by_slug = problem
    if by_slug is not None and int(by_slug.get("id", -1)) != submission.problem_id:
        raise AutomationError(
            f"slug 冲突：{submission.slug} 已属于 LC{by_slug.get('id')}，不能导入 LC{submission.problem_id}"
        )
    return None


def _alternative_path(repo: Path, problem_dir: Path, submission: Submission) -> Path:
    extension = LANGUAGE_EXTENSIONS[submission.language]
    for length in (12, 16, 24, 64):
        candidate = problem_dir / "submissions" / f"{submission.sha256[:length]}.{extension}"
        if not candidate.exists() or candidate.read_bytes() == submission.code:
            return candidate
    raise AutomationError(f"无法为 LC{submission.problem_id} 分配无冲突 submission 文件名")


def import_submission(
    repo: Path,
    data: MutableMapping[str, Any],
    submission: Submission,
    conflict: str = "preserve",
    dry_run: bool = False,
) -> ImportResult:
    if not submission.accepted:
        raise AutomationError(
            f"LC{submission.problem_id} 没有明确的 Accepted 证明；拒绝写入 canonical solution"
        )
    allowed_languages, automatic_sources = _language_policy(data)
    if allowed_languages and submission.language not in allowed_languages:
        raise AutomationError(
            f"LC{submission.problem_id} 的 {submission.language} submission 不符合仓库允许语言；拒绝导入"
        )
    if (
        data.get("repository_mode") == REAL_REPOSITORY_MODE
        and submission.source_kind not in automatic_sources
    ):
        raise AutomationError(
            f"LC{submission.problem_id} 的来源 {submission.source_kind} 不允许自动标记为真实 Accepted"
        )
    record = _find_problem(data, submission)
    if record is not None and str(record.get("language", "")).lower() != submission.language:
        raise AutomationError(
            f"LC{submission.problem_id} 已记录为 {record.get('language')}，收到 {submission.language}；"
            "禁止跨语言替换或混入，请保留原始输入并人工核对来源"
        )
    extension = LANGUAGE_EXTENSIONS[submission.language]
    desired_solution = f"problems/{submission.problem_id:04d}-{submission.slug}/solution.{extension}"
    new_record = record is None
    existing_target = safe_repo_path(repo, desired_solution)
    legacy_sha: Optional[str] = None
    if record is None:
        if existing_target.is_file() and existing_target.read_bytes() != submission.code:
            legacy_sha = hashlib.sha256(existing_target.read_bytes()).hexdigest()
        record = _new_problem_record(submission, repo, desired_solution, legacy_sha=legacy_sha)
        data["problems"].append(record)

    solution_relative = str(record["solution"])
    solution_path = safe_repo_path(repo, solution_relative)
    changed: List[str] = []
    canonical_matches = False
    if not solution_path.exists():
        if not new_record:
            expected_sha = str(record.get("source", {}).get("sha256", "")).lower()
            if expected_sha != submission.sha256:
                raise AutomationError(
                    f"LC{submission.problem_id} 的 canonical solution 缺失，且新代码哈希与原记录不符；"
                    "请从 Git 历史恢复原文件，自动化不会替换它"
                )
        if not dry_run:
            atomic_write_bytes(solution_path, submission.code)
        changed.append(solution_relative)
        action = "new" if new_record else "restored"
    else:
        existing = solution_path.read_bytes()
        if existing == submission.code:
            canonical_matches = True
            action = "new" if new_record else "unchanged"
        else:
            if conflict == "reject":
                if new_record:
                    data["problems"].remove(record)
                raise AutomationError(
                    f"LC{submission.problem_id} 已有不同的 {solution_relative}；按 --conflict reject 拒绝导入"
                )
            alternative = _alternative_path(repo, solution_path.parent, submission)
            alternative_relative = alternative.relative_to(repo).as_posix()
            if not alternative.exists():
                if not dry_run:
                    atomic_write_bytes(alternative, submission.code)
                changed.append(alternative_relative)
                action = "submission"
            else:
                action = "unchanged"
            submissions = record.setdefault("submissions", [])
            if not isinstance(submissions, list):
                raise AutomationError(f"LC{submission.problem_id} 的 submissions 字段不是数组")
            if not any(isinstance(item, dict) and item.get("sha256") == submission.sha256 for item in submissions):
                submissions.append(
                    {
                        "path": alternative_relative,
                        "language": submission.language,
                        "acceptance": {
                            "status": "verified" if submission.accepted else "unverified",
                            "source": submission.acceptance_source,
                        },
                        "source": _source_object(submission, repo),
                        "sha256": submission.sha256,
                    }
                )
                if action == "unchanged":
                    action = "metadata"

    if submission.accepted and not new_record and canonical_matches:
        evidence_field = record.get("accepted_evidence", [])
        submissions_field = record.get("submissions", [])
        if not isinstance(evidence_field, list) or not isinstance(submissions_field, list):
            raise AutomationError(f"LC{submission.problem_id} 的 Accepted 证据字段不是数组")
        existing_evidence = evidence_field + submissions_field
        already_recorded = any(
            isinstance(item, Mapping)
            and item.get("sha256") == submission.sha256
            and isinstance(item.get("source"), Mapping)
            and _source_kind(item["source"].get("kind")) == submission.source_kind
            for item in existing_evidence
        )
        if not already_recorded:
            evidence = record.setdefault("accepted_evidence", [])
            if not isinstance(evidence, list):
                raise AutomationError(f"LC{submission.problem_id} 的 accepted_evidence 字段不是数组")
            evidence.append(
                {
                    "language": submission.language,
                    "acceptance": {"status": "verified", "source": submission.acceptance_source},
                    "source": _source_object(submission, repo),
                    "sha256": submission.sha256,
                }
            )
            if action == "unchanged":
                action = "metadata"

    if submission.accepted:
        acceptance = record.setdefault("acceptance", {})
        if acceptance.get("status") != "verified" or acceptance.get("source") != submission.acceptance_source:
            acceptance["status"] = "verified"
            acceptance["source"] = submission.acceptance_source
            if action == "unchanged":
                action = "metadata"
        for field, value in (
            ("title", submission.title),
            ("title_zh", submission.title_zh),
            ("difficulty", submission.difficulty),
        ):
            if record.get(field) in (None, "", "Unknown", f"LeetCode {submission.problem_id}") and value:
                record[field] = value
                if action == "unchanged":
                    action = "metadata"
        if not record.get("topics") and submission.topics:
            record["topics"] = submission.topics
            record["primary_topic"] = submission.primary_topic
            if action == "unchanged":
                action = "metadata"

    metadata_changed = save_database(repo, data, dry_run=dry_run)
    if metadata_changed:
        changed.append("metadata/problems.json")
    return ImportResult(action, submission.problem_id, submission.title, sorted(set(changed)), solution_relative)


def discover_inputs(source: Path, repo: Path) -> List[Tuple[str, Path]]:
    source = source.resolve()
    if source.is_file():
        if source.suffix.lower() != ".json":
            raise AutomationError(f"只支持 JSON bundle 文件：{source}")
        return [("bundle", source)]
    if not source.is_dir():
        raise AutomationError(f"导入源不存在：{source}")
    if is_leethub_directory(source):
        return [("directory", source)]

    discovered: List[Tuple[str, Path]] = []
    for current_text, directories, files in os.walk(str(source), followlinks=False):
        current = Path(current_text)
        directories[:] = sorted(
            [
                name
                for name in directories
                if name not in PROTECTED_TOP_LEVEL
                and name != "__pycache__"
                and not (current / name).is_symlink()
            ],
            key=str.lower,
        )
        if current != source and is_leethub_directory(current):
            discovered.append(("directory", current))
            directories[:] = []
            continue
        for name in sorted(files, key=str.lower):
            child = current / name
            if child.suffix.lower() != ".json" or child.is_symlink():
                continue
            recognized_name = bool(re.match(r"^(?:leetcode|submission|bundle)[-_].*\.json$", name, re.IGNORECASE))
            in_inbox = any(part.lower() in {"incoming", "inbox", ".leethub", "leetcode-submissions"} for part in child.parts)
            if source != repo or recognized_name or in_inbox:
                discovered.append(("bundle", child))
    return discovered


def _validate_consumable(path: Path, repo: Path) -> str:
    resolved = path.resolve()
    if not _is_inside(resolved, repo) or resolved == repo.resolve():
        raise AutomationError(f"为安全起见，不删除仓库外或仓库根导入源：{path}")
    relative = resolved.relative_to(repo.resolve())
    if not relative.parts or relative.parts[0] in PROTECTED_TOP_LEVEL:
        raise AutomationError(f"拒绝删除受保护路径：{relative.as_posix()}")
    return relative.as_posix()


def _unique_auxiliary_target(target: Path, content: bytes) -> Path:
    if not target.exists() or target.read_bytes() == content:
        return target
    digest = hashlib.sha256(content).hexdigest()[:12]
    return target.with_name(f"{target.stem}-{digest}{target.suffix}")


def _preserve_directory_auxiliary(
    source: Path,
    repo: Path,
    submissions: Sequence[Submission],
    source_results: Sequence[ImportResult],
    dry_run: bool,
) -> List[str]:
    if not source_results:
        raise AutomationError(f"导入源没有可验证的代码，拒绝清理：{source}")
    problem_ids = {item.problem_id for item in source_results}
    if len(problem_ids) != 1:
        raise AutomationError(f"单个 LeetHub 目录包含多个题号，拒绝清理：{source}")
    problem_dir = safe_repo_path(repo, source_results[0].solution_path).parent

    stored_hashes: set[str] = set()
    if problem_dir.exists():
        for candidate in problem_dir.rglob("*"):
            if candidate.is_file() and candidate.suffix.lower().lstrip(".") in CODE_EXTENSIONS:
                stored_hashes.add(hashlib.sha256(candidate.read_bytes()).hexdigest())
    if dry_run:
        # Planned imports are intentionally not written during a dry run, but
        # they still count as preserved for the consume-safety simulation.
        stored_hashes.update(item.sha256 for item in submissions)
    missing = sorted({item.sha256 for item in submissions} - stored_hashes)
    if missing:
        raise AutomationError(
            f"导入代码尚未完整保存在 canonical 目录，拒绝清理 {source}：{', '.join(missing)}"
        )

    imported_code = {
        Path(item.original_path).resolve()
        for item in submissions
        if Path(item.original_path).is_absolute()
    }
    if not imported_code:
        imported_code = {path.resolve() for path in _candidate_code_files(source)}

    changed: List[str] = []
    for item in sorted(source.rglob("*"), key=lambda path: path.as_posix().lower()):
        if item.is_symlink():
            raise AutomationError(f"导入源包含符号链接，拒绝自动清理：{item}")
        if not item.is_file() or item.resolve() in imported_code:
            continue
        relative = item.relative_to(source)
        content = item.read_bytes()
        target = _unique_auxiliary_target(problem_dir / "source" / relative, content)
        if not dry_run:
            atomic_write_bytes(target, content)
        changed.append(target.relative_to(repo).as_posix())
    return changed


def _consume_path(path: Path, repo: Path) -> Optional[str]:
    relative_text = _validate_consumable(path, repo)
    resolved = path.resolve()
    if resolved.is_dir():
        shutil.rmtree(resolved)
    elif resolved.exists():
        resolved.unlink()
    return relative_text


def organize(
    repo: Path,
    sources: Sequence[Path],
    conflict: str = "preserve",
    consume: bool = False,
    dry_run: bool = False,
) -> Tuple[List[ImportResult], List[str]]:
    data = load_database(repo)
    validate_database(data)
    inputs: List[Tuple[str, Path]] = []
    seen: set[str] = set()
    for source in sources:
        for kind, path in discover_inputs(source, repo):
            key = str(path.resolve()).casefold()
            if key not in seen:
                inputs.append((kind, path))
                seen.add(key)
    if consume:
        for kind, path in inputs:
            _validate_consumable(path, repo)
            if kind != "directory":
                raise AutomationError("为避免丢失 bundle 中的未知字段，自动化只清理 LeetHub 目录，不删除 JSON bundle")
    results: List[ImportResult] = []
    consumed: List[str] = []
    for kind, path in inputs:
        submissions = submissions_from_directory(path, repo) if kind == "directory" else submissions_from_bundle(path, repo)
        source_results: List[ImportResult] = []
        for submission in submissions:
            result = import_submission(repo, data, submission, conflict=conflict, dry_run=dry_run)
            results.append(result)
            source_results.append(result)
        if consume:
            auxiliary = _preserve_directory_auxiliary(path, repo, submissions, source_results, dry_run)
            if source_results:
                source_results[0].changed_paths.extend(auxiliary)
            if dry_run:
                consumed.append(path.resolve().relative_to(repo.resolve()).as_posix())
            else:
                consumed_path = _consume_path(path, repo)
                if consumed_path:
                    consumed.append(consumed_path)
    return results, consumed


def _markdown(value: Any) -> str:
    return str(value if value not in (None, "") else "待补充").replace("|", "\\|").replace("\n", " ")


def _topic_display(topic: str) -> str:
    return " ".join(part.title() for part in topic.replace("_", "-").split("-") if part) or "Uncategorized"


def render_problem_readme(problem: Mapping[str, Any]) -> str:
    acceptance = problem["acceptance"]
    analysis = problem["analysis"]
    status = str(acceptance.get("status", "unverified"))
    verified_text = "已验证 Accepted" if status == "verified" else "历史导入，Accepted 状态待验证"
    topics = "、".join(str(topic) for topic in problem["topics"]) or "待补充"
    solution_name = Path(str(problem["solution"])).name
    pitfalls = analysis.get("pitfalls", "")
    if isinstance(pitfalls, list):
        pitfalls_text = "；".join(str(item) for item in pitfalls if str(item).strip()) or "待补充"
    else:
        pitfalls_text = _markdown(pitfalls)
    return (
        f"# LC{problem['id']} {_markdown(problem['title_zh'])} / {_markdown(problem['title'])}\n\n"
        "<!-- 此文件由 scripts/leetcode_repo.py 根据 metadata/problems.json 生成。 -->\n\n"
        f"- 难度：{_markdown(problem['difficulty'])}\n"
        f"- 语言：{_markdown(problem['language'])}\n"
        f"- Topics：{topics}\n"
        f"- 主分类：{_markdown(problem['primary_topic'])}\n"
        f"- 验证状态：{verified_text}\n\n"
        "## 我的代码\n\n"
        f"[`{solution_name}`]({solution_name}) 是保留的原始解答；自动化不会覆盖它。\n\n"
        "## Codex 分析\n\n"
        f"- 解法思路：{_markdown(analysis.get('approach'))}\n"
        f"- 时间复杂度：{_markdown(analysis.get('time_complexity'))}\n"
        f"- 空间复杂度：{_markdown(analysis.get('space_complexity'))}\n"
        f"- 易错点：{pitfalls_text}\n"
        f"- 分析作者：{_markdown(analysis.get('author'))}\n"
    )


def render_root_readme(data: Mapping[str, Any]) -> str:
    problems = data["problems"]
    ordered = sorted(problems, key=lambda item: (slugify(item["primary_topic"]), int(item["id"])))
    verified_items = [item for item in ordered if is_counted_verified(item, data)]
    not_counted = len(ordered) - len(verified_items)
    counts = {
        level: sum(1 for item in verified_items if item["difficulty"] == level)
        for level in ("Easy", "Medium", "Hard")
    }
    allowed_languages, _ = _language_policy(data)
    language_text = ", ".join(sorted(allowed_languages)) if allowed_languages else "按真实 submission 保留"
    lines = [
        "# LeetCode",
        "",
        "<!-- 此文件由 scripts/leetcode_repo.py 根据 metadata/problems.json 生成。 -->",
        "",
        "保存本人原始 LeetCode 解答与简洁学习记录。`solution.*` 永不被自动化覆盖；AI 建议只会独立存放。",
        "",
        f"Verified Accepted / Solved: **{len(verified_items)}**",
        "",
        f"Historical or unverified records（不计入 Solved）: **{not_counted}**",
        "",
        f"Verified difficulty — Easy: **{counts['Easy']}** · Medium: **{counts['Medium']}** · Hard: **{counts['Hard']}**",
        "",
        f"Accepted language policy: **{language_text}**",
        "",
        "## 自动同步",
        "",
        "在仓库根目录运行 `sync.cmd`。它只执行快进拉取，随后导入新题、生成索引、扫描敏感信息、规范提交并正常 push。",
        "",
    ]
    grouped: Dict[str, List[Mapping[str, Any]]] = {}
    for problem in ordered:
        grouped.setdefault(slugify(problem["primary_topic"] or "uncategorized"), []).append(problem)
    for topic in sorted(grouped):
        lines.extend(
            [
                f"## {_topic_display(topic)}",
                "",
                "| ID | Problem | Language | Difficulty | Status | Solution |",
                "| --: | --- | --- | --- | --- | --- |",
            ]
        )
        for problem in grouped[topic]:
            solution = str(problem["solution"])
            problem_readme = (Path(solution).parent / "README.md").as_posix()
            title = f"{_markdown(problem['title_zh'])} / {_markdown(problem['title'])}"
            status = "verified" if is_counted_verified(problem, data) else "historical/unverified"
            lines.append(
                f"| {int(problem['id'])} | [{title}]({problem_readme}) | {_markdown(problem['language'])} | {_markdown(problem['difficulty'])} | {status} | [code]({solution}) |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate_readmes(repo: Path, check: bool = False, dry_run: bool = False) -> List[str]:
    data = load_database(repo)
    validate_database(data, require_files=True, repo=repo)
    planned: Dict[Path, str] = {repo / "README.md": render_root_readme(data)}
    for problem in data["problems"]:
        solution_path = safe_repo_path(repo, str(problem["solution"]))
        planned[solution_path.parent / "README.md"] = render_problem_readme(problem)
    changed: List[str] = []
    for path, content in planned.items():
        current = path.read_text(encoding="utf-8-sig") if path.exists() else None
        if current != content:
            changed.append(path.relative_to(repo).as_posix())
            if not check and not dry_run:
                atomic_write_text(path, content)
    return sorted(changed)


def _secret_patterns() -> List[Tuple[str, re.Pattern[str]]]:
    return [
        ("private key", re.compile("-----BEGIN " + "(?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
        ("AWS access key", re.compile("AK" + r"IA[0-9A-Z]{16}")),
        ("GitHub token", re.compile("gh" + r"[pousr]_[A-Za-z0-9]{20,255}")),
        ("GitHub fine-grained token", re.compile(r"github_pat_[A-Za-z0-9_]{20,255}")),
        ("OpenAI-style key", re.compile("sk" + r"-[A-Za-z0-9_-]{20,255}")),
        ("Slack token", re.compile("xox" + r"[abprs]-[A-Za-z0-9-]{10,255}")),
        (
            "LeetCode/browser session",
            re.compile(
                r"(?i)\b(?:leetcode_session|csrftoken|cf_clearance)\b"
                r"\s*[:=]\s*['\"]?([A-Za-z0-9._%+\-/=]{8,})"
            ),
        ),
        (
            "Cookie header",
            re.compile(r"(?i)\bcookie\b\s*:\s*['\"]?([^'\"\r\n]{12,})"),
        ),
        (
            "hard-coded credential",
            re.compile(
                r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|client[_-]?secret)\b"
                r"\s*[:=]\s*['\"]([^'\"\r\n]{8,})['\"]"
            ),
        ),
        ("credential in URL", re.compile(r"https?://[^\s/:@]{2,}:[^\s/@]{4,}@")),
    ]


def _placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in ("example", "placeholder", "redacted", "changeme", "your_", "your-", "xxxx", "dummy"))


def _filename_is_sensitive(path: str) -> bool:
    name = Path(path).name.lower()
    return name == ".env" or name.startswith(".env.") and name not in {".env.example", ".env.sample"} or name in {
        "id_rsa",
        "id_dsa",
        "id_ed25519",
        "credentials.json",
        "service-account.json",
        "cookies.txt",
        "cookies.json",
        "session.json",
    } or name.endswith((".pem", ".p12", ".pfx"))


def _all_scan_files(repo: Path) -> List[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise AutomationError(f"git ls-files 失败：{completed.stderr.decode('utf-8', 'replace').strip()}")
    return [part.decode("utf-8", "surrogateescape") for part in completed.stdout.split(b"\0") if part]


def _staged_scan_files(repo: Path) -> List[str]:
    completed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise AutomationError(f"git diff --cached 失败：{completed.stderr.decode('utf-8', 'replace').strip()}")
    return [part.decode("utf-8", "surrogateescape") for part in completed.stdout.split(b"\0") if part]


def _ref_scan_files(repo: Path, ref: str) -> List[str]:
    completed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", ref],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise AutomationError(
            f"git ls-tree {ref} 失败：{completed.stderr.decode('utf-8', 'replace').strip()}"
        )
    return [part.decode("utf-8", "surrogateescape") for part in completed.stdout.split(b"\0") if part]


def _decode_scan_content(content: bytes) -> str:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16", "replace")
    if b"\0" in content[:8192]:
        even_nuls = content[0::2].count(0)
        odd_nuls = content[1::2].count(0)
        encoding = "utf-16-be" if even_nuls > odd_nuls else "utf-16-le"
        return content.decode(encoding, "replace")
    return content.decode("utf-8", "replace")


def scan_secrets(repo: Path, staged: bool = False, ref: Optional[str] = None) -> List[str]:
    if staged and ref:
        raise AutomationError("scan 不能同时使用 --staged 和 --ref")
    paths = _ref_scan_files(repo, ref) if ref else (_staged_scan_files(repo) if staged else _all_scan_files(repo))
    issues: List[str] = []
    patterns = _secret_patterns()
    for relative in paths:
        normalized = relative.replace("\\", "/")
        if normalized.startswith(".git/"):
            continue
        if _filename_is_sensitive(normalized):
            issues.append(f"{normalized}: 敏感文件名")
        if staged:
            completed = subprocess.run(
                ["git", "show", f":{relative}"], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            if completed.returncode != 0:
                continue
            content = completed.stdout
        elif ref:
            completed = subprocess.run(
                ["git", "show", f"{ref}:{relative}"], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            if completed.returncode != 0:
                continue
            content = completed.stdout
        else:
            path = safe_repo_path(repo, relative)
            if not path.is_file():
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
        text = _decode_scan_content(content)
        for line_number, line in enumerate(text.splitlines(), 1):
            for label, pattern in patterns:
                match = pattern.search(line)
                if match and not (match.groups() and _placeholder(str(match.group(1)))):
                    issues.append(f"{normalized}:{line_number}: 疑似 {label}")
    return sorted(set(issues))


def guard_canonical_solutions(repo: Path) -> List[str]:
    """Reject staged changes to solutions already declared in HEAD metadata."""
    metadata = subprocess.run(
        ["git", "show", "HEAD:metadata/problems.json"],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if metadata.returncode != 0:
        return []
    try:
        data = json.loads(metadata.stdout.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["HEAD 中的 metadata/problems.json 无法解析，不能验证原始解答"]
    if not isinstance(data, dict) or not isinstance(data.get("problems"), list):
        return ["HEAD 中的 metadata/problems.json 结构无效，不能验证原始解答"]
    issues: List[str] = []
    for problem in data["problems"]:
        if not isinstance(problem, dict) or not isinstance(problem.get("solution"), str):
            continue
        relative = problem["solution"]
        before = subprocess.run(
            ["git", "show", f"HEAD:{relative}"], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        if before.returncode != 0:
            continue
        after = subprocess.run(
            ["git", "show", f":{relative}"], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        if after.returncode != 0:
            issues.append(f"LC{problem.get('id')}: 禁止删除或移动原始解答 {relative}")
        elif after.stdout != before.stdout:
            source = problem.get("source", {})
            provenance = None
            if isinstance(source, Mapping) and _source_kind(source.get("kind")) in PROVENANCE_SOURCE_KINDS:
                commit = str(source.get("commit") or "").strip().lower()
                original = str(source.get("original_path") or "").strip()
                if re.fullmatch(r"[0-9a-f]{40}", commit) and original:
                    restored = subprocess.run(
                        ["git", "show", f"{commit}:{original}"],
                        cwd=str(repo),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                    if restored.returncode == 0:
                        provenance = restored.stdout
            if provenance is None or after.stdout != provenance:
                issues.append(
                    f"LC{problem.get('id')}: 禁止覆盖或转换原始解答 {relative}；"
                    "唯一例外是恢复为 metadata 所指向 Git blob 的精确字节"
                )
    return issues


def _git_is_clean_for_sync(repo: Path) -> bool:
    worktree = subprocess.run(["git", "diff", "--quiet"], cwd=str(repo)).returncode == 0
    index = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(repo)).returncode == 0
    return worktree and index


def _has_upstream(repo: Path) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=str(repo),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _upstream_push_target(repo: Path) -> Tuple[str, str]:
    branch = git_output(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    remote = git_output(repo, ["config", "--get", f"branch.{branch}.remote"])
    merge_ref = git_output(repo, ["config", "--get", f"branch.{branch}.merge"])
    if not remote or remote == "." or not merge_ref.startswith("refs/heads/"):
        raise AutomationError("upstream 不是明确的远端分支；拒绝猜测 push 目标")
    if not git_output(repo, ["config", "--get", f"remote.{remote}.url"], check=False):
        raise AutomationError(f"upstream 远端不存在或没有 URL：{remote}")
    return remote, merge_ref


def _ensure_safe_git_state(repo: Path) -> None:
    branch = git_output(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    if not branch:
        raise AutomationError("当前是 detached HEAD；自动同步要求明确的本地分支")
    git_dir_text = git_output(repo, ["rev-parse", "--git-dir"])
    git_dir = (repo / git_dir_text).resolve() if not Path(git_dir_text).is_absolute() else Path(git_dir_text).resolve()
    operation_markers = (
        git_dir / "MERGE_HEAD",
        git_dir / "CHERRY_PICK_HEAD",
        git_dir / "REVERT_HEAD",
        git_dir / "rebase-merge",
        git_dir / "rebase-apply",
    )
    if any(path.exists() for path in operation_markers):
        raise AutomationError("检测到未完成的 merge/rebase/cherry-pick/revert；拒绝自动同步")


def _require_upstream_equal(repo: Path) -> None:
    counts = git_output(repo, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
    try:
        ahead, behind = (int(part) for part in counts.split())
    except (ValueError, TypeError) as exc:
        raise AutomationError(f"无法判断 upstream 关系：{counts}") from exc
    if ahead or behind:
        raise AutomationError(
            f"pull --ff-only 后本地与 upstream 仍不一致（ahead={ahead}, behind={behind}）；拒绝夹带或覆盖其他提交"
        )


def _stage_paths(repo: Path, paths: Iterable[str]) -> None:
    candidates = sorted({path for path in paths if path and not Path(path).is_absolute()})
    normalized: List[str] = []
    for path in candidates:
        if (repo / path).exists() or git_output(repo, ["ls-files", "--", path], check=False):
            normalized.append(path)
    for start in range(0, len(normalized), 100):
        git_output(repo, ["add", "-A", "--", *normalized[start : start + 100]])


def _default_commit_message(results: Sequence[ImportResult]) -> str:
    meaningful = [item for item in results if item.action != "unchanged"]
    unique = {item.problem_id: item for item in meaningful}
    if len(unique) == 1:
        item = next(iter(unique.values()))
        return f"solve: LC{item.problem_id} {item.title}"
    if unique:
        return f"solve: sync {len(unique)} LeetCode solutions"
    return "docs: update LeetCode index"


def _validate_commit_message(message: str) -> None:
    if not re.match(r"^(?:solve|docs|chore|fix|test): .{4,}$", message.strip()):
        raise AutomationError("commit message 必须使用 solve:/docs:/chore:/fix:/test: 前缀并说明具体内容")


def sync_repository(args: argparse.Namespace) -> int:
    repo = repository_root(args.repo)
    _ensure_safe_git_state(repo)
    if not args.dry_run and not _git_is_clean_for_sync(repo):
        raise AutomationError("仓库已有已跟踪修改或暂存内容；为避免误提交，请先处理它们")
    has_upstream = _has_upstream(repo)
    if not args.dry_run and not args.no_push and not has_upstream:
        raise AutomationError("当前分支没有 upstream；拒绝猜测 push 目标。可先设置 upstream，或使用 --no-push")
    if not args.dry_run and not args.no_pull and has_upstream:
        git_output(repo, ["pull", "--ff-only"])
    if not args.dry_run and not args.no_push:
        _require_upstream_equal(repo)

    sources = [Path(value).expanduser() for value in args.source] if args.source else [repo]
    sources = [(repo / path).resolve() if not path.is_absolute() else path.resolve() for path in sources]
    results, consumed = organize(
        repo,
        sources,
        conflict=args.conflict,
        consume=args.consume,
        dry_run=args.dry_run,
    )
    generated = generate_readmes(repo, dry_run=args.dry_run)
    issues = scan_secrets(repo)
    if issues:
        raise AutomationError("敏感信息扫描失败：\n" + "\n".join(f"  - {issue}" for issue in issues))
    changed: List[str] = list(generated) + list(consumed)
    for result in results:
        changed.extend(result.changed_paths)
    new_count = len({item.problem_id for item in results if item.action in {"new", "restored"}})
    submission_count = len([item for item in results if item.action in {"submission", "metadata"}])
    if args.dry_run:
        print(f"DRY RUN：新增 {new_count} 题，独立 submission {submission_count} 个，计划改动 {len(set(changed))} 个路径；未 pull/commit/push")
        for path in sorted(set(changed)):
            print(f"  {path}")
        return 0

    _stage_paths(repo, changed)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(repo)).returncode != 0
    if not staged:
        print("没有需要提交的变化；未 push。")
        return 0
    solution_issues = guard_canonical_solutions(repo)
    if solution_issues:
        raise AutomationError("原始题解保护检查失败：\n" + "\n".join(f"  - {issue}" for issue in solution_issues))
    staged_issues = scan_secrets(repo, staged=True)
    if staged_issues:
        raise AutomationError("暂存内容敏感信息扫描失败：\n" + "\n".join(f"  - {issue}" for issue in staged_issues))
    message = args.message or _default_commit_message(results)
    _validate_commit_message(message)
    git_output(repo, ["commit", "-m", message])
    commit = git_output(repo, ["rev-parse", "--short", "HEAD"])
    pushed = False
    if not args.no_push:
        if not _has_upstream(repo):
            raise AutomationError(f"已创建 commit {commit}，但当前分支没有 upstream；请设置 upstream 后重试 push")
        remote, merge_ref = _upstream_push_target(repo)
        git_output(repo, ["push", remote, f"HEAD:{merge_ref}"])
        pushed = True
    print(
        f"新增 {new_count} 题，独立 submission {submission_count} 个；commit {commit}；push {'成功' if pushed else '已跳过'}。"
    )
    return 0


def install_hooks(repo: Path) -> None:
    hooks = repo / ".githooks"
    if not (hooks / "pre-commit").is_file() or not (hooks / "pre-push").is_file():
        raise AutomationError(".githooks/pre-commit 或 pre-push 不存在")
    git_output(repo, ["config", "--local", "core.hooksPath", ".githooks"])
    for path in (hooks / "pre-commit", hooks / "pre-push"):
        try:
            path.chmod(path.stat().st_mode | 0o111)
        except OSError:
            pass
    print("已为当前仓库启用 .githooks（未修改全局 Git 配置）。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LeetCode 仓库安全自动化")
    parser.add_argument("--repo", help="仓库路径；默认是脚本所在仓库")
    commands = parser.add_subparsers(dest="command", required=True)

    import_parser = commands.add_parser("import", help="导入 LeetHub 目录或 JSON bundle")
    import_parser.add_argument("--source", action="append", required=True, help="导入源，可重复")
    import_parser.add_argument("--conflict", choices=("preserve", "reject"), default="preserve")
    import_parser.add_argument("--consume", action="store_true", help="成功后删除仓库内原始导入项")
    import_parser.add_argument("--dry-run", action="store_true")

    generate_parser = commands.add_parser("generate", help="由 metadata 生成 README")
    generate_parser.add_argument("--check", action="store_true", help="只检查，过期时返回非零")

    scan_parser = commands.add_parser("scan", help="扫描敏感信息")
    scan_parser.add_argument("--staged", action="store_true", help="只扫描暂存内容")
    scan_parser.add_argument("--ref", help="扫描指定 Git tree/commit")

    commands.add_parser("guard-solutions", help="拒绝暂存区覆盖 HEAD 中的原始解答")

    sync_parser = commands.add_parser("sync", help="pull --ff-only → 导入 → README → scan → commit → push")
    sync_parser.add_argument("--source", action="append", default=[], help="导入源，可重复；默认扫描仓库根")
    sync_parser.add_argument("--conflict", choices=("preserve", "reject"), default="preserve")
    sync_parser.add_argument("--consume", action="store_true", help="成功导入后移除仓库内 LeetHub 原目录/bundle")
    sync_parser.add_argument("--dry-run", action="store_true", help="只预览；不 pull、写文件、commit 或 push")
    sync_parser.add_argument("--no-push", action="store_true", help="commit 后不 push")
    sync_parser.add_argument("--no-pull", action="store_true", help="不执行 pull --ff-only")
    sync_parser.add_argument("--message", help="自定义规范化 commit message")

    commands.add_parser("install-hooks", help="仅为当前仓库启用版本化 hooks")
    validate_parser = commands.add_parser("validate", help="验证 metadata 和 solution 路径")
    validate_parser.add_argument("--no-files", action="store_true", help="不检查 solution 文件是否存在")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        repo = repository_root(args.repo)
        if args.command == "import":
            sources = [Path(value).expanduser() for value in args.source]
            sources = [(repo / path).resolve() if not path.is_absolute() else path.resolve() for path in sources]
            results, consumed = organize(
                repo, sources, conflict=args.conflict, consume=args.consume, dry_run=args.dry_run
            )
            for result in results:
                print(f"LC{result.problem_id}: {result.action} ({result.solution_path})")
            if consumed:
                print(f"已整理导入源：{len(consumed)}")
            if not results:
                print("没有发现可导入的 LeetHub 目录或 JSON bundle。")
            return 0
        if args.command == "generate":
            changed = generate_readmes(repo, check=args.check)
            if args.check and changed:
                print("README 需要重新生成：", file=sys.stderr)
                for path in changed:
                    print(f"  {path}", file=sys.stderr)
                return 1
            print(f"README 已同步；更新 {len(changed)} 个文件。")
            return 0
        if args.command == "scan":
            issues = scan_secrets(repo, staged=args.staged, ref=args.ref)
            if issues:
                for issue in issues:
                    print(issue, file=sys.stderr)
                return 1
            print("敏感信息扫描通过。")
            return 0
        if args.command == "guard-solutions":
            issues = guard_canonical_solutions(repo)
            if issues:
                for issue in issues:
                    print(issue, file=sys.stderr)
                return 1
            print("原始解答保护检查通过。")
            return 0
        if args.command == "sync":
            return sync_repository(args)
        if args.command == "install-hooks":
            install_hooks(repo)
            return 0
        if args.command == "validate":
            data = load_database(repo)
            validate_database(data, require_files=not args.no_files, repo=repo)
            print(f"metadata 验证通过：{len(data['problems'])} 题。")
            return 0
        parser.error("未知命令")
    except AutomationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
