import logging
import os
from datetime import datetime, timedelta

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import models, ssh_actions, wol
from .database import DB_PATH, SessionLocal

logger = logging.getLogger("pm-tool.scheduler")

TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "Asia/Tokyo")

jobstores = {"default": SQLAlchemyJobStore(url=f"sqlite:///{DB_PATH}")}
scheduler = BackgroundScheduler(jobstores=jobstores, timezone=TIMEZONE)


def job_id_for(schedule_id: int) -> str:
    return f"schedule-{schedule_id}"


def run_schedule(schedule_id: int) -> None:
    db = SessionLocal()
    try:
        schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
        if schedule is None or not schedule.enabled:
            return
        server = db.query(models.Server).filter(models.Server.id == schedule.server_id).first()
        if server is None or not server.enabled:
            return
        run_action(db, server, schedule.action, schedule_id=schedule.id, triggered_by="schedule")
    except Exception:
        logger.exception("スケジュール実行中にエラーが発生しました (schedule_id=%s)", schedule_id)
    finally:
        db.close()


def run_action(db, server: models.Server, action: str, schedule_id: int | None = None, triggered_by: str = "manual") -> models.ExecutionLog:
    log = models.ExecutionLog(
        server_id=server.id,
        server_name=server.name,
        schedule_id=schedule_id,
        action=action,
        triggered_by=triggered_by,
        status="error",
        message="",
        started_at=datetime.utcnow(),
    )
    try:
        if action == "shutdown":
            status, message = ssh_actions.execute_shutdown(server)
        elif action == "start":
            if not server.mac_address:
                status, message = "error", "MACアドレスが未登録のため起動できません"
            else:
                wol.send_magic_packet(server.mac_address, server.broadcast_ip or "255.255.255.255")
                status, message = "success", f"WOLマジックパケットを送信しました (MAC: {server.mac_address})"
        else:
            status, message = "error", f"不明なアクションです: {action}"
    except Exception as e:
        status, message = "error", str(e)

    log.status = status
    log.message = message
    log.finished_at = datetime.utcnow()
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def sync_job(schedule: models.Schedule) -> None:
    """Schedule行の内容(有効/無効・cron)をAPSchedulerのジョブへ反映する。"""
    job_id = job_id_for(schedule.id)
    if not schedule.enabled:
        remove_job(schedule.id)
        return
    trigger = CronTrigger.from_crontab(schedule.cron_expression, timezone=TIMEZONE)
    scheduler.add_job(
        run_schedule,
        trigger=trigger,
        id=job_id,
        args=[schedule.id],
        replace_existing=True,
        misfire_grace_time=300,
    )


def remove_job(schedule_id: int) -> None:
    job_id = job_id_for(schedule_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def load_all_jobs() -> None:
    """Schedule テーブルを正として、APSchedulerのジョブを再同期する。"""
    db = SessionLocal()
    try:
        for schedule in db.query(models.Schedule).all():
            sync_job(schedule)
    finally:
        db.close()


def prune_old_logs() -> None:
    days = int(os.getenv("LOG_RETENTION_DAYS", "90"))
    cutoff = datetime.utcnow() - timedelta(days=days)
    db = SessionLocal()
    try:
        deleted = db.query(models.ExecutionLog).filter(models.ExecutionLog.started_at < cutoff).delete()
        db.commit()
        if deleted:
            logger.info("実行ログを%d件削除しました(保持期間%d日超過)", deleted, days)
    finally:
        db.close()


def start() -> None:
    scheduler.start()
    load_all_jobs()
    scheduler.add_job(
        prune_old_logs,
        trigger=CronTrigger(hour=3, minute=30, timezone=TIMEZONE),
        id="log-retention-cleanup",
        replace_existing=True,
    )
