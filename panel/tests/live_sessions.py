"""Opt-in real Hermes adapter smoke test; all state lives in a temporary home.

Run with a Python interpreter containing Hermes and the panel dependencies.
No model/provider is called. No live profile or credentials are loaded.
"""
import asyncio
import os
from pathlib import Path
import secrets
import sys
import tempfile


async def check():
    with tempfile.TemporaryDirectory(prefix='panel-hermes-test-') as directory:
        os.environ['HERMES_HOME'] = directory
        os.environ['HERMES_PROFILE'] = 'default'
        os.environ['API_SERVER_KEY'] = secrets.token_hex(32)
        os.environ['HERMES_API_SERVER_KEY'] = os.environ['API_SERVER_KEY']
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter
        import aiohttp

        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
            'host': '127.0.0.1', 'port': 0, 'key': os.environ['API_SERVER_KEY'],
        }))
        assert await adapter.connect(), 'Hermes API adapter startup failed'
        try:
            port = adapter._site._server.sockets[0].getsockname()[1]
            url = f'http://127.0.0.1:{port}'
            os.environ['HERMES_API_URL'] = url
            headers = {'Authorization': f"Bearer {os.environ['API_SERVER_KEY']}"}
            async with aiohttp.ClientSession() as client:
                async with client.post(url + '/api/sessions', headers=headers,
                                       json={'title': 'Panel integration smoke'}) as response:
                    assert response.status == 201, await response.text()
                    created = await response.json()
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            import app

            def panel_request():
                client = app.app.test_client()
                assert client.get('/api/assistant/sessions').status_code == 401
                with client.session_transaction() as login:
                    login['logged_in'] = True
                response = client.get('/api/assistant/sessions')
                assert response.status_code == 200, response.get_data(as_text=True)
                assert any(item['id'] == created['session']['id'] for item in response.json['data'])
                return len(response.json['data'])

            count = await asyncio.to_thread(panel_request)
            print(f'PASS: real Hermes session create; authenticated panel list ({count} session); unauthenticated 401')
        finally:
            await adapter.disconnect()


if __name__ == '__main__':
    asyncio.run(check())
