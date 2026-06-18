"""
Unit tests for BandOrchestrator._find_room_with_agents and create_or_get_session.

Tests the dynamic room-lookup fix: instead of always creating a new room,
the orchestrator should reuse an existing room where TDA is a participant.

Run from tabib-v1/src/:
    python3 -m pytest test_band_orchestrator.py -v
or:
    python3 test_band_orchestrator.py
"""

import pathlib
import sys
import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

_SRC = pathlib.Path(__file__).parent.resolve()
_INTAKE_SITE = (
    pathlib.Path.home()
    / "Projects/tabib-agents/intake/.venv/lib/python3.14/site-packages"
)
for _p in (_SRC, _INTAKE_SITE):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _make_room(room_id: str, updated_at: dt.datetime) -> SimpleNamespace:
    return SimpleNamespace(id=room_id, updated_at=updated_at)


def _make_participant(agent_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=agent_id)


def _make_orchestrator_with_mock_client(tda_id="tda-uuid", tta_id="tta-uuid"):
    """Build a BandOrchestrator with all Band SDK calls mocked out."""
    with patch("band_orchestrator._BAND_SDK_AVAILABLE", True):
        with patch("band_orchestrator.RestClient") as MockRestClient:
            mock_client = MagicMock()
            MockRestClient.return_value = mock_client

            from band_orchestrator import BandOrchestrator
            orc = BandOrchestrator(
                agent_api_key="band_a_test_key",
                agent_id="tia-uuid",
                tda_id=tda_id,
                tta_id=tta_id,
            )
            orc._client = mock_client
            return orc, mock_client


def test_find_room_returns_most_recent_room_with_tda():
    orc, client = _make_orchestrator_with_mock_client()

    old_room = _make_room("old-room-id", dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc))
    new_room = _make_room("new-room-id", dt.datetime(2026, 6, 17, tzinfo=dt.timezone.utc))

    list_chats_resp = SimpleNamespace(data=[old_room, new_room], metadata=SimpleNamespace(total_pages=1))
    client.agent_api_chats.list_agent_chats.return_value = list_chats_resp

    def participants_for(room_id, request_options=None):
        if room_id == "new-room-id":
            return SimpleNamespace(data=[_make_participant("tda-uuid"), _make_participant("tta-uuid")])
        return SimpleNamespace(data=[])

    client.agent_api_participants.list_agent_chat_participants.side_effect = participants_for

    result = orc._find_room_with_agents()
    assert result == "new-room-id", f"Expected new-room-id, got {result}"
    print("PASS: test_find_room_returns_most_recent_room_with_tda")


def test_find_room_falls_back_to_older_room_if_newest_lacks_tda():
    orc, client = _make_orchestrator_with_mock_client()

    old_room = _make_room("old-room-id", dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc))
    new_room = _make_room("new-room-id", dt.datetime(2026, 6, 17, tzinfo=dt.timezone.utc))

    list_chats_resp = SimpleNamespace(data=[old_room, new_room], metadata=SimpleNamespace(total_pages=1))
    client.agent_api_chats.list_agent_chats.return_value = list_chats_resp

    def participants_for(room_id, request_options=None):
        if room_id == "old-room-id":
            return SimpleNamespace(data=[_make_participant("tda-uuid")])
        return SimpleNamespace(data=[])  # new room has no TDA

    client.agent_api_participants.list_agent_chat_participants.side_effect = participants_for

    result = orc._find_room_with_agents()
    assert result == "old-room-id", f"Expected old-room-id, got {result}"
    print("PASS: test_find_room_falls_back_to_older_room_if_newest_lacks_tda")


def test_find_room_returns_none_when_no_room_has_tda():
    orc, client = _make_orchestrator_with_mock_client()

    room = _make_room("some-room-id", dt.datetime(2026, 6, 17, tzinfo=dt.timezone.utc))
    list_chats_resp = SimpleNamespace(data=[room], metadata=SimpleNamespace(total_pages=1))
    client.agent_api_chats.list_agent_chats.return_value = list_chats_resp
    client.agent_api_participants.list_agent_chat_participants.return_value = SimpleNamespace(data=[])

    result = orc._find_room_with_agents()
    assert result is None, f"Expected None, got {result}"
    print("PASS: test_find_room_returns_none_when_no_room_has_tda")


def test_find_room_returns_none_when_tda_not_configured():
    orc, client = _make_orchestrator_with_mock_client(tda_id=None, tta_id=None)

    result = orc._find_room_with_agents()
    assert result is None
    client.agent_api_chats.list_agent_chats.assert_not_called()
    print("PASS: test_find_room_returns_none_when_tda_not_configured")


def test_create_or_get_session_reuses_existing_room_after_restart():
    """Simulates bridge restart: no in-memory cache, but Band API has an active room."""
    orc, client = _make_orchestrator_with_mock_client()

    existing_room = _make_room("existing-room-id", dt.datetime(2026, 6, 17, tzinfo=dt.timezone.utc))
    list_chats_resp = SimpleNamespace(data=[existing_room], metadata=SimpleNamespace(total_pages=1))
    client.agent_api_chats.list_agent_chats.return_value = list_chats_resp
    client.agent_api_participants.list_agent_chat_participants.return_value = SimpleNamespace(
        data=[_make_participant("tda-uuid")]
    )

    session_id = orc.create_or_get_session("whatsapp:+919876543210")

    assert session_id == "existing-room-id", f"Expected existing-room-id, got {session_id}"
    client.agent_api_chats.create_agent_chat.assert_not_called()
    print("PASS: test_create_or_get_session_reuses_existing_room_after_restart")


def test_create_or_get_session_creates_new_room_when_none_found():
    """When no existing room has TDA, a new room is created and agents added."""
    orc, client = _make_orchestrator_with_mock_client()

    list_chats_resp = SimpleNamespace(data=[], metadata=SimpleNamespace(total_pages=1))
    client.agent_api_chats.list_agent_chats.return_value = list_chats_resp

    new_chat = SimpleNamespace(data=SimpleNamespace(id="brand-new-room-id"))
    client.agent_api_chats.create_agent_chat.return_value = new_chat

    session_id = orc.create_or_get_session("whatsapp:+919876543210")

    assert session_id == "brand-new-room-id", f"Expected brand-new-room-id, got {session_id}"
    client.agent_api_chats.create_agent_chat.assert_called_once()
    assert client.agent_api_participants.add_agent_chat_participant.call_count == 2
    print("PASS: test_create_or_get_session_creates_new_room_when_none_found")


def test_create_or_get_session_uses_cache_on_second_call():
    """Second call for the same phone number hits the in-memory cache, no API calls."""
    orc, client = _make_orchestrator_with_mock_client()

    list_chats_resp = SimpleNamespace(data=[], metadata=SimpleNamespace(total_pages=1))
    client.agent_api_chats.list_agent_chats.return_value = list_chats_resp
    new_chat = SimpleNamespace(data=SimpleNamespace(id="room-abc"))
    client.agent_api_chats.create_agent_chat.return_value = new_chat

    phone = "whatsapp:+919876543210"
    first = orc.create_or_get_session(phone)
    client.reset_mock()  # clear call counts
    second = orc.create_or_get_session(phone)

    assert first == second == "room-abc"
    client.agent_api_chats.list_agent_chats.assert_not_called()
    client.agent_api_chats.create_agent_chat.assert_not_called()
    print("PASS: test_create_or_get_session_uses_cache_on_second_call")


if __name__ == "__main__":
    tests = [
        test_find_room_returns_most_recent_room_with_tda,
        test_find_room_falls_back_to_older_room_if_newest_lacks_tda,
        test_find_room_returns_none_when_no_room_has_tda,
        test_find_room_returns_none_when_tda_not_configured,
        test_create_or_get_session_reuses_existing_room_after_restart,
        test_create_or_get_session_creates_new_room_when_none_found,
        test_create_or_get_session_uses_cache_on_second_call,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__} — {e}")
            failed += 1
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
