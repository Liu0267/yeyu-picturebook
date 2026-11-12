"""A端生成服务的HTTP客户端。

统一解包A端 Envelope（request_id / status / data / error / warnings）。
所有A端访问都经本客户端，前端不直连A端。
"""

import httpx

from . import config


class AClientError(Exception):
    """A端不可达、超时或返回业务错误。"""

    def __init__(self, code, message, http=502, retryable=False):
        self.code = code
        self.message = message
        self.http = http
        self.retryable = retryable
        super().__init__(message)


class AClient:
    def __init__(self, base_url=None, timeout=8.0):
        self.base_url = (base_url or config.A_BASE_URL).rstrip('/')
        self._client = httpx.Client(timeout=timeout)

    def close(self):
        self._client.close()

    def _unwrap(self, resp):
        try:
            payload = resp.json()
        except ValueError:
            raise AClientError('A_BAD_RESPONSE', 'A端返回非JSON响应。', resp.status_code)
        error = payload.get('error') if isinstance(payload, dict) else None
        if error or (isinstance(payload, dict) and payload.get('status') == 'error'):
            raise AClientError(
                error.get('code', 'A_ERROR') if error else 'A_ERROR',
                error.get('message', 'A端返回错误。') if error else 'A端返回错误。',
                resp.status_code,
                bool(error.get('retryable')) if error else False,
            )
        if resp.status_code >= 400:
            raise AClientError('A_HTTP_ERROR', f'A端返回HTTP {resp.status_code}。', resp.status_code)
        return payload.get('data')

    def get(self, path):
        resp = self._client.get(self.base_url + path)
        return self._unwrap(resp)

    def post(self, path, json=None):
        resp = self._client.post(self.base_url + path, json=json)
        return self._unwrap(resp)

    def patch(self, path, json=None):
        resp = self._client.patch(self.base_url + path, json=json)
        return self._unwrap(resp)

    def stream_asset(self, asset_id, range_header=None):
        """流式转发A端媒体资产（支持HTTP Range）。调用方负责关闭响应。"""
        headers = {'Range': range_header} if range_header else None
        req = self._client.build_request('GET', f'{self.base_url}/v1/assets/{asset_id}', headers=headers)
        return self._client.send(req, stream=True)

    def health(self):
        try:
            return self.get('/v1/health')
        except (AClientError, httpx.HTTPError):
            return None
