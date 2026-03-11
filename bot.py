# -*- coding: utf-8 -*-
import logging
import os
import time
import uuid
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from encar_report import extract_carid, fetch_report_pdf, run_report_diagnostics

# ===== Настройки =====
ADMIN_ID = 377261863  # твой Telegram ID
STORAGE_DIR = Path("pdf_storage")
STORAGE_DIR.mkdir(exist_ok=True)

# Лог в консоль, чтобы видеть входящие сообщения
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ===== Команды =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Можешь:\n"
        "• Отправить PDF — сохраню и дам ID для поста.\n"
        "• Написать ID машины или ссылку Encar — скачаю отчёт, переведу на русский, соберу PDF и дам ссылку.\n"
        "• /myid — твой Telegram ID.\n"
        "• /report_diag — диагностика логотипа и схем (админ)."
    )


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка: твой ID и совпадает ли с админом."""
    user_id = update.message.from_user.id
    is_admin = user_id == ADMIN_ID
    await update.message.reply_text(
        f"Твой Telegram ID: `{user_id}`\n"
        f"ADMIN_ID в боте: `{ADMIN_ID}`\n"
        f"Ты админ: {'да' if is_admin else 'нет'}",
        parse_mode="Markdown",
    )


async def cmd_report_diag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Самодиагностика: где ищутся шаблоны и картинки (логотип, схемы). Только для админа."""
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("Только для администратора.")
        return
    bot_dir = Path(__file__).resolve().parent
    diag = run_report_diagnostics(bot_dir)
    lines = diag.get("log_lines", ["Диагностика не выполнена."])
    text = "📋 Диагностика отчёта (логотип и схемы):\n\n" + "\n".join(lines)
    await update.message.reply_text(text[:4000])


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Ты не имеешь права загружать файлы.")
        return

    doc = update.message.document
    if doc.mime_type != "application/pdf":
        await update.message.reply_text("Только PDF разрешено!")
        return

    file_id = str(uuid.uuid4())
    file_path = STORAGE_DIR / f"{file_id}.pdf"

    await doc.get_file().download_to_drive(file_path)
    await update.message.reply_text(f"PDF сохранен. Ссылка/ID для поста: `{file_id}`", parse_mode="Markdown")


def _looks_like_encar_or_id(text: str) -> bool:
    """Сообщение похоже на ссылку Encar или ID машины."""
    if not text:
        return False
    t = text.strip().lower()
    if "encar" in t or "carid=" in t:
        return True
    if t.isdigit() and len(t) >= 6:
        return True
    return False


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = (update.message.text or "").strip()

    carid_extracted = extract_carid(text)
    # В консоли видно каждое входящее сообщение (для отладки)
    print(f"[{time.strftime('%H:%M:%S')}] [Текст] user_id={user_id} len={len(text)} carid={carid_extracted}", flush=True)
    logger.info("Текст от %s, carid=%s", user_id, carid_extracted)

    if user_id != ADMIN_ID:
        if _looks_like_encar_or_id(text):
            await update.message.reply_text(
                "Запрашивать отчёты по ссылке или ID может только администратор."
            )
        return

    carid = carid_extracted
    if not carid:
        if _looks_like_encar_or_id(text):
            await update.message.reply_text(
                "Не удалось извлечь ID машины из ссылки. Проверь, что в ссылке есть carid=ЧИСЛО или путь вида .../detail/ЧИСЛО."
            )
        return

    status = await update.message.reply_text(
        f"Запрашиваю отчёт Encar для carid={carid}…"
    )

    async def report_status(msg: str):
        try:
            await status.edit_text(f"{msg}\n\ncarid={carid}")
        except Exception:
            pass

    try:
        file_id = str(uuid.uuid4())
        file_path = STORAGE_DIR / f"{file_id}.pdf"
        print(f"[{time.strftime('%H:%M:%S')}] [BOT] вызов fetch_report_pdf carid={carid}", flush=True)
        bot_dir = Path(__file__).resolve().parent
        ok, html_path, images_ok = await fetch_report_pdf(carid, file_path, on_status=report_status, base_dir=bot_dir)
        print(f"[{time.strftime('%H:%M:%S')}] [BOT] fetch_report_pdf ok={ok} images_ok={images_ok} html_path={html_path}", flush=True)
        if not ok:
            await status.edit_text(
                "Не удалось сформировать отчёт (таймаут, страница Encar недоступна или ошибка).\n"
                "Проверьте логи бота. Команда /report_diag — диагностика логотипа и схем."
            )
            return
        with open(file_path, "rb") as f:
            await update.message.reply_document(
                document=f, filename=f"encar_report_{carid}_ru.pdf"
            )
        if html_path and html_path.exists():
            with open(html_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"encar_report_{carid}_ru.html",
                    caption="Если в PDF нет логотипа и схем — откройте этот HTML в браузере → Печать (Ctrl+P) → Сохранить как PDF." if not images_ok else None,
                )
        msg = f"Отчёт по carid={carid} переведён на русский и сохранён.\nСсылка/ID для поста: `{file_id}`"
        if not images_ok and html_path:
            msg += "\n\n⚠️ В PDF картинки могли не отобразиться — приложен HTML для печати в PDF из браузера."
        await status.edit_text(msg, parse_mode="Markdown")
    except Exception as e:
        await status.edit_text(f"Ошибка: {e}")


# ===== Запуск бота =====
def main():
    TOKEN = "8596627705:AAFHUS6_b3jqhBm1NyLGsEARFhxHL0PJ4Go"  # тестовый бот
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("report_diag", cmd_report_diag))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Бот запущен…")
    app.run_polling()


if __name__ == "__main__":
    main()
