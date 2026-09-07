"""Own HTTP attempts and their responses before handing off to an SSE reader."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx

from corki.models.backoff import backoff
from corki.models.http_errors import http_error

wait_http_retry = asyncio.sleep


@asynccontextmanager
async def model_http_stream(client, url, *, headers, payload, max_retries, base_seconds):
    response = None
    for attempt in range(max_retries + 1):
        try:
            request = client.build_request("POST", url, headers=headers, json=payload)
            response = await client.send(request, stream=True)
            if response.is_success:
                break
            try:
                body = (await response.aread()).decode("utf-8", errors="replace")
            except httpx.RequestError:
                # The reference transport keeps the status even if reading its
                # diagnostic body fails. In particular, a 429 must not become
                # a retryable network error just because its body is truncated.
                body = ""
            finally:
                await response.aclose()
            if not 500 <= response.status_code < 600 or attempt == max_retries:
                raise http_error(response, body)
        except httpx.TransportError:
            if response is not None:
                await response.aclose()
            if attempt == max_retries:
                raise
        delay = backoff(base_seconds, attempt)
        logging.getLogger(__name__).debug(
            "Retrying model HTTP request",
            extra={"retry_attempt": attempt + 1, "retry_delay": delay, "retry_layer": "http"},
        )
        await wait_http_retry(delay)
        response = None
    # Keep the yielded reader outside the retry catch: body failures belong to
    # sampling, whose next request must be rebuilt from the updated history.
    try:
        yield response
    finally:
        await response.aclose()
