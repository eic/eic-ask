import io
import json
import os
import unittest
from unittest import mock
import urllib.error
import urllib.request

from eic_ask import __version__
from eic_ask.cli import _extract_text, main

PROMPT = "what is EICrecon?"


class _FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


class CliTests(unittest.TestCase):
    def test_short_prompt_is_rejected_before_any_request(self):
        stderr = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen") as urlopen, mock.patch("sys.stderr", new=stderr):
            exit_code = main(["init"])
        self.assertEqual(exit_code, 1)
        self.assertIn("Ask a full question", stderr.getvalue())
        urlopen.assert_not_called()

    def test_unquoted_prompt_is_joined_into_query(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse('{"answer":"Use podio with a local analysis workflow."}')

        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=io.StringIO()
        ):
            exit_code = main(
                [
                    "What",
                    "is",
                    "a",
                    "good",
                    "example",
                    "for",
                    "podio",
                    "data",
                    "analysis",
                    "in",
                    "C++?",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["url"], "https://api.aprozo.com/query")
        self.assertEqual(captured["timeout"], 30.0)
        self.assertEqual(captured["body"], {"query": "What is a good example for podio data analysis in C++?", "top_k": 3})

    def test_user_agent_is_versioned(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["user_agent"] = request.get_header("User-agent")
            return _FakeResponse('{"answer":"ok"}')

        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=io.StringIO()
        ):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["user_agent"], f"eic-ask/{__version__}")

    def test_http_error_is_reported_clearly(self):
        error = urllib.error.HTTPError(
            "https://api.aprozo.com/query",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error":"missing query"}'),
        )

        def fake_urlopen(*args, **kwargs):
            raise error

        stderr = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stderr", new=stderr
        ):
            exit_code = main(["--endpoint", "https://api.aprozo.com/query", PROMPT])

        self.assertEqual(exit_code, 1)
        self.assertIn("API request failed: 400 Bad Request", stderr.getvalue())
        self.assertIn("missing query", stderr.getvalue())

    def test_stdin_prompt_is_used_when_no_argv_prompt(self):
        stdin = io.StringIO("pipe this in")

        def fake_urlopen(request, timeout=None):
            self.assertEqual(json.loads(request.data.decode("utf-8")), {"query": "pipe this in", "top_k": 3})
            return _FakeResponse('{"answer":"stdin worked"}')

        with mock.patch("sys.stdin", new=stdin), mock.patch.object(
            urllib.request, "urlopen", side_effect=fake_urlopen
        ), mock.patch("sys.stdout", new=io.StringIO()):
            exit_code = main([])

        self.assertEqual(exit_code, 0)

    def test_empty_response_body_is_rejected(self):
        stderr = io.StringIO()

        def fake_urlopen(request, timeout=None):
            return _FakeResponse("")

        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stderr", new=stderr
        ):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 1)
        self.assertIn("empty response", stderr.getvalue())

    def test_invalid_json_is_reported(self):
        stderr = io.StringIO()

        def fake_urlopen(request, timeout=None):
            return _FakeResponse("not-json")

        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stderr", new=stderr
        ):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 1)
        self.assertIn("invalid JSON", stderr.getvalue())

    def test_transport_error_is_reported(self):
        error = urllib.error.URLError("timed out")

        def fake_urlopen(*args, **kwargs):
            raise error

        stderr = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stderr", new=stderr
        ):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 1)
        self.assertIn("Unable to reach", stderr.getvalue())
        self.assertIn("timed out", stderr.getvalue())

    def test_token_is_rejected_for_insecure_http_endpoints(self):
        stderr = io.StringIO()

        with mock.patch.dict(os.environ, {"EIC_ASK_TOKEN": "abc123"}, clear=False), mock.patch(
            "sys.stderr", new=stderr
        ):
            exit_code = main(["--endpoint", "http://example.com/query", PROMPT])

        self.assertEqual(exit_code, 1)
        self.assertIn("EIC_ASK_TOKEN is only sent to HTTPS endpoints", stderr.getvalue())

    def test_raw_json_mode_prints_full_payload(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse('{"nested":{"value":1}}')

        stdout = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=stdout
        ):
            exit_code = main(["--json", PROMPT])

        self.assertEqual(exit_code, 0)
        self.assertIn('"nested": {', stdout.getvalue())
        self.assertIn('"value": 1', stdout.getvalue())

    def test_references_are_printed_below_answer(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                '{"answer":"The EIC is a collider. [1]","references":["Brookhaven National Laboratory","ePIC experiment"]}'
            )

        stdout = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=stdout
        ):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("The EIC is a collider.", output)
        self.assertIn("[1] Brookhaven National Laboratory", output)
        self.assertIn("[2] ePIC experiment", output)

    def test_references_can_be_hidden(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                '{"answer":"The EIC is a collider. [1]","references":["Brookhaven National Laboratory"]}'
            )

        stdout = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=stdout
        ):
            exit_code = main(["--no-references", PROMPT])

        self.assertEqual(exit_code, 0)
        self.assertNotIn("[1] Brookhaven National Laboratory", stdout.getvalue())

    def test_reference_links_are_included(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                '{"answer":"The EIC is a collider. [1]","references":[{"title":"Brookhaven National Laboratory","url":"https://www.bnl.gov/"}]}'
            )

        stdout = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=stdout
        ):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("[1] Brookhaven National Laboratory (https://www.bnl.gov/)", output)

    def test_extract_text_joins_lists_of_dict_items(self):
        self.assertEqual(_extract_text([{"text": "one"}, {"text": "two"}]), "one\ntwo")

    def test_choices_message_is_extracted(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse('{"choices":[{"message":"hello"}]}')

        stdout = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=stdout
        ):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue().strip(), "hello")

    def test_choices_text_is_extracted(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse('{"choices":[{"text":"hello"}]}')

        stdout = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=stdout
        ):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue().strip(), "hello")

    def test_token_can_come_from_environment(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["auth"] = request.get_header("Authorization")
            return _FakeResponse('{"answer":"ok"}')

        with mock.patch.dict(os.environ, {"EIC_ASK_TOKEN": "abc123"}, clear=False), mock.patch.object(
            urllib.request, "urlopen", side_effect=fake_urlopen
        ), mock.patch("sys.stdout", new=io.StringIO()):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 0)
        self.assertIsNotNone(captured["auth"])
        self.assertTrue(captured["auth"].startswith("Bearer "))
        self.assertIn("abc123", captured["auth"])

    def test_token_is_optional(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["auth"] = request.get_header("Authorization")
            return _FakeResponse('{"answer":"ok"}')

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            urllib.request, "urlopen", side_effect=fake_urlopen
        ), mock.patch("sys.stdout", new=io.StringIO()):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 0)
        self.assertIsNone(captured["auth"])


    def test_top_k_flag_is_sent(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse('{"answer":"ok"}')

        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=io.StringIO()
        ):
            exit_code = main(["-k", "5", PROMPT])

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["body"]["top_k"], 5)

    def test_duplicate_references_are_collapsed(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                '{"answer":"x [1]","citations":[{"title":"A","url":"https://a"},{"title":"A","url":"https://a"},{"title":"B","url":"https://b"}]}'
            )

        stdout = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=stdout
        ):
            main([PROMPT])

        output = stdout.getvalue()
        self.assertEqual(output.count("https://a"), 1)
        self.assertIn("[2] B (https://b)", output)

    def test_refusal_hides_references(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                '{"answer":"I couldn\'t find enough support in the indexed sources.","citations":[{"title":"A","url":"https://a"}],"retrieval_debug":{"generation":{"support":"insufficient"}}}'
            )

        stdout = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=stdout
        ):
            main([PROMPT])

        self.assertNotIn("https://a", stdout.getvalue())

    def test_missing_answer_text_gives_short_message(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse('{"citations":[],"retrieval_debug":{"big":"x"}}')

        stdout = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=stdout
        ):
            main([PROMPT])

        self.assertIn("no answer text", stdout.getvalue())
        self.assertNotIn("retrieval_debug", stdout.getvalue())

    def test_http_error_body_is_truncated_and_detail_preferred(self):
        error = urllib.error.HTTPError(
            "https://api.aprozo.com/query",
            530,
            "",
            {},
            io.BytesIO(json.dumps({"detail": "tunnel down", "junk": "z" * 5000}).encode()),
        )

        def fake_urlopen(*args, **kwargs):
            raise error

        stderr = io.StringIO()
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stderr", new=stderr
        ), mock.patch("time.sleep"):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 1)
        self.assertIn("530", stderr.getvalue())
        self.assertIn("tunnel down", stderr.getvalue())
        self.assertLess(len(stderr.getvalue()), 400)

    def test_retryable_status_is_retried_once(self):
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.HTTPError(request.full_url, 503, "Busy", None, io.BytesIO(b""))
            return _FakeResponse('{"answer":"ok"}')

        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "sys.stdout", new=io.StringIO()
        ), mock.patch("time.sleep"):
            exit_code = main([PROMPT])

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls["n"], 2)

    def test_interactive_mode_sends_history_and_resets_on_new(self):
        bodies = []

        def fake_urlopen(request, timeout=None):
            bodies.append(json.loads(request.data.decode("utf-8")))
            return _FakeResponse('{"answer":"A%d","citations":[]}' % len(bodies))

        lines = iter(["what is X", "and Y?", "/new", "fresh", "exit"])
        with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), mock.patch(
            "builtins.input", side_effect=lambda _p: next(lines)
        ), mock.patch("sys.stdout", new=io.StringIO()), mock.patch("sys.stderr", new=io.StringIO()):
            exit_code = main(["-i"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(bodies), 3)
        self.assertNotIn("history", bodies[0])
        self.assertEqual(
            bodies[1]["history"],
            [{"role": "user", "content": "what is X"}, {"role": "assistant", "content": "A1"}],
        )
        self.assertNotIn("history", bodies[2])

    def test_interactive_mode_ends_on_eof(self):
        with mock.patch("builtins.input", side_effect=EOFError), mock.patch(
            "sys.stderr", new=io.StringIO()
        ):
            self.assertEqual(main(["-i"]), 0)


if __name__ == "__main__":
    unittest.main()
