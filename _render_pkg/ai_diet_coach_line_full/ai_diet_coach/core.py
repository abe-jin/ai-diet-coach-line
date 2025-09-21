
"""
AI Diet Coach core logic (shared by CLI/LINE).

Features:
- BMR (Mifflin–St Jeor), TDEE
- Safe daily kcal delta capping
- Plan builder (mode-aware macros)
- Weight logging + suggestions
- Weight history summary
- Profile editing helpers
- Onboarding progress display
"""
from __future__ import annotations
from typing import Dict, Tuple, Optional, List
import math, statistics
from datetime import datetime

# ---------- Calculations ----------

def calculate_bmr(sex: str, *, weight_kg: float, height_cm: float, age: int) -> float:
    s = (sex or "").lower()
    if s not in {"male", "female"}:
        raise ValueError("sex must be 'male' or 'female'")
    base = 10 * float(weight_kg) + 6.25 * float(height_cm) - 5 * int(age)
    base += 5 if s == "male" else -161
    return round(base, 2)

def activity_factor(activity: str) -> float:
    factors = {
        "sedentary": 1.2,
        "light": 1.375,
        "moderate": 1.55,
        "active": 1.725,
        "very_active": 1.9,
    }
    if activity not in factors:
        raise ValueError("activity must be one of: " + ", ".join(factors))
    return factors[activity]

def calculate_tdee(bmr: float, activity: str) -> float:
    return round(float(bmr) * activity_factor(activity), 2)

def build_plan(
    sex: str, age: int, height_cm: float, weight_kg: float,
    activity: str, mode: str,
    goal_weight: float | None = None, deadline_days: int | None = None
) -> Dict:
    bmr = calculate_bmr(sex, weight_kg=weight_kg, height_cm=height_cm, age=age)
    tdee = calculate_tdee(bmr, activity)

    # daily delta from goal/deadline if provided
    delta_kcal = 0.0
    mode_calc = (mode or "recomp").lower()
    notes = []

    if goal_weight is not None and deadline_days is not None and deadline_days > 0:
        delta_kg = goal_weight - weight_kg
        raw_daily = (delta_kg * 7700.0) / deadline_days  # 1kg ~ 7700kcal
        # cap to safe range
        daily_change = max(min(raw_daily, 500.0), -750.0)
        delta_kcal = daily_change
        if abs(raw_daily - daily_change) > 1e-6:
            notes.append("安全のため日次の増減を±750/500 kcalに制限しました。")

    # fallback to mode presets if not using deadline targeting
    if delta_kcal == 0.0:
        if mode_calc == "cut":
            delta_kcal = -500.0
        elif mode_calc == "bulk":
            delta_kcal = +300.0
        else:
            delta_kcal = 0.0

    target_kcal = max(round(tdee + delta_kcal), 1200)
    maintenance = round(tdee)

    # macros
    protein_g = max(2.0 * weight_kg, 1.6 * weight_kg)
    fat_g = max(0.6 * weight_kg, 40.0)
    remaining = max(target_kcal - (protein_g * 4 + fat_g * 9), 100.0)
    carb_g = remaining / 4.0

    return {
        "bmr": bmr,
        "tdee": tdee,
        "maintenance_kcal": maintenance,
        "target_kcal": target_kcal,
        "delta_kcal": round(delta_kcal),
        "mode": mode_calc,
        "protein_g": round(protein_g),
        "fat_g": round(fat_g),
        "carb_g": round(carb_g),
        "notes": notes,
    }

# ---------- Weight logging & suggestions ----------

def suggest_after_log(history: List[Dict], mode: str, recent_window: int = 7) -> str:
    """Simple dynamic suggestion after a new weight log."""
    if not history:
        return "ログを続けましょう。まずは1〜2週間、同じ条件で測定を。"
    mode = (mode or "recomp").lower()
    # use last N
    last = history[-recent_window:]
    if len(last) >= 2:
        delta = last[-1]["weight"] - last[0]["weight"]
        days = (last[-1]["date"] - last[0]["date"]).days or 1
        daily_change = delta / days
    else:
        daily_change = 0.0

    if mode == "cut":
        if daily_change < -0.15:
            return "減量が速すぎるかも。炭水化物を+20〜40gか、総カロリー+100〜150kcalを検討。"
        elif daily_change > 0.05:
            return "体重が増えています。就寝前の間食を見直し、活動量の確保を。"
        else:
            return "良いペースです。この調子で。タンパク質は2g/kg確保を。"
    elif mode == "bulk":
        if daily_change > 0.25:
            return "増量が速いかも。脂質を-10gまたは総カロリー-100kcalを検討。"
        elif daily_change < 0.05:
            return "増えづらい場合は炭水化物+30〜50gを試して。"
        else:
            return "良い増量ペース。トレ前後の炭水化物を意識。"
    else:
        if abs(daily_change) < 0.05:
            return "体重は概ね維持。フォームと睡眠を整えて質を上げよう。"
        else:
            return "維持期でも上下はあります。1〜2週間の平均で見ていきましょう。"

def summarise_history(history: List[Dict], days: int = 30) -> Dict:
    if not history:
        return {"count": 0}
    # last N days
    cutoff = history[-1]["date"]
    window = [x for x in history if (cutoff - x["date"]).days <= days]
    if not window:
        return {"count": 0}
    weights = [x["weight"] for x in window]
    delta = weights[-1] - weights[0]
    avg = round(sum(weights) / len(weights), 2)
    trend = "↘" if delta < -0.3 else ("↗" if delta > 0.3 else "→")
    return {
        "count": len(window),
        "from": window[0]["date"].date().isoformat(),
        "to": window[-1]["date"].date().isoformat(),
        "start": round(weights[0], 2),
        "end": round(weights[-1], 2),
        "avg": avg,
        "delta": round(delta, 2),
        "trend": trend,
    }

# ---------- Profile helpers ----------

def progress_bar(done: int, total: int, width: int = 10) -> str:
    done = max(0, min(done, total))
    filled = math.floor(width * done / total) if total else 0
    return "【" + "█" * filled + "░" * (width - filled) + f"】 {done}/{total}"

def validate_profile(p: Dict) -> None:
    required = ["sex", "age", "height_cm", "weight_kg", "activity", "mode"]
    missing = [k for k in required if k not in p]
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))
