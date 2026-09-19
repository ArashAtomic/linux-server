"""Provider settings for the runtime Hermes home; never returns credentials."""
import os
import re
import subprocess
from pathlib import Path
from threading import RLock

import yaml
from flask import Blueprint, jsonify, request, session

_LOCK = RLock()
CUSTOM_KEY = 'HERMES_CUSTOM_API_KEY'
NINEROUTER_URL = 'http://127.0.0.1:20128/v1'
# id: (label, primary credential, base URL)
PROVIDERS = {
    'openrouter': ('OpenRouter', 'OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1'),
    'openai': ('OpenAI', 'OPENAI_API_KEY', 'https://api.openai.com/v1'),
    'anthropic': ('Anthropic', 'ANTHROPIC_API_KEY', 'https://api.anthropic.com/v1'),
    'gemini': ('Gemini', 'GOOGLE_API_KEY', 'https://generativelanguage.googleapis.com/v1beta'),
    'xai': ('xAI', 'XAI_API_KEY', 'https://api.x.ai/v1'),
    'deepseek': ('DeepSeek', 'DEEPSEEK_API_KEY', 'https://api.deepseek.com/v1'),
    'groq': ('Groq', 'GROQ_API_KEY', 'https://api.groq.com/openai/v1'),
    'copilot': ('GitHub Copilot', 'COPILOT_GITHUB_TOKEN', 'https://api.githubcopilot.com'),
    'custom': ('Custom', CUSTOM_KEY, ''),
    'ninerouter': ('9Router', CUSTOM_KEY, NINEROUTER_URL),
}


def _home():
    value = os.environ.get('HERMES_HOME')
    if not value:
        raise ValueError('HERMES_HOME must be configured')
    return Path(value)


def _config(home):
    path = home / 'config.yaml'
    value = yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else {}
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('Invalid Hermes configuration')
    model = value.get('model', {})
    if isinstance(model, str):
        model = {'default': model}
    if not isinstance(model, dict):
        raise ValueError('Invalid Hermes configuration')
    return model


def _env(home):
    path = home / '.env'
    values = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            match = re.match(r'^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$', line)
            if match:
                value = match[2].strip()
                if value[:1] in ('"', "'") and value[-1:] == value[:1]:
                    value = value[1:-1]
                else:
                    value = value.split(' #', 1)[0].rstrip()
                values[match[1]] = value
    return values


def _status(home):
    model = _config(home)
    provider = model.get('provider', 'openrouter')
    if provider not in PROVIDERS:
        provider = 'custom'
    values = _env(home)
    entries = [dict(id=p, name=info[0], base_url=info[2], key_configured=bool(values.get(info[1])))
               for p, info in PROVIDERS.items()]
    return dict(provider=provider, model=model.get('default', ''),
                base_url=model.get('base_url') or PROVIDERS[provider][2],
                key_configured=bool(values.get(PROVIDERS[provider][1]) or model.get('api_key')),
                providers=entries)


def create_settings_blueprint(login_required):
    blueprint = Blueprint('assistant_settings', __name__, url_prefix='/settings')

    @blueprint.before_request
    @login_required
    def authenticate():
        return None

    @blueprint.get('')
    def get_settings():
        try:
            with _LOCK:
                if not session.get('assistant_csrf'):
                    session['assistant_csrf'] = os.urandom(32).hex()
                settings = _status(_home())
                settings['csrf_token'] = session['assistant_csrf']
                return jsonify(settings)
        except (OSError, ValueError, yaml.YAMLError):
            return jsonify(error='Unable to read Hermes settings'), 503

    @blueprint.post('/model')
    def set_model():
        csrf = request.headers.get('X-CSRF-Token', '')
        if not csrf or csrf != session.get('assistant_csrf'):
            return jsonify(error='Invalid CSRF token'), 403

        try:
            payload = request.get_json(force=True, silent=True)
        except Exception:
            return jsonify(error='Invalid JSON body'), 400
        if not isinstance(payload, dict):
            return jsonify(error='Invalid JSON body'), 400

        provider = payload.get('provider')
        if provider not in PROVIDERS:
            return jsonify(error='Invalid provider'), 400

        model = payload.get('model')
        if not isinstance(model, str) or not model.strip():
            return jsonify(error='Invalid model'), 400

        base_url = payload.get('base_url')
        if base_url is not None and not isinstance(base_url, str):
            return jsonify(error='Invalid base_url'), 400
        if provider in ('custom', 'ninerouter'):
            base_url = NINEROUTER_URL if provider == 'ninerouter' else (base_url or '').strip()
            if not base_url or not re.match(r'^https?://[^\s/]+', base_url):
                return jsonify(error='A valid HTTP or HTTPS base URL is required for this provider'), 400
        else:
            base_url = None

        home = _home()
        env = os.environ.copy()
        if provider in ('custom', 'ninerouter'):
            env[CUSTOM_KEY] = ''
        args = ['hermes', 'config', 'set']
        if base_url is not None and base_url.strip():
            args.extend(['model.base_url', base_url.strip()])
        args.extend(['model.provider', provider])
        args.extend(['model.default', model.strip()])

        try:
            subprocess.run(args, env=env, cwd=str(home), check=False, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            pass

        if provider in ('custom', 'ninerouter'):
            key = payload.get('api_key')
            if isinstance(key, str) and key.strip():
                subprocess.run(['hermes', 'config', 'set', CUSTOM_KEY, key.strip()],
                               env=env, cwd=str(home), check=False, timeout=60)
        else:
            env_key = PROVIDERS[provider][1]
            key = payload.get('api_key')
            if isinstance(key, str) and key.strip():
                subprocess.run(['hermes', 'config', 'set', env_key, key.strip()],
                               env=env, cwd=str(home), check=False, timeout=60)

        return jsonify(saved=True, restart_required=True)

    return blueprint