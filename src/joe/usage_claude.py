from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


def claude_status(
    path: Path | None,
    *,
    unavailable: Callable[[str, str], dict[str, Any]],
    now_timestamp: float,
    stale_usage_seconds: float,
) -> dict[str, Any]:
    cache_path = path or Path.home() / ".claude.json"
    try:
        payload = json.loads(cache_path.read_text())
        cached = payload["cachedUsageUtilization"]
        fetched_at = float(cached["fetchedAtMs"]) / 1000
        utilization = cached["utilization"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return unavailable("claude", "Quota non encore mis en cache par Claude")

    windows = []
    for name, key, duration in (
        ("5 heures", "five_hour", 300),
        ("7 jours", "seven_day", 10080),
        ("Sonnet · 7 jours", "seven_day_sonnet", 10080),
        ("Opus · 7 jours", "seven_day_opus", 10080),
    ):
        item = utilization.get(key)
        if not isinstance(item, dict) or item.get("utilization") is None:
            continue
        resets_at = iso_timestamp(item.get("resets_at"))
        if resets_at is not None and resets_at <= now_timestamp:
            continue
        used = max(0.0, min(100.0, float(item["utilization"])))
        windows.append(
            {
                "name": name,
                "used_percent": round(used, 1),
                "remaining_percent": round(100 - used, 1),
                "resets_at": resets_at,
                "duration_minutes": duration,
            }
        )
    age_seconds = now_timestamp - fetched_at
    if age_seconds > stale_usage_seconds:
        return unavailable(
            "claude",
            "Données trop anciennes · ouvre /usage dans Claude pour les actualiser",
        )
    stale = age_seconds > 15 * 60
    return {
        "provider": "claude",
        "available": bool(windows),
        "stale": stale,
        "plan": None,
        "windows": windows,
        "message": (
            "Dernière mesure connue · ouvre /usage dans Claude pour l’actualiser"
            if windows and stale
            else None if windows else "Aucune limite communiquée par Claude"
        ),
    }


def refresh_claude_status(
    timeout: int,
    *,
    refresh_via_tmux: Callable[[int], dict[str, Any] | None],
    parse_screen: Callable[..., dict[str, Any] | None],
    fallback_reader: Callable[[], dict[str, Any]],
    subprocess_module=subprocess,
    pexpect_module=None,
    os_module=os,
    time_module=time,
) -> dict[str, Any]:
    tmux_status = refresh_via_tmux(timeout)
    if tmux_status:
        return tmux_status
    output = ""
    child = None
    if pexpect_module is None:
        try:
            import pexpect as pexpect_module
        except ImportError:
            fallback = fallback_reader()
            fallback["message"] = (
                "Actualisation interactive Claude indisponible sur cette plateforme"
            )
            fallback["stale"] = True
            return fallback
    try:
        env = os_module.environ.copy()
        env["DISABLE_AUTOUPDATER"] = "1"
        child = pexpect_module.spawn(
            "claude",
            ["--ax-screen-reader", "--permission-mode", "plan"],
            env=env,
            encoding="utf-8",
            timeout=timeout,
        )
        child.expect("/effort")
        time_module.sleep(0.8)
        child.send("/usage\r")
        try:
            child.expect("Current session", timeout=6)
        except pexpect_module.TIMEOUT:
            child.send("/usage\r")
            child.expect("Current session", timeout=max(6, timeout - 6))
        output = child.before + child.after
        deadline = time_module.monotonic() + 5
        while time_module.monotonic() < deadline:
            try:
                output += child.read_nonblocking(8192, 0.5)
            except pexpect_module.TIMEOUT:
                continue
            except pexpect_module.EOF:
                break
    except (OSError, pexpect_module.ExceptionPexpect):
        output = ""
    finally:
        if child is not None:
            if child.isalive():
                child.sendcontrol("c")
            child.close(force=True)
    live = parse_screen(output)
    if live:
        return live
    fallback = fallback_reader()
    fallback["message"] = (
        "Actualisation /usage indisponible · dernière mesure locale"
        if fallback.get("available")
        else "Actualisation /usage Claude indisponible"
    )
    fallback["stale"] = True
    return fallback


def refresh_claude_via_tmux(
    timeout: int,
    *,
    parse_screen: Callable[[str], dict[str, Any] | None],
    subprocess_module=subprocess,
    shutil_module=shutil,
    time_module=time,
    uuid_module=uuid,
) -> dict[str, Any] | None:
    if not shutil_module.which("tmux") or not shutil_module.which("claude"):
        return None
    session = f"joe-claude-usage-{uuid_module.uuid4().hex[:10]}"
    try:
        started = subprocess_module.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-x",
                "120",
                "-y",
                "50",
                "-s",
                session,
                "claude",
                "--ax-screen-reader",
                "--permission-mode",
                "plan",
            ],
            stdout=subprocess_module.DEVNULL,
            stderr=subprocess_module.DEVNULL,
            check=False,
        )
        if started.returncode:
            return None
        deadline = time_module.monotonic() + timeout
        sent = False
        while time_module.monotonic() < deadline:
            screen = subprocess_module.run(
                ["tmux", "capture-pane", "-p", "-J", "-t", session],
                text=True,
                capture_output=True,
                check=False,
            ).stdout
            if not sent and "/effort" in screen:
                subprocess_module.run(
                    ["tmux", "send-keys", "-t", session, "/usage", "Enter"],
                    stdout=subprocess_module.DEVNULL,
                    stderr=subprocess_module.DEVNULL,
                    check=False,
                )
                sent = True
            if sent and "Current week (all models)" in screen and "used" in screen:
                parsed = parse_screen(screen)
                if parsed:
                    return parsed
            time_module.sleep(0.4)
    finally:
        subprocess_module.run(
            ["tmux", "kill-session", "-t", session],
            stdout=subprocess_module.DEVNULL,
            stderr=subprocess_module.DEVNULL,
            check=False,
        )
    return None


def parse_claude_usage_screen(
    output: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    text = strip_terminal_codes(output)
    current = now or datetime.now().astimezone()
    patterns = (
        (
            "5 heures",
            r"Current session\s+(\d+(?:\.\d+)?)%.*?used\s+Resets ([^\r\n]+)",
            300,
        ),
        (
            "7 jours",
            r"Current week \(all models\)\s+(\d+(?:\.\d+)?)%.*?used\s+Resets ([^\r\n]+)",
            10080,
        ),
    )
    windows = []
    for name, pattern, duration in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            continue
        used = max(0.0, min(100.0, float(match.group(1))))
        windows.append(
            {
                "name": name,
                "used_percent": round(used, 1),
                "remaining_percent": round(100 - used, 1),
                "resets_at": claude_reset_timestamp(match.group(2), current),
                "duration_minutes": duration,
            }
        )
    if not windows:
        return None
    return {
        "provider": "claude",
        "available": True,
        "stale": False,
        "plan": None,
        "windows": windows,
        "message": "Actualisé via /usage Claude",
    }


def strip_terminal_codes(value: str) -> str:
    value = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", value)
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)


def claude_reset_timestamp(value: str, now: datetime) -> float | None:
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip().replace(" ", "")
    for pattern in (
        "%I:%M%p",
        "%I%p",
        "%b%d,%I:%M%p",
        "%b%d,%I%p",
    ):
        try:
            parsed = datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
        if pattern in {"%I:%M%p", "%I%p"}:
            target = now.replace(
                hour=parsed.hour,
                minute=parsed.minute,
                second=0,
                microsecond=0,
            )
            if target <= now:
                target += timedelta(days=1)
        else:
            target = now.replace(
                month=parsed.month,
                day=parsed.day,
                hour=parsed.hour,
                minute=parsed.minute,
                second=0,
                microsecond=0,
            )
            if target < now - timedelta(days=2):
                target = target.replace(year=target.year + 1)
        return target.timestamp()
    return None


def iso_timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).timestamp()
    except ValueError:
        return None
