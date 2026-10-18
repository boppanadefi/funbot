"""
Unit tests for Solana RPC and WebSocket Connection Monitor.
"""

from unittest.mock import patch, MagicMock
import pytest
from src.telemetry.rpc_client import SolanaConnectionMonitor, mask_endpoint_key


def test_mask_endpoint_key():
    url = "wss://mainnet.helius-rpc.com/?api-key=795a4f2f-6d09-49a7-afdd-b9365365125e"
    masked = mask_endpoint_key(url)
    assert "795a...125e" in masked
    assert "795a4f2f-6d09-49a7-afdd-b9365365125e" not in masked

    url_with_param = "https://mainnet.helius-rpc.com/?api-key=secretkey123&commitment=confirmed"
    masked_param = mask_endpoint_key(url_with_param)
    assert "secr...y123" in masked_param
    assert "commitment=confirmed" in masked_param

    normal_url = "https://api.mainnet-beta.solana.com"
    assert mask_endpoint_key(normal_url) == normal_url


def test_check_http_rpc_success():
    monitor = SolanaConnectionMonitor(http_url="https://mock-rpc.com", ws_url="wss://mock-ws.com")
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": 450000000}
        mock_post.return_value = mock_resp

        res = monitor.check_http_rpc()
        assert res["ok"] is True
        assert res["slot"] == 450000000
        assert res["status"] == "ONLINE"


def test_check_http_rpc_failure():
    monitor = SolanaConnectionMonitor(http_url="https://mock-rpc.com", ws_url="wss://mock-ws.com")
    with patch("requests.post", side_effect=Exception("Connection refused")):
        res = monitor.check_http_rpc()
        assert res["ok"] is False
        assert res["slot"] is None
        assert res["status"] == "OFFLINE"
        assert "Connection refused" in res["error"]


def test_get_full_diagnostics():
    monitor = SolanaConnectionMonitor(
        http_url="https://mainnet.helius-rpc.com/?api-key=1234567890",
        ws_url="wss://mainnet.helius-rpc.com/?api-key=1234567890",
    )
    with patch.object(monitor, "check_http_rpc") as mock_http, patch.object(monitor, "check_ws_rpc") as mock_ws:
        mock_http.return_value = {
            "ok": True,
            "slot": 458000000,
            "latency_ms": 110.0,
            "endpoint": "https://mock",
            "status": "ONLINE",
            "error": None,
        }
        mock_ws.return_value = {
            "ok": True,
            "sub_id": 999,
            "latency_ms": 220.0,
            "endpoint": "wss://mock",
            "status": "CONNECTED",
            "error": None,
        }

        diag = monitor.get_full_diagnostics(force_refresh=True)
        assert diag["is_connected"] is True
        assert diag["provider"] == "Helius Dedicated RPC (Mainnet)"
        assert diag["slot"] == 458000000
        assert diag["staleness_compliant"] is True
        assert diag["avg_latency_ms"] == 165.0
