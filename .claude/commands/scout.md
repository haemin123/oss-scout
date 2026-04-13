Search for open-source boilerplates using the oss-scout MCP server.

Use the `search_boilerplate` tool with the following query: $ARGUMENTS

After getting results, analyze the candidates and recommend the best match.
If the user's query includes a language preference (e.g., "TypeScript", "Python"), pass it as the `language` parameter.
Default parameters: min_stars=100, max_results=5.

Present results in a table format:

| Repo | Stars | Quality | License | Description |
|------|-------|---------|---------|-------------|

For each result, note:
- Whether the license is permissive or copyleft
- Any warnings from sub-agent validation (security, compatibility)
- The combined score (quality + agent scores)

After the table, recommend the top 1-2 candidates with reasoning, and suggest using `/scout-explain <repo_url>` for deeper analysis or `/scout-scaffold <repo_url> <dir>` to scaffold.
