To make your patch_file tool as robust and safe as the TypeScript FileEditTool from Claude Code, you need to add several critical features. Below is a structured comparison and the required upgrades.

🔍 Key Missing Features & Recommended Upgrades
1. Read‑Before‑Edit Tracking (Staleness Protection)
Problem: Your tool doesn’t know if the file was modified after it was last read. This can cause lost updates.

Solution: Maintain a global registry (e.g., file_state = {}) storing (content, timestamp, offset) for each file. Before applying a patch, verify that the file on disk still matches the last‑seen content. If not → reject with “File has been modified since read”.

2. Permission System (Allow/Deny Rules)
Problem: No way to restrict which files can be patched (e.g., deny .env or system files).

Solution: Add a permission checker that supports wildcard patterns (/home/**/secrets, *.key). Reject edits that match a deny rule.

3. Replace All Occurrences (replace_all)
Problem: Your tool replaces only the first occurrence of content_before (using .replace() with a string). If the same block appears multiple times, the user cannot replace all.

Solution: Add an optional replace_all: bool parameter. If True, replace every non‑overlapping occurrence.

4. Quote Normalisation & Preservation
Problem: Curly quotes (“ ” and ‘ ’) in the file but straight quotes (" ') in the AI’s proposed string will cause a match failure.

Solution: Implement:

normalize_quotes() – converts curly to straight for matching.

find_actual_string() – searches using normalised versions.

preserve_quote_style() – after match, apply original curly quotes to the new string.

5. Line Ending Handling
Problem: Your code reads as UTF‑8 and writes back the same string, but does not normalise \r\n to \n before processing. This can break exact matches.

Solution: Read file → replace \r\n with \n for internal comparison. When writing, detect original line endings and restore them.

6. File Size Limit
Problem: No limit – a multi‑gigabyte file could be read into memory and crash the process.

Solution: Reject files larger than, e.g., 1 GiB. Use os.path.getsize() before reading.

7. Creation of New Files
Problem: Your tool fails if the file does not exist. The Claude tool allows creating a new file when old_string (your content_before) is empty.

Solution: If content_before == "" and file does not exist → create it (write content_after). If file exists but is empty, also allow.

8. Encoding Detection
Problem: Assumes UTF‑8. Real files may be UTF‑16 or have BOM.

Solution: Use chardet or read BOM bytes to detect encoding (UTF‑8, UTF‑16 LE/BE). Convert everything to a normalised internal representation (e.g., UTF‑8 with \n line endings).

9. Atomic Read‑Modify‑Write
Problem: There is a race window between reading and writing. If another process writes during that window, changes are lost.

Solution: Use a file lock (e.g., fcntl.flock on Unix, msvcrt.locking on Windows) or write to a temporary file then atomic rename (os.replace).

10. Multiple Matches & Uniqueness Check
Problem: If content_before appears more than once and replace_all is False, the tool should reject and ask for more context.

Solution: Count occurrences. If >1 and replace_all=False → error with “Found N matches, please provide more context or set replace_all=true”.

11. Jupyter Notebook Detection
Problem: Editing .ipynb files with a text replacement tool is dangerous (breaks JSON structure).

Solution: Detect extension .ipynb and return a specific error suggesting a notebook‑dedicated tool.

12. LSP Integration
Problem: Language servers (e.g., TypeScript, Pyright) are not notified of file changes, so diagnostics become stale.

Solution: After writing, call LSP endpoints: didChange (content update) and didSave (trigger re‑analysis).

13. Git Diff Generation
Problem: No way for the AI to see a structured diff of the change.

Solution: Use difflib.unified_diff to generate a patch, optionally call git diff to get repository‑level diff with metadata (additions, deletions, repo name).

14. File History Backup
Problem: No undo capability if the AI makes a mistake.

Solution: Before editing, copy the file to a hidden backup (e.g., .history/<file>.backup). This allows restoration.

15. Analytics & Logging
Problem: No telemetry to track tool usage, error rates, or performance.

Solution: Log events (file path anonymised, size of old/new strings, success/failure, duration) to an internal analytics system.

16. Desanitization of Special Tokens
Problem: The AI may output sanitised versions of internal markers (e.g., <fnr> instead of <function_results>).

Solution: Apply a mapping of common replacements before matching (e.g., <fnr> → <function_results>, <n> → <name>).

17. UNC Path Security
Problem: On Windows, UNC paths (\\server\share) can trigger SMB authentication and leak credentials.

Solution: Reject paths starting with \\ or // unless explicitly allowed.

18. Whitespace Stripping for Markdown
Problem: Stripping trailing whitespace changes Markdown semantics (two spaces = hard line break).

Solution: Only strip trailing whitespace for non‑Markdown files (detect .md / .mdx).

19. Diff Display Enhancement
Problem: Your show_diff works well but lacks:

Truncation for very large diffs (limit to ~8KB).

Unified diff format with line numbers and context.

A compact summary when changes are huge.

Solution: Implement snippet truncation and optionally a standard unified diff view.

20. Error Codes & Structured Responses
Problem: Returns plain string messages. The Claude tool returns structured error codes (0‑10) so the AI can react differently.

Solution: Return a JSON object with success: bool, error_code: int, message: str, and optional meta (e.g., actual_old_string, match_count).

📦 Recommended Code Structure (Upgraded)
python
class FileEditTool:
    def __init__(self, permission_rules=None, lsp_client=None):
        self.file_state = {}  # path -> (content, timestamp, offset)
        self.permission_rules = permission_rules or []
        self.lsp = lsp_client

    def validate_input(self, file_path, old_string, new_string, replace_all):
        # 1. Permission check (deny/allow)
        # 2. File size check
        # 3. Existence & empty file creation logic
        # 4. Jupyter detection
        # 5. Read state & staleness check
        # 6. Find actual string (with quote normalisation)
        # 7. Uniqueness check (count matches)
        # 8. Return success + meta (actual_old_string)

    def call(self, file_path, old_string, new_string, replace_all):
        # 1. Expand path, create parent dirs
        # 2. Backup if history enabled
        # 3. Read current content
        # 4. Validate again (atomic)
        # 5. Compute patch & new content
        # 6. Write using atomic rename
        # 7. Update LSP (didChange + didSave)
        # 8. Generate git diff if requested
        # 9. Log analytics
        # 10. Return structured result
✅ Summary of Required Changes
Feature	Your tool	Claude’s tool	Action needed
Read‑before‑edit check	❌	✅	Add state tracking
Permission rules	❌	✅	Implement allow/deny patterns
replace_all	❌	✅	Add parameter & logic
Quote normalisation	❌	✅	Add normalise/preserve
Line ending handling	❌	✅	Normalise CRLF → LF
File size limit	❌	✅	Add size check
Create new file	❌	✅	Support old_string=""
Encoding detection	❌	✅	Use BOM/chardet
Atomic write	❌	✅	Use temp+rename
Uniqueness check	❌	✅	Count matches & reject
Jupyter detection	❌	✅	Block .ipynb
LSP integration	❌	✅	Notify language servers
Git diff	❌	✅	Run git diff & return
File history backup	❌	✅	Create .history backups
Analytics	❌	✅	Log usage events
Desanitization	❌	✅	Replace special tokens
UNC path security	❌	✅	Reject UNC paths
Markdown whitespace	❌	✅	Preserve trailing spaces
Diff truncation	❌	✅	Limit snippet size
Error codes	❌	✅	Return structured errors
🚀 Next Steps
Start with safety – add file size limit, permission checks, and staleness protection.

Improve matching – implement quote normalisation and uniqueness validation.

Add atomic writes & backups – prevent corruption and allow undo.

Integrate with your AI’s context – expose replace_all in the schema.

Test thoroughly – edge cases (empty file, large file, concurrent edits, various encodings).

Once these upgrades are in place, your patch_file tool will be on par with the Claude Code Edit tool. Would you like me to provide concrete code examples for any of these improvements?


because 