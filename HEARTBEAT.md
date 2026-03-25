# HEARTBEAT.md — WeatherSafeClaw

## Scheduled Tasks (managed via OpenClaw cron)

### Morning Start — 06:10 ET daily
- Start the trading agent: `bash start.sh`
- Confirm PID and post start-of-day message to `#trading`
- Bankroll cap: $50

### Health Check — every 4 hours
- Check `.agent.pid` to confirm the agent process is alive
- Tail `logs/errors/agent.log` for errors
- Tail today's `logs/trades/YYYY-MM-DD.jsonl` for trade activity
- Post summary to `#trading`

### Keepalive — every 15 minutes (silent)
- Check if agent is running via `.agent.pid`
- If stopped: run `bash start.sh` to restart
- No Discord notification unless restart was needed

### End-of-Day Shutdown — 22:05 ET daily
- Run `bash stop.sh`
- Confirm agent stopped cleanly
- Post end-of-day summary to `#trading`

## Agent Process

- Start: `bash /Users/smini/dev/kalshi-trader/start.sh`
- Stop: `bash /Users/smini/dev/kalshi-trader/stop.sh`
- PID file: `/Users/smini/dev/kalshi-trader/.agent.pid`
- Log: `/Users/smini/dev/kalshi-trader/logs/errors/agent.log`
- Trades: `/Users/smini/dev/kalshi-trader/logs/trades/YYYY-MM-DD.jsonl`

## Discord

All notifications go to `#trading` channel (ID: `1483527849372549180`)
OpenClaw handles Discord delivery natively — no webhook needed.
