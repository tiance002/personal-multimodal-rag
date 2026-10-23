"""Documented media token bounds shared by admission and request validation."""
import json
import re
from urllib.parse import urlsplit
from .types import ProviderError


def deepseek_inline_image_input(config, body, wire, capability):
    """Official Flash inline JPEG/PNG bound: wire bytes + 1024 per image.

    DeepSeek's Vision guide documents at most 1024 input tokens per image:
    https://api-docs.deepseek.com/guides/vision/#token-usage (checked 2026-10-17).
    Keeping ALL wire bytes, including base64, deliberately over-reserves the
    text/framing contribution. This is not provider actual usage. Remote images,
    files, animated media and other provider/model/protocol identities refuse.
    """
    import base64
    import io
    from PIL import Image

    def deny():
        raise ProviderError('MODEL_INPUT_BOUND_NOT_APPLICABLE')

    endpoint = urlsplit(config.endpoint_url or config.base_url)
    if (config.provider != 'deepseek' or config.model != 'deepseek-flash'
            or config.api != 'openai-completions' or not config.supports_vision
            or capability != 'vision' or endpoint.scheme != 'https'
            or endpoint.netloc not in ('api.deepseek.com', 'api.deepseek.com:443')
            or body.get('model') != config.model):
        deny()
    try:
        if json.loads(wire) != body:
            deny()
    except (ValueError, TypeError):
        deny()
    messages = body.get('messages')
    if not isinstance(messages, list) or not messages or body.get('contents') or body.get('input'):
        deny()
    images = 0
    for message in messages:
        if not isinstance(message, dict):
            deny()
        parts = message.get('content')
        if not isinstance(parts, list):
            if parts is not None and not isinstance(parts, str):
                deny()
            continue
        for part in parts:
            if (isinstance(part, dict) and part.get('type') == 'text'
                    and set(part) <= {'type', 'text'} and isinstance(part.get('text'), str)):
                continue
            if (not isinstance(part, dict) or set(part) != {'type', 'image_url'}
                    or part.get('type') != 'image_url' or message.get('role') != 'user'):
                deny()
            image = part['image_url']
            if (not isinstance(image, dict) or set(image) - {'url', 'detail'}
                    or image.get('detail', 'auto') not in ('low', 'high', 'original', 'auto')):
                deny()
            match = re.fullmatch(r'data:image/(jpeg|png);base64,([A-Za-z0-9+/=]+)', str(image.get('url', '')))
            if match is None or len(match[2]) > 4 * ((32 * 1024 * 1024 + 2) // 3):
                deny()
            try:
                raw = base64.b64decode(match[2], validate=True)
                if not raw or len(raw) > 32 * 1024 * 1024:
                    deny()
                with Image.open(io.BytesIO(raw)) as opened:
                    if (opened.format != {'jpeg': 'JPEG', 'png': 'PNG'}[match[1]]
                            or getattr(opened, 'n_frames', 1) != 1
                            or max(opened.size) > 8192):
                        deny()
                    opened.verify()
            except (ValueError, OSError, SyntaxError, Image.DecompressionBombError):
                deny()
            images += 1
    if not 1 <= images <= 600:
        deny()
    return len(wire) + images * 1024


def deepseek_inline_image_context_input(config, body, wire, capability):
    """Context bound: validated image tokens plus text/framing bytes.

    The original reservation recipe deliberately retains base64 bytes. Model
    context accounting must not treat those transport bytes as image tokens.
    Validate the same exact identity/media contract first, then remove only
    each verified base64 payload; the documented 1024/image bound remains.
    """
    reservation = deepseek_inline_image_input(config, body, wire, capability)
    payload_bytes = 0
    for message in body['messages']:
        if isinstance(message.get('content'), list):
            for part in message['content']:
                if part.get('type') == 'image_url':
                    payload_bytes += len(part['image_url']['url'].split(';base64,', 1)[1])
    return reservation - payload_bytes
