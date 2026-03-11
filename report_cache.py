# -*- coding: utf-8 -*-
"""
Кэш отчётов по carid: повторный запрос в течение CACHE_HOURS отдаёт ту же ссылку.
Ссылка действительна LINK_DAYS дней; после истечения показывается страница «отчёт устарел».
"""
import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

CACHE_HOURS = int(os.environ.get("REPORT_CACHE_HOURS", "48"))
LINK_DAYS = int(os.environ.get("REPORT_LINK_DAYS", "7"))


def _cache_path(data_dir: Path) -> Path:
    return data_dir / "report_cache.json"


def _load(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {"by_carid": {}, "by_token": {}}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"by_carid": {}, "by_token": {}}


def _save(cache_path: Path, data: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_cached_token(carid: str, data_dir: Path) -> str | None:
    """
    Если по carid есть отчёт, созданный не позднее CACHE_HOURS назад, возвращает его token.
    Иначе None.
    """
    path = _cache_path(data_dir)
    data = _load(path)
    by_carid = data.get("by_carid", {})
    entry = by_carid.get(carid)
    if not entry:
        return None
    try:
        created = datetime.fromisoformat(entry["created_at"])
        if datetime.now() - created > timedelta(hours=CACHE_HOURS):
            return None
        return entry.get("token")
    except Exception:
        return None


def save_report(carid: str, html_content: str, reports_dir: Path, data_dir: Path) -> str:
    """
    Генерирует token, сохраняет HTML в reports_dir/<token>.html, регистрирует в кэше.
    Возвращает token.
    """
    token = secrets.token_urlsafe(12)
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = reports_dir / f"{token}.html"
    report_file.write_text(html_content, encoding="utf-8")

    now = datetime.now()
    expires_at = now + timedelta(days=LINK_DAYS)
    path_str = str(report_file.resolve())

    cache_path = _cache_path(data_dir)
    data = _load(cache_path)
    by_carid = data.setdefault("by_carid", {})
    by_token = data.setdefault("by_token", {})

    by_carid[carid] = {
        "token": token,
        "path": path_str,
        "created_at": now.isoformat(),
    }
    by_token[token] = {
        "carid": carid,
        "path": path_str,
        "expires_at": expires_at.isoformat(),
    }
    _save(cache_path, data)
    return token


def get_report_path(token: str, data_dir: Path) -> tuple[Path | None, bool]:
    """
    По token возвращает (путь к HTML, истёк ли срок).
    Если token не найден или срок истёк: (None, True).
    """
    path = _cache_path(data_dir)
    data = _load(path)
    by_token = data.get("by_token", {})
    entry = by_token.get(token)
    if not entry:
        return (None, True)
    try:
        expires_at = datetime.fromisoformat(entry["expires_at"])
        if datetime.now() > expires_at:
            return (None, True)
        return (Path(entry["path"]), False)
    except Exception:
        return (None, True)
