"""Yandex Disk client for file operations."""
import asyncio
import re
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
    
    async def list_all_pdf_files(self, spec_number: str = None) -> List[str]:
        """
        List all PDF files in the configured folder and subfolders, optionally filtered by spec number.
        
        Args:
            spec_number: Optional specification number to filter files by filename
        
        Returns:
            List of full paths to PDF files
            
        Raises:
            yadisk.exceptions.UnauthorizedError: If token is invalid
            yadisk.exceptions.PathNotFoundError: If folder doesn't exist
        """
        def _list_files_recursive(folder_path: str) -> List[str]:
            """Recursively list all PDF files in folder and subfolders."""
            pdf_files = []
            
            try:
                items = list(self.client.listdir(folder_path))
                
                for item in items:
                    if item.type == "file" and item.name.lower().endswith(".pdf"):
                        # If spec_number provided, filter by filename
                        if spec_number:
                            if self._filename_matches_spec(item.name, spec_number):
                                pdf_files.append(item.path)
                        else:
                            pdf_files.append(item.path)
                    elif item.type == "dir":
                        # Recursively search in subdirectories
                        pdf_files.extend(_list_files_recursive(item.path))
                        
            except Exception as e:
                logger.warning(f"Error listing folder {folder_path}: {e}")
            
            return pdf_files
        
        def _list_files():
            try:
                # Check if client is authorized
                if not self.client.check_token():
                    raise yadisk.exceptions.UnauthorizedError("Invalid Yandex Disk token")
                
                # Recursively list all PDF files
                pdf_files = _list_files_recursive(self.folder_path)
                
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
    
    def _filename_matches_spec(self, filename: str, spec_number: str) -> bool:
        """
        Check if filename contains the specification number.
        
        Args:
            filename: PDF filename to check
            spec_number: Specification number to search for (e.g., "47589" or "47589/1")
        
        Returns:
            True if filename contains the spec number
        """
        # Extract base number (before /)
        base_number = spec_number.split('/')[0]
        
        # Escape special regex characters
        escaped_number = re.escape(base_number)
        
        # Pattern: look for the base number with optional "/digit" suffix in filename
        # Example: "ДОПП 47589 от 123456.pdf" or "ДОПП 47589/1 от 123456.pdf"
        pattern = rf'{escaped_number}(?:/\d+)?'
        
        match = re.search(pattern, filename)
        return match is not None
    
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
