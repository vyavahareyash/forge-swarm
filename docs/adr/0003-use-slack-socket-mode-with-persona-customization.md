# 3. Use Slack Socket Mode with Single-Bot Persona Customization

## Context
Deploying an internal agent daemon requires bidirectional communication with human engineers without opening public ingress HTTP ports or configuring reverse proxies (e.g. ngrok). Presenting multiple agent roles clearly in a thread typically requires registering and maintaining multiple distinct Slack apps and tokens.

## Decision
We connect via Slack Bolt Socket Mode (using `xapp` app-level token) and override bot usernames and emoji avatars per message using the `chat:write.customize` scope on a single bot token.

## Consequences
- **Positive**: Zero public firewall openings, simple one-app installation, and distinct visual personas (`[Atlas]`, `[Forge]`, `[Sentinel]`, `[Quota Warden]`) inside a single war-room thread.
- **Negative**: Requires websocket connection persistence and depends on Slack's proprietary Block Kit formatting.
