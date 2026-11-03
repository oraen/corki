"""Provider-neutral limits, TLS classification and credential errors."""
import ssl

MAX_BODY = 16 * 1024 * 1024

def certificate_failure(error: BaseException) -> bool:
    """A trust-store problem is permanent, not an offline retry condition."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ssl.SSLCertVerificationError):
            return True
        error = error.__cause__ or error.__context__
    return False


class CredentialsUnavailable(Exception):
    """Safe error with no credential material attached."""


