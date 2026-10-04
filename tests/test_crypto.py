from cryptography.fernet import Fernet
from app.core.crypto import SecretBox


def test_secret_box_round_trip():
    box = SecretBox(Fernet.generate_key().decode())
    value = "123456:secret"
    encrypted = box.encrypt(value)
    assert encrypted != value
    assert box.decrypt(encrypted) == value
