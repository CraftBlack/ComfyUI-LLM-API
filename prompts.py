"""System-prompt presets shared with the local ``ComfyUI-LLM-text-processor``.

Both packs read ``ComfyUI/models/LLM/prompts/*.txt``, so a preset written for
the local llama.cpp node is picked up here unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path

NO_SYSTEM_PROMPT = "none"
PROMPT_FOLDER_KEY = "llm_text_processor_prompts"


def prompt_root() -> Path | None:
    try:
        import folder_paths

        return Path(folder_paths.models_dir) / "LLM" / "prompts"
    except Exception:  # noqa: BLE001 - folder_paths always exists inside ComfyUI
        return None


def system_prompt_options() -> list[str]:
    """Preset ``.txt`` names, or just ``["none"]`` when the folder is absent."""
    names: list[str] = []
    try:
        import folder_paths

        if PROMPT_FOLDER_KEY in folder_paths.folder_names_and_paths:
            names = [
                name
                for name in folder_paths.get_filename_list(PROMPT_FOLDER_KEY)
                if os.sep not in name and "/" not in name
            ]
    except Exception:  # noqa: BLE001
        names = []

    if not names:
        root = prompt_root()
        if root is not None and root.is_dir():
            names = sorted(p.name for p in root.glob("*.txt"))

    return [NO_SYSTEM_PROMPT] + names


def read_preset(name: str) -> tuple[str | None, str | None]:
    """Return ``(text, warning)`` for a preset name; never raises."""
    candidates: list[Path] = []
    try:
        import folder_paths

        if PROMPT_FOLDER_KEY in folder_paths.folder_names_and_paths:
            full = folder_paths.get_full_path(PROMPT_FOLDER_KEY, name)
            if full:
                candidates.append(Path(full))
    except Exception:  # noqa: BLE001
        pass

    root = prompt_root()
    if root is not None:
        candidates.append(root / name)
        candidates.append(root / f"{name}.txt")
    candidates.append(Path(name))

    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace"), None
        except OSError:
            continue

    where = str(root) if root is not None else "ComfyUI/models/LLM/prompts"
    return None, (
        f"system prompt preset '{name}' was not found in {where}; continuing without a "
        "system prompt (paste the text into system_prompt_text instead)"
    )


def build_system_prompt(preset: str, inline_text: str) -> tuple[str, list[str]]:
    """Inline text wins over the preset; missing presets become a warning."""
    warnings: list[str] = []
    inline = (inline_text or "").strip()
    if inline:
        return inline, warnings

    preset = (preset or "").strip()
    if not preset or preset == NO_SYSTEM_PROMPT:
        return "", warnings

    text, warning = read_preset(preset)
    if warning:
        warnings.append(warning)
    return (text or "").strip(), warnings
