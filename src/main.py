"""Main entry point for the Telegram bot."""
import os
import sys
import signal
import asyncio
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
    
    # Add file handler with rotation in /tmp (writable in read-only container)
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


async def heartbeat_task():
    """Update heartbeat file periodically for healthcheck."""
    heartbeat_file = Path(settings.tmp_dir) / "heartbeat"
    while True:
        try:
            heartbeat_file.touch()
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"Heartbeat update failed: {e}")
            await asyncio.sleep(60)


async def post_init(application: Application) -> None:
    """Post-initialization hook to start background tasks."""
    # Start heartbeat task
    asyncio.create_task(heartbeat_task())
    logger.info("Heartbeat task started")


async def post_shutdown(application: Application) -> None:
    """Post-shutdown hook for cleanup."""
    logger.info("Cleaning up resources...")
    # Remove PID file
    pid_file = Path(settings.tmp_dir) / "bot.pid"
    if pid_file.exists():
        pid_file.unlink()
    logger.info("Cleanup completed")


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
    
    # Write PID file for healthcheck
    pid_file = tmp_dir / "bot.pid"
    pid_file.write_text(str(os.getpid()))
    logger.info(f"PID file created: {pid_file}")
    
    # Setup graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Create application
    application = Application.builder().token(settings.bot_token).build()
    
    # Register lifecycle hooks
    application.post_init = post_init
    application.post_shutdown = post_shutdown
    
    # Register command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Register message handler for specification numbers (private chats only)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_spec_number)
    )
    
    logger.info("Handlers registered")
    
    # Start the bot
    logger.info("Bot is running. Press Ctrl+C to stop.")
    try:
        application.run_polling(allowed_updates=["message"], close_loop=False)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        logger.info("Bot stopped")


if __name__ == "__main__":
    main()
