from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from tamesu.logging import CallLogWriter, REDACTED, redact_value


class LoggingTests(unittest.TestCase):
    def test_recursive_redaction_preserves_usage_fields(self) -> None:
        secret = "sk-super-secret-value"
        value = {
            "authorization": f"Bearer {secret}",
            "nested": {
                "api-key": secret,
                "input_tokens": 14,
                "max_tokens": 256,
                "message": f"request failed with api_key={secret}",
                "url": (
                    "https://example.test/path?part=1&X-Amz-Signature=signed-value"
                ),
            },
        }

        redacted = redact_value(value, secret_values=[secret])

        self.assertEqual(redacted["authorization"], REDACTED)
        self.assertEqual(redacted["nested"]["api-key"], REDACTED)
        self.assertEqual(redacted["nested"]["input_tokens"], 14)
        self.assertEqual(redacted["nested"]["max_tokens"], 256)
        serialized = json.dumps(redacted)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("signed-value", serialized)

    def test_concurrent_appends_produce_complete_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "run.jsonl"
            writer = CallLogWriter(path)
            threads = [
                threading.Thread(
                    target=lambda worker=worker: [
                        writer.append(
                            "test_event",
                            at="2026-10-03T00:00:00Z",
                            run_id="test-run",
                            worker=worker,
                            sequence=sequence,
                        )
                        for sequence in range(20)
                    ]
                )
                for worker in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(events), 160)
            self.assertTrue(all(event["schema_version"] == 1 for event in events))
            self.assertTrue(all(event["event"] == "test_event" for event in events))
            identities = {(event["worker"], event["sequence"]) for event in events}
            self.assertEqual(len(identities), 160)


if __name__ == "__main__":
    unittest.main()
