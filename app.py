import os
import asyncio
import sqlite3
import poplib
import secrets
import re
import html
from email import message_from_bytes
from email.header import decode_header
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from aiosmtpd.controller import Controller
from typing import List, Optional

app = FastAPI()
DB_FILE = "myserver_mail.db"

# ==========================================
# 核心：动态读取系统环境变量
# ==========================================
ENV_FILE = "/etc/lightningmail.env"

def get_env_config():
    config = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    config[k.strip()] = v.strip(' "\'')
    return config

def save_env_config(key, value):
    config = get_env_config()
    config[key] = str(value)
    try:
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            for k, v in config.items():
                f.write(f'{k}="{v}"\n')
    except Exception as e:
        pass

_current_env = get_env_config()
POP3_PORT = int(_current_env.get("LM_PORT", 110))
POP3_HOST = _current_env.get("LM_POP_HOST", "127.0.0.1")

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

def get_attachments(msg):
    attachments = []
    for part_index, part in enumerate(msg.walk()):
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if not filename and disposition != "attachment":
            continue
        filename = decode_mime_word(filename or f"attachment-{len(attachments) + 1}")
        attachments.append({
            "index": part_index,
            "filename": filename,
            "content_type": part.get_content_type() or "application/octet-stream",
            "size": len(payload)
        })
    return attachments

def require_mailbox_access(email, password):
    email = email.strip().lower()
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM accounts WHERE email = ? AND password = ?", (email, password))
        if not cursor.fetchone():
            raise HTTPException(status_code=401, detail="Invalid mailbox credentials")
    return email

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
class SysConfigModel(BaseModel): pop_host: str; pop_port: int
class AttachmentRequest(BaseModel): email: str; password: str; mail_id: int; part_index: int

# ==========================================
# 新增系统配置同步接口
# ==========================================
@app.get("/api/system_config")
def get_sys_config():
    env_conf = get_env_config()
    return {
        "pop_host": env_conf.get("LM_POP_HOST", "127.0.0.1"),
        "pop_port": int(env_conf.get("LM_PORT", 110))
    }

@app.post("/api/admin/system_config")
def update_sys_config(config: SysConfigModel, authorization: str = Header(None)):
    global POP3_HOST, POP3_PORT
    POP3_HOST = config.pop_host
    POP3_PORT = config.pop_port
    save_env_config("LM_POP_HOST", POP3_HOST)
    save_env_config("LM_PORT", POP3_PORT)
    return {"status": "success"}
# ==========================================

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

@app.get("/api/admin/config")
def list_configs():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT domain_name FROM domains "
            "WHERE domain_name IS NOT NULL AND TRIM(domain_name) != '' "
            "ORDER BY id"
        )
        domains = [row[0].strip().lower() for row in cursor.fetchall()]
    return {"domains": domains}

@app.get("/api/admin/config/{suffix}")
def get_config(suffix: str):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM domains WHERE domain_name=?", (suffix.lower(),))
        if cursor.fetchone(): return {"is_active": True}
        return None

@app.delete("/api/admin/config/{suffix}")
def delete_config(suffix: str):
    suffix = suffix.strip().lower()
    if not suffix:
        raise HTTPException(status_code=400, detail="Domain suffix is required")
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM domains WHERE domain_name=?", (suffix,))
        conn.commit()
        deleted = cursor.rowcount
    return {"status": "success", "deleted": deleted}

class LoginRequest(BaseModel): email: str; password: str

@app.post("/api/login")
def login_and_fetch(req: LoginRequest):
    email = req.email.strip().lower()
    if "@" not in email: raise HTTPException(status_code=400, detail="邮箱格式错误")
    
    try:
        # 使用动态拉取的最新端口连接本地 POP3
        env_conf = get_env_config()
        current_port = int(env_conf.get("LM_PORT", 110))
        pop = poplib.POP3('127.0.0.1', current_port, timeout=5)
        pop.user(email)
        pop.pass_(req.password)
        
        num_msgs = len(pop.list()[1])
        uidl_map = {}
        try:
            for line in pop.uidl()[1]:
                parts = line.decode("utf-8", errors="ignore").split()
                if len(parts) >= 2 and parts[1].startswith("msg_"):
                    uidl_map[int(parts[0])] = int(parts[1][4:])
        except Exception:
            pass
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
                "id": uidl_map.get(i),
                "from": sender, 
                "subject": sub, 
                "snippet": snippet, 
                "full_html": full_html, 
                "date": msg.get("Date", ""),
                "attachments": get_attachments(msg)
            })
        pop.quit()
        return {"status": "success", "emails": emails}
    except Exception as e:
        raise HTTPException(status_code=401, detail="凭证错误")

@app.post("/api/mail/attachment")
def download_attachment(req: AttachmentRequest):
    email = require_mailbox_access(req.email, req.password)
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT raw_source FROM emails WHERE id = ? AND to_address = ?", (req.mail_id, email))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    msg = message_from_bytes(row[0])
    for part_index, part in enumerate(msg.walk()):
        if part_index != req.part_index:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            break
        filename = decode_mime_word(part.get_filename() or f"attachment-{part_index}")
        content_type = part.get_content_type() or "application/octet-stream"
        return Response(
            content=payload,
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
                "X-Content-Type-Options": "nosniff"
            }
        )
    raise HTTPException(status_code=404, detail="Attachment not found")

@app.get("/", response_class=HTMLResponse)
def index():
    with open("index.html", "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(
        content,
        headers={"Cache-Control": "no-store, max-age=0"},
    )

@app.on_event("startup")
async def startup_event():
    smtp_handler = LocalMailHandler()
    smtp_controller = Controller(smtp_handler, hostname='0.0.0.0', port=25)
    smtp_controller.start()
    await asyncio.start_server(pop3_handle_client, '0.0.0.0', POP3_PORT)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8888)
