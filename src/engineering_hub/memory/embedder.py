"""
OllamaEmbedder -- generate text embeddings from a locally running Ollama instance.

No cloud. No API keys. Requires Ollama to be running and the chosen model pulled:
    ollama pull nomic-embed-text        # 274MB, 768-dim, fast
    ollama pull mxbai-embed-large       # 670MB, 1024-dim, higher quality
"""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "nomic-embed-text"

KNOWN_DIMENSIONS = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
}


class OllamaEmbedder:
    """Generate text embeddings via a local Ollama instance."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = "http://localhost:11434",
        timeout: int = 30,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._detected_dims: Optional[int] = None

    def embed(self, text: str) -> list[float]:
        """
        Embed a single string. Returns a list of float32 values.

        Raises
        ------
        RuntimeError  if Ollama is unreachable or returns an error
        ValueError    if text is empty
        """
        text = text.strip()
        if not text:
            raise ValueError("Cannot embed empty string")

        # ~6000 chars is a safe proxy for model context limits without tokenizing
        if len(text) > 6000:
            text = text[:6000]
            logger.debug("Text truncated to 6000 chars for embedding")

        try:
            resp = requests.post(
                f"{self.host}/api/embeddings",
                json={"model": self.model, "prompt": text},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            embedding = resp.json()["embedding"]
            self._detected_dims = len(embedding)
            return embedding

        except requests.ConnectionError:
            raise RuntimeError(
                f"Cannot reach Ollama at {self.host}. "
                "Start it with: ollama serve"
            )
        except requests.HTTPError:
            if resp.status_code == 404:
                raise RuntimeError(
                    f"Ollama model '{self.model}' not found. "
                    f"Pull it first: ollama pull {self.model}"
                )
            raise RuntimeError(f"Ollama error {resp.status_code}: {resp.text[:200]}")
        except KeyError:
            raise RuntimeError(
                f"Unexpected Ollama response -- missing 'embedding' key. "
                f"Response: {resp.text[:200]}"
            )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of texts. Ollama doesn't batch natively so this loops.
        Skips empty strings silently.
        """
        results = []
        for text in texts:
            if text.strip():
                results.append(self.embed(text))
        return results

    def is_available(self) -> bool:
        """
        Return True if Ollama is reachable and this model is available locally.
        Safe to call at startup -- won't raise.
        """
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=5)
            models = [m["name"] for m in resp.json().get("models", [])]
            return any(self.model in m for m in models)
        except Exception:
            return False

    @property
    def dimensions(self) -> int:
        """
        Return the expected embedding dimension for this model.
        Uses detected value from first embed() call, or falls back to KNOWN_DIMENSIONS.
        """
        if self._detected_dims:
            return self._detected_dims
        return KNOWN_DIMENSIONS.get(self.model, 768)
