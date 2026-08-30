"""Regression checks for the files intended to be shared with interviewers.

The check deliberately scans the publishable source and documentation tree instead
of relying on Git state: a local checkout can be dirty or lack a usable Git index.
Tests and historical planning notes are excluded because they contain safe test
fixtures and implementation discussion, not release artifacts.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXCLUDED_PARTS = {
    ".git",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
NON_PUBLIC_PREFIXES = (
    "backend/tests/",
    "docs/superpowers/",
)
PUBLISHABLE_SUFFIXES = {
    ".css",
    ".csv",
    ".json",
    ".jsonl",
    ".md",
    ".ps1",
    ".py",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yml",
    ".yaml",
}
ACTUAL_TUNNEL_URL = re.compile(
    r"https://(?!\*\.)(?!example\.)[a-z0-9-]+\.trycloudflare\.com(?:/[^\s)]*)?",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|openai[_-]?api[_-]?key|secret[_-]?key|model[_-]?key)\b\s*[:=]\s*"
    r"[\"']?(?![\"']?(?:$|none\b|null\b|changeme\b|example\b|your[_-]?key\b))[A-Za-z0-9_\-]{12,}",
    re.IGNORECASE | re.MULTILINE,
)
RAW_MODEL_FIELD = re.compile(
    r"[\"'](?:raw_)?(?:prompt|response|model_response|model_prompt)[\"']\s*:",
    re.IGNORECASE,
)


def _is_ignored_local_file(path: Path, root: Path) -> bool:
    """Allow local-only artifacts only when a checked-in ignore rule covers them."""

    ignore_file = root / ".gitignore"
    if not ignore_file.exists():
        return False
    ignored_patterns = {
        line.strip()
        for line in ignore_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    relative = path.relative_to(root).as_posix()
    return any(
        fnmatch(relative, pattern.rstrip("/"))
        or fnmatch(path.name, pattern.rstrip("/"))
        for pattern in ignored_patterns
    )


def find_public_artifact_violations(root: Path) -> list[str]:
    """Return deterministic, relative-path findings for public artifact leakage."""

    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or EXCLUDED_PARTS.intersection(path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith(NON_PUBLIC_PREFIXES):
            continue
        if path.name == ".env" and not _is_ignored_local_file(path, root):
            findings.append(f"tracked environment file: {relative}")
            continue
        is_database = path.suffix.lower() in {".duckdb", ".db", ".sqlite", ".sqlite3"}
        if is_database and not _is_ignored_local_file(path, root):
            findings.append(f"database artifact: {relative}")
            continue
        if path.suffix.lower() not in PUBLISHABLE_SUFFIXES:
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        if SECRET_ASSIGNMENT.search(text):
            findings.append(f"possible key material: {relative}")
        if ACTUAL_TUNNEL_URL.search(text):
            findings.append(f"temporary tunnel URL: {relative}")
        if RAW_MODEL_FIELD.search(text):
            findings.append(f"raw model prompt/response field: {relative}")
    return findings


def test_public_repository_artifacts_are_safe_to_share() -> None:
    assert find_public_artifact_violations(REPOSITORY_ROOT) == []


def test_scanner_rejects_secrets_databases_tunnels_and_raw_model_artifacts(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text(
        'OPENAI_API_KEY = "sk-not-a-real-key-but-long-enough"\n'
        'tunnel = "https://temporary-demo.trycloudflare.com"\n'
        '{"raw_response": "private model prose"}\n',
        encoding="utf-8",
    )
    (tmp_path / "data.duckdb").write_bytes(b"DUCK")
    (tmp_path / ".env").write_text("LOCAL_ONLY=value", encoding="utf-8")

    findings = find_public_artifact_violations(tmp_path)

    assert set(findings) == {
        "tracked environment file: .env",
        "possible key material: README.md",
        "raw model prompt/response field: README.md",
        "temporary tunnel URL: README.md",
        "database artifact: data.duckdb",
    }


def test_scanner_allows_an_ignored_local_env_but_not_a_public_one(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text("LOCAL_ONLY=value", encoding="utf-8")

    assert find_public_artifact_violations(tmp_path) == []


def test_handoff_documents_cover_cold_start_route_boundaries_and_narrative() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    case_study = (REPOSITORY_ROOT / "docs" / "CASE_STUDY.md").read_text(
        encoding="utf-8"
    )
    interview_story = (REPOSITORY_ROOT / "docs" / "INTERVIEW_STORY.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "docker compose up --build -d",
        "/embodied",
        "real_robot_data=false",
        "live_model=false",
        "不控制机器人",
        "MIT License",
    ):
        assert required in readme

    for required in (
        "特征工程",
        "Agent 边界",
        "业务 KPI",
        "上线门禁",
        "ROI",
        "不构成生产 ROI 结论",
    ):
        assert required in case_study

    for required in ("90 秒", "5 分钟", "深挖", "Unitree", "合成"):
        assert required in interview_story
