"""Relay tickets (cloud/cloud_ticket.py): signed by the control plane, checked by a
worker with no call, and worth nothing without the key they name."""

import cloud_ticket as t

SECRET = "s"


def test_a_ticket_round_trips():
    p = t.verify(SECRET, t.sign(SECRET, "m1", "ca1", 3, "key", "Club"))
    assert (p["m"], p["n"], p["w"], p["o"]) == ("m1", "ca1", 3, "Club")
    assert p["k"] == t.key_hash("key")


def test_the_key_itself_is_not_in_it():
    token = t.sign(SECRET, "m1", "ca1", 1, "the-relay-key")
    assert "the-relay-key" not in t._unb64(token.split(".")[0]).decode()


def test_another_secret_does_not_verify():
    assert t.verify("other", t.sign(SECRET, "m1", "ca1", 1, "k")) is None


def test_an_edited_payload_does_not_verify():
    body, mac = t.sign(SECRET, "m1", "ca1", 1, "k").split(".")
    forged = t._b64(t._unb64(body).replace(b'"w":1', b'"w":2'))
    assert t.verify(SECRET, f"{forged}.{mac}") is None


def test_it_expires_after_a_meet_day():
    token = t.sign(SECRET, "m1", "ca1", 1, "k", now=1000)
    assert t.verify(SECRET, token, now=1000 + t.TTL_SECONDS - 1)
    assert t.verify(SECRET, token, now=1000 + t.TTL_SECONDS + 1) is None


def test_garbage_is_refused_not_raised():
    for junk in (None, "", "abc", "a.b.c", "!!!.???", 42):
        assert t.verify(SECRET, junk) is None


def test_no_secret_verifies_nothing():
    assert t.verify("", t.sign(SECRET, "m1", "ca1", 1, "k")) is None
