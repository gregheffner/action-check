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
        # Repos are passed in as full_name (e.g. "user/repo"); only show the repo name.
        super().__init__(
            *[
                ListItem(Static(repo.split("/")[-1] if isinstance(repo, str) else repo))
                for repo in repos
            ],
            **kwargs,
        )


class WorkflowList(ListView):
    def __init__(self, workflows, **kwargs):
        super().__init__(
            *[ListItem(Static(workflow["name"])) for workflow in workflows], **kwargs
        )


class RunList(ListView):
    def __init__(self, runs, **kwargs):
        super().__init__(
            *[
                ListItem(
                    Static(
                        f"{run.get('created', run.get('name', '?'))} [{run['status']}]"
                    )
                )
                for run in runs
            ],
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
    # Helper for pretty column headers
    @staticmethod
    def pretty_header(text, width=22, color="magenta"):
        line = "─" * width
        pad = max((width - len(text)) // 2, 0)
        centered = (
            " " * pad + f"[{color} bold]{text}[/]" + " " * (width - len(text) - pad)
        )
        return f"{line}\n{centered}\n{line}"

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

    def compose(self) -> ComposeResult:
        """Compose the UI layout and dynamic banner."""
        from rich.console import Console

        console = Console()
        try:
            banner_width = console.size.width
        except Exception:
            banner_width = 80
        banner_width = max(banner_width, 60)  # Minimum width for aesthetics

        app_name = "CICDMonitorApp"
        author = "Greg Heffner"
        repo_url = "https://github.com/gregheffner/action-check"
        app_name_str = f"[bold magenta]{app_name}[/]"
        author_str = f"[cyan]Created by {author}[/]"
        repo_str = f"[yellow]Repo: {repo_url}[/]"

        def center_text(text: str, width: int) -> str:
            import re

            plain = re.sub(r"\[.*?\]", "", text)
            pad = max((width - len(plain)) // 2, 0)
            return " " * pad + text + " " * (width - len(plain) - pad)

        top = "╔" + "═" * (banner_width - 2) + "╗"
        mid = (
            "║"
            + center_text(app_name_str, banner_width - 2)
            + "║\n"
            + "║"
            + center_text(author_str, banner_width - 2)
            + "║\n"
            + "║"
            + center_text(repo_str, banner_width - 2)
            + "║"
        )
        bot = "╚" + "═" * (banner_width - 2) + "╝"
        banner_text = f"{top}\n{mid}\n{bot}"

        yield Static(banner_text, classes="banner", expand=False, markup=True)
        yield Header()

        with Horizontal():
            with Vertical():
                yield Static(
                    self.pretty_header("Repositories", 22, "magenta"),
                    classes="title",
                    markup=True,
                )
                self.repo_list = RepoList(self.repos, id="repo-list")
                yield self.repo_list
            with Vertical():
                yield Static(
                    self.pretty_header("Workflows", 22, "cyan"),
                    classes="title",
                    markup=True,
                )
                self.workflow_list = WorkflowList(self.workflows, id="workflow-list")
                yield self.workflow_list
            with Vertical():
                yield Static(
                    self.pretty_header("Recent Runs", 22, "yellow"),
                    classes="title",
                    markup=True,
                )
                self.run_list = RunList(self.runs, id="run-list")
                yield self.run_list
            with Vertical():
                yield Static(
                    self.pretty_header("Logs", 22, "green"),
                    classes="title",
                    markup=True,
                )
                self.log_view = WrappedLog(id="log-view", expand=True)
                yield self.log_view

        yield Footer()

    # CSS_PATH = "cicd_monitor.tcss"
    selected_repo = reactive(None)
    selected_workflow = reactive(None)
    selected_run = reactive(None)
    repos = reactive([])
    workflows = reactive([])
    runs = reactive([])

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
            # Display only the short repo name (part after '/'),
            # but keep the full name in self.repos for API calls.
            if isinstance(repo, str) and "/" in repo and not repo.startswith("Error"):
                display = repo.split("/")[-1]
            else:
                display = repo
            self.repo_list.append(ListItem(Static(display)))
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
                                "created": created_fmt,
                                "log": f"Run ID: {run['id']}\nStatus: {run['conclusion'] or run['status']}",
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
            self.run_list.append(
                ListItem(Static(f"{run.get('created', run['name'])} [{run['status']}]"))
            )
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
            await self.show_detailed_log(idx, open_browser=True)

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
            await self.show_detailed_log(event.list_view.index, open_browser=True)

    def update_log_view(self, run_index):
        self.log_view.clear()
        if 0 <= run_index < len(self.runs):
            self.log_view.write(self.runs[run_index]["log"])

    async def show_detailed_log(self, run_index, open_browser: bool = False):
        """Show detailed information for a run and print its GitHub URL."""

        self.log_view.clear()

        if not (0 <= run_index < len(self.runs)):
            return

        run = self.runs[run_index]
        repo_index = self.repo_list.index
        repo_name = self.repos[repo_index] if repo_index is not None else None
        run_id = run["id"]

        details = None
        if repo_name and run_id:
            try:
                details = await self.fetch_run_details(repo_name, run_id)
            except Exception as e:
                self.log_view.write(f"[ERROR] Could not fetch details: {e}")

        log_text = run["log"]

        if details:
            log_text += f"\nActor: {details.get('actor', '?')}"
            log_text += f"\nDuration: {details.get('duration', '?')}"
            if details.get("steps"):
                log_text += "\nSteps:"
                for step in details["steps"]:
                    log_text += (
                        f"\n- {step['name']}: {step['status']}"
                        f" ({step.get('conclusion', '')})"
                    )
                    if step.get("error"):
                        log_text += f"\n  Error: {step['error']}"
        else:
            log_text += "\n[INFO] No extra details available."

        if repo_name and run_id:
            log_url = f"https://github.com/{repo_name}/actions/runs/{run_id}"
            log_text += (
                f"\n[INFO] Log URL: {log_url}\n"
                "Copy and paste this URL into your browser."
            )

        self.log_view.write(log_text)

    async def fetch_run_details(self, repo_name: str, run_id: int):
        """Fetch additional details for a workflow run.

        Returns a dict with optional keys: actor, duration, steps.
        """

        if not GITHUB_TOKEN:
            return None

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }

        url = f"{GITHUB_API}/repos/{repo_name}/actions/runs/{run_id}/jobs"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers)
        except Exception as exc:
            self.log_view.write(
                f"[WARN] Failed to contact GitHub for run details: {exc}"
            )
            return None

        if resp.status_code != 200:
            self.log_view.write(
                f"[WARN] Could not fetch job details: HTTP {resp.status_code}"
            )
            return None

        data = resp.json() or {}
        jobs = data.get("jobs") or []
        if not jobs:
            return None

        job = jobs[0]

        # Derive actor information if available
        actor = job.get("runner_name") or job.get("runner_group_name") or "?"

        # Compute duration from started_at/completed_at
        from datetime import datetime

        started_at = job.get("started_at")
        completed_at = job.get("completed_at")
        duration = "?"
        if started_at and completed_at:
            try:
                start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                delta = end_dt - start_dt
                total_seconds = int(delta.total_seconds())
                minutes, seconds = divmod(max(total_seconds, 0), 60)
                if minutes:
                    duration = f"{minutes}m {seconds}s"
                else:
                    duration = f"{seconds}s"
            except Exception:
                duration = "?"

        steps_data = []
        for step in job.get("steps") or []:
            failure_details = step.get("failure_details") or {}
            steps_data.append(
                {
                    "name": step.get("name", "?"),
                    "status": step.get("status", "?"),
                    "conclusion": step.get("conclusion"),
                    "error": failure_details.get("message"),
                }
            )

        return {"actor": actor, "duration": duration, "steps": steps_data}

    async def _do_rerun_job(self, run):
        self.log_view.write(f"[ACTION] Re-running job '{run['name']}'...")
        # Implement re-run logic here


if __name__ == "__main__":
    try:
        CICDMonitorApp().run()
    except Exception as e:
        print(f"Exception during app run: {e}")
