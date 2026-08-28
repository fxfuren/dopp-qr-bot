"""Telegram bot message handlers."""

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from rapidfuzz import fuzz, process as fuzz_process
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .config import settings
from .pdf_processor import (
    extract_org_and_supplier,
    find_vehicle_in_text,
    extract_all_spec_numbers_from_text,
    extract_qr_from_pdf,
    extract_spec_number_from_text,
    extract_text_from_pdf,
    extract_vehicle_registration,
    find_spec_number_in_text,
    normalize_chars,
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


def _fuzzy_match_folder(query: str | None, folders: list[str]) -> str | None:
    """
    Fuzzy-match *query* against *folders* using rapidfuzz.

    Both query and folder names are normalised (normalize_chars + upper + strip)
    before comparison.  Returns the **original** (un-normalised) folder name of
    the best match, or None if query is empty / score below threshold.
    """
    if not query or not folders:
        return None

    norm_query = normalize_chars(query.upper().strip())
    norm_folders = [normalize_chars(f.upper().strip()) for f in folders]

    hit = fuzz_process.extractOne(
        norm_query,
        norm_folders,
        scorer=fuzz.WRatio,
        score_cutoff=75,
    )
    if hit is None:
        return None

    _match, _score, idx = hit
    return folders[idx]


async def handle_group_pdf(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle a PDF document sent to a group chat.

    Upload happens ONLY when a single unambiguous target folder is found.
    Any failure (no text, no fields, no folder match, ambiguous match,
    YaDisk error) aborts the upload and replies with an explicit error.

    Decision matrix:
      - Text empty / unreadable          → abort, reply error
      - Both org and supplier are None   → abort, reply error
      - YaDisk folder listing fails      → abort, reply error
      - 0 fields matched any folder      → abort, reply error
      - 2 fields matched DIFFERENT folders → abort, reply error
      - 1 field matched OR both matched SAME folder → upload, cache, reply ok
    """
    message = update.message
    if message is None or message.document is None:
        return

    doc = message.document
    filename = doc.file_name or f"upload_{doc.file_id}.pdf"

    logger.info(
        f"Group PDF received: {filename!r} "
        f"(chat={message.chat_id}, from={message.from_user and message.from_user.id})"
    )

    tmp_dir = Path(settings.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    local_pdf_path = tmp_dir / filename

    async def _reply_error(text: str) -> None:
        await message.reply_text(text, reply_to_message_id=message.message_id)

    async with _pdf_semaphore:
        try:
            # ── 1. Download from Telegram ─────────────────────────────────
            tg_file = await context.bot.get_file(doc.file_id)
            await tg_file.download_to_drive(str(local_pdf_path))
            logger.debug(f"Downloaded Telegram PDF to {local_pdf_path}")

            # ── 2. Extract text ───────────────────────────────────────────
            text = await extract_text_from_pdf(str(local_pdf_path))
            if not text or not text.strip():
                logger.warning(f"No text extracted from {filename!r} (scanned/image PDF?)")
                await _reply_error(
                    f"❌ Не удалось извлечь текст из файла `{filename}`.\n"
                    "Возможно, это отсканированный документ без текстового слоя.\n"
                    "Файл не загружен."
                )
                return

            # ── 3. Parse org / supplier fields ────────────────────────────
            fields = extract_org_and_supplier(text)
            org_raw = fields["org"]
            supplier_raw = fields["supplier"]
            logger.info(f"Extracted: org={org_raw!r}, supplier={supplier_raw!r}")

            if org_raw is None and supplier_raw is None:
                logger.warning(f"Fields 1А and 2А not found in {filename!r}")
                await _reply_error(
                    f"❌ В документе `{filename}` не найдены поля\n"
                    "«1А НАИМЕНОВАНИЕ ОРГАНИЗАЦИИ» и «2А НАИМЕНОВАНИЕ ПОСТАВЩИКА».\n"
                    "Файл не загружен."
                )
                return

            # ── 4. List Yandex Disk top-level folders ─────────────────────
            yadisk_client = YaDiskClient(
                token=settings.yandex_disk_token,
                folder_path=settings.yadisk_folder,
            )
            try:
                folders = await yadisk_client.list_top_level_folders()
            except Exception as disk_err:
                logger.error(f"Failed to list YaDisk folders: {disk_err}")
                await _reply_error(
                    f"❌ Не удалось получить список папок на Яндекс Диске.\n"
                    "Файл не загружен."
                )
                return

            logger.debug(f"YaDisk top-level folders ({len(folders)}): {folders}")

            # ── 5. Fuzzy matching ─────────────────────────────────────────
            org_folder = _fuzzy_match_folder(org_raw, folders)
            supplier_folder = _fuzzy_match_folder(supplier_raw, folders)
            logger.info(f"Folder match: org→{org_folder!r}, supplier→{supplier_folder!r}")

            def _norm(f: str | None) -> str | None:
                return normalize_chars(f.upper().strip()) if f else None

            org_norm = _norm(org_folder)
            sup_norm = _norm(supplier_folder)

            # ── 6. Decision: exactly one target or abort ──────────────────
            if org_folder and supplier_folder and org_norm != sup_norm:
                # Both fields matched DIFFERENT folders — ambiguous
                logger.warning(
                    f"Ambiguous folder match for {filename!r}: "
                    f"org→{org_folder!r}, supplier→{supplier_folder!r}"
                )
                await _reply_error(
                    f"❌ Невозможно определить папку для `{filename}`:\n"
                    f"поле «Организация» указывает на папку «{org_folder}»,\n"
                    f"поле «Поставщик» указывает на папку «{supplier_folder}».\n"
                    "Файл не загружен. Загрузите вручную."
                )
                return

            # Pick the matched folder (at least one is set, or both same)
            target_folder = org_folder or supplier_folder
            if target_folder is None:
                # Neither field matched any folder → upload to root with warning
                matched_values = []
                if org_raw:
                    matched_values.append(f"«{org_raw}»")
                if supplier_raw:
                    matched_values.append(f"«{supplier_raw}»")
                values_str = " и ".join(matched_values)
                logger.warning(f"No folder match for {filename!r}: extracted {values_str}, uploading to root")
                upload_to_root = True
            else:
                upload_to_root = False

            # ── 7. Upload ─────────────────────────────────────────────────
            base = settings.yadisk_folder.rstrip("/")
            if upload_to_root:
                remote_path = f"{base}/{filename}"
            else:
                remote_path = f"{base}/{target_folder}/{filename}"

            try:
                await yadisk_client.upload_file(str(local_pdf_path), remote_path)
                logger.info(f"Uploaded {filename!r} → {remote_path}")
            except Exception as upload_err:
                logger.error(f"YaDisk upload failed for {filename!r}: {upload_err}")
                await _reply_error(
                    f"❌ Ошибка при загрузке `{filename}` на Яндекс Диск.\n"
                    "Попробуйте позже или загрузите вручную."
                )
                return

            # ── 8. Index into PDF cache ───────────────────────────────────
            try:
                from .cache import PDFCache
                from datetime import datetime, timezone
                cache = PDFCache(str(Path(settings.data_dir) / "pdf_cache.db"))
                modified = datetime.now(timezone.utc).isoformat()

                all_spec_numbers = extract_all_spec_numbers_from_text(text)
                vehicle_info = extract_vehicle_registration(text)
                vehicle_reg = vehicle_info.get("vehicle") if vehicle_info else None
                trailer_reg = vehicle_info.get("trailer") if vehicle_info else None

                await cache.save_pdf_cache(
                    remote_path, modified, text, all_spec_numbers, vehicle_reg, trailer_reg
                )
                logger.info(f"Cached {remote_path!r} (specs={all_spec_numbers})")
            except Exception as cache_err:
                logger.error(f"Cache indexing failed (non-fatal): {cache_err}")

            # ── 9. Success reply ──────────────────────────────────────────
            if upload_to_root:
                await message.reply_text(
                    f"⚠️ Файл `{filename}` загружен в корень.\n"
                    f"_Подходящая папка на Яндекс Диске не найдена._",
                    reply_to_message_id=message.message_id,
                    parse_mode="Markdown",
                )
            else:
                # Show which field(s) led to the folder decision
                match_source = []
                if org_folder and _norm(org_folder) == _norm(target_folder):
                    match_source.append("организация")
                if supplier_folder and _norm(supplier_folder) == _norm(target_folder):
                    match_source.append("поставщик")
                source_str = ", ".join(match_source)
                await message.reply_text(
                    f"✅ Файл `{filename}` загружен в папку `{target_folder}`.\n"
                    f"_Совпадение по: {source_str}_",
                    reply_to_message_id=message.message_id,
                    parse_mode="Markdown",
                )

        except Exception as e:
            logger.exception(f"Unhandled error in handle_group_pdf: {e}")
            await _reply_error("❌ Внутренняя ошибка при обработке файла. Файл не загружен.")

        finally:
            try:
                if local_pdf_path.exists():
                    local_pdf_path.unlink()
                    logger.debug(f"Cleaned up temp PDF: {local_pdf_path}")
            except OSError as e:
                logger.warning(f"Failed to delete temp PDF {local_pdf_path}: {e}")


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
    """Execute vehicle number search — returns ALL matches within last 30 days."""
    driver_info = get_driver_info(update.effective_user)

    await status_msg.edit_text(f"🔍 Ищу документы по номеру авто {vehicle_number}...")

    yadisk_client = YaDiskClient(
        token=settings.yandex_disk_token, folder_path=settings.yadisk_folder
    )

    try:
        from .cache import PDFCache
        cache = PDFCache(str(Path(settings.data_dir) / 'pdf_cache.db'))

        # ── Fast path: look up cache for all entries within 30 days ───────
        cached_entries = await cache.get_cached_by_vehicle(vehicle_number, days=30)

        if cached_entries:
            logger.info(
                f"Vehicle search cache hit: {len(cached_entries)} entries "
                f"for {vehicle_number} within 30 days"
            )
            await status_msg.edit_text(
                f"🔍 Найдено {len(cached_entries)} документов за последние 30 дней. "
                "Извлекаю QR-коды..."
            )
            await _process_cached_vehicle_results(
                update, context, yadisk_client, cached_entries,
                vehicle_number, status_msg, driver_info
            )
            return

        # ── Slow path: scan all files on Yandex Disk ──────────────────────
        logger.info(f"Vehicle {vehicle_number} not in cache, scanning YaDisk...")
        await status_msg.edit_text(
            f"🔍 В кэше не найдено. "
            f"Получаю список всех документов для поиска авто {vehicle_number}..."
        )
        pdf_files = await yadisk_client.list_all_pdf_files(spec_number=None)

        if not pdf_files:
            await status_msg.edit_text("❌ Не найдено документов.")
            return

        await _process_and_send_results(
            update, context, yadisk_client, pdf_files, vehicle_number, "vehicle", status_msg, driver_info
        )
    except Exception as e:
        logger.error(f"Fatal error processing vehicle request: {e}")
        await status_msg.edit_text(
            "❌ Произошла ошибка при обработке запроса. "
            "Пожалуйста, попробуйте позже."
        )


async def _process_cached_vehicle_results(
    update, context, yadisk_client: YaDiskClient,
    cached_entries: list[dict], vehicle_number: str, status_msg, driver_info: str
) -> None:
    """
    For each cached vehicle entry download the PDF (needed for QR extraction)
    and send all found QR codes. Already have text/meta from cache.
    """
    tmp_dir = Path(settings.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    qr_codes = []
    errors = 0

    for idx, entry in enumerate(cached_entries):
        remote_path = entry['remote_path']
        pdf_name = Path(remote_path).name
        safe_name = vehicle_number.replace('/', '_').replace(' ', '_')
        local_pdf_path = tmp_dir / f"v_{safe_name}_{idx}.pdf"
        local_qr_path = tmp_dir / f"v_{safe_name}_{idx}_qr.png"

        async with _pdf_semaphore:
            try:
                await yadisk_client.download_file(remote_path, str(local_pdf_path))
                await extract_qr_from_pdf(
                    str(local_pdf_path),
                    str(local_qr_path),
                    crop_coords=None,
                    dpi=settings.qr_dpi,
                )
                spec_numbers = entry['spec_numbers']
                display_spec = ", ".join(spec_numbers) if spec_numbers else pdf_name
                qr_codes.append({
                    "path": local_qr_path,
                    "filename": display_spec,
                    "vehicle_reg": entry['vehicle_reg'],
                    "trailer_reg": entry['trailer_reg'],
                })
                logger.info(f"QR extracted from cached {pdf_name}")
            except Exception as e:
                logger.error(f"Error extracting QR from cached {pdf_name}: {e}")
                errors += 1
            finally:
                try:
                    if local_pdf_path.exists():
                        local_pdf_path.unlink()
                except OSError:
                    pass

    if not qr_codes:
        await status_msg.edit_text(
            f"❌ Не удалось извлечь QR-коды для авто {vehicle_number}."
        )
        return

    # Send all QR codes
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
                    caption=caption,
                )

        logger.info(f"Sent {len(qr_codes)} QR code(s) for vehicle {vehicle_number}")

        first_qr = qr_codes[0]
        await send_notification(
            context=context,
            driver_info=driver_info,
            spec_number=first_qr["filename"],
            vehicle_reg=first_qr["vehicle_reg"],
            trailer_reg=first_qr.get("trailer_reg"),
        )
    except Exception as e:
        logger.error(f"Error sending vehicle QR codes: {e}")
        errors += 1
    finally:
        for qr_data in qr_codes:
            try:
                if qr_data["path"].exists():
                    qr_data["path"].unlink()
            except OSError:
                pass

    summary = f"✅ Готово! Отправлено QR-кодов: {len(qr_codes)}"
    if errors > 0:
        summary += f"\n⚠️ Ошибок: {errors}"
    await status_msg.edit_text(summary)


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
