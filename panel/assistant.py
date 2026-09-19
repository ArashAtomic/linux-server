"""Authenticated web adapter for Hermes's supported REST API and commands."""
import os

import requests
from flask import Blueprint, g, jsonify, request, session

from assistant_settings import create_settings_blueprint

HERMES_CAPABILITIES = {
    'sessions': 'session_management',
    'runs': 'run_submission',
    'stream': 'run_events_sse',
    'run_stop': 'run_stop',
    'run_approval': 'run_approval',
    'session_chat': 'session_chat',
    'model_options': 'model_options',
    'endpoints_sessions': 'endpoints_sessions',
}


def _has_capabilities(session):
    return session.get('assistant_capabilities', {})


def _check_capabilities(session):
    missing = []
    for feature, capability in HERMES_CAPABILITIES.items():
        if not session.get(f'assistant_{feature}'):
            missing.append(capability)
    return missing


def _safe_capabilities():
    return {feature: False for feature in HERMES_CAPABILITIES}


def create_assistant_blueprint(login_required):
    blueprint = Blueprint('assistant', __name__, url_prefix='/api/assistant')
    settings_blueprint = create_settings_blueprint(login_required)
    blueprint.register_blueprint(settings_blueprint)

    @blueprint.before_request
    @login_required
    def authenticate():
        return None

    @blueprint.get('/capabilities')
    @login_required
    def capabilities():
        hermes_capabilities = _safe_capabilities()
        try:
            url = os.environ.get('HERMES_API_URL', 'http://127.0.0.1:8642').rstrip('/')
            key = os.environ.get('HERMES_API_SERVER_KEY', '')
            if key:
                with requests.get(
                    f'{url}/v1/capabilities',
                    headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'},
                    timeout=(3, 15), allow_redirects=False,
                ) as response:
                    if response.ok:
                        data = response.json()
                        endpoints = data.get('endpoints', {}) if isinstance(data, dict) else {}
                        features = data.get('features', {}) if isinstance(data, dict) else {}
                        for feature, capability in HERMES_CAPABILITIES.items():
                            value = endpoints.get(capability, False) or features.get(feature, False)
                            if isinstance(value, str):
                                value = value.lower() not in {'false', '0', 'no'}
                            hermes_capabilities[feature] = bool(value)
        except Exception:
            pass
        g.assistant_capabilities = hermes_capabilities
        return jsonify({
            'capabilities': hermes_capabilities,
            'csrf_token': session.get('assistant_csrf', ''),
        })

    @blueprint.get('/sessions')
    @login_required
    def sessions():
        capabilities = getattr(g, 'assistant_capabilities', None)
        if not capabilities:
            # Fallback: read from session if capabilities endpoint wasn't hit first
            capabilities = session.get('assistant_capabilities', {})
        if not capabilities:
            return jsonify(error='Hermes capabilities unavailable', missing=list(HERMES_CAPABILITIES.keys())), 503
        try:
            limit = int(request.args.get('limit', 50))
            offset = int(request.args.get('offset', 0))
        except ValueError:
            return jsonify(error='Invalid pagination'), 400
        if not 1 <= limit <= 100 or offset < 0:
            return jsonify(error='Invalid pagination'), 400
        key = os.environ.get('HERMES_API_SERVER_KEY', '')
        if not key:
            return jsonify(error='Hermes API key is not configured'), 503
        url = os.environ.get('HERMES_API_URL', 'http://127.0.0.1:8642').rstrip('/')
        try:
            with requests.get(
                f'{url}/api/sessions',
                params={'limit': limit, 'offset': offset},
                headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'},
                timeout=(3, 15), allow_redirects=False,
            ) as response:
                if response.status_code == 404:
                    return jsonify(error='Installed Hermes does not support session management. Update Hermes.'), 503
                if not response.ok:
                    return jsonify(error='Hermes rejected session request', upstream_status=response.status_code), 502
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get('data'), list):
                    return jsonify(error='Unexpected Hermes sessions response'), 502
                return jsonify(data)
        except (requests.RequestException, ValueError):
            return jsonify(error='Hermes session service is unavailable'), 503

    @blueprint.post('/commands')
    @login_required
    def commands():
        payload = request.get_json(silent=True) or {}
        command = payload.get('command', '')
        if not isinstance(command, str) or not command.strip():
            return jsonify(error='Command required'), 400
        if not command.startswith('/'):
            return jsonify(error='Only slash commands are supported here'), 400
        key = os.environ.get('HERMES_API_SERVER_KEY', '')
        if not key:
            return jsonify(error='Hermes API key is not configured'), 503
        url = os.environ.get('HERMES_API_URL', 'http://127.0.0.1:8642').rstrip('/')
        command_name = command.split(None, 1)[0].lower()
        if command_name in {'/new', '/reset'}:
            return jsonify(response='New Hermes session ready', command=command_name)
        if command_name == '/help':
            return jsonify(response='Available commands: /new, /reset, /sessions, /models, /status')
        upstream_path = {
            '/sessions': '/api/sessions',
            '/models': '/v1/models',
            '/status': '/health',
        }.get(command_name)
        if not upstream_path:
            return jsonify(error=f'Unsupported command: {command_name}'), 400
        try:
            with requests.get(
                f'{url}{upstream_path}',
                headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'},
                timeout=(3, 15), allow_redirects=False,
            ) as response:
                if not response.ok:
                    return jsonify(error='Hermes rejected the command', upstream_status=response.status_code), 502
                try:
                    data = response.json() if response.content else {}
                except ValueError:
                    return jsonify(error='Hermes returned an unexpected command response'), 502
                if command_name == '/sessions':
                    sessions = data.get('data', []) if isinstance(data, dict) else []
                    labels = [item.get('title') or item.get('id', 'Untitled') for item in sessions]
                    return jsonify(response='\n'.join(labels) or 'No sessions found', sessions=sessions)
                if command_name == '/models':
                    models = data.get('data', []) if isinstance(data, dict) else []
                    return jsonify(response='\n'.join(item.get('id', '') for item in models), models=models)
                return jsonify(data)
        except (requests.RequestException, ValueError):
            return jsonify(error='Hermes command service is unavailable'), 503

    return blueprint
