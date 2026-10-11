import logging
import os
import time

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
    deadline: float | None = None,
) -> str:
    if image.width * image.height > settings.OCR_MAX_IMAGE_PIXELS:
        raise OCRProcessingError("Image dimensions exceed the OCR limit.")
    configure_tesseract()

    try:
        return pytesseract.image_to_string(image, lang=lang, timeout=_remaining_timeout(deadline))

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
                    timeout=_remaining_timeout(deadline),
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


def _remaining_timeout(deadline: float | None) -> float:
    remaining = settings.OCR_TIMEOUT_SECONDS if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise OCRProcessingError("OCR job exceeded its time limit.")
    return min(settings.OCR_TIMEOUT_SECONDS, remaining)


def run_ocr(
    file_path: str,
    mime_type: str,
    lang: str = "ind+eng",
) -> str:
    deadline = time.monotonic() + settings.OCR_JOB_TIMEOUT_SECONDS
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
            extracted_texts = []
            with pdfium.PdfDocument(file_path) as pdf:
                for page_idx in range(min(len(pdf), 5)):
                    page = pdf[page_idx]
                    try:
                        width, height = page.get_size()
                        if width * height * 4 > settings.OCR_MAX_IMAGE_PIXELS:
                            raise OCRProcessingError("PDF page dimensions exceed the OCR limit.")
                        bitmap = page.render(scale=2.0)
                        try:
                            pil_image = bitmap.to_pil()
                            try:
                                page_text = run_ocr_on_image(pil_image, lang=lang, deadline=deadline)
                            finally:
                                pil_image.close()
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
                    if page_text.strip():
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
                deadline=deadline,
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
