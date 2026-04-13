Scaffold a GitHub repository using the oss-scout MCP server.

Parse the arguments: $ARGUMENTS

Expected formats:
- `<repo_url> <target_dir>` — e.g., `https://github.com/owner/repo ./my-project`
- `<owner/repo> <target_dir>` — e.g., `owner/repo ./my-project`
- `<repo_url> <target_dir> --subdir <subdir>` — extract only a subdirectory

If the repo_url does not start with "https://", prepend "https://github.com/".
If no target_dir is provided, use the repo name as the directory (e.g., `./repo`).

Use the `scaffold` tool with the parsed parameters:
- repo_url: the full GitHub URL
- target_dir: the target directory
- subdir: (optional) subdirectory to extract
- generate_claude_md: true

After scaffolding, present:
1. **Status**: success or error
2. **Files created**: count
3. **Path**: where files were extracted
4. **CLAUDE.md**: location of the generated file
5. **Next steps**: show each suggested command in a code block

If the scaffold fails due to a security check (path traversal, non-empty directory), explain the error clearly and suggest how to fix it.
