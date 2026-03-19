from __future__ import annotations

import json
import logging

from findmejobs.observability.logging import JsonFormatter


def test_structured_logging_outputs_json() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord("findmejobs", logging.INFO, __file__, 10, "hello", (), None)
    record.payload = {"stage": "ingest", "status": "ok"}
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "hello"
    assert payload["stage"] == "ingest"
    assert payload["status"] == "ok"
