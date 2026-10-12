"""Pinned official V4.1 image recipe for ordinary service; no URL fetching.

The local rendering/preprocessing bound is conditional on hosted equivalence.
Supplier usage exceeding it suspends the shared policy in the durable ledger.
"""
import base64
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path

from backend.app.adapters.models.ollama import validate_caption_image
from backend.app.adapters.models.v6_text_capacity import (
    DEEPSEEK_RECIPE_VERSION, DEEPSEEK_TOKENIZER_SHA256, DEEPSEEK_RENDERER_IDENTITY,
    DEEPSEEK_CONTEXT_WINDOW,
)
from backend.app.ports.providers import ProviderRequestNotSent


NATIVE_SHA256 = '1461bc4895c188d4ad60ca05eb7bd663aa7fa3286f2ecbebca39ecd73c5b0ec1'
PACKAGE_SHA256 = 'e52ab6b2dff1f2e33ef24ce346191b9a046de5c25b08a8c0f873293fe640cd5d'
CAPTION_PROMPT = 'Describe this figure briefly. Do not infer precise table values.'


def verified_recipe(tokenizer_path):
    try:
        import deepseek_recipe as recipe
        import deepseek_recipe._native as native
        path = Path(tokenizer_path)
        if (platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64')
                or version('deepseek-recipe') != DEEPSEEK_RECIPE_VERSION
                or hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest() != NATIVE_SHA256
                or hashlib.sha256(Path(recipe.__file__).read_bytes()).hexdigest() != PACKAGE_SHA256
                or hashlib.sha256(path.read_bytes()).hexdigest() != DEEPSEEK_TOKENIZER_SHA256):
            raise ValueError()
        return recipe, recipe.Tokenizer.from_file(str(path))
    except Exception:
        raise ProviderRequestNotSent('DEEPSEEK_RECIPE_RESOURCES_UNVERIFIED') from None


class DeepseekV41VisionCapacityCounter:
    provider = 'deepseek'
    model = 'deepseek-flash'
    execution_kind = 'REAL'
    identity = DEEPSEEK_RENDERER_IDENTITY + ';single-inline-image-original-detail/v1'

    def __init__(self, tokenizer_path):
        self.tokenizer_path = Path(tokenizer_path)
        verified_recipe(self.tokenizer_path)

    def measure(self, payload, wire_bytes):
        if (type(payload) is not dict or set(payload) != {'model', 'max_tokens', 'stream', 'thinking', 'messages'}
                or payload.get('model') != self.model or payload.get('stream') is not False
                or type(payload.get('max_tokens')) is not int or payload['max_tokens'] != 512
                or payload.get('thinking') != {'type': 'disabled'}
                or type(wire_bytes) is not bytes or json.dumps(payload).encode() != wire_bytes):
            raise ProviderRequestNotSent('SERVICE_VISION_PROTOCOL_UNSUPPORTED')
        messages = payload['messages']
        if (type(messages) is not list or len(messages) != 1 or type(messages[0]) is not dict
                or set(messages[0]) != {'role', 'content'} or messages[0]['role'] != 'user'):
            raise ProviderRequestNotSent('SERVICE_VISION_PROTOCOL_UNSUPPORTED')
        parts = messages[0]['content']
        if (type(parts) is not list or len(parts) != 2
                or parts[0] != {'type': 'text', 'text': CAPTION_PROMPT}
                or type(parts[1]) is not dict or set(parts[1]) != {'type', 'image_url'}
                or parts[1]['type'] != 'image_url' or type(parts[1]['image_url']) is not dict
                or set(parts[1]['image_url']) != {'url', 'detail'}
                or parts[1]['image_url']['detail'] != 'original'):
            raise ProviderRequestNotSent('SERVICE_VISION_PROTOCOL_UNSUPPORTED')
        url = parts[1]['image_url']['url']
        if type(url) is not str or not url.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,')):
            raise ProviderRequestNotSent('SERVICE_VISION_INLINE_IMAGE_REQUIRED')
        try:
            raw = base64.b64decode(url.split(',', 1)[1], validate=True)
            validate_caption_image(raw)
            recipe, tokenizer = verified_recipe(self.tokenizer_path)
            converted = recipe.ChatCompletionRequest(json.loads(wire_bytes)).convert(
                recipe.ConversionOptions(default_thinking_mode=False))
            encoding = recipe.DeepseekV41Encoding().with_tokenizer(tokenizer)
            rendered = encoding.render_conversation(converted.conversation)
            if len(rendered.image_sources) != 1:
                raise ValueError()
            # Single original-detail image uses the pinned recipe's defaults.
            # Preprocess bytes directly: never instantiate a URL fetcher.
            processed = recipe.OpenCvImagePreprocessor().preprocess(raw,
                recipe.PreprocessOptions('original', 8192, 512))
            adjustment = recipe.MultiModalData([processed]).image_token_adjustment()
            if type(adjustment) is not int or adjustment < 0 or type(rendered.prompt) is not str:
                raise ValueError()
            bound = len(rendered.prompt.encode('utf-8')) + adjustment
            if bound <= 0 or bound + 512 > DEEPSEEK_CONTEXT_WINDOW:
                raise ValueError()
            return bound, 512
        except ProviderRequestNotSent:
            raise
        except Exception:
            raise ProviderRequestNotSent('SERVICE_VISION_CAPACITY_UNVERIFIED') from None
