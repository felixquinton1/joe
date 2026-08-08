from __future__ import annotations

from datetime import date, datetime, time as clock_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def normalize_schedule(value: Any) -> dict[str, Any]:
    if not value:
        return {"timezone": "Europe/Paris", "windows": []}
    if not isinstance(value, dict):
        raise ValueError("Le calendrier Autonomous est invalide.")
    timezone = str(value.get("timezone", "Europe/Paris"))
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Fuseau horaire Autonomous inconnu.") from exc
    windows = value.get("windows")
    if not isinstance(windows, list) or len(windows) > 14:
        raise ValueError("Les fenêtres Autonomous sont invalides.")
    clean = []
    for item in windows:
        if not isinstance(item, dict):
            raise ValueError("Une fenêtre Autonomous est invalide.")
        days = sorted({int(day) for day in item.get("days", range(7))})
        if not days or any(day < 0 or day > 6 for day in days):
            raise ValueError("Les jours Autonomous doivent être compris entre 0 et 6.")
        start = _parse_time(str(item.get("start", "")))
        end = _parse_time(str(item.get("end", "")))
        if start == end:
            raise ValueError("Une fenêtre Autonomous ne peut pas durer 24 heures.")
        clean.append({
            "days": days,
            "start": start.strftime("%H:%M"),
            "end": end.strftime("%H:%M"),
        })
    return {"timezone": timezone, "windows": clean}


def schedule_state(schedule: dict[str, Any], timestamp: float) -> dict[str, Any]:
    windows = schedule.get("windows") or []
    if not windows:
        return {"active": True, "next_start": None, "window_end": None}
    zone = ZoneInfo(str(schedule.get("timezone", "Europe/Paris")))
    current = datetime.fromtimestamp(timestamp, zone)
    intervals: list[tuple[datetime, datetime]] = []
    for offset in range(-1, 9):
        day = current.date() + timedelta(days=offset)
        for item in windows:
            if day.weekday() not in item["days"]:
                continue
            start, end = _interval(day, item, zone)
            intervals.append((start, end))
    intervals.sort(key=lambda interval: interval[0])
    for start, end in intervals:
        if start <= current < end:
            return {
                "active": True,
                "next_start": start.timestamp(),
                "window_end": end.timestamp(),
            }
    future = next((start for start, _ in intervals if start > current), None)
    return {
        "active": False,
        "next_start": future.timestamp() if future else None,
        "window_end": None,
    }


def _parse_time(value: str) -> clock_time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("Les heures Autonomous doivent utiliser HH:MM.") from exc


def _interval(day: date, item: dict[str, Any], zone: ZoneInfo) -> tuple[datetime, datetime]:
    start_time = _parse_time(item["start"])
    end_time = _parse_time(item["end"])
    start = datetime.combine(day, start_time, zone)
    end_day = day if end_time > start_time else day + timedelta(days=1)
    return start, datetime.combine(end_day, end_time, zone)
