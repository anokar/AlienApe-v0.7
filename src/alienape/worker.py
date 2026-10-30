import asyncio
import threading
import httpx
from .security import redactor
from .i18n import localize
from PyQt6.QtCore import QThread, pyqtSignal


def user_error(exc, language="system"):
    if isinstance(exc, httpx.ConnectError):
        return localize("SERVER_CONNECT_ERROR",language)
    if isinstance(exc, httpx.TimeoutException):
        return localize("SERVER_TIMEOUT_ERROR",language)
    if isinstance(exc, httpx.HTTPStatusError):
        return localize("SERVER_HTTP_ERROR",language,status=exc.response.status_code)
    return redactor.text(str(exc)) or type(exc).__name__


class AgentWorker(QThread):
    answer = pyqtSignal(str)
    failure = pyqtSignal(str)
    activity = pyqtSignal(object)
    permission_needed = pyqtSignal(object)
    models_ready = pyqtSignal(object)

    def __init__(self, *, agent=None, task="", backend=None, log=None, permissions=None):
        super().__init__()
        self.agent, self.task, self.backend = agent, task, backend
        self.log, self.permissions = log, permissions
        self.stopped = agent.stopped if agent else threading.Event()
        self._loop = self._task = self._permission_future = None

    def observe(self, kind, details=None, step=0, resources=None):
        trace=getattr(self.agent,'diagnostic_trace',None) if self.agent else None
        self.activity.emit(self.log.write(kind, details, step, resources,**({'diagnostic_trace_id':trace.trace_id} if trace else {})))

    async def ask(self, name, arguments, path):
        if getattr(self.agent,'diagnostic_trace',None):self.agent.diagnostic_trace.record('PERMISSION_WAIT',{'name':name,'path':str(path),'status':'WAITING'})
        self._permission_future = asyncio.get_running_loop().create_future()
        self.permission_needed.emit({"name": name, "arguments": arguments, "path": str(path)})
        try:
            return await self._permission_future
        finally:
            self._permission_future = None

    async def authorize(self, name, arguments, path):
        if self.agent:self.permissions.bind(self.agent.workspace.root,self.agent.task_id)
        allowed=await self.permissions.request(name, arguments, path, self.ask)
        if getattr(self.agent,'diagnostic_trace',None):self.agent.diagnostic_trace.record('PERMISSION_RESULT',{'name':name,'path':str(path),'allowed':allowed,'rule':self.permissions.rules.get(name,'ask')})
        return allowed

    def resolve_permission(self, decision):
        if getattr(self.agent,'diagnostic_trace',None):self.agent.diagnostic_trace.record('PERMISSION_DECISION',{'decision':decision})
        def resolve():
            if self._permission_future and not self._permission_future.done():
                self._permission_future.set_result(decision)
        try:
            if self._loop:
                self._loop.call_soon_threadsafe(resolve)
        except RuntimeError:
            pass

    def cancel(self):
        self.stopped.set()
        try:
            if self._loop and self._task:
                self._loop.call_soon_threadsafe(self._task.cancel)
        except RuntimeError:
            pass

    def run(self):
        async def work():
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
            if self.stopped.is_set():
                raise asyncio.CancelledError()
            if self.agent:
                self.agent.observe = self.observe
                self.agent.permission = self.authorize
                if getattr(self.agent,'diagnostic_store',None):
                    self.agent.diagnostic_session_id=self.log.session_id
                    self.agent.diagnostic_project_id=self.log.project_id
                self.answer.emit(redactor.text(await self.agent.run(self.task)))
            else:
                models = await self.backend.health_check()
                self.models_ready.emit({"models": models, "capabilities": self.backend.get_capabilities()})
        try:
            asyncio.run(work())
        except asyncio.CancelledError:
            if getattr(self.agent,'diagnostic_trace',None):self.agent.diagnostic_trace.record('FINAL',{'text':'Gestoppt. Weitere Werkzeugaufrufe wurden verhindert.','source':'ERROR_HANDLER','failure_classification':str(self.agent.last_failure_type)})
            self.answer.emit("Gestoppt. Weitere Werkzeugaufrufe wurden verhindert.")
        except Exception as exc:
            language=getattr(getattr(getattr(self.agent,'settings',None),'ui',None),'language','system')
            if getattr(self.agent,'diagnostic_trace',None):self.agent.diagnostic_trace.record('FINAL',{'text':user_error(exc,language),'source':'ERROR_HANDLER','failure_classification':str(self.agent.last_failure_type)},force=True)
            if not self.agent and self.log:
                self.observe("ERROR", {"message": str(exc), "type": type(exc).__name__})
            self.failure.emit(user_error(exc,language))
        finally:
            self._loop = self._task = None
