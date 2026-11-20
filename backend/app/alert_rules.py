from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics_models import AlertRule, AppSetting
from .analytics_schemas import Advisory, AlertCode, AlertSettings
from .athletes import local_date

ALERT_SETTING_KEY = "alert_thresholds"


def load_settings(db: Session) -> AlertSettings:
    thresholds = db.get(AppSetting, ALERT_SETTING_KEY)
    overrides = {row.code: row.enabled for row in db.scalars(select(AlertRule))}
    return AlertSettings(
        **(thresholds.value if thresholds else {}),
        enabled={code for code in AlertCode if overrides.get(code, True)},
    )


def save_settings(db: Session, settings: AlertSettings):
    thresholds = db.get(AppSetting, ALERT_SETTING_KEY)
    if thresholds is None:
        thresholds = AppSetting(key=ALERT_SETTING_KEY)
        db.add(thresholds)
    thresholds.value = settings.model_dump(mode="json", exclude={"enabled"})
    existing = {row.code: row for row in db.scalars(select(AlertRule))}
    for code in AlertCode:
        if code not in existing:
            existing[code] = AlertRule(code=code)
            db.add(existing[code])
        existing[code].enabled = code in settings.enabled


def evaluate(settings: AlertSettings, local_now: datetime, user, diary, latest, missing_rpe, sync) -> list[Advisory]:
    if not user.active or user.activated_at is None:
        return []

    today = local_now.date()
    timezone = local_now.tzinfo.key
    last_date = local_date(timezone, latest.started_at if latest else user.activated_at)
    days_since = (today - last_date).days
    fatigue = max(diary.general_fatigue, diary.leg_fatigue) if diary else None
    rules = (
        (AlertCode.DIARY_MISSING, diary is None and local_now.hour >= settings.diary_due_hour, None),
        (AlertCode.ILLNESS, diary is not None and diary.illness_symptoms, None),
        (AlertCode.FATIGUE, fatigue is not None and fatigue >= settings.fatigue_min, fatigue),
        (AlertCode.SORENESS, diary is not None and diary.muscle_soreness >= settings.soreness_min,
         diary.muscle_soreness if diary else None),
        (AlertCode.LOW_MOTIVATION, diary is not None and diary.motivation <= settings.motivation_max,
         diary.motivation if diary else None),
        (AlertCode.RPE_MISSING, missing_rpe > 0, missing_rpe),
        (AlertCode.SYNC_ERROR, (sync.available or sync.has_session) and sync.status in {
            "temporary_error", "reauthorization", "interaction_required", "unavailable",
        }, None),
        (AlertCode.INACTIVE, days_since >= settings.inactive_days, days_since),
    )
    return [Advisory(code=code, value=value) for code, enabled, value in rules if enabled and code in settings.enabled]
