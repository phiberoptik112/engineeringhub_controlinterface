# Product Requirements Document: Engineering Hub Control Interface

**Version**: 1.0  
**Date**: February 15, 2026  
**Author**: Jake Pfitsch  
**Status**: Draft

---

## Executive Summary

The Engineering Hub Control Interface is a persistent, agent-first workspace that enables fluid collaboration between human engineers and AI agents on technical projects. Unlike traditional project management tools that focus on business operations, this hub is designed specifically for engineering work: research, technical writing, standards compliance, and analysis.

The system connects to a Django-based business backend (consultingmanager) via REST API while maintaining its own shared-notes-based workflow optimized for deep technical work with constrained time availability (20 hours/week).

---

## Problem Statement

### Current Pain Points

1. **Context Switching**: Engineers constantly switch between business tools (invoicing, proposals) and technical work (research, specifications, analysis)
2. **Lost Context**: Project-specific knowledge (scope, standards, client preferences) exists in disparate systems and must be manually retrieved
3. **Manual Agent Orchestration**: Running AI agents requires manual prompting, context building, and result synthesis
4. **No Persistent Collaboration**: Each agent interaction starts fresh; no shared workspace or memory
5. **Time Constraints**: With only 20 hours/week available, need maximum efficiency in task delegation and agent coordination

### Target Users

**Primary User**: Solo engineering consultant (Jake)
- Acoustic engineering projects
- Multiple concurrent clients
- Limited time availability
- Need to delegate research, documentation, and analysis
- Must maintain high technical quality and standards compliance

**Secondary Users**: AI Agents
- Research Agent
- Technical Writer Agent
- Standards Checker Agent
- ref_engineer Agent (workflow review specialist)
- evaluator Agent (document comparison and selection)

---

## Goals and Non-Goals

### Goals

1. **Single Shared Workspace**: One markdown file serves as the "team room" where human and agents collaborate
2. **Automatic Context Injection**: Agents automatically receive project-specific context (scope, standards, budget, history)
3. **Persistent Memory**: All decisions, findings, and work logged in searchable shared notes
4. **Seamless Django Integration**: Business data (projects, proposals, files) flows into engineering workspace via API
5. **Minimal Overhead**: Simple text-file interface, no complex UI to learn or maintain
6. **Agent Specialization**: Each agent has clear role and receives context formatted for that role

### Non-Goals

1. **Not replacing Django backend**: Business operations (billing, contracts) stay in Django
2. **Not a general PM tool**: Optimized for engineering work, not project management
3. **Not multi-user initially**: Single-user focused (can expand later)
4. **Not real-time collaboration**: Async file-based workflow is intentional
5. **Not a code editor**: Uses existing editors (Doom Emacs), provides orchestration layer

---

## User Personas

### Persona 1: Jake (Engineering Consultant)

**Background**:
- Acoustic engineering consultant
- Manages 3-5 concurrent projects
- 20 hours/week available
- Needs to delegate research, writing, analysis to agents
- Must ensure ASTM/ISO standards compliance

**Typical Workflow**:
```
Morning:
- Review shared notes for agent updates
- Add new tasks for agents based on client emails
- Approve/edit agent-generated documents
- Flag decisions in notes for future reference

Afternoon:
- Deep work on project-specific analysis
- Agents handle research, doc writing in background
- Check standards compliance via checker agent
- Update project status for billing
```

**Pain Points**:
- Manually building context for each agent query
- Losing track of which agent found what
- Re-explaining project scope repeatedly
- Searching for standards requirements across documents

### Persona 2: Research Agent

**Role**: Technical research specialist

**Inputs Needed**:
- Project scope and standards requirements
- Previous research findings
- Related tasks in progress

**Outputs**:
- Research summaries with citations
- Comparison documents
- Technical recommendations
- Links to relevant standards/papers

**Constraints**:
- Must cite sources
- Must align with project scope
- Should flag conflicts or gaps in requirements

### Persona 3: Technical Writer Agent

**Role**: Documentation specialist

**Inputs Needed**:
- Project scope
- Research findings
- Client background/preferences
- Standards to reference
- Existing documents for style matching

**Outputs**:
- Technical specifications
- Test protocols
- Client reports
- Proposal sections

**Constraints**:
- Must be technically accurate
- Must match client's technical sophistication
- Must reference correct standard versions

---

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────┐
│         Django Backend (consultingmanager)          │
│   - Projects, Clients, Billing                      │
│   - Proposals, Contracts                            │
│   - File Storage, Metadata                          │
│   - Business Logic                                  │
└──────────────┬──────────────────────────────────────┘
               │
               │ REST API (Django REST Framework)
               │ Auth: Token-based
               │
┌──────────────▼──────────────────────────────────────┐
│          Engineering Hub (Python)                   │
│                                                      │
│  ┌────────────────────────────────────────┐        │
│  │    Shared Notes File                   │        │
│  │    (Markdown with org-roam structure)  │        │
│  │    - Active tasks                      │        │
│  │    - Agent communication thread        │        │
│  │    - Project context cache             │        │
│  │    - Decision log                      │        │
│  └────────────┬───────────────────────────┘        │
│               │                                      │
│  ┌────────────▼───────────────────────────┐        │
│  │    Agent Orchestrator                  │        │
│  │    - File watcher                      │        │
│  │    - Task parser/dispatcher            │        │
│  │    - Workflow orchestrator            │        │
│  │    - Context manager                   │        │
│  │    - Django API client                 │        │
│  └────────────┬───────────────────────────┘        │
│               │                                      │
│       ┌───────┴────────┬──────────────┬─────────────┐
│       │                │              │             │
│  ┌────▼─────┐    ┌────▼─────┐   ┌───▼────┐   ┌─────▼─────┐
│  │ Research │    │Technical │   │Standards│   │ref_engineer│
│  │  Agent   │    │  Writer  │   │ Checker │   │ evaluator  │
│  │ (Claude) │    │ (Claude) │   │ (Claude)│   │ (Claude)   │
│  └──────────┘    └──────────┘   └─────────┘   └────────────┘
└─────────────────────────────────────────────────────┘
               │
               │ File System Access
               │
┌──────────────▼──────────────────────────────────────┐
│          Output Files                               │
│    - Research documents                             │
│    - Technical specifications                       │
│    - Reports (drafts, revisions, finals)            │
│    - Analysis reports                               │
└─────────────────────────────────────────────────────┘
```

### Data Flow

```
1. Django Sync Flow:
   User opens project in Django → Triggers API call → 
   Updates "Project Context Cache" in shared notes

2. Task Assignment Flow:
   User types task in shared notes (@agent: PENDING) →
   File watcher detects change →
   Orchestrator parses task →
   Builds project context from Django API →
   Formats context for agent type →
   Agent executes with full context →
   Results written to shared notes

3. Workflow Execution Flow:
   User triggers workflow (@workflow: multi_revision_report) →
   WorkflowOrchestrator loads workflow definition →
   Executes steps sequentially (iterations, reviews, revisions) →
   Each step logs progress to shared notes →
   Final step uploads to Django Documents folder

4. Context Retrieval Flow:
   Agent needs more detail →
   Uses tool (get_project_file, get_standard_details) →
   Orchestrator queries Django API or local DB →
   Returns formatted result to agent
```

---

## Core Features

### Feature 1: Shared Notes File (Single Source of Truth)

**Description**: A markdown file with org-roam structure serves as the persistent workspace.

**Structure**:
```markdown
---
workspace: engineering-hub
sync_url: http://localhost:8000/api
auth_token: ${DJANGO_API_TOKEN}
---

# Engineering Hub - Daily Notes

## Active Engineering Tasks
### @agent-name: STATUS
> Project: [[django://project/ID]]
> Task: Description
> Context: Additional info
> Deliverable: [[/path/to/output]]

## Workflows
### @workflow: workflow_name
> Project: [[django://project/ID]]
> Status: PENDING | RUNNING | COMPLETED
> Params: {...}
> **Workflow Log**: Step-by-step progress

## Agent Communication Thread
**[timestamp] @agent**
Message content

## Project Context Cache
### Project ID: Title
Auto-updated project info from Django

## Engineering Log
Dated entries with decisions and findings

## Quick Links
External resources
```

**Requirements**:
- Valid markdown syntax
- YAML frontmatter for config
- `@agent` mentions for task assignment
- `django://` URIs for project references
- `[[wikilinks]]` for file references
- Status values: PENDING, IN_PROGRESS, COMPLETED, BLOCKED
- `@workflow:` syntax for multi-step workflows (see Section 7.5)

### Feature 2: Automatic Project Context Injection

**Description**: When a task references a Django project, the orchestrator automatically fetches and formats relevant context for the assigned agent.

**Context Layers**:

1. **Base Project Info** (always included)
   - Title, client, status, budget
   - Project description
   - Start/end dates

2. **Scope of Work** (parsed from proposals)
   - List of deliverables
   - Testing requirements
   - Standards to comply with

3. **Standards & Requirements** (extracted)
   - ASTM standards (e.g., E336-17a)
   - ISO standards
   - Client-specific requirements

4. **Historical Context** (from shared notes)
   - Previous decisions for this project
   - Research findings
   - Ongoing related tasks

5. **Available Resources** (from Django)
   - Recent files
   - Proposals
   - Test data
   - Client communications

**Agent-Specific Formatting**:

Research Agent receives:
```
## Project Context: [Title]
**Scope of Work**:
- Item 1
- Item 2

**Standards & Requirements**:
- ASTM E336-17a
- ISO 12345

**Previous Research**:
[Summary of prior findings]

Your research should focus on...
```

Technical Writer receives:
```
## Project Context: [Title]
**Client**: [Name] - [Background]
**Budget**: $XX,XXX

**Document Purpose**:
[Scope items relevant to this doc]

**Technical Standards to Reference**:
[List]

**Available Research**:
[Links to research outputs]

Write documentation that is technically accurate while being accessible to [client].
```

### Feature 3: Agent Orchestration

**Description**: Background service that watches shared notes, dispatches tasks to agents, and manages execution.

**Components**:

1. **File Watcher**
   - Monitors shared notes for changes
   - Detects new tasks via `@agent: PENDING` pattern
   - Triggers orchestrator on modification

2. **Task Parser**
   - Extracts task metadata (agent, project, description)
   - Validates syntax
   - Extracts project references

3. **Context Manager**
   - Queries Django API for project context
   - Builds structured context object
   - Formats for specific agent type

4. **Agent Worker Pool**
   - Manages agent threads
   - Queues tasks per agent
   - Executes via Claude API
   - Writes results back to shared notes

5. **Django API Client**
   - Authenticated requests to Django
   - Caches responses
   - Handles rate limiting

**Configuration** (config.yaml):
```yaml
django:
  api_url: "http://localhost:8000/api"
  auth_token_env: "DJANGO_API_TOKEN"
  cache_ttl: 300

workspace:
  notes_file: "/Users/jakepfitsch/org-roam/engineering-hub/shared-notes.md"
  output_dir: "/Users/jakepfitsch/org-roam/engineering-hub/outputs"
  
agents:
  research:
    system_prompt_file: "prompts/research-agent.txt"
    tools: ["web_search", "web_fetch", "django_api"]
  technical-writer:
    system_prompt_file: "prompts/technical-writer.txt"
    tools: ["create_file", "view", "django_api"]
  ref_engineer:
    system_prompt_file: "prompts/ref-engineer.txt"
    tools: ["web_search", "get_project_file", "get_standard_details"]
  evaluator:
    system_prompt_file: "prompts/evaluator.txt"
    tools: ["get_project_file", "view"]

# See Section 7.1 for workflow definitions
workflows:
  multi_revision_report:
    steps: [...]
```

### Feature 4: Django REST API

**Description**: API layer on Django backend to expose project data to engineering hub.

**Endpoints**:

```python
GET  /api/projects/                    # List projects
GET  /api/projects/{id}/               # Project detail
GET  /api/projects/{id}/context/       # Rich context for agents
GET  /api/files/?project={id}          # Project files
GET  /api/proposals/?project={id}      # Project proposals
GET  /api/clients/{id}/                # Client info
POST /api/projects/{id}/sync/          # Trigger context sync
POST /api/projects/{id}/files/upload/  # Upload to project Documents folder
```

**Authentication**:
- Token-based (Django REST Framework)
- Per-user tokens
- Rate limiting: 100 requests/hour

**Example Response** (`/api/projects/23/context/`):
```json
{
  "project": {
    "id": 23,
    "title": "Building Acoustic Assessment",
    "client_name": "Acme Construction",
    "status": "in_progress",
    "budget": "45000.00",
    "description": "..."
  },
  "scope": [
    "ASTC testing per ASTM E336-17a",
    "AIIC testing per ASTM E1007-16",
    "Acoustic modeling and recommendations"
  ],
  "standards": [
    {"type": "ASTM", "id": "ASTM E336-17a"},
    {"type": "ASTM", "id": "ASTM E1007-16"}
  ],
  "recent_files": [...],
  "proposals": [...],
  "metadata": {
    "file_structure": "...",
    "scope_analysis": {...},
    "dollar_amounts": {...}
  }
}
```

### Feature 5: Agent Tools for On-Demand Context

**Description**: Tools that agents can invoke to query for specific information during task execution.

**Available Tools**:

1. **get_project_file**
   ```python
   {
     "name": "get_project_file",
     "description": "Retrieve a specific file from the project",
     "parameters": {
       "project_id": int,
       "filename": str
     }
   }
   ```

2. **get_standard_details**
   ```python
   {
     "name": "get_standard_details",
     "description": "Get detailed information about a testing standard",
     "parameters": {
       "standard_id": str  # e.g., "ASTM E336-17a"
     }
   }
   ```

3. **get_similar_projects**
   ```python
   {
     "name": "get_similar_projects",
     "description": "Find past projects with similar scope",
     "parameters": {
       "project_id": int,
       "scope_keywords": list[str]
     }
   }
   ```

4. **get_client_preferences**
   ```python
   {
     "name": "get_client_preferences",
     "description": "Get client preferences from past projects",
     "parameters": {
       "client_name": str
     }
   }
   ```

### Feature 6: Interface Options

**Option A: Doom Emacs Integration** (Primary)
```elisp
;; engineering-hub.el
(defun engineering-hub-start ()
  "Start the agent orchestrator"
  (start-process "engineering-hub" "*hub*" "python" "-m" "engineering_hub"))

(defun engineering-hub-status ()
  "Show agent status in side window"
  (display-buffer-in-side-window (get-buffer-create "*agent-status*")))

(defun engineering-hub-assign-task (agent-name)
  "Insert task template for agent"
  (insert (format "\n### @%s: PENDING\n> Project: \n> Task: \n\n" agent-name)))

;; Keybindings
;; C-c h s - Start orchestrator
;; C-c h v - View status
;; C-c h t - Assign task
```

**Option B: Terminal UI** (Fallback)
- Rich library for split-pane layout
- Top pane: Shared notes (read-only view)
- Bottom pane: Agent status and logs
- Update frequency: 1Hz

---

## Technical Specifications

### Technology Stack

**Backend (Django)**:
- Django 5.2
- Django REST Framework 3.14+
- PostgreSQL (production) / SQLite (dev)
- Token authentication

**Hub (Python)**:
- Python 3.11+
- Anthropic Python SDK
- watchdog (file watching)
- pyyaml (config)
- requests (API client)
- rich (terminal UI)

**Agents**:
- Claude Sonnet 4.5 via Claude API
- 4000 token max per response
- Tools: web_search, create_file, view

### File Structure

**Workspace** (`/Users/jakepfitsch/org-roam/engineering-hub/`):

```
engineering-hub/
├── config.yaml           # User configuration
├── shared-notes.md       # Agent collaboration file
├── outputs/
│   ├── research/
│   ├── docs/
│   └── analysis/
```

**Package** (`engineeringhub_controlinterface/`):

```
src/engineering_hub/
├── __init__.py
├── __main__.py
├── cli.py
├── core/
│   ├── constants.py
│   ├── exceptions.py
│   └── models.py
├── notes/
│   ├── parser.py
│   ├── writer.py
│   └── manager.py
├── django/
│   ├── client.py
│   ├── models.py
│   └── cache.py
├── context/
│   ├── manager.py
│   └── formatters.py
├── agents/
│   ├── worker.py
│   ├── prompts.py
│   └── registry.py
├── orchestration/
│   ├── orchestrator.py
│   ├── watcher.py
│   └── dispatcher.py
├── config/
│   ├── settings.py
│   └── loader.py
└── prompts/
    └── research-agent.txt
tests/
└── ...
```

### Security Considerations

1. **API Token Storage**
   - Environment variable: `DJANGO_API_TOKEN`
   - Never commit to version control
   - Rotate periodically

2. **File Permissions**
   - Shared notes: 600 (user read/write only)
   - Config file: 600
   - Output directory: 700

3. **API Rate Limiting**
   - Django: 100 requests/hour/token
   - Claude API: Standard tier limits
   - Implement exponential backoff

4. **Data Privacy**
   - Project data stays local + Django DB
   - No agent conversation logging to external services
   - Outputs stored locally

---

## 7. Workflow System

The base PRD provides the foundation for single-task agent execution. This section extends the system to support **multi-step workflows** with agent collaboration, iterative refinement, and Django folder sync.

### 7.0 Current Coverage vs. Workflow Requirements

**What's Currently Covered ✓**

- ✓ Reading project summaries (Django API + context injection)
- ✓ Agents creating documents (technical-writer agent)
- ✓ Agent communication thread (for collaboration)
- ✓ Saving outputs to local folders

**What's Missing ✗**

- ✗ **Multi-iteration generation**: No mechanism for "draft 2-3 iterations"
- ✗ **ref_engineer agent**: Only defined research, technical-writer, standards-checker
- ✗ **Agent-to-agent review workflow**: No structured review/revision process
- ✗ **Automatic evaluation/selection**: No "choose the best revision" capability
- ✗ **Django folder sync**: No way to save back to Django's project Documents folder
- ✗ **Complex multi-step orchestration**: Current orchestrator is task-by-task, not workflow-based

### 7.1 Workflow Definitions

Workflows are defined in YAML configuration and support multi-step orchestration with dependencies, iterations, and non-agent actions.

**config.yaml - Workflow definitions**:

```yaml
# config.yaml - Add workflow definitions
workflows:
  multi_revision_report:
    steps:
      - name: "generate_iterations"
        agent: "technical-writer"
        iterations: 3
        output_pattern: "/outputs/reports/{project_id}_draft_{iteration}.md"
        
      - name: "review_iterations"
        agent: "ref_engineer"
        inputs: 
          - from_step: "generate_iterations"
          - project_context: true
        output: "/outputs/reviews/{project_id}_review.md"
        
      - name: "revise_based_on_review"
        agent: "technical-writer"
        inputs:
          - from_step: "generate_iterations"
          - from_step: "review_iterations"
        output_pattern: "/outputs/reports/{project_id}_revised_{iteration}.md"
        
      - name: "evaluate_and_select"
        agent: "evaluator"
        inputs:
          - from_step: "generate_iterations"
          - from_step: "revise_based_on_review"
        output: "/outputs/reports/{project_id}_vFinal_for_review.md"
        
      - name: "save_to_django"
        action: "upload_to_project"
        input: "/outputs/reports/{project_id}_vFinal_for_review.md"
        destination: "Documents/"
```

### 7.2 Agent Collaboration Patterns

- **Review workflows**: Agent A reviews agent B's work (e.g., ref_engineer reviews technical-writer drafts)
- **Iterative refinement**: Generate → review → revise → select
- **Parallel execution**: Multiple agents working simultaneously (future)

### 7.3 Workflow Agents

**ref_engineer Agent**:

```python
REF_ENGINEER_AGENT = {
    "system_prompt": """You are a reference engineer who reviews technical 
    reports for accuracy, completeness, and adherence to standards.
    
    Your role is to:
    1. Review draft reports against project scope and standards
    2. Verify all claims are properly sourced
    3. Identify missing source materials or references
    4. Check calculations and technical assertions
    5. Suggest specific improvements with references to source materials
    
    For each draft, provide:
    - Technical accuracy assessment
    - Missing references/sources to add
    - Specific revision suggestions
    - Overall quality rating (1-10)""",
    
    "tools": ["web_search", "get_project_file", "get_standard_details"],
}
```

**evaluator Agent**:

```python
EVALUATOR_AGENT = {
    "system_prompt": """You evaluate and select the best version from multiple 
    document drafts based on:
    
    1. Technical accuracy and completeness
    2. Clarity and readability for target audience
    3. Proper sourcing and references
    4. Adherence to project scope and standards
    5. Professional presentation
    
    Provide structured comparison and clear selection rationale.""",
}
```

### 7.4 Workflow Orchestration

```python
# engineering_hub/workflow_orchestrator.py
class WorkflowOrchestrator:
    """Orchestrates multi-step workflows with agent collaboration"""
    
    def execute_workflow(self, workflow_name: str, params: Dict):
        """Execute a defined workflow"""
        workflow_def = self.config['workflows'][workflow_name]
        context = {"params": params, "outputs": {}}
        
        for step in workflow_def['steps']:
            step_result = self._execute_step(step, context)
            context['outputs'][step['name']] = step_result
            
            # Log to shared notes
            self._log_workflow_progress(workflow_name, step, step_result)
        
        return context['outputs']
    
    def _execute_step(self, step: Dict, context: Dict):
        """Execute a single workflow step"""
        if step.get('iterations'):
            return self._execute_iterations(step, context)
        elif step.get('action'):
            return self._execute_action(step, context)
        else:
            return self._execute_agent_task(step, context)
    
    def _execute_iterations(self, step: Dict, context: Dict):
        """Generate multiple iterations"""
        agent = self.agents[step['agent']]
        iterations = []
        
        for i in range(1, step['iterations'] + 1):
            # Build prompt for this iteration
            prompt = self._build_iteration_prompt(step, context, i)
            
            # Execute agent
            result = agent.execute(prompt)
            
            # Save output
            output_path = step['output_pattern'].format(
                project_id=context['params']['project_id'],
                iteration=i
            )
            self._save_output(output_path, result)
            iterations.append(output_path)
            
        return iterations
    
    def _execute_action(self, step: Dict, context: Dict):
        """Execute non-agent actions (e.g., file uploads)"""
        if step['action'] == 'upload_to_project':
            return self._upload_to_django(
                file_path=step['input'],
                destination=step['destination'],
                project_id=context['params']['project_id']
            )
```

### 7.5 Shared Notes Workflow Syntax

```markdown
## Workflows

### @workflow: multi_revision_report
> Project: [[django://project/25]]
> Status: PENDING
> Params:
>   - report_type: "program_narrative"
>   - include_sections: ["executive_summary", "progress", "analysis", "todos"]
> 
> **Steps**:
> 1. Generate 3 draft iterations (technical-writer)
> 2. Review all drafts (ref_engineer)
> 3. Generate 2nd revision based on review (technical-writer)
> 4. Evaluate and select best (evaluator)
> 5. Save to Django Documents folder
>
> **Workflow Log**:
> [2026-02-15 10:00] Step 1 started: Generating iterations...
> [2026-02-15 10:15] Step 1 complete: 3 drafts created
>   - /outputs/reports/25_draft_1.md
>   - /outputs/reports/25_draft_2.md
>   - /outputs/reports/25_draft_3.md
> [2026-02-15 10:16] Step 2 started: ref_engineer reviewing...
```

### 7.6 Django Integration

**File upload endpoint** (consultingmanager):

```python
# consultingmanager/api/views.py
class ProjectFileUploadView(views.APIView):
    """Upload files to project Documents folder"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, pk):
        project = Project.objects.get(pk=pk)
        file_obj = request.FILES['file']
        destination = request.data.get('destination', '')
        
        # Create File instance
        file_instance = File.objects.create(
            project=project,
            title=file_obj.name,
            file_type=self._detect_type(file_obj),
            file=file_obj,
            description=request.data.get('description', 'Generated by Engineering Hub')
        )
        
        # Optionally save to specific folder structure
        if destination:
            # Handle folder organization
            pass
            
        return Response({
            'id': file_instance.id,
            'url': file_instance.file.url,
            'message': 'File uploaded successfully'
        })
```

**API endpoint**: `POST /api/projects/{id}/files/upload/`

---

## Success Metrics

### Phase 1 (MVP) - 2 weeks
- [ ] Shared notes file format validated
- [ ] Single agent (research) can execute tasks
- [ ] Django API returns project context
- [ ] Context successfully injected into agent prompt
- [ ] Agent output written back to shared notes

### Phase 2 (Core Functionality) - 4 weeks
- [ ] 3 agents operational (research, writer, checker)
- [ ] Automatic context injection working for all agents
- [ ] Agent tools functional (get_project_file, etc.)
- [ ] File watcher detects and dispatches tasks
- [ ] 80% reduction in manual context building time

### Phase 3 (Polish) - 2 weeks
- [ ] Doom Emacs integration functional
- [ ] Error handling robust
- [ ] Documentation complete
- [ ] Successfully managing 2+ real projects via hub

### Phase 4 (Workflow System) - Future
- [ ] Workflow definitions and WorkflowOrchestrator implemented
- [ ] ref_engineer and evaluator agents operational
- [ ] Multi-iteration generation working
- [ ] Django file upload endpoint and sync
- [ ] End-to-end multi_revision_report workflow

### Long-term Metrics (3 months)
- **Time Efficiency**: Reduce time spent on documentation/research by 50%
- **Quality**: Zero standards compliance gaps on delivered projects
- **Context Accuracy**: 95% of agent outputs require no context corrections
- **User Satisfaction**: Jake rates system 8/10 or higher

---

## Implementation Phases

### Phase 1: Foundation (Week 1-2)

**Django API**:
- [ ] Install Django REST Framework
- [ ] Create serializers (Project, File, Proposal)
- [ ] Implement `/api/projects/{id}/context/` endpoint
- [ ] Add token authentication
- [ ] Test API manually with curl/Postman

**Hub Core**:
- [ ] Create shared-notes.md format
- [ ] Implement SharedNotesManager (parse, update)
- [ ] Implement basic file watcher
- [ ] Create config.yaml structure
- [ ] Build Django API client

**Testing**:
- [ ] Create test project in Django
- [ ] Verify API returns correct context
- [ ] Verify notes parsing works

### Phase 2: Single Agent (Week 3-4)

**Agent Infrastructure**:
- [ ] Implement AgentWorker base class
- [ ] Create research agent config + prompt
- [ ] Implement context manager
- [ ] Build context formatter for research agent
- [ ] Connect file watcher → orchestrator → agent

**Testing**:
- [ ] Manually add task to shared notes
- [ ] Verify agent receives project context
- [ ] Verify agent output written correctly
- [ ] Test error handling (bad project ID, etc.)

### Phase 3: Multi-Agent (Week 5-6)

**Agent Expansion**:
- [ ] Add technical writer agent
- [ ] Add standards checker agent
- [ ] Implement agent-specific formatters
- [ ] Test agent interactions (research → writer)

**Agent Tools**:
- [ ] Implement get_project_file tool
- [ ] Implement get_standard_details tool
- [ ] Test tool execution and response handling

**Testing**:
- [ ] Run all agents on same project
- [ ] Verify context isolation between agents
- [ ] Test concurrent task execution

### Phase 4: Interface & Polish (Week 7-8)

**Doom Emacs Integration**:
- [ ] Create engineering-hub.el
- [ ] Implement keybindings
- [ ] Add status display
- [ ] Test workflow in Emacs

**Error Handling**:
- [ ] Add retry logic for API failures
- [ ] Implement graceful degradation
- [ ] Add logging and debugging output
- [ ] Create error recovery procedures

**Documentation**:
- [ ] Write user guide
- [ ] Document agent capabilities
- [ ] Create troubleshooting guide
- [ ] Add example workflows

### Phase 5: Workflow System (Future)

**Workflow Infrastructure**:
- [ ] Implement WorkflowOrchestrator class
- [ ] Add workflow definitions to config.yaml
- [ ] Parse @workflow syntax in shared notes
- [ ] Implement iteration and step execution logic

**Workflow Agents**:
- [ ] Add ref_engineer agent (prompts + tools)
- [ ] Add evaluator agent (prompts + tools)
- [ ] Test agent-to-agent handoff

**Django Integration**:
- [ ] Implement ProjectFileUploadView
- [ ] Add POST /api/projects/{id}/files/upload/ endpoint
- [ ] Test file upload from hub to Django

**Testing**:
- [ ] End-to-end multi_revision_report workflow
- [ ] Verify workflow log updates in shared notes

---

## Open Questions

1. **Context Window Management**: How to handle when full project context exceeds Claude's context window?
   - *Proposed*: Implement tiered context (essential + on-demand)

2. **Agent Coordination**: How should agents handle conflicting outputs?
   - *Proposed*: Add conflict detection in shared notes, flag for human review

3. **Version Control**: Should shared notes be git-tracked?
   - *Proposed*: Yes, with .gitignore for outputs/

4. **Multi-Project**: How to handle working on multiple projects simultaneously?
   - *Proposed*: Separate sections in shared notes, context manager handles all

5. **Offline Mode**: Should hub work without Django connection?
   - *Proposed*: Yes, degrade gracefully (use cached context)

6. **Workflow Failure Recovery**: How to resume a failed workflow mid-step?
   - *Proposed*: Checkpoint outputs per step; allow manual resume from last successful step

---

## Appendix A: Example Workflows

### Workflow 1: Starting a New Project

1. Create project in Django (business side)
2. Add initial task in shared notes:
   ```markdown
   ### @research: PENDING
   > Project: [[django://project/25]]
   > Task: Review project proposal and extract scope + standards
   > Deliverable: [[/outputs/research/project-25-scope.md]]
   ```
3. Orchestrator auto-fetches Django context
4. Research agent executes with full context
5. Agent creates scope document
6. Agent updates shared notes with findings
7. Jake reviews and assigns next task (technical writer)

### Workflow 2: Standards Compliance Check

1. Technical writer completes spec document
2. Jake assigns compliance check:
   ```markdown
   ### @standards-checker: PENDING
   > Project: [[django://project/25]]
   > Task: Verify spec compliance with ASTM E336-17a
   > Context: Check [[/outputs/docs/test-protocol-25.md]]
   > Deliverable: Compliance report with any gaps
   ```
3. Standards checker loads project context (knows required standards)
4. Agent reads spec document
5. Agent uses get_standard_details tool
6. Agent generates gap analysis
7. Jake addresses gaps before client delivery

### Workflow 3: Client Report Generation

1. Testing complete, data analyzed
2. Jake assigns report writing:
   ```markdown
   ### @technical-writer: PENDING
   > Project: [[django://project/25]]
   > Task: Draft executive summary for test results
   > Context: 
   >   - Results in [[/outputs/analysis/test-results-25.csv]]
   >   - Use professional but accessible tone for client
   > Deliverable: [[/outputs/docs/executive-summary-25.md]]
   ```
3. Writer agent loads project + client context
4. Agent knows client's technical background
5. Agent drafts summary matching client sophistication
6. Jake reviews, edits, delivers

### Workflow 4: Multi-Revision Report (Workflow System)

Full workflow using `multi_revision_report` with agent collaboration:

```markdown
## Active Workflows

### @workflow: multi_revision_report
> Project: [[django://project/25]]
> Execute: multi_revision_report
> Status: RUNNING

---
**[2026-02-15 14:00] System**
Starting workflow: multi_revision_report for Project 25

**[2026-02-15 14:01] Step 1/5: Generate Iterations**
@technical-writer generating 3 draft iterations...

**[2026-02-15 14:15] @technical-writer**
Created 3 draft reports:
- Draft 1: Focused on executive summary + high-level progress
- Draft 2: Detailed analysis with more technical depth
- Draft 3: Balanced narrative with integrated todos

Files: [[/outputs/reports/25_draft_1.md]], [[/outputs/reports/25_draft_2.md]], [[/outputs/reports/25_draft_3.md]]

**[2026-02-15 14:16] Step 2/5: Review Iterations**
@ref_engineer reviewing all drafts...

**[2026-02-15 14:30] @ref_engineer**
Review complete. Key findings:

Draft 1:
- Missing source materials for acoustic test results claim
- Need reference to ASTM E336-17a Section 7.2
- Todo items not prioritized
- Rating: 6/10

Draft 2:
- Excellent technical depth but too complex for client
- Add: Reference to site survey from 2024-01-15
- Good sourcing overall
- Rating: 7/10

Draft 3:
- Best balance of narrative + technical content
- Missing: Budget vs actual comparison
- Should add reference to proposal scope document
- Rating: 8/10

**Recommended additions for revision:**
1. Add budget tracking data from [[django://project/25/files/budget-tracker.xlsx]]
2. Reference site survey: [[django://project/25/files/site-survey-2024-01-15.pdf]]
3. Add ASTM E336-17a compliance note
4. Prioritize todos by deadline

Review document: [[/outputs/reviews/25_review.md]]

**[2026-02-15 14:31] Step 3/5: Revise Based on Review**
@technical-writer creating revisions incorporating ref_engineer feedback...

**[2026-02-15 14:45] @technical-writer**
Created 2 revised versions:
- Revision 1: Based on Draft 3 + all recommended additions
- Revision 2: Based on Draft 2 + simplified language + recommended additions

Files: [[/outputs/reports/25_revised_1.md]], [[/outputs/reports/25_revised_2.md]]

**[2026-02-15 14:46] Step 4/5: Evaluate and Select**
@evaluator comparing all versions...

**[2026-02-15 14:50] @evaluator**
Evaluation complete.

**Selected: Revision 1** (based on Draft 3)

Rationale:
- Best balance of technical accuracy + client accessibility
- All source materials properly referenced
- Todos prioritized by deadline as requested
- Budget tracking integrated cleanly
- Complies with all project standards

Final version: [[/outputs/reports/25_vFinal_for_review.md]]

**[2026-02-15 14:51] Step 5/5: Save to Django**
Uploading to Project 25 Documents folder...

**[2026-02-15 14:52] System**
✓ Workflow complete!
File uploaded: http://localhost:8000/projects/25/files/47
Location: Documents/25_vFinal_for_review.md

@jake: Please review final document and approve for client delivery.
```

---

## Appendix B: Agent Prompt Examples

### Research Agent Base Prompt

```
You are a research assistant specializing in acoustic engineering and building science. Your role is to:

1. Gather and synthesize technical information from authoritative sources
2. Summarize research papers, standards documents, and technical specifications
3. Provide recommendations with supporting evidence and citations
4. Create comparison documents when evaluating alternatives
5. Flag conflicts or inconsistencies in requirements

CRITICAL REQUIREMENTS:
- Always cite your sources with full references
- Distinguish between requirements and recommendations
- Note the publication date of standards (e.g., ASTM E336-17a vs 2015 version)
- Highlight any gaps or ambiguities in project scope
- Create deliverables as markdown documents in /outputs/research/

OUTPUT FORMAT:
For each research task, create a structured document with:
1. Executive Summary
2. Detailed Findings (with citations)
3. Recommendations
4. References

You will receive project-specific context including scope, standards, and budget. 
Use this context to focus your research on project-relevant topics.
```

### Technical Writer Base Prompt

```
You are a technical writer creating documentation for acoustic engineering projects. Your role is to:

1. Write clear, comprehensive technical documentation
2. Create test protocols following industry standards
3. Draft client-facing reports with appropriate technical depth
4. Develop specifications and design documents
5. Ensure all documents reference correct standard versions

CRITICAL REQUIREMENTS:
- Match the client's technical sophistication level
- Use consistent terminology throughout documents
- Reference specific standard sections (e.g., "per ASTM E336-17a Section 7.2")
- Include all required elements per standard templates
- Create deliverables as markdown or formatted documents in /outputs/docs/

STYLE GUIDELINES:
- Use active voice
- Define acronyms on first use
- Include measurement units
- Number all figures and tables
- Provide cross-references where relevant

You will receive project context including scope, client background, and research findings.
Use this to ensure technical accuracy while maintaining appropriate accessibility.
```

### ref_engineer Agent Base Prompt

```
You are a reference engineer who reviews technical reports for accuracy, completeness, 
and adherence to standards.

Your role is to:
1. Review draft reports against project scope and standards
2. Verify all claims are properly sourced
3. Identify missing source materials or references
4. Check calculations and technical assertions
5. Suggest specific improvements with references to source materials

For each draft, provide:
- Technical accuracy assessment
- Missing references/sources to add
- Specific revision suggestions
- Overall quality rating (1-10)
```

### evaluator Agent Base Prompt

```
You evaluate and select the best version from multiple document drafts based on:

1. Technical accuracy and completeness
2. Clarity and readability for target audience
3. Proper sourcing and references
4. Adherence to project scope and standards
5. Professional presentation

Provide structured comparison and clear selection rationale.
```

---

**End of PRD**