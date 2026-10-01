"""Load local configuration without printing or copying credentials."""
from pathlib import Path
import os
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def local_settings():
    path = ROOT / '.env'
    if not path.is_file():
        return {}
    result = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, separator, value = line.removeprefix('export ').partition('=')
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name.strip()):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
        result[name.strip()] = value
    return result


def setting(name, default=''):
    # The user's non-empty .env values take precedence over old process variables.
    return local_settings().get(name, '').strip() or os.environ.get(name, '').strip() or default


def credential(name):
    value = setting(name)
    if not value:
        raise RuntimeError(f'缺少 {name}，请在本地 .env 中填写。')
    return value


def credential_source(name):
    if local_settings().get(name, '').strip():
        return '.env'
    return 'environment' if os.environ.get(name, '').strip() else 'missing'


def answer_config():
    base_url = setting('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').rstrip('/')
    model = setting('DEEPSEEK_MODEL', 'deepseek-v4-flash')
    parts = urlsplit(base_url)
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        raise RuntimeError('DEEPSEEK_BASE_URL 须为不带密钥、参数或片段的 HTTPS 服务地址。')
    if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,100}', model):
        raise RuntimeError('DEEPSEEK_MODEL 格式不正确。')
    return {'base_url': base_url, 'model': model, 'api_key': credential('DEEPSEEK_API_KEY')}


def redact(value):
    """Redact known credentials from any text that may be written to disk."""
    secrets = [setting(name) for name in ('DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY')]
    # Read settings once per response, not once for every coordinate or nested value.
    def visit(item):
        if isinstance(item, str):
            for secret in secrets:
                if secret:
                    item = item.replace(secret, '[REDACTED]')
            return item
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        return item
    return visit(value)
