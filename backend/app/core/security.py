import hashlib
import hmac

from app.core.logging import get_logger

logger = get_logger(__name__)


def verify_github_signature(
    payload: bytes,
    signature_header: str | None,
    secret: str,
    allow_insecure_empty: bool = False,
) -> bool:
    """Verify GitHub webhook HMAC SHA-256 signature.
    
    Args:
        payload: Raw HTTP request body as bytes.
        signature_header: Content of the 'X-Hub-Signature-256' header (e.g. 'sha256=abcdef...').
        secret: Webhook secret configured in GitHub and platform settings.
        allow_insecure_empty: If True and secret is blank, allow requests (useful in isolated local tests).
        
    Returns:
        bool: True if signature matches or safely allowed, False otherwise.
    """
    if not secret:
        if allow_insecure_empty:
            logger.warning("github_signature_check_bypassed", reason="secret_not_configured")
            return True
        logger.error("github_signature_verification_failed", reason="secret_not_configured")
        return False

    if not signature_header:
        logger.warning("github_signature_missing", header="X-Hub-Signature-256")
        return False

    if not signature_header.startswith("sha256="):
        logger.warning("github_signature_malformed", header=signature_header)
        return False

    mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
    expected_signature = f"sha256={mac.hexdigest()}"

    # Constant-time comparison to protect against timing attacks
    is_valid = hmac.compare_digest(expected_signature, signature_header)
    if not is_valid:
        logger.warning("github_signature_mismatch")
    return is_valid


def compute_github_signature(payload: bytes, secret: str) -> str:
    """Helper to compute valid 'sha256=...' signature for testing and webhook generation."""
    mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"
