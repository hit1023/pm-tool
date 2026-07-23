import io

import paramiko

from . import crypto


def _safe_read(stream) -> str:
    """シャットダウンで接続が切断される場合があるため、読み取り失敗を許容する。"""
    try:
        return stream.read(4096).decode(errors="replace")
    except Exception:
        return ""


def _load_private_key(key_text: str, passphrase: str | None):
    key_classes = (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey, paramiko.DSSKey)
    last_error: Exception | None = None
    for key_cls in key_classes:
        try:
            return key_cls.from_private_key(io.StringIO(key_text), password=passphrase)
        except paramiko.SSHException as e:
            last_error = e
            continue
    raise ValueError(f"秘密鍵の形式を認識できませんでした: {last_error}")


def execute_shutdown(server, timeout: int = 10) -> tuple[str, str]:
    """SSH経由でシャットダウンコマンドを実行する。戻り値は (status, message)。"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect_kwargs = dict(
            hostname=server.host,
            port=server.ssh_port or 22,
            username=server.ssh_user,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )

        if server.auth_type == "key":
            key_text = crypto.decrypt(server.ssh_secret_encrypted)
            if not key_text:
                return "error", "秘密鍵が登録されていません"
            passphrase = crypto.decrypt(server.ssh_key_passphrase_encrypted)
            connect_kwargs["pkey"] = _load_private_key(key_text, passphrase)
        else:
            password = crypto.decrypt(server.ssh_secret_encrypted)
            if not password:
                return "error", "SSHパスワードが登録されていません"
            connect_kwargs["password"] = password

        client.connect(**connect_kwargs)
        _stdin, stdout, stderr = client.exec_command(server.shutdown_command, timeout=timeout)
        out = _safe_read(stdout)
        err = _safe_read(stderr)

        message = f"コマンド送信成功: {server.shutdown_command}"
        if out.strip():
            message += f"\nstdout: {out.strip()}"
        if err.strip():
            message += f"\nstderr: {err.strip()}"
        return "success", message
    except Exception as e:
        return "error", f"SSH接続/実行エラー: {e}"
    finally:
        client.close()
