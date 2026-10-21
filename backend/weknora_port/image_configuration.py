"""Pinned local MinerU OCR and independent visual Caption bindings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .documents import DocumentLifecycleError, _json_object
from .models.types import CallContext


@dataclass(frozen=True)
class ImageBinding:
    adapter: str
    model_id: str = ""
    source: str = ""
    capability: str = ""

    def public(self) -> dict[str, str]:
        return {"adapter": self.adapter, "model_id": self.model_id,
                "source": self.source, "capability": self.capability}


@dataclass(frozen=True)
class ImageSelection:
    ocr: ImageBinding
    caption: ImageBinding | None
    caption_error: str = ""
    config_version: int = 1

    def public(self) -> dict[str, Any]:
        return {"ocr": self.ocr.public(),
                "caption": self.caption.public() if self.caption else None,
                "caption_error": self.caption_error,
                "config_version": self.config_version}


def effective_image_config(kb: Mapping[str, Any], override: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """KB defaults followed by nonempty upload values; empty is not a reset."""
    base = _json_object(kb.get("image_processing_config"))
    # P31 retires the P27 physical-page selector; preserve stored history.
    base.pop("image_pages", None)
    if override is not None and "image_pages" in override:
        raise DocumentLifecycleError("IMAGE_PAGES_UNSUPPORTED")
    if override:
        for key in ("ocr_adapter", "ocr_model_id", "caption_mode", "caption_model_id",
                    "description_language", "custom_instructions"):
            value = override.get(key)
            if value is not None and value != "":
                base[key] = value
        if override.get('caption_mode') == 'follow_ocr':
            base.pop('caption_model_id', None)
        elif override.get('caption_model_id') and not override.get('caption_mode'):
            base['caption_mode'] = 'explicit'
    if base.get('ocr_adapter') in (None, '', 'mineru'):
        base.pop('ocr_model_id', None)
    return base


def _image_wire_supported(config: Any) -> bool:
    if config.api == "openai-completions":
        return bool(config.chat.supports_multi_content) or config.provider == "weknoracloud"
    return config.api in {"openai-responses", "anthropic-messages", "google-generative-ai", "ollama"}


async def _vision_binding(model_id: str, source: str, model_store: Any,
                          catalogue: Any, context: CallContext) -> ImageBinding:
    if not isinstance(model_id, str) or not model_id:
        raise DocumentLifecycleError("IMAGE_VISION_MODEL_REQUIRED")
    # The scoped model store rejects deleted/disabled/wrong-owner identities.
    role = await model_store.resolve(model_id)
    config = catalogue.resolve(role, model_id)
    declared = bool(role.parameters.get("vision_user_declared"))
    if role.role != "vlm" and not declared:
        raise DocumentLifecycleError("IMAGE_MODEL_TYPE_INVALID")
    if not config.supports_vision:
        raise DocumentLifecycleError("IMAGE_VISION_CAPABILITY_UNKNOWN")
    if not _image_wire_supported(config):
        raise DocumentLifecycleError("IMAGE_ADAPTER_UNSUPPORTED")
    capability = "USER_DECLARED_UNTESTED" if declared and not config.cataloged else "CATALOG_DECLARED"
    return ImageBinding("vlm", model_id, source, capability)


async def resolve_image_selection(kb: Mapping[str, Any], override: Mapping[str, Any] | None,
                                  model_store: Any, catalogue: Any, context: CallContext) -> ImageSelection:
    cfg = effective_image_config(kb, override)
    if override and override.get("ocr_model_id"):
        raise DocumentLifecycleError("IMAGE_OCR_MODEL_RETIRED")
    adapter = cfg.get("ocr_adapter") or "mineru"
    if adapter in {"tesseract", "vlm"}:
        raise DocumentLifecycleError("IMAGE_OCR_ADAPTER_RETIRED")
    if adapter != "mineru":
        raise DocumentLifecycleError("IMAGE_OCR_ADAPTER_INVALID")
    ocr = ImageBinding("mineru", source="local_parser", capability="LOCAL_ENGINE")
    explicit = cfg.get("caption_model_id") or ""
    mode = cfg.get("caption_mode") or ("explicit" if explicit else "follow_ocr")
    if mode not in {"explicit", "follow_ocr"}:
        raise DocumentLifecycleError("IMAGE_CAPTION_MODE_INVALID")
    if mode == "explicit":
        # Invalid explicit bindings are terminal configuration errors, never
        # interpreted as an absent value and never inherited from OCR.
        caption = await _vision_binding(explicit,
            "upload" if override and override.get("caption_model_id") else "knowledge_base",
            model_store, catalogue, context)
        return ImageSelection(ocr, caption)
    if explicit:
        raise DocumentLifecycleError("IMAGE_CAPTION_MODE_CONFLICT")
    return ImageSelection(ocr, None, "IMAGE_CAPTION_VISION_MODEL_REQUIRED")


async def validate_pinned_selection(snapshot: Mapping[str, Any], model_store: Any,
                                    catalogue: Any, context: CallContext) -> ImageSelection:
    """Recheck revocation without changing a task's model choices."""
    if snapshot.get('config_version') != 1 or not isinstance(snapshot.get('ocr'),dict):
        raise DocumentLifecycleError('IMAGE_TASK_CONFIG_INVALID')
    ocr_row=snapshot['ocr']
    if ocr_row.get('adapter') in {'tesseract', 'vlm'}:
        raise DocumentLifecycleError('IMAGE_OCR_ADAPTER_RETIRED')
    if ocr_row.get('adapter') != 'mineru':
        raise DocumentLifecycleError('IMAGE_TASK_CONFIG_INVALID')
    ocr=ImageBinding('mineru',source=ocr_row.get('source','local_parser'),capability='LOCAL_ENGINE')
    caption_row=snapshot.get('caption')
    if caption_row is None:
        caption=None
    elif isinstance(caption_row,dict) and caption_row.get('adapter')=='vlm':
        caption=await _vision_binding(caption_row.get('model_id',''),caption_row.get('source',''),
                                      model_store,catalogue,context)
    else:raise DocumentLifecycleError('IMAGE_TASK_CONFIG_INVALID')
    return ImageSelection(ocr,caption,snapshot.get('caption_error',''))
