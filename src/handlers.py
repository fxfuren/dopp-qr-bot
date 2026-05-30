"""Telegram bot message handlers."""

import asyncio
import re
from pathlib import Path

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from .config import settings
from .pdf_processor import (
    extract_qr_from_pdf,
    extract_text_from_pdf,
    extract_vehicle_registration,
    find_spec_number_in_text,
    extract_spec_number_from_text,
)
from .yadisk_client import YaDiskClient


async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /start command."""
    welcome_message = (
        "👋 Привет! Я бот для извлечения QR-кодов для СПОТ.\n\n"
        "Просто отправьте мне номер СМР (например: 47589 или 47589/1), "
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
        "1. Отправьте номер СМР (например: 47589)\n"
        "2. Бот найдет все документы с этим номером\n"
        "3. Вы получите QR-коды из найденных документов\n\n"
        "Примеры:\n"
        "• 47589 - найдет все документы (47589, 47589/1, 47589/2 и т.д.)\n"
        "• 47589/1 - найдет только документ с номером 47589/1\n\n"
        "Если возникли проблемы, проверьте правильность номера СМР."
    )
    await update.message.reply_text(help_message)


async def handle_spec_number(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle specification number messages.

    Algorithm:
    1. Validate input (must be number or number/number)
    2. List all PDF files from Yandex Disk
    3. For each PDF:
       - Download to temp directory
       - Extract text from first page
       - Check if spec number is in text
       - If found: extract QR code and send to user
       - Clean up temp files
    4. Send summary message
    """
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    spec_number = update.message.text.strip()

    logger.info(
        f"Request from user {user_id} (@{username}): spec_number={spec_number}"
    )

    # Validate input format
    if not re.match(r"^\d+(/\d+)?$", spec_number):
        await update.message.reply_text(
            "❌ Неверный формат номера СМР.\n"
            "Используйте формат: 47589 или 47589/1"
        )
        return

    # Initialize Yandex Disk client
    yadisk_client = YaDiskClient(
        token=settings.yandex_disk_token, folder_path=settings.yadisk_folder
    )

    # Send status message
    status_msg = await update.message.reply_text("🔍 Ищу документы...")

    try:
        # Step 1: Try to find files by spec number in filename (fast)
        pdf_files = await yadisk_client.list_all_pdf_files(spec_number=spec_number)
        
        if not pdf_files:
            # Step 2: If no files found by name, search all files by content (slower but thorough)
            logger.info(f"No files found by filename for {spec_number}, checking all files by content...")
            await status_msg.edit_text(
                f"🔍 Файлы с номером {spec_number} не найдены по названию.\n"
                f"Проверяю содержимое всех документов (может занять время)..."
            )
            pdf_files = await yadisk_client.list_all_pdf_files(spec_number=None)
            
            if not pdf_files:
                await status_msg.edit_text("❌ Не найдено документов.")
                return
        
        await status_msg.edit_text(
            f"📂 Найдено документов: {len(pdf_files)}. Проверяю содержимое..."
        )
        
        # Ensure temp directory exists
        tmp_dir = Path(settings.tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        
        # Process all PDF files in parallel
        async def process_pdf(index: int, remote_path: str):
            """Process a single PDF file."""
            pdf_filename = Path(remote_path).name
            safe_spec_number = spec_number.replace('/', '_')
            local_pdf_path = tmp_dir / f"{safe_spec_number}_{index}.pdf"
            local_qr_path = tmp_dir / f"{safe_spec_number}_{index}_qr.png"
            
            try:
                # Download PDF
                logger.debug(f"Processing file {index + 1}/{len(pdf_files)}: {pdf_filename}")
                await yadisk_client.download_file(remote_path, str(local_pdf_path))
                
                # Extract text from all pages to find spec number
                text = await extract_text_from_pdf(str(local_pdf_path), page_number=None)
                
                # Check if spec number is in text
                if find_spec_number_in_text(text, spec_number):
                    logger.info(f"Spec number {spec_number} found in {pdf_filename}")
                    
                    # Extract actual spec number from text
                    actual_spec_number = extract_spec_number_from_text(text)
                    display_spec = actual_spec_number if actual_spec_number else spec_number
                    
                    # Extract vehicle registration number
                    vehicle_reg = extract_vehicle_registration(text)
                    
                    # Extract QR code with auto-detection
                    try:
                        await extract_qr_from_pdf(
                            str(local_pdf_path),
                            str(local_qr_path),
                            crop_coords=None,
                            dpi=settings.qr_dpi
                        )
                    except Exception as e:
                        logger.error(f"Failed to extract QR code: {e}")
                        raise
                    
                    return {
                        'path': local_qr_path,
                        'filename': display_spec,
                        'vehicle_reg': vehicle_reg
                    }
                else:
                    logger.debug(f"Spec number {spec_number} not found in {pdf_filename}")
                    return None
                
            except Exception as e:
                logger.error(f"Error processing {pdf_filename}: {e}")
                return {'error': True}
        
        # Process all PDFs concurrently
        results = await asyncio.gather(
            *[process_pdf(i, path) for i, path in enumerate(pdf_files)],
            return_exceptions=True
        )
        
        # Collect successful QR codes and count errors
        qr_codes = []
        errors = 0
        
        for result in results:
            if isinstance(result, Exception):
                errors += 1
            elif result is not None:
                if result.get('error'):
                    errors += 1
                else:
                    qr_codes.append(result)

        # Send each QR code as a separate message
        if qr_codes:
            try:
                for qr_data in qr_codes:
                    caption = f"📄 {qr_data['filename']}"
                    if qr_data["vehicle_reg"]:
                        caption += f"\n🚗 АВТО: {qr_data['vehicle_reg']}"

                    with open(qr_data["path"], "rb") as qr_file:
                        await update.message.reply_photo(
                            photo=qr_file, caption=caption
                        )

                logger.info(
                    f"Sent {len(qr_codes)} QR code(s) for spec {spec_number}"
                )

            except Exception as e:
                logger.error(f"Error sending QR codes: {e}")
                errors += 1

        # Clean up temporary files
        for qr_data in qr_codes:
            try:
                if qr_data["path"].exists():
                    qr_data["path"].unlink()
            except Exception as e:
                logger.warning(f"Failed to delete {qr_data['path']}: {e}")

        # Send summary message
        if not qr_codes:
            await status_msg.edit_text(
                f"❌ Спецификация {spec_number} не найдена в документах."
            )
        else:
            summary = f"✅ Готово! Отправлено QR-кодов: {len(qr_codes)}"
            if errors > 0:
                summary += f"\n⚠️ Ошибок при обработке: {errors}"
            await status_msg.edit_text(summary)

        logger.info(
            f"Request completed: spec_number={spec_number}, "
            f"sent={len(qr_codes)}, errors={errors}"
        )

    except Exception as e:
        logger.error(f"Fatal error processing request: {e}")
        await status_msg.edit_text(
            "❌ Произошла ошибка при обработке запроса. "
            "Пожалуйста, попробуйте позже."
        )
