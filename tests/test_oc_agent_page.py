"""The agent jobs on the Streamlit page, under AppTest. Synthetic data only; nothing is sent anywhere."""
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import test_oc_page as tp
from oc import service
from oc.adapters import ai

PAGE = str(tp.PAGE)
BOX = "Draft NEW titles and colour words with AI (check every one)"
RECORD = dict(ok=True, cause="Page 2 carries a <b>discount</b> line.", next_step="Ask Colwyn. [link](http://x)",
              who="colwyn", refs=dict(pages=[2], rows=[], checks=["Order confirmation passes its gates (parsed = printed)"]),
              dropped=[], usage=dict(requests=1), usage_text="claude-opus-5-5, 1 request(s), about US$0.02")
FAIL = dict(Check="Order confirmation passes its gates (parsed = printed)", Result="FAIL", Detail="document discount 100.00")


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv(ai.KEY_NAME, raising=False)
    monkeypatch.setattr(ai, "api_key", lambda: None)   # whatever secrets.toml this machine has


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(ai.KEY_NAME, "sk-ant-api03-SENTINEL-must-never-leak-0042")


def _brand(tmp_path, monkeypatch):
    return tp._page_with_brand(tmp_path, monkeypatch, "adapter: otb\noc_label: MM\noc_supplier: X Ltd\n")


def test_no_key_no_titles_box(tmp_path, monkeypatch, no_key):
    at = _brand(tmp_path, monkeypatch)
    assert not [c for c in at.checkbox if c.label == BOX]
    assert not [m for m in at.markdown if "AI explanation" in m.value]


def test_a_key_offers_the_titles_box_off_by_default_saying_what_it_sends(tmp_path, monkeypatch, key):
    at = _brand(tmp_path, monkeypatch)
    (box,) = [c for c in at.checkbox if c.label == BOX]
    assert box.value is False and "amber" in box.help and "never touches Type, prices" in box.help
    assert "stays red, with the AI's suggestion" in box.help
    assert any(c.value == ("Ticking it sends each new product's codes, its cart row and the catalog's and past POs' "
                           "names for its style and colour to Anthropic.") for c in at.caption)


def _boom(*a, **k):
    raise AssertionError("explained again on a rerun")


@pytest.mark.parametrize("step", ["draft", "final"])
def test_an_explanation_is_shown_as_plain_text_and_not_asked_again(tmp_path, monkeypatch, key, step):
    monkeypatch.setattr(service, "explain_draft", _boom)
    monkeypatch.setattr(service, "explain_finalize", _boom)
    at = AppTest.from_file(PAGE, default_timeout=60)
    if step == "draft":
        at.session_state["draft"] = dict(po=None, review=pd.DataFrame(), checks=[FAIL], file="x.xlsx",
                                         made_from="oc.pdf and catalog.csv, brand config t", explain=RECORD)
    else:
        at.session_state["final"] = dict(zip=None, checks=[FAIL], errors=pd.DataFrame(), file="x.zip", run="r",
                                         made_from="po.xlsx and catalog.csv, brand config t", explain=RECORD)
    at.run()
    at.run()
    assert not at.exception
    assert [m.value for m in at.markdown if "AI explanation" in m.value] == ["**AI explanation (check it)**"]
    (text,) = [t.value for t in at.text if t.value.startswith("Cause:")]
    assert text.splitlines()[0] == "Cause: Page 2 carries a <b>discount</b> line."   # shown as typed, never rendered
    assert "Who: this needs Colwyn" in text and "It changes no check" in text
    assert not [m for m in at.markdown if "<b>discount</b>" in m.value or "](http" in m.value]


def test_no_explanation_record_shows_nothing(tmp_path, monkeypatch, key):
    at = AppTest.from_file(PAGE, default_timeout=60)
    at.session_state["draft"] = dict(po=None, review=pd.DataFrame(), checks=[FAIL], file="x.xlsx",
                                     made_from="oc.pdf and catalog.csv, brand config t", explain=None)
    at.run()
    assert not at.exception and not [m for m in at.markdown if "AI explanation" in m.value]


# ---- driving the real buttons: what is sent, and the per-session caps ----------------------------------------
TITLES_CAP, EXPLAIN_CAP = 10, 20


@pytest.fixture
def page(tmp_path, monkeypatch, key):
    """The page with a brand, a catalog and an OC uploaded; service.draft, finalize and the explanations faked."""
    from oc.agent import titles
    seen = dict(draft=[], explain_draft=0, explain_finalize=0, po=None)

    def draft(*a, **kw):
        seen["draft"].append(kw.get("ai_titles"))
        rows = [dict(Check=titles.CHECK, Result="INFO", Detail="drafted")] if kw.get("ai_titles") else []
        return seen["po"], pd.DataFrame(columns=["Row", "Code", "Column", "Value", "Source", "Colour", "Why"]), [FAIL] + rows

    def explain(kind):
        def fn(path, checks, cfg, ai_factory=None):
            seen[kind] += 1
            return RECORD
        return fn
    monkeypatch.setattr(service, "draft", draft)
    monkeypatch.setattr(service, "finalize", lambda *a, **kw: (None, [FAIL], pd.DataFrame()))
    monkeypatch.setattr(service, "explain_draft", explain("explain_draft"))
    monkeypatch.setattr(service, "explain_finalize", explain("explain_finalize"))
    at = _brand(tmp_path, monkeypatch)
    at.file_uploader(key="w_catalog").set_value(("catalog.csv", b"ID\n", "text/csv"))
    at.file_uploader(key="w_oc").set_value(("oc.pdf", b"%PDF-1.4", "application/pdf"))
    at.file_uploader(key="w_po").set_value(("po.xlsx", b"PK", "application/octet-stream"))
    at.run()
    assert not at.exception
    return at, seen


DRAFT_ASK = "Ask the AI what went wrong (sends the PDF's text to Anthropic)"
FINAL_ASK = "Ask the AI what went wrong (sends the PO rows the failing checks name to Anthropic)"


def tick(at):
    at.checkbox(key="w_ai_titles").check().run()
    assert not at.exception


def press(at, label):
    (b,) = [b for b in at.button if b.label == label]
    b.click().run()
    assert not at.exception


def test_the_box_goes_to_the_draft_and_a_refusal_is_explained_only_on_a_click(page):
    at, seen = page
    press(at, "Build draft PO")
    assert seen["draft"] == [False]   # off by default
    tick(at)
    seen["po"] = b"PK"   # a draft was given: nothing to explain, no button
    press(at, "Build draft PO")
    assert seen["draft"] == [False, True] and not [b for b in at.button if b.label == DRAFT_ASK]
    seen["po"] = None
    press(at, "Build draft PO")
    assert seen["explain_draft"] == 0 and not [t for t in at.text if t.value.startswith("Cause:")]   # never automatic
    press(at, DRAFT_ASK)
    assert seen["explain_draft"] == 1 and [t.value for t in at.text if t.value.startswith("Cause:")]
    at.run()
    assert seen["explain_draft"] == 1 and not [b for b in at.button if b.label == DRAFT_ASK]   # kept, not asked again
    press(at, "Make upload files")
    assert seen["explain_finalize"] == 0
    press(at, FINAL_ASK)
    assert seen["explain_finalize"] == 1


def test_title_runs_stop_at_the_session_cap(page):
    at, seen = page
    at.session_state["ai_title_runs"] = TITLES_CAP - 1
    tick(at)
    press(at, "Build draft PO")
    assert seen["draft"] == [True] and at.session_state["ai_title_runs"] == TITLES_CAP
    at.run()   # the box is drawn above the build, so the cap shows from the next rerun (as for AI reads)
    (box,) = [c for c in at.checkbox if c.label == BOX]
    assert box.disabled
    assert any(c.value.startswith(f"AI title drafting is off: this browser session has used its {TITLES_CAP}")
               for c in at.caption)
    press(at, "Build draft PO")
    assert seen["draft"] == [True, False] and at.session_state["ai_title_runs"] == TITLES_CAP


@pytest.mark.parametrize("button,ask,kind", [("Build draft PO", DRAFT_ASK, "explain_draft"),
                                             ("Make upload files", FINAL_ASK, "explain_finalize")])
def test_explanations_stop_at_the_session_cap(page, button, ask, kind):
    at, seen = page
    at.session_state["ai_explains"] = EXPLAIN_CAP - 1
    press(at, button)
    press(at, ask)
    assert seen[kind] == 1 and at.session_state["ai_explains"] == EXPLAIN_CAP
    press(at, button)
    press(at, ask)
    assert seen[kind] == 1 and at.session_state["ai_explains"] == EXPLAIN_CAP
    assert any(c.value.startswith(f"No AI explanation: this browser session has used its {EXPLAIN_CAP}")
               for c in at.caption)


def test_titles_ran_reads_the_job_row():
    from oc.agent import titles
    assert service.titles_ran([dict(Check=titles.CHECK, Result="WARN", Detail="did not run")])
    assert not service.titles_ran([dict(Check="Order confirmation reader", Result="INFO", Detail="x")])
