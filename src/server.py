import os
import subprocess
import asyncio
import logging
import json
import re
import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from openai import AsyncOpenAI

# Load environment variables
load_dotenv()

# 1. Initialize Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-sre-agent")

# 2. Configuration (Loaded from .env)
API_KEY = os.getenv("LLM_API_KEY")
BASE_URL = os.getenv("LLM_BASE_URL")
MODEL_NAME = os.getenv("LLM_MODEL", "gemini-3-pro-preview")

if not API_KEY:
    # Fallback/Warning if env not set, though .env is preferred
    logger.warning("LLM_API_KEY not found in .env, checking hardcoded defaults or failing...")

# Client Timeout (120s for Batch Operations)
http_client = httpx.AsyncClient(verify=False, timeout=120.0)
llm_client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, http_client=http_client)
mcp_server = Server("linux-sre-agent")

# ==========================================
# 🧠 Core Logic: Batch Investigation Prompt
# ==========================================
SRE_SYSTEM_PROMPT = \"\"\"
# Role
You are an Elite SRE Agent using SSH Key Auth.
Your goal is to diagnose issues within **3 STEPS** to avoid timeouts.

# ⚡ EFFICIENCY STRATEGY: BATCH COMMANDS
Do NOT run single commands. You must "Gather All Evidence" in one go.
- **Bad**: `ls /etc` -> wait -> `cat /etc/issue`
- **Good (Batch)**: `echo "--- OS ---"; cat /etc/os-release; echo "--- MEM ---"; free -h; echo "--- SERVICE ---"; systemctl status nginx`

# Execution Logic
- **Remote**: `ssh -o ControlMaster=auto -o ControlPath=/tmp/ssh-%r@%h:%p -o ControlPersist=600 -o StrictHostKeyChecking=no <USER>@<HOST> '<COMMAND>'`
- **Local**: Direct shell command.
- **Network Scan**: Use `nmap` if available, or `ping` loops.

# OODA Loop (Compressed)
1. **Initial Broad Check**: Check process, ports, logs, and config in ONE COMMAND using `;`.
2. **Deep Dive**: Only if the first step is inconclusive.
3. **Report**: Stop immediately once the root cause is visible.

# Output Format (JSON ONLY)
{
  "thought": "I will run a batch diagnostic...",
  "command": "cmd1; echo split; cmd2",
  "is_final": boolean,
  "final_report": "Summary"
}
\"\"\"

def clean_json_string(s: str) -> str:
    try:
        if "```" in s:
            match = re.search(r"(\{.*\})", s, re.DOTALL)
            if match: return match.group(1)
        return s.strip()
    except: return s

BANNED_COMMANDS = ["rm -rf /", "mkfs", "> /dev/sda", ":(){:|:&};:"]

def is_safe_command(command: str) -> bool:
    if not command: return True
    cmd_lower = command.lower()
    for ban in BANNED_COMMANDS:
        if ban in cmd_lower: return False
    return True

async def raw_execute(command: str) -> dict:
    if not is_safe_command(command):
        return {"returncode": -1, "stdout": "", "stderr": "Blocked: High-risk command."}
    try:
        # Log clean command for debugging
        debug_cmd = command.split("'")[-2] if "'" in command else command
        logger.info(f"⚡ Batch Executing: {debug_cmd[:100]}...")
        
        process = await asyncio.create_subprocess_shell(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        
        return {
            "returncode": process.returncode,
            "stdout": stdout.decode(errors='replace'),
            "stderr": stderr.decode(errors='replace')
        }
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": f"Error: {str(e)}"}

async def sre_think(history: list, instruction: str) -> dict:
    messages = [{"role": "system", "content": SRE_SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": f"Task: {instruction}"})
    
    for step in history:
        messages.append({"role": "assistant", "content": f"Cmd: {step['cmd']}"})
        messages.append({"role": "user", "content": f"Result: {step['code']}\nOut: {step['output'][:1500]}\nErr: {step['error'][:1000]}"})

    try:
        resp = await llm_client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        return json.loads(clean_json_string(resp.choices[0].message.content))
    except Exception as e:
        return {"thought": "JSON Error", "command": None, "is_final": True, "final_report": f"Brain Fault: {e}"}

async def sre_investigation_loop(instruction: str) -> str:
    history = []
    max_steps = 3
    user_report = [f"🚀 **SRE Agent**: Processing \"{instruction}\"\n"]

    for step in range(max_steps):
        decision = await sre_think(history, instruction)
        thought = decision.get("thought", "Thinking...")
        cmd = decision.get("command")
        is_final = decision.get("is_final", False)
        
        logger.info(f"🤔 Step {step+1}: {thought}")
        
        if is_final:
            final_text = decision.get("final_report", "Task done.")
            user_report.append(f"\n✅ **Root Cause**:\n{final_text}")
            return "\n".join(user_report)
        
        if not cmd: break

        user_report.append(f"**Step {step+1}**: {thought}\n> `{cmd}`")
        result = await raw_execute(cmd)
        
        out_display = result['stdout'].strip() or result['stderr'].strip() or "(No Output)"
        user_report.append(f"```\n{out_display[:800]}\n```\n")
        
        history.append({
            "cmd": cmd, 
            "code": result['returncode'], 
            "output": result['stdout'], 
            "error": result['stderr']
        })

    return "\n".join(user_report) + "\n⏳ **Analysis Limit**: Showing partial findings."

@mcp_server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [Tool(name="execute_system_command", description="SRE Batch Agent", inputSchema={"type": "object", "properties": {"instruction": {"type": "string"}}, "required": ["instruction"]})]

@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    return [TextContent(type="text", text=await sre_investigation_loop(arguments.get("instruction")))]

if __name__ == "__main__":
    import uvicorn
    async def starlette_app(scope, receive, send):
        sse = SseServerTransport("/messages")
        if scope['type'] != 'http': return
        path = scope.get("path", "")
        method = scope.get("method", "")
        if (path == "/sse" or path == "/sse/") and method == "GET":
            async with sse.connect_sse(scope, receive, send) as streams:
                await mcp_server.run(streams[0], streams[1], mcp_server.create_initialization_options())
            return
        if (path == "/messages" or path == "/messages/") and method == "POST":
            await sse.handle_post_message(scope, receive, send)
            return
        await send({'type': 'http.response.start', 'status': 404, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'Not Found'})

    uvicorn.run(starlette_app, host="0.0.0.0", port=8000)
