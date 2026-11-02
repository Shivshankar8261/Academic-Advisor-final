"""
ALICE's identity: her name, and how she recognises being greeted or asked who she is. Fixed in code, so it holds across
new chats and restarts, needs no model, and costs no tokens.

Text from the microphone arrives through the same path as typed text, so "Hey, Alice!" and "hi alice." match alike.
"""
import re

from config import ADVISOR_FULL_NAME, ADVISOR_NAME

GREETING_REPLY = "Hi! I'm Alice, your academic advisor. What would you like help with today?"
IDENTITY_REPLY = (f"I'm {ADVISOR_NAME}: {ADVISOR_FULL_NAME}. I'm the academic advisor for Vidyashilp University, and I can "
                  "help with courses, prerequisites, credits, minors, attendance and registration.")

HELLO = r"(?:hi+|hey+|hello+|hiya|namaste|greetings|yo|good (?:morning|afternoon|evening))"
# Whisper can spell the name differently; the variants count only straight after a greeting word.
HEARD_AS = r"(?:alice|alise|alis|allis|alyce|ellis|elis|elise)"
GREETING_RE = re.compile(rf"{HELLO}(?: there)?(?: (?:{HEARD_AS}|advisor))?(?: there)?|alice")
ADDRESS_RE = re.compile(rf"^\W*(?:{HELLO}\W+(?:there\W+)?{HEARD_AS}|alice)\b[\s,:;.!-]+(?=\w)", re.I)
IDENTITY_RE = re.compile(
    r"(?:what(?:'s| is| s)|tell me|say|give me) your (?:full |real )?name(?: please)?"
    r"|your name|what (?:should|do|can) i call you|what are you called|who (?:are|r) (?:you|u)|introduce yourself"
    r"|(?:are you|is this|am i talking to) alice"
    r"|what(?:'s| is| s) alice|who(?:'s| is| s) alice"
    r"|what (?:does|do) (?:the name )?alice (?:stand for|mean)"
    r"|(?:what(?:'s| is| s) )?(?:the )?(?:full form|meaning|expansion|acronym) (?:of|for) (?:(?:the name )?alice|your name)"
    r"|(?:what(?:'s| is| s) )?(?:the )?(?:alice|your name) (?:full form|acronym)"
    r"|what(?:'s| is| s) your full form")


def _plain(message: str) -> str:
    """Lower case, straight apostrophes, punctuation as spaces: "HEY, Alice!!" -> "hey alice"."""
    text = re.sub(r"[^\w\s']", " ", message.lower().replace("’", "'"))
    return re.sub(r"\s+", " ", text).strip()


def strip_address(message: str) -> str:
    """"Hi Alice, how many credits?" -> "how many credits?". A greeting alone is left as it is."""
    if IDENTITY_RE.fullmatch(_plain(message)):  # "alice full form": her name is the subject here, not an address
        return message.strip()
    return ADDRESS_RE.sub("", message.strip(), count=1)


def reply(message: str) -> str | None:
    """The fixed reply if the whole message is a greeting or asks who she is; None for anything else."""
    for text in (_plain(message), _plain(strip_address(message))):
        if GREETING_RE.fullmatch(text):
            return GREETING_REPLY
        if IDENTITY_RE.fullmatch(text):
            return IDENTITY_REPLY
    return None
