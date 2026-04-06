#!/bin/bash
# Engineering Hub Control Interface - Environment Initialization
# Source this file to activate the virtual environment and set up the shell
# Usage: source init.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activate virtual environment
if [ -d "$SCRIPT_DIR/.venv" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
    echo "✓ Virtual environment activated"
else
    echo "Error: .venv not found. Create it with: python3.11 -m venv .venv"
    return 1 2>/dev/null || exit 1
fi

# Set default environment variables (can be overridden by .env file)
export PYTHONPATH="${SCRIPT_DIR}/src:${PYTHONPATH:-}"

# Load .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
    echo "✓ Loaded environment from .env"
fi

# run the .venv 
source "$SCRIPT_DIR/.venv/bin/activate"
echo "✓ Activated virtual environment"

# Check for required API keys (warn if missing)
if [ -z "${ENGINEERING_HUB_ANTHROPIC_API_KEY:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "⚠ Warning: No Anthropic API key set (ENGINEERING_HUB_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY)"
fi

if [ -z "${ENGINEERING_HUB_DJANGO_API_TOKEN:-}" ]; then
    echo "⚠ Warning: No Django API token set (ENGINEERING_HUB_DJANGO_API_TOKEN)"
fi

# Check Ollama availability (used for memory embeddings and weekly-review agent work context)
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "✓ Ollama running (memory embeddings available)"
else
    echo "⚠ Ollama not running — memory embeddings disabled"
    echo "  Start with: ollama serve"
    echo "  Pull model: ollama pull nomic-embed-text"
fi

# Display Python version
echo "✓ Python: $(python --version)"
echo "✓ Working directory: $SCRIPT_DIR"
echo ""
echo "To install dependencies: pip install -e '.[dev]'"
echo "To run tests: pytest"
