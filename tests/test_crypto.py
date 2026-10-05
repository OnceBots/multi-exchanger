from cryptography.fernet import Fernet

from app.core.crypto import SecretBox


def test_secret_box_round_trip():
    box = SecretBox(Fernet.generate_key().decode())
    assert box.decrypt(box.encrypt("secret-token")) == "secret-token"
