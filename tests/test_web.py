from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import socket
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from nextbus.config import Config
from nextbus.data import Dataset
from nextbus.server import make_server


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Config(population=120, start=600, end=660)
        cls.data = Dataset(cls.config)
        cls.server = make_server(cls.data, cls.config, 0)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = Request(self.base + path, data=body, headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=10) as response:
            return response.headers, response.read()

    def test_incomplete_browser_connection_does_not_block_assets(self):
        # Browsers can pre-open a connection or send a partial request. The
        # remaining stylesheet/script/data requests must still be served.
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2) as idle:
            idle.sendall(b'GET / HTTP/1.1\r\n')
            paths = ['/', '/style.css?v=1', '/app.js?v=1', '/api/setup']
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(self.request, paths))
            self.assertIn('text/html', responses[0][0]['Content-Type'])
            self.assertIn('text/css', responses[1][0]['Content-Type'])
            self.assertIn('text/javascript', responses[2][0]['Content-Type'])
            setup = json.loads(responses[3][1])
            self.assertTrue(setup['days'])
            self.assertEqual(len(setup['routes']), 7)
            self.assertTrue(setup['facilities'])
            self.assertTrue(all(headers['Cache-Control'] == 'no-store' for headers, _ in responses))

    def test_run_api_serializes_steps_and_rejects_invalid_step(self):
        _, body = self.request('/api/reset', asdict(self.config))
        initial = json.loads(body)
        self.assertEqual(initial['time'], 600)
        self.assertTrue(initial['map']['destinations'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.request('/api/step', {'minutes': 5}), range(2)))
        _, body = self.request('/api/snapshot')
        self.assertEqual(json.loads(body)['time'], 610)
        with self.assertRaises(HTTPError) as failed:
            self.request('/api/step', {'minutes': -1})
        self.assertEqual(failed.exception.code, 400)
        _, body = self.request('/api/snapshot')
        self.assertEqual(json.loads(body)['time'], 610)

    def test_unconfigured_llm_is_not_presented_as_working(self):
        with self.assertRaises(HTTPError) as failed:
            self.request('/api/reset', dict(asdict(self.config), arm='LLM-A'))
        self.assertEqual(failed.exception.code, 400)
        self.assertIn('Configure llm_endpoint', failed.exception.read().decode())

    def test_unified_home_and_embedded_map(self):
        _, home = self.request('/')
        _, legacy = self.request('/experiments')
        self.assertEqual(home, legacy)
        self.assertIn(b'Advanced simulation settings', home)
        self.assertIn(b'id="results"', home)
        self.assertIn(b'src="/map-view?viewer=1"', home)
        self.assertNotIn(b'>Simulator</a>', home)
        _, viewer = self.request('/map-view?viewer=1')
        self.assertIn(b'<canvas id="map"', viewer)
        self.assertNotIn(b'<iframe', viewer)


if __name__ == '__main__':
    unittest.main()
