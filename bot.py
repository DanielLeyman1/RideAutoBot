# -*- coding: utf-8 -*-
import logging
import os
import time
import uuid
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from encar_report import extract_carid, fetch_report_pdf

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
        "• Написать /myid — покажу твой Telegram ID (для проверки админа)."
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
        ok = await fetch_report_pdf(carid, file_path, on_status=report_status)
        print(f"[{time.strftime('%H:%M:%S')}] [BOT] fetch_report_pdf вернул ok={ok}", flush=True)
        if not ok:
            await status.edit_text("Не удалось сформировать PDF (страница не открылась или нет отчёта).")
            return
        with open(file_path, "rb") as f:
            await update.message.reply_document(
            document=f, filename=f"encar_report_{carid}_ru.pdf"
        )
        await status.edit_text(
            f"Отчёт по carid={carid} переведён на русский и сохранён.\nСсылка/ID для поста: `{file_id}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await status.edit_text(f"Ошибка: {e}")


# ===== Запуск бота =====
def main():
    TOKEN = "8725470238:AAGiXoMb0ETxMRUwwuuIDyludQtKASBN97c"  # тестовый бот
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Бот запущен…")
    app.run_polling()


if __name__ == "__main__":
    main()
