"""The Streamlit page under AppTest, and what it needs installed. Synthetic data only."""
import importlib.metadata
import io
import re
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "pages" / "1_Order_confirmation_to_PO.py"


def _norm(req):
    return re.sub(r"\s+", "", req.split(";")[0]).lower()


def test_requirements_install_the_authlib_that_st_login_needs():
    """st.login raises unless Authlib is installed, and Community Cloud installs only requirements.txt."""
    need = [_norm(r) for r in importlib.metadata.requires("streamlit")
            if r.lower().startswith("authlib") and 'extra == "auth"' in r]
    have = {_norm(line) for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()}
    assert need and set(need) <= have, (need, sorted(have))


def test_step_2_says_to_use_the_rollback_from_the_same_zip_and_names_its_run_id():
    run = "2026-11-09-1432-mm-ss27"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{run}-PU-merge-ROLLBACK.xlsx", b"x")
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.session_state["final"] = dict(zip=buf.getvalue(), checks=[], errors=pd.DataFrame(), file=f"{run}-uploads.zip",
                                     run=run, made_from="po.xlsx and catalog.csv, brand config test")
    at.run()
    assert not at.exception
    info = [i.value for i in at.info if "PU-merge-ROLLBACK" in i.value]
    assert len(info) == 1
    assert ("Use the PU-merge-ROLLBACK from the same zip as the PU-merge you import, made before importing. "
            f"Every file in this zip carries run id {run}.") in info[0]
    assert "the first PU-merge-ROLLBACK" not in info[0] and "Keep the first" not in info[0]


def test_requirements_install_the_anthropic_sdk_the_ai_reader_imports():
    """Community Cloud installs only requirements.txt; without the SDK the AI reader cannot run."""
    have = [_norm(line) for line in (ROOT / "requirements.txt").read_text().splitlines()]
    assert any(h.startswith("anthropic>=") for h in have), have


BRAND_YAML = ("vendor: X\nseason: S/S 27\nsku_prefix: XX\ncost_multiplier: 1.5\ncost_currency: EUR\n"
              "margin_floor: 2.5\n")


def _page_with_brand(tmp_path, monkeypatch, extra):
    d = tmp_path / "brands"
    d.mkdir()
    (d / "aaa.local.yaml").write_text(BRAND_YAML + extra)
    monkeypatch.setenv("OC_BRAND_DIR", str(d))
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.run()
    assert not at.exception
    return at


@pytest.mark.parametrize("name", ["otb", "admilano", "adidas", "fog"])
def test_a_fixed_reader_brand_offers_the_ai_reader_off_by_default(tmp_path, monkeypatch, name):
    at = _page_with_brand(tmp_path, monkeypatch, f"adapter: {name}\n")
    (box,) = [c for c in at.checkbox if c.label == "Read the OC with AI (for a layout the tool does not know)"]
    assert box.value is False and "no order confirmations found" in box.help
    assert not any("always read by AI" in c.value for c in at.caption)


def test_an_ai_brand_says_its_ocs_are_always_read_by_ai(tmp_path, monkeypatch):
    at = _page_with_brand(tmp_path, monkeypatch, "adapter: ai\noc_supplier: X Ltd\njoin_key: '{style} {colour}'\n")
    assert not [c for c in at.checkbox if "AI" in c.label]
    assert any(c.value.startswith("This brand's order confirmations are always read by AI") for c in at.caption)


def test_the_brand_not_listed_expander_says_what_a_config_holds(tmp_path, monkeypatch):
    at = _page_with_brand(tmp_path, monkeypatch, "adapter: otb\n")
    (exp,) = [e for e in at.expander if e.label == "My brand is not in the list"]
    text = " ".join(m.value for m in exp.markdown)
    for needle in ("needs a brand config before it can read", "Colwyn makes it from example.yaml",
                   "approved vendor list", "SKU prefix", "cost multiplier and the cost currency", "margin floor",
                   "`adapter: ai`", "`oc_supplier`", "`join_key` and `colour_codes`"):
        assert needle in text, needle


def test_after_a_draft_the_page_says_which_reader_was_used(tmp_path, monkeypatch):
    at = _page_with_brand(tmp_path, monkeypatch, "adapter: otb\n")
    ai_row = dict(Check="Order confirmation reader", Result="WARN",
                  Detail="read by AI (claude-opus-5-5) in 40 s, about US$0.50. A quantity read under the wrong size "
                         "passes every gate, so check each line's sizes against the PDF.")
    at.session_state["draft"] = dict(po=None, review=pd.DataFrame(), file="x.xlsx", made_from="oc.pdf",
                                     checks=[ai_row, dict(Check="A gate", Result="FAIL", Detail="broken")])
    at.run()
    assert any(w.value.startswith("Order confirmation read by AI (claude-opus-5-5)") and
               "check each line's sizes against the PDF" in w.value for w in at.warning)
    otb_row = dict(ai_row, Result="INFO", Detail="read by the built-in reader for the OTB layout; no AI was used")
    at.session_state["draft"] = dict(at.session_state["draft"], checks=[otb_row])
    at.run()
    assert any(c.value == "Order confirmation read by the built-in reader for the OTB layout; no AI was used"
               for c in at.caption)
    assert not any("read by AI" in w.value for w in at.warning)
