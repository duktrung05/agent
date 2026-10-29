"""Explicit HTTPS origin and single-account access for a private VPS deployment."""

import base64
import binascii
from dataclasses import dataclass, field
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import urlsplit


def public_origin(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme == "https" and parsed.hostname
                 and re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", parsed.hostname)
                 and not parsed.username and not parsed.password
                 and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
                 and (port is None or 1 <= port <= 65535)
                 and not any(char.isspace() for char in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Public origin cần là HTTPS origin, ví dụ https://research.example.com.")
    return "https://" + parsed.hostname + (f":{port}" if port and port != 443 else "")


def password_digest(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)


def create_auth_file(path, username):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
        raise ValueError("Tên đăng nhập cần 1–64 ký tự chữ, số, dấu chấm, gạch dưới hoặc gạch ngang.")
    password = getpass.getpass("Mật khẩu (ít nhất 12 ký tự): ")
    if not 12 <= len(password) <= 256:
        raise ValueError("Mật khẩu cần từ 12 đến 256 ký tự.")
    if password != getpass.getpass("Nhập lại mật khẩu: "):
        raise ValueError("Mật khẩu nhập lại không khớp.")
    salt = secrets.token_bytes(16)
    payload = {"version": 1, "username": username, "salt": salt.hex(),
               "digest": password_digest(password, salt).hex()}
    # Exclusive creation prevents accidentally replacing a working account.
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as output:
        json.dump(payload, output)
        output.write("\n")


@dataclass
class WebAccess:
    origin: str | None = None
    auth_file: str | None = None
    username: str | None = field(default=None, init=False)
    _salt: bytes = field(default=b"", init=False, repr=False)
    _digest: bytes = field(default=b"", init=False, repr=False)
    _verified: bytes = field(default=b"", init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self):
        if self.origin:
            self.origin = public_origin(self.origin)
            if not self.auth_file:
                raise ValueError("Chạy qua tên miền cần --auth-file để bảo vệ dữ liệu và API.")
        if self.auth_file:
            try:
                payload = json.loads(Path(self.auth_file).read_text(encoding="utf-8"))
                self.username = payload["username"]
                self._salt = bytes.fromhex(payload["salt"])
                self._digest = bytes.fromhex(payload["digest"])
                if (payload["version"] != 1 or not isinstance(self.username, str)
                        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", self.username)
                        or len(self._salt) != 16 or len(self._digest) != 32):
                    raise ValueError()
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ValueError("File tài khoản không hợp lệ; tạo bằng --create-auth.") from exc

    def authenticated(self, header):
        if not self.auth_file:
            return True
        if not header or len(header) > 2048:
            return False
        fingerprint = hashlib.sha256(header.encode("utf-8")).digest()
        # Only cache the fingerprint of a successfully verified credential. Serialize
        # password hashing to bound memory even with concurrent failed requests.
        with self._lock:
            if secrets.compare_digest(fingerprint, self._verified):
                return True
            try:
                scheme, encoded = header.split(" ", 1)
                if scheme.lower() != "basic":
                    return False
                user, password = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
                if not 12 <= len(password) <= 256:
                    return False
            except (ValueError, UnicodeError, binascii.Error):
                return False
            digest = password_digest(password, self._salt)
            valid = (secrets.compare_digest(user.encode("utf-8"), self.username.encode("utf-8"))
                     & secrets.compare_digest(digest, self._digest))
            if valid:
                self._verified = fingerprint
            return valid
