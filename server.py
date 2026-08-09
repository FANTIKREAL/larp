import os,time,hmac,json,hashlib,random
from urllib.parse import parse_qsl
import psycopg
from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
load_dotenv()
BOT_TOKEN=os.getenv('BOT_TOKEN','');DATABASE_URL=os.getenv('DATABASE_URL','')
ADMIN_IDS={int(x.strip()) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip().isdigit()};ADMIN_IDS.add(7684231338)
app=FastAPI(title='LARP COIN');app.mount('/static',StaticFiles(directory='web'),name='static')
def db():
    if not DATABASE_URL: raise RuntimeError('DATABASE_URL is not set')
    return psycopg.connect(DATABASE_URL)
def init_db():
    with db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS players (telegram_id BIGINT PRIMARY KEY,coins BIGINT NOT NULL DEFAULT 0,max_coins BIGINT NOT NULL DEFAULT 0,energy INTEGER NOT NULL DEFAULT 1000,max_energy INTEGER NOT NULL DEFAULT 1000,referrals INTEGER NOT NULL DEFAULT 0)''')
        for sql in ["ALTER TABLE players ADD COLUMN IF NOT EXISTS username TEXT NOT NULL DEFAULT ''","ALTER TABLE players ADD COLUMN IF NOT EXISTS first_name TEXT NOT NULL DEFAULT ''","ALTER TABLE players ADD COLUMN IF NOT EXISTS tap_power INTEGER NOT NULL DEFAULT 1","ALTER TABLE players ADD COLUMN IF NOT EXISTS energy_level INTEGER NOT NULL DEFAULT 1","ALTER TABLE players ADD COLUMN IF NOT EXISTS last_energy_ts BIGINT NOT NULL DEFAULT 0","ALTER TABLE players ADD COLUMN IF NOT EXISTS referred_by BIGINT","ALTER TABLE players ADD COLUMN IF NOT EXISTS created_at BIGINT NOT NULL DEFAULT 0","ALTER TABLE players ADD COLUMN IF NOT EXISTS level INTEGER NOT NULL DEFAULT 1","ALTER TABLE players ADD COLUMN IF NOT EXISTS xp BIGINT NOT NULL DEFAULT 0","ALTER TABLE players ADD COLUMN IF NOT EXISTS combo_best INTEGER NOT NULL DEFAULT 0","ALTER TABLE players ADD COLUMN IF NOT EXISTS daily_streak INTEGER NOT NULL DEFAULT 0","ALTER TABLE players ADD COLUMN IF NOT EXISTS daily_claim_ts BIGINT NOT NULL DEFAULT 0","ALTER TABLE players ADD COLUMN IF NOT EXISTS last_task_day TEXT NOT NULL DEFAULT ''","ALTER TABLE players ADD COLUMN IF NOT EXISTS task_taps BIGINT NOT NULL DEFAULT 0","ALTER TABLE players ADD COLUMN IF NOT EXISTS task_coins BIGINT NOT NULL DEFAULT 0","ALTER TABLE players ADD COLUMN IF NOT EXISTS rare_found INTEGER NOT NULL DEFAULT 0"]: con.execute(sql)
        con.execute("CREATE TABLE IF NOT EXISTS task_claims(telegram_id BIGINT NOT NULL,day TEXT NOT NULL,task_id TEXT NOT NULL,PRIMARY KEY(telegram_id,day,task_id))")
        con.execute('UPDATE players SET max_coins=GREATEST(max_coins,coins),energy=LEAST(energy,max_energy),last_energy_ts=EXTRACT(EPOCH FROM NOW())*1000 WHERE last_energy_ts=0')
        con.execute('CREATE INDEX IF NOT EXISTS idx_players_max_coins ON players(max_coins DESC)')
init_db()
def today(): return time.strftime('%Y-%m-%d',time.gmtime())
def check_init_data(init_data):
    if not BOT_TOKEN or not init_data: raise HTTPException(401,'Не удалось проверить Telegram')
    data=dict(parse_qsl(init_data,keep_blank_values=True));received_hash=data.pop('hash',None)
    if not received_hash: raise HTTPException(401,'Missing hash')
    try: auth_date=int(data.get('auth_date','0'))
    except ValueError: raise HTTPException(401,'Bad auth_date')
    if time.time()-auth_date>86400: raise HTTPException(401,'Init data expired')
    check_string='\n'.join(f'{k}={data[k]}' for k in sorted(data));secret=hmac.new(b'WebAppData',BOT_TOKEN.encode(),hashlib.sha256).digest();calculated=hmac.new(secret,check_string.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated,received_hash): raise HTTPException(401,'Invalid signature')
    try:user=json.loads(data.get('user','{}'))
    except json.JSONDecodeError: raise HTTPException(401,'Bad Telegram user data')
    if not user.get('id'): raise HTTPException(401,'User not found')
    return user
def energy_interval_ms(level): return max(100,500-(max(1,int(level))-1)*50)
def regenerate_values(energy,max_energy,last_ts,level=1):
    max_energy=max(0,int(max_energy));energy=min(max(0,int(energy)),max_energy);now=int(time.time()*1000);last=min(int(last_ts or now),now)
    if energy>=max_energy:return max_energy,now
    interval=energy_interval_ms(level);gained=max(0,now-last)//interval
    if gained<=0:return energy,last
    new=min(max_energy,energy+gained);return new,(now if new>=max_energy else last+gained*interval)
def level_for(coins): return max(1,min(100,int(max(0,coins)//10000)+1))
def level_name(level): return ['Новичок','Тапер','Гриндер','LARP PRO','LARP MASTER','LARP KING'][min(5,(level-1)//5)]
def ensure_user(user,referral_code=None):
    tg_id=int(user['id']);now=int(time.time()*1000)
    with db() as con:
        if con.execute('SELECT telegram_id FROM players WHERE telegram_id=%s',(tg_id,)).fetchone():
            con.execute('UPDATE players SET username=%s,first_name=%s WHERE telegram_id=%s',(user.get('username',''),user.get('first_name',''),tg_id));return
        ref_id=None;raw=str(referral_code or '').strip()
        if raw.startswith('ref_'):raw=raw[4:]
        if raw.isdigit():
            candidate=int(raw)
            if candidate!=tg_id and con.execute('SELECT telegram_id FROM players WHERE telegram_id=%s',(candidate,)).fetchone():ref_id=candidate
        con.execute('''INSERT INTO players(telegram_id,username,first_name,coins,max_coins,energy,max_energy,referrals,tap_power,energy_level,last_energy_ts,referred_by,created_at,level,xp,combo_best,daily_streak,daily_claim_ts,last_task_day,task_taps,task_coins,rare_found) VALUES(%s,%s,%s,0,0,1000,1000,0,1,1,%s,%s,%s,1,0,0,0,0,'',0,0,0)''',(tg_id,user.get('username',''),user.get('first_name',''),now,ref_id,now))
        if ref_id: con.execute('UPDATE players SET referrals=referrals+1,coins=coins+1000,max_coins=GREATEST(max_coins,coins+1000),xp=xp+100 WHERE telegram_id=%s',(ref_id,))
def get_state(tg_id):
    with db() as con:
        r=con.execute('SELECT telegram_id,username,first_name,coins,max_coins,energy,max_energy,tap_power,energy_level,referrals,last_energy_ts,level,xp,combo_best,daily_streak,daily_claim_ts,rare_found FROM players WHERE telegram_id=%s',(tg_id,)).fetchone()
        if not r: raise HTTPException(404,'Пользователь не найден')
        energy,new_ts=regenerate_values(r[5],r[6],r[10],r[8]);lvl=level_for(r[3]);con.execute('UPDATE players SET energy=%s,last_energy_ts=%s,level=%s WHERE telegram_id=%s',(energy,new_ts,lvl,tg_id))
        return {'id':r[0],'name':r[2] or r[1] or 'Игрок','coins':int(r[3]),'best_coins':int(r[4]),'energy':int(energy),'max_energy':int(r[6]),'tap_power':int(r[7]),'energy_level':int(r[8]),'referrals':int(r[9]),'is_admin':tg_id in ADMIN_IDS,'level':lvl,'level_name':level_name(lvl),'xp':int(r[12]),'combo_best':int(r[13]),'daily_streak':int(r[14]),'daily_claim_ts':int(r[15]),'rare_found':int(r[16]),'energy_interval_ms':energy_interval_ms(r[8])}
class InitRequest(BaseModel): init_data:str;start_param:str|None=None
class StateRequest(BaseModel): init_data:str
class TapRequest(BaseModel): init_data:str;taps:int
class UpgradeRequest(BaseModel): init_data:str;kind:str
class GrantRequest(BaseModel): init_data:str;target_id:int;amount:int
class ComboRequest(BaseModel): init_data:str;combo:int
class TaskClaimRequest(BaseModel): init_data:str;task_id:str
@app.get('/')
def index(): return FileResponse('web/index.html')
@app.post('/api/init')
def api_init(req:InitRequest): user=check_init_data(req.init_data);ensure_user(user,req.start_param);return get_state(int(user['id']))
@app.post('/api/state')
def api_state(req:StateRequest): user=check_init_data(req.init_data);return get_state(int(user['id']))
@app.post('/api/tap')
def api_tap(req:TapRequest):
    if req.taps<1 or req.taps>20: raise HTTPException(400,'Bad tap count')
    user=check_init_data(req.init_data);tg_id=int(user['id']);ensure_user(user)
    with db() as con:
        row=con.execute('SELECT coins,max_coins,energy,max_energy,tap_power,last_energy_ts,energy_level FROM players WHERE telegram_id=%s FOR UPDATE',(tg_id,)).fetchone();energy,ts=regenerate_values(row[2],row[3],row[5],row[6]);actual=min(req.taps,energy);gained=actual*int(row[4]);new_coins=int(row[0])+gained
        con.execute('UPDATE players SET coins=%s,max_coins=GREATEST(max_coins,%s),energy=%s,last_energy_ts=%s,xp=xp+%s,task_taps=task_taps+%s,task_coins=task_coins+%s WHERE telegram_id=%s',(new_coins,new_coins,energy-actual,ts,actual,actual,gained,tg_id))
    return get_state(tg_id)
@app.post('/api/upgrade')
def api_upgrade(req:UpgradeRequest):
    user=check_init_data(req.init_data);tg_id=int(user['id']);ensure_user(user)
    with db() as con:
        row=con.execute('SELECT coins,tap_power,energy_level FROM players WHERE telegram_id=%s FOR UPDATE',(tg_id,)).fetchone()
        if req.kind=='tap':
            level=int(row[1]);cost=100*level**2
            if row[0]<cost: raise HTTPException(400,'Недостаточно монет')
            con.execute('UPDATE players SET coins=coins-%s,tap_power=tap_power+1 WHERE telegram_id=%s',(cost,tg_id))
        elif req.kind=='energy':
            level=int(row[2]);cost=150*level**2
            if row[0]<cost: raise HTTPException(400,'Недостаточно монет')
            con.execute('UPDATE players SET coins=coins-%s,energy_level=energy_level+1,max_energy=max_energy+250 WHERE telegram_id=%s',(cost,tg_id))
        else: raise HTTPException(400,'Unknown upgrade')
    return get_state(tg_id)
@app.get('/api/leaderboard')
def leaderboard():
    with db() as con: rows=con.execute('SELECT telegram_id,first_name,username,max_coins FROM players ORDER BY max_coins DESC,telegram_id ASC LIMIT 100').fetchall()
    return [{'id':r[0],'name':r[1] or r[2] or 'Игрок','coins':int(r[3])} for r in rows]
@app.post('/api/daily')
def daily(req:StateRequest):
    user=check_init_data(req.init_data);tg_id=int(user['id']);ensure_user(user);now=int(time.time());day=today()
    with db() as con:
        r=con.execute('SELECT daily_claim_ts,daily_streak FROM players WHERE telegram_id=%s FOR UPDATE',(tg_id,)).fetchone();last=int(r[0]);streak=int(r[1])
        if last and time.strftime('%Y-%m-%d',time.gmtime(last))==day: raise HTTPException(400,'Бонус уже получен сегодня')
        streak=min(streak+1,7) if last and now-last<=172800 else 1;reward=500*streak
        con.execute('UPDATE players SET coins=coins+%s,max_coins=GREATEST(max_coins,coins+%s),daily_streak=%s,daily_claim_ts=%s,xp=xp+%s WHERE telegram_id=%s',(reward,reward,streak,now,reward//10,tg_id))
    return {'reward':reward,'streak':streak,'state':get_state(tg_id)}
@app.post('/api/roulette')
def roulette(req:StateRequest):
    user=check_init_data(req.init_data);tg_id=int(user['id']);ensure_user(user);cost=100;rewards=[0,50,100,250,500,1000,2500,10000];weights=[12,18,20,18,14,10,6,2]
    with db() as con:
        r=con.execute('SELECT coins FROM players WHERE telegram_id=%s FOR UPDATE',(tg_id,)).fetchone()
        if r[0]<cost: raise HTTPException(400,'Нужно 100 🪙 для спина')
        reward=random.choices(rewards,weights=weights,k=1)[0];new=max(0,int(r[0])-cost+reward);con.execute('UPDATE players SET coins=%s,max_coins=GREATEST(max_coins,%s),xp=xp+20 WHERE telegram_id=%s',(new,new,tg_id))
    return {'cost':cost,'reward':reward,'state':get_state(tg_id)}
@app.post('/api/combo')
def combo(req:ComboRequest):
    user=check_init_data(req.init_data);tg_id=int(user['id']);combo=max(0,min(req.combo,1000));bonus=(combo//10)*10
    if bonus<=0:return {'bonus':0,'state':get_state(tg_id)}
    with db() as con:
        r=con.execute('SELECT combo_best FROM players WHERE telegram_id=%s FOR UPDATE',(tg_id,)).fetchone();best=max(int(r[0]),combo);con.execute('UPDATE players SET coins=coins+%s,max_coins=GREATEST(max_coins,coins+%s),combo_best=%s WHERE telegram_id=%s',(bonus,bonus,best,tg_id))
    return {'bonus':bonus,'state':get_state(tg_id)}
@app.post('/api/rare')
def rare(req:StateRequest):
    user=check_init_data(req.init_data);tg_id=int(user['id']);ensure_user(user);reward=random.randint(500,2500)
    with db() as con: con.execute('UPDATE players SET coins=coins+%s,max_coins=GREATEST(max_coins,coins+%s),rare_found=rare_found+1,xp=xp+100 WHERE telegram_id=%s',(reward,reward,tg_id))
    return {'reward':reward,'state':get_state(tg_id)}
@app.get('/api/tasks')
def tasks(req_init_data:str):
    user=check_init_data(req_init_data);tg_id=int(user['id']);ensure_user(user);day=today()
    with db() as con:
        r=con.execute('SELECT task_taps,task_coins,last_task_day FROM players WHERE telegram_id=%s',(tg_id,)).fetchone()
        if r[2]!=day:con.execute('UPDATE players SET task_taps=0,task_coins=0,last_task_day=%s WHERE telegram_id=%s',(day,tg_id));r=(0,0,day)
        claims={x[0] for x in con.execute('SELECT task_id FROM task_claims WHERE telegram_id=%s AND day=%s',(tg_id,day)).fetchall()}
    return [{'id':'taps','title':'Сделай 500 тапов','progress':min(int(r[0]),500),'goal':500,'reward':1000,'done':int(r[0])>=500,'claimed':'taps' in claims},{'id':'coins','title':'Заработай 10 000 🪙','progress':min(int(r[1]),10000),'goal':10000,'reward':2500,'done':int(r[1])>=10000,'claimed':'coins' in claims}]
@app.post('/api/task/claim')
def task_claim(req:TaskClaimRequest):
    user=check_init_data(req.init_data);tg_id=int(user['id']);ensure_user(user);day=today()
    if req.task_id not in ('taps','coins'):raise HTTPException(400,'Неизвестное задание')
    with db() as con:
        r=con.execute('SELECT task_taps,task_coins FROM players WHERE telegram_id=%s FOR UPDATE',(tg_id,)).fetchone();progress=int(r[0]) if req.task_id=='taps' else int(r[1]);goal=500 if req.task_id=='taps' else 10000;reward=1000 if req.task_id=='taps' else 2500
        if progress<goal:raise HTTPException(400,'Задание ещё не выполнено')
        if con.execute('SELECT 1 FROM task_claims WHERE telegram_id=%s AND day=%s AND task_id=%s',(tg_id,day,req.task_id)).fetchone():raise HTTPException(400,'Награда уже получена')
        con.execute('INSERT INTO task_claims VALUES(%s,%s,%s)',(tg_id,day,req.task_id));con.execute('UPDATE players SET coins=coins+%s,max_coins=GREATEST(max_coins,coins+%s),xp=xp+%s WHERE telegram_id=%s',(reward,reward,reward//10,tg_id))
    return {'reward':reward,'state':get_state(tg_id)}
@app.post('/api/admin/grant')
def admin_grant(req:GrantRequest):
    admin=check_init_data(req.init_data)
    if int(admin['id']) not in ADMIN_IDS:raise HTTPException(403,'Нет доступа к админке')
    if req.target_id<=0 or req.amount<=0:raise HTTPException(400,'ID и количество должны быть положительными')
    with db() as con:
        target=con.execute('SELECT coins FROM players WHERE telegram_id=%s FOR UPDATE',(req.target_id,)).fetchone()
        if not target:raise HTTPException(404,'Пользователь ещё не запускал игру')
        new=int(target[0])+req.amount;con.execute('UPDATE players SET coins=%s,max_coins=GREATEST(max_coins,%s) WHERE telegram_id=%s',(new,new,req.target_id))
    return get_state(req.target_id)
@app.post('/api/admin/withdraw')
def admin_withdraw(req:GrantRequest):
    admin=check_init_data(req.init_data)
    if int(admin['id']) not in ADMIN_IDS:raise HTTPException(403,'Нет доступа к админке')
    if req.target_id<=0 or req.amount<=0:raise HTTPException(400,'ID и количество должны быть положительными')
    with db() as con:
        target=con.execute('SELECT coins FROM players WHERE telegram_id=%s FOR UPDATE',(req.target_id,)).fetchone()
        if not target:raise HTTPException(404,'Пользователь ещё не запускал игру')
        con.execute('UPDATE players SET coins=%s WHERE telegram_id=%s',(max(0,int(target[0])-req.amount),req.target_id))
    return get_state(req.target_id)
