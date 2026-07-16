import os
import re
import time
import uuid
import sqlite3
import poplib
import html as html_lib
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr

from email import policy
from email.parser import BytesParser
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "mail_login_panel.db"

load_dotenv(BASE_DIR / ".env")

POP_HOST = os.getenv("POP_HOST", "127.0.0.1")
POP_PORT = int(os.getenv("POP_PORT", "110"))
POP_SSL = os.getenv("POP_SSL", "false").lower() == "true"
POP_TIMEOUT = int(os.getenv("POP_TIMEOUT", "10"))

SESSION_EXPIRE_MINUTES = int(os.getenv("SESSION_EXPIRE_MINUTES", "30"))
DEFAULT_ALLOWED_DOMAIN = os.getenv("DEFAULT_ALLOWED_DOMAIN", "edu.zitw.de").strip().lower()


app = FastAPI(title="Mail Login Panel", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


SESSIONS = {}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LogoutRequest(BaseModel):
    token: str


def now_ts() -> int:
    return int(time.time())


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS allowed_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS login_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            ip TEXT,
            success INTEGER NOT NULL,
            message TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )

    if DEFAULT_ALLOWED_DOMAIN:
        cur.execute(
            "INSERT OR IGNORE INTO allowed_domains(domain, created_at) VALUES(?, ?)",
            (DEFAULT_ALLOWED_DOMAIN, now_ts()),
        )

    conn.commit()
    conn.close()


def is_allowed_email(email: str) -> bool:
    email = email.strip().lower()

    if "@" not in email:
        return False

    domain = email.split("@", 1)[1]

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT domain FROM allowed_domains WHERE domain = ?", (domain,))
    row = cur.fetchone()
    conn.close()

    return row is not None


def clean_expired_sessions():
    expire_before = now_ts() - SESSION_EXPIRE_MINUTES * 60

    expired_tokens = []
    for token, sess in SESSIONS.items():
        if sess["last_active"] < expire_before:
            expired_tokens.append(token)

    for token in expired_tokens:
        SESSIONS.pop(token, None)


def create_pop_connection():
    if POP_SSL:
        return poplib.POP3_SSL(POP_HOST, POP_PORT, timeout=POP_TIMEOUT)
    return poplib.POP3(POP_HOST, POP_PORT, timeout=POP_TIMEOUT)


def test_pop_login(email: str, password: str):
    pop = None
    try:
        pop = create_pop_connection()

        try:
            welcome = pop.getwelcome().decode(errors="ignore")
        except Exception:
            welcome = ""

        pop.user(email)
        pop.pass_(password)

        count, size = pop.stat()

        try:
            pop.quit()
        except Exception:
            pass

        return {
            "success": True,
            "message": "POP 登录成功",
            "mail_count": count,
            "mailbox_size": size,
            "welcome": welcome,
        }

    except poplib.error_proto as e:
        try:
            if pop:
                pop.quit()
        except Exception:
            pass

        return {
            "success": False,
            "message": "邮箱账号或密码错误，或者 POP 服务拒绝登录",
            "detail": str(e),
        }

    except Exception as e:
        try:
            if pop:
                pop.quit()
        except Exception:
            pass

        return {
            "success": False,
            "message": "无法连接 POP 服务",
            "detail": str(e),
        }


def save_login_log(email: str, success: bool, message: str, ip: Optional[str] = None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO login_logs(email, ip, success, message, created_at)
        VALUES(?, ?, ?, ?, ?)
        """,
        (email, ip, 1 if success else 0, message, now_ts()),
    )
    conn.commit()
    conn.close()


def get_session_from_header(authorization: Optional[str]):
    clean_expired_sessions()

    if not authorization:
        raise HTTPException(status_code=401, detail="缺少登录 token")

    token = authorization.replace("Bearer ", "").strip()
    sess = SESSIONS.get(token)

    if not sess:
        raise HTTPException(status_code=401, detail="登录已过期")

    sess["last_active"] = now_ts()
    return sess


def decode_header_value(value) -> str:
    if not value:
        return ""

    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def safe_decode_payload(payload: bytes, charset: Optional[str]) -> str:
    candidates = []

    if charset:
        candidates.append(charset)

    candidates += [
        "utf-8",
        "gb18030",
        "gbk",
        "gb2312",
        "big5",
        "windows-1252",
        "iso-8859-1",
    ]

    used = set()

    for enc in candidates:
        if not enc:
            continue

        enc = enc.lower().strip()
        if enc in used:
            continue

        used.add(enc)

        try:
            return payload.decode(enc)
        except Exception:
            continue

    return payload.decode("utf-8", errors="replace")


def get_part_text(part) -> str:
    charset = part.get_content_charset()

    payload = part.get_payload(decode=True)

    if payload is None:
        raw = part.get_payload()
        if isinstance(raw, str):
            return raw
        return ""

    return safe_decode_payload(payload, charset)


def html_to_text(content: str) -> str:
    if not content:
        return ""

    content = re.sub(r"(?is)<script.*?>.*?</script>", " ", content)
    content = re.sub(r"(?is)<style.*?>.*?</style>", " ", content)
    content = re.sub(r"(?is)<br\s*/?>", "\n", content)
    content = re.sub(r"(?is)</p>", "\n", content)
    content = re.sub(r"(?is)<.*?>", " ", content)
    content = html_lib.unescape(content)
    content = re.sub(r"[ \t\r\f\v]+", " ", content)
    content = re.sub(r"\n\s*\n+", "\n", content)

    return content.strip()


def make_preview(text: str, length: int = 160) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) > length:
        return text[:length] + "..."
    return text


def extract_verification_code(subject: str, text: str, html: str) -> Optional[str]:
    source = " ".join(
        [
            subject or "",
            text or "",
            html_to_text(html or ""),
        ]
    )

    source = re.sub(r"\s+", " ", source)

    patterns = [
        r"(?i)(?:验证码|校验码|动态码|安全码|verification code|security code|login code|code|otp|passcode)[^\d]{0,30}(\d{4,8})",
        r"(?i)(\d{4,8})[^\d]{0,20}(?:验证码|校验码|动态码|verification code|security code|code|otp)",
        r"\b(\d{6})\b",
        r"\b(\d{4,8})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1)

    return None


def parse_mail_date(value) -> str:
    raw = decode_header_value(value)

    if not raw:
        return ""

    try:
        dt = parsedate_to_datetime(raw)
        return dt.isoformat()
    except Exception:
        return raw


def parse_email(raw_bytes: bytes, uidl: str) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    subject = decode_header_value(msg.get("Subject", ""))
    from_raw = decode_header_value(msg.get("From", ""))
    to_raw = decode_header_value(msg.get("To", ""))
    date_raw = msg.get("Date", "")

    from_name, from_email = parseaddr(from_raw)
    to_name, to_email = parseaddr(to_raw)

    from_name = decode_header_value(from_name)
    to_name = decode_header_value(to_name)

    text_parts = []
    html_parts = []
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue

            content_type = part.get_content_type()
            disposition = str(part.get_content_disposition() or "").lower()

            filename = part.get_filename()
            if filename:
                filename = decode_header_value(filename)

            if disposition == "attachment" or filename:
                payload = part.get_payload(decode=True) or b""
                attachments.append(
                    {
                        "filename": filename or "attachment",
                        "content_type": content_type,
                        "size": len(payload),
                    }
                )
                continue

            if content_type == "text/plain":
                text_parts.append(get_part_text(part))
            elif content_type == "text/html":
                html_parts.append(get_part_text(part))
    else:
        content_type = msg.get_content_type()

        if content_type == "text/html":
            html_parts.append(get_part_text(msg))
        else:
            text_parts.append(get_part_text(msg))

    html_content = "\n".join([x for x in html_parts if x]).strip()
    text_content = "\n".join([x for x in text_parts if x]).strip()

    if not text_content and html_content:
        text_content = html_to_text(html_content)

    preview = make_preview(text_content or html_to_text(html_content))
    code = extract_verification_code(subject, text_content, html_content)

    return {
        "uidl": uidl,
        "subject": subject,
        "from": from_email,
        "from_name": from_name,
        "from_raw": from_raw,
        "to": to_email,
        "to_name": to_name,
        "to_raw": to_raw,
        "date": parse_mail_date(date_raw),
        "preview": preview,
        "code": code,
        "html": html_content,
        "text": text_content,
        "has_attachment": len(attachments) > 0,
        "attachments": attachments,
    }


def login_pop(email: str, password: str):
    pop = create_pop_connection()
    pop.user(email)
    pop.pass_(password)
    return pop


def get_uidl_map(pop) -> dict:
    result = {}
    try:
        _, lines, _ = pop.uidl()
    except Exception:
        return result

    for line in lines:
        if isinstance(line, bytes):
            line = line.decode(errors="ignore")

        parts = line.strip().split()
        if len(parts) >= 2:
            try:
                number = int(parts[0])
                uidl = parts[1]
                result[number] = uidl
            except Exception:
                continue

    return result


def fetch_latest_mails(sess: dict, limit: int = 30) -> list:
    limit = max(1, min(limit, 100))

    email_addr = sess["email"]
    password = sess["password"]

    cache = sess.setdefault("cache", {})

    pop = None

    try:
        pop = login_pop(email_addr, password)
        uidl_map = get_uidl_map(pop)

        if not uidl_map:
            return []

        latest_numbers = sorted(uidl_map.keys(), reverse=True)[:limit]
        mails = []

        for number in latest_numbers:
            uidl = uidl_map[number]

            if uidl not in cache:
                _, lines, _ = pop.retr(number)
                raw_bytes = b"\r\n".join(lines)
                parsed = parse_email(raw_bytes, uidl)
                parsed["pop_number"] = number
                cache[uidl] = parsed

            mail = cache[uidl]

            mails.append(
                {
                    "uidl": mail["uidl"],
                    "subject": mail["subject"],
                    "from": mail["from"],
                    "from_name": mail["from_name"],
                    "date": mail["date"],
                    "preview": mail["preview"],
                    "code": mail["code"],
                    "has_attachment": mail["has_attachment"],
                    "attachments": mail["attachments"],
                }
            )

        return mails

    finally:
        try:
            if pop:
                pop.quit()
        except Exception:
            pass


def fetch_mail_detail_by_uidl(sess: dict, uidl: str) -> dict:
    cache = sess.setdefault("cache", {})

    if uidl in cache:
        return cache[uidl]

    email_addr = sess["email"]
    password = sess["password"]

    pop = None

    try:
        pop = login_pop(email_addr, password)
        uidl_map = get_uidl_map(pop)

        target_number = None

        for number, item_uidl in uidl_map.items():
            if item_uidl == uidl:
                target_number = number
                break

        if target_number is None:
            raise HTTPException(status_code=404, detail="邮件不存在或已被删除")

        _, lines, _ = pop.retr(target_number)
        raw_bytes = b"\r\n".join(lines)
        parsed = parse_email(raw_bytes, uidl)
        parsed["pop_number"] = target_number
        cache[uidl] = parsed

        return parsed

    finally:
        try:
            if pop:
                pop.quit()
        except Exception:
            pass


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(
        (BASE_DIR / "index.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )

@app.get("/api/health")
def health():
    clean_expired_sessions()

    return {
        "success": True,
        "message": "ok",
        "pop_host": POP_HOST,
        "pop_port": POP_PORT,
        "pop_ssl": POP_SSL,
        "online_count": len(SESSIONS),
    }


@app.post("/api/user/login")
def user_login(data: LoginRequest):
    clean_expired_sessions()

    email = data.email.strip().lower()
    password = data.password

    if not password:
        raise HTTPException(status_code=400, detail="请输入邮箱密码")

    if not is_allowed_email(email):
        save_login_log(email, False, "邮箱后缀不允许")
        raise HTTPException(status_code=403, detail="该邮箱后缀不允许登录")

    result = test_pop_login(email, password)

    if not result["success"]:
        save_login_log(email, False, result["message"])
        raise HTTPException(status_code=401, detail=result["message"])

    token = uuid.uuid4().hex

    SESSIONS[token] = {
        "email": email,
        "password": password,
        "login_time": now_ts(),
        "last_active": now_ts(),
        "mail_count": result.get("mail_count", 0),
        "cache": {},
    }

    save_login_log(email, True, "登录成功")

    return {
        "success": True,
        "message": "登录成功",
        "token": token,
        "email": email,
        "mail_count": result.get("mail_count", 0),
        "mailbox_size": result.get("mailbox_size", 0),
    }


@app.post("/api/user/logout")
def user_logout(data: LogoutRequest):
    SESSIONS.pop(data.token, None)

    return {
        "success": True,
        "message": "已退出登录",
    }


@app.get("/api/user/me")
def user_me(authorization: Optional[str] = Header(default=None)):
    sess = get_session_from_header(authorization)

    return {
        "success": True,
        "email": sess["email"],
        "login_time": sess["login_time"],
        "last_active": sess["last_active"],
    }


@app.get("/api/mail/list")
def mail_list(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(default=30, ge=1, le=100),
):
    sess = get_session_from_header(authorization)

    try:
        mails = fetch_latest_mails(sess, limit=limit)

        return {
            "success": True,
            "email": sess["email"],
            "count": len(mails),
            "mails": mails,
        }

    except poplib.error_proto:
        raise HTTPException(status_code=401, detail="POP 登录失效，请重新登录")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取邮件失败: {str(e)}")


@app.get("/api/mail/detail")
def mail_detail(
    uidl: str,
    authorization: Optional[str] = Header(default=None),
):
    sess = get_session_from_header(authorization)

    try:
        mail = fetch_mail_detail_by_uidl(sess, uidl)

        return {
            "success": True,
            "mail": mail,
        }

    except HTTPException:
        raise
    except poplib.error_proto:
        raise HTTPException(status_code=401, detail="POP 登录失效，请重新登录")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取邮件详情失败: {str(e)}")


@app.get("/api/admin/online")
def admin_online():
    clean_expired_sessions()

    users = []
    for token, sess in SESSIONS.items():
        users.append(
            {
                "email": sess["email"],
                "login_time": sess["login_time"],
                "last_active": sess["last_active"],
            }
        )

    return {
        "success": True,
        "online_count": len(users),
        "users": users,
    }


@app.get("/panel", response_class=HTMLResponse)
def panel_page():
    return (BASE_DIR / "index.html").read_text(encoding="utf-8")


# === ADMIN PANEL API START ===

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")

ADMIN_TOKENS = {}


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminDomainRequest(BaseModel):
    domain: str


def normalize_domain(domain: str) -> str:
    domain = (domain or "").strip().lower()
    domain = domain.replace("http://", "").replace("https://", "")
    domain = domain.strip().strip("/")
    if domain.startswith("@"):
        domain = domain[1:]
    return domain


def require_admin(authorization: Optional[str]):
    if not authorization:
        raise HTTPException(status_code=401, detail="缺少管理员 token")

    token = authorization.replace("Bearer ", "").strip()

    admin = ADMIN_TOKENS.get(token)
    if not admin:
        raise HTTPException(status_code=401, detail="管理员登录已过期")

    admin["last_active"] = now_ts()
    return admin


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return (BASE_DIR / "admin.html").read_text(encoding="utf-8")


@app.post("/api/admin/login")
def admin_login(data: AdminLoginRequest):
    username = data.username.strip()

    if username != ADMIN_USERNAME or data.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="管理员账号或密码错误")

    token = uuid.uuid4().hex

    ADMIN_TOKENS[token] = {
        "username": username,
        "login_time": now_ts(),
        "last_active": now_ts(),
    }

    return {
        "success": True,
        "message": "管理员登录成功",
        "token": token,
        "username": username,
    }


@app.get("/api/admin/me")
def admin_me(authorization: Optional[str] = Header(default=None)):
    admin = require_admin(authorization)

    return {
        "success": True,
        "username": admin["username"],
        "login_time": admin["login_time"],
        "last_active": admin["last_active"],
    }


@app.get("/api/admin/domains")
def admin_domains(authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, domain, created_at FROM allowed_domains ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    return {
        "success": True,
        "domains": [
            {
                "id": row["id"],
                "domain": row["domain"],
                "created_at": row["created_at"],
            }
            for row in rows
        ],
    }


@app.post("/api/admin/domain")
def admin_add_domain(
    data: AdminDomainRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_admin(authorization)

    domain = normalize_domain(data.domain)

    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="请输入正确的邮箱后缀，例如 edu.zitw.de")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO allowed_domains(domain, created_at) VALUES(?, ?)",
        (domain, now_ts()),
    )
    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": "添加成功",
        "domain": domain,
    }


@app.delete("/api/admin/domain/{domain}")
def admin_delete_domain(
    domain: str,
    authorization: Optional[str] = Header(default=None),
):
    require_admin(authorization)

    domain = normalize_domain(domain)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM allowed_domains WHERE domain = ?", (domain,))
    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": "删除成功",
        "domain": domain,
    }


@app.get("/api/admin/stats")
def admin_stats(authorization: Optional[str] = Header(default=None)):
    require_admin(authorization)
    clean_expired_sessions()

    users = []

    for token, sess in SESSIONS.items():
        users.append(
            {
                "email": sess["email"],
                "login_time": sess["login_time"],
                "last_active": sess["last_active"],
                "mail_count": sess.get("mail_count", 0),
            }
        )

    return {
        "success": True,
        "online_count": len(users),
        "users": users,
        "pop": {
            "host": POP_HOST,
            "port": POP_PORT,
            "ssl": POP_SSL,
        },
    }

# === ADMIN PANEL API END ===
