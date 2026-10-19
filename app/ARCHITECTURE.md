# Corki desktop

The desktop is an independent host. The renderer communicates through a narrow
preload API; the Electron main process owns a Python stdio bridge. The bridge
uses Corki's Runtime, event types, SQLite session repository and writer leases.
No desktop-specific branches belong in the Agent core.

Canonical messages remain in Corki's session database. Desktop metadata (titles,
drafts, request admission records) lives separately under the selected Corki
home. A running task belongs to the bridge, not to a mounted page. Notifications
have an instance ID and monotonically increasing sequence; bounded replay falls
back to a snapshot. An uncertain submission is reconciled, never auto-retried.

Reference observations from the locally installed desktop bundle: isolated main
window/preload; finite subscription cleanup; composition-aware input handling;
submission outcome-unknown state; measured scroll anchoring and response spacer.
These are design references, not copied source. This app deliberately omits
account, telemetry, remote host, cloud and commerce integrations.

Security reference: https://www.electronjs.org/docs/latest/tutorial/security
Process reference: https://www.electronjs.org/docs/latest/tutorial/process-model

## Bounded transport fragments

Requests remain JSON-RPC lines capped at 2 MiB. Python responses and events above
that frame size use ordered `transport.chunk` envelopes: a random message ID,
zero-based index, original UTF-8 byte length, final marker and JSON text fragment.
The main process reconstructs one message at a time before normal RPC/event
validation; renderer APIs do not change. Fragments cannot interleave. Logical
messages are limited to 64 MiB, assembly to 1,024 fragments and 30 seconds, and
Python's outgoing queue to 16 frames with backpressure. Invalid/truncated streams
fail the connection without resubmitting tasks. All timers/buffers are released
on completion, malformed input or process exit. Attachments still use local
references and history still paginates; fragmentation does not replace those
smaller projections or make an unbounded history request safe.

Live display projections retain at most 200 coalesced events per running thread.
Assistant text and other large string fields use `SpooledTemporaryFile` with a
4 KiB RAM threshold; append operations avoid full-string copies. The file belongs
to the transient projection value and closes when that value is discarded. This
cache never supplies model context or replaces the core conversation database.
Tool output/reasoning have a 64,000-character rolling display; assistant replies
retain their beginning. Serializing a requested snapshot materializes the text
for bounded transport, so request-time memory differs from idle cache residency.
