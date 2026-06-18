"""
BandOrchestrator (agent-API variant): Uses the Band *agent* REST API
(band_a_... key) — no Enterprise plan required.

Architecture:
  The orchestrator acts AS the TABIB Intake Agent (TIA).

  Real Band multi-agent loop (when TDA/TTA IDs are configured):
  1. create_or_get_session  → agent_api_chats.create_agent_chat()
     Adds TDA and TTA as participants via agent_api_participants.
  2. post_patient_message   → posts "@TDA <symptoms>" to the Band room,
                              mentioning TDA so Band routes the message.
  3. poll_for_decision      → polls agent_api_messages.list_agent_messages()
                              every 2s for a message containing "Decision:".
                              If found within 25s → band_agent_loop path.
                              If timeout → falls back to inline pipeline.

  Inline fallback (when TDA/TTA IDs are absent OR Band loop times out):
  - Runs the TABIB pipeline (Intake→Diagnostic→Triage) in-process via
    Anthropic and returns the result directly. Path = inline_fallback.

Required env vars (tabib-v1/.env):
  BAND_AGENT_API_KEY  band_a_... key from agent_config.yaml
  BAND_AGENT_ID       agent UUID from agent_config.yaml (TIA)
  ANTHROPIC_API_KEY   used by the inline pipeline
  BAND_TDA_ID         (optional) Diagnostic Agent UUID in Band
  BAND_TTA_ID         (optional) Triage Agent UUID in Band
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from band.client.rest import (
        ChatMessageRequest,
        ChatMessageRequestMentionsItem,
        ChatRoomRequest,
        DEFAULT_REQUEST_OPTIONS,
        NotFoundError,
        ParticipantRequest,
        RestClient,
        UnauthorizedError,
    )
    from thenvoi_rest.errors.forbidden_error import ForbiddenError

    _BAND_SDK_AVAILABLE = True
except ImportError:
    _BAND_SDK_AVAILABLE = False

_BAND_REST_URL = "https://app.band.ai"
_POLL_INTERVAL_SECONDS = 2


class BandSessionError(Exception):
    """Band chat session could not be created, or a message could not be sent."""


class BandTimeoutError(Exception):
    """Pipeline did not complete within the polling timeout."""


class BandOrchestrator:
    """Routes patient messages through the TABIB pipeline via Band agent API.

    Authenticates as the TABIB Intake Agent using its agent key, so no
    Enterprise plan is required.

    When BAND_TDA_ID and BAND_TTA_ID are set, the orchestrator attempts the
    real Band multi-agent loop: TIA posts symptoms to TDA in the Band room and
    polls for a "Decision:" reply. If neither ID is set, or if the loop does
    not produce a decision within 25 s, the orchestrator falls back to running
    the Intake→Diagnostic→Triage pipeline inline via Anthropic.

    The Band chat room always serves as an audit trail regardless of path.
    """

    def __init__(
        self,
        agent_api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        tda_id: Optional[str] = None,
        tta_id: Optional[str] = None,
    ) -> None:
        """
        Args:
            agent_api_key: band_a_... key. Falls back to BAND_AGENT_API_KEY env var.
            agent_id: TIA UUID. Falls back to BAND_AGENT_ID env var.
            tda_id: TDA UUID. Falls back to BAND_TDA_ID env var. Optional.
            tta_id: TTA UUID. Falls back to BAND_TTA_ID env var. Optional.

        Raises:
            BandSessionError: If band-sdk is not installed or credentials are missing.
        """
        if not _BAND_SDK_AVAILABLE:
            raise BandSessionError(
                "band-sdk is not installed. Run: pip install band-sdk[anthropic]"
            )

        key = agent_api_key or os.getenv("BAND_AGENT_API_KEY")
        if not key:
            raise BandSessionError(
                "Agent API key required. Set BAND_AGENT_API_KEY or pass agent_api_key."
            )

        aid = agent_id or os.getenv("BAND_AGENT_ID")
        if not aid:
            raise BandSessionError(
                "Agent ID required. Set BAND_AGENT_ID or pass agent_id."
            )

        self._agent_id: str = aid
        self._tda_id: Optional[str] = tda_id or os.getenv("BAND_TDA_ID") or None
        self._tta_id: Optional[str] = tta_id or os.getenv("BAND_TTA_ID") or None
        self._client = RestClient(api_key=key, base_url=_BAND_REST_URL)
        self._sessions: dict[str, str] = {}   # phone_number → chat_room_id
        self._pending: dict[str, str] = {}    # session_id → patient message text

        # Normalise empty strings from env to None
        if self._tda_id == "":
            self._tda_id = None
        if self._tta_id == "":
            self._tta_id = None

        band_loop_ready = bool(self._tda_id)
        logger.info(
            "[BandOrchestrator] Ready. agent_id=%s tda_id=%s tta_id=%s band_loop=%s",
            aid,
            self._tda_id or "(not set)",
            self._tta_id or "(not set)",
            band_loop_ready,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def _find_room_with_agents(self) -> Optional[str]:
        """Return the most recently updated Band room that has TDA as a participant.

        Queries all rooms TIA belongs to (paginated), checks each for TDA
        membership, and returns the most recently active match. Returns None
        if no suitable room is found or if TDA is not configured.
        """
        if not self._tda_id:
            return None

        try:
            all_rooms = []
            page = 1
            while True:
                resp = self._client.agent_api_chats.list_agent_chats(
                    page=page,
                    page_size=100,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
                if resp and resp.data:
                    all_rooms.extend(resp.data)
                total_pages = getattr(getattr(resp, "metadata", None), "total_pages", None)
                if total_pages is None or page >= total_pages:
                    break
                page += 1
        except Exception as e:
            logger.warning("[BandOrchestrator] Could not list agent chats: %s", e)
            return None

        # Most recently updated first
        all_rooms.sort(key=lambda r: r.updated_at, reverse=True)

        for room in all_rooms:
            try:
                parts_resp = self._client.agent_api_participants.list_agent_chat_participants(
                    room.id,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
                participant_ids = {p.id for p in (parts_resp.data or [])}
                if self._tda_id in participant_ids:
                    logger.info(
                        "[BandOrchestrator] Found active room %s with TDA (updated_at=%s)",
                        room.id, room.updated_at,
                    )
                    return room.id
            except Exception as e:
                logger.warning(
                    "[BandOrchestrator] Could not check participants for room %s: %s",
                    room.id, e,
                )
                continue

        return None

    def create_or_get_session(self, phone_number: str) -> str:
        """Return an existing Band chat room for this patient or create a new one.

        First checks the in-memory cache, then queries the Band API for an
        existing room where TDA is already a participant (survives bridge
        restarts). Only creates a new room when none is found.

        Args:
            phone_number: Patient identifier (e.g. "919876543210").

        Returns:
            Band chat room UUID (session_id).

        Raises:
            BandSessionError: If the Band API rejects room creation.
        """
        if phone_number in self._sessions:
            session_id = self._sessions[phone_number]
            logger.info(
                "[BandOrchestrator] Reusing cached session %s for %s", session_id, phone_number
            )
            return session_id

        # Look for an existing room where the agents are already present
        existing = self._find_room_with_agents()
        if existing:
            self._sessions[phone_number] = existing
            logger.info(
                "[BandOrchestrator] Reusing existing agent room %s for %s", existing, phone_number
            )
            return existing

        logger.info("[BandOrchestrator] Creating new Band session for %s", phone_number)
        try:
            response = self._client.agent_api_chats.create_agent_chat(
                chat=ChatRoomRequest(),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            session_id: str = response.data.id
        except UnauthorizedError as e:
            raise BandSessionError(
                f"Band API rejected agent credentials: {e}"
            ) from e
        except ForbiddenError as e:
            code = getattr(getattr(e.body, "error", None), "code", "forbidden")
            msg = getattr(getattr(e.body, "error", None), "message", str(e))
            raise BandSessionError(f"Band API access denied ({code}): {msg}") from e
        except Exception as e:
            raise BandSessionError(
                f"Failed to create Band chat session for {phone_number}: {e}"
            ) from e

        self._sessions[phone_number] = session_id
        logger.info("[BandOrchestrator] Created session %s for %s", session_id, phone_number)

        # Add TDA and TTA as participants so they can receive @mentions
        for agent_label, agent_id in [("TDA", self._tda_id), ("TTA", self._tta_id)]:
            if not agent_id:
                logger.info(
                    "[BandOrchestrator] Skipping %s participant (ID not configured)", agent_label
                )
                continue
            try:
                self._client.agent_api_participants.add_agent_chat_participant(
                    chat_id=session_id,
                    participant=ParticipantRequest(participant_id=agent_id),
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
                logger.info(
                    "[BandOrchestrator] Added %s (%s) to session %s",
                    agent_label, agent_id, session_id,
                )
            except Exception as e:
                # Non-fatal: log and continue — Band loop will still time out to
                # inline fallback if the participant couldn't be added.
                logger.warning(
                    "[BandOrchestrator] Could not add %s (%s) to session %s: %s",
                    agent_label, agent_id, session_id, e,
                )

        return session_id

    def post_patient_message(self, session_id: str, message: str) -> None:
        """Post the patient's message to the Band room and store it for the pipeline.

        If TDA is configured, the message is posted with an @TDA mention so Band
        routes it to the Diagnostic Agent. Otherwise the message is stored
        internally for the inline pipeline fallback.

        Args:
            session_id: Band chat room UUID from create_or_get_session.
            message: Raw WhatsApp message text from the patient.

        Raises:
            BandSessionError: If the Band API call fails fatally.
        """
        self._pending[session_id] = message
        logger.info(
            "[BandOrchestrator] Stored message for session %s: %r", session_id, message[:80]
        )

        if not self._tda_id:
            logger.info(
                "[BandOrchestrator] TDA not configured — skipping @mention post, "
                "will use inline fallback."
            )
            return

        # Post "@TDA <patient symptoms>" with a mention so Band routes the message
        content = f"@TDA {message}"
        try:
            self._client.agent_api_messages.create_agent_chat_message(
                chat_id=session_id,
                message=ChatMessageRequest(
                    content=content,
                    mentions=[ChatMessageRequestMentionsItem(id=self._tda_id)],
                ),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            logger.info(
                "[BandOrchestrator] Posted @TDA message to session %s (%d chars)",
                session_id, len(content),
            )
        except Exception as e:
            # Non-fatal: log and rely on inline fallback in poll_for_decision
            logger.warning(
                "[BandOrchestrator] Could not post @TDA message to session %s: %s — "
                "poll_for_decision will fall back to inline pipeline.",
                session_id, e,
            )

    def poll_for_decision(self, session_id: str, timeout: int = 25) -> str:
        """Wait for a "Decision:" reply from TDA/TTA or fall back to inline pipeline.

        Tries the real Band multi-agent loop first:
          - Polls list_agent_messages() every 2 s looking for a message whose
            content contains "Decision:".
          - If found within *timeout* seconds → returns it, path=band_agent_loop.
          - If timeout expires → runs the inline Anthropic pipeline, path=inline_fallback.

        When TDA is not configured the method skips straight to inline fallback.

        Args:
            session_id: Band chat room UUID to process.
            timeout: Maximum seconds to wait for the Band loop (default 25).

        Returns:
            Full triage decision text prefixed with "Decision:".
            Always includes "[path=band_agent_loop]" or "[path=inline_fallback]".

        Raises:
            BandSessionError: If no pending message exists for this session, or
                              if the inline pipeline fails.
        """
        patient_message = self._pending.pop(session_id, None)
        if not patient_message:
            raise BandSessionError(
                f"No pending message for session {session_id}. "
                "Call post_patient_message first."
            )

        # ------------------------------------------------------------------
        # Try Band multi-agent loop (only when TDA is configured)
        # ------------------------------------------------------------------
        if self._tda_id:
            logger.info(
                "[BandOrchestrator] Polling Band loop for session %s (timeout=%ds)",
                session_id, timeout,
            )
            deadline = time.monotonic() + timeout
            seen_ids: set[str] = set()

            while time.monotonic() < deadline:
                try:
                    resp = self._client.agent_api_messages.list_agent_messages(
                        chat_id=session_id,
                        request_options=DEFAULT_REQUEST_OPTIONS,
                    )
                    messages = resp.data if resp and resp.data else []
                except Exception as e:
                    logger.warning(
                        "[BandOrchestrator] list_agent_messages error for %s: %s",
                        session_id, e,
                    )
                    messages = []

                for msg in messages:
                    if msg.id in seen_ids:
                        continue
                    seen_ids.add(msg.id)
                    if "Decision:" in (msg.content or ""):
                        decision = msg.content.strip()
                        if not decision.lstrip().startswith("Decision:"):
                            # Extract from wherever "Decision:" appears
                            idx = decision.find("Decision:")
                            decision = decision[idx:]
                        logger.info(
                            "[BandOrchestrator] Band loop decision for session %s: %r",
                            session_id, decision[:120],
                        )
                        result = f"{decision} [path=band_agent_loop]"
                        return result

                time.sleep(_POLL_INTERVAL_SECONDS)

            logger.info(
                "[BandOrchestrator] Band loop timed out for session %s after %ds — "
                "falling back to inline pipeline.",
                session_id, timeout,
            )
        else:
            logger.info(
                "[BandOrchestrator] TDA not configured — using inline fallback directly "
                "for session %s.",
                session_id,
            )

        # ------------------------------------------------------------------
        # Inline fallback: Intake → Diagnostic → Triage via Anthropic
        # ------------------------------------------------------------------
        logger.info(
            "[BandOrchestrator] Running inline pipeline for session %s", session_id
        )
        try:
            from band_coordinator import run_tabib_pipeline  # noqa: PLC0415

            decision_text = run_tabib_pipeline(
                whatsapp_message=patient_message,
                patient_id=session_id,
            )
        except Exception as e:
            raise BandSessionError(
                f"TABIB pipeline failed for session {session_id}: {e}"
            ) from e

        # Normalise: ensure output starts with "Decision:"
        decision = (
            decision_text
            if decision_text.lstrip().startswith("Decision:")
            else f"Decision: {decision_text}"
        )

        logger.info(
            "[BandOrchestrator] Inline decision for session %s: %r",
            session_id, decision[:120],
        )
        result = f"{decision} [path=inline_fallback]"
        return result
