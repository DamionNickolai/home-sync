"""Mobile receipt capture via native phone camera (not browser webcam)."""

from __future__ import annotations

import base64

import streamlit as st
import streamlit.components.v2 as components_v2

_HTML = """
<div class="receipt-capture-wrap">
  <input id="receipt-capture-input" type="file" accept="image/*" capture="environment" />
  <button type="button" id="receipt-capture-btn">📷 Take photo</button>
  <img id="receipt-capture-preview" alt="Receipt preview" />
  <p id="receipt-capture-status"></p>
</div>
"""

_CSS = """
.receipt-capture-wrap { display: none; }
.receipt-capture-wrap.mobile { display: block; }
#receipt-capture-input { display: none; }
#receipt-capture-btn {
  display: block;
  width: 100%;
  border: 1px solid rgba(250, 250, 250, 0.25);
  border-radius: 0.5rem;
  background: rgb(255, 75, 75);
  color: #fff;
  font-size: 1rem;
  font-weight: 600;
  padding: 0.65rem 1rem;
  cursor: pointer;
}
#receipt-capture-preview {
  display: none;
  margin-top: 0.65rem;
  max-width: 220px;
  border-radius: 0.5rem;
  border: 1px solid rgba(250, 250, 250, 0.2);
}
#receipt-capture-status {
  margin: 0.45rem 0 0;
  font-size: 0.85rem;
  color: rgba(250, 250, 250, 0.75);
}
"""

_JS = r"""
export default function(component) {
  const { setStateValue, parentElement } = component;
  const wrap = parentElement.querySelector(".receipt-capture-wrap");
  const input = parentElement.querySelector("#receipt-capture-input");
  const btn = parentElement.querySelector("#receipt-capture-btn");
  const preview = parentElement.querySelector("#receipt-capture-preview");
  const status = parentElement.querySelector("#receipt-capture-status");

  function isMobileClient() {
    return (
      /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
        navigator.userAgent
      ) || (navigator.maxTouchPoints > 1 && window.innerWidth < 900)
    );
  }

  if (!wrap || !input || !btn) {
    return;
  }

  if (!isMobileClient()) {
    wrap.classList.remove("mobile");
    return;
  }
  wrap.classList.add("mobile");

  if (input.dataset.bound === "1") {
    return;
  }
  input.dataset.bound = "1";

  btn.onclick = () => input.click();

  input.onchange = () => {
    const file = input.files && input.files[0];
    if (!file) {
      return;
    }

    const reader = new FileReader();
    reader.onload = (event) => {
      const img = new Image();
      img.onload = () => {
        const maxW = 1600;
        let w = img.width;
        let h = img.height;
        if (w > maxW) {
          h = Math.round((h * maxW) / w);
          w = maxW;
        }
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        canvas.getContext("2d").drawImage(img, 0, 0, w, h);
        const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
        preview.src = dataUrl;
        preview.style.display = "block";
        const sizeKb = Math.max(1, Math.round((dataUrl.length * 3) / 4 / 1024));
        const fileName = file.name || "receipt.jpg";
        status.textContent = fileName + " (" + sizeKb + " KB)";
        setStateValue("capture", {
          data: dataUrl,
          name: fileName,
          type: "image/jpeg",
          size: file.size,
        });
        input.value = "";
      };
      img.onerror = () => {
        status.textContent = "Could not read that photo. Try again or use gallery upload.";
      };
      img.src = event.target.result;
    };
    reader.onerror = () => {
      status.textContent = "Could not read that photo. Try again or use gallery upload.";
    };
    reader.readAsDataURL(file);
  };
}
"""

_capture_component = components_v2.component(
    "mobile_receipt_capture",
    html=_HTML,
    css=_CSS,
    js=_JS,
)


def mobile_receipt_capture(*, key: str | None = None, on_change=None) -> dict | None:
    """Return {data, name, type} when the user captures a photo on mobile, else None."""
    result = _capture_component(
        key=key,
        default={"capture": None},
        on_capture_change=on_change or (lambda: None),
        height="content",
    )
    capture = getattr(result, "capture", None)
    if not capture or not isinstance(capture, dict):
        return None
    if not capture.get("data"):
        return None
    return capture


def mobile_capture_to_bytes(result: dict) -> tuple[bytes, str, str] | None:
    """Decode a mobile_receipt_capture result into bytes, mime type, and filename."""
    data_url = str(result.get("data") or "")
    if not data_url.startswith("data:") or "," not in data_url:
        return None
    header, payload = data_url.split(",", 1)
    mime_type = header.split(";")[0].split(":", 1)[1] if ":" in header else "image/jpeg"
    file_name = str(result.get("name") or "receipt.jpg")
    try:
        file_bytes = base64.b64decode(payload)
    except (ValueError, TypeError):
        return None
    if not file_bytes:
        return None
    return file_bytes, mime_type, file_name
