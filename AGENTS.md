# Joe repository workflow

- After each coherent modification to Joe, run the relevant tests, create a
  descriptive Git commit, and push the current tracked branch to `origin`.
- Keep commits small enough to identify and revert one logical change.
- Never commit credentials, authentication tokens, local caches, provider
  transcripts, restricted challenge data, or generated `.agentflow` state.
- Autonomous campaigns must start from a clean Git repository with an `origin`
  remote. Enable the project's automatic commit/push setting and preserve one
  descriptive checkpoint for every research or implementation iteration.
- Use a private remote for a newly created Autonomous repository. Do not publish
  a repository or change its visibility without explicit user approval.
- Do not push directly to the default branch for a large or risky change; push a
  feature branch and report the branch and commit to the user.
