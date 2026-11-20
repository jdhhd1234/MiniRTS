"""SQLite accounts, bearer sessions, and atomic account mutations."""

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from hashlib import pbkdf2_hmac, sha256
from hmac import compare_digest
import json
from pathlib import Path
import secrets
import sqlite3

from . import gacha
from .game import data

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "minrts.db"
PASSWORD_ITERATIONS = 600_000


class AccountError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Session:
    """로그인 토큰으로 확인한 계정: DB id와 API용 사용자 정보."""
    user_id: int
    user: dict


def _password_hash(password, salt):
    return pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)


def _token_hash(token):
    return sha256(token.encode()).digest()


def _user(row):
    return {
        "username": row["username"], "points": row["points"], "max_stage": row["max_stage"],
        "unlocks": json.loads(row["unlocks"]), "templates": json.loads(row["templates"]),
    }


class Database:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL").fetchone()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    points INTEGER NOT NULL CHECK(points >= 0),
                    max_stage INTEGER NOT NULL,
                    unlocks TEXT NOT NULL,
                    templates TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash BLOB PRIMARY KEY NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id)
                );
            """)

    @contextmanager
    def connection(self, write=False):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                if write:
                    conn.execute("BEGIN IMMEDIATE")
                yield conn

    @staticmethod
    def _session(conn, user_id):
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO sessions VALUES (?, ?)", (_token_hash(token), user_id))
        return token

    @staticmethod
    def _by_id(conn, user_id):
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def register(self, username, password):
        username, password = self._credentials(username, password)
        salt = secrets.token_bytes(16)
        digest = _password_hash(password, salt)
        unlocks = dict.fromkeys(data.BASE_UNLOCKED, 1)
        with self.connection(write=True) as conn:
            cursor = conn.execute("""
                INSERT INTO users(username, salt, password_hash, points, max_stage, unlocks, templates)
                VALUES (?, ?, ?, 300, 1, ?, ?) ON CONFLICT(username) DO NOTHING
            """, (username, salt, digest, json.dumps(unlocks), json.dumps(data.DEFAULT_TEMPLATES)))
            if not cursor.rowcount:
                raise AccountError("이미 사용 중인 이름입니다.", 409)
            token = self._session(conn, cursor.lastrowid)
            return {"token": token, "user": _user(self._by_id(conn, cursor.lastrowid))}

    def login(self, username, password):
        username, password = self._credentials(username, password)
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        salt = row["salt"] if row else bytes(16)
        digest = _password_hash(password, salt)
        if row is None or not compare_digest(digest, row["password_hash"]):
            raise AccountError("이름 또는 비밀번호가 올바르지 않습니다.", 401)
        with self.connection(write=True) as conn:
            return {"token": self._session(conn, row["id"]), "user": _user(row)}

    @staticmethod
    def _credentials(username, password):
        if not isinstance(username, str) or not username.strip():
            raise AccountError("사용자 이름을 입력해 주세요.")
        if not isinstance(password, str) or not password:
            raise AccountError("비밀번호를 입력해 주세요.")
        if len(username) > 128 or len(password) > 1024:
            raise AccountError("사용자 이름 또는 비밀번호가 너무 깁니다.")
        try:
            username.encode()
            password.encode()
        except UnicodeError as exc:
            raise AccountError("사용자 이름 또는 비밀번호에 사용할 수 없는 문자가 있습니다.") from exc
        return username.strip(), password

    def authenticate(self, token):
        if not isinstance(token, str) or not token.isascii() or not 1 <= len(token) <= 128:
            raise AccountError("로그인이 필요합니다.", 401)
        with self.connection() as conn:
            row = conn.execute("""
                SELECT users.* FROM users JOIN sessions ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
            """, (_token_hash(token),)).fetchone()
            if row is None:
                raise AccountError("유효하지 않은 로그인입니다.", 401)
            return Session(row["id"], _user(row))

    def save_templates(self, user_id, templates):
        if not isinstance(templates, list) or len(templates) > data.MAX_TEMPLATES:
            raise AccountError(f"편제는 최대 {data.MAX_TEMPLATES}개까지 저장할 수 있습니다.")
        with self.connection(write=True) as conn:
            user = _user(self._by_id(conn, user_id))
            clean = []

            for template in templates:
                if not isinstance(template, dict):
                    raise AccountError("편제 형식이 올바르지 않습니다.")

                name, battalions = template.get("name"), template.get("battalions")

                if not isinstance(name, str) or not name.strip():
                    raise AccountError("편제 이름을 입력해 주세요.")

                try:
                    name.encode()
                except UnicodeError as exc:
                    raise AccountError("편제 이름에 사용할 수 없는 문자가 있습니다.") from exc

                if isinstance(battalions, list):
                    for key in battalions:
                        if not isinstance(key, str):
                            raise AccountError("대대 식별자가 올바르지 않습니다.")

                error = data.validate_template(battalions, user["unlocks"])

                if error:
                    raise AccountError(error)

                clean.append({"name": name.strip(), "battalions": battalions})

            conn.execute("UPDATE users SET templates = ? WHERE id = ?", (json.dumps(clean), user_id))
            return _user(self._by_id(conn, user_id))

    def pull(self, user_id):
        with self.connection(write=True) as conn:
            user = _user(self._by_id(conn, user_id))
            if user["points"] < data.GACHA_COST:
                raise AccountError("가챠 포인트가 부족합니다.")
            try:
                result = gacha.pull(user["unlocks"])
            except ValueError as exc:
                raise AccountError(str(exc)) from exc
            user["unlocks"][result.battalion] = result.level
            conn.execute("UPDATE users SET points = points - ?, unlocks = ? WHERE id = ?",
                         (data.GACHA_COST, json.dumps(user["unlocks"]), user_id))
            return {"result": asdict(result), "user": _user(self._by_id(conn, user_id))}

    def add_points(self, user_id, points):
        with self.connection(write=True) as conn:
            conn.execute("UPDATE users SET points = points + ? WHERE id = ?", (points, user_id))

    def finish_match(self, user_id, stage, winner):
        with self.connection(write=winner == 1) as conn:
            if winner == 1:
                conn.execute("UPDATE users SET max_stage = MAX(max_stage, ?) WHERE id = ?",
                             (min(stage + 1, max(data.STAGES)), user_id))
            return _user(self._by_id(conn, user_id))
