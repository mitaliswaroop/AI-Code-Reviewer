import os
import requests
from fastapi import FastAPI, Request, BackgroundTasks
from google import genai
from dotenv import load_dotenv
import subprocess
import tempfile

# Load environment variables from the .env file
load_dotenv()

app = FastAPI(title="AI Code Reviewer")

# Now it pulls securely from .env instead of being hardcoded
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GEMINI_TOKEN = os.getenv("GEMINI_TOKEN")

def get_pr_diff(owner: str, repo: str, pr_num: int) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.diff"
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.text
    raise Exception(f"GitHub Error {response.status_code}: {response.text}")


def get_pr_files(owner: str, repo: str, pr_num: int) -> list:
    """Fetches the actual raw Python files from the PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}/files"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    response = requests.get(url, headers=headers)

    files_data = []
    if response.status_code == 200:
        for file in response.json():
            if file["filename"].endswith(".py"):
                raw_url = file["raw_url"]
                raw_response = requests.get(raw_url, headers=headers)
                files_data.append({
                    "filename": file["filename"],
                    "content": raw_response.text
                })
    return files_data


def run_flake8(code_string: str) -> str:
    """Runs flake8 on a string of Python code and returns the report."""
    # Create a temporary file to hold the code
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as temp:
        temp.write(code_string.encode("utf-8"))
        temp_path = temp.name

    # Run flake8 strictly via subprocess
    result = subprocess.run(["flake8", temp_path], capture_output=True, text=True)

    # Clean up the file so we don't leak memory
    os.remove(temp_path)

    # Flake8 returns the temporary absolute path. Let's clean it up.
    return result.stdout.replace(temp_path, "file.py")


def run_semgrep(code_string: str) -> str:
    """Runs Semgrep on a string of code to find security vulnerabilities."""
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as temp:
        temp.write(code_string.encode("utf-8"))
        temp_path = temp.name

    # Run semgrep with the default CI ruleset and disable telemetry
    result = subprocess.run(
        ["semgrep", "scan", "--config=p/ci", "--metrics=off", "--quiet", temp_path],
        capture_output=True,
        text=True
    )

    os.remove(temp_path)

    # Clean up the absolute temporary paths for the AI
    return result.stdout.replace(temp_path, "file.py")


def review_code_with_gemini(diff_text: str, flake8_report: str, semgrep_report: str) -> str:
    """Sends the diff and static analysis reports to Gemini for review."""
    client = genai.Client(api_key=GEMINI_TOKEN)
    prompt = f"""You are a senior software engineer. Review the following PR diff.
    We have run two static analysis tools on the code. 

    1. Flake8 (Style & Syntax):
    {flake8_report}

    2. Semgrep (Security & Bugs):
    {semgrep_report}

    Focus exclusively on:
    - Logic bugs
    - Security vulnerabilities
    - Major performance issues
    - Synthesizing the Flake8 and Semgrep findings into actionable advice.

    Code Diff:
    {diff_text}
    """

    # A list of models to try, from fastest/cheapest to most capable
    models_to_try = [
        'gemini-2.5-flash',
        'gemini-2.0-flash',
        'gemini-1.5-flash',
        'gemini-1.5-pro'
    ]

    for model_name in models_to_try:
        try:
            print(f"[Processing] Attempting review with {model_name}...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            return response.text

        except Exception as e:
            print(f"[Warning] {model_name} failed: {e}")
            print("Trying the next available model...")
            continue  # Try the next model in the list

    # If the loop finishes and all models failed
    raise Exception("All Gemini models are currently overloaded. Please try again later.")

def post_comment_to_pr(owner: str, repo: str, pr_num: int, comment: str):
    """Posts the AI review back to the GitHub PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_num}/comments"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {"body": comment}

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 201:
        print("[Success] Review posted successfully to GitHub!")
    else:
        print(f"[Error] Failed to post comment: {response.status_code} - {response.text}")


def process_review_task(owner: str, repo: str, pr_number: int):
    try:
        print(f"\n[Processing] Fetching diff for {owner}/{repo} PR #{pr_number}...")
        diff = get_pr_diff(owner, repo, pr_number)

        print("[Processing] Running Flake8 and Semgrep static analysis...")
        python_files = get_pr_files(owner, repo, pr_number)

        flake8_report = ""
        semgrep_report = ""

        for file in python_files:
            # Run Flake8
            f_issues = run_flake8(file["content"])
            if f_issues:
                flake8_report += f"\nIssues in {file['filename']}:\n{f_issues}\n"

            # Run Semgrep
            s_issues = run_semgrep(file["content"])
            if s_issues:
                semgrep_report += f"\nIssues in {file['filename']}:\n{s_issues}\n"

        # Provide clean fallback messages if the code is flawless
        if not flake8_report:
            flake8_report = "Flake8 found no styling or syntax issues!"
        if not semgrep_report:
            semgrep_report = "Semgrep found no security vulnerabilities!"

        print("[Processing] Running Gemini code review...")
        review = review_code_with_gemini(diff, flake8_report, semgrep_report)

        print("[Processing] Sending review to GitHub...")
        post_comment_to_pr(owner, repo, pr_number, review)

    except Exception as e:
        print(f"[Error] Failed to process review: {e}")


@app.post("/webhook")
async def handle_github_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    event_type = request.headers.get("X-GitHub-Event")
    action = payload.get("action")

    print(f"\n[DEBUG] GitHub called! Event: '{event_type}', Action: '{action}'")

    # Only review if a PR is opened or updated
    if event_type == "pull_request" and action in ["opened", "reopened", "synchronize"]:
        pr_number = payload["pull_request"]["number"]
        repo_name = payload["repository"]["name"]
        owner_name = payload["repository"]["owner"]["login"]

        print(f"[Success] Starting review for PR #{pr_number}")
        background_tasks.add_task(process_review_task, owner_name, repo_name, pr_number)
        return {"status": "accepted"}

    print("[DEBUG] Ignored. We only care about Pull Requests.")
    return {"status": "ignored"}