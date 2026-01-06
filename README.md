
# GitHub Action TUI: Monitor & Trigger Workflows

![Screenshot of GitHub Action TUI](action-check.png)

A terminal dashboard (built with [Textual](https://github.com/Textualize/textual)) for monitoring and managing GitHub Actions workflows across your repositories.

Use it to quickly see which workflows are passing or failing, drill into runs, and manually trigger workflows without leaving your terminal.

---

## Quickstart

```bash
git clone https://github.com/gregheffner/action-check.git
cd action-check

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt

echo "GITHUB_TOKEN=ghp_yourgithubtokenhere" > .env

python action-check.py
```

Once running, use the arrow keys to move around and `q` to quit. See **Usage** below for all key bindings.

---

## What It Does

This TUI connects to the GitHub API using your personal access token and lets you:

- Browse repositories that have GitHub Actions workflows.
- Inspect workflows for each repository.
- Monitor recent workflow runs and their status.
- Trigger workflows manually with a keypress.
- Re-run failed jobs from the terminal.
- View logs and status updates as runs progress.

---

## Features

- **Browse Repositories** – See all your GitHub repositories with Actions workflows.
- **View Workflows** – List and select workflows for each repository.
- **Monitor Runs** – See recent workflow runs, their status, and details.
- **Trigger Workflows** – Manually dispatch workflows from the TUI.
- **Re-run Jobs** – Re-run failed jobs directly from the TUI.
- **Live Logs** – View logs and status updates in real time.

---

## Installation

### Requirements

- Python 3.8+
- [Textual](https://github.com/Textualize/textual)
- [httpx](https://www.python-httpx.org/)
- [python-dotenv](https://github.com/theskumar/python-dotenv)

Install dependencies:

```bash
pip install -r requirements.txt
```

Using a virtual environment is recommended:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

---

## Configuration

1. **Create a GitHub Personal Access Token (PAT)**
  - Go to https://github.com/settings/tokens (or **Settings → Developer settings → Personal access tokens**).
  - Create a token (classic or fine-grained) with at least:
    - `repo`
    - `workflow`

2. **Create a .env file** in the project root:

  ```env
  GITHUB_TOKEN=ghp_yourgithubtokenhere
  ```

3. **Ensure .env is not committed**
  - The repository’s `.gitignore` is configured so that `.env` is not tracked.

---

## Usage

Start the TUI:

```bash
python action-check.py
```

### Keyboard Navigation

- Left / Right arrows – Move between columns (repos, workflows, runs).
- Up / Down arrows – Navigate lists.
- `t` – Trigger the selected workflow.
- `r` – Re-run the selected job.
- `q` – Quit the application.

### Typical Workflows

- **Monitor CI status** – Open the app, pick a repo, select a workflow, and watch recent runs and logs.
- **Manually trigger a workflow** – Navigate to the desired workflow and press `t`.

---

## Troubleshooting

- **401 / 403 errors** – Check that your `GITHUB_TOKEN` is valid and has `repo` and `workflow` scopes.
- **Empty repository list** – Ensure the token has access to the repositories you expect (org vs. user scopes).
- **No workflows shown for a repo** – Verify that the repo actually has GitHub Actions workflows configured.

---

## Security

- Your `.env` and token are ignored by git via `.gitignore`.
- The application only uses your token locally to call the GitHub API.
- No sensitive data is uploaded to GitHub by this TUI.

---

## License

MIT

---

*Built with [Textual](https://github.com/Textualize/textual) for a modern terminal experience.*
