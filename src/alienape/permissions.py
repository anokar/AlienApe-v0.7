import json

class PermissionManager:
    """Only write/mkdir are grantable. Session grants are scoped to exact paths."""
    BLOCKED = {"run_python", "run_command", "delete_file", "outside_workspace", "network_other_than_model_server"}

    def __init__(self, rules=None):
        self.rules = dict(rules or {})
        self.grants = set()

    def bind(self, workspace, task):
        context=(str(workspace),str(task))
        if getattr(self,"context",None)!=context:
            self.grants.clear();self.context=context

    async def request(self, name, arguments, path, ask):
        if name in self.BLOCKED or name not in {"read_file", "list_files", "search_files", "write_file", "make_directory",'verify_python_tests','verify_godot'}:
            return False
        if name in {"read_file", "list_files", "search_files"}:
            return True
        rule = self.rules.get(name, "ask")
        if rule == "block":
            return False
        key = (name, str(path), json.dumps(arguments,sort_keys=True) if name.startswith("verify_") else "")
        if rule == "auto" or key in self.grants:
            return True
        result = await ask(name, arguments, path)
        if result == "session":
            self.grants.add(key)
        return result in {"once", "session"}
