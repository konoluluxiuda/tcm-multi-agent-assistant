"""
Minimal RAG evaluation harness for the Multi-Agent Medical Assistant.

Two phases:
  1. --gen-qa  : sample chunks from the ingested corpus and auto-generate QA pairs
                 (question + reference answer + source doc) using the chat model.
  2. --eval    : run each question through the full agent and compute label-free
                 metrics + a badcase list.

Modes:
  --verify     check the vector DB dimension/point count and run one live probe
  --gen-qa     (re)generate data/eval/qa_dataset.json from the corpus
  --eval       run the evaluation against the cached dataset (add --judge for LLM judge)
  --all        --gen-qa then --eval

COUPLING NOTE: this script deliberately avoids editing agents/**. It relies on the
following internals, which a refactor could break silently:
  - agents.agent_decision.process_query(query) -> full graph state dict
  - the state keys: messages[-1].content, agent_name, retrieval_confidence, insufficient_info
  - agents.agent_decision.process_query(session_id=...) for memory isolation
  - agents.agent_decision.MedicalRAG (swapped for a source-capturing subclass)
  - agents.rag_agent.MedicalRAG.process_query -> {response, sources, confidence}
"""

import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from config import Config, create_chat_model

config = Config()

STOPWORDS = set(
    "the a an of and or to in is are was were be been being it its this that these those "
    "for on with as by at from into than then so such not no do does did can could would "
    "should will may might must if but while which who whom whose what when where why how".split()
)


# --------------------------------------------------------------------------- utils

def _parse_json(text: str) -> dict:
    """Best-effort JSON parse, tolerating markdown code fences and surrounding prose."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def content_tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def keyword_recall(reference: str, answer: str) -> float:
    ref = content_tokens(reference)
    if not ref:
        return 0.0
    return len(ref & content_tokens(answer)) / len(ref)


def _last_content(state: dict) -> str:
    msgs = state.get("messages") or []
    if not msgs:
        return ""
    return getattr(msgs[-1], "content", str(msgs[-1]))


# ------------------------------------------------------------------- corpus / QA gen

def scroll_corpus() -> list:
    """Read all ingested chunks back from Qdrant (payload: page_content + metadata)."""
    from qdrant_client import QdrantClient

    client = QdrantClient(path=config.rag.vector_local_path)
    chunks = []
    try:
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name=config.rag.collection_name,
                with_payload=True,
                limit=256,
                offset=offset,
            )
            for p in points:
                payload = p.payload or {}
                text = payload.get("page_content") or payload.get("content") or ""
                meta = payload.get("metadata") or {}
                source = meta.get("source") or payload.get("source") or "unknown"
                if text.strip():
                    chunks.append({"text": text, "source": source})
            if offset is None:
                break
    finally:
        client.close()
    return chunks


def sample_chunks(chunks: list, n_per_doc: int, seed: int) -> list:
    """Deterministic per-document sample."""
    by_doc = {}
    for c in chunks:
        by_doc.setdefault(c["source"], []).append(c)
    rng = random.Random(seed)
    sampled = []
    for source in sorted(by_doc):
        items = by_doc[source]
        if len(items) > n_per_doc:
            items = rng.sample(items, n_per_doc)
        sampled.extend(items)
    return sampled


QA_PROMPT = """You are building an evaluation set for a medical RAG system.
Using ONLY the excerpt below from "{source}", write ONE specific, factual question that is
fully answerable from the excerpt, plus a concise reference answer grounded only in the
excerpt. Do not use outside knowledge. Avoid yes/no questions.
Return strict JSON only: {{"question": "...", "answer": "..."}}

Excerpt:
{chunk}"""


def generate_qa(chunks: list, n_per_doc: int, seed: int) -> list:
    model = create_chat_model(temperature=0.0)
    samples = sample_chunks(chunks, n_per_doc, seed)
    qa, seen = [], set()
    for i, c in enumerate(samples):
        prompt = QA_PROMPT.format(source=c["source"], chunk=c["text"][:4000])
        item = None
        for attempt in range(2):
            try:
                raw = model.invoke(prompt).content
                parsed = _parse_json(raw)
                if parsed.get("question") and parsed.get("answer"):
                    item = parsed
                    break
            except Exception as e:
                print(f"  [gen-qa] item {i} attempt {attempt + 1} failed: {e}")
        if not item:
            continue
        key = re.sub(r"\W+", " ", item["question"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        qa.append({
            "id": f"q{len(qa) + 1:03d}",
            "question": item["question"].strip(),
            "reference_answer": item["answer"].strip(),
            "source": c["source"],
            "evidence_snippet": c["text"][:1500],
        })
        print(f"  [gen-qa] {len(qa)} pairs generated")
    return qa


# ------------------------------------------------------------------------ agent eval

def make_recorder():
    """Subclass MedicalRAG to capture sources/confidence, which the top-level state drops."""
    import agents.agent_decision as ad
    from agents.rag_agent import MedicalRAG

    class RecordingMedicalRAG(MedicalRAG):
        captured = []

        def process_query(self, query, chat_history=None):
            res = super().process_query(query, chat_history)
            RecordingMedicalRAG.captured.append(res)
            return res

    return ad, RecordingMedicalRAG


def run_case(ad, Recorder, qa: dict, idx: int) -> dict:
    Recorder.captured = []
    ad.MedicalRAG = Recorder

    rec = {
        "id": qa["id"],
        "question": qa["question"],
        "reference_answer": qa["reference_answer"],
        "expected_source": qa["source"],
    }
    t0 = time.time()
    try:
        state = ad.process_query(qa["question"], session_id=f"eval-{idx}")
        rec["answer"] = _last_content(state)
        rec["agent_name"] = state.get("agent_name")
        rec["retrieval_confidence"] = float(state.get("retrieval_confidence", 0.0) or 0.0)
        rec["insufficient_info"] = bool(state.get("insufficient_info", False))
        rag = Recorder.captured[-1] if Recorder.captured else {}
        rec["retrieved_sources"] = [s.get("path") for s in (rag.get("sources") or [])]
        rec["rag_confidence"] = float(rag.get("confidence", 0.0)) if rag else None
    except Exception as e:
        rec.update({
            "answer": "", "agent_name": "ERROR", "retrieval_confidence": 0.0,
            "insufficient_info": False, "retrieved_sources": [], "rag_confidence": None,
            "error": f"{type(e).__name__}: {e}",
        })
    rec["latency"] = round(time.time() - t0, 2)
    rec["keyword_recall"] = round(keyword_recall(rec["reference_answer"], rec.get("answer", "")), 3)
    return rec


def judge_answer(question: str, evidence: str, answer: str, model) -> dict:
    prompt = f"""You are grading a medical RAG answer. Given the question, the source
evidence, and the answer, decide whether the answer is factually supported by the evidence
and correctly answers the question. Return strict JSON only:
{{"score": 0-5, "correct": true/false, "reason": "..."}}

Question: {question}

Evidence:
{evidence}

Answer:
{answer}"""
    try:
        return _parse_json(model.invoke(prompt).content)
    except Exception as e:
        return {"score": None, "correct": None, "reason": f"judge error: {e}"}


# ---------------------------------------------------------------------------- metrics

def _pct(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def compute_metrics(cases: list) -> dict:
    n = len(cases)
    if n == 0:
        return {"n_cases": 0}

    def _basename(p):
        return (p or "").rstrip("/").split("/")[-1].lower()

    def _hit(c):
        expected = _basename(c["expected_source"])
        retrieved = [_basename(p) for p in (c.get("retrieved_sources") or [])]
        return bool(expected and any(expected in r for r in retrieved))

    # Cases that actually reached retrieval (excludes guardrail-blocked / errored cases,
    # which never retrieve and would otherwise be counted as retrieval misses).
    rag = [c for c in cases if c.get("rag_confidence") is not None]
    routing = Counter(c.get("agent_name") for c in cases)

    def _conf_stats(subset):
        vals = sorted(c.get("retrieval_confidence", 0.0) for c in subset)
        if not vals:
            return {}
        below = sum(1 for v in vals if v < config.rag.min_retrieval_confidence)
        return {
            "min": round(vals[0], 3),
            "p25": round(_pct(vals, 0.25), 3),
            "median": round(_pct(vals, 0.5), 3),
            "p75": round(_pct(vals, 0.75), 3),
            "max": round(vals[-1], 3),
            "mean": round(sum(vals) / len(vals), 3),
            "fraction_below_threshold": round(below / len(vals), 3),
        }

    recalls = sorted(c.get("keyword_recall", 0.0) for c in cases)
    metrics = {
        "n_cases": n,
        "min_retrieval_confidence": config.rag.min_retrieval_confidence,
        "retrieval_hit_rate": round(sum(1 for c in cases if _hit(c)) / n, 3),
        "rag_attempted": len(rag),
        "retrieval_hit_rate_among_rag": round(sum(1 for c in rag if _hit(c)) / len(rag), 3) if rag else None,
        "rag_stay_rate": round(routing.get("RAG_AGENT", 0) / n, 3),
        "guardrail_blocked": routing.get("INPUT_GUARDRAILS", 0),
        "routing": dict(routing),
        "confidence_rag": _conf_stats(rag),
        "confidence_all": _conf_stats(cases),
        "keyword_recall": {
            "mean": round(sum(recalls) / n, 3),
            "median": round(_pct(recalls, 0.5), 3),
        },
        "errors": sum(1 for c in cases if c.get("error")),
        "mean_latency_s": round(sum(c.get("latency", 0.0) for c in cases) / n, 2),
    }
    judged = [c for c in cases if c.get("judge")]
    if judged:
        valid = [c["judge"] for c in judged if c["judge"].get("correct") is not None]
        metrics["judge"] = {
            "n_judged": len(valid),
            "correct_rate": round(sum(1 for j in valid if j["correct"]) / len(valid), 3) if valid else None,
            "mean_score": round(sum(j["score"] for j in valid if j.get("score") is not None) / len(valid), 2) if valid else None,
        }
    return metrics


LOW_OVERLAP_THRESHOLD = 0.3


def classify_badcases(cases: list) -> list:
    bad = []
    for c in cases:
        reasons = []
        expected = (c["expected_source"] or "").rstrip("/").split("/")[-1].lower()
        retrieved = [((p or "").rstrip("/").split("/")[-1].lower()) for p in (c.get("retrieved_sources") or [])]
        # Only a retrieval miss if retrieval actually ran (guardrail/errors never retrieve).
        if c.get("rag_confidence") is not None and expected and not any(expected in r for r in retrieved):
            reasons.append("retrieval_miss")
        if c.get("agent_name") == "INPUT_GUARDRAILS":
            reasons.append("guardrail_blocked")
        elif c.get("agent_name") != "RAG_AGENT":
            reasons.append("routed_away")
        if c.get("insufficient_info"):
            reasons.append("insufficient")
        if c.get("keyword_recall", 0.0) < LOW_OVERLAP_THRESHOLD:
            reasons.append("low_answer_overlap")
        if c.get("judge") and c["judge"].get("correct") is False:
            reasons.append("judge_fail")
        if c.get("error"):
            msg = str(c["error"]).lower()
            reasons.append("web_search_no_key" if "tavily" in msg else "error")
        if reasons:
            bad.append({
                "id": c["id"],
                "reasons": reasons,
                "question": c["question"],
                "reference_answer": c["reference_answer"],
                "expected_source": c["expected_source"],
                "retrieved_sources": c.get("retrieved_sources"),
                "agent_name": c.get("agent_name"),
                "retrieval_confidence": c.get("retrieval_confidence"),
                "keyword_recall": c.get("keyword_recall"),
                "answer": (c.get("answer") or "")[:800],
                "judge": c.get("judge"),
                "error": c.get("error"),
            })
    return bad


# ---------------------------------------------------------------------------- reports

def write_reports(qa, cases, metrics, badcases, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {
        "timestamp": stamp,
        "n_cases": len(cases),
        "chat_model": config.rag.llm.model_name,
        "embedding_model": config.rag.embedding_model.model,
        "embedding_dim": config.rag.embedding_dim,
        "collection": config.rag.collection_name,
        "min_retrieval_confidence": config.rag.min_retrieval_confidence,
    }
    report = {"meta": meta, "metrics": metrics, "cases": cases, "badcases": badcases}
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "badcases.json").write_text(json.dumps(badcases, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# RAG Evaluation Summary", "", f"- Generated: {stamp}", f"- Cases: {metrics.get('n_cases')}",
             f"- Chat model: {meta['chat_model']} | Embedding: {meta['embedding_model']} ({meta['embedding_dim']}d)",
             f"- Badcases: {len(badcases)}", ""]
    lines += ["## Metrics", "", "| Metric | Value |", "|---|---|"]
    for k in ("n_cases", "retrieval_hit_rate", "retrieval_hit_rate_among_rag", "rag_attempted",
              "rag_stay_rate", "guardrail_blocked", "errors", "mean_latency_s"):
        if k in metrics:
            lines.append(f"| {k} | {metrics[k]} |")
    for k, v in metrics.get("confidence_rag", {}).items():
        lines.append(f"| confidence_rag.{k} | {v} |")
    for k, v in metrics.get("keyword_recall", {}).items():
        lines.append(f"| keyword_recall.{k} | {v} |")
    if "judge" in metrics:
        for k, v in metrics["judge"].items():
            lines.append(f"| judge.{k} | {v} |")
    lines += ["", "## Routing distribution", ""]
    for agent, cnt in sorted(metrics.get("routing", {}).items(), key=lambda x: -x[1]):
        lines.append(f"- {agent}: {cnt}")
    reason_counts = Counter(r for b in badcases for r in b["reasons"])
    if reason_counts:
        lines += ["", "## Badcase reasons", ""]
        for reason, cnt in reason_counts.most_common():
            lines.append(f"- {reason}: {cnt}")
    lines += ["", f"## Badcases ({len(badcases)})", ""]
    for b in badcases[:20]:
        lines.append(f"### {b['id']} — {', '.join(b['reasons'])}")
        lines.append(f"- Q: {b['question']}")
        lines.append(f"- expected source: {b['expected_source']} | routing: {b['agent_name']} | confidence: {b['retrieval_confidence']} | kw recall: {b['keyword_recall']}")
        if b.get("error"):
            lines.append(f"- error: {b['error']}")
        lines.append("")
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote: {out_dir}/report.json, summary.md, badcases.json")


# ------------------------------------------------------------------------------- verify

def verify_db():
    from qdrant_client import QdrantClient

    client = QdrantClient(path=config.rag.vector_local_path)
    try:
        info = client.get_collection(config.rag.collection_name)
        vectors = info.config.params.vectors
        print("collection:", config.rag.collection_name)
        print("vectors:", vectors)
        print("points:", client.count(config.rag.collection_name).count)
        size = None
        if isinstance(vectors, dict) and "dense" in vectors:
            size = getattr(vectors["dense"], "size", None)
    finally:
        client.close()

    expected = config.rag.embedding_dim
    ok = size == expected
    print(f"expected dim: {expected} | actual: {size} -> {'OK' if ok else 'MISMATCH'}")
    if not ok:
        print("Delete data/qdrant_db/ and re-run ingest.")

    from agents.rag_agent import MedicalRAG
    rag = MedicalRAG(config)
    res = rag.process_query("What is described about the disease in this document?")
    print("probe confidence:", res.get("confidence"), "| sources:", len(res.get("sources") or []))
    return ok


# --------------------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="Minimal RAG evaluation harness")
    p.add_argument("--verify", action="store_true", help="check DB dim/count and run a probe")
    p.add_argument("--gen-qa", action="store_true", help="generate QA pairs from the corpus")
    p.add_argument("--eval", action="store_true", help="run evaluation against the cached dataset")
    p.add_argument("--all", action="store_true", help="gen-qa then eval")
    p.add_argument("--judge", action="store_true", help="add LLM-as-judge scoring (extra call per case)")
    p.add_argument("--n-per-doc", type=int, default=10, help="QA pairs per source document")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--recompute", action="store_true", help="recompute metrics/badcases from an existing report.json (no agent calls)")
    p.add_argument("--out", default="data/eval", help="output directory")
    args = p.parse_args()

    out_dir = Path(args.out)
    qa_path = out_dir / "qa_dataset.json"

    if args.verify:
        sys.exit(0 if verify_db() else 1)

    if args.recompute:
        report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        cases = report["cases"]
        qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() else []
        metrics = compute_metrics(cases)
        badcases = classify_badcases(cases)
        write_reports(qa, cases, metrics, badcases, out_dir)
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        return

    do_gen = args.gen_qa or args.all
    do_eval = args.eval or args.all
    if not (do_gen or do_eval):
        p.error("choose one of --verify / --gen-qa / --eval / --all")

    if do_gen:
        print("Scrolling corpus from Qdrant...")
        chunks = scroll_corpus()
        print(f"  {len(chunks)} chunks across {len({c['source'] for c in chunks})} documents")
        print(f"Generating QA pairs (n_per_doc={args.n_per_doc})...")
        qa = generate_qa(chunks, args.n_per_doc, args.seed)
        out_dir.mkdir(parents=True, exist_ok=True)
        qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {len(qa)} QA pairs -> {qa_path}")

    if do_eval:
        if not qa_path.exists():
            p.error(f"{qa_path} not found; run --gen-qa first")
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        print(f"Evaluating {len(qa)} cases through the agent...")
        ad, Recorder = make_recorder()
        judge_model = create_chat_model(temperature=0.0) if args.judge else None
        cases = []
        for i, item in enumerate(qa):
            rec = run_case(ad, Recorder, item, i)
            if judge_model:
                rec["judge"] = judge_answer(item["question"], item.get("evidence_snippet", ""), rec["answer"], judge_model)
            cases.append(rec)
            flag = "BAD" if rec.get("agent_name") != "RAG_AGENT" or rec.get("error") else "ok"
            print(f"  [{i + 1}/{len(qa)}] {rec['id']} {flag} conf={rec.get('retrieval_confidence')} recall={rec.get('keyword_recall')}")
        metrics = compute_metrics(cases)
        badcases = classify_badcases(cases)
        write_reports(qa, cases, metrics, badcases, out_dir)
        print("\nMetrics:")
        print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
