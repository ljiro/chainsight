from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.settings import Settings
from services.shipment_tracking import webhooks as wh

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
BODY = b'[{"eventID": "1"}]'


def header(secret: str, ts: datetime, body: bytes = BODY) -> str:
    t = int(ts.timestamp())
    return f"t={t},v1={wh.sign(secret, t, body)}"


def test_valid_signature_and_rotation() -> None:
    assert wh.verify_signature(["s1"], header("s1", NOW), BODY, now=NOW, tolerance_s=300) == NOW
    # Old and new secret both accepted during rotation; the sender may send both signatures.
    t = int(NOW.timestamp())
    both = f"t={t},v1={wh.sign('old', t, BODY)},v1={wh.sign('new', t, BODY)}"
    assert wh.verify_signature(["new"], both, BODY, now=NOW, tolerance_s=300) == NOW


@pytest.mark.parametrize(
    ("hdr", "body", "code"),
    [
        (None, BODY, "signature_missing"),
        ("garbage", BODY, "signature_invalid"),
        (header("wrong", NOW), BODY, "signature_invalid"),
        (header("s1", NOW), BODY + b" ", "signature_invalid"),  # body tampered
        (header("s1", NOW - timedelta(minutes=6)), BODY, "signature_expired"),  # replay later
        (header("s1", NOW + timedelta(minutes=6)), BODY, "signature_expired"),
    ],
)
def test_rejections(hdr: str | None, body: bytes, code: str) -> None:
    with pytest.raises(wh.WebhookRejected) as e:
        wh.verify_signature(["s1"], hdr, body, now=NOW, tolerance_s=300)
    assert e.value.code == code and e.value.status == 401


def test_secrets_config() -> None:
    s = Settings(
        _env_file=None, carrier_webhook_secrets='{"maersk": "a", "msc": ["b", "c"], "x": 1}'
    )  # type: ignore[call-arg]
    assert wh.secrets_for(s, "maersk") == ["a"]
    assert wh.secrets_for(s, "msc") == ["b", "c"]
    assert wh.secrets_for(s, "x") == [] and wh.secrets_for(s, "none") == []
    assert wh.secrets_for(Settings(_env_file=None, carrier_webhook_secrets="not json"), "a") == []  # type: ignore[call-arg]


def test_references_extracted_from_dcsa_events() -> None:
    event = {
        "documentReferences": [
            {"documentReferenceType": "BKG", "documentReferenceValue": " abc123 "},
            {"documentReferenceType": "TRD", "documentReferenceValue": "BL9"},
            {"documentReferenceType": "XYZ", "documentReferenceValue": "ignored"},
        ],
        "equipmentReference": "msku1234565",
    }
    assert wh.references_in(event) == {
        ("booking", "ABC123"), ("bill_of_lading", "BL9"), ("container", "MSKU1234565"),
    }  # fmt: skip
