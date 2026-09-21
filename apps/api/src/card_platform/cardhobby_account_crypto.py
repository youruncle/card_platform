"""Backend-only authenticated encryption; a DB backup alone cannot decrypt secrets."""

import json
import os
import stat
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class AccountCipher:
    def __init__(self, key_file: str):
        path = Path(key_file)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.exists():
            fd, temporary = tempfile.mkstemp(prefix='.cardhobby-key-', dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)  # Atomic publish, never replace a concurrent key.
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError('卡淘加密密钥文件权限必须为 600')
            self.fernet = Fernet(stream.read())

    def encrypt(self, owner, purpose, value):
        return self.fernet.encrypt(json.dumps(
            {'owner': str(owner), 'purpose': purpose, 'value': value}
        ).encode()).decode()

    def decrypt(self, owner, purpose, token):
        try:
            value = json.loads(self.fernet.decrypt(token.encode()))
            if value['owner'] != str(owner) or value['purpose'] != purpose:
                raise InvalidToken
            return value['value']
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise ValueError('卡淘凭据无法解密，请重新绑定账号') from None
