Check the license of a GitHub repository using the oss-scout MCP server.

Use the `check_license` tool with repo_url: $ARGUMENTS

If the argument does not start with "https://", prepend "https://github.com/" to form the full URL.

Present the license check results clearly:

## License Check: owner/repo

- **License**: (name)
- **SPDX ID**: (identifier)
- **Category**: permissive / copyleft / unknown / none
- **Commercial Use**: Yes or No
- **Recommended**: Yes or No

### Warnings

List any warnings from the check. If there are no warnings, state "No warnings."

### What This Means

Briefly explain the implications:
- If permissive (MIT, Apache-2.0, BSD): safe for most commercial projects
- If copyleft (GPL, AGPL): derivative works must be open-sourced
- If unknown/none: legal review needed before use
