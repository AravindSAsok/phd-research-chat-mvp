import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import chat_logic
from google import genai
from streamlit.testing.v1 import AppTest

from chat_logic import (
    CLARIFICATION_MESSAGE,
    NO_EVIDENCE_MESSAGE,
    api_error_message,
    build_context,
    build_instruction,
    generate_with_retries,
    needs_clarification,
    recent_conversation,
    replace_source_ids,
)


SAMPLE_DOCUMENTS = {
    "AravindAsok Resume.pdf": [
        {
            "text": "Aravind Asok is a software engineer. Address: Trivandrum, Kerala.",
            "page": 1,
        },
        {
            "text": "Education: Bachelor of Technology in Computer Science from ABC University.",
            "page": 2,
        },
        {
            "text": "Professional experience includes a software developer role and engineering projects.",
            "page": 3,
        },
    ]
}


class FakeApiError(Exception):
    def __init__(self, code, status="", headers=None, message="temporary failure"):
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.response = SimpleNamespace(headers=headers or {})


class ChatLogicTests(unittest.TestCase):
    def test_first_question_retrieves_evidence_and_converts_page_citation(self):
        context, sources = build_context("Who is Aravind?", SAMPLE_DOCUMENTS)
        self.assertIn("Aravind Asok", context)
        self.assertEqual(sources["S1"], {"filename": "AravindAsok Resume.pdf", "page": 1})
        response = generate_with_retries(lambda: "Aravind is a software engineer [S1].")
        self.assertEqual(
            replace_source_ids(response, sources),
            "Aravind is a software engineer [AravindAsok Resume.pdf (PDF p. 1)].",
        )

    def test_followup_location_uses_recent_person_context(self):
        history = [{"question": "Who is Aravind?", "answer": "Aravind is a software engineer."}]
        context, sources = build_context("Where is he from?", SAMPLE_DOCUMENTS, history=history)
        self.assertIn("Trivandrum", context)
        self.assertIn("PDF page=1", context)
        self.assertTrue(any(source["page"] == 1 for source in sources.values()))
        self.assertFalse(needs_clarification("Where is he from?", history))

    def test_short_who_uses_history_and_clarifies_without_it(self):
        history = [{"question": "Who is Aravind?", "answer": "Aravind is a software engineer."}]
        context, _ = build_context("Who?", SAMPLE_DOCUMENTS, history=history)
        self.assertIn("Aravind", context)
        self.assertFalse(needs_clarification("Who?", history))
        self.assertTrue(needs_clarification("Who?", []))
        self.assertIn("clarify", CLARIFICATION_MESSAGE.casefold())

    def test_education_and_experience_synonyms_match(self):
        history = [{"question": "Who is Aravind?", "answer": "Aravind is a software engineer."}]
        education, sources = build_context("What did he study?", SAMPLE_DOCUMENTS, history=history)
        experience, _ = build_context("Tell me more about his experience.", SAMPLE_DOCUMENTS, history=history)
        self.assertIn("Bachelor of Technology", education)
        self.assertTrue(any(source["page"] == 2 for source in sources.values()))
        self.assertIn("software developer role", experience)

    def test_prompt_has_recent_context_and_one_current_question(self):
        question = "CURRENT_QUESTION_TOKEN"
        instruction = build_instruction(
            question,
            "Evidence excerpt [S1]",
            [{"question": "Who is Aravind?", "answer": "Aravind is a software engineer."}],
        )
        self.assertIn("RECENT CONVERSATION", instruction)
        self.assertIn("Aravind is a software engineer", instruction)
        self.assertEqual(instruction.count(question), 1)
        self.assertEqual(instruction.count("QUESTION:"), 1)
        self.assertIn("Conversation history may clarify", instruction)
        self.assertIn("only from the supplied document excerpts", instruction)

    def test_recent_history_excludes_failed_answers_and_orphan_users(self):
        messages = [
            {"role": "user", "content": "Who is Aravind?"},
            {"role": "assistant", "content": "Aravind is a software engineer."},
            {"role": "user", "content": "failed question"},
            {"role": "assistant", "content": "Gemini is temporarily unavailable.", "error": True},
            {"role": "user", "content": "orphaned question"},
        ]
        self.assertEqual(
            recent_conversation(messages),
            [{"question": "Who is Aravind?", "answer": "Aravind is a software engineer."}],
        )

    def test_503_retries_are_bounded_and_successful_retry_returns_answer(self):
        calls, sleeps = [], []

        def request():
            calls.append(None)
            if len(calls) < 3:
                raise FakeApiError(503, "UNAVAILABLE")
            return "answer"

        result = generate_with_retries(
            request, sleep_fn=sleeps.append, random_fn=lambda low, high: low
        )
        self.assertEqual(result, "answer")
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(sleeps[0] < sleeps[1])

    def test_failed_503_is_bounded_and_a_later_question_can_succeed(self):
        calls, sleeps = [], []

        def unavailable():
            calls.append(None)
            raise FakeApiError(503, "UNAVAILABLE")

        with self.assertRaises(FakeApiError):
            generate_with_retries(
                unavailable, sleep_fn=sleeps.append, random_fn=lambda low, high: low
            )
        self.assertEqual(len(calls), 3)
        self.assertIn(
            "temporarily unavailable",
            api_error_message(FakeApiError(503, "UNAVAILABLE")),
        )
        self.assertEqual(generate_with_retries(lambda: "next answer", sleep_fn=sleeps.append), "next answer")

    def test_retry_after_is_honored_and_long_wait_is_not_ignored(self):
        calls, sleeps = [], []

        def rate_limited_then_ok():
            calls.append(None)
            if len(calls) == 1:
                raise FakeApiError(429, "RESOURCE_EXHAUSTED", {"Retry-After": "1.25"})
            return "answer"

        self.assertEqual(generate_with_retries(rate_limited_then_ok, sleep_fn=sleeps.append), "answer")
        self.assertEqual(sleeps, [1.25])

        calls.clear()

        def long_wait():
            calls.append(None)
            raise FakeApiError(503, "UNAVAILABLE", {"Retry-After": "30"})

        with self.assertRaises(FakeApiError):
            generate_with_retries(long_wait, sleep_fn=sleeps.append)
        self.assertEqual(len(calls), 1)

    def test_auth_and_invalid_request_errors_are_not_retried(self):
        for code, status, expected in (
            (401, "PERMISSION_DENIED", "rejected the API key"),
            (400, "INVALID_ARGUMENT", "HTTP 400"),
        ):
            calls = []

            def permanent_error():
                calls.append(None)
                raise FakeApiError(code, status)

            with self.assertRaises(FakeApiError):
                generate_with_retries(permanent_error, sleep_fn=lambda _: self.fail("must not sleep"))
            self.assertEqual(len(calls), 1)
            self.assertIn(expected.casefold(), api_error_message(FakeApiError(code, status)).casefold())

    def test_quota_without_retry_after_is_not_retried(self):
        calls = []

        def quota_error():
            calls.append(None)
            raise FakeApiError(429, "RESOURCE_EXHAUSTED")

        with self.assertRaises(FakeApiError):
            generate_with_retries(quota_error, sleep_fn=lambda _: self.fail("must not sleep"))
        self.assertEqual(len(calls), 1)
        self.assertIn("quota or rate limit", api_error_message(FakeApiError(429, "RESOURCE_EXHAUSTED")))

    def test_absent_person_evidence_returns_no_context_or_fabricated_citation(self):
        context, sources = build_context("Who is Marie Curie?", SAMPLE_DOCUMENTS)
        self.assertEqual(context, "")
        self.assertEqual(sources, {})
        self.assertIn("can't answer it from the available evidence", NO_EVIDENCE_MESSAGE)
        self.assertEqual(replace_source_ids("No citation [S99].", sources), "No citation .")

    def test_trivandrum_alias_and_pdf_and_txt_citations_are_preserved(self):
        docs = {
            "notes.txt": [{"text": "Aravind lives in Trivandrum.", "page": None}],
            "resume.pdf": [{"text": "Aravind lives in Thiruvananthapuram.", "page": 4}],
        }
        context, sources = build_context("Where is Aravind?", docs)
        self.assertIn("Trivandrum", context)
        self.assertIn("Thiruvananthapuram", context)
        labels = [
            replace_source_ids(f"[{source_id}]", {source_id: source})
            for source_id, source in sources.items()
        ]
        self.assertIn("[notes.txt]", labels)
        self.assertIn("[resume.pdf (PDF p. 4)]", labels)

    def test_streamlit_submits_each_turn_once_and_sends_recent_context(self):
        project = Path(__file__).resolve().parents[1]
        prompts = []
        retry_attempts = []

        class FakeModels:
            def generate_content(self, model, contents, config):
                prompts.append(contents)
                return SimpleNamespace(text="Aravind is a software engineer [S1].")

        class FakeClient:
            def __init__(self, **kwargs):
                retry_attempts.append(kwargs["http_options"].retry_options.attempts)
                self.models = FakeModels()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only-key"}), patch.object(
            genai, "Client", FakeClient
        ):
            app = AppTest.from_file(str(project / "app.py"), default_timeout=20).run()
            app.session_state["documents"] = SAMPLE_DOCUMENTS
            app.run()
            app.chat_input[0].set_value("Who is Aravind?").run()
            app.chat_input[0].set_value("Where is he from?").run()

        messages = app.session_state["messages"]
        self.assertEqual([message["role"] for message in messages], [
            "user", "assistant", "user", "assistant"
        ])
        self.assertEqual(len(prompts), 2)
        self.assertEqual(retry_attempts, [1, 1])
        self.assertEqual(prompts[0].count("Who is Aravind?"), 1)
        self.assertIn("RECENT CONVERSATION", prompts[1])
        self.assertEqual(prompts[1].count("Where is he from?"), 1)
        self.assertIn("AravindAsok Resume.pdf (PDF p. 1)", messages[1]["content"])

    def test_streamlit_api_failure_does_not_poison_next_question(self):
        project = Path(__file__).resolve().parents[1]
        prompts = []
        calls = []

        class FakeModels:
            def generate_content(self, model, contents, config):
                calls.append(None)
                prompts.append(contents)
                if len(calls) <= 3:
                    raise FakeApiError(503, "UNAVAILABLE")
                return SimpleNamespace(text="Aravind is a software engineer [S1].")

        class FakeClient:
            def __init__(self, **kwargs):
                self.models = FakeModels()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        real_retry = chat_logic.generate_with_retries
        instant_retry = lambda request: real_retry(
            request, sleep_fn=lambda _: None, random_fn=lambda low, high: low
        )
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only-key"}), patch.object(
            genai, "Client", FakeClient
        ), patch("chat_logic.generate_with_retries", instant_retry):
            app = AppTest.from_file(str(project / "app.py"), default_timeout=20).run()
            app.session_state["documents"] = SAMPLE_DOCUMENTS
            app.run()
            app.chat_input[0].set_value("Where is Aravind?").run()
            self.assertTrue(app.session_state["messages"][1]["error"])
            self.assertIn("temporarily unavailable", app.session_state["messages"][1]["content"])
            app.chat_input[0].set_value("Who is Aravind?").run()

        messages = app.session_state["messages"]
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(messages), 4)
        self.assertFalse(messages[-1]["error"])
        self.assertNotIn("RECENT CONVERSATION", prompts[-1])

    def test_existing_ui_upload_and_secret_configuration_markers_remain(self):
        project = Path(__file__).resolve().parents[1]
        app = (project / "app.py").read_text(encoding="utf-8")
        theme = (project / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        self.assertIn(r'APP_NAME = "p!nk \u2014 The Research Chat"', app)
        self.assertIn('type=["pdf", "docx", "txt"]', app)
        self.assertIn('if suffix == ".pdf":', app)
        self.assertIn('if suffix == ".docx":', app)
        self.assertIn('elif suffix == ".txt":', app)
        self.assertIn("PdfReader(uploaded_file)", app)
        self.assertIn("Document(uploaded_file)", app)
        self.assertIn('raw.decode("utf-8-sig")', app)
        self.assertIn("types.HttpRetryOptions(attempts=1)", app)
        self.assertIn('"error": failed', app)
        self.assertIn('os.getenv("GEMINI_API_KEY")', app)
        self.assertIn('get_secret("GEMINI_API_KEY"', app)
        self.assertIn("--pink-primary: #F4A6C1", app)
        self.assertIn("--text-primary: #000000", app)
        self.assertIn('textColor = "#000000"', theme)


if __name__ == "__main__":
    unittest.main()
