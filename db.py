import hashlib
import os
import sqlite3
from config import ALLOWED_USERS

DB_FILE = os.getenv("DB_FILE", "debts.db")

# Соль, чтобы хэши этой базы нельзя было подменить хэшами из другой копии.
# Менять после создания базы нельзя — цепочка сломается.
_CHAIN_SALT = "debt-tracker-v1"

GENESIS_HASH = hashlib.sha256(_CHAIN_SALT.encode()).hexdigest()


# ─── hash ────────────────────────────────────────────────────────────────────

def _compute_hash(
    tx_id: int,
    payer: str,
    debtor: str,
    amount: float,
    description: str,
    created_at: str,
    prev_hash: str,
) -> str:
    """SHA-256 от всех полей записи + хэш предыдущей записи."""
    raw = "|".join([
        _CHAIN_SALT,
        str(tx_id),
        payer,
        debtor,
        f"{amount:.10f}",
        description,
        created_at,
        prev_hash,
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_last_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT chain_hash FROM transactions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else GENESIS_HASH


# ─── init ────────────────────────────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                payer       TEXT    NOT NULL,
                debtor      TEXT    NOT NULL,
                amount      REAL    NOT NULL,
                description TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                chain_hash  TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nicknames (
                username    TEXT PRIMARY KEY,
                nickname    TEXT NOT NULL
            )
        """)
        conn.commit()


# ─── nicknames ───────────────────────────────────────────────────────────────

def set_nick(username: str, nickname: str):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT INTO nicknames (username, nickname) VALUES (?, ?)"
            " ON CONFLICT(username) DO UPDATE SET nickname = excluded.nickname",
            (username.lower(), nickname),
        )
        conn.commit()


def clear_nick(username: str):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM nicknames WHERE username = ?", (username.lower(),))
        conn.commit()


def get_nicks() -> dict[str, str]:
    """Возвращает {username: nickname} для всех у кого задан псевдоним."""
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT username, nickname FROM nicknames").fetchall()
    return {u: n for u, n in rows}


# ─── write ───────────────────────────────────────────────────────────────────

def add_transaction(payer: str, debtor: str, amount: float, description: str = "") -> int:
    with sqlite3.connect(DB_FILE) as conn:
        created_at = conn.execute("SELECT datetime('now', 'localtime')").fetchone()[0]
        prev_hash  = _get_last_hash(conn)

        # Вставляем без chain_hash, чтобы получить реальный id
        cur = conn.execute(
            "INSERT INTO transactions (payer, debtor, amount, description, created_at, chain_hash)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (payer, debtor, amount, description, created_at, ""),
        )
        tx_id = cur.lastrowid

        chain_hash = _compute_hash(tx_id, payer, debtor, amount, description, created_at, prev_hash)
        conn.execute(
            "UPDATE transactions SET chain_hash = ? WHERE id = ?",
            (chain_hash, tx_id),
        )
        conn.commit()
    return tx_id


def delete_transaction(tx_id: int) -> bool:
    """
    Физически удаляет запись. После удаления цепочка ломается —
    это ожидаемо: /verify покажет нарушение, если удалить не последнюю запись.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        conn.commit()
        return cur.rowcount > 0


# ─── verify ──────────────────────────────────────────────────────────────────

class ChainError:
    def __init__(self, tx_id: int, reason: str):
        self.tx_id  = tx_id
        self.reason = reason

    def __str__(self):
        return f"#{self.tx_id}: {self.reason}"


def verify_chain() -> list[ChainError]:
    """
    Проверяет целостность хэш-цепочки.
    Возвращает список ошибок (пустой = всё чисто).
    """
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            "SELECT id, payer, debtor, amount, description, created_at, chain_hash"
            " FROM transactions ORDER BY id ASC"
        ).fetchall()

    errors: list[ChainError] = []
    prev_hash = GENESIS_HASH

    for tx_id, payer, debtor, amount, desc, created_at, stored_hash in rows:
        expected = _compute_hash(tx_id, payer, debtor, amount, desc, created_at, prev_hash)
        if expected != stored_hash:
            errors.append(ChainError(tx_id, "хэш не совпадает — данные изменены"))
        prev_hash = stored_hash  # двигаемся по цепочке как есть, чтобы найти все сломанные

    return errors


# ─── read ────────────────────────────────────────────────────────────────────

def get_net_balance() -> tuple[str, str, float]:
    """Возвращает (должник, кредитор, сумма). Сумма 0 — никто никому не должен."""
    users = list(ALLOWED_USERS)
    user_a, user_b = users[0], users[1]

    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT payer, debtor, amount FROM transactions").fetchall()

    net = 0.0
    for payer, debtor, amount in rows:
        if payer == user_b and debtor == user_a:
            net += amount
        elif payer == user_a and debtor == user_b:
            net -= amount

    if net > 0:
        return user_a, user_b, round(net, 2)
    elif net < 0:
        return user_b, user_a, round(-net, 2)
    else:
        return user_a, user_b, 0.0


def get_history(limit: int = 15):
    with sqlite3.connect(DB_FILE) as conn:
        return conn.execute(
            """SELECT id, payer, debtor, amount, description, created_at, chain_hash
               FROM transactions
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()


def settle_all() -> float:
    """Добавляет компенсирующую запись, обнуляющую баланс. Возвращает сумму."""
    debtor, creditor, amount = get_net_balance()
    if amount == 0:
        return 0.0
    add_transaction(creditor, debtor, amount, "✅ Взаиморасчёт")
    return amount
