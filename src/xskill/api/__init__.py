"""xskill.api — HTTP 层（FastAPI app + SSE 流式端点）"""

from xskill.api.app import create_app

__all__ = ["create_app"]
