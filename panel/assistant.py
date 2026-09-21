"""Authenticated web adapter for Hermes's supported REST API (sessions, capabilities, provider settings)."""
import os
import re

import requests
from flask import Blueprint, g, jsonify, request, session

from assistant_settings import create_settings_blueprint

HERMES_CAPABILITIES = {
    'sessions': 'sessions',
    'runs': 'run_submission',
    'stream': 'chat_completions_streaming',
    'run_stop': 'run_stop',
    'run_approval': 'run_approval',
    'session_chat': 'session_chat',
    'model_options': 'model_options',
    'endpoints_sessions': 'sessions',
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


SESSION_ID_RE = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')


def _hermes_connection():
    url = os.environ.get('HERMES_API_URL', 'http://127.0.0.1:8642').rstrip('/')
    key = os.environ.get('HERMES_API_SERVER_KEY', '')
    return url, key, {'Authorization': f'Bearer {key}', 'Accept': 'application/json'}


def _csrf_token():
    if not session.get('assistant_csrf'):
        session['assistant_csrf'] = os.urandom(32).hex()
    return session['assistant_csrf']


def _csrf_ok():
    token = request.headers.get('X-CSRF-Token', '')
    return bool(token) and token == session.get('assistant_csrf')


def _clean_title(value):
    if not isinstance(value, str):
        return None
    title = ' '.join(value.split())
    if not title or len(title) > 120 or any(ord(ch) < 32 for ch in title):
        return None
    return title


def create_assistant_blueprint(login_required, restart_hermes=None):
    blueprint = Blueprint('assistant', __name__, url_prefix='/api/assistant')
    settings_blueprint = create_settings_blueprint(login_required, restart_hermes)
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
            'csrf_token': _csrf_token(),
        })

    @blueprint.get('/sessions')
    @login_required
    def sessions():
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

    @blueprint.get('/sessions/<session_id>')
    @login_required
    def session_detail(session_id):
        if not SESSION_ID_RE.match(session_id):
            return jsonify(error='Invalid session id'), 400
        url, key, headers = _hermes_connection()
        if not key:
            return jsonify(error='Hermes API key is not configured'), 503
        try:
            with requests.get(f'{url}/api/sessions/{session_id}', headers=headers,
                              timeout=(3, 15), allow_redirects=False) as session_response:
                if session_response.status_code == 404:
                    return jsonify(error='Session not found'), 404
                if not session_response.ok:
                    return jsonify(error='Hermes rejected session request', upstream_status=session_response.status_code), 502
                session_data = session_response.json()
            with requests.get(f'{url}/api/sessions/{session_id}/messages', headers=headers,
                              timeout=(3, 15), allow_redirects=False) as messages_response:
                if not messages_response.ok:
                    return jsonify(error='Hermes rejected message request', upstream_status=messages_response.status_code), 502
                messages_data = messages_response.json()
            messages = messages_data.get('data', []) if isinstance(messages_data, dict) else []
            return jsonify(session=session_data.get('session', session_data), messages=messages)
        except (requests.RequestException, ValueError):
            return jsonify(error='Hermes session service is unavailable'), 503

    @blueprint.post('/sessions')
    @login_required
    def create_session():
        if not _csrf_ok():
            return jsonify(error='Invalid CSRF token'), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = {}
        title = None
        if payload.get('title') is not None:
            title = _clean_title(payload.get('title'))
            if title is None:
                return jsonify(error='Title must be 1-120 characters'), 400
        url, key, headers = _hermes_connection()
        if not key:
            return jsonify(error='Hermes API key is not configured'), 503
        # Hermes session titles are unique, so retry with a numeric suffix, then untitled.
        candidates = [title] + [f'{title} ({n})' for n in range(2, 6)] + [None] if title else [None]
        try:
            for candidate in candidates:
                with requests.post(f'{url}/api/sessions', json={'title': candidate} if candidate else {},
                                   headers=headers, timeout=(3, 15), allow_redirects=False) as response:
                    if response.status_code == 404:
                        return jsonify(error='Installed Hermes does not support session management. Update Hermes.'), 503
                    if response.status_code in (200, 201):
                        data = response.json()
                        created = data.get('session', data) if isinstance(data, dict) else {}
                        if not isinstance(created, dict) or not created.get('id'):
                            return jsonify(error='Unexpected Hermes session response'), 502
                        return jsonify(session=created), 201
                    if response.status_code not in (400, 409, 422):
                        return jsonify(error='Hermes rejected session creation', upstream_status=response.status_code), 502
            return jsonify(error='Hermes rejected session creation'), 502
        except (requests.RequestException, ValueError):
            return jsonify(error='Hermes session service is unavailable'), 503

    @blueprint.patch('/sessions/<session_id>')
    @login_required
    def rename_session(session_id):
        if not _csrf_ok():
            return jsonify(error='Invalid CSRF token'), 403
        if not SESSION_ID_RE.match(session_id):
            return jsonify(error='Invalid session id'), 400
        payload = request.get_json(silent=True)
        title = _clean_title(payload.get('title')) if isinstance(payload, dict) else None
        if title is None:
            return jsonify(error='Title must be 1-120 characters'), 400
        url, key, headers = _hermes_connection()
        if not key:
            return jsonify(error='Hermes API key is not configured'), 503
        try:
            with requests.patch(f'{url}/api/sessions/{session_id}', json={'title': title}, headers=headers,
                                timeout=(3, 15), allow_redirects=False) as response:
                if response.status_code == 404:
                    return jsonify(error='Session not found'), 404
                if response.status_code == 409:
                    return jsonify(error='Another chat already uses that title'), 409
                if response.status_code in (400, 422):
                    return jsonify(error='Hermes rejected that title'), 400
                if not response.ok:
                    return jsonify(error='Hermes rejected the rename', upstream_status=response.status_code), 502
                return jsonify(renamed=True, title=title)
        except requests.RequestException:
            return jsonify(error='Hermes session service is unavailable'), 503

    @blueprint.delete('/sessions/<session_id>')
    @login_required
    def delete_session(session_id):
        if not _csrf_ok():
            return jsonify(error='Invalid CSRF token'), 403
        if not SESSION_ID_RE.match(session_id):
            return jsonify(error='Invalid session id'), 400
        url, key, headers = _hermes_connection()
        if not key:
            return jsonify(error='Hermes API key is not configured'), 503
        try:
            with requests.delete(f'{url}/api/sessions/{session_id}', headers=headers,
                                 timeout=(3, 15), allow_redirects=False) as response:
                if response.status_code == 404:
                    return jsonify(error='Session not found'), 404
                if not response.ok:
                    return jsonify(error='Hermes rejected the delete', upstream_status=response.status_code), 502
                return jsonify(deleted=True)
        except requests.RequestException:
            return jsonify(error='Hermes session service is unavailable'), 503

    return blueprint
