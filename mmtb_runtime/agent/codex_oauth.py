"""Codex CLI agent variant that authenticates via ChatGPT OAuth instead of API key.

Subscribers (Codex Pro) authenticate by running ``codex login`` once on the
host, which writes ``~/.codex/auth.json`` containing a refresh-able OAuth
token bundle (no static OPENAI_API_KEY). Harbor's built-in ``Codex`` agent
expects an API key in the environment and synthesises an auth.json from it,
so it is incompatible with subscription auth.

This subclass keeps Harbor's install logic (npm install ``@openai/codex``)
and trajectory-conversion logic, but replaces the auth setup: it copies the
host's ``auth.json`` content (passed in via ``CODEX_OAUTH_AUTH_JSON``) into
the container at ``$CODEX_HOME/auth.json`` so codex-cli reads OAuth tokens
directly. The token bundle is removed after the trial.
"""

from __future__ import annotations

import json
import os
import shlex

from harbor.agents.installed.base import with_prompt_template
from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths


class CodexOAuth(Codex):
    """Codex CLI authenticated via ChatGPT subscription (OAuth) instead of API key."""

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if not self.model_name:
            raise ValueError("Model name is required")

        oauth_blob = os.environ.get("CODEX_OAUTH_AUTH_JSON")
        if not oauth_blob:
            raise ValueError(
                "CODEX_OAUTH_AUTH_JSON env var not set. "
                "Run scripts/run_codex_cli.sh which loads ~/.codex/auth.json into "
                "this variable, or set it manually to the contents of an existing "
                "codex-cli auth.json."
            )
        try:
            json.loads(oauth_blob)
        except json.JSONDecodeError as exc:
            raise ValueError(f"CODEX_OAUTH_AUTH_JSON is not valid JSON: {exc}") from exc

        escaped_instruction = shlex.quote(instruction)
        model = self.model_name.split("/")[-1]

        codex_home = EnvironmentPaths.agent_dir.as_posix()
        env = {
            "CODEX_HOME": codex_home,
            "CODEX_OAUTH_AUTH_JSON": oauth_blob,
        }
        if openai_base_url := os.environ.get("OPENAI_BASE_URL"):
            env["OPENAI_BASE_URL"] = openai_base_url

        cli_flags = self.build_cli_flags()
        reasoning_flag = (cli_flags + " ") if cli_flags else ""

        # Write the OAuth auth.json directly (no symlink, no API-key auth file).
        # `printf %s` preserves the JSON exactly; chmod 600 mirrors what
        # `codex login` writes on the host.
        setup_command = (
            "set -euo pipefail; "
            'mkdir -p "$CODEX_HOME"; '
            'printf "%s" "$CODEX_OAUTH_AUTH_JSON" > "$CODEX_HOME/auth.json"; '
            'chmod 600 "$CODEX_HOME/auth.json"'
        )
        skills_command = self._build_register_skills_command()
        if skills_command:
            setup_command += f"\n{skills_command}"
        mcp_command = self._build_register_mcp_servers_command()
        if mcp_command:
            setup_command += f"\n{mcp_command}"

        await self.exec_as_agent(environment, command=setup_command, env=env)

        run_env = {"CODEX_HOME": codex_home}
        try:
            await self.exec_as_agent(
                environment,
                command=(
                    "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                    "codex exec "
                    "--dangerously-bypass-approvals-and-sandbox "
                    "--skip-git-repo-check "
                    f"--model {model} "
                    "--json "
                    "--enable unified_exec "
                    f"{reasoning_flag}"
                    "-- "
                    f"{escaped_instruction} "
                    f"2>&1 </dev/null | tee {EnvironmentPaths.agent_dir / self._OUTPUT_FILENAME}"
                ),
                env=run_env,
            )
        finally:
            try:
                await self.exec_as_agent(
                    environment,
                    command='rm -f "$CODEX_HOME/auth.json"',
                    env=run_env,
                )
            except Exception:
                pass
