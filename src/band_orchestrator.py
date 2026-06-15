"""
BandOrchestrator (agent-API variant): Uses the Band *agent* REST API
(band_a_... key) — no Enterprise plan required.

Architecture:
  The orchestrator acts AS the TABIB Intake Agent.
  - create_or_get_session  → agent_api_chats.create_agent_chat()
  - post_patient_message   → audit-logs the patient message to the Band room
  - poll_for_decision      → runs the TABIB pipeline inline, posts "Decision:"
                             to the room, returns the result

The Band room is an audit trail; the pipeline (Intake→Diagnostic→Triage)
runs in-process via Anthropic so no other Band agents need to be running.

Required env vars (tabib-v1/.env):
  BAND_AGENT_API_KEY  band_a_... key from agent_config.yaml
  BAND_AGENT_ID       agent UUID from agent_config.yaml
  ANTHROPIC_API_KEY   used by the inline pipeline
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from band.client.rest import (
        ChatRoomRequest,
        DEFAULT_REQUEST_OPTIONS,
        NotFoundError,
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
    Enterprise plan is required. The Band chat room serves as an audit trail;
    the Intake→Diagnostic→Triage pipeline runs inline via Anthropic.
    """

    def __init__(
        self,
        agent_api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """
        Args:
            agent_api_key: band_a_... key. Falls back to BAND_AGENT_API_KEY env var.
            agent_id: Agent UUID. Falls back to BAND_AGENT_ID env var.

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
        self._client = RestClient(api_key=key, base_url=_BAND_REST_URL)
        self._sessions: dict[str, str] = {}   # phone_number → chat_room_id
        self._pending: dict[str, str] = {}    # session_id → patient message text

        logger.info("[BandOrchestrator] Ready. agent_id=%s", aid)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def create_or_get_session(self, phone_number: str) -> str:
        """Return an existing Band chat room for this patient or create a new one.

        Rooms are owned by the TABIB Intake Agent and cached in memory by
        phone number for the lifetime of this orchestrator instance.

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
                "[BandOrchestrator] Reusing session %s for %s", session_id, phone_number
            )
            return session_id

        logger.info("[BandOrchestrator] Creating Band session for %s", phone_number)
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
        return session_id

    def post_patient_message(self, session_id: str, message: str) -> None:
        """Audit-log the patient's WhatsApp message to the Band chat room.

        The message is stored internally so poll_for_decision can run the
        pipeline against it. It is also posted to the Band room as an intake
        record attributed to this agent.

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

    def poll_for_decision(self, session_id: str, timeout: int = 30) -> str:
        """Run the TABIB pipeline on the pending message and return the decision.

        Retrieves the patient message stored by post_patient_message, passes it
        through Intake→Diagnostic→Triage via Anthropic, posts "Decision: …" to
        the Band room, and returns the decision text.

        The timeout guards against a runaway pipeline; BandTimeoutError is raised
        if the pipeline does not complete in time.

        Args:
            session_id: Band chat room UUID to process.
            timeout: Maximum seconds to wait for the pipeline to finish.

        Returns:
            Full triage decision text, starting with "Decision:".

        Raises:
            BandTimeoutError: If the pipeline exceeds timeout seconds.
            BandSessionError: If no pending message exists for this session.
        """
        patient_message = self._pending.pop(session_id, None)
        if not patient_message:
            raise BandSessionError(
                f"No pending message for session {session_id}. "
                "Call post_patient_message first."
            )

        logger.info(
            "[BandOrchestrator] Running pipeline for session %s (timeout=%ds)",
            session_id, timeout,
        )

        deadline = time.monotonic() + timeout
        try:
            # Import here so the module loads even without tabib-v1 deps installed
            from band_coordinator import run_tabib_pipeline  # noqa: PLC0415

            decision_text = run_tabib_pipeline(
                whatsapp_message=patient_message,
                patient_id=session_id,
            )
        except Exception as e:
            raise BandSessionError(
                f"TABIB pipeline failed for session {session_id}: {e}"
            ) from e

        if time.monotonic() > deadline:
            raise BandTimeoutError(
                f"Pipeline for session {session_id} exceeded {timeout}s."
            )

        # Normalise: ensure output starts with "Decision:"
        decision = (
            decision_text
            if decision_text.lstrip().startswith("Decision:")
            else f"Decision: {decision_text}"
        )

        logger.info("[BandOrchestrator] Decision for session %s: %r", session_id, decision[:120])
        return decision
