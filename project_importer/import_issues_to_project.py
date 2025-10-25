#!/usr/bin/env python3
import csv, os, sys
from typing import Dict, Tuple, List
import requests
from dotenv import load_dotenv

GQL_ENDPOINT = "https://api.github.com/graphql"
REST_ROOT = "https://api.github.com"

def die(msg, code=1):
    print(f"ERROR: {msg}", flush=True)
    sys.exit(code)

def gh_headers(token: str) -> Dict[str,str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

def graphql(token: str, query: str, variables: Dict=None) -> Dict:
    r = requests.post(GQL_ENDPOINT, headers=gh_headers(token), json={"query": query, "variables": variables or {}})
    if r.status_code != 200:
        die(f"GraphQL HTTP {r.status_code}: {r.text}")
    data = r.json()
    if "errors" in data:
        die(f"GraphQL errors: {data['errors']}")
    return data["data"]

def rest(token: str, method: str, path: str, json_body: Dict=None):
    import requests
    url = f"{REST_ROOT}{path}"
    r = requests.request(method, url, headers=gh_headers(token), json=json_body)
    if r.status_code >= 400:
        die(f"REST {method} {path} -> HTTP {r.status_code}: {r.text}")
    return r

def find_project(token: str, owner: str, number: int) -> Tuple[str, Dict]:
    q = """
    query($login:String!, $number:Int!) {
      user(login:$login) {
        projectV2(number:$number) { id title fields(first:100) {
          nodes {
            ... on ProjectV2SingleSelectField { id name options { id name } }
            ... on ProjectV2FieldCommon { id name }
          }}
        }
      }
      organization(login:$login) {
        projectV2(number:$number) { id title fields(first:100) {
          nodes {
            ... on ProjectV2SingleSelectField { id name options { id name } }
            ... on ProjectV2FieldCommon { id name }
          }}
        }
      }
    }
    """
    d = graphql(token, q, {"login": owner, "number": number})
    proj = (d.get("user") or {}).get("projectV2") or (d.get("organization") or {}).get("projectV2")
    if not proj: die(f"Project {number} not found for owner {owner}")
    return proj["id"], proj

def get_status_field_info(project):
    for f in project["fields"]["nodes"]:
        if f["name"].lower() == "status" and "options" in f:
            return f["id"], {o["name"].lower(): o["id"] for o in f["options"]}
    die("Status field not found on project (create Todo/In Progress/Done).")

def ensure_labels(token, owner, repo, labels):
    if not labels: return
    r = rest(token, "GET", f"/repos/{owner}/{repo}/labels?per_page=100")
    existing = {l["name"].lower() for l in r.json()}
    for lb in labels:
        if lb.strip().lower() not in existing:
            rest(token, "POST", f"/repos/{owner}/{repo}/labels", {"name": lb.strip(), "color": "ededed"})

def create_issue(token, repo_full, title, body, labels):
    owner, repo = repo_full.split("/",1)
    ensure_labels(token, owner, repo, labels)
    payload = {"title": title, "body": body}
    if labels: payload["labels"] = labels
    r = rest(token, "POST", f"/repos/{owner}/{repo}/issues", payload)
    j = r.json()
    return j["number"], j["node_id"]

def add_issue_to_project(token, project_id, issue_node_id):
    m = """
    mutation($projectId:ID!, $contentId:ID!) {
      addProjectV2ItemById(input:{projectId:$projectId, contentId:$contentId}) {
        item { id }
      }
    }
    """
    d = graphql(token, m, {"projectId": project_id, "contentId": issue_node_id})
    return d["addProjectV2ItemById"]["item"]["id"]

def set_status(token, project_id, item_id, field_id, status_options, status_name):
    opt_id = status_options.get((status_name or "Todo").lower())
    if not opt_id: die(f"Unknown Status '{status_name}'")
    m = """
    mutation($projectId:ID!, $itemId:ID!, $fieldId:ID!, $optId:String!) {
      updateProjectV2ItemFieldValue(input:{
        projectId:$projectId, itemId:$itemId, fieldId:$fieldId,
        value:{ singleSelectOptionId:$optId }
      }) { clientMutationId }
    }
    """
    graphql(token, m, {"projectId": project_id, "itemId": item_id, "fieldId": field_id, "optId": opt_id})

def append_tasklist(token, repo_full, parent_number, tasks):
    owner, repo = repo_full.split("/",1)
    r = rest(token, "GET", f"/repos/{owner}/{repo}/issues/{parent_number}")
    body = r.json().get("body") or ""
    body += "\\n\\n## Subtasks\\n"
    for num, title in tasks:
        body += f"- [ ] #{num} — {title}\\n"
    rest(token, "PATCH", f"/repos/{owner}/{repo}/issues/{parent_number}", {"body": body})

def main():
    load_dotenv()
    token = os.getenv("GH_PAT")
    repo_full = os.getenv("REPO")
    project_owner = os.getenv("PROJECT_OWNER")
    project_number = int(os.getenv("PROJECT_NUMBER","0"))
    csv_path = os.getenv("CSV_PATH")

    if not token or not repo_full or not project_owner or not project_number or not csv_path:
        die("Missing env vars: GH_PAT, REPO, PROJECT_OWNER, PROJECT_NUMBER, CSV_PATH")

    project_id, project = find_project(token, project_owner, project_number)
    field_id, status_opts = get_status_field_info(project)

    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "title": (r.get("Title") or "").strip(),
                "body": (r.get("Body") or "").strip(),
                "labels": [x.strip() for x in (r.get("Labels") or "").split(",") if x.strip()],
                "status": (r.get("Status") or "Todo").strip() or "Todo",
                "parent": (r.get("Parent") or "").strip(),
            })

    title_to_issue = {}
    parent_children = {}

    for r in rows:
        if not r["title"]:
            continue
        num, node = create_issue(token, repo_full, r["title"], r["body"], r["labels"])
        item_id = add_issue_to_project(token, project_id, node)
        set_status(token, project_id, item_id, field_id, status_opts, r["status"])
        title_to_issue[r["title"]] = (num, node)
        if r["parent"]:
            parent_children.setdefault(r["parent"], []).append((num, r["title"]))

    for parent_title, tasks in parent_children.items():
        if parent_title not in title_to_issue:
            die(f"Parent '{parent_title}' not found. Ensure parent row appears before its children.")
        parent_num, _ = title_to_issue[parent_title]
        append_tasklist(token, repo_full, parent_num, tasks)

    print(f"Imported {len(rows)} issues and linked subtasks.")

if __name__ == "__main__":
    main()
