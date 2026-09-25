"""sign_payload is the one piece of Phase 6 worth unit testing directly: it has to be
deterministic and it has to change when the timestamp changes (that's what defeats a
replayed old payload with a forged new timestamp). The full signing/delivery/rejection loop
was verified against a running receiver process, not mocked here, see README's Phase 6
section.
"""
import hmac
import hashlib

from shared.webhook_dispatcher import sign_payload


def test_signature_is_deterministic_for_the_same_inputs():
    sig1 = sign_payload("secret", 1000, b'{"a":1}')
    sig2 = sign_payload("secret", 1000, b'{"a":1}')
    assert sig1 == sig2


def test_signature_changes_if_the_timestamp_changes():
    #this is what defeats a replay attack that reuses an old, validly-signed body under a
    #forged timestamp: the signature can't be reused across timestamps.
    sig_old = sign_payload("secret", 1000, b'{"a":1}')
    sig_new = sign_payload("secret", 2000, b'{"a":1}')
    assert sig_old != sig_new


def test_signature_changes_if_the_body_changes():
    sig1 = sign_payload("secret", 1000, b'{"a":1}')
    sig2 = sign_payload("secret", 1000, b'{"a":2}')
    assert sig1 != sig2


def test_signature_matches_hand_computed_hmac():
    #cross-check against the exact same construction a receiver is expected to use
    #(shared/webhook_dispatcher.py's own docstring, and scripts/webhook_test_receiver.py)
    #rather than just checking sign_payload against itself.
    secret, ts, body = "secret", 1234567890, b'{"event":"build.succeeded"}'
    expected = hmac.new(secret.encode(), f"{ts}.".encode()+body, hashlib.sha256).hexdigest()
    assert sign_payload(secret, ts, body) == expected
