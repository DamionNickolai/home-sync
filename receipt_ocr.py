"""receipt_ocr.py — receipt vision OCR via Google Gemini.

Mirrors get-fit-together's AI setup:
  - google-genai SDK
  - st.secrets["ai"]["GEMINI_API_KEY"]
  - default model gemini-3.1-flash-lite

Optional OpenAI fallback via [openai] api_key when receipt_ocr.provider = openai.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import date

import streamlit as st
from google import genai
from google.genai import types


_SYSTEM_PROMPT = """You are a receipt parser. Extract all line items from the receipt image.

Return ONLY a valid JSON object — no explanation, no markdown.

Schema:
{
  "merchant": "<store name or null>",
  "date": "<YYYY-MM-DD or null>",
  "total": <number or null>,
  "tax": <number or null>,
  "lines": [
    {"description": "<item name>", "amount": <number or null>},
    ...
  ]
}

Rules:
- Include every distinct product or service line item that has a price (use pre-tax item amounts when shown).
- Extract total sales tax into "tax". If the receipt shows multiple tax lines, sum them into "tax".
- Omit subtotal and total rows from "lines" — do not list tax as a line item.
- Use null when a value cannot be determined.
- Do not include any text outside the JSON object.
"""

# Same default as get-fit-together/ai_coach.py
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


class ReceiptOcrError(Exception):
    """OCR could not run or could not parse a usable response."""


@dataclass(frozen=True)
class OcrProviderConfig:
    provider: str
    api_key: str
    model: str


def extract_receipt_data(file_bytes: bytes, mime_type: str) -> dict | None:
    """Backward-compatible wrapper — returns data or None."""
    try:
        return extract_receipt_data_or_raise(file_bytes, mime_type)
    except ReceiptOcrError:
        return None


def extract_receipt_data_or_raise(file_bytes: bytes, mime_type: str) -> dict:
    """Send file bytes to the configured vision provider and return receipt data."""
    config = _resolve_ocr_config()
    if config is None:
        raise ReceiptOcrError(
            "OCR is not configured. Add GEMINI_API_KEY under [ai] in "
            ".streamlit/secrets.toml (same as get-fit-together), then restart the app."
        )

    image_bytes, effective_mime = _prepare_image_bytes(file_bytes, mime_type)
    if not image_bytes:
        raise ReceiptOcrError("Could not prepare the receipt image for OCR.")

    if config.provider == "gemini":
        raw = _call_gemini_vision(config.api_key, config.model, image_bytes, effective_mime)
    else:
        raw = _call_openai_vision(
            config.api_key,
            config.model,
            base64.b64encode(image_bytes).decode(),
            effective_mime,
        )

    parsed = _parse_response(raw)
    if not parsed:
        raise ReceiptOcrError(
            "OCR ran but could not parse line items from the response. "
            "Add lines manually below."
        )
    if not parsed.get("lines"):
        raise ReceiptOcrError(
            "OCR did not find any priced line items on this receipt. "
            "Add lines manually below."
        )
    return parsed


def render_pdf_preview_png(file_bytes: bytes, *, dpi: int = 120) -> bytes | None:
    """Render the first PDF page to PNG bytes for in-app preview."""
    try:
        return _pdf_to_png_bytes(file_bytes, dpi=dpi)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Provider config (get-fit-together compatible)
# ---------------------------------------------------------------------------

def _secret_str(*path: str) -> str | None:
    try:
        node = st.secrets
        for key in path:
            node = node[key]
        value = str(node or "").strip()
        return value or None
    except (KeyError, AttributeError, TypeError):
        return None


def _gemini_api_key() -> str | None:
    """Primary: [ai] GEMINI_API_KEY (get-fit-together). Fallback: [gemini] api_key."""
    return _secret_str("ai", "GEMINI_API_KEY") or _secret_str("gemini", "api_key")


def _gemini_model() -> str:
    return (
        _secret_str("ai", "GEMINI_MODEL")
        or _secret_str("ai", "RECEIPT_OCR_MODEL")
        or _secret_str("gemini", "model")
        or DEFAULT_GEMINI_MODEL
    )


def _resolve_ocr_config() -> OcrProviderConfig | None:
    """Pick provider + credentials from secrets."""
    preference = (_secret_str("receipt_ocr", "provider") or "auto").lower()
    gemini_key = _gemini_api_key()
    openai_key = _secret_str("openai", "api_key")

    if preference == "gemini":
        if not gemini_key:
            return None
        return OcrProviderConfig(provider="gemini", api_key=gemini_key, model=_gemini_model())

    if preference == "openai":
        if not openai_key:
            return None
        return OcrProviderConfig(
            provider="openai",
            api_key=openai_key,
            model=_secret_str("openai", "model") or DEFAULT_OPENAI_MODEL,
        )

    # auto — prefer Gemini (shared with get-fit-together)
    if gemini_key:
        return OcrProviderConfig(provider="gemini", api_key=gemini_key, model=_gemini_model())
    if openai_key:
        return OcrProviderConfig(
            provider="openai",
            api_key=openai_key,
            model=_secret_str("openai", "model") or DEFAULT_OPENAI_MODEL,
        )
    return None


# ---------------------------------------------------------------------------
# Image preparation
# ---------------------------------------------------------------------------

def _prepare_image_bytes(file_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    normalized_mime = (mime_type or "").split(";")[0].strip().lower()
    if normalized_mime == "application/pdf" or file_bytes[:4] == b"%PDF":
        return _pdf_to_png_bytes(file_bytes), "image/png"
    return file_bytes, normalized_mime or "image/jpeg"


def _pdf_to_png_bytes(file_bytes: bytes, *, dpi: int = 200) -> bytes:
    """Render the first PDF page to PNG for vision OCR."""
    try:
        import fitz  # noqa: PLC0415
    except ImportError as exc:
        raise ReceiptOcrError(
            "PDF receipts require the pymupdf package. "
            "Run: pip install pymupdf"
        ) from exc

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.page_count == 0:
            raise ReceiptOcrError("The PDF has no pages.")
        pix = doc[0].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    except ReceiptOcrError:
        raise
    except Exception as exc:
        raise ReceiptOcrError(f"Could not render the PDF for OCR: {exc}") from exc


# ---------------------------------------------------------------------------
# Provider API calls
# ---------------------------------------------------------------------------

def _call_gemini_vision(api_key: str, model: str, image_bytes: bytes, mime_type: str) -> str:
    """Call Gemini via google-genai SDK (same stack as get-fit-together)."""
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=[
                _SYSTEM_PROMPT,
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=2048,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:
        raise ReceiptOcrError(f"Gemini OCR request failed: {exc}") from exc

    text = getattr(response, "text", None)
    if not text:
        raise ReceiptOcrError("Gemini returned an empty response.")
    return text


def _call_openai_vision(api_key: str, model: str, image_b64: str, mime_type: str) -> str:
    """Optional OpenAI vision fallback."""
    import urllib.error
    import urllib.request  # noqa: PLC0415

    payload = {
        "model": model,
        "max_tokens": 2048,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _SYSTEM_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_b64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ReceiptOcrError(f"OpenAI OCR error ({exc.code}): {detail[:300]}") from exc
    except Exception as exc:
        raise ReceiptOcrError(f"OpenAI OCR request failed: {exc}") from exc

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReceiptOcrError("OpenAI returned an unexpected response.") from exc


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> dict | None:
    """Extract and normalize the JSON payload from the model's response."""
    text = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None

    result: dict = {
        "merchant": _str_or_none(data.get("merchant")),
        "date": _parse_date(data.get("date")),
        "total": _float_or_none(data.get("total")),
        "tax": _float_or_none(data.get("tax")),
        "lines": [],
    }
    for ln in data.get("lines") or []:
        result["lines"].append({
            "description": str(ln.get("description") or "").strip(),
            "amount": _float_or_none(ln.get("amount")),
        })
    return result


def _str_or_none(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s and s.lower() != "null" else None


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_date(value) -> str | None:
    s = _str_or_none(value)
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%d-%b-%Y"):
        try:
            return date.strftime(
                __import__("datetime").datetime.strptime(s, fmt).date(), "%Y-%m-%d"
            )
        except ValueError:
            pass
    return None
