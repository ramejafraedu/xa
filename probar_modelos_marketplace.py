"""Probe and rank models from GitHub Models and Gemini API.

This script can:
    1) Discover models from GitHub Marketplace and test them in chat/completions.
    2) Test the model list shown in VS Code model picker.
    3) Test Gemini models using Gemini native API (google-genai).

Usage examples:
    python probar_modelos_marketplace.py --mode vscode
    python probar_modelos_marketplace.py --mode marketplace --max-models 20
    python probar_modelos_marketplace.py --mode all --top 8
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from config import settings

try:
        from google import genai
except Exception:  # pragma: no cover - optional runtime dependency
        genai = None


MARKETPLACE_PAGES = [
    "https://github.com/marketplace?type=models",
    "https://github.com/marketplace?type=models&page=2",
    "https://github.com/marketplace?type=models&page=3",
]

MODEL_LINK_RE = re.compile(r"href=[\"']/marketplace/models/([^/\"'\s>]+)/([^\"'\s>?#]+)[\"']")

DEFAULT_MODELS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "o4-mini",
    "o3",
    "o3-mini",
    "Phi-4-mini-instruct",
    "Phi-4",
]

VSCODE_MODEL_PICKER = [
    {"display": "Claude Haiku 4.5", "provider": "github"},
    {"display": "Gemini 2.5 Pro", "provider": "gemini"},
    {"display": "Gemini 3 Flash (Preview)", "provider": "gemini"},
    {"display": "Gemini 3.1 Pro (Preview)", "provider": "gemini"},
    {"display": "GPT-4.1", "provider": "github"},
    {"display": "GPT-4o", "provider": "github"},
    {"display": "GPT-5 mini", "provider": "github"},
    {"display": "GPT-5.1", "provider": "github"},
    {"display": "GPT-5.2", "provider": "github"},
    {"display": "GPT-5.2-Codex", "provider": "github"},
    {"display": "GPT-5.3-Codex", "provider": "github"},
    {"display": "GPT-5.4 mini", "provider": "github"},
    {"display": "Grok Code Fast 1", "provider": "github"},
    {"display": "Raptor mini (Preview)", "provider": "github"},
]

EXPLICIT_GITHUB_ALIASES = {
    "Claude Haiku 4.5": ["claude-haiku-4.5", "claude-haiku-4-5"],
    "GPT-4.1": ["gpt-4.1", "gpt-4-1"],
    "GPT-4o": ["gpt-4o"],
    "GPT-5 mini": ["gpt-5-mini"],
    "GPT-5.1": ["gpt-5.1", "gpt-5-1"],
    "GPT-5.2": ["gpt-5.2", "gpt-5-2"],
    "GPT-5.2-Codex": ["gpt-5.2-codex", "gpt-5-2-codex"],
    "GPT-5.3-Codex": ["gpt-5.3-codex", "gpt-5-3-codex"],
    "GPT-5.4 mini": ["gpt-5.4-mini", "gpt-5-4-mini"],
    "Grok Code Fast 1": ["grok-code-fast-1"],
    "Raptor mini (Preview)": ["raptor-mini-preview", "raptor-mini"],
}

EXPLICIT_GEMINI_ALIASES = {
    "Gemini 2.5 Pro": ["gemini-2.5-pro", "models/gemini-2.5-pro"],
    "Gemini 3 Flash (Preview)": ["gemini-3-flash-preview", "models/gemini-3-flash-preview"],
    "Gemini 3.1 Pro (Preview)": ["gemini-3.1-pro-preview", "models/gemini-3.1-pro-preview"],
}

PROBE_PROMPT = (
    "Devuelve SOLO JSON valido sin markdown con estas keys: "
    "titulo, gancho, cta. Tema: ahorro inteligente en 2026."
)


@dataclass
class Candidate:
    display_name: str
    provider: str  # github | gemini
    variants: list[str]


@dataclass
class ProbeResult:
    display_name: str
    provider: str
    requested_model: str
    resolved_model: str
    ping_ok: bool
    json_ok: bool
    avg_latency_sec: float | None
    score: float
    error: str = ""


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.strip()
        if not key:
            continue
        k_lower = key.lower()
        if k_lower in seen:
            continue
        seen.add(k_lower)
        out.append(key)
    return out


def _slugify_model_name(text: str) -> str:
    value = text.strip().lower()
    value = value.replace("(", " ").replace(")", " ")
    value = re.sub(r"[^a-z0-9.+-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def _model_variants_from_display(display_name: str, provider: str) -> list[str]:
    if provider == "github" and display_name in EXPLICIT_GITHUB_ALIASES:
        return unique(EXPLICIT_GITHUB_ALIASES[display_name])
    if provider == "gemini" and display_name in EXPLICIT_GEMINI_ALIASES:
        return unique(EXPLICIT_GEMINI_ALIASES[display_name])

    slug = _slugify_model_name(display_name)
    variants = [
        slug,
        slug.replace(".", "-"),
        slug.replace(".", ""),
    ]
    return unique(variants)


def _looks_like_chat_model(model_id: str) -> bool:
    value = model_id.lower()
    leaf = value.split("/")[-1]

    if leaf.endswith((".svg", ".png", ".jpg", ".jpeg", ".webp")):
        return False

    if any(token in leaf for token in ("embedding", "rerank", "moderation")):
        return False

    prefixes = (
        "gpt",
        "o1",
        "o3",
        "o4",
        "phi",
        "llama",
        "mistral",
        "qwen",
        "deepseek",
        "jamba",
        "ai21",
    )
    return leaf.startswith(prefixes)


def fetch_marketplace_models(timeout: float) -> list[str]:
    headers = {"User-Agent": "video-factory-model-probe/1.0"}
    found: list[str] = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for url in MARKETPLACE_PAGES:
            try:
                response = client.get(url, headers=headers)
                if response.status_code >= 400:
                    continue

                html = response.text
                for provider, raw_slug in MODEL_LINK_RE.findall(html):
                    slug = unquote(raw_slug).strip()
                    if not slug:
                        continue

                    variants = [
                        slug,
                        slug.lower(),
                        f"{provider}/{slug}",
                        f"{provider}/{slug.lower()}",
                    ]
                    for item in variants:
                        if _looks_like_chat_model(item):
                            found.append(item)
            except Exception:
                continue

    return unique(found)


def _extract_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""

    message = (choices[0] or {}).get("message") or {}
    content = message.get("content", "")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(p for p in parts if p).strip()

    return ""


def _extract_gemini_text(response: Any) -> str:
    text = getattr(response, "text", "")
    if isinstance(text, str) and text.strip():
        return text.strip()

    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        chunks: list[str] = []
        for part in parts:
            part_text = getattr(part, "text", "")
            if isinstance(part_text, str) and part_text.strip():
                chunks.append(part_text.strip())
        if chunks:
            return "\n".join(chunks)

    return ""


def _truncate_error(text: str, max_len: int = 220) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def github_chat_probe(model: str, prompt: str, timeout: float) -> tuple[bool, float | None, str, str]:
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Content-Type": "application/json",
    }
    messages = [
        {"role": "system", "content": "You are a strict API probe. Keep responses concise."},
        {"role": "user", "content": prompt},
    ]

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        try:
            started = time.perf_counter()
            response = client.post(
                settings.inference_api_url,
                headers=headers,
                json={"model": model, "messages": messages},
            )
            latency = round(time.perf_counter() - started, 3)
        except Exception as exc:
            return False, None, "", _truncate_error(str(exc))

    if response.status_code >= 400:
        return False, latency, "", _truncate_error(f"HTTP {response.status_code}: {response.text}")

    try:
        data = response.json()
    except Exception as exc:
        return False, latency, "", _truncate_error(f"Invalid JSON response: {exc}")

    content = _extract_content(data)
    if not content:
        return False, latency, "", "Empty content in response"
    return True, latency, content, ""


def gemini_chat_probe(client: Any, model: str, prompt: str) -> tuple[bool, float | None, str, str]:
    try:
        started = time.perf_counter()
        response = client.models.generate_content(model=model, contents=prompt)
        latency = round(time.perf_counter() - started, 3)
    except Exception as exc:
        return False, None, "", _truncate_error(str(exc))

    content = _extract_gemini_text(response)
    if not content:
        return False, latency, "", "Empty content in response"
    return True, latency, content, ""


def _parse_json_from_text(text: str) -> dict[str, Any] | None:
    cleaned = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
    cleaned = cleaned.replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    candidate = cleaned[start : end + 1]
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None
    return data


def compute_score(ping_ok: bool, json_ok: bool, avg_latency: float | None) -> float:
    score = 0.0
    if ping_ok:
        score += 30.0
    if json_ok:
        score += 40.0
    if ping_ok and json_ok:
        score += 10.0

    if avg_latency is not None:
        # 0..20 points: lower latency gets higher score.
        score += max(0.0, 20.0 - min(avg_latency, 20.0))

    return round(score, 2)


def _unknown_model_error(error: str) -> bool:
    lowered = error.lower()
    tokens = ("unknown model", "model_not_found", "no model", "does not exist")
    return any(token in lowered for token in tokens)


def _probe_github_candidate(candidate: Candidate, timeout: float) -> tuple[bool, bool, float | None, str, str, str]:
    last_error = ""
    last_latency: float | None = None

    for variant in candidate.variants:
        ping_ok, latency, text, error = github_chat_probe(variant, PROBE_PROMPT, timeout)
        last_latency = latency
        if not ping_ok:
            last_error = error
            # Keep trying aliases if model is unknown.
            if _unknown_model_error(error):
                continue
            return False, False, last_latency, variant, error, ""

        parsed = _parse_json_from_text(text)
        required = {"titulo", "gancho", "cta"}
        json_ok = bool(parsed and required.issubset(set(parsed.keys())))
        if not json_ok:
            return True, False, last_latency, variant, "Model responded, but JSON schema was not valid", text
        return True, True, last_latency, variant, "", text

    return False, False, last_latency, candidate.variants[0], last_error or "All aliases failed", ""


def _get_gemini_available_models(api_key: str) -> set[str]:
    if genai is None or not api_key:
        return set()

    client = genai.Client(api_key=api_key)
    available: set[str] = set()
    for model in client.models.list():
        name = getattr(model, "name", "")
        if not name:
            continue
        normalized = str(name).replace("models/", "")

        # Keep text-generation models only.
        lowered = normalized.lower()
        if any(tag in lowered for tag in ("tts", "lyria", "veo", "embedding", "image")):
            continue
        if not (lowered.startswith("gemini") or lowered.startswith("gemma")):
            continue
        available.add(normalized)
    return available


def _resolve_gemini_model(variants: list[str], available: set[str]) -> str:
    normalized = {m.replace("models/", "") for m in available}
    for variant in variants:
        candidate = variant.replace("models/", "")
        if candidate in normalized:
            return candidate
    return ""


def _probe_gemini_candidate(
    candidate: Candidate,
    gemini_client: Any,
    available_models: set[str],
) -> tuple[bool, bool, float | None, str, str, str]:
    resolved = _resolve_gemini_model(candidate.variants, available_models)
    if not resolved:
        return (
            False,
            False,
            None,
            candidate.variants[0],
            "Model not available in current Gemini account",
            "",
        )

    ping_ok, latency, text, error = gemini_chat_probe(gemini_client, resolved, PROBE_PROMPT)
    if not ping_ok:
        return False, False, latency, resolved, error, ""

    parsed = _parse_json_from_text(text)
    required = {"titulo", "gancho", "cta"}
    json_ok = bool(parsed and required.issubset(set(parsed.keys())))
    if not json_ok:
        return True, False, latency, resolved, "Model responded, but JSON schema was not valid", text
    return True, True, latency, resolved, "", text


def evaluate_candidate(
    candidate: Candidate,
    timeout: float,
    gemini_client: Any,
    gemini_available: set[str],
) -> ProbeResult:
    if candidate.provider == "gemini":
        ping_ok, json_ok, avg_latency, resolved_model, error, _ = _probe_gemini_candidate(
            candidate, gemini_client, gemini_available
        )
    else:
        ping_ok, json_ok, avg_latency, resolved_model, error, _ = _probe_github_candidate(
            candidate, timeout
        )

    score = compute_score(ping_ok=ping_ok, json_ok=json_ok, avg_latency=avg_latency)
    return ProbeResult(
        display_name=candidate.display_name,
        provider=candidate.provider,
        requested_model=candidate.variants[0],
        resolved_model=resolved_model,
        ping_ok=ping_ok,
        json_ok=json_ok,
        avg_latency_sec=avg_latency,
        score=score,
        error=error,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe models from VS Code picker, GitHub Marketplace, and Gemini API"
    )
    parser.add_argument(
        "--mode",
        default="vscode",
        choices=["vscode", "marketplace", "all"],
        help="vscode: test model picker list, marketplace: discover from GitHub Marketplace, all: combine both",
    )
    parser.add_argument(
        "--models",
        default="",
        help="Comma-separated model IDs to test directly against GitHub endpoint",
    )
    parser.add_argument("--max-models", type=int, default=15, help="Maximum number of models to probe")
    parser.add_argument("--top", type=int, default=5, help="How many top recommendations to print")
    parser.add_argument("--timeout", type=float, default=45.0, help="Request timeout (seconds)")
    parser.add_argument(
        "--cooldown",
        type=float,
        default=1.5,
        help="Seconds to wait between model probes (helps reduce 429)",
    )
    parser.add_argument(
        "--no-gemini",
        action="store_true",
        help="Skip Gemini API tests (even in vscode/all mode)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output JSON path. Defaults to workspace/output/model_probe_<timestamp>.json",
    )
    return parser.parse_args()


def build_candidates(args: argparse.Namespace) -> list[Candidate]:
    if args.models.strip():
        manual_models = unique([item.strip() for item in args.models.split(",") if item.strip()])
        return [
            Candidate(display_name=m, provider="github", variants=[m]) for m in manual_models
        ]

    candidates: list[Candidate] = []

    if args.mode in ("vscode", "all"):
        for item in VSCODE_MODEL_PICKER:
            provider = item["provider"]
            if provider == "gemini" and args.no_gemini:
                continue
            variants = _model_variants_from_display(item["display"], provider)
            candidates.append(
                Candidate(
                    display_name=item["display"],
                    provider=provider,
                    variants=variants,
                )
            )

    if args.mode in ("marketplace", "all"):
        marketplace = fetch_marketplace_models(timeout=args.timeout)
        combined = unique(DEFAULT_MODELS + marketplace)
        for model in combined[: max(1, args.max_models)]:
            candidates.append(Candidate(display_name=model, provider="github", variants=[model]))

    # De-duplicate by provider+primary variant.
    dedup: dict[str, Candidate] = {}
    for c in candidates:
        key = f"{c.provider}:{c.variants[0].lower()}"
        dedup[key] = c

    return list(dedup.values())


def save_report(
    results: list[ProbeResult],
    best: list[ProbeResult],
    output_arg: str,
    mode: str,
) -> Path:
    settings.ensure_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_arg) if output_arg else settings.output_dir / f"model_probe_{timestamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "github_endpoint": settings.inference_api_url,
        "tested": len(results),
        "working": len([r for r in results if r.ping_ok]),
        "recommended": [asdict(r) for r in best],
        "results": [asdict(r) for r in results],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def main() -> int:
    args = parse_args()

    if not settings.github_token:
        print("ERROR: GITHUB_TOKEN is missing in .env")
        return 1

    candidates = build_candidates(args)
    if not candidates:
        print("No candidate models found. Use --models to specify model IDs.")
        return 1

    gemini_client = None
    gemini_available: set[str] = set()
    if not args.no_gemini:
        gemini_key = settings.next_gemini_key()
        if gemini_key and genai is not None:
            try:
                gemini_client = genai.Client(api_key=gemini_key)
                gemini_available = _get_gemini_available_models(gemini_key)
            except Exception as exc:
                print(f"WARN: Gemini init failed: {_truncate_error(str(exc))}")
        elif any(c.provider == "gemini" for c in candidates):
            print("WARN: Gemini models requested but no GEMINI_API_KEY/google-genai available.")

    print(f"Testing {len(candidates)} models | mode={args.mode}")
    print(f"GitHub endpoint: {settings.inference_api_url}")
    if gemini_available:
        print(f"Gemini available models detected: {len(gemini_available)}")

    results: list[ProbeResult] = []
    for idx, candidate in enumerate(candidates, start=1):
        provider_tag = candidate.provider.upper()
        print(f"[{idx}/{len(candidates)}] [{provider_tag}] Probing {candidate.display_name} ...", end=" ", flush=True)
        if candidate.provider == "gemini" and gemini_client is None:
            result = ProbeResult(
                display_name=candidate.display_name,
                provider="gemini",
                requested_model=candidate.variants[0],
                resolved_model=candidate.variants[0],
                ping_ok=False,
                json_ok=False,
                avg_latency_sec=None,
                score=0.0,
                error="Gemini client not available",
            )
        else:
            result = evaluate_candidate(
                candidate,
                timeout=args.timeout,
                gemini_client=gemini_client,
                gemini_available=gemini_available,
            )

        results.append(result)
        state = "OK" if result.ping_ok else "FAIL"
        latency = f"{result.avg_latency_sec:.2f}s" if result.avg_latency_sec is not None else "n/a"
        print(f"{state} | score={result.score:.1f} | latency={latency}")

        if args.cooldown > 0 and idx < len(candidates):
            time.sleep(args.cooldown)

    ranked = sorted(
        results,
        key=lambda r: (r.score, r.json_ok, r.ping_ok, -(r.avg_latency_sec or 999.0)),
        reverse=True,
    )
    working = [r for r in ranked if r.ping_ok and r.json_ok]
    recommended = working[: max(1, args.top)]

    report_path = save_report(results, recommended, args.output, args.mode)

    print("\nTop recommended models:")
    if not recommended:
        print("No working models found with current token/endpoint.")
    else:
        for i, item in enumerate(recommended, start=1):
            latency = f"{item.avg_latency_sec:.2f}s" if item.avg_latency_sec is not None else "n/a"
            print(
                f"{i}. {item.display_name} [{item.provider}] -> {item.resolved_model} | "
                f"score={item.score:.1f} | ping_ok={item.ping_ok} | json_ok={item.json_ok} | latency={latency}"
            )

    print(f"\nReport written to: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
