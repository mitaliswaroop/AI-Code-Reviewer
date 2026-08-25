import os
import requests
from fastapi import FastAPI, Request, BackgroundTasks
from google import genai
from dotenv import load_dotenv

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


def review_code_with_gemini(diff_text: str) -> str:
    """Sends the diff to Gemini for review, falling back to other models if busy."""
    client = genai.Client(api_key=GEMINI_TOKEN)
    prompt = f"""You are a senior software engineer. Review the following pull request diff.
Focus exclusively on:
- Logic bugs
- Security vulnerabilities
- Major performance issues

Keep the response brief, structured, and actionable.

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

        print("[Processing] Running Gemini code review...")
        review = review_code_with_gemini(diff)

        print("\n=== AI Code Review Result ===")
        print(review.strip())
        print("==============================\n")

        # --- NEW CODE ---
        print("[Processing] Sending review to GitHub...")
        post_comment_to_pr(owner, repo, pr_number, review)
        # ----------------

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