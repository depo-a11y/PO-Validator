"""The AI OC reader, offline: a fake Anthropic client and a hand-built synthetic PDF. No real OC, no network.

Real-API tests run only with OC_AI_LIVE=1 (they cost money)."""
import base64
import copy
import csv
import io
import json
import os
import sys
import types
import zipfile

import openpyxl
import pytest

import test_oc_tool as base
from oc import brand as brandm, complete, poxlsx, service
from oc.adapters import ai, otb
from oc.resolve import AI_OC, COLOUR
from oc.util import join_key

SENTINEL = "sk-ant-api03-SENTINEL-must-never-leak-0042"


# ---- a synthetic PDF with a real text layer ------------------------------------------
def make_pdf(path, lines):
    """One A4 page of Helvetica text, one string per line; no lines = no text layer."""
    def esc(t):
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    body = "BT /F1 9 Tf 12 TL 40 800 Td " + " ".join(f"({esc(t)}) Tj T*" for t in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(body)} >>\nstream\n{body}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)
    return str(path)


PDF_LINES = [
    "ORDER CONFIRMATION Example Atelier Ltd",
    "Document Number: SO-90001 Document Date: 01 July 2026",
    "Style: EXJK01 Fabric: 100% Cotton Colour: Grey S M L Qty Unit Price Total",
    "Description: Example Jacket 1 2 1 4 EUR 120,00 EUR 480,00",
    "Style: EXTS02 Fabric: 100% Cotton Colour: Black XS S Qty Unit Price Total",
    "Description: Example Tee 2 1 3 EUR 45,50 EUR 136,50",
    "Style: EXCP03 Fabric: 100% Wool Colour: Black ONE SIZE Qty Unit Price Total",
    "Description: Example Cap 2 2 EUR 1.050,00 EUR 2.100,00",
    "Total Qty 9 Net Value EUR 2.716,50",
]


def _line(style, fabric, colour, desc, sizes, qty, price, value):
    return dict(line_no=None, style=style, fabric=fabric, colour=colour, product_class=None, description=desc,
                composition=None, made_in=None, deliver_from=None, deliver_to=None,
                sizes=[dict(size=s, qty_text=q) for s, q in sizes], qty_text=qty, price_text=price,
                line_value_text=value, page=1, rrp_text=None, rrp_currency=None)


RESPONSE = dict(supplier="Example Atelier Ltd", document_kind="order_confirmation", has_size_breakdown=True,
                pages_without_lines=[],
                documents=[dict(number="SO-90001", label=None, date="01 July 2026", collection=None, currency="EUR",
                                total_qty_text="9", net_total_text="EUR 2.716,50", gross_total_text=None, pages=[1],
                                lines=[
    _line("EXJK01", "100% Cotton", "Grey", "Example Jacket", [("S", "1"), ("M", "2"), ("L", "1")], "4",
          "EUR 120,00", "EUR 480,00"),
    _line("EXTS02", "100% Cotton", "Black", "Example Tee", [("XS", "2"), ("S", "1")], "3", "EUR 45,50",
          "EUR 136,50"),
    _line("EXCP03", "100% Wool", "Black", "Example Cap", [("ONE SIZE", "2")], "2", "EUR 1.050,00", "EUR 2.100,00"),
])])


def _expected_line(n, style, fabric, colour, desc, sizes, qty, price, value):
    return dict(line_no=n, style=style, fabric=fabric, colour=colour, product_class="", sizes=sizes, qty=qty,
                price=price, line_value=value, rrp=None, rrp_currency=None, deliver_from=None, deliver_to=None,
                page=1, description=desc, composition=None, made_in=None, reader="ai")


EXPECTED = [dict(doc="SO-90001", date="01 July 2026", collection=None, currency="EUR", total_qty=9, net=2716.5,
                 gross=None, gender=None, pages=[1], reader="ai", issuer="Example Atelier Ltd", label=None, lines=[
    _expected_line(1, "EXJK01", "100% Cotton", "Grey", "Example Jacket", {"S": 1, "M": 2, "L": 1}, 4, 120.0, 480.0),
    _expected_line(2, "EXTS02", "100% Cotton", "Black", "Example Tee", {"XS": 2, "S": 1}, 3, 45.5, 136.5),
    _expected_line(3, "EXCP03", "100% Wool", "Black", "Example Cap", {"ONE SIZE": 2}, 2, 1050.0, 2100.0),
])]


# ---- a fake Anthropic client ------------------------------------------------------------
def message(data=None, text=None, stop="end_turn", category=None, model=ai.MODEL, tokens=(12000, 3000), blocks=None):
    content = blocks if blocks is not None else [
        types.SimpleNamespace(type="thinking", thinking=""),
        types.SimpleNamespace(type="text", text=text if text is not None else json.dumps(data))]
    return types.SimpleNamespace(
        content=content, stop_reason=stop, model=model,
        stop_details=types.SimpleNamespace(type="refusal", category=category, explanation=None) if category else None,
        usage=types.SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1], iterations=None))


class FakeStream:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        self.client.entered += 1
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        self.client.final += 1
        return self.client.msg


class FakeClient:
    """Records every request. Only the streamed beta endpoint exists: a non-streamed or non-beta call fails."""

    def __init__(self, msg=None, error=None):
        self.msg, self.error = msg, error
        self.calls, self.keys, self.entered, self.final = [], [], 0, 0
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return FakeStream(self)

    def factory(self, key):
        self.keys.append(key)
        return self


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(ai.KEY_NAME, SENTINEL)


@pytest.fixture
def pdf(tmp_path):
    return make_pdf(tmp_path / "oc.pdf", PDF_LINES)


def _cfg(**kw):
    d = dict(base.BRAND, vendor="EXAMPLE ATELIER", adapter="ai", season="S/S 27", sku_prefix="EXSS27", oc_label=None,
             oc_supplier="Example Atelier Ltd", join_key="{style} {colour_code}",
             colour_codes={"Grey": "GR", "black": "BK"})
    d.update(kw)
    return brandm.from_mapping(d, "test config", "ex")


def _catalog(tmp_path):
    p = tmp_path / "catalog.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        csv.DictWriter(fh, fieldnames=base.CAT_COLS).writeheader()
    return str(p)


def _refused(pdf, data=None, **msg_kw):
    fake = FakeClient(message(data if data is not None else RESPONSE, **msg_kw))
    with pytest.raises(ai.ReadRefused) as e:
        ai.read(pdf, _cfg(), factory=fake.factory)
    return e.value


# ---- reading -------------------------------------------------------------------------
def test_a_read_returns_otb_shaped_docs_that_pass_the_same_gates(pdf, key):
    fake = FakeClient(message(RESPONSE))
    docs, warns, info = ai.read(pdf, _cfg(), factory=fake.factory)
    assert docs == EXPECTED and warns == []
    assert otb.gate_problems(docs) == []
    assert info["model"] == ai.MODEL and info["input_tokens"] == 12000 and info["output_tokens"] == 3000
    assert info["cost_usd"] == pytest.approx(12000 * 4 / 1e6 + 3000 * 20 / 1e6)
    assert info["raw"] == RESPONSE


def test_the_request_is_pinned(pdf, key):
    fake = FakeClient(message(RESPONSE))
    ai.read(pdf, _cfg(), factory=fake.factory)
    assert fake.keys == [SENTINEL] and len(fake.calls) == 1 and fake.entered == fake.final == 1
    kw = fake.calls[0]
    pdf_bytes = open(pdf, "rb").read()
    doc, prompt = kw["messages"][0]["content"]
    assert kw["messages"][0]["role"] == "user" and len(kw["messages"]) == 1
    assert doc == {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                  "data": base64.standard_b64encode(pdf_bytes).decode("ascii")}}
    assert "\n" not in doc["source"]["data"] and base64.b64decode(doc["source"]["data"]) == pdf_bytes
    assert prompt["type"] == "text" and prompt["text"]
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 64000 and kw["system"] == ai.SYSTEM
    assert kw["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": ai.SCHEMA}}
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert set(kw) == {"model", "max_tokens", "system", "messages", "output_config", "betas", "fallbacks"}


def test_the_schema_is_strict_everywhere():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])
            for v in node["properties"].values():
                walk(v)
        if node.get("type") == "array":
            walk(node["items"])
    walk(ai.SCHEMA)
    line = ai.SCHEMA["properties"]["documents"]["items"]["properties"]["lines"]["items"]["properties"]
    for k in ("qty_text", "price_text", "line_value_text"):
        assert line[k]["type"] == ["string", "null"]  # numbers come back as printed text
    assert line["sizes"]["items"]["properties"]["qty_text"]["type"] == "string"


def test_the_system_prompt_says_transcribe_never_compute():
    s = ai.SYSTEM
    for phrase in ("never compute", "exactly as printed", "only where a quantity is printed",
                   "has_size_breakdown to false", "not a deposit", "ISO 4217", "issued the document"):
        assert phrase in s, phrase


# ---- corrupted extraction is refused -------------------------------------------------------
def _mut(f):
    d = copy.deepcopy(RESPONSE)
    f(d, d["documents"][0], d["documents"][0]["lines"])
    return d


CORRUPT = {
    "one size qty +1": (lambda d, doc, ls: ls[0]["sizes"][1].update(qty_text="3"), ai.GATE_CHECK),
    "price text changed": (lambda d, doc, ls: ls[0].update(price_text="EUR 121,00"), ai.GROUNDING_CHECK),
    "line value text changed": (lambda d, doc, ls: ls[1].update(line_value_text="EUR 136,60"), ai.GROUNDING_CHECK),
    "a line dropped": (lambda d, doc, ls: ls.pop(1), ai.GATE_CHECK),
    "total qty text changed": (lambda d, doc, ls: doc.update(total_qty_text="10"), ai.GATE_CHECK),
    "net total text changed": (lambda d, doc, ls: doc.update(net_total_text="EUR 2.716,60"), ai.GROUNDING_CHECK),
    "style code not in the PDF": (lambda d, doc, ls: ls[2].update(style="EXCP99"), ai.GROUNDING_CHECK),
    "document number not in the PDF": (lambda d, doc, ls: doc.update(number="SO-90002"), ai.GROUNDING_CHECK),
    "currency mismatch": (lambda d, doc, ls: doc.update(currency="USD"), ai.CURRENCY_CHECK),
    "no currency": (lambda d, doc, ls: doc.update(currency=None), ai.CURRENCY_CHECK),
    "supplier mismatch": (lambda d, doc, ls: d.update(supplier="Another House SpA"), ai.SUPPLIER_CHECK),
    "a line with empty sizes": (lambda d, doc, ls: ls[0].update(sizes=[]), ai.SIZES_CHECK),
    "no size breakdown": (lambda d, doc, ls: (d.update(has_size_breakdown=False), [l.update(sizes=[]) for l in ls]),
                          ai.SIZES_CHECK),
    "says no size grid yet lists sizes": (lambda d, doc, ls: d.update(has_size_breakdown=False), ai.SIZES_CHECK),
    "unparseable price": (lambda d, doc, ls: ls[0].update(price_text="about 120"), ai.NUMBERS_CHECK),
    "ambiguous line value": (lambda d, doc, ls: ls[0].update(line_value_text="480,0"), ai.NUMBERS_CHECK),
    "price not printed": (lambda d, doc, ls: ls[0].update(price_text=None), ai.NUMBERS_CHECK),
    "unparseable size qty": (lambda d, doc, ls: ls[0]["sizes"][0].update(qty_text="1.5"), ai.NUMBERS_CHECK),
    "a size listed twice": (lambda d, doc, ls: ls[1]["sizes"].append(dict(size="S", qty_text="1")), ai.NUMBERS_CHECK),
    "no documents": (lambda d, doc, ls: d.update(documents=[]), ai.GATE_CHECK),
}


@pytest.mark.parametrize("name", list(CORRUPT))
def test_a_corrupted_extraction_is_refused_for_the_right_reason(pdf, key, name):
    f, check = CORRUPT[name]
    e = _refused(pdf, _mut(f))
    fails = [c["Check"] for c in e.checks if c["Result"] == "FAIL"]
    assert check in fails, (name, e.checks)
    assert e.info["cost_usd"] > 0  # a refused read still reports what it cost


def test_the_unmutated_response_reads_clean(pdf, key):
    assert ai.read(pdf, _cfg(), factory=FakeClient(message(_mut(lambda *a: None))).factory)[0] == EXPECTED


@pytest.mark.parametrize("kw,needle", [
    (dict(stop="max_tokens"), "output limit"),
    (dict(stop="refusal", category="general_harms"), "declined to read this document (refusal, category general_harms)"),
    (dict(stop="pause_turn"), "stopped early (pause_turn)"),
    (dict(text="{not json"), "not valid JSON"),
    (dict(text='{"supplier": "x"}'), "did not have the expected shape"),
])
def test_a_read_that_did_not_finish_is_refused(pdf, key, kw, needle):
    e = _refused(pdf, **kw)
    assert [c["Check"] for c in e.checks] == [ai.READ_CHECK] and needle in e.checks[0]["Detail"]


def test_a_packing_list_with_no_prices_says_so(pdf, key):
    d = _mut(lambda d, doc, ls: (d.update(document_kind="delivery_note"),
                                 [l.update(price_text=None, line_value_text=None) for l in ls]))
    e = _refused(pdf, d)
    by = {c["Check"]: c for c in e.checks}
    assert "packing list or delivery note carries no prices" in by[ai.NUMBERS_CHECK]["Detail"]
    assert by[ai.KIND_CHECK]["Result"] == "FAIL" and "delivery note" in by[ai.KIND_CHECK]["Detail"]


@pytest.mark.parametrize("kind", ["invoice", "delivery_note"])
def test_an_invoice_or_delivery_note_is_refused_as_shipped_not_ordered(pdf, key, kind):
    e = _refused(pdf, _mut(lambda d, doc, ls: d.update(document_kind=kind)))
    (k,) = [c for c in e.checks if c["Check"] == ai.KIND_CHECK]
    assert k["Result"] == "FAIL" and "shipped, not what was ordered" in k["Detail"]


def test_a_proforma_with_sizes_reads_with_a_warning(pdf, key):
    docs, warns, _ = ai.read(pdf, _cfg(), factory=FakeClient(message(
        _mut(lambda d, doc, ls: d.update(document_kind="proforma_invoice")))).factory)
    assert docs == EXPECTED and [(w["Check"], w["Result"]) for w in warns] == [(ai.KIND_CHECK, "WARN")]


def test_the_supplier_fail_names_what_was_read_and_the_brand(pdf, key):
    e = _refused(pdf, _mut(lambda d, doc, ls: d.update(supplier="Another House SpA")))
    detail = next(c["Detail"] for c in e.checks if c["Check"] == ai.SUPPLIER_CHECK)
    assert "'Another House SpA'" in detail and "EXAMPLE ATELIER" in detail and "My brand is not in the list" in detail


def test_supplier_match_ignores_case_and_punctuation():
    assert ai.suppliers_match("EXAMPLE ATELIER LTD.", ["Example Atelier Ltd"])
    assert ai.suppliers_match("Margiela S.A.S.", ["x", "MARGIELA SAS"])
    assert not ai.suppliers_match("Example Atelier", ["Example Atelier Ltd"])
    assert not ai.suppliers_match("", [""])


# ---- grounding -------------------------------------------------------------------------
def test_no_text_layer_is_refused_before_anything_is_sent(tmp_path, key):
    scan = make_pdf(tmp_path / "scan.pdf", [])
    assert ai.pdf_text(scan).strip() == ""
    fake = FakeClient(message(RESPONSE))
    with pytest.raises(ai.ReadRefused) as e:
        ai.read(scan, _cfg(), factory=fake.factory)
    assert [(c["Check"], c["Result"], c["Detail"]) for c in e.value.checks] == [(ai.TEXT_CHECK, "FAIL", ai.NO_TEXT)]
    assert ai.NO_TEXT == "this PDF has no text to check the AI reading against (a scan?); ask Colwyn"
    assert fake.calls == [] and fake.keys == [] and e.value.info is None


def test_bold_text_printed_twice_is_still_found(tmp_path, key):
    """Some proformas draw bold glyphs twice, so the text layer reads 'PPRROOFFOORRMMAA' and '44' for 4."""
    lines = [l.replace("SO-90001", "SSOO--9900000011") for l in PDF_LINES]
    doubled = make_pdf(tmp_path / "bold.pdf", lines)
    assert "SO-90001" not in ai.pdf_text(doubled)
    docs, warns, _ = ai.read(doubled, _cfg(), factory=FakeClient(message(RESPONSE)).factory)
    assert docs == EXPECTED and warns == []
    assert ai.undouble("PPRROOFFOORRMMAA - Deposit 44 1488.60") == "PROFORMA - Deposit 4 1488.60"


def test_amounts_are_found_without_their_currency_mark():
    hay = ai.Haystack("Total € 1,488.60 and 30.00")
    assert hay.has("EUR 1,488.60", amount=True) and hay.has("€ 30.00", amount=True)
    assert not hay.has("€ 1,488.61", amount=True) and not hay.has("", amount=True)


# ---- numbers -------------------------------------------------------------------------
@pytest.mark.parametrize("text,value", [
    ("€ 1,488.60", 1488.6), ("1.234,56", 1234.56), ("315.610,00", 315610.0), ("EUR 100,00", 100.0),
    ("100", 100.0), ("$1,962.00", 1962.0), ("1488.60", 1488.6), ("1,50", 1.5), ("100,00 EUR", 100.0),
    ("US$ 490.50", 490.5), ("1 962,00", 1962.0), ("1,234,567.89", 1234567.89),
])
def test_money_parses_printed_amounts(text, value):
    assert ai.parse_money(text) == value


@pytest.mark.parametrize("text", ["1,488", "1.234", "12.5", "1.488.60", "1,234,56", "about 120", "-", "", None,
                                  "1,23,456.00", "-100,00", "100,000", "1.234.56,00"])
def test_money_refuses_what_is_ambiguous(text):
    assert ai.parse_money(text) is None


@pytest.mark.parametrize("text,value", [("12", 12), (" 4 ", 4), ("1,234", 1234), ("1.234", 1234)])
def test_quantities_parse(text, value):
    assert ai.parse_int(text) == value


@pytest.mark.parametrize("text", ["1.5", "4 pcs", "1,23", "", None, "-1"])
def test_quantities_refuse_what_is_not_a_count(text):
    assert ai.parse_int(text) is None


def test_a_dash_size_cell_is_left_out(pdf, key):
    docs, _, _ = ai.read(pdf, _cfg(), factory=FakeClient(message(
        _mut(lambda d, doc, ls: ls[1]["sizes"].append(dict(size="M", qty_text="-"))))).factory)
    assert docs[0]["lines"][1]["sizes"] == {"XS": 2, "S": 1}


# ---- the key, the client and the service errors --------------------------------------------
def test_api_key_comes_from_the_environment_then_the_secrets(monkeypatch):
    monkeypatch.delenv(ai.KEY_NAME, raising=False)
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={ai.KEY_NAME: "from-secrets"}))
    assert ai.api_key() == "from-secrets"
    monkeypatch.setenv(ai.KEY_NAME, "from-env")
    assert ai.api_key() == "from-env"
    monkeypatch.delenv(ai.KEY_NAME)

    class Broken:
        def __getitem__(self, k):
            raise FileNotFoundError("no secrets.toml")
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets=Broken()))
    assert ai.api_key() is None
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={}))
    assert ai.api_key() is None


def test_no_key_is_a_plain_fail_and_nothing_is_sent(tmp_path, pdf, monkeypatch):
    monkeypatch.delenv(ai.KEY_NAME, raising=False)
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={}))
    fake = FakeClient(message(RESPONSE))
    po, review, checks = service.draft(pdf, _catalog(tmp_path), _cfg(), ai_factory=fake.factory)
    assert po is None and fake.keys == [] and fake.calls == []
    assert [c["Detail"] for c in service.fails(checks)] == [ai.NO_KEY]
    assert ai.NO_KEY == ("The AI reader is not set up: the app has no ANTHROPIC_API_KEY in its secrets. Ask Colwyn.")
    row = next(c for c in checks if c["Check"] == service.READER_CHECK)
    assert row["Result"] == "INFO" and row["Detail"].startswith("not read")


def _sdk_errors():
    import anthropic
    import httpx2
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(cls, code, msg):
        return cls(msg, response=httpx2.Response(code, request=req), body=None)
    return [
        (status(anthropic.AuthenticationError, 401, f"invalid x-api-key {SENTINEL}"), "refused the app's API key"),
        (status(anthropic.PermissionDeniedError, 403, "no"), "refused the app's API key"),
        (status(anthropic.RateLimitError, 429, "slow down"), "try again in a few minutes"),
        (status(anthropic.OverloadedError, 529, "Overloaded"), "try again in a few minutes"),
        (status(anthropic.InternalServerError, 500, "boom"), "try again in a few minutes"),
        (anthropic.APITimeoutError(request=req), "try again in a few minutes"),
        (anthropic.APIConnectionError(request=req), "try again in a few minutes"),
        (status(anthropic.BadRequestError, 400, "PDF has more than 100 pages"),
         "rejected the request: PDF has more than 100 pages"),
        (status(anthropic.NotFoundError, 404, "model not found"), "answered with an error (404)"),
        (RuntimeError(f"socket said {SENTINEL}"), "the AI request failed (RuntimeError"),
    ]


@pytest.mark.parametrize("i", range(10))
def test_sdk_errors_become_plain_fails_without_the_key(pdf, key, i):
    err, needle = _sdk_errors()[i]
    with pytest.raises(ai.ReadRefused) as e:
        ai.read(pdf, _cfg(), factory=FakeClient(error=err).factory)
    (c,) = e.value.checks
    assert c["Check"] == ai.READ_CHECK and needle in c["Detail"], c
    assert SENTINEL not in c["Detail"] and SENTINEL not in str(e.value) and e.value.info is None


def _all_text(po, review, checks):
    out = [json.dumps(checks, default=str), review.to_csv() if review is not None else ""]
    if po:
        with zipfile.ZipFile(io.BytesIO(po)) as z:
            out += [z.read(n).decode("utf-8", "replace") for n in z.namelist()]
    return "\n".join(out)


def test_the_key_is_in_no_check_review_workbook_or_log(tmp_path, pdf, key, capsys, caplog):
    caplog.set_level("DEBUG")
    fake = FakeClient(message(RESPONSE))
    po, review, checks = service.draft(pdf, _catalog(tmp_path), _cfg(), ai_factory=fake.factory)
    assert fake.keys == [SENTINEL] and po is not None
    err = _sdk_errors()[-1][0]  # an error whose own message carries the key, and is echoed (scrubbed)
    po2, review2, checks2 = service.draft(pdf, _catalog(tmp_path), _cfg(), ai_factory=FakeClient(error=err).factory)
    out = capsys.readouterr()
    blob = "\n".join([_all_text(po, review, checks), _all_text(po2, review2, checks2), out.out, out.err, caplog.text])
    assert SENTINEL not in blob and "[hidden]" in blob


def test_the_real_client_factory_takes_the_key(monkeypatch):
    c = ai.make_client(SENTINEL)
    assert c.api_key == SENTINEL and SENTINEL not in repr(c)


def test_text_after_the_last_fallback_switch_is_the_answer():
    blocks = [types.SimpleNamespace(type="text", text="{partial"),
              types.SimpleNamespace(type="fallback"),
              types.SimpleNamespace(type="thinking", thinking=""),
              types.SimpleNamespace(type="text", text='{"a": 1}')]
    assert ai.final_text(types.SimpleNamespace(content=blocks)) == '{"a": 1}'


def test_a_fallback_model_is_named_in_the_reader_row(pdf, key):
    _, _, info = ai.read(pdf, _cfg(), factory=FakeClient(message(RESPONSE, model="claude-opus-4-8")).factory)
    row = ai.reader_check(info)
    assert "answered by the fallback model claude-opus-4-8" in row["Detail"] and info["model"] == "claude-opus-4-8"


# ---- the service, end to end on a header-only catalog --------------------------------------
def test_draft_marks_every_ai_read_value_amber_and_says_which_reader(tmp_path, pdf, key):
    fake = FakeClient(message(RESPONSE))
    po, review, checks = service.draft(pdf, _catalog(tmp_path), _cfg(), ai_factory=fake.factory)
    assert po is not None and not service.blocking(checks)
    oc = review[review["Source"].isin(["OC", AI_OC])]
    assert len(oc) and set(oc["Source"]) == {AI_OC} and set(oc["Colour"]) == {"amber"}
    assert oc["Why"].str.startswith("read by AI from SO-90001 line ").all()
    assert "green" not in set(review[review["Column"].isin(["Order Qty", "Wholesale Price", poxlsx.CODE])]["Colour"])
    codes = set(review[review["Column"] == poxlsx.CODE]["Value"])
    assert codes == {"EXJK01 GR", "EXTS02 BK", "EXCP03 BK"}
    ws = openpyxl.load_workbook(io.BytesIO(po))["Sheet1"]
    qty_col = poxlsx.PO_HEADER.index("Order Qty") + 1
    assert {ws.cell(r, qty_col).fill.fgColor.rgb for r in range(2, ws.max_row + 1)} == {"00FFEB9C"}
    assert sum(ws.cell(r, qty_col).value for r in range(2, ws.max_row + 1)) == 9
    row = next(c for c in checks if c["Check"] == service.READER_CHECK)
    assert row["Result"] == "WARN" and "claude-opus-5-5" in row["Detail"] and "US$0.11" in row["Detail"]
    for phrase in ("Every quantity, size and price came from an AI reading", "passed the same gates",
                   "Each line's code, colour, price and line value were found in that line's part of the PDF text, "
                   "and its size names near the line",
                   "quantities and totals are too short to look up, so they were checked by arithmetic only",
                   "Check every line's code, colour, sizes, quantities and price against the PDF."):
        assert phrase in row["Detail"], phrase
    assert "every code and amount" not in row["Detail"]
    assert next(c for c in checks if c["Check"] == "OC totals reproduced (parsed = printed)")["Result"] == "PASS"
    assert COLOUR[AI_OC] == "amber"


def test_a_refused_read_gives_no_workbook_and_still_says_what_it_cost(tmp_path, pdf, key):
    fake = FakeClient(message(_mut(lambda d, doc, ls: doc.update(currency="USD"))))
    po, review, checks = service.draft(pdf, _catalog(tmp_path), _cfg(), ai_factory=fake.factory)
    assert po is None and review.empty
    assert [c["Check"] for c in service.blocking(checks)] == [ai.CURRENCY_CHECK]
    row = next(c for c in checks if c["Check"] == service.READER_CHECK)
    assert row["Result"] == "WARN" and "refused" in row["Detail"] and "US$" in row["Detail"]


def test_an_unmapped_colour_is_a_fail_naming_every_one(tmp_path, pdf, key):
    po, _, checks = service.draft(pdf, _catalog(tmp_path), _cfg(colour_codes={"GREY": "GR"}),
                                  ai_factory=FakeClient(message(RESPONSE)).factory)
    assert po is None
    (f,) = service.fails(checks)
    assert f["Check"] == "Every OC colour has a code in the brand config (colour_codes)"
    assert "Black" in f["Detail"] and "colour_codes" in f["Detail"] and "Grey" not in f["Detail"].split(":")[1]


def test_reader_ai_forces_the_ai_reader_for_an_otb_brand(tmp_path, pdf, key, monkeypatch):
    monkeypatch.setattr(service, "adapter", lambda cfg: pytest.fail("the otb reader ran"))
    cfg = _cfg(adapter="otb", join_key=None, colour_codes={}, oc_supplier="Example Atelier Ltd", oc_label="MM")
    read = copy.deepcopy(RESPONSE)
    read["documents"][0]["collection"] = "MM MALE RTW AVP"
    fake = FakeClient(message(read))
    po, review, checks = service.draft(pdf, _catalog(tmp_path), cfg, reader="ai", ai_factory=fake.factory)
    assert len(fake.calls) == 1 and po is not None
    assert set(review[review["Column"] == poxlsx.CODE]["Value"]) == {"EXJK01100% COTTON GREY", "EXTS02100% COTTON BLACK",
                                                                    "EXCP03100% WOOL BLACK"}


def test_an_otb_brand_with_no_oc_supplier_cannot_be_read_by_ai(tmp_path, pdf, key):
    cfg = brandm.from_mapping(dict(base.BRAND), "test config", "mm")
    fake = FakeClient(message(RESPONSE))
    po, _, checks = service.draft(pdf, _catalog(tmp_path), cfg, reader="ai", ai_factory=fake.factory)
    assert po is None and fake.calls == []
    (f,) = service.fails(checks)
    assert f["Check"] == ai.SETUP_CHECK and "oc_supplier" in f["Detail"]


def test_the_otb_reader_row_is_info_and_no_confirmation_points_at_the_ai_box(tmp_path, monkeypatch):
    def parse_checked(path):
        raise otb.GateError(["no order confirmations found"])
    monkeypatch.setattr(service, "adapter", lambda cfg: types.SimpleNamespace(parse_checked=parse_checked))
    po, _, checks = service.draft("oc.pdf", _catalog(tmp_path), dict(base.BRAND, _name="mm", _path="t"))
    row = next(c for c in checks if c["Check"] == service.READER_CHECK)
    assert row["Result"] == "INFO" and "no AI was used" in row["Detail"]
    (f,) = service.fails(checks)
    assert f["Detail"] == "no order confirmations found. " + service.AI_HINT
    assert service.AI_HINT == ("If this is the right brand and its layout changed, tick 'Read the OC with AI' and "
                               "build again (the box needs oc_supplier in the brand's config).")


def test_complete_marks_a_price_taken_from_an_ai_read_amber(tmp_path):
    cat = base.catm.load(_write_one_product_catalog(tmp_path))
    docs = copy.deepcopy(base._docs())
    for l in docs[0]["lines"]:
        l["reader"] = "ai"
    row = {"Command": "MERGE", "Title": "Replica Sneaker in White", "Vendor": "MAISON MARGIELA",
           "Type": "MEN SHOES SNEAKERS", "Option2 Value": "41", "Variant SKU": "ZXFW26101", "Variant Price": 800.0,
           poxlsx.CODE: "S99WS0001P0001 101", "Order Qty": 1, "Wholesale Price": 150.0, "Total Order": 150.0,
           "Variant Cost": 225.0, poxlsx.COST_PRICE: "EUR 150", poxlsx.MADE_IN: "Italy"}
    hand = tmp_path / "hand.xlsx"
    base._hand_po(hand, [row])
    out = tmp_path / "po.xlsx"
    complete.run(str(hand), docs, base.BRAND, cat, base._hist(), base._tags(), str(out),
                 price_overrides={"S99WS0001P0001 101": 200.0})
    ws = openpyxl.load_workbook(out)["Review"]
    hdr = [c.value for c in ws[1]]
    rows = [dict(zip(hdr, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]
    price = [r for r in rows if r["Column"] == "Wholesale Price"]
    assert price and all(r["Source"] == AI_OC and r["Colour"] == "amber" for r in price)
    assert price[0]["Why"].startswith("read by AI from SS - 1 line 1; OC confirms EUR 200")
    po = openpyxl.load_workbook(out).worksheets[0]
    assert po.cell(2, poxlsx.PO_HEADER.index("Wholesale Price") + 1).fill.fgColor.rgb == "00FFEB9C"


def _write_one_product_catalog(tmp_path):
    rows = [base._variant("1", "Replica Sneaker in White", "MEN SHOES SNEAKERS", "Active", "S99WS0001P0001 101",
                          "WHITE", s, f"ZXFW26{100 + i}", "800.00", "800.00", "310.00", i == 0)
            for i, s in enumerate(["40", "41", "42"])]
    return base._write_catalog(tmp_path / "cat.csv", rows, base.CAT_COLS)


# ---- the brand's join key ------------------------------------------------------------------
@pytest.mark.parametrize("style,fabric,colour", [
    ("S99WZ0004", "P0004", "T0004"), ("S11ZZ0001", "M00001", "510"), ("SB1ZZ0001", None, "T0001"),
    ("S99WZ0004", "P0004", None), (" s99wz0004 ", "p0004 ", " t0004"), ("EXHD04", "", "GR")])
def test_the_default_join_key_is_todays_rule_exactly(style, fabric, colour):
    assert brandm.code_for({}, style, fabric, colour) == join_key(style, fabric, colour)
    assert brandm.code_for(dict(base.BRAND), style, fabric, colour) == join_key(style, fabric, colour)
    cfg = brandm.from_mapping(dict(base.BRAND), "t")
    assert cfg["join_key"] == "{style}{fabric} {colour}"
    assert brandm.code_for(cfg, style, fabric, colour) == join_key(style, fabric, colour)


def test_the_otb_draft_codes_are_unchanged():
    from oc.draft import oc_lines
    lines = oc_lines(base._docs(), brandm.from_mapping(dict(base.BRAND), "t"))
    assert [l["code"] for l in lines] == ["S99WS0001P0001 101", "S99WS0001P0001 900"]
    assert [l["code"] for l in oc_lines(base._docs())] == [l["code"] for l in lines]


def test_a_colour_code_join_key_builds_the_store_code():
    cfg = _cfg()
    assert brandm.code_for(cfg, "EXHD04", "100% Cotton", "grey ") == "EXHD04 GR"
    with pytest.raises(brandm.ColourCodeError, match="Navy"):
        brandm.code_for(cfg, "EXHD04", None, "Navy")


@pytest.mark.parametrize("extra,needle", [
    (dict(oc_supplier=None), "adapter: ai needs oc_supplier. Add oc_supplier: the company name printed"),
    (dict(join_key=None), "adapter: ai needs join_key. Add join_key: how the store's manufacture_code is built"),
    (dict(oc_supplier=None, join_key=None), "adapter: ai needs oc_supplier and join_key"),
    (dict(join_key="{style} {color}"), "may use only {style}, {fabric}, {colour}, {colour_code}, each written "
                                         "exactly so (no format specs, conversions or other braces); not allowed: {color}"),
    (dict(join_key="{style} {0}"), "not allowed: {0}"),
    (dict(join_key="{style} {}"), "not allowed: {}"),
    (dict(join_key="{style"), "not allowed: {style"),
    (dict(join_key="{style} }"), "may use only"),
    (dict(join_key="ABC"), "may use only"),
    (dict(join_key="{style} {colour_code}", colour_codes=None), "no colour_codes table"),
    (dict(adapter="pdfminer"), "adapter must be one of otb, admilano, adidas, fog, ai"),
    (dict(oc_supplier=[""]), "oc_supplier is a company name or a list"),
])
def test_a_bad_reader_config_fails_at_load_saying_what_to_add(extra, needle):
    d = dict(base.BRAND, adapter="ai", oc_supplier="X Ltd", join_key="{style} {colour_code}",
             colour_codes={"GREY": "GR"})
    for k, v in extra.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    with pytest.raises(brandm.BrandError) as e:
        brandm.from_mapping(d, "test config")
    assert needle in str(e.value), str(e.value)


@pytest.mark.parametrize("tpl", ["{style:>999999999}", "{style!r} {colour}", "{style.upper} {colour}",
                                 "{style[0]} {colour}", "{style}{fabric:{colour}}", "{{style}} {colour}"])
def test_a_join_key_with_a_format_spec_conversion_or_attribute_is_refused_at_load(tpl):
    """A config can be an upload on the hosted page: nothing in it may make the server build a string."""
    d = dict(base.BRAND, adapter="ai", oc_supplier="X Ltd", join_key=tpl, colour_codes={"GREY": "GR"})
    with pytest.raises(brandm.BrandError, match="no format specs, conversions or other braces"):
        brandm.from_mapping(d, "uploaded file x.yaml")


def test_the_join_key_is_filled_by_substitution_never_by_format():
    """Even a template that skipped the load check is copied literally, never interpreted."""
    assert brandm.code_for({"join_key": "{style:>40}|{style}"}, "ab", None, None) == "{STYLE:>40}|AB"
    assert brandm.code_for({"join_key": "{style!r} {colour}"}, "ab", None, "x") == "{STYLE!R} X"


def test_an_ai_config_from_yaml_loads():
    text = "\n".join(["vendor: X", "adapter: ai", "season: S/S 27", "sku_prefix: XX", "cost_multiplier: 1.5",
                      "cost_currency: EUR", "margin_floor: 2.5", "oc_supplier: [X Ltd, X Limited]",
                      "join_key: '{style} {colour_code}'", "colour_codes: {Grey: GR}"])
    cfg = brandm.from_text(text, "uploaded file x.yaml", "x")
    assert cfg["oc_supplier"] == ["X Ltd", "X Limited"] and cfg["colour_codes"] == {"GREY": "GR"}


def test_the_example_config_documents_the_ai_keys():
    text = open(os.path.join(os.path.dirname(brandm.__file__), "brands", "example.yaml")).read()
    for key in ("adapter:", "oc_supplier", "join_key", "colour_codes"):
        assert key in text
    assert brandm.from_text(text, "example.yaml")["join_key"] == brandm.DEFAULT_JOIN_KEY


# ---- live (costs money) -----------------------------------------------------------------------
LIVE = os.environ.get("OC_AI_LIVE") == "1"


@pytest.mark.skipif(not LIVE, reason="set OC_AI_LIVE=1 (and ANTHROPIC_API_KEY) to call the real API")
def test_live_the_synthetic_oc_reads_clean(pdf):
    docs, warns, info = ai.read(pdf, _cfg())
    assert otb.gate_problems(docs) == [] and otb.totals(docs)["units"] == 9
    assert info["input_tokens"] > 0 and info["cost_usd"] > 0
