# -*- coding: utf-8 -*-
"""
Парсер HTML страницы отчёта Encar (mdsl_regcar.do).
Извлекает: основные данные (basic), таблица общего состояния (summary),
секция ремонта (repair), детальная таблица (detail).
"""
import json
import re
from pathlib import Path
from typing import Any

def _strip(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def _text(el) -> str:
    if el is None:
        return ""
    return _strip(el.get_text() if hasattr(el, "get_text") else str(el))


def parse_report_html(html: str) -> dict[str, Any]:
    """
    Парсит HTML отчёта Encar. Возвращает:
    - basic: dict { "차명": "ES300h...", "연식": "2021년", ... }
    - summary: list of { "label": "주행거리", "status": "양호", "value": "95,023km" }
    - repair: list of { "label": "사고이력", "value": "있음" }
    - detail: list of { "device": "원동기", "item": "작동상태", "status": "양호" }
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = {"basic": {}, "summary": [], "repair": [], "detail": []}

    # 1) Основная таблица .inspec_carinfo table.ckst — в каждой строке пары th+td, th+td
    table_basic = soup.select_one(".inspec_carinfo table.ckst")
    if table_basic:
        for tr in table_basic.select("tbody tr"):
            cells = tr.find_all(["th", "td"])
            i = 0
            while i < len(cells) - 1:
                if cells[i].name == "th":
                    key = _text(cells[i])
                    val = _text(cells[i + 1]) if cells[i + 1].name == "td" else ""
                    if key:
                        out["basic"][key] = val
                    i += 2
                else:
                    i += 1

    # 2) Таблица общего состояния .tbl_total
    table_total = soup.select_one("table.tbl_total")
    if table_total:
        for tr in table_total.select("tbody tr"):
            th = tr.select_one("th[scope=row]")
            if not th:
                continue
            label = _text(th)
            # Статус: .txt_state.on или первый .txt_state
            status_el = tr.select_one(".txt_state.on") or tr.select_one(".txt_state")
            status = _text(status_el) if status_el else ""
            # Значение: .txt_detail или вторая td
            detail_el = tr.select_one(".txt_detail")
            value = _text(detail_el) if detail_el else ""
            if not value and status:
                tds = tr.select("td.td_left")
                for td in tds:
                    t = _text(td)
                    if t and t != status and not t.startswith("span"):
                        value = t
                        break
            out["summary"].append({"label": label, "status": status, "value": value})

    # 3) Секция ремонта .section_repair
    section_repair = soup.select_one(".section_repair table.tbl_repair")
    if section_repair:
        for tr in section_repair.select("tbody tr"):
            th = tr.select_one("th[scope=row]")
            if not th:
                continue
            label = _text(th)
            if "자세히" in label or "uibtn" in label:
                label = re.sub(r"\s*자세히보기\s*", "", label)
            status_el = tr.select_one(".txt_state.on") or tr.select_one(".txt_state")
            value = _text(status_el) if status_el else ""
            out["repair"].append({"label": label, "value": value})

    # 4) Детальная таблица .tbl_detail (упрощённо: строка = устройство + пункт + статус)
    table_detail = soup.select_one("table.tbl_detail")
    if table_detail:
        current_device = ""
        for tr in table_detail.select("tbody tr"):
            ths = tr.select("th[scope=row]")
            tds = tr.select("td.td_left")
            status_el = tr.select_one(".txt_state.on") or tr.select_one(".txt_state")
            status = _text(status_el) if status_el else ""
            if ths:
                # Первый th может быть устройством (rowspan), остальные — пункт
                first = _text(ths[0])
                if first and first not in ("양호", "불량", "없음", "적정", "부족", "미세누유", "누유", "미세누수", "누수"):
                    if len(ths) >= 2:
                        current_device = first
                        item = _text(ths[1])
                    else:
                        item = first
                    out["detail"].append({"device": current_device, "item": item, "status": status})

    return out


def load_mapping() -> dict:
    path = Path(__file__).resolve().parent / "data" / "report_mapping.json"
    if not path.exists():
        return {"labels": {}, "status_words": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_mapping(data: dict, mapping: dict) -> dict:
    """
    Применяет маппинг: корейские ключи и значения статусов → русские.
    Данные по авто (пробег, VIN, даты) не трогаем.
    """
    labels = mapping.get("labels", {})
    status_words = mapping.get("status_words", {})

    def map_label(k: str) -> str:
        k_clean = _strip(k)
        return labels.get(k_clean, labels.get(k_clean.replace("\n", " "), k_clean))

    def map_value(v: str) -> str:
        if not v:
            return v
        v_clean = _strip(v)
        return status_words.get(v_clean, v_clean)

    out = {"basic": {}, "summary": [], "repair": [], "detail": []}

    for k, v in data.get("basic", {}).items():
        out["basic"][map_label(k)] = v

    for row in data.get("summary", []):
        out["summary"].append({
            "label": map_label(row.get("label", "")),
            "status": map_value(row.get("status", "")),
            "value": row.get("value", ""),
        })

    for row in data.get("repair", []):
        out["repair"].append({
            "label": map_label(row.get("label", "")),
            "value": map_value(row.get("value", "")),
        })

    for row in data.get("detail", []):
        out["detail"].append({
            "device": map_label(row.get("device", "")),
            "item": map_label(row.get("item", "")),
            "status": map_value(row.get("status", "")),
        })

    return out
