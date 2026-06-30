from fastapi import APIRouter
from app.models.workflow_schemas import WorkflowRequest, WorkflowResponse
from app.core.llm_factory import create_llm
from app.agents.hyperparam_agent import run_hyperparam_agent

router = APIRouter(prefix="/hyperparam-opt", tags=["hyperparam-opt"])


@router.post("/run", response_model=WorkflowResponse)
async def run_hyperparam_opt(request: WorkflowRequest):
    llm = create_llm(
        request.llm_config.provider,
        request.llm_config.model,
        request.llm_config.api_key,
    )
    result = await run_hyperparam_agent(llm, request.payload)
    return WorkflowResponse(workflow="hyperparam-opt", result=result)
