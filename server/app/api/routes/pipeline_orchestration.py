from fastapi import APIRouter
from app.models.workflow_schemas import WorkflowRequest, WorkflowResponse
from app.core.llm_factory import create_llm
from app.agents.pipeline_agent import run_pipeline_agent

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post("/run", response_model=WorkflowResponse)
async def run_pipeline_orchestration(request: WorkflowRequest):
    llm = create_llm(
        request.llm_config.provider,
        request.llm_config.model,
        request.llm_config.api_key,
    )
    result = await run_pipeline_agent(llm, request.payload)
    return WorkflowResponse(workflow="pipeline", result=result)
