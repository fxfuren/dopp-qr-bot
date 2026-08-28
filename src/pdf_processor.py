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
        # Try pdfplumber first (better text extraction)
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
            logger.warning(f"pdfplumber failed for {pdf_path}: {e}, trying PyMuPDF fallback...")

        # Fallback: use PyMuPDF (fitz) — handles broken/non-standard PDFs
        try:
            doc = fitz.open(pdf_path)
            if page_number is not None:
                if page_number > len(doc):
                    raise ValueError(f"Page {page_number} does not exist in PDF")
                text = doc[page_number - 1].get_text()
            else:
                texts = [doc[i].get_text() for i in range(len(doc))]
                text = "\n".join(texts)
            doc.close()
            logger.debug(f"PyMuPDF fallback succeeded for {pdf_path}")
            return text
        except Exception as e2:
            logger.error(f"Error extracting text from {pdf_path}: {e2}")
            raise
    
    # Run in thread pool to avoid blocking
    return await asyncio.to_thread(_extract)


def find_spec_number_in_text(text: str, search_number: str) -> bool:
    """
    Search for specification number in text.
    
    Supports flexible matching:
    - Search "47589" matches: "47589", "47589/1", "47589/2", "№47589", "№ 47589"
    - Search "47589/1" matches: "47589/1" specifically
    - Search "475892" matches: "47589/2" (interprets trailing digit as /suffix)
    - Search "А1842" matches: "А1842", "А1842/2", etc.
    
    Supports alphanumeric spec numbers (e.g., "47589", "А1842", "AVN2218").
    
    Args:
        text: Text to search in
        search_number: Specification number to search for
        
    Returns:
        True if specification number is found, False otherwise
    """
    if not text or not search_number:
        return False
        
    text = normalize_chars(text.upper())
    search_number = normalize_chars(search_number.upper())
    
    # Extract base number (before /)
    base_number = search_number.split('/')[0]
    escaped_number = re.escape(base_number)
    
    # Strategy 1: Direct match of the full base number with digit boundaries
    # Matches: "47589", "47589/1", "А1842", "А1842/2", "AVN2218/1"
    # Negative lookahead (?!\d|\.\d) prevents matching "47943" inside "47945" or "47943.84"
    pattern = rf"(?<!\d)(?<![\./]){escaped_number}(?:/[A-ZА-Яa-zа-я0-9]+)?(?!\d|\.\d)"
    if re.search(pattern, text, re.IGNORECASE):
        return True
    
    # Strategy 2: If search_number is pure digits and long enough,
    # try splitting last 1 digit as a potential /suffix
    # E.g., "475892" -> try matching "47589/2"
    if re.match(r'^\d{4,}$', base_number) and len(base_number) >= 5:
        # Try splitting off last digit as suffix: "475892" -> "47589/2"
        truncated = base_number[:-1]
        suffix = base_number[-1]
        escaped_truncated = re.escape(truncated)
        pattern2 = rf"(?<!\d)(?<![\./]){escaped_truncated}/{suffix}(?!\d|\.\d)"
        if re.search(pattern2, text, re.IGNORECASE):
            return True
    
    return False


def extract_all_spec_numbers_from_text(text: str) -> list[str]:
    """
    Extract ALL specification numbers from PDF text.
    
    A single DOPP document may contain multiple specifications (e.g., 47843 and 47843/1).
    This function finds all of them.
    
    Supports multiple PDF formats:
    - "6.1А НОМЕР: AVN2218/1 6.1Б ДАТА:" (number on same line)
    - "6.1А НОМЕР: 6.1Б ДАТА: ...\n47589/2" (number on next line)
    - "6.1 Спецификация No 47589/2 от ..." (from specification header)
    
    Args:
        text: Text extracted from PDF
        
    Returns:
        List of specification numbers found (may be empty)
    """
    if not text:
        return []
    
    found_numbers: list[str] = []
    
    # Pattern 1: Number on the SAME line after "НОМЕР:"
    # Example: "6.1АНОМЕР: А1842/2 6.1БДАТА:" or "6.1АНОМЕР: AVN2218/1 6.1БДАТА:"
    pattern1 = r'6\.1[АA]\s*НОМЕР:\s*([A-ZА-Яa-zа-я0-9]+(?:/[A-ZА-Яa-zа-я0-9]+)?)\s+6\.1[БB]'
    for match in re.finditer(pattern1, text, re.IGNORECASE):
        spec = match.group(1).strip()
        if spec not in found_numbers:
            found_numbers.append(spec)
    
    # Pattern 2: Number on the NEXT line after "НОМЕР:" (when НОМЕР: is followed by 6.1Б immediately)
    # Example: "6.1АНОМЕР: 6.1БДАТА: 26.05.2026\n47589/2"
    pattern2 = r'6\.1[АA]\s*НОМЕР:\s*6\.1[БB]\s*ДАТА:.*?\n\s*([A-ZА-Яa-zа-я0-9]+(?:/[A-ZА-Яa-zа-я0-9]+)?)\s*\n'
    for match in re.finditer(pattern2, text, re.IGNORECASE):
        spec = match.group(1).strip()
        if spec not in found_numbers:
            found_numbers.append(spec)
    
    # Pattern 3: From "Спецификация" header line
    # Example: "6.1 Спецификация No 47589/2 от 26.05.2026" or "6.1Спецификация№AVN2218/1от"
    pattern3 = r'6\.1\s*Спецификация\s*(?:No\.?|№)\s*([A-ZА-Яa-zа-я0-9]+(?:/[0-9]+)?)(?=\s*от|\s*$)'
    for match in re.finditer(pattern3, text, re.IGNORECASE):
        spec = match.group(1).strip()
        if spec not in found_numbers:
            found_numbers.append(spec)
    
    if found_numbers:
        logger.debug(f"Found spec numbers in text: {found_numbers}")
    else:
        logger.debug("No spec numbers found in text")
    
    return found_numbers


def extract_spec_number_from_text(text: str) -> Optional[str]:
    """
    Extract specification number from PDF text.
    
    Returns the first found specification number.
    For extracting all numbers, use extract_all_spec_numbers_from_text().
    
    Args:
        text: Text extracted from PDF
        
    Returns:
        Specification number or None if not found
    """
    numbers = extract_all_spec_numbers_from_text(text)
    return numbers[0] if numbers else None


def extract_vehicle_registration(text: str) -> Optional[dict]:
    """
    Extract vehicle and trailer registration numbers from PDF text.
    
    Looks for pattern: "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК" followed by the registration number.
    Supports both formats:
    - With spaces: "4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК 4.1Б НОМЕР ПРИЦЕПА"
    - Without spaces: "4.1ААВТО:РЕГИСТРАЦИОННЫЙ(?:ЗНАК|НОМЕР) 4.1БНОМЕРПРИЦЕПА"
    Supports Cyrillic vehicle plates, Latin, digits, slashes, and hyphens.
    
    Args:
        text: Text extracted from PDF
        
    Returns:
        Dict with 'vehicle' and 'trailer' keys, or None if not found.
        Example: {"vehicle": "С542ОА67", "trailer": "А4351А-2"}
    """
    if not text:
        return None
    
    # Try multiple patterns to handle different OCR quality
    # Patterns 1-3: pdfplumber-style (label and value on same/nearby line, keywords merged/spaced)
    # Pattern 4: fitz-style (label on one line, value on next line, spaces inside reg numbers)
    patterns = [
        # Pattern 1: Standard format with spaces (most common)
        r'4\.1[АA]\s+АВТО:\s*РЕГИСТРАЦИОННЫЙ\s+(?:ЗНАК|НОМЕР)(?:[\s\n]*4\.1[БB]\s+НОМЕР\s+ПРИЦЕПА)?\s*[\n\s]+([A-ZА-Яa-zа-я0-9/\-\s]+?)(?:\n|$)',
        
        # Pattern 2: Compact format without spaces between keywords
        r'4\.1[АA]АВТО:РЕГИСТРАЦИОННЫЙ(?:ЗНАК|НОМЕР)(?:[\s\n]*4\.1[БB]НОМЕРПРИЦЕПА)?\s*[\n\s]+([A-ZА-Яa-zа-я0-9/\-\s]+?)(?:\n|$)',
        
        # Pattern 3: Mixed format (some spaces, but not all)
        r'4\.1[АA]\s*АВТО:\s*РЕГИСТРАЦИОННЫЙ\s*(?:ЗНАК|НОМЕР)(?:[\s\n]*4\.1[БB]\s*НОМЕР\s*ПРИЦЕПА)?\s*[\n\s]+([A-ZА-Яa-zа-я0-9/\-\s]+?)(?:\n|$)',
    ]
    
    for pattern_idx, pattern in enumerate(patterns, 1):
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            full_line = match.group(1).strip()
            logger.debug(f"Vehicle registration found using pattern {pattern_idx}: {full_line}")
            
            # Split by whitespace to separate vehicle number from trailer number
            parts = full_line.split()
            
            if not parts:
                continue
            
            def is_full_plate(p):
                return bool(re.search(r'[A-Za-zА-Яа-я]', p) and re.search(r'[0-9]', p))
                
            vehicle = None
            trailer = None
            
            if is_full_plate(parts[0]) and len(parts[0]) >= 4:
                # Compact format: first part is vehicle, rest is trailer
                vehicle = parts[0]
                trailer = ' '.join(parts[1:]) if len(parts) > 1 else None
            else:
                vehicle_parts = [parts[0]]
                trailer_parts = []
                for i in range(1, len(parts)):
                    # If current part is a full plate, it's the trailer
                    if is_full_plate(parts[i]) and len(parts[i]) >= 4:
                        trailer_parts = parts[i:]
                        break
                    # If previous part ends the vehicle (region code digit or hyphen+digit)
                    if re.match(r'^(\d|-\d)$', parts[i-1]) or re.search(r'-\d$', parts[i-1]):
                        trailer_parts = parts[i:]
                        break
                    vehicle_parts.append(parts[i])
                
                vehicle = ' '.join(vehicle_parts)
                trailer = ' '.join(trailer_parts) if trailer_parts else None
                
            logger.debug(f"Extracted: vehicle={vehicle}, trailer={trailer}")
            
            # Handle case where vehicle contains "/" separating vehicle/trailer
            # Example: "519ATP05/46BSA05" -> vehicle="519ATP05", trailer="46BSA05"
            if vehicle and '/' in vehicle:
                slash_parts = vehicle.split('/', 1)
                # Only split if both parts look like registration numbers (not spec suffixes like /1, /2)
                if len(slash_parts) == 2 and len(slash_parts[1]) >= 3:
                    vehicle = slash_parts[0]
                    # If trailer was a duplicate of the original vehicle string, replace it
                    if trailer is None or trailer == match.group(1).strip().split()[0]:
                        trailer = slash_parts[1]
                    logger.debug(f"Split vehicle/trailer by slash: vehicle={vehicle}, trailer={trailer}")
            
            # Clean up empty trailer
            if trailer and trailer.strip() in ('', '__________'):
                trailer = None
            
            # Normalize: pdfplumber splits chars with spaces: "BA 5118 5" -> "BA51185"
            if vehicle:
                vehicle = normalize_chars(re.sub(r'\s+', '', vehicle).upper())
            if trailer:
                trailer = normalize_chars(re.sub(r'\s+', '', trailer).upper())
            
            return {"vehicle": vehicle, "trailer": trailer}

    # Pattern 4: fitz-style — label and value on separate lines, spaces inside reg number
    # Example:
    #   4.1А АВТО: РЕГИСТРАЦИОННЫЙ ЗНАК
    #   AE 9595 5
    #   4.1Б НОМЕР ПРИЦЕПА
    #   A 1523 I 5
    fitz_pattern = (
        r'4\.1[АA]\s+АВТО:\s+РЕГИСТРАЦИОННЫЙ\s+(?:ЗНАК|НОМЕР)\s*\n'
        r'\s*([A-ZА-Яa-zа-я0-9][A-ZА-Яa-zа-я0-9\s\-]*?)\s*\n'
        r'(?:\s*4\.1[БB]\s+НОМЕР\s+ПРИЦЕПА\s*\n'
        r'\s*([A-ZА-Яa-zа-я0-9_]*[A-ZА-Яa-zа-я0-9\s\-_]*?))?(?:\s*\n|$)'
    )
    fitz_match = re.search(fitz_pattern, text, re.IGNORECASE)
    if fitz_match:
        vehicle_raw = fitz_match.group(1).strip()
        trailer_raw = fitz_match.group(2).strip() if fitz_match.group(2) else None
        logger.debug(f"Vehicle registration found using fitz pattern: vehicle_raw={vehicle_raw!r}, trailer_raw={trailer_raw!r}")
        # Remove internal spaces — fitz splits individual chars/groups with spaces
        vehicle = normalize_chars(re.sub(r'\s+', '', vehicle_raw).upper()) if vehicle_raw else None
        trailer = normalize_chars(re.sub(r'\s+', '', trailer_raw).upper()) if trailer_raw else None
        if trailer in ('', '__________', '___', None):
            trailer = None
        logger.debug(f"Extracted (fitz): vehicle={vehicle}, trailer={trailer}")
        return {"vehicle": vehicle, "trailer": trailer}

    logger.debug("Vehicle registration number not found in text")
    
    # Help debug missing vehicles by logging the relevant section
    section_match = re.search(r'4\.1.*?4\.2', text, re.DOTALL | re.IGNORECASE)
    if section_match:
        logger.warning(f"Could not extract vehicle from section: {repr(section_match.group(0))}")
    else:
        logger.warning("Could not find section 4.1 to extract vehicle.")
        
    return None


def normalize_chars(s: str) -> str:
    """Normalize visually identical Cyrillic and Latin characters to Latin."""
    # Mapping Cyrillic letters used in vehicle plates to Latin equivalents
    mapping = str.maketrans(
        'АВЕКМНОРСТХУІ',
        'ABEKMHOPCTXYI'
    )
    return s.translate(mapping)


def extract_org_and_supplier(text: str) -> dict:
    """
    Extract organization name and supplier name from PDF text.

    Looks for:
      - Field 1A: НАИМЕНОВАНИЕ ОРГАНИЗАЦИИ / ФИО ИП  (client/org)
      - Field 2A: НАИМЕНОВАНИЕ ПОСТАВЩИКА             (supplier)

    pdfplumber collapses ALL spaces within a line, so the label and value
    look like:
      "1АНАИМЕНОВАНИЕОРГАНИЗАЦИИ/ФИОИП(ФАМИЛИЯ,ИМЯ,ОТЧЕСТВО1)"
      "ОБЩЕСТВОСОГРАНИЧЕННОЙОТВЕТСТВЕННОСТЬЮ\"ЭЛКОМЭЛЕКТРО\""
      "2АНАИМЕНОВАНИЕПОСТАВЩИКА"
      "ООО\"ТорговыйдомЭКСПОРТТОРГ\""

    Patterns are matched against the raw text (NOT normalize_chars'd):
    normalize_chars converts Cyrillic A/E/O/... to Latin look-alikes which
    would corrupt the Cyrillic label keywords used in the regex.

    Returns:
        dict with keys 'org' (str|None) and 'supplier' (str|None)
    """
    result = {"org": None, "supplier": None}

    if not text:
        return result

    # ── Field 1А ──────────────────────────────────────────────────────────
    # Strategy (a): pdfplumber collapsed — label on one line, value on next.
    #   "1АНАИМЕНОВАНИЕОРГАНИЗАЦИИ...\n<value>\n"
    # Strategy (b): fitz spaced — label on one line, value on next.
    #   "1А НАИМЕНОВАНИЕ ОРГАНИЗАЦИИ...\n<value>\n"
    # Strategy (c): fitz spaced — value inline after colon.
    #   "1А НАИМЕНОВАНИЕ ОРГАНИЗАЦИИ...: <value>  2А"
    org_patterns = [
        # (a) collapsed label, value on next line
        r'1[АA]НАИМЕНОВАНИЕОРГАНИЗАЦИИ[^\n]*\n\s*(.+?)\s*\n',
        # (b) spaced label, value on next line
        r'1[АA]\s+НАИМЕНОВАНИЕ\s+ОРГАНИЗАЦИИ[^\n]*\n\s*(.+?)\s*\n',
        # (c) spaced label, value inline after colon
        r'1[АA]\s*НАИМЕНОВАНИЕ\s*ОРГАНИЗАЦИИ[^\n]*?:\s*(.+?)(?:\s+2[АA]|\n|$)',
    ]

    for pat in org_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val:
                result["org"] = val
                break

    # ── Field 2А ──────────────────────────────────────────────────────────
    # Same two collapsed/spaced/inline strategies.
    supplier_patterns = [
        # (a) collapsed label, value on next line
        r'2[АA]НАИМЕНОВАНИЕПОСТАВЩИКА[^\n]*\n\s*(.+?)\s*\n',
        # (b) spaced label, value on next line
        r'2[АA]\s+НАИМЕНОВАНИЕ\s+ПОСТАВЩИКА[^\n]*\n\s*(.+?)\s*\n',
        # (c) spaced label, value inline after colon
        r'2[АA]\s*НАИМЕНОВАНИЕ\s*ПОСТАВЩИКА[^\n]*?:\s*(.+?)(?:\s+[23][АAБBбb]|\n|$)',
    ]

    for pat in supplier_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val:
                result["supplier"] = val
                break

    logger.debug(f"extract_org_and_supplier: org={result['org']!r}, supplier={result['supplier']!r}")
    return result


def find_vehicle_in_text(text: str, vehicle_number: str) -> bool:
    """Search for vehicle registration number in PDF text (Section 4.1)."""
    if not text or not vehicle_number:
        return False
    normalized_search = normalize_chars(re.sub(r'[\s\-]+', '', vehicle_number).upper())
    # Extract section 4.1 (vehicle info) — from "4.1А" to "4.2" or "Раздел 5"
    section_match = re.search(
        r'4\.1[АA].*?(?=4\.2|Раздел\s*5|$)',
        text, re.DOTALL | re.IGNORECASE
    )
    if not section_match:
        return False
    section_text = section_match.group(0)
    normalized_section = normalize_chars(re.sub(r'[\s\-]+', '', section_text).upper())
    return normalized_search in normalized_section


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
