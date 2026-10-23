"""Identify hardware from advertised model names, independently of payloads."""

from __future__ import annotations

import re

from .const import MODEL_CP35, TLM_DX_CP35, TLM_STANDARD

_MODEL = re.compile(
    r"(?<![a-z0-9])(?:DX[-_ ]?)?CP[-_ ]?(\d+)(?![a-z0-9])", re.IGNORECASE
)


def model_from_name(name: str | None) -> str | None:
    """Recognize CP27/CP35 and other explicitly advertised CP model numbers."""
    if name and (match := _MODEL.search(name)):
        return f"DX-CP{match.group(1)}"
    return None


def normalize_model(model: str) -> str:
    """Canonicalize a model label while preserving custom model descriptions."""
    model = model.strip()
    if match := _MODEL.fullmatch(model):
        return f"DX-CP{match.group(1)}"
    return model


def default_tlm_format(model: str) -> str:
    """Select the known CP35 quirk; an explicit firmware option overrides it."""
    return TLM_DX_CP35 if normalize_model(model) == MODEL_CP35 else TLM_STANDARD
