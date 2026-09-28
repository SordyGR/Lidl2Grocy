"""VNC works without a password (RED first)."""
import os


def _entrypoint_text():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "docker-entrypoint.sh"), encoding="utf-8") as f:
        return f.read()


def test_entrypoint_starts_x11vnc_without_password():
    text = _entrypoint_text()
    assert "-nopw" in text
    assert "-rfbauth" not in text
    assert "VNC_PASSWORD" not in text
    assert ".vnc-pass" not in text
