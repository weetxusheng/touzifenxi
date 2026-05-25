from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from feedcore.article_fetcher import ArticleClient
from feedcore.pipeline import RequestsRssClient
from feedcore.remote_fetcher import (
    FetchOnlyClients,
    FetchRunManager,
    FetchRunRequest,
    RemoteFetchConfig,
    run_fetch_only,
)


def default_fetch_runner(config: RemoteFetchConfig):
    return run_fetch_only(
        config=config,
        clients=FetchOnlyClients(
            rss=RequestsRssClient(),
            article=ArticleClient(
                browser_enabled=config.browser_enabled,
                document_dir=None,
                save_html=False,
                save_pdf=False,
                browser_timeout=config.browser_timeout,
            ),
            translator=None,
        ),
    )


def create_app(*, output_dir: Path, token: str | None = None, manager: FetchRunManager | None = None):
    try:
        from fastapi import Depends, Header, HTTPException
        from fastapi.responses import FileResponse
        from fastapi import FastAPI
    except ImportError as exc:
        raise RuntimeError("Remote fetch server requires: python -m pip install -e .[remote]") from exc

    app = FastAPI(title="FeedCore Remote Fetcher", version="0.1.0")
    run_manager = manager or FetchRunManager(output_dir=output_dir, runner=default_fetch_runner)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not token:
            return
        expected = f"Bearer {token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid bearer token")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/fetch-runs", dependencies=[Depends(require_token)])
    def create_fetch_run(payload: dict[str, Any]) -> dict[str, object]:
        try:
            request = FetchRunRequest.from_payload(payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return run_manager.create_run(request).to_dict()

    @app.get("/api/fetch-runs/{run_id}", dependencies=[Depends(require_token)])
    def get_fetch_run(run_id: str) -> dict[str, object]:
        status = run_manager.get_status(run_id)
        if status is None:
            raise HTTPException(status_code=404, detail="fetch run not found")
        return status.to_dict()

    @app.get("/api/fetch-runs/{run_id}/artifact", dependencies=[Depends(require_token)])
    def download_artifact(run_id: str):
        artifact_path = run_manager.get_artifact_path(run_id)
        if artifact_path is None or not artifact_path.exists():
            raise HTTPException(status_code=404, detail="artifact is not ready")
        return FileResponse(
            artifact_path,
            media_type="application/zip",
            filename=artifact_path.name,
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the FeedCore remote fetcher HTTP service.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--output-dir", default="remote_output")
    parser.add_argument(
        "--token",
        default=os.environ.get("FEEDCORE_FETCHER_TOKEN"),
        help="Bearer token for remote fetch APIs. Defaults to FEEDCORE_FETCHER_TOKEN.",
    )
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Remote fetch server requires: python -m pip install -e .[remote]") from exc

    app = create_app(output_dir=Path(args.output_dir), token=args.token)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
