"""ComfyUI IMAGE tensor -> OpenAI ``image_url`` content parts.

Shared by the multi-provider node. Downscales and re-encodes so the inline
data-URI stays inside the limits both gateways accept (Grokified documents
20 MiB and jpg/png only).
"""

from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image

#: Grokified docs: 20 MiB per image. Stay under it with room to spare.
MAX_INLINE_IMAGE_BYTES = 18 * 1024 * 1024


def encode_image(pil_image: Image.Image, max_bytes: int) -> tuple[str, bytes]:
    """PNG when it fits, otherwise progressively smaller JPEG."""
    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    blob = buffer.getvalue()
    if len(blob) <= max_bytes:
        return "image/png", blob

    quality = 92
    while True:
        buffer = io.BytesIO()
        pil_image.save(buffer, format="JPEG", quality=quality, subsampling=0, optimize=True)
        blob = buffer.getvalue()
        if len(blob) <= max_bytes or quality <= 60:
            break
        quality -= 10

    scale = 0.75
    while len(blob) > max_bytes and scale > 0.2:
        size = (max(1, int(pil_image.width * scale)), max(1, int(pil_image.height * scale)))
        buffer = io.BytesIO()
        pil_image.resize(size, Image.LANCZOS).save(
            buffer, format="JPEG", quality=85, subsampling=0, optimize=True
        )
        blob = buffer.getvalue()
        scale -= 0.2

    return "image/jpeg", blob


def image_to_parts(
    image,
    *,
    detail: str = "auto",
    max_side: int = 2048,
    max_images: int = 3,
    max_bytes: int = MAX_INLINE_IMAGE_BYTES,
    supports_detail: bool = True,
) -> tuple[list[dict], list[str]]:
    """ComfyUI IMAGE tensor ``[B,H,W,C]`` float 0..1 -> image_url parts.

    Returns ``(parts, warnings)``; never raises on odd tensor shapes.
    """
    if image is None:
        return [], []

    tensor = image.detach().cpu().numpy() if hasattr(image, "detach") else np.asarray(image)
    tensor = np.asarray(tensor)
    if tensor.ndim == 3:
        tensor = tensor[None, ...]
    if tensor.ndim == 2:
        tensor = tensor[None, ..., None]
    if tensor.ndim != 4:
        return [], [f"unsupported image tensor shape {tensor.shape}; input ignored"]

    warnings: list[str] = []
    total = int(tensor.shape[0])
    if max_images and total > max_images:
        warnings.append(f"{total} images were supplied; only the first {max_images} were sent")
        tensor = tensor[:max_images]

    parts: list[dict] = []
    for frame in tensor:
        frame = np.asarray(frame)
        if frame.ndim == 3 and frame.shape[-1] == 1:
            frame = frame[..., 0]
        if frame.dtype == np.uint8:
            array = np.ascontiguousarray(frame)
        else:
            array = np.clip(frame.astype(np.float32) * 255.0, 0, 255).astype(np.uint8)

        pil_image = Image.fromarray(array)
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
        if max_side and max(pil_image.size) > max_side:
            pil_image.thumbnail((max_side, max_side), Image.LANCZOS)

        mime, blob = encode_image(pil_image, max_bytes)
        uri = f"data:{mime};base64," + base64.b64encode(blob).decode("ascii")
        image_url: dict = {"url": uri}
        if supports_detail and detail:
            image_url["detail"] = detail
        parts.append({"type": "image_url", "image_url": image_url})

    return parts, warnings
