from __future__ import annotations

from pathlib import Path


def read_utf8_compatible(path: Path) -> str:
    """Read current UTF-8 state or migrate legacy Windows CP-1252 state."""
    payload = path.read_bytes()
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("cp1252")


def read_user_text(path: Path) -> str:
    """Read a user-authored text file, whatever editor saved it.

    UTF-8 est la norme aujourd'hui, mais un ancien éditeur Windows a pu
    enregistrer en cp1252, et le Bloc-notes ajoutait un BOM. Aucun de ces cas
    ne doit faire échouer Joe, ni parvenir aux modèles déformé : un skill écrit
    en français y perdait tous ses accents.
    """
    payload = path.read_bytes()
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return payload.decode("cp1252", errors="replace")
