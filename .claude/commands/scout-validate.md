Validate a GitHub repository using the oss-scout MCP server.

Use the `validate_repo` tool with repo_url: $ARGUMENTS

If the argument does not start with "https://", prepend "https://github.com/" to form the full URL.

Present the validation results in this format:

## Validation Report: owner/repo

**Overall**: PASS or FAIL (based on overall_passed)
**Aggregate Score**: X.XX / 1.00

### Agent Results

| Agent | Score | Status | Key Findings |
|-------|-------|--------|-------------|

For each agent (license, quality, security, compatibility):
- Show the score (0-1)
- Show PASS or FAIL status
- List the most important findings and warnings

### Recommendations

Based on the validation:
- If all agents pass: recommend proceeding with `/scout-scaffold`
- If any agent fails: explain the risks and what to check before using the repo
- Always mention license warnings and security findings prominently
