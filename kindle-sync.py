#!/usr/bin/env python3
"""Invia a Send to Kindle i nuovi documenti presenti in una cartella.

La configurazione e' letta da ~/.config/kindle-sync.conf.  Lo stato dei file
gia' inviati e' conservato in ~/.local/share/kindle-sync/state.sqlite3.
"""

from __future__ import annotations

import configparser
import argparse
import hashlib
import logging
import mimetypes
import os
import smtplib
import ssl
import fcntl
import sqlite3
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = SERVICE_DIR / "data" if (SERVICE_DIR / "data").is_dir() else SERVICE_DIR
CONFIG = Path(os.environ.get("KINDLE_SYNC_CONFIG", DEFAULT_DATA / "kindle-sync.conf"))
STATE_DIR = Path(os.environ.get("KINDLE_SYNC_STATE_DIR", DEFAULT_DATA))
ALLOWED_SUFFIXES = {".epub", ".pdf", ".doc", ".docx", ".txt", ".rtf", ".htm", ".html"}
# Il limite esatto dipende dal provider SMTP; 45 MB lascia spazio al MIME.
MAX_ATTACHMENT_BYTES = 45 * 1024 * 1024


def load_config() -> configparser.SectionProxy:
    parser = configparser.ConfigParser(interpolation=None)
    if not CONFIG.exists():
        raise RuntimeError(f"Configurazione mancante: {CONFIG}")
    parser.read(CONFIG)
    if "kindle" not in parser:
        raise RuntimeError("Sezione [kindle] mancante nella configurazione")
    section = parser["kindle"]
    required = ("source_dir", "kindle_address", "smtp_host", "smtp_port", "smtp_user", "smtp_password")
    missing = [key for key in required if not section.get(key, "").strip()]
    if missing:
        raise RuntimeError("Valori mancanti: " + ", ".join(missing))
    return section


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE_DIR / "state.sqlite3")
    db.execute("CREATE TABLE IF NOT EXISTS sent (sha256 TEXT PRIMARY KEY, path TEXT NOT NULL, sent_at TEXT NOT NULL)")
    return db


def already_sent(db: sqlite3.Connection, digest: str) -> bool:
    return db.execute("SELECT 1 FROM sent WHERE sha256 = ?", (digest,)).fetchone() is not None


def send_file(path: Path, cfg: configparser.SectionProxy) -> None:
    message = EmailMessage()
    message["From"] = cfg["smtp_user"]
    message["To"] = cfg["kindle_address"]
    message["Subject"] = ""
    message.set_content("Documento personale inviato automaticamente.")
    content_type, encoding = mimetypes.guess_type(path.name)
    maintype, subtype = (content_type or "application/octet-stream").split("/", 1)
    with path.open("rb") as handle:
        message.add_attachment(handle.read(), maintype=maintype, subtype=subtype, filename=path.name)

    host, port = cfg["smtp_host"], cfg.getint("smtp_port")
    use_ssl = cfg.getboolean("smtp_ssl", fallback=True)
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=60, context=ssl.create_default_context()) as server:
            server.login(cfg["smtp_user"], cfg["smtp_password"])
            refused = server.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
    else:
        with smtplib.SMTP(host, port, timeout=60) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(cfg["smtp_user"], cfg["smtp_password"])
            refused = server.send_message(message)
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)


def main() -> int:
    parser = argparse.ArgumentParser(description="Invia nuovi documenti a Send to Kindle")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--file", action="append", type=Path, dest="files", help="invia soltanto questo file (ripetibile)")
    action.add_argument("--force-file", action="append", type=Path, dest="force_files", help="reinvia questo file anche se gia' registrato")
    action.add_argument("--mark-existing", action="store_true", help="registra i file attuali senza inviarli")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.WARNING if arguments.mark_existing else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        send_lock = (STATE_DIR / "send.lock").open("a")
        fcntl.flock(send_lock, fcntl.LOCK_EX)
        cfg = load_config()
        source = Path(cfg["source_dir"]).expanduser()
        if not source.is_dir():
            raise RuntimeError(f"Cartella sorgente non trovata: {source}")
        db = database()
        force_send = bool(arguments.force_files)
        requested_paths = arguments.force_files or arguments.files
        paths = [path.expanduser() for path in requested_paths] if requested_paths else sorted(source.rglob("*"))
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            size = path.stat().st_size
            if size > MAX_ATTACHMENT_BYTES:
                logging.warning("Saltato (oltre 45 MB): %s", path)
                continue
            digest = sha256(path)
            if already_sent(db, digest) and not force_send:
                continue
            if arguments.mark_existing:
                db.execute("INSERT INTO sent VALUES (?, ?, ?)", (digest, str(path), datetime.now(timezone.utc).isoformat()))
                db.commit()
                logging.info("Registrato senza invio: %s", path.name)
                continue
            logging.info("Invio: %s", path)
            send_file(path, cfg)
            db.execute("INSERT OR REPLACE INTO sent VALUES (?, ?, ?)", (digest, str(path), datetime.now(timezone.utc).isoformat()))
            db.commit()
            logging.info("Inviato: %s", path.name)
        return 0
    except Exception as error:
        logging.error("Sincronizzazione non completata (%s). Controlla configurazione e rete.", type(error).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
