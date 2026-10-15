"""Server retry advice keeps the header deadline and respects error class."""

import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx

from corki.core.retry import retry_delay
from corki.models.failure import ModelFailure
from corki.models.http_errors import http_error
from corki.models.response_errors import failed_response
from corki.models.retry_after import retry_after_deadline


def test_retry_after_accepts_seconds_and_http_date_but_not_malformed_values():
    assert retry_after_deadline("0") is not None
    date = format_datetime(datetime.now(UTC) + timedelta(seconds=3))
    assert retry_after_deadline(date) is not None
    for value in ("", "-1", "1.5", "tomorrow", "9" * 100):
        assert retry_after_deadline(value) is None


def test_failed_event_header_takes_precedence_over_message():
    error = failed_response(
        {"error": {
            "code": "rate_limit_exceeded",
            "message": "try again in 99s",
            "headers": {"Retry-After": "0"},
        }}
    )
    assert error.retryable
    assert error.retry_after_seconds is not None and error.retry_after_seconds < 1


def test_terminal_usage_limit_ignores_retry_advice():
    response = httpx.Response(
        429, json={"error": {"type": "usage_limit_reached"}}, headers={"Retry-After": "0"}
    )
    error = http_error(response, response.text)
    assert not error.retryable


def test_persisted_retry_deadline_counts_down_instead_of_restarting():
    response = httpx.Response(503, text="busy", headers={"Retry-After": "2"})
    error = http_error(response, response.text)
    failure = ModelFailure.from_error(error, 0)
    assert failure.retry_after_epoch is not None
    first = retry_delay(failure, 0.1)
    time.sleep(0.01)
    assert 0 <= retry_delay(failure, 0.1) < first <= 2
