# Project 13 n8n Cloud Implementation Guide

## What this gives you
- A safe n8n workflow that calls your backend orchestrator endpoint.
- Preserves existing backend behavior (no backend logic changes).
- Supports both build-time testing and production triggering.

## Files created
- Workflow JSON: [.github/p13-n8n-workflow.json](.github/p13-n8n-workflow.json)
- This guide: [.github/p13-n8n-implementation-guide.md](.github/p13-n8n-implementation-guide.md)

## Why this is the safest approach
Your backend already implements all P13 requirements in one endpoint:
- POST /api/workflows/research-image-run
- Authentication via access_token cookie
- Research digest generation
- Digest storage in chat thread
- Image generation with fallback
- Optional Slack delivery
- Full workflow_runs logging

This exists in:
- [backend/app/api/workflows.py](backend/app/api/workflows.py)
- [backend/app/services/workflow_orchestrator_service.py](backend/app/services/workflow_orchestrator_service.py)

## Trigger decision (your earlier question)
Use both:
1. Manual Trigger: keep for repeatable testing.
2. Webhook Trigger: use for production from your app.

About your sample "When chat message received" trigger:
- Keep it as a sandbox sample.
- Do not make it your primary production trigger unless your real source is n8n Chat UI events.
- If needed later, wire that trigger to the same Normalize Input node.

## Import steps in n8n Cloud
1. Open n8n Cloud.
2. Create New Workflow.
3. Use Import from File and choose [.github/p13-n8n-workflow.json](.github/p13-n8n-workflow.json).
4. Save as Project13 - Research Digest + Image.

## First-time required edits after import
In node Normalize Input:
- backend_base_url
- service_email
- service_password
- chat_thread_id (for test)

Recommended: pass these from Webhook payload in production and do not hardcode secrets.

## Expected production payload to Webhook Trigger
~~~json
{
  "backend_base_url": "https://your-backend-host",
  "service_email": "service-user@domain.com",
  "service_password": "your-password",
  "chat_thread_id": "00000000-0000-0000-0000-000000000000",
  "user_request": "Research AI alignment, summarize in chat, generate image of concept",
  "topic": "AI alignment",
  "max_rounds": 3,
  "papers_per_round": 5,
  "min_papers": 6,
  "num_images": 1,
  "aspect_ratio": "1:1",
  "image_prompt": "",
  "send_slack_dm": false
}
~~~

## How the imported workflow runs
1. Trigger (Manual or Webhook)
2. Normalize Input
3. Login to backend and extract cookie
4. Call POST /api/workflows/research-image-run
5. If completed/completed_with_errors:
   - Fetch thread messages
   - Build final payload
   - Branch on image existence
6. Return final output as either:
   - success_with_image
   - success_text_only

## Error handling behavior
- Auth failure path returns failed_auth.
- Orchestrator non-complete path returns failed_workflow.
- Backend still logs step-level failures and may return completed_with_errors.
- This matches requirement: failures logged, flow continues where possible.

## Validation checklist
1. Run via Manual Trigger with test data.
2. Verify run_id is returned.
3. Verify status is completed or completed_with_errors.
4. Verify digest message appears in the same thread.
5. Verify image URL is present when image exists.
6. Verify text fallback when image generation fails.
7. Verify workflow_runs row exists for each run.

## Optional improvements after baseline is stable
1. Add Slack node in n8n to send final digest + image URL.
2. Add Error Trigger workflow for operational alerts.
3. Move secrets to n8n Variables or Credentials-only pattern.
4. Replace test chat_thread_id with dynamic thread lookup from app context.

## Note on your p13 requirements file
Your current [.github/p13-n8n-project-requirements.md](.github/p13-n8n-project-requirements.md) appears to contain binary/document content instead of plain markdown text. If you want, I can help convert it into clean markdown so it becomes easier to maintain.
