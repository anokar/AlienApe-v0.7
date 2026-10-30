"""Bounded project sequencing, built on the existing controller operations."""
from dataclasses import dataclass, field
from .contracts import WriteScope
from .project_plan import validate_plan
from .project_execution import file_steps, execute_file_step


@dataclass
class ProjectFileResult:
    target: str
    write_status: str = 'PENDING'
    verification_status: str = 'NOT_RUN'
    failure_attribution: str = ''
    model: str = ''
    protocol: str = ''


@dataclass
class ProjectBuildResult:
    project_root: str
    plan_status: str
    planned_directories: list[str] = field(default_factory=list)
    created_directories: list[str] = field(default_factory=list)
    failed_directories: list[str] = field(default_factory=list)
    files: list[ProjectFileResult] = field(default_factory=list)
    project_status: str = 'PENDING'
    project_verification: str = 'NOT_RUN'


async def execute_project(agent, raw_plan, root, goal):
    """Validate first, then use existing guarded mkdir and file execution.

    Existing roots must be empty. There is no automatic repair or rollback.
    """
    plan = validate_plan(raw_plan, agent.workspace, root, goal)
    root_path = agent.workspace.path(plan.root)
    if not root_path.parent.is_dir():
        raise FileNotFoundError('Project root parent must already exist')
    if root_path.exists() and (not root_path.is_dir() or any(root_path.iterdir())):
        raise FileExistsError('Existing populated project is not supported')
    steps = file_steps(plan)
    directories = [plan.root] + [plan.root + '/' + p for p in plan.directories]
    result = ProjectBuildResult(plan.root, 'VALIDATED', directories,
                                files=[ProjectFileResult(s.target_path) for s in steps])
    failed_dirs = set()
    for directory in directories:
        if any(directory.startswith(p + '/') for p in failed_dirs):
            failed_dirs.add(directory)
            result.failed_directories.append(directory)
            continue
        agent.emit('PROJECT_DIRECTORY_STEP',{'path':directory,'status':'START'})
        if agent.workspace.path(directory).is_dir():
            continue
        agent.write_scope = WriteScope(False, [directory], True, False, True)
        try:
            await agent.execute_tool('make_directory', {'path':directory}, origin='controller')
            result.created_directories.append(directory)
            agent.emit('PROJECT_DIRECTORY_STEP',{'path':directory,'status':'SUCCESS'})
        except Exception:
            failed_dirs.add(directory)
            result.failed_directories.append(directory)
            agent.emit('PROJECT_DIRECTORY_STEP',{'path':directory,'status':'FAILURE'})
    outcomes = {}
    for step, item in zip(steps, result.files):
        if any(step.target_path.startswith(p + '/') for p in failed_dirs) or any(outcomes.get(p) != 'SUCCESS' for p in step.dependencies):
            item.write_status = 'SKIPPED_DEPENDENCY'
            agent.emit('PROJECT_STEP_SKIPPED',{'path':step.target_path,'reason':'DEPENDENCY'})
            outcomes[step.target_path] = item.write_status
            continue
        item.write_status = 'RUNNING'
        agent.emit('PROJECT_FILE_STEP_START',{'path':step.target_path})
        try:
            answer, verification = await execute_file_step(agent, step)
            item.model = str(getattr(getattr(agent,'current_profile',None),'id','') or getattr(agent.resources,'model',''))
            item.protocol = str(getattr(getattr(agent,'response_contract',None),'mode',''))
            if step.target_path not in agent.written_paths or not agent.workspace.path(step.target_path).is_file():
                item.write_status = 'FAILURE'
                item.failure_attribution = str(agent.last_failure_type)
            else:
                item.write_status = 'SUCCESS'
                item.verification_status = getattr(verification, 'verification_status', 'NOT_RUN') if verification else 'NOT_RUN'
        except Exception:
            item.write_status = 'FAILURE'
            item.failure_attribution = str(agent.last_failure_type)
        item.model = str(getattr(getattr(agent,'current_profile',None),'id','') or getattr(agent.resources,'model',''))
        item.protocol = str(getattr(getattr(agent,'response_contract',None),'mode',''))
        agent.emit('PROJECT_FILE_STEP_RESULT',{'path':step.target_path,'write_status':item.write_status,'verification_status':str(item.verification_status),'attribution':item.failure_attribution,'model':item.model,'protocol':item.protocol})
        outcomes[step.target_path] = ('FAILURE' if item.verification_status in ('FAIL', 'ERROR', 'TIMEOUT', 'ABORTED') else item.write_status)
    result.project_status = 'SUCCESS' if all(x.write_status == 'SUCCESS' and x.verification_status not in ('FAIL', 'ERROR', 'TIMEOUT', 'ABORTED') for x in result.files) else 'FAILURE'
    return result


def summarize_project(result, language="en"):
    from .i18n import localize
    lines = [localize("PROJECT_EXECUTION",language) + ": " + result.project_status, localize("PROJECT_WRITTEN",language) + ":"]
    lines.extend('- ' + f.target + ' (' + localize('PROJECT_FILE_VERIFICATION',language) + ': ' + str(f.verification_status) + ')' for f in result.files if f.write_status == 'SUCCESS')
    lines.append(localize('PROJECT_FAILED_SKIPPED',language) + ':')
    lines.extend('- ' + f.target + ': ' + f.write_status for f in result.files if f.write_status != 'SUCCESS')
    lines.append(localize('PROJECT_VERIFICATION',language) + ': ' + result.project_verification)
    return '\n'.join(lines)
