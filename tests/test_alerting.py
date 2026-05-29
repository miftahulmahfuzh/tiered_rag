import logging

from tiered_rag.alerting import Alerter, GapAlert


def test_alert_is_collected_in_memory():
    a = Alerter()
    a.alert(GapAlert(kind="abstain", query="capital of France?", answer="I don't know"))
    assert len(a.alerts) == 1
    assert a.alerts[0].kind == "abstain"


def test_alert_emits_a_structured_log_line(caplog):
    a = Alerter()
    with caplog.at_level(logging.WARNING, logger="tiered_rag.alerts"):
        a.alert(GapAlert(kind="unverified", query="q", answer="bad", reason="claim X unsupported"))
    assert any("unverified" in r.getMessage() for r in caplog.records)


def test_no_webhook_call_when_url_empty(monkeypatch):
    # If a webhook were attempted with an empty URL the test HTTP layer would be touched;
    # assert the alerter does not call out when no URL is configured.
    called = {"n": 0}
    import tiered_rag.alerting as al
    monkeypatch.setattr(al.httpx, "post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    Alerter(webhook_url="").alert(GapAlert(kind="abstain", query="q", answer="a"))
    assert called["n"] == 0


def test_webhook_failure_is_swallowed(monkeypatch):
    import tiered_rag.alerting as al

    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(al.httpx, "post", _boom)
    # must not raise
    Alerter(webhook_url="http://example.test/hook").alert(
        GapAlert(kind="unverified", query="q", answer="a"))
