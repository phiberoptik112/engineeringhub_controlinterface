"""Response models for Django API."""

from typing import Any

from pydantic import BaseModel, Field


class ProjectResponse(BaseModel):
    """Project data from Django API."""

    id: int
    title: str
    client_name: str
    status: str
    budget: str | None = None
    description: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class StandardResponse(BaseModel):
    """Standard reference from Django API."""

    type: str
    id: str


class FileResponse(BaseModel):
    """File data from Django API."""

    id: int
    title: str
    file_type: str
    url: str | None = None
    created_at: str | None = None


class ProposalResponse(BaseModel):
    """Proposal data from Django API."""

    id: int
    title: str
    status: str
    amount: str | None = None


class ProjectContextResponse(BaseModel):
    """Rich project context from Django API."""

    project: ProjectResponse
    scope: list[str] = Field(default_factory=list)
    standards: list[StandardResponse] = Field(default_factory=list)
    recent_files: list[FileResponse] = Field(default_factory=list)
    proposals: list[ProposalResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectListResponse(BaseModel):
    """Paginated list of projects."""

    results: list[ProjectResponse]
    count: int


class FileListResponse(BaseModel):
    """Paginated list of files."""

    results: list[FileResponse]
    count: int


class FileUploadResponse(BaseModel):
    """Response from file upload."""

    id: int
    url: str
    message: str


class ClientResponse(BaseModel):
    """Client data from Django API."""

    id: int
    name: str
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
