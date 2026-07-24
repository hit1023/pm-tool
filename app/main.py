import logging
import os
import socket
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import auth, crypto, models, schemas, scheduler
from .database import Base, SessionLocal, engine, get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pm-tool")

Base.metadata.create_all(bind=engine)


def _migrate_schema() -> None:
    """旧バージョン(サーバーにSSH認証情報を直接保持していた頃)からの移行処理。
    servers.credential_id列を追加し、既存の認証情報をcredentialsテーブルへ移す。
    新規インストールでは何もしない(冪等)。
    """
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(servers)"))}
        if "credential_id" not in columns:
            conn.execute(text("ALTER TABLE servers ADD COLUMN credential_id INTEGER"))
            conn.commit()
        if "ssh_secret_encrypted" not in columns:
            return  # 旧カラム自体が存在しない(新規インストール)ので移行不要

    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id, name, auth_type, ssh_secret_encrypted, ssh_key_passphrase_encrypted "
            "FROM servers WHERE credential_id IS NULL AND ssh_secret_encrypted IS NOT NULL"
        )).fetchall()
        for row in rows:
            credential = models.Credential(
                name=f"{row.name}の認証情報",
                auth_type=row.auth_type,
                secret_encrypted=row.ssh_secret_encrypted,
                passphrase_encrypted=row.ssh_key_passphrase_encrypted,
            )
            db.add(credential)
            db.flush()
            db.execute(
                text(
                    "UPDATE servers SET credential_id = :cid, "
                    "ssh_secret_encrypted = NULL, ssh_key_passphrase_encrypted = NULL "
                    "WHERE id = :sid"
                ),
                {"cid": credential.id, "sid": row.id},
            )
        db.commit()
        if rows:
            logger.info("%d件のサーバー認証情報を「認証情報」として移行しました", len(rows))
    finally:
        db.close()

    # 旧カラムはNOT NULL制約を持つが新モデルではINSERT時に値を渡さないため、
    # 残したままだと新規サーバー登録が失敗する。移行完了後に列自体を削除する。
    with engine.connect() as conn:
        for col in ("auth_type", "ssh_secret_encrypted", "ssh_key_passphrase_encrypted"):
            try:
                conn.execute(text(f"ALTER TABLE servers DROP COLUMN {col}"))
                conn.commit()
            except Exception as e:
                logger.warning("旧カラム%sの削除に失敗しました: %s", col, e)


def _ensure_initial_admin() -> None:
    db = SessionLocal()
    try:
        if db.query(models.User).count() > 0:
            return
        username = os.getenv("ADMIN_USERNAME", "admin")
        password = os.getenv("ADMIN_PASSWORD")
        if not password:
            logger.warning(
                "ADMIN_PASSWORDが未設定のため初期管理者を作成できません。.envを設定してコンテナを再作成してください。"
            )
            return
        user = models.User(username=username, password_hash=auth.hash_password(password))
        db.add(user)
        db.commit()
        logger.info("初期管理者ユーザー '%s' を作成しました", username)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _migrate_schema()
    _ensure_initial_admin()
    scheduler.start()
    yield
    scheduler.scheduler.shutdown(wait=False)


app = FastAPI(title="pm-tool", lifespan=lifespan)


# ============================================================
# auth
# ============================================================
@app.post("/api/auth/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    if auth.is_locked_out(payload.username):
        raise HTTPException(
            status_code=429,
            detail=f"ログイン試行回数が上限を超えました。{auth.LOCKOUT_MINUTES}分待ってから再試行してください。",
        )
    user = db.query(models.User).filter(models.User.username == payload.username).first()
    if user is None or not auth.verify_password(payload.password, user.password_hash):
        auth.record_failed_attempt(payload.username)
        raise HTTPException(status_code=401, detail="ユーザー名またはパスワードが違います")
    auth.clear_failed_attempts(payload.username)
    token = auth.create_access_token(user.username)
    return schemas.TokenResponse(access_token=token)


@app.get("/api/auth/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user


# ============================================================
# users
# ============================================================
@app.get("/api/users", response_model=list[schemas.UserOut])
def list_users(
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    return db.query(models.User).order_by(models.User.id).all()


@app.post("/api/users", response_model=schemas.UserOut, status_code=201)
def create_user(
    payload: schemas.UserCreate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    if db.query(models.User).filter(models.User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="そのユーザー名は既に使用されています")
    user = models.User(username=payload.username, password_hash=auth.hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.put("/api/users/{user_id}/password", response_model=schemas.UserOut)
def change_password(
    user_id: int,
    payload: schemas.PasswordChange,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    user.password_hash = auth.hash_password(payload.password)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/api/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    if db.query(models.User).count() <= 1:
        raise HTTPException(status_code=400, detail="最後の1人のユーザーは削除できません")
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="自分自身は削除できません")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません")
    db.delete(user)
    db.commit()


# ============================================================
# credentials
# ============================================================
def _credential_out(credential: models.Credential) -> schemas.CredentialOut:
    return schemas.CredentialOut(
        id=credential.id,
        name=credential.name,
        auth_type=credential.auth_type,
        created_at=credential.created_at,
    )


@app.get("/api/credentials", response_model=list[schemas.CredentialOut])
def list_credentials(
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    credentials = db.query(models.Credential).order_by(models.Credential.id).all()
    return [_credential_out(c) for c in credentials]


@app.post("/api/credentials", response_model=schemas.CredentialOut, status_code=201)
def create_credential(
    payload: schemas.CredentialCreate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    credential = models.Credential(
        name=payload.name,
        auth_type=payload.auth_type,
        secret_encrypted=crypto.encrypt(payload.secret),
        passphrase_encrypted=crypto.encrypt(payload.passphrase),
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)
    return _credential_out(credential)


@app.put("/api/credentials/{credential_id}", response_model=schemas.CredentialOut)
def update_credential(
    credential_id: int,
    payload: schemas.CredentialUpdate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    credential = db.query(models.Credential).filter(models.Credential.id == credential_id).first()
    if credential is None:
        raise HTTPException(status_code=404, detail="認証情報が見つかりません")

    data = payload.model_dump(exclude_unset=True)
    if "secret" in data:
        secret = data.pop("secret")
        if secret:
            credential.secret_encrypted = crypto.encrypt(secret)
    if "passphrase" in data:
        passphrase = data.pop("passphrase")
        if passphrase:
            credential.passphrase_encrypted = crypto.encrypt(passphrase)
    for key, value in data.items():
        setattr(credential, key, value)

    db.commit()
    db.refresh(credential)
    return _credential_out(credential)


@app.delete("/api/credentials/{credential_id}", status_code=204)
def delete_credential(
    credential_id: int,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    credential = db.query(models.Credential).filter(models.Credential.id == credential_id).first()
    if credential is None:
        raise HTTPException(status_code=404, detail="認証情報が見つかりません")
    using_servers = db.query(models.Server).filter(models.Server.credential_id == credential_id).all()
    if using_servers:
        names = "、".join(s.name for s in using_servers)
        raise HTTPException(
            status_code=400,
            detail=f"この認証情報は次のサーバーで使用されているため削除できません: {names}",
        )
    db.delete(credential)
    db.commit()


# ============================================================
# servers
# ============================================================
def _server_out(server: models.Server) -> schemas.ServerOut:
    return schemas.ServerOut(
        id=server.id,
        name=server.name,
        host=server.host,
        ssh_port=server.ssh_port,
        ssh_user=server.ssh_user,
        credential_id=server.credential_id,
        credential_name=server.credential.name if server.credential else None,
        mac_address=server.mac_address,
        broadcast_ip=server.broadcast_ip,
        shutdown_command=server.shutdown_command,
        enabled=server.enabled,
        created_at=server.created_at,
    )


@app.get("/api/servers", response_model=list[schemas.ServerOut])
def list_servers(
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    servers = db.query(models.Server).order_by(models.Server.id).all()
    return [_server_out(s) for s in servers]


def _check_credential_exists(db: Session, credential_id: Optional[int]) -> None:
    if credential_id is None:
        return
    if db.query(models.Credential).filter(models.Credential.id == credential_id).first() is None:
        raise HTTPException(status_code=404, detail="指定した認証情報が見つかりません")


@app.post("/api/servers", response_model=schemas.ServerOut, status_code=201)
def create_server(
    payload: schemas.ServerCreate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    _check_credential_exists(db, payload.credential_id)
    server = models.Server(
        name=payload.name,
        host=payload.host,
        ssh_port=payload.ssh_port,
        ssh_user=payload.ssh_user,
        credential_id=payload.credential_id,
        mac_address=payload.mac_address,
        broadcast_ip=payload.broadcast_ip,
        shutdown_command=payload.shutdown_command,
        enabled=payload.enabled,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return _server_out(server)


@app.put("/api/servers/{server_id}", response_model=schemas.ServerOut)
def update_server(
    server_id: int,
    payload: schemas.ServerUpdate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="サーバーが見つかりません")

    data = payload.model_dump(exclude_unset=True)
    if "credential_id" in data:
        _check_credential_exists(db, data["credential_id"])
    for key, value in data.items():
        setattr(server, key, value)

    db.commit()
    db.refresh(server)
    return _server_out(server)


@app.delete("/api/servers/{server_id}", status_code=204)
def delete_server(
    server_id: int,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="サーバーが見つかりません")
    for schedule in list(server.schedules):
        scheduler.remove_job(schedule.id)
    db.query(models.ExecutionLog).filter(models.ExecutionLog.server_id == server_id).update(
        {"server_id": None}
    )
    db.delete(server)
    db.commit()


@app.post("/api/servers/{server_id}/action", response_model=schemas.ExecutionLogOut)
def manual_action(
    server_id: int,
    payload: schemas.ServerActionRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="サーバーが見つかりません")
    if not server.enabled:
        raise HTTPException(status_code=400, detail="このサーバーは無効化されています")
    return scheduler.run_action(db, server, payload.action, triggered_by=f"manual:{current_user.username}")


@app.get("/api/servers/{server_id}/status")
def check_status(
    server_id: int,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="サーバーが見つかりません")
    return {"online": _tcp_ping(server.host, server.ssh_port or 22)}


def _tcp_ping(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ============================================================
# schedules
# ============================================================
def _schedule_out(schedule: models.Schedule) -> schemas.ScheduleOut:
    return schemas.ScheduleOut(
        id=schedule.id,
        server_id=schedule.server_id,
        server_name=schedule.server.name if schedule.server else None,
        action=schedule.action,
        cron_expression=schedule.cron_expression,
        enabled=schedule.enabled,
        created_at=schedule.created_at,
    )


@app.get("/api/schedules", response_model=list[schemas.ScheduleOut])
def list_schedules(
    server_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    query = db.query(models.Schedule)
    if server_id is not None:
        query = query.filter(models.Schedule.server_id == server_id)
    return [_schedule_out(s) for s in query.order_by(models.Schedule.id).all()]


@app.post("/api/schedules", response_model=schemas.ScheduleOut, status_code=201)
def create_schedule(
    payload: schemas.ScheduleCreate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    server = db.query(models.Server).filter(models.Server.id == payload.server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="サーバーが見つかりません")
    schedule = models.Schedule(
        server_id=payload.server_id,
        action=payload.action,
        cron_expression=payload.cron_expression,
        enabled=payload.enabled,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    scheduler.sync_job(schedule)
    return _schedule_out(schedule)


@app.put("/api/schedules/{schedule_id}", response_model=schemas.ScheduleOut)
def update_schedule(
    schedule_id: int,
    payload: schemas.ScheduleUpdate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
    if schedule is None:
        raise HTTPException(status_code=404, detail="スケジュールが見つかりません")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(schedule, key, value)
    db.commit()
    db.refresh(schedule)
    scheduler.sync_job(schedule)
    return _schedule_out(schedule)


@app.delete("/api/schedules/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
    if schedule is None:
        raise HTTPException(status_code=404, detail="スケジュールが見つかりません")
    scheduler.remove_job(schedule_id)
    db.delete(schedule)
    db.commit()


# ============================================================
# execution logs
# ============================================================
@app.get("/api/logs", response_model=list[schemas.ExecutionLogOut])
def list_logs(
    server_id: Optional[int] = None,
    action: Optional[str] = None,
    status: Optional[str] = Query(None),
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(auth.get_current_user),
):
    query = db.query(models.ExecutionLog)
    if server_id is not None:
        query = query.filter(models.ExecutionLog.server_id == server_id)
    if action is not None:
        query = query.filter(models.ExecutionLog.action == action)
    if status is not None:
        query = query.filter(models.ExecutionLog.status == status)
    query = query.order_by(models.ExecutionLog.started_at.desc())
    return query.offset(offset).limit(min(max(limit, 1), 500)).all()


# ============================================================
# static webui (SPA) — 必ずAPIルートの後に登録すること
# ============================================================
app.mount("/", StaticFiles(directory="/webui", html=True), name="webui")
