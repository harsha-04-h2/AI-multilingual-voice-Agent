# Modal Deployment

This project deploys the LiveKit voice receptionist as one warm Modal container.
The Modal wrapper starts the existing command, `python src/agent.py start`, so the
LiveKit agent business logic stays in `src/agent.py`.

## What Modal Runs

- App name: `client-voice-agent`
- Secret name: `client-voice-agent-env`
- Entrypoint: `modal_app.py`
- Worker command inside Modal: `python -u src/agent.py start`
- Warm containers: `1`
- Max containers: `1`
- CPU: `2`
- Memory: `2048 MB`
- Timeout: `24 hours`

The wrapper starts the LiveKit worker during container startup. If the worker
crashes, the container exits so Modal can replace the warm worker.

## Required Secrets

Create one Modal secret named `client-voice-agent-env` with these values:

```bash
modal secret create client-voice-agent-env \
  LIVEKIT_URL="wss://your-project.livekit.cloud" \
  LIVEKIT_API_KEY="your_livekit_api_key" \
  LIVEKIT_API_SECRET="your_livekit_api_secret" \
  DEEPGRAM_API_KEY="your_deepgram_api_key" \
  CARTESIA_API_KEY="your_cartesia_api_key" \
  GEMINI_API_KEY="your_gemini_api_key" \
  WEBHOOK_URL="https://events-12managment.app.n8n.cloud/webhook/voice" \
  SIP_TRUNK_ID="your_livekit_sip_trunk_id" \
  SIP_DOMAIN="your_sip_domain" \
  DEFAULT_TRANSFER_NUMBER="+919999999999"
```

If you do not use call transfer, set `DEFAULT_TRANSFER_NUMBER` to an empty value
or leave it out. If you do not use outbound SIP dialing, leave `SIP_TRUNK_ID`
empty until your LiveKit SIP trunk is ready.

## Local Testing

Install dependencies:

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run the LiveKit worker locally:

```bash
python src/agent.py download-files
python src/agent.py start
```

Test the Modal wrapper locally against Modal:

```bash
modal run modal_app.py
```

## Deploy

Login to Modal once:

```bash
modal setup
```

Deploy the warm worker:

```bash
modal deploy modal_app.py
```

Check worker health:

```bash
modal run modal_app.py
```

## Logs

```bash
modal app logs client-voice-agent --timestamps
```

## Restart

Redeploying is the cleanest restart:

```bash
modal deploy modal_app.py
```

You can also stop the app and deploy again:

```bash
modal app stop client-voice-agent
modal deploy modal_app.py
```

## Production Checklist

- Use the production n8n webhook URL in `WEBHOOK_URL`.
- Keep real keys only in Modal secrets, never in `.env.example`.
- Confirm the deployed logs show the LiveKit worker is registered.
- Confirm Deepgram, Gemini, and Cartesia keys are present in the Modal secret.
- Confirm SIP settings before using outbound calls or transfers.
- Keep `min_containers=1` for low call-start latency.
- Keep `max_containers=1` unless you intentionally want multiple receptionist workers.
