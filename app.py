import gradio as gr
from PIL import Image
try:
    from cog import Path  # type: ignore
except Exception:  # pragma: no cover - fallback for non-cog environments
    from pathlib import Path

from predict import FluxDevKontextPredictor
from flux.util import ASPECT_RATIOS

# Initialize predictor on startup
predictor = FluxDevKontextPredictor()
predictor.setup()


def gradio_predict(
    prompt: str,
    input_image: Image.Image,
    aspect_ratio: str,
    num_inference_steps: int,
    guidance: float,
    seed: int | None,
    output_format: str,
    output_quality: int,
    disable_safety_checker: bool,
    go_fast: bool,
):
    # Save input image to temporary path
    input_path = "gradio_input.png"
    input_image.save(input_path)
    seed = int(seed) if seed not in (None, "") else None
    result_path = predictor.predict(
        prompt=prompt,
        input_image=Path(input_path),
        aspect_ratio=aspect_ratio,
        num_inference_steps=num_inference_steps,
        guidance=guidance,
        seed=seed,
        output_format=output_format,
        output_quality=output_quality,
        disable_safety_checker=disable_safety_checker,
        go_fast=go_fast,
    )
    return Image.open(result_path)


inputs = [
    gr.Textbox(label="Prompt", lines=2),
    gr.Image(type="pil", label="Input Image"),
    gr.Dropdown(choices=list(ASPECT_RATIOS.keys()), value="match_input_image", label="Aspect Ratio"),
    gr.Slider(4, 50, value=28, step=1, label="Num Inference Steps"),
    gr.Slider(0, 10, value=2.5, step=0.1, label="Guidance"),
    gr.Number(value=None, label="Seed (leave blank for random)"),
    gr.Dropdown(choices=["webp", "jpg", "png"], value="webp", label="Output Format"),
    gr.Slider(0, 100, value=80, step=1, label="Output Quality"),
    gr.Checkbox(value=False, label="Disable Safety Checker"),
    gr.Checkbox(value=True, label="Go Fast"),
]


demo = gr.Interface(
    fn=gradio_predict,
    inputs=inputs,
    outputs=gr.Image(label="Output"),
    title="FLUX.1 Kontext Gradio Demo",
    description="Image-to-image editing with the FLUX.1 Kontext model.",
)


if __name__ == "__main__":
    demo.launch()
