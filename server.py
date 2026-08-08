import os
import time
import hmac
import json
import hashlib
import sqlite3
from urllib.parse import parse_qsl
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "tapalka.db")

app = FastAPI(title="Coin Tap Mini App")
app.mount("/static", StaticFiles(directory="web"), name="static")

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        coins INTEGER DEFAULT 0,
        energy INTEGER DEFAULT 1000,
        max_energy INTEGER DEFAULT 1000,
        tap_power INTEGER DEFAULT 1,
        energy_level INTEGER DEFAULT 1,
        last_energy_ts INTEGER DEFAULT 0,
        referred_by INTEGER DEFAULT NULL,
        referrals INTEGER DEFAULT 0,
        created_at INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_users_coins ON users(coins DESC);
    """)
    con.commit()
    con.close()

init_db()

def check_init_data(init_data: str):
    if not BOT_TOKEN or not init_data:
        raise HTTPException(401, "Invalid init data")

    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "Missing hash")

    auth_date = int(data.get("auth_date", "0"))
    if time.time() - auth_date > 86400:
        raise HTTPException(401, "Init data expired")

    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(401, "Invalid signature")

    user = json.loads(data.get("user", "{}"))
    if not user.get("id"):
        raise HTTPException(401, "User not found")
    return user

def get_user(tg_id):
    con = db()
    row = con.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    con.close()
    return row

def create_user(user, referral_code=None):
    tg_id = int(user["id"])
    now = int(time.time())
    con = db()
    existing = con.execute("SELECT tg_id FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    if not existing:
        ref = None
        if referral_code and str(referral_code).isdigit():
            ref_candidate = int(referral_code)
            if ref_candidate != tg_id and con.execute(
                "SELECT tg_id FROM users WHERE tg_id=?", (ref_candidate,)
            ).fetchone():
                ref = ref_candidate
        con.execute(
            """INSERT INTO users
            (tg_id, username, first_name, energy, max_energy, tap_power,
             energy_level, last_energy_ts, referred_by, created_at)
            VALUES (?, ?, ?, 1000, 1000, 1, 1, ?, ?, ?)""",
            (tg_id, user.get("username",""), user.get("first_name",""), now, ref, now)
        )
        if ref:
            con.execute("UPDATE users SET referrals=referrals+1, coins=coins+1000 WHERE tg_id=?", (ref,))
        con.commit()
    con.close()

def regenerate(row):
    now = int(time.time())
    energy = row["energy"]
    elapsed = max(0, now - row["last_energy_ts"])
    energy = min(row["max_energy"], energy + elapsed)
    return energy, now

class InitRequest(BaseModel):
    init_data: str
    start_param: str | None = None

class TapRequest(BaseModel):
    init_data: str
    taps: int

class UpgradeRequest(BaseModel):
    init_data: str
    kind: str

@app.get("/")
def index():
    return FileResponse("web/index.html")

@app.post("/api/init")
def api_init(req: InitRequest):
    user = check_init_data(req.init_data)
    create_user(user, req.start_param)
    row = get_user(int(user["id"]))
    energy, now = regenerate(row)

    con = db()
    con.execute("UPDATE users SET energy=?, last_energy_ts=?, username=?, first_name=? WHERE tg_id=?",
                (energy, now, user.get("username",""), user.get("first_name",""), int(user["id"])))
    con.commit()
    con.close()

    return state(int(user["id"]))

def state(tg_id):
    row = get_user(tg_id)
    if not row:
        raise HTTPException(404, "User not found")
    energy, now = regenerate(row)
    con = db()
    con.execute("UPDATE users SET energy=?, last_energy_ts=? WHERE tg_id=?", (energy, now, tg_id))
    con.commit()
    con.close()
    row = get_user(tg_id)
    return {
        "id": row["tg_id"],
        "name": row["first_name"] or row["username"] or "Игрок",
        "coins": row["coins"],
        "energy": row["energy"],
        "max_energy": row["max_energy"],
        "tap_power": row["tap_power"],
        "energy_level": row["energy_level"],
        "referrals": row["referrals"],
    }

@app.post("/api/tap")
def tap(req: TapRequest):
    if req.taps < 1 or req.taps > 20:
        raise HTTPException(400, "Bad tap count")
    user = check_init_data(req.init_data)
    tg_id = int(user["id"])
    row = get_user(tg_id)
    if not row:
        create_user(user)
        row = get_user(tg_id)

    energy, now = regenerate(row)
    actual = min(req.taps, energy)
    gained = actual * row["tap_power"]

    con = db()
    con.execute(
        "UPDATE users SET coins=coins+?, energy=?, last_energy_ts=? WHERE tg_id=?",
        (gained, energy-actual, now, tg_id)
    )
    con.commit()
    con.close()
    return state(tg_id)

@app.post("/api/upgrade")
def upgrade(req: UpgradeRequest):
    user = check_init_data(req.init_data)
    tg_id = int(user["id"])
    row = get_user(tg_id)
    if not row:
        create_user(user)
        row = get_user(tg_id)

    if req.kind == "tap":
        level = row["tap_power"]
        cost = 100 * (level ** 2)
        if row["coins"] < cost:
            raise HTTPException(400, "Недостаточно монет")
        con = db()
        con.execute("UPDATE users SET coins=coins-?, tap_power=tap_power+1 WHERE tg_id=?", (cost, tg_id))
    elif req.kind == "energy":
        level = row["energy_level"]
        cost = 150 * (level ** 2)
        if row["coins"] < cost:
            raise HTTPException(400, "Недостаточно монет")
        con = db()
        con.execute(
            "UPDATE users SET coins=coins-?, energy_level=energy_level+1, max_energy=max_energy+250 WHERE tg_id=?",
            (cost, tg_id)
        )
    else:
        raise HTTPException(400, "Unknown upgrade")

    con.commit()
    con.close()
    return state(tg_id)

@app.get("/api/leaderboard")
def leaderboard():
    con = db()
    rows = con.execute(
        "SELECT first_name, username, coins FROM users ORDER BY coins DESC LIMIT 20"
    ).fetchall()
    con.close()
    return [
        {"name": r["first_name"] or r["username"] or "Игрок", "coins": r["coins"]}
        for r in rows
    ]
