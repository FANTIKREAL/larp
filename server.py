import os
import time
import hmac
import json
import hashlib
from urllib.parse import parse_qsl

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

app = FastAPI(title="LARP COIN")
app.mount("/static", StaticFiles(directory="web"), name="static")


def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(DATABASE_URL)


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS players (
                telegram_id BIGINT PRIMARY KEY,
                coins BIGINT NOT NULL DEFAULT 0,
                max_coins BIGINT NOT NULL DEFAULT 0,
                energy INTEGER NOT NULL DEFAULT 1000,
                max_energy INTEGER NOT NULL DEFAULT 1000,
                referrals INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS username TEXT NOT NULL DEFAULT ''")
        con.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS first_name TEXT NOT NULL DEFAULT ''")
        con.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS tap_power INTEGER NOT NULL DEFAULT 1")
        con.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS energy_level INTEGER NOT NULL DEFAULT 1")
        con.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS last_energy_ts BIGINT NOT NULL DEFAULT 0")
        con.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS referred_by BIGINT")
        con.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS created_at BIGINT NOT NULL DEFAULT 0")
        con.execute("UPDATE players SET max_coins = GREATEST(max_coins, coins)")
        con.execute("UPDATE players SET energy = LEAST(energy, max_energy)")
        con.execute("UPDATE players SET last_energy_ts = EXTRACT(EPOCH FROM NOW()) * 1000 WHERE last_energy_ts = 0")
        con.execute("CREATE INDEX IF NOT EXISTS idx_players_max_coins ON players(max_coins DESC)")


init_db()


def check_init_data(init_data: str):
    if not BOT_TOKEN or not init_data:
        raise HTTPException(401, "Не удалось проверить Telegram")
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "Missing hash")
    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        raise HTTPException(401, "Bad auth_date")
    if time.time() - auth_date > 86400:
        raise HTTPException(401, "Init data expired")
    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(401, "Invalid signature")
    try:
        user = json.loads(data.get("user", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(401, "Bad Telegram user data")
    if not user.get("id"):
        raise HTTPException(401, "User not found")
    return user


def regenerate_values(energy, max_energy, last_ts):
    """Restore exactly 1 energy every 500 ms, never above max_energy."""
    max_energy = max(0, int(max_energy))
    energy = min(max(0, int(energy)), max_energy)
    now = int(time.time() * 1000)
    last = int(last_ts or now)
    if last > now:
        last = now

    if energy >= max_energy:
        # Once full, don't accumulate a huge backlog while the player is away.
        return max_energy, now

    elapsed = max(0, now - last)
    gained = elapsed // 500
    if gained <= 0:
        return energy, last

    new_energy = min(max_energy, energy + gained)
    if new_energy >= max_energy:
        new_last = now
    else:
        new_last = last + gained * 500
    return new_energy, new_last


def ensure_user(user, referral_code=None):
    tg_id = int(user["id"])
    now = int(time.time() * 1000)
    with db() as con:
        row = con.execute("SELECT telegram_id FROM players WHERE telegram_id=%s", (tg_id,)).fetchone()
        if row:
            con.execute("UPDATE players SET username=%s, first_name=%s WHERE telegram_id=%s", (user.get("username", ""), user.get("first_name", ""), tg_id))
            return
        ref_id = None
        if referral_code:
            raw = str(referral_code)
            if raw.startswith("ref_"):
                raw = raw[4:]
            if raw.isdigit():
                candidate = int(raw)
                if candidate != tg_id:
                    exists = con.execute("SELECT telegram_id FROM players WHERE telegram_id=%s", (candidate,)).fetchone()
                    if exists:
                        ref_id = candidate
        con.execute("""
            INSERT INTO players
            (telegram_id, username, first_name, coins, max_coins, energy, max_energy,
             referrals, tap_power, energy_level, last_energy_ts, referred_by, created_at)
            VALUES (%s,%s,%s,0,0,1000,1000,0,1,1,%s,%s,%s)
        """, (tg_id, user.get("username", ""), user.get("first_name", ""), now, ref_id, now))
        if ref_id:
            con.execute("""
                UPDATE players
                SET referrals=referrals+1, coins=coins+1000,
                    max_coins=GREATEST(max_coins, coins+1000)
                WHERE telegram_id=%s
            """, (ref_id,))


def get_state(tg_id):
    with db() as con:
        r = con.execute("""
            SELECT telegram_id,username,first_name,coins,max_coins,energy,max_energy,
                   tap_power,energy_level,referrals,last_energy_ts
            FROM players WHERE telegram_id=%s
        """, (tg_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Пользователь не найден")

        energy, new_ts = regenerate_values(r[5], r[6], r[10])
        con.execute("UPDATE players SET energy=%s,last_energy_ts=%s WHERE telegram_id=%s", (energy, new_ts, tg_id))

        return {
            "id": r[0], "name": r[2] or r[1] or "Игрок", "coins": int(r[3]),
            "best_coins": int(r[4]), "energy": int(energy), "max_energy": int(r[6]),
            "tap_power": int(r[7]), "energy_level": int(r[8]), "referrals": int(r[9]),
            "is_admin": tg_id in ADMIN_IDS,
        }


class InitRequest(BaseModel):
    init_data: str
    start_param: str | None = None

class StateRequest(BaseModel):
    init_data: str

class TapRequest(BaseModel):
    init_data: str
    taps: int

class UpgradeRequest(BaseModel):
    init_data: str
    kind: str

class GrantRequest(BaseModel):
    init_data: str
    target_id: int
    amount: int


@app.get("/")
def index():
    return FileResponse("web/index.html")

@app.post("/api/init")
def api_init(req: InitRequest):
    user = check_init_data(req.init_data)
    ensure_user(user, req.start_param)
    return get_state(int(user["id"]))

@app.post("/api/state")
def api_state(req: StateRequest):
    user = check_init_data(req.init_data)
    return get_state(int(user["id"]))

@app.post("/api/tap")
def api_tap(req: TapRequest):
    if req.taps < 1 or req.taps > 20:
        raise HTTPException(400, "Bad tap count")
    user = check_init_data(req.init_data)
    tg_id = int(user["id"])
    ensure_user(user)
    with db() as con:
        row = con.execute("""
            SELECT coins,max_coins,energy,max_energy,tap_power,last_energy_ts
            FROM players WHERE telegram_id=%s FOR UPDATE
        """, (tg_id,)).fetchone()
        energy, ts = regenerate_values(row[2], row[3], row[5])
        actual = min(int(req.taps), energy)
        gained = actual * int(row[4])
        new_coins = int(row[0]) + gained
        new_best = max(int(row[1]), new_coins)
        con.execute("""
            UPDATE players SET coins=%s,max_coins=%s,energy=%s,last_energy_ts=%s
            WHERE telegram_id=%s
        """, (new_coins, new_best, energy - actual, ts, tg_id))
    return get_state(tg_id)

@app.post("/api/upgrade")
def api_upgrade(req: UpgradeRequest):
    user = check_init_data(req.init_data)
    tg_id = int(user["id"])
    ensure_user(user)
    with db() as con:
        row = con.execute("""
            SELECT coins,max_coins,tap_power,energy_level,max_energy
            FROM players WHERE telegram_id=%s FOR UPDATE
        """, (tg_id,)).fetchone()
        if req.kind == "tap":
            level = int(row[2]); cost = 100 * (level ** 2)
            if int(row[0]) < cost: raise HTTPException(400, "Недостаточно монет")
            con.execute("UPDATE players SET coins=coins-%s,tap_power=tap_power+1 WHERE telegram_id=%s", (cost, tg_id))
        elif req.kind == "energy":
            level = int(row[3]); cost = 150 * (level ** 2)
            if int(row[0]) < cost: raise HTTPException(400, "Недостаточно монет")
            con.execute("""
                UPDATE players SET coins=coins-%s,energy_level=energy_level+1,
                max_energy=max_energy+250 WHERE telegram_id=%s
            """, (cost, tg_id))
        else:
            raise HTTPException(400, "Unknown upgrade")
    return get_state(tg_id)

@app.get("/api/leaderboard")
def leaderboard():
    with db() as con:
        rows = con.execute("""
            SELECT first_name,username,max_coins FROM players
            ORDER BY max_coins DESC,telegram_id ASC LIMIT 20
        """).fetchall()
    return [{"name": r[0] or r[1] or "Игрок", "coins": int(r[2])} for r in rows]

@app.post("/api/admin/grant")
def admin_grant(req: GrantRequest):
    admin = check_init_data(req.init_data)
    if int(admin["id"]) not in ADMIN_IDS:
        raise HTTPException(403, "Нет доступа к админке")
    if req.target_id <= 0 or req.amount <= 0:
        raise HTTPException(400, "ID и количество должны быть положительными")
    with db() as con:
        target = con.execute("SELECT coins,max_coins FROM players WHERE telegram_id=%s FOR UPDATE", (req.target_id,)).fetchone()
        if not target:
            raise HTTPException(404, "Пользователь с таким Telegram ID ещё не запускал игру")
        new_coins = int(target[0]) + int(req.amount)
        new_best = max(int(target[1]), new_coins)
        con.execute("UPDATE players SET coins=%s,max_coins=%s WHERE telegram_id=%s", (new_coins, new_best, req.target_id))
    return get_state(req.target_id)
