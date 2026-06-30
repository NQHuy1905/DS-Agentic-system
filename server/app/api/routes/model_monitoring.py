from fastapi import APIRouter
from app.models.workflow_schemas import WorkflowRequest, WorkflowResponse
from app.core.llm_factory import create_llm
from app.agents.monitoring_agent import run_monitoring_agent

router = APIRouter(prefix="/model-monitoring", tags=["model-monitoring"])


@router.post("/run", response_model=WorkflowResponse)
async def run_model_monitoring(request: WorkflowRequest):
    llm = create_llm(
        request.llm_config.provider,
        request.llm_config.model,
        request.llm_config.api_key,
    )
    result = await run_monitoring_agent(llm, request.payload)
    return WorkflowResponse(workflow="model-monitoring", result=result)
