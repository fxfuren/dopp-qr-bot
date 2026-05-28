"""Yandex Disk client for file operations."""
import asyncio
from typing import List

import yadisk
from loguru import logger


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
    
    async def list_all_pdf_files(self) -> List[str]:
        """
        List all PDF files in the configured folder.
        
        Returns:
            List of full paths to PDF files
            
        Raises:
            yadisk.exceptions.UnauthorizedError: If token is invalid
            yadisk.exceptions.PathNotFoundError: If folder doesn't exist
        """
        def _list_files():
            try:
                # Check if client is authorized
                if not self.client.check_token():
                    raise yadisk.exceptions.UnauthorizedError("Invalid Yandex Disk token")
                
                # List all items in the folder
                items = list(self.client.listdir(self.folder_path))
                
                # Filter only PDF files (optionally filter by "ДОПП" prefix)
                pdf_files = []
                for item in items:
                    if item.type == "file" and item.name.lower().endswith(".pdf"):
                        # Optionally filter by prefix
                        if item.name.startswith("ДОПП"):
                            pdf_files.append(item.path)
                
                logger.info(f"Found {len(pdf_files)} PDF files in {self.folder_path}")
                return pdf_files
                
            except yadisk.exceptions.UnauthorizedError as e:
                logger.error(f"Authorization error: {e}")
                raise
            except yadisk.exceptions.PathNotFoundError as e:
                logger.error(f"Folder not found: {self.folder_path}")
                raise
            except Exception as e:
                logger.error(f"Error while listing files: {e}")
                raise
        
        # Run in thread pool to avoid blocking
        return await asyncio.to_thread(_list_files)
    
    async def download_file(self, remote_path: str, local_path: str) -> None:
        """
        Download a file from Yandex Disk.
        
        Args:
            remote_path: Path to the file on Yandex Disk
            local_path: Local path where the file should be saved
            
        Raises:
            yadisk.exceptions.PathNotFoundError: If remote file doesn't exist
        """
        def _download():
            try:
                self.client.download(remote_path, local_path)
                logger.debug(f"Downloaded {remote_path} to {local_path}")
            except yadisk.exceptions.PathNotFoundError as e:
                logger.error(f"File not found on Yandex Disk: {remote_path}")
                raise
            except Exception as e:
                logger.error(f"Error while downloading {remote_path}: {e}")
                raise
        
        # Run in thread pool to avoid blocking
        await asyncio.to_thread(_download)
