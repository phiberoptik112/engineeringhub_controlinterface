"""FastAPI mock server for Engineering Hub development.

Run with: uvicorn mock_server.main:app --port 8000 --reload
"""

from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.responses import JSONResponse

from mock_server.data import CLIENTS, PROJECT_CONTEXTS, PROJECTS, VALID_TOKEN

app = FastAPI(
    title="Engineering Hub Mock API",
    description="Mock API server simulating consultingmanager Django backend",
    version="0.1.0",
)


def verify_token(authorization: str = Header(...)) -> str:
    """Verify the API token."""
    if not authorization.startswith("Token "):
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    token = authorization.replace("Token ", "")
    if token != VALID_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    return token


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Engineering Hub Mock API", "version": "0.1.0"}


@app.get("/api/projects/")
async def list_projects(token: str = Depends(verify_token)):
    """List all projects."""
    return {"results": list(PROJECTS.values()), "count": len(PROJECTS)}


@app.get("/api/projects/{project_id}/")
async def get_project(project_id: int, token: str = Depends(verify_token)):
    """Get a single project."""
    if project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="Project not found")
    return PROJECTS[project_id]


@app.get("/api/projects/{project_id}/context/")
async def get_project_context(project_id: int, token: str = Depends(verify_token)):
    """Get rich project context for agents."""
    if project_id not in PROJECT_CONTEXTS:
        raise HTTPException(status_code=404, detail="Project not found")
    return PROJECT_CONTEXTS[project_id]


@app.get("/api/files/")
async def list_files(project: int | None = None, token: str = Depends(verify_token)):
    """List files, optionally filtered by project."""
    if project is None:
        # Return all files from all projects
        all_files = []
        for ctx in PROJECT_CONTEXTS.values():
            all_files.extend(ctx.get("recent_files", []))
        return {"results": all_files, "count": len(all_files)}

    if project not in PROJECT_CONTEXTS:
        raise HTTPException(status_code=404, detail="Project not found")

    files = PROJECT_CONTEXTS[project].get("recent_files", [])
    return {"results": files, "count": len(files)}


@app.get("/api/proposals/")
async def list_proposals(project: int | None = None, token: str = Depends(verify_token)):
    """List proposals, optionally filtered by project."""
    if project is None:
        all_proposals = []
        for ctx in PROJECT_CONTEXTS.values():
            all_proposals.extend(ctx.get("proposals", []))
        return {"results": all_proposals, "count": len(all_proposals)}

    if project not in PROJECT_CONTEXTS:
        raise HTTPException(status_code=404, detail="Project not found")

    proposals = PROJECT_CONTEXTS[project].get("proposals", [])
    return {"results": proposals, "count": len(proposals)}


@app.get("/api/clients/{client_id}/")
async def get_client(client_id: int, token: str = Depends(verify_token)):
    """Get client information."""
    if client_id not in CLIENTS:
        raise HTTPException(status_code=404, detail="Client not found")
    return CLIENTS[client_id]


@app.post("/api/projects/{project_id}/sync/")
async def sync_project(project_id: int, token: str = Depends(verify_token)):
    """Trigger context sync for a project."""
    if project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "synced", "project_id": project_id}


@app.post("/api/projects/{project_id}/files/upload/")
async def upload_file(project_id: int, token: str = Depends(verify_token)):
    """Upload a file to a project (mock - returns success without actually uploading)."""
    if project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="Project not found")

    # Mock successful upload
    return {
        "id": 999,
        "url": f"/files/999/uploaded-file.md",
        "message": "File uploaded successfully",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
