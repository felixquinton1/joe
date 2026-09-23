from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree


WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD = f"{{{WORD_NAMESPACE}}}"
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 100_000


def extract_document_text(
    path: Path,
    content_type: str = "",
    *,
    max_chars: int = MAX_TEXT_CHARS,
) -> str | None:
    """Extract bounded text from a supported local document without a shell."""
    if (
        path.suffix.lower() != ".docx"
        and content_type
        != "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return None
    return _extract_docx(path, max_chars=max_chars)


def _extract_docx(path: Path, *, max_chars: int) -> str:
    parts: list[str] = []
    total_xml_bytes = 0
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if name == "word/document.xml"
                or (
                    name.startswith("word/header")
                    or name.startswith("word/footer")
                    or name in {"word/footnotes.xml", "word/endnotes.xml"}
                )
                and name.endswith(".xml")
            ]
            if "word/document.xml" not in names:
                return ""
            names.sort(key=lambda name: (name != "word/document.xml", name))
            for name in names:
                info = archive.getinfo(name)
                total_xml_bytes += info.file_size
                if total_xml_bytes > MAX_XML_BYTES:
                    break
                xml = archive.read(info)
                declarations = xml.upper()
                if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
                    return ""
                # WordprocessingML does not need DTDs or custom entities. They
                # are rejected above, so the standard parser cannot resolve an
                # external resource or expand an attacker-controlled entity.
                root = ElementTree.fromstring(xml)  # nosec B314
                for paragraph in root.iter(f"{WORD}p"):
                    text: list[str] = []
                    for node in paragraph.iter():
                        if node.tag == f"{WORD}t" and node.text:
                            text.append(node.text)
                        elif node.tag == f"{WORD}tab":
                            text.append("\t")
                        elif node.tag in {f"{WORD}br", f"{WORD}cr"}:
                            text.append("\n")
                    value = "".join(text).strip()
                    if value:
                        parts.append(value)
                    if sum(len(item) + 1 for item in parts) >= max_chars:
                        break
                if sum(len(item) + 1 for item in parts) >= max_chars:
                    break
    except (
        OSError,
        KeyError,
        RuntimeError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
    ):
        return ""
    return "\n".join(parts)[:max_chars]
