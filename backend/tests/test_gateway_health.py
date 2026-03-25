import importlib
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.channels.service import NoChannelsConfiguredError

gateway_app_module = importlib.import_module('app.gateway.app')


class DummyChannelService:
    def __init__(self, status):
        self._status = status

    def get_status(self):
        return self._status


def _patch_config(monkeypatch, checkpointer_type='postgres'):
    monkeypatch.setattr(
        gateway_app_module,
        'get_app_config',
        lambda: SimpleNamespace(checkpointer=SimpleNamespace(type=checkpointer_type), model_extra={}),
    )
    monkeypatch.setattr(
        gateway_app_module,
        'get_gateway_config',
        lambda: SimpleNamespace(host='0.0.0.0', port=8001),
    )


def test_health_reports_disabled_channels(monkeypatch):
    _patch_config(monkeypatch)

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
    assert payload['runtime']['checkpointer']['type'] == 'postgres'


def test_health_reports_degraded_channels(monkeypatch):
    _patch_config(monkeypatch)

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


def test_ready_reports_ready(monkeypatch):
    _patch_config(monkeypatch)

    async def fake_start_channel_service():
        raise NoChannelsConfiguredError('No IM channels are enabled in config.')

    async def fake_stop_channel_service():
        return None

    async def fake_probe_langgraph():
        return True, None

    monkeypatch.setattr('app.channels.service.start_channel_service', fake_start_channel_service)
    monkeypatch.setattr('app.channels.service.stop_channel_service', fake_stop_channel_service)
    monkeypatch.setattr(gateway_app_module, '_probe_langgraph', fake_probe_langgraph)

    with TestClient(gateway_app_module.create_app()) as client:
        response = client.get('/ready')

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'ready'
    assert payload['checks']['langgraph']['ok'] is True


def test_ready_reports_not_ready_when_langgraph_fails(monkeypatch):
    _patch_config(monkeypatch)

    async def fake_start_channel_service():
        raise NoChannelsConfiguredError('No IM channels are enabled in config.')

    async def fake_stop_channel_service():
        return None

    async def fake_probe_langgraph():
        return False, 'langgraph probe failed: boom'

    monkeypatch.setattr('app.channels.service.start_channel_service', fake_start_channel_service)
    monkeypatch.setattr('app.channels.service.stop_channel_service', fake_stop_channel_service)
    monkeypatch.setattr(gateway_app_module, '_probe_langgraph', fake_probe_langgraph)

    with TestClient(gateway_app_module.create_app()) as client:
        response = client.get('/ready')

    assert response.status_code == 503
    payload = response.json()
    assert payload['status'] == 'not_ready'
    assert payload['checks']['langgraph']['ok'] is False


def test_health_reports_external_channels_mode(monkeypatch):
    _patch_config(monkeypatch)
    monkeypatch.setenv('DEER_FLOW_CHANNEL_SERVICE_MODE', 'external')

    with TestClient(gateway_app_module.create_app()) as client:
        response = client.get('/health')

    assert response.status_code == 200
    payload = response.json()
    assert payload['channels']['status'] == 'external'
    assert 'external channel worker' in payload['channels']['reason']
