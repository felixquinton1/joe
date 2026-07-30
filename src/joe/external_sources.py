from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse


KNOWN_SOURCES = {
    "crewly": "https://crewlyai.com/en",
    "zag": "https://zag.niclaslindstedt.se/",
    "maestro": "https://github.com/RunMaestro/Maestro",
    "shep": "https://shep.bot/",
    "agetor": "https://www.agetor.dev/",
    "callcode": "https://callcode.dev/",
    "the cog": "https://www.thecog.dev/",
    "compozy": "https://www.compozy.com/",
    "spooling": "https://spooling.ai/",
}
_URL = re.compile(r"https?://[^\s<>\]\)]+", re.IGNORECASE)
_TAG = re.compile(r"(?is)<(script|style|noscript).*?</\1>|<[^>]+>")


@dataclass(frozen=True)
class SourceRecord:
    label: str
    url: str
    status: str
    content: str = ""


def _fetch(label: str, url: str) -> SourceRecord:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return SourceRecord(label, url, "refused: non-HTTPS public source")
    try:
        candidates = [url]
        if parsed.netloc.lower() == "github.com":
            bits = parsed.path.strip("/").split("/")
            if len(bits) >= 2:
                owner, repo = bits[0], bits[1].removesuffix(".git")
                candidates = [
                    f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md",
                    f"https://raw.githubusercontent.com/{owner}/{repo}/master/README.md",
                ]
        raw = b""
        for candidate in candidates:
            try:
                with urllib.request.urlopen(candidate, timeout=6) as response:
                    raw = response.read(80_000)
                if raw:
                    break
            except (OSError, urllib.error.URLError):
                continue
        text = _TAG.sub(" ", raw.decode("utf-8", "replace"))
        text = html.unescape(re.sub(r"\s+", " ", text)).strip()[:24_000]
        return SourceRecord(label, url, "verified", text) if text else SourceRecord(label, url, "refused: empty response")
    except (OSError, urllib.error.URLError, UnicodeError) as exc:
        return SourceRecord(label, url, f"refused: {type(exc).__name__}")


def collect_sources(request: str) -> tuple[str, tuple[SourceRecord, ...]]:
    """Fetch bounded public references and return context plus an evidence ledger."""
    lower = request.lower()
    targets: list[tuple[str, str]] = []
    for url in dict.fromkeys(_URL.findall(request)):
        clean = url.rstrip(".,;")
        parsed = urlparse(clean)
        bits = parsed.path.strip("/").split("/")
        label = (
            f"{bits[0]}/{bits[1]}"
            if parsed.netloc.lower() == "github.com" and len(bits) >= 2
            else parsed.netloc
        )
        targets.append((label, clean))
    for label, url in KNOWN_SOURCES.items():
        if re.search(rf"(?<![\w-]){re.escape(label)}(?![\w-])", lower) and url not in {u for _, u in targets}:
            targets.append((label, url))
    if not targets:
        return "", ()
    records = tuple(_fetch(label, url) for label, url in targets[:12])
    ledger = ["## External source ledger"]
    chunks: list[str] = []
    for record in records:
        ledger.append(f"- [{record.status.upper()}] {record.label}: {record.url}")
        if record.status == "verified":
            title = "External public reference" if "/" in record.label else "Source"
            chunks.append(f"## {title}: {record.label}\n{record.content}")
    result = "\n".join(ledger)
    if chunks:
        result += "\n\n" + "\n\n".join(chunks)
    return result, records
