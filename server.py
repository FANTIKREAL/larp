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

# В Render -> Environment Variables:
# ADMIN_IDS = "123456789,987654321"
def admin_ids():
    raw = os.getenv("ADMIN_IDS", "")
    return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}

app = FastAPI(title="LARP COIN")
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
        best_coins INTEGER DEFAULT 0,
        energy INTEGER DEFAULT 1000,
        max_energy INTEGER DEFAULT 1000,
        tap_power INTEGER DEFAULT 1,
        energy_level INTEGER DEFAULT 1,
        last_energy_ts INTEGER DEFAULT 0,
        referred_by INTEGER DEFAULT NULL,
        referrals INTEGER DEFAULT 0,
        created_at INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_users_best_coins ON users(best_coins DESC);
    """)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    if "best_coins" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN best_coins INTEGER DEFAULT 0")
    # Старые значения времени были в секундах. Переводим их в миллисекунды.
    con.execute("""
        UPDATE users
        SET last_energy_ts = last_energy_ts * 1000
        WHERE last_energy_ts > 0 AND last_energy_ts < 100000000000
    """)
    con.execute("UPDATE users SET best_coins=coins WHERE best_coins < coins")
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
    secret_key = hmac.new(
        b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    calculated = hmac.new(
        secret_key, check_string.encode(), hashlib.sha256
    ).hexdigest()

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
    now = int(time.time() * 1000)

    con = db()
    existing = con.execute(
        "SELECT tg_id FROM users WHERE tg_id=?", (tg_id,)
    ).fetchone()

    if not existing:
        ref = None
        if referral_code and str(referral_code).isdigit():
            ref_candidate = int(referral_code)
            if (
                ref_candidate != tg_id
                and con.execute(
                    "SELECT tg_id FROM users WHERE tg_id=?", (ref_candidate,)
                ).fetchone()
            ):
                ref = ref_candidate

        con.execute(
            """INSERT INTO users
            (tg_id, username, first_name, coins, best_coins, energy,
             max_energy, tap_power, energy_level, last_energy_ts,
             referred_by, created_at)
            VALUES (?, ?, ?, 0, 0, 1000, 1000, 1, 1, ?, ?, ?)""",
            (
                tg_id,
                user.get("username", ""),
                user.get("first_name", ""),
                now,
                ref,
                now,
            ),
        )

        if ref:
            # Реферальный бонус тоже влияет на рекорд.
            con.execute(
                """UPDATE users
                   SET referrals=referrals+1,
                       coins=coins+1000,
                       best_coins=MAX(best_coins, coins+1000)
                   WHERE tg_id=?""",
                (ref,),
            )

    con.commit()
    con.close()


def regenerate(row):
    now = int(time.time() * 1000)
    last = int(row["last_energy_ts"] or now)
    elapsed = max(0, now - last)

    # 1 энергия каждые 0.5 секунды.
    gained = elapsed // 500
    if gained <= 0:
        return int(row["energy"]), last

    energy = min(int(row["max_energy"]), int(row["energy"]) + int(gained))

    if energy >= int(row["max_energy"]):
        new_last = now
    else:
        new_last = last + int(gained) * 500

    return energy, new_last


class InitRequest(BaseModel):
    init_data: str
    start_param: str | None = None


class TapRequest(BaseModel):
    init_data: str
    taps: int


class UpgradeRequest(BaseModel):
    init_data: str
    kind: str


class StateRequest(BaseModel):
    init_data: str


class GrantRequest(BaseModel):
    init_data: str
    target_id: int
    amount: int


@app.get("/")
def index():
    return FileResponse("web/index.html")


def save_energy(tg_id, energy, ts):
    con = db()
    con.execute(
        "UPDATE users SET energy=?, last_energy_ts=? WHERE tg_id=?",
        (energy, ts, tg_id),
    )
    con.commit()
    con.close()


def state(tg_id):
    row = get_user(tg_id)
    if not row:
        raise HTTPException(404, "User not found")

    energy, ts = regenerate(row)
    save_energy(tg_id, energy, ts)
    row = get_user(tg_id)

    return {
        "id": row["tg_id"],
        "name": row["first_name"] or row["username"] or "Игрок",
        "coins": row["coins"],
        "best_coins": row["best_coins"],
        "energy": row["energy"],
        "max_energy": row["max_energy"],
        "tap_power": row["tap_power"],
        "energy_level": row["energy_level"],
        "referrals": row["referrals"],
        "is_admin": row["tg_id"] in admin_ids(),
    }


@app.post("/api/init")
def api_init(req: InitRequest):
    user = check_init_data(req.init_data)
    create_user(user, req.start_param)
    tg_id = int(user["id"])

    row = get_user(tg_id)
    energy, ts = regenerate(row)
    con = db()
    con.execute(
        """UPDATE users
           SET energy=?, last_energy_ts=?, username=?, first_name=?
           WHERE tg_id=?""",
        (
            energy,
            ts,
            user.get("username", ""),
            user.get("first_name", ""),
            tg_id,
        ),
    )
    con.commit()
    con.close()

    return state(tg_id)


@app.post("/api/state")
def api_state(req: StateRequest):
    user = check_init_data(req.init_data)
    return state(int(user["id"]))


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

    energy, ts = regenerate(row)
    actual = min(int(req.taps), int(energy))
    gained = actual * int(row["tap_power"])

    new_coins = int(row["coins"]) + gained
    new_best = max(int(row["best_coins"]), new_coins)

    con = db()
    con.execute(
        """UPDATE users
           SET coins=?, best_coins=?, energy=?, last_energy_ts=?
           WHERE tg_id=?""",
        (new_coins, new_best, energy - actual, ts, tg_id),
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
        level = int(row["tap_power"])
        cost = 100 * (level ** 2)

        if row["coins"] < cost:
            raise HTTPException(400, "Недостаточно монет")

        con = db()
        con.execute(
            "UPDATE users SET coins=coins-?, tap_power=tap_power+1 WHERE tg_id=?",
            (cost, tg_id),
        )

    elif req.kind == "energy":
        level = int(row["energy_level"])
        cost = 150 * (level ** 2)

        if row["coins"] < cost:
            raise HTTPException(400, "Недостаточно монет")

        con = db()
        con.execute(
            """UPDATE users
               SET coins=coins-?,
                   energy_level=energy_level+1,
                   max_energy=max_energy+250
               WHERE tg_id=?""",
            (cost, tg_id),
        )

    else:
        raise HTTPException(400, "Unknown upgrade")

    con.commit()
    con.close()

    # best_coins специально НЕ уменьшается после покупки.
    return state(tg_id)


@app.get("/api/leaderboard")
def leaderboard():
    con = db()
    rows = con.execute(
        """SELECT first_name, username, best_coins
           FROM users
           ORDER BY best_coins DESC, tg_id ASC
           LIMIT 20"""
    ).fetchall()
    con.close()

    return [
        {
            "name": r["first_name"] or r["username"] or "Игрок",
            "coins": r["best_coins"],
        }
        for r in rows
    ]


@app.post("/api/admin/grant")
def admin_grant(req: GrantRequest):
    admin = check_init_data(req.init_data)
    admin_id = int(admin["id"])

    if admin_id not in admin_ids():
        raise HTTPException(403, "Нет доступа к админке")

    if req.target_id <= 0 or req.amount <= 0:
        raise HTTPException(400, "ID и количество должны быть положительными")

    target = get_user(req.target_id)
    if not target:
        raise HTTPException(404, "Пользователь с таким Telegram ID ещё не запускал игру")

    new_coins = int(target["coins"]) + int(req.amount)
    new_best = max(int(target["best_coins"]), new_coins)

    con = db()
    con.execute(
        "UPDATE users SET coins=?, best_coins=? WHERE tg_id=?",
        (new_coins, new_best, req.target_id),
    )
    con.commit()
    con.close()

    return state(req.target_id)
