# -*- coding: utf-8 -*-
"""
Standalone CLI evaluator for new datasets (e.g. PROD 10-dialogue set).
Usage:
    python evaluate_dataset.py [path_to_input.json] [path_to_output.json]

Example:
    python evaluate_dataset.py dev_dataset.json results/dev/results.json
    python evaluate_dataset.py prod_dataset.json results/prod/results.json
"""

import sys
import os
import json
import argparse
from typing import Any, Dict, List

# Ensure current directory is in path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from evaluator import (
    DEFAULT_CHECKLIST,
    evaluate_conversation_analytical,
    evaluate_conversation_llm,
)

TARGET_CRITERIA = [
    ("contact", ["01", "c01", "contact"], "boolean", False),
    ("discovery", ["02", "c02", "discovery"], "boolean", True),
    ("identification", ["03", "c03", "identification"], "boolean", True),
    ("consultation", ["04", "c04", "consultation"], "scale", True),
    ("empathy", ["05", "c05", "empathy"], "boolean", False),
    ("dialogue", ["06", "c06", "dialogue"], "scale", False),
    ("closure", ["07", "c07", "closure"], "boolean", True),
    ("critical", ["08", "c08", "critical"], "critical", False),
]

def parse_input_payload(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], dict) and "records" in data["data"]:
            return data["data"]["records"]
        if "records" in data and isinstance(data["records"], list):
            return data["records"]
        if "conversation_id" in data and "transcript" in data:
            return [data]
    return []

def evaluate_and_format(records: List[Dict[str, Any]], model_name: str = "gemini-flash-lite-latest") -> Dict[str, Any]:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    llm_cfg = {
        "provider": "gemini" if api_key else "builtin",
        "model": model_name if api_key else "Freedom-CQA-Hybrid",
        "api_key": api_key,
        "temperature": 0.0,
    }

    results = []
    total_turns_count = 0

    for idx, rec in enumerate(records, 1):
        cid = rec.get("conversation_id", f"dialogue_{idx}")
        transcript = rec.get("transcript", [])
        total_turns = len(transcript)
        total_turns_count += total_turns

        try:
            if llm_cfg["provider"] == "gemini":
                ev = evaluate_conversation_llm(rec, DEFAULT_CHECKLIST, llm_cfg)
            else:
                ev = evaluate_conversation_analytical(rec, DEFAULT_CHECKLIST)
        except Exception as err:
            results.append({
                "conversation_id": cid,
                "status": "error",
                "criteria": [],
                "score": None,
                "error": f"Evaluation error: {str(err)}"
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
        for target_id, aliases, ctype, allow_na in TARGET_CRITERIA:
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
                        if 1 <= turn_num <= total_turns and turn_num not in evidence_turns:
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

        score_val = ev.get("final_score")
        if score_val is not None:
            try:
                score_val = round(float(score_val), 2)
            except (ValueError, TypeError):
                score_val = 0.0
        else:
            score_val = 0.0

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
                "final_score": score_val,
                "critical_flag": crit_value
            }
        })

    return {
        "schema_version": "1.0",
        "team_name": "bandwagon fallacy",
        "model": {
            "mode": "external_api",
            "name": model_name or "gemini-flash-lite-latest"
        },
        "results": results
    }

def main():
    parser = argparse.ArgumentParser(description="Evaluate CQA dataset and export hackathon results.json")
    parser.add_argument("input", nargs="?", default="fbroker_dataset.json", help="Path to input JSON dataset")
    parser.add_argument("output", nargs="?", default="results/prod/results.json", help="Path to output results.json")
    parser.add_argument("--model", default="gemini-flash-lite-latest", help="Model name in output json")

    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    output_path = os.path.abspath(args.output)

    if not os.path.exists(input_path):
        # Try finding in common locations
        alt_paths = [
            os.path.join(CURRENT_DIR, args.input),
            os.path.join(CURRENT_DIR, "data", args.input),
            os.path.join(CURRENT_DIR, "data", "dev_dataset.json")
        ]
        found = False
        for p in alt_paths:
            if os.path.exists(p):
                input_path = p
                found = True
                break
        if not found:
            print(f"[ERROR] Input file not found: {args.input}")
            sys.exit(1)

    print(f"Loading dataset from: {input_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    records = parse_input_payload(payload)
    print(f"Found {len(records)} dialogue records.")

    if not records:
        print("[ERROR] No valid dialogue records found in input JSON.")
        sys.exit(1)

    print("Evaluating dialogues...")
    results_json = evaluate_and_format(records, model_name=args.model)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results_json, f, ensure_ascii=False, indent=2)

    scores = [
        r["score"]["final_score"]
        for r in results_json["results"]
        if isinstance(r.get("score"), dict) and r["score"].get("final_score") is not None
    ]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0

    print("Evaluation completed successfully.")
    print(f"Dialogues evaluated: {len(results_json['results'])}")
    print(f"Average score: {avg_score} / 100")
    print(f"Output saved to: {output_path}")

if __name__ == "__main__":
    main()
