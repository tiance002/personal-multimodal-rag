"""Query understanding and local expansion from fixed WeKnora source.

Tokenizer dependencies are explicit: a missing Han segmenter never becomes an
English whitespace approximation. Python jieba alone is not GoJieba parity.
"""
from __future__ import annotations
import json
import re
from typing import Callable, Sequence
from ..models.types import ChatOptions, Message, CallCancelled, RunDeadlineExceeded
STOPWORDS = frozenset('的 是 在 了 和 与 或 a an the is are was were be been being have has had do does did will would could should may might must can to of in for on with at by from as into through about what how why when where which who whom whose'.split())

class TokenizerUnavailable(RuntimeError):

    def __init__(self):
        super().__init__('TOKENIZER_UNAVAILABLE')
from ..conversation.unicode15 import HAN, L, ND, WHITE_SPACE, contains as unicode_contains, lower as go_lower

def is_han(ch):
    return unicode_contains(HAN, ch)

def tokenize(text: str, segmenter: Callable[[str], Sequence[str]] | None=None):
    """Expansion run splitting: Han vs Unicode letter/digit vs punctuation."""
    tokens, run, kind = ([], [], None)

    def flush():
        if not run:
            return
        value = ''.join(run)
        if kind == 'han':
            if segmenter is None:
                raise TokenizerUnavailable()
            tokens.extend(segmenter(value))
        else:
            tokens.append(value)
        run.clear()
    for ch in text:
        k = 'han' if is_han(ch) else 'word' if unicode_contains(L, ch) or unicode_contains(ND, ch) else None
        if k != kind:
            flush()
            kind = k
        if k:
            run.append(ch)
        else:
            flush()
    flush()
    return tokens

def simple_tokens(text, segmenter=None):
    text = go_lower(go_strip(text))
    if any(map(is_han, text)):
        if segmenter is None:
            raise TokenizerUnavailable()
        values = segmenter(text)
    else:
        values = go_fields(text)
    return {go_strip(w) for w in values if len(go_strip(w)) > 1 and any((not is_punctuation_symbol_space(c) for c in w))}

def jaccard(a, b):
    return len(a & b) / len(a | b) if a or b else 0.0

def expand_queries(rewrite: str, original: str, segmenter=None):
    q = go_strip(rewrite)
    if not q:
        return []
    seen = {go_lower(q), go_lower(original)}
    out = []

    def add(s):
        s = go_strip(s)
        if len(s.encode('utf-8')) >= 3 and go_lower(s) not in seen:
            seen.add(go_lower(s))
            out.append(s)
    keywords = [t for t in tokenize(q, segmenter) if len(t) > 1 and go_lower(t) not in STOPWORDS]
    if len(keywords) >= 2:
        add(' '.join(keywords))
    for m in re.finditer('[\\"\'「」『』]([^\\"\'「」『』]+)[\\"\'「」『』]', q):
        if len(m[1].encode()) > 2:
            add(m[1])
    for s in re.split('[,，;；、。！？!?\\s]+', q, flags=re.ASCII):
        if len(go_strip(s).encode()) > 5:
            add(s)
    stripped = re.sub('^(什么是|什么|如何|怎么|怎样|为什么|为何|哪个|哪些|谁|何时|何地|请问|请告诉我|帮我|我想知道|我想了解)', '', q)
    if stripped != q:
        add(stripped)
    return out[:5]

def parse_understanding(state, output):
    raw = go_strip(output)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        start, end = (raw.find('{'), raw.rfind('}'))
        try:
            data = json.loads(raw[start:end + 1]) if start >= 0 else None
        except ValueError:
            return False
    if not isinstance(data, dict):
        return False

    def first(keys):
        for key in keys:
            if isinstance(data.get(key), str):
                return data[key]
        return ''
    rewrite = first(('rewrite_query', 'rewritten_query', 'query', 'question'))
    if go_strip(rewrite):
        state.rewrite_query = go_strip(rewrite)
    state.intent = go_strip(data.get('intent', '')) if isinstance(data.get('intent', ''), str) else ''
    desc = go_strip(first(('image_description', 'image_desc', 'image_text', 'image_ocr_text', 'description')))
    ocr = go_strip(first(('ocr_text', 'ocr', 'full_ocr', 'image_ocr', 'ocr_content')))
    if ocr and ocr not in desc:
        desc = desc + '\n\n[OCR]\n' + ocr if desc else ocr
    state.image_description = go_strip(desc)
    return True

def needs_retrieval(intent, web_search_enabled=False):
    # A model-produced unknown label must not authorize bypassing retrieval.
    return web_search_enabled if intent == 'web_search' else intent not in ('greeting', 'chitchat', 'follow_up', 'image_only', 'doc_only')


_KB_REQUEST = re.compile(
    r'知识库|资料库|库中|库内|\bknowledge\s*base\b|\bKB\b|'
    r'(?:查找|搜索|检索|寻找|列出|列举|帮我找)(?:一下|一些|所有|全部|相关|类似|其他|更多|已有|已上传|的|\s)*(?:文档|文件|资料|报告)|'
    r'\b(?:search(?:\s+for)?|find|retrieve|look\s+up|list|browse)\s+'
    r'(?:(?:the|some|all|related|similar|other|more|stored|uploaded|available)\s+)*(?:documents?|files?|sources?|reports?)\b', re.I)


def resolve_intent(state):
    """Validate bypass prerequisites using the original request, never its rewrite.

    Classification is advisory. Stored-source requests always retrieve; only
    known social intents, actual history or actual attachments can use the
    corresponding non-retrieval paths. This is not a second intent classifier.
    """
    intent = state.intent
    query = go_strip(state.query)
    if _KB_REQUEST.search(query):
        return 'kb_search'
    # Attachment-only analysis remains available. The absence marker is not
    # an attachment and cannot grant this exception.
    if intent == 'image_only' and state.images:
        return intent
    if intent == 'doc_only' and go_strip(state.attachment_prompt) and '<no_document_attached' not in state.attachment_prompt:
        return intent
    if intent in ('greeting', 'chitchat'):
        return intent
    if intent == 'follow_up':
        return intent if any(go_strip(h.answer) for h in state.history) else 'kb_search'
    if intent in ('image_only', 'doc_only'):
        return 'kb_search'
    if intent in ('kb_search', 'clarification', 'summarize', 'web_search'):
        return intent
    return 'kb_search'

async def understand_query(state, context, registry, *, history_loader=None, caption_sink=None):
    state.rewrite_query = state.query
    if not state.enable_rewrite and (not state.images):
        return
    if not state.history and state.max_rounds > 0 and history_loader:
        try:
            state.history = list(await history_loader(state, context))
        except Exception:
            pass
    model, use_images = (None, False)

    async def get(model_id):
        if not model_id:
            return None
        try:
            return await registry.chat(model_id, context)
        except (CallCancelled, RunDeadlineExceeded):
            raise
        except Exception:
            return None
    if state.images:
        if state.chat_model_supports_vision:
            model = await get(state.chat_model_id)
        if model is None:
            model = await get(state.vlm_model_id)
        use_images = model is not None
    if model is None:
        model = await get(state.query_understand_model_id or state.chat_model_id)
        if model is None and state.query_understand_model_id and state.query_understand_model_id != state.chat_model_id:
            model = await get(state.chat_model_id)
    if model is None:
        return
    from .context import render_placeholders
    history = ''.join((f'------BEGIN------\nUser question: {h.query}\nAssistant answer: {h.answer}\n------END------\n' for h in state.history))
    q = state.query + (f'\n\n<images_uploaded count="{len(state.images)}" />' if state.images else '\n\n<no_image_attached />')
    q += state.attachment_prompt or '\n<no_document_attached />'
    q += state.memory_background
    values = {'conversation': history, 'query': q, 'language': state.language}
    messages = (Message('system', render_placeholders(state.rewrite_system_prompt, values)), Message('user', render_placeholders(state.rewrite_user_prompt, values), images=tuple(state.images) if use_images else ()))
    try:
        response = await model.chat(messages, ChatOptions(temperature=0.3, max_completion_tokens=500 if use_images else 150, thinking=False), context.child('query_rewrite'))
    except (CallCancelled, RunDeadlineExceeded):
        raise
    except Exception:
        return
    parse_understanding(state, response.content)
    state.intent = resolve_intent(state)
    if caption_sink and state.image_description and state.user_message_id:
        try:
            await caption_sink(state, context)
        except Exception:
            pass
    if not needs_retrieval(state.intent, state.web_search_enabled):
        raw = state.intent_system_prompts.get(state.intent, '')
        if isinstance(raw, str) and go_strip(raw):
            state.system_prompt_override = raw
        if not state.system_prompt_override:
            state.system_prompt_override = state.global_intent_system_prompts.get(state.intent, '')

class CppJiebaSegmenter:
    """Pure Python semantic port of pinned CPPJieba MP/Mix/HMM/Query.

    Assets are verified before initialization; no reference-directory access,
    dynamic user dictionary or downloaded models. Direct Go oracle is NOT RUN.
    """
    parity = 'SOURCE_PORT_ORACLE_UNVERIFIED'
    MIN = -3.14e+100

    def __init__(self, asset_directory=None):
        from pathlib import Path
        import hashlib, math
        directory = Path(asset_directory) if asset_directory else Path(__file__).with_name('tokenizer_assets')
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        for name, expected in manifest['files'].items():
            data = (directory / name).read_bytes()
            if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
                raise ValueError('TOKENIZER_ASSET_MISMATCH')
        rows = [line.split() for line in (directory / 'jieba.dict.utf8').read_text(encoding='utf-8').splitlines() if go_strip(line)]
        total = sum((float(row[1]) for row in rows))
        weights = [math.log(float(row[1]) / total) for row in rows]
        self.words = {row[0]: weight for row, weight in zip(rows, weights)}
        self.minimum = min(weights)
        median = sorted(weights)[len(weights) // 2]
        self.user_single = set()
        for line in (directory / 'user.dict.utf8').read_text(encoding='utf-8').splitlines():
            row = line.split()
            if not row:
                continue
            self.words[row[0]] = math.log(int(row[1]) / total) if len(row) == 3 else median
            if len(row[0]) == 1:
                self.user_single.add(row[0])
        self.prefixes = set()
        for word in self.words:
            for n in range(1, len(word)):
                self.prefixes.add(word[:n])
        lines = [go_strip(line) for line in (directory / 'hmm_model.utf8').read_text(encoding='utf-8').splitlines() if go_strip(line) and (not line.startswith('#'))]
        self.starts = list(map(float, lines[0].split()))
        self.transitions = [list(map(float, line.split())) for line in lines[1:5]]
        self.emits = [{pair.split(':')[0]: float(pair.split(':')[1]) for pair in line.split(',')} for line in lines[5:9]]

    def _mp(self, text):
        route = [(0.0, 1)] * (len(text) + 1)
        for i in range(len(text) - 1, -1, -1):
            best = self.MIN
            length = 1
            for n in range(1, min(512, len(text) - i) + 1):
                word = text[i:i + n]
                if n == 1 or word in self.words:
                    value = route[i + n][0] + self.words.get(word, self.minimum)
                    if value > best:
                        best, length = (value, n)
                if word not in self.prefixes:
                    break
            route[i] = (best, length)
        out = []
        i = 0
        while i < len(text):
            n = route[i][1]
            out.append(text[i:i + n])
            i += n
        return out

    def _viterbi(self, text):
        weights = [self.starts[y] + self.emits[y].get(text[0], self.MIN) for y in range(4)]
        paths = []
        for ch in text[1:]:
            new = []
            back = []
            for y in range(4):
                best = self.MIN
                previous = 1
                emission = self.emits[y].get(ch, self.MIN)
                for old in range(4):
                    score = weights[old] + self.transitions[old][y] + emission
                    if score > best:
                        best, previous = (score, old)
                new.append(best)
                back.append(previous)
            weights = new
            paths.append(back)
        state = 1 if weights[1] >= weights[3] else 3
        states = [state]
        for path in reversed(paths):
            state = path[state]
            states.append(state)
        states.reverse()
        out = []
        start = 0
        for i, state in enumerate(states):
            if state % 2:
                out.append(text[start:i + 1])
                start = i + 1
        return out

    def _hmm(self, text):
        out = []
        i = 0
        left = 0
        while i < len(text):
            if ord(text[i]) >= 128:
                i += 1
                continue
            if i > left:
                out.extend(self._viterbi(text[left:i]))
            match = re.match('[a-zA-Z][a-zA-Z0-9]*|[0-9][0-9.]*', text[i:])
            end = i + len(match[0]) if match else i + 1
            out.append(text[i:end])
            i = end
            left = end
        if left < i:
            out.extend(self._viterbi(text[left:i]))
        return out

    def _mix(self, text):
        words = self._mp(text)
        out = []
        i = 0
        while i < len(words):
            word = words[i]
            if len(word) > 1 or word in self.user_single:
                out.append(word)
                i += 1
                continue
            j = i
            while j < len(words) and len(words[j]) == 1 and (words[j] not in self.user_single):
                j += 1
            out.extend(self._hmm(''.join(words[i:j])))
            i = j
        return out

    def __call__(self, text):
        out = []
        for part in re.split('([ \\t\\n，。])', text):
            if not part:
                continue
            if part in ' \t\n，。':
                out.append(part)
                continue
            for word in self._mix(part):
                for n in (2, 3):
                    if len(word) > n:
                        for i in range(len(word) - n + 1):
                            candidate = word[i:i + n]
                            if candidate in self.words:
                                out.append(candidate)
                out.append(word)
        return out
from functools import lru_cache

@lru_cache(maxsize=1)
def default_segmenter():
    return CppJiebaSegmenter()
_UNICODE_P = ((33, 35, 1), (37, 42, 1), (44, 47, 1), (58, 59, 1), (63, 64, 1), (91, 93, 1), (95, 123, 28), (125, 161, 36), (167, 171, 4), (182, 183, 1), (187, 191, 4), (894, 903, 9), (1370, 1375, 1), (1417, 1418, 1), (1470, 1472, 2), (1475, 1478, 3), (1523, 1524, 1), (1545, 1546, 1), (1548, 1549, 1), (1563, 1565, 2), (1566, 1567, 1), (1642, 1645, 1), (1748, 1792, 44), (1793, 1805, 1), (2039, 2041, 1), (2096, 2110, 1), (2142, 2404, 262), (2405, 2416, 11), (2557, 2678, 121), (2800, 3191, 391), (3204, 3572, 368), (3663, 3674, 11), (3675, 3844, 169), (3845, 3858, 1), (3860, 3898, 38), (3899, 3901, 1), (3973, 4048, 75), (4049, 4052, 1), (4057, 4058, 1), (4170, 4175, 1), (4347, 4960, 613), (4961, 4968, 1), (5120, 5742, 622), (5787, 5788, 1), (5867, 5869, 1), (5941, 5942, 1), (6100, 6102, 1), (6104, 6106, 1), (6144, 6154, 1), (6468, 6469, 1), (6686, 6687, 1), (6816, 6822, 1), (6824, 6829, 1), (7002, 7008, 1), (7037, 7038, 1), (7164, 7167, 1), (7227, 7231, 1), (7294, 7295, 1), (7360, 7367, 1), (7379, 8208, 829), (8209, 8231, 1), (8240, 8259, 1), (8261, 8273, 1), (8275, 8286, 1), (8317, 8318, 1), (8333, 8334, 1), (8968, 8971, 1), (9001, 9002, 1), (10088, 10101, 1), (10181, 10182, 1), (10214, 10223, 1), (10627, 10648, 1), (10712, 10715, 1), (10748, 10749, 1), (11513, 11516, 1), (11518, 11519, 1), (11632, 11776, 144), (11777, 11822, 1), (11824, 11855, 1), (11858, 11869, 1), (12289, 12291, 1), (12296, 12305, 1), (12308, 12319, 1), (12336, 12349, 13), (12448, 12539, 91), (42238, 42239, 1), (42509, 42511, 1), (42611, 42622, 11), (42738, 42743, 1), (43124, 43127, 1), (43214, 43215, 1), (43256, 43258, 1), (43260, 43310, 50), (43311, 43359, 48), (43457, 43469, 1), (43486, 43487, 1), (43612, 43615, 1), (43742, 43743, 1), (43760, 43761, 1), (44011, 64830, 20819), (64831, 65040, 209), (65041, 65049, 1), (65072, 65106, 1), (65108, 65121, 1), (65123, 65128, 5), (65130, 65131, 1), (65281, 65283, 1), (65285, 65290, 1), (65292, 65295, 1), (65306, 65307, 1), (65311, 65312, 1), (65339, 65341, 1), (65343, 65371, 28), (65373, 65375, 2), (65376, 65381, 1), (65792, 65794, 1), (66463, 66512, 49), (66927, 67671, 744), (67871, 67903, 32), (68176, 68184, 1), (68223, 68336, 113), (68337, 68342, 1), (68409, 68415, 1), (68505, 68508, 1), (69293, 69461, 168), (69462, 69465, 1), (69510, 69513, 1), (69703, 69709, 1), (69819, 69820, 1), (69822, 69825, 1), (69952, 69955, 1), (70004, 70005, 1), (70085, 70088, 1), (70093, 70107, 14), (70109, 70111, 1), (70200, 70205, 1), (70313, 70731, 418), (70732, 70735, 1), (70746, 70747, 1), (70749, 70854, 105), (71105, 71127, 1), (71233, 71235, 1), (71264, 71276, 1), (71353, 71484, 131), (71485, 71486, 1), (71739, 72004, 265), (72005, 72006, 1), (72162, 72255, 93), (72256, 72262, 1), (72346, 72348, 1), (72350, 72354, 1), (72448, 72457, 1), (72769, 72773, 1), (72816, 72817, 1), (73463, 73464, 1), (73539, 73551, 1), (73727, 74864, 1137), (74865, 74868, 1), (77809, 77810, 1), (92782, 92783, 1), (92917, 92983, 66), (92984, 92987, 1), (92996, 93847, 851), (93848, 93850, 1), (94178, 113823, 19645), (121479, 121483, 1), (125278, 125279, 1))
_UNICODE_S = ((36, 43, 7), (60, 62, 1), (94, 96, 2), (124, 126, 2), (162, 166, 1), (168, 169, 1), (172, 174, 2), (175, 177, 1), (180, 184, 4), (215, 247, 32), (706, 709, 1), (722, 735, 1), (741, 747, 1), (749, 751, 2), (752, 767, 1), (885, 900, 15), (901, 1014, 113), (1154, 1421, 267), (1422, 1423, 1), (1542, 1544, 1), (1547, 1550, 3), (1551, 1758, 207), (1769, 1789, 20), (1790, 2038, 248), (2046, 2047, 1), (2184, 2546, 362), (2547, 2554, 7), (2555, 2801, 246), (2928, 3059, 131), (3060, 3066, 1), (3199, 3407, 208), (3449, 3647, 198), (3841, 3843, 1), (3859, 3861, 2), (3862, 3863, 1), (3866, 3871, 1), (3892, 3896, 2), (4030, 4037, 1), (4039, 4044, 1), (4046, 4047, 1), (4053, 4056, 1), (4254, 4255, 1), (5008, 5017, 1), (5741, 6107, 366), (6464, 6622, 158), (6623, 6655, 1), (7009, 7018, 1), (7028, 7036, 1), (8125, 8127, 2), (8128, 8129, 1), (8141, 8143, 1), (8157, 8159, 1), (8173, 8175, 1), (8189, 8190, 1), (8260, 8274, 14), (8314, 8316, 1), (8330, 8332, 1), (8352, 8384, 1), (8448, 8449, 1), (8451, 8454, 1), (8456, 8457, 1), (8468, 8470, 2), (8471, 8472, 1), (8478, 8483, 1), (8485, 8489, 2), (8494, 8506, 12), (8507, 8512, 5), (8513, 8516, 1), (8522, 8525, 1), (8527, 8586, 59), (8587, 8592, 5), (8593, 8967, 1), (8972, 9000, 1), (9003, 9254, 1), (9280, 9290, 1), (9372, 9449, 1), (9472, 10087, 1), (10132, 10180, 1), (10183, 10213, 1), (10224, 10626, 1), (10649, 10711, 1), (10716, 10747, 1), (10750, 11123, 1), (11126, 11157, 1), (11159, 11263, 1), (11493, 11498, 1), (11856, 11857, 1), (11904, 11929, 1), (11931, 12019, 1), (12032, 12245, 1), (12272, 12283, 1), (12292, 12306, 14), (12307, 12320, 13), (12342, 12343, 1), (12350, 12351, 1), (12443, 12444, 1), (12688, 12689, 1), (12694, 12703, 1), (12736, 12771, 1), (12800, 12830, 1), (12842, 12871, 1), (12880, 12896, 16), (12897, 12927, 1), (12938, 12976, 1), (12992, 13311, 1), (19904, 19967, 1), (42128, 42182, 1), (42752, 42774, 1), (42784, 42785, 1), (42889, 42890, 1), (43048, 43051, 1), (43062, 43065, 1), (43639, 43641, 1), (43867, 43882, 15), (43883, 64297, 20414), (64434, 64450, 1), (64832, 64847, 1), (64975, 65020, 45), (65021, 65023, 1), (65122, 65124, 2), (65125, 65126, 1), (65129, 65284, 155), (65291, 65308, 17), (65309, 65310, 1), (65342, 65344, 2), (65372, 65374, 2), (65504, 65510, 1), (65512, 65518, 1), (65532, 65533, 1), (65847, 65855, 1), (65913, 65929, 1), (65932, 65934, 1), (65936, 65948, 1), (65952, 66000, 48), (66001, 66044, 1), (67703, 67704, 1), (68296, 71487, 3191), (73685, 73713, 1), (92988, 92991, 1), (92997, 113820, 20823), (118608, 118723, 1), (118784, 119029, 1), (119040, 119078, 1), (119081, 119140, 1), (119146, 119148, 1), (119171, 119172, 1), (119180, 119209, 1), (119214, 119274, 1), (119296, 119361, 1), (119365, 119552, 187), (119553, 119638, 1), (120513, 120539, 26), (120571, 120597, 26), (120629, 120655, 26), (120687, 120713, 26), (120745, 120771, 26), (120832, 121343, 1), (121399, 121402, 1), (121453, 121460, 1), (121462, 121475, 1), (121477, 121478, 1), (123215, 123647, 432), (126124, 126128, 4), (126254, 126704, 450), (126705, 126976, 271), (126977, 127019, 1), (127024, 127123, 1), (127136, 127150, 1), (127153, 127167, 1), (127169, 127183, 1), (127185, 127221, 1), (127245, 127405, 1), (127462, 127490, 1), (127504, 127547, 1), (127552, 127560, 1), (127568, 127569, 1), (127584, 127589, 1), (127744, 128727, 1), (128732, 128748, 1), (128752, 128764, 1), (128768, 128886, 1), (128891, 128985, 1), (128992, 129003, 1), (129008, 129024, 16), (129025, 129035, 1), (129040, 129095, 1), (129104, 129113, 1), (129120, 129159, 1), (129168, 129197, 1), (129200, 129201, 1), (129280, 129619, 1), (129632, 129645, 1), (129648, 129660, 1), (129664, 129672, 1), (129680, 129725, 1), (129727, 129733, 1), (129742, 129755, 1), (129760, 129768, 1), (129776, 129784, 1), (129792, 129938, 1), (129940, 129994, 1))

def _category_contains(table, char):
    from bisect import bisect_right
    starts = _CATEGORY_STARTS[id(table)]
    value = ord(char)
    i = bisect_right(starts, value) - 1
    if i < 0:
        return False
    lo, hi, stride = table[i]
    return value <= hi and (value - lo) % stride == 0
_CATEGORY_STARTS = {id(t): tuple((row[0] for row in t)) for t in (_UNICODE_P, _UNICODE_S)}

def is_punctuation_symbol_space(char):
    return _category_contains(_UNICODE_P, char) or _category_contains(_UNICODE_S, char) or unicode_contains(WHITE_SPACE, char)

def go_strip(text):
    start, end = (0, len(text))
    while start < end and unicode_contains(WHITE_SPACE, text[start]):
        start += 1
    while end > start and unicode_contains(WHITE_SPACE, text[end - 1]):
        end -= 1
    return text[start:end]

def go_fields(text):
    words = []
    start = None
    for i, char in enumerate(text):
        if unicode_contains(WHITE_SPACE, char):
            if start is not None:
                words.append(text[start:i])
                start = None
        elif start is None:
            start = i
    if start is not None:
        words.append(text[start:])
    return words
