print("Starting cicd_monitor.py")

import asyncio
import os

import httpx
from dotenv import load_dotenv
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, ListItem, ListView, Static

# Load .env for GITHUB_TOKEN
load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API = "https://api.github.com"

RUNS = []  # Will be filled after repo selection


class RepoList(ListView):
    def __init__(self, repos, **kwargs):
        super().__init__(*[ListItem(Static(repo)) for repo in repos], **kwargs)


class WorkflowList(ListView):
    def __init__(self, workflows, **kwargs):
        super().__init__(
            *[ListItem(Static(workflow["name"])) for workflow in workflows], **kwargs
        )


class RunList(ListView):
    def __init__(self, runs, **kwargs):
        super().__init__(
            *[ListItem(Static(f"{run['name']} [{run['status']}]")) for run in runs],
            **kwargs,
        )


class WrappedLog(Static):
    def __init__(self, *args, **kwargs):
        super().__init__("", *args, **kwargs)
        self.lines = []

    def write(self, text):
        # Split text into lines and append
        for line in str(text).splitlines():
            self.lines.append(line)
        self.update("\n".join(self.lines))

    def clear(self):
        self.lines = []
        self.update("")


class CICDMonitorApp(App):
    BINDINGS = [
        ("left", "focus_prev_column", "Focus previous column"),
        ("right", "focus_next_column", "Focus next column"),
        ("q", "quit", "Quit the TUI"),
        ("t", "trigger_workflow", "Trigger selected workflow"),
        ("r", "rerun_job", "Re-run selected job"),
    ]

    def action_quit(self):
        self.exit()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.columns = ["repo-list", "workflow-list", "run-list"]
        self.focused_column = 0
        self._pending_action = None
        print("CICDMonitorApp __init__ called")

    async def on_mount(self):
        print("CICDMonitorApp mounted")
        # Set initial focus to repo-list
        self.set_focus(self.repo_list)

    def action_focus_prev_column(self):
        if self.focused_column > 0:
            self.focused_column -= 1
            self._focus_column()

    def action_focus_next_column(self):
        if self.focused_column < len(self.columns) - 1:
            self.focused_column += 1
            self._focus_column()

    def _focus_column(self):
        col_id = self.columns[self.focused_column]
        if col_id == "repo-list":
            self.set_focus(self.repo_list)
        elif col_id == "workflow-list":
            self.set_focus(self.workflow_list)
        elif col_id == "run-list":
            self.set_focus(self.run_list)

    # CSS_PATH = "cicd_monitor.tcss"
    selected_repo = reactive(None)
    selected_workflow = reactive(None)
    selected_run = reactive(None)
    repos = reactive([])
    workflows = reactive([])
    runs = reactive([])

    def compose(self) -> ComposeResult:
        print("CICDMonitorApp compose called")
        yield Header()
        with Horizontal():
            with Vertical():
                yield Static("Repositories", classes="title")
                self.repo_list = RepoList(self.repos, id="repo-list")
                yield self.repo_list
            with Vertical():
                yield Static("Workflows", classes="title")
                self.workflow_list = WorkflowList(self.workflows, id="workflow-list")
                yield self.workflow_list
            with Vertical():
                yield Static("Recent Runs", classes="title")
                self.run_list = RunList(self.runs, id="run-list")
                yield self.run_list
            with Vertical():
                yield Static("Logs", classes="title")
                self.log_view = WrappedLog(id="log-view", expand=True)
                yield self.log_view
        yield Footer()

    async def on_mount(self):
        print("CICDMonitorApp mounted")
        await self.load_repos()
        self.repo_list.index = 0
        self.workflow_list.index = 0
        self.run_list.index = 0
        self.update_log_view(0)

    async def load_repos(self):
        from zoneinfo import ZoneInfo

        if not GITHUB_TOKEN:
            self.repos = ["No GITHUB_TOKEN in .env"]
            return
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        repos_with_actions = []
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{GITHUB_API}/user/repos", headers=headers)
            if resp.status_code == 200:
                all_repos = resp.json()
                # Only include repos with at least one workflow
                for repo in all_repos:
                    full_name = repo["full_name"]
                    wf_resp = await client.get(
                        f"{GITHUB_API}/repos/{full_name}/actions/workflows",
                        headers=headers,
                    )
                    if wf_resp.status_code == 200 and wf_resp.json().get("workflows"):
                        repos_with_actions.append(full_name)
                self.repos = repos_with_actions
            else:
                self.repos = [f"Error: {resp.status_code}"]
        self.repo_list.clear()
        for repo in self.repos:
            self.repo_list.append(ListItem(Static(repo)))
        # Auto-load workflows for first repo
        if self.repos:
            await self.load_workflows(self.repos[0])

    async def load_workflows(self, repo_name):
        self.workflows = []
        if not GITHUB_TOKEN or repo_name.startswith("Error"):
            self.workflows = [{"id": 0, "name": "No token or error"}]
        else:
            headers = {
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            }
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{GITHUB_API}/repos/{repo_name}/actions/workflows", headers=headers
                )
                if resp.status_code == 200:
                    self.workflows = [
                        {"id": wf["id"], "name": wf["name"]}
                        for wf in resp.json().get("workflows", [])
                    ]
                else:
                    self.workflows = [{"id": 0, "name": f"Error: {resp.status_code}"}]
        self.workflow_list.clear()
        for wf in self.workflows:
            self.workflow_list.append(ListItem(Static(wf["name"])))
        # Auto-load runs for first workflow
        if self.workflows:
            await self.load_runs(
                repo_name, self.workflows[0]["id"], self.workflows[0]["name"]
            )

    async def load_runs(self, repo_name, workflow_id, workflow_name):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        self.runs = []
        if not GITHUB_TOKEN or repo_name.startswith("Error") or workflow_id == 0:
            self.runs = [
                {
                    "id": 0,
                    "status": "error",
                    "name": "No token or error",
                    "log": "Cannot load runs.",
                }
            ]
        else:
            headers = {
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            }
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{GITHUB_API}/repos/{repo_name}/actions/workflows/{workflow_id}/runs",
                    headers=headers,
                )
                if resp.status_code == 200:
                    for run in resp.json().get("workflow_runs", []):
                        created_at = run.get("created_at", "?")
                        # Format date for EST
                        try:
                            dt_utc = datetime.fromisoformat(
                                created_at.replace("Z", "+00:00")
                            )
                            created_fmt = dt_utc.astimezone(
                                ZoneInfo("America/New_York")
                            ).strftime("%Y-%m-%d %I:%M:%S %p EST")
                        except Exception:
                            created_fmt = created_at
                        self.runs.append(
                            {
                                "id": run["id"],
                                "status": run["conclusion"] or run["status"],
                                "name": workflow_name,
                                "log": f"Run ID: {run['id']}\nStatus: {run['conclusion'] or run['status']}\nDate: {created_fmt}",
                            }
                        )
                else:
                    self.runs = [
                        {
                            "id": 0,
                            "status": "error",
                            "name": "Error loading runs",
                            "log": f"HTTP {resp.status_code}",
                        }
                    ]
        self.run_list.clear()
        for run in self.runs:
            self.run_list.append(ListItem(Static(f"{run['name']} [{run['status']}]")))
        self.update_log_view(0)

    async def on_list_view_highlighted(self, event):
        # This event is called when the selection (highlight) changes
        idx = event.list_view.index
        if idx is None:
            return
        if event.list_view.id == "repo-list":
            repo_name = self.repos[idx]
            await self.load_workflows(repo_name)
        elif event.list_view.id == "workflow-list":
            repo_index = self.repo_list.index
            if repo_index is None or not self.repos:
                return
            repo_name = self.repos[repo_index]
            workflow = self.workflows[idx]
            await self.load_runs(repo_name, workflow["id"], workflow["name"])
        elif event.list_view.id == "run-list":
            self.update_log_view(idx)

    # Keep on_list_view_selected for explicit selection (e.g., Enter key)
    async def on_list_view_selected(self, event):
        if event.list_view.id == "repo-list":
            repo_name = self.repos[event.list_view.index]
            await self.load_workflows(repo_name)
        elif event.list_view.id == "workflow-list":
            repo_index = self.repo_list.index
            repo_name = self.repos[repo_index]
            workflow = self.workflows[event.list_view.index]
            await self.load_runs(repo_name, workflow["id"], workflow["name"])
        elif event.list_view.id == "run-list":
            self.update_log_view(event.list_view.index)

    def update_log_view(self, run_index):
        self.log_view.clear()
        if 0 <= run_index < len(self.runs):
            self.log_view.write(self.runs[run_index]["log"])

    async def action_trigger_workflow(self):
        repo_index = self.repo_list.index
        workflow_index = self.workflow_list.index
        if (
            repo_index is None
            or workflow_index is None
            or repo_index < 0
            or workflow_index < 0
        ):
            self.log_view.write("[ERROR] No repo or workflow selected.")
            return
        repo_name = self.repos[repo_index]
        workflow = self.workflows[workflow_index]
        workflow_id = workflow["id"]
        ref = "main"
        self.log_view.write(
            f"[CONFIRM] Press 'y' to confirm triggering workflow '{workflow['name']}', any other key to cancel."
        )
        self._pending_action = ("trigger", repo_name, workflow)

    async def action_rerun_job(self):
        run_index = self.run_list.index
        if run_index is None or run_index < 0:
            self.log_view.write("[ERROR] No job selected.")
            return
        run = self.runs[run_index]
        self.log_view.write(
            f"[CONFIRM] Press 'y' to confirm re-running job '{run['name']}', any other key to cancel."
        )
        self._pending_action = ("rerun", run)

    async def on_key(self, event):
        if hasattr(self, "_pending_action") and self._pending_action:
            if event.key == "y":
                action = self._pending_action
                self._pending_action = None
                if action[0] == "trigger":
                    repo_name, workflow = action[1], action[2]
                    await self._do_trigger_workflow(repo_name, workflow)
                elif action[0] == "rerun":
                    run = action[1]
                    await self._do_rerun_job(run)
            else:
                self.log_view.write("[INFO] Action canceled.")
                self._pending_action = None

    async def _do_trigger_workflow(self, repo_name, workflow):
        workflow_id = workflow["id"]
        ref = "main"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        self.log_view.write(f"[ACTION] Triggering workflow '{workflow['name']}'...")
        async with httpx.AsyncClient() as client:
            dispatch_url = f"{GITHUB_API}/repos/{repo_name}/actions/workflows/{workflow_id}/dispatches"
            resp = await client.post(dispatch_url, headers=headers, json={"ref": ref})
            if resp.status_code in (201, 204):
                self.log_view.write("[INFO] Workflow dispatch triggered.")
            else:
                self.log_view.write(
                    f"[ERROR] Failed to trigger workflow: HTTP {resp.status_code}\n{resp.text}"
                )

    async def _do_rerun_job(self, run):
        self.log_view.write(f"[ACTION] Re-running job '{run['name']}'...")
        # Implement re-run logic here


if __name__ == "__main__":
    try:
        CICDMonitorApp().run()
    except Exception as e:
        print(f"Exception during app run: {e}")
