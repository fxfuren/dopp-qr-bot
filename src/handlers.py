"""Telegram bot message handlers."""

import asyncio
import re
from pathlib import Path

from loguru import logger
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .config import settings
from .pdf_processor import (
    find_vehicle_in_text,
    extract_all_spec_numbers_from_text,
    extract_qr_from_pdf,
    extract_spec_number_from_text,
    extract_text_from_pdf,
    extract_vehicle_registration,
    find_spec_number_in_text,
)
from .yadisk_client import YaDiskClient

# Maximum concurrent PDF processing tasks
MAX_CONCURRENT_PDF = 5
_pdf_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PDF)


def get_driver_info(user) -> str:
    """
    Format driver information for notifications.
    Returns username, first name, or user ID.
    """
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        return user.first_name
    else:
        return f"ID: {user.id}"


def validate_spec_number(text: str) -> str | None:
    """
    Validate and clean specification number from user input.

    Returns cleaned spec number or None if invalid.
    """
    spec_number = text.strip()
    if not re.match(r"^[A-ZА-Яa-zа-я0-9]+(/[A-ZА-Яa-zа-я0-9]+)?$", spec_number):
        return None
    return spec_number


async def search_pdf_files(
    yadisk_client: YaDiskClient,
    spec_number: str,
    status_msg,
) -> list[dict] | None:
    """
    Search for PDF files on Yandex Disk by spec number.

    First tries to find by filename (fast), then falls back to listing
    all files for content-based search (slow).

    Returns list of remote paths or None if nothing found.
    """
    # Step 1: Try to find files by spec number in filename (fast)
    pdf_files = await yadisk_client.list_all_pdf_files(spec_number=spec_number)

    if pdf_files:
        return pdf_files

    # Step 2: Fall back to listing all files for content-based search
    logger.info(
        f"No files found by filename for {spec_number}, checking all files by content..."
    )
    await status_msg.edit_text(
        f"🔍 Файлы с номером {spec_number} не найдены по названию.\n"
        f"Проверяю содержимое всех документов (может занять время)..."
    )
    pdf_files = await yadisk_client.list_all_pdf_files(spec_number=None)

    return pdf_files if pdf_files else None


async def process_single_pdf(
    yadisk_client: YaDiskClient,
    file_info: dict,
    index: int,
    total: int,
    search_term: str,
    search_mode: str,
    tmp_dir: Path,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """
    Process a single PDF file: download, check for spec number, extract QR.

    Returns dict with QR data if found, None if spec not in this file,
    or dict with 'error': True on failure.
    """
    remote_path = file_info['path']
    modified = file_info['modified']
    pdf_filename = file_info['name']
    
    safe_spec_number = search_term.replace("/", "_")
    local_pdf_path = tmp_dir / f"{safe_spec_number}_{index}.pdf"
    local_qr_path = tmp_dir / f"{safe_spec_number}_{index}_qr.png"

    from .cache import PDFCache
    from .config import settings
    cache = PDFCache(str(Path(settings.data_dir) / 'pdf_cache.db'))

    async with semaphore:
        try:
            # Check cache first
            cached_data = await cache.get_cached_pdf(remote_path, modified)
            
            if cached_data:
                text = cached_data['text']
                logger.debug(f"Cache hit for {pdf_filename}")
                all_spec_numbers = cached_data['spec_numbers']
                display_spec = ", ".join(all_spec_numbers) if all_spec_numbers else search_term
                vehicle_reg = cached_data['vehicle_reg']
                trailer_reg = cached_data['trailer_reg']
            else:
                # Download PDF
                logger.debug(f"Processing file {index + 1}/{total}: {pdf_filename}")
                await yadisk_client.download_file(remote_path, str(local_pdf_path))
                # Extract text from all pages
                text = await extract_text_from_pdf(str(local_pdf_path), page_number=None)
                
                # Extract data to save to cache immediately
                all_spec_numbers = extract_all_spec_numbers_from_text(text)
                display_spec = ", ".join(all_spec_numbers) if all_spec_numbers else search_term
                vehicle_info = extract_vehicle_registration(text)
                vehicle_reg = vehicle_info.get("vehicle") if vehicle_info else None
                trailer_reg = vehicle_info.get("trailer") if vehicle_info else None
                
                # Save to cache
                await cache.save_pdf_cache(remote_path, modified, text, all_spec_numbers, vehicle_reg, trailer_reg)

            # Check if text matches the search term
            if search_mode == 'spec':
                found = find_spec_number_in_text(text, search_term)
            else:
                found = find_vehicle_in_text(text, search_term)

            if not found:
                logger.debug(f"{search_mode} {search_term} not found in {pdf_filename}")
                return None

            logger.info(f"{search_mode} {search_term} found in {pdf_filename}")

            # We need the PDF locally to extract the QR code!
            if cached_data and not local_pdf_path.exists():
                 logger.debug(f"Downloading {pdf_filename} to extract QR code...")
                 await yadisk_client.download_file(remote_path, str(local_pdf_path))

            # Extract QR code with auto-detection
            await extract_qr_from_pdf(
                str(local_pdf_path),
                str(local_qr_path),
                crop_coords=None,
                dpi=settings.qr_dpi,
            )

            return {
                "path": local_qr_path,
                "filename": display_spec,
                "vehicle_reg": vehicle_reg,
                "trailer_reg": trailer_reg,
            }

        except Exception as e:
            logger.error(f"Error processing {pdf_filename}: {e}")
            return {"error": True}

        finally:
            # Always clean up downloaded PDF
            try:
                if local_pdf_path.exists():
                    local_pdf_path.unlink()
                    logger.debug(f"Cleaned up temp PDF: {local_pdf_path}")
            except OSError as e:
                logger.warning(f"Failed to delete temp PDF {local_pdf_path}: {e}")


async def send_notification(
    context: ContextTypes.DEFAULT_TYPE,
    driver_info: str,
    spec_number: str,
    vehicle_reg: str | None,
    trailer_reg: str | None = None,
) -> None:
    """
    Send notification to the notification chat about QR code request.
    """
    if not settings.notification_chat_id:
        logger.debug("Notification chat ID not configured, skipping notification")
        return

    try:
        notification_text = (
            f"Водитель запросил QR-код и успешно его получил\n\n"
            f"👤 Водитель: {driver_info}\n"
            f"📄 СМР: {spec_number}"
        )

        if vehicle_reg:
            notification_text += f"\n🚗 АВТО: {vehicle_reg}"
        if trailer_reg:
            notification_text += f"\n🚛 ПРИЦЕП: {trailer_reg}"

        await context.bot.send_message(
            chat_id=settings.notification_chat_id, text=notification_text
        )
        logger.info(f"Notification sent to chat {settings.notification_chat_id}")
    except Exception as e:
        logger.error(
            f"Failed to send notification to chat {settings.notification_chat_id}: {e}"
        )
        logger.info(
            "Убедитесь что: 1) Бот добавлен в чат, 2) Боту даны права администратора "
            "или права на отправку сообщений, 3) Chat ID правильный"
        )


async def send_qr_results(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status_msg,
    qr_codes: list[dict],
    errors: int,
    spec_number: str,
    driver_info: str,
) -> None:
    """
    Send QR code images to user, notification to chat, and summary message.
    """
    if not qr_codes:
        await status_msg.edit_text(
            f"❌ Спецификация {spec_number} не найдена в документах."
        )
        return

    try:
        for qr_data in qr_codes:
            caption = f"📄 {qr_data['filename']}"
            if qr_data["vehicle_reg"]:
                caption += f"\n🚗 АВТО: {qr_data['vehicle_reg']}"
            if qr_data.get("trailer_reg"):
                caption += f"\n🚛 ПРИЦЕП: {qr_data['trailer_reg']}"

            with open(qr_data["path"], "rb") as qr_file:
                await update.message.reply_photo(photo=qr_file, caption=caption)

        logger.info(f"Sent {len(qr_codes)} QR code(s) for spec {spec_number}")

        # Send notification about successful QR code request
        first_qr = qr_codes[0]
        await send_notification(
            context=context,
            driver_info=driver_info,
            spec_number=first_qr["filename"],
            vehicle_reg=first_qr["vehicle_reg"],
            trailer_reg=first_qr.get("trailer_reg"),
        )

    except Exception as e:
        logger.error(f"Error sending QR codes: {e}")
        errors += 1

    finally:
        # Clean up QR code images
        for qr_data in qr_codes:
            try:
                if qr_data["path"].exists():
                    qr_data["path"].unlink()
            except OSError as e:
                logger.warning(f"Failed to delete {qr_data['path']}: {e}")

    # Send summary message
    summary = f"✅ Готово! Отправлено QR-кодов: {len(qr_codes)}"
    if errors > 0:
        summary += f"\n⚠️ Ошибок при обработке: {errors}"
    await status_msg.edit_text(summary)


async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /start command."""
    welcome_message = (
        "👋 Привет! Я бот для извлечения QR-кодов для СПОТ.\n\n"
        "Просто отправьте мне номер СМР (например: 47589) или номер авто (например: C542OA67), "
        "и я найду соответствующие документы и отправлю вам QR-коды.\n\n"
        "Используйте /help для получения дополнительной информации."
    )
    await update.message.reply_text(welcome_message)


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /help command."""
    help_message = (
        "📖 Как использовать бота:\n\n"
        "1. Отправьте номер СМР (например: 47589) или номер авто (например: C542OA67)\n"
        "2. Выберите тип поиска по кнопке\n"
        "3. Бот найдет все документы с этим номером\n"
        "4. Вы получите QR-коды из найденных документов\n\n"
        "Примеры СМР:\n"
        "• 47589 - найдет все документы (47589, 47589/1, 47589/2 и т.д.)\n"
        "• 47589/1 - найдет только документ с номером 47589/1\n\n"
        "Примеры авто:\n"
        "• C542OA67 - поиск по номеру авто\n"
        "• BA 5118 5 - поиск по номеру авто с пробелами\n\n"
        "Если возникли проблемы, проверьте правильность номера."
    )
    await update.message.reply_text(help_message)


async def handle_user_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle user text input and show search options."""
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    text = update.message.text

    search_term = text.strip()
    # Basic validation for vehicle or spec number
    if len(search_term) < 3 or len(search_term) > 30:
        await update.message.reply_text(
            "❌ Неверный формат ввода.\n"
            "Пожалуйста, введите корректный номер СМР (например: 47589) или номер авто."
        )
        return
        
    logger.info(f"User {user_id} (@{username}) entered: {search_term}")
    
    # Validation for Spec number specifically (can be relaxed for vehicle)
    validated_spec = validate_spec_number(search_term)
    
    keyboard = [
        [
            InlineKeyboardButton("📄 По номеру СМР", callback_data=f"s:{validated_spec or search_term}"),
            InlineKeyboardButton("🚗 По номеру авто", callback_data=f"v:{search_term}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Выберите тип поиска для \"{search_term}\":",
        reply_markup=reply_markup
    )


async def handle_search_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle callback from search inline keyboard."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    try:
        mode, search_term = data.split(':', 1)
    except ValueError:
        logger.error(f"Invalid callback data: {data}")
        return
        
    status_msg = query.message
    
    if mode == 's':
        await _do_spec_search(update, context, search_term, status_msg)
    elif mode == 'v':
        await _do_vehicle_search(update, context, search_term, status_msg)


async def _do_spec_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE, spec_number: str, status_msg
) -> None:
    """Execute specification number search."""
    driver_info = get_driver_info(update.effective_user)
    
    if not validate_spec_number(spec_number):
        await status_msg.edit_text(
            "❌ Неверный формат номера СМР.\n"
            "Используйте формат: 47589, 47589/1 или AVN2218"
        )
        return

    await status_msg.edit_text("🔍 Ищу документы по номеру СМР...")
    
    yadisk_client = YaDiskClient(
        token=settings.yandex_disk_token, folder_path=settings.yadisk_folder
    )

    try:
        pdf_files = await search_pdf_files(yadisk_client, spec_number, status_msg)
        if not pdf_files:
            await status_msg.edit_text("❌ Не найдено документов.")
            return

        await _process_and_send_results(
            update, context, yadisk_client, pdf_files, spec_number, "spec", status_msg, driver_info
        )
    except Exception as e:
        logger.error(f"Fatal error processing request: {e}")
        await status_msg.edit_text(
            "❌ Произошла ошибка при обработке запроса. "
            "Пожалуйста, попробуйте позже."
        )


async def _do_vehicle_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE, vehicle_number: str, status_msg
) -> None:
    """Execute vehicle number search."""
    driver_info = get_driver_info(update.effective_user)
    
    await status_msg.edit_text(f"🔍 Ищу документы по номеру авто {vehicle_number}...")
    
    yadisk_client = YaDiskClient(
        token=settings.yandex_disk_token, folder_path=settings.yadisk_folder
    )

    try:
        # For vehicle search, we must list all PDF files
        await status_msg.edit_text(
            f"🔍 Получаю список всех документов для поиска авто {vehicle_number}..."
        )
        pdf_files = await yadisk_client.list_all_pdf_files(spec_number=None)
        
        if not pdf_files:
            await status_msg.edit_text("❌ Не найдено документов.")
            return

        await _process_and_send_results(
            update, context, yadisk_client, pdf_files, vehicle_number, "vehicle", status_msg, driver_info
        )
    except Exception as e:
        logger.error(f"Fatal error processing request: {e}")
        await status_msg.edit_text(
            "❌ Произошла ошибка при обработке запроса. "
            "Пожалуйста, попробуйте позже."
        )


async def _process_and_send_results(
    update, context, yadisk_client, pdf_files, search_term, search_mode, status_msg, driver_info
):
    await status_msg.edit_text(
        f"📂 Найдено файлов для проверки: {len(pdf_files)}. Проверяю содержимое..."
    )

    tmp_dir = Path(settings.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    animation_running = True
    dots = [".", "..", "..."]
    dot_index = 0

    async def animate_status():
        nonlocal dot_index
        while animation_running:
            try:
                await status_msg.edit_text(
                    f"📂 Найдено файлов для проверки: {len(pdf_files)}. "
                    f"Проверяю содержимое{dots[dot_index]}"
                )
                dot_index = (dot_index + 1) % len(dots)
                await asyncio.sleep(1)
            except Exception:
                await asyncio.sleep(1)

    animation_task = asyncio.create_task(animate_status())

    results = await asyncio.gather(
        *[
            process_single_pdf(
                yadisk_client, path, i, len(pdf_files),
                search_term, search_mode, tmp_dir, _pdf_semaphore,
            )
            for i, path in enumerate(pdf_files)
        ],
        return_exceptions=True,
    )

    animation_running = False
    animation_task.cancel()
    try:
        await animation_task
    except asyncio.CancelledError:
        pass

    qr_codes = []
    errors = 0
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Error in process_single_pdf: {result}")
            errors += 1
        elif result is not None:
            if result.get("error"):
                errors += 1
            else:
                qr_codes.append(result)

    if not qr_codes:
        if errors > 0:
            await status_msg.edit_text(
                f"❌ Документы по запросу '{search_term}' не найдены. Возникли ошибки ({errors}). Проверьте логи."
            )
        else:
            await status_msg.edit_text(
                f"❌ Документы по запросу '{search_term}' не найдены."
            )
        return

    try:
        for qr_data in qr_codes:
            caption = f"📄 {qr_data['filename']}"
            if qr_data["vehicle_reg"]:
                caption += f"\n🚗 АВТО: {qr_data['vehicle_reg']}"
            if qr_data.get("trailer_reg"):
                caption += f"\n🚛 ПРИЦЕП: {qr_data['trailer_reg']}"

            with open(qr_data["path"], "rb") as qr_file:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=qr_file,
                    caption=caption
                )

        logger.info(f"Sent {len(qr_codes)} QR code(s) for {search_mode} {search_term}")

        first_qr = qr_codes[0]
        await send_notification(
            context=context,
            driver_info=driver_info,
            spec_number=first_qr["filename"],
            vehicle_reg=first_qr["vehicle_reg"],
            trailer_reg=first_qr.get("trailer_reg"),
        )

    except Exception as e:
        logger.error(f"Error sending QR codes: {e}")
        errors += 1

    finally:
        for qr_data in qr_codes:
            try:
                if qr_data["path"].exists():
                    qr_data["path"].unlink()
            except OSError as e:
                logger.warning(f"Failed to delete {qr_data['path']}: {e}")

    summary = f"✅ Готово! Отправлено QR-кодов: {len(qr_codes)}"
    if errors > 0:
        summary += f"\n⚠️ Ошибок при обработке: {errors}"
    await status_msg.edit_text(summary)
