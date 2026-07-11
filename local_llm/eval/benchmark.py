"""Model-comparison benchmark for the on-prem QA pipeline (Week 13, Stage 6).

Runs the *exact production pipeline* (``qa.router.answer``) over a fixed question
set for one or more Ollama models, so the comparison is apples-to-apples: same
retrieval, same tool schemas, same prompts — only the model swaps. This is the
"I did model selection, I didn't just grab the first 3B that ran" evidence.

Measures four things per model:

  1. **tool-use success** — on data questions, did it call the right tool
     (and with the right stock code when one is expected)? Numbers are only
     trustworthy if the model reliably reaches for the tool.
  2. **RAG grounding** — on concept questions, did the expected knowledge doc
     get retrieved and injected?
  3. **honest downgrade** — on out-of-bounds questions, did it avoid fabricating
     a data-tool call and avoid a false citation?
  4. **speed** — end-to-end wall-clock latency per question, plus a clean raw
     generation rate (tok/s) from a dedicated micro-benchmark.

zh-TW answer *quality* is the one axis a script shouldn't fake: every answer is
dumped to a Markdown grading sheet for a human pass.

Run (Windows: set ``PYTHONUTF8=1``; needs the repo .venv + Ollama up):

    python -m local_llm.eval.benchmark                       # default: config model
    python -m local_llm.eval.benchmark qwen2.5:3b breeze2:3b # compare two models

Requires the local snapshot (``scripts/sync_snapshot.py``) and the ingested
knowledge base (``python -m local_llm.rag.ingest``).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from local_llm import config
from local_llm.eval.questions import QUESTIONS
from local_llm.qa.router import answer
from local_llm.tools.schemas import DATA_TOOLS

DATA_TOOL_NAMES = set(DATA_TOOLS)

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# 原始生成速度微基準：固定提示、不帶工具，量純 decode tok/s（不含檢索與工具往返）。
_SPEED_PROMPT = "請用三句話說明什麼是本益比。"


def available_models() -> set[str]:
    """回傳 Ollama 目前已安裝的模型 tag 集合。"""
    try:
        resp = requests.get(f"{config.OLLAMA_HOST}/api/tags", timeout=10)
        resp.raise_for_status()
        return {m["name"] for m in resp.json().get("models", [])}
    except requests.RequestException:
        return set()


def _resolve(model: str, installed: set[str]) -> str | None:
    """把使用者給的模型名對到實際 tag（容許省略 ``:latest``）。找不到回 None。"""
    if model in installed:
        return model
    if f"{model}:latest" in installed:
        return f"{model}:latest"
    base = model.split(":")[0]
    for tag in installed:
        if tag.split(":")[0] == base:
            return tag
    return None


def raw_tok_per_sec(model: str) -> float | None:
    """量單模型的純生成速度（tok/s）。先跑一次熱機，再取第二次的計時。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _SPEED_PROMPT}],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    try:
        requests.post(f"{config.OLLAMA_HOST}/api/chat", json=payload, timeout=300)  # 熱機
        resp = requests.post(f"{config.OLLAMA_HOST}/api/chat", json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        n = data.get("eval_count")
        dur_ns = data.get("eval_duration")
        if n and dur_ns:
            return round(n / (dur_ns / 1e9), 1)
    except requests.RequestException:
        pass
    return None


def _norm_code(v: object) -> str:
    """把工具參數裡的 code 正規化成純數字字串（模型可能帶 '2330'、2330、'2330.TW'）。"""
    return "".join(ch for ch in str(v) if ch.isdigit())


def score_one(item: dict, out: dict) -> dict:
    """依題目路徑對單一模型回覆評分。回傳含 pass 與診斷欄位的 dict。"""
    called = [t["name"] for t in out["tool_calls"]]
    data_called = [t for t in out["tool_calls"] if t["name"] in DATA_TOOL_NAMES]
    ref_sources = {r.get("source") for r in out["refs"]}
    route = item["route"]

    passed = False
    detail = ""
    tool_intent = None  # data 題專用：模型有無「選對工具」，含把呼叫寫進純文字的情形
    if route == "data":
        want_tool = item["expect_tool"]
        tool_ok = want_tool in called
        code_ok = True
        if item.get("expect_code"):
            want = item["expect_code"]
            code_ok = any(
                t["name"] == want_tool and _norm_code(t["args"].get("code", "")) == want
                for t in out["tool_calls"]
            )
        passed = tool_ok and code_ok  # 主指標＝生產管線真的執行到工具（native tool_calls）
        # 意圖：即使模型把 function call 印成文字（未走 Ollama 原生 tool 通道），也算選對工具。
        tool_intent = tool_ok or (want_tool in (out.get("answer") or ""))
        detail = f"called={called or '—'}"
        if not tool_ok and tool_intent:
            detail += " (tool named in text, not executed)"
        elif not code_ok:
            detail += " (wrong/no code)"
    elif route == "rag":
        passed = item["expect_source"] in ref_sources
        detail = f"refs={sorted(s for s in ref_sources if s) or '—'}"
    elif route == "oob":
        # 誠實降級：沒亂呼叫資料工具，也沒被灌進（誤命中的）知識片段。
        passed = not data_called and not ref_sources
        bad = []
        if data_called:
            bad.append(f"data_tool={[t['name'] for t in data_called]}")
        if ref_sources:
            bad.append(f"false_refs={sorted(s for s in ref_sources if s)}")
        detail = "; ".join(bad) if bad else "clean downgrade"

    return {
        "id": item["id"],
        "route": route,
        "pass": passed,
        "tool_intent": tool_intent,
        "detail": detail,
        "tool_calls": called,
        "refs": sorted(s for s in ref_sources if s),
    }


def run_model(model: str) -> dict:
    """對單一模型跑完整題組，回傳逐題結果與彙總指標。"""
    print(f"\n=== {model} ===", file=sys.stderr)
    # 熱機一次，避免第一題吃到 GPU 冷載入而灌大延遲。
    try:
        answer("暖機", model=model)
    except Exception:  # noqa: BLE001 — 暖機失敗不致命，續跑
        pass

    per_q: list[dict] = []
    for item in QUESTIONS:
        t0 = time.perf_counter()
        try:
            out = answer(item["q"], model=model)
            err = None
        except Exception as e:  # noqa: BLE001 — 單題失敗記錄後續跑
            out = {"answer": f"[ERROR] {type(e).__name__}: {e}", "tool_calls": [], "refs": []}
            err = str(e)
        latency = round(time.perf_counter() - t0, 2)

        sc = score_one(item, out)
        sc["latency_s"] = latency
        sc["answer"] = out["answer"]
        sc["error"] = err
        per_q.append(sc)
        mark = "PASS" if sc["pass"] else "FAIL"
        print(f"  [{mark}] {item['id']:<18} {latency:>5}s  {sc['detail']}", file=sys.stderr)

    agg = _aggregate(per_q)
    agg["tok_per_sec"] = raw_tok_per_sec(model)
    return {"model": model, "questions": per_q, "aggregate": agg}


def _rate(items: list[dict], route: str) -> tuple[int, int]:
    subset = [q for q in items if q["route"] == route]
    return sum(1 for q in subset if q["pass"]), len(subset)


def _aggregate(per_q: list[dict]) -> dict:
    d_pass, d_n = _rate(per_q, "data")
    r_pass, r_n = _rate(per_q, "rag")
    o_pass, o_n = _rate(per_q, "oob")
    total_pass = d_pass + r_pass + o_pass
    total_n = d_n + r_n + o_n
    latencies = [q["latency_s"] for q in per_q]
    data_q = [q for q in per_q if q["route"] == "data"]
    intent_pass = sum(1 for q in data_q if q.get("tool_intent"))
    return {
        "tool_use": [d_pass, d_n],
        "tool_intent": [intent_pass, len(data_q)],
        "rag_grounding": [r_pass, r_n],
        "honest_downgrade": [o_pass, o_n],
        "overall": [total_pass, total_n],
        "mean_latency_s": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "max_latency_s": max(latencies) if latencies else None,
    }


def _pct(pair: list[int]) -> str:
    p, n = pair
    return f"{p}/{n} ({round(100 * p / n)}%)" if n else "—"


def markdown_table(runs: list[dict]) -> str:
    """把多個模型的彙總指標排成 Markdown 對照表。"""
    rows = [
        ("模型 Model", [r["model"] for r in runs]),
        ("工具執行成功 Tool-use (native)", [_pct(r["aggregate"]["tool_use"]) for r in runs]),
        ("工具選對意圖 Tool intent (incl. text)", [_pct(r["aggregate"]["tool_intent"]) for r in runs]),
        ("知識庫命中 RAG grounding", [_pct(r["aggregate"]["rag_grounding"]) for r in runs]),
        ("誠實降級 Honest downgrade", [_pct(r["aggregate"]["honest_downgrade"]) for r in runs]),
        ("整體正確 Overall", [_pct(r["aggregate"]["overall"]) for r in runs]),
        ("平均延遲 Mean latency", [f"{r['aggregate']['mean_latency_s']}s" for r in runs]),
        ("最慢單題 Max latency", [f"{r['aggregate']['max_latency_s']}s" for r in runs]),
        ("生成速度 Raw tok/s", [str(r["aggregate"]["tok_per_sec"] or "—") for r in runs]),
    ]
    model_names = rows[0][1]
    header = "| " + " | ".join(["指標 Metric"] + model_names) + " |"
    sep = "|" + "|".join(["---"] * (len(runs) + 1)) + "|"
    lines = [header, sep]
    for label, cells in rows[1:]:
        lines.append("| " + " | ".join([label] + cells) + " |")
    return "\n".join(lines)


def grading_sheet(runs: list[dict]) -> str:
    """產出人工評 zh-TW 品質用的 Markdown：每題並列各模型答案。"""
    out = ["# zh-TW 品質人工評分表\n", "每題 1–5 分（自然度／正確性／是否杜撰）。\n"]
    for item in QUESTIONS:
        out.append(f"\n## [{item['route']}] {item['id']} — {item['q']}")
        out.append(f"> 評分重點：{item['note']}\n")
        for r in runs:
            sc = next((s for s in r["questions"] if s["id"] == item["id"]), None)
            if not sc:
                continue
            mark = "✅" if sc["pass"] else "❌"
            out.append(f"**{r['model']}** {mark} （{sc['latency_s']}s，score ___/5）")
            out.append(f"```\n{sc['answer']}\n```")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    models = argv or [config.LLM_MODEL]
    installed = available_models()
    if not installed:
        print("Ollama 無回應（http://localhost:11434）——請先 `ollama serve`。", file=sys.stderr)
        return 2

    runs: list[dict] = []
    for m in models:
        resolved = _resolve(m, installed)
        if resolved is None:
            print(f"⚠️  略過 '{m}'：Ollama 未安裝（現有：{sorted(installed)}）。", file=sys.stderr)
            continue
        runs.append(run_model(resolved))

    if not runs:
        print("沒有可跑的模型。", file=sys.stderr)
        return 1

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (RESULTS_DIR / f"bench_{stamp}.json").write_text(
        json.dumps(runs, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (RESULTS_DIR / f"answers_{stamp}.md").write_text(grading_sheet(runs), encoding="utf-8")

    table = markdown_table(runs)
    print("\n" + table)
    print(f"\n完整結果：{RESULTS_DIR / f'bench_{stamp}.json'}", file=sys.stderr)
    print(f"人工評分表：{RESULTS_DIR / f'answers_{stamp}.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
