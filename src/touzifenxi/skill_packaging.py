from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

REQUIRED_SKILL_FILES = (
    "SKILL.md",
    "agents/agent.yaml",
)
REQUIRED_SKILL_DIRS = (
    "agents",
    "config",
    "prompts",
    "scripts",
    "src",
    "output",
)
SHARED_BUNDLE_DIRS = (
    "src/touzifenxi",
)
EXCLUDED_SUFFIXES = (".pyc",)
EXCLUDED_PARTS = {"__pycache__", ".DS_Store"}
EXCLUDED_FILENAMES = {"runtime.local.json"}
OUTPUT_KEEP_FILENAMES = {".gitkeep", "README.md"}


@dataclass(frozen=True)
class SkillPackageResult:
    skill_dir: Path
    output_path: Path
    archived_files: list[str]


def resolve_skill_directory(project_root: Path, skill_name: str) -> Path:
    """Resolve a skill name to its directory under the project skill root."""

    return (project_root / "skills" / skill_name).resolve()


def skill_archive_name(skill_dir: Path) -> str:
    """Return the canonical archive name for a packaged skill."""

    return f"{skill_dir.name}-skill.zip"


def validate_skill_directory(skill_dir: Path) -> list[str]:
    """Return human-readable validation issues for a skill directory."""

    issues: list[str] = []
    if not skill_dir.exists():
        return [f"Skill 目录不存在: {skill_dir}"]
    if not skill_dir.is_dir():
        return [f"Skill 路径不是目录: {skill_dir}"]

    for relative_path in REQUIRED_SKILL_FILES:
        if not (skill_dir / relative_path).exists():
            issues.append(f"缺少必需文件: {relative_path}")
    for relative_path in REQUIRED_SKILL_DIRS:
        if not (skill_dir / relative_path).exists():
            issues.append(f"缺少必需目录: {relative_path}")

    skill_doc = skill_dir / "SKILL.md"
    if skill_doc.exists():
        content = skill_doc.read_text(encoding="utf-8")
        if not contains_chinese_body(content):
            issues.append("SKILL.md 正文必须使用中文。")
        description = extract_frontmatter_value(content, "description")
        if not description:
            issues.append("SKILL.md frontmatter 缺少 description。")
        elif not contains_chinese(description):
            issues.append("SKILL.md frontmatter 的 description 必须使用中文。")
        for marker in ("适用场景", "默认行为", "输出产物", "目录说明", "分发约束"):
            if marker not in content:
                issues.append(f"SKILL.md 缺少规范章节: {marker}")

    return dedupe_preserve_order(issues)


def package_skill_directory(skill_dir: Path, output_path: Path) -> SkillPackageResult:
    """Package a validated skill directory into a zip archive."""

    issues = validate_skill_directory(skill_dir)
    if issues:
        joined = "\n".join(f"- {issue}" for issue in issues)
        raise ValueError(f"Skill 校验失败，无法打包:\n{joined}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    archived_files: list[str] = []
    project_root = skill_dir.parents[1]
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(skill_dir.rglob("*")):
            if path.is_dir():
                continue
            if not should_archive_skill_path(skill_dir, path):
                continue
            relative_path = path.relative_to(skill_dir.parent)
            archive.write(path, arcname=str(relative_path))
            archived_files.append(str(relative_path))
        for relative_dir in SHARED_BUNDLE_DIRS:
            bundle_root = (project_root / relative_dir).resolve()
            if not bundle_root.exists() or not bundle_root.is_dir():
                continue
            for path in sorted(bundle_root.rglob("*")):
                if path.is_dir():
                    continue
                if not should_archive_shared_path(path):
                    continue
                relative_path = path.relative_to(project_root)
                archive.write(path, arcname=str(relative_path))
                archived_files.append(str(relative_path))
    return SkillPackageResult(skill_dir=skill_dir, output_path=output_path, archived_files=archived_files)


def should_archive_skill_path(skill_dir: Path, path: Path) -> bool:
    """Return whether a skill file should be included in the packaged archive."""

    if any(part in EXCLUDED_PARTS for part in path.parts):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    if path.name in EXCLUDED_FILENAMES:
        return False

    relative_path = path.relative_to(skill_dir)
    if relative_path.parts and relative_path.parts[0] == "output":
        return path.name in OUTPUT_KEEP_FILENAMES

    return True


def should_archive_shared_path(path: Path) -> bool:
    """Return whether a shared dependency file should be bundled."""

    if any(part in EXCLUDED_PARTS for part in path.parts):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    return True


def contains_chinese_body(content: str) -> bool:
    """Check whether the markdown body contains Chinese characters."""

    body = content
    if content.startswith("---"):
        _, _, remainder = content.partition("\n---")
        body = remainder
    return any("\u4e00" <= char <= "\u9fff" for char in body)


def contains_chinese(content: str) -> bool:
    """Check whether a text contains at least one Chinese character."""

    return any("\u4e00" <= char <= "\u9fff" for char in content)


def extract_frontmatter_value(content: str, key: str) -> str:
    """Extract a plain YAML frontmatter value without introducing a YAML dependency."""

    if not content.startswith("---"):
        return ""
    _, _, remainder = content.partition("\n---")
    frontmatter = content[: len(content) - len(remainder)]
    prefix = f"{key}:"
    for line in frontmatter.splitlines():
        if line.startswith(prefix):
            return line.partition(":")[2].strip()
    return ""


def dedupe_preserve_order(items: list[str]) -> list[str]:
    """Remove duplicates while preserving the original order."""

    seen: set[str] = set()
    results: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        results.append(item)
    return results
