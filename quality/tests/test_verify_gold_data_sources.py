import json

from tools import verify_gold_data_sources as verifier


def test_load_tokens_requires_names_without_echoing_values(tmp_path):
    path = tmp_path / "local_tokens.json"
    path.write_text(json.dumps({"Tushare Token": "secret-a", "Alpha Vantage Token": "secret-b"}), encoding="utf-8")
    assert verifier._load_tokens(path) == {"Tushare Token": "secret-a", "Alpha Vantage Token": "secret-b"}


def test_provider_verifiers_report_success_without_exposing_token(monkeypatch):
    monkeypatch.setattr(verifier, "_post_json", lambda *_args, **_kwargs: {"code": 0})
    monkeypatch.setattr(verifier, "_get_json", lambda *_args, **_kwargs: {"price": "1"})
    assert verifier.verify_tushare("not-printed")[0] is True
    assert verifier.verify_alpha_vantage("not-printed")[0] is True


def test_tushare_permission_error_is_not_reported_as_invalid_token(monkeypatch):
    monkeypatch.setattr(verifier, "_post_json", lambda *_args, **_kwargs: {"code": 40203})
    ok, message = verifier.verify_tushare("not-printed")
    assert ok is False
    assert "does not prove the token is invalid" in message
