"""A brand config on hold: every OC for the brand is refused, before any reader runs. Synthetic data only."""
import re

import pytest

import test_oc_tool as base
from oc import brand as brandm, service
from test_oc_currency import _catalog

REASON = "the store's codes and the OC's do not join yet"


def _held(**kw):
    return {**base.BRAND, "hold": REASON, **kw}


def _no_reader(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a reader ran for a config on hold")
    monkeypatch.setattr(service, "adapter", boom)
    from oc.adapters import ai
    monkeypatch.setattr(ai, "read", boom)


@pytest.mark.parametrize("reader", [None, "ai"])
def test_a_config_on_hold_is_refused_before_any_reader_runs(monkeypatch, reader):
    _no_reader(monkeypatch)
    docs, checks, info = service.read_oc("oc.pdf", _held(), reader=reader)
    assert docs is None and info is None
    assert checks == [{"Check": service.HOLD_CHECK, "Result": "FAIL",
                       "Detail": f"This brand's config is on hold: {REASON}"}]
    assert service.AI_HINT not in checks[0]["Detail"]


def test_a_draft_for_a_config_on_hold_writes_nothing(tmp_path, monkeypatch):
    _no_reader(monkeypatch)
    po, review, checks = service.draft("oc.pdf", _catalog(tmp_path), _held())
    assert po is None and review.empty
    assert [c["Detail"] for c in service.fails(checks)] == [f"This brand's config is on hold: {REASON}"]


def test_a_draft_from_a_cart_for_a_config_on_hold_writes_nothing(tmp_path, monkeypatch):
    """The cart path (no OC yet) reads no reader module either: it is refused like an OC."""
    from oc import cartorder
    monkeypatch.setattr(cartorder, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cart read on hold")))
    po, review, checks = service.draft(None, _catalog(tmp_path), _held(), cart_path=str(tmp_path / "cart.xlsx"))
    assert po is None and review.empty
    assert [c["Detail"] for c in service.fails(checks)] == [f"This brand's config is on hold: {REASON}"]
    docs, checks, cart_ = cartorder.read_order(str(tmp_path / "cart.xlsx"), _held())
    assert docs is None and cart_ is None and checks[0]["Check"] == service.HOLD_CHECK


def test_hold_loads_as_a_sentence():
    assert brandm.from_mapping(dict(base.BRAND, hold=REASON), "t")["hold"] == REASON
    assert "hold" not in brandm.from_mapping(dict(base.BRAND), "t")


@pytest.mark.parametrize("bad", ["", "  ", True, 1, ["x"]])
def test_hold_that_is_not_a_sentence_is_refused_at_load(bad):
    with pytest.raises(brandm.BrandError, match="hold is the reason this brand's config must not be used yet"):
        brandm.from_mapping(dict(base.BRAND, hold=bad), "t")


@pytest.mark.parametrize("extra, want", [
    (dict(on_hold=REASON), "t: unknown key 'on_hold'"),
    (dict(holds=REASON, joinkey="{style}"), "t: unknown keys 'holds', 'joinkey'"),
])
def test_an_unknown_top_level_key_fails_at_load_naming_it(extra, want):
    """Review 2026-11-13-buyer-17 F5: a misspelt key was ignored, so `on_hold:` loaded the brand unheld."""
    with pytest.raises(brandm.BrandError, match=re.escape(want)):
        brandm.from_mapping(dict(base.BRAND, **extra), "t")
    with pytest.raises(brandm.BrandError, match=re.escape(want.replace("t:", "upload:"))):
        brandm.from_text("\n".join(f"{k}: {v!r}" for k, v in dict(base.BRAND, **extra).items()), "upload")


# ---- every path that uses a config fails closed on hold ------------------------------------------------------------
HOLD_ROW = {"Check": brandm.HOLD_CHECK, "Result": "FAIL", "Detail": f"This brand's config is on hold: {REASON}"}


def test_the_page_refuses_to_finalize_for_a_config_on_hold(tmp_path):
    zbytes, checks, errors = service.finalize(str(tmp_path / "no-po.xlsx"), str(tmp_path / "no-catalog.csv"), _held())
    assert zbytes is None and checks == [HOLD_ROW] and errors.empty


def test_finalize_writes_nothing_for_a_config_on_hold(tmp_path):
    from oc import finalize
    out = tmp_path / "out"
    out.mkdir()
    assert finalize.run(str(tmp_path / "no-po.xlsx"), _held(), None, str(out / "run-")) == [HOLD_ROW]
    assert list(out.iterdir()) == []


def test_complete_writes_nothing_for_a_config_on_hold(tmp_path):
    from oc import complete
    out = tmp_path / "PO.xlsx"
    assert complete.run(str(tmp_path / "no-po.xlsx"), [], _held(), None, None, {}, str(out)) == [HOLD_ROW]
    assert not out.exists()


def _held_yaml(tmp_path):
    cfg = tmp_path / "held.yaml"
    cfg.write_text("vendor: X\nadapter: otb\noc_label: ZZ\nseason: S/S 27\nsku_prefix: XX\ncost_multiplier: 1.5\n"
                   f"cost_currency: EUR\nmargin_floor: 2.5\nhold: \"{REASON}\"\n")
    return str(cfg)


def test_the_cli_finalize_and_complete_refuse_a_config_on_hold(tmp_path, capsys):
    from oc import cli
    cat, cfg = _catalog(tmp_path), _held_yaml(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    assert cli.main(["finalize", "--po", str(tmp_path / "no-po.xlsx"), "--catalog", cat, "--brand", "x",
                     "--brand-config", cfg, "--out-prefix", str(out / "run-")]) == 1
    with pytest.raises(SystemExit) as e:
        cli.main(["complete", "--po", str(tmp_path / "no-po.xlsx"), "--oc", "oc.pdf", "--catalog", cat, "--brand", "x",
                  "--brand-config", cfg, "--out", str(out / "PO.xlsx")])
    assert e.value.code == 1 and list(out.iterdir()) == []
    printed = capsys.readouterr().out
    assert printed.count(f"[FAIL] {brandm.HOLD_CHECK}: This brand's config is on hold: {REASON}") == 2
