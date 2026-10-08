import base64
import hashlib
import hmac
import secrets


_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LENGTH = 32


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        pin.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LENGTH,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_pin(pin: str, encoded_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_value, digest_value = encoded_hash.split("$")
        if algorithm != "scrypt":
            return False
        n_value, r_value, p_value = int(n), int(r), int(p)
        if n_value > 2**18 or r_value > 32 or p_value > 4:
            return False
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
        actual = hashlib.scrypt(
            pin.encode("utf-8"),
            salt=salt,
            n=n_value,
            r=r_value,
            p=p_value,
            dklen=len(expected),
        )
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)
