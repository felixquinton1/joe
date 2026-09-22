"""L'interface anglaise ne doit plus laisser passer de français.

`translate()` retombe sur le français quand une clé manque en anglais : une
traduction oubliée ne casse rien, elle s'affiche simplement dans la mauvaise
langue. Et une étiquette écrite en dur dans le code n'a même pas de clé sur
laquelle retomber. Ces deux oublis se vérifient mécaniquement.
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "src" / "joe" / "web_assets"


def _block(source: str, language: str) -> str:
    """Return the body of the `fr:`/`en:` object literal, braces matched."""
    start = source.index(f"{language}: {{") + len(f"{language}: ")
    depth = 0
    quote = ""
    index = start
    while index < len(source):
        char = source[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
        index += 1
    raise AssertionError(f"bloc {language} non terminé dans i18n.js")


def _keys(block: str) -> set[str]:
    """Collect the keys declared at the top level of one language block."""
    keys: set[str] = set()
    depth = 0
    quote = ""
    index = 0
    while index < len(block):
        char = block[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif depth == 0:
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", block[index:])
            if match and (index == 0 or not block[index - 1].isalnum()):
                keys.add(match.group(1))
                index += match.end()
                continue
        index += 1
    return keys


def test_every_french_string_has_an_english_translation():
    source = (ASSETS / "i18n.js").read_text(encoding="utf-8")

    french = _keys(_block(source, "fr"))
    english = _keys(_block(source, "en"))

    assert french, "le bloc français doit exposer des clés"
    assert not french - english, (
        "clés sans traduction anglaise : " + ", ".join(sorted(french - english))
    )
    assert not english - french, (
        "clés anglaises sans équivalent français : "
        + ", ".join(sorted(english - french))
    )


def test_no_message_label_is_written_in_the_code():
    """« Toi » restait affiché au-dessus des messages en interface anglaise.

    Une étiquette de bulle se donne par sa clé (`labelKey`), jamais en clair :
    sinon elle échappe à la traduction et au changement de langue.
    """
    offenders = []
    for path in sorted(ASSETS.glob("*.js")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if re.search(r"addMessage\(\s*[\"'`]", line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, "étiquettes écrites en dur : " + ", ".join(offenders)


def test_no_visible_label_is_written_in_french_in_the_code():
    """« Autre identifiant… » s'affichait en anglais aussi.

    Une chaîne passée en `label:` arrive telle quelle dans un menu : elle doit
    venir du dictionnaire, comme le reste.
    """
    accented = re.compile(r"""label:\s*["'`][^"'`]*[éèêàùôûçœÉÈÀ]""")
    offenders = []
    for path in sorted(ASSETS.glob("*.js")):
        if path.name == "i18n.js":
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if accented.search(line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, "libellés écrits en dur : " + ", ".join(offenders)
