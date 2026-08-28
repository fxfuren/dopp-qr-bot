"""Yandex Disk client for file operations."""
import asyncio
from pathlib import Path
import re
from typing import List

import yadisk
from loguru import logger

# Retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds


def _is_retryable_error(error: Exception) -> bool:
    """Check if error is transient and worth retrying."""
    # Network/connection errors
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True
    # yadisk internal errors that may be transient
    if isinstance(error, yadisk.exceptions.InternalServerError):
        return True
    # requests library errors
    try:
        import requests
        if isinstance(error, (requests.ConnectionError, requests.Timeout)):
            return True
    except ImportError:
        pass
    return False


async def _retry_async(func, *args, operation_name: str = "operation", **kwargs):
    """Execute an async function with retries and exponential backoff."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if not _is_retryable_error(e) or attempt == MAX_RETRIES:
                raise
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                f"{operation_name} failed (attempt {attempt}/{MAX_RETRIES}): {e}. "
                f"Retrying in {delay:.1f}s..."
            )
            await asyncio.sleep(delay)
    raise last_error


class YaDiskClient:
    """Client for interacting with Yandex Disk API."""
    
    def __init__(self, token: str, folder_path: str):
        """
        Initialize Yandex Disk client.
        
        Args:
            token: OAuth token for Yandex Disk
            folder_path: Path to the folder containing PDF files
        """
        self.client = yadisk.YaDisk(token=token)
        self.folder_path = folder_path
        logger.info(f"YaDisk client initialized for folder: {folder_path}")
    
    async def list_all_pdf_files(self, spec_number: str = None) -> List[dict]:
        """
        List all PDF files in the configured folder and subfolders,
        optionally filtered by spec number.
        
        Args:
            spec_number: Optional specification number to filter files by filename
        
        Returns:
            List of dicts with file info (path, modified, name)
        """
        async def _do_list():
            def _list_files_recursive(folder_path: str) -> List[dict]:
                """Recursively list all PDF files in folder and subfolders."""
                pdf_files = []
                try:
                    items = list(self.client.listdir(folder_path))
                    for item in items:
                        if item.type == "file" and item.name.lower().endswith(".pdf"):
                            file_info = {
                                'path': item.path,
                                'modified': item.modified.isoformat() if hasattr(item, 'modified') and item.modified else '',
                                'name': item.name
                            }
                            if spec_number:
                                if self._filename_matches_spec(item.name, spec_number):
                                    pdf_files.append(file_info)
                            else:
                                pdf_files.append(file_info)
                        elif item.type == "dir":
                            pdf_files.extend(_list_files_recursive(item.path))
                except Exception as e:
                    logger.error(f"Error listing directory {folder_path}: {e}")
                    raise
                return pdf_files

            def _list_files():
                if not self.client.check_token():
                    raise yadisk.exceptions.UnauthorizedError("Invalid Yandex Disk token")
                pdf_files = _list_files_recursive(self.folder_path)
                logger.info(f"Found {len(pdf_files)} PDF files in {self.folder_path}")
                return pdf_files

            return await asyncio.to_thread(_list_files)

        try:
            return await _retry_async(
                _do_list, operation_name="list_pdf_files"
            )
        except yadisk.exceptions.UnauthorizedError:
            logger.error("Authorization error: Invalid Yandex Disk token")
            raise
        except yadisk.exceptions.PathNotFoundError:
            logger.error(f"Folder not found: {self.folder_path}")
            raise
        except Exception as e:
            logger.error(f"Error while listing files: {e}")
            raise
    
    def _filename_matches_spec(self, filename: str, spec_number: str) -> bool:
        """
        Check if filename contains the specification number.
        """
        base_number = spec_number.split('/')[0]
        escaped_number = re.escape(base_number)
        pattern = rf'{escaped_number}(?:/\d+)?'
        return re.search(pattern, filename) is not None
    
    async def download_file(self, remote_path: str, local_path: str) -> None:
        """
        Download a file from Yandex Disk with retry logic.
        """
        async def _do_download():
            def _download():
                self.client.download(remote_path, local_path)
                logger.debug(f"Downloaded {remote_path} to {local_path}")
            return await asyncio.to_thread(_download)

        try:
            await _retry_async(
                _do_download, operation_name=f"download({Path(remote_path).name})"
            )
        except yadisk.exceptions.PathNotFoundError:
            logger.error(f"File not found on Yandex Disk: {remote_path}")
            raise
        except Exception as e:
            logger.error(f"Error while downloading {remote_path}: {e}")
            raise

    async def list_top_level_folders(self) -> list[str]:
        """
        Return the names of all direct subdirectories of self.folder_path.
        """
        async def _do_list():
            def _list():
                items = list(self.client.listdir(self.folder_path))
                return [item.name for item in items if item.type == "dir"]
            return await asyncio.to_thread(_list)

        try:
            return await _retry_async(_do_list, operation_name="list_top_level_folders")
        except Exception as e:
            logger.error(f"Error listing top-level folders in {self.folder_path}: {e}")
            raise

    async def upload_file(self, local_path: str, remote_path: str) -> None:
        """
        Upload a local file to Yandex Disk with retry logic.

        Args:
            local_path: Absolute path to the local file to upload.
            remote_path: Destination path on Yandex Disk (must include filename).
        """
        async def _do_upload():
            def _upload():
                self.client.upload(local_path, remote_path, overwrite=True)
                logger.debug(f"Uploaded {local_path} to {remote_path}")
            return await asyncio.to_thread(_upload)

        try:
            await _retry_async(
                _do_upload, operation_name=f"upload({Path(local_path).name})"
            )
        except Exception as e:
            logger.error(f"Error while uploading {local_path} to {remote_path}: {e}")
            raise
