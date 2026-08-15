from __future__ import annotations

from .schema_runtime import CANONICAL_NAMES, RESOURCES, build_models

MODELS = build_models()
globals().update(MODELS)

CANONICAL_MODELS = {name: MODELS[name] for name in CANONICAL_NAMES}
HELPER_MODELS = {name: model for name, model in MODELS.items() if name not in CANONICAL_MODELS}

__all__ = [*CANONICAL_NAMES, "CANONICAL_MODELS", "HELPER_MODELS", "MODELS"]
