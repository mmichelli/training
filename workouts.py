"""Build Garmin structured workouts from the plan and push to the Fenix.

A Garmin workout is a sequence of steps. Each step has a type (warmup, interval,
recovery, cooldown), a duration (time or distance), and an optional target
(HR zone, pace, or open). We build a workout per "quality" session in the plan
(sub-threshold, hills) — easy runs are kept open since they don't benefit from
on-watch prompts.

Once authenticated, `push_all()` uploads each workout and schedules it onto the
Garmin training calendar for the right date so it appears on the Fenix watch
under Training & Planning → Calendar.

Run:
    uv run python workouts.py            # build + push all
    uv run python workouts.py --dry-run  # build only, print summary
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import garth

from plan_lookup import PLAN_START
from sync import login

# HR zone IDs in Garmin: 1=Z1 ... 5=Z5. We target zone 3 ceiling for sub-threshold.
ZONE_SUBTHRESHOLD = 3
ZONE_EASY = 2

STEP_TYPE = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup"},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval"},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery"},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
}

END_CONDITION = {
    "time": {"conditionTypeId": 2, "conditionTypeKey": "time"},
    "distance": {"conditionTypeId": 3, "conditionTypeKey": "distance"},
    "lap_button": {"conditionTypeId": 1, "conditionTypeKey": "lap.button"},
}

TARGET_NONE = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
TARGET_HR_ZONE = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}


def step(
    order: int,
    step_type: str,
    end_condition: str,
    end_value: float,
    target_zone: int | None = None,
    description: str = "",
) -> dict[str, Any]:
    s: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": STEP_TYPE[step_type],
        "endCondition": END_CONDITION[end_condition],
        "endConditionValue": end_value,
        "targetType": TARGET_HR_ZONE if target_zone else TARGET_NONE,
        "description": description,
    }
    if target_zone:
        s["zoneNumber"] = target_zone
    return s


@dataclass
class Workout:
    name: str
    description: str
    steps: list[dict] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutName": self.name,
            "description": self.description,
            "workoutSegments": [
                {
                    "segmentOrder": 1,
                    "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
                    "workoutSteps": self.steps,
                }
            ],
        }


def subthreshold_short(week: int) -> Workout:
    """Tuesday short reps. Weeks 21+."""
    reps = 10 if week < 25 else 12
    rep_m = 400 if week < 25 else 600
    name = f"W{week} Tue · {reps}×{rep_m}m sub-threshold"
    steps: list[dict] = [
        step(1, "warmup", "distance", 2000, ZONE_EASY, "2 km easy warmup"),
    ]
    for i in range(reps):
        steps.append(step(2 + i * 2, "interval", "distance", rep_m, ZONE_SUBTHRESHOLD,
                          f"Rep {i+1} sub-threshold (top Z3, never Z4)"))
        steps.append(step(3 + i * 2, "recovery", "time", 60, ZONE_EASY, "60s walk/jog"))
    steps.append(step(len(steps) + 1, "cooldown", "distance", 1000, ZONE_EASY, "1 km cooldown"))
    return Workout(name=name, description="NSA short reps. Never above top of Z3.", steps=steps)


def subthreshold_medium(week: int) -> Workout:
    """Thursday medium reps."""
    if week <= 14:
        reps, rep_m = 5, 1000
    elif week <= 16:
        reps, rep_m = 7, 1000
    elif week <= 18:
        reps, rep_m = 4, 1500
    elif week <= 20:
        reps, rep_m = 5, 1500
    elif week <= 28:
        reps, rep_m = 6, 1500
    else:
        reps, rep_m = 4, 2000
    name = f"W{week} Thu · {reps}×{rep_m/1000:g}km sub-threshold"
    steps: list[dict] = [step(1, "warmup", "distance", 2000, ZONE_EASY, "2 km easy warmup")]
    for i in range(reps):
        steps.append(step(2 + i * 2, "interval", "distance", rep_m, ZONE_SUBTHRESHOLD,
                          f"Rep {i+1} sub-threshold"))
        steps.append(step(3 + i * 2, "recovery", "time", 90, ZONE_EASY, "90s walk"))
    steps.append(step(len(steps) + 1, "cooldown", "distance", 1000, ZONE_EASY, "1 km cooldown"))
    return Workout(name=name, description="NSA medium reps. Top Z3 ceiling.", steps=steps)


def subthreshold_long(week: int) -> Workout:
    """Marathon-build Tuesday long reps (weeks 33-38)."""
    if week <= 34:
        reps, rep_m = 5, 2000
    elif week <= 36:
        reps, rep_m = 4, 2500
    else:
        reps, rep_m = 3, 3000
    name = f"W{week} Tue · {reps}×{rep_m/1000:g}km sub-threshold"
    steps: list[dict] = [step(1, "warmup", "distance", 2000, ZONE_EASY, "2 km warmup")]
    for i in range(reps):
        steps.append(step(2 + i * 2, "interval", "distance", rep_m, ZONE_SUBTHRESHOLD,
                          f"Rep {i+1} sub-threshold"))
        steps.append(step(3 + i * 2, "recovery", "time", 120, ZONE_EASY, "2 min walk"))
    steps.append(step(len(steps) + 1, "cooldown", "distance", 1000, ZONE_EASY, "1 km cooldown"))
    return Workout(name=name, description="NSA long reps. Threshold development.", steps=steps)


def tempo_continuous(week: int) -> Workout:
    """Marathon-specific Thursday continuous tempo blocks (weeks 33-38)."""
    if week <= 34:
        block_min = 10
        reps = 3
    elif week <= 36:
        block_min = 20
        reps = 2
    else:
        block_min = 40
        reps = 1
    name = f"W{week} Thu · {reps}×{block_min}min tempo"
    steps: list[dict] = [step(1, "warmup", "distance", 2000, ZONE_EASY, "2 km warmup")]
    for i in range(reps):
        steps.append(step(2 + i * 2, "interval", "time", block_min * 60, ZONE_SUBTHRESHOLD,
                          f"Tempo block {i+1}, top Z3"))
        if i < reps - 1:
            steps.append(step(3 + i * 2, "recovery", "time", 180, ZONE_EASY, "3 min jog"))
    steps.append(step(len(steps) + 1, "cooldown", "distance", 1000, ZONE_EASY, "1 km cooldown"))
    return Workout(name=name, description="Marathon-pace continuous tempo.", steps=steps)


def hills(week: int, reps: int = 10) -> Workout:
    name = f"W{week} Sat · {reps}×90s hills"
    steps: list[dict] = [step(1, "warmup", "distance", 2000, ZONE_EASY, "2 km warmup")]
    for i in range(reps):
        steps.append(step(2 + i * 2, "interval", "time", 90, ZONE_SUBTHRESHOLD,
                          f"Hill {i+1} — strong but controlled, top Z3"))
        steps.append(step(3 + i * 2, "recovery", "lap_button", 0, ZONE_EASY, "Jog down recovery"))
    steps.append(step(len(steps) + 1, "cooldown", "distance", 2000, ZONE_EASY, "2 km cooldown"))
    return Workout(name=name, description="Hill insurance — quad-specific strength.", steps=steps)


def constantia_sim(week: int, reps: int) -> Workout:
    name = f"W{week} Thu · {reps}×1km uphill (Constantia sim)"
    steps: list[dict] = [step(1, "warmup", "distance", 2000, ZONE_EASY, "2 km warmup")]
    for i in range(reps):
        steps.append(step(2 + i * 2, "interval", "distance", 1000, ZONE_SUBTHRESHOLD,
                          f"Uphill km {i+1} (treadmill 5-8% or local hill)"))
        steps.append(step(3 + i * 2, "recovery", "lap_button", 0, ZONE_EASY, "Jog down"))
    steps.append(step(len(steps) + 1, "cooldown", "distance", 2000, ZONE_EASY, "2 km cooldown"))
    return Workout(name=name, description="Constantia Nek simulation.", steps=steps)


def schedule_for_week(week: int) -> dict[str, Workout]:
    """Return weekday -> Workout for QUALITY days in the given plan week.
    Easy days are intentionally absent — leave the watch open."""
    out: dict[str, Workout] = {}
    if 9 <= week <= 20:
        out["Thu"] = subthreshold_medium(week)
    elif 21 <= week <= 32:
        out["Tue"] = subthreshold_short(week)
        out["Thu"] = subthreshold_medium(week)
        if week in {22, 25, 28, 31}:
            out["Sat"] = hills(week)
    elif 33 <= week <= 39:
        out["Tue"] = subthreshold_long(week)
        out["Thu"] = tempo_continuous(week)
    elif 42 <= week <= 44:
        out["Tue"] = subthreshold_medium(week)
        out["Thu"] = constantia_sim(week, reps=week - 39)  # 3, 4, 5
    elif week == 45:
        out["Tue"] = subthreshold_medium(week)
        out["Thu"] = constantia_sim(week, reps=4)
    return out


def push_all(start_week: int = 1, end_week: int = 48, dry_run: bool = False) -> int:
    """Upload all quality workouts and schedule them on Garmin's calendar."""
    if not dry_run:
        login()
    pushed = 0
    for week in range(start_week, end_week + 1):
        sched = schedule_for_week(week)
        for wd, wo in sched.items():
            day_idx = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].index(wd)
            d = PLAN_START + timedelta(weeks=week - 1, days=day_idx)
            print(f"W{week:02d} {wd} {d}: {wo.name} ({len(wo.steps)} steps)")
            if dry_run:
                continue
            # Create workout
            resp = garth.connectapi(
                "/workout-service/workout", method="POST", json=wo.to_payload()
            )
            workout_id = resp.get("workoutId")
            if not workout_id:
                print(f"  !! failed to create workout: {resp}")
                continue
            # Schedule onto calendar for date d
            garth.connectapi(
                f"/workout-service/schedule/{workout_id}",
                method="POST",
                json={"date": d.isoformat()},
            )
            pushed += 1
    print(f"\n{'(dry run) ' if dry_run else ''}pushed {pushed} workouts")
    return pushed


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    push_all(dry_run=dry)
