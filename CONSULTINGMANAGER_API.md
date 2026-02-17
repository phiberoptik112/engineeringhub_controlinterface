# Consultingmanager API Reference

This document defines the REST API endpoints required in the Django consultingmanager backend to support the Engineering Hub Control Interface.

## Authentication

All endpoints require token-based authentication using Django REST Framework's TokenAuthentication.

**Header format:**
```
Authorization: Token <your-token>
```

**Setup in Django:**
1. Install Django REST Framework: `pip install djangorestframework`
2. Add to `INSTALLED_APPS`: `'rest_framework'`, `'rest_framework.authtoken'`
3. Run migrations: `python manage.py migrate`
4. Generate tokens for users: `python manage.py drf_create_token <username>`

## Endpoints

### Projects

#### List Projects
```
GET /api/projects/
```

**Response (200 OK):**
```json
{
  "results": [
    {
      "id": 1,
      "title": "Office Building Acoustic Assessment",
      "client_name": "Acme Construction",
      "status": "in_progress",
      "budget": "45000.00",
      "description": "Comprehensive acoustic assessment...",
      "start_date": "2026-01-15",
      "end_date": "2026-03-30"
    }
  ],
  "count": 1
}
```

#### Get Project Detail
```
GET /api/projects/{id}/
```

**Response (200 OK):**
```json
{
  "id": 1,
  "title": "Office Building Acoustic Assessment",
  "client_name": "Acme Construction",
  "status": "in_progress",
  "budget": "45000.00",
  "description": "Comprehensive acoustic assessment...",
  "start_date": "2026-01-15",
  "end_date": "2026-03-30"
}
```

**Response (404 Not Found):**
```json
{
  "detail": "Project not found"
}
```

#### Get Project Context (Rich Data for Agents)
```
GET /api/projects/{id}/context/
```

This is the primary endpoint for the Engineering Hub. It returns comprehensive project context including scope, standards, files, and metadata formatted for AI agent consumption.

**Response (200 OK):**
```json
{
  "project": {
    "id": 1,
    "title": "Office Building Acoustic Assessment",
    "client_name": "Acme Construction",
    "status": "in_progress",
    "budget": "45000.00",
    "description": "Comprehensive acoustic assessment...",
    "start_date": "2026-01-15",
    "end_date": "2026-03-30"
  },
  "scope": [
    "ASTC testing per ASTM E336-17a for 12 party walls",
    "AIIC testing per ASTM E1007-16 for 8 floor assemblies",
    "Background noise measurements per ANSI S12.2",
    "Acoustic modeling and recommendations report"
  ],
  "standards": [
    {"type": "ASTM", "id": "ASTM E336-17a"},
    {"type": "ASTM", "id": "ASTM E1007-16"},
    {"type": "ANSI", "id": "ANSI S12.2"}
  ],
  "recent_files": [
    {
      "id": 101,
      "title": "Site Survey Report",
      "file_type": "pdf",
      "url": "/files/101/site-survey.pdf",
      "created_at": "2026-01-20"
    }
  ],
  "proposals": [
    {
      "id": 1,
      "title": "Acoustic Assessment Proposal",
      "status": "accepted",
      "amount": "45000.00"
    }
  ],
  "metadata": {
    "client_technical_level": "moderate",
    "priority": "high",
    "notes": "Client prefers detailed technical reports with visual aids."
  }
}
```

**Implementation Notes:**
- `scope`: Extract from accepted proposal documents. Parse deliverables list.
- `standards`: Extract standard references (ASTM, ISO, ANSI, etc.) from scope and proposals.
- `recent_files`: Last 10 files associated with the project.
- `metadata`: Additional context useful for agents. Include:
  - `client_technical_level`: "low", "moderate", "high" - helps agents adjust language
  - `priority`: "low", "medium", "high"
  - `notes`: Any special instructions or client preferences

#### Trigger Project Sync
```
POST /api/projects/{id}/sync/
```

Triggers a refresh of cached project context. Used when project data changes.

**Response (200 OK):**
```json
{
  "status": "synced",
  "project_id": 1
}
```

### Files

#### List Project Files
```
GET /api/files/?project={project_id}
```

**Query Parameters:**
- `project` (optional): Filter by project ID

**Response (200 OK):**
```json
{
  "results": [
    {
      "id": 101,
      "title": "Site Survey Report",
      "file_type": "pdf",
      "url": "/files/101/site-survey.pdf",
      "created_at": "2026-01-20"
    }
  ],
  "count": 1
}
```

#### Upload File to Project
```
POST /api/projects/{id}/files/upload/
```

**Request:**
- Content-Type: `multipart/form-data`
- Body:
  - `file`: The file to upload
  - `destination` (optional): Subfolder path (e.g., "Documents/")
  - `description` (optional): File description

**Response (201 Created):**
```json
{
  "id": 999,
  "url": "/files/999/uploaded-file.md",
  "message": "File uploaded successfully"
}
```

### Proposals

#### List Project Proposals
```
GET /api/proposals/?project={project_id}
```

**Query Parameters:**
- `project` (optional): Filter by project ID

**Response (200 OK):**
```json
{
  "results": [
    {
      "id": 1,
      "title": "Acoustic Assessment Proposal",
      "status": "accepted",
      "amount": "45000.00"
    }
  ],
  "count": 1
}
```

### Clients

#### Get Client Detail
```
GET /api/clients/{id}/
```

**Response (200 OK):**
```json
{
  "id": 1,
  "name": "Acme Construction",
  "contact_name": "John Smith",
  "email": "jsmith@acme-construction.com",
  "phone": "555-0100",
  "address": "123 Builder Lane, Construction City, CC 12345"
}
```

## Django Implementation Example

### Serializers

```python
# consultingmanager/api/serializers.py
from rest_framework import serializers
from projects.models import Project, File, Proposal
from clients.models import Client


class ProjectSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='client.name', read_only=True)

    class Meta:
        model = Project
        fields = [
            'id', 'title', 'client_name', 'status', 'budget',
            'description', 'start_date', 'end_date'
        ]


class FileSerializer(serializers.ModelSerializer):
    class Meta:
        model = File
        fields = ['id', 'title', 'file_type', 'url', 'created_at']


class ProposalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proposal
        fields = ['id', 'title', 'status', 'amount']


class StandardSerializer(serializers.Serializer):
    type = serializers.CharField()
    id = serializers.CharField()


class ProjectContextSerializer(serializers.Serializer):
    project = ProjectSerializer()
    scope = serializers.ListField(child=serializers.CharField())
    standards = StandardSerializer(many=True)
    recent_files = FileSerializer(many=True)
    proposals = ProposalSerializer(many=True)
    metadata = serializers.DictField()
```

### Views

```python
# consultingmanager/api/views.py
from rest_framework import viewsets, views
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser

from projects.models import Project, File
from .serializers import (
    ProjectSerializer,
    ProjectContextSerializer,
    FileSerializer,
)


class ProjectViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['get'])
    def context(self, request, pk=None):
        """Return rich project context for agents."""
        project = self.get_object()

        # Build context
        context_data = {
            'project': ProjectSerializer(project).data,
            'scope': self._extract_scope(project),
            'standards': self._extract_standards(project),
            'recent_files': FileSerializer(
                project.files.order_by('-created_at')[:10],
                many=True
            ).data,
            'proposals': ProposalSerializer(
                project.proposals.all(),
                many=True
            ).data,
            'metadata': self._build_metadata(project),
        }

        return Response(context_data)

    @action(detail=True, methods=['post'])
    def sync(self, request, pk=None):
        """Trigger context sync."""
        project = self.get_object()
        # Implement any cache invalidation here
        return Response({'status': 'synced', 'project_id': project.id})

    def _extract_scope(self, project):
        """Extract scope items from accepted proposals."""
        # Implementation depends on your data model
        scope = []
        for proposal in project.proposals.filter(status='accepted'):
            # Parse proposal content for scope items
            pass
        return scope

    def _extract_standards(self, project):
        """Extract standards references from project."""
        # Implementation depends on your data model
        return []

    def _build_metadata(self, project):
        """Build metadata dict for agents."""
        return {
            'client_technical_level': 'moderate',  # Could be a field on Client
            'priority': project.priority if hasattr(project, 'priority') else 'medium',
            'notes': project.notes if hasattr(project, 'notes') else '',
        }


class ProjectFileUploadView(views.APIView):
    """Upload files to project Documents folder."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request, pk):
        project = Project.objects.get(pk=pk)
        file_obj = request.FILES.get('file')

        if not file_obj:
            return Response({'error': 'No file provided'}, status=400)

        file_instance = File.objects.create(
            project=project,
            title=file_obj.name,
            file=file_obj,
            description=request.data.get(
                'description',
                'Generated by Engineering Hub'
            ),
        )

        return Response({
            'id': file_instance.id,
            'url': file_instance.file.url,
            'message': 'File uploaded successfully'
        }, status=201)
```

### URLs

```python
# consultingmanager/api/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProjectViewSet, ProjectFileUploadView

router = DefaultRouter()
router.register(r'projects', ProjectViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path(
        'projects/<int:pk>/files/upload/',
        ProjectFileUploadView.as_view(),
        name='project-file-upload'
    ),
]
```

### Settings

```python
# consultingmanager/settings.py
INSTALLED_APPS = [
    # ...
    'rest_framework',
    'rest_framework.authtoken',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'user': '100/hour',
    },
}
```

## Testing the API

Using the mock server for development:

```bash
# Install mock server dependencies
pip install -e ".[mock-server]"

# Run mock server
uvicorn mock_server.main:app --port 8000 --reload

# Test endpoints
curl -H "Authorization: Token test-token-12345" http://localhost:8000/api/projects/
curl -H "Authorization: Token test-token-12345" http://localhost:8000/api/projects/1/context/
```

## Rate Limiting

The API implements rate limiting of 100 requests per hour per authenticated user. The Engineering Hub client implements exponential backoff for 429 responses.

## Error Responses

All error responses follow this format:

```json
{
  "detail": "Error message here"
}
```

Common status codes:
- `400 Bad Request`: Invalid request data
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Resource not found
- `429 Too Many Requests`: Rate limit exceeded
- `500 Internal Server Error`: Server error
