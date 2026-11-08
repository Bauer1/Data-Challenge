#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Freedom Broker — Conversation Quality Analytics (CQA) Backend Server.
Zero-dependency Python HTTP & REST API server (uses standard library + sqlite3 + urllib/requests).
Ready for standalone launch or embedding as a Microsoft Dynamics 365 Web Resource backend adapter.
"""

from __future__ import annotations
import base64
import copy
import datetime
import json
import mimetypes
import os
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from asr_pocketsphinx import (
    build_synthetic_pcm16_from_transcript,
    ensure_real_audio_file,
    get_pocketsphinx_status,
    normalize_bilingual_transcript,
    process_conversation_with_pocketsphinx,
)
from evaluator import (
    DEFAULT_CHECKLIST,
    PRESET_CRITERIA_TEMPLATES,
    calculate_checklist_score,
    compute_time_delta_and_var_metrics,
    enrich_transcript_with_offsets,
    evaluate_conversation_analytical,
    evaluate_conversation_llm,
    extract_api_key,
    extract_model_from_snippet,
    is_llm_active,
    LLM_PROVIDERS,
    mask_api_key,
    normalize_llm_config,
    test_llm_connection,
)
from concurrent.futures import ThreadPoolExecutor
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIO_CACHE_DIR = os.path.join(DATA_DIR, "audio_cache")
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
DB_PATH = os.path.join(DATA_DIR, "cqa.db")
DEV_DATASET_FALLBACK = os.path.join(BASE_DIR, "dev_dataset.json")
FBROKER_DATASET_FALLBACK = os.path.join(BASE_DIR, "fbroker_dataset.json")
SWAP_DATASET_FALLBACK = os.path.join(BASE_DIR, "swap_dataset.json")

DEFAULT_ORGANIZER_API_URL = (
    "https://ec88f21f1accecf38e20095a9fe175.a5.environment.api.powerplatform.com/"
    "powerautomate/automations/direct/cu/11/workflows/b3d2a68006a24662be2b721454cb4e00/"
    "triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0"
    "&sig=5Sog2MThgCRTlk3HObv70LRmRk6u7IOwzlwMpe804qo"
)
ORGANIZER_API_URL = (os.environ.get("ORGANIZER_API_URL", "") or DEFAULT_ORGANIZER_API_URL).strip()

DB_LOCK = threading.Lock()


def get_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with DB_LOCK:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS checklists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                is_active INTEGER DEFAULT 0,
                config_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                dataset_group_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                language TEXT NOT NULL,
                audio_url TEXT,
                transcript_json TEXT NOT NULL,
                initial_eval_json TEXT,
                current_eval_json TEXT,
                checklist_id TEXT,
                appeals_json TEXT DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()

        # Seed default LLM settings if not present
        cur.execute("SELECT value_json FROM settings WHERE key = 'llm_config'")
        row_llm = cur.fetchone()
        if not row_llm:
            env_key = os.environ.get("GEMINI_API_KEY", "")
            default_llm = {
                "provider": "gemini" if env_key else "builtin",
                "model": "gemini-flash-lite-latest",
                "api_key": env_key,
                "base_url": "",
                "cost_per_1m_input_usd": 0.15,
                "cost_per_1m_output_usd": 0.60,
                "usd_to_kzt": 500.0
            }
            cur.execute(
                "INSERT INTO settings (key, value_json) VALUES (?, ?)",
                ("llm_config", json.dumps(default_llm, ensure_ascii=False))
            )
        else:
            try:
                saved_llm = json.loads(row_llm["value_json"])
                if saved_llm.get("model") == "gemini-flash-latest":
                    saved_llm["model"] = "gemini-flash-lite-latest"
                    cur.execute(
                        "UPDATE settings SET value_json = ? WHERE key = 'llm_config'",
                        (json.dumps(saved_llm, ensure_ascii=False),)
                    )
            except Exception:
                pass

        # Seed default checklist if not present
        cur.execute("SELECT COUNT(*) as cnt FROM checklists")
        if cur.fetchone()["cnt"] == 0:
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cur.execute(
                "INSERT INTO checklists (id, name, description, is_active, config_json, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (
                    DEFAULT_CHECKLIST["id"],
                    DEFAULT_CHECKLIST["name"],
                    DEFAULT_CHECKLIST["description"],
                    json.dumps(DEFAULT_CHECKLIST, ensure_ascii=False),
                    now_iso
                )
            )
        conn.commit()

        # Seed DEV conversations if table is empty and dev_dataset.json exists
        cur.execute("SELECT COUNT(*) as cnt FROM conversations")
        if cur.fetchone()["cnt"] == 0 and os.path.exists(DEV_DATASET_FALLBACK):
            try:
                with open(DEV_DATASET_FALLBACK, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                records = payload.get("data", {}).get("records", [])
                group_id = payload.get("data", {}).get("dataset_group_id", "DEV")
                _upsert_conversations_internal(conn, records, group_id, DEFAULT_CHECKLIST, {"provider": "builtin"})
                _seed_demo_appeals(conn, DEFAULT_CHECKLIST)
            except Exception as exc:
                print(f"[WARN] Could not seed dev_dataset.json: {exc}")
        else:
            # Ensure existing voice call transcripts in DB have bilingual KZ/RU code-switching normalization applied
            cur.execute("SELECT conversation_id, transcript_json FROM conversations")
            for row in cur.fetchall():
                try:
                    raw_tr = json.loads(row["transcript_json"])
                    norm_tr, corrs = normalize_bilingual_transcript(raw_tr)
                    if corrs:
                        cur.execute(
                            "UPDATE conversations SET transcript_json = ? WHERE conversation_id = ?",
                            (json.dumps(norm_tr, ensure_ascii=False), row["conversation_id"]),
                        )
                except Exception:
                    pass
            conn.commit()

        # Ensure FBroker (PROD 10 chats) and Swap (3 chats) datasets are seeded in intermediate SQLite DB
        for fb_path, default_grp in ((FBROKER_DATASET_FALLBACK, "FBroker"), (SWAP_DATASET_FALLBACK, "Swap")):
            if os.path.exists(fb_path):
                try:
                    with open(fb_path, "r", encoding="utf-8") as f:
                        fb_payload = json.load(f)
                    fb_records = fb_payload.get("data", {}).get("records", [])
                    fb_group = fb_payload.get("data", {}).get("dataset_group_id", default_grp)
                    if fb_records:
                        _upsert_conversations_internal(conn, fb_records, fb_group, DEFAULT_CHECKLIST, {"provider": "builtin"})
                except Exception as exc:
                    print(f"[WARN] Could not seed {fb_path}: {exc}")

        conn.close()


def _upsert_conversations_internal(
    conn: sqlite3.Connection,
    records: List[Dict[str, Any]],
    group_id: str,
    checklist: Dict[str, Any],
    llm_config: Dict[str, Any]
) -> int:
    cur = conn.cursor()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    count = 0
    for rec in records:
        cid = rec.get("conversation_id")
        if not cid:
            continue
        channel = rec.get("channel", "agent chat")
        language = rec.get("language", "RU")
        audio_url = rec.get("audio_url")
        transcript = rec.get("transcript", [])

        # Automatically repair bilingual KZ/RU code-switching ASR misrecognitions on ingest
        if transcript:
            transcript, _ = normalize_bilingual_transcript(transcript)
            rec = dict(rec)
            rec["transcript"] = transcript

        # Auto-run CMU PocketSphinx ASR if voice call has audio_url/audio_base64 and no transcript (or explicit flag)
        if (not transcript or rec.get("use_pocketsphinx")) and (audio_url or rec.get("audio_base64") or channel == "voice call"):
            try:
                custom_bytes = base64.b64decode(rec["audio_base64"]) if rec.get("audio_base64") else None
                sphinx_mode = "pure_sphinx" if not transcript else "enhance"
                sphinx_out = process_conversation_with_pocketsphinx(
                    rec, AUDIO_CACHE_DIR, mode=sphinx_mode, custom_audio_bytes=custom_bytes
                )
                if sphinx_out.get("transcript"):
                    transcript = sphinx_out["transcript"]
                    rec = dict(rec)
                    rec["transcript"] = transcript
                    if not audio_url and custom_bytes:
                        audio_url = f"/api/audio/{urllib.parse.quote(cid)}"
            except Exception as sphinx_exc:
                print(f"[WARN] CMU PocketSphinx auto-ASR failed for {cid}: {sphinx_exc}")

        eval_result = evaluate_conversation_llm(rec, checklist, llm_config)
        eval_json = json.dumps(eval_result, ensure_ascii=False)

        cur.execute("SELECT appeals_json FROM conversations WHERE conversation_id = ?", (cid,))
        existing = cur.fetchone()
        appeals_json = existing["appeals_json"] if existing else "[]"

        cur.execute("""
            INSERT INTO conversations (
                conversation_id, dataset_group_id, channel, language, audio_url,
                transcript_json, initial_eval_json, current_eval_json, checklist_id, appeals_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
                dataset_group_id = excluded.dataset_group_id,
                channel = excluded.channel,
                language = excluded.language,
                audio_url = excluded.audio_url,
                transcript_json = excluded.transcript_json,
                initial_eval_json = excluded.initial_eval_json,
                current_eval_json = excluded.current_eval_json,
                checklist_id = excluded.checklist_id,
                updated_at = excluded.updated_at
        """, (
            cid, group_id, channel, language, audio_url,
            json.dumps(transcript, ensure_ascii=False),
            eval_json, eval_json, checklist["id"], appeals_json, now_iso
        ))
        count += 1
    conn.commit()
    return count


def _seed_demo_appeals(conn: sqlite3.Connection, checklist: Dict[str, Any]) -> None:
    """
    Seeds two realistic appeals so the reviewer queue in Контур 2 is immediately demonstrable:
    1. p1-ru-001 (criterion c07 'Завершение') — Status: 'pending' (На рассмотрении проверяющего).
    2. p1-ru-015 (criterion c04 'Корректная и полная консультация') — Status: 'resolved_changed' (Пересмотрено).
    """
    cur = conn.cursor()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # 1. Pending appeal on p1-ru-001 (Criterion c07 Завершение)
    cur.execute("SELECT * FROM conversations WHERE conversation_id = 'p1-ru-001'")
    row1 = cur.fetchone()
    if row1:
        curr_eval = json.loads(row1["current_eval_json"])
        appeal_obj = {
            "id": "app-demo-001",
            "conversation_id": "p1-ru-001",
            "criterion_id": "c07",
            "criterion_code": "07",
            "criterion_name": "Завершение",
            "original_answer": "НЕТ",
            "original_score": curr_eval["final_score"],
            "operator_comment": "Клиент в реплике [10] уже подтвердил, что нашёл тариф («Нашла, спасибо»). В реплике [11] я вежливо ответила «Пожалуйста. Хорошего вечера!». Прошу зачесть критерий завершения.",
            "author": "Алина (Оператор чата)",
            "status": "pending",
            "created_at": now_iso,
            "decision": None,
            "new_answer": None,
            "reviewer_comment": "",
            "reviewer_name": "",
            "decided_at": None,
            "final_score_after": None
        }
        for item in curr_eval["criteria_results"]:
            if item["criterion_id"] == "c07":
                item["appeal"] = appeal_obj
                break
        cur.execute(
            "UPDATE conversations SET current_eval_json = ?, appeals_json = ? WHERE conversation_id = 'p1-ru-001'",
            (json.dumps(curr_eval, ensure_ascii=False), json.dumps([appeal_obj], ensure_ascii=False))
        )

    # 2. Resolved appeal on p1-ru-015 (Criterion c04 Корректная и полная консультация: 15 -> 20)
    cur.execute("SELECT * FROM conversations WHERE conversation_id = 'p1-ru-015'")
    row2 = cur.fetchone()
    if row2:
        init_eval = json.loads(row2["initial_eval_json"])
        curr_eval = json.loads(row2["current_eval_json"])
        appeal_obj2 = {
            "id": "app-demo-002",
            "conversation_id": "p1-ru-015",
            "criterion_id": "c04",
            "criterion_code": "04",
            "criterion_name": "Корректная и полная консультация",
            "original_answer": 15,
            "original_score": init_eval["final_score"],
            "operator_comment": "В реплике [9] я прямо объяснила, что старый логин остаётся в системе и после закрытия счёта, а в [11] подтвердила, что для регистрации сейчас нужно выбрать другой логин.",
            "author": "Дина (Оператор чата)",
            "status": "changed",
            "created_at": now_iso,
            "decision": "change",
            "new_answer": 20,
            "reviewer_comment": "Апелляция удовлетворена: в репликах [9] и [11] оператор дала однозначный ответ, что старый логин не освобождается и нужно выбрать новый.",
            "reviewer_name": "Рустам К. (Старший QA-аудитор)",
            "decided_at": now_iso,
            "final_score_after": 100.0
        }
        # Recalculate with c04 = 20
        updated_inputs = []
        for item in curr_eval["criteria_results"]:
            c_copy = dict(item)
            if c_copy["criterion_id"] == "c04":
                c_copy["initial_evaluation"] = {
                    "answer": item["answer"],
                    "raw_points": item["raw_points"],
                    "redistributed_points": item["redistributed_points"],
                    "reasoning": item["reasoning"]
                }
                c_copy["answer"] = 20
                c_copy["appeal"] = appeal_obj2
            updated_inputs.append(c_copy)
        recalculated = calculate_checklist_score(checklist, updated_inputs)
        recalculated["meta"] = curr_eval.get("meta", {})
        appeal_obj2["final_score_after"] = recalculated["final_score"]
        cur.execute(
            "UPDATE conversations SET current_eval_json = ?, appeals_json = ? WHERE conversation_id = 'p1-ru-015'",
            (json.dumps(recalculated, ensure_ascii=False), json.dumps([appeal_obj2], ensure_ascii=False))
        )

    conn.commit()


def get_active_checklist(conn: sqlite3.Connection, checklist_id: Optional[str] = None) -> Dict[str, Any]:
    cur = conn.cursor()
    if checklist_id:
        cur.execute("SELECT config_json FROM checklists WHERE id = ?", (checklist_id,))
        row = cur.fetchone()
        if row:
            return json.loads(row["config_json"])
    cur.execute("SELECT config_json FROM checklists WHERE is_active = 1 LIMIT 1")
    row = cur.fetchone()
    if row:
        return json.loads(row["config_json"])
    return copy.deepcopy(DEFAULT_CHECKLIST)


def get_llm_config(conn: sqlite3.Connection) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT value_json FROM settings WHERE key = 'llm_config'")
    row = cur.fetchone()
    if row:
        try:
            return normalize_llm_config(json.loads(row["value_json"]))
        except Exception:
            pass
    return normalize_llm_config({"provider": "builtin", "model": "gemini-flash-latest", "api_key": ""})


def public_llm_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """LLM config safe to send to the browser: the API key itself is never returned, only a mask."""
    cfg = normalize_llm_config(cfg)
    env_key = ""
    if cfg["provider"] == "gemini":
        env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    elif cfg["provider"] == "openai":
        env_key = os.environ.get("OPENAI_API_KEY") or ""
    out = {k: v for k, v in cfg.items() if k != "api_key"}
    out["has_api_key"] = bool(cfg["api_key"])
    out["api_key_masked"] = mask_api_key(cfg["api_key"])
    out["api_key_from_env"] = bool(env_key) and not cfg["api_key"]
    out["llm_active"] = is_llm_active(cfg)
    out["providers"] = LLM_PROVIDERS
    return out


# =============================================================================
# Background batch LLM evaluation jobs (LLM calls take seconds each, so batches
# run outside the HTTP request and the UI polls progress via /api/jobs/current)
# =============================================================================
JOBS_LOCK = threading.Lock()
CURRENT_JOB: Dict[str, Any] = {}
LLM_CONCURRENCY = max(1, int(os.environ.get("LLM_CONCURRENCY", "2")))


def get_current_job_snapshot() -> Optional[Dict[str, Any]]:
    with JOBS_LOCK:
        if not CURRENT_JOB:
            return None
        snap = {k: v for k, v in CURRENT_JOB.items() if not k.startswith("_")}
        snap["errors"] = list(CURRENT_JOB.get("errors", []))[-5:]
        return snap


def start_batch_llm_job(reason: str, reset_appeals: bool, conversation_ids: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """
    Starts (or restarts) a background job that re-evaluates conversations with the configured LLM.
    Returns the job snapshot, or None if the LLM is not active.
    """
    with DB_LOCK:
        conn = get_db()
        llm_cfg = get_llm_config(conn)
        checklist = get_active_checklist(conn)
        cur = conn.cursor()
        if conversation_ids:
            marks = ",".join("?" for _ in conversation_ids)
            cur.execute(f"SELECT conversation_id FROM conversations WHERE conversation_id IN ({marks}) ORDER BY conversation_id", conversation_ids)
        else:
            cur.execute("SELECT conversation_id FROM conversations ORDER BY conversation_id")
        ids = [r["conversation_id"] for r in cur.fetchall()]
        conn.close()

    if not is_llm_active(llm_cfg) or not ids:
        return None

    with JOBS_LOCK:
        if CURRENT_JOB.get("status") == "running":
            CURRENT_JOB["_cancel"].set()
        cancel_evt = threading.Event()
        CURRENT_JOB.clear()
        CURRENT_JOB.update({
            "id": uuid.uuid4().hex[:10],
            "status": "running",
            "reason": reason,
            "provider": llm_cfg["provider"],
            "model": llm_cfg["model"],
            "total": len(ids),
            "done": 0,
            "llm_ok": 0,
            "fallback": 0,
            "errors": [],
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "finished_at": None,
            "_cancel": cancel_evt,
        })
        job_id = CURRENT_JOB["id"]

    def process_one(cid: str) -> None:
        if cancel_evt.is_set():
            return
        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT conversation_id, channel, language, audio_url, transcript_json FROM conversations WHERE conversation_id = ?", (cid,))
            row = cur.fetchone()
            conn.close()
        if not row:
            return
        rec = {
            "conversation_id": row["conversation_id"],
            "channel": row["channel"],
            "language": row["language"],
            "audio_url": row["audio_url"],
            "transcript": json.loads(row["transcript_json"]),
        }
        ev = evaluate_conversation_llm(rec, checklist, llm_cfg)
        if cancel_evt.is_set():
            return
        ev_json = json.dumps(ev, ensure_ascii=False)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with DB_LOCK:
            conn = get_db()
            conn.execute(
                f"""UPDATE conversations
                    SET initial_eval_json = ?, current_eval_json = ?, checklist_id = ?, updated_at = ?
                    {", appeals_json = '[]'" if reset_appeals else ""}
                    WHERE conversation_id = ?""",
                (ev_json, ev_json, checklist["id"], now_iso, cid),
            )
            conn.commit()
            conn.close()
        meta = ev.get("meta", {})
        with JOBS_LOCK:
            if CURRENT_JOB.get("id") != job_id:
                return
            CURRENT_JOB["done"] += 1
            if meta.get("llm_used"):
                CURRENT_JOB["llm_ok"] += 1
                if meta.get("model_name"):
                    CURRENT_JOB["model"] = meta["model_name"]
            else:
                CURRENT_JOB["fallback"] += 1
                err = meta.get("llm_error") or meta.get("llm_fallback_note") or "неизвестная ошибка"
                CURRENT_JOB["errors"].append(f"{cid}: {err}")
                print(f"[LLM] {cid}: fallback — {err}")

    def runner() -> None:
        with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as pool:
            list(pool.map(process_one, ids))
        with JOBS_LOCK:
            if CURRENT_JOB.get("id") == job_id:
                CURRENT_JOB["status"] = "cancelled" if cancel_evt.is_set() else "finished"
                CURRENT_JOB["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                print(f"[LLM] Job {job_id} {CURRENT_JOB['status']}: {CURRENT_JOB['llm_ok']} LLM / {CURRENT_JOB['fallback']} fallback из {CURRENT_JOB['total']}")

    threading.Thread(target=runner, name=f"llm-job-{job_id}", daemon=True).start()
    return get_current_job_snapshot()


class CQARequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Concise server log
        pass

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Range")
        self.send_header("Access-Control-Expose-Headers", "X-Audio-Source, Accept-Ranges, Content-Range")

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if path == "/api/state":
                self._handle_get_state()
            elif path == "/api/conversations":
                self._handle_list_conversations(query)
            elif path.startswith("/api/conversations/"):
                cid = urllib.parse.unquote(path.split("/api/conversations/", 1)[1])
                self._handle_get_conversation(cid)
            elif path == "/api/checklists":
                self._handle_list_checklists()
            elif path == "/api/appeals":
                self._handle_list_appeals()
            elif path == "/api/settings/llm":
                with DB_LOCK:
                    conn = get_db()
                    cfg = get_llm_config(conn)
                    conn.close()
                # The raw API key is never sent back to the browser
                self._send_json({"isSuccess": True, "llm_config": public_llm_config(cfg)})
            elif path == "/api/jobs/current":
                self._send_json({"isSuccess": True, "job": get_current_job_snapshot()})
            elif path == "/api/export/hackathon":
                self._handle_export_hackathon(query)
            elif path == "/api/asr/status":
                self._send_json({"isSuccess": True, "asr_status": get_pocketsphinx_status()})
            elif path.startswith("/api/audio/"):
                cid = urllib.parse.unquote(path.split("/api/audio/", 1)[1])
                self._handle_audio_proxy(cid)
            else:
                self._serve_static(path)
        except Exception as exc:
            self._send_json({"isSuccess": False, "error": str(exc)}, status=500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            body = self._read_json_body()
            if path == "/api/datasets/fetch":
                self._handle_fetch_dataset(body)
            elif path == "/api/conversations/upload":
                self._handle_upload_conversations(body)
            elif path == "/api/checklists":
                self._handle_save_checklist(body)
            elif path == "/api/evaluate":
                self._handle_evaluate(body)
            elif path == "/api/appeals/submit":
                self._handle_submit_appeal(body)
            elif path == "/api/appeals/decide":
                self._handle_decide_appeal(body)
            elif path == "/api/export/hackathon":
                self._handle_export_hackathon(body)
            elif path == "/api/settings/llm":
                self._handle_save_llm_settings(body)
            elif path == "/api/settings/llm/test":
                self._handle_test_llm_settings(body)
            elif path == "/api/jobs/cancel":
                with JOBS_LOCK:
                    if CURRENT_JOB.get("status") == "running":
                        CURRENT_JOB["_cancel"].set()
                self._send_json({"isSuccess": True, "job": get_current_job_snapshot()})
            elif path == "/api/asr/transcribe":
                self._handle_asr_transcribe(body)
            else:
                self._send_json({"isSuccess": False, "error": f"Unknown endpoint {path}"}, status=404)
        except Exception as exc:
            self._send_json({"isSuccess": False, "error": str(exc)}, status=500)

    # -------------------------------------------------------------------------
    # Endpoint Implementations
    # -------------------------------------------------------------------------
    def _handle_get_state(self) -> None:
        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id, name, description, is_active, config_json, updated_at FROM checklists ORDER BY updated_at DESC")
            checklists = []
            active_checklist = None
            for r in cur.fetchall():
                cfg = json.loads(r["config_json"])
                cfg["is_active"] = bool(r["is_active"])
                cfg["updated_at"] = r["updated_at"]
                checklists.append(cfg)
                if r["is_active"]:
                    active_checklist = cfg

            cur.execute("SELECT dataset_group_id, channel, language, current_eval_json, initial_eval_json, appeals_json FROM conversations")
            rows = cur.fetchall()
            llm_cfg = get_llm_config(conn)
            conn.close()

        total_convs = len(rows)
        scores = []
        risk_indices = []
        var_95_vals = []
        bayes_confs = []
        audit_recommended_cnt = 0
        crit_count = 0
        na_dialogues = 0
        pending_appeals = 0
        resolved_appeals = 0
        by_lang: Dict[str, List[float]] = {}
        by_channel: Dict[str, List[float]] = {}
        groups = set()

        for r in rows:
            groups.add(r["dataset_group_id"])
            ev = json.loads(r["current_eval_json"]) if r["current_eval_json"] else {}
            sc = ev.get("final_score")
            if sc is not None:
                scores.append(float(sc))
                by_lang.setdefault(r["language"], []).append(float(sc))
                by_channel.setdefault(r["channel"], []).append(float(sc))
            if ev.get("has_critical_error"):
                crit_count += 1
            if ev.get("na_count", 0) > 0:
                na_dialogues += 1
            alg = ev.get("algorithms", {})
            act = alg.get("actuarial_risk", {})
            if act:
                risk_indices.append(float(act.get("collective_risk_index_norm", 0.0)))
                if act.get("var_95_sec") is not None:
                    var_95_vals.append(float(act.get("var_95_sec", 0.0)))
                bb = act.get("bayesian_beta", {})
                if bb.get("posterior_confidence") is not None:
                    bayes_confs.append(float(bb.get("posterior_confidence", 90.0)))
                if bb.get("recommend_supervisor_audit"):
                    audit_recommended_cnt += 1
            apps = json.loads(r["appeals_json"]) if r["appeals_json"] else []
            for a in apps:
                if a.get("status") == "pending":
                    pending_appeals += 1
                else:
                    resolved_appeals += 1

        avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
        avg_risk = round(sum(risk_indices) / len(risk_indices), 2) if risk_indices else 0.0
        avg_var_95 = round(sum(var_95_vals) / len(var_95_vals), 1) if var_95_vals else 0.0
        avg_bayes = round(sum(bayes_confs) / len(bayes_confs), 2) if bayes_confs else 92.5
        lang_stats = {k: {"count": len(v), "avg_score": round(sum(v) / len(v), 2)} for k, v in by_lang.items()}
        channel_stats = {k: {"count": len(v), "avg_score": round(sum(v) / len(v), 2)} for k, v in by_channel.items()}

        self._send_json({
            "isSuccess": True,
            "active_checklist": active_checklist or DEFAULT_CHECKLIST,
            "checklists": checklists,
            "preset_criteria_templates": PRESET_CRITERIA_TEMPLATES,
            "llm_config": public_llm_config(llm_cfg),
            "llm_job": get_current_job_snapshot(),
            "asr_status": get_pocketsphinx_status(),
            "stats": {
                "total_conversations": total_convs,
                "dataset_groups": sorted(list(groups)),
                "average_score": avg_score,
                "average_risk_index": avg_risk,
                "average_var_95_sec": avg_var_95,
                "average_bayesian_confidence": avg_bayes,
                "supervisor_audit_recommended_count": audit_recommended_cnt,
                "critical_errors_count": crit_count,
                "na_dialogues_count": na_dialogues,
                "pending_appeals_count": pending_appeals,
                "resolved_appeals_count": resolved_appeals,
                "by_language": lang_stats,
                "by_channel": channel_stats
            }
        })

    def _handle_list_conversations(self, query: Dict[str, List[str]]) -> None:
        group_filter = (query.get("group_id", [""])[0] or "").strip()
        lang_filter = (query.get("language", [""])[0] or "").strip().upper()
        channel_filter = (query.get("channel", [""])[0] or "").strip().lower()
        status_filter = (query.get("filter", [""])[0] or "").strip().lower()
        search_q = (query.get("search", [""])[0] or "").strip().lower()

        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT * FROM conversations ORDER BY conversation_id ASC")
            rows = cur.fetchall()
            conn.close()

        items = []
        for r in rows:
            cid = r["conversation_id"]
            grp = r["dataset_group_id"]
            ch = r["channel"]
            lang = r["language"]

            if group_filter:
                gf_up = group_filter.upper()
                grp_up = grp.upper()
                if gf_up in ("PROD", "FBROKER"):
                    if grp_up not in ("PROD", "FBROKER"):
                        continue
                elif grp_up != gf_up:
                    continue
            if lang_filter and lang.upper() != lang_filter:
                continue
            if channel_filter and ch.lower() != channel_filter:
                continue

            transcript = json.loads(r["transcript_json"])
            init_eval = json.loads(r["initial_eval_json"]) if r["initial_eval_json"] else {}
            curr_eval = json.loads(r["current_eval_json"]) if r["current_eval_json"] else {}
            appeals = json.loads(r["appeals_json"]) if r["appeals_json"] else []

            has_crit = bool(curr_eval.get("has_critical_error"))
            na_cnt = int(curr_eval.get("na_count", 0))
            has_pending_appeal = any(a.get("status") == "pending" for a in appeals)
            has_resolved_appeal = any(a.get("status") in ("confirmed", "changed") for a in appeals)
            alg = curr_eval.get("algorithms", {})
            act = alg.get("actuarial_risk", {})
            bb = act.get("bayesian_beta", {})
            rec_audit = bool(bb.get("recommend_supervisor_audit"))

            if status_filter == "critical" and not has_crit:
                continue
            if status_filter == "na" and na_cnt == 0:
                continue
            if status_filter == "appeals" and not appeals:
                continue
            if status_filter == "pending_appeal" and not has_pending_appeal:
                continue
            if status_filter == "audit" and not rec_audit:
                continue
            if status_filter == "low_score" and (curr_eval.get("final_score") is None or curr_eval.get("final_score", 100) >= 75):
                continue

            # Extract first client question preview & operator name
            client_preview = ""
            operator_preview = ""
            for t in transcript:
                if t.get("speaker") == "client" and not client_preview and len(t.get("text", "")) > 3:
                    client_preview = t.get("text", "")
                if t.get("speaker") == "operator" and not operator_preview:
                    operator_preview = t.get("text", "")

            if search_q:
                full_haystack = f"{cid} {lang} {ch} {client_preview} {operator_preview}".lower()
                if search_q not in full_haystack:
                    # Also check all transcript texts
                    if not any(search_q in t.get("text", "").lower() for t in transcript):
                        continue

            items.append({
                "conversation_id": cid,
                "dataset_group_id": grp,
                "channel": ch,
                "language": lang,
                "has_audio": bool(r["audio_url"]),
                "turns_count": len(transcript),
                "client_preview": client_preview[:110],
                "initial_score": init_eval.get("final_score"),
                "final_score": curr_eval.get("final_score"),
                "has_critical_error": has_crit,
                "audit_clearance_status": curr_eval.get("audit_clearance_status", "BLOCKED_CRITICAL_FAIL" if has_crit else "CLEARED"),
                "na_count": na_cnt,
                "na_weight_redistributed": curr_eval.get("na_weight_redistributed", 0),
                "risk_index": act.get("collective_risk_index_norm", 0.0),
                "risk_tier": act.get("risk_tier", "LOW"),
                "var_95_sec": act.get("var_95_sec", 0.0),
                "bayesian_confidence": bb.get("posterior_confidence", 92.0),
                "recommend_supervisor_audit": rec_audit,
                "appeals_count": len(appeals),
                "has_pending_appeal": has_pending_appeal,
                "has_resolved_appeal": has_resolved_appeal,
                "checklist_id": r["checklist_id"],
                "updated_at": r["updated_at"]
            })

        self._send_json({"isSuccess": True, "total": len(items), "records": items})

    def _handle_export_hackathon(self, query_or_body: Dict[str, Any]) -> None:
        group_id = "DEV"
        if isinstance(query_or_body, dict):
            if "group_id" in query_or_body:
                val = query_or_body["group_id"]
                group_id = val[0] if isinstance(val, list) and val else str(val)
        group_id = (group_id or "DEV").strip()

        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            if group_id.upper() in ("ALL", "*", ""):
                cur.execute("SELECT * FROM conversations ORDER BY conversation_id ASC")
            elif group_id.upper() in ("PROD", "FBROKER"):
                cur.execute(
                    "SELECT * FROM conversations WHERE UPPER(dataset_group_id) IN ('PROD', 'FBROKER') ORDER BY conversation_id ASC"
                )
            else:
                cur.execute(
                    "SELECT * FROM conversations WHERE UPPER(dataset_group_id) = UPPER(?) ORDER BY conversation_id ASC",
                    (group_id,)
                )
            rows = cur.fetchall()
            llm_cfg = get_llm_config(conn)
            conn.close()

        provider = llm_cfg.get("provider", "gemini")
        model_name = llm_cfg.get("model", "gemini-flash-lite-latest")
        if provider == "builtin" or not model_name or model_name == "Freedom-CQA-Hybrid":
            mode = "external_api"
            model_name = "gemini-flash-lite-latest"
        else:
            mode = "local" if provider == "ollama" else ("external_api" if provider in ("gemini", "openai") else "hybrid")

        target_criteria = [
            ("contact", ["01", "c01", "contact"], "boolean", False),
            ("discovery", ["02", "c02", "discovery"], "boolean", True),
            ("identification", ["03", "c03", "identification"], "boolean", True),
            ("consultation", ["04", "c04", "consultation"], "scale", True),
            ("empathy", ["05", "c05", "empathy"], "boolean", False),
            ("dialogue", ["06", "c06", "dialogue"], "scale", False),
            ("closure", ["07", "c07", "closure"], "boolean", True),
            ("critical", ["08", "c08", "critical"], "critical", False),
        ]

        results = []
        for r in rows:
            cid = r["conversation_id"]
            ev_raw = r["current_eval_json"] or r["initial_eval_json"]
            if not ev_raw:
                results.append({
                    "conversation_id": cid,
                    "status": "error",
                    "criteria": [],
                    "score": None,
                    "error": "Оценка для данного диалога отсутствует."
                })
                continue

            try:
                ev = json.loads(ev_raw)
            except Exception as e:
                results.append({
                    "conversation_id": cid,
                    "status": "error",
                    "criteria": [],
                    "score": None,
                    "error": f"Ошибка парсинга оценки: {str(e)}"
                })
                continue

            crit_results = ev.get("criteria_results", [])
            by_code = {}
            for cr in crit_results:
                c_code = str(cr.get("code", "")).strip()
                c_id = str(cr.get("criterion_id", "")).strip()
                if c_code:
                    by_code[c_code] = cr
                if c_id:
                    by_code[c_id] = cr

            export_criteria = []
            for target_id, aliases, ctype, allow_na in target_criteria:
                matched = None
                for a in aliases:
                    if a in by_code:
                        matched = by_code[a]
                        break

                if not matched:
                    export_criteria.append({
                        "criterion_id": target_id,
                        "value": False if ctype != "scale" else (None if allow_na else 0),
                        "rationale": "Критерий не найден в результатах оценки.",
                        "evidence_turns": []
                    })
                    continue

                is_na = bool(matched.get("is_na", False))
                ans = matched.get("answer")
                ans_str = str(ans).strip().upper() if ans is not None else ""

                if is_na and allow_na:
                    val = None
                elif ctype == "critical":
                    val = bool(ans_str in ("ДА", "YES", "TRUE", "1"))
                elif ctype == "boolean":
                    val = bool(ans_str in ("ДА", "YES", "TRUE", "1", "ИӘ"))
                elif ctype == "scale":
                    if is_na:
                        val = None
                    else:
                        try:
                            val = int(round(float(matched.get("scale_value", ans) or 0)))
                        except (ValueError, TypeError):
                            val = 0
                else:
                    val = ans

                evidence_turns = []
                for ev_item in matched.get("evidence", []):
                    t_idx = ev_item.get("turn_index", ev_item.get("evidence_line_index"))
                    if t_idx is not None:
                        try:
                            turn_num = int(t_idx) + 1
                            if turn_num > 0 and turn_num not in evidence_turns:
                                evidence_turns.append(turn_num)
                        except (ValueError, TypeError):
                            pass
                evidence_turns.sort()

                rationale = matched.get("reasoning") or matched.get("na_reason") or ""
                if not rationale and is_na:
                    rationale = "Критерий неприменим к данному диалогу."

                export_criteria.append({
                    "criterion_id": target_id,
                    "value": val,
                    "rationale": rationale,
                    "evidence_turns": evidence_turns
                })

            final_score = ev.get("final_score")
            if final_score is not None:
                try:
                    final_score = round(float(final_score), 2)
                except (ValueError, TypeError):
                    final_score = 0.0

            crit_value = False
            for ec in export_criteria:
                if ec["criterion_id"] == "critical":
                    crit_value = bool(ec["value"])
                    break

            results.append({
                "conversation_id": cid,
                "status": "ok",
                "criteria": export_criteria,
                "score": {
                    "final_score": final_score,
                    "critical_flag": crit_value
                }
            })

        out_data = {
            "schema_version": "1.0",
            "team_name": "bandwagon fallacy",
            "model": {
                "mode": mode,
                "name": model_name
            },
            "results": results
        }

        sub_folder = "prod" if group_id.upper() in ("PROD", "FBROKER") else ("swap" if group_id.upper() == "SWAP" else "dev")
        repo_results_dir = os.path.join(BASE_DIR, "results", sub_folder)
        os.makedirs(repo_results_dir, exist_ok=True)
        results_file_path = os.path.join(repo_results_dir, "results.json")
        with open(results_file_path, "w", encoding="utf-8") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2)

        body = json.dumps(out_data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="results.json"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_get_conversation(self, cid: str) -> None:
        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT * FROM conversations WHERE conversation_id = ?", (cid,))
            row = cur.fetchone()
            conn.close()

        if not row:
            self._send_json({"isSuccess": False, "error": f"Диалог {cid} не найден"}, status=404)
            return

        raw_transcript = json.loads(row["transcript_json"])
        norm_transcript, _ = normalize_bilingual_transcript(raw_transcript)
        enriched_transcript = enrich_transcript_with_offsets(norm_transcript)
        init_eval = json.loads(row["initial_eval_json"]) if row["initial_eval_json"] else {}
        curr_eval = json.loads(row["current_eval_json"]) if row["current_eval_json"] else {}
        appeals = json.loads(row["appeals_json"]) if row["appeals_json"] else []

        self._send_json({
            "isSuccess": True,
            "conversation": {
                "conversation_id": row["conversation_id"],
                "dataset_group_id": row["dataset_group_id"],
                "channel": row["channel"],
                "language": row["language"],
                "audio_url": row["audio_url"],
                "proxy_audio_url": f"/api/audio/{urllib.parse.quote(row['conversation_id'])}" if row["audio_url"] else None,
                "transcript": enriched_transcript,
                "initial_evaluation": init_eval,
                "current_evaluation": curr_eval,
                "appeals": appeals,
                "checklist_id": row["checklist_id"],
                "updated_at": row["updated_at"]
            }
        })

    def _handle_fetch_dataset(self, body: Dict[str, Any]) -> None:
        group_id = (body.get("group_id") or "DEV").strip()
        custom_url = (body.get("api_url") or "").strip()

        api_group_param = group_id
        if group_id.upper() in ("PROD", "FBROKER"):
            api_group_param = "FBroker"
        elif group_id.upper() == "SWAP":
            api_group_param = "Swap"
        elif group_id.upper() == "DEV":
            api_group_param = "DEV"

        records: List[Dict[str, Any]] = []
        source_note = ""

        target_url = custom_url
        if not target_url and ORGANIZER_API_URL:
            separator = "&" if "?" in ORGANIZER_API_URL else "?"
            target_url = (
                f"{ORGANIZER_API_URL}{separator}"
                f"group_id={urllib.parse.quote(api_group_param)}"
            )
        try:
            if not target_url:
                raise RuntimeError(
                    "Задайте ORGANIZER_API_URL или передайте api_url для загрузки набора."
                )
            req = urllib.request.Request(target_url, headers={"Accept": "application/json"}, method="GET")
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("isSuccess") and "data" in payload:
                    records = payload["data"].get("records", [])
                    group_id = payload["data"].get("dataset_group_id", group_id)
                    source_note = f"Загружено напрямую из API организаторов (group_id={group_id})"
        except Exception as api_err:
            error_type = type(api_err).__name__
            if group_id.upper() == "DEV" and os.path.exists(DEV_DATASET_FALLBACK):
                with open(DEV_DATASET_FALLBACK, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                records = payload.get("data", {}).get("records", [])
                source_note = f"Загружено из локального кэша DEV (API недоступен: {error_type})"
            elif group_id.upper() in ("PROD", "FBROKER") and os.path.exists(FBROKER_DATASET_FALLBACK):
                with open(FBROKER_DATASET_FALLBACK, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                records = payload.get("data", {}).get("records", [])
                group_id = "FBroker"
                source_note = f"Загружено из локального кэша FBroker (API недоступен: {error_type})"
            elif group_id.upper() == "SWAP" and os.path.exists(SWAP_DATASET_FALLBACK):
                with open(SWAP_DATASET_FALLBACK, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                records = payload.get("data", {}).get("records", [])
                group_id = "Swap"
                source_note = f"Загружено из локального кэша Swap (API недоступен: {error_type})"
            else:
                self._send_json({
                    "isSuccess": False,
                    "error": (
                        f"Не удалось получить данные из API для group_id='{group_id}' "
                        f"(ошибка {error_type}). Проверьте URL и доступность сервиса."
                    )
                }, status=400)
                return

        with DB_LOCK:
            conn = get_db()
            checklist = get_active_checklist(conn, body.get("checklist_id"))
            # Instant analytical evaluation on import; LLM refinement runs in the background
            imported_count = _upsert_conversations_internal(conn, records, group_id, checklist, {"provider": "builtin"})
            conn.close()

        job = start_batch_llm_job(
            f"Оценка импортированного набора {group_id}", reset_appeals=False,
            conversation_ids=[r.get("conversation_id") for r in records if r.get("conversation_id")],
        )
        self._send_json({
            "isSuccess": True,
            "group_id": group_id,
            "imported_count": imported_count,
            "job": job,
            "message": f"{source_note}. Обработано и оценено диалогов: {imported_count}."
            + (f" LLM-оценка запущена в фоне ({job['model']})." if job else "")
        })

    def _handle_upload_conversations(self, body: Dict[str, Any]) -> None:
        group_id = (body.get("group_id") or "CUSTOM").strip()
        payload = body.get("payload")
        if not payload:
            self._send_json({"isSuccess": False, "error": "Пустой JSON пакет"}, status=400)
            return

        records: List[Dict[str, Any]] = []
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            if "data" in payload and isinstance(payload["data"], dict) and "records" in payload["data"]:
                records = payload["data"]["records"]
                group_id = payload["data"].get("dataset_group_id", group_id)
            elif "records" in payload and isinstance(payload["records"], list):
                records = payload["records"]
            elif "conversation_id" in payload and "transcript" in payload:
                records = [payload]

        if not records:
            self._send_json({"isSuccess": False, "error": "Не найдено записей диалогов (ожидается поле data.records или массив объектов с conversation_id и transcript)."}, status=400)
            return

        with DB_LOCK:
            conn = get_db()
            checklist = get_active_checklist(conn, body.get("checklist_id"))
            cnt = _upsert_conversations_internal(conn, records, group_id, checklist, {"provider": "builtin"})
            conn.close()

        job = start_batch_llm_job(
            f"Оценка загруженного набора {group_id}", reset_appeals=False,
            conversation_ids=[r.get("conversation_id") for r in records if isinstance(r, dict) and r.get("conversation_id")],
        )
        self._send_json({
            "isSuccess": True,
            "imported_count": cnt,
            "first_conversation_id": records[0].get("conversation_id"),
            "job": job,
            "message": f"Успешно загружено и оценено диалогов: {cnt}"
            + (f". LLM-оценка запущена в фоне ({job['model']})." if job else "")
        })

    def _handle_list_checklists(self) -> None:
        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id, name, description, is_active, config_json, updated_at FROM checklists ORDER BY updated_at DESC")
            items = []
            for r in cur.fetchall():
                cfg = json.loads(r["config_json"])
                cfg["is_active"] = bool(r["is_active"])
                cfg["updated_at"] = r["updated_at"]
                items.append(cfg)
            conn.close()
        self._send_json({"isSuccess": True, "checklists": items, "presets": PRESET_CRITERIA_TEMPLATES})

    def _handle_save_checklist(self, body: Dict[str, Any]) -> None:
        checklist = body.get("checklist")
        apply_to_all = bool(body.get("apply_to_all", True))
        if not checklist or not isinstance(checklist, dict):
            self._send_json({"isSuccess": False, "error": "Некорректный формат чек-листа"}, status=400)
            return

        cid = (checklist.get("id") or f"custom-{int(datetime.datetime.now().timestamp())}").strip()
        checklist["id"] = cid
        name = (checklist.get("name") or "Пользовательский чек-лист").strip()
        desc = (checklist.get("description") or "").strip()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        re_evaluated = 0
        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE checklists SET is_active = 0")
            cur.execute("""
                INSERT INTO checklists (id, name, description, is_active, config_json, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    is_active = 1,
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
            """, (cid, name, desc, json.dumps(checklist, ensure_ascii=False), now_iso))
            conn.commit()

            if apply_to_all:
                llm_cfg = get_llm_config(conn)
                cur.execute("SELECT conversation_id, channel, language, audio_url, transcript_json FROM conversations")
                all_rows = cur.fetchall()
                for row in all_rows:
                    rec = {
                        "conversation_id": row["conversation_id"],
                        "channel": row["channel"],
                        "language": row["language"],
                        "audio_url": row["audio_url"],
                        "transcript": json.loads(row["transcript_json"])
                    }
                    # Use fast analytical engine for batch re-evaluation of all 65 conversations so it completes in <0.2s
                    eval_res = evaluate_conversation_analytical(rec, checklist)
                    eval_json = json.dumps(eval_res, ensure_ascii=False)
                    cur.execute("""
                        UPDATE conversations
                        SET initial_eval_json = ?, current_eval_json = ?, checklist_id = ?, updated_at = ?
                        WHERE conversation_id = ?
                    """, (eval_json, eval_json, cid, now_iso, row["conversation_id"]))
                    re_evaluated += 1
                conn.commit()
            conn.close()

        # With an external LLM configured, refine the instant analytical results in the background
        job = start_batch_llm_job("Переоценка по новому чек-листу", reset_appeals=False) if apply_to_all else None

        self._send_json({
            "isSuccess": True,
            "checklist": checklist,
            "re_evaluated_count": re_evaluated,
            "job": job,
            "message": f"Чек-лист «{name}» сохранён и применён к {re_evaluated} диалогам."
            + (f" Уточняющая LLM-оценка запущена в фоне ({job['model']})." if job else "")
        })

    def _handle_evaluate(self, body: Dict[str, Any]) -> None:
        cid = body.get("conversation_id")
        eval_all = bool(body.get("all", False))
        group_id = (body.get("group_id") or "").strip()
        checklist_id = body.get("checklist_id")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        target_cids: List[str] = []
        with DB_LOCK:
            conn = get_db()
            checklist = get_active_checklist(conn, checklist_id)
            llm_cfg = get_llm_config(conn)
            cur = conn.cursor()

            if eval_all:
                if group_id and group_id.upper() not in ("ALL", "*"):
                    if group_id.upper() in ("PROD", "FBROKER"):
                        cur.execute(
                            "SELECT conversation_id, channel, language, audio_url, transcript_json FROM conversations WHERE UPPER(dataset_group_id) IN ('PROD', 'FBROKER') ORDER BY conversation_id ASC"
                        )
                    else:
                        cur.execute(
                            "SELECT conversation_id, channel, language, audio_url, transcript_json FROM conversations WHERE UPPER(dataset_group_id) = UPPER(?) ORDER BY conversation_id ASC",
                            (group_id,)
                        )
                else:
                    cur.execute("SELECT conversation_id, channel, language, audio_url, transcript_json FROM conversations ORDER BY conversation_id ASC")
                rows = cur.fetchall()
                target_cids = [r["conversation_id"] for r in rows]

                use_llm_batch = is_llm_active(llm_cfg) and not body.get("force_builtin")
                if use_llm_batch:
                    conn.close()
                else:
                    for r in rows:
                        rec = {
                            "conversation_id": r["conversation_id"],
                            "channel": r["channel"],
                            "language": r["language"],
                            "audio_url": r["audio_url"],
                            "transcript": json.loads(r["transcript_json"])
                        }
                        ev = evaluate_conversation_analytical(rec, checklist)
                        ev_json = json.dumps(ev, ensure_ascii=False)
                        cur.execute("""
                            UPDATE conversations
                            SET initial_eval_json = ?, current_eval_json = ?, checklist_id = ?, appeals_json = '[]', updated_at = ?
                            WHERE conversation_id = ?
                        """, (ev_json, ev_json, checklist["id"], now_iso, r["conversation_id"]))
                    conn.commit()
                    conn.close()
                    self._send_json({
                        "isSuccess": True,
                        "evaluated_count": len(rows),
                        "message": f"Диалоги набора {group_id or 'ALL'} ({len(rows)} шт.) успешно переоценены по чек-листу «{checklist['name']}»."
                    })
                    return

            else:
                cur.execute("SELECT * FROM conversations WHERE conversation_id = ?", (cid,))
                row = cur.fetchone()
                conn.close()

        if eval_all:
            # LLM batch: runs in the background, the UI polls /api/jobs/current
            job = start_batch_llm_job(
                f"Оценка набора {group_id or 'ALL'}",
                reset_appeals=True,
                conversation_ids=target_cids
            )
            self._send_json({
                "isSuccess": True,
                "job": job,
                "message": (
                    f"Запущена оценка {job['total']} диалогов ({group_id or 'ALL'}) через {LLM_PROVIDERS[job['provider']]['label']} ({job['model']})."
                    if job else "Нет диалогов для оценки."
                ),
            })
            return

        if not row:
            self._send_json({"isSuccess": False, "error": f"Диалог {cid} не найден"}, status=404)
            return

        rec = {
            "conversation_id": row["conversation_id"],
            "channel": row["channel"],
            "language": row["language"],
            "audio_url": row["audio_url"],
            "transcript": json.loads(row["transcript_json"])
        }
        # The LLM call can take several seconds — never hold the DB lock while waiting for it
        ev = evaluate_conversation_llm(rec, checklist, llm_cfg)
        ev_json = json.dumps(ev, ensure_ascii=False)
        with DB_LOCK:
            conn = get_db()
            conn.execute("""
                UPDATE conversations
                SET initial_eval_json = ?, current_eval_json = ?, checklist_id = ?, appeals_json = '[]', updated_at = ?
                WHERE conversation_id = ?
            """, (ev_json, ev_json, checklist["id"], now_iso, cid))
            conn.commit()
            conn.close()

        meta = ev.get("meta", {})
        if meta.get("llm_used"):
            engine_note = f" (модель: {meta.get('model_name')})"
        elif meta.get("llm_fallback_note"):
            engine_note = f". ⚠️ {meta['llm_fallback_note']}"
        else:
            engine_note = " (встроенный движок)"
        self._send_json({
            "isSuccess": True,
            "conversation_id": cid,
            "evaluation": ev,
            "llm_used": bool(meta.get("llm_used")),
            "message": f"Оценка диалога {cid} выполнена. Итоговый балл: {ev['final_score']}{engine_note}"
        })

    def _handle_submit_appeal(self, body: Dict[str, Any]) -> None:
        cid = (body.get("conversation_id") or "").strip()
        crit_id = (body.get("criterion_id") or "").strip()
        comment = (body.get("comment") or "").strip()
        author = (body.get("author") or "Оператор службы поддержки").strip()

        if not cid or not crit_id or not comment:
            self._send_json({"isSuccess": False, "error": "Укажите диалог, критерий и комментарий к апелляции."}, status=400)
            return

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT * FROM conversations WHERE conversation_id = ?", (cid,))
            row = cur.fetchone()
            if not row:
                conn.close()
                self._send_json({"isSuccess": False, "error": f"Диалог {cid} не найден"}, status=404)
                return

            curr_eval = json.loads(row["current_eval_json"])
            appeals = json.loads(row["appeals_json"]) if row["appeals_json"] else []

            target_crit = None
            for item in curr_eval.get("criteria_results", []):
                if item["criterion_id"] == crit_id:
                    target_crit = item
                    break

            if not target_crit:
                conn.close()
                self._send_json({"isSuccess": False, "error": f"Критерий {crit_id} не найден в оценке"}, status=404)
                return

            appeal_obj = {
                "id": f"app-{cid}-{crit_id}-{int(datetime.datetime.now().timestamp())}",
                "conversation_id": cid,
                "criterion_id": crit_id,
                "criterion_code": target_crit.get("code", ""),
                "criterion_name": target_crit.get("name", ""),
                "original_answer": target_crit.get("answer"),
                "original_score": curr_eval.get("final_score"),
                "operator_comment": comment,
                "author": author,
                "status": "pending",
                "created_at": now_iso,
                "decision": None,
                "new_answer": None,
                "reviewer_comment": "",
                "reviewer_name": "",
                "decided_at": None,
                "final_score_after": None
            }

            target_crit["appeal"] = appeal_obj
            # Replace existing appeal on same criterion or append
            appeals = [a for a in appeals if a.get("criterion_id") != crit_id]
            appeals.append(appeal_obj)

            cur.execute("""
                UPDATE conversations
                SET current_eval_json = ?, appeals_json = ?, updated_at = ?
                WHERE conversation_id = ?
            """, (json.dumps(curr_eval, ensure_ascii=False), json.dumps(appeals, ensure_ascii=False), now_iso, cid))
            conn.commit()
            conn.close()

        self._send_json({
            "isSuccess": True,
            "appeal": appeal_obj,
            "current_evaluation": curr_eval,
            "appeals": appeals,
            "message": f"Апелляция по критерию «{target_crit.get('code')} {target_crit.get('name')}» сохранена и передана проверяющему."
        })

    def _handle_decide_appeal(self, body: Dict[str, Any]) -> None:
        cid = (body.get("conversation_id") or "").strip()
        crit_id = (body.get("criterion_id") or "").strip()
        decision = (body.get("decision") or "confirm").strip().lower()  # "confirm" or "change"
        new_answer = body.get("new_answer")
        reviewer_comment = (body.get("reviewer_comment") or "").strip()
        reviewer_name = (body.get("reviewer_name") or "Старший проверяющий QA").strip()

        if not cid or not crit_id or not reviewer_comment:
            self._send_json({"isSuccess": False, "error": "Укажите решение и причину проверяющего."}, status=400)
            return

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT * FROM conversations WHERE conversation_id = ?", (cid,))
            row = cur.fetchone()
            if not row:
                conn.close()
                self._send_json({"isSuccess": False, "error": f"Диалог {cid} не найден"}, status=404)
                return

            checklist = get_active_checklist(conn, row["checklist_id"])
            init_eval = json.loads(row["initial_eval_json"])
            curr_eval = json.loads(row["current_eval_json"])
            appeals = json.loads(row["appeals_json"]) if row["appeals_json"] else []

            # Find original criterion from initial_eval
            init_crit_map = {c["criterion_id"]: c for c in init_eval.get("criteria_results", [])}

            updated_inputs = []
            resolved_appeal = None

            for item in curr_eval.get("criteria_results", []):
                c_copy = copy.deepcopy(item)
                if c_copy["criterion_id"] == crit_id:
                    orig_c = init_crit_map.get(crit_id, item)
                    if not c_copy.get("initial_evaluation"):
                        c_copy["initial_evaluation"] = {
                            "answer": orig_c.get("answer"),
                            "raw_points": orig_c.get("raw_points"),
                            "redistributed_points": orig_c.get("redistributed_points"),
                            "reasoning": orig_c.get("reasoning")
                        }
                    existing_app = c_copy.get("appeal") or {
                        "id": f"app-{cid}-{crit_id}-{int(datetime.datetime.now().timestamp())}",
                        "conversation_id": cid,
                        "criterion_id": crit_id,
                        "criterion_code": c_copy.get("code", ""),
                        "criterion_name": c_copy.get("name", ""),
                        "original_answer": orig_c.get("answer"),
                        "original_score": init_eval.get("final_score"),
                        "operator_comment": "Прямой пересмотр критерия проверяющим",
                        "author": "Оператор",
                        "created_at": now_iso
                    }

                    if decision == "change" and new_answer is not None:
                        c_copy["answer"] = new_answer
                        existing_app["status"] = "changed"
                        existing_app["decision"] = "change"
                        existing_app["new_answer"] = new_answer
                    else:
                        # Confirm existing/original evaluation
                        c_copy["answer"] = orig_c.get("answer")
                        existing_app["status"] = "confirmed"
                        existing_app["decision"] = "confirm"
                        existing_app["new_answer"] = orig_c.get("answer")

                    existing_app["reviewer_comment"] = reviewer_comment
                    existing_app["reviewer_name"] = reviewer_name
                    existing_app["decided_at"] = now_iso
                    c_copy["appeal"] = existing_app
                    resolved_appeal = existing_app

                updated_inputs.append(c_copy)

            raw_tr = enrich_transcript_with_offsets(json.loads(row["transcript_json"]))
            td_metrics = compute_time_delta_and_var_metrics(raw_tr)
            recalculated = calculate_checklist_score(
                checklist, updated_inputs, time_delta_metrics=td_metrics, transcript=raw_tr
            )
            recalculated["meta"] = curr_eval.get("meta", {})

            if resolved_appeal:
                resolved_appeal["final_score_after"] = recalculated["final_score"]
                # Also sync inside recalculated criteria_results
                for item in recalculated["criteria_results"]:
                    if item["criterion_id"] == crit_id and item.get("appeal"):
                        item["appeal"]["final_score_after"] = recalculated["final_score"]
                appeals = [a for a in appeals if a.get("criterion_id") != crit_id]
                appeals.append(resolved_appeal)

            cur.execute("""
                UPDATE conversations
                SET current_eval_json = ?, appeals_json = ?, updated_at = ?
                WHERE conversation_id = ?
            """, (json.dumps(recalculated, ensure_ascii=False), json.dumps(appeals, ensure_ascii=False), now_iso, cid))
            conn.commit()
            conn.close()

        self._send_json({
            "isSuccess": True,
            "conversation_id": cid,
            "initial_evaluation": init_eval,
            "current_evaluation": recalculated,
            "appeals": appeals,
            "message": f"Решение сохранено. Финальный результат пересчитан: {recalculated['final_score']} баллов."
        })

    def _handle_list_appeals(self) -> None:
        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT conversation_id, channel, language, initial_eval_json, current_eval_json, appeals_json FROM conversations")
            rows = cur.fetchall()
            conn.close()

        all_appeals = []
        for r in rows:
            apps = json.loads(r["appeals_json"]) if r["appeals_json"] else []
            init_ev = json.loads(r["initial_eval_json"]) if r["initial_eval_json"] else {}
            curr_ev = json.loads(r["current_eval_json"]) if r["current_eval_json"] else {}
            for a in apps:
                item = dict(a)
                item["channel"] = r["channel"]
                item["language"] = r["language"]
                item["initial_total_score"] = init_ev.get("final_score")
                item["current_total_score"] = curr_ev.get("final_score")
                all_appeals.append(item)

        # Sort pending first, then by created_at desc
        all_appeals.sort(key=lambda x: (0 if x.get("status") == "pending" else 1, x.get("created_at", "")))
        self._send_json({"isSuccess": True, "appeals": all_appeals})

    def _merge_llm_settings(self, cfg: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        """Applies settings from the UI onto the stored config (sanitizing pasted keys / curl snippets)."""
        cfg = dict(cfg)
        prev_provider = cfg.get("provider", "builtin")
        for k in ("provider", "model", "base_url", "cost_per_1m_input_usd", "cost_per_1m_output_usd", "usd_to_kzt"):
            if k in body and body[k] is not None:
                cfg[k] = body[k]

        raw_key = str(body.get("api_key") or "").strip()
        snippet_model = ""
        if body.get("clear_api_key"):
            cfg["api_key"] = ""
        elif raw_key:
            clean_key = extract_api_key(raw_key)
            if not clean_key:
                raise ValueError("Не удалось распознать API-ключ во введённом тексте. Вставьте сам ключ (например, AIza… или AQ.…).")
            cfg["api_key"] = clean_key
            snippet_model = extract_model_from_snippet(raw_key)
            # A key was entered but the provider was left on the built-in engine — switch automatically
            if str(cfg.get("provider") or "builtin") == "builtin":
                cfg["provider"] = "openai" if clean_key.startswith("sk-") else "gemini"

        # Changing provider without an explicit model: use the model from a pasted snippet or the provider default
        new_provider = str(cfg.get("provider") or "builtin")
        if not body.get("model") and snippet_model:
            cfg["model"] = snippet_model
        elif new_provider != prev_provider and not body.get("model") and new_provider in LLM_PROVIDERS:
            cfg["model"] = LLM_PROVIDERS[new_provider]["default_model"] or cfg.get("model")
        return normalize_llm_config(cfg)

    def _handle_save_llm_settings(self, body: Dict[str, Any]) -> None:
        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            try:
                cfg = self._merge_llm_settings(get_llm_config(conn), body)
            except ValueError as exc:
                conn.close()
                self._send_json({"isSuccess": False, "error": str(exc)}, status=400)
                return
            cur.execute(
                "INSERT OR REPLACE INTO settings (key, value_json) VALUES ('llm_config', ?)",
                (json.dumps(cfg, ensure_ascii=False),)
            )
            conn.commit()
            conn.close()

        test_result = None
        if body.get("test", True) and cfg["provider"] != "builtin" and is_llm_active(cfg):
            test_result = test_llm_connection(cfg)
            # The requested model was retired and an alias answered instead — persist the working model
            if test_result.get("ok") and test_result.get("model") and test_result["model"] != cfg["model"]:
                cfg["model"] = test_result["model"]
                with DB_LOCK:
                    conn = get_db()
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value_json) VALUES ('llm_config', ?)",
                        (json.dumps(cfg, ensure_ascii=False),),
                    )
                    conn.commit()
                    conn.close()

        job = None
        if body.get("reevaluate_all") and (test_result is None or test_result.get("ok")):
            job = start_batch_llm_job("Переоценка после смены модели", reset_appeals=True)

        label = LLM_PROVIDERS[cfg["provider"]]["label"]
        if cfg["provider"] == "builtin":
            message = "Сохранено: используется встроенный аналитический движок."
        elif not is_llm_active(cfg):
            message = f"Сохранено, но для {label} не задан API-ключ — оценки будут выполняться встроенным движком."
        elif test_result and not test_result.get("ok"):
            message = f"Настройки сохранены, но проверка {label} не прошла. {test_result.get('message')}"
        else:
            message = f"Сохранено: {label} ({cfg['model']}) подключён."
            if job:
                message += f" Запущена переоценка {job['total']} диалогов."
        self._send_json({
            "isSuccess": True,
            "llm_config": public_llm_config(cfg),
            "test": test_result,
            "job": job,
            "message": message,
        })

    def _handle_test_llm_settings(self, body: Dict[str, Any]) -> None:
        """Tests the settings currently entered in the form (without saving them)."""
        with DB_LOCK:
            conn = get_db()
            stored = get_llm_config(conn)
            conn.close()
        try:
            cfg = self._merge_llm_settings(stored, body)
        except ValueError as exc:
            self._send_json({"isSuccess": False, "error": str(exc)}, status=400)
            return
        if cfg["provider"] != "builtin" and not is_llm_active(cfg):
            self._send_json({"isSuccess": True, "test": {"ok": False, "message": "API-ключ не задан."}})
            return
        self._send_json({"isSuccess": True, "test": test_llm_connection(cfg)})

    def _handle_asr_transcribe(self, body: Dict[str, Any]) -> None:
        """
        Runs CMU PocketSphinx ASR & VAD segmentation on a voice call conversation (`conversation_id`)
        or uploaded audio file (`audio_base64`).
        Optionally updates the stored transcript (`apply_to_conversation: true`) and re-evaluates the call.
        """
        cid = (body.get("conversation_id") or "").strip()
        mode = (body.get("mode") or "enhance").strip()
        apply_changes = bool(body.get("apply_to_conversation", True))
        audio_b64 = body.get("audio_base64")
        custom_bytes = base64.b64decode(audio_b64) if audio_b64 else None

        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT * FROM conversations WHERE conversation_id = ?", (cid,))
            row = cur.fetchone()
            conn.close()

        if not row:
            self._send_json({"isSuccess": False, "error": f"Диалог '{cid}' не найден"}, status=404)
            return

        conv_dict = {
            "conversation_id": row["conversation_id"],
            "channel": row["channel"],
            "language": row["language"],
            "audio_url": row["audio_url"],
            "transcript": json.loads(row["transcript_json"]),
        }

        sphinx_res = process_conversation_with_pocketsphinx(
            conv_dict,
            AUDIO_CACHE_DIR,
            mode=mode,
            custom_audio_bytes=custom_bytes,
        )

        new_eval = None
        if apply_changes and sphinx_res.get("transcript"):
            new_transcript = sphinx_res["transcript"]
            conv_dict["transcript"] = new_transcript
            with DB_LOCK:
                conn = get_db()
                checklist = get_active_checklist(conn)
                llm_cfg = get_llm_config(conn)
                conn.close()
            new_eval = evaluate_conversation_llm(conv_dict, checklist, llm_cfg)
            with DB_LOCK:
                conn = get_db()
                cur = conn.cursor()
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cur.execute(
                    """
                    UPDATE conversations
                    SET transcript_json = ?, current_eval_json = ?, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (
                        json.dumps(new_transcript, ensure_ascii=False),
                        json.dumps(new_eval, ensure_ascii=False),
                        now_iso,
                        cid,
                    ),
                )
                conn.commit()
                conn.close()

        self._send_json({
            "isSuccess": True,
            "conversation_id": cid,
            "mode": mode,
            "applied": apply_changes,
            "audio_source": sphinx_res.get("audio_source"),
            "sphinx_report": sphinx_res.get("sphinx_report"),
            "corrections_count": sphinx_res.get("corrections_count", 0),
            "corrections": sphinx_res.get("corrections", []),
            "transcript": enrich_transcript_with_offsets(sphinx_res.get("transcript", [])),
            "updated_evaluation": new_eval,
            "message": (
                f"CMU PocketSphinx обработал запись звонка '{cid}': "
                f"выделено сегментов VAD — {sphinx_res.get('sphinx_report', {}).get('vad_segments_count', 0)}, "
                f"исправлено искажений транскрибации — {sphinx_res.get('corrections_count', 0)}."
            ),
        })

    def _handle_audio_proxy(self, cid: str) -> None:
        """
        Streams or proxies voice call audio for conversation `cid` with local disk caching,
        Power Automate HTML wrapper extraction, and Range header support.
        """
        with DB_LOCK:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT audio_url, transcript_json FROM conversations WHERE conversation_id = ?", (cid,))
            row = cur.fetchone()
            conn.close()

        if not row:
            self._send_json({"isSuccess": False, "error": "Диалог не найден"}, status=404)
            return

        audio_url = row["audio_url"]
        os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(AUDIO_CACHE_DIR, f"{cid}.wav")
        meta_file = os.path.join(AUDIO_CACHE_DIR, f"{cid}.meta.json")

        real_file, source_kind = ensure_real_audio_file(cid, audio_url, AUDIO_CACHE_DIR) if audio_url else (None, "unavailable")
        if not real_file:
            # Generate valid 16kHz PCM audio matching full transcript timeline so browser player & Range seeking always work
            transcript = enrich_transcript_with_offsets(json.loads(row["transcript_json"] or "[]"))
            build_synthetic_pcm16_from_transcript(transcript, cache_file)
            with open(meta_file, "w", encoding="utf-8") as mf:
                json.dump({"content_type": "audio/wav", "synthesized_fallback": True}, mf)
            audio_source_header = "synthesized"
        else:
            audio_source_header = "real"

        content_type = "audio/wav"
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as mf:
                    meta_obj = json.load(mf)
                    content_type = meta_obj.get("content_type", "audio/wav")
                    if meta_obj.get("synthesized_fallback"):
                        audio_source_header = "synthesized"
            except Exception:
                pass

        file_size = os.path.getsize(cache_file)
        range_header = self.headers.get("Range")

        if range_header:
            m = urllib.parse.unquote(range_header)
            try:
                byte_range = m.replace("bytes=", "").split("-")
                start = int(byte_range[0]) if byte_range[0] else 0
                end = int(byte_range[1]) if (len(byte_range) > 1 and byte_range[1]) else file_size - 1
                end = min(end, file_size - 1)
                chunk_len = max(0, end - start + 1)
                with open(cache_file, "rb") as f:
                    f.seek(start)
                    data = f.read(chunk_len)
                self.send_response(206)
                self._send_cors_headers()
                self.send_header("Content-Type", content_type)
                self.send_header("X-Audio-Source", audio_source_header)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception:
                pass

        with open(cache_file, "rb") as f:
            data = f.read()
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("X-Audio-Source", audio_source_header)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        safe_rel = os.path.normpath(path.lstrip("/"))
        if safe_rel.startswith(".."):
            self._send_json({"isSuccess": False, "error": "Forbidden"}, status=403)
            return
        full_path = os.path.join(PUBLIC_DIR, safe_rel)
        if not os.path.isfile(full_path):
            # Fallback to index.html for SPA
            full_path = os.path.join(PUBLIC_DIR, "index.html")
        if not os.path.isfile(full_path):
            self._send_json({"isSuccess": False, "error": "Not found"}, status=404)
            return

        ctype, _ = mimetypes.guess_type(full_path)
        if not ctype:
            ctype = "text/plain; charset=utf-8"
        elif ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype = f"{ctype}; charset=utf-8"

        with open(full_path, "rb") as f:
            content = f.read()

        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    init_db()
    server = ThreadingHTTPServer((host, port), CQARequestHandler)
    print(f"==================================================================")
    print(f"  Freedom Broker CQA — Conversation Quality Analytics")
    print(f"  Server running at: http://localhost:{port}")
    print(f"  Dynamics 365 Web Resource mode: http://localhost:{port}/?d365=1")
    with DB_LOCK:
        _conn = get_db()
        _cfg = get_llm_config(_conn)
        _conn.close()
    if is_llm_active(_cfg):
        print(f"  Evaluation engine: {LLM_PROVIDERS[_cfg['provider']]['label']} ({_cfg['model']}, key {mask_api_key(_cfg['api_key']) or 'from env'})")
    else:
        print(f"  Evaluation engine: built-in hybrid analytical engine (LLM not configured)")
    print(f"==================================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.server_close()


if __name__ == "__main__":
    port_env = int(os.environ.get("PORT", "8080"))
    run_server(port=port_env)
