"""
FastAPI server for on-demand LLM summarization.

This server provides:
- POST /api/summarize: Generate summary for given article content
- GET /api/health: Health check

Usage:
    python -m app.api_server
    # or with uvicorn directly:
    uvicorn app.api_server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm_summarizer import LLMSummarizer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
LOGGER = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="News Summarizer API",
    description="LLM-powered summarization for news articles",
    version="1.0.0",
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SummarizeRequest(BaseModel):
    """Request model for /api/summarize."""
    title: str = Field(default="", description="Article title")
    content: str = Field(..., description="Full article content")
    max_chars: Optional[int] = Field(
        default=None,
        description="Maximum characters to send to LLM (default from settings)"
    )


class SummarizeResponse(BaseModel):
    """Response model for /api/summarize."""
    success: bool
    summary: str
    error: Optional[str] = None


class HealthResponse(BaseModel):
    """Response model for /api/health."""
    status: str
    version: str


# Initialize settings and summarizer on startup
settings = None
summarizer = None


@app.on_event("startup")
async def startup_event():
    """Initialize settings and summarizer on startup."""
    global settings, summarizer
    try:
        settings = get_settings()
        summarizer = LLMSummarizer(settings)
        LOGGER.info("Summarizer initialized successfully")
    except Exception as exc:
        LOGGER.error("Failed to initialize: %s", exc)
        raise


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="ok", version="1.0.0")


@app.post("/api/summarize", response_model=SummarizeResponse, tags=["summarize"])
async def summarize(request: SummarizeRequest):
    """
    Generate summary for article content.

    - **title**: Article title (optional)
    - **content**: Full article content (required, min 50 chars)
    - **max_chars**: Optional max characters to send to LLM
    """
    if not request.content or len(request.content.strip()) < 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content too short (minimum 50 characters required)"
        )

    if summarizer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Summarizer not initialized"
        )

    try:
        # Truncate if too long (LLM context limits)
        max_chars = request.max_chars or getattr(settings, 'llm_summary_max_chars', 8000)
        content = request.content
        if len(content) > max_chars:
            content = content[:max_chars] + "..."
            LOGGER.debug("Content truncated to %d chars", max_chars)

        summary = summarizer._generate_summary(content, request.title)

        return SummarizeResponse(
            success=True,
            summary=summary,
            error=None
        )

    except Exception as exc:
        LOGGER.exception("Failed to generate summary")
        return SummarizeResponse(
            success=False,
            summary="",
            error=str(exc)
        )


def main():
    """Run the FastAPI server using uvicorn."""
    port = int(os.environ.get("API_PORT", 8000))
    host = os.environ.get("API_HOST", "0.0.0.0")
    reload = os.environ.get("API_RELOAD", "false").lower() == "true"

    import uvicorn
    LOGGER.info(f"Starting FastAPI server on http://{host}:{port}")
    uvicorn.run(
        "app.api_server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
