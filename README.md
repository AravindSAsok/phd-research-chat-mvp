# p!nk — The Research Chat

A small Streamlit app for asking questions about uploaded PDF, DOCX, and TXT research documents with the Gemini API.

## What it does

- Accepts multiple PDF, DOCX, and TXT files in one session.
- Extracts PDF page text and keeps page numbers for source references. Scanned PDFs need OCR, which is not included.
- Ranks excerpts with lightweight keyword and topic synonyms, using recent completed turns to resolve short follow-up questions.
- Says when no relevant text was found. The answer prompt also tells Gemini to state when retrieved excerpts do not provide enough evidence.
- Converts valid source IDs into filenames and PDF page references, and lists the sources from the retrieved excerpts.
- Keeps uploads and extracted text in the Streamlit session; this app does not save them to an application database.
- Reads `GEMINI_API_KEY` from `.env` or Streamlit Secrets. The key is never included in app source code.

Retrieval is lightweight and document-grounded. Check important answers against the original documents.

## Requirements

- Python 3.10 or later
- A Gemini API key from https://aistudio.google.com/apikey

## Install and run on Windows PowerShell

Open PowerShell and run these commands from the project folder:

```powershell
Set-Location -LiteralPath 'D:\dummies\phd-research-chat-mvp'
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

In Notepad, replace `replace_with_your_key` with your real key, save, and close the file. Do not paste the key into a PowerShell command. Then launch the app:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the local URL printed in the terminal, usually http://localhost:8501. Stop the server with `Ctrl+C` in PowerShell.

If `py -3.12 --version` fails because Python is not installed, install Python 3.12 with WinGet:

```powershell
winget install --exact --id Python.Python.3.12
```

Close and reopen PowerShell after installing Python, then repeat the commands above. The commands use the virtual environment's Python directly, so PowerShell script activation is not needed.

You can instead add the key in `.streamlit/secrets.toml` using TOML syntax:

```toml
GEMINI_API_KEY = "your-real-key"
GEMINI_MODEL = "gemini-3.8-flash"
```

The `.env` file and `.streamlit/secrets.toml` are excluded by `.gitignore`. Keep the actual key in only one local configuration location.

## Automated regression tests

Run this in PowerShell from the project folder:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

These tests mock Gemini calls and do not need or print an API key.

## Test the running app

Use non-confidential sample files. Upload a text-based PDF, a DOCX, and a TXT file together, then click **Process uploaded files**. Ask a question whose answer is clearly present and verify that the answer cites a filename and, for PDF evidence, a page number. Scanned PDFs may produce no text.

Ask a question on a topic absent from all uploaded files. The app should either say it found no relevant text or Gemini should say the excerpts do not contain enough evidence. It should not provide an unsupported answer or a made-up source reference.

To check missing-key handling, stop the app, remove `GEMINI_API_KEY` temporarily from `.env` and from `.streamlit/secrets.toml` if you use it, then run `Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue` in PowerShell before restarting the app. Confirm the missing-key warning, then restore the setting. Do not share the key in screenshots or chat.

The app maps common quota/rate-limit errors to a specific message. Confirm this path if Gemini returns an actual HTTP 429 during normal use; do not intentionally exhaust your quota just to trigger it.

## Deploy on Streamlit Community Cloud

1. Put `app.py`, `requirements.txt`, `.streamlit/config.toml`, `.gitignore`, and this README in a GitHub repository. Do not include `.env`, `.streamlit/secrets.toml`, or uploaded research documents. Check `.gitignore` before the first push.
2. Keep the repository private if the source should not be public. Community Cloud can access private repositories after you grant it the additional GitHub permission. A deployed app's visibility follows its Community Cloud settings and repository permissions.
3. Sign in at https://share.streamlit.io/ with GitHub, select **Create app**, then choose the repository, branch, and `app.py` as the entrypoint.
4. In **Advanced settings**, select Python 3.12 and add these values in the **Secrets** box:

   ```toml
   GEMINI_API_KEY = "your-real-key"
   GEMINI_MODEL = "gemini-3.8-flash"
   ```

5. Wait for the build to complete, open the generated app URL, and test with non-confidential sample documents.

The current app has no user login, rate limit, or abuse protection. Anyone allowed to use it can send requests through your Gemini API key. Uploaded content is sent to Gemini for answering; review Google's current data-use terms before using unpublished or confidential research. Streamlit hosting has its own operational behavior, so this app's no-database design is not a promise about provider logs or retention.

## Troubleshooting

- Missing-key warning: check that `GEMINI_API_KEY` is in `.env` or Streamlit Secrets and that the file was saved.
- Invalid-key message: verify the key in Google AI Studio and remove accidental quote marks or spaces in `.env`.
- Quota/rate-limit message: wait and check the current quota for the Gemini API key.
- Model-not-found message: check `GEMINI_MODEL` against the model names available to your key.
- No readable text from a PDF: it may be scanned or image-only; OCR is not included.
