"""Current solution-bound registration; credentials are projected, never minted here."""
import json
import os
from pathlib import Path
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

MAX_RESPONSE = 32768

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None

class BoundRegistration:
    def __init__(self, solution_id, token_url, secret_file, internal_file, destinations, ca_file=None):
        self.solution_id = solution_id
        self.token_url = token_url
        self.secret_file = secret_file
        self.internal_file = internal_file
        self.destinations = frozenset(destinations)
        for value in (*destinations, token_url):
            parsed = urlsplit(value)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('invalid registration endpoint')
            if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1') and not parsed.hostname.endswith('.svc.cluster.local'):
                raise ValueError('registration requires HTTPS or private mesh endpoint')
        tls = ssl.create_default_context(cafile=ca_file)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=tls))

    @staticmethod
    def credential(path):
        with Path(path).open('rb') as source:
            raw = source.read(MAX_RESPONSE + 1)
        value = raw.decode('ascii').strip()
        if len(raw) > MAX_RESPONSE or not value or any(ord(c) < 33 or ord(c) > 126 for c in value):
            raise ValueError('registration credential unavailable')
        return value

    def post(self, url, body):
        if url not in self.destinations or body.get('id') != self.solution_id:
            return 'registration association mismatch'
        try:
            # Each half and each renewal gets a new single-use token. Read files
            # on each attempt so an atomic credential projection can rotate.
            headers = {'Content-Type': 'application/json',
                       'X-Codefly-Internal-Token': self.credential(self.internal_file),
                       'X-Codefly-Solution-Secret': self.credential(self.secret_file)}
            request = urllib.request.Request(self.token_url, data=json.dumps({'id': self.solution_id}).encode(), headers=headers, method='POST')
            with self.opener.open(request, timeout=5) as response:
                if response.status != 200:
                    return 'registration token unavailable'
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                return 'registration token unavailable'
            value = json.loads(raw)
            token = value.get('token')
            if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                return 'registration token unavailable'
            request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'X-Codefly-Solution-Registration': token}, method='POST')
            with self.opener.open(request, timeout=5) as response:
                return None if 200 <= response.status < 300 else 'registration refused'
        except Exception:
            # Do not log credentials, response bodies or endpoint query strings.
            return 'registration unavailable'

    @classmethod
    def configured(cls, solution_id, gateway, host_register, gateway_register):
        secret = os.environ.get('SOLUTION_REGISTRATION_SECRET_FILE')
        if not secret:
            return None
        internal = os.environ.get('SOLUTION_REGISTRATION_INTERNAL_TOKEN_FILE')
        if not internal:
            raise ValueError('solution registration internal token file required')
        return cls(solution_id, gateway.rstrip('/')+'/solutions/_registration-token', secret, internal,
                   [host_register, gateway_register], os.environ.get('SOLUTION_REGISTRATION_CA_FILE'))
