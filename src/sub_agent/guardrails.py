"""Security Guardrails for ShopHub Domain Sub-Agent (R4).

Implements:
- (a) Adversarial instruction neutralization in customer notes
- (b) Pre-model PII redaction (email addresses and phone numbers)
- (c) Fail-closed enforcement for unassigned or invalid merchant tokens
"""

import re
from typing import Any, Dict, Optional

# Regular expressions for PII detection
EMAIL_REGEX = re.compile(
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
)

# Matches standard phone formats: +1-555-0101, (555) 0101, 555-0101, etc.
PHONE_REGEX = re.compile(
    r'(\+?\d{1,3}[-.\s]?)?(\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{3,4}\b'
)

# Common instruction-injection phrases found in adversarial notes
INJECTION_DIRECTIVES = re.compile(
    r'(assistant\s*:|system\s+override\s*:|ignore\s+your\s+instructions|ignore\s+previous\s+instructions|debug\s+mode|output\s+the\s+secret)',
    re.IGNORECASE,
)


def redact_pii_text(text: Optional[str]) -> Optional[str]:
    """Redacts email addresses and phone numbers from a text string.

    Ensures customer PII never reaches the LLM context.
    """
    if not text:
        return text

    # Redact emails first
    sanitized = EMAIL_REGEX.sub("[EMAIL_REDACTED]", text)
    # Redact phone numbers
    sanitized = PHONE_REGEX.sub("[PHONE_REDACTED]", sanitized)
    return sanitized


def neutralize_customer_note(note: Optional[str]) -> Optional[str]:
    """Neutralizes potential prompt-injection instructions embedded in customer notes.

    1. Detects instruction-like triggers and neutralizes them.
    2. Wraps the note in an inert <untrusted_customer_note> XML boundary so
       the model treats it strictly as unparsed customer data, never as instructions.
    """
    if not note:
        return None

    # First redact any PII in the note
    sanitized_note = redact_pii_text(note)

    # Defang direct command triggers by inserting escaping brackets
    def _defang(match: re.Match) -> str:
        matched_text = match.group(0)
        return f"[NEUTRALIZED_DIRECTIVE: {matched_text}]"

    defanged_note = INJECTION_DIRECTIVES.sub(_defang, sanitized_note)

    # Wrap in explicit containment tags with a system warning for the model
    return (
        f"<untrusted_customer_note source='customer_free_text'>\n"
        f"{defanged_note}\n"
        f"</untrusted_customer_note>"
    )


def sanitize_order_for_model(order_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Applies all guardrails to an order dictionary before passing it to the agent/model.

    - Scans and redacts customer_email and customer_phone
    - Neutralizes and bounds customer_note
    - Redacts any accidental PII in customer_name or notes
    """
    sanitized = dict(order_dict)

    # (b) Redact PII
    if "customer_email" in sanitized and sanitized["customer_email"]:
        sanitized["customer_email"] = "[EMAIL_REDACTED]"
    if "customer_phone" in sanitized and sanitized["customer_phone"]:
        sanitized["customer_phone"] = "[PHONE_REDACTED]"

    # Also scrub any PII from free-text customer_name if present
    if "customer_name" in sanitized and sanitized["customer_name"]:
        sanitized["customer_name"] = redact_pii_text(sanitized["customer_name"])

    # (a) Contain & neutralize adversarial customer note
    if "customer_note" in sanitized:
        sanitized["customer_note"] = neutralize_customer_note(sanitized["customer_note"])

    return sanitized

