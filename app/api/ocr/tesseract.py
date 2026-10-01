import logging
import os

from PIL import Image
import pypdfium2 as pdfium
import pytesseract

from app.core.config import settings


logger = logging.getLogger(__name__)


class OCRProcessingError(Exception):
    """Raised when OCR extraction fails."""


def configure_tesseract() -> None:
    if settings.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

    if settings.TESSDATA_PREFIX:
        os.environ["TESSDATA_PREFIX"] = settings.TESSDATA_PREFIX


def run_ocr_on_image(
    image: Image.Image,
    lang: str = "ind+eng",
) -> str:
    configure_tesseract()

    try:
        return pytesseract.image_to_string(image, lang=lang)

    except pytesseract.TesseractNotFoundError as e:
        logger.error(
            "Tesseract executable not found at '%s': %s",
            settings.TESSERACT_CMD,
            e,
        )
        raise OCRProcessingError(
            "Tesseract OCR engine is not installed or configured."
        ) from e

    except pytesseract.TesseractError as e:
        err_msg = str(e)

        logger.warning(
            "Tesseract OCR error with lang '%s': %s",
            lang,
            err_msg,
        )

        if "ind" in lang and (
            "tessdata" in err_msg.lower()
            or "language" in err_msg.lower()
        ):
            try:
                logger.info(
                    "Attempting OCR fallback to 'eng' language pack..."
                )

                return pytesseract.image_to_string(
                    image,
                    lang="eng",
                )

            except Exception as fallback_err:
                logger.error(
                    "Fallback OCR to 'eng' also failed: %s",
                    fallback_err,
                )

                raise OCRProcessingError(
                    "OCR processing failed."
                ) from fallback_err

        raise OCRProcessingError(
            "OCR processing failed."
        ) from e

    except Exception as e:
        logger.error(
            "Unexpected error during image OCR: %s",
            e,
        )

        raise OCRProcessingError(
            "OCR processing failed."
        ) from e


def run_ocr(
    file_path: str,
    mime_type: str,
    lang: str = "ind+eng",
) -> str:
    if not os.path.exists(file_path):
        raise OCRProcessingError(
            "Document file not found for processing."
        )

    is_pdf = (
        mime_type == "application/pdf"
        or file_path.lower().endswith(".pdf")
    )

    if is_pdf:
        try:
            pdf = pdfium.PdfDocument(file_path)
            extracted_texts = []

            max_pages = min(len(pdf), 5)

            for page_idx in range(max_pages):
                page = pdf[page_idx]
                bitmap = page.render(scale=2.0)

                if hasattr(bitmap, "to_pil_image"):
                    pil_image = bitmap.to_pil_image()
                else:
                    pil_image = bitmap.to_pil()

                page_text = run_ocr_on_image(
                    pil_image,
                    lang=lang,
                )

                if not page_text.strip():
                    continue

                extracted_texts.append(page_text.strip())

            return "\n\n".join(extracted_texts)

        except OCRProcessingError:
            raise

        except Exception as e:
            logger.error(
                "Failed to process PDF for OCR: %s",
                e,
            )

            raise OCRProcessingError(
                "OCR processing failed."
            ) from e

    try:
        with Image.open(file_path) as image:
            return run_ocr_on_image(
                image,
                lang=lang,
            )

    except OCRProcessingError:
        raise

    except Exception as e:
        logger.error(
            "Failed to open image for OCR: %s",
            e,
        )

        raise OCRProcessingError(
            "OCR processing failed."
        ) from e