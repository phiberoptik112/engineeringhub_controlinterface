"""Shared defaults for the Journaler MLX stack."""

# Used when config has neither journaler.model_path nor mlx.model_path.
# Gemma 4 31B instruction-tuned, 8-bit quantized — requires mlx-vlm.
DEFAULT_JOURNALER_MLX_MODEL_ID = "mlx-community/gemma-4-31b-it-8bit"
