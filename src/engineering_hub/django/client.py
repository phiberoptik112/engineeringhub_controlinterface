"""Django REST API client for Engineering Hub."""

import logging
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from engineering_hub.core.exceptions import DjangoAPIError
from engineering_hub.django.cache import TTLCache
from engineering_hub.django.models import (
    ClientResponse,
    FileListResponse,
    FileUploadResponse,
    ProjectContextResponse,
    ProjectListResponse,
    ProjectResponse,
)

logger = logging.getLogger(__name__)


class DjangoClient:
    """Client for communicating with Django consultingmanager backend."""

    def __init__(
        self,
        api_url: str,
        api_token: str,
        cache_ttl: int = 300,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        """Initialize the Django API client.

        Args:
            api_url: Base URL for the API (e.g., "http://localhost:8000/api")
            api_token: Authentication token
            cache_ttl: Cache TTL in seconds (default 5 minutes)
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries for failed requests
        """
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout
        self._cache = TTLCache(ttl_seconds=cache_ttl)

        # Set up session with retry logic
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Token {api_token}",
                "Content-Type": "application/json",
            }
        )

        # Configure retry strategy
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
    ) -> dict:
        """Make an API request with error handling.

        Args:
            method: HTTP method
            endpoint: API endpoint (relative to api_url)
            params: Query parameters
            data: Request body data
            files: Files to upload

        Returns:
            Response JSON data

        Raises:
            DjangoAPIError: On API errors
        """
        url = f"{self.api_url}{endpoint}"

        try:
            # Remove Content-Type for file uploads
            headers = None
            if files:
                headers = {"Authorization": f"Token {self.api_token}"}

            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=data if not files else None,
                files=files,
                headers=headers,
                timeout=self.timeout,
            )

            if response.status_code == 429:
                # Rate limited - wait and retry
                retry_after = int(response.headers.get("Retry-After", 60))
                logger.warning(f"Rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                return self._request(method, endpoint, params, data, files)

            if response.status_code >= 400:
                error_detail = "Unknown error"
                try:
                    error_detail = response.json().get("detail", str(response.text))
                except Exception:
                    error_detail = response.text

                raise DjangoAPIError(
                    f"API error: {error_detail}",
                    status_code=response.status_code,
                )

            return response.json()

        except requests.RequestException as e:
            raise DjangoAPIError(f"Request failed: {e}")

    def _get_cached(self, cache_key: str, endpoint: str, params: dict | None = None) -> dict:
        """Get data with caching."""
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for {cache_key}")
            return cached

        data = self._request("GET", endpoint, params=params)
        self._cache.set(cache_key, data)
        return data

    # Project endpoints

    def list_projects(self) -> ProjectListResponse:
        """List all projects."""
        data = self._get_cached("projects:list", "/projects/")
        return ProjectListResponse(**data)

    def get_project(self, project_id: int) -> ProjectResponse:
        """Get a single project by ID."""
        data = self._get_cached(f"project:{project_id}", f"/projects/{project_id}/")
        return ProjectResponse(**data)

    def get_project_context(self, project_id: int) -> ProjectContextResponse:
        """Get rich project context for agents."""
        data = self._get_cached(
            f"project_context:{project_id}",
            f"/projects/{project_id}/context/",
        )
        return ProjectContextResponse(**data)

    def sync_project(self, project_id: int) -> dict:
        """Trigger a context sync for a project."""
        # Invalidate cache
        self._cache.invalidate(f"project:{project_id}")
        self._cache.invalidate(f"project_context:{project_id}")

        return self._request("POST", f"/projects/{project_id}/sync/")

    # File endpoints

    def list_files(self, project_id: int | None = None) -> FileListResponse:
        """List files, optionally filtered by project."""
        params = {"project": project_id} if project_id else None
        cache_key = f"files:project:{project_id}" if project_id else "files:all"

        data = self._get_cached(cache_key, "/files/", params=params)
        return FileListResponse(**data)

    def upload_file(
        self,
        project_id: int,
        file_path: Path,
        destination: str | None = None,
        description: str | None = None,
    ) -> FileUploadResponse:
        """Upload a file to a project.

        Args:
            project_id: Project to upload to
            file_path: Local path to file
            destination: Optional destination folder in project
            description: Optional file description

        Returns:
            FileUploadResponse with file ID and URL
        """
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f)}
            data = {}
            if destination:
                data["destination"] = destination
            if description:
                data["description"] = description

            response = self._request(
                "POST",
                f"/projects/{project_id}/files/upload/",
                files=files,
                data=data if data else None,
            )

        # Invalidate files cache
        self._cache.invalidate(f"files:project:{project_id}")
        self._cache.invalidate("files:all")

        return FileUploadResponse(**response)

    # Client endpoints

    def get_client(self, client_id: int) -> ClientResponse:
        """Get client information."""
        data = self._get_cached(f"client:{client_id}", f"/clients/{client_id}/")
        return ClientResponse(**data)

    # Cache management

    def invalidate_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
        logger.info("Django API cache cleared")
