"""Quick local smoke test for the access-control layer.

Runs a tiny FastAPI app with ONLY mount_auth + a dummy protected route — no
Gradio, no LLM, no Pinecone — so you can exercise /login, /logout, /health
end-to-end against a real Redis (docker-compose redis) in seconds.

Usage:
    # terminal 1: start a redis
    docker run -p 6379:6379 --rm redis:7-alpine
    # terminal 2: run this server
    APP_API_KEY=local-demo-key-123 REDIS_URL=redis://localhost:6379/0 \
        venv/bin/python scripts/dev_auth_smoke.py
    # terminal 3: try the curls below
"""
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Force AUTH on for the smoke test unless already set.
os.environ.setdefault("AUTH_ENABLED", "true")
os.environ.setdefault("APP_API_KEY", "local-demo-key-123")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from src.access_control import mount_auth  # noqa: E402

app = FastAPI()
mount_auth(app)


@app.get("/")
def _root():
    return {"hello": "authenticated world"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print("=" * 60)
    print("Access-control smoke server on http://localhost:%d" % port)
    print("APP_API_KEY = %s" % os.environ["APP_API_KEY"])
    print("-" * 60)
    print("Try:")
    print('  curl -i http://localhost:%d/health' % port)
    print('  curl -i http://localhost:%d/' % port)
    print('  curl -i -H "X-API-Key: %s" http://localhost:%d/' % (os.environ["APP_API_KEY"], port))
    print('  # browser: open http://localhost:%d/  -> redirected to /login' % port)
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=port)
