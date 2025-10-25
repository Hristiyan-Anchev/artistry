#!/usr/bin/env python3
import csv
import os
import sys
import time
import json
from typing import Dict, Optional, Tuple, List
import requests
from dotenv import load_dotenv

GQL_ENDPOINT = "https://api.github.com/graphql"
REST_ROOT = "https://api.github.com"

def log(msg):
    print(msg, flush=True)

def die(msg, code=1):
    log(f"ERROR: {msg}")
    sys.exit(code)

def get_env(name: str, required: bool=True, default: Optional[str]=None) -> str:
    v = os.getenv(name, default)
    if required and not v:
        die(f"Missing env var: {name}")
    return v

def gh_headers(token: str) -> Dict[str,str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }

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

def find_project(token: str, owner: str, project_number: int):
    q_user = """
    query($login:String!, $number:Int!) {
      user(login:$login) {
        projectV2(number:$number) {
          id
          title
          fields(first:100) {
            nodes {
              ... on ProjectV2SingleSelectField {
                id
                name
                options { id name }
              }
              ... on ProjectV2FieldCommon {
                id
                name
              }
            }
          }
        }
      }
    }
    """
    data = graphql(token, q_user, {"login": owner, "number": project_number})
    proj = data.get("user", {}).get("projectV2")
    if proj:
        return proj["id"], proj

    q_org = """
    query($login:String!, $number:Int!) {
      organization(login:$login) {
        projectV2(number:$number) {
          id
          title
          fields(first:100) {
            nodes {
              ... on ProjectV2SingleSelectField {
                id
                name
                options { id name }
              }
              ... on ProjectV2FieldCommon {
                id
                name
              }
            }
          }
        }
      }
    }
    """
    data = graphql(token, q_org, {"login": owner, "number": project_number})
    proj = data.get("organization", {}).get("projectV2")
    if proj:
        return proj["id"], proj
    die(f"Project number {project_number} not found under owner '{owner}'.")

def get_status_field_info(project):
    field_id = None
    options = {}
    for f in project["fields"]["nodes"]:
        if f["name"].lower() == "status" and "options" in f:
            field_id = f["id"]
            for opt in f["options"]:
                options[opt["name"].strip().lower()] = opt["id"]
            break
    if not field_id:
        die("Could not find 'Status' single-select field on the project. Create it first with options: Todo, In Progress, Done.")
    return field_id, options

def ensure_labels(token, owner, repo, labels):
    if not labels:
        return
    import requests
    r = rest(token, "GET", f"/repos/{owner}/{repo}/labels?per_page=100")
    existing = {l["name"].lower() for l in r.json()}
    for lb in labels:
        if lb.strip().lower() not in existing:
            rest(token, "POST", f"/repos/{owner}/{repo}/labels", {"name": lb.strip(), "color": "ededed"})
            log(f"Created label: {lb}")

def create_issue(token, repo_full, title, body, labels):
    owner, repo = repo_full.split("/", 1)
    ensure_labels(token, owner, repo, labels)
    payload = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    r = rest(token, "POST", f"/repos/{owner}/{repo}/issues", payload)
    js = r.json()
    return js["number"], js["node_id"]

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
    key = (status_name or "Todo").strip().lower()
    opt_id = status_options.get(key)
    if not opt_id:
        die(f"Status '{status_name}' not found in project. Available: {list(status_options.keys())}")
    m = """
    mutation($projectId:ID!, $itemId:ID!, $fieldId:ID!, $optId:String!) {
      updateProjectV2ItemFieldValue(input:{
        projectId:$projectId,
        itemId:$itemId,
        fieldId:$fieldId,
        value:{ singleSelectOptionId:$optId }
      }) { clientMutationId }
    }
    """
    graphql(token, m, {"projectId": project_id, "itemId": item_id, "fieldId": field_id, "optId": opt_id})

def main():
    from dotenv import load_dotenv
    load_dotenv()
    token = os.getenv("GITHUB_TOKEN")
    repo_full = os.getenv("REPO")  # e.g., "username/art-storefront"
    project_owner = os.getenv("PROJECT_OWNER")  # your user/org login
    project_number = int(os.getenv("PROJECT_NUMBER", "0"))
    csv_path = os.getenv("CSV_PATH")

    if not token or not repo_full or not project_owner or not project_number or not csv_path:
        die("Missing one of required env vars: GITHUB_TOKEN, REPO, PROJECT_OWNER, PROJECT_NUMBER, CSV_PATH")

    project_id, project = find_project(token, project_owner, project_number)
    field_id, status_options = get_status_field_info(project)
    log(f"Project found: id={project_id}. Status options: {list(status_options.keys())}")

    created = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = (row.get("Title") or "").strip()
            if not title:
                log("Skipping row without Title")
                continue
            body = (row.get("Body") or "").strip()
            labels_csv = (row.get("Labels") or "").strip()
            status = (row.get("Status") or "Todo").strip() or "Todo"
            labels = [l.strip() for l in labels_csv.split(",") if l.strip()] if labels_csv else []

            num, node = create_issue(token, repo_full, title, body, labels)
            log(f"Issue created: #{num}")
            item_id = add_issue_to_project(token, project_id, node)
            set_status(token, project_id, item_id, field_id, status_options, status)
            created += 1

    log(f"Done. Created {created} issues, added to project, and set Status.")

if __name__ == "__main__":
    main()
