"""Chat session persistence — CRUD + LLM title generation."""

import json
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, release_connection_before_external_io
from app.models import ChatSession, ChatSessionMessage
from app.services.llm_provider import llm_chat, estimate_cost

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat Sessions"])


# ── Pydantic schemas ──

class SessionCreate(BaseModel):
    title: str | None = None


class SessionSummary(BaseModel):
    id: int
    title: str | None
    created_at: str
    updated_at: str
    message_count: int = 0


class MessageAppend(BaseModel):
    role: str = Field(..., pattern=r"^(user|assistant)$")
    text: str = Field(..., min_length=1)
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    neurons_activated: int = 0
    neuron_scores: list[dict] | None = None


class SessionTitleUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class MessageOut(BaseModel):
    id: int
    role: str
    text: str
    model: str | None
    input_tokens: int
    output_tokens: int
    cost: float
    neurons_activated: int
    neuron_scores: list[dict] | None
    created_at: str


class SessionDetail(BaseModel):
    id: int
    title: str | None
    created_at: str
    updated_at: str
    messages: list[MessageOut]


# ── Endpoints ──

@router.post("/sessions")
async def create_session(
    body: SessionCreate | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Create a new chat session."""
    session = ChatSession(title=body.title if body else None)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    assert session.id is not None, "Session ID must be set after commit"
    return {"id": session.id, "created_at": session.created_at.isoformat()}


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List non-archived sessions, newest first."""
    stmt = (
        select(ChatSession)
        .where(ChatSession.archived_at.is_(None))
        .order_by(ChatSession.updated_at.desc())
        .limit(limit)
        .options(selectinload(ChatSession.messages))
    )
    result = await db.execute(stmt)
    sessions = result.scalars().all()
    assert isinstance(sessions, list) or hasattr(sessions, '__iter__'), "Expected iterable sessions"
    return [
        SessionSummary(
            id=s.id,
            title=s.title,
            created_at=s.created_at.isoformat(),
            updated_at=s.updated_at.isoformat(),
            message_count=len(s.messages),
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: int, db: AsyncSession = Depends(get_db)):
    """Load a session with all its messages."""
    stmt = (
        select(ChatSession)
        .where(ChatSession.id == session_id)
        .options(selectinload(ChatSession.messages))
    )
    result = await db.execute(stmt)
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    assert s.id == session_id, "Loaded session ID mismatch"
    return SessionDetail(
        id=s.id,
        title=s.title,
        created_at=s.created_at.isoformat(),
        updated_at=s.updated_at.isoformat(),
        messages=[_msg_out(m) for m in s.messages],
    )


@router.post("/sessions/{session_id}/messages")
async def append_message(
    session_id: int,
    body: MessageAppend,
    db: AsyncSession = Depends(get_db),
):
    """Append a message to a session."""
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    msg = ChatSessionMessage(
        session_id=session_id,
        role=body.role,
        text=body.text,
        model=body.model,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        cost=body.cost,
        neurons_activated=body.neurons_activated,
        neuron_scores_json=json.dumps(body.neuron_scores) if body.neuron_scores else None,
    )
    db.add(msg)
    session.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(msg)
    assert msg.id is not None, "Message ID must be set after commit"
    return {"id": msg.id, "created_at": msg.created_at.isoformat()}


@router.patch("/sessions/{session_id}")
async def update_session_title(
    session_id: int,
    body: SessionTitleUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a session's title."""
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.title = body.title
    session.updated_at = datetime.utcnow()
    await db.commit()
    return {"ok": True}


@router.delete("/sessions/{session_id}")
async def archive_session(session_id: int, db: AsyncSession = Depends(get_db)):
    """Soft-delete (archive) a session."""
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.archived_at = datetime.utcnow()
    await db.commit()
    return {"ok": True}


# Patterns that strip common preambles Haiku sometimes emits despite the
# system prompt telling it not to. Order matters: more specific first.
# JPL-6: tuples of (pattern, replacement) — immutable module data.
_TITLE_PREAMBLE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Apology / refusal that transitions via "but/however/so/therefore":
    # "I appreciate the question but X", "I can't answer this directly, but X",
    # "I don't have access, but X". Broader verb set to catch Haiku's many
    # refusal phrasings.
    (re.compile(
        r"^\s*(?:i\s+appreciate|i\s+can(?:'|no)t|i\s+don(?:'|no)t|i[\s']m\s+(?:sorry|not|unable)|"
        r"as\s+an?\s+(?:ai|assistant|llm|language\s+model)|as\s+claude|sorry|unfortunately|"
        r"let\s+me|this\s+appears|it\s+looks)\b"
        r"[^\n]*?\b(?:but|however|so|therefore)\s+",
        re.IGNORECASE,
    ), ""),
    # Hedge / observation that transitions via "about/regarding/concerning/on":
    # "I think the user is asking about X", "I notice X is about Y",
    # "It seems to be about X".
    (re.compile(
        r"^\s*(?:i\s+(?:think|believe|would\s+say|notice|see|observe)|"
        r"i[\s']d\s+say|it\s+(?:seems|appears|looks)|this\s+(?:seems|appears|is))\b"
        r"[^\n]*?\b(?:about|regarding|concerning|on)\s+",
        re.IGNORECASE,
    ), ""),
    # Label-style prefixes: "Title:", "Topic:", "Subject:"
    (re.compile(
        r"^\s*(?:title|topic|subject|the\s+topic|the\s+subject)\s*[:\-—]\s*",
        re.IGNORECASE,
    ), ""),
    # Explicit lead-in phrases: "The topic is X", "This question is about X",
    # "Here is the title: X"
    (re.compile(
        r"^\s*(?:the\s+topic\s+is|the\s+subject\s+is|this\s+(?:question\s+)?is\s+about|"
        r"here\s+is\s+(?:the\s+)?(?:title|topic)\s*[:\-—]?)\s*",
        re.IGNORECASE,
    ), ""),
)


# If after all regex passes the title STILL starts with one of these tokens,
# the LLM is almost certainly still emitting a preamble/refusal rather than
# a topic — reject and fall back to message-based extraction. No real title
# in the 3-6 word range starts with "I", "we", "my", "sorry", etc.
_TITLE_REJECT_LEADING_TOKENS: frozenset[str] = frozenset({
    "i", "we", "my", "our", "you", "your", "it", "that",
    "sorry", "unfortunately", "as", "let", "this", "here",
    "the", "a", "an",  # real titles rarely start with bare article
})
_TITLE_MAX_WORDS = 6


# System prompt for the title generator. Kept as a module constant (not
# inline) so `generate_title` stays under the JPL-4 60-line guideline.
_TITLE_SYSTEM_PROMPT: str = (
    "You extract the TOPIC of a user's question into a short title. "
    "Your entire response must be exactly 3-6 words describing the topic. "
    "START YOUR RESPONSE WITH THE TOPIC WORDS IMMEDIATELY — do NOT write "
    "\"Title:\", \"The topic is\", \"I think\", \"I appreciate\", \"Here is\", "
    "\"This question is about\", or any other preamble. No quotes, no "
    "punctuation at the end, no explanation, no apology. If the message "
    "is vague or conversational, pick the most concrete noun in it or "
    "output \"General Inquiry\".\n\n"
    "Examples of CORRECT responses:\n"
    "  Input: What are the key requirements of AS9100D?\n"
    "  Output: AS9100D Key Requirements\n"
    "  Input: Help me understand our cost allocation process\n"
    "  Output: Cost Allocation Process Overview\n"
    "  Input: Compare FAR and DFARS compliance\n"
    "  Output: FAR vs DFARS Compliance\n"
    "  Input: what is our travel policy for international trips\n"
    "  Output: International Travel Policy\n"
    "  Input: hi\n"
    "  Output: General Inquiry\n\n"
    "Examples of INCORRECT responses (do NOT do these):\n"
    "  \"I appreciate the question but...\"  ← preamble, forbidden\n"
    "  \"I can't answer this directly...\"   ← apology, forbidden\n"
    "  \"Title: AS9100D Requirements\"       ← has label prefix, forbidden\n"
    "  \"The topic is cost allocation\"      ← has lead-in, forbidden"
)


# Common filler words stripped when deriving a title directly from a user
# message (fallback path). Tuple so JPL-6 mutable-global rule holds.
_TITLE_FALLBACK_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "is", "are", "am", "be", "to", "of",
    "in", "on", "at", "for", "and", "or", "what", "how",
    "why", "when", "where", "who", "which", "can", "do",
    "does", "i", "you", "we", "they", "my", "our",
    # Conversational greetings — if the message is just these, the title
    # becomes empty and we fall through to "General Inquiry".
    "hi", "hello", "hey", "thanks", "thank",
})


def _looks_like_preamble(title: str) -> str | bool:
    """True if the title's first word suggests the LLM is still preambling.

    After the regex passes, a 3-6-word title starting with "I", "We",
    "Sorry", "As", "Let", etc. is almost always a refusal or hedge — not
    a topic. When this hits, callers should use the fallback extractor.
    """
    assert isinstance(title, str), "title must be str"
    first = title.strip().split(None, 1)
    if not first:
        return True
    return first[0].lower().rstrip(",.:!?'\"") in _TITLE_REJECT_LEADING_TOKENS


def _fallback_title_from_message(text: str) -> str:
    """Derive a title from the first content-y words of the user's message.

    Used when the LLM returns pure preamble and `_clean_generated_title`
    strips everything. Takes up to six non-stopword tokens.
    """
    assert isinstance(text, str), "text must be str"
    words = [
        w for w in re.split(r"\s+", text.strip())
        if w and w.lower() not in _TITLE_FALLBACK_STOPWORDS
    ][:_TITLE_MAX_WORDS]
    return " ".join(words)[:200] or "General Inquiry"


def _clean_generated_title(raw: str) -> str:
    """Strip preambles, quotes, and trailing punctuation from an LLM-generated title.

    Handles the common failure modes where the model emits "I appreciate the
    question but...", "Title: X", or an apologetic lead-in despite the prompt.
    """
    assert isinstance(raw, str), "raw must be str"
    text = raw.strip()
    # Take only the first line — sometimes the model adds a blank line + explanation.
    text = text.split("\n")[0].strip()
    # Strip surrounding quotes.
    text = text.strip('"').strip("'").strip("`").strip()
    # Strip recognized preambles until none match (max 3 iterations — bounded).
    for _ in range(3):
        before = text
        for pattern, replacement in _TITLE_PREAMBLE_PATTERNS:
            text = pattern.sub(replacement, text)
        text = text.strip('"').strip("'").strip()
        if text == before:
            break
    # Trim trailing punctuation (. ! ? : ; , -) that some models append.
    text = text.rstrip(".!?:;,-—").strip()
    # Cap at _TITLE_MAX_WORDS words so a rambling output stays a title.
    words = text.split()
    if not words:
        return ""
    return " ".join(words[:_TITLE_MAX_WORDS])[:200]


@router.post("/sessions/{session_id}/generate-title")
async def generate_title(
    session_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Use Haiku to generate a 3-6 word title from the first exchange."""
    stmt = (
        select(ChatSession)
        .where(ChatSession.id == session_id)
        .options(selectinload(ChatSession.messages))
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Extract the first user message only
    first_user_msg = next((m for m in session.messages if m.role == "user"), None)
    if not first_user_msg:
        raise HTTPException(status_code=400, detail="No user message to generate title from")

    # The session and its first message are fully loaded. Return the read
    # transaction's connection before the title-model subprocess runs.
    await release_connection_before_external_io(db)

    try:
        # max_tokens=40 (not 20) so occasional preamble doesn't truncate the
        # actual title — post-processing strips preambles before saving.
        res = await llm_chat(_TITLE_SYSTEM_PROMPT, first_user_msg.text[:300], max_tokens=40, model="haiku")
        title = _clean_generated_title(res["text"])
        if not title or _looks_like_preamble(title):
            # Either cleaner stripped everything, OR the LLM is still
            # emitting a preamble after all regex passes (Haiku occasionally
            # refuses: "I notice X", "I don't have access to Y"). Fall back
            # to deterministic extraction from the user's own message.
            title = _fallback_title_from_message(first_user_msg.text)
        assert len(title) > 0, "Generated title must not be empty"
    except (AssertionError, ValueError, RuntimeError) as e:
        logger.warning("Title generation failed: %s", e)
        title = "Untitled conversation"
        res = {"input_tokens": 0, "output_tokens": 0}

    session.title = title
    session.updated_at = datetime.utcnow()
    await db.commit()
    cost = estimate_cost("haiku", res.get("input_tokens", 0), res.get("output_tokens", 0))
    return {"title": title, "cost_usd": cost}


def _msg_out(m: ChatSessionMessage) -> MessageOut:
    """Convert a ChatSessionMessage ORM object to a MessageOut schema."""
    scores = None
    if m.neuron_scores_json:
        try:
            scores = json.loads(m.neuron_scores_json)
        except json.JSONDecodeError:
            scores = None
    assert m.role in ("user", "assistant"), f"Invalid message role: {m.role}"
    return MessageOut(
        id=m.id,
        role=m.role,
        text=m.text,
        model=m.model,
        input_tokens=m.input_tokens,
        output_tokens=m.output_tokens,
        cost=m.cost,
        neurons_activated=m.neurons_activated,
        neuron_scores=scores,
        created_at=m.created_at.isoformat(),
    )
