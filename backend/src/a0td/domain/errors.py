"""Error and warning codes (SPEC 10) and the domain exception."""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FORMAT_NOT_AVAILABLE_IN_IMAGE = "FORMAT_NOT_AVAILABLE_IN_IMAGE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    PIXEL_LIMIT_EXCEEDED = "PIXEL_LIMIT_EXCEEDED"
    PAGE_TOO_LARGE = "PAGE_TOO_LARGE"
    PASSWORD_REQUIRED = "PASSWORD_REQUIRED"
    PASSWORD_INVALID = "PASSWORD_INVALID"
    PRINT_NOT_ALLOWED = "PRINT_NOT_ALLOWED"
    CORRUPT_FILE = "CORRUPT_FILE"
    CONVERSION_FAILED = "CONVERSION_FAILED"
    CONVERSION_TIMEOUT = "CONVERSION_TIMEOUT"
    INVALID_SETTINGS = "INVALID_SETTINGS"
    OVERLAP_TOO_LARGE = "OVERLAP_TOO_LARGE"
    CONTENT_AREA_TOO_SMALL = "CONTENT_AREA_TOO_SMALL"
    SCALE_OUT_OF_RANGE = "SCALE_OUT_OF_RANGE"
    TOO_MANY_SHEETS = "TOO_MANY_SHEETS"
    DOCUMENT_NOT_READY = "DOCUMENT_NOT_READY"
    NOT_FOUND = "NOT_FOUND"
    JOB_CANCELLED = "JOB_CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    PRINTER_UNREACHABLE = "PRINTER_UNREACHABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CALIBRATION_OUT_OF_RANGE = "CALIBRATION_OUT_OF_RANGE"
    PRINTER_READONLY = "PRINTER_READONLY"
    PRESET_READONLY = "PRESET_READONLY"
    PRINT_REJECTED = "PRINT_REJECTED"  # the printer answered but refused or aborted the job


# HTTP status of each code in the REST API; the web bridge reports it too (SPEC 10, 17bis).
HTTP_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.UNSUPPORTED_FORMAT: 415,
    ErrorCode.FORMAT_NOT_AVAILABLE_IN_IMAGE: 415,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.TOO_MANY_PAGES: 422,
    ErrorCode.PIXEL_LIMIT_EXCEEDED: 422,
    ErrorCode.PAGE_TOO_LARGE: 422,
    ErrorCode.PASSWORD_REQUIRED: 409,
    ErrorCode.PASSWORD_INVALID: 422,
    ErrorCode.PRINT_NOT_ALLOWED: 403,
    ErrorCode.CORRUPT_FILE: 422,
    ErrorCode.CONVERSION_FAILED: 422,
    ErrorCode.CONVERSION_TIMEOUT: 504,
    ErrorCode.INVALID_SETTINGS: 422,
    ErrorCode.OVERLAP_TOO_LARGE: 422,
    ErrorCode.CONTENT_AREA_TOO_SMALL: 422,
    ErrorCode.SCALE_OUT_OF_RANGE: 422,
    ErrorCode.TOO_MANY_SHEETS: 422,
    ErrorCode.DOCUMENT_NOT_READY: 409,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.JOB_CANCELLED: 409,
    ErrorCode.INTERRUPTED: 500,
    ErrorCode.PRINTER_UNREACHABLE: 502,
    ErrorCode.AUTH_REQUIRED: 401,
    ErrorCode.CALIBRATION_OUT_OF_RANGE: 422,
    ErrorCode.PRINTER_READONLY: 403,
    ErrorCode.PRESET_READONLY: 403,
    ErrorCode.PRINT_REJECTED: 502,
}


class WarningCode(StrEnum):
    LOW_DPI = "LOW_DPI"
    VERY_LOW_DPI = "VERY_LOW_DPI"
    PHYSICAL_SIZE_ASSUMED = "PHYSICAL_SIZE_ASSUMED"
    NONSTANDARD_SIZE = "NONSTANDARD_SIZE"
    SMALL_TEXT = "SMALL_TEXT"
    THIN_LINES = "THIN_LINES"
    MANY_SHEETS = "MANY_SHEETS"
    FONTS_SUBSTITUTED = "FONTS_SUBSTITUTED"
    ANNOTATIONS_FLATTENED = "ANNOTATIONS_FLATTENED"
    LAYERS_PRESENT = "LAYERS_PRESENT"
    LAYERS_RASTERIZED = "LAYERS_RASTERIZED"  # render: layer state forced a rasterized output
    DARK_BACKGROUND = "DARK_BACKGROUND"
    PDF_REPAIRED = "PDF_REPAIRED"
    LOW_FIDELITY_FORMAT = "LOW_FIDELITY_FORMAT"
    BOX_FALLBACK = "BOX_FALLBACK"  # requested page box is missing; the crop box is used
    ICC_IGNORED = "ICC_IGNORED"  # no colour engine here: embedded ICC / CMYK profile approximated
    LOCAL_LIMIT = "LOCAL_LIMIT"  # web local mode: a setting was lowered to the browser's limits


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DomainError(Exception):
    """An error with a stable machine-readable code and parameters for i18n."""

    def __init__(
        self,
        code: ErrorCode,
        detail: str | None = None,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail or code.value
        self.params: dict[str, Any] = params or {}
        # Per-field errors (used by INVALID_SETTINGS).
        self.errors = errors
