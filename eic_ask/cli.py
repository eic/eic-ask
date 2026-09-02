from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from eic_ask import __version__


DEFAULT_ENDPOINT = "https://api.aprozo.com/query"
DEFAULT_TIMEOUT = 30.0
DEFAULT_USER_AGENT = f"eic-ask/{__version__}"


class CLIError(RuntimeError):
    pass


@dataclass
class RequestConfig:
    endpoint: str
    timeout: float
    raw_json: bool
    show_references: bool = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eic-ask",
        description="Send a prompt to the EIC Aprozo query API.",
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


def _request_payload(prompt: str) -> bytes:
    return json.dumps({"query": prompt}).encode("utf-8")


def _build_request(prompt: str, config: RequestConfig) -> urllib.request.Request:
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
        data=_request_payload(prompt),
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
        # Prefer explicit Aprozo fields first, then common LLM-style keys.
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
        refs = []
        for item in payload:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    refs.append(text)
                continue
            if isinstance(item, dict):
                for key in ("text", "title", "label", "name", "source", "citation"):
                    value = item.get(key)
                    if value is None:
                        continue
                    text = _extract_text(value)
                    if text:
                        refs.append(text)
                        break
        return refs
    return []


def _format_references(references: list[str]) -> str:
    """Render numbered references beneath the answer text."""
    return "\n".join(f"[{index}] {ref}" for index, ref in enumerate(references, start=1))


def _format_output(payload: Any, raw_json: bool, show_references: bool) -> str:
    if raw_json:
        return _pretty_json(payload)
    text = _extract_text(payload)
    if text:
        parts = [text]
        if show_references:
            references = _extract_references(payload)
            if references:
                parts.append(_format_references(references))
        return "\n\n".join(parts)
    return _pretty_json(payload)


def _pretty_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


def ask(prompt: str, config: RequestConfig) -> str:
    request = _build_request(prompt, config)
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            body_text = _read_response_body(response)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:
            body = b""
        body_text = body.decode("utf-8", errors="replace").strip()
        message = f"API request failed: {exc.code} {exc.reason}"
        if body_text:
            message = f"{message}\n{body_text}"
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

    return _format_output(payload, config.raw_json, config.show_references)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_intermixed_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        prompt = _prompt_text(args.prompt)
        output = ask(
            prompt,
            RequestConfig(
                endpoint=args.endpoint,
                timeout=args.timeout,
                raw_json=args.json,
                show_references=args.show_references,
            ),
        )
    except CLIError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(output)
    return 0
