# -*- coding: utf-8 -*-
"""
Получение PDF-отчёта Encar по carid или ссылке на машину.
Загрузка страницы → перевод текста на русский → сборка PDF.
"""
import asyncio
import re
import time
from pathlib import Path


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# Точный формат как на сайте (method и carid)
REPORT_URL = "https://www.encar.com/md/sl/mdsl_regcar.do?method=inspectionViewNew&carid={carid}"

# Паттерны для извлечения carid (query carid=... или путь /detail/123/)
CARID_PATTERN = re.compile(r"carid=(\d+)", re.I)
CARID_PATH_PATTERN = re.compile(r"encar\.com[^/]*/.*?/(?:detail/)?(\d{6,})(?:\?|/|$)", re.I)
CARID_ONLY_PATTERN = re.compile(r"^\s*(\d{6,})\s*$")  # только цифры, минимум 6

# Корейские символы (Хангул) — переводим только такой текст
HANGUL_RE = re.compile(r"[\uAC00-\uD7A3]+")

# Сколько кусков переводить одновременно (ускорение)
PARALLEL_CHUNKS = 6
# Пауза между "волнами" параллельных запросов (сек)
TRANSLATE_BATCH_DELAY = 0.15
# Лимит символов на один запрос (Google ~5000)
TRANSLATE_MAX_CHARS = 4000
# Разделитель (Private Use — не встречается в тексте)
SEP = "\uE000"
# Таймаут перевода (сек); при превышении — PDF без перевода
TRANSLATE_TIMEOUT = 90


def _has_hangul(s: str) -> bool:
    return bool(HANGUL_RE.search(s))


def _translate_chunk_sync(chunk: str) -> list[str]:
    """Переводит один кусок (вызывается из потока). Возвращает список фраз по SEP."""
    try:
        from deep_translator import GoogleTranslator
        tr = GoogleTranslator(source="ko", target="ru").translate(chunk) or chunk
        return tr.split(SEP)
    except Exception:
        return chunk.split(SEP)


async def _translate_texts_async(texts: list[str]) -> list[str]:
    """
    Переводит тексты асинхронно: куски отправляются параллельно (по PARALLEL_CHUNKS),
    чтобы уложиться в ~1 минуту.
    """
    if not texts:
        _log("TRANSLATE: нет текста")
        return []
    indices_need = [j for j, t in enumerate(texts) if t.strip() and _has_hangul(t.strip())]
    if not indices_need:
        _log("TRANSLATE: нет корейского")
        return list(texts)

    strings_need = [texts[j].strip() for j in indices_need]
    combined = SEP.join(strings_need)
    _log(f"TRANSLATE: сегментов={len(strings_need)}, символов={len(combined)}")

    segments = combined.split(SEP)
    chunks = []
    current = []
    current_len = 0
    for s in segments:
        add_len = len(s) + len(SEP)
        if current_len + add_len > TRANSLATE_MAX_CHARS and current:
            chunks.append(SEP.join(current))
            current = [s]
            current_len = add_len
        else:
            current.append(s)
            current_len += add_len
    if current:
        chunks.append(SEP.join(current))

    _log(f"TRANSLATE: кусков={len(chunks)}, параллельно по {PARALLEL_CHUNKS}...")
    all_parts = []
    for i in range(0, len(chunks), PARALLEL_CHUNKS):
        batch = chunks[i : i + PARALLEL_CHUNKS]
        tasks = [asyncio.to_thread(_translate_chunk_sync, ch) for ch in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, r in enumerate(results):
            if isinstance(r, Exception):
                _log(f"  ошибка куска: {r}")
                all_parts.extend(batch[idx].split(SEP))
            else:
                all_parts.extend(r)
        if i + len(batch) < len(chunks):
            await asyncio.sleep(TRANSLATE_BATCH_DELAY)

    if len(all_parts) != len(strings_need):
        _log(f"TRANSLATE: несовпадение {len(all_parts)} != {len(strings_need)}, без перевода")
        return list(texts)

    trans_idx = 0
    result = []
    for j in range(len(texts)):
        if j in indices_need:
            result.append((all_parts[trans_idx] or texts[j]).strip() if trans_idx < len(all_parts) else texts[j])
            trans_idx += 1
        else:
            result.append(texts[j])
    _log("TRANSLATE: готово")
    return result


def _translate_texts_sync(texts: list[str]) -> list[str]:
    """
    Переводит все строки с корейского на русский за несколько запросов:
    собираем все фразы в один текст, режем по ~4000 символов, переводим каждый кусок.
    """
    if not texts:
        _log("TRANSLATE: нет текста")
        return []
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        raise ImportError("Установите deep-translator: pip install deep-translator")

    indices_need = [j for j, t in enumerate(texts) if t.strip() and _has_hangul(t.strip())]
    if not indices_need:
        _log("TRANSLATE: нет корейского")
        return list(texts)

    strings_need = [texts[j].strip() for j in indices_need]
    combined = SEP.join(strings_need)
    _log(f"TRANSLATE: сегментов={len(strings_need)}, всего символов={len(combined)}")

    # Режем на куски по TRANSLATE_MAX_CHARS по границам SEP
    segments = combined.split(SEP)
    chunks = []
    current = []
    current_len = 0
    for s in segments:
        add_len = len(s) + len(SEP)
        if current_len + add_len > TRANSLATE_MAX_CHARS and current:
            chunks.append(SEP.join(current))
            current = [s]
            current_len = add_len
        else:
            current.append(s)
            current_len += add_len
    if current:
        chunks.append(SEP.join(current))

    _log(f"TRANSLATE: кусков={len(chunks)}, перевожу...")
    translator = GoogleTranslator(source="ko", target="ru")
    all_parts = []
    for i, ch in enumerate(chunks):
        t0 = time.time()
        for attempt in range(3):
            try:
                tr = translator.translate(ch) or ch
                all_parts.extend(tr.split(SEP))
                _log(f"  кусок {i+1}/{len(chunks)} ок, {time.time()-t0:.1f}s")
                break
            except Exception as e:
                _log(f"  кусок {i+1}/{len(chunks)} попытка {attempt+1}/3: {e}")
                if attempt == 2:
                    all_parts.extend(ch.split(SEP))
                else:
                    time.sleep(1.0)
        time.sleep(TRANSLATE_DELAY)

    # Если разделители исказились — не переводим по одной (это 10+ мин), отдаём как есть
    if len(all_parts) != len(strings_need):
        _log(f"TRANSLATE: несовпадение {len(all_parts)} != {len(strings_need)}, возвращаю без перевода")
        return list(texts)

    trans_idx = 0
    result = []
    for j in range(len(texts)):
        if j in indices_need:
            result.append((all_parts[trans_idx] or texts[j]).strip() if trans_idx < len(all_parts) else texts[j])
            trans_idx += 1
        else:
            result.append(texts[j])
    _log("TRANSLATE: готово")
    return result


def _parse_html_and_get_texts(html: str, base_url: str = "https://www.encar.com"):
    """Парсит HTML, добавляет <base>, возвращает (soup, list of (node, text))."""
    from bs4 import BeautifulSoup
    _log("HTML: парсинг...")
    soup = BeautifulSoup(html, "html.parser")
    head = soup.find("head")
    if head:
        base = soup.new_tag("base", href=base_url.rstrip("/") + "/")
        head.insert(0, base)
    body = soup.find("body")
    if not body:
        return None, []
    skip_tags = {"script", "style", "noscript"}
    nodes_to_translate = []
    for el in body.descendants:
        if not isinstance(el, str) or el.parent.name in skip_tags:
            continue
        s = str(el).strip()
        if s and _has_hangul(s):
            nodes_to_translate.append((el, s))
    return soup, nodes_to_translate


async def _translate_html_async(html: str, base_url: str = "https://www.encar.com") -> str:
    """
    Парсит HTML, переводит текст асинхронно (параллельные куски), подставляет обратно.
    """
    soup, nodes_to_translate = _parse_html_and_get_texts(html, base_url)
    if not soup or not nodes_to_translate:
        _log("HTML: нет узлов для перевода")
        return html
    _log(f"HTML: узлов с корейским={len(nodes_to_translate)}, перевод...")
    texts = [t for _, t in nodes_to_translate]
    translated = await _translate_texts_async(texts)
    _log("HTML: подставляю в разметку...")
    for (node, _), new_text in zip(nodes_to_translate, translated, strict=True):
        node.replace_with(new_text)
    return str(soup)


def extract_carid(text: str) -> str | None:
    """
    Извлекает carid из текста: ссылка Encar (fem.encar.com, www.encar.com и т.д.) или просто ID (число).
    Возвращает строку с ID или None.
    """
    if not text or not text.strip():
        return None
    text = text.strip()
    # Ссылка encar: сначала carid= в query, иначе ID из пути (/detail/123 или /cars/detail/123)
    if "encar" in text.lower():
        m = CARID_PATTERN.search(text)
        if m:
            return m.group(1)
        m = CARID_PATH_PATTERN.search(text)
        if m:
            return m.group(1)
        return None
    if "carid=" in text.lower():
        m = CARID_PATTERN.search(text)
        return m.group(1) if m else None
    # Только число (ID)
    m = CARID_ONLY_PATTERN.match(text)
    return m.group(1) if m else None


def _render_report_template(data_ru: dict) -> str:
    """Рендерит HTML отчёта из шаблона и данных (маппинг уже применён)."""
    from jinja2 import Environment, FileSystemLoader
    template_dir = Path(__file__).resolve().parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    template = env.get_template("report_ru.html")
    return template.render(**data_ru)


async def fetch_report_pdf_mapped(
    carid: str,
    save_path: Path,
    on_status=None,
) -> bool:
    """
    Режим «парсинг + маппинг + шаблон»: загружает отчёт Encar, извлекает данные,
    применяет маппинг корейский→русский, подставляет в шаблон и сохраняет PDF.
    Быстро, без перевода через API.
    """
    async def _status(msg: str):
        if on_status:
            await on_status(msg)

    try:
        from playwright.async_api import async_playwright
        from report_parser import parse_report_html, load_mapping, apply_mapping
    except ImportError as e:
        _log(f"REPORT_MAPPED: импорт {e}")
        return False

    url = REPORT_URL.format(carid=carid)
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _log("REPORT_MAPPED: старт")
        await _status("Открываю страницу Encar…")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 900, "height": 1200},
                    locale="ko-KR",
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                )
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=35000)
                try:
                    await page.wait_for_selector(".inspec_carinfo, #bodydiv", timeout=12000)
                except Exception:
                    pass
                await page.wait_for_timeout(2000)
                _log("REPORT_MAPPED: страница загружена")
                await _status("Извлекаю данные, формирую отчёт на русском…")
                html = await page.content()
                data = parse_report_html(html)
                mapping = load_mapping()
                data_ru = apply_mapping(data, mapping)
                rendered = _render_report_template(data_ru)
                await page.set_content(rendered, wait_until="domcontentloaded", timeout=10000)
                await page.wait_for_timeout(800)
                await _status("Формирую PDF…")
                await page.pdf(path=str(save_path), format="A4", print_background=True)
                _log("REPORT_MAPPED: готово")
                return True
            finally:
                await browser.close()
    except Exception as e:
        import traceback
        _log(f"REPORT_MAPPED: ошибка {e}")
        traceback.print_exc()
        return False


async def fetch_report_pdf(
    carid: str,
    save_path: Path,
    translate_to_russian: bool = True,
    on_status=None,
) -> bool:
    """
    Открывает страницу отчёта Encar. По умолчанию использует режим маппинга (быстро).
    При translate_to_russian=True и необходимости можно переключиться на перевод по API.
    """
    # Сначала пробуем быстрый путь: парсинг + маппинг + шаблон
    ok = await fetch_report_pdf_mapped(carid, save_path, on_status=on_status)
    if ok:
        return True
    # Fallback: старый путь с переводом HTML
    async def _status(msg: str):
        if on_status:
            await on_status(msg)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError("Установите playwright: pip install playwright && playwright install chromium")

    url = REPORT_URL.format(carid=carid)
    base_url = "https://www.encar.com"
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _log("REPORT: старт (fallback перевод)")
        await _status("Открываю страницу Encar…")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 900, "height": 1200},
                    locale="ko-KR",
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                )
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=35000)
                try:
                    await page.wait_for_selector(".inspec_carinfo, #bodydiv", timeout=12000)
                except Exception:
                    pass
                await page.wait_for_timeout(2500)
                _log("REPORT: страница загружена")

                if translate_to_russian:
                    await _status("Страница загружена. Перевожу на русский (макс 2 мин)…")
                    html = await page.content()
                    try:
                        translated_html = await asyncio.wait_for(
                            _translate_html_async(html, base_url),
                            timeout=TRANSLATE_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        _log("REPORT: таймаут перевода, отдаю PDF без перевода")
                        await _status("Перевод не успел. Формирую PDF на корейском…")
                        translated_html = None
                    if translated_html is not None:
                        await _status("Вставляю перевод, формирую PDF…")
                        await page.set_content(translated_html, wait_until="networkidle", timeout=15000)
                        await page.wait_for_timeout(1000)
                    else:
                        await _status("Формирую PDF…")
                else:
                    await _status("Формирую PDF…")

                await page.pdf(path=str(save_path), format="A4", print_background=True)
                _log("REPORT: готово")
                return True
            finally:
                await browser.close()
    except Exception as e:
        import traceback
        _log(f"REPORT: ошибка {e}")
        traceback.print_exc()
        return False
