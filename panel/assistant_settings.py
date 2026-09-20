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


def _atomic_write(path, text):
    home = path.parent
    home.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text, encoding='utf-8')
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _write_model_config(home, provider, model, base_url):
    path = home / 'config.yaml'
    value = yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(value, dict):
        value = {}
    section = value.get('model')
    if isinstance(section, str):
        section = {'default': section}
    if not isinstance(section, dict):
        section = {}
    section['provider'] = provider
    section['default'] = model
    if base_url:
        section['base_url'] = base_url
    else:
        section.pop('base_url', None)
    value['model'] = section
    _atomic_write(path, yaml.safe_dump(value, sort_keys=False))


def _upsert_env(home, name, secret):
    path = home / '.env'
    lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    pattern = re.compile(r'^\s*(?:export\s+)?' + re.escape(name) + r'\s*=')
    entry = f'{name}={secret}'
    if any(pattern.match(line) for line in lines):
        lines = [entry if pattern.match(line) else line for line in lines]
    else:
        lines.append(entry)
    _atomic_write(path, '\n'.join(lines) + '\n')


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


def create_settings_blueprint(login_required, restart_hermes=None):
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
        model = model.strip()
        base_url = base_url.strip() if base_url else None

        def run_cli(*args):
            try:
                subprocess.run(['hermes', 'config', 'set', *args], env=env, cwd=str(home),
                               check=False, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                pass

        if base_url:
            run_cli('model.base_url', base_url)
        run_cli('model.provider', provider)
        run_cli('model.default', model)

        api_key = payload.get('api_key')
        api_key = api_key.strip() if isinstance(api_key, str) else ''
        if api_key and ('\n' in api_key or '\r' in api_key or len(api_key) > 5000):
            return jsonify(error='Provider key is invalid'), 400
        env_name = CUSTOM_KEY if provider in ('custom', 'ninerouter') else PROVIDERS[provider][1]
        if api_key:
            run_cli(env_name, api_key)

        # `hermes config set` can fail silently or leave a stale base_url behind, so verify and
        # fall back to writing the files directly. Otherwise the panel would report a model
        # that Hermes never actually saved.
        try:
            current = _config(home)
            if (current.get('provider') != provider or current.get('default') != model
                    or (current.get('base_url') or None) != (base_url or None)):
                _write_model_config(home, provider, model, base_url)
            if api_key and _env(home).get(env_name) != api_key:
                _upsert_env(home, env_name, api_key)
        except (OSError, ValueError, yaml.YAMLError):
            return jsonify(error='Unable to write Hermes settings'), 503

        if restart_hermes is None:
            return jsonify(saved=True, restarted=False, model=model, provider=provider)
        try:
            restart_hermes()
        except Exception:
            return jsonify(saved=True, restarted=False, model=model, provider=provider,
                           error='Settings saved, but Hermes did not restart cleanly. Check Logs > Hermes Gateway.'), 503
        return jsonify(saved=True, restarted=True, model=model, provider=provider)

    return blueprint