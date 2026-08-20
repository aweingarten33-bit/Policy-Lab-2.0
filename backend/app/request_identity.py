"""
Who owns a background job.

Assessment finding #14: a job id alone was enough to read a job's contents,
and those contents are somebody's policy text and their analysis of it.
UUIDv4 ids are impractical to guess, so the real-world risk was low -- but
"unguessable" is not "authorized", and closing it does not require the user
system the app does not have yet.

The client sends a random per-session id and a job is only readable by the
session that created it. This is deliberately NOT authentication: everyone
shares one app password, so it cannot prove who someone is. It answers a
narrower question -- is this the same client that started the job? -- which
is exactly what job access needs.

Lives in its own module because both app.main and the routers need it, and
importing it from app.main creates a circular import.
"""

from typing import Optional

from starlette.requests import Request

CLIENT_ID_HEADER = "x-client-id"

# Bounds what an untrusted header can put into a dict key and a log line.
_MAX_CLIENT_ID_CHARS = 128

# Used when a request carries no client id. A frontend cached from before this
# shipped keeps working rather than losing access to its own jobs on deploy --
# such clients simply share the anonymous bucket, which is how the app behaved
# before ownership existed at all.
ANONYMOUS = "anonymous"


def client_id(request: Request) -> str:
    """The calling client's session id, or ANONYMOUS."""
    raw: Optional[str] = request.headers.get(CLIENT_ID_HEADER)
    if not raw:
        return ANONYMOUS
    cleaned = raw.strip()[:_MAX_CLIENT_ID_CHARS]
    return cleaned or ANONYMOUS
