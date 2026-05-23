import os
import asyncio
import sqlite3
import poplib
import secrets
import re
import html
from email import message_from_bytes
from email.header import decode_header
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from aiosmtpd.controller import Controller
from typing import List, Optional

app = FastAPI()
DB_FILE = "myserver_mail.db"

# ==========================================
# 核心优化：动态读取系统环境变量，彻底告别占位符
# ==========================================
ENV_FILE = "/etc/lightningmail.env"
ENV_CONFIG = {}
if os.path.exists(ENV_FILE):
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                ENV_CONFIG[k.strip()] = v.strip(' "\'')

# 获取外部设定的 POP3 端口，若不存在则默认回退到安全的 110
POP3_PORT = int(ENV_CONFIG.get("LM_PORT", 110))
# ==========================================

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS domains (id INTEGER PRIMARY KEY AUTOINCREMENT, domain_name TEXT UNIQUE)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS emails (id INTEGER PRIMARY KEY AUTOINCREMENT, to_address TEXT, from_address TEXT, subject TEXT, body TEXT, raw_source BLOB, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS pop3_configs (domain_suffix TEXT PRIMARY KEY, display_name TEXT, pop3_server TEXT, pop3_port INTEGER, use_ssl BOOLEAN, is_active BOOLEAN)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS accounts (email TEXT PRIMARY KEY, password TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS web_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, token TEXT)''')
        try: cursor.execute('ALTER TABLE accounts ADD COLUMN owner_token TEXT')
        except: pass
        try: cursor.execute('ALTER TABLE accounts ADD COLUMN remark TEXT')
        except: pass
        conn.commit()
init_db()

def decode_mime_word(s):
    if not s: return ""
    res = ""
    for part, charset in decode_header(s):
        if isinstance(part, bytes):
            res += part.decode(charset or 'utf-8', errors='ignore')
        else: res += str(part)
    return res

class LocalMailHandler:
    async def handle_DATA(self, server, session, envelope):
        rcpt_tos = envelope.rcpt_tos
        raw_source = envelope.content
        msg = message_from_bytes(raw_source)
        subject = decode_mime_word(msg.get('Subject', "无主题"))
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    break
        else: body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            for to_addr in rcpt_tos:
                to_addr = to_addr.lower()
                if "@" not in to_addr: continue
                _, domain = to_addr.split("@", 1)
                cursor.execute("SELECT 1 FROM domains WHERE domain_name = ?", (domain,))
                if cursor.fetchone():
                    cursor.execute("INSERT INTO emails (to_address, from_address, subject, body, raw_source) VALUES (?, ?, ?, ?, ?)", (to_addr, envelope.mail_from, subject, body, raw_source))
                    conn.commit()
        return '250 Message accepted for delivery'

async def pop3_handle_client(reader, writer):
    writer.write(b"+OK MyServer POP3 Server Ready\r\n")
    await writer.drain()
    user = None
    authenticated = False
    mail_list = []
    try:
        while True:
            line = await reader.readline()
            if not line: break
            cmd_line = line.decode('utf-8', errors='ignore').strip()
            if not cmd_line: continue
            parts = cmd_line.split(' ', 1)
            cmd = parts[0].upper()
            arg = parts[1] if len(parts) > 1 else ""
            
            if cmd == 'QUIT':
                writer.write(b"+OK Goodbye\r\n")
                await writer.drain()
                break
            if not authenticated:
                if cmd == 'USER':
                    user = arg.lower()
                    writer.write(b"+OK User accepted\r\n")
                elif cmd == 'PASS':
                    if user and '@' in user:
                        with sqlite3.connect(DB_FILE) as conn:
                            cursor = conn.cursor()
                            cursor.execute("SELECT 1 FROM accounts WHERE email = ? AND password = ?", (user, arg))
                            if cursor.fetchone():
                                authenticated = True
                                cursor.execute("SELECT id, raw_source FROM emails WHERE to_address = ? ORDER BY id ASC", (user,))
                                mail_list = cursor.fetchall()
                                writer.write(b"+OK Mailbox open\r\n")
                            else: writer.write(b"-ERR Authentication failed\r\n")
                    else: writer.write(b"-ERR Invalid user format\r\n")
                else: writer.write(b"-ERR Login first\r\n")
                await writer.drain()
                continue
            
            if cmd == 'STAT': writer.write(f"+OK {len(mail_list)} {sum(len(m[1]) for m in mail_list)}\r\n".encode())
            elif cmd == 'LIST':
                writer.write(f"+OK {len(mail_list)} messages\r\n".encode())
                for i, m in enumerate(mail_list): writer.write(f"{i+1} {len(m[1])}\r\n".encode())
                writer.write(b".\r\n")
            elif cmd == 'RETR':
                try:
                    idx = int(arg) - 1
                    if 0 <= idx < len(mail_list):
                        raw = mail_list[idx][1]
                        writer.write(f"+OK {len(raw)} octets\r\n".encode())
                        writer.write(raw)
                        if not raw.endswith(b'\r\n'): writer.write(b'\r\n')
                        writer.write(b".\r\n")
                    else: writer.write(b"-ERR No such message\r\n")
                except: writer.write(b"-ERR Invalid args\r\n")
            elif cmd == 'UIDL':
                writer.write(b"+OK\r\n")
                for i, m in enumerate(mail_list): writer.write(f"{i+1} msg_{m[0]}\r\n".encode())
                writer.write(b".\r\n")
            else: writer.write(b"+OK\r\n")
            await writer.drain()
    except: pass
    finally:
        writer.close()
        await writer.wait_closed()

class AuthModel(BaseModel): username: str; password: str
class POP3ConfigModel(BaseModel): domain_suffix: str; display_name: str; pop3_server: str; pop3_port: int; use_ssl: bool; is_active: bool
class AccountModel(BaseModel): email: str; password: str; remark: Optional[str] = ""
class SyncRequest(BaseModel): accounts: List[AccountModel]
class UnlinkRequest(BaseModel): email: str

@app.post("/api/account/create")
def create_single_account(acc: AccountModel, authorization: str = Header(None)):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO accounts (email, password, owner_token, remark) VALUES (?, ?, ?, ?)",
                       (acc.email.lower(), acc.password, authorization, acc.remark))
        conn.commit()
    return {"status": "success"}

@app.post("/api/account/unlink")
def unlink_account(req: UnlinkRequest, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE accounts SET owner_token = NULL WHERE email = ? AND owner_token = ?", (req.email.lower(), authorization))
        conn.commit()
    return {"status": "success"}

@app.post("/api/auth/register")
def register(req: AuthModel):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        try:
            token = secrets.token_hex(16)
            cursor.execute("INSERT INTO web_users (username, password, token) VALUES (?, ?, ?)", (req.username, req.password, token))
            conn.commit()
            return {"status": "success", "token": token, "username": req.username}
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="用户名已存在")

@app.post("/api/auth/login")
def login(req: AuthModel):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT token FROM web_users WHERE username=? AND password=?", (req.username, req.password))
        res = cursor.fetchone()
        if not res: raise HTTPException(status_code=401, detail="账号或密码错误")
        return {"status": "success", "token": res[0], "username": req.username}

@app.get("/api/user/accounts")
def get_user_accounts(authorization: str = Header(None)):
    if not authorization: return {"accounts": []}
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email, password, remark FROM accounts WHERE owner_token=? ORDER BY rowid DESC", (authorization,))
        return {"accounts": [{"email": r[0], "password": r[1], "remark": r[2] or ""} for r in cursor.fetchall()]}

@app.post("/api/user/sync")
def sync_accounts(req: SyncRequest, authorization: str = Header(None)):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        for acc in req.accounts:
            cursor.execute('''
                INSERT INTO accounts (email, password, owner_token, remark) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET 
                owner_token=excluded.owner_token, remark=excluded.remark
            ''', (acc.email.lower(), acc.password, authorization, acc.remark))
        conn.commit()
    return {"status": "success"}

@app.post("/api/admin/config")
def save_config(config: POP3ConfigModel):
    suffix = config.domain_suffix.strip().lower()
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO domains (domain_name) VALUES (?)", (suffix,))
        conn.commit()
    return {"status": "success"}

@app.get("/api/admin/config/{suffix}")
def get_config(suffix: str):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM domains WHERE domain_name=?", (suffix.lower(),))
        if cursor.fetchone(): return {"is_active": True}
        return None

class LoginRequest(BaseModel): email: str; password: str

@app.post("/api/login")
def login_and_fetch(req: LoginRequest):
    email = req.email.strip().lower()
    if "@" not in email: raise HTTPException(status_code=400, detail="邮箱格式错误")
    
    try:
        # 使用动态加载的全局 POP3_PORT 变量
        pop = poplib.POP3('127.0.0.1', POP3_PORT, timeout=5)
        pop.user(email)
        pop.pass_(req.password)
        
        num_msgs = len(pop.list()[1])
        emails = []
        start = max(1, num_msgs - 29) 
        
        for i in range(num_msgs, start - 1, -1):
            _, lines, _ = pop.retr(i)
            msg = message_from_bytes(b'\r\n'.join(lines))
            
            sub = decode_mime_word(msg.get("Subject", "无主题"))
            sender = decode_mime_word(msg.get("From", "未知"))
            
            body_plain = ""
            body_html = ""
            
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    charset = part.get_content_charset() or 'utf-8'
                    if ctype == "text/plain" and not body_plain:
                        body_plain = part.get_payload(decode=True).decode(charset, errors="ignore")
                    elif ctype == "text/html" and not body_html:
                        body_html = part.get_payload(decode=True).decode(charset, errors="ignore")
            else:
                charset = msg.get_content_charset() or 'utf-8'
                ctype = msg.get_content_type()
                if ctype == "text/plain": body_plain = msg.get_payload(decode=True).decode(charset, errors="ignore")
                elif ctype == "text/html": body_html = msg.get_payload(decode=True).decode(charset, errors="ignore")
                else: body_plain = msg.get_payload(decode=True).decode(charset, errors="ignore")
            
            raw_text = body_plain if body_plain else re.sub(r'<[^>]+>', ' ', body_html)
            raw_text = html.unescape(raw_text).strip()
            snippet = " ".join(raw_text.split())[:80] + ("..." if len(raw_text) > 80 else "")
            
            full_html = body_html if body_html else f"<pre style='font-family:sans-serif; white-space:pre-wrap; padding:20px;'>{html.escape(body_plain)}</pre>"
                
            emails.append({
                "from": sender, 
                "subject": sub, 
                "snippet": snippet, 
                "full_html": full_html, 
                "date": msg.get("Date", "")
            })
        pop.quit()
        return {"status": "success", "emails": emails}
    except Exception as e:
        raise HTTPException(status_code=401, detail="凭证错误")

@app.get("/", response_class=HTMLResponse)
def index():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()

@app.on_event("startup")
async def startup_event():
    smtp_handler = LocalMailHandler()
    smtp_controller = Controller(smtp_handler, hostname='0.0.0.0', port=25)
    smtp_controller.start()
    
    # 使用动态加载的全局 POP3_PORT 变量
    await asyncio.start_server(pop3_handle_client, '0.0.0.0', POP3_PORT)

if __name__ == "__main__":
    import uvicorn
    # 强制在 127.0.0.1 运行，只接受 Nginx 代理，完美避开公网骚扰
    uvicorn.run(app, host="127.0.0.1", port=8888)
