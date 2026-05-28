#!/bin/bash
cd /Users/altair/Documents/Projects/LarkListener
export PYTHONPATH="/Users/altair/Documents/Projects/LarkListener"
export LLM_PROXY_API_KEY="sk-ek9AEV2BXg5Vl6ChjiNa5A"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
exec /usr/bin/python3 -m lark_listener.main
