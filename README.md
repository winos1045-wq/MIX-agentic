# MIX-agentic
This is Beta version Development Agent : Advancing 18.7 %




# MIX Agent

An autonomous agentic AI built on Google Gemini, designed for software engineering and cybersecurity tasks. Runs entirely in the terminal with a rich interactive interface, tool execution loop, file injection, and multi-agent coordination.

---

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Agent](#running-the-agent)
- [Interface Overview](#interface-overview)
- [Commands](#commands)
- [File Injection](#file-injection)
- [Agent Groups](#agent-groups)
- [Tool Reference](#tool-reference)
- [Architecture](#architecture)
- [Security Model](#security-model)
- [Token Tracking](#token-tracking)

---
## layers
<img width="1440" height="1312" alt="image" src="https://github.com/user-attachments/assets/b8176fe7-5a33-4ac0-88de-a89675f4212a" />



## Requirements

| Dependency | Version | Purpose |
|------------|---------|---------|
| Python | 3.10+ | Runtime |
| google-genai | latest | Gemini API client |
| rich | latest | Terminal rendering |
| prompt-toolkit | latest | Interactive prompt, autocomplete |
| python-dotenv | latest | Environment loading |

---

## Installation

**1. Clone the repository**

```bash
git clone https://github.com/winos1045-wq/MIX-agentic.git
cd mix-agent/llm
```

**2. Create and activate a virtual environment**

```bash
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
.venv\Scripts\activate           # Windows
```

**3. Install dependencies**

```bash
pip install -r requirements.txt
```

If you do not have a `requirements.txt`, install manually:

```bash
pip install google-genai rich prompt-toolkit python-dotenv
```

---

## Configuration

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_api_key_here
```

The agent looks for `.env` in the same directory as `main.py`. If the file is absent, it falls back to environment variables exported in the shell.

**To get a Gemini API key:**  
Visit [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey), create a project, and copy the key.

### Optional: Codespace Tools

If you have `func/codespace_tools.py` present, the agent auto-detects and loads those tools on startup. No extra configuration required.

---

## Running the Agent

```bash
python main.py
```

The welcome screen shows the current model, working directory, and available features.

**Verbose mode** — shows token counts after each iteration:

```
> your question here --verbose
```

---

## Interface Overview

<img width="1368" height="710" alt="image" src="https://github.com/user-attachments/assets/9628dd77-d0c8-4528-b7f2-65d22eda8496" />


The prompt turns cyan when inside an agent group session.

---

## Commands

All commands are prefixed with `/`.

### General

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/history` | Display the last 10 messages in session history |
| `/clear` | Wipe the current session history |
| `/status` | Show CWD, session ID, token counts, context size, and agent group info |
| `/monitor_on` | Enable verbose API and HTTP logging |
| `/monitor_off` | Disable verbose logging |
| `/reload` | Restart the agent process in-place (`os.execv`) |
| `/exit` or `/quit` or `/q` | Exit gracefully |

### Agent Group Commands

| Command | Description |
|---------|-------------|
| `/agent` | Launch the interactive wizard to join or create a group |
| `/agent status` | List all members in the current group |
| `/agent inbox` | Read pending messages from other agents |
| `/agent send <id> <message>` | Send a direct message to an agent by ID prefix |
| `/agent broadcast <message>` | Send a message to all agents in the group |
| `/agent leave` | Leave the current group |

---

## File Injection

Prefix any file or directory path with `@` inside your prompt to inject its content directly into the request context.

```
> review this file @src/auth/jwt.py and find potential vulnerabilities
```

```
> summarize the structure of @src/
```

**Behavior by path type:**

| Input | Result |
|-------|--------|
| `@path/to/file.py` | Full file content injected as `<injected_file>` block |
| `@path/to/directory/` | Directory listing injected as `<injected_dir>` block |
| Blocked path | Shown as `x path  reason` — not injected |
| Missing path | Shown as `? path  not found` — not injected |

The injection report is printed before the agent processes your message. Injected files are immediately available in the model's context — the agent will not re-fetch them via `get_file_content`.

**Autocomplete:** Start typing `@` and press Tab to browse available files and directories.

---

## Agent Groups

Agent groups allow multiple running instances of MIX Agent (across different terminals or machines) to coordinate, pass messages, and share work.

**Creating or joining a group:**

```
/agent
  Group name : my-project
  Agent name : backend
```

If the group does not exist, it is created. If it exists, you join it and see the current member list.

**Each agent in the group has:**

| Property | Description |
|----------|-------------|
| Name | Human-readable label you set |
| ID | Auto-generated UUID |
| Rank | Role within the group (e.g., leader, member) |
| Status | `thinking`, `idle`, or `offline` |

Messages arrive in real-time and are printed inline in your terminal. Use `/agent inbox` to retrieve buffered messages if you were away.

---

## Tool Reference

The agent has access to the following tools during its execution loop:

### File System

| Tool | Description |
|------|-------------|
| `get_files_info` | List files and metadata in a directory |
| `get_file_content` | Read a file, optionally with a line range |
| `write_file` | Create a new file with given content |
| `patch_file` | Apply targeted changes to an existing file |
| `get_project_map` | Generate a structural map of the codebase |

### Code & Execution

| Tool | Description |
|------|-------------|
| `run_shell` | Execute a shell command |
| `run_python_file` | Run a Python script |
| `search_code` | Grep-style pattern search across the codebase |
| `verify_change` | Confirm a file change was applied correctly |
| `build_project` | Run the project build system |
| `install_dependencies` | Install packages via pip, npm, etc. |
| `plan_project` | Generate a structured task plan |

### Web

| Tool | Description |
|------|-------------|
| `web_search` | Search the web (max 8 calls per session) |
| `web_fetch` | Fetch and read a full web page by URL |

### Codespace (if available)

| Tool | Description |
|------|-------------|
| `cs_run_shell` | Run a shell command in the codespace |
| `cs_read_file` | Read a file from the codespace |
| `cs_write_file` | Write a file to the codespace |
| `cs_patch_file` | Patch a file in the codespace |

---

## Architecture

```
main.py
  MIXAgent
    SessionManager       -- maintains conversation history (rolling window: 25 messages)
    StatusBar            -- animated spinner with elapsed time and token count
    TokenCounter         -- tracks prompt / completion / thinking / cached tokens
    CommandHandler       -- parses and dispatches /commands
    UI                   -- Rich-powered terminal output
      MarkdownRenderer   -- splits response into prose and code blocks
    AgentGroup           -- multi-agent coordination layer
    FilePathCompleter    -- @-path autocomplete (background thread, 30s refresh)
    CommandCompleter     -- /command autocomplete
    PathGuard            -- enforces file access rules
    FileInjector         -- handles @path expansion before sending to model
```

**Request loop:**

```
user input
  -> file injection (@paths expanded)
  -> session history assembled
  -> model.generate_content()
  -> if function_calls: execute tools, append results, loop
  -> if text response: render markdown, record tokens, return
```

---

## Security Model

The path guard runs on every file-related tool call before execution.

**Always blocked:**

| Category | Examples |
|----------|---------|
| Credentials | `.env`, private keys, credential files |
| Version control | `.git/` internals |
| Dependencies | `node_modules/`, `venv/`, `.venv/` |
| Agent internals | `sessions/`, `logs/`, `agents/` |

**Rules:**

- If a tool attempts to access a blocked path, it receives `Blocked: path not allowed` as the result and the attempt is logged.
- The agent is instructed to stop and report on any path guard block — it must not retry or attempt workarounds.
- Write operations are subject to stricter checks than reads.
- `web_fetch` is blocked on `localhost`, `127.0.0.1`, and all internal IP ranges.

---

## Token Tracking

Use `/status` at any time to see the current session token usage:

```
Tokens (session)
  requests      12
  prompt        48,320
  completion    9,140
  thinking      2,011
  cached        6,400
  total         59,471
```

After each model response, a compact summary is printed:

```
  in 4,210  .  out 812  .  think 344  .  cached 1,200  .  session 59,471
```

---

## Project Layout

```
mix-agent/
  main.py                  -- entry point, agent loop, UI
  call_function.py         -- dispatches tool calls to implementations
  file_injector.py         -- @path injection logic
  path_guard.py            -- file access security layer
  func/
    web_fetch_search.py    -- web_search and web_fetch tools
    get_files_info.py
    get_file_content.py
    write_file.py
    run_python_file.py
    run_shell.py
    patch_file.py
    build.py
    plan_project.py
    grep_tool.py
    verify_change.py
    project_map.py
    agent_group.py         -- multi-agent coordination
    codespace_tools.py     -- optional codespace integration
  .env                     -- API key (not committed)
```

---

## Developer

Built by **Mohamed FAAFAA**  
Model: `gemma-4-26b-a4b-it` via Google Gemini API


## تحيا الـــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــــعلوم الطبيعية

hhhhhhhhhhhhhhhhhhhhh
<img width="664" height="268" alt="image" src="https://github.com/user-attachments/assets/272ac161-9c24-4101-9b1e-f447e0ceec3e" />

<img width="1900" height="1023" alt="image" src="https://github.com/user-attachments/assets/9b72b20a-0d6e-46a1-8347-1b8a3bca7c2b" 


  <img width="1905" height="1001" alt="image" src="https://github.com/user-attachments/assets/85c54c38-2b0c-4400-9426-cfe08e900e22" />



