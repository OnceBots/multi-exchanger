from app.services.passwords import hash_password, verify_password


def test_password_hash_is_not_plaintext_and_verifies():
    record = hash_password("secreto-123")
    assert record["hash"] != "secreto-123"
    assert verify_password("secreto-123", record)
    assert not verify_password("otro", record)


def test_empty_password_rejected():
    try:
        hash_password("")
    except ValueError:
        return
    raise AssertionError("Expected ValueError")
