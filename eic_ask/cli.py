from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_ENDPOINT = "https://api.aprozo.com/query"
DEFAULT_TIMEOUT = 30.0


class CLIError(RuntimeError):
    pass


@dataclass
class RequestConfig:
    endpoint: str
    timeout: float
    token: str | None
    raw_json: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eic-ask",
        description="Send a prompt to the EIC Aprozo query API.",
    )
    parser.add_argument("prompt", nargs="*", help="Prompt to send to the API.")
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
        "--token",
        default=os.getenv("EIC_ASK_TOKEN"),
        help="OAuth bearer token for authenticated access.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full response as formatted JSON.",
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
    request = urllib.request.Request(
        config.endpoint,
        data=_request_payload(prompt),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    if config.token:
        request.add_header("Authorization", "Bearer " + config.token)
    return request


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
        texts = [text for item in value if (text := _extract_text(item))]
        if texts and all(isinstance(item, str) for item in value):
            return "\n".join(texts)
        if len(texts) == 1:
            return texts[0]
        return None
    if isinstance(value, dict):
        for key in (
            "answer",
            "response",
            "text",
            "message",
            "content",
            "output",
            "result",
            "summary",
            "detail",
        ):
            extracted = _extract_text(value.get(key))
            if extracted:
                return extracted
        choices = value.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                extracted = _extract_text(first.get("message"))
                if extracted:
                    return extracted
                extracted = _extract_text(first.get("text"))
                if extracted:
                    return extracted
        if len(value) == 1:
            return _extract_text(next(iter(value.values())))
    return None


def _format_output(payload: Any, raw_json: bool) -> str:
    if raw_json:
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    text = _extract_text(payload)
    if text:
        return text
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


def ask(prompt: str, config: RequestConfig) -> str:
    request = _build_request(prompt, config)
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            body_text = _read_response_body(response)
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace").strip()
        message = f"API request failed: {exc.code} {exc.reason}"
        if body_text:
            message = f"{message}\n{body_text}"
        raise CLIError(message) from exc
    except urllib.error.URLError as exc:
        raise CLIError(f"Unable to reach {config.endpoint}: {exc.reason}") from exc

    if not body_text:
        raise CLIError("API returned an empty response.")

    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError as exc:
        raise CLIError(
            "API returned invalid JSON. "
            f"Expected JSON but received: {body_text[:200]}"
        ) from exc

    return _format_output(payload, config.raw_json)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parse = getattr(parser, "parse_intermixed_args", parser.parse_args)
    args = parse(argv)
    try:
        prompt = _prompt_text(args.prompt)
        output = ask(
            prompt,
            RequestConfig(
                endpoint=args.endpoint,
                timeout=args.timeout,
                token=args.token,
                raw_json=args.json,
            ),
        )
    except CLIError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(output)
    return 0
