from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def global_skills_root() -> Path:
    """Directory holding skills shared by every project of this Joe install."""
    return Path.home() / ".joe" / "global-skills"


def list_skills(project: Path, configured_paths: list[str] | None = None) -> list[dict[str, Any]]:
    roots = [project / ".agentflow" / "skills", project / "skills"]
    roots.extend(Path(path).expanduser() for path in (configured_paths or []))
    found: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        for path in sorted(root.rglob("SKILL.md")):
            if not path.is_file():
                continue
            found.append({
                "name": path.parent.name,
                "path": str(path),
                "scope": "project" if path.is_relative_to(project.resolve()) else "configured",
                # Every listed skill is loaded into the shared context, so it is
                # active for all providers of the project.
                "active": True,
                "size": path.stat().st_size,
            })
    return found


def list_global_skills() -> list[dict[str, Any]]:
    root = global_skills_root().resolve()
    found: list[dict[str, Any]] = []
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("SKILL.md")):
        if not path.is_file():
            continue
        found.append({
            "name": path.parent.name,
            "path": str(path),
            "scope": "global",
            "active": True,
            "size": path.stat().st_size,
        })
    return found


def promote_skill(project: Path, name: str) -> dict[str, Any]:
    """Copy a project skill into the global directory shared by all projects."""
    skill_name = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    if not skill_name:
        raise ValueError("Le skill doit avoir un nom non vide.")
    source = (project / ".agentflow" / "skills" / skill_name / "SKILL.md").resolve()
    root = (project / ".agentflow" / "skills").resolve()
    if root not in source.parents or not source.is_file():
        raise FileNotFoundError(f"Skill de projet introuvable : {skill_name}")
    destination = (global_skills_root() / skill_name / "SKILL.md").resolve()
    global_root = global_skills_root().resolve()
    if global_root not in destination.parents:
        raise ValueError("Destination de skill commun invalide.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source.read_text(errors="replace"))
    return {
        "name": skill_name,
        "path": str(destination),
        "scope": "global",
        "active": True,
    }


def import_skill(
    project: Path,
    source: Path,
    *,
    name: str | None = None,
    source_provider: str = "unknown",
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    source_file = source / "SKILL.md" if source.is_dir() else source
    if not source_file.is_file():
        raise FileNotFoundError(f"Skill introuvable : {source_file}")
    skill_name = re.sub(r"[^a-z0-9-]+", "-", (name or source_file.parent.name).lower()).strip("-")
    if not skill_name:
        raise ValueError("Le skill doit avoir un nom non vide.")
    content = source_file.read_text(errors="replace").strip()
    destination = (project / ".agentflow" / "skills" / skill_name / "SKILL.md").resolve()
    root = (project / ".agentflow" / "skills").resolve()
    if root not in destination.parents:
        raise ValueError("Destination de skill invalide.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "<!-- Joe shared skill: imported from "
        f"{source_provider}; provider-specific commands must be translated. -->\n\n"
    )
    destination.write_text(header + content + "\n")
    return {
        "name": skill_name,
        "path": str(destination),
        "source_provider": source_provider,
        "converted": True,
    }
