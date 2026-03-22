# -*- coding: utf-8 -*-
"""
HTTP-сервер для раздачи отчётов по короткой ссылке /r/<token>.
Также /<token> — если reverse proxy (Caddy) отрезает префикс /r/ и шлёт только токен.
"""
import base64
import os
import re
from pathlib import Path

from flask import Flask, abort, send_file

from report_cache import get_report_path

app = Flask(__name__)
# Каталоги задаются при старте через init_report_server()
_REPORTS_DIR = None
_DATA_DIR = None
_EXPIRED_HTML = None


def _build_expired_html(template_dir: Path, data_dir: Path) -> str:
    """Собирает HTML страницы «отчёт устарел» с логотипом в стиле сайта."""
    from jinja2 import Environment, FileSystemLoader
    logo_src = ""
    logo_path = template_dir / "images" / "logo.svg"
    if logo_path.exists():
        try:
            raw = logo_path.read_bytes()
            logo_src = f"data:image/svg+xml;base64,{base64.b64encode(raw).decode('ascii')}"
        except Exception:
            pass
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    template = env.get_template("expired.html")
    return template.render(logo_src=logo_src, og_image=logo_src)


def init_report_server(reports_dir: Path, data_dir: Path, template_dir: Path) -> None:
    global _REPORTS_DIR, _DATA_DIR, _EXPIRED_HTML
    _REPORTS_DIR = Path(reports_dir)
    _DATA_DIR = Path(data_dir)
    _EXPIRED_HTML = _build_expired_html(template_dir, data_dir)


def _looks_like_report_token(token: str) -> bool:
    """Как у secrets.token_urlsafe: без точек и слэшей, чтобы не перехватывать favicon.ico и т.п."""
    if not token or len(token) < 8 or len(token) > 128:
        return False
    if "." in token or "/" in token or "\\" in token:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", token))


def _deliver_report(token: str):
    if _DATA_DIR is None or _REPORTS_DIR is None:
        return _EXPIRED_HTML or "Отчёт устарел.", 404, {"Content-Type": "text/html; charset=utf-8"}
    cached_path, expired = get_report_path(token, _DATA_DIR)
    if expired or cached_path is None:
        return _EXPIRED_HTML or "Отчёт устарел.", 200, {"Content-Type": "text/html; charset=utf-8"}
    html_file = _REPORTS_DIR / f"{token}.html"
    path_to_send = html_file if html_file.exists() else cached_path
    if not path_to_send.exists():
        return _EXPIRED_HTML or "Отчёт устарел.", 200, {"Content-Type": "text/html; charset=utf-8"}
    return send_file(
        path_to_send,
        mimetype="text/html; charset=utf-8",
        as_attachment=False,
        download_name=None,
    )


@app.route("/r/<token>")
def serve_report(token: str):
    return _deliver_report(token)


@app.route("/<token>")
def serve_report_root(token: str):
    """Caddy/nginx иногда проксируют путь без /r/ — только токен в корне."""
    if not _looks_like_report_token(token):
        abort(404)
    return _deliver_report(token)


def run_server(port: int = None, reports_dir: Path = None, data_dir: Path = None, template_dir: Path = None) -> None:
    port = port or int(os.environ.get("REPORT_SERVER_PORT", "9090"))
    base = Path(__file__).resolve().parent
    reports_dir = reports_dir or base / "reports"
    data_dir = data_dir or base / "data"
    template_dir = template_dir or base / "templates"
    init_report_server(reports_dir, data_dir, template_dir)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
