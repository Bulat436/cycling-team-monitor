from datetime import date, datetime
from time import time
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .athlete_models import AthleteProfile, FtpRecord, HeartRateZoneProfile, PowerZoneProfile, WeightRecord
from .athlete_schemas import (
    FtpMeasurement, FtpValue, ProfileDetail, ProfileEdit, ProfileSummary,
    WeightMeasurement, WeightValue, ZoneKind, Zones,
)
from .models import User
from .security import DB, Auth, Coach, audit

router = APIRouter(prefix="/athletes", tags=["Спортсмены"])
ZONE_MODELS = {"power": PowerZoneProfile, "heart-rate": HeartRateZoneProfile}


def local_date(timezone: str, timestamp: float | None = None) -> date:
    return datetime.fromtimestamp(time() if timestamp is None else timestamp, ZoneInfo(timezone)).date()


def list_profiles(db: Session):
    return db.execute(
        select(AthleteProfile, User).join(User, User.id == AthleteProfile.athlete_id)
        .where(User.role == "athlete").order_by(User.name, User.id)
    ).all()


def profile_summary(profile: AthleteProfile, user: User) -> ProfileSummary:
    return ProfileSummary(
        athlete_id=profile.athlete_id, name=user.name, active=user.active,
        team_group=profile.team_group, timezone=profile.timezone,
    )


def athlete_scope(athlete_id: int, auth: Auth, db: DB) -> tuple[AthleteProfile, User]:
    if auth.user.role != "coach" and auth.user.id != athlete_id:
        raise HTTPException(404, "athlete_missing")
    profile = db.get(AthleteProfile, athlete_id)
    user = db.get(User, athlete_id)
    if not profile or not user or user.role != "athlete":
        raise HTTPException(404, "athlete_missing")
    return profile, user


AthleteScope = Annotated[tuple[AthleteProfile, User], Depends(athlete_scope)]


def ftp_records(db: Session, athlete_id: int):
    return db.scalars(select(FtpRecord).where(FtpRecord.athlete_id == athlete_id)
                      .order_by(FtpRecord.effective_from.desc())).all()


def weight_records(db: Session, athlete_id: int):
    return db.scalars(select(WeightRecord).where(WeightRecord.athlete_id == athlete_id)
                      .order_by(WeightRecord.measured_on.desc(), WeightRecord.id.desc())).all()


@router.get("", response_model=list[ProfileSummary])
def athletes(db: DB, coach: Coach):
    return [profile_summary(profile, user) for profile, user in list_profiles(db)]


@router.get("/{athlete_id}", response_model=ProfileDetail)
def profile_detail(scope: AthleteScope, auth: Auth, db: DB):
    profile, user = scope
    today = local_date(profile.timezone)
    ftp = ftp_records(db, profile.athlete_id)
    weights = weight_records(db, profile.athlete_id)
    power = db.get(PowerZoneProfile, profile.athlete_id)
    heart_rate = db.get(HeartRateZoneProfile, profile.athlete_id)
    current_ftp = next((record for record in ftp if record.effective_from <= today), None)
    current_weight = next((record for record in weights if record.measured_on <= today), None)
    indicator_sources = [record for record in (current_ftp, current_weight, power, heart_rate) if record is not None]
    if profile.height_cm is not None:
        indicator_sources.append(profile)
    return ProfileDetail(
        **profile_summary(profile, user).model_dump(), email=user.email,
        birth_date=profile.birth_date, height_cm=profile.height_cm, specialization=profile.specialization,
        coach_notes=profile.coach_notes if auth.user.role == "coach" else None,
        local_date=today,
        indicators_updated_at=max((record.updated_at for record in indicator_sources), default=None),
        current_ftp=current_ftp, current_weight=current_weight,
        ftp_history=ftp, weight_history=weights,
        power_zones=power.zones if power else [], heart_rate_zones=heart_rate.zones if heart_rate else [],
    )


@router.put("/{athlete_id}", status_code=204)
def edit_profile(payload: ProfileEdit, scope: AthleteScope, db: DB, coach: Coach):
    profile, user = scope
    if payload.birth_date and payload.birth_date > local_date(payload.timezone):
        raise HTTPException(422, "future_birth_date")
    user.name = payload.name
    for field, value in payload.model_dump(exclude={"name"}).items():
        setattr(profile, field, value)
    audit(db, "athlete_profile_updated", coach.id, profile.athlete_id)
    db.commit()


@router.get("/{athlete_id}/ftp", response_model=list[FtpMeasurement])
def ftp_history(scope: AthleteScope, db: DB):
    return ftp_records(db, scope[0].athlete_id)


@router.put("/{athlete_id}/ftp/{effective_from}", response_model=FtpMeasurement)
def save_ftp(payload: FtpValue, effective_from: date, scope: AthleteScope, db: DB, coach: Coach):
    athlete_id = scope[0].athlete_id
    record = db.get(FtpRecord, (athlete_id, effective_from))
    if record is None:
        record = FtpRecord(athlete_id=athlete_id, effective_from=effective_from, watts=payload.watts)
        db.add(record)
    else:
        record.watts = payload.watts
    audit(db, "athlete_ftp_updated", coach.id, athlete_id)
    db.commit()
    return record


@router.get("/{athlete_id}/weight", response_model=list[WeightMeasurement])
def weight_history(scope: AthleteScope, db: DB):
    return weight_records(db, scope[0].athlete_id)


def save_weight_record(
    payload: WeightValue, profile: AthleteProfile, record: WeightRecord, db: Session, coach: User,
) -> WeightRecord:
    if payload.measured_on > local_date(profile.timezone):
        raise HTTPException(422, "future_weight_date")
    record.measured_on = payload.measured_on
    record.kg = payload.kg
    db.add(record)
    audit(db, "athlete_weight_updated", coach.id, profile.athlete_id)
    db.commit()
    return record


@router.post("/{athlete_id}/weight", status_code=201, response_model=WeightMeasurement)
def add_weight(payload: WeightValue, scope: AthleteScope, db: DB, coach: Coach):
    profile, _ = scope
    return save_weight_record(payload, profile, WeightRecord(athlete_id=profile.athlete_id), db, coach)


@router.put("/{athlete_id}/weight/{measurement_id}", response_model=WeightMeasurement)
def edit_weight(payload: WeightValue, measurement_id: int, scope: AthleteScope, db: DB, coach: Coach):
    profile, _ = scope
    record = db.scalar(select(WeightRecord).where(
        WeightRecord.id == measurement_id, WeightRecord.athlete_id == profile.athlete_id,
    ))
    if not record:
        raise HTTPException(404, "measurement_missing")
    return save_weight_record(payload, profile, record, db, coach)


@router.get("/{athlete_id}/zones/{kind}", response_model=Zones)
def get_zones(kind: ZoneKind, scope: AthleteScope, db: DB):
    record = db.get(ZONE_MODELS[kind], scope[0].athlete_id)
    return {"zones": record.zones if record else []}


@router.put("/{athlete_id}/zones/{kind}", response_model=Zones)
def save_zones(payload: Zones, kind: ZoneKind, scope: AthleteScope, db: DB, coach: Coach):
    athlete_id = scope[0].athlete_id
    if kind == "heart-rate" and any(
        zone.minimum > 300 or (zone.maximum is not None and zone.maximum > 300) for zone in payload.zones
    ):
        raise HTTPException(422, "invalid_heart_rate_bounds")
    model = ZONE_MODELS[kind]
    record = db.get(model, athlete_id)
    values = payload.model_dump()["zones"]
    if record is None:
        db.add(model(athlete_id=athlete_id, zones=values))
    else:
        record.zones = values
    audit(db, "athlete_zones_updated", coach.id, athlete_id)
    db.commit()
    return payload
