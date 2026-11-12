"""What the agent jobs share: the key gate, the streamed call, usage and cost, and plain-text checks.

The key, the client, the error wording and the fallback-aware answer text are the AI reader's (oc/adapters/ai.py),
reused here rather than copied."""
import json
import re

from ..adapters import ai

MODEL = ai.MODEL
FALLBACK_BETA = ai.FALLBACK_BETA
FORMULA_LEAD = ("=", "+", "-", "@")


def connect(factory=None):
    """(client, key), or (None, None) when there is no key: the one gate both jobs pass. With no key nothing is
    built and nothing is sent."""
    key = ai.api_key()
    if not key:
        return None, None
    return (factory or ai.make_client)(key), key


def send(client, req):
    """One request on the beta endpoint, streamed; the final message."""
    with client.beta.messages.stream(**req) as stream:
        return stream.get_final_message()


class Usage:
    """Tokens and US$ over the requests of one job run."""

    def __init__(self):
        self.requests, self.model = 0, None
        self.t = dict(input_tokens=0, cache_write_tokens=0, cache_read_tokens=0, output_tokens=0)

    def add(self, msg):
        self.requests += 1
        self.model = getattr(msg, "model", None) or self.model
        for k, v in ai.tokens(msg).items():
            self.t[k] += v

    @property
    def cost_usd(self):
        return round(ai.cost_usd(self.t), 4)

    def text(self):
        fb = "" if self.model in (None, MODEL) else f", answered by the fallback model {self.model} (costed at {MODEL} prices)"
        t = self.t
        return (f"{self.model or MODEL}{fb}, {self.requests} request(s), about US${self.cost_usd:.2f}: "
                f"{t['input_tokens']:,} input + {t['cache_write_tokens']:,} cache write + "
                f"{t['cache_read_tokens']:,} cache read + {t['output_tokens']:,} output tokens")

    def as_dict(self):
        return dict(model=self.model or MODEL, requests=self.requests, cost_usd=self.cost_usd, **self.t)


def answer(msg):
    """The answer as JSON, or raise ValueError saying why it cannot be used."""
    if msg.stop_reason == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        raise ValueError(f"the AI declined (refusal, category {cat or 'not given'})")
    if msg.stop_reason != "end_turn":
        raise ValueError(f"the answer stopped early ({msg.stop_reason})")
    try:
        return json.loads(ai.final_text(msg))
    except ValueError:
        raise ValueError("the AI's answer was not valid JSON") from None


def error_text(e, key):
    """Plain-English detail for an exception from the client, with the key hidden."""
    return ai._scrub(ai.api_error(e), key)


MARKUP = re.compile(r"[<>`]|\]\(|\*\*|https?://|www\.|^\s*#", re.I | re.M)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
DOMAIN = re.compile(r"(?<![\w@.-])(?:[a-z0-9-]+\.)+(?:com|net|org|io|ai|app|dev|co|au|uk|nz|info|biz|xyz|shop|store|"
                    r"online|site|link|me|ly|gg|tv|us|eu|de|fr|it|es|nl|ch|cn|jp|ru|in|example)(?![a-z0-9-])", re.I)
PHONE = re.compile(r"(?<![\w/])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{1,4}(?:[\s.-]\d{3,4}){1,3}(?![\w/])")


def contact_problem(text):
    """'' unless `text` holds an e-mail address, a link or web address, or a phone number (8 or more digits in phone
    shape). Model output is shown to a person, and a planted address or number must never reach them."""
    t = str(text or "")
    if EMAIL.search(t):
        return "holds an e-mail address"
    if re.search(r"https?://|www\.", t, re.I) or DOMAIN.search(t):
        return "holds a link or a web address"
    if any(sum(c.isdigit() for c in m.group(0)) >= 8 for m in PHONE.finditer(t)):
        return "holds a phone number"
    return ""


def plain_problem(text, cap):
    """Why `text` cannot be shown as plain text, else ''."""
    t = str(text or "").strip()
    if not t:
        return "empty"
    if len(t) > cap:
        return f"longer than {cap} characters"
    if t.startswith(FORMULA_LEAD):
        return f"starts with '{t[0]}'"
    if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", t):
        return "holds control characters"
    contact = contact_problem(t)
    if contact:
        return contact
    if MARKUP.search(t):
        return "holds markup or a link"   # never echoes what it found: it may be planted text
    return ""


def shown(value, cap=200):
    """A rejected model value as the Review may show it: quoted when it is plain text, else withheld."""
    return f"'{value}'" if not plain_problem(value, cap) else "(not shown)"
