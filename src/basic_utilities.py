from typing import Optional, Tuple, List, Iterable
from .models import MESSAGE_CONSUMED, MESSAGE_INCOMING, MESSAGE_OUTGOING, LogEvent, IGNORED_MNEMONICS
from .parsing import normalize_type, extract_message_name
from .tool_logger import logger

# ============================================================
# Basic utilities
# ============================================================

def extract_mnemonic_from_line(line: str) -> Optional[str]:
    """
    Extract the 2nd |-separated field as mnemonic, normalized to upper case.
    Returns None if line does not look like a TTCN event line.
    """
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    parts = raw.split("|", 2)
    if len(parts) < 2:
        return None
    return normalize_type(parts[1].strip())

def classify_event(ev: LogEvent) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (bucket, label) where bucket in {"in", "consume", "out"} or None.
    label is a human-readable short identifier, e.g. message name.
    """
    et = ev.type
    label = extract_message_name(ev)

    if et in MESSAGE_INCOMING:
        return "in", label
    if et in MESSAGE_CONSUMED:
        return "consume", label
    if et in MESSAGE_OUTGOING:
        return "out", label
    return None, None

def preprocess_lines(lines: Iterable[str]) -> List[str]:
    """
    Pre-processor that removes lines whose mnemonic is in IGNORED_MNEMONICS.

    This runs before parsing; it is purely a filter on raw lines.
    """
    filtered: List[str] = []
    dropped = 0

    for idx, line in enumerate(lines, start=1):
        mnem = extract_mnemonic_from_line(line)
        if mnem is not None and mnem in IGNORED_MNEMONICS:
            dropped += 1
            logger.debug(
                "preprocess_lines: dropping line %d with ignored mnemonic %s: %s",
                idx,
                mnem,
                line.rstrip("\n"),
            )
            continue

        filtered.append(line)

    logger.info(
        "preprocess_lines: input_lines=%d kept=%d dropped=%d",
        idx if 'idx' in locals() else 0,
        len(filtered),
        dropped,
    )
    return filtered