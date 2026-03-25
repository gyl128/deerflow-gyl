from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.channels.service import NoChannelsConfiguredError
import importlib

gateway_app_module = importlib.import_module("app.gateway.app")


class DummyChannelService:
    def __init__(self, status):
        self._status = status

    def get_status(self):
        return self._status


def test_health_reports_disabled_channels(monkeypatch):
    monkeypatch.setattr(gateway_app_module, 'get_app_config', lambda: SimpleNamespace())
    monkeypatch.setattr(gateway_app_module, 'get_gateway_config', lambda: SimpleNamespace(host='0.0.0.0', port=8001))

    async def fake_start_channel_service():
        raise NoChannelsConfiguredError('No IM channels are enabled in config.')

    async def fake_stop_channel_service():
        return None

    monkeypatch.setattr('app.channels.service.start_channel_service', fake_start_channel_service)
    monkeypatch.setattr('app.channels.service.stop_channel_service', fake_stop_channel_service)

    with TestClient(gateway_app_module.create_app()) as client:
        response = client.get('/health')

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'healthy'
    assert payload['channels']['status'] == 'disabled'
    assert 'No IM channels are enabled in config.' in payload['channels']['reason']


def test_health_reports_degraded_channels(monkeypatch):
    monkeypatch.setattr(gateway_app_module, 'get_app_config', lambda: SimpleNamespace())
    monkeypatch.setattr(gateway_app_module, 'get_gateway_config', lambda: SimpleNamespace(host='0.0.0.0', port=8001))

    async def fake_start_channel_service():
        return DummyChannelService(
            {
                'status': 'degraded',
                'reason': 'one or more enabled channels failed to start',
                'service_running': True,
                'enabled_channels': ['feishu'],
                'running_channels': [],
                'failed_channels': {'feishu': 'start failed: boom'},
                'channels': {'feishu': {'enabled': True, 'running': False, 'error': 'start failed: boom'}},
            }
        )

    async def fake_stop_channel_service():
        return None

    monkeypatch.setattr('app.channels.service.start_channel_service', fake_start_channel_service)
    monkeypatch.setattr('app.channels.service.stop_channel_service', fake_stop_channel_service)

    with TestClient(gateway_app_module.create_app()) as client:
        response = client.get('/health')

    assert response.status_code == 200
    payload = response.json()
    assert payload['channels']['status'] == 'degraded'
    assert payload['channels']['failed_channels']['feishu'] == 'start failed: boom'
