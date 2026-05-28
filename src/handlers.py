"""Telegram bot message handlers."""
import os
import re
from pathlib import Path

from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes
from loguru import logger

from .config import settings
from .yadisk_client import YaDiskClient
from .pdf_processor import extract_text_from_pdf, find_spec_number_in_text, extract_qr_from_pdf


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    welcome_message = (
        "👋 Привет! Я бот для извлечения QR-кодов.\n\n"
        "Просто отправьте мне номер спецификации (например: 47589 или 47589/1), "
        "и я найду соответствующие документы и отправлю вам QR-коды.\n\n"
        "Используйте /help для получения дополнительной информации."
    )
    await update.message.reply_text(welcome_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    help_message = (
        "📖 Как использовать бота:\n\n"
        "1. Отправьте номер спецификации (например: 47589)\n"
        "2. Бот найдет все документы с этим номером\n"
        "3. Вы получите QR-коды из найденных документов\n\n"
        "Примеры:\n"
        "• 47589 - найдет все документы (47589, 47589/1, 47589/2 и т.д.)\n"
        "• 47589/1 - найдет только документ с номером 47589/1\n\n"
        "Если возникли проблемы, проверьте правильность номера спецификации."
    )
    await update.message.reply_text(help_message)


async def handle_spec_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    
    logger.info(f"Request from user {user_id} (@{username}): spec_number={spec_number}")
    
    # Validate input format
    if not re.match(r'^\d+(/\d+)?$', spec_number):
        await update.message.reply_text(
            "❌ Неверный формат номера спецификации.\n"
            "Используйте формат: 47589 или 47589/1"
        )
        return
    
    # Initialize Yandex Disk client
    yadisk_client = YaDiskClient(
        token=settings.yandex_disk_token,
        folder_path=settings.yadisk_folder
    )
    
    # Send status message
    status_msg = await update.message.reply_text("🔍 Ищу документы...")
    
    try:
        # List all PDF files
        pdf_files = await yadisk_client.list_all_pdf_files()
        
        if not pdf_files:
            await status_msg.edit_text("❌ Не найдено документов.")
            return
        
        await status_msg.edit_text(
            f"📂 Найдено документов: {len(pdf_files)}. Проверяю содержимое..."
        )
        
        # Process each PDF file
        qr_codes = []  # List to collect QR codes for media group
        errors = 0
        
        # Ensure temp directory exists
        tmp_dir = Path(settings.tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        
        for index, remote_path in enumerate(pdf_files):
            pdf_filename = Path(remote_path).name
            # Sanitize spec_number for filesystem (replace / with _)
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
                    
                    # Extract QR code with auto-detection
                    try:
                        # Use auto-detection (no manual coordinates needed)
                        await extract_qr_from_pdf(
                            str(local_pdf_path),
                            str(local_qr_path),
                            crop_coords=None,  # Auto-detect QR code with zxing-cpp
                            dpi=settings.qr_dpi
                        )
                    except Exception as e:
                        logger.error(f"Failed to extract QR code: {e}")
                        raise
                    
                    # Add QR code to collection
                    # Simple: find "от XXXXXX" and cut everything after it, remove .pdf
                    match = re.search(r'(.*от\s*\d{6})', pdf_filename)
                    clean_filename = match.group(1) if match else pdf_filename
                    clean_filename = clean_filename.replace('.pdf', '').replace('.PDF', '')
                    
                    qr_codes.append({
                        'path': local_qr_path,
                        'filename': clean_filename
                    })
                else:
                    logger.debug(f"Spec number {spec_number} not found in {pdf_filename}")
                
            except Exception as e:
                logger.error(f"Error processing {pdf_filename}: {e}")
                errors += 1
        
        # Send all QR codes as media group or single message
        if qr_codes:
            try:
                if len(qr_codes) == 1:
                    # Send single QR code
                    with open(qr_codes[0]['path'], 'rb') as qr_file:
                        await update.message.reply_photo(
                            photo=qr_file,
                            caption=f"📄 {qr_codes[0]['filename']}"
                        )
                else:
                    # Send multiple QR codes as media group with captions
                    media_group = []
                    for qr_data in qr_codes:
                        with open(qr_data['path'], 'rb') as qr_file:
                            media_group.append(
                                InputMediaPhoto(
                                    media=qr_file.read(),
                                    caption=f"📄 {qr_data['filename']}"
                                )
                            )
                    
                    await update.message.reply_media_group(media=media_group)
                
                logger.info(f"Sent {len(qr_codes)} QR code(s) for spec {spec_number}")
                
            except Exception as e:
                logger.error(f"Error sending QR codes: {e}")
                errors += 1
        
        # Clean up temporary files
        for qr_data in qr_codes:
            try:
                if qr_data['path'].exists():
                    qr_data['path'].unlink()
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
