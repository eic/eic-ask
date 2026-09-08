from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from eic_ask import __version__


DEFAULT_ENDPOINT = "https://api.aprozo.com/query"
DEFAULT_TIMEOUT = 30.0
DEFAULT_TOP_K = 3
MAX_ERROR_BODY = 300
RETRY_STATUSES = {429, 502, 503, 530}
MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_CHARS = 1500
DEFAULT_USER_AGENT = f"eic-ask/{__version__}"


class CLIError(RuntimeError):
    pass


@dataclass
class RequestConfig:
    endpoint: str
    timeout: float
    raw_json: bool
    show_references: bool = True
    top_k: int = DEFAULT_TOP_K


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eic-ask",
        description="Send a prompt to the EIC Documentation query API.",
    )
    parser.add_argument("prompt", nargs="*", help="Prompt to send to the API or stdin.")
    parser.add_argument(
        "-e",
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"API endpoint URL (default: {DEFAULT_ENDPOINT}).",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "-k",
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Number of sources to retrieve and cite (default: {DEFAULT_TOP_K}).",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Chat mode: follow-ups keep context (/new resets, exit or Ctrl-D quits). "
        "Default when run without a prompt in a terminal.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full response as formatted JSON.",
    )
    parser.add_argument(
        "--no-references",
        "--hide-references",
        dest="show_references",
        action="store_false",
        help="Hide numbered references after the response text.",
    )
    return parser


def _prompt_text(parts: list[str]) -> str:
    prompt = " ".join(parts).strip()
    if prompt:
        return prompt
    if not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
        if prompt:
            return prompt
    raise CLIError("No prompt supplied. Provide a query or pipe one on stdin.")


def _request_payload(
    prompt: str, top_k: int = DEFAULT_TOP_K, history: list[dict[str, str]] | None = None
) -> bytes:
    payload: dict[str, Any] = {"query": prompt, "top_k": top_k}
    if history:
        payload["history"] = history[-MAX_HISTORY_MESSAGES:]
    return json.dumps(payload).encode("utf-8")


def _build_request(
    prompt: str, config: RequestConfig, history: list[dict[str, str]] | None = None
) -> urllib.request.Request:
    token = os.getenv("EIC_ASK_TOKEN")
    endpoint = urllib.parse.urlparse(config.endpoint)
    if token and endpoint.scheme.lower() != "https":
        raise CLIError(
            "EIC_ASK_TOKEN is only sent to HTTPS endpoints. "
            f"Refusing to send the token to {config.endpoint!r}."
        )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if token:
        headers["Authorization"] = "Bearer " + token

    return urllib.request.Request(
        config.endpoint,
        data=_request_payload(prompt, config.top_k, history),
        headers=headers,
        method="POST",
    )


def _read_response_body(response: Any) -> str:
    body = response.read()
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return str(body)


def _extract_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        texts = [extracted for item in value if (extracted := _extract_text(item))]
        if texts:
            return "\n".join(texts)
        return None
    if isinstance(value, dict):
        # Prefer explicit API fields first, then common LLM-style keys.
        for key in (
            "answer",
            "response",
            "text",
            "message",
            "content",
            "choices",
            "output",
            "result",
            "summary",
            "detail",
        ):
            extracted = _extract_text(value.get(key))
            if extracted:
                return extracted
    return None


def _extract_references(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        for key in ("references", "citations", "sources", "footnotes"):
            if key in payload:
                value = payload[key]
                if isinstance(value, list):
                    return _extract_references(value)
                if isinstance(value, dict):
                    refs = _extract_references(value)
                    if refs:
                        return refs
                return []
        return []
    if isinstance(payload, list):
        refs: list[str] = []
        seen: set[str] = set()
        for item in payload:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    refs.append(text)
                continue
            if isinstance(item, dict):
                label = None
                for key in ("text", "title", "label", "name", "source", "citation"):
                    value = item.get(key)
                    if value is None:
                        continue
                    text = _extract_text(value)
                    if text:
                        label = text
                        break

                url = None
                for key in ("url", "link", "href", "source_url", "website"):
                    value = item.get(key)
                    if isinstance(value, str):
                        text = value.strip()
                        if text:
                            url = text
                            break

                key = url or label
                if not key or key in seen:
                    continue
                seen.add(key)
                if label and url:
                    refs.append(f"{label} ({url})")
                else:
                    refs.append(label or url)
        return refs
    return []


def _format_references(references: list[str]) -> str:
    """Render numbered references beneath the answer text."""
    return "\n".join(f"[{index}] {ref}" for index, ref in enumerate(references, start=1))


def _is_refusal(payload: Any, text: str) -> bool:
    if isinstance(payload, dict):
        generation = (payload.get("retrieval_debug") or {}).get("generation") or {}
        if isinstance(generation, dict) and generation.get("support") == "insufficient":
            return True
    return text.startswith("I couldn't find")


def _format_output(payload: Any, raw_json: bool, show_references: bool) -> str:
    if raw_json:
        return _pretty_json(payload)
    text = _extract_text(payload)
    if not text:
        return "API returned no answer text (use --json to inspect)."
    parts = [text]
    if show_references and not _is_refusal(payload, text):
        references = _extract_references(payload)
        if references:
            parts.append(_format_references(references))
    return "\n\n".join(parts)


def _pretty_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


def _error_detail(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
        for key in ("detail", "title", "error", "message"):
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
    except ValueError:
        pass
    return text[:MAX_ERROR_BODY] + ("…" if len(text) > MAX_ERROR_BODY else "")


def _retry_after(exc: urllib.error.HTTPError) -> float:
    try:
        return min(float(exc.headers.get("Retry-After", "2")), 10.0)
    except (TypeError, ValueError):
        return 2.0


def _query(
    prompt: str, config: RequestConfig, history: list[dict[str, str]] | None = None
) -> Any:
    request = _build_request(prompt, config, history)
    retried = False
    while True:
        try:
            with urllib.request.urlopen(request, timeout=config.timeout) as response:
                body_text = _read_response_body(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUSES and not retried:
                retried = True
                time.sleep(_retry_after(exc))
                continue
            try:
                body = exc.read()
            except Exception:
                body = b""
            message = f"API request failed: {exc.code} {exc.reason}"
            detail = _error_detail(body)
            if detail:
                message = f"{message}: {detail}"
            raise CLIError(message) from exc
        except urllib.error.URLError as exc:
            raise CLIError(f"Unable to reach {config.endpoint}: {exc.reason!s}") from exc

    if not body_text:
        raise CLIError("API returned an empty response.")

    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError as exc:
        raise CLIError(
            "API returned invalid JSON. "
            f"Expected JSON but received: {body_text[:200]}"
        ) from exc

    return payload


def ask(prompt: str, config: RequestConfig) -> str:
    return _format_output(_query(prompt, config), config.raw_json, config.show_references)


def interactive(config: RequestConfig, first: str = "") -> int:
    history: list[dict[str, str]] = []
    print("eic-ask: follow-ups keep context; /new resets, exit or Ctrl-D quits.", file=sys.stderr)
    pending = first
    while True:
        if pending:
            line, pending = pending, ""
        else:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                return 0
        if not line:
            continue
        if line in ("exit", "quit", "/quit"):
            return 0
        if line == "/new":
            history.clear()
            print("(new topic)", file=sys.stderr)
            continue
        try:
            payload = _query(line, config, history)
        except CLIError as exc:
            print(str(exc), file=sys.stderr)
            continue
        print(_format_output(payload, config.raw_json, config.show_references))
        print()
        text = _extract_text(payload)
        if text and not _is_refusal(payload, text):
            history += [
                {"role": "user", "content": line},
                {"role": "assistant", "content": text[:MAX_HISTORY_CHARS]},
            ]
            del history[:-MAX_HISTORY_MESSAGES]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_intermixed_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    config = RequestConfig(
        endpoint=args.endpoint,
        timeout=args.timeout,
        raw_json=args.json,
        show_references=args.show_references,
        top_k=args.top_k,
    )
    first = " ".join(args.prompt).strip()
    if args.interactive or (not first and sys.stdin.isatty()):
        return interactive(config, first)
    try:
        prompt = _prompt_text(args.prompt)
        output = ask(prompt, config)
    except CLIError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(output)
    return 0
