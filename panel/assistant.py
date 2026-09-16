"""Authenticated web adapter for Hermes's supported REST API."""
import os

import requests
from flask import Blueprint, jsonify, request


def create_assistant_blueprint(login_required):
    blueprint = Blueprint('assistant', __name__, url_prefix='/api/assistant')

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

    return blueprint
