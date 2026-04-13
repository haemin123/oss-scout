Analyze a GitHub repository using the oss-scout MCP server.

Use the `explain_repo` tool with repo_url: $ARGUMENTS

If the argument does not start with "https://", prepend "https://github.com/" to form the full URL.

Present the analysis in this structured format:

## Repository Analysis

**Description**: (from the result)

**Tech Stack**: List all detected technologies as badges or a comma-separated list.

**File Structure**:
Show the file tree summary in a code block.

**How to Use**:
Show the setup/installation instructions found in the README.

**Caveats/Warnings**:
List any warnings about license, archive status, low stars, or poor documentation.

**License**: Show the license name and whether it is permissive or copyleft.

After the analysis, suggest next steps:
- Use `/scout-validate <repo_url>` for full sub-agent validation
- Use `/scout-scaffold <repo_url> <target_dir>` to scaffold the project
