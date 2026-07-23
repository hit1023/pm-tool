from datetime import datetime
from typing import Literal, Optional

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------- auth ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------- users ----------
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=200)


class PasswordChange(BaseModel):
    password: str = Field(min_length=8, max_length=200)


# ---------- servers ----------
class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    host: str
    ssh_port: int = 22
    ssh_user: str
    auth_type: Literal["key", "password"] = "key"
    ssh_secret: Optional[str] = None
    ssh_key_passphrase: Optional[str] = None
    mac_address: Optional[str] = None
    broadcast_ip: Optional[str] = None
    shutdown_command: str = "sudo shutdown -h now"
    enabled: bool = True


class ServerUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    auth_type: Optional[Literal["key", "password"]] = None
    ssh_secret: Optional[str] = None
    ssh_key_passphrase: Optional[str] = None
    mac_address: Optional[str] = None
    broadcast_ip: Optional[str] = None
    shutdown_command: Optional[str] = None
    enabled: Optional[bool] = None


class ServerOut(BaseModel):
    id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    auth_type: str
    has_secret: bool
    mac_address: Optional[str] = None
    broadcast_ip: Optional[str] = None
    shutdown_command: str
    enabled: bool
    created_at: datetime


class ServerActionRequest(BaseModel):
    action: Literal["shutdown", "start"]


# ---------- schedules ----------
class ScheduleCreate(BaseModel):
    server_id: int
    action: Literal["shutdown", "start"]
    cron_expression: str
    enabled: bool = True

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v):
        if not croniter.is_valid(v):
            raise ValueError("cron形式が不正です")
        return v


class ScheduleUpdate(BaseModel):
    action: Optional[Literal["shutdown", "start"]] = None
    cron_expression: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v):
        if v is not None and not croniter.is_valid(v):
            raise ValueError("cron形式が不正です")
        return v


class ScheduleOut(BaseModel):
    id: int
    server_id: int
    server_name: Optional[str] = None
    action: str
    cron_expression: str
    enabled: bool
    created_at: datetime


# ---------- execution logs ----------
class ExecutionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: Optional[int] = None
    server_name: Optional[str] = None
    schedule_id: Optional[int] = None
    action: str
    triggered_by: str
    status: str
    message: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
