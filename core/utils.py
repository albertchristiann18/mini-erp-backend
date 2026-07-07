import io
import os
import tempfile
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Union

import fitz
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image
from ulid.api.api import Api
from ulid.providers import DEFAULT


def generate_ulid() -> Any:
    """Generate a new ULID for use as default in Django models."""
    return Api(DEFAULT).new()


def round_decimal(value: Any, places: int = 3) -> Decimal:
    # This is 100% safe for ERP use
    return Decimal(str(value)).quantize(Decimal(f"1.{'0' * places}"), rounding=ROUND_HALF_UP)


def get_default_shipping_config() -> dict:
    return {
        "insurance": {"is_required": False, "fee_type": "percentage"},
        "general": {
            "reguler": {
                "cod": True,
                "expeditions": [
                    {
                        "code": "anteraja_reguler",
                        "name": "Anteraja Reguler",
                        "is_active": False,
                    },
                    {"code": "id_express", "name": "ID Express", "is_active": False},
                    {"code": "jne", "name": "JNE Reguler", "is_active": False},
                    {"code": "ninja_xpress", "name": "Ninja Xpress", "is_active": False},
                    {"code": "pos_reguler", "name": "Pos Reguler", "is_active": False},
                    {"code": "sicepat", "name": "SiCepat REG", "is_active": False},
                    {"code": "jnt", "name": "J&T Express", "is_active": False},
                    {"code": "express", "name": "Express", "is_active": False},
                ],
            },
            "instant": {
                "cod": False,
                "expeditions": [
                    {"code": "grab", "name": "GrabExpress", "is_active": False},
                    {"code": "gojek", "name": "GoSend Instant", "is_active": False},
                ],
            },
            "instant_priority": {
                "cod": False,
                "expeditions": [
                    {
                        "code": "grab",
                        "name": "GrabExpress Instant Prioritas",
                        "is_active": False,
                    },
                    {"code": "gojek", "name": "GoSend Instant Prioritas", "is_active": False},
                ],
            },
            "cargo": {
                "cod": False,
                "expeditions": [
                    {"code": "anteraja_cargo", "name": "Anteraja Cargo", "is_active": False},
                    {
                        "code": "anteraja_economy",
                        "name": "Anteraja Economy",
                        "is_active": False,
                    },
                    {"code": "jnt", "name": "J&T Cargo", "is_active": False},
                    {"code": "jne", "name": "JNE Trucking (JTR)", "is_active": False},
                    {"code": "sentral_cargo", "name": "Sentral Cargo", "is_active": False},
                    {"code": "sicepat_gokil", "name": "Sicepat Gokil", "is_active": False},
                    {"code": "sicepat_halu", "name": "SiCepat Halu", "is_active": False},
                    {"code": "express_eco", "name": "Express Eco", "is_active": False},
                ],
            },
            "sameday": {
                "cod": False,
                "expeditions": [
                    {"code": "anteraja", "name": "Anteraja Sameday", "is_active": False},
                    {"code": "grab", "name": "GrabExpress Sameday", "is_active": False},
                    {"code": "gojek", "name": "GoSend Same Day", "is_active": False},
                ],
            },
            "nextday": {
                "cod": False,
                "expeditions": [
                    {"code": "jne", "name": "JNE YES", "is_active": False},
                    {"code": "sicepat", "name": "Sicepat BEST", "is_active": False},
                ],
            },
        },
        "marketplaces": {
            "Shopee": {
                "reguler": {
                    "expeditions": [
                        {"code": "spx_standard", "name": "SPX Standard", "is_active": False}
                    ]
                },
                "cargo": {
                    "expeditions": [{"code": "spx_hemat", "name": "SPX Hemat", "is_active": False}]
                },
                "instant": {
                    "expeditions": [
                        {"code": "spx_instant", "name": "SPX Instant", "is_active": False}
                    ]
                },
                "instant_priority": {
                    "expeditions": [
                        {
                            "code": "spx_instant_prio",
                            "name": "SPX Instant Prioritas",
                            "is_active": False,
                        }
                    ]
                },
                "sameday": {
                    "expeditions": [
                        {"code": "spx_sameday", "name": "SPX Sameday", "is_active": False}
                    ]
                },
            },
            "Tokopedia_TikTok": {"use_general_config": True},
        },
    }


def is_valid_pdf(file_input: Union[UploadedFile, str], max_size_mb: int = 2) -> tuple[bool, str]:
    """
    Unified check for Size, Header, and Structure.
    Supports both file paths (str) and Django File Objects.

    Returns: (bool, str) -> (True, None) or (False, "Error Message")
    """
    # 1. SETUP: Determine if it's a Path or a File Object
    limit_bytes = max_size_mb * 1024 * 1024

    # 2. CHECK SIZE
    if isinstance(file_input, str):
        if not os.path.exists(file_input):
            return False, "File not found."
        current_size = os.path.getsize(file_input)
    else:
        # For Django InMemoryUploadedFile / TemporaryUploadedFile
        current_size = file_input.size or 0

    if current_size > limit_bytes:
        return (
            False,
            f"File too large ({current_size / 1024 / 1024:.2f}MB). Limit is {max_size_mb}MB.",
        )

    # 3. CHECK HEADER (Magic Bytes) & STRUCTURE
    try:
        if isinstance(file_input, str):
            # -- Logic for File Path --
            doc = fitz.open(file_input)

            # Additional Header Check (Manual)
            with open(file_input, "rb") as f:
                header = f.read(4)
        else:
            # -- Logic for File Object --
            # Reset pointer to start
            file_input.seek(0)
            header = file_input.read(4)

            # Reset again to read full stream for fitz
            file_input.seek(0)
            file_bytes = file_input.read()

            # Open from memory
            doc = fitz.open(stream=file_bytes, filetype="pdf")

            # CRITICAL: Reset pointer for the next part of your code (Compression/Saving)
            file_input.seek(0)

        # 4. VALIDATE CONTENT
        if header != b"%PDF":
            doc.close()
            return False, "File is not a PDF (Header mismatch)."

        if not doc.is_pdf:
            doc.close()
            return False, "File structure is invalid or corrupt."

        doc.close()
        return True, ""

    except Exception as e:
        return False, f"Could not process file: {str(e)}"


def compress_pdf_iterative(uploaded_file: Any, target_mb: float = 2.0) -> tuple[ContentFile, bool]:
    """
    Compress a PDF file iteratively at increasing power levels until it is under
    target_mb. Returns (ContentFile, was_compressed: bool).

    If even power=4 cannot reach target_mb, returns the smallest result achieved.
    The input file must already be a valid PDF -- call is_valid_pdf() before this.
    """
    target_bytes = int(target_mb * 1024 * 1024)

    # If already under target, return as-is wrapped in ContentFile
    uploaded_file.seek(0)
    original_bytes = uploaded_file.read()
    uploaded_file.seek(0)
    if len(original_bytes) <= target_bytes:
        return ContentFile(original_bytes, name=uploaded_file.name), False

    best: ContentFile | None = None
    best_size = len(original_bytes)

    for power in range(1, 5):  # 1, 2, 3, 4
        try:
            # compress_pdf_file reads from uploaded_file.chunks() -- reset pointer first
            uploaded_file.seek(0)
            compressed = compress_pdf_file(uploaded_file, power=power)
            compressed_size = len(compressed.read())
            compressed.seek(0)
            if best is None or compressed_size < best_size:
                best = compressed
                best_size = compressed_size
            if compressed_size <= target_bytes:
                break
        except Exception:
            continue
        finally:
            uploaded_file.seek(0)

    if best is None:
        # All compression attempts failed -- return original
        return ContentFile(original_bytes, name=uploaded_file.name), False

    return best, True


def compress_pdf_file(uploaded_file: Any, power: int = 3) -> ContentFile:
    """
    Takes an InMemoryUploadedFile (Django), compresses it using your proven logic
    via temporary disk files (to save RAM), and returns a Django ContentFile.
    """
    is_valid, message = is_valid_pdf(uploaded_file, max_size_mb=100)
    if not is_valid:
        raise ValidationError(f"Uploaded file is not a valid PDF. reason : {message}")

    # --- 1. SETUP: Write Input to Temp Disk ---
    # We do this to give PyMuPDF a physical file path to work with.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_input:
        for chunk in uploaded_file.chunks():
            tmp_input.write(chunk)
        input_path = tmp_input.name

    output_path = f"{input_path}_compressed.pdf"

    try:
        # --- 2. CONFIGURATION (Matches your 'compress_pdf') ---
        settings = {
            0: {"jpg_quality": 95, "scale": 1.0, "grayscale": False},
            1: {"jpg_quality": 75, "scale": 0.8, "grayscale": False},
            2: {"jpg_quality": 50, "scale": 0.6, "grayscale": False},
            3: {"jpg_quality": 30, "scale": 0.4, "grayscale": False},
            4: {"jpg_quality": 15, "scale": 0.3, "grayscale": False},
        }
        config = settings.get(power, settings[3])
        jpg_quality = config["jpg_quality"]
        scale = config["scale"]

        # --- 3. OPEN & PROCESS ---
        doc = fitz.open(input_path)

        for page in doc:
            image_list = page.get_images()
            for img_idx, img in enumerate(image_list):
                xref = img[0]
                smask = img[1]  # Transparency mask

                # A. Skip tiny images (logos, icons)
                base_info = doc.extract_image(xref)
                if base_info and (base_info["width"] < 300 or base_info["height"] < 300):
                    continue

                try:
                    # B. Handle Transparency (The 'Gold Standard' Logic)
                    pix_base = fitz.Pixmap(doc, xref)
                    if smask > 0:
                        mask = fitz.Pixmap(doc, smask)
                        pix = fitz.Pixmap(pix_base, mask)
                    else:
                        pix = pix_base

                    # C. Protect Color (Convert CMYK/others to RGB)
                    if pix.colorspace and pix.colorspace.name not in (
                        fitz.csGRAY.name,
                        fitz.csRGB.name,
                    ):
                        pix = fitz.Pixmap(fitz.csRGB, pix)

                    # D. PIL Conversion
                    mode = "RGBA" if pix.alpha else "RGB"
                    img_pil = Image.frombytes(mode, (pix.width, pix.height), pix.samples)

                    # E. Resize
                    new_size = (
                        max(1, int(img_pil.width * scale)),
                        max(1, int(img_pil.height * scale)),
                    )
                    img_pil = img_pil.resize(new_size, Image.Resampling.LANCZOS)

                    # F. Optional Grayscale
                    if config.get("grayscale"):
                        img_pil = img_pil.convert("L")
                        mode = "L"  # Update mode for saving logic below

                    # G. Save to Buffer (PNG for Transp / JPG for Content)
                    save_buffer = io.BytesIO()

                    if mode == "RGBA" and not config.get("grayscale"):
                        # Keep transparent images as PNG to avoid black backgrounds
                        img_pil.save(save_buffer, format="PNG", optimize=True)
                    else:
                        # Convert to RGB for JPEG saving
                        if mode != "L":  # If it's not grayscale already
                            img_pil = img_pil.convert("RGB")

                        img_pil.save(
                            save_buffer,
                            format="JPEG",
                            quality=jpg_quality,
                            optimize=True,
                            progressive=True,
                        )

                    # H. Replace Image in PDF
                    page.replace_image(xref, stream=save_buffer.getvalue())

                except Exception as e:
                    # Log error but continue processing other images
                    print(f"Warning: Image {img_idx} on page {page.number} failed: {e}")
                    continue

        # --- 4. METADATA SCRUBBING ---
        if power >= 4:
            doc.scrub(metadata=True, thumbnails=True, javascript=True)

        # --- 5. SAVE TO DISK (Optimized) ---
        doc.save(output_path, garbage=4, deflate=True, clean=True, use_objstms=True)
        doc.close()

        # --- 6. RETURN AS DJANGO FILE ---
        # Read the compressed file from disk into memory
        with open(output_path, "rb") as f:
            compressed_content = f.read()

        return ContentFile(compressed_content, name=uploaded_file.name)

    except Exception as e:
        raise ValidationError(f"Compression failed: {e}. Returning original file.")

    finally:
        # --- 7. CLEANUP TEMP FILES ---
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)
