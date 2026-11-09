"""The Streamlit page under AppTest, and what it needs installed. Synthetic data only."""
import importlib.metadata
import io
import re
import zipfile
from pathlib import Path

import pandas as pd
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


def test_step_2_tells_the_buyer_to_keep_the_first_rollback():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x-PU-merge-ROLLBACK.xlsx", b"x")
    at = AppTest.from_file(str(PAGE), default_timeout=60)
    at.session_state["final"] = dict(zip=buf.getvalue(), checks=[], errors=pd.DataFrame(), file="x-uploads.zip",
                                     made_from="po.xlsx and catalog.csv, brand config test")
    at.run()
    assert not at.exception
    info = [i.value for i in at.info if "PU-merge-ROLLBACK" in i.value]
    assert len(info) == 1 and ("Keep the first PU-merge-ROLLBACK you make and never use one made after importing "
                               "PU-merge") in info[0]
