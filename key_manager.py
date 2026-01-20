import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Master Key from Environment or Default
MASTER_PASSWORD = os.getenv("MASTER_KEY", "SENTINEL_AI_MASTER_SECRET_KEY_CHANGE_THIS").encode()

def _get_fernet():
    salt = b'sentinel_salt_' # In production, use unique salt per user
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(MASTER_PASSWORD))
    return Fernet(key)

def encrypt_key(private_key: str) -> str:
    f = _get_fernet()
    return f.encrypt(private_key.encode()).decode()

def decrypt_key(encrypted_key: str) -> str:
    f = _get_fernet()
    return f.decrypt(encrypted_key.encode()).decode()