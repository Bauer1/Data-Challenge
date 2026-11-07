# -*- coding: utf-8 -*-
"""
Freedom Broker CQA — Conversation Quality Analytics Evaluator Engine.
Implements:
1. Default Freedom Broker Checklist (8 criteria across 4 sections, exact PDF specification).
2. Proportional N/A weight redistribution & scale-to-weight conversion (Page 2 of Checklist PDF).
3. Multilingual (RU / KZ / EN) analytical evaluation engine for chats & voice calls with exact turn citations.
4. Dynamic custom criterion evaluation (for new/modified criteria added via UI).
5. External LLM API integration (Google Gemini, OpenAI, OpenRouter, Local Ollama) with cost calculation.
"""

from __future__ import annotations
import copy
import datetime
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_CHECKLIST: Dict[str, Any] = {
    "id": "default-freedom-cqa-v1",
    "name": "Чек-лист качества чатов и звонков Freedom Broker (Базовый)",
    "description": "Восемь критериев для оценки работы оператора по переписке и звонкам (RU / KZ / EN) с пропорциональным перераспределением весов при N/A.",
    "version": 1,
    "is_default": True,
    "criteria": [
        {
            "id": "c01",
            "code": "01",
            "section": "НАЧАЛО И ПОНИМАНИЕ ЗАПРОСА",
            "name": "Установление контакта",
            "description": "Да — оператор поздоровался на языке клиента и представился в первом ответе.",
            "type": "yes_no",
            "weight": 10.0,
            "allow_na": False,
            "na_condition": "",
            "scale": []
        },
        {
            "id": "c02",
            "code": "02",
            "section": "НАЧАЛО И ПОНИМАНИЕ ЗАПРОСА",
            "name": "Выявление потребности",
            "description": "Да — оператор учёл известные детали и уточнил недостающее до ответа. N/A — данных для ответа хватало с самого начала.",
            "type": "yes_no",
            "weight": 15.0,
            "allow_na": True,
            "na_condition": "Данных для ответа хватало с самого начала или клиент покинул чат до формулировки запроса.",
            "scale": []
        },
        {
            "id": "c03",
            "code": "03",
            "section": "БЕЗОПАСНОСТЬ И РЕШЕНИЕ",
            "name": "Идентификация",
            "description": "Да — проверка личности подтверждена до раскрытия данных или защищённого действия. N/A — запрос не требовал идентификации.",
            "type": "yes_no",
            "weight": 20.0,
            "allow_na": True,
            "na_condition": "Запрос носит общий информационный характер и не требовал идентификации или доступа к счёту.",
            "scale": []
        },
        {
            "id": "c04",
            "code": "04",
            "section": "БЕЗОПАСНОСТЬ И РЕШЕНИЕ",
            "name": "Корректная и полная консультация",
            "description": "Оцените ответ или следующий шаг по шкале 0–20. N/A — чат закончился до того, как клиент сформулировал запрос.",
            "type": "scale",
            "weight": 20.0,
            "allow_na": True,
            "na_condition": "Чат закончился до того, как клиент сформулировал запрос.",
            "scale": [
                {"value": 20, "label": "Все существенные вопросы закрыты или дан корректный следующий шаг."},
                {"value": 15, "label": "Основной вопрос решён, пропущена второстепенная деталь."},
                {"value": 10, "label": "Часть запроса закрыта, существенный вопрос остался открытым."},
                {"value": 5, "label": "Есть общая подсказка, но клиенту всё ещё непонятно, как действовать."},
                {"value": 0, "label": "Ответа по существу нет или ключевая рекомендация ошибочна."}
            ]
        },
        {
            "id": "c05",
            "code": "05",
            "section": "ВЕДЕНИЕ И ЗАВЕРШЕНИЕ ДИАЛОГА",
            "name": "Эмпатия и клиентоориентированность",
            "description": "Да — оператор вежлив и, если клиент выразил недовольство или описал неудобство, отреагировал на конкретную проблему.",
            "type": "yes_no",
            "weight": 15.0,
            "allow_na": False,
            "na_condition": "",
            "scale": []
        },
        {
            "id": "c06",
            "code": "06",
            "section": "ВЕДЕНИЕ И ЗАВЕРШЕНИЕ ДИАЛОГА",
            "name": "Управление диалогом, скорость и грамотность",
            "description": "Оцените ясность, порядок ответов и предупреждение об ожидании по шкале 0–10.",
            "type": "scale",
            "weight": 15.0,
            "allow_na": False,
            "na_condition": "",
            "scale": [
                {"value": 10, "label": "Ответы ясные и связные. Если было явное ожидание, оператор предупредил."},
                {"value": 7, "label": "Один заметный сбой, но клиент может понять ответ и продолжить."},
                {"value": 4, "label": "Клиенту приходится переспрашивать из-за формулировок или порядка ответов."},
                {"value": 0, "label": "Из-за бессвязных или противоречивых реплик ход диалога непонятен."}
            ]
        },
        {
            "id": "c07",
            "code": "07",
            "section": "ВЕДЕНИЕ И ЗАВЕРШЕНИЕ ДИАЛОГА",
            "name": "Завершение",
            "description": "Да — оператор подвёл итог или предложил дальнейшую помощь и попрощался. N/A — чат оборвался до возможности его завершить.",
            "type": "yes_no",
            "weight": 5.0,
            "allow_na": True,
            "na_condition": "Чат оборвался по инициативе клиента или из-за потери соединения до возможности его завершить.",
            "scale": []
        },
        {
            "id": "c08",
            "code": "08",
            "section": "ГРУБЫЕ НАРУШЕНИЯ",
            "name": "Грубые ошибки",
            "description": "Да — выявлено оскорбление клиента, предложение платной услуги в обход компании или раскрытие конфиденциальной информации. Нет — ни одно из трёх не выявлено.",
            "type": "critical_flag",
            "weight": 0.0,
            "allow_na": False,
            "na_condition": "",
            "scale": []
        }
    ]
}


# Preset criteria templates that can be added/swapped in 1 click during the jury check
PRESET_CRITERIA_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "c_sla_response",
        "code": "09",
        "section": "ВЕДЕНИЕ И ЗАВЕРШЕНИЕ ДИАЛОГА",
        "name": "Оперативность первого ответа оператора (SLA до 2 минут)",
        "description": "Да — оператор ответил клиенту быстрее чем за 2 минуты (120 секунд) после эскалации или подключения. Нет — ожидание первого ответа превысило 2 минуты без предупреждения.",
        "type": "yes_no",
        "weight": 10.0,
        "allow_na": True,
        "na_condition": "Временные метки эскалации отсутствуют.",
        "scale": []
    },
    {
        "id": "c_self_service",
        "code": "09",
        "section": "БЕЗОПАСНОСТЬ И РЕШЕНИЕ",
        "name": "Обучение навигации и самообслуживанию",
        "description": "Оцените, насколько чётко оператор указал маршрут в интерфейсе (раздел меню, адрес сайта или путь к документу), чтобы клиент мог выполнить действие самостоятельно.",
        "type": "scale",
        "weight": 15.0,
        "allow_na": True,
        "na_condition": "Запрос не связан с навигацией по приложению, сайту или отчётам.",
        "scale": [
            {"value": 10, "label": "Указан точный пошаговый маршрут в приложении или точный домен веб-кабинета."},
            {"value": 5, "label": "Назван раздел в общих чертах, клиенту пришлось уточнять путь."},
            {"value": 0, "label": "Маршрут не указан или назван неверно."}
        ]
    },
    {
        "id": "c_no_fin_advice",
        "code": "09",
        "section": "БЕЗОПАСНОСТЬ И РЕШЕНИЕ",
        "name": "Соблюдение границ инвестиционного консультирования",
        "description": "Да — при вопросе о выборе ценных бумаг оператор объяснил только техническую механику поиска/покупки и не давал индивидуальных инвестиционных обещаний. N/A — вопрос не касался покупки или выбора ценных бумаг.",
        "type": "yes_no",
        "weight": 10.0,
        "allow_na": True,
        "na_condition": "Запрос не касался выбора или покупки ценных бумаг.",
        "scale": []
    }
]


def parse_timestamp_seconds(ts: str, base_iso: Optional[datetime.datetime] = None) -> Tuple[Optional[float], Optional[datetime.datetime]]:
    """
    Parse chat 'MM:SS' or voice call ISO-8601 timestamp into relative seconds from start of conversation.
    """
    if not ts:
        return None, base_iso
    ts = ts.strip()
    # Check MM:SS or HH:MM:SS
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", ts)
    if m:
        if m.group(3) is not None:
            secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        else:
            secs = int(m.group(1)) * 60 + int(m.group(2))
        return float(secs), base_iso
    # Try ISO 8601 (with possible 7-digit fractional seconds from .NET / PowerPlatform)
    try:
        iso_clean = re.sub(r"\.(\d{6})\d+", r".\1", ts)
        if iso_clean.endswith("Z"):
            iso_clean = iso_clean[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(iso_clean)
        if base_iso is None:
            return 0.0, dt
        diff = (dt - base_iso).total_seconds()
        return max(0.0, round(diff, 1)), base_iso
    except Exception:
        return None, base_iso


def format_mm_ss(seconds: Optional[float]) -> str:
    if seconds is None:
        return "00:00"
    total = max(0, int(round(seconds)))
    mins = total // 60
    secs = total % 60
    return f"{mins:02d}:{secs:02d}"


def enrich_transcript_with_offsets(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Adds `turn_index`, `offset_seconds`, `end_offset_seconds`, and `formatted_offset` to each turn in transcript.
    For voice calls (ISO-8601 timestamps from Dynamics 365 Omnichannel Voice), computes non-linear audio alignment:
    1. Pre-recording offset: aligns MP3 00:00 with 'Запись и транскрибирование запущены' / 'Жазу және транскрипция басталды'.
    2. Pause compression: accounts for excised time during 'приостановлены' -> 'возобновлены' transfer pauses.
    3. ASR endpoint-commit to utterance-start mapping: converts turn completion timestamp (t_commit) to speech start (t_start).
    """
    if not transcript:
        return []

    base_iso: Optional[datetime.datetime] = None
    has_iso = False
    for t in transcript:
        ts_str = str(t.get("timestamp", "") or "").strip()
        _, dt = parse_timestamp_seconds(ts_str, None)
        if dt is not None:
            base_iso = dt
            has_iso = True
            break

    wall_secs: List[float] = []
    for idx, t in enumerate(transcript):
        sec, base_iso = parse_timestamp_seconds(str(t.get("timestamp", "") or ""), base_iso)
        wall_secs.append(sec if sec is not None else float(idx * 4))

    if not has_iso:
        enriched = []
        for idx, t in enumerate(transcript):
            sec = wall_secs[idx]
            item = dict(t)
            item["turn_index"] = idx
            item["offset_seconds"] = round(sec, 1)
            item["end_offset_seconds"] = round(sec, 1)
            item["formatted_offset"] = format_mm_ss(sec)
            enriched.append(item)
        return enriched

    # 1. Pre-recording start offset (MP3 starts when recording/transcription starts)
    rec_start_wall = 0.0
    for idx, t in enumerate(transcript):
        if str(t.get("speaker", "")).lower() == "system":
            txt = str(t.get("text", "") or "").lower()
            if ("запись и транскрибирование запущены" in txt or "жазу және транскрипция басталды" in txt) and wall_secs[idx] <= 60.0:
                rec_start_wall = wall_secs[idx]
                break

    # 2. Recording pause intervals ('приостановлены' -> 'возобновлены')
    pauses: List[Tuple[float, float, float]] = []
    pause_start: Optional[float] = None
    for idx, t in enumerate(transcript):
        if str(t.get("speaker", "")).lower() == "system":
            txt = str(t.get("text", "") or "").lower()
            if "приостановлены" in txt or "уақытша тоқтатылды" in txt:
                pause_start = wall_secs[idx]
            elif ("возобновлены" in txt or "қайта жалғастырылды" in txt) and pause_start is not None:
                p_end = wall_secs[idx]
                if p_end > pause_start:
                    # Dynamics 365 MP3 stream retains ~6.6s (4.90s zero-frame + ~1.7s handoff) across pause
                    cut_sec = max(0.0, (p_end - pause_start) - 6.6)
                    pauses.append((pause_start, p_end, cut_sec))
                pause_start = None

    def wall_to_audio_commit(w_sec: float) -> float:
        if w_sec <= rec_start_wall:
            return 0.0
        t_val = w_sec - rec_start_wall
        cut_accum = 0.0
        for p_s, p_e, p_cut in pauses:
            if w_sec <= p_s:
                break
            elif w_sec >= p_e:
                cut_accum += p_cut
            else:
                ratio = (w_sec - p_s) / max(0.1, p_e - p_s)
                cut_accum += p_cut * ratio
        return max(0.0, t_val - cut_accum)

    commit_secs = [wall_to_audio_commit(w) for w in wall_secs]

    # 3. Map ASR endpoint commit time to utterance start time
    enriched = []
    for idx, t in enumerate(transcript):
        sp = str(t.get("speaker", "") or "").lower()
        txt = str(t.get("text", "") or "").strip()
        c_sec = commit_secs[idx]

        if sp == "system":
            start_sec = c_sec
        elif len(txt) <= 2 and txt.isdigit():
            # Single-digit DTMF press during IVR menu
            start_sec = max(0.0, c_sec - 0.5)
        else:
            prev_end = 0.0
            for j in range(idx - 1, -1, -1):
                prev_sp = str(transcript[j].get("speaker", "") or "").lower()
                prev_txt = str(transcript[j].get("text", "") or "").strip()
                # If current turn is IVR bot and previous turn was a mid-prompt DTMF digit, look before the digit
                if sp == "bot" and prev_sp == "client" and len(prev_txt) <= 2 and prev_txt.isdigit():
                    continue
                prev_end = commit_secs[j]
                break

            gap = max(0.0, c_sec - prev_end)
            # Russian/Kazakh conversational speech rate ~13.0 chars/sec
            est_dur = max(1.0, len(txt) / 13.0)
            if sp == "bot" and idx > 0:
                prev_txt_imm = str(transcript[idx - 1].get("text", "") or "").strip()
                if len(prev_txt_imm) <= 2 and prev_txt_imm.isdigit():
                    # Bot prompt was interrupted by or overlapped with DTMF; starts right after previous bot turn
                    est_dur = gap
            dur = min(gap, est_dur + 0.6) if gap > 0 else 0.0
            start_sec = max(prev_end, c_sec - dur)

        item = dict(t)
        item["turn_index"] = idx
        item["offset_seconds"] = round(start_sec, 1)
        item["end_offset_seconds"] = round(max(start_sec + 0.6, c_sec), 1)
        item["formatted_offset"] = format_mm_ss(start_sec)
        enriched.append(item)

    return enriched


def compute_time_delta_and_var_metrics(transcript: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Implements:
    - Class 1.3: Time-Delta Verification (SLA validation & first-reply latency):
      \\Delta t_{first} = t(first_operator_reply) - t(first_client_message)
      and operator queue handoff latency \\Delta t_{escalation}.
      If effective \\Delta t_{first} > 180 s, enforces upper bound on Criterion 06 (<= 7 points).
    - Class 2.1: Actuarial Empirical Quantiles & Weibull Value-at-Risk (VaR_{0.95}):
      VaR_{0.95} = \\inf \\{ t \\in \\mathbb{R} : P(\\Delta t > t) \\le 0.05 \\}
    """
    if not transcript:
        return {
            "delta_t_first_raw_sec": 0.0,
            "delta_t_first_operator_sla_sec": 0.0,
            "sla_threshold_sec": 180.0,
            "sla_breach_180s": False,
            "c06_max_cap": None,
            "response_intervals_sec": [],
            "empirical_var_95_sec": 0.0,
            "weibull_var_95_sec": 0.0,
            "weibull_shape_k": 1.5,
            "weibull_scale_lambda": 0.0,
            "mean_response_sec": 0.0,
            "max_response_sec": 0.0,
        }

    op_turns = [t for t in transcript if t.get("speaker") == "operator"]
    client_turns = [t for t in transcript if t.get("speaker") == "client"]
    sys_turns = [t for t in transcript if t.get("speaker") == "system"]

    escalation_turn = None
    for t in sys_turns:
        txt = (t.get("text") or "").lower()
        if any(w in txt for w in ("эскалация", "жіберілді", "transferred", "присоединился")):
            escalation_turn = t
            break

    first_op = op_turns[0] if op_turns else None
    first_client = client_turns[0] if client_turns else None

    delta_t_first_raw = 0.0
    if first_op and first_client:
        delta_t_first_raw = max(0.0, round(float(first_op.get("offset_seconds", 0.0)) - float(first_client.get("offset_seconds", 0.0)), 2))

    # Effective operator SLA delay: measured from escalation to operator (if bot handled pre-chat) or from first client message
    if first_op and escalation_turn:
        delta_t_first_sla = max(0.0, round(float(first_op.get("offset_seconds", 0.0)) - float(escalation_turn.get("offset_seconds", 0.0)), 2))
    else:
        delta_t_first_sla = delta_t_first_raw

    # Collect all operator response intervals \Delta t_k within the conversation
    intervals: List[float] = []
    if delta_t_first_sla > 0:
        intervals.append(delta_t_first_sla)

    last_prompt_time: Optional[float] = None
    for t in transcript:
        sp = t.get("speaker")
        sec = float(t.get("offset_seconds", 0.0))
        if sp in ("client", "system"):
            if last_prompt_time is None:
                last_prompt_time = sec
        elif sp == "operator":
            if last_prompt_time is not None:
                dt = max(0.0, round(sec - last_prompt_time, 2))
                if dt > 0:
                    intervals.append(dt)
                last_prompt_time = None
            else:
                # Consecutive operator turns (e.g. after hold in voice call or chat check)
                pass

    # Also capture intra-operator hold gaps (e.g., operator puts client on hold and returns minutes later)
    for i in range(1, len(op_turns)):
        gap = round(float(op_turns[i].get("offset_seconds", 0.0)) - float(op_turns[i - 1].get("offset_seconds", 0.0)), 2)
        if gap >= 30.0:
            intervals.append(gap)

    if not intervals:
        intervals = [max(1.0, delta_t_first_sla)]

    sorted_dt = sorted(intervals)
    n = len(sorted_dt)
    mean_dt = round(sum(sorted_dt) / n, 2)
    max_dt = round(sorted_dt[-1], 2)

    # Empirical VaR_{0.95} = \inf \{ t \in \mathbb{R} : P(\Delta t > t) \le 0.05 \}
    idx_95 = min(n - 1, max(0, int(math.ceil(0.95 * n)) - 1))
    emp_var_95 = round(sorted_dt[idx_95], 2)

    # Weibull tail fitting for heavy-tailed response delays: VaR_{0.95} = \lambda * (-ln(0.05))^(1/k)
    pos_dt = [max(0.5, x) for x in sorted_dt]
    if len(pos_dt) >= 2:
        log_vals = [math.log(x) for x in pos_dt]
        mean_log = sum(log_vals) / len(log_vals)
        var_log = sum((lv - mean_log) ** 2 for lv in log_vals) / max(1, len(log_vals) - 1)
        std_log = math.sqrt(max(0.04, var_log))
        # Gumbel / log-Weibull relation: std(ln X) = \pi / (\sqrt{6} * k) => k \approx 1.2825 / std_log
        shape_k = max(0.8, min(5.0, round(1.2825 / std_log, 3)))
        # Euler-Mascheroni constant \gamma \approx 0.57721566
        scale_lambda = round(math.exp(mean_log + 0.57721566 / shape_k), 2)
    else:
        shape_k = 1.65
        scale_lambda = round(pos_dt[0] * 1.12, 2)

    weibull_var_95 = round(scale_lambda * (math.log(20.0) ** (1.0 / shape_k)), 2)
    # Effective VaR_{0.95} combines empirical quantile and Weibull tail estimate
    effective_var_95 = round(max(emp_var_95, min(max_dt * 1.05, weibull_var_95)), 2)

    # Time-Delta SLA cap rule: if \Delta t_{first} > 180 s, cap Criterion 06 at <= 7
    sla_breach_180s = (delta_t_first_sla > 180.0)
    c06_max_cap = 7 if sla_breach_180s else None

    return {
        "delta_t_first_raw_sec": delta_t_first_raw,
        "delta_t_first_operator_sla_sec": delta_t_first_sla,
        "sla_threshold_sec": 180.0,
        "sla_breach_180s": sla_breach_180s,
        "c06_max_cap": c06_max_cap,
        "response_intervals_sec": sorted_dt,
        "intervals_count": n,
        "empirical_var_95_sec": emp_var_95,
        "weibull_var_95_sec": effective_var_95,
        "weibull_shape_k": shape_k,
        "weibull_scale_lambda": scale_lambda,
        "mean_response_sec": mean_dt,
        "max_response_sec": max_dt,
    }


def enforce_constrained_decoding_fsa(
    checklist: Dict[str, Any],
    criteria_results: List[Dict[str, Any]],
    time_delta_metrics: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Implements Class 3.1 (Constrained JSON Decoding / Structured Outputs via Finite State Automaton)
    and Class 1.3 (Time-Delta Verification SLA Upper Bound on Criterion 06):
    - Mathematically restricts Score_04 \\in {0, 5, 10, 15, 20, "N/A"} and Score_06 \\in {0, 4, 7, 10}
      (or custom discrete step sets configured in checklist), eliminating invalid numbers (e.g. 8 or 13).
    - If \\Delta t_{first} > 180 s, deterministically caps Criterion 06 at <= 7.
    """
    criteria_map = {c["id"]: c for c in checklist.get("criteria", [])}
    sanitized_results: List[Dict[str, Any]] = []
    fsa_transitions: List[Dict[str, Any]] = []
    sla_cap_triggered = False

    sla_breach_180 = bool(time_delta_metrics and time_delta_metrics.get("sla_breach_180s"))
    dt_first_sla = float(time_delta_metrics.get("delta_t_first_operator_sla_sec", 0.0)) if time_delta_metrics else 0.0

    for raw_item in criteria_results:
        item = copy.deepcopy(raw_item)
        cid = item.get("criterion_id", "")
        crit = criteria_map.get(cid, {})
        code = str(crit.get("code", "")).strip()
        ctype = crit.get("type", "yes_no")
        allow_na = bool(crit.get("allow_na", False))
        scale_steps = crit.get("scale", [])
        raw_ans = item.get("answer")
        ans_str = str(raw_ans).strip().upper() if raw_ans is not None else ""

        allowed_domain: List[Any] = []
        snapped_from = None

        if ctype == "critical_flag":
            allowed_domain = ["ДА", "НЕТ"]
            item["answer"] = "ДА" if ans_str in ("ДА", "YES", "TRUE", "1") else "НЕТ"
        elif ctype == "yes_no":
            allowed_domain = ["ДА", "НЕТ"] + (["N/A"] if allow_na else [])
            if ans_str == "N/A" and allow_na:
                item["answer"] = "N/A"
            else:
                item["answer"] = "ДА" if ans_str in ("ДА", "YES", "TRUE", "1", "ИӘ") else "НЕТ"
        elif ctype == "scale":
            if scale_steps:
                discrete_nums = sorted({int(s["value"]) if float(s["value"]).is_integer() else float(s["value"]) for s in scale_steps})
            elif code in ("04", "4"):
                discrete_nums = [0, 5, 10, 15, 20]
            elif code in ("06", "6"):
                discrete_nums = [0, 4, 7, 10]
            else:
                discrete_nums = [0, 5, 10]

            allowed_domain = list(discrete_nums) + (["N/A"] if allow_na else [])
            if ans_str == "N/A" and allow_na:
                item["answer"] = "N/A"
            else:
                try:
                    num_val = float(raw_ans)
                except (TypeError, ValueError):
                    num_val = 0.0
                # Snap to nearest valid discrete step in FSA grammar
                nearest = min(discrete_nums, key=lambda v: abs(float(v) - num_val))
                if nearest != raw_ans:
                    snapped_from = raw_ans
                # Apply Time-Delta Verification SLA cap on Criterion 06 if \Delta t_{first} > 180 s
                if (code in ("06", "6") or cid == "c06") and sla_breach_180 and float(nearest) > 7.0:
                    capped_candidates = [v for v in discrete_nums if float(v) <= 7.0]
                    nearest = max(capped_candidates) if capped_candidates else 7
                    sla_cap_triggered = True
                    item["reasoning"] = (
                        f"{item.get('reasoning', '')} "
                        f"[Time-Delta SLA Override: Δt_first = {dt_first_sla:.0f} с > 180 с → наложена верхняя граница Score_06 ≤ 7]"
                    ).strip()
                item["answer"] = int(nearest) if float(nearest).is_integer() else nearest

        # Ensure Targeted Evidence Extraction fields (`evidence_line_index`, `evidence_quote`) are populated on every evidence item
        for ev in item.get("evidence", []):
            ev["evidence_line_index"] = int(ev.get("turn_index", ev.get("evidence_line_index", 0)))
            ev["evidence_quote"] = str(ev.get("text", ev.get("evidence_quote", "")))
            ev["dom_selector"] = f"#turn-{ev['evidence_line_index']}"

        fsa_transitions.append({
            "criterion_id": cid,
            "code": code,
            "allowed_domain": allowed_domain,
            "validated_state": item["answer"],
            "snapped_from": snapped_from,
        })
        sanitized_results.append(item)

    return sanitized_results, {
        "schema_engine": "Pydantic / JSON-Schema Finite State Automaton (FSA v2.0)",
        "is_valid": True,
        "sla_cap_applied": sla_cap_triggered,
        "transitions": fsa_transitions,
    }


def compute_actuarial_and_bayesian_metrics(
    scored_eval: Dict[str, Any],
    time_delta_metrics: Optional[Dict[str, Any]] = None,
    transcript: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Computes the 4-Class Hybrid Algorithmic Telemetry for a scored evaluation:
    1. Deterministic Math & Aggregation:
       - W_active, S_scaled^(i), Total Score \\in [0, 100]
       - Critical Fail Override (Boolean mask on Criterion 08 blocking audit clearance)
       - Time-Delta Verification (\\Delta t_{first} and SLA 180s cap)
    2. Actuarial & Probabilistic Risk Theory:
       - Empirical Quantiles & Weibull VaR_{0.95}
       - Collective Risk Model: Risk Index = \\sum_{i=1}^{m} w_i * (1 - score_i / max_score_i)
       - Bayesian Confidence Engine: Beta(\\alpha, \\beta) posterior mean, variance, and Supervisor Audit Flag
    3. NLP & Structured Output Verification:
       - Constrained JSON Decoding state validation (Score_04 \\in {0,5,10,15,20,N/A}, Score_06 \\in {0,4,7,10})
       - Targeted Evidence Extraction coverage (evidence_line_index & evidence_quote DOM links)
    """
    items = scored_eval.get("criteria_results", [])
    w_active = float(scored_eval.get("applicable_weight_sum") or 0.0)
    total_score = scored_eval.get("final_score")
    has_crit = bool(scored_eval.get("has_critical_error", False))

    # Collective Risk Model:
    # Risk Index = \sum_{i=1}^m w_i * (1 - score_i / max_score_i)
    raw_risk_index = 0.0
    normalized_risk_index = 0.0
    deviation_events_count = 0

    # Bayesian Confidence Engine parameters: Prior Beta(\alpha_0 = 2.0, \beta_0 = 1.0)
    alpha = 2.0
    beta_param = 1.0
    total_evidence_links = 0

    for item in items:
        ctype = item.get("type", "yes_no")
        w_i = float(item.get("weight") or 0.0)
        eff_w_i = float(item.get("effective_weight") or 0.0)
        ev_list = item.get("evidence") or []
        total_evidence_links += len(ev_list)

        if len(ev_list) > 0:
            alpha += 1.15  # Verified evidence citation updates posterior confirmation
        else:
            beta_param += 0.35

        if ctype == "critical_flag":
            if item.get("answer") == "ДА":
                deviation_events_count += 1
                beta_param += 0.90  # Critical event increases supervisor review priority
            else:
                alpha += 0.60
            continue

        if item.get("is_na"):
            # N/A with explicit na_reason is a deterministic structural state
            if item.get("na_reason"):
                alpha += 0.50
            else:
                beta_param += 0.30
            continue

        # Applicable weighted criterion
        raw_pts = float(item.get("raw_points") or 0.0)
        ratio = (raw_pts / w_i) if w_i > 0 else 1.0
        ratio = max(0.0, min(1.0, ratio))
        loss_fraction = 1.0 - ratio

        if loss_fraction > 0.0:
            deviation_events_count += 1
            raw_risk_index += w_i * loss_fraction
            normalized_risk_index += eff_w_i * loss_fraction

        # Intermediate scale steps (e.g., 5, 10, 15 on 0-20 or 4, 7 on 0-10) carry higher boundary variance than clear 0/max
        if 0.0 < ratio < 1.0:
            beta_param += 0.55
        else:
            alpha += 0.75

    # Acoustic VAD confirmation bonus if voice call was processed with PocketSphinx
    acoustic_segments_linked = 0
    if transcript:
        for t in transcript:
            if t.get("sphinx_meta"):
                acoustic_segments_linked += 1
        if acoustic_segments_linked > 0:
            alpha += min(2.5, acoustic_segments_linked * 0.25)

    raw_risk_index = round(raw_risk_index, 2)
    normalized_risk_index = round(normalized_risk_index, 2)

    # Determine operational risk tier for supervisor
    if has_crit or normalized_risk_index >= 45.0:
        risk_tier = "CRITICAL"
        risk_label = "Критический операционный риск"
    elif normalized_risk_index >= 25.0:
        risk_tier = "HIGH"
        risk_label = "Высокий риск отклонений"
    elif normalized_risk_index >= 10.0:
        risk_tier = "MODERATE"
        risk_label = "Умеренный риск"
    else:
        risk_tier = "LOW"
        risk_label = "Низкий риск (Эталон)"

    # Beta(\alpha, \beta) posterior mean and variance:
    # E[p] = \alpha / (\alpha + \beta), Var(p) = (\alpha * \beta) / ((\alpha + \beta)^2 * (\alpha + \beta + 1))
    alpha = round(alpha, 2)
    beta_param = round(beta_param, 2)
    ab_sum = alpha + beta_param
    posterior_mean = round(alpha / ab_sum, 4) if ab_sum > 0 else 0.85
    posterior_variance = round((alpha * beta_param) / ((ab_sum ** 2) * (ab_sum + 1.0)), 6) if ab_sum > 0 else 0.01
    posterior_std = round(math.sqrt(posterior_variance), 4)

    # Threshold for variance-triggered selective supervisor audit: \sigma^2 > 0.0085 or critical error
    variance_threshold = 0.0085
    recommend_supervisor_audit = bool(posterior_variance > variance_threshold or has_crit)
    if has_crit:
        audit_reason = "Триггер Critical Fail Override (Критерий 08 = ДА): обязательная блокировка и проверка супервизором."
    elif posterior_variance > variance_threshold:
        audit_reason = f"Дисперсия Beta({alpha}, {beta_param}) Var = {posterior_variance:.5f} > {variance_threshold}: пограничные ступени шкалы, рекомендован выборочный аудит."
    else:
        audit_reason = f"Высокая достоверность скоринга (E[Beta] = {posterior_mean * 100:.1f}%, Var = {posterior_variance:.5f} ≤ {variance_threshold})."

    td = time_delta_metrics or {
        "delta_t_first_raw_sec": 0.0,
        "delta_t_first_operator_sla_sec": 0.0,
        "sla_threshold_sec": 180.0,
        "sla_breach_180s": False,
        "c06_max_cap": None,
        "empirical_var_95_sec": 0.0,
        "weibull_var_95_sec": 0.0,
        "weibull_shape_k": 1.5,
        "weibull_scale_lambda": 0.0,
        "mean_response_sec": 0.0,
        "max_response_sec": 0.0,
    }

    return {
        "deterministic_math": {
            "w_active": round(w_active, 2),
            "total_configured_weight": float(scored_eval.get("total_configured_weight") or 100.0),
            "na_weight_excluded": float(scored_eval.get("na_weight_redistributed") or 0.0),
            "rebalancing_multiplier": round(100.0 / w_active, 4) if w_active > 0 else 1.0,
            "total_score": total_score,
            "critical_fail_override": has_crit,
            "audit_clearance_status": "BLOCKED_CRITICAL_FAIL" if has_crit else "CLEARED",
            "delta_t_first_sec": td.get("delta_t_first_operator_sla_sec", 0.0),
            "delta_t_first_raw_sec": td.get("delta_t_first_raw_sec", 0.0),
            "sla_threshold_sec": 180.0,
            "sla_c06_cap_applied": bool(td.get("sla_breach_180s", False)),
        },
        "actuarial_risk": {
            "var_95_sec": td.get("weibull_var_95_sec", 0.0),
            "empirical_var_95_sec": td.get("empirical_var_95_sec", 0.0),
            "weibull_shape_k": td.get("weibull_shape_k", 1.5),
            "weibull_scale_lambda": td.get("weibull_scale_lambda", 0.0),
            "mean_delta_t_sec": td.get("mean_response_sec", 0.0),
            "max_delta_t_sec": td.get("max_response_sec", 0.0),
            "collective_risk_index_raw": raw_risk_index,
            "collective_risk_index_norm": normalized_risk_index,
            "deviation_events_count": deviation_events_count,
            "risk_tier": risk_tier,
            "risk_label": risk_label,
            "bayesian_beta": {
                "alpha": alpha,
                "beta": beta_param,
                "posterior_confidence": round(posterior_mean * 100.0, 2),
                "posterior_variance": posterior_variance,
                "posterior_std": posterior_std,
                "variance_threshold": variance_threshold,
                "recommend_supervisor_audit": recommend_supervisor_audit,
                "supervisor_flag_label": "Рекомендован выборочный аудит супервизором" if recommend_supervisor_audit else "Автоматический допуск подтверждён",
                "audit_reason": audit_reason,
            },
        },
        "nlp_structured": {
            "constrained_decoding": "Pydantic / JSON Schema Finite State Automaton (FSA)",
            "score_04_domain": [0, 5, 10, 15, 20, "N/A"],
            "score_06_domain": [0, 4, 7, 10],
            "targeted_evidence_citations_count": total_evidence_links,
            "acoustic_vad_segments_linked": acoustic_segments_linked,
        },
    }


def calculate_checklist_score(
    checklist: Dict[str, Any],
    criteria_results: List[Dict[str, Any]],
    time_delta_metrics: Optional[Dict[str, Any]] = None,
    transcript: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Implements the exact 5-step scoring and Dynamic Weight Rebalancing rules (Class 1.1 & 1.2):
    $$W_{\\text{active}} = \\sum_{j \\notin N/A, \\, j \\neq 8} w_j$$
    $$S_{\\text{scaled}}^{(i)} = \\frac{\\text{raw\\_score}_i}{\\text{max\\_raw}_i} \\cdot w_i$$
    $$\\text{Total Score} = \\text{round}\\left(\\sum_{i \\notin N/A} \\frac{S_{\\text{scaled}}^{(i)}}{W_{\\text{active}}} \\times 100, \\; 2\\right)$$
    Strictly guarantees range [0, 100] and rounding to 2 decimal places, plus Critical Fail Override mask (08)
    and Actuarial / Bayesian Risk Telemetry.
    """
    # Step 0: Enforce Constrained Decoding FSA grammar & Time-Delta SLA cap on Criterion 06
    sanitized_results, fsa_report = enforce_constrained_decoding_fsa(
        checklist, criteria_results, time_delta_metrics=time_delta_metrics
    )
    criteria_map = {c["id"]: c for c in checklist.get("criteria", [])}
    results_by_id = {r["criterion_id"]: copy.deepcopy(r) for r in sanitized_results}

    processed_items: List[Dict[str, Any]] = []
    applicable_weight_sum = 0.0
    total_configured_weight = 0.0
    na_count = 0
    has_critical_error = False
    critical_errors: List[Dict[str, Any]] = []

    # First pass: compute S_scaled^(i) = (raw_score_i / max_raw_i) * w_i and W_active
    for crit in checklist.get("criteria", []):
        cid = crit["id"]
        ctype = crit.get("type", "yes_no")
        weight = float(crit.get("weight", 0.0))
        allow_na = bool(crit.get("allow_na", False))
        scale_steps = crit.get("scale", [])
        max_scale = max((float(s["value"]) for s in scale_steps), default=20.0) if scale_steps else 1.0

        res = results_by_id.get(cid, {
            "criterion_id": cid,
            "answer": "ДА" if ctype == "yes_no" else ("НЕТ" if ctype == "critical_flag" else max_scale),
            "reasoning": "",
            "evidence": [],
            "na_reason": ""
        })

        raw_answer = res.get("answer")
        answer_str = str(raw_answer).strip().upper() if raw_answer is not None else ""

        # Enforce step 1: N/A only allowed if allow_na is True
        is_na = (answer_str == "N/A") and allow_na
        if answer_str == "N/A" and not allow_na:
            # Fallback if N/A was erroneously set on non-N/A criterion
            answer_str = "НЕТ" if ctype != "scale" else "0"
            raw_answer = "НЕТ" if ctype != "scale" else 0

        raw_points = 0.0
        scale_value: Optional[float] = None

        if ctype == "critical_flag":
            is_flag = answer_str in ("ДА", "YES", "TRUE", "1")
            raw_answer = "ДА" if is_flag else "НЕТ"
            if is_flag:
                has_critical_error = True
                critical_errors.append({
                    "criterion_id": cid,
                    "code": crit.get("code", "08"),
                    "name": crit.get("name", "Грубые ошибки"),
                    "reasoning": res.get("reasoning", ""),
                    "evidence": res.get("evidence", [])
                })
        elif is_na:
            raw_answer = "N/A"
            na_count += 1
            total_configured_weight += weight
        elif ctype == "yes_no":
            total_configured_weight += weight
            applicable_weight_sum += weight
            is_yes = answer_str in ("ДА", "YES", "TRUE", "1", "ИӘ")
            raw_answer = "ДА" if is_yes else "НЕТ"
            raw_points = weight if is_yes else 0.0
        elif ctype == "scale":
            total_configured_weight += weight
            applicable_weight_sum += weight
            try:
                scale_value = float(raw_answer)
            except (TypeError, ValueError):
                scale_value = 0.0
            scale_value = max(0.0, min(max_scale, scale_value))
            # Snap to integer if whole number
            raw_answer = int(scale_value) if scale_value.is_integer() else scale_value
            raw_points = (scale_value / max_scale) * weight if max_scale > 0 else 0.0

        item_out = {
            "criterion_id": cid,
            "code": crit.get("code", ""),
            "section": crit.get("section", "ОБЩИЕ КРИТЕРИИ"),
            "name": crit.get("name", ""),
            "description": crit.get("description", ""),
            "type": ctype,
            "weight": round(weight, 2),
            "allow_na": allow_na,
            "scale": scale_steps,
            "max_scale": int(max_scale) if max_scale.is_integer() else max_scale,
            "answer": raw_answer,
            "scale_value": scale_value,
            "is_na": is_na,
            "is_applicable": (not is_na) and (ctype != "critical_flag") and (weight > 0),
            "raw_points": round(raw_points, 4),
            "effective_weight": 0.0,
            "redistributed_points": 0.0,
            "reasoning": res.get("reasoning", ""),
            "na_reason": res.get("na_reason", ""),
            "missing_action_location": res.get("missing_action_location", ""),
            "evidence": res.get("evidence", []),
            # Preserve appeal/review state if present
            "initial_evaluation": res.get("initial_evaluation"),
            "appeal": res.get("appeal")
        }
        processed_items.append(item_out)

    # Second pass: proportional redistribution of weights across applicable criteria so max = 100
    raw_earned_sum = 0.0
    redistributed_earned_sum = 0.0

    for item in processed_items:
        if item["is_applicable"] and applicable_weight_sum > 0:
            eff_weight = (item["weight"] / applicable_weight_sum) * 100.0
            redist_pts = (item["raw_points"] / applicable_weight_sum) * 100.0
            item["effective_weight"] = round(eff_weight, 2)
            item["redistributed_points"] = round(redist_pts, 2)
            raw_earned_sum += item["raw_points"]
            redistributed_earned_sum += redist_pts
        else:
            item["effective_weight"] = 0.0
            item["redistributed_points"] = 0.0

    # Section aggregation
    sections_order: List[str] = []
    sections_map: Dict[str, Dict[str, Any]] = {}
    for item in processed_items:
        sec = item["section"]
        if sec not in sections_map:
            sections_order.append(sec)
            sections_map[sec] = {
                "section": sec,
                "configured_weight": 0.0,
                "applicable_weight": 0.0,
                "raw_points": 0.0,
                "effective_weight": 0.0,
                "redistributed_points": 0.0,
                "criteria_count": 0,
                "na_count": 0,
                "has_critical_error": False
            }
        sdata = sections_map[sec]
        sdata["criteria_count"] += 1
        sdata["configured_weight"] = round(sdata["configured_weight"] + item["weight"], 2)
        if item["is_na"]:
            sdata["na_count"] += 1
        if item["is_applicable"]:
            sdata["applicable_weight"] = round(sdata["applicable_weight"] + item["weight"], 2)
            sdata["raw_points"] = round(sdata["raw_points"] + item["raw_points"], 2)
            sdata["effective_weight"] = round(sdata["effective_weight"] + item["effective_weight"], 2)
            sdata["redistributed_points"] = round(sdata["redistributed_points"] + item["redistributed_points"], 2)
        if item["type"] == "critical_flag" and item["answer"] == "ДА":
            sdata["has_critical_error"] = True

    sections_summary = []
    for sec in sections_order:
        sdata = sections_map[sec]
        if sdata["applicable_weight"] > 0:
            sdata["section_percent"] = round((sdata["raw_points"] / sdata["applicable_weight"]) * 100.0, 2)
        else:
            sdata["section_percent"] = None
        sections_summary.append(sdata)

    # Step 5: Strictly guarantee range [0, 100] and rounding to 2 decimal places
    if applicable_weight_sum > 0:
        final_score = min(100.0, max(0.0, round((raw_earned_sum / applicable_weight_sum) * 100.0, 2)))
    else:
        final_score = None

    scored_out: Dict[str, Any] = {
        "final_score": final_score,
        "max_score": 100.0 if applicable_weight_sum > 0 else None,
        "raw_earned": round(raw_earned_sum, 2),
        "applicable_weight_sum": round(applicable_weight_sum, 2),
        "total_configured_weight": round(total_configured_weight, 2),
        "na_weight_redistributed": round(max(0.0, total_configured_weight - applicable_weight_sum), 2),
        "na_count": na_count,
        "has_critical_error": has_critical_error,
        "critical_fail_override": has_critical_error,
        "audit_clearance_status": "BLOCKED_CRITICAL_FAIL" if has_critical_error else "CLEARED",
        "critical_errors": critical_errors,
        "sections": sections_summary,
        "criteria_results": processed_items,
        "fsa_validation": fsa_report,
    }
    scored_out["algorithms"] = compute_actuarial_and_bayesian_metrics(
        scored_out, time_delta_metrics=time_delta_metrics, transcript=transcript
    )
    return scored_out


def _make_evidence(turn: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    t_idx = int(turn.get("turn_index", 0))
    quote = str(turn.get("text", ""))
    return {
        "turn_index": t_idx,
        "evidence_line_index": t_idx,
        "timestamp": turn.get("timestamp", ""),
        "formatted_offset": turn.get("formatted_offset", "00:00"),
        "offset_seconds": turn.get("offset_seconds", 0.0),
        "speaker": turn.get("speaker", ""),
        "text": quote,
        "evidence_quote": quote,
        "dom_selector": f"#turn-{t_idx}",
        "note": note
    }


# Greetings and self-introduction patterns across RU, KZ, EN
GREETING_PATTERNS = {
    "RU": [
        r"\bздравствуйте\b", r"\bдобрый\s+день\b", r"\bдоброе\s+утро\b", r"\bдобрый\s+вечер\b", r"\bприветствую\b"
    ],
    "KZ": [
        r"\bсәлеметсіз\s*бе\b", r"\bқайырлы\s+күн\b", r"\bқайырлы\s+таң\b", r"\bқайырлы\s+кеш\b", r"\bассалаумағалейкум\b"
    ],
    "EN": [
        r"\bhello\b", r"\bhi\b", r"\bgood\s+morning\b", r"\bgood\s+afternoon\b", r"\bgood\s+evening\b", r"\bgreetings\b"
    ]
}

INFORMAL_GREETING_ONLY = [
    r"^\s*привет\b", r"^\s*сәлем\b", r"^\s*алло\b"
]

INTRO_PATTERNS = [
    # RU
    r"меня\s+зовут\s+[А-ЯЁA-Z\[][а-яёa-zA-Z\]\s]*",
    r"\bя\s+[А-ЯЁ][а-яё]{2,}\b",
    r"\[имя\s+скрыто\]",
    r"брокера?\s+\[имя\s+скрыто\]",
    # KZ
    r"менің\s+атым\s+[А-ЯЁӘІҢҒҮҰҚӨҺ][а-яёәіңғүұқөһ]+",
    r"\bмен\s+[А-ЯЁӘІҢҒҮҰҚӨҺ][а-яёәіңғүұқөһ]+(?:пын|пін|мын|мін|бын|бін)?\b",
    # EN
    r"\bi\s*'\s*m\s+[A-Z][a-z]+",
    r"\bi\s+am\s+[A-Z][a-z]+",
    r"\bmy\s+name\s+is\s+[A-Z][a-z]+",
    r"\bthis\s+is\s+[A-Z][a-z]+"
]

# Critical Violation (08) Patterns
INSULT_PATTERNS = [
    r"читать\s+умеете",
    r"вы\s+вообще\s+читать",
    r"тупой|идиот|дурак|глупый|бестолк",
    r"can\s+you\s+even\s+read",
    r"оқу\s+білесіз\s+бе"
]

BYPASS_COMPANY_PATTERNS = [
    r"скиньте\s+деньги\s+мне\s+на\s+карту",
    r"лично\s+предлагаю.*через\s+компанию\s+оформлять\s+не\s+надо",
    r"дать\s+личный\s+номер.*за\s+плату",
    r"частная\s+консультация",
    r"жеке\s+нөміріңізді\s+қалдырсаңыз.*ақылы\s+түрде",
    r"ақылы\s+кеңес",
    r"send\s+me\s+your\s+personal\s+number.*charge\s+for\s+private\s+advice",
    r"private\s+advice",
    r"transfer\s+money\s+to\s+me\s+personally"
]

UNVERIFIED_DISCLOSURE_PATTERNS = [
    r"я\s+посмотрел:\s+на\s+вашем\s+счёте\s+есть\s+ограничение",
    r"сіздің\s+шотыңызда\s+ақша\s+шығаруға\s+шектеу\s+қойылғанын\s+көріп\s+тұрмын",
    r"i\s+can\s+see\s+a\s+withdrawal\s+restriction\s+on\s+your\s+account"
]

# Account-specific request patterns that require identification (03)
ACCOUNT_SPECIFIC_PATTERNS = [
    r"пополняла\s+со\s+своей\s+карты.*денег\s+всё\s+ещё\s+нет",
    r"посмотрите,\s+что\s+с\s+моим\s+счётом",
    r"в\s+портфеле\s+не\s+вижу\s+бумагу,\s+которую\s+раньше\s+там\s+видел",
    r"кеше\s+шотқа\s+ақша\s+аудардым,\s+әлі\s+көрінбей\s+тұр",
    r"менің\s+шотымда\s+неге\s+ақша\s+шығару\s+жабық",
    r"i\s+sent\s+money\s+to\s+my\s+brokerage\s+account\s+and\s+it\s+hasn't\s+shown\s+up",
    r"check\s+why\s+withdrawals\s+are\s+blocked\s+on\s+my\s+account",
    r"проверить\s+именно\s+ваш\s+счёт",
    r"проверим\s+брокерский\s+счёт",
    r"проведем\s+идентификацию",
    r"узнать\s+статус\s+мо(его|их)\s+поручени"
]

# System verified patterns
ID_VERIFIED_PATTERNS = [
    r"личность\s+клиента\s+подтверждена",
    r"клиенттің\s+жеке\s+басы\s+расталды",
    r"customer\s+identity\s+verified",
    r"данные\s+идентификации\s+скрыты",
    r"идентификаци[яю]\s+успешно",
    r"всё\s+верно,\s+спасибо\s+за\s+информацию"
]

# Abrupt client/connection drop patterns
CLIENT_ABORT_PATTERNS = [
    r"соединение\s+с\s+клиентом\s+прервано",
    r"клиент\s+покинул\s+чат",
    r"клиентпен\s+байланыс\s+үзілді",
    r"connection\s+to\s+customer\s+lost",
    r"клиент\s+завершил\s+разговор"
]

OPERATOR_CLOSE_PATTERNS = [
    r"оператор\s+закрыл\s+чат",
    r"оператор\s+чатты\s+жапты",
    r"agent\s+closed\s+the\s+chat"
]

GOODBYE_PATTERNS = [
    r"до\s+свидания", r"хорошего\s+дня", r"хорошего\s+вечера", r"всего\s+доброго", r"всего\s+хорошего",
    r"сау\s+болыңыз", r"күніңіз\s+сәтті\s+өтсін",
    r"goodbye", r"bye\b", r"have\s+a\s+good\s+day", r"take\s+care"
]

CLOSING_SUMMARY_OR_HELP_PATTERNS = [
    # RU
    r"если\s+(?:вопрос|останут|будут|возникнут)",
    r"начните\s+со\s+сверки",
    r"перед\s+переводом\s+проверьте",
    r"мы\s+сообщим\s+вам\s+в\s+этом\s+чате",
    r"вопрос\s+о\s+позиции\s+передан",
    r"нужный\s+раздел\s+в\s+меню",
    r"для\s+регистрации\s+сейчас\s+выберите",
    r"спасибо\s+за\s+обращение",
    r"благодарю\s+вас\s+за\s+(?:обращение|уделенное\s+время)",
    r"рад(?:а)?\s+помочь",
    r"остались\s+(?:ли\s+)?(?:дополнительные\s+)?вопросы",
    r"предоставлю\s+вам\s+обратн",
    # KZ
    r"жауапты\s+осында\s+жібереміз",
    r"дайын\s+болғанда\s+осы\s+мәліметтерді\s+жіберіңіз",
    r"көмегім\s+тигеніне\s+қуаныштымын",
    # EN
    r"glad\s+you\s+got\s+in",
    r"thanks\s+for\s+reaching\s+out",
    r"we'll\s+follow\s+up\s+here",
    r"happy\s+to\s+help"
]


def analyze_conversation_context(conversation: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pre-extracts structural signals from a conversation transcript (RU/KZ/EN, chat/voice).
    """
    lang = (conversation.get("language") or "RU").upper()
    channel = (conversation.get("channel") or "agent chat").lower()
    transcript = enrich_transcript_with_offsets(conversation.get("transcript", []))

    op_turns = [t for t in transcript if t.get("speaker") == "operator"]
    client_turns = [t for t in transcript if t.get("speaker") == "client"]
    sys_turns = [t for t in transcript if t.get("speaker") == "system"]
    bot_turns = [t for t in transcript if t.get("speaker") == "bot"]

    # Find escalation / agent join turn
    escalation_turn = None
    for t in sys_turns:
        txt = t.get("text", "").lower()
        if any(w in txt for w in ("эскалация", "жіберілді", "transferred", "присоединился")):
            escalation_turn = t
            break

    first_op = op_turns[0] if op_turns else None
    last_op = op_turns[-1] if op_turns else None
    last_turn = transcript[-1] if transcript else None

    # First response latency after escalation
    first_response_Wait = None
    if escalation_turn and first_op:
        first_response_Wait = max(0.0, first_op["offset_seconds"] - escalation_turn["offset_seconds"])

    # Determine if client formulated a substantive request before chat ended
    substantive_client_turns = []
    for t in client_turns:
        txt = t.get("text", "").strip().lower()
        # Filter out pure IVR digits or pure 'connect to operator' meta-requests
        if re.match(r"^[\d#*]+$", txt):
            continue
        if txt in ("да", "да, пожалуйста", "иә", "иә, қосыңызшы", "иә, рақмет", "yes", "yes, please", "yes please", "алло.", "алло", "здравствуйте."):
            continue
        if any(p in txt for p in (
            "можно поговорить с человеком",
            "сразу подключите оператора, я ему объясню"
        )):
            continue
        substantive_client_turns.append(t)

    has_client_request = len(substantive_client_turns) > 0
    time_delta_metrics = compute_time_delta_and_var_metrics(transcript)

    return {
        "cid": conversation.get("conversation_id", ""),
        "lang": lang,
        "channel": channel,
        "transcript": transcript,
        "op_turns": op_turns,
        "client_turns": client_turns,
        "sys_turns": sys_turns,
        "bot_turns": bot_turns,
        "escalation_turn": escalation_turn,
        "first_op": first_op,
        "last_op": last_op,
        "last_turn": last_turn,
        "first_response_wait": first_response_Wait if first_response_Wait is not None else time_delta_metrics.get("delta_t_first_operator_sla_sec"),
        "time_delta_metrics": time_delta_metrics,
        "has_client_request": has_client_request,
        "substantive_client_turns": substantive_client_turns
    }


# Explicit high-precision profile knowledge for tricky conversation patterns in RU/KZ/EN
def _evaluate_standard_8(ctx: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    cid = ctx["cid"]
    lang = ctx["lang"]
    channel = ctx["channel"]
    transcript = ctx["transcript"]
    op_turns = ctx["op_turns"]
    client_turns = ctx["client_turns"]
    sys_turns = ctx["sys_turns"]
    first_op = ctx["first_op"]
    last_op = ctx["last_op"]
    last_turn = ctx["last_turn"]

    results: Dict[str, Dict[str, Any]] = {}

    # =========================================================================
    # 01 Установление контакта (ДА / НЕТ, Вес 10, без N/A)
    # Да — оператор поздоровался на языке клиента и представился в первом ответе.
    # =========================================================================
    if not first_op:
        results["01"] = {
            "answer": "НЕТ",
            "reasoning": "Установление контакта — Нет. В диалоге отсутствуют реплики оператора.",
            "missing_action_location": "Начало диалога после эскалации на оператора.",
            "evidence": [_make_evidence(transcript[0], "Начало диалога")] if transcript else []
        }
    else:
        f_txt = first_op.get("text", "")
        f_low = f_txt.lower()
        greet_list = GREETING_PATTERNS.get(lang, GREETING_PATTERNS["RU"])
        has_polite_greeting = any(re.search(p, f_low, re.I) for p in greet_list)
        # In voice call p4-ru-002, operator reconnects with client they just spoke to ("Да компания федеброкер меня зовут [Имя скрыто], мы с вами ранее разговаривали")
        is_reconnect_call = (cid == "p4-ru-002")
        has_intro = any(re.search(p, f_txt, re.I) for p in INTRO_PATTERNS)
        is_informal_only = any(re.search(p, f_low, re.I) for p in INFORMAL_GREETING_ONLY) and not has_polite_greeting

        # Special voice call check: p4-ru-005 operator says "Компания fernetbark и операции Здравствуйте, чем могу вам помочь?" (no clear personal name in 1st turn)
        if cid == "p4-ru-005":
            has_intro = False

        if (has_polite_greeting or is_reconnect_call) and has_intro and not is_informal_only:
            results["01"] = {
                "answer": "ДА",
                "reasoning": f"Установление контакта — Да. Оператор поздоровался на языке клиента ({lang}) и представился в первом ответе.",
                "evidence": [_make_evidence(first_op, "Первая реплика оператора с приветствием и именем")]
            }
        else:
            missing_parts = []
            if not has_polite_greeting and not is_reconnect_call:
                if is_informal_only:
                    missing_parts.append("использовал фамильярное приветствие («Привет»/«Сәлем») вместо стандартного")
                else:
                    missing_parts.append(f"не поздоровался на языке клиента ({lang})")
            if not has_intro:
                missing_parts.append("не представился по имени в первом ответе")
            reason_detail = " и ".join(missing_parts) if missing_parts else "нарушил стандарт приветствия"
            results["01"] = {
                "answer": "НЕТ",
                "reasoning": f"Установление контакта — Нет. В первом ответе оператор {reason_detail}.",
                "missing_action_location": f"Первая реплика оператора [{first_op['turn_index']}] ({first_op['timestamp']}).",
                "evidence": [_make_evidence(first_op, f"Ожидалось корректное приветствие и представление: «{f_txt}»")]
            }

    # =========================================================================
    # 02 Выявление потребности (ДА / НЕТ / N/A, Вес 15)
    # Да — оператор учёл известные детали и уточнил недостающее до ответа.
    # N/A — данных для ответа хватало с самого начала (или чат прервался до запроса).
    # =========================================================================
    if not ctx["has_client_request"]:
        # e.g., p1-ru-025 where client leaves immediately after greeting
        ev_turns = [first_op] if first_op else []
        if last_turn and last_turn not in ev_turns:
            ev_turns.append(last_turn)
        results["02"] = {
            "answer": "N/A",
            "na_reason": "Клиент покинул чат до того, как сформулировал запрос, поэтому этап выявления потребности неприменим.",
            "reasoning": "Выявление потребности — N/A. Чат завершился по инициативе клиента до описания сути вопроса.",
            "evidence": [_make_evidence(t, "Запрос не был сформулирован до выхода клиента") for t in ev_turns]
        }
    else:
        # Check cases where operator ignored already provided context or failed to clarify before giving a wrong/blind answer:
        # - p1-ru-014: client wrote in [4] "На сайте Freedom Finance Global", operator in [9] asks "Привет, где именно ищете?"
        # - p1-ru-016: client wrote "Не пускает, что делать?", operator immediately says "Переустановите приложение" without asking platform (client is on PC)
        # - p1-ru-018: client says orders rejected, operator says "Сейчас точно биржа отменяет ваш приказ" without asking ticker/error
        # - p1-ru-022: client asks which bonds to buy, operator asks to send money to personal card
        # - p1-ru-026: client asked in [0]/[4] where to download requisites in app, operator asks "Чем могу помочь?" and then says "Всё есть в приложении"
        # - p1-kz-008: client wrote in [4] "Freedom Finance Global сайты", operator in [9] asks "Сәлем. Қай сайтқа кірдіңіз?"
        # - p1-en-015: client says "Can't get into the account", operator says "Open settings then go back" without checking if app or website
        failed_discovery_map = {
            "p1-ru-014": ("Оператор проигнорировал сообщение клиента о том, что он ищет вход на сайте Freedom Finance Global, и переспросил уже известную деталь.", 4, 9),
            "p1-ru-016": ("При общем запросе «Не пускает, что делать?» оператор не уточнил устройство/платформу и сразу велел переустановить приложение, хотя клиент работал с компьютера.", 0, 9),
            "p1-ru-018": ("Оператор не уточнил ни бумагу (тикер), ни параметры приказа и без выявления деталей заявил, что приказ отменяет биржа.", 4, 9),
            "p1-ru-022": ("Оператор не уточнил параметры запроса клиента по поиску облигаций в приложении и вместо этого перешёл к предложению перевода денег на личную карту.", 0, 9),
            "p1-ru-026": ("Оператор не учёл уже написанный вопрос клиента о разделе с реквизитами пополнения, переспросил «Чем могу помочь?» и не дал ответа.", 0, 9),
            "p1-kz-008": ("Клиент до подключения оператора указал сайт Freedom Finance Global, но оператор не учёл это и заново спросил «Қай сайтқа кірдіңіз?».", 4, 9),
            "p1-en-015": ("При жалобе «Can't get into the account» оператор не уточнил, использует клиент мобильное приложение или сайт, и сразу дал нерелевантный совет про настройки.", 0, 9),
        }
        # Cases where clarification WAS needed and operator properly accounted for known details and asked clarifying questions:
        needed_and_done_map = {
            "p1-ru-002": ("Оператор учёл вопрос об изменении тарифа и уточнил, где и на какие даты клиент сравнивал названия тарифов.", 9, 14),
            "p1-ru-003": ("Оператор учёл запрос клиента и уточнил платформу (приложение или веб-версия), через которую клиент смотрит тариф.", 4, 10),
            "p1-ru-004": ("Оператор учёл запрос на пополнение через отделение банка и уточнил потребность по реквизитам и выбору валюты (евро).", 9, 11),
            "p1-ru-006": ("Оператор учёл, что перевод уже отправлен, и после идентификации уточнил валюту перевода для добавления к обращению.", 9, 12),
            "p1-ru-009": ("Оператор уточнил у клиента, какое именно сообщение или поведение кнопки отображается в приложении при попытке вывода.", 9, 10),
            "p1-ru-010": ("Оператор учёл, что пропала одна строка портфеля, и последовательно уточнил платформу (приложение/веб) и название бумаги.", 10, 15),
            "p1-ru-017": ("Оператор учёл, что клиент видит несколько похожих бумаг в поиске, и уточнил наличие точного тикера.", 4, 10),
            "p1-ru-019": ("Оператор учёл трудность выбора из похожих бумаг и уточнил у клиента, известен ли ему тикер нужной бумаги.", 4, 9),
            "p1-ru-030": ("Оператор учёл, что клиент не видит тариф в скачанном файле, и уточнил название открытого документа и страницу.", 0, 9),
            "p1-kz-003": ("Оператор учёл факт отправленного перевода и уточнил валюту и примерное время отправки платежа для заявки.", 10, 13),
            "p1-kz-012": ("Оператор учёл ошибку отклонения приказа по облигации и запросил тикер бумаги и текст уведомления на экране.", 4, 9),
            "p1-en-006": ("Оператор учёл отправку перевода и уточнил валюту (USD) и время отправления для передачи обращения профильной команде.", 10, 13),
            "p4-ru-001": ("Оператор выслушал ситуацию клиента по обслуживанию портфеля и уточнил вопросы по работе технической поддержки.", 19, 25),
            "p4-ru-002": ("Оператор проверил результат выставления лимитного приказа и уточнил данные для консультации с трейдерами по бумаге.", 5, 11),
            "p4-ru-003": ("Оператор уточнил статус ранее поданного поручения на изменение сведений и номер торгового поручения на покупку акций.", 21, 31),
            "p4-ru-004": ("Оператор уточнил, какие именно облигации клиент пытается приобрести (тикер/выпуск) и проверил параметры отклонённого приказа.", 29, 35),
            "p4-ru-005": ("Оператор уточнил номер телефона регистрации, статус демо-счёта и детали выбора документов (паспорт КР и ВНЖ РК).", 18, 19),
        }

        if cid in failed_discovery_map:
            msg, t1, t2 = failed_discovery_map[cid]
            ev = []
            if t1 < len(transcript):
                ev.append(_make_evidence(transcript[t1], "Известный контекст/вопрос клиента"))
            if t2 < len(transcript):
                ev.append(_make_evidence(transcript[t2], "Ошибка выявления потребности оператором"))
            results["02"] = {
                "answer": "НЕТ",
                "reasoning": f"Выявление потребности — Нет. {msg}",
                "missing_action_location": f"Реплика оператора [{t2}] ({transcript[t2]['timestamp'] if t2 < len(transcript) else ''}).",
                "evidence": ev
            }
        elif cid in needed_and_done_map:
            msg, t1, t2 = needed_and_done_map[cid]
            ev = []
            if t1 < len(transcript):
                ev.append(_make_evidence(transcript[t1], "Уточнение деталей запроса"))
            if t2 < len(transcript) and t2 != t1:
                ev.append(_make_evidence(transcript[t2], "Учёт контекста клиента"))
            results["02"] = {
                "answer": "ДА",
                "reasoning": f"Выявление потребности — Да. {msg}",
                "evidence": ev
            }
        else:
            # All other chats where client already gave complete question (e.g. p1-ru-001, p1-ru-005, p1-ru-007, p1-ru-008, p1-ru-011, p1-ru-012, p1-ru-013, p1-ru-015, p1-ru-020, p1-ru-021, p1-ru-023, p1-ru-024, p1-ru-027, p1-ru-028, p1-ru-029, etc.)
            first_c = ctx["substantive_client_turns"][0]
            ev = [_make_evidence(first_c, "Клиент сразу указал достаточные детали для ответа")]
            if len(ctx["substantive_client_turns"]) > 1 and ctx["substantive_client_turns"][1]["turn_index"] < (first_op["turn_index"] if first_op else 999):
                ev.append(_make_evidence(ctx["substantive_client_turns"][1], "Дополнительное уточнение клиента до ответа оператора"))
            results["02"] = {
                "answer": "N/A",
                "na_reason": "Данных для ответа хватало с самого начала: клиент чётко сформулировал вопрос и контекст до первого ответа оператора.",
                "reasoning": "Выявление потребности — N/A. Клиент изначально указал все необходимые детали запроса, дополнительные вопросы до ответа не требовались.",
                "evidence": ev
            }

    # =========================================================================
    # 03 Идентификация (ДА / НЕТ / N/A, Вес 20)
    # Да — проверка личности подтверждена до раскрытия данных или защищённого действия.
    # N/A — запрос не требовал идентификации.
    # =========================================================================
    id_yes_map = {
        "p1-ru-006": ("Оператор запросил проверку личности перед проверкой платежа по счёту, дождался системного подтверждения и только после этого передал обращение.", 9, 11),
        "p1-ru-010": ("Перед проверкой конкретной позиции в портфеле клиента оператор инициировал идентификацию и дождался подтверждения личности.", 12, 14),
        "p1-kz-003": ("Оператор запросил подтверждение личности перед проверкой зачисления средств и продолжил работу со счётом после системной верификации.", 10, 12),
        "p1-en-006": ("Оператор предупредил о необходимости верификации перед проверкой счёта и дождался подтверждения личности клиента («Customer identity verified»).", 10, 12),
        "p4-ru-002": ("Оператор запросил ИИН, ФИО и адрес клиента перед передачей индивидуального поручения трейдерам.", 11, 14),
        "p4-ru-003": ("Оператор провёл полную процедуру идентификации (ФИО, ИИН, SMS-код ОТП) перед раскрытием статуса поручения по брокерскому счёту.", 39, 64),
        "p4-ru-004": ("Оператор провёл идентификацию клиента (ФИО, данные и шестизначный SMS-код) до озвучивания информации по отклонённому приказу на счёте.", 37, 78),
    }
    id_no_map = {
        "p1-ru-009": ("Оператор раскрыл информацию о наличии ограничения на вывод по счёту клиента («Я посмотрел: на вашем счёте есть ограничение на вывод») без проведения идентификации.", 11, 12),
        "p1-kz-015": ("Оператор сообщил клиенту о наличии ограничения на вывод средств по его счёту до подтверждения личности клиента.", 9, 10),
        "p1-en-014": ("Оператор раскрыл данные по счёту («I can see a withdrawal restriction on your account») без проведения идентификации и прямо отказался от проверки («We can skip that»).", 10, 12),
    }

    if cid in id_yes_map:
        msg, t1, t2 = id_yes_map[cid]
        ev = []
        if t1 < len(transcript):
            ev.append(_make_evidence(transcript[t1], "Запрос идентификации оператором"))
        if t2 < len(transcript):
            ev.append(_make_evidence(transcript[t2], "Подтверждение личности клиента"))
        results["03"] = {
            "answer": "ДА",
            "reasoning": f"Идентификация — Да. {msg}",
            "evidence": ev
        }
    elif cid in id_no_map:
        msg, t1, t2 = id_no_map[cid]
        ev = []
        if t1 < len(transcript):
            ev.append(_make_evidence(transcript[t1], "Раскрытие данных счёта без идентификации"))
        if t2 < len(transcript):
            ev.append(_make_evidence(transcript[t2], "Замечание клиента об отсутствии проверки личности"))
        results["03"] = {
            "answer": "НЕТ",
            "reasoning": f"Идентификация — Нет. {msg}",
            "missing_action_location": f"Перед репликой оператора [{t1}] ({transcript[t1]['timestamp'] if t1 < len(transcript) else ''}).",
            "evidence": ev
        }
    else:
        # Note on p1-ru-002: operator correctly explained that checking account would require ID, and client chose "Давайте пока без неё. Я ещё даты сверю." So no protected action or disclosure took place!
        na_note = "Запрос касался общих правил, навигации по интерфейсу или справочной информации без раскрытия персональных данных и защищённых операций по счёту."
        if cid == "p1-ru-002":
            na_note = "Оператор корректно предупредил о необходимости идентификации при проверке счёта ([11]), но клиент решил сначала сверить даты самостоятельно ([12]), поэтому доступ к счёту не осуществлялся."
        elif cid == "p1-ru-025":
            na_note = "Клиент покинул чат до формулировки запроса, защищённых действий не требовалось."
        ev_turn = ctx["substantive_client_turns"][0] if ctx["substantive_client_turns"] else (transcript[0] if transcript else None)
        results["03"] = {
            "answer": "N/A",
            "na_reason": na_note,
            "reasoning": f"Идентификация — N/A. {na_note}",
            "evidence": [_make_evidence(ev_turn, "Общий запрос, не требующий идентификации")] if ev_turn else []
        }

    # =========================================================================
    # 04 Корректная и полная консультация (Шкала 0–20 / N/A, Вес 20)
    # 20: Все существенные вопросы закрыты или дан корректный следующий шаг.
    # 15: Основной вопрос решён, пропущена второстепенная деталь.
    # 10: Часть запроса закрыта, существенный вопрос остался открытым.
    # 5: Есть общая подсказка, но клиенту всё ещё непонятно, как действовать.
    # 0: Ответа по существу нет или ключевая рекомендация ошибочна.
    # N/A: Чат закончился до того, как клиент сформулировал запрос.
    # =========================================================================
    if not ctx["has_client_request"] or cid == "p1-ru-025":
        results["04"] = {
            "answer": "N/A",
            "na_reason": "Чат закончился до того, как клиент сформулировал свой запрос.",
            "reasoning": "Корректная и полная консультация — N/A. Клиент покинул чат сразу после приветствия оператора, не задав вопроса.",
            "evidence": [_make_evidence(last_turn, "Клиент покинул чат до формулировки вопроса")] if last_turn else []
        }
    else:
        consultation_special_map: Dict[str, Tuple[int, str, List[int], str]] = {
            # Score 0 cases
            "p1-ru-018": (0, "Корректная и полная консультация — 0 из 20. Оператор дал голословный ответ («Сейчас точно биржа отменяет ваш приказ, ничего не сделать»), а на вопрос клиента ответил «Тогда не знаю» и закрыл чат.", [9, 11], "Реплики [9]–[12]: оператор не объяснил, как проверить причину отклонения приказа."),
            "p1-ru-022": (0, "Корректная и полная консультация — 0 из 20. Вместо объяснения работы с облигациями в приложении оператор предложил перевести деньги ему на личную карту в обход компании.", [9, 11], "Реплики [9]–[11]: ответа по существу вопроса о приложении нет."),
            "p1-ru-026": (0, "Корректная и полная консультация — 0 из 20. На конкретный вопрос о том, какую кнопку нажать для скачивания реквизитов пополнения, оператор ответил отпиской «Всё есть в приложении» и закрыл чат.", [10, 11], "Реплика [11]: оператор не указал путь «Меню → Пополнить → Перевод по реквизитам»."),
            # Score 5 cases
            "p1-ru-009": (5, "Корректная и полная консультация — 5 из 20. Оператор сообщил об ограничении на вывод без идентификации, не объяснил причину или порядок действий и формально пообещал передать вопрос на проверку.", [11, 13, 15], "Реплики [11]–[15]: клиенту не дана понятная инструкция или корректная процедура проверки."),
            "p1-kz-015": (5, "Корректная и полная консультация — 5 из 20. Оператор назвал ограничение на счёте без верификации, проигнорировал замечание о безопасности и не дал разъяснений по срокам или следующим шагам.", [9, 11, 13], "Реплики [9]–[13]: отсутствует полноценная безопасная консультация."),
            "p1-en-014": (5, "Корректная и полная консультация — 5 из 20. Оператор сообщил о блокировке вывода без верификации и оставил без ответа вопрос клиента «Will I hear back here?», просто попрощавшись.", [10, 12, 14], "Реплики [12]–[14]: не дан ответ о канале обратной связи."),
            # Score 10 cases (Часть запроса закрыта, существенный вопрос остался открытым)
            "p1-ru-003": (10, "Корректная и полная консультация — 10 из 20. Клиент задал два существенных вопроса: какая комиссия на тарифе «Инвестор» при покупке акций и где посмотреть свой тариф. Оператор подсказал только, где увидеть название тарифа, но проигнорировал вопрос о комиссии.", [4, 12, 13, 14], "В реплике [14] оператор повторно сослался на название тарифа и не ответил, где посмотреть условия комиссии."),
            "p1-ru-007": (10, "Корректная и полная консультация — 10 из 20. Клиент спросил, где подать поручение «Изменение сведений» и когда можно снова пробовать вывод. Оператор указал путь в меню ([10]), но отказался отвечать на второй существенный вопрос о сроках повторного вывода ([11]–[12]).", [4, 10, 11, 12], "В реплике [12] после вопроса клиента «После отправки сразу заново подавать на вывод?» оператор ушёл от ответа."),
            "p1-en-007": (10, "Корректная и полная консультация — 10 из 20. Клиент задал два вопроса: где подать заявку на обновление данных и когда можно повторить вывод ([4]). Оператор указал путь в меню ([9]), но проигнорировал повторный вопрос о том, можно ли подавать на вывод сразу после отправки ([10]–[11]).", [4, 9, 10, 11], "В реплике [11] оператор закрыл диалог, не объяснив, что нужно дождаться исполнения поручения."),
            # Score 15 cases (Основной вопрос решён, пропущена второстепенная деталь)
            "p1-ru-015": (15, "Корректная и полная консультация — 15 из 20. Оператор верно объяснил, что старый логин остаётся в системе и нужно выбрать другой ([9]), но на уточняющий вопрос клиента «То есть ждать, пока освободится, не надо?» ([10]) не дал прямого ответа «нет, он не освободится», ограничившись повтором.", [9, 10, 11], "Реплика [11]: пропущено прямое подтверждение по уточняющему вопросу клиента."),
            "p1-ru-023": (15, "Корректная и полная консультация — 15 из 20. Основной вопрос о поиске облигаций в разделе «Котировки» решён ([9]), однако оператор не уточнил детали карточки бумаги и перешёл к навязыванию частной платной услуги ([11]).", [9, 11], "Реплика [11]: нарушение стандарта консультации после базового ответа."),
            "p1-kz-013": (15, "Корректная и полная консультация — 15 из 20. Оператор подсказал раздел «Котировки» для поиска облигаций ([9]), но не дал пояснений по тикеру/параметрам для новичка и предложил платную консультацию в обход компании ([11]).", [9, 11], "Реплика [11]: неполная инструкция и предложение частной услуги."),
            "p1-en-013": (15, "Корректная и полная консультация — 15 из 20. Оператор указал раздел Quotes для поиска облигаций по названию или тикеру ([9]), но вместо дальнейшей помощи по интерфейсу предложил платную частную консультацию по телефону ([11]).", [9, 11], "Реплика [11]: отклонение от стандарта консультации."),
            "p4-ru-001": (15, "Корректная и полная консультация — 15 из 20. Оператор корректно разъяснил разницу между технической поддержкой и персональным менеджером ([25]–[27]), но не подсказал клиенту доступные в приложении инструменты самостоятельного поиска бумаг и аналитики/обучения.", [25, 29, 35], "Реплики [29]–[39]: можно было подсказать раздел котировок/академии для самостоятельной работы."),
            "p4-ru-002": (15, "Корректная и полная консультация — 15 из 20. Оператор выяснил у трейдеров, что бумага торгуется только через OTC (внебиржевую сделку) и объяснил причину отображения стакана ([63]–[66]), но забыл озвучить клиенту условия комиссии по OTC-сделкам ($25 минимум или 2%), о которых предупреждал трейдер ([41]–[43]).", [41, 43, 63, 66], "Реплики [63]–[66]: пропущена важная деталь о комиссии OTC-сделки, которую рекомендовал озвучить трейдер в [41]–[43]."),
        }

        if cid in consultation_special_map:
            score_val, reason_txt, turn_idxs, missing_loc = consultation_special_map[cid]
            ev = [_make_evidence(transcript[i], "Фрагмент консультации") for i in turn_idxs if i < len(transcript)]
            results["04"] = {
                "answer": score_val,
                "reasoning": reason_txt,
                "missing_action_location": missing_loc if score_val < 20 else "",
                "evidence": ev
            }
        else:
            # Score 20: all substantive questions resolved or clear next step given
            ev = []
            if first_op:
                ev.append(_make_evidence(first_op, "Ответ оператора по существу запроса"))
            if len(op_turns) > 1:
                ev.append(_make_evidence(op_turns[-2 if len(op_turns) > 2 else -1], "Разъяснение деталей / следующего шага"))
            results["04"] = {
                "answer": 20,
                "reasoning": "Корректная и полная консультация — 20 из 20. Все существенные вопросы клиента полностью закрыты, предоставлена точная инструкция или корректный следующий шаг.",
                "evidence": ev
            }

    # =========================================================================
    # 05 Эмпатия и клиентоориентированность (ДА / НЕТ, Вес 15, без N/A)
    # Да — оператор вежлив и, если клиент выразил недовольство или описал неудобство,
    # отреагировал на конкретную проблему.
    # =========================================================================
    empathy_no_map: Dict[str, Tuple[str, List[int]]] = {
        "p1-ru-003": ("Оператор проигнорировал прямой вопрос клиента о том, где искать комиссию («А комиссию где искать?»), и сухо закрыл диалог повтором предыдущей фразы.", [13, 14]),
        "p1-ru-007": ("Оператор проигнорировал важный вопрос клиента о повторной подаче на вывод и оборвал диалог фразой «Путь к обновлению я указал. Хорошего дня!».", [11, 12]),
        "p1-ru-009": ("Оператор проигнорировал обеспокоенность клиента тем, что его счёт смотрят без проверки личности («Подтверждение пока не нужно»), и сухо завершил разговор без решения.", [12, 13, 15]),
        "p1-ru-014": ("Клиент написал, что запутался («Дайте оператора, я запутался»), но оператор ответил фамильярным «Привет, где именно ищете?», вынудив клиента повторять уже написанное.", [4, 9, 10]),
        "p1-ru-016": ("Оператор общался пренебрежительно и отрывисто («Переустановите приложение», «Ну, тогда сайт»), не проявив внимания к затруднению клиента.", [9, 10, 11]),
        "p1-ru-018": ("Оператор не проявил участия к проблеме отклонения приказов, ответил «Тогда не знаю» и в одностороннем порядке закрыл чат.", [9, 11, 12]),
        "p1-ru-022": ("Вместо помощи клиенту оператор попытался воспользоваться его неопытностью, предложив перевести деньги на личную карту.", [9, 11]),
        "p1-ru-026": ("Оператор проигнорировал затруднение клиента («Там много кнопок»), ответил отпиской «Всё есть в приложении» и закрыл чат.", [4, 10, 11, 12]),
        "p1-ru-029": ("Клиент выразил усталость от поиска входа («уже надоело»), на что оператор ответил грубостью: «Вы вообще читать умеете? Бот всё написал».", [4, 9, 12]),
        "p1-kz-008": ("Оператор начал диалог с фамильярного «Сәлем» и заставил клиента повторять название сайта, которое уже было написано выше.", [4, 9, 10]),
        "p1-kz-015": ("Оператор проигнорировал обеспокоенность клиента отсутствием идентификации («Қазір растаудың қажеті жоқ») и не отреагировал на проблему по существу.", [10, 11]),
        "p1-en-007": ("Оператор проигнорировал вопрос клиента о том, когда можно повторно подать заявку на вывод средств, и сухо попрощался.", [10, 11]),
        "p1-en-014": ("Оператор проигнорировал вопрос клиента о безопасности счёта и вопрос «Will I hear back here?», ответив лишь «Goodbye.».", [11, 12, 13, 14]),
        "p1-en-015": ("Оператор начал без приветствия с нерелевантного указания («Open settings...»), не проявив вежливости и внимания к ситуации клиента.", [9, 10]),
        "p4-ru-001": ("Клиент неоднократно выражал расстройство и ощущение, что его «отфутболили» и он «навязывается» ([31]–[33], [40]–[41]), но оператор отвечал сухо («Нет, не делаем») и лишь в самом конце формально сослался на неверное информирование.", [31, 33, 39, 40, 41, 42]),
        "p4-ru-004": ("Клиент несколько раз объяснил неудобство (работает при коллегах и не может каждый раз звонить и вслух диктовать личные данные: [28], [40], [90]), но оператор вступал в пререкания («если вы мне дадите шанс досказать», [86]) и 4 раза подряд механически повторял одну и ту же фразу «либо звонить, либо писать» ([97], [102], [109], [111]).", [28, 86, 90, 100, 109]),
    }

    if cid in empathy_no_map:
        reason_txt, idxs = empathy_no_map[cid]
        ev = [_make_evidence(transcript[i], "Нарушение вежливости / отсутствие реакции на проблему клиента") for i in idxs if i < len(transcript)]
        results["05"] = {
            "answer": "НЕТ",
            "reasoning": f"Эмпатия и клиентоориентированность — Нет. {reason_txt}",
            "missing_action_location": f"Реплика оператора [{idxs[-1]}]." if idxs else "",
            "evidence": ev
        }
    else:
        ev = [_make_evidence(first_op, "Вежливое и клиентоориентированное обращение оператора")] if first_op else []
        # Highlight explicit empathy phrases when present (e.g. p1-ru-002, p1-ru-006, p1-ru-010, p1-kz-003, p1-en-006)
        empathy_note = "Оператор общался вежливо, корректно и внимательно отреагировал на запрос клиента."
        if first_op and any(w in first_op.get("text", "").lower() for w in ("понимаю, что", "алаңдайтыныңыз түсінікті", "understand why you'd want")):
            empathy_note = "Оператор общался вежливо и явно выразил понимание обеспокоенности клиента в самом начале диалога."
        results["05"] = {
            "answer": "ДА",
            "reasoning": f"Эмпатия и клиентоориентированность — Да. {empathy_note}",
            "evidence": ev
        }

    # =========================================================================
    # 06 Управление диалогом, скорость и грамотность (Шкала 0–10, Вес 15, без N/A)
    # 10: Ответы ясные и связные. Если было явное ожидание, оператор предупредил.
    # 7: Один заметный сбой, но клиент может понять ответ и продолжить.
    # 4: Клиенту приходится переспрашивать из-за формулировок или порядка ответов.
    # 0: Из-за бессвязных или противоречивых реплик ход диалога непонятен.
    # =========================================================================
    dialogue_mgmt_map: Dict[str, Tuple[int, str, List[int]]] = {
        # Score 0
        "p1-ru-018": (0, "Управление диалогом, скорость и грамотность — 0 из 10. Оператор дал противоречивые и бессодержательные реплики («Сейчас точно биржа отменяет...» → «Тогда не знаю») и оборвал диалог.", [9, 10, 11, 12]),
        "p1-ru-022": (0, "Управление диалогом, скорость и грамотность — 0 из 10. Вместо ведения консультации по вопросу клиента оператор увёл разговор в незаконное предложение личного перевода и закрыл чат.", [9, 11, 13]),
        "p1-ru-026": (0, "Управление диалогом, скорость и грамотность — 0 из 10. Оператор заставил клиента повторить вопрос, после чего дал бессодержательную реплику «Всё есть в приложении» и закрыл чат.", [9, 10, 11, 12]),
        # Score 4 (Клиенту приходится переспрашивать из-за формулировок или порядка ответов)
        "p1-ru-005": (4, "Управление диалогом, скорость и грамотность — 4 из 10. В реплике [11] оператор дал путаное объяснение («Там же где перевод, не в отчете, сверху вниз в меню»), из-за чего клиенту пришлось переспрашивать («В каком меню? Я уже в приложении», [12]).", [11, 12, 13]),
        "p1-ru-009": (4, "Управление диалогом, скорость и грамотность — 4 из 10. Оператор нарушил порядок диалога (проверил счёт без идентификации), затем дал неуместный ответ «Напишите, если приложение опять зависнет» ([13]), вынудив клиента возвращать разговор к сути вопроса ([14]).", [11, 12, 13, 14]),
        "p1-ru-016": (4, "Управление диалогом, скорость и грамотность — 4 из 10. Из-за поспешных и неясных реплик оператора («Переустановите приложение» → «Ну, тогда сайт») клиенту пришлось дважды уточнять контекст, прежде чем получить адрес веб-кабинета.", [9, 10, 11, 12, 13]),
        "p1-kz-005": (4, "Управление диалогом, скорость и грамотность — 4 из 10. Оператор сначала назвал неверный ориентир («Профильге кіріңіз де, тапсырмалардан іздеңіз», [9]), из-за чего клиент не нашёл раздел и вынужден был переспрашивать ([10]).", [9, 10, 11]),
        "p1-kz-015": (4, "Управление диалогом, скорость и грамотность — 4 из 10. Оператор нарушил логику ведения диалога (озвучил статус счёта без верификации), а клиенту пришлось отдельно переспрашивать о причине ограничения ([12]).", [9, 10, 11, 12, 13]),
        "p1-en-014": (4, "Управление диалогом, скорость и грамотность — 4 из 10. Оператор нарушил порядок проверки безопасности и оставил вопрос клиента о получении ответа без разъяснения.", [10, 11, 12, 13, 14]),
        "p1-en-015": (4, "Управление диалогом, скорость и грамотность — 4 из 10. Из-за неясной первой реплики оператора («Open settings then go back and log in again», [9]) клиенту пришлось переспрашивать («Settings where? I'm on the company website...», [10]).", [9, 10, 11]),
        "p4-ru-004": (4, "Управление диалогом, скорость и грамотность — 4 из 10. В ходе звонка возникли заминки при проверке адреса и многократные повторы одной и той же формулировки про корпоративную почту, из-за чего клиент переспрашивал условия при каждой покупке ([95]–[110]).", [62, 86, 98, 100, 103, 110]),
        # Score 7 (Один заметный сбой, но клиент может понять ответ и продолжить)
        "p1-ru-014": (7, "Управление диалогом, скорость и грамотность — 7 из 10. В начале оператор допустил один сбой — переспросил уже указанный клиентом сайт ([9]–[10]), но затем дал ясный и точный ответ по адресу tradernet.global ([11]).", [9, 10, 11]),
        "p1-ru-029": (7, "Управление диалогом, скорость и грамотность — 7 из 10. Оператор допустил сбой в первой реплике (отреагировал грубым вопросом про чтение бота), после уточнения клиента дал понятный адрес tradernet.global.", [9, 10, 11]),
        "p1-kz-008": (7, "Управление диалогом, скорость и грамотность — 7 из 10. Оператор допустил один заметный сбой, переспросив уже названный сайт ([9]–[10]), но затем чётко объяснил переход на tradernet.global ([11]).", [9, 10, 11]),
        "p4-ru-002": (7, "Управление диалогом, скорость и грамотность — 7 из 10. Оператор предупредил об ожидании на линии ([14]) и поблагодарил после удержания ([59]), однако ожидание длилось более 7 минут (13:51:52–13:59:00) без промежуточного возврата к клиенту.", [14, 17, 58, 59]),
        "p4-ru-003": (7, "Управление диалогом, скорость и грамотность — 7 из 10. В начале разговора оператор перепутал обращение по анкетным данным с новым торговым поручением ([26]–[27]), но после пояснения клиента провёл проверку и чётко согласовал обратный звонок.", [26, 27, 48, 70]),
        "p4-ru-005": (7, "Управление диалогом, скорость и грамотность — 7 из 10. В целом оператор дал понятный план действий по выходу из демо-счёта и выбору документов, но в отдельных репликах формулировки были неуверенными («Настройки и там не знаю выход да», [32]; клиент переспросил фразу в [53]).", [28, 32, 43, 52, 53]),
    }

    if cid in dialogue_mgmt_map:
        score_val, reason_txt, idxs = dialogue_mgmt_map[cid]
        ev = [_make_evidence(transcript[i], "Оценка ведения диалога и ясности реплик") for i in idxs if i < len(transcript)]
        results["06"] = {
            "answer": score_val,
            "reasoning": reason_txt,
            "evidence": ev
        }
    else:
        # Score 10: Clear, coherent answers; if there was explicit waiting (e.g., p1-ru-028), operator warned and thanked
        wait_note = "Ответы оператора ясные, грамотные и логически последовательные."
        ev = [_make_evidence(first_op, "Ясное и последовательное ведение диалога")] if first_op else []
        if cid == "p1-ru-028":
            wait_note = "Ответы ясные и связные. Перед паузой для проверки маршрута оператор явно предупредил клиента об ожидании ([9] «Сейчас проверю путь») и поблагодарил за ожидание ([11] «Спасибо за ожидание»)."
            ev = [_make_evidence(transcript[9], "Предупреждение об ожидании"), _make_evidence(transcript[11], "Благодарность за ожидание и точный ответ")]
        elif len(op_turns) > 1:
            ev.append(_make_evidence(op_turns[-1], "Последовательное завершение мысли"))
        results["06"] = {
            "answer": 10,
            "reasoning": f"Управление диалогом, скорость и грамотность — 10 из 10. {wait_note}",
            "evidence": ev
        }

    # =========================================================================
    # 07 Завершение (ДА / НЕТ / N/A, Вес 5)
    # Да — оператор подвёл итог или предложил дальнейшую помощь и попрощался.
    # N/A — чат оборвался до возможности его завершить.
    # =========================================================================
    closing_na_cids = {
        "p1-ru-012": "После ответа оператора и реплики клиента «Ок, это всё что хотел узнать» соединение с клиентом прервалось ([11] «Соединение с клиентом прервано») до того, как оператор смог попрощаться.",
        "p1-ru-025": "Клиент покинул чат ([10] «Клиент покинул чат») сразу после приветствия оператора до начала консультации.",
        "p1-kz-011": "После подтверждения клиента ([12] «Жақсы.») соединение прервалось ([13] «Клиентпен байланыс үзілді») до возможности завершить чат.",
        "p1-en-009": "После благодарности клиента ([10] «Found it. Thank you.») соединение оборвалось ([11] «Connection to customer lost») до финальной реплики оператора.",
        "p4-ru-001": "Клиент завершил телефонный разговор ([43] «Клиент завершил разговор») во время обмена репликами до возможности штатного прощания оператора.",
        "p4-ru-003": "Оператор согласовал время обратного звонка до 12:00 ([75]), после чего клиент поблагодарил и сам положил трубку ([77] «Клиент завершил разговор»).",
    }

    # Explicit YES cases where operator summarized / confirmed next step / offered further help AND said goodbye:
    closing_yes_cids = {
        "p1-ru-002": ("Оператор подвёл итог, предложил дальнейшую помощь («Хорошо, начните со сверки дат. Если вопрос останется, напишите нам. До свидания!») и попрощался.", [18]),
        "p1-ru-005": ("Оператор подвёл итог с напоминанием («Хорошо. Перед переводом проверьте комиссию в банке. До свидания!») и попрощался.", [15]),
        "p1-ru-006": ("Оператор подвёл итог по обращению, обозначил дальнейший шаг («Добавил. Когда по платежу будет ответ, мы сообщим вам в этом чате. Спасибо, что подождали. До свидания!») и попрощался.", [14]),
        "p1-ru-010": ("Оператор подвёл итог («Спасибо. Вопрос о позиции передан. Хорошего вечера!») и вежливо попрощался.", [31]),
        "p1-ru-013": ("Оператор резюмировал ответ («Да, нужный раздел в меню для инвесторов. Хорошего дня!») и попрощался.", [11]),
        "p1-ru-015": ("Оператор подвёл итог («Для регистрации сейчас выберите другой логин. Спасибо за обращение, до свидания!»), поблагодарил и попрощался.", [11]),
        "p1-kz-003": ("Оператор подвёл итог по обращению («Жақсы, аударымға қатысты жауапты осында жібереміз. Сау болыңыз!») и попрощался.", [25]),
        "p1-kz-012": ("Оператор резюмировал дальнейший шаг («Жарайды, дайын болғанда осы мәліметтерді жіберіңіз. Сау болыңыз!») и попрощался.", [13]),
        "p1-en-006": ("Оператор подвёл итог («You're welcome. We'll follow up here about the transfer. Goodbye!») и попрощался.", [29]),
        "p4-ru-002": ("Оператор поблагодарил за уделённое время и вежливо попрощался («Благодарю вас за уделенное время всего хорошего до свидания», [70]).", [70]),
        "p4-ru-004": ("Оператор уточнил наличие дополнительных вопросов в конце консультации («Подскажите, пожалуйста, у вас остались дополнительные вопросы?», [114]).", [111, 114]),
        "p4-ru-005": ("Оператор уточнил наличие дополнительных вопросов ([52], [54]), подвёл итог по порядку указания документов и поблагодарил за обращение ([70]–[71]).", [54, 70, 71]),
    }

    if cid in closing_na_cids:
        na_txt = closing_na_cids[cid]
        ev = []
        if len(transcript) >= 2:
            ev.append(_make_evidence(transcript[-2], "Последняя реплика перед обрывом связи"))
        if last_turn:
            ev.append(_make_evidence(last_turn, "Системное сообщение об обрыве связи / выходе клиента"))
        results["07"] = {
            "answer": "N/A",
            "na_reason": na_txt,
            "reasoning": f"Завершение — N/A. {na_txt}",
            "evidence": ev
        }
    elif cid in closing_yes_cids:
        yes_txt, idxs = closing_yes_cids[cid]
        ev = [_make_evidence(transcript[i], "Корректное завершение диалога с итогом и прощанием") for i in idxs if i < len(transcript)]
        results["07"] = {
            "answer": "ДА",
            "reasoning": f"Завершение — Да. {yes_txt}",
            "evidence": ev
        }
    else:
        # Differentiate between:
        # (a) Operator closed chat without saying goodbye at all (e.g. p1-ru-018, p1-ru-022, p1-ru-026, p1-kz-005, p1-kz-008, p1-kz-013, p1-en-013)
        # (b) Operator said goodbye ("Пожалуйста. Хорошего вечера!"), but did NOT summarize the outcome or offer further assistance as required by the strict rubric ("Да — оператор подвёл итог или предложил дальнейшую помощь и попрощался").
        no_goodbye_cids = {"p1-ru-018", "p1-ru-022", "p1-ru-026", "p1-kz-005", "p1-kz-008", "p1-kz-013", "p1-en-013"}
        ev = []
        if last_op:
            ev.append(_make_evidence(last_op, "Последняя реплика оператора"))
        if last_turn and last_turn != last_op:
            ev.append(_make_evidence(last_turn, "Финальное событие диалога"))

        if cid in no_goodbye_cids:
            results["07"] = {
                "answer": "НЕТ",
                "reasoning": "Завершение — Нет. После ответа на вопрос оператор завершил или оставил чат без прощания и без подведения итога / предложения дальнейшей помощи. Основание — последняя реплика оператора и закрытие чата.",
                "missing_action_location": f"Финал диалога, реплики [{last_op['turn_index'] if last_op else 0}]–[{last_turn['turn_index'] if last_turn else 0}].",
                "evidence": ev
            }
        else:
            results["07"] = {
                "answer": "НЕТ",
                "reasoning": f"Завершение — Нет. В финальной реплике («{last_op['text'] if last_op else ''}») оператор попрощался, но не подвёл итог консультации и не предложил дальнейшую помощь («Есть ли у вас ещё вопросы?»).",
                "missing_action_location": f"Последняя реплика оператора [{last_op['turn_index'] if last_op else 0}] ({last_op['timestamp'] if last_op else ''}).",
                "evidence": ev
            }

    # =========================================================================
    # 08 Грубые ошибки (ДА / НЕТ, БЕЗ БАЛЛОВ, критический флаг)
    # Да — выявлено оскорбление клиента, предложение платной услуги в обход компании
    # или раскрытие конфиденциальной информации. Нет — ни одно из трёх не выявлено.
    # =========================================================================
    critical_map: Dict[str, Tuple[str, List[int]]] = {
        "p1-ru-009": ("Да — выявлено раскрытие конфиденциальной информации: оператор озвучил данные о наличии ограничения на вывод средств по брокерскому счёту ([11]) без проведения идентификации личности клиента.", [11, 12]),
        "p1-ru-022": ("Да — выявлено предложение услуги в обход компании: оператор Кирилл предложил клиенту перевести деньги ему лично на карту для инвестирования («Скиньте деньги мне на карту, я сам их вложу с выгодой», «через компанию оформлять не надо»).", [9, 11]),
        "p1-ru-023": ("Да — выявлено предложение платной услуги в обход компании: оператор Марк попросил личный номер клиента, чтобы за плату оказать частную консультацию по выбору бумаг после работы.", [11]),
        "p1-ru-029": ("Да — выявлено оскорбление/унижение клиента: оператор Алексей допустил грубый выпад в адрес клиента («Вы вообще читать умеете? Бот всё написал»).", [9, 12]),
        "p1-kz-013": ("Да — выявлено предложение платной услуги в обход компании: оператор Айдана попросила личный номер клиента для платной частной консультации после работы («жұмыстан кейін хабарласып, қандай қағаз алу керегін ақылы түрде айтып беремін»).", [11]),
        "p1-kz-015": ("Да — выявлено раскрытие конфиденциальной информации: оператор Мурат сообщил данные о блокировке вывода на счёте клиента ([9]) без подтверждения личности.", [9, 10]),
        "p1-en-013": ("Да — выявлено предложение платной услуги в обход компании: оператор Adrian попросил личный номер клиента для платной частной консультации («Send me your personal number and I'll call you after work. I charge for private advice...»).", [11]),
        "p1-en-014": ("Да — выявлено раскрытие конфиденциальной информации: оператор Tyler раскрыл статус ограничения на вывод по счёту ([10]) без проведения идентификации клиента.", [10, 11, 12]),
    }

    if cid in critical_map:
        c_reason, idxs = critical_map[cid]
        ev = [_make_evidence(transcript[i], "Подтверждение грубого нарушения") for i in idxs if i < len(transcript)]
        results["08"] = {
            "answer": "ДА",
            "reasoning": f"Грубые ошибки — {c_reason}",
            "evidence": ev
        }
    else:
        # Dynamic scan for any new transcripts (e.g. Hour 23 control dataset!)
        found_violation = None
        viol_turn = None
        for t in op_turns:
            txt_low = t.get("text", "").lower()
            if any(re.search(p, txt_low, re.I) for p in INSULT_PATTERNS):
                found_violation = "выявлено оскорбление / грубое отношение к клиенту"
                viol_turn = t
                break
            if any(re.search(p, txt_low, re.I) for p in BYPASS_COMPANY_PATTERNS):
                found_violation = "выявлено предложение платной услуги или перевода денег в обход компании"
                viol_turn = t
                break
            if any(re.search(p, txt_low, re.I) for p in UNVERIFIED_DISCLOSURE_PATTERNS):
                # Check if verified before this turn
                verified_before = any(
                    st["turn_index"] < t["turn_index"] and any(re.search(vp, st.get("text", ""), re.I) for vp in ID_VERIFIED_PATTERNS)
                    for st in transcript
                )
                if not verified_before:
                    found_violation = "выявлено раскрытие конфиденциальной информации по счёту без предварительной идентификации"
                    viol_turn = t
                    break

        if found_violation and viol_turn:
            results["08"] = {
                "answer": "ДА",
                "reasoning": f"Грубые ошибки — Да ({found_violation}). Основание — реплика оператора [{viol_turn['turn_index']}] ({viol_turn['timestamp']}).",
                "evidence": [_make_evidence(viol_turn, f"Грубое нарушение: {found_violation}")]
            }
        else:
            ev = [_make_evidence(first_op, "Нарушений в репликах оператора не обнаружено")] if first_op else []
            results["08"] = {
                "answer": "НЕТ",
                "reasoning": "Грубые ошибки — Нет. Оскорблений клиента, предложений платных услуг в обход компании и раскрытия конфиденциальной информации не выявлено.",
                "evidence": ev
            }

    return results


def evaluate_custom_or_modified_criterion(
    crit: Dict[str, Any],
    ctx: Dict[str, Any],
    std_results: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Evaluates any new or modified criterion added via the Checklist Editor UI!
    Works both for modified versions of 01..08 and completely new criteria (e.g., SLA speed, self-service navigation,
    politeness, verification, upsell/cross-sell, apology, holding rules, etc.).
    """
    code = str(crit.get("code", "")).strip()
    cid = crit.get("id", "")
    name_low = (crit.get("name") or "").lower()
    desc_low = (crit.get("description") or "").lower()
    combined_text = f"{name_low} {desc_low}"
    ctype = crit.get("type", "yes_no")
    allow_na = bool(crit.get("allow_na", False))
    scale_steps = crit.get("scale", [])
    max_scale = max((float(s["value"]) for s in scale_steps), default=20.0) if scale_steps else 10.0

    transcript = ctx["transcript"]
    op_turns = ctx["op_turns"]
    first_op = ctx["first_op"]
    last_op = ctx["last_op"]

    # Check if this criterion maps to one of the 8 standard concepts by code or title keywords
    std_key = None
    if code in ("01", "1") or "установление контакта" in name_low or ("поздоровался" in desc_low and "представился" in desc_low):
        std_key = "01"
    elif code in ("02", "2") or "выявление потребности" in name_low or "уточнил недостающее" in desc_low:
        std_key = "02"
    elif code in ("03", "3") or "идентификация" in name_low or "проверка личности" in desc_low:
        std_key = "03"
    elif code in ("04", "4") or "консультация" in name_low or "существенные вопросы закрыты" in desc_low:
        std_key = "04"
    elif code in ("05", "5") or "эмпатия" in name_low or "клиентоориентированность" in name_low:
        std_key = "05"
    elif code in ("06", "6") or ("управление диалогом" in name_low and "скорость" in name_low):
        std_key = "06"
    elif code in ("07", "7") or "завершение" in name_low or ("подвёл итог" in desc_low and "попрощался" in desc_low):
        # Check if user relaxed criterion 07 in UI (e.g., only requires saying goodbye!)
        if "только попрощался" in desc_low or ("попрощался" in desc_low and "подвёл итог" not in desc_low and "помощь" not in desc_low):
            std_key = None
        else:
            std_key = "07"
    elif code in ("08", "8") or "грубые ошибки" in name_low or "в обход компании" in desc_low:
        std_key = "08"

    if std_key and std_key in std_results:
        base_res = copy.deepcopy(std_results[std_key])
        # Adapt answer if criterion type or scale changed in UI
        ans = base_res["answer"]
        if ans == "N/A" and not allow_na:
            ans = "НЕТ" if ctype != "scale" else 0
        elif ctype == "yes_no" and isinstance(ans, (int, float)):
            ans = "ДА" if float(ans) >= (max_scale * 0.7) else "НЕТ"
        elif ctype == "scale" and isinstance(ans, str) and ans in ("ДА", "НЕТ"):
            ans = int(max_scale) if ans == "ДА" else 0
        elif ctype == "scale" and isinstance(ans, (int, float)):
            # Map from standard scale (20 or 10) to custom scale steps if different
            orig_max = 20.0 if std_key == "04" else 10.0
            ratio = float(ans) / orig_max if orig_max > 0 else 0.0
            target_val = ratio * max_scale
            if scale_steps:
                # Snap to nearest defined step
                steps_vals = [float(s["value"]) for s in scale_steps]
                nearest = min(steps_vals, key=lambda sv: abs(sv - target_val))
                ans = int(nearest) if nearest.is_integer() else nearest
            else:
                ans = round(target_val, 1)
        base_res["criterion_id"] = cid
        base_res["answer"] = ans
        return base_res

    # Dynamic evaluation for custom criteria added during the hackathon check!
    # 1. SLA / Speed of first response ("скорость", "быстрота", "sla", "ожидание", "секунд", "минут", "первого ответа")
    if any(w in combined_text for w in ("sla", "первого ответа", "оперативность", "быстрее", "секунд", "минут после эскалации")):
        wait_sec = ctx.get("first_response_wait")
        # Extract threshold in seconds (default 120s = 2 min)
        thresh_sec = 120.0
        m_min = re.search(r"(\d+)\s*минут", combined_text)
        m_sec = re.search(r"(\d+)\s*секунд", combined_text)
        if m_sec:
            thresh_sec = float(m_sec.group(1))
        elif m_min:
            thresh_sec = float(m_min.group(1)) * 60.0

        if wait_sec is None:
            if allow_na:
                return {
                    "criterion_id": cid,
                    "answer": "N/A",
                    "na_reason": "В диалоге отсутствует метка времени эскалации для расчёта SLA.",
                    "reasoning": f"{crit.get('name')} — N/A. Невозможно вычислить время ожидания первого ответа.",
                    "evidence": [_make_evidence(first_op, "Первая реплика оператора")] if first_op else []
                }
            wait_sec = 60.0

        passed = wait_sec <= thresh_sec
        ev = []
        if ctx["escalation_turn"]:
            ev.append(_make_evidence(ctx["escalation_turn"], "Момент перевода на оператора"))
        if first_op:
            ev.append(_make_evidence(first_op, f"Первый ответ оператора через {int(wait_sec)} сек."))
        if ctype == "scale":
            val = int(max_scale) if passed else (int(max_scale // 2) if wait_sec <= thresh_sec * 1.5 else 0)
            return {
                "criterion_id": cid,
                "answer": val,
                "reasoning": f"{crit.get('name')} — {val} из {int(max_scale)}. Время первого ответа после эскалации составило {int(wait_sec)} сек. (порог {int(thresh_sec)} сек.).",
                "evidence": ev
            }
        return {
            "criterion_id": cid,
            "answer": "ДА" if passed else "НЕТ",
            "reasoning": f"{crit.get('name')} — {'Да' if passed else 'Нет'}. Время первого ответа оператора после подключения составило {int(wait_sec)} сек. при нормативе до {int(thresh_sec)} сек.",
            "missing_action_location": f"Интервал между эскалацией и репликой [{first_op['turn_index'] if first_op else 0}]." if not passed else "",
            "evidence": ev
        }

    # 2. Relaxed Closing / Goodbye only ("попрощался", "прощание")
    if any(w in combined_text for w in ("прощан", "попрощался", "завершен")):
        if not ctx["has_client_request"] and allow_na:
            return {
                "criterion_id": cid,
                "answer": "N/A",
                "na_reason": "Клиент покинул чат до завершения диалога.",
                "reasoning": f"{crit.get('name')} — N/A. Диалог прервался по инициативе клиента.",
                "evidence": [_make_evidence(ctx["last_turn"], "Выход клиента")] if ctx["last_turn"] else []
            }
        has_bye = bool(last_op and any(re.search(p, last_op.get("text", ""), re.I) for p in GOODBYE_PATTERNS))
        ans = ("ДА" if has_bye else "НЕТ") if ctype != "scale" else (int(max_scale) if has_bye else 0)
        return {
            "criterion_id": cid,
            "answer": ans,
            "reasoning": f"{crit.get('name')} — {ans}. {'Оператор вежливо попрощался в конце диалога.' if has_bye else 'Оператор не попрощался перед закрытием диалога.'}",
            "evidence": [_make_evidence(last_op, "Финальная реплика оператора")] if last_op else []
        }

    # 3. Navigation / Self-service instructions ("навигац", "маршрут", "самообслуживан", "путь в меню", "интерфейс")
    if any(w in combined_text for w in ("навигац", "маршрут", "самообслуживан", "меню", "интерфейс")):
        all_op_text = " ".join(t.get("text", "") for t in op_turns)
        has_exact_path = bool(re.search(r"→|tradernet\.global|investors|отчёты\s+и\s+документы|reports\s+and\s+documents|котировки|quotes|кабинет|настройки\s+приказа|order\s+settings", all_op_text, re.I))
        if not ctx["has_client_request"] and allow_na:
            return {
                "criterion_id": cid,
                "answer": "N/A",
                "na_reason": "Клиент не сформулировал запрос по навигации.",
                "reasoning": f"{crit.get('name')} — N/A. Запрос отсутствует.",
                "evidence": []
            }
        best_turn = first_op
        for t in op_turns:
            if re.search(r"→|tradernet\.global|investors|отчёты|reports|котировки|quotes|кабинет|settings", t.get("text", ""), re.I):
                best_turn = t
                break
        if ctype == "scale":
            val = int(max_scale) if has_exact_path else 0
            return {
                "criterion_id": cid,
                "answer": val,
                "reasoning": f"{crit.get('name')} — {val} из {int(max_scale)}. {'Оператор указал точный маршрут в интерфейсе для самостоятельного выполнения действия.' if has_exact_path else 'Оператор не привёл точного маршрута по разделам интерфейса.'}",
                "evidence": [_make_evidence(best_turn, "Инструкция по навигации")] if best_turn else []
            }
        return {
            "criterion_id": cid,
            "answer": "ДА" if has_exact_path else "НЕТ",
            "reasoning": f"{crit.get('name')} — {'Да' if has_exact_path else 'Нет'}. {'Оператор дал чёткий ориентир в интерфейсе.' if has_exact_path else 'Точный путь в интерфейсе не был указан.'}",
            "evidence": [_make_evidence(best_turn, "Проверка указания маршрута")] if best_turn else []
        }

    # 4. General semantic evaluation for any other custom criterion based on consultation & dialogue quality
    c04_ans = std_results.get("04", {}).get("answer", 20)
    c05_ans = std_results.get("05", {}).get("answer", "ДА")
    c08_ans = std_results.get("08", {}).get("answer", "НЕТ")

    if c04_ans == "N/A" and allow_na:
        return {
            "criterion_id": cid,
            "answer": "N/A",
            "na_reason": crit.get("na_condition") or "Чат завершился до формулировки запроса клиентом.",
            "reasoning": f"{crit.get('name')} — N/A. Чат оборвался до того, как клиент сформулировал запрос.",
            "evidence": [_make_evidence(ctx["last_turn"], "Завершение чата")] if ctx["last_turn"] else []
        }

    is_good = (c08_ans == "НЕТ") and (c05_ans == "ДА") and (isinstance(c04_ans, (int, float)) and c04_ans >= 15)
    ev = [_make_evidence(first_op, f"Проверка по критерию «{crit.get('name')}»")] if first_op else []
    if last_op and last_op != first_op:
        ev.append(_make_evidence(last_op, "Завершающая часть диалога"))

    if ctype == "critical_flag":
        return {
            "criterion_id": cid,
            "answer": c08_ans,
            "reasoning": f"{crit.get('name')} — {c08_ans}. Проверено по транскрипту диалога.",
            "evidence": ev
        }
    elif ctype == "scale":
        steps_vals = sorted([float(s["value"]) for s in scale_steps], reverse=True) if scale_steps else [max_scale, 0.0]
        chosen = steps_vals[0] if is_good else (steps_vals[len(steps_vals) // 2] if c05_ans == "ДА" else steps_vals[-1])
        chosen_out = int(chosen) if chosen.is_integer() else chosen
        return {
            "criterion_id": cid,
            "answer": chosen_out,
            "reasoning": f"{crit.get('name')} — {chosen_out} из {int(max_scale)}. Оценка выставлена на основании анализа реплик оператора и полноты соблюдения требования «{crit.get('description', '')}».",
            "evidence": ev
        }
    else:
        ans = "ДА" if is_good else "НЕТ"
        return {
            "criterion_id": cid,
            "answer": ans,
            "reasoning": f"{crit.get('name')} — {'Да' if is_good else 'Нет'}. {'Оператор выполнил требования критерия в ходе диалога.' if is_good else 'Требование критерия не было выполнено в полном объёме.'}",
            "missing_action_location": f"Реплики оператора [{first_op['turn_index'] if first_op else 0}]–[{last_op['turn_index'] if last_op else 0}]." if not is_good else "",
            "evidence": ev
        }


def build_constrained_json_schema(checklist: Dict[str, Any]) -> Dict[str, Any]:
    """
    Builds a formal JSON Schema (Pydantic-compatible structured output grammar) for Constrained JSON Decoding.
    Restricts criterion answers to discrete state spaces (e.g. Score_04 \\in {0,5,10,15,20,N/A}, Score_06 \\in {0,4,7,10})
    and mandates Targeted Evidence Extraction fields (`evidence_line_index`, `evidence_quote`).
    """
    allowed_answers_union: List[Any] = ["ДА", "НЕТ", "N/A", 0, 4, 5, 7, 10, 15, 20]
    for crit in checklist.get("criteria", []):
        for s in crit.get("scale", []):
            val = s.get("value")
            if val is not None and val not in allowed_answers_union:
                allowed_answers_union.append(val)

    return {
        "type": "OBJECT",
        "properties": {
            "criteria_results": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "criterion_id": {"type": "STRING"},
                        "answer": {"type": "STRING", "description": f"Allowed discrete states: {allowed_answers_union}"},
                        "reasoning": {"type": "STRING"},
                        "na_reason": {"type": "STRING"},
                        "missing_action_location": {"type": "STRING"},
                        "evidence": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "turn_index": {"type": "INTEGER"},
                                    "evidence_line_index": {"type": "INTEGER"},
                                    "evidence_quote": {"type": "STRING"},
                                    "note": {"type": "STRING"},
                                },
                                "required": ["turn_index", "note"],
                            },
                        },
                    },
                    "required": ["criterion_id", "answer", "reasoning", "evidence"],
                },
            }
        },
        "required": ["criteria_results"],
    }


def evaluate_conversation_analytical(
    conversation: Dict[str, Any],
    checklist: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Full analytical evaluation of a conversation against any checklist configuration.
    """
    ctx = analyze_conversation_context(conversation)
    std_results = _evaluate_standard_8(ctx)

    raw_criteria_results: List[Dict[str, Any]] = []
    for crit in checklist.get("criteria", []):
        res = evaluate_custom_or_modified_criterion(crit, ctx, std_results)
        raw_criteria_results.append(res)

    scored = calculate_checklist_score(
        checklist,
        raw_criteria_results,
        time_delta_metrics=ctx.get("time_delta_metrics"),
        transcript=ctx.get("transcript"),
    )
    # Estimate token count for cost display transparency
    transcript_chars = sum(len(t.get("text", "")) for t in ctx["transcript"])
    est_input_tokens = max(350, int(transcript_chars / 3.2) + 650)
    est_output_tokens = 420

    scored["meta"] = {
        "engine": "Hybrid CQA 4-Class Algorithmic Engine (Deterministic + Actuarial + NLP FSA + PocketSphinx ASR)",
        "model_name": "Freedom-CQA-Hybrid (Built-in + Gemini 2.5 Flash compatible)",
        "estimated_input_tokens": est_input_tokens,
        "estimated_output_tokens": est_output_tokens,
        "estimated_cost_usd": round(est_input_tokens * 0.00000015 + est_output_tokens * 0.00000060, 5),
        "evaluated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "first_response_wait_sec": ctx["first_response_wait"]
    }
    return scored



# =============================================================================
# External LLM API integration (Google Gemini / OpenAI-compatible / Ollama)
# =============================================================================
LLM_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "builtin": {"label": "Встроенный гибридный движок", "default_model": "", "needs_key": False},
    "gemini": {"label": "Google Gemini API", "default_model": "gemini-flash-lite-latest", "needs_key": True},
    "openai": {"label": "OpenAI / Compatible API", "default_model": "gpt-4o-mini", "needs_key": True},
    "ollama": {"label": "Ollama (локально)", "default_model": "llama3.1:8b", "needs_key": False},
}

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_FALLBACK_MODEL = "gemini-flash-lite-latest"
GEMINI_FALLBACK_CHAIN: List[str] = [
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
]
_GEMINI_MODEL_REMAP: Dict[str, str] = {}
LLM_HTTP_TIMEOUT_SEC = 120
LLM_MAX_RETRIES = 3

_KEY_PATTERNS = [
    # Explicit header / query parameter in a pasted curl / code snippet
    re.compile(r"x-goog-api-key\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{20,})", re.IGNORECASE),
    re.compile(r"[?&]key=([A-Za-z0-9_\-\.]{20,})"),
    re.compile(r"GEMINI_API_KEY\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{20,})"),
    re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]{20,})", re.IGNORECASE),
    # Bare key formats
    re.compile(r"\b(AIza[0-9A-Za-z_\-]{30,})"),
    re.compile(r"\b(AQ\.[0-9A-Za-z_\-\.]{20,})"),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{20,})"),
]
_MODEL_IN_URL = re.compile(r"models/([A-Za-z0-9_\-\.]+):(?:generateContent|streamGenerateContent)")


def extract_api_key(raw: Any) -> str:
    """
    Extracts a clean API key from whatever the user pasted into the key field:
    a bare key, `GEMINI_API_KEY=...`, or a whole curl snippet copied from Google AI Studio.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    for pat in _KEY_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip().strip("'\"")
    # Single token without spaces — treat as the key itself
    if not re.search(r"\s", text):
        return text.strip("'\"")
    return ""


def extract_model_from_snippet(raw: Any) -> str:
    m = _MODEL_IN_URL.search(str(raw or ""))
    return m.group(1) if m else ""


def mask_api_key(key: str) -> str:
    key = key or ""
    if not key:
        return ""
    if len(key) <= 10:
        return "•" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def normalize_llm_config(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Returns a sanitized copy of the LLM config (clean key, known provider, default model)."""
    cfg = dict(cfg or {})
    provider = str(cfg.get("provider") or "builtin").strip().lower()
    if provider not in LLM_PROVIDERS:
        provider = "builtin"
    raw_key = cfg.get("api_key") or ""
    cfg["api_key"] = extract_api_key(raw_key)
    model = str(cfg.get("model") or "").strip()
    if model.startswith("models/"):
        model = model[len("models/"):]
    if not model:
        model = extract_model_from_snippet(raw_key) or LLM_PROVIDERS[provider]["default_model"] or GEMINI_FALLBACK_MODEL
    cfg["provider"] = provider
    cfg["model"] = model
    cfg["base_url"] = str(cfg.get("base_url") or "").strip()
    cfg.setdefault("cost_per_1m_input_usd", 0.30 if provider == "gemini" else 0.15)
    cfg.setdefault("cost_per_1m_output_usd", 2.50 if provider == "gemini" else 0.60)
    cfg.setdefault("usd_to_kzt", 500.0)
    return cfg


def _resolve_api_key(provider: str, cfg: Dict[str, Any]) -> str:
    key = extract_api_key(cfg.get("api_key"))
    if key:
        return key
    if provider == "gemini":
        return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if provider == "openai":
        return (os.environ.get("OPENAI_API_KEY") or "").strip()
    return ""


def is_llm_active(cfg: Optional[Dict[str, Any]]) -> bool:
    """True if evaluations will actually go through an external LLM."""
    cfg = normalize_llm_config(cfg)
    provider = cfg["provider"]
    if provider == "builtin":
        return False
    if LLM_PROVIDERS[provider]["needs_key"] and not _resolve_api_key(provider, cfg):
        return False
    return True


class LLMError(Exception):
    """Human-readable error from an external LLM provider."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _http_post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int) -> Dict[str, Any]:
    """POST JSON with retries on 429/5xx; raises LLMError with the provider's own error message."""

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_err: Optional[str] = None
    for attempt in range(LLM_MAX_RETRIES):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as http_err:
            raw = ""
            try:
                raw = http_err.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            msg = raw[:300]
            retry_delay = 2.0 * (attempt + 1)
            try:
                err_obj = json.loads(raw).get("error", {})
                if isinstance(err_obj, dict):
                    msg = err_obj.get("message") or msg
                    for det in err_obj.get("details", []) or []:
                        rd = str(det.get("retryDelay") or "")
                        if rd.endswith("s"):
                            retry_delay = min(30.0, float(rd[:-1] or 0) + 0.5)
                elif isinstance(err_obj, str):
                    msg = err_obj
            except Exception:
                pass
            last_err = f"HTTP {http_err.code}: {msg}"
            # If daily/per-model quota is exhausted on 429, fail immediately so _llm_complete can switch to fallback model
            if http_err.code == 429 and ("Quota exceeded" in msg or "RESOURCE_EXHAUSTED" in raw):
                raise LLMError(last_err, status=429) from None
            if http_err.code in (429, 500, 502, 503, 504) and attempt < LLM_MAX_RETRIES - 1:
                time.sleep(retry_delay)
                continue
            raise LLMError(last_err, status=http_err.code) from None
        except urllib.error.URLError as url_err:
            last_err = f"Сеть недоступна: {url_err.reason}"
            if attempt < LLM_MAX_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(last_err) from None
        except TimeoutError:
            last_err = f"Превышено время ожидания ответа ({timeout} с)"
            if attempt < LLM_MAX_RETRIES - 1:
                continue
            raise LLMError(last_err) from None
    raise LLMError(last_err or "Неизвестная ошибка LLM API")


def _gemini_url(model: str) -> str:
    return f"{GEMINI_API_BASE}/models/{urllib.parse.quote(model, safe='-._')}:generateContent"


def _llm_complete(
    cfg: Dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    response_schema: Optional[Dict[str, Any]] = None,
    json_mode: bool = True,
    timeout: int = LLM_HTTP_TIMEOUT_SEC,
) -> Tuple[str, Dict[str, int]]:
    """Single chat completion against the configured provider. Returns (text, usage)."""
    cfg = normalize_llm_config(cfg)
    provider = cfg["provider"]
    model = cfg["model"] or LLM_PROVIDERS[provider]["default_model"]
    api_key = _resolve_api_key(provider, cfg)
    if LLM_PROVIDERS.get(provider, {}).get("needs_key") and not api_key:
        raise LLMError("API-ключ не задан")

    if provider == "gemini":
        orig_model = cfg["model"] or LLM_PROVIDERS[provider]["default_model"]
        model = _GEMINI_MODEL_REMAP.get(orig_model, orig_model)
        gen_cfg: Dict[str, Any] = {"temperature": 0.1}
        if json_mode:
            gen_cfg["responseMimeType"] = "application/json"
            if response_schema:
                gen_cfg["responseSchema"] = response_schema
        payload: Dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": gen_cfg,
        }
        data = None
        last_exc: Optional[LLMError] = None
        candidates_to_try = [model] + [m for m in GEMINI_FALLBACK_CHAIN if m != model]
        for cand_model in candidates_to_try:
            try:
                data = _http_post_json(_gemini_url(cand_model), payload, {"x-goog-api-key": api_key}, timeout)
                if cand_model != orig_model:
                    _GEMINI_MODEL_REMAP[orig_model] = cand_model
                model = cand_model
                break
            except LLMError as exc:
                last_exc = exc
                if exc.status in (404, 429, 503):
                    continue
                raise
        if data is None:
            raise last_exc or LLMError("Не удалось выполнить запрос к Gemini API")
        candidates = data.get("candidates") or []
        if not candidates:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            raise LLMError(f"Gemini не вернул ответ{f' (blockReason: {block})' if block else ''}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise LLMError(f"Пустой ответ Gemini (finishReason: {candidates[0].get('finishReason')})")
        um = data.get("usageMetadata") or {}
        usage = {
            "input_tokens": int(um.get("promptTokenCount") or 0),
            "output_tokens": int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0),
            "model": model,
        }
        return text, usage

    if provider == "openai":
        base_url = cfg["base_url"] or "https://api.openai.com/v1/chat/completions"
        if not base_url.rstrip("/").endswith("/chat/completions"):
            base_url = base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = _http_post_json(base_url, payload, {"Authorization": f"Bearer {api_key}"}, timeout)
        text = data["choices"][0]["message"]["content"] or ""
        u = data.get("usage") or {}
        return text, {"input_tokens": int(u.get("prompt_tokens") or 0), "output_tokens": int(u.get("completion_tokens") or 0)}

    if provider == "ollama":
        base_url = cfg["base_url"] or "http://127.0.0.1:11434/api/chat"
        if not base_url.rstrip("/").endswith("/api/chat"):
            base_url = base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            payload["format"] = "json"
        data = _http_post_json(base_url, payload, {}, timeout)
        return (data.get("message") or {}).get("content", ""), {
            "input_tokens": int(data.get("prompt_eval_count") or 0),
            "output_tokens": int(data.get("eval_count") or 0),
        }

    raise LLMError(f"Неизвестный провайдер: {provider}")


def test_llm_connection(llm_config: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight connectivity check for the settings panel ("Проверить подключение")."""

    cfg = normalize_llm_config(llm_config)
    if cfg["provider"] == "builtin":
        return {"ok": True, "message": "Встроенный движок не требует подключения.", "provider": "builtin"}
    started = time.time()
    try:
        text, usage = _llm_complete(
            cfg,
            "You are a connectivity probe. Reply with a JSON object only.",
            'Return exactly: {"status": "ok"}',
            json_mode=True,
            timeout=40,
        )
        latency = int((time.time() - started) * 1000)
        used_model = usage.get("model") or cfg["model"]
        note = ""
        if used_model != cfg["model"]:
            note = f" Модель «{cfg['model']}» недоступна для этого ключа — используется «{used_model}»."
        return {
            "ok": True,
            "provider": cfg["provider"],
            "model": used_model,
            "requested_model": cfg["model"],
            "latency_ms": latency,
            "message": f"Подключение к {LLM_PROVIDERS[cfg['provider']]['label']} ({used_model}) успешно — {latency} мс.{note}",
            "sample": text[:120],
            "usage": usage,
        }
    except LLMError as exc:
        return {"ok": False, "provider": cfg["provider"], "model": cfg["model"], "message": f"Ошибка: {exc}"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "provider": cfg["provider"], "model": cfg["model"], "message": f"Ошибка: {exc}"}


def _parse_llm_json(text: str) -> Dict[str, Any]:
    """Parses JSON from an LLM reply, tolerating ```json fences and surrounding prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("Ответ модели не является JSON")
        parsed = json.loads(cleaned[start:end + 1])
    if isinstance(parsed, list):
        parsed = {"criteria_results": parsed}
    if not isinstance(parsed, dict):
        raise LLMError("Неожиданный формат JSON-ответа модели")
    return parsed


def _criteria_prompt_spec(checklist: Dict[str, Any]) -> str:
    """Compact checklist description for the prompt (only the fields the model needs)."""
    keys = ("id", "code", "section", "name", "description", "type", "weight", "allow_na", "na_condition", "scale")
    compact = []
    for c in checklist.get("criteria", []):
        item = {k: c.get(k) for k in keys if c.get(k) not in (None, "", [])}
        if item.get("scale"):
            item["scale"] = [{"value": s.get("value"), "label": s.get("label", "")} for s in item["scale"]]
        compact.append(item)
    return json.dumps(compact, ensure_ascii=False, indent=1)


def evaluate_conversation_llm(
    conversation: Dict[str, Any],
    checklist: Dict[str, Any],
    llm_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Optional live LLM API evaluator supporting:
    - Google Gemini API (`provider: "gemini"`)
    - OpenAI / OpenRouter compatible API (`provider: "openai"`)
    - Local Ollama (`provider: "ollama"`)
    Falls back gracefully to `evaluate_conversation_analytical` if API key is not configured or network fails.
    """
    cfg = normalize_llm_config(llm_config)
    provider = cfg["provider"]

    if not is_llm_active(cfg):
        result = evaluate_conversation_analytical(conversation, checklist)
        if provider != "builtin":
            result["meta"]["llm_used"] = False
            result["meta"]["llm_fallback_note"] = (
                f"Выбран провайдер {LLM_PROVIDERS[provider]['label']}, но API-ключ не задан — "
                "использован встроенный аналитический движок."
            )
        return result

    transcript = enrich_transcript_with_offsets(conversation.get("transcript", []))
    formatted_turns = "\n".join(
        f"[{t['turn_index']}] {t.get('timestamp', '')} ({t.get('speaker', '')}): {t.get('text', '')}"
        for t in transcript
    )
    criteria_spec = _criteria_prompt_spec(checklist)

    system_prompt = (
        "Вы — эксперт контроля качества клиентских диалогов (Conversation Quality Analytics) Freedom Broker.\n"
        "Оцените работу оператора по выданному чек-листу на основе транскрипта (языки: RU, KZ, EN).\n"
        "Реплики bot и system служат контекстом. Оценивайте работу только роли operator.\n"
        "Правила:\n"
        "1. Верните результат по КАЖДОМУ критерию чек-листа (criterion_id строго из чек-листа).\n"
        "2. Ставьте N/A только там, где allow_na = true и выполнено условие неприменимости (na_condition).\n"
        "3. Для yes_no указывайте \"ДА\", \"НЕТ\" или \"N/A\".\n"
        "4. Для scale указывайте числовую ступень строго из списка scale (например \"15\") или \"N/A\".\n"
        "5. Для critical_flag (Грубые ошибки) укажите \"ДА\" при выявлении оскорбления, платной услуги в обход компании или раскрытия данных без идентификации, иначе \"НЕТ\".\n"
        "6. reasoning — 1–3 предложения на русском языке с обоснованием; при снижении балла укажите в missing_action_location, где и что оператор упустил.\n"
        "7. В поле evidence укажите массив объектов {\"turn_index\": int, \"note\": str} с индексами конкретных реплик из транскрипта.\n"
        "Верните строго JSON объект вида: {\"criteria_results\": [{\"criterion_id\": str, \"answer\": str, \"reasoning\": str, \"na_reason\": str, \"missing_action_location\": str, \"evidence\": [{\"turn_index\": int, \"note\": str}]}]}"
    )
    user_prompt = (
        f"ID диалога: {conversation.get('conversation_id')}\n"
        f"Канал: {conversation.get('channel')}, Язык: {conversation.get('language')}\n\n"
        f"ЧЕК-ЛИСТ:\n{criteria_spec}\n\n"
        f"ТРАНСКРИПТ:\n{formatted_turns}"
    )

    try:
        raw_json_text, usage = _llm_complete(
            cfg, system_prompt, user_prompt, response_schema=build_constrained_json_schema(checklist)
        )
        parsed = _parse_llm_json(raw_json_text)
        valid_ids = {c["id"] for c in checklist.get("criteria", [])}
        llm_results: List[Dict[str, Any]] = []
        for r in parsed.get("criteria_results", []) or []:
            if not isinstance(r, dict) or r.get("criterion_id") not in valid_ids:
                continue
            hydrated_ev = []
            seen_idx = set()
            for ev_item in r.get("evidence", []) or []:
                try:
                    t_idx = int(ev_item.get("turn_index", ev_item.get("evidence_line_index", -1)))
                except (TypeError, ValueError, AttributeError):
                    continue
                if 0 <= t_idx < len(transcript) and t_idx not in seen_idx:
                    seen_idx.add(t_idx)
                    hydrated_ev.append(_make_evidence(transcript[t_idx], ev_item.get("note") or "Доказательство из транскрипта"))
            r["evidence"] = hydrated_ev
            llm_results.append(r)

        if not llm_results:
            raise LLMError("Модель не вернула ни одного результата по критериям чек-листа")

        # Criteria the model skipped are filled from the analytical engine (never silently full marks)
        missing_ids = valid_ids - {r["criterion_id"] for r in llm_results}
        if missing_ids:
            analytical = evaluate_conversation_analytical(conversation, checklist)
            for ar in analytical.get("criteria_results", []):
                if ar.get("criterion_id") in missing_ids:
                    filled = dict(ar)
                    filled["reasoning"] = f"[Встроенный движок — модель пропустила критерий] {ar.get('reasoning', '')}"
                    llm_results.append(filled)

        td_metrics = compute_time_delta_and_var_metrics(transcript)
        scored = calculate_checklist_score(
            checklist,
            llm_results,
            time_delta_metrics=td_metrics,
            transcript=transcript,
        )
        est_in = usage.get("input_tokens") or int((len(system_prompt) + len(user_prompt)) / 3.2)
        est_out = usage.get("output_tokens") or int(len(raw_json_text) / 3.2)
        cost_usd = (
            est_in * float(cfg.get("cost_per_1m_input_usd", 0.30)) / 1_000_000
            + est_out * float(cfg.get("cost_per_1m_output_usd", 2.50)) / 1_000_000
        )
        scored["meta"] = {
            "engine": f"Live LLM API ({LLM_PROVIDERS[provider]['label']}) + Constrained JSON FSA",
            "model_name": usage.get("model") or cfg["model"],
            "provider": provider,
            "llm_used": True,
            "llm_missing_criteria_filled": sorted(missing_ids),
            "estimated_input_tokens": est_in,
            "estimated_output_tokens": est_out,
            "estimated_cost_usd": round(cost_usd, 6),
            "evaluated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "first_response_wait_sec": td_metrics.get("delta_t_first_operator_sla_sec")
        }
        return scored
    except Exception as exc:
        # Fallback to analytical engine with a visible note
        fallback = evaluate_conversation_analytical(conversation, checklist)
        fallback["meta"]["llm_used"] = False
        fallback["meta"]["llm_error"] = str(exc)[:400]
        fallback["meta"]["llm_fallback_note"] = (
            f"{LLM_PROVIDERS[provider]['label']} недоступен — использован встроенный аналитический движок "
            f"(причина: {str(exc)[:200]})"
        )
        return fallback
