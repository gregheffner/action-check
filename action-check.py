import asyncio
import os
import random
import string

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


def remove_emojis(text: str) -> str:
    """Return *text* with common emoji characters removed.

    GitHub Action step names often include emoji; the TUI log view should
    render plain text only.
    """

    if not isinstance(text, str):
        return ""

    cleaned = "".join(
        ch
        for ch in text
        if not (
            0x1F300 <= ord(ch) <= 0x1FAFF
            or 0x1F600 <= ord(ch) <= 0x1F64F
            or 0x2600 <= ord(ch) <= 0x26FF
            or 0x2700 <= ord(ch) <= 0x27BF
        )
    )
    return cleaned.strip()


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


class MatrixBanner(Static):
    """Animated "Matrix"-style banner with static centered text.

    The interior area shows falling characters while the author and
    repository lines remain readable in the middle of the box.
    """

    def __init__(self, width: int, height: int, author: str, repo: str) -> None:
        super().__init__("", classes="banner", expand=False, markup=True)
        # Ensure minimum sensible dimensions
        self.width = max(width, 60)
        # Height must allow top+bottom borders, two text lines, and matrix rows
        self.height = max(height, 7)

        self.inner_width = self.width - 2
        self.inner_height = self.height - 2

        self.author_str = author
        self.repo_str = repo

        # Choose rows (inside the box) for the static text
        center = self.inner_height // 2
        self.author_row = max(0, center - 1)
        self.repo_row = min(self.inner_height - 1, center)

        # Character set for the matrix effect
        self._charset = "01" + string.ascii_uppercase

        # Tail length controls how long each vertical stream remains visible.
        # Use the full interior height so streams can travel top-to-bottom.
        self.tail_length = self.inner_height

        # Persistent character and age grids for the interior area. We shift
        # these downward every frame to create clear vertical motion.
        self._chars = [
            [" " for _ in range(self.inner_width)] for _ in range(self.inner_height)
        ]
        self._ages = [
            [self.tail_length for _ in range(self.inner_width)]
            for _ in range(self.inner_height)
        ]

    def on_mount(self) -> None:  # type: ignore[override]
        # Update the banner about 10 times per second
        self.set_interval(0.1, self.animate)

    def _center_text(self, text: str) -> str:
        import re

        # Strip Rich markup to compute visual width
        plain = re.sub(r"\[.*?\]", "", text)
        pad = max((self.inner_width - len(plain)) // 2, 0)
        return " " * pad + text + " " * max(self.inner_width - len(plain) - pad, 0)

    def animate(self) -> None:
        # Shift existing characters down one row to create vertical motion.
        for y in range(self.inner_height - 1, 0, -1):
            for x in range(self.inner_width):
                self._chars[y][x] = self._chars[y - 1][x]
                self._ages[y][x] = self._ages[y - 1][x] + 1

        # Spawn new heads at the top row with some probability per column.
        for x in range(self.inner_width):
            if random.random() < 0.35:
                self._chars[0][x] = random.choice(self._charset)
                self._ages[0][x] = 0
            else:
                # Age out any previous character at the top
                self._ages[0][x] = self.tail_length

        # Build a frame from the current chars/ages with appropriate colors.
        frame = [
            [" " for _ in range(self.inner_width)] for _ in range(self.inner_height)
        ]

        for y in range(self.inner_height):
            for x in range(self.inner_width):
                ch = self._chars[y][x]
                age = self._ages[y][x]
                if not ch or age >= self.tail_length:
                    continue
                if age == 0:
                    frame[y][x] = f"[bright_green]{ch}[/]"
                else:
                    frame[y][x] = f"[green]{ch}[/]"

        lines = []

        # Top border
        lines.append("╔" + "═" * self.inner_width + "╗")

        # Interior
        import re

        for row in range(self.inner_height):
            row_cells = frame[row][:]

            if row == self.author_row or row == self.repo_row:
                text = self.author_str if row == self.author_row else self.repo_str
                # Compute starting column using plain length (without markup)
                plain = re.sub(r"\[.*?\]", "", text)
                start = max((self.inner_width - len(plain)) // 2, 0)
                start = min(start, max(self.inner_width - len(plain), 0))

                # Left side retains matrix characters
                left = "".join(row_cells[:start])
                # Right side also keeps matrix characters after the text span
                right = "".join(row_cells[start + len(plain) :])
                inner = left + text + right
            else:
                inner = "".join(row_cells)

            lines.append("║" + inner + "║")

        # Bottom border
        lines.append("╚" + "═" * self.inner_width + "╝")

        self.update("\n".join(lines))


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

    def action_trigger_workflow(self):
        """Key binding: trigger the currently selected workflow run on GitHub."""

        repo_index = getattr(self.repo_list, "index", None)
        workflow_index = getattr(self.workflow_list, "index", None)
        if (
            repo_index is None
            or workflow_index is None
            or not self.repos
            or not self.workflows
        ):
            self.log_view.write("[WARN] No repository or workflow selected.")
            return

        repo_name = self.repos[repo_index]
        workflow = self.workflows[workflow_index]
        if not isinstance(workflow, dict) or workflow.get("id") in (None, 0):
            self.log_view.write("[WARN] Selected workflow cannot be triggered.")
            return

        asyncio.create_task(
            self._do_trigger_workflow(
                repo_name=repo_name,
                workflow_id=workflow["id"],
                workflow_name=workflow.get("name", "(unnamed)"),
            )
        )

    def action_rerun_job(self):
        """Key binding: re-run the currently selected job (workflow run)."""

        run_index = getattr(self.run_list, "index", None)
        if run_index is None or not self.runs:
            self.log_view.write("[WARN] No run selected to re-run.")
            return

        run = self.runs[run_index]
        asyncio.create_task(self._do_rerun_job(run))

    def _focus_column(self):
        col_id = self.columns[self.focused_column]
        if col_id == "repo-list":
            self.set_focus(self.repo_list)
        elif col_id == "workflow-list":
            self.set_focus(self.workflow_list)
        elif col_id == "run-list":
            self.set_focus(self.run_list)

    def compose(self) -> ComposeResult:
        """Compose the UI layout and dynamic, animated banner."""
        from rich.console import Console

        console = Console()
        try:
            term_size = console.size
            banner_width = term_size.width
            term_height = term_size.height
        except Exception:
            banner_width = 80
            term_height = 24
        banner_width = max(banner_width, 60)

        # Make the banner tall enough that vertical motion is obvious,
        # but not so tall that it dominates smaller terminals.
        banner_height = max(9, min(14, term_height // 3))

        # Derive a reasonable column header width from the banner/terminal width
        # so that column names appear visually centered within their panes.
        # Keep a sensible minimum to avoid overly narrow headers.
        column_width = max((banner_width // 4) - 4, 22)

        author_str = "[cyan]Created by Greg Heffner[/]"
        repo_str = "[yellow]Repo: https://github.com/gregheffner/action-check[/]"

        yield MatrixBanner(
            width=banner_width,
            height=banner_height,
            author=author_str,
            repo=repo_str,
        )
        yield Header()

        with Horizontal():
            with Vertical():
                yield Static(
                    self.pretty_header("Repositories", column_width, "magenta"),
                    classes="title",
                    markup=True,
                )
                self.repo_list = RepoList(self.repos, id="repo-list")
                yield self.repo_list
            with Vertical():
                yield Static(
                    self.pretty_header("Workflows", column_width, "cyan"),
                    classes="title",
                    markup=True,
                )
                self.workflow_list = WorkflowList(self.workflows, id="workflow-list")
                yield self.workflow_list
            with Vertical():
                yield Static(
                    self.pretty_header("Recent Runs", column_width, "yellow"),
                    classes="title",
                    markup=True,
                )
                self.run_list = RunList(self.runs, id="run-list")
                yield self.run_list
            with Vertical():
                yield Static(
                    self.pretty_header("Logs", column_width, "green"),
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
        """Show detailed information for a run in a compact, readable format."""

        self.log_view.clear()

        if not (0 <= run_index < len(self.runs)):
            return

        run = self.runs[run_index]
        repo_index = self.repo_list.index
        repo_name = self.repos[repo_index] if repo_index is not None else None
        run_id = run["id"]

        # Base run status used for the header; may be overridden by
        # fresher data from fetch_run_details (job conclusion).
        raw_status = str(run.get("status", run.get("conclusion", "?")) or "?")

        details = None
        if repo_name and run_id:
            try:
                details = await self.fetch_run_details(repo_name, run_id)
            except Exception as e:
                self.log_view.write(f"[ERROR] Could not fetch details: {e}")

        # Prefer job-level conclusion when available so status is up to date.
        if details and details.get("job_conclusion"):
            raw_status = str(details.get("job_conclusion") or raw_status)

        status_lower = raw_status.lower()
        if status_lower in ("success", "completed"):
            status_color, status_label = "green", "SUCCESS"
        elif status_lower in ("failure", "failed", "cancelled", "timed_out"):
            status_color, status_label = "red", "FAILURE"
        elif status_lower in ("neutral", "skipped", "action_required"):
            status_color, status_label = (
                "yellow",
                status_lower.replace("_", " ").upper(),
            )
        else:
            status_color, status_label = "cyan", raw_status.upper()

        actor = details.get("actor", "?") if details else "?"
        duration = details.get("duration", "?") if details else "?"

        lines = []
        # Compact header line
        header = (
            f"[bold]Run ID:[/] {run_id}    "
            f"[bold]Status:[/] [{status_color}]{status_label}[/]    "
            f"[bold]Actor:[/] {actor}    "
            f"[bold]Duration:[/] {duration}"
        )
        lines.append(header)

        steps = details.get("steps") if details else None
        if steps:
            lines.append("")
            lines.append("[bold underline]Steps[/bold underline]:")
            for step in steps:
                raw_name = step.get("name", "?")
                name = remove_emojis(raw_name) or "?"
                step_status_raw = step.get("conclusion") or step.get("status") or "?"
                step_status_str = str(step_status_raw)
                step_lower = step_status_str.lower()
                if step_lower in ("success", "completed"):
                    step_color, step_label = "green", "SUCCESS"
                elif step_lower in (
                    "failure",
                    "failed",
                    "cancelled",
                    "timed_out",
                ):
                    step_color, step_label = "red", "FAILURE"
                elif step_lower in ("neutral", "skipped", "action_required"):
                    step_color, step_label = (
                        "yellow",
                        step_lower.replace("_", " ").upper(),
                    )
                else:
                    step_color, step_label = "cyan", step_status_str.upper()

                line = f"- {name}: [{step_color}]{step_label}[/]"
                if step.get("error"):
                    line += f" - [red]{step['error']}[/]"
                lines.append(line)
        else:
            lines.append("")
            lines.append("[INFO] No step details available.")

        if repo_name and run_id:
            log_url = f"https://github.com/{repo_name}/actions/runs/{run_id}"
            lines.append("")
            lines.append("─" * 40)
            lines.append(f"[bold]Log URL:[/] {log_url}")
            lines.append("")
            lines.append("Copy and paste this URL into your browser.")

        self.log_view.write("\n".join(lines))

        # Update in-memory status and the label in the runs list so the
        # column no longer shows stale states like "queued" once the job
        # has completed.
        try:
            # Mutate stored run data
            self.runs[run_index]["status"] = raw_status

            # Update visual label for this run list item
            label = f"{run.get('created', run.get('name', '?'))} [{raw_status}]"
            item = self.run_list.children[run_index]
            # The text widget should be the first (and only) child
            text_widget = item.children[0]
            if isinstance(text_widget, Static):
                text_widget.update(label)
        except Exception:
            # If for any reason the UI structure is different, ignore; the
            # header still shows the correct, up-to-date status.
            pass

    async def _do_trigger_workflow(
        self, repo_name: str, workflow_id: int, workflow_name: str
    ):
        """Dispatch a new run of the given workflow via GitHub's API."""

        if not GITHUB_TOKEN:
            self.log_view.write(
                "[ERROR] GITHUB_TOKEN is not configured; cannot trigger."
            )
            return

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }

        self.log_view.write(
            f"[ACTION] Triggering workflow '{workflow_name}' for repo '{repo_name}'..."
        )

        default_ref = "main"
        try:
            async with httpx.AsyncClient() as client:
                # Try to discover the default branch for the repo
                try:
                    repo_resp = await client.get(
                        f"{GITHUB_API}/repos/{repo_name}", headers=headers
                    )
                    if repo_resp.status_code == 200:
                        default_ref = repo_resp.json().get("default_branch", "main")
                except Exception:
                    # Fallback to "main" on any error here
                    pass

                dispatch_url = f"{GITHUB_API}/repos/{repo_name}/actions/workflows/{workflow_id}/dispatches"
                resp = await client.post(
                    dispatch_url, headers=headers, json={"ref": default_ref}
                )

            if resp.status_code in (200, 201, 202, 204):
                self.log_view.write("[INFO] Workflow dispatch accepted by GitHub.")
                # Refresh runs for the active workflow so the new run shows up soon.
                workflow_index = getattr(self.workflow_list, "index", None)
                if workflow_index is not None and 0 <= workflow_index < len(
                    self.workflows
                ):
                    wf = self.workflows[workflow_index]
                    await self.load_runs(repo_name, wf["id"], wf.get("name", ""))
            else:
                self.log_view.write(
                    f"[ERROR] Failed to trigger workflow: HTTP {resp.status_code} - {resp.text}"
                )
        except Exception as exc:
            self.log_view.write(f"[ERROR] Exception while triggering workflow: {exc}")

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

        return {
            "actor": actor,
            "duration": duration,
            "steps": steps_data,
            "job_conclusion": job.get("conclusion"),
        }

    async def _do_rerun_job(self, run):
        """Request a re-run of the selected workflow run via GitHub's API."""

        if not GITHUB_TOKEN:
            self.log_view.write(
                "[ERROR] GITHUB_TOKEN is not configured; cannot re-run."
            )
            return

        repo_index = getattr(self.repo_list, "index", None)
        if repo_index is None or not self.repos:
            self.log_view.write("[WARN] No repository selected for re-run.")
            return

        repo_name = self.repos[repo_index]
        run_id = run.get("id")
        if not run_id:
            self.log_view.write("[WARN] Selected run has no valid ID.")
            return

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }

        self.log_view.write(
            f"[ACTION] Requesting re-run for workflow run {run_id} in '{repo_name}'..."
        )

        try:
            async with httpx.AsyncClient() as client:
                url = f"{GITHUB_API}/repos/{repo_name}/actions/runs/{run_id}/rerun"
                resp = await client.post(url, headers=headers)

            if resp.status_code in (200, 201, 202, 204):
                self.log_view.write("[INFO] Re-run request accepted by GitHub.")
                # Refresh runs for the current workflow so status updates soon.
                workflow_index = getattr(self.workflow_list, "index", None)
                if workflow_index is not None and 0 <= workflow_index < len(
                    self.workflows
                ):
                    wf = self.workflows[workflow_index]
                    await self.load_runs(repo_name, wf["id"], wf.get("name", ""))
            else:
                self.log_view.write(
                    f"[ERROR] Failed to request re-run: HTTP {resp.status_code} - {resp.text}"
                )
        except Exception as exc:
            self.log_view.write(f"[ERROR] Exception while requesting re-run: {exc}")


if __name__ == "__main__":
    try:
        CICDMonitorApp().run()
    except Exception as e:
        print(f"Exception during app run: {e}")
