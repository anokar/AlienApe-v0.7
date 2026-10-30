"""Controller-owned project-file step adapter (not yet exposed in the UI)."""
from dataclasses import dataclass
from .intent_resolution import IntentID, IntentResolution
from .task_slots import ContentMode, TaskSlots
from .project_plan import ProjectPlan


@dataclass(frozen=True)
class ProjectFileStep:
    target_path: str
    purpose: str
    language_hint: str
    dependencies: tuple[str, ...]
    generation_request: str


def file_steps(plan: ProjectPlan):
    """Translate a validated plan to ordered, exactly targeted file steps.

    This is a pure adapter: it grants no permission and performs no operations.
    The caller must validate the plan immediately before use.
    """
    if not isinstance(plan, ProjectPlan):
        raise TypeError('A validated ProjectPlan is required')
    files = {f.path: f for f in plan.files}
    return tuple(ProjectFileStep(
        target_path=plan.root + '/' + f.path,
        purpose=f.purpose,
        language_hint=f.language,
        dependencies=tuple(plan.root + '/' + d for d in f.depends_on),
        generation_request=plan.goal,
    ) for name in plan.execution_order if (f := files.get(name)) is not None)


def single_file_resolution(step: ProjectFileStep):
    """Create the same explicit CREATE_FILE contract used by ordinary tasks."""
    if not isinstance(step, ProjectFileStep):
        raise TypeError('ProjectFileStep required')
    target = (step.target_path,)
    slots = TaskSlots(intent_id=IntentID.CREATE_FILE, target_paths=target,
        source_paths=step.dependencies, content_mode=ContentMode.GENERATE, generation_request=step.generation_request,
        language_hint=step.language_hint, mutation_requested=True,
        resolution_source='PROJECT_PLAN', slot_sources={'target_path':'CONTROLLER_APPROVED_PLAN'})
    return IntentResolution(intent_id=IntentID.CREATE_FILE, confidence=1.0,
        source='PROJECT_PLAN', referenced_paths=target + step.dependencies, output_paths=target,
        mutation_requested=True, project_scope='files', operations=(IntentID.CREATE_FILE,),
        handled=True, slots=slots)

async def execute_file_step(agent, step):
    """Invoke the ordinary single-file path with controller-owned resolution.

    The project caller is responsible for validating the plan and dependencies;
    this adapter neither grants permissions nor chooses a transport.
    """
    target = agent.workspace.path(step.target_path)
    if target.exists():
        raise FileExistsError('Project file target already exists')
    task = 'Create the approved file. Project goal: ' + step.generation_request[:2000] + '\nPurpose: ' + step.purpose[:500]
    agent.intent_resolution = single_file_resolution(step)
    agent.resolution_task = task
    agent.response_contract = None
    answer = await agent._run(task)
    verification = await agent.verify_written()
    return answer, verification
