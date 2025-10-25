# GitHub Projects Backlog Import

Two ways to run the importer:

## Option A — Run locally
1. Ensure you have **Python 3.10+**.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in:
   - `GITHUB_TOKEN` — fine-grained PAT with `Issues: Read & Write`, `Projects: Read & Write`, `Repository: Read`
   - `REPO` — e.g. `yourname/art-storefront`
   - `PROJECT_OWNER` — your GitHub login (or org)
   - `PROJECT_NUMBER` — the number in the Project URL
   - `CSV_PATH` — path to your CSV (default `issues.csv`)
4. Prepare/adjust `issues.csv` with columns: `Title,Body,Labels,Status` (Status in `Todo | In Progress | Done`).
5. `python import_issues_to_project.py`

## Option B — Run as a GitHub Action
1. Add `import_issues_to_project.py` and `workflow_import.yml` to your repo (place workflow at `.github/workflows/workflow_import.yml`).
2. Commit your `issues.csv` to the repo root.
3. In GitHub → **Actions** → select **Import backlog into GitHub Project** → **Run workflow** with inputs:
   - `csv_path`: `issues.csv`
   - `project_owner`: your login or org name
   - `project_number`: e.g., `1`
   - `repo_full`: the same repo (`owner/name`) where to create issues
4. The action uses the built-in `GITHUB_TOKEN` with permissions to create issues and update projects.

## Notes
- The script auto-creates missing labels in the repo.
- It finds your Project and the **Status** field and sets its value for each imported item.
- If your Project is **org-level**, set `PROJECT_OWNER` to the org login; the script will auto-detect user vs org.
- If you want to import more rows, just expand `issues.csv` or point `CSV_PATH` to another file.
