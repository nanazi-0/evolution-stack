#!/usr/bin/env python3
"""极简 OpenAI 兼容 embedding 服务（本地、离线模型）。

给 OpenViking 用：它要求 embedding 走 API（api_base/api_key/model/dimension），
但本机没有可用的 embedding 服务商，所以在本地起一个。

默认模型 BAAI/bge-small-zh-v1.5（中文，512 维）。
首次启动会从 HuggingFace 拉模型；国内建议设 HF_ENDPOINT=https://hf-mirror.com。

  python embeddings_server.py --port 18080

接口：
  POST /v1/embeddings  {"input": ["文本"], "model": "..."} -> {"data":[{"embedding":[...]}]}
  GET  /health
"""
from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
_model = None
_lock = threading.Lock()


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding
                _model = TextEmbedding(model_name=MODEL_NAME)
    return _model


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._send(200, {"status": "ok", "model": MODEL_NAME})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/v1/embeddings"):
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            raw = payload.get("input")
            texts = [raw] if isinstance(raw, str) else list(raw or [])
            if not texts:
                return self._send(400, {"error": "input is empty"})
            vectors = [list(map(float, v)) for v in get_model().embed(texts)]
            data = [{"object": "embedding", "index": i, "embedding": v}
                    for i, v in enumerate(vectors)]
            self._send(200, {"object": "list", "data": data,
                             "model": payload.get("model") or MODEL_NAME,
                             "usage": {"prompt_tokens": sum(len(t) for t in texts),
                                       "total_tokens": sum(len(t) for t in texts)}})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("EMBED_PORT", 18080)))
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    print(f"embedding server on http://{args.host}:{args.port} model={MODEL_NAME}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
