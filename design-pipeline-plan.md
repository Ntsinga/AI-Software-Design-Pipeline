# Design Pipeline --- Concept & Implementation Plan

## 1. Vision

Build a provider-neutral, requirements-driven software design pipeline
that turns a business requirements document and an existing
project/folder into a living set of connected design artifacts.

The pipeline should support:

-   Reading an existing Business Requirements Document (BRD), or
    generating one when absent.
-   Building a structured understanding of the business and proposed
    solution.
-   Creating a conceptual Business Model and Solution Model as part of
    the system-model creation methodology.
-   Selecting the most useful architecture, process, data, and
    interaction diagrams.
-   Generating Mermaid diagrams through MCP capabilities.
-   Generating lightweight interactive React/HTML system mockups.
-   Providing one interactive review/approval workspace for BRDs,
    diagrams, flows, data views, and UI mockups.
-   Capturing approvals, comments, requested changes, and retries as
    durable project artifacts.
-   Propagating approved BRD changes to affected downstream artifacts
    through dependency tracking.
-   Allowing failed individual stages to be retried without restarting
    the entire pipeline.
-   Remaining usable from Claude Code, VS Code, Codex, or other AI
    harnesses.
-   Keeping canonical agent/workflow definitions independent from any
    vendor-specific agent format.
-   Exposing the Design Pipeline as an MCP server.
-   Allowing the Design Pipeline to consume other MCP servers such as
    Mermaid, Figma later, GitHub, browser, filesystem, etc.
-   Using Python and Pydantic for the initial implementation.

The central design principle is:

> Workflow Engine = code\
> Agents = reasoning\
> MCP = capabilities\
> Model adapters = provider portability\
> Artifacts = shared context\
> Dependency graph = change propagation

This separation should remain a foundational architectural rule.

------------------------------------------------------------------------

# 2. Core Architectural Principles

## 2.1 The BRD is the business source of truth

The BRD describes business intent, requirements, actors, rules,
workflows, and outcomes.

However, the BRD is not the only model used by the system.

The pipeline should progressively derive richer representations from it.

``` text
BRD
 │
 ▼
Business Understanding
 │
 ├── Business Model
 │
 └── Solution Model
        │
        ▼
   System Model
        │
        ├── Workflows / Processes
        ├── Data / Entities
        ├── Architecture
        ├── Integrations
        ├── Actors / Permissions
        └── UI / Screens
```

The Business Model and Solution Model should be treated as important
conceptual layers in the system-model creation methodology, not
necessarily as mandatory standalone workflow stages.

The agent should use these models to better reason about the system
before deciding which diagrams and mockups are appropriate.

------------------------------------------------------------------------

# 3. Conceptual System-Model Methodology

The pipeline should not jump directly from prose requirements to
diagrams.

Instead, the Requirements Agent should construct a progressively richer
conceptual model.

## 3.1 Business Model

The Business Model describes:

-   Business actors
-   Stakeholders
-   Business capabilities
-   Business goals
-   Business processes
-   Business rules
-   Business outcomes
-   Roles and responsibilities
-   External organizations
-   Key business events

Example:

``` text
Audit Manager
    │
    ├── Plans Audits
    ├── Assigns Auditors
    ├── Reviews Findings
    └── Approves Reports

Auditor
    │
    ├── Performs Audit
    ├── Records Findings
    └── Requests Closure
```

## 3.2 Solution Model

The Solution Model describes how the proposed software supports the
business model.

It can include:

-   Major system capabilities
-   Application boundaries
-   Major services/components
-   Integrations
-   Data ownership
-   User-facing capabilities
-   External dependencies
-   Security/authentication concepts
-   Important technical constraints

Example:

``` text
Business Capability
        │
        ▼
System Capability
        │
        ├── Audit Management
        ├── Finding Management
        ├── Report Approval
        └── Notifications
```

## 3.3 System Model

The System Model combines the relevant business and solution
understanding into a structured representation used by downstream
agents.

It should capture relationships such as:

``` text
Requirement
   ↓
Business capability
   ↓
Business workflow
   ↓
System capability
   ↓
Entity / data
   ↓
API / service
   ↓
Screen / UI
```

Not every relationship needs to exist for every artifact.

The goal is traceability, not excessive modeling.

------------------------------------------------------------------------

# 4. Canonical Agent Architecture

Agent definitions must be provider-neutral.

Canonical agent definitions should live in:

``` text
.design/agents/
    requirements.yaml
    architecture.yaml
    ux.yaml
```

These are the authoritative definitions.

They should not be Claude-specific, VS Code-specific, OpenAI-specific,
or Gemini-specific.

Example:

``` yaml
id: architecture-agent

description: >
  Analyze an approved system model and produce the
  appropriate architecture and technical diagrams.

inputs:
  - brd
  - business-model
  - solution-model
  - system-model

outputs:
  - architecture-model
  - diagram-specifications
  - artifact-dependencies

tools:
  - project.read
  - artifact.read
  - artifact.write
  - mermaid.render

constraints:
  - Do not modify approved business requirements.
  - Prefer the smallest useful set of diagrams.
```

The canonical definition describes the agent's role, inputs, outputs,
constraints, and capabilities.

It should not hard-code a model vendor.

------------------------------------------------------------------------

# 5. Harness Adapters

Canonical agent definitions can be compiled/adapted into
harness-specific formats.

For example:

``` text
.design/agents/architect.yaml
            │
       ┌────┴─────┐
       ▼          ▼
Claude format   VS Code format
```

Potential generated locations:

``` text
.claude/agents/
.github/agents/
```

The canonical definition remains authoritative.

Vendor-specific files are adapters.

This prevents the project from becoming tied to:

``` text
.agent.md
```

or any other vendor-specific format.

Eventually additional adapters may support:

-   Claude Code
-   VS Code
-   Codex
-   Other agent harnesses
-   Custom web application
-   CI/CD execution

The canonical agent definition must remain portable.

------------------------------------------------------------------------

# 6. Workflow Engine

The Workflow Engine is ordinary application code.

It should not initially be an autonomous LLM supervisor.

Its responsibilities include:

-   Executing workflow steps.
-   Managing workflow state.
-   Creating tasks.
-   Scheduling tasks.
-   Enforcing dependencies.
-   Handling human approval gates.
-   Retrying failed steps.
-   Tracking artifacts.
-   Detecting changes.
-   Resolving artifact dependencies.
-   Triggering downstream updates.
-   Running independent steps in parallel when safe.
-   Persisting execution history.

The Workflow Engine should be designed around composable workflow steps.

``` text
Workflow
  │
  ├── Step
  ├── Step
  ├── Approval Gate
  ├── Step
  └── Step
```

A step should be easy to:

-   Add
-   Remove
-   Replace
-   Refactor
-   Reorder
-   Copy
-   Reuse
-   Execute independently
-   Retry independently

Avoid designing the pipeline as one giant agent prompt.

------------------------------------------------------------------------

# 7. Standard Workflow Step Contract

Every step should eventually conform to a common task contract.

Conceptually:

``` python
class WorkflowStep:
    id: str
    name: str
    inputs: list[str]
    outputs: list[str]

    async def execute(context) -> StepResult:
        ...
```

A step should have:

-   Stable ID
-   Inputs
-   Outputs
-   Preconditions
-   Postconditions
-   Required capabilities
-   Optional model capability
-   Retry policy
-   Approval policy
-   Dependency information

This makes workflow steps reusable components.

Example:

``` yaml
id: generate-architecture-diagrams

inputs:
  - system-model
  - solution-model

outputs:
  - architecture-diagram
  - context-diagram
  - process-diagrams

requires:
  - mermaid.render
```

------------------------------------------------------------------------

# 8. Agent vs Workflow Step

Do not assume every workflow step requires an agent.

Some steps are deterministic code.

Examples:

``` text
Detect file change       → code
Calculate hash           → code
Resolve dependencies     → code
Validate schema          → code
Run Mermaid              → MCP capability
Analyze requirements    → agent
Recommend diagrams      → agent
Design UI               → agent
```

The agent should be used where reasoning is valuable.

The Workflow Engine should handle deterministic operations.

------------------------------------------------------------------------

# 9. Handoff Architecture

The preferred internal mechanism is a structured task/handoff rather
than passing conversational history between agents.

Canonical handoff:

``` json
{
  "task_id": "task-284",
  "from": "requirements-agent",
  "to": "architecture-agent",

  "objective": "Design the initial system architecture.",

  "inputs": {
    "brd": "artifact://brd/v4",
    "business_model": "artifact://business-model/v3",
    "solution_model": "artifact://solution-model/v3",
    "system_model": "artifact://system-model/v4"
  },

  "changes": [
    "BR-017 added Director approval"
  ],

  "constraints": [
    "Do not change approved business requirements"
  ],

  "expected_outputs": [
    "architecture-model",
    "diagram-recommendations"
  ]
}
```

The handoff is a structured task.

The receiving agent reads the referenced artifacts and performs the
task.

Do not depend on the sending agent's hidden reasoning or entire
conversation history.

------------------------------------------------------------------------

# 10. Task-Based Orchestration Instead of Giant Agent Chains

Preferred architecture:

``` text
                    Workflow Engine
                    /      |       \
                   /       |        \
                  ▼        ▼         ▼
          Requirements  Architecture  UX
             Agent         Agent      Agent
```

Not:

``` text
Requirements Agent
        ↓
Architecture Agent
        ↓
UI Agent
        ↓
Data Agent
        ↓
...
```

The Workflow Engine should retain control.

Agents are workers.

This makes retries, approvals, parallel execution, auditing, and change
propagation much easier.

------------------------------------------------------------------------

# 11. Human Approval Is a First-Class Workflow State

Human review is not an external afterthought.

It is part of the state machine.

Initial example:

``` text
REQUIREMENTS_DRAFTED
       ↓
AWAITING_REQUIREMENTS_APPROVAL
       ↓
REQUIREMENTS_APPROVED
       ↓
ARCHITECTURE_GENERATED
       ↓
AWAITING_ARCHITECTURE_APPROVAL
       ↓
ARCHITECTURE_APPROVED
       ↓
MOCKUPS_GENERATED
       ↓
AWAITING_UI_APPROVAL
       ↓
COMPLETE
```

The system should support more granular states.

Example:

``` text
GENERATING
GENERATED
AWAITING_REVIEW
CHANGES_REQUESTED
RETRYING
APPROVED
FAILED
SUPERSEDED
```

These states should be attached to individual artifacts and workflow
steps, not only to the entire project.

------------------------------------------------------------------------

# 12. Interactive Review Workspace

The user should have one place for review.

The system should generate an interactive React/HTML review application.

It may initially be temporary or local.

Example:

``` text
http://localhost:xxxx/design-review
```

Later it can be deployed to a standard project-specific review URL.

The workspace should contain:

``` text
┌───────────────────────────────────────────────┐
│ Design Pipeline Review                        │
├───────────────┬───────────────────────────────┤
│ Requirements  │                               │
│ Architecture  │        PREVIEW                │
│ Data Model    │                               │
│ Processes     │                               │
│ Mockups       │                               │
│ Changes       │                               │
│ History       │                               │
└───────────────┴───────────────────────────────┘
```

The user should be able to:

-   Preview Mermaid diagrams.
-   Zoom and inspect diagrams.
-   Navigate between related artifacts.
-   Preview process flows.
-   Preview data/entity relationships.
-   Navigate interactive mockups.
-   Comment on a specific artifact.
-   Comment on a specific UI screen.
-   Approve an artifact.
-   Request changes.
-   Retry generation.
-   Compare versions.
-   View previous feedback.
-   See which requirements produced an artifact.

------------------------------------------------------------------------

# 13. Comments Must Become Structured Data

A comment should not disappear into a chat transcript.

Example:

``` json
{
  "comment_id": "comment-019",
  "artifact_id": "mockup-report-approval-v4",
  "location": {
    "screen": "ReportApproval",
    "element": "approval-panel"
  },
  "author": "user",
  "text": "Director approval should only appear for high-risk reports.",
  "status": "open",
  "created_at": "...",
  "resolved_at": null
}
```

The agent receives these comments when retrying or revising the
artifact.

This creates a feedback loop:

``` text
Generate
   ↓
Preview
   ↓
Comment
   ↓
Store feedback
   ↓
Retry
   ↓
Agent reads artifact + comments
   ↓
New version
   ↓
Review again
```

------------------------------------------------------------------------

# 14. Approval and Retry Semantics

Each artifact should have independent lifecycle state.

Example:

``` text
Requirements ✓
Architecture ✓
ERD ✓
Process ✓
Mockup ✕

Retry Mockup
```

Retrying the mockup should not regenerate the approved architecture.

The retry task should contain:

``` text
Original artifact
+
Relevant requirements
+
System model
+
Previous version
+
Open comments
+
Previous generation metadata
+
User's new instruction
```

This keeps token usage low while preserving context.

------------------------------------------------------------------------

# 15. Artifact Versioning

Every generated artifact should be versioned.

Example:

``` text
BRD
v1
v2
v3

Architecture
v1
v2

Report Approval Mockup
v1
v2
v3
```

Artifacts should record:

-   Version
-   Parent version
-   Generator
-   Agent
-   Model/provider
-   Timestamp
-   Input artifacts
-   Requirement IDs
-   Comments
-   Approval state
-   Superseded status

Example:

``` yaml
artifact_id: mockup-report-approval
version: 5

generated_by:
  agent: ux-agent
  provider: anthropic
  model: ...

inputs:
  - brd:v8
  - system-model:v7
  - workflow:report-approval:v4

requirements:
  - BR-017
  - BR-022

status: awaiting_review
```

------------------------------------------------------------------------

# 16. Dependency Graph

The dependency graph is the mechanism that makes change propagation
possible.

Example:

``` text
BR-017
 │
 ├── Business Process: report-approval
 │
 ├── Solution Capability: approval-management
 │
 ├── Entity: Approval
 │
 ├── API: POST /approvals
 │
 ├── Diagram: report-approval-flow
 │
 └── UI: report-approval-screen
```

A dependency registry may look like:

``` yaml
BR-017:
  affects:
    - business-process:report-approval
    - entity:approval
    - diagram:report-approval-flow
    - screen:report-approval
    - api:approval
```

This should eventually become a directed graph.

------------------------------------------------------------------------

# 17. BRD Change Propagation

The desired flow is:

``` text
BRD changed
     ↓
Detect change
     ↓
Requirement diff
     ↓
Update Business/Solution/System understanding
     ↓
Dependency analysis
     ↓
Identify affected artifacts
     ↓
Present impact report
     ↓
Human approval
     ↓
Regenerate affected artifacts
```

Do not automatically regenerate everything.

Example:

``` text
BR-017 changed

Affected:
✓ Approval process diagram
✓ ERD
✓ Report Approval mockup
✓ Permission model

Unaffected:
— Authentication
— Audit creation
— System context
```

The user should be able to approve the impact plan before regeneration.

------------------------------------------------------------------------

# 18. File Changes as Events

The project should eventually support:

``` text
BRD.md modified
      ↓
File watcher / Git event
      ↓
Design Pipeline
      ↓
Change analysis
```

Possible triggers:

-   Local filesystem watcher
-   Git commit
-   Pull request
-   Manual "Analyze Changes"
-   MCP tool invocation
-   CI event
-   Webhook

This should be optional.

Do not make the pipeline continuously regenerate artifacts without
explicit policy.

------------------------------------------------------------------------

# 19. Diagram Strategy

The Architecture Agent should choose diagrams based on the system.

Do not always generate every possible diagram.

For a typical business application, useful initial diagrams may include:

1.  System Context
2.  High-level Architecture / Container diagram
3.  Entity Relationship Diagram
4.  Major Business Process / Activity diagrams
5.  Sequence diagrams for complex interactions

The agent should explain why a diagram is useful.

Example:

``` text
Recommended:
✓ System Context
✓ Architecture
✓ ERD
✓ Audit Finding Process

Not currently necessary:
— Deployment diagram
— Detailed sequence diagrams
```

The goal is the smallest useful design set.

------------------------------------------------------------------------

# 20. Mermaid MCP

The Design Pipeline should consume Mermaid as a capability rather than
implementing diagram generation itself.

Conceptually:

``` text
Architecture Agent
       ↓
Diagram Specification
       ↓
Design Pipeline
       ↓
Mermaid MCP
       ↓
Rendered Diagram
```

The agent should reason about the diagram.

Mermaid should render it.

The pipeline should retain both:

``` text
diagram.mmd
diagram.svg/png/html
```

The Mermaid source is the editable canonical diagram representation.

------------------------------------------------------------------------

# 21. Mockup Strategy

Use React/HTML for system mockups.

Do not initially rely on image generation as the canonical mockup
mechanism.

The UX Agent should generate a lightweight interactive prototype.

The objective is not production-quality UI.

The objective is:

-   Validate workflow
-   Validate information architecture
-   Validate data
-   Validate screen transitions
-   Validate permissions
-   Validate business rules
-   Identify missing requirements

Example:

``` text
Audit Dashboard
      ↓
Audit Details
      ↓
Findings
      ↓
Finding Details
      ↓
Management Response
      ↓
Approval
```

Mockups should use realistic but synthetic data.

------------------------------------------------------------------------

# 22. Token-Minimal Mockup Generation

The pipeline should progressively optimize toward minimal generation.

Avoid asking an agent to regenerate an entire application when only one
screen changed.

Preferred:

``` text
Existing mockup
+
affected screen
+
system model delta
+
comments
      ↓
small targeted modification
```

Potential implementation:

-   Component-level generation
-   Screen-level files
-   Shared UI component library
-   Structured screen specification
-   JSON state/data definitions
-   Patch-based updates
-   Reuse of existing React components

Eventually:

``` text
mockup/
├── app/
├── screens/
│   ├── dashboard/
│   ├── audit-details/
│   └── finding-details/
├── components/
└── data/
```

An agent can modify only the affected screen/component.

------------------------------------------------------------------------

# 23. Model Provider Abstraction

The pipeline must not hard-code one model vendor.

Conceptual interface:

``` python
class ModelProvider:
    async def generate(
        self,
        messages,
        tools=None,
        output_schema=None
    ):
        ...
```

Provider adapters:

``` text
ModelProvider
 ├── OpenAIProvider
 ├── AnthropicProvider
 ├── GeminiProvider
 └── OtherProvider
```

Canonical agents request capabilities rather than vendors.

Example:

``` yaml
model:
  capability: reasoning
```

rather than:

``` yaml
model: claude-specific-model
```

A model router can resolve the requested capability.

------------------------------------------------------------------------

# 24. MCP Architecture

The Design Pipeline should expose an MCP server.

Example tools:

``` text
design.initialize_project()
design.status()

design.read_brd()
design.generate_brd()

design.build_system_model()
design.validate_system_model()

design.recommend_diagrams()
design.generate_diagrams()

design.plan_mockups()
design.generate_mockups()

design.analyze_change()
design.get_impact()

design.approve_artifact()
design.request_changes()
design.retry_artifact()

design.get_comments()
design.get_artifact_history()
```

This MCP server is the portable interface used by AI harnesses.

------------------------------------------------------------------------

# 25. Design Pipeline as an MCP Host/Client

The Design Pipeline should also be capable of consuming other MCP
servers.

``` text
                 Design Pipeline
                       │
             MCP clients / adapters
             ┌─────────┼─────────┐
             ▼         ▼         ▼
         Mermaid     Figma     GitHub
           MCP        MCP        MCP
```

Potential capabilities:

-   Filesystem
-   Mermaid
-   Browser
-   GitHub
-   Figma
-   Databases
-   Documentation systems
-   Project management tools
-   Future design/research tools

The Design Pipeline MCP is therefore both:

-   An MCP server to its host.
-   An MCP client/host to specialized MCP capabilities.

------------------------------------------------------------------------

# 26. MCP Is Not the Internal Programming Language

Do not turn every internal function into an MCP tool.

Ordinary code should handle:

-   Hashing
-   File comparison
-   Dependency resolution
-   State transitions
-   Schema validation
-   Artifact registry
-   Graph traversal
-   Workflow execution

MCP should be used where an external capability or interoperable tool
boundary is useful.

------------------------------------------------------------------------

# 27. Harness Portability

The desired architecture:

``` text
                 Claude Code
                 VS Code
                 Codex
                 Custom UI
                     │
                     │ MCP
                     ▼
             Design Pipeline MCP
                     │
                     ▼
             Design Pipeline Runtime
```

The harness is not the source of truth.

The project definition and artifacts are.

------------------------------------------------------------------------

# 28. Suggested Project Structure

``` text
design-pipeline/
│
├── server/
│   ├── api/
│   └── mcp/
│
├── orchestrator/
│   ├── workflow.py
│   ├── state_machine.py
│   ├── dependency_graph.py
│   ├── task_manager.py
│   └── artifact_registry.py
│
├── agents/
│   ├── runtime.py
│   ├── loader.py
│   └── prompts.py
│
├── providers/
│   ├── base.py
│   ├── openai.py
│   ├── anthropic.py
│   └── gemini.py
│
├── capabilities/
│   ├── filesystem.py
│   ├── mermaid.py
│   └── browser.py
│
├── schemas/
│   ├── requirements.py
│   ├── business_model.py
│   ├── solution_model.py
│   ├── system_model.py
│   ├── task.py
│   ├── artifact.py
│   ├── comment.py
│   └── workflow.py
│
├── review-app/
│   └── React application
│
└── tests/
```

------------------------------------------------------------------------

# 29. Project-Level Design Directory

Each project using the pipeline should contain:

``` text
.design/
│
├── agents/
│   ├── requirements.yaml
│   ├── architecture.yaml
│   └── ux.yaml
│
├── workflows/
│   └── design-pipeline.yaml
│
├── schemas/
│
├── artifacts/
│   ├── brd/
│   ├── business/
│   ├── solution/
│   ├── system/
│   ├── architecture/
│   ├── diagrams/
│   └── mockups/
│
├── state/
│   ├── project-state.yaml
│   ├── dependency-graph.yaml
│   └── execution-history.jsonl
│
└── review/
    ├── comments/
    └── approvals/
```

------------------------------------------------------------------------

# 30. Pydantic

Python + Pydantic is a strong fit.

Pydantic should define contracts between the components.

Examples:

``` python
class Artifact(BaseModel):
    id: str
    type: str
    version: int
    status: ArtifactStatus
    inputs: list[ArtifactReference]
    requirements: list[str]
```

``` python
class Handoff(BaseModel):
    task_id: str
    source_agent: str
    target_agent: str
    objective: str
    inputs: dict[str, str]
    changes: list[str]
    constraints: list[str]
    expected_outputs: list[str]
```

``` python
class Comment(BaseModel):
    id: str
    artifact_id: str
    text: str
    location: dict | None
    status: str
```

Structured contracts are essential for provider portability and reliable
orchestration.

------------------------------------------------------------------------

# 31. Workflow Definition

Workflows should also be declarative.

Example:

``` yaml
id: initial-design

steps:

  - id: inspect-project
    type: deterministic

  - id: requirements
    agent: requirements-agent
    depends_on:
      - inspect-project

  - id: requirements-approval
    type: human-approval
    depends_on:
      - requirements

  - id: architecture
    agent: architecture-agent
    depends_on:
      - requirements-approval

  - id: architecture-approval
    type: human-approval
    depends_on:
      - architecture

  - id: mockups
    agent: ux-agent
    depends_on:
      - architecture-approval
```

Because the workflow is declarative, steps can eventually be moved,
copied, removed, or reused.

------------------------------------------------------------------------

# 32. Parallelization

Once an approved system model exists, independent outputs can be
generated in parallel.

Example:

``` text
                 System Model
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
        ERD       Architecture  Process
          │           │           │
          └───────────┼───────────┘
                      ▼
                  UI Planning
```

The Workflow Engine should determine whether dependencies permit
parallel execution.

------------------------------------------------------------------------

# 33. Review Application Architecture

Initial implementation:

``` text
FastAPI
   │
   ├── Workflow API
   ├── Artifact API
   ├── Approval API
   ├── Comment API
   └── MCP server
           │
           ▼
      React Review App
```

The review application should consume the same artifact APIs used by
agents.

This avoids building a separate source of truth for the UI.

------------------------------------------------------------------------

# 34. Interactive Diagram Review

Mermaid diagrams should be embedded directly in the review workspace.

Example:

``` text
Architecture
────────────────────────────

        [User]
           │
           ▼
      [Web Client]
           │
           ▼
      [API Server]
        /       \
       ▼         ▼
   [Postgres]  [External API]

[Approve] [Request Changes]
```

Comments should be attachable to the diagram/artifact and, where
practical, to a specific node or region.

------------------------------------------------------------------------

# 35. Interactive Mockup Review

Mockups should run as actual React/HTML screens.

Example:

``` text
┌───────────────────────────────────┐
│ Audit Report                      │
├───────────────────────────────────┤
│ Status: Awaiting Approval         │
│ Risk: HIGH                        │
│                                   │
│ Findings: 7                       │
│                                   │
│ [Approve] [Request Changes]       │
└───────────────────────────────────┘
```

The user can navigate the system rather than only looking at static
screenshots.

------------------------------------------------------------------------

# 36. Feedback Context

When retrying an artifact, the agent should receive only the context
necessary to perform the change.

Conceptually:

``` text
Retry Context
├── Current artifact
├── Artifact specification
├── Relevant requirements
├── Relevant system-model section
├── Open comments
├── Previous generation metadata
└── New user instruction
```

Avoid resending the entire project.

This is central to the token-minimization strategy.

------------------------------------------------------------------------

# 37. Change Impact UI

When a requirement changes, the review application should display:

``` text
Requirement BR-017 changed.

Potentially affected:

☑ Approval Process
☑ Approval Entity
☑ Approval Diagram
☑ Report Approval Mockup
☑ Permission Model

No detected impact:

☐ Authentication
☐ Audit Creation
☐ Dashboard
```

The user can approve the proposed propagation.

------------------------------------------------------------------------

# 38. Auditability

Every important action should be recorded.

Example event stream:

``` text
2026-08-28T...
REQUIREMENTS_GENERATED
artifact=brd
version=4

2026-08-28T...
REQUIREMENTS_APPROVED
artifact=brd
version=4

2026-08-28T...
ARCHITECTURE_GENERATED
version=2

2026-08-28T...
COMMENT_ADDED
artifact=architecture
comment=...

2026-08-28T...
ARCHITECTURE_RETRY
version=3

2026-08-28T...
ARCHITECTURE_APPROVED
version=3
```

This creates a durable design history.

------------------------------------------------------------------------

# 39. Initial State Machine

Suggested artifact states:

``` text
DRAFT
   ↓
GENERATING
   ↓
GENERATED
   ↓
AWAITING_REVIEW
   ├───────────────┐
   │               │
   ▼               ▼
APPROVED      CHANGES_REQUESTED
                   │
                   ▼
                 RETRY
                   │
                   ▼
              GENERATING
```

Failure path:

``` text
GENERATING
    ↓
FAILED
    ↓
RETRY
```

An artifact can be approved while another artifact is failed or awaiting
review.

------------------------------------------------------------------------

# 40. Initial Implementation Phases

## Phase 0 --- Prompt/Workflow Prototype

Use the current coding harness with `.design/agents/*.md` or YAML
definitions.

Prove:

-   Requirements process
-   System-model methodology
-   Diagram selection
-   Mockup generation
-   Review sequence

No complex infrastructure.

## Phase 1 --- Canonical Definitions + Schemas

Implement:

-   Canonical agent definitions
-   Workflow schema
-   Artifact schema
-   Handoff/task schema
-   Comment schema
-   Approval schema
-   Business Model schema
-   Solution Model schema
-   System Model schema

## Phase 2 --- Python Workflow Runtime

Implement:

-   Workflow Engine
-   State machine
-   Artifact registry
-   Dependency graph
-   Task manager
-   Pydantic validation

## Phase 3 --- Model Provider Layer

Implement provider adapters:

-   OpenAI
-   Anthropic
-   Gemini
-   Other providers as needed

The agents remain provider-neutral.

## Phase 4 --- MCP Server

Expose the Design Pipeline as an MCP server.

Support:

-   Project initialization
-   Status
-   Run step
-   Approve
-   Comment
-   Retry
-   Impact analysis
-   Artifact retrieval

## Phase 5 --- MCP Capability Integration

Integrate:

-   Mermaid
-   Filesystem
-   Browser
-   GitHub
-   Other useful MCPs

## Phase 6 --- React Review Workspace

Build:

-   Artifact navigation
-   Mermaid preview
-   Interactive mockups
-   Comments
-   Approvals
-   Retry controls
-   Version history
-   Change impact view

## Phase 7 --- Incremental Change Propagation

Implement:

-   BRD diffing
-   Requirement IDs
-   Dependency graph
-   Impact analysis
-   Selective regeneration

## Phase 8 --- Harness Adapters

Generate/adapt canonical agents into:

``` text
.claude/agents/
.github/agents/
```

and additional harness formats as required.

------------------------------------------------------------------------

# 41. Non-Goals for V1

Do not initially build:

-   Fully autonomous multi-agent conversations.
-   A2A-based distributed agents.
-   Production-ready UI design.
-   Automatic code generation from every mockup.
-   Automatic regeneration of every artifact after every edit.
-   Complex vector databases solely for project memory.
-   Autonomous approval decisions.
-   Production deployment infrastructure for review applications.

The first goal is a reliable requirements-to-design pipeline.

------------------------------------------------------------------------

# 42. A2A

A2A should remain a future interoperability option.

Current architecture:

``` text
Workflow Engine
      │
      ├── Requirements Agent
      ├── Architecture Agent
      └── UX Agent
```

Future architecture could support external independent agents:

``` text
Requirements Agent
      │
      │ A2A
      ▼
External Architecture Agent
      │
      │ A2A
      ▼
External UI Agent
```

Do not introduce A2A into the initial implementation unless independent
remote agent systems become an actual requirement.

------------------------------------------------------------------------

# 43. Key Architectural Decisions

The following decisions are intentional:

### Decision 1

Canonical agent definitions are vendor-neutral.

### Decision 2

Claude Code and VS Code agent files are generated/adapted from canonical
definitions.

### Decision 3

The Workflow Engine is code, not an autonomous supervisor agent.

### Decision 4

Agents perform reasoning.

### Decision 5

MCP provides capabilities and interoperability.

### Decision 6

The Design Pipeline is exposed as an MCP server.

### Decision 7

The Design Pipeline can consume other MCP servers.

### Decision 8

Artifacts are the shared context between agents.

### Decision 9

Structured task/handoff objects are used instead of passing entire
conversations.

### Decision 10

Human approval is a first-class workflow state.

### Decision 11

Comments and feedback are persisted as structured artifacts.

### Decision 12

The dependency graph controls change propagation.

### Decision 13

React/HTML is the canonical mechanism for interactive system mockups.

### Decision 14

Mermaid is used for diagram representation/rendering.

### Decision 15

Pydantic defines internal contracts.

### Decision 16

The Business Model and Solution Model are part of the system-model
creation methodology.

### Decision 17

Workflow steps must be modular and movable.

### Decision 18

The system should optimize for minimal regeneration and minimal token
usage.

------------------------------------------------------------------------

# 44. Target Architecture

``` text
                        USER
                         │
              ┌──────────┼──────────┐
              │          │          │
         Claude Code   VS Code    Custom UI
              │          │          │
              └──────────┼──────────┘
                         │
                        MCP
                         │
              ┌──────────▼──────────┐
              │ Design Pipeline MCP │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  Workflow Engine    │
              │       (code)        │
              └──────────┬──────────┘
                         │
             ┌───────────┼────────────┐
             ▼           ▼            ▼
       Requirements  Architecture    UX
          Agent        Agent        Agent
             │           │            │
             └───────────┼────────────┘
                         │
                  Shared Artifacts
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
       BRD         System Model      Mockup Specs
        │                │                │
        │        ┌───────┼───────┐        │
        │        ▼       ▼       ▼        │
        │     Business Solution Data      │
        │      Model    Model    Model     │
        │                │                │
        └────────────────┼────────────────┘
                         │
                  Dependency Graph
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
      Mermaid MCP     Browser/MCP    Other MCPs
          │              │
          ▼              ▼
      Diagrams       React/HTML
                         │
                         ▼
                 Review Workspace
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
          Comment      Approve      Retry
             │           │           │
             └───────────┼───────────┘
                         ▼
                  Updated Artifacts
```

------------------------------------------------------------------------

# 45. Final Conceptual Separation

The project should preserve these six boundaries:

``` text
Workflow Engine = code
Agents          = reasoning
MCP             = capabilities
Model adapters  = provider portability
Artifacts       = shared context
Dependency graph = change propagation
```

Everything else should fit around these boundaries.

This is the core architecture of the Design Pipeline.

The system should feel like a compiler/toolchain for software design:

``` text
Business Requirements
        ↓
Business Understanding
        ↓
Solution Understanding
        ↓
System Model
        ↓
Design Artifacts
        ↓
Interactive Review
        ↓
Approved Design
```

But unlike a one-shot generator, the output is a **living, traceable
design system**.

Changing a requirement should not mean starting again.

It should mean:

``` text
Requirement changed
       ↓
Impact detected
       ↓
Affected artifacts identified
       ↓
Human review
       ↓
Targeted regeneration
       ↓
New versions
       ↓
Review
       ↓
Approval
```

That is the central product behavior to preserve throughout
implementation.
