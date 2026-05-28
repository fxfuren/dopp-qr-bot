"""Main entry point for the Telegram bot."""
import sys
from pathlib import Path

from loguru import logger
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from .config import settings
from .handlers import start_command, help_command, handle_spec_number


def setup_logging():
    """Configure loguru logging."""
    logger.remove()  # Remove default handler
    
    # Add console handler with custom format
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=settings.log_level,
        colorize=True
    )
    
    # Add file handler with rotation
    log_dir = Path(settings.tmp_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    logger.add(
        log_dir / "bot.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level=settings.log_level,
        rotation="10 MB",
        retention="7 days",
        compression="zip"
    )
    
    logger.info("Logging configured")


def main():
    """Initialize and run the bot."""
    # Setup logging
    setup_logging()
    
    logger.info("Starting Telegram bot...")
    logger.info(f"Yandex Disk folder: {settings.yadisk_folder}")
    logger.info(f"Temp directory: {settings.tmp_dir}")
    logger.info(f"QR DPI: {settings.qr_dpi}")
    
    # Create temp directory if it doesn't exist
    tmp_dir = Path(settings.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Temp directory created: {tmp_dir}")
    
    # Create application
    application = Application.builder().token(settings.bot_token).build()
    
    # Register command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Register message handler for specification numbers
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_spec_number)
    )
    
    logger.info("Handlers registered")
    
    # Start the bot
    logger.info("Bot is running. Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
