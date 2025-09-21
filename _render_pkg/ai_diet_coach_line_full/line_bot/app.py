
from __future__ import annotations
import os, json
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from ai_diet_coach.core import (
    build_plan, progress_bar, validate_profile,
    summarise_history, suggest_after_log,
)

from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage

CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")

app = FastAPI()
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN) if CHANNEL_ACCESS_TOKEN else None
parser = WebhookParser(CHANNEL_SECRET) if CHANNEL_SECRET else None

DATA_DIR = Path("./data"); DATA_DIR.mkdir(parents=True, exist_ok=True)

ONBOARD_KEYS = [
    ("sex", "性別を入力（male/female）"),
    ("age", "年齢を入力（整数）"),
    ("height_cm", "身長(cm)を入力。例: 170"),
    ("weight_kg", "体重(kg)を入力。例: 65"),
    ("activity", "活動量（sedentary/light/moderate/active/very_active）"),
    ("mode", "モード（cut/recomp/bulk）"),
    ("goal_weight", "目標体重(kg)を入力（任意。スキップは 'skip'）"),
    ("deadline_days", "期限(日)を入力（任意。スキップは 'skip'）"),
]

HELP = (
    "使い方:\n"
    "・start … オンボーディング開始（進捗バー表示）\n"
    "・plan … 現在のプロフィールでプラン再計算\n"
    "・log 65.2 … 体重を記録\n"
    "・history … 履歴サマリ（7日/30日）\n"
    "・profile show / profile set activity active … プロフィール表示/変更\n"
    "・guide … モード別の食事ガイド\n"
    "・reset … 状態を初期化\n"
    "・help … このヘルプ"
)

def _p(uid: str) -> Path: return DATA_DIR / f"{uid}.json"

def load_state(uid: str) -> dict:
    p = _p(uid)
    if not p.exists(): return {"profile": {}, "history": [], "stage": "idle", "onboard_idx": 0}
    try:
        raw = json.loads(p.read_text("utf-8"))
        # parse dates
        for x in raw.get("history", []):
            if isinstance(x.get("date"), str):
                x["date"] = datetime.fromisoformat(x["date"])
        return raw
    except Exception:
        return {"profile": {}, "history": [], "stage": "idle", "onboard_idx": 0}

def save_state(uid: str, state: dict) -> None:
    # stringify dates
    dump = {"profile": state.get("profile", {}), "history": [], "stage": state.get("stage", "idle"), "onboard_idx": state.get("onboard_idx", 0)}
    for x in state.get("history", []):
        dump["history"].append({"date": (x["date"].isoformat() if isinstance(x["date"], datetime) else str(x["date"])), "weight": x["weight"]})
    _p(uid).write_text(json.dumps(dump, ensure_ascii=False, indent=2), "utf-8")

def reply(token: str, text: str) -> None:
    if not line_bot_api:
        raise HTTPException(status_code=500, detail="LINE credentials not set")
    line_bot_api.reply_message(token, TextSendMessage(text=text))

def format_plan(profile: dict) -> str:
    goal_w = profile.get("goal_weight")
    deadline = profile.get("deadline_days")
    plan = build_plan(
        profile["sex"], int(profile["age"]), float(profile["height_cm"]), float(profile["weight_kg"]),
        profile["activity"], profile["mode"],
        float(goal_w) if goal_w not in (None, "", "skip") else None,
        int(deadline) if deadline not in (None, "", "skip") else None,
    )
    lines = [
        "📊 プラン",
        f"BMR: {plan['bmr']} kcal / TDEE: {plan['tdee']} kcal",
        f"目標: {plan['target_kcal']} kcal（維持 {plan['maintenance_kcal']} kcal, Δ {plan['delta_kcal']}）",
        f"マクロ: P {plan['protein_g']}g / F {plan['fat_g']}g / C {plan['carb_g']}g",
    ]
    if plan["notes"]:
        lines.append("Note: " + " ".join(plan["notes"]))
    lines.append("※推定値です。医療上の助言ではありません。")
    return "\n".join(lines)

def guide_text(mode: str) -> str:
    m = (mode or "recomp").lower()
    if m == "cut":
        return "🍽 ガイド(cut): 体重×2gのタンパク質、脂質は体重×0.6g目安、残り炭水化物。就寝前の間食は控えめに。NEATを確保。"
    if m == "bulk":
        return "🍽 ガイド(bulk): 体重×2gのタンパク質、炭水化物はトレ前後を厚めに。脂質は控えめ〜中庸。週+0.25〜0.5kg以内を目安。"
    return "🍽 ガイド(recomp): Pを十分に確保しつつ、日々の活動量と睡眠を最適化。週あたり±0.25kgに収まるよう微調整。"

@app.post("/callback")
async def callback(request: Request):
    if not parser or not line_bot_api:
        raise HTTPException(status_code=500, detail="Missing LINE credentials")
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        events = parser.parse(body.decode("utf-8"), signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature/body")

    for ev in events:
        if isinstance(ev, MessageEvent) and isinstance(ev.message, TextMessage):
            uid = ev.source.user_id
            text = (ev.message.text or "").strip()
            state = load_state(uid)
            profile = state.get("profile", {})

            # Commands
            low = text.lower()
            if low in {"help", "ヘルプ"}:
                reply(ev.reply_token, HELP); continue

            if low in {"reset", "リセット"}:
                state = {"profile": {}, "history": [], "stage": "idle", "onboard_idx": 0}
                save_state(uid, state)
                reply(ev.reply_token, "状態を初期化しました。'start' でオンボーディングを開始できます。"); continue

            if low in {"start", "開始"}:
                state["stage"] = "onboarding"
                state["onboard_idx"] = 0
                # ask first
                key, q = ONBOARD_KEYS[state["onboard_idx"]]
                pb = progress_bar(0, len(ONBOARD_KEYS))
                save_state(uid, state)
                reply(ev.reply_token, f"オンボーディングを開始します。\n{pb}\n{q}")
                continue

            if low == "plan":
                try:
                    validate_profile(profile)
                    reply(ev.reply_token, format_plan(profile))
                except Exception as e:
                    reply(ev.reply_token, f"プロフィールが未完了です: {e}\n'start' で設定してください。")
                continue

            if low.startswith("log "):
                try:
                    w = float(low.split()[1])
                    state["history"] = state.get("history", [])
                    state["history"].append({"date": datetime.now(), "weight": round(w, 2)})
                    save_state(uid, state)
                    # suggestion
                    hist = state["history"]
                    # build lightweight for core
                    simple_hist = [{"date": h["date"], "weight": h["weight"]} for h in hist]
                    s = suggest_after_log(simple_hist, profile.get("mode", "recomp"))
                    reply(ev.reply_token, f"記録しました: {w} kg\n{s}")
                except Exception as e:
                    reply(ev.reply_token, f"ログに失敗: {e}\n例: log 65.2")
                continue

            if low == "history":
                hist = state.get("history", [])
                if not hist:
                    reply(ev.reply_token, "まだ体重履歴がありません。'log 65.2' のように記録してください。")
                else:
                    # Convert for core summary
                    simple = [{"date": h["date"], "weight": h["weight"]} for h in hist]
                    s7 = summarise_history(simple, days=7)
                    s30 = summarise_history(simple, days=30)
                    msg = ["📈 履歴サマリ"]
                    if s7.get("count", 0) > 0:
                        msg.append(f"7日: {s7['from']}→{s7['to']} ({s7['trend']})  平均 {s7['avg']}kg  変化 {s7['delta']}kg")
                    if s30.get("count", 0) > 0:
                        msg.append(f"30日: {s30['from']}→{s30['to']} ({s30['trend']}) 平均 {s30['avg']}kg  変化 {s30['delta']}kg")
                    reply(ev.reply_token, "\n".join(msg))
                continue

            if low == "guide":
                if not profile.get("mode"):
                    reply(ev.reply_token, "まずは 'start' でプロフィールを設定してください。")
                else:
                    reply(ev.reply_token, guide_text(profile.get("mode")))
                continue

            if low.startswith("profile show"):
                reply(ev.reply_token, f"プロフィール: {json.dumps(profile, ensure_ascii=False)}")
                continue

            if low.startswith("profile set "):
                # e.g. profile set activity active
                parts = text.split()
                try:
                    key, value = parts[2], parts[3]
                    if key in {"age"}: value = int(value)
                    elif key in {"height_cm", "weight_kg", "goal_weight"}: value = float(value)
                    state["profile"][key] = value
                    save_state(uid, state)
                    reply(ev.reply_token, f"更新しました: {key} = {value}")
                except Exception as e:
                    reply(ev.reply_token, "例: profile set activity active\n    profile set goal_weight 62")
                continue

            # Onboarding flow
            if state.get("stage") == "onboarding":
                idx = state.get("onboard_idx", 0)
                key, prompt = ONBOARD_KEYS[idx]
                val = text.strip()
                if key == "sex":
                    if val.lower() not in {"male", "female"}:
                        reply(ev.reply_token, "male/female を入力してください"); continue
                    state["profile"]["sex"] = val.lower()
                elif key == "age":
                    state["profile"]["age"] = int(val)
                elif key == "height_cm":
                    state["profile"]["height_cm"] = float(val)
                elif key == "weight_kg":
                    state["profile"]["weight_kg"] = float(val)
                elif key == "activity":
                    if val not in {"sedentary", "light", "moderate", "active", "very_active"}:
                        reply(ev.reply_token, "sedentary/light/moderate/active/very_active から選択してください"); continue
                    state["profile"]["activity"] = val
                elif key == "mode":
                    if val.lower() not in {"cut", "recomp", "bulk"}:
                        reply(ev.reply_token, "cut/recomp/bulk から選択してください"); continue
                    state["profile"]["mode"] = val.lower()
                elif key == "goal_weight":
                    if val.lower() == "skip":
                        state["profile"]["goal_weight"] = None
                    else:
                        state["profile"]["goal_weight"] = float(val)
                elif key == "deadline_days":
                    if val.lower() == "skip":
                        state["profile"]["deadline_days"] = None
                    else:
                        state["profile"]["deadline_days"] = int(val)

                idx += 1
                state["onboard_idx"] = idx
                done = min(idx, len(ONBOARD_KEYS))
                if idx >= len(ONBOARD_KEYS):
                    # finished
                    state["stage"] = "idle"
                    save_state(uid, state)
                    # show plan
                    try:
                        validate_profile(state["profile"])
                        pb = progress_bar(done, len(ONBOARD_KEYS))
                        msg = f"オンボーディング完了！\n{pb}\n\n" + format_plan(state["profile"])
                    except Exception as e:
                        msg = f"設定に不足があります: {e}"
                    reply(ev.reply_token, msg)
                else:
                    pb = progress_bar(done, len(ONBOARD_KEYS))
                    qkey, qtext = ONBOARD_KEYS[idx]
                    save_state(uid, state)
                    reply(ev.reply_token, f"{pb}\n{qtext}")
                continue

            # Fallback
            reply(ev.reply_token, "コマンドが見つかりません。'help' を送って使い方を確認してください。")

    return JSONResponse({"status": "ok"})
