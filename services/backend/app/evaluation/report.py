"""Assembling and writing the evaluation report: versioned JSON plus a
Markdown rendering (task-19-brief.md requirements 9-10)."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Number = float | int


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_directory(directory: Path) -> str:
    """A stable digest over every file's relative path and contents,
    sorted by path so the digest never depends on filesystem iteration
    order.
    """
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(directory)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def git_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def git_dirty(repo_root: Path) -> bool:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "status", "--porcelain"],  # noqa: S607
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return bool(result.stdout.strip())
    except OSError:
        return False


def build_targets(sections: dict[str, Any]) -> tuple[dict[str, Number], dict[str, dict[str, Any]]]:
    """The named, numeric targets from task-19-brief.md requirements 2-5,
    and how each one actually did. Targets are never adjusted after seeing
    results.
    """
    targets: dict[str, Number] = {
        "retrieval_hybrid_recall_at_5": 0.85,
        "entities_micro_f1": 0.90,
        "classification_tfidf_macro_f1": 0.80,
        "calculations_all_pass": 1,
    }
    actuals: dict[str, Any] = {
        "retrieval_hybrid_recall_at_5": sections["retrieval"]["modes"]["hybrid"]["recall_at_5"],
        "entities_micro_f1": sections["entities"]["micro_f1"],
        "classification_tfidf_macro_f1": sections["classification"]["tfidf"]["macro_f1"],
        "calculations_all_pass": 1 if sections["calculations"]["all_passed"] else 0,
    }
    target_results = {
        name: {
            "target": target,
            "actual": actuals[name],
            "met": actuals[name] >= target,
        }
        for name, target in targets.items()
    }
    return targets, target_results


def build_report(
    *,
    embedder_name: str,
    classifier_version: str,
    prompt_versions: dict[str, str],
    llm_provider: str,
    llm_model: str,
    corpus_sha256: str,
    notes_test_sha256: str,
    questions_sha256: str,
    seed: int,
    retrieval: dict[str, Any],
    entities: dict[str, Any],
    classification: dict[str, Any],
    calculations: dict[str, Any],
    agents: dict[str, Any],
    fairness: dict[str, Any],
    security: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    sections = {
        "retrieval": retrieval,
        "entities": entities,
        "classification": classification,
        "calculations": calculations,
    }
    targets, target_results = build_targets(sections)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(repo_root),
        "git_dirty": git_dirty(repo_root),
        "python": sys.version,
        "platform": platform.platform(),
        "versions": {
            "embedder": embedder_name,
            "classifier": classifier_version,
            "prompts": prompt_versions,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "corpus_sha256": corpus_sha256,
            "notes_test_sha256": notes_test_sha256,
            "questions_sha256": questions_sha256,
            "seed": seed,
        },
        "retrieval": retrieval,
        "entities": entities,
        "classification": classification,
        "calculations": calculations,
        "agents": agents,
        "fairness": fairness,
        "security": security,
        "targets": targets,
        "target_results": target_results,
    }


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# LineSense evaluation report")
    lines.append("")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Git commit: `{report['git_commit']}`{' (dirty)' if report['git_dirty'] else ''}")
    lines.append(f"Embedder: `{report['versions']['embedder']}`")
    lines.append(f"LLM provider: `{report['versions']['llm_provider']}` (**never a live model**)")
    lines.append("")

    lines.append("## Targets")
    lines.append("")
    lines.append("| Target | Threshold | Actual | Met |")
    lines.append("|---|---|---|---|")
    for name, result in report["target_results"].items():
        met = "yes" if result["met"] else "**NO**"
        lines.append(f"| {name} | {_fmt(result['target'])} | {_fmt(result['actual'])} | {met} |")
    lines.append("")

    retrieval = report["retrieval"]
    lines.append("## Retrieval")
    lines.append("")
    lines.append(f"Questions evaluated: {retrieval['questions_evaluated']}")
    lines.append("")
    lines.append("| Mode | Recall@5 | MRR@10 | Failures |")
    lines.append("|---|---|---|---|")
    for mode, data in retrieval["modes"].items():
        lines.append(
            f"| {mode} | {_fmt(data['recall_at_5'])} | {_fmt(data['mrr_at_10'])} "
            f"| {len(data['failures'])} |"
        )
    lines.append("")

    entities = report["entities"]
    lines.append("## Entities")
    lines.append("")
    lines.append(
        f"Notes evaluated: {entities['notes_evaluated']} -- "
        f"micro P/R/F1: {_fmt(entities['micro_precision'])}/"
        f"{_fmt(entities['micro_recall'])}/{_fmt(entities['micro_f1'])}"
    )
    lines.append("")
    lines.append("| Label | Precision | Recall | F1 | Support |")
    lines.append("|---|---|---|---|---|")
    for label, scores in entities["per_label"].items():
        lines.append(
            f"| {label} | {_fmt(scores['precision'])} | {_fmt(scores['recall'])} "
            f"| {_fmt(scores['f1'])} | {scores['support']} |"
        )
    lines.append("")

    classification = report["classification"]
    lines.append("## Classification")
    lines.append("")
    for name in ("keyword_baseline", "tfidf"):
        data = classification[name]
        lines.append(
            f"- **{name}**: accuracy {_fmt(data['accuracy'])}, macro-F1 {_fmt(data['macro_f1'])}"
        )
    lines.append("")

    calculations = report["calculations"]
    lines.append("## Calculations")
    lines.append("")
    for check in calculations["checks"]:
        status = "PASS" if check["passed"] else "**FAIL**"
        lines.append(
            f"- {status}: `{check['name']}`" + (f" -- {check['error']}" if check["error"] else "")
        )
    lines.append("")

    agents = report["agents"]
    lines.append("## Agents (fixture provider -- NOT a live-LLM evaluation)")
    lines.append("")
    lines.append(
        f"Scenarios completed: {agents['scenarios_completed']}/{agents['scenarios_requested']} "
        f"({len(agents['errors'])} errored)"
    )
    lines.append("")
    lines.append("| Approach | Material-conflict accuracy | Capacity-sufficient accuracy |")
    lines.append("|---|---|---|")
    for approach in ("deterministic_baseline", "single_agent_baseline", "four_agent_flow"):
        mc = agents["accuracy"]["material_conflict"][approach]
        cap = agents["accuracy"]["capacity_sufficient"][approach]
        mc_str = _fmt(mc) if mc is not None else "n/a"
        cap_str = _fmt(cap) if cap is not None else "n/a"
        lines.append(f"| {approach} | {mc_str} | {cap_str} |")
    lines.append("")
    lines.append(f"Note: {agents['single_agent_baseline_note']}")
    lines.append("")

    fairness = report["fairness"]
    lines.append("## Fairness (fixture provider -- NOT a live-LLM evaluation)")
    lines.append("")
    rate = fairness["pass_rate"]
    lines.append(
        f"Pairs completed: {fairness['pairs_completed']}/{fairness['pairs_requested']} "
        f"-- pass rate: {_fmt(rate) if rate is not None else 'n/a'}"
    )
    lines.append("")
    lines.append(f"Limitation: {fairness['limitation']}")
    lines.append("")

    security = report["security"]
    lines.append("## Security")
    lines.append("")
    injection = security["prompt_injection"]
    lines.append(f"Prompt-injection suite: {injection['blocked']}/{injection['total']} blocked")
    abstention = security["abstention"]
    lines.append(f"Abstention checks: {abstention['passed']}/{abstention['total']} passed")
    lines.append("")

    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    timestamped_path = output_dir / f"eval-{timestamp}.json"
    latest_json_path = output_dir / "latest.json"
    latest_md_path = output_dir / "latest.md"

    payload = json.dumps(report, indent=2, sort_keys=True, default=str)
    timestamped_path.write_text(payload + "\n", encoding="utf-8")
    latest_json_path.write_text(payload + "\n", encoding="utf-8")
    latest_md_path.write_text(render_markdown(report), encoding="utf-8")
    return timestamped_path, latest_json_path, latest_md_path
