from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .text_encoding import read_user_text


_CREATE_REQUEST = re.compile(
    r"\b(?:cr(?:ée|éer|éé|ee|eer)|ajoute(?:r)?)\s+"
    r"(?:moi\s+)?(?:un\s+)?skill\b(?P<tail>.*)",
    re.IGNORECASE,
)


def parse_skill_request(request: str) -> dict[str, str] | None:
    """Recognize explicit skill creation requests without involving a provider."""
    match = _CREATE_REQUEST.search(request.strip())
    if not match:
        return None
    tail = match.group("tail").strip(" .")
    global_scope = bool(
        re.search(r"\b(?:commun|global|tous les projets)\b", tail, re.IGNORECASE)
    )
    tail = re.sub(
        r"^(?:commun|global|de projet|pour ce projet)\b",
        "",
        tail,
        flags=re.IGNORECASE,
    ).strip()
    tail = re.sub(
        r"^(?:appel[eé]|nomm[eé])\s+",
        "",
        tail,
        flags=re.IGNORECASE,
    )
    quoted = re.match(r"""["“'«]\s*(?P<name>[^"”'»]+?)\s*["”'»](?P<rest>.*)""", tail)
    if quoted:
        name = quoted.group("name").strip()
        rest = quoted.group("rest").strip()
    else:
        named = re.match(r"(?P<name>[A-Za-z0-9_-]+)(?P<rest>.*)", tail)
        if not named:
            return {"name": "", "instructions": "", "scope": "global" if global_scope else "project"}
        name = named.group("name").strip()
        rest = named.group("rest").strip()
    instructions = ""
    instruction_match = re.search(
        r"(?:^:|^qui\s+|^avec\s+(?:les\s+)?instructions?\s*:?)\s*(?P<body>.+)",
        rest,
        re.IGNORECASE,
    )
    if instruction_match:
        instructions = instruction_match.group("body").strip()
    elif "test" in name.lower():
        instructions = (
            "Lorsque l’utilisateur demande de vérifier ce skill, répondre avec "
            "le marqueur exact `SKILL_TEST_ACTIF`."
        )
    return {
        "name": name,
        "instructions": instructions,
        "scope": "global" if global_scope else "project",
    }


def global_skills_root() -> Path:
    """Directory holding skills shared by every project of this Joe install."""
    return Path.home() / ".joe" / "global-skills"


def list_skills(project: Path, configured_paths: list[str] | None = None) -> list[dict[str, Any]]:
    roots = [_project_skills_root(project), project / "skills"]
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


def create_skill(
    project: Path | None,
    name: str,
    instructions: str,
    *,
    global_scope: bool = False,
) -> dict[str, Any]:
    """Create a provider-neutral skill directly from user instructions."""
    skill_name = _skill_name(name)
    content = instructions.strip()
    if not content:
        raise ValueError("Les instructions du skill sont obligatoires.")
    if len(content) > 20_000:
        raise ValueError("Les instructions du skill sont trop longues.")
    root = global_skills_root() if global_scope else _project_skills_root(project)
    destination = (root / skill_name / "SKILL.md").resolve()
    resolved_root = root.resolve()
    if resolved_root not in destination.parents:
        raise ValueError("Destination de skill invalide.")
    if destination.exists():
        raise FileExistsError(f"Le skill « {skill_name} » existe déjà.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(f"# {name.strip()}\n\n{content}\n", encoding="utf-8")
    return {
        "name": skill_name,
        "path": str(destination),
        "scope": "global" if global_scope else "project",
        "active": True,
    }


def promote_skill(project: Path, name: str) -> dict[str, Any]:
    """Copy a project skill into the global directory shared by all projects."""
    skill_name = _skill_name(name)
    source = (_project_skills_root(project) / skill_name / "SKILL.md").resolve()
    root = _project_skills_root(project).resolve()
    if root not in source.parents or not source.is_file():
        raise FileNotFoundError(f"Skill de projet introuvable : {skill_name}")
    destination = (global_skills_root() / skill_name / "SKILL.md").resolve()
    global_root = global_skills_root().resolve()
    if global_root not in destination.parents:
        raise ValueError("Destination de skill commun invalide.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = read_user_text(source)
    content = re.sub(
        r"^<!-- Joe shared skill: imported from .*? -->\n\n",
        "",
        content,
        count=1,
    )
    destination.write_text(content.rstrip(), encoding="utf-8")
    return {
        "name": skill_name,
        "path": str(destination),
        "scope": "global",
        "active": True,
    }


def _skill_directory(
    project: Path | None, name: str, *, global_scope: bool
) -> tuple[Path, Path]:
    """Resolve a skill directory, refusing anything outside its root."""
    skill_name = _skill_name(name)
    root = (
        global_skills_root() if global_scope else _project_skills_root(project)
    ).resolve()
    directory = (root / skill_name).resolve()
    # Le nom est déjà normalisé, mais un lien symbolique pourrait sortir de la
    # racine : la vérification porte donc sur le chemin résolu.
    if root not in directory.parents or not (directory / "SKILL.md").is_file():
        raise FileNotFoundError(f"Skill introuvable : {skill_name}")
    return directory, root


def read_skill(
    project: Path | None, name: str, *, global_scope: bool = False
) -> dict[str, Any]:
    """Return a skill's full text.

    La liste n'annonçait qu'une taille en octets. Or tout skill listé entre
    dans le contexte partagé : sans son contenu, on ne peut ni vérifier ce
    qu'il demande aux fournisseurs, ni décider de le retirer.
    """
    directory, _ = _skill_directory(project, name, global_scope=global_scope)
    document = directory / "SKILL.md"
    return {
        "name": directory.name,
        "path": str(document),
        "scope": "global" if global_scope else "project",
        "size": document.stat().st_size,
        "content": read_user_text(document),
    }


def delete_skill(
    project: Path | None, name: str, *, global_scope: bool = False
) -> dict[str, Any]:
    """Remove a skill and everything it ships.

    Un skill listé est toujours actif : sans suppression, rien ne permettait
    de le sortir du contexte partagé, qui ne pouvait donc que grossir.
    """
    directory, _ = _skill_directory(project, name, global_scope=global_scope)
    shutil.rmtree(directory)
    return {
        "name": directory.name,
        "scope": "global" if global_scope else "project",
        "deleted": True,
    }


def import_skill(
    project: Path | None,
    source: Path,
    *,
    name: str | None = None,
    source_provider: str = "unknown",
    global_scope: bool = False,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    source_file = source / "SKILL.md" if source.is_dir() else source
    if not source_file.is_file():
        raise FileNotFoundError(f"Skill introuvable : {source_file}")
    skill_name = _skill_name(name or source_file.parent.name)
    content = read_user_text(source_file).strip()
    root = global_skills_root() if global_scope else _project_skills_root(project)
    destination = (root / skill_name / "SKILL.md").resolve()
    root = root.resolve()
    if root not in destination.parents:
        raise ValueError("Destination de skill invalide.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "<!-- Joe shared skill: imported from "
        f"{source_provider}; provider-specific commands must be translated. -->\n\n"
    )
    destination.write_text(header + content + "\n", encoding="utf-8")
    return {
        "name": skill_name,
        "path": str(destination),
        "source_provider": source_provider,
        "converted": True,
        "scope": "global" if global_scope else "project",
    }


def _project_skills_root(project: Path | None) -> Path:
    if project is None:
        raise ValueError("Un projet est requis pour créer ce skill.")
    return project / ".agentflow" / "skills"


def _skill_name(name: str) -> str:
    skill_name = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    if not skill_name:
        raise ValueError("Le skill doit avoir un nom non vide.")
    return skill_name
