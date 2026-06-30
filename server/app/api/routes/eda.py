from fastapi import APIRouter
from app.models.workflow_schemas import WorkflowRequest, WorkflowResponse
from app.core.llm_factory import create_llm
from app.agents.eda_agent import run_eda_agent

router = APIRouter(prefix="/eda", tags=["eda"])


@router.post("/run", response_model=WorkflowResponse)
async def run_eda(request: WorkflowRequest):
    llm = create_llm(
        request.llm_config.provider,
        request.llm_config.model,
        request.llm_config.api_key,
    )
    result = await run_eda_agent(llm, request.payload)
    return WorkflowResponse(workflow="eda", result=result)
