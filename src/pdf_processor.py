"""PDF processing module for extracting text and QR codes."""
import asyncio
import re
import os
from pathlib import Path
from typing import Optional, Tuple

import pdfplumber
import fitz  # PyMuPDF
from PIL import Image
import zxingcpp
from loguru import logger


def find_qr_code_coordinates(img: Image.Image, dpi: int = 150) -> Optional[Tuple[int, int, int, int]]:
    """Automatically find QR code coordinates using zxing-cpp."""
    try:
        results = zxingcpp.read_barcodes(img)
        if not results:
            logger.warning("zxing-cpp: QR code not found in image")
            return None

        pos = results[0].position
        padding = 10
        coords = (
            max(0, min(pos.top_left.x, pos.bottom_left.x) - padding),
            max(0, min(pos.top_left.y, pos.top_right.y) - padding),
            min(img.width, max(pos.top_right.x, pos.bottom_right.x) + padding),
            min(img.height, max(pos.bottom_left.y, pos.bottom_right.y) + padding),
        )
        logger.debug(f"zxing-cpp: QR found at {coords}")
        return coords

    except Exception as e:
        logger.error(f"Error finding QR code: {e}")
        return None


async def extract_text_from_pdf(pdf_path: str, page_number: int = None) -> str:
    """
    Extract text from a specific page or all pages of a PDF file.
    
    Args:
        pdf_path: Path to the PDF file
        page_number: Page number to extract (1-indexed). If None, extracts all pages.
        
    Returns:
        Extracted text as string
        
    Raises:
        Exception: If PDF cannot be read or page doesn't exist
    """
    def _extract():
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if page_number is not None:
                    # Extract single page
                    if page_number > len(pdf.pages):
                        raise ValueError(f"Page {page_number} does not exist in PDF")
                    
                    page = pdf.pages[page_number - 1]  # pdfplumber uses 0-indexed pages
                    text = page.extract_text() or ""
                else:
                    # Extract all pages
                    texts = []
                    for page in pdf.pages:
                        page_text = page.extract_text() or ""
                        texts.append(page_text)
                    text = "\n".join(texts)
                
                return text
        except Exception as e:
            logger.error(f"Error extracting text from {pdf_path}: {e}")
            raise
    
    # Run in thread pool to avoid blocking
    return await asyncio.to_thread(_extract)


def find_spec_number_in_text(text: str, search_number: str) -> bool:
    """
    Search for specification number in text.
    
    Supports flexible matching:
    - Search "47589" matches: "47589", "47589/1", "47589/2", "№47589", "№ 47589"
    - Search "47589/1" matches: "47589", "47589/1", "47589/2" (all variants with base number)
    
    Args:
        text: Text to search in
        search_number: Specification number to search for (e.g., "47589" or "47589/1")
        
    Returns:
        True if specification number is found, False otherwise
    """
    if not text or not search_number:
        return False
    
    # Extract base number (before /)
    base_number = search_number.split('/')[0]
    
    # Escape special regex characters in base_number
    escaped_number = re.escape(base_number)
    
    # Pattern: optional "№", optional spaces, the base number, optional "/digit" suffix
    # This will match all variants with the same base number
    pattern = rf"№?\s*{escaped_number}(?:/\d+)?\b"
    
    match = re.search(pattern, text, re.IGNORECASE)
    return match is not None


def extract_spec_number_from_text(text: str) -> Optional[str]:
    """
    Extract specification number from PDF text.
    
    Looks for pattern: "6.1А НОМЕР:47589" or similar.
    
    Args:
        text: Text extracted from PDF
        
    Returns:
        Specification number or None if not found
    """
    if not text:
        return None
    
    # Pattern to find specification number
    # Example: "6.1А НОМЕР:47589" or "6.1А НОМЕР: 47589"
    pattern = r'6\.1[АA]\s*НОМЕР:\s*(\d+(?:/\d+)?)'
    
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        spec_number = match.group(1).strip()
        logger.debug(f"Found spec number in text: {spec_number}")
        return spec_number
    
    logger.debug("Spec number not found in text")
    return None


def extract_vehicle_registration(text: str) -> Optional[str]:
    """
    Extract vehicle registration number from PDF text.
    
    Looks for pattern: "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК" followed by the registration number.
    
    Args:
        text: Text extracted from PDF
        
    Returns:
        Vehicle registration number or None if not found
    """
    if not text:
        return None
    
    # Pattern: Find the section with vehicle registration
    # Example:
    # "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК 4.1Б НОМЕР ПРИЦЕПА
    #  BA 5118 5 A 1295 K 5"
    pattern = r'4\.1[АA]\s*АВТО:\s*РЕГИСТРАЦИОННЫЙ\s+ЗНАК\s+4\.1[БB]\s*НОМЕР\s+ПРИЦЕПА\s*[\n\s]+([A-Z0-9\s]+?)(?:\n|$)'
    
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if match:
        full_line = match.group(1).strip()
        # Split by whitespace and take first 3 parts as vehicle registration
        # Format: "BA 5118 5" (vehicle) "A 1295 K 5" (trailer)
        parts = full_line.split()
        if len(parts) >= 3:
            # Take first 3 parts as vehicle registration number
            reg_number = ' '.join(parts[:3])
            logger.debug(f"Found vehicle registration: {reg_number}")
            return reg_number
        elif parts:
            # If less than 3 parts, return what we have
            reg_number = ' '.join(parts)
            logger.debug(f"Found vehicle registration: {reg_number}")
            return reg_number
    
    logger.debug("Vehicle registration number not found in text")
    return None


async def extract_qr_from_pdf(
    pdf_path: str,
    output_path: str,
    crop_coords: tuple[int, int, int, int] = None,
    dpi: int = 150
) -> str:
    """
    Extract QR code from the first page of a PDF file using PyMuPDF.
    Automatically finds QR code if coordinates not provided.
    
    Args:
        pdf_path: Path to the PDF file
        output_path: Path to save the extracted QR code image
        crop_coords: Optional tuple of (left, top, right, bottom) coordinates for cropping.
                     If None, will automatically detect QR code location.
        dpi: DPI resolution for PDF rendering (default: 150)
        
    Returns:
        Path to the saved QR code image
        
    Raises:
        Exception: If PDF cannot be converted or QR code not found
    """
    def _extract():
        try:
            # Open PDF with PyMuPDF
            doc = fitz.open(pdf_path)
            page = doc[0]  # First page
            
            # Convert to image at specified DPI
            zoom = dpi / 72  # 72 is default DPI
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            
            # Convert to PIL Image
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # Use provided coordinates or auto-detect
            if crop_coords is None:
                logger.info("Auto-detecting QR code location...")
                coords = find_qr_code_coordinates(img, dpi)
                if coords is None:
                    raise ValueError("Could not automatically locate QR code in PDF")
                left, top, right, bottom = coords
            else:
                left, top, right, bottom = crop_coords
            
            # Crop the QR code area
            qr_image = img.crop((left, top, right, bottom))
            
            # Save the cropped image
            qr_image.save(output_path, "PNG")
            logger.debug(f"QR code extracted and saved to {output_path}")
            
            doc.close()
            return output_path
            
        except Exception as e:
            logger.error(f"Error extracting QR from {pdf_path}: {e}")
            raise
    
    # Run in thread pool to avoid blocking
    return await asyncio.to_thread(_extract)
