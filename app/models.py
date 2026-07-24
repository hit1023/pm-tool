import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base


def utcnow():
    return datetime.datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Credential(Base):
    __tablename__ = "credentials"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    auth_type = Column(String, nullable=False, default="key")  # "key" | "password"
    secret_encrypted = Column(Text, nullable=False)  # 秘密鍵本文 or パスワード(暗号化済み)
    passphrase_encrypted = Column(Text, nullable=True)  # 秘密鍵のパスフレーズ(暗号化済み、任意)
    created_at = Column(DateTime, default=utcnow)

    servers = relationship("Server", back_populates="credential")


class Server(Base):
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    host = Column(String, nullable=False)
    ssh_port = Column(Integer, nullable=False, default=22)
    ssh_user = Column(String, nullable=False)
    credential_id = Column(Integer, ForeignKey("credentials.id"), nullable=True)
    mac_address = Column(String, nullable=True)
    broadcast_ip = Column(String, nullable=True)
    shutdown_command = Column(String, nullable=False, default="sudo shutdown -h now")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow)

    credential = relationship("Credential", back_populates="servers")
    schedules = relationship("Schedule", back_populates="server", cascade="all, delete-orphan")


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    action = Column(String, nullable=False)  # "shutdown" | "start"
    cron_expression = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow)

    server = relationship("Server", back_populates="schedules")


class ExecutionLog(Base):
    __tablename__ = "execution_logs"

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=True)
    server_name = Column(String, nullable=True)
    schedule_id = Column(Integer, nullable=True)
    action = Column(String, nullable=False)
    triggered_by = Column(String, nullable=False)  # "schedule" | "manual:<username>"
    status = Column(String, nullable=False)  # "success" | "error"
    message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
