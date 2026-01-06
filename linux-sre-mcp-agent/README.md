# 🤖 Linux SRE MCP Agent

An autonomous Site Reliability Engineer (SRE) agent powered by the Model Context Protocol (MCP) and Gemini. This agent connects to your Cherry Studio (or other MCP clients), intelligently diagnoses Linux server issues, performs network scans, and executes remediation steps using an OODA Loop (Observe, Orient, Decide, Act) strategy.

## ✨ Features

* **🧠 Batch Diagnostics**: Uses Gemini to analyze multiple system states (Processes, Logs, Network) in a single pass.
* **🚀 SSH Multiplexing**: Implements `ControlMaster` for millisecond-latency executions.
* **🛡️ Hybrid Execution**: Automatically detects if the target is Local or Remote.
* **🔑 Key-Based Auth**: Secure, password-less operation using SSH keys and `sudo NOPASSWD`.
* **🕵️ Network Awareness**: Capable of scanning local subnets.
* **🔄 Self-Healing**: Detects errors and autonomously digs for root causes.

## 🚀 Installation

1.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configure Environment**
    ```bash
    cp .env.example .env
    # Edit .env with your API keys
    ```

3.  **Run**
    ```bash
    python src/server.py
    ```
